"""Раздел «Лица на фото»: безопасная локальная обработка изображений.

Только:
- наличие лица и количество лиц;
- визуальные характеристики (размер, ориентация, доминирующие цвета);
- локальное сравнение двух фото (перцептивный хеш + цветовая гистограмма).

Без поиска личности, без сторонних баз и без социальных сетей.
"""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.db import Database
from app.handlers.user import lang_of, show
from app.i18n import t
from app.keyboards import get_faces_menu, get_faces_prompt_menu
from app.reports.base import render_report
from app.services import faces
from app.services.search_runner import deliver_report

log = logging.getLogger(__name__)
router = Router()

MAX_FILE_SIZE = 10 * 1024 * 1024


class FacesState(StatesGroup):
    waiting_detect = State()
    waiting_compare_first = State()
    waiting_compare_second = State()


def _report(kind: str, findings: dict, language: str) -> bytes:
    ru = language == "ru"
    sections: list[dict] = []

    if kind == "detect":
        facts = findings["facts"]
        rows = [
            ("Размер" if ru else "Size", f"{facts.width}×{facts.height}", True),
            ("Формат" if ru else "Format", facts.format or "—"),
            ("Ориентация" if ru else "Orientation", facts.orientation),
            ("Вес" if ru else "Weight", f"{facts.size_kb} KB"),
        ]
        face = facts.faces
        if face is None:
            rows.append(("Лица" if ru else "Faces", "—"))
        elif not face.available:
            rows.append(("Лица" if ru else "Faces", "модуль недоступен" if ru else "module unavailable"))
        else:
            rows.append(
                (
                    "Лиц найдено" if ru else "Faces detected",
                    str(face.faces),
                    False,
                    "value ok" if face.faces else "value",
                )
            )
        sections.append({"id": "main", "title": "Изображение" if ru else "Image", "rows": rows})
        if facts.dominant_colors:
            chips = "".join(
                f'<span class="chip" style="border-left:3px solid {c}">{c}</span>'
                for c in facts.dominant_colors
            )
            sections.append(
                {
                    "id": "colors",
                    "title": "Доминирующие цвета" if ru else "Dominant colors",
                    "html": f'<div class="chips">{chips}</div>',
                }
            )
        sections.append(
            {
                "id": "limits",
                "title": "Ограничения" if ru else "Limitations",
                "note": (
                    "Определяется только наличие и количество лиц. Поиск личности "
                    "по фотографии не выполняется."
                    if ru
                    else "Only face presence and count are detected. Identity search "
                    "by photo is not performed."
                ),
            }
        )
        stats = [("faces", str(face.faces) if face and face.available else "—")]
        title = "Лица на фото" if ru else "Faces in photo"
    else:
        data = findings["compare"]
        ru_label = {
            "same": ("совпадает", "same"),
            "similar": ("похоже", "similar"),
            "different": ("отличается", "different"),
        }.get(data["verdict"], ("—", "—"))
        rows = [
            (
                "Совпадение" if ru else "Match",
                f"{data['similarity']}% · {ru_label[0] if ru else ru_label[1]}",
                False,
                "value ok" if data["verdict"] == "same" else "value",
            ),
            ("Хэмминг" if ru else "Hamming distance", str(data["distance"]), True),
            (
                "Пересечение цветов" if ru else "Color overlap",
                f"{data['color_overlap']}%",
            ),
            (
                "Размеры" if ru else "Sizes",
                f"{data['size_a'][0]}×{data['size_a'][1]} / "
                f"{data['size_b'][0]}×{data['size_b'][1]}",
                True,
            ),
            (
                "Лиц (1 / 2)" if ru else "Faces (1 / 2)",
                f"{data['faces_a']} / {data['faces_b']}",
            ),
        ]
        sections.append({"id": "main", "title": "Сравнение" if ru else "Comparison", "rows": rows})
        sections.append(
            {
                "id": "limits",
                "title": "Ограничения" if ru else "Limitations",
                "note": (
                    "Сравнение локальное, по изображению. Это не идентификация "
                    "человека и не поиск по сторонним базам."
                    if ru
                    else "The comparison is local and image-based. It is not identity "
                    "verification and not a search in third-party databases."
                ),
            }
        )
        stats = [(f"{data['similarity']}%", "match" if not ru else "совпадение")]
        title = "Сравнение фото" if ru else "Photo comparison"

    return render_report(
        title=title,
        language=language,
        sections=sections,
        stats=stats,
        tags=["Image"],
        source="local processing",
    )


async def _download(message: Message, language: str) -> bytes | None:
    document = message.document
    if document is not None:
        mime = (document.mime_type or "").lower()
        if not mime.startswith("image/"):
            await message.answer(t(language, "faces_not_image"))
            return None
        file_obj = document
    elif message.photo:
        file_obj = message.photo[-1]
    else:
        return None

    if (file_obj.file_size or 0) > MAX_FILE_SIZE:
        await message.answer(t(language, "faces_too_big"))
        return None
    try:
        buffer = await message.bot.download(file_obj)
        if buffer is None:
            await message.answer(t(language, "faces_error"))
            return None
        return await asyncio.to_thread(buffer.read)
    except Exception:  # noqa: BLE001
        log.warning("faces: не удалось скачать файл")
        await message.answer(t(language, "faces_error"))
        return None


@router.callback_query(F.data == "m:faces")
async def cb_faces_menu(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await show(callback, t(language, "faces_prompt"), get_faces_menu(language))


@router.callback_query(F.data == "fc:detect")
async def cb_faces_detect(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await state.set_state(FacesState.waiting_detect)
    await show(callback, t(language, "faces_detect_prompt"), get_faces_prompt_menu(language))


@router.callback_query(F.data == "fc:compare")
async def cb_faces_compare(callback: CallbackQuery, db: Database, state: FSMContext):
    await callback.answer()
    await state.clear()
    language = await lang_of(db, callback.from_user.id)
    await state.set_state(FacesState.waiting_compare_first)
    await show(callback, t(language, "faces_compare_first"), get_faces_prompt_menu(language))


@router.message(FacesState.waiting_detect, F.photo | F.document)
async def on_detect(message: Message, db: Database, state: FSMContext):
    await state.clear()
    language = await lang_of(db, message.from_user.id)
    if not faces.cv_available():
        await message.answer(t(language, "faces_cv_missing"), reply_markup=get_faces_menu(language))
        return

    data = await _download(message, language)
    if data is None:
        return

    status = await message.answer(t(language, "photo_analyzing"))
    try:
        facts = await asyncio.to_thread(faces.describe, data, with_faces=True)
        if facts.error:
            await message.answer(t(language, "faces_error"), reply_markup=get_faces_menu(language))
            return
        report = await asyncio.to_thread(_report, "detect", {"facts": facts}, language)
        await deliver_report(
            message,
            db,
            language,
            report,
            filename=f"faces_{int(time.time())}.html",
            caption=t(
                language,
                "caption_faces",
                count=facts.faces.faces if facts.faces and facts.faces.available else "—",
            ),
            user_id=message.from_user.id,
        )
    except Exception:  # noqa: BLE001
        log.exception("faces: ошибка обработки")
        await message.answer(t(language, "faces_error"), reply_markup=get_faces_menu(language))
    finally:
        try:
            await status.delete()
        except TelegramBadRequest:
            pass


@router.message(FacesState.waiting_compare_first, F.photo | F.document)
async def on_compare_first(message: Message, db: Database, state: FSMContext):
    language = await lang_of(db, message.from_user.id)
    data = await _download(message, language)
    if data is None:
        return
    await state.update_data(first=data)
    await state.set_state(FacesState.waiting_compare_second)
    await message.answer(t(language, "faces_compare_second"), reply_markup=get_faces_prompt_menu(language))


@router.message(FacesState.waiting_compare_second, F.photo | F.document)
async def on_compare_second(message: Message, db: Database, state: FSMContext):
    language = await lang_of(db, message.from_user.id)
    data = await _download(message, language)
    if data is None:
        return
    state_data = await state.get_data()
    await state.clear()
    first = state_data.get("first")
    if not first:
        await message.answer(t(language, "faces_error"), reply_markup=get_faces_menu(language))
        return

    status = await message.answer(t(language, "photo_analyzing"))
    try:
        result = await asyncio.to_thread(faces.compare, first, data)
        if result.get("error"):
            await message.answer(t(language, "faces_error"), reply_markup=get_faces_menu(language))
            return
        report = await asyncio.to_thread(_report, "compare", {"compare": result}, language)
        await deliver_report(
            message,
            db,
            language,
            report,
            filename=f"faces_compare_{int(time.time())}.html",
            caption=t(language, "caption_faces_compare", percent=result["similarity"]),
            user_id=message.from_user.id,
        )
    except Exception:  # noqa: BLE001
        log.exception("faces: ошибка сравнения")
        await message.answer(t(language, "faces_error"), reply_markup=get_faces_menu(language))
    finally:
        try:
            await status.delete()
        except TelegramBadRequest:
            pass