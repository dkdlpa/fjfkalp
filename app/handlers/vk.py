"""VK OSINT: ввод ID/ссылки и HTML-отчёт."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.db import Database
from app.handlers.user import lang_of, show
from app.i18n import t
from app.keyboards import get_vk_menu
from app.reports import vk_report
from app.services import vk
from app.services.search_runner import SearchSpec, run_search

log = logging.getLogger(__name__)
router = Router()


class VKState(StatesGroup):
    waiting_target = State()


async def run_vk(
    message: Message, db: Database, language: str, value: str, user_id: int | None = None
) -> None:
    await run_search(message, db, language, VK_SPEC, value, user_id=user_id)


def _summary(findings: dict, language: str) -> str:
    profile = findings.get("profile") or {}
    name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
    return "\n".join(
        [
            t(language, "summary_title"),
            name or "—",
            f"{findings.get('groups', {}).get('count', 0)} groups · "
            f"{findings.get('friends', {}).get('count', 0)} friends",
        ]
    )


VK_SPEC = SearchSpec(
    kind="vk",
    normalize=vk.normalize_vk_id,
    validate=vk.is_valid_vk_id,
    analyze=vk.analyze,
    build_report=vk_report.build,
    invalid_key="vk_invalid",
    error_map={"not_found": "not_found", "invalid_input": "vk_invalid"},
    caption_label="VK",
    filename_prefix="vk",
    ok_to_charge=lambda f: not f.get("error"),
    summary=_summary,
)


@router.callback_query(F.data == "m:vk")
async def cb_vk_menu(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await state.set_state(VKState.waiting_target)
    await show(callback, t(language, "vk_hint"), get_vk_menu(language))


@router.message(VKState.waiting_target)
async def handle_vk(message: Message, db: Database, state: FSMContext):
    await state.clear()
    target = (message.text or "").strip()
    language = await lang_of(db, message.from_user.id)
    if not vk.is_valid_vk_id(vk.normalize_vk_id(target)):
        await state.set_state(VKState.waiting_target)
        await message.answer(t(language, "vk_invalid"), reply_markup=get_vk_menu(language))
        return
    await run_vk(message, db, language, target, user_id=message.from_user.id)