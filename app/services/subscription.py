"""Проверка обязательной подписки на каналы-спонсоры.

Ключевые принципы (см. ТЗ):
- Подписка проверяется только через bot.get_chat_member; это возможно, лишь
  если бот администратор канала.
- Ошибка настройки (chat not found, CHAT_ADMIN_REQUIRED, ...) НЕ означает
  "пользователь не подписан": это отдельный класс, который ведёт себя по
  SUBSCRIPTION_ON_ERROR (closed | grace).
- Кэшируется только успех; TTL из SUBSCRIPTION_CACHE_TTL.
- Каналы проверяются параллельно с таймаутом и общим семафором.
- На одного пользователя одновременно идёт не более одной проверки.
- В логи не попадают токены, ID каналов и ссылки: только название и тип.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner

from app.config import Channel, settings

log = logging.getLogger(__name__)

_SUBSCRIBED = {"member", "administrator", "creator"}
# Ошибки, означающие "человека нет в канале", а не сбой настройки.
_NOT_MEMBER_HINTS = ("user not found", "participant_id_invalid")
# Ошибки настройки канала (бот не админ / неверный ID / приватность).
_CONFIG_HINTS = (
    "chat not found",
    "member list is inaccessible",
    "chat_admin_required",
    "bot is not a member",
    "not enough rights",
    "bots can't",
    "chat_member",
)


@dataclass
class ChannelResult:
    channel: Channel
    ok: bool = False
    error: str | None = None      # "not_member" | "config" | "temporary"
    detail: str = ""


@dataclass
class SubResult:
    ok: bool
    missing: list[str] = field(default_factory=list)
    error: str | None = None      # "config" | "temporary" | None
    stale_ok: bool = False
    missing_channels: list[Channel] = field(default_factory=list)


@dataclass
class ChannelDiag:
    title: str
    chat_id_ok: bool
    found: bool
    bot_is_admin: bool
    members: int | None
    admin_check_ok: bool
    note: str


def _classify_exception(exc: BaseException) -> str:
    """'config' | 'temporary' | 'not_member' для исключения Telegram."""
    text = str(exc).lower()
    if any(hint in text for hint in _NOT_MEMBER_HINTS):
        return "not_member"
    if any(hint in text for hint in _CONFIG_HINTS):
        return "config"
    if isinstance(exc, TelegramRetryAfter):
        return "temporary"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return "temporary"
    # Forbidden и прочие API-ошибки прав трактуем как ошибку настройки.
    name = type(exc).__name__.lower()
    if name in {"forbidden", "telegrambiddenerror"}:
        return "config"
    if "forbidden" in name:
        return "config"
    return "temporary"


def _is_member(member) -> bool | None:
    """True — подписан, False — нет, None — не удалось определить."""
    if member is None:
        return None
    status = getattr(member, "status", None)
    if status in _SUBSCRIBED:
        return True
    if status == "restricted":
        return getattr(member, "is_member", False) is True
    if status in {"left", "kicked"}:
        return False
    return False


class SubscriptionService:
    def __init__(self) -> None:
        self._channels: tuple[Channel, ...] = settings.sponsor_channels
        self._links: dict[str, str] = {
            channel.chat_id: channel.link for channel in self._channels if channel.link
        }
        self._cache: "OrderedDict[int, float]" = OrderedDict()
        self._locks: dict[int, asyncio.Lock] = {}
        self._sem = asyncio.Semaphore(10)
        self._last_config_alert: dict[str, float] = {}
        self._temp_errors: list[float] = []
        self._last_temp_alert = 0.0

    # ---------- публичный API ----------
    @property
    def channels(self) -> tuple[Channel, ...]:
        return self._channels

    @property
    def enabled(self) -> bool:
        return bool(self._channels)

    def channel_label(self, chat_id: str) -> str:
        for channel in self._channels:
            if channel.chat_id == chat_id:
                return channel.title
        return "Канал"

    def link_for(self, chat_id: str) -> str:
        return self._links.get(chat_id, "")

    async def check(self, bot, user_id: int, force: bool = False) -> SubResult:
        if not self._channels:
            return SubResult(ok=True)

        if not force:
            cached = self._cache.get(user_id)
            if cached is not None:
                if time.monotonic() - cached < settings.subscription_cache_ttl:
                    self._cache.move_to_end(user_id)
                    return SubResult(ok=True)

        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            # Пока ждали, могла пройти проверка другого апдейта.
            if not force:
                cached = self._cache.get(user_id)
                if cached is not None and time.monotonic() - cached < settings.subscription_cache_ttl:
                    return SubResult(ok=True)
            result = await self._check_all(bot, user_id)
        self._locks.pop(user_id, None)
        return result

    def set_member_status(self, user_id: int, status: str | None) -> None:
        """Обновление из chat_member: left/kicked — сброс кэша, member — успех."""
        if status in {"member", "administrator", "creator"}:
            self._remember(user_id)
        elif status in {"left", "kicked"}:
            self._cache.pop(user_id, None)
        elif status == "restricted":
            # Без is_member точного ответа нет — просто сбрасываем, перепроверим.
            self._cache.pop(user_id, None)

    async def self_check(self, bot) -> list[ChannelDiag]:
        diags: list[ChannelDiag] = []
        me = await bot.get_me()
        for channel in self._channels:
            diag = ChannelDiag(
                title=channel.title,
                chat_id_ok=channel.chat_id.startswith("-100") and channel.chat_id[1:].isdigit(),
                found=False,
                bot_is_admin=False,
                members=None,
                admin_check_ok=False,
                note="",
            )
            try:
                chat = await bot.get_chat(channel.chat_id)
                diag.found = True
                diag.members = getattr(chat, "member_count", None)
            except Exception as exc:  # noqa: BLE001
                diag.note = "канал не найден или бот не в нём"
                log.error(
                    "Самопроверка канала %s: канал недоступен (%s)",
                    channel.title,
                    type(exc).__name__,
                )
                diags.append(diag)
                continue
            try:
                member = await bot.get_chat_member(channel.chat_id, me.id)
                diag.admin_check_ok = True
                if isinstance(member, (ChatMemberAdministrator, ChatMemberOwner)) or getattr(
                    member, "status", None) in {"administrator", "creator"}:
                    diag.bot_is_admin = True
                    diag.note = "ок"
                else:
                    diag.note = "бот не администратор канала"
                    log.error("Самопроверка канала %s: бот не админ", channel.title)
            except Exception as exc:  # noqa: BLE001
                diag.note = "нет прав проверить бота в канале"
                log.error(
                    "Самопроверка канала %s: нет доступа (%s)",
                    channel.title,
                    type(exc).__name__,
                )
            diags.append(diag)
        return diags

    # ---------- внутреннее ----------
    def _remember(self, user_id: int) -> None:
        self._cache[user_id] = time.monotonic()
        self._cache.move_to_end(user_id)
        while len(self._cache) > 50_000:
            self._cache.popitem(last=False)

    async def _check_channel(self, bot, channel: Channel, user_id: int) -> ChannelResult:
        async with self._sem:
            for attempt in range(2):
                try:
                    member = await asyncio.wait_for(
                        bot.get_chat_member(channel.chat_id, user_id),
                        timeout=settings.subscription_check_timeout,
                    )
                    subscribed = _is_member(member)
                    if subscribed:
                        return ChannelResult(channel, ok=True)
                    return ChannelResult(channel, error="not_member")
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    kind = "temporary"
                    detail = "timeout"
                except TelegramRetryAfter as exc:
                    kind = "temporary"
                    detail = "retry_after"
                    if attempt == 0:
                        await asyncio.sleep(min(max(1.0, exc.retry_after), 3.0))
                        continue
                except Exception as exc:  # noqa: BLE001
                    kind = _classify_exception(exc)
                    detail = type(exc).__name__
                    if kind in {"config", "not_member"}:
                        return ChannelResult(channel, error=kind, detail=detail)
                if attempt == 0:
                    await asyncio.sleep(1.0)
                    continue
                return ChannelResult(channel, error=kind, detail=detail)
        return ChannelResult(channel, error="temporary", detail="exhausted")

    async def _check_all(self, bot, user_id: int) -> SubResult:
        results = await asyncio.gather(
            *(self._check_channel(bot, channel, user_id) for channel in self._channels),
            return_exceptions=True,
        )
        missing: list[Channel] = []
        config_channels: list[Channel] = []
        temp_failed = False
        for channel, res in zip(self._channels, results):
            if isinstance(res, BaseException):
                temp_failed = True
                continue
            if res.ok:
                continue
            if res.error == "not_member":
                missing.append(channel)
            elif res.error == "config":
                config_channels.append(channel)
                log.error(
                    "Проверка подписки: канал «%s» настроен неверно (%s)",
                    channel.title,
                    res.detail,
                )
            else:
                temp_failed = True
                log.warning(
                    "Проверка подписки: канал «%s» временно недоступен (%s)",
                    channel.title,
                    res.detail,
                )

        if missing:
            # Явная отписка: доступа нет, показываем именно эти каналы.
            self._cache.pop(user_id, None)
            return SubResult(
                ok=False,
                error=None,
                missing=[c.chat_id for c in missing],
                missing_channels=missing,
            )

        if config_channels:
            await self._alert_config(config_channels)
            return await self._on_soft_error(user_id, "config")

        if temp_failed:
            await self._maybe_alert_temp()
            return await self._on_soft_error(user_id, "temporary")

        self._remember(user_id)
        return SubResult(ok=True)

    async def _on_soft_error(self, user_id: int, kind: str) -> SubResult:
        if settings.subscription_on_error == "closed":
            return SubResult(ok=False, error=kind)
        # grace: пускаем недавно проверенных
        cached = self._cache.get(user_id)
        if cached is not None and time.monotonic() - cached < settings.subscription_grace_seconds:
            return SubResult(ok=True, stale_ok=True, error=kind)
        return SubResult(ok=False, error=kind)

    async def _alert_config(self, channels: list[Channel]) -> None:
        now = time.monotonic()
        for channel in channels:
            last = self._last_config_alert.get(channel.chat_id, 0.0)
            if now - last < 1800:
                continue
            self._last_config_alert[channel.chat_id] = now
            await _send_admins(
                f"Проверка подписки: канал «{channel.title}» настроен неверно. "
                "Бот должен быть администратором канала, а ID должен быть верным. "
                "Проверьте ID и права бота."
            )

    async def _maybe_alert_temp(self) -> None:
        now = time.monotonic()
        self._temp_errors = [ts for ts in self._temp_errors if now - ts < 60]
        self._temp_errors.append(now)
        if len(self._temp_errors) <= 5:
            return
        if now - self._last_temp_alert < 1800:
            return
        self._last_temp_alert = now
        await _send_admins(
            "Проверка подписки: повторяющиеся временные ошибки Telegram. "
            "Проверьте сеть и доступность API."
        )


async def _send_admins(text: str) -> None:
    from app.services import bot_ref  # ленивый импорт, без цикла

    bot = bot_ref.get()
    if bot is None:
        return
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001
            pass


service = SubscriptionService()