"""События chat_member для каналов-спонсоров.

Приходят только если бот администратор канала и при старте передан
allowed_updates с "chat_member". Здесь обновляем кэш подписки, чтобы отписка
учитывалась сразу.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import ChatMemberUpdated

from app.services.subscription import service

log = logging.getLogger(__name__)
router = Router()


@router.chat_member()
async def on_chat_member(event: ChatMemberUpdated):
    if not service.enabled:
        return
    sponsor_ids = {channel.chat_id for channel in service.channels}
    chat_id = str(event.chat.id)
    username = f"@{event.chat.username}" if event.chat.username else ""
    if chat_id not in sponsor_ids and username not in sponsor_ids:
        return
    status = getattr(event.new_chat_member, "status", None)
    service.set_member_status(event.new_chat_member.user.id, status)
    log.debug("chat_member: статус подписки обновлён")