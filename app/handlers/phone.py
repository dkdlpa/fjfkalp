"""Phone OSINT — хендлеры и общая логика."""
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
from app.keyboards import get_phone_menu
from app.reports import phone_report
from app.services.phone import analyze

log = logging.getLogger(__name__)
router = Router()


class PhoneState(StatesGroup):
    waiting_target = State()


async def run_phone(message: Message, db: Database, language: str, value: str, user_id: int | None = None) -> None:
    """Тонкая обёртка над общим конвейером (F1)."""
    await run_search(message, db, language, PHONE_SPEC, value, user_id=user_id)


PHONE_SPEC = SearchSpec(
    kind="phone",
    normalize=lambda v: v.strip(),
    validate=lambda v: bool(v),
    analyze=analyze,
    build_report=phone_report.build,
    invalid_key="phone_invalid",
    caption_label="Phone",
    filename_prefix="phone",
    # analyze сам выносит CPU-bound часть в поток; to_thread здесь не нужен.
    ok_to_charge=lambda f: not f.get("error"),
)

@router.callback_query(F.data == "m:phone")
async def cb_phone_menu(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await state.set_state(PhoneState.waiting_target)
    await show(callback, t(language, "phone_hint"), get_phone_menu(language))


@router.message(PhoneState.waiting_target)
async def handle_phone(message: Message, db: Database, state: FSMContext):
    await state.clear()
    target = (message.text or "").strip()
    language = await lang_of(db, message.from_user.id)

    if not target:
        await state.set_state(PhoneState.waiting_target)
        await message.answer(t(language, "phone_invalid"), reply_markup=get_phone_menu(language))
        return

    await run_phone(message, db, language, target, user_id=message.from_user.id)