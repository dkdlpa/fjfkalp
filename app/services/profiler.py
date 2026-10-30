"""Лёгкий профилировщик асинхронных (и синхронных) задач.

Измеряет:
- wall time (perf_counter);
- CPU time процесса (process_time);
- изменение RSS через /proc/self/statm (дёшево);
- опционально tracemalloc, если PROFILE_TRACEMALLOC=1 (точнее, но дороже).

Если задача превышает пороги CPU/wall/RAM, пишется WARNING в лог.
Последние замеры хранятся в ограниченной deque — можно отдать в админку.

Важно: CPU-время процесса при параллельных корутинах включает работу соседних
задач. Для поиска профилей это приемлемо: важен факт превышения порога.
"""
from __future__ import annotations

import functools
import logging
import os
import time
import tracemalloc
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Callable

from app.config import settings

log = logging.getLogger("profiler")

_METRICS: deque[dict[str, Any]] = deque(maxlen=500)
_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def _rss_kb() -> int | None:
    """Текущий RSS процесса, КБ (Linux/Android)."""
    try:
        with open("/proc/self/statm", "rb") as file:
            return int(file.read().split()[1]) * _PAGE_SIZE // 1024
    except Exception:  # noqa: BLE001
        return None


def _memory_now() -> int | None:
    if settings.profile_tracemalloc:
        try:
            if not tracemalloc.is_tracing():
                tracemalloc.start()
            return tracemalloc.get_traced_memory()[0] // 1024
        except Exception:  # noqa: BLE001
            return None
    return _rss_kb()


def _finish(stats: dict[str, Any], wall0: float, cpu0: float, mem0: int | None, warn: bool = True) -> None:
    stats["wall_ms"] = round((time.perf_counter() - wall0) * 1000, 2)
    stats["cpu_ms"] = round((time.process_time() - cpu0) * 1000, 2)
    mem1 = _memory_now()
    if mem0 is not None and mem1 is not None:
        stats["memory_kb"] = mem1 - mem0
    else:
        stats["memory_kb"] = None

    warning = (
        stats["cpu_ms"] > settings.profile_cpu_warn_ms
        or stats["wall_ms"] > settings.profile_wall_warn_ms
        or (
            stats.get("memory_kb") is not None
            and stats["memory_kb"] > settings.profile_memory_warn_kb
        )
    )
    stats["warning"] = warning
    stats["warning"] = warning and warn
    _METRICS.append(dict(stats))
    if warning and warn:
        log.warning(
            "PROFILE %s: cpu=%.0fms wall=%.0fms mem=%sKB",
            stats.get("name", "?"),
            stats["cpu_ms"],
            stats["wall_ms"],
            stats.get("memory_kb"),
        )


@asynccontextmanager
async def profile(name: str, warn: bool = True):
    wall0 = time.perf_counter()
    cpu0 = time.process_time()
    mem0 = _memory_now()
    stats: dict[str, Any] = {"name": name, "memory_kb": None, "warning": False}
    try:
        yield stats
    finally:
        _finish(stats, wall0, cpu0, mem0, warn)


@contextmanager
def profile_sync(name: str, warn: bool = True):
    wall0 = time.perf_counter()
    cpu0 = time.process_time()
    mem0 = _memory_now()
    stats: dict[str, Any] = {"name": name, "memory_kb": None, "warning": False}
    try:
        yield stats
    finally:
        _finish(stats, wall0, cpu0, mem0, warn)


def profiled(name: str) -> Callable:
    """Декоратор для async-функций: логирует превышение порогов."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            async with profile(name):
                return await func(*args, **kwargs)

        return wrapper

    return decorator


def profiled_sync(name: str) -> Callable:
    """Декоратор для CPU-bound синхронных функций (например, phonenumbers)."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with profile_sync(name):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def get_metrics(limit: int = 50) -> list[dict[str, Any]]:
    """Последние замеры (для админки/диагностики)."""
    return list(_METRICS)[-limit:]


async def run_profiled(coro, name: str):
    """Утилита для одной корутины: измерить, дождаться, вернуть результат."""
    async with profile(name):
        return await coro


__all__ = [
    "get_metrics",
    "profile",
    "profile_sync",
    "profiled",
    "profiled_sync",
    "run_profiled",
]