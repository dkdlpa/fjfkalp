"""GitHub OSINT: ввод никнейма и HTML-отчёт."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.db import Database
from app.handlers.user import lang_of, show
from app.i18n import t
from app.keyboards import get_github_menu
from app.reports import github_report
from app.services import github
from app.services.search_runner import SearchSpec, run_search_heavy

log = logging.getLogger(__name__)
router = Router()


class GitHubState(StatesGroup):
    waiting_target = State()


async def run_github(
    message: Message, db: Database, language: str, value: str, user_id: int | None = None
) -> None:
    await run_search_heavy(message, db, language, GITHUB_SPEC, value, user_id=user_id)


def _summary(findings: dict, language: str) -> str:
    return "\n".join(
        [
            t(language, "summary_title"),
            f"{findings.get('username') or '—'}",
            f"{findings.get('repos_count', 0)} repos · {findings.get('followers', 0)} followers",
        ]
    )


GITHUB_SPEC = SearchSpec(
    kind="github",
    normalize=github.normalize_query,
    validate=github.is_valid_username,
    analyze=github.analyze,
    build_report=github_report.build,
    invalid_key="github_invalid",
    error_map={"not_found": "not_found", "invalid_input": "github_invalid"},
    caption_label="GitHub",
    filename_prefix="github",
    ok_to_charge=lambda f: not f.get("error"),
    summary=_summary,
)


@router.callback_query(F.data == "m:github")
async def cb_github_menu(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await state.set_state(GitHubState.waiting_target)
    await show(callback, t(language, "github_hint"), get_github_menu(language))


@router.message(GitHubState.waiting_target)
async def handle_github(message: Message, db: Database, state: FSMContext):
    await state.clear()
    target = (message.text or "").strip()
    language = await lang_of(db, message.from_user.id)
    if not github.is_valid_username(github.normalize_query(target)):
        await state.set_state(GitHubState.waiting_target)
        await message.answer(t(language, "github_invalid"), reply_markup=get_github_menu(language))
        return
    await run_github(message, db, language, target, user_id=message.from_user.id)