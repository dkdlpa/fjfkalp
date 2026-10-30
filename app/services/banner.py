"""Баннер главного меню www.png: загрузка через FSInputFile один раз, далее по file_id."""
from __future__ import annotations

import logging
import os

from aiogram import Bot
from aiogram.types import FSInputFile

from app.config import settings
from app.db import Database

log = logging.getLogger(__name__)

SETTING_KEY = "banner_file_id"


async def send_first(bot: Bot, db: Database, chat_id: int, text: str, reply_markup=None):
    """Отправляет файл с диска, сохраняет file_id и возвращает отправленное сообщение."""
    path = settings.banner_path
    if not os.path.isfile(path):
        log.warning("Баннер %s не найден", path)
        return None
    try:
        message = await bot.send_photo(
            chat_id, FSInputFile(path), caption=text, reply_markup=reply_markup
        )
    except Exception:  # noqa: BLE001
        log.exception("Не удалось отправить баннер")
        return None
    if message.photo:
        await db.set_setting(SETTING_KEY, message.photo[-1].file_id)
        log.info("Баннер загружен, file_id сохранен")
    return message
