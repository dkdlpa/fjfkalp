"""Поиск по никнейму: проверка username по множеству платформ."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.db import Database
from app.services.search_runner import SearchSpec, run_search_heavy
from app.handlers.user import lang_of, show
from app.i18n import t
from app.keyboards import get_username_menu
from app.reports import username_report
from app.services.username_search import parse_username, search_username

log = logging.getLogger(__name__)
router = Router()


class UsernameState(StatesGroup):
    waiting_username = State()


@router.callback_query(F.data == "m:username")
async def cb_username_menu(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await state.set_state(UsernameState.waiting_username)
    await show(callback, t(language, "username_hint"), get_username_menu(language))


async def _analyze_username(value: str) -> dict:
    username = parse_username(value) or value
    return await search_username(username)


async def run_username(message: Message, db: Database, language: str, value: str, user_id: int | None = None) -> None:
    """Тонкая обёртка над общим конвейером (F1)."""
    await run_search_heavy(message, db, language, USERNAME_SPEC, value, user_id=user_id)


def _summary_username(f: dict, language: str) -> str:
    found = len(f.get("found") or [])
    lines = [t(language, "summary_title"), f"Найдено: {found}"]
    if f.get("errors_count"):
        lines.append(t(language, "summary_failed_sources", sources=f["errors_count"]))
    return "\n".join(lines)


USERNAME_SPEC = SearchSpec(
    kind="username",
    normalize=lambda v: v.strip(),
    validate=lambda v: parse_username(v) is not None,
    analyze=_analyze_username,
    build_report=lambda f, v, lang: username_report.build(f, lang),
    invalid_key="username_invalid",
    caption_label="Username",
    filename_prefix="username",
    ok_to_charge=lambda f: not f.get("sources_unavailable"),
    summary=_summary_username,
)




@router.message(UsernameState.waiting_username)
async def handle_username(message: Message, db: Database, state: FSMContext):
    await state.clear()
    target = (message.text or "").strip()
    language = await lang_of(db, message.from_user.id)

    if not parse_username(target):
        await state.set_state(UsernameState.waiting_username)
        await message.answer(t(language, "username_invalid"), reply_markup=get_username_menu(language))
        return

    await run_username(message, db, language, target, user_id=message.from_user.id)
