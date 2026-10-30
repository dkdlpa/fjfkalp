"""Dork-генератор: выбор движка, ввод данных, HTML-отчёт с живым поиском."""
from __future__ import annotations

import asyncio
import html
import logging
import time
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.db import Database
from app.handlers.helpers import spend_request as _spend
from app.handlers.user import lang_of, show, show_from_report
from app.i18n import t
from app.keyboards import get_back_menu, get_dork_engine_menu
from app.reports import dork_report
from app.services.dork import ENGINES, extract_entities, generate_dorks
from app.services.search_runner import deliver_report
from app.services.web_search import search_many_detailed

log = logging.getLogger(__name__)
router = Router()

_results: dict[int, dict[str, Any]] = {}
SESSION_TTL = 1800
_MAX_SESSIONS = 1000


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


class DorkState(StatesGroup):
    waiting_data = State()


def _prune() -> None:
    now = time.monotonic()
    for uid in [u for u, v in _results.items() if now - v.get("created", 0) > SESSION_TTL]:
        _results.pop(uid, None)
    if len(_results) > _MAX_SESSIONS:
        oldest = sorted(_results.items(), key=lambda kv: kv[1].get("created", 0))
        for uid, _ in oldest[: len(_results) - _MAX_SESSIONS]:
            _results.pop(uid, None)


async def _live_search(items: list[dict]) -> dict:
    """Живой веб-поиск по самым приоритетным dork-запросам."""
    queries = [it.get("query", "") for it in items[:3]]
    try:
        return await asyncio.wait_for(
            search_many_detailed(queries, 6, limit=3),
            timeout=settings.search_deadline,
        )
    except Exception:  # noqa: BLE001
        log.exception("live web search failed")
        return {"results": []}


@router.callback_query(F.data == "dork:start")
async def cb_choose_engine(callback: CallbackQuery, state: FSMContext, db: Database):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    if await show_from_report(callback, t(language, "dork_title"), get_dork_engine_menu(language)):
        return
    await show(callback, t(language, "dork_title"), get_dork_engine_menu(language))


@router.callback_query(F.data.startswith("dork:engine:"))
async def cb_engine_selected(callback: CallbackQuery, state: FSMContext, db: Database):
    await callback.answer()
    engine = callback.data.split(":")[2]
    language = await lang_of(db, callback.from_user.id)
    if engine not in ENGINES:
        await callback.message.answer(t(language, "unknown_engine"))
        return
    await state.update_data(engine=engine)
    await state.set_state(DorkState.waiting_data)
    text = f"<b>{_esc(ENGINES[engine]['name'])}</b>\n" + t(language, "dork_hint")
    await show(callback, text, get_back_menu(language, "dork:start"))


@router.message(DorkState.waiting_data)
async def handle_dork_data(message: Message, state: FSMContext, db: Database):
    data = await state.get_data()
    engine = data.get("engine")
    await state.clear()
    language = await lang_of(db, message.from_user.id)

    if not engine:
        await message.answer(t(language, "dork_failed"), reply_markup=get_back_menu(language, "m:search"))
        return

    entities = extract_entities((message.text or "").strip())
    if sum(len(v) for v in entities.values()) == 0:
        await message.answer(t(language, "dork_empty"), reply_markup=get_back_menu(language, "m:search"))
        return

    status = await message.answer(t(language, "dork_generating"))
    try:
        items = await generate_dorks(entities, engine)
        if not items:
            await message.answer(t(language, "dork_failed"), reply_markup=get_back_menu(language, "m:search"))
            return

        if not await _spend(message, db, language, "dork"):

            return
        _prune()

        web = await _live_search(items)

        if not (web.get("results") or []) and web.get("error"):
            await message.answer(
                t(language, "dork_live_unavailable"),
                reply_markup=get_back_menu(language, "m:search"),
            )

        _results[message.from_user.id] = {
            "entities": entities,
            "engine": engine,
            "items": items,
            "web": web,
            "created": time.monotonic(),
        }

        report = dork_report.build(entities, engine, items, web, language)
        filename = f"dork_{engine}_{int(time.time())}.html"
        await deliver_report(
            message,
            db,
            language,
            report,
            filename=filename,
            caption=t(
                language,
                "dork_report_caption",
                engine=ENGINES[engine]["name"],
                count=len(items),
            ),
            user_id=message.from_user.id,
        )
    finally:
        try:
            await status.delete()
        except TelegramBadRequest:
            pass


@router.callback_query(F.data == "dork:export")
async def cb_export(callback: CallbackQuery, db: Database):
    await callback.answer()
    language = await lang_of(db, callback.from_user.id)
    _prune()
    session = _results.get(callback.from_user.id)
    if not session or time.monotonic() - session.get("created", 0) > SESSION_TTL:
        _results.pop(callback.from_user.id, None)
        await callback.message.answer(t(language, "report_expired"))
        return
    web = session.get("web")
    if web is None:
        web = await _live_search(session["items"])
        session["web"] = web
    report = dork_report.build(session["entities"], session["engine"], session["items"], web, language)
    filename = f"dork_{session['engine']}_{int(time.time())}.html"
    await deliver_report(
        callback.message,
        db,
        language,
        report,
        filename=filename,
        caption=t(
            language,
            "dork_report_caption",
            engine=ENGINES[session["engine"]]["name"],
            count=len(session["items"]),
        ),
        user_id=callback.from_user.id,
    )
