"""TikTok-поиск: анализ профиля."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.db import Database
from app.services.search_runner import SearchSpec, run_search
from app.handlers.user import lang_of, show
from app.i18n import t
from app.keyboards import get_tiktok_menu
from app.reports import tiktok_report
from app.services.tiktok import analyze, parse_tiktok_input

log = logging.getLogger(__name__)
router = Router()


class TikTokState(StatesGroup):
    waiting_target = State()


async def run_tiktok(message: Message, db: Database, language: str, value: str, user_id: int | None = None) -> None:
    """Тонкая обёртка над общим конвейером (F1)."""
    await run_search(message, db, language, TIKTOK_SPEC, value, user_id=user_id)


TIKTOK_SPEC = SearchSpec(
    kind="tiktok",
    normalize=lambda v: v.strip(),
    validate=lambda v: parse_tiktok_input(v) is not None,
    analyze=analyze,
    build_report=tiktok_report.build,
    invalid_key="tiktok_invalid",
    error_map={"not_found": "not_found"},
    caption_label="TikTok",
    filename_prefix="tiktok",
)

@router.callback_query(F.data == "m:tiktok")
async def cb_tiktok_menu(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await state.set_state(TikTokState.waiting_target)
    await show(callback, t(language, "tiktok_hint"), get_tiktok_menu(language))


@router.message(TikTokState.waiting_target)
async def handle_tiktok(message: Message, db: Database, state: FSMContext):
    await state.clear()
    target = (message.text or "").strip()
    language = await lang_of(db, message.from_user.id)

    if not parse_tiktok_input(target):
        await state.set_state(TikTokState.waiting_target)
        await message.answer(t(language, "tiktok_invalid"), reply_markup=get_tiktok_menu(language))
        return

    await run_tiktok(message, db, language, target, user_id=message.from_user.id)