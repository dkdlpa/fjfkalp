"""Навигация: старт, главное меню с баннером, реф-система, профиль, инфо."""
from __future__ import annotations

import asyncio
import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.db import Database
from app.handlers.helpers import get_bot_username, referral_link
from app.i18n import t
from app.keyboards import (
    get_channels_menu,
    get_consent_menu,
    get_info_menu,
    get_language_menu,
    get_main_menu,
    get_profile_menu,
    get_ref_menu,
    get_report_menu,
    get_search_menu,
    get_simple_back,
    get_support_menu,
)
from app.services import banner, report_host

log = logging.getLogger(__name__)
router = Router()


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


async def show(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    """Навигация через редактирование текущего сообщения (текст или подпись)."""
    message = callback.message
    if message is None:
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=reply_markup)
        return
    if getattr(message, "caption", None) is not None or message.photo:
        try:
            await message.edit_caption(caption=text, reply_markup=reply_markup)
            return
        except TelegramBadRequest:
            pass
    if getattr(message, "text", None) is not None:
        try:
            await message.edit_text(text, reply_markup=reply_markup)
            return
        except TelegramBadRequest:
            pass
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=reply_markup)


async def show_from_report(callback: CallbackQuery, text: str, reply_markup=None) -> bool:
    """Если текущее сообщение - отчёт (документ/фото), отправляет новое сообщение.

    Так HTML-отчёт остаётся в чате, а навигация идёт отдельной карточкой.
    Возвращает True, если обработано (отчёт не тронут).
    """
    message = callback.message
    if message is not None and getattr(message, "document", None) is not None:
        await callback.bot.send_message(
            callback.from_user.id, text, reply_markup=reply_markup
        )
        return True
    return False


async def show_banner(
    callback: CallbackQuery | None,
    db: Database,
    text: str,
    reply_markup=None,
    message: Message | None = None,
) -> None:
    """Главное меню с баннером. Если баннер уже в сообщении — правим подпись.

    Для /start передается message (callback нет), для навигации — callback.
    """
    bot = callback.bot if callback is not None else message.bot
    chat_id = callback.from_user.id if callback is not None else message.chat.id
    current = callback.message if callback is not None else None
    file_id = await db.get_setting(banner.SETTING_KEY)
    if current is not None and current.photo and file_id:
        try:
            await current.edit_caption(caption=text, reply_markup=reply_markup)
            return
        except TelegramBadRequest:
            pass
    try:
        if current is not None:
            await current.delete()
    except TelegramBadRequest:
        pass
    sent = None
    if file_id:
        try:
            sent = await bot.send_photo(
                chat_id, file_id, caption=text, reply_markup=reply_markup
            )
        except TelegramBadRequest:
            # file_id принадлежит другому боту/устарел — сбрасываем и грузим заново.
            log.warning("Невалидный banner_file_id, перезагружаю баннер")
            await db.set_setting(banner.SETTING_KEY, "")
            file_id = None
    if sent is None:
        sent = await banner.send_first(bot, db, chat_id, text, reply_markup)
    if sent is not None:
        return
    if callback is not None:
        await show(callback, text, reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)


async def lang_of(db: Database, uid: int) -> str:
    return (await db.get_user_language(uid)) or settings.default_language


MAX_ID = 2**63 - 1


def _parse_referrer(message: Message) -> int | None:
    """Достаёт реферера из /start ref_<id> без исключений и без допущений."""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        return None
    arg = parts[1].strip()
    if not arg.startswith("ref_"):
        return None
    value = arg[4:]
    if not value or len(value) > 12:
        return None
    if not (value.isascii() and value.isdecimal()):
        return None
    number = int(value)
    if number <= 0 or number >= MAX_ID:
        return None
    return number


def _detect_language(user) -> str:
    """ru/uk/be/kk -> ru, иначе en (E6)."""
    code = (getattr(user, "language_code", None) or "").lower()
    base = code.split("-")[0]
    return "ru" if base in {"ru", "uk", "be", "kk"} else "en"


