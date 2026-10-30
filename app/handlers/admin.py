"""Админ-панель INFEX.

Команда по умолчанию /en69 (настраивается ADMIN_COMMAND).
Функции: статистика, рассылка, выдача запросов, обслуживание, список
пользователей, каналы, ошибки, очистка кэша и БД. Плюс быстрые команды
/give, /giveall. Тексты без эмодзи.
"""
from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.config import settings
from app.db import Database
from app.keyboards import get_admin_back_menu, get_admin_menu
from app.services import cache as search_cache
from app.services.http import clear_cache

log = logging.getLogger(__name__)
router = Router()


class AdminState(StatesGroup):
    waiting_broadcast = State()
    waiting_grant = State()


def _guard(user_id: int) -> bool:
    return settings.is_admin(user_id)


# Человекочитаемые названия существующих и новых видов поиска.
# Старые kind-и остаются как есть — историческая статистика не обнуляется.
_MODULE_LABELS = {
    "telegram": "Telegram (see.tg)",
    "ton": "TON-кошелёк (see.tg)",
    "github": "GitHub",
    "vk": "VK",
    "phone": "Телефон",
    "email": "Email",
    "username": "Никнейм",
    "tiktok": "TikTok",
    "ip": "IP / Домен",
    "dork": "Dork",
    "gift": "Подарок (see.tg)",
    "phone_extra": "Доп. блок номера",
}


async def _edit(callback: CallbackQuery, text: str, markup=None) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        try:
            await callback.message.answer(text, reply_markup=markup)
        except TelegramBadRequest:
            pass


@router.message(Command(settings.admin_command.lstrip("/")))
async def cmd_admin(message: Message, state: FSMContext):
    if not _guard(message.from_user.id):
        return
    await state.clear()
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await message.answer("<b>Админ-панель</b>", reply_markup=get_admin_menu())


@router.callback_query(F.data == "a:panel")
async def cb_panel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not _guard(callback.from_user.id):
        return
    await state.clear()
    await _edit(callback, "<b>Админ-панель</b>", get_admin_menu())


@router.callback_query(F.data == "a:stats")
async def cb_stats(callback: CallbackQuery, db: Database):
    await callback.answer()
    if not _guard(callback.from_user.id):
        return
    s = await db.get_stats()
    text = (
        "<b>Статистика</b>\n"
        "<code>────────────────────────────</code>\n"
        f"Всего: <b>{s['total']}</b>\n"
        f"Активных: <b>{s['active']}</b>\n"
        f"Заблокировано: <b>{s['blocked']}</b>\n"
        f"RU / EN: <code>{s['ru']}</code> / <code>{s['en']}</code>\n"
        f"Поисков: <b>{s['searches']}</b>\n"
        f"Запросов за сутки: <b>{s['requests_today']}</b>"
    )
    if s.get("payments_count"):
        text += (
            f"\nОплат: <b>{s['payments_count']}</b>"
            f"\nВыручка: <b>${s['payments_usd']:.2f}</b>"
        )
    modules = s.get("modules") or []
    if modules:
        text += "\n\n<b>По модулям</b>\n<code>────────────────────────────</code>"
        for kind, cnt in modules:
            label = _MODULE_LABELS.get(str(kind), str(kind))
            text += f"\n{html.escape(label)}: <b>{cnt}</b>"
    await _edit(callback, text, get_admin_menu())


@router.callback_query(F.data == "a:cleanup")
async def cb_cleanup(callback: CallbackQuery, db: Database):
    await callback.answer()
    if not _guard(callback.from_user.id):
        return
    await db.cleanup()
    clear_cache()
    search_cache.clear()
    await _edit(callback, "Кэш и база данных очищены.", get_admin_menu())


@router.callback_query(F.data == "a:grant")
async def cb_grant(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not _guard(callback.from_user.id):
        return
    await state.set_state(AdminState.waiting_grant)
    await _edit(
        callback,
        "Отправьте: <code>ID количество</code>\n"
        "Пример: <code>123456789 50</code>\n"
        "Отрицательное число списывает запросы.",
        get_admin_back_menu(),
    )


@router.message(AdminState.waiting_grant)
async def do_grant(message: Message, bot: Bot, db: Database, state: FSMContext):
    if not _guard(message.from_user.id):
        return
    await state.clear()
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[0].lstrip("-").isdigit() or not parts[1].lstrip("-").isdigit():
        await message.answer("Некорректный формат. Пример: <code>123456789 50</code>")
        return
    uid, amount = int(parts[0]), int(parts[1])
    if abs(amount) > settings.admin_grant_limit:
        await message.answer(f"Слишком большое значение. Лимит: {settings.admin_grant_limit}.")
        return
    balance = await db.add_credits(uid, amount, "admin_grant", meta=f"by={message.from_user.id}")
    await db.log_admin(message.from_user.id, "grant", f"uid={uid} amount={amount}")
    await message.answer(f"Пользователю <code>{uid}</code> начислено <b>{amount}</b>. Баланс: <b>{balance}</b>.")
    try:
        await bot.send_message(uid, f"Вам начислено {amount} запросов. Баланс: {balance}.")
    except Exception:  # noqa: BLE001
        log.warning("Не удалось уведомить %s", uid)


@router.callback_query(F.data == "a:maintenance")
async def cb_maintenance(callback: CallbackQuery, db: Database):
    await callback.answer()
    if not _guard(callback.from_user.id):
        return
    current = (await db.get_setting("maintenance", "0")) == "1"
    await db.set_setting("maintenance", "0" if current else "1")
    state_text = "включено" if not current else "выключено"
    await _edit(callback, f"Обслуживание: <b>{state_text}</b>", get_admin_menu())


@router.callback_query(F.data == "a:broadcast")
async def cb_broadcast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not _guard(callback.from_user.id):
        return
    await state.set_state(AdminState.waiting_broadcast)
    await _edit(
        callback,
        "Отправьте текст рассылки одним сообщением. Можно использовать HTML-разметку.",
        get_admin_back_menu(),
    )


@router.message(AdminState.waiting_broadcast)
async def do_broadcast(message: Message, bot: Bot, db: Database, state: FSMContext):
    if not _guard(message.from_user.id):
        return
    await state.clear()
    text = message.html_text
    total = await db.count_active_users()
    status = await message.answer(f"Рассылка начата: <b>{total}</b> получателей.")
    await db.log_admin(message.from_user.id, "broadcast_start", f"len={len(text)}")
    sent = failed = 0
    sem = asyncio.Semaphore(8)

    async def send_one(uid: int) -> tuple[bool, bool]:
        async with sem:
            try:
                await bot.send_message(uid, text)
                return True, False
            except TelegramForbiddenError:
                return False, True
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
                try:
                    await bot.send_message(uid, text)
                    return True, False
                except Exception:  # noqa: BLE001
                    return False, False
            except Exception:  # noqa: BLE001
                return False, False
            finally:
                await asyncio.sleep(0.15)

    batch: list[int] = []
    async for uid in db.iter_user_ids(batch_size=500):
        batch.append(uid)
        if len(batch) >= 100:
            results = await asyncio.gather(*(send_one(u) for u in batch))
            sent += sum(1 for ok, _ in results if ok)
            failed += sum(1 for ok, _ in results if not ok)
            for (ok, blocked), u in zip(results, batch):
                if blocked:
                    await db.set_blocked(u, True)
            batch.clear()
    if batch:
        results = await asyncio.gather(*(send_one(u) for u in batch))
        sent += sum(1 for ok, _ in results if ok)
        failed += sum(1 for ok, _ in results if not ok)
        for (ok, blocked), u in zip(results, batch):
            if blocked:
                await db.set_blocked(u, True)

    try:
        await status.edit_text(f"Готово. Доставлено: <b>{sent}</b>, ошибок: <b>{failed}</b>.")
    except TelegramBadRequest:
        await message.answer(f"Готово. Доставлено: <b>{sent}</b>, ошибок: <b>{failed}</b>.")


@router.callback_query(F.data == "a:users")
async def cb_users(callback: CallbackQuery, db: Database):
    await callback.answer()
    if not _guard(callback.from_user.id):
        return
    ids = await db.get_all_user_ids()
    payload = ("\n".join(str(i) for i in ids) + "\n").encode("utf-8")
    await callback.message.answer_document(
        BufferedInputFile(payload, filename="users.txt"),
        caption=f"Пользователей: <b>{len(ids)}</b>",
    )


@router.callback_query(F.data == "a:channels")
async def cb_channels(callback: CallbackQuery):
    await callback.answer()
    if not _guard(callback.from_user.id):
        return
    public = list(settings.channels) or ["нет"]
    private = [f"{cid} ({link or 'без ссылки'})" for cid, link in settings.private_channels] or ["нет"]
    text = (
        "<b>Каналы подписки</b>\n"
        f"Публичные: <code>{html.escape(', '.join(public))}</code>\n"
        f"Приватные: <code>{html.escape(', '.join(private))}</code>"
    )
    await _edit(callback, text, get_admin_menu())


@router.callback_query(F.data == "a:errors")
async def cb_errors(callback: CallbackQuery, db: Database):
    await callback.answer()
    if not _guard(callback.from_user.id):
        return
    errors = await db.get_recent_errors(10)
    if not errors:
        text = "Ошибок не зафиксировано."
    else:
        lines = ["<b>Последние ошибки</b>", "<code>────────────────────────────</code>"]
        for err in errors:
            lines.append(f"<code>{html.escape(err['created_at'])}</code> · {html.escape(err['context'])}")
            lines.append(html.escape((err["message"] or "")[:160]))
            lines.append("")
        text = "\n".join(lines)
    await _edit(callback, text, get_admin_menu())


@router.message(Command("subcheck"))
async def cmd_subcheck(message: Message, bot: Bot):
    if not _guard(message.from_user.id):
        return
    from app.i18n import t as _t
    from app.services.subscription import service

    if not service.enabled:
        await message.answer(_t("ru", "subcheck_empty"))
        return
    diags = await service.self_check(bot)
    lines = [_t("ru", "subcheck_title")]
    for d in diags:
        try:
            res = await service.check(bot, message.from_user.id, force=True)
            you = "да" if res.ok else ("ошибка" if res.error else "нет")
        except Exception:  # noqa: BLE001
            you = "ошибка"
        lines.append(
            _t(
                "ru",
                "subcheck_line",
                title=html.escape(d.title),
                found="да" if d.found else "нет",
                admin="да" if d.bot_is_admin else "нет",
                members=d.members if d.members is not None else "-",
                you=you,
            )
        )
    await message.answer("\n".join(lines))


@router.message(Command("adminlog"))
async def cmd_adminlog(message: Message, db: Database):
    if not _guard(message.from_user.id):
        return
    rows = await db.get_admin_log(20)
    if not rows:
        await message.answer("Журнал пуст.")
        return
    lines = ["<b>Журнал админ-действий</b>"]
    for r in rows:
        lines.append(
            f"<code>{html.escape(r['created_at'])}</code> {r['admin_id']} "
            f"{html.escape(r['action'])} {html.escape(r['meta'] or '')}"
        )
    await message.answer("\n".join(lines))


# ---- Быстрые команды ----
@router.message(Command("give"))
async def cmd_give(message: Message, bot: Bot, db: Database):
    if not _guard(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].lstrip("-").isdigit() or not parts[2].lstrip("-").isdigit():
        await message.answer("Формат: /give user_id amount")
        return
    uid, amount = int(parts[1]), int(parts[2])
    if abs(amount) > settings.admin_grant_limit:
        await message.answer(f"Слишком большое значение. Лимит: {settings.admin_grant_limit}.")
        return
    balance = await db.add_credits(uid, amount, "admin_give", meta=f"by={message.from_user.id}")
    await db.log_admin(message.from_user.id, "give", f"uid={uid} amount={amount}")
    await message.answer(f"Пользователю {uid} начислено {amount}. Баланс: {balance}.")
    try:
        await bot.send_message(uid, f"Вам начислено {amount} запросов. Баланс: {balance}.")
    except Exception:  # noqa: BLE001
        log.warning("Не удалось уведомить %s", uid)


@router.message(Command("giveall"))
async def cmd_giveall(message: Message, bot: Bot, db: Database):
    if not _guard(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /giveall amount")
        return
    amount = int(parts[1])
    if abs(amount) > settings.admin_grant_limit:
        await message.answer(f"Слишком большое значение. Лимит: {settings.admin_grant_limit}.")
        return
    count = await db.give_all(amount, "admin_giveall")
    await db.log_admin(message.from_user.id, "giveall", f"amount={amount} count={count}")
    sent = 0
    async for uid in db.iter_user_ids(batch_size=500):
        try:
            await bot.send_message(uid, f"Вам начислено {amount} запросов.")
        except Exception:  # noqa: BLE001
            pass
        sent += 1
        if sent % 25 == 0:
            await asyncio.sleep(1)
    await message.answer(f"Начислено {amount} запросов всем пользователям. Получателей: {count}.")
