"""Универсальный запуск поиска (F1).

Один конвейер для всех типов:
    нормализация -> кэш -> анализ -> проверка "успешности" -> списание ->
    HTML-отчёт -> краткая сводка -> клавиатура.

Каждый тип описывается SearchSpec. Правила «когда не списывать» задаются
функцией ok_to_charge, которая смотрит на findings. Хэндлеры остаются тонкими.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, Message

from app.config import settings
from app.db import Database
from app.i18n import t
from app.keyboards import get_report_link_menu, get_report_menu
from app.services import cache, report_host

log = logging.getLogger(__name__)

# Защита от перегрузки: один активный поиск на пользователя и общий лимит
# одновременных «тяжёлых» поисков (username, email). Создаются лениво, чтобы
# семафор привязался к текущему event loop.
_heavy_sem: asyncio.Semaphore | None = None
_active_users: set[int] = set()
HEAVY_KINDS = {"username", "email"}


def _heavy_semaphore() -> asyncio.Semaphore:
    global _heavy_sem
    if _heavy_sem is None:
        from app.config import settings

        _heavy_sem = asyncio.Semaphore(max(1, settings.max_heavy_searches))
    return _heavy_sem


@dataclass
class SearchSpec:
    kind: str                       # "email", "phone", ...
    normalize: Callable[[str], str]
    validate: Callable[[str], bool]
    analyze: Callable[[str], Awaitable[dict]]
    build_report: Callable[[dict, str, str], bytes]
    error_map: dict[str, str] = field(default_factory=dict)
    invalid_key: str = "invalid_input"
    unavailable_key: str = "unavailable"
    caption_label: str = ""
    to_thread: bool = False
    ok_to_charge: Callable[[dict], bool] = lambda f: not f.get("error")
    summary: Callable[[dict, str], str] | None = None
    filename_prefix: str = "report"
    # Публиковать ли временную ссылку (только если REPORTS_WEB_ENABLED=1).
    publish_link: bool = True


def _safe_hash(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()[:8]


async def deliver_report(
    message: Message,
    db: Database,
    language: str,
    report: bytes,
    *,
    filename: str,
    caption: str,
    user_id: int,
    publish: bool = True,
) -> None:
    """Отправляет готовый отчёт: временной ссылкой или файлом.

    Отчёт собирается один раз. Если веб-публикация включена, создаётся
    непредсказуемая ссылка; повторный поиск при открытии не выполняется.
    Если публикация недоступна — файл уходит как раньше, без ошибки.
    """
    published = None
    if publish:
        try:
            published = await asyncio.to_thread(report_host.publish, report)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось опубликовать отчёт, отправляю файлом")
            published = None

    if published is not None:
        report_host.register(user_id, published)
        caption = caption + "\n" + t(
            language, "report_link_caption", minutes=settings.reports_ttl_minutes
        )
        markup = get_report_link_menu(language, published.url, published.token)
    else:
        markup = get_report_menu(language)

    try:
        await message.answer_document(
            BufferedInputFile(report, filename=filename),
            caption=caption,
            reply_markup=markup,
        )
    except TelegramBadRequest:
        # Подпись могла не пройти (например, слишком длинная) — шлём файл без неё.
        await message.answer_document(
            BufferedInputFile(report, filename=filename),
            reply_markup=markup,
        )


async def run_search(
    message: Message,
    db: Database,
    language: str,
    spec: SearchSpec,
    raw: str,
    user_id: int | None = None,
) -> None:
    if user_id is None:
        user_id = message.from_user.id

    # Один активный поиск на пользователя.
    if user_id in _active_users:
        await message.answer(t(language, "search_busy"), reply_markup=get_report_menu(language))
        return
    # Перепроверка подписки перед платным действием (force, без кэша).
    from app.services.subscription import service as _sub_service

    if _sub_service.enabled and not settings.is_admin(user_id):
        sub = await _sub_service.check(message.bot, user_id, force=True)
        if not sub.ok:
            if sub.error in {"config", "temporary"}:
                await message.answer(
                    t(language, "sub_check_unavailable"),
                    reply_markup=get_report_menu(language),
                )
            else:
                from app.keyboards import get_channels_menu

                names = ", ".join(c.title for c in (sub.missing_channels or [])) or "-"
                channels = [c.chat_id for c in (sub.missing_channels or [])]
                links = {c.chat_id: c.link for c in (sub.missing_channels or []) if c.link}
                await message.answer(
                    t(language, "sub_not_subscribed", channels=names),
                    reply_markup=get_channels_menu(language, channels, links),
                )
            return

    _active_users.add(user_id)
    status = await message.answer(t(language, "status_searching"))
    try:
        value = spec.normalize(raw)
        if not spec.validate(value):
            await message.answer(t(language, spec.invalid_key), reply_markup=get_report_menu(language))
            return

        key = cache.make_key(spec.kind, value)
        findings = await cache.get(db, key)
        if findings is None:
            analyze_coro = (
                asyncio.to_thread(spec.analyze, value)
                if spec.to_thread
                else spec.analyze(value)
            )
            try:
                # Страховка: ни один источник не должен держать пользователя
                # дольше общего дедлайна поиска.
                findings = await asyncio.wait_for(
                    analyze_coro, timeout=settings.search_deadline + 0.5
                )
            except asyncio.TimeoutError:
                findings = {"error": "unavailable"}
            if findings.get("error"):
                err = findings["error"]
                if err == "invalid_input":
                    await message.answer(t(language, spec.invalid_key), reply_markup=get_report_menu(language))
                    return
                if err in spec.error_map:
                    await message.answer(t(language, spec.error_map[err]), reply_markup=get_report_menu(language))
                    return
                await message.answer(t(language, spec.unavailable_key), reply_markup=get_report_menu(language))
                return
            if not spec.ok_to_charge(findings):
                await message.answer(t(language, "sources_unavailable"), reply_markup=get_report_menu(language))
                return
            await cache.set(db, key, findings)
        elif not spec.ok_to_charge(findings):
            await message.answer(t(language, "sources_unavailable"), reply_markup=get_report_menu(language))
            return

        from app.handlers.helpers import spend_request  # ленивый импорт: нет цикла

        if not await spend_request(message, db, language, spec.kind, user_id=user_id):
            return

        # Агрегированный счётчик дополнительного блока номера.
        extra = findings.get("extra")
        if isinstance(extra, dict) and extra.get("status") == "ok":
            await db.bump_module("phone_extra")

        report = await asyncio.to_thread(spec.build_report, findings, value, language)
        filename = f"{spec.filename_prefix}_{_safe_hash(value)}_{int(time.time())}.html"
        caption = t(language, "report_caption", target=html.escape(value))
        if spec.caption_label:
            caption = f"{spec.caption_label}: {html.escape(value)}"

        if spec.summary is not None:
            try:
                await message.answer(await asyncio.to_thread(spec.summary, findings, language))
            except Exception:  # noqa: BLE001
                log.debug("summary failed")

        # Временная ссылка: отчёт публикуется один раз, ссылка живёт
        # REPORTS_TTL_MINUTES и удаляется фоновой задачей. Если веб выключен,
        # просто отправляем файл, как раньше.
        await deliver_report(
            message,
            db,
            language,
            report,
            filename=filename,
            caption=caption,
            user_id=user_id,
            publish=spec.publish_link,
        )
    finally:
        _active_users.discard(user_id)
        try:
            await status.delete()
        except TelegramBadRequest:
            pass


async def run_search_heavy(
    message: Message,
    db: Database,
    language: str,
    spec: SearchSpec,
    raw: str,
    user_id: int | None = None,
) -> None:
    """Тяжёлый поиск (username/email) под общим семафором.

    Если слот занят, показываем «Вы в очереди» и ждём; слот освобождается
    в finally.
    """
    sem = _heavy_semaphore()
    if sem.locked():
        try:
            await message.answer(t(language, "search_queued"))
        except Exception:  # noqa: BLE001
            pass
    async with sem:
        await run_search(message, db, language, spec, raw, user_id=user_id)

