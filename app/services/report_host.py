"""Временные HTML-отчёты по ссылке.

Как работает:
1. Отчёт уже собран в памяти.
2. Публикуем его в REPORTS_DIR под криптослучайным именем
   (<token>.html, token = 32 символа из secrets.token_urlsafe).
3. Отдаём пользователю ссылку REPORTS_PUBLIC_BASE/r/<token>.html.
4. Файл живёт REPORTS_TTL_MINUTES и удаляется фоновой задачей.
   Дополнительно доступно ручное удаление (кнопка «Удалить отчёт»).

Чего НЕ делает:
- не сохраняет отчёты в БД и не ведёт историю поисков;
- не создаёт резервных копий;
- не отдаёт листинг каталога;
- не пишет содержимое отчёта в логи.

Если REPORTS_WEB_ENABLED=0 или не задан публичный адрес, publish()
возвращает None — бот просто пришлёт файл документом, как раньше.

Встроенный мини-сервер (asyncio, без зависимостей) нужен для схемы
«Nginx -> 127.0.0.1:8089». Если Nginx отдаёт каталог напрямую, встроенный
сервер можно не включать (REPORTS_BIND_HOST=0.0.0.0 не требуется).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")
_SUFFIX = ".html"
_URL_PREFIX = "/r/"

# Ограничения мини-сервера: не держим много соединений и большие запросы.
_MAX_CONNECTIONS = 64
_ACQUIRE_TIMEOUT = 3.0
_MAX_REQUEST_LINE = 2048
_MAX_HEADER_BYTES = 8192
_REQUEST_TIMEOUT = 10.0
_owned_lock = asyncio.Lock()
_MAX_OWNED = 5000


@dataclass(frozen=True)
class Published:
    token: str
    path: Path
    url: str
    expires_at: float


# Отчёты, опубликованные текущей сессией: user_id -> Published.
# Нужно только для проверки права на кнопку «Удалить отчёт».
# В БД и на диск (кроме самого HTML) ничего не пишется.
_owned: "OrderedDict[int, Published]" = OrderedDict()


def register(user_id: int, published: Published) -> None:
    _owned[user_id] = published
    _owned.move_to_end(user_id)
    while len(_owned) > _MAX_OWNED:
        _owned.popitem(last=False)


def owned(user_id: int) -> Published | None:
    return _owned.get(user_id)


def forget(user_id: int) -> None:
    _owned.pop(user_id, None)


def _dir() -> Path:
    return Path(settings.reports_dir).resolve()


def _ensure_dir() -> Path:
    directory = _dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


def publish(html_bytes: bytes) -> Published | None:
    """Сохраняет отчёт и возвращает ссылку. None — если веб выключен."""
    if not settings.reports_web_ready:
        return None
    try:
        directory = _ensure_dir()
        token = secrets.token_urlsafe(24)  # 32 символа
        if not _TOKEN_RE.match(token):
            token = secrets.token_hex(16)
        path = directory / f"{token}{_SUFFIX}"
        # Атомарная запись: сначала temp, потом replace.
        tmp = directory / f".{token}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(html_bytes)
            os.replace(tmp, path)
        finally:
            with suppress(FileNotFoundError):
                tmp.unlink()
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        expires = time.time() + settings.reports_ttl_minutes * 60
        url = f"{settings.reports_public_base}{_URL_PREFIX}{token}{_SUFFIX}"
        return Published(token=token, path=path, url=url, expires_at=expires)
    except Exception:  # noqa: BLE001
        log.exception("Не удалось опубликовать отчёт")
        return None


def remove(token: str) -> bool:
    """Удаляет конкретный отчёт. Принимает только валидный токен."""
    if not _TOKEN_RE.match(token or ""):
        return False
    path = _dir() / f"{token}{_SUFFIX}"
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        log.warning("Не удалось удалить отчёт")
        return False


def prune() -> int:
    """Удаляет просроченные отчёты. Возвращает число удалённых файлов."""
    directory = _dir()
    if not directory.is_dir():
        return 0
    cutoff = time.time() - settings.reports_ttl_minutes * 60
    removed = 0
    entries: list[tuple[float, Path]] = []
    live_tokens: set[str] = set()
    for path in directory.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if not name.endswith(_SUFFIX):
            continue
        stem = name[: -len(_SUFFIX)]
        if not _TOKEN_RE.match(stem):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff:
            try:
                path.unlink()
                removed += 1
                continue
            except OSError:
                pass
        entries.append((stat.st_mtime, path))
        live_tokens.add(stem)

    # Не оставляем «сиротские» временные файлы после сбоев записи.
    for tmp in directory.glob(".*.tmp"):
        try:
            if time.time() - tmp.stat().st_mtime > 3600:
                tmp.unlink()
                removed += 1
        except OSError:
            pass

    # Реестр владельцев не должен расти: забываем удалённые/просроченные.
    for uid, published in list(_owned.items()):
        if published.token not in live_tokens:
            _owned.pop(uid, None)

    # Потолок числа файлов: удаляем самые старые сверх лимита.
    if len(entries) > settings.reports_max_files:
        entries.sort()
        for _, path in entries[: len(entries) - settings.reports_max_files]:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


async def prune_loop(interval: float = 60.0) -> None:
    while True:
        try:
            await asyncio.sleep(interval)
            removed = await asyncio.to_thread(prune)
            if removed:
                log.info("Удалено просроченных отчётов: %s", removed)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            log.exception("Ошибка очистки отчётов")


# --------------------------------------------------------------------------
# Встроенный мини-сервер (опционально)
# --------------------------------------------------------------------------
_SECURITY_HEADERS = (
    "Cache-Control: no-store, max-age=0\r\n"
    "X-Content-Type-Options: nosniff\r\n"
    "X-Frame-Options: DENY\r\n"
    "X-Robots-Tag: noindex, nofollow, noarchive\r\n"
    "Referrer-Policy: no-referrer\r\n"
)


def _resolve_request_path(raw_path: str) -> Path | None:
    """/r/<token>.html -> путь в REPORTS_DIR. Всё остальное — None."""
    path = raw_path.split("?", 1)[0]
    if not path.startswith(_URL_PREFIX):
        return None
    name = path[len(_URL_PREFIX):]
    if not name.endswith(_SUFFIX):
        return None
    stem = name[: -len(_SUFFIX)]
    if not _TOKEN_RE.match(stem):
        return None
    candidate = (_dir() / name).resolve()
    # Защита от path traversal: файл обязан лежать прямо в REPORTS_DIR.
    if candidate.parent != _dir():
        return None
    return candidate


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = await asyncio.wait_for(reader.readline(), timeout=_REQUEST_TIMEOUT)
        if not request or len(request) > _MAX_REQUEST_LINE:
            await _respond(writer, 400, b"bad request")
            return
        try:
            method, target, _ = request.decode("latin-1").split(" ", 2)
        except ValueError:
            await _respond(writer, 400, b"bad request")
            return
        # Дочитываем заголовки, чтобы клиент не ждал и соединение закрывалось чисто.
        drained = 0
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=_REQUEST_TIMEOUT)
            if not line or line in (b"\r\n", b"\n"):
                break
            drained += len(line)
            if drained > _MAX_HEADER_BYTES:
                await _respond(writer, 400, b"headers too large")
                return
        if method not in {"GET", "HEAD"}:
            await _respond(writer, 405, b"method not allowed")
            return
        path = _resolve_request_path(target)
        if path is None:
            await _respond(writer, 404, b"not found")
            return
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except (FileNotFoundError, OSError):
            await _respond(writer, 404, b"not found")
            return
        await _respond(writer, 200, data if method == "GET" else b"", head_only=method == "HEAD")
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    except Exception:  # noqa: BLE001
        log.debug("report server: внутренняя ошибка")
    finally:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=5)
        except Exception:  # noqa: BLE001
            pass


async def _respond(writer: asyncio.StreamWriter, status: int, body: bytes, head_only: bool = False) -> None:
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}.get(
        status, "OK"
    )
    headers = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        + _SECURITY_HEADERS
        + "Connection: close\r\n\r\n"
    )
    writer.write(headers.encode("latin-1"))
    if not head_only:
        writer.write(body)
    await writer.drain()


async def start_server() -> asyncio.AbstractServer | None:
    """Запускает мини-сервер, если он включён и публичный адрес задан."""
    if not settings.reports_web_ready:
        return None
    _ensure_dir()
    limit = asyncio.Semaphore(_MAX_CONNECTIONS)

    async def _guarded(reader, writer):
        # Всплеск запросов: короткая очередь вместо мгновенного обрыва.
        # Если слот не освободился за _ACQUIRE_TIMEOUT — закрываем соединение,
        # чтобы не копить задачи и память.
        try:
            await asyncio.wait_for(limit.acquire(), timeout=_ACQUIRE_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            await _handle(reader, writer)
        finally:
            limit.release()

    try:
        server = await asyncio.start_server(
            _guarded, settings.reports_bind_host, settings.reports_bind_port
        )
    except OSError as exc:
        log.error("Не удалось запустить сервер отчётов: %s", type(exc).__name__)
        return None
    log.info(
        "Сервер отчётов: http://%s:%s%s",
        settings.reports_bind_host,
        settings.reports_bind_port,
        _URL_PREFIX,
    )
    return server


__all__ = [
    "Published",
    "forget",
    "owned",
    "register",
    "prune",
    "prune_loop",
    "publish",
    "remove",
    "start_server",
]