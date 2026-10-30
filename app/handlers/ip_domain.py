"""Domain / IP OSINT — хендлеры и общая логика."""
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
from app.keyboards import get_ip_menu, get_report_menu
from app.reports import ip_domain_report
from app.services.ip_domain import analyze, detect_kind

log = logging.getLogger(__name__)
router = Router()


class IPState(StatesGroup):
    waiting_target = State()


async def run_ip_domain(message: Message, db: Database, language: str, value: str, user_id: int | None = None) -> None:
    """Тонкая обёртка над общим конвейером (F1)."""
    kind = detect_kind(value)
    if isinstance(kind, tuple) and kind[0] == "ip_private":
        await message.answer(t(language, "ip_private"), reply_markup=get_report_menu(language))
        return
    await run_search(message, db, language, IP_SPEC, value, user_id=user_id)


IP_SPEC = SearchSpec(
    kind="ip",
    normalize=lambda v: v.strip(),
    validate=lambda v: isinstance(detect_kind(v), tuple) and detect_kind(v)[0] in {"ip", "domain"},
    analyze=analyze,
    build_report=ip_domain_report.build,
    invalid_key="ip_invalid",
    error_map={"not_found": "not_found"},
    caption_label="IP / Domain",
    filename_prefix="ip",
)

@router.callback_query(F.data == "m:ip")
async def cb_ip_menu(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await state.set_state(IPState.waiting_target)
    await show(callback, t(language, "ip_hint"), get_ip_menu(language))


@router.message(IPState.waiting_target)
async def handle_ip(message: Message, db: Database, state: FSMContext):
    await state.clear()
    target = (message.text or "").strip()
    language = await lang_of(db, message.from_user.id)

    if not detect_kind(target):
        await state.set_state(IPState.waiting_target)
        await message.answer(t(language, "ip_invalid"), reply_markup=get_ip_menu(language))
        return

    await run_ip_domain(message, db, language, target, user_id=message.from_user.id)