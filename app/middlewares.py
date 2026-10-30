"""Мидлвари: ошибки, обслуживание, подписка, дневная квота, антифлуд.

Порядок регистрации (извне внутрь):
    ErrorLogging -> Maintenance -> ForceSubscribe -> Balance -> RateLimit
Каждый слой предельно дешёвый: язык и настройки берутся из кэша БД.
"""
from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.db import Database
from app.i18n import t
from app.keyboards import get_consent_menu, get_no_balance_menu

log = logging.getLogger(__name__)

# Zero-PII: перед записью ошибок в БД вычищаем email, телефоны, IP и токены.
_REDACT_PATTERNS = (
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key|access[_-]?token|token)[=:]\s*[A-Za-z0-9._\-]+"), r"\1=[REDACTED]"),
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_\-]{30,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+"), "[REDACTED_EMAIL]"),
    (re.compile(r"\+?\d[\d\s\-().]{7,}\d"), "[REDACTED_PHONE]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[REDACTED_IP]"),
)


def sanitize(text: str) -> str:
    """Заменяет PII/секреты на плейсхолдеры."""
    text = str(text or "")
    for pattern, repl in _REDACT_PATTERNS:
        text = pattern.sub(repl, text)
    return text


async def _language(data: dict[str, Any], user_id: int | None) -> str:
    if user_id is None:
        return settings.default_language
    db = data.get("db")
    if isinstance(db, Database):
        return (await db.get_user_language(user_id)) or settings.default_language
    return settings.default_language


class ErrorLoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except Exception as exc:  # noqa: BLE001
            log.exception("Необработанная ошибка (%s): %s", type(event).__name__, exc)
            db = data.get("db")
            if isinstance(db, Database):
                await db.log_error(type(event).__name__, sanitize(str(exc)))
            user = getattr(event, "from_user", None)
            language = await _language(data, user.id if user else None)
            try:
                if isinstance(event, Message):
                    await event.answer(t(language, "error_generic"))
                elif isinstance(event, CallbackQuery):
                    await event.answer(t(language, "error_generic"), show_alert=True)
            except TelegramBadRequest:
                pass
            return None


class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and settings.is_admin(user.id):
            return await handler(event, data)

        default = "1" if settings.maintenance_mode else "0"
        db = data.get("db")
        if isinstance(db, Database):
            maintenance = (await db.get_setting("maintenance", default)) == "1"
        else:
            maintenance = settings.maintenance_mode
        if not maintenance:
            return await handler(event, data)

        language = await _language(data, user.id if user else None)
        try:
            if isinstance(event, Message):
                await event.answer(t(language, "maintenance"))
            elif isinstance(event, CallbackQuery):
                await event.answer(t(language, "maintenance"), show_alert=True)
        except TelegramBadRequest:
            pass
        return None


class TermsMiddleware(BaseMiddleware):
    """Обязательное согласие с условиями для всех Message/CallbackQuery.

    Пропускает: /start (с любыми аргументами), принятие условий, выбор языка.
    Остальных без согласия останавливает и показывает экран условий.
    Принявшие кэшируются в памяти (не более 50 000), чтобы не бить БД на
    каждый апдейт.
    """

    def __init__(self, max_cache: int = 50_000):
        self._accepted: "OrderedDict[int, None]" = OrderedDict()
        self._max = max_cache

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None or user.is_bot:
            return await handler(event, data)
        if settings.is_admin(user.id):
            return await handler(event, data)

        is_callback = isinstance(event, CallbackQuery) or hasattr(event, "data")
        if is_callback:
            action = getattr(event, "data", None)
            if action == "terms:accept":
                # Принятие усlовий: фиксируем в БД и кэше, затем пускаем хэндлер.
                db = data.get("db")
                if isinstance(db, Database):
                    try:
                        await db.accept_terms(user.id)
                    except Exception:  # noqa: BLE001
                        log.exception("Не удалось сохранить согласие")
                self._remember(user.id)
                return await handler(event, data)
            if action in {"lang:ru", "lang:en"}:
                return await handler(event, data)
        else:
            text = getattr(event, "text", "") or ""
            if text.startswith("/start"):
                return await handler(event, data)

        if user.id in self._accepted:
            self._accepted.move_to_end(user.id)
            return await handler(event, data)

        db = data.get("db")
        if isinstance(db, Database) and await db.has_accepted_terms(user.id):
            self._remember(user.id)
            return await handler(event, data)

        await self._show(event, data, user.id)
        return None

    def _remember(self, user_id: int) -> None:
        self._accepted[user_id] = None
        self._accepted.move_to_end(user_id)
        while len(self._accepted) > self._max:
            self._accepted.popitem(last=False)

    async def _show(self, event, data, user_id: int) -> None:
        language = await _language(data, user_id)
        text = (
            t(language, "terms_title")
            + "\n\n"
            + t(language, "terms_text", terms=settings.terms_link or "-")
        )
        markup = get_consent_menu(language)
        state = data.get("state")
        if state is not None:
            try:
                await state.clear()
            except Exception:  # noqa: BLE001
                pass
        if isinstance(event, CallbackQuery) or hasattr(event, "data"):
            await event.answer(t(language, "disclaimer_required"), show_alert=True)
            if event.message is not None:
                try:
                    await event.message.edit_text(text, reply_markup=markup)
                except TelegramBadRequest:
                    await event.message.answer(text, reply_markup=markup)
        else:
            await event.answer(text, reply_markup=markup)


