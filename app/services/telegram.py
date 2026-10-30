"""Telegram OSINT через see.tg Public API (kartoshka.free).

Полная замена FunStat. Источник данных — только официальный REST API see.tg,
без WebSocket и без обхода приватности.

Возможности (в рамках методов документации):
- профиль владельца по @username / t.me-ссылке / ID / TON-адресу
  (через /resolve и /owner/{ref});
- подарки владельца (/owner/{ref}/gifts) и история профиля
  (/owner/{ref}/history);
- карточка конкретного подарка (/gift/{ref}) и его история
  (/gift/{ref}/history);
- поиск подарков (/gifts) простыми фильтрами;
- отдельный поиск по TON-адресу (/resolve + /owner/{ref}).

Дорогие методы (/owners/search, /owner/{ref}/who, /gift/{ref}/who) не
вызываются автоматически: они стоят 0.01 TON и не входят в бесплатную квоту.

Ошибки маппятся в те же коды, что уже понимает конвейер:
    not_found / invalid_input / source_not_configured / unavailable.
Всё, что связано с квотой/лимитом/сбоем источника, — «unavailable»
(пользователь видит нейтральный текст).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.config import settings
from app.services import see_tg
from app.services.see_tg import SeeTgError
from app.validators import parse_telegram

log = logging.getLogger(__name__)

# Уведомление админа об ошибке конфигурации источника (регистрируется в main).
# Не чаще раза в час, без токенов и пользовательских данных в тексте.
_ADMIN_NOTIFIER = None
_LAST_ALERT = 0.0
_ALERT_INTERVAL = 3600.0


def set_admin_notifier(func) -> None:
    global _ADMIN_NOTIFIER
    _ADMIN_NOTIFIER = func


async def _alert_admin(text: str) -> None:
    global _LAST_ALERT
    if _ADMIN_NOTIFIER is None:
        return
    now = time.monotonic()
    if now - _LAST_ALERT < _ALERT_INTERVAL:
        return
    _LAST_ALERT = now
    try:
        await _ADMIN_NOTIFIER(text)
    except Exception:  # noqa: BLE001
        pass


_TON_RE = re.compile(r"^(?:ton://)?(?:EQ|UQ|Ef|Uf)[A-Za-z0-9_\-]{40,60}$")
_TON_RAW_RE = re.compile(r"^-?\d{1,20}:[A-Za-z0-9_\-]{40,70}$")


def _map_error(error: SeeTgError) -> str:
    if error.kind == "not_configured":
        return "source_not_configured"
    if error.kind == "invalid_input":
        return "invalid_input"
    if error.kind == "not_found":
        return "not_found"
    return "unavailable"


def is_ton_address(value: str) -> bool:
    value = (value or "").strip()
    return bool(_TON_RE.match(value) or _TON_RAW_RE.match(value))


def _empty(target: str) -> dict:
    return {
        "target": target,
        "error": None,
        "profile": {},
        "gifts": {},
        "history": {},
        "notes": [],
        "hidden": False,
        "source": "see.tg",
    }


def _collect_items(payload: dict) -> tuple[list[dict], str | None, int | None]:
    return (
        see_tg.items_of(payload),
        see_tg.next_cursor(payload),
        see_tg.total_of(payload),
    )


async def _resolve_ref(target: str) -> tuple[str, str]:
    """Возвращает (kind, ref). kind: 'owner' | 'gift'."""
    target = (target or "").strip()
    if not target:
        raise SeeTgError("invalid_input", method="resolve")

    # Уже похоже на внутреннюю ссылку see.tg/ref — отдаём как есть.
    if is_ton_address(target):
        return "owner", target

    parsed = parse_telegram(target)
    ref = target
    if parsed:
        _, ref = parsed

    resolved = await see_tg.resolve(ref)
    rtype = str(resolved.get("type") or "").lower()
    if rtype == "gift":
        gift = resolved.get("gift") or {}
        gift_ref = gift.get("ref") or gift.get("id") or ref
        return "gift", str(gift_ref)
    if rtype == "owner":
        owner = resolved.get("owner") or {}
        owner_ref = owner.get("ref") or owner.get("see_id") or owner.get("id") or ref
        return "owner", str(owner_ref)
    raise SeeTgError("not_found", method="resolve")


def _profile_view(payload: dict) -> dict:
    """Нормализует карточку владельца, не добавляя отсутствующих полей."""
    if not isinstance(payload, dict):
        return {}
    profile = dict(payload.get("owner") or payload.get("profile") or payload)
    # Имена могут приходить списком.
    names = profile.get("names")
    if isinstance(names, list) and names and not profile.get("name"):
        first = names[0]
        if isinstance(first, dict):
            profile["name"] = first.get("name")
        elif isinstance(first, str):
            profile["name"] = first
    usernames = profile.get("usernames")
    if isinstance(usernames, list):
        flattened = []
        for item in usernames:
            if isinstance(item, dict) and item.get("username"):
                flattened.append(str(item["username"]).lstrip("@"))
            elif isinstance(item, str) and item:
                flattened.append(item.lstrip("@"))
        profile["usernames"] = flattened
        if flattened and not profile.get("username"):
            profile["username"] = flattened[0]
    return profile


async def analyze(target: str) -> dict:
    """Профиль + подарки + история одного владельца (или карточка подарка)."""
    result = _empty(target)
    if not settings.see_tg_ready:
        result["error"] = "source_not_configured"
        return result

    try:
        kind, ref = await _resolve_ref(target)
    except SeeTgError as exc:
        if exc.kind in {"auth_error", "quota", "rate_limited", "server_error"}:
            await _alert_admin(
                f"see.tg: ошибка источника ({exc.kind}, HTTP {exc.status}). "
                "Проверьте токен и баланс API."
            )
        result["error"] = _map_error(exc)
        return result

    try:
        if kind == "gift":
            gift = await see_tg.gift(ref)
            result["gift"] = gift.get("gift") or gift
            try:
                history = await see_tg.gift_history(ref)
                items, cursor, total = _collect_items(history)
                result["gift_history"] = {"items": items, "next": cursor, "total": total}
            except SeeTgError:
                result["gift_history"] = {"items": []}
            result["kind"] = "gift"
            return result

        owner = await see_tg.owner(ref)
        result["profile"] = _profile_view(owner)
        if owner.get("hiddenByOwner"):
            result["hidden"] = True

        gifts_error = history_error = None
        try:
            gifts = await see_tg.owner_gifts(ref)
            items, cursor, total = _collect_items(gifts)
            result["gifts"] = {"items": items, "next": cursor, "total": total}
            if gifts.get("hiddenByOwner"):
                result["hidden"] = True
        except SeeTgError as exc:
            gifts_error = exc.kind

        try:
            history = await see_tg.owner_history(ref)
            items, cursor, total = _collect_items(history)
            result["history"] = {"items": items, "next": cursor, "total": total}
            if history.get("hiddenByOwner"):
                result["hidden"] = True
        except SeeTgError as exc:
            history_error = exc.kind

        if gifts_error and history_error and not result["profile"]:
            result["error"] = "unavailable"
            return result

        if result["hidden"]:
            result["notes"].append(
                "Владелец скрыл историю профиля — события возвращаются "
                "замаскированными или пустыми."
            )
        result["kind"] = "owner"
        return result
    except SeeTgError as exc:
        result["error"] = _map_error(exc)
        return result


async def analyze_ton(target: str) -> dict:
    """Отдельный поиск по TON-адресу. Не требует Telegram-профиля."""
    result = {
        "target": target,
        "error": None,
        "wallet": {},
        "profile": {},
        "notes": [],
        "source": "see.tg",
    }
    if not settings.see_tg_ready:
        result["error"] = "source_not_configured"
        return result
    if not is_ton_address(target):
        result["error"] = "invalid_input"
        return result

    try:
        resolved = await see_tg.resolve(target.strip())
    except SeeTgError as exc:
        result["error"] = _map_error(exc)
        return result

    wallet: dict[str, Any] = {}
    resolved_type = str(resolved.get("type") or "").lower()
    payload = resolved.get("wallet") or resolved.get("owner") or {}
    if isinstance(payload, dict):
        wallet = dict(payload)
    wallet.setdefault("type", resolved_type or "wallet")

    # Дополнительно: если у кошелька есть публичный владелец — покажем профиль.
    ref = wallet.get("ref") or wallet.get("see_id") or wallet.get("owner_id")
    if ref:
        try:
            owner = await see_tg.owner(str(ref))
            result["profile"] = _profile_view(owner)
        except SeeTgError:
            pass

    result["wallet"] = wallet
    result["notes"].append(
        "Показаны только сведения, которые возвращает API see.tg. "
        "Полная история блокчейна и баланс могут быть недоступны."
    )
    return result


async def find_gift(query: str) -> dict:
    """Поиск конкретного подарка: Slug-N или ссылка."""
    result = {
        "target": query,
        "error": None,
        "gift": {},
        "gift_history": {"items": []},
        "notes": [],
        "source": "see.tg",
        "kind": "gift",
    }
    if not settings.see_tg_ready:
        result["error"] = "source_not_configured"
        return result

    value = (query or "").strip()
    if not value:
        result["error"] = "invalid_input"
        return result

    # Slug-N: разберём на части и найдём через /gifts.
    slug, num = None, None
    match = re.fullmatch(r"([A-Za-z0-9_]+)-(\d{1,9})", value)
    if match:
        slug, num = match.group(1), match.group(2)

    try:
        if slug and num:
            found = await see_tg.gifts_search({"slug": slug, "num": num}, limit=10)
            items = see_tg.items_of(found)
            if not items:
                result["error"] = "not_found"
                return result
            gift = items[0]
        else:
            gift = await see_tg.gift(value)
            gift = gift.get("gift") or gift
        result["gift"] = gift
        ref = gift.get("ref") or gift.get("id") or value
        try:
            history = await see_tg.gift_history(str(ref))
            items, cursor, total = _collect_items(history)
            result["gift_history"] = {"items": items, "next": cursor, "total": total}
        except SeeTgError:
            pass
        return result
    except SeeTgError as exc:
        result["error"] = _map_error(exc)
        return result


async def search_gifts(filters: dict[str, str]) -> dict:
    """Поиск подарков по фильтрам (/v1/gifts). Только одна страница."""
    result = {
        "target": ", ".join(f"{k}={v}" for k, v in filters.items() if v),
        "error": None,
        "gifts": {},
        "notes": [],
        "source": "see.tg",
        "kind": "gifts",
    }
    if not settings.see_tg_ready:
        result["error"] = "source_not_configured"
        return result
    try:
        payload = await see_tg.gifts_search(filters)
        items, cursor, total = _collect_items(payload)
        result["gifts"] = {"items": items, "next": cursor, "total": total}
        return result
    except SeeTgError as exc:
        result["error"] = _map_error(exc)
        return result


__all__ = [
    "analyze",
    "analyze_ton",
    "find_gift",
    "is_ton_address",
    "search_gifts",
]