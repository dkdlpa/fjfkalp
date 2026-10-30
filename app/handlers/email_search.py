"""Email-поиск: валидация, сервисы регистрации, утечки."""
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
from app.keyboards import get_email_menu
from app.reports import email_report
from app.services.email_search import analyze, is_valid_email

log = logging.getLogger(__name__)
router = Router()


class EmailState(StatesGroup):
    waiting_email = State()


async def run_email(message: Message, db: Database, language: str, value: str, user_id: int | None = None) -> None:
    """Тонкая обёртка над общим конвейером (F1)."""
    await run_search_heavy(message, db, language, EMAIL_SPEC, value, user_id=user_id)


def _summary_email(f: dict, language: str) -> str:
    exists = f.get("exists")
    status = {True: "существует", False: "не найден", None: t(language, "email_unknown")}[exists]
    lines = [
        t(language, "summary_title"),
        f"Статус: {status}",
        f"Сервисов: {len(f.get('services') or [])}",
        f"Утечек: {len(f.get('breaches') or [])}",
    ]
    return "\n".join(lines)


EMAIL_SPEC = SearchSpec(
    kind="email",
    normalize=lambda v: v.strip().lower(),
    validate=is_valid_email,
    analyze=analyze,
    build_report=email_report.build,
    invalid_key="email_invalid",
    error_map={"unavailable": "unavailable"},
    caption_label="Email",
    filename_prefix="email",
    ok_to_charge=lambda f: f.get("validation_ok") or bool(f.get("services") or f.get("breaches")),
    summary=_summary_email,
)



@router.callback_query(F.data == "m:email")
async def cb_email_menu(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await state.set_state(EmailState.waiting_email)
    await show(callback, t(language, "email_hint"), get_email_menu(language))


@router.message(EmailState.waiting_email)
async def handle_email(message: Message, db: Database, state: FSMContext):
    await state.clear()
    email = (message.text or "").strip()
    language = await lang_of(db, message.from_user.id)

    if not is_valid_email(email):
        await state.set_state(EmailState.waiting_email)
        await message.answer(t(language, "email_invalid"), reply_markup=get_email_menu(language))
        return

    await run_email(message, db, language, email, user_id=message.from_user.id)