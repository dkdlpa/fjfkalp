"""Точка входа: логирование, Bot/Dispatcher/DB, мидлвари, роутеры, polling."""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import BotCommand

from app.config import settings
from app.db import Database
from app.fsm import TTLMemoryStorage
from app.handlers import ROUTERS
from app.middlewares import (
    BalanceMiddleware,
    ErrorLoggingMiddleware,
    ForceSubscribeMiddleware,
    MaintenanceMiddleware,
    RateLimitMiddleware,
    TermsMiddleware,
)
from app.services import bot_ref, cache as search_cache, report_host
from app.services.http import clear_cache, close_client
from app.services.payments import poll_loop
from app.services.subscription import service as subscription_service


log = logging.getLogger("bot")


def setup_logging() -> None:
    """Zero-PII: логи пишем ТОЛЬКО в stdout (без FileHandler).

    URL-запросы httpx и события aiogram могут содержать PII, поэтому их
    уровень поднят до WARNING.
    """
    formatter = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=formatter, stream=sys.stdout)
    for noisy in ("httpx", "httpcore", "aiogram.event"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def _prune_loop(db: Database, storage: TTLMemoryStorage, retention_days: int) -> None:
    """Одна фоновая задача: БД, HTTP-кэш, кэш результатов и FSM."""
    while True:
        try:
            await asyncio.sleep(3 * 3600)
            await db.prune(retention_days)
            clear_cache()
            search_cache.clear()
            await storage.prune()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            logging.getLogger("bot").exception("Ошибка очистки БД")


async def _report_channel_diags(bot: Bot, diags) -> None:
    """Логирует результаты самопроверки каналов и шлёт админам одну сводку.

    Токены, ID каналов и пригласительные ссылки не логируются: только
    название канала и тип проблемы. Ошибка диагностики не должна ронять бота
    (вызывающий код оборачивает вызов в try/except).
    """
    diags = diags or []
    problems = [d for d in diags if not (d.found and d.bot_is_admin)]
    for d in diags:
        if d.found and d.bot_is_admin:
            logging.getLogger("bot").info("Канал «%s»: ок", d.title)
        else:
            logging.getLogger("bot").error(
                "Канал «%s»: %s", d.title, d.note or "проблема с каналом"
            )
    if not problems:
        return
    lines = ["Проверка каналов-спонсоров при старте:"]
    for d in problems:
        lines.append(f"- {d.title}: {d.note or 'канал не найден'}")
    lines.append("Добавьте бота администратором канала и проверьте ID.")
    text = "\n".join(lines)
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001
            pass


async def main() -> None:
    setup_logging()
    log = logging.getLogger("bot")
    log.info("Запуск OSINT-бота…")
    if not settings.see_tg_ready:
        log.warning(
            "SEE_TG_TOKEN не задан — Telegram-поиск и поиск TON недоступны. "
            "Задайте токен see.tg в .env."
        )
    if not settings.github_ready:
        log.warning("GitHub OSINT выключен.")
    elif not settings.github_token:
        log.info("GITHUB_TOKEN не задан — GitHub отдаёт 60 запросов/час на IP.")
    if not settings.vk_ready:
        log.warning("VK_TOKEN не задан — раздел VK сообщит, что источник не настроен.")
    if settings.reports_web_ready:
        log.info(
            "Временные ссылки на отчёты включены (TTL %s мин).",
            settings.reports_ttl_minutes,
        )
    else:
        log.info("Временные ссылки на отчёты выключены — отчёты уходят файлом.")

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = TTLMemoryStorage()
    dp = Dispatcher(storage=storage)

    db = Database(settings.db_path)
    # Всё, что уже создано, должно быть закрыто при любом сбое инициализации.
    report_server = None
    tasks: list[asyncio.Task] = []
    try:
        await db.connect()
        await db.prune(settings.retention_days)
        dp["db"] = db

        for observer in (dp.message, dp.callback_query):
            observer.middleware(ErrorLoggingMiddleware())
            observer.middleware(TermsMiddleware())
            observer.middleware(MaintenanceMiddleware())
            observer.middleware(ForceSubscribeMiddleware())
            observer.middleware(BalanceMiddleware())
            observer.middleware(RateLimitMiddleware())

        from app.services import telegram as tg_service

        async def _notify_admin(text: str) -> None:
            for admin_id in settings.admin_ids:
                try:
                    await bot.send_message(admin_id, text)
                except Exception:  # noqa: BLE001
                    pass

        tg_service.set_admin_notifier(_notify_admin)

        for router in ROUTERS:
            dp.include_router(router)

        tasks.append(asyncio.create_task(_prune_loop(db, storage, settings.retention_days)))
        # Очистка временных отчётов нужна только если публикация включена.
        if settings.reports_web_ready:
            tasks.append(asyncio.create_task(report_host.prune_loop()))
            report_server = await report_host.start_server()
        if settings.payments_ready:
            tasks.append(asyncio.create_task(poll_loop(bot, db)))
            log.info("Поллинг платежей CryptoPay запущен (%.0f с)", settings.payment_poll_interval)
        else:
            log.warning("CRYPTOPAY_TOKEN не задан — покупка запросов недоступна.")

        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Запуск / Start"),
                    BotCommand(command="cancel", description="Отмена / Cancel"),
                    BotCommand(command="balance", description="Баланс / Balance"),
                    BotCommand(command="language", description="Язык / Language"),
                    BotCommand(command="help", description="Поддержка / Support"),
                ]
            )
            await bot.get_me()
        except TelegramUnauthorizedError:
            log.error("Неверный BOT_TOKEN — проверьте .env")
            return
        await bot.delete_webhook(drop_pending_updates=True)
        bot_ref.set_bot(bot)
        try:
            if subscription_service.enabled:
                diags = await subscription_service.self_check(bot)
                await _report_channel_diags(bot, diags)
        except Exception:  # noqa: BLE001 — диагностика не должна мешать запуску
            log.exception("Самопроверка каналов не удалась, продолжаю запуск")
        allowed = set(dp.resolve_used_update_types()) | {"chat_member"}
        await dp.start_polling(bot, allowed_updates=sorted(allowed))
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        if report_server is not None:
            report_server.close()
            with suppress(Exception):
                await report_server.wait_closed()
        for closer in (db.close, storage.close, close_client, bot.session.close):
            try:
                await closer()
            except Exception:  # noqa: BLE001 — не мешаем закрыть остальное
                log.debug("Ошибка при закрытии: %s", getattr(closer, "__qualname__", closer))
        log.info("Бот остановлен.")


if __name__ == "__main__":
    try:
        import uvloop  # noqa: PLC0415

        uvloop.install()
    except ImportError:
        pass
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass