"""Общие помощники для хэндлеров поиска (без состояния)."""
from __future__ import annotations

from aiogram import Bot
from aiogram.types import Message

from app.config import settings
from app.db import Database
from app.i18n import t
from app.keyboards import get_no_balance_menu
from app.services import billing

_bot_username: str | None = None


async def get_bot_username(bot: Bot) -> str | None:
    global _bot_username
    if _bot_username is None:
        try:
            _bot_username = (await bot.get_me()).username
        except Exception:  # noqa: BLE001
            return None
    return _bot_username


def referral_link(bot_username: str | None, uid: int) -> str | None:
    if not bot_username:
        return None
    return f"https://t.me/{bot_username.lstrip('@')}?start=ref_{uid}"


async def has_search_balance(db: Database, user_id: int) -> bool:
    """Есть ли триал или оплаченные запросы (предпроверка для callback)."""
    if await db.trial_available(user_id):
        return True
    return await db.get_credits(user_id) > 0


async def show_no_balance(message: Message, language: str) -> None:
    await message.answer(t(language, "no_balance"), reply_markup=get_no_balance_menu(language))


async def spend_request(
    message: Message,
    db: Database,
    language: str,
    kind: str,
    user_id: int | None = None,
) -> bool:
    """Списывает запрос после успешного поиска (сначала пробные).

    user_id передаётся явно: для сообщений бота message.from_user — это бот,
    поэтому списывать по нему нельзя. Если user_id не задан — берём автора.
    Реферальный бонус начисляется пригласившему СТРОГО после первого
    успешного списания у приглашённого (D3).
    """
    if user_id is None:
        user_id = message.from_user.id
    if await db.spend_credit(user_id, kind) is None:
        await message.answer(t(language, "no_balance"), reply_markup=get_no_balance_menu(language))
        return False
    referrer_id = await billing.grant_referral_bonus(db, user_id)
    if referrer_id is not None:
        try:
            ref_lang = (await db.get_user_language(referrer_id)) or settings.default_language
            await message.bot.send_message(
                referrer_id, t(ref_lang, "referral_joined", bonus=settings.referral_bonus)
            )
        except Exception:  # noqa: BLE001
            pass
    return True