class ForceSubscribeMiddleware(BaseMiddleware):
    """Только вызывает SubscriptionService.check; вся логика — в сервисе."""

    def __init__(self) -> None:
        self._last_shown: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        from app.services.subscription import service

        if not service.enabled:
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        if not user or user.is_bot or settings.is_admin(user.id):
            return await handler(event, data)

        bot = data.get("bot")
        if not bot:
            return await handler(event, data)

        result = await service.check(bot, user.id)
        if result.ok:
            return await handler(event, data)

        await self._ask(event, data, user.id, result)
        return None

    async def _ask(self, event, data, user_id: int, result) -> None:
        language = await _language(data, user_id)
        now = time.monotonic()
        if now - self._last_shown.get(user_id, 0.0) < 5.0:
            if isinstance(event, CallbackQuery):
                await event.answer()
            return
        self._last_shown[user_id] = now

        from app.keyboards import get_channels_menu

        if result.error in {"config", "temporary"}:
            text = t(language, "sub_check_unavailable")
            kb = None
        else:
            text = t(language, "sub_not_subscribed", channels=", ".join(
                c.title for c in (result.missing_channels or [])
            ) or "-")
            channels = [c.chat_id for c in (result.missing_channels or [])]
            links = {c.chat_id: c.link for c in (result.missing_channels or []) if c.link}
            kb = get_channels_menu(language, channels, links)

        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message is not None:
                try:
                    await event.message.edit_text(text, reply_markup=kb)
                except TelegramBadRequest:
                    await event.message.answer(text, reply_markup=kb)
        else:
            await event.answer(text, reply_markup=kb)

class BalanceMiddleware(BaseMiddleware):
    """Не пускает в поиск при нулевом балансе запросов.

    Списание делает сам хэндлер (db.spend_credit) только после успешного
    результата — неудачный поиск запросы не тратит. Здесь лишь дешёвая
    предпроверка, чтобы не гонять сеть без баланса.
    """

    async def __call__(self, handler, event, data):
        if isinstance(event, CallbackQuery):
            return await handler(event, data)
        user = getattr(event, "from_user", None)
        if user is None or settings.is_admin(user.id):
            return await handler(event, data)
        if (getattr(event, "text", "") or "").startswith("/"):
            return await handler(event, data)
        # Фото/файлы обрабатывает бесплатный раздел «Фото».
        if getattr(event, "photo", None) or getattr(event, "document", None):
            return await handler(event, data)
        db = data.get("db")
        if not isinstance(db, Database):
            return await handler(event, data)
        if await db.trial_available(user.id) or await db.get_credits(user.id) > 0:
            return await handler(event, data)
        language = await _language(data, user.id)
        await event.answer(t(language, "no_balance"), reply_markup=get_no_balance_menu(language))
        return None


class RateLimitMiddleware(BaseMiddleware):
    """Антифлуд: жёсткий для сообщений, мягкий для callback (D6)."""

    def __init__(
        self,
        seconds: float | None = None,
        cb_limit: int = 10,
        cb_window: float = 5.0,
    ):
        self._seconds = seconds if seconds is not None else settings.rate_limit_seconds
        self._last: dict[int, float] = {}
        self._cb_limit = cb_limit
        self._cb_window = cb_window
        self._cb_hits: dict[int, list[float]] = {}
        self._last_cleanup = time.monotonic()

    def _cleanup(self, now: float) -> None:
        if now - self._last_cleanup < 300:
            return
        cutoff = now - max(self._seconds * 10, 600)
        self._last = {uid: ts for uid, ts in self._last.items() if ts > cutoff}
        self._last_cleanup = now

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if isinstance(event, CallbackQuery):
            if user is None or settings.is_admin(user.id):
                return await handler(event, data)
            now = time.monotonic()
            hits = [t0 for t0 in self._cb_hits.get(user.id, []) if now - t0 < self._cb_window]
            if len(hits) >= self._cb_limit:
                self._cb_hits[user.id] = hits
                await event.answer(t("ru", "too_many", seconds=int(self._cb_window)), show_alert=False)
                return None
            hits.append(now)
            self._cb_hits[user.id] = hits
            return await handler(event, data)

        if user is None or settings.is_admin(user.id):
            return await handler(event, data)

        now = time.monotonic()
        self._cleanup(now)
        wait = self._seconds - (now - self._last.get(user.id, 0.0))
        if wait <= 0:
            self._last[user.id] = now
        if wait > 0:
            language = await _language(data, user.id)
            await event.answer(t(language, "too_many", seconds=int(wait) + 1))
            return None
        return await handler(event, data)