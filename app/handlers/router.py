"""Smart Router (E1): определение типа ввода вне FSM и «одна кнопка» подтверждения.

Поиск НЕ запускается автоматически: сначала показывается распознанный тип и
кнопки [Искать] [Это другое]. Это защищает от случайного списания.
"""
from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.db import Database
from app.handlers.helpers import has_search_balance, show_no_balance
from app.handlers.user import lang_of
from app.i18n import t
from app.services.email_search import is_valid_email
from app.services.ip_domain import detect_kind
from app.services.username_search import parse_username
from app.validators import parse_telegram

router = Router()

_TYPE_ORDER = ("email", "phone", "ip", "telegram", "tiktok", "username")


def _phone(value: str) -> bool:
    digits = "".join(ch for ch in value if ch.isdigit())
    return value.startswith("+") and 7 <= len(digits) <= 15


def detect_type(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if is_valid_email(value):
        return "email"
    if _phone(value):
        return "phone"
    if isinstance(detect_kind(value), tuple):
        return "ip"
    if parse_telegram(value) and (value.lower().startswith(("@", "t.me/", "http")) or "t.me/" in value.lower()):
        return "telegram"
    if "tiktok.com" in value.lower():
        return "tiktok"
    if parse_username(value) and value.strip().lstrip("@").isalnum():
        return "username"
    return None


def _confirm_kb(language: str, kind: str):
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_search_that"), callback_data=f"r:go:{kind}")
    kb.button(text=t(language, "btn_not_that"), callback_data="r:types")
    kb.button(text=t(language, "btn_cancel"), callback_data="m:cancel")
    kb.adjust(2, 1)
    return kb.as_markup()


def _types_kb(language: str):
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_telegram"), callback_data="r:go:telegram")
    kb.button(text=t(language, "btn_username"), callback_data="r:go:username")
    kb.button(text=t(language, "btn_tiktok"), callback_data="r:go:tiktok")
    kb.button(text=t(language, "btn_email"), callback_data="r:go:email")
    kb.button(text=t(language, "btn_phone"), callback_data="r:go:phone")
    kb.button(text=t(language, "btn_ip_domain"), callback_data="r:go:ip")
    kb.button(text=t(language, "btn_cancel"), callback_data="m:cancel")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()


_KIND_TITLE = {
    "email": "Email",
    "phone": "Телефон",
    "ip": "IP / Домен",
    "telegram": "Telegram",
    "tiktok": "TikTok",
    "username": "Никнейм",
}

# Последний введённый текст на пользователя (для кнопки [Искать]).
# Значение: (текст, monotonic-время). Ограничено по размеру и TTL.
_PENDING: dict[int, tuple[str, float]] = {}
_MAX_PENDING = 1000
_PENDING_TTL = 15 * 60


def _prune_pending(now: float) -> None:
    expired = [uid for uid, (_, ts) in _PENDING.items() if now - ts > _PENDING_TTL]
    for uid in expired:
        _PENDING.pop(uid, None)
    while len(_PENDING) >= _MAX_PENDING:
        _PENDING.pop(next(iter(_PENDING)), None)


def pending_text(user_id: int) -> str:
    item = _PENDING.get(user_id)
    if item is None:
        return ""
    text, ts = item
    if time.monotonic() - ts > _PENDING_TTL:
        _PENDING.pop(user_id, None)
        return ""
    return text


@router.message(F.text)
async def smart_router(message: Message, db: Database, state: FSMContext):
    if await state.get_state() is not None:
        return
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return
    language = await lang_of(db, message.from_user.id)
    kind = detect_type(text)
    if kind is None:
        return

    _prune_pending(time.monotonic())
    _PENDING[message.from_user.id] = (text, time.monotonic())

    if kind == "username":
        await message.answer(t(language, "choose_type"), reply_markup=_types_kb(language))
        return
    await message.answer(
        t(language, "detected", kind=_KIND_TITLE.get(kind, kind), value=text),
        reply_markup=_confirm_kb(language, kind),
    )


@router.callback_query(F.data == "r:types")
async def cb_types(callback: CallbackQuery, db: Database):
    language = await lang_of(db, callback.from_user.id)
    await callback.answer()
    await callback.message.edit_text(t(language, "choose_type"), reply_markup=_types_kb(language))


@router.callback_query(F.data.startswith("r:go:"))
async def cb_go(callback: CallbackQuery, db: Database):
    await callback.answer()
    language = await lang_of(db, callback.from_user.id)
    kind = callback.data.split(":", 2)[2]
    value = pending_text(callback.from_user.id)
    if not value:
        await callback.message.answer(t(language, "search_cancelled"))
        return
    # Мидлвара Balance не работает для callback — проверяем баланс здесь.
    if not settings.is_admin(callback.from_user.id) and not await has_search_balance(
        db, callback.from_user.id
    ):
        await show_no_balance(callback.message, language)
        return
    await _dispatch(
        callback.message, db, language, kind, value, user_id=callback.from_user.id
    )


async def _dispatch(
    message: Message,
    db: Database,
    language: str,
    kind: str,
    value: str,
    user_id: int | None = None,
) -> None:
    """Запускает существующие run_* функции. user_id обязателен для callback:
    message.from_user у сообщений бота — это сам бот."""
    if user_id is None:
        user_id = message.from_user.id
    if kind == "email":
        from app.handlers.email_search import run_email
        await run_email(message, db, language, value, user_id=user_id)
    elif kind == "phone":
        from app.handlers.phone import run_phone
        await run_phone(message, db, language, value, user_id=user_id)
    elif kind == "ip":
        from app.handlers.ip_domain import run_ip_domain
        await run_ip_domain(message, db, language, value, user_id=user_id)
    elif kind == "telegram":
        from app.handlers.telegram import run_telegram_search
        await run_telegram_search(message, db, language, value, user_id=user_id)
    elif kind == "tiktok":
        from app.handlers.tiktok import run_tiktok
        await run_tiktok(message, db, language, value, user_id=user_id)
    elif kind == "username":
        from app.handlers.username import run_username
        await run_username(message, db, language, value, user_id=user_id)