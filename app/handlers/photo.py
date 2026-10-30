"""Раздел Фото: выбор режима, метаданные и определение местоположения."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.db import Database
from app.handlers.user import lang_of, show
from app.i18n import t
from app.keyboards import (
    get_photo_actions,
    get_photo_prompt_menu,
    get_photo_result_menu,
)
from app.services.photo_ai import build_metadata, analyze_location

log = logging.getLogger(__name__)
router = Router()

MAX_FILE_SIZE = 10 * 1024 * 1024


class PhotoState(StatesGroup):
    waiting_meta = State()
    waiting_geo = State()


_inflight: set[int] = set()


async def _safe_edit(message: Message, text: str, markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "ph:start")
async def cb_photo_start(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await show(callback, t(language, "photo_prompt"), get_photo_actions(language))


@router.callback_query(F.data.in_({"ph:mode:meta", "ph:mode:geo"}))
async def cb_photo_mode(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    language = await lang_of(db, callback.from_user.id)
    if callback.data == "ph:mode:meta":
        await state.set_state(PhotoState.waiting_meta)
        text = t(language, "photo_meta_prompt")
    else:
        await state.set_state(PhotoState.waiting_geo)
        text = t(language, "photo_geo_prompt")
    await show(callback, text, get_photo_prompt_menu(language))


@router.callback_query(F.data == "ph:again")
async def cb_photo_again(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    language = await lang_of(db, callback.from_user.id)
    current = await state.get_state()
    if current == PhotoState.waiting_meta.state:
        text = t(language, "photo_meta_prompt")
    elif current == PhotoState.waiting_geo.state:
        text = t(language, "photo_geo_prompt")
    else:
        await show(callback, t(language, "photo_prompt"), get_photo_actions(language))
        return
    await show(callback, text, get_photo_prompt_menu(language))


@router.message(StateFilter(PhotoState.waiting_meta, PhotoState.waiting_geo), F.photo | F.document)
async def on_photo(message: Message, db: Database, state: FSMContext):
    language = await lang_of(db, message.from_user.id)
    user_id = message.from_user.id

    document = message.document
    was_file = False
    if document is not None:
        mime = (document.mime_type or "").lower()
        if not mime.startswith("image/"):
            await message.answer(t(language, "photo_not_image"))
            return
        file_obj = document
        was_file = True
    elif message.photo:
        file_obj = message.photo[-1]
    else:
        return

    if (file_obj.file_size or 0) > MAX_FILE_SIZE:
        await message.answer(t(language, "photo_too_big"))
        return

    if user_id in _inflight:
        await message.answer(t(language, "photo_busy"))
        return

    _inflight.add(user_id)
    try:
        try:
            buffer = await message.bot.download(file_obj)
            if buffer is None:
                await message.answer(t(language, "photo_error"))
                return
            data = await asyncio.to_thread(buffer.read)
        except Exception:  # noqa: BLE001
            log.warning("Не удалось скачать изображение (user_id=%s)", user_id)
            await message.answer(t(language, "photo_error"))
            return

        current = await state.get_state()
        status = await message.answer(t(language, "photo_analyzing"))
        markup = get_photo_result_menu(language)

        if current == PhotoState.waiting_meta.state:
            text = await asyncio.to_thread(build_metadata, data, was_file)
            if not was_file:
                text = f"{text}\n\n{t(language, 'photo_meta_prompt')}"
        else:
            result, error = await analyze_location(data, user_id=user_id)
            if error == "no_vision":
                text = t(language, "photo_no_vision")
            elif error or not result:
                text = t(language, "photo_error")
            else:
                text = result

        try:
            await status.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            await message.answer(text, reply_markup=markup)
    finally:
        _inflight.discard(user_id)