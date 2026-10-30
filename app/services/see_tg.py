"""Клиент see.tg Public API (kartoshka.free) — Telegram-профили и подарки.

Документация: https://kartoshka.free/ (PDF «see.tg Public API»).

Правила, заложенные в модуль:
- токен только из окружения (SEE_TG_TOKEN), в коде и логах его нет;
- методы /ping и /me бесплатны, используются для диагностики;
- дорогие методы (/owners/search, /owner/{ref}/who, /gift/{ref}/who) стоят
  0.01 TON за запрос и не входят в бесплатную квоту — по умолчанию выключены
  (SEE_TG_ALLOW_EXPENSIVE=1 включает их осознанно);
- 402 (нет бесплатных запросов и баланса), 429 (лимит), 5xx и сетевые сбои
  для пользователя выглядят одинаково нейтрально: «технические работы».
  Тип ошибки, HTTP-код и метод уходят только во внутренний лог;
- никакого WebSocket, мониторинга, массового сбора и рыночной аналитики;
- pagination: первая страница + nextCursor, без автоматического обхода всех
  страниц (это была бы выгрузка базы).

Если данные показываются в отчёте, рядом обязательна подпись
«Данные: see.tg» со ссылкой на https://see.tg — это требование API.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.config import settings
from app.services.http import get_json

log = logging.getLogger(__name__)

SOURCE_NAME = "see.tg"
SOURCE_URL = "https://see.tg"

# Минимальный интервал между запросами (клиентская страховка от 429).
_MIN_INTERVAL = 0.35
_rate_lock = asyncio.Lock()
_last_call = 0.0

ATTRIBUTION = (
    f'Данные: <a href="{SOURCE_URL}" target="_blank" rel="noopener noreferrer">'
    f"{SOURCE_NAME}</a>"
)


class SeeTgError(RuntimeError):
    """Ошибка API. kind определяет, что показать пользователю."""

    def __init__(self, kind: str, *, status: int | None = None, method: str = ""):
        super().__init__(kind)
        self.kind = kind
        self.status = status
        self.method = method


# kind -> ключ текста для пользователя
#   not_found / invalid_input — обычные пользовательские ответы;
#   temporary — всё остальное (включая 402/429/5xx), нейтральный текст.
def user_key(error: SeeTgError) -> str:
    if error.kind == "not_found":
        return "not_found"
    if error.kind == "invalid_input":
        return "invalid_input"
    if error.kind == "not_configured":
        return "source_not_configured"
    if error.kind == "expensive_disabled":
        return "see_tg_expensive_disabled"
    return "source_maintenance"


async def _throttle() -> None:
    global _last_call
    async with _rate_lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL - (now - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.monotonic()


def _map_status(status: int) -> str:
    if status == 404:
        return "not_found"
    if status == 400:
        return "invalid_input"
    if status == 401:
        return "auth_error"
    if status == 402:
        return "quota"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server_error"
    return "http_error"


async def _call(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    expensive: bool = False,
    ttl: int = 0,
) -> Any:
    """GET /v1/<method> -> result. Бросает SeeTgError.

    ttl=0 отключает кэш httpx-клиента: данные о подарках меняются, а платный
    запрос не должен «исчезать» из-за кэша (мы всё равно платим за вызов).
    """
    if not settings.see_tg_ready:
        raise SeeTgError("not_configured", method=method)
    if expensive and not settings.see_tg_expensive:
        raise SeeTgError("expensive_disabled", method=method)

    url = f"{settings.see_tg_base_url}/{method.lstrip('/')}"
    headers = {"Authorization": f"Bearer {settings.see_tg_token}"}

    await _throttle()
    data, error = await get_json(
        url,
        params={k: v for k, v in (params or {}).items() if v not in (None, "")},
        headers=headers,
        ttl=ttl,
        timeout=settings.see_tg_timeout,
    )

    if error:
        status: int | None = None
        if error.startswith("http_"):
            head = error.split(":", 1)[0]
            try:
                status = int(head.split("_", 1)[1])
            except (IndexError, ValueError):
                status = None
        if status is not None:
            kind = _map_status(status)
        elif error in {"timeout", "network", "not_json"}:
            kind = "temporary"
        else:
            kind = "temporary"
        # Внутренний лог: метод, HTTP-код, тип ошибки. Без токена и тела запроса.
        log.warning(
            "see.tg %s: kind=%s status=%s method=%s",
            kind,
            kind,
            status,
            method,
        )
        if status == 429 and isinstance(data, dict):
            pass
        raise SeeTgError(kind, status=status, method=method)

    if isinstance(data, dict) and data.get("ok") is False:
        code = data.get("error_code")
        try:
            status = int(code) if code is not None else None
        except (TypeError, ValueError):
            status = None
        kind = _map_status(status) if status else "temporary"
        log.warning("see.tg ok=false: kind=%s status=%s method=%s", kind, status, method)
        raise SeeTgError(kind, status=status, method=method)

    if isinstance(data, dict) and "result" in data:
        return data["result"]
    return data


# --------------------------------------------------------------------------
# Публичные методы API
# --------------------------------------------------------------------------
async def ping() -> dict:
    return _as_dict(await _call("ping", ttl=0))


async def me() -> dict:
    return _as_dict(await _call("me", ttl=0))


async def resolve(ref: str) -> dict:
    result = _as_dict(await _call("resolve", {"ref": ref}, ttl=30))
    result.setdefault("type", "owner" if result.get("owner") else "unknown")
    return result


async def owner(ref: str) -> dict:
    return _as_dict(await _call(f"owner/{ref}", ttl=30))


async def owner_gifts(
    ref: str, *, limit: int | None = None, cursor: str | None = None, on_sale: bool = False
) -> dict:
    params = {
        "limit": min(100, limit or settings.see_tg_page_size),
        "cursor": cursor,
    }
    if on_sale:
        params["on_sale"] = "true"
    return _as_dict(await _call(f"owner/{ref}/gifts", params, ttl=30))


async def owner_history(ref: str, *, limit: int | None = None, cursor: str | None = None) -> dict:
    params = {"limit": min(100, limit or settings.see_tg_page_size), "cursor": cursor}
    return _as_dict(await _call(f"owner/{ref}/history", params, ttl=30))


async def gift(ref: str) -> dict:
    return _as_dict(await _call(f"gift/{ref}", ttl=30))


async def gift_history(ref: str, *, limit: int | None = None, cursor: str | None = None) -> dict:
    params = {"limit": min(100, limit or settings.see_tg_page_size), "cursor": cursor}
    return _as_dict(await _call(f"gift/{ref}/history", params, ttl=30))


async def gifts_search(values: dict[str, str], *, limit: int | None = None, cursor: str | None = None) -> dict:
    """Поиск подарков с фильтрами (тот же набор, что «Найти» в see.tg)."""
    params = {k: v for k, v in values.items() if v not in (None, "")}
    params["limit"] = min(100, limit or settings.see_tg_page_size)
    if cursor:
        params["cursor"] = cursor
    return _as_dict(await _call("gifts", params, ttl=30))


async def owners_search(query: str, *, limit: int | None = None, cursor: str | None = None) -> dict:
    """ДОРОГОЙ метод (0.01 TON, вне бесплатной квоты). Выключен по умолчанию."""
    params = {
        "q": query,
        "limit": min(100, limit or settings.see_tg_page_size),
        "cursor": cursor,
    }
    return _as_dict(await _call("owners/search", params, expensive=True, ttl=30))


async def owner_who(ref: str, *, gift: str | None = None) -> dict:
    """ДОРОГОЙ метод. Поиск возможных владельцев скрытого профиля."""
    params = {"gift": gift} if gift else None
    return _as_dict(await _call(f"owner/{ref}/who", params, expensive=True, ttl=30))


# --------------------------------------------------------------------------
# Разбор ответов (без выдумывания полей)
# --------------------------------------------------------------------------
def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value}
    return {}


def items_of(payload: dict) -> list[dict]:
    """Достаёт список записей из ответа с пагинацией."""
    for key in ("items", "gifts", "owners", "events", "history", "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)]
    if isinstance(payload.get("gift"), dict):
        return [payload["gift"]]
    return []


def next_cursor(payload: dict) -> str | None:
    for key in ("nextCursor", "next_cursor", "cursor"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def total_of(payload: dict) -> int | None:
    for key in ("total", "count"):
        value = payload.get(key)
        if isinstance(value, int):
            return value
    return None


def nano_to_ton(value: Any) -> float | None:
    """gram/нанотоны -> TON. В API суммы приходят в минимальных единицах."""
    try:
        return int(value) / 1_000_000_000
    except (TypeError, ValueError):
        return None


def fmt_ton(value: Any) -> str:
    ton = nano_to_ton(value)
    if ton is None:
        return "—"
    if ton >= 1000:
        return f"{ton:,.0f} TON".replace(",", " ")
    if ton >= 1:
        return f"{ton:,.2f} TON".replace(",", " ")
    return f"{ton:.4f} TON"


def gift_title(gift: dict) -> str:
    """Человекочитаемое имя подарка из известных полей."""
    parts = []
    title = gift.get("title") or gift.get("name") or gift.get("slug")
    if title:
        parts.append(str(title))
    elif gift.get("collection"):
        parts.append(str(gift["collection"]))
    num = gift.get("num") or gift.get("number")
    if num not in (None, ""):
        parts.append(f"#{num}")
    return " ".join(parts) or "—"


__all__ = [
    "ATTRIBUTION",
    "SOURCE_NAME",
    "SOURCE_URL",
    "SeeTgError",
    "fmt_ton",
    "gift",
    "gift_history",
    "gift_title",
    "gifts_search",
    "items_of",
    "me",
    "nano_to_ton",
    "next_cursor",
    "owner",
    "owner_gifts",
    "owner_history",
    "owner_who",
    "owners_search",
    "ping",
    "resolve",
    "total_of",
    "user_key",
]