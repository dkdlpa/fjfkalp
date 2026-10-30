"""Кэш результатов поиска — только в памяти процесса.

ВАЖНО: по требованиям ТЗ содержимое отчётов и результаты поисков НЕ должны
сохраняться в базе данных. Раньше этот модуль писал findings в таблицу
search_cache; теперь кэш живёт в RAM с TTL и исчезает при перезапуске.

Таблица search_cache в существующей БД не удаляется и не меняется — просто
новые записи в неё не пишутся. Пользовательские данные и статистика целы.

Ключ формируется из нормализованного значения и хэшируется sha256, поэтому
даже в памяти нет открытого PII в виде ключа.
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict

from app.config import settings
from app.db import Database

log = logging.getLogger(__name__)

TTL_SECONDS = 600  # 10 минут; переопределяется SEARCH_CACHE_TTL
_MAX_ENTRIES = 500

_memory: "OrderedDict[str, tuple[float, object]]" = OrderedDict()


def _normalize(kind: str, value: str) -> str:
    value = (value or "").strip()
    low = value.lower()
    if kind == "phone":
        digits = "".join(ch for ch in value if ch.isdigit())
        return f"+{digits}" if digits else low
    if kind in {"email", "username", "telegram"}:
        return low.lstrip("@")
    if kind == "ip":
        # домен или IP: убрать схему и путь
        if "://" in low:
            low = low.split("://", 1)[1]
        low = low.split("/", 1)[0].split("?", 1)[0]
        return low
    return low


def make_key(kind: str, value: str) -> str:
    raw = f"{kind}:{_normalize(kind, value)}"
    digest = hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()
    return f"{kind}:{digest}"


def _ttl() -> float:
    return float(settings.search_cache_ttl or TTL_SECONDS)


def clear() -> None:
    """Полная очистка кэша результатов (при старте/очистке админом)."""
    _memory.clear()


async def get(db: Database, key: str) -> object | None:  # noqa: ARG001 — сигнатура совместима
    entry = _memory.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _ttl():
        _memory.pop(key, None)
        return None
    _memory.move_to_end(key)
    return value


async def set(db: Database, key: str, value: object) -> None:  # noqa: ARG001
    _memory[key] = (time.monotonic(), value)
    _memory.move_to_end(key)
    while len(_memory) > _MAX_ENTRIES:
        _memory.popitem(last=False)


__all__ = ["clear", "get", "make_key", "set"]