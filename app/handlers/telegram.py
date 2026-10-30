"""Telegram-поиск через see.tg: профиль, подарки и история.

Поиск запускается ТОЛЬКО после нажатия кнопки Telegram в меню и отправки
данных в состоянии TelegramState.waiting_target. Никакого автопоиска по
произвольному тексту нет.
"""
from __future__ import annotations

import html
import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.db import Database
from app.services.search_runner import SearchSpec, run_search
from app.handlers.user import lang_of, show
from app.i18n import t
from app.keyboards import get_telegram_menu
from app.reports import telegram as telegram_report
from app.services.telegram import analyze
from app.validators import parse_telegram

log = logging.getLogger(__name__)
router = Router()

_ERROR_KEYS = {
    "not_found": "not_found",
    "invalid_input": "invalid_input",
    "source_not_configured": "source_not_configured",
    "unavailable": "unavailable",
    "auth_error": "source_not_configured",
}


class TelegramState(StatesGroup):
    waiting_target = State()


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


async def run_telegram_search(message: Message, db: Database, language: str, value: str, user_id: int | None = None) -> None:
    """Поиск по Telegram через see.tg Public API."""
    await run_search(message, db, language, TELEGRAM_SPEC, value, user_id=user_id)


def _valid_telegram_target(value: str) -> bool:
    """@username, ID, t.me/... или ссылка на подарок вида Slug-N."""
    if parse_telegram(value) is not None:
        return True
    return bool(re.fullmatch(r"[A-Za-z0-9_]{1,64}-\d{1,9}", (value or "").strip()))


TELEGRAM_SPEC = SearchSpec(
    kind="telegram",
    normalize=lambda v: v.strip(),
    validate=_valid_telegram_target,
    analyze=analyze,
    build_report=telegram_report.build,
    invalid_key="invalid_input",
    error_map={
        "not_found": "not_found",
        "source_not_configured": "source_not_configured",
        "invalid_input": "invalid_input",
    },
    caption_label="",
    filename_prefix="telegram",
)

@router.callback_query(F.data == "m:telegram")
async def cb_telegram_menu(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await state.set_state(TelegramState.waiting_target)
    await show(callback, t(language, "telegram_hint"), get_telegram_menu(language))


@router.message(TelegramState.waiting_target)
async def handle_search(message: Message, db: Database, state: FSMContext):
    await state.clear()
    target = (message.text or "").strip()
    language = await lang_of(db, message.from_user.id)

    if not _valid_telegram_target(target):
        await state.set_state(TelegramState.waiting_target)
        await message.answer(t(language, "invalid_input"), reply_markup=get_telegram_menu(language))
        return

    await run_telegram_search(message, db, language, target, user_id=message.from_user.id)