async def _balance_line(db: Database, uid: int, language: str) -> str:
    """Строка баланса для меню (E2)."""
    credits = await db.get_credits(uid)
    st = await db.trial_state(uid)
    trial_left = max(0, settings.trial_requests - st["used"])
    ends = st["ends_at"]
    return t(
        language,
        "balance_short",
        balance=credits,
        trial=trial_left,
        total=settings.trial_requests,
        ends=ends[5:16] if ends and not st["expired"] else "-",
    )


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    is_new = await db.get_user_profile(uid) is None
    await db.upsert_user(uid, message.from_user.username, message.from_user.first_name)
    await db.set_blocked(uid, False)

    if is_new:
        await db.set_user_language(uid, _detect_language(message.from_user))
        referrer_id = _parse_referrer(message)
        if referrer_id and await db.set_referrer(uid, referrer_id):
            log.info("Новый реферал: referrer=%s user=%s", referrer_id, uid)

    language = await lang_of(db, uid)
    if not await db.has_accepted_terms(uid):
        await message.answer(
            t(language, "terms_title") + "\n\n" + t(language, "terms_text", terms=settings.terms_link or "-"),
            reply_markup=get_consent_menu(language),
        )
        return
    await show_banner(None, db, t(language, "main_title"), get_main_menu(language), message=message)


@router.callback_query(F.data == "terms:accept")
async def cb_terms_accept(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await db.accept_terms(callback.from_user.id)
    language = await lang_of(db, callback.from_user.id)
    await show_banner(callback, db, t(language, "main_title"), get_main_menu(language))


@router.message(Command("balance"))
async def cmd_balance(message: Message, db: Database):
    language = await lang_of(db, message.from_user.id)
    credits, _ = await db.get_credits_and_paid(message.from_user.id)
    st = await db.trial_state(message.from_user.id)
    left = max(0, settings.trial_requests - st["used"])
    ends = st["ends_at"][5:16] if st["ends_at"] and not st["expired"] else "-"
    await message.answer(
        t(language, "balance_full", balance=credits, trial=left, total=settings.trial_requests, ends=ends)
    )


@router.message(Command("language"))
async def cmd_language(message: Message, db: Database):
    language = await lang_of(db, message.from_user.id)
    await message.answer(t(language, "lang_title"), reply_markup=get_language_menu(language))


@router.message(Command("help"))
async def cmd_help(message: Message, db: Database):
    language = await lang_of(db, message.from_user.id)
    support = (settings.support_bot or "").strip()
    if not support:
        await message.answer(t(language, "help_not_configured"))
        return
    if not support.startswith("@"):
        support = "@" + support.lstrip("@")
    await message.answer(
        t(language, "help_command", support=esc(support)),
        reply_markup=get_support_menu(language, support),
    )


@router.callback_query(F.data.startswith("rp:del:"))
async def cb_delete_report(callback: CallbackQuery, db: Database):
    language = await lang_of(db, callback.from_user.id)
    token = callback.data.split(":", 2)[2]
    published = report_host.owned(callback.from_user.id)
    if published is None or getattr(published, "token", "") != token:
        await callback.answer(t(language, "report_delete_forbidden"), show_alert=True)
        return
    removed = await asyncio.to_thread(report_host.remove, token)
    report_host.forget(callback.from_user.id)
    await callback.answer(
        t(language, "report_deleted") if removed else t(language, "report_link_missing")
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=get_report_menu(language))
    except TelegramBadRequest:
        pass


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, db: Database, state: FSMContext):
    await state.clear()
    language = await lang_of(db, message.from_user.id)
    await message.answer(t(language, "main_title"), reply_markup=get_main_menu(language))


@router.callback_query(F.data == "m:cancel")
async def cb_cancel(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await show(callback, t(language, "search_cancelled"))


@router.callback_query(F.data == "r:repeat")
async def cb_repeat(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    from app.handlers import router as smart
    from app.handlers.helpers import has_search_balance, show_no_balance
    language = await lang_of(db, callback.from_user.id)
    value = smart.pending_text(callback.from_user.id)
    kind = smart.detect_type(value) if value else None
    if not value or kind is None:
        await show(callback, t(language, "report_expired"))
        return
    if not settings.is_admin(callback.from_user.id) and not await has_search_balance(
        db, callback.from_user.id
    ):
        await show_no_balance(callback.message, language)
        return
    await smart._dispatch(
        callback.message, db, language, kind, value, user_id=callback.from_user.id
    )


@router.callback_query(F.data == "m:main")
async def cb_main(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    title = t(language, "main_title") + "\n\n" + await _balance_line(db, callback.from_user.id, language)
    await show_banner(callback, db, title, get_main_menu(language))


@router.callback_query(F.data == "m:search")
async def cb_search(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    title = t(language, "search_title") + "\n\n" + await _balance_line(db, callback.from_user.id, language)
    if await show_from_report(callback, title, get_search_menu(language)):
        return
    await show(callback, title, get_search_menu(language))


@router.callback_query(F.data == "m:ref")
async def cb_ref(callback: CallbackQuery, db: Database):
    await callback.answer()
    language = await lang_of(db, callback.from_user.id)
    link = referral_link(await get_bot_username(callback.bot), callback.from_user.id) or "-"
    count, bonus = await db.get_referral_stats(callback.from_user.id)
    text = t(language, "ref_title", link=esc(link), count=count, bonus=bonus)
    await show(callback, text, get_ref_menu(language))


@router.callback_query(F.data == "m:profile")
async def cb_profile(callback: CallbackQuery, db: Database):
    await callback.answer()
    language = await lang_of(db, callback.from_user.id)
    uid = callback.from_user.id
    credits, paid_cents = await db.get_credits_and_paid(uid)
    referrals, _ = await db.get_referral_stats(uid)
    username = callback.from_user.username
    text = t(
        language,
        "profile_body",
        uid=esc(uid),
        name=esc(callback.from_user.first_name or "-"),
        username=esc(username) if username else "-",
        lang_name=t(language, "lang_ru_name"),
        balance=credits,
        referrals=referrals,
        deposited=f"{paid_cents / 100:.2f}",
    )
    await show(callback, t(language, "profile_title") + "\n" + text, get_profile_menu(language))


@router.callback_query(F.data == "m:language")
async def cb_language_menu(callback: CallbackQuery, db: Database):
    await callback.answer()
    language = await lang_of(db, callback.from_user.id)
    await show(callback, t(language, "lang_title"), get_language_menu(language))


@router.callback_query(F.data.in_({"lang:ru", "lang:en"}))
async def cb_language(callback: CallbackQuery, db: Database):
    await callback.answer()
    language = callback.data.split(":", 1)[1]
    await db.set_user_language(callback.from_user.id, language)
    await show(callback, t(language, "profile_title"), get_profile_menu(language))


@router.callback_query(F.data == "m:info")
async def cb_info(callback: CallbackQuery, db: Database):
    await callback.answer()
    language = await lang_of(db, callback.from_user.id)
    await show(callback, t(language, "info_title"), get_info_menu(language))


@router.callback_query(F.data == "m:help")
async def cb_help(callback: CallbackQuery, db: Database):
    await callback.answer()
    language = await lang_of(db, callback.from_user.id)
    await show(callback, t(language, "help"), get_simple_back(language, "m:info"))


@router.callback_query(F.data == "m:dev")
async def cb_dev(callback: CallbackQuery, db: Database):
    await callback.answer()
    language = await lang_of(db, callback.from_user.id)
    text = t(
        language,
        "dev_title",
        creator=esc(settings.creator_link),
        author=esc(settings.author_link),
        news=esc(settings.news_link),
    )
    await show(callback, text, get_simple_back(language, "m:info"))


@router.callback_query(F.data == "sub:check")
async def cb_subscription_checked(callback: CallbackQuery, db: Database):
    from app.services.subscription import service

    result = await service.check(callback.bot, callback.from_user.id, force=True)
    language = await lang_of(db, callback.from_user.id)
    if result.ok:
        await callback.answer(t(language, "sub_check_ok"))
        await show_banner(callback, db, t(language, "main_title"), get_main_menu(language))
        return
    if result.error in {"config", "temporary"}:
        # ровно один ответ на callback
        await callback.answer(t(language, "sub_check_unavailable"), show_alert=True)
        return
    names = ", ".join(c.title for c in (result.missing_channels or [])) or "-"
    await callback.answer(t(language, "sub_not_subscribed", channels=names), show_alert=True)
    channels = [c.chat_id for c in (result.missing_channels or [])]
    links = {c.chat_id: c.link for c in (result.missing_channels or []) if c.link}
    if callback.message is not None:
        try:
            await callback.message.edit_text(
                t(language, "sub_not_subscribed", channels=names),
                reply_markup=get_channels_menu(language, channels, links),
            )
        except Exception:  # noqa: BLE001
            pass


@router.callback_query(F.data == "sub:no_link")
async def cb_private_channel(callback: CallbackQuery, db: Database):
    await callback.answer()
    language = await lang_of(db, callback.from_user.id)
    if callback.message is not None:
        await callback.message.answer(t(language, "private_link_missing"))