"""Поиск по TON-кошельку (see.tg): отдельная кнопка и ручной ввод адреса."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.db import Database
from app.handlers.user import lang_of, show
from app.i18n import t
from app.keyboards import get_ton_menu
from app.reports import see_tg_report
from app.services import telegram as tg_service
from app.services.search_runner import SearchSpec, run_search

log = logging.getLogger(__name__)
router = Router()


class TonState(StatesGroup):
    waiting_target = State()


async def run_ton(
    message: Message, db: Database, language: str, value: str, user_id: int | None = None
) -> None:
    await run_search(message, db, language, TON_SPEC, value, user_id=user_id)


def _summary(findings: dict, language: str) -> str:
    wallet = findings.get("wallet") or {}
    owner = findings.get("profile") or {}
    lines = [t(language, "summary_title"), findings.get("target") or "—"]
    name = owner.get("name") or owner.get("username")
    if name:
        lines.append(f"owner: {name}")
    if wallet.get("type"):
        lines.append(f"type: {wallet['type']}")
    return "\n".join(lines)


TON_SPEC = SearchSpec(
    kind="ton",
    normalize=lambda v: v.strip(),
    validate=tg_service.is_ton_address,
    analyze=tg_service.analyze_ton,
    build_report=see_tg_report.build_ton,
    invalid_key="ton_invalid",
    error_map={"not_found": "not_found", "invalid_input": "ton_invalid"},
    caption_label="TON",
    filename_prefix="ton",
    ok_to_charge=lambda f: not f.get("error"),
    summary=_summary,
)


@router.callback_query(F.data == "m:ton")
async def cb_ton_menu(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await state.set_state(TonState.waiting_target)
    await show(callback, t(language, "ton_hint"), get_ton_menu(language))


@router.message(TonState.waiting_target)
async def handle_ton(message: Message, db: Database, state: FSMContext):
    await state.clear()
    target = (message.text or "").strip()
    language = await lang_of(db, message.from_user.id)
    if not tg_service.is_ton_address(target):
        await state.set_state(TonState.waiting_target)
        await message.answer(t(language, "ton_invalid"), reply_markup=get_ton_menu(language))
        return
    await run_ton(message, db, language, target, user_id=message.from_user.id)