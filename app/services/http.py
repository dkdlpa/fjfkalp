"""Единый асинхронный HTTP-клиент: пул соединений, DNS-кэш и лимиты.

Оптимизации под CPU/RAM:
- один переиспользуемый AsyncClient (keep-alive, общий пул соединений);
- DNS-кэш на 300 секунд (ttl_dns_cache=300): повторные запросы к тому же
  хосту не вызывают системный резолвер;
- глобальный asyncio.Semaphore(15) ограничивает число одновременных
  сетевых запросов, чтобы не плодить тысячи сокетов и задач;
- потоковое чтение с жёстким лимитом байт: парсеры получают максимум
  первые 16 КБ страницы и не тянут в RAM многомегабайтные ответы;
- OrderedDict-кэш: O(1) вставка/вытеснение;
- in-flight coalescing: одинаковые параллельные запросы ждут один вызов.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import ipaddress
import json
import logging
import socket
import time
from collections import OrderedDict

import httpx

from app.config import settings

log = logging.getLogger(__name__)

# HTTP/2 включаем только если установлен пакет h2 (httpx[http2]).
_HTTP2_AVAILABLE = importlib.util.find_spec("h2") is not None

# Аналог aiohttp ttl_dns_cache=300 для httpx: собственный кэш резолвера.
DNS_TTL = 300
MAX_PARALLEL_REQUESTS = 15  # совместимость; фактический лимит — settings
MAX_BODY_BYTES = 2 * 1024 * 1024

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()
_requests = asyncio.Semaphore(max(1, settings.max_outbound_concurrency))

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

_cache: OrderedDict[str, tuple[float, object]] = OrderedDict()
_cache_max = 64
_inflight: dict[str, asyncio.Future] = {}

_dns_cache: dict[str, tuple[float, str]] = {}
_dns_lock = asyncio.Lock()


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def _resolve_cached(host: str, port: int) -> str | None:
    """Резолвит хост и держит результат в памяти DNS_TTL секунд."""
    now = time.monotonic()
    cached = _dns_cache.get(host)
    if cached and now - cached[0] < DNS_TTL:
        return cached[1]

    async with _dns_lock:
        now = time.monotonic()
        cached = _dns_cache.get(host)
        if cached and now - cached[0] < DNS_TTL:
            return cached[1]
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("DNS resolve failed for %s: %s", host, type(exc).__name__)
            return None
        ipv4: str | None = None
        fallback: str | None = None
        for info in infos:
            try:
                addr = info[4][0]
                ipaddress.ip_address(addr)
            except (ValueError, IndexError):
                continue
            if info[0] == socket.AF_INET:
                ipv4 = addr
                break
            if fallback is None:
                fallback = addr
        ip = ipv4 or fallback
        if ip:
            _dns_cache[host] = (now, ip)
            if len(_dns_cache) > 256:
                _dns_cache.pop(next(iter(_dns_cache)))
        return ip


class _CachedDNSTransport(httpx.AsyncHTTPTransport):
    """Транспорт httpx с кэшированием DNS и сохранением SNI/Host.

    httpx не умеет ttl_dns_cache из aiohttp, поэтому резолвим сами и
    подменяем только host в URL. Заголовок Host и SNI-имя остаются
    исходными — TLS-сертификат проверяется для настоящего домена.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        host = url.host
        if host and not _is_ip(host) and url.scheme in {"http", "https"}:
            port = url.port or (443 if url.scheme == "https" else 80)
            ip = await _resolve_cached(host, port)
            if ip:
                try:
                    default_port = (url.scheme == "http" and port == 80) or (
                        url.scheme == "https" and port == 443
                    )
                    if "host" not in request.headers:
                        request.headers["Host"] = host if default_port else f"{host}:{port}"
                    request.url = url.copy_with(host=ip)
                    request.extensions["sni_hostname"] = host
                except Exception:  # noqa: BLE001 — при сбое идём обычным путём
                    request.url = url
        return await super().handle_async_request(request)


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                limits = httpx.Limits(
                    max_connections=max(1, settings.http_max_connections),
                    max_keepalive_connections=max(
                        1, settings.http_keepalive_connections
                    ),
                    keepalive_expiry=30.0,
                )
                _client = httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        connect=settings.http_connect_timeout,
                        read=settings.http_read_timeout,
                        write=settings.http_read_timeout,
                        pool=settings.http_connect_timeout,
                    ),
                    headers=_DEFAULT_HEADERS,
                    follow_redirects=True,
                    limits=limits,
                    transport=_CachedDNSTransport(
                        limits=limits, http2=_HTTP2_AVAILABLE
                    ),
                )
    return _client

async def gather_with_deadline(
    *aws,
    timeout: float | None = None,
) -> list:
    """Параллельно ждёт задачи не дольше общего дедлайна.

    Возвращает результаты в исходном порядке. Задача, не успевшая за дедлайн,
    отменяется и заменяется на asyncio.TimeoutError — вызывающий код получает
    частичные данные, а не ждёт самый медленный источник.
    """
    deadline = settings.search_deadline if timeout is None else timeout
    tasks = [asyncio.ensure_future(aw) for aw in aws]
    if not tasks:
        return []

    done, pending = await asyncio.wait(tasks, timeout=max(0.0, deadline))
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    results: list = []
    for task in tasks:
        if task in done:
            try:
                results.append(task.result())
            except Exception as exc:  # noqa: BLE001
                results.append(exc)
        else:
            results.append(asyncio.TimeoutError())
    return results


async def close_client() -> None:
    global _client
    if _client is not None:
        client, _client = _client, None
        await client.aclose()
    _dns_cache.clear()


def clear_cache() -> None:
    """Полностью очищает in-memory кэш HTTP-ответов (Zero-PII)."""
    _cache.clear()


def _key(url: str, params: dict | None, method: str, headers: dict | None) -> str:
    auth = ""
    if headers:
        token = headers.get("Authorization") or headers.get("authorization")
        if token:
            auth = hashlib.blake2b(str(token).encode("utf-8"), digest_size=8).hexdigest()
    raw = method.upper() + "|" + url + "|" + auth + "|" + json.dumps(
        params or {}, sort_keys=True, default=str
    )
    return hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


def _cache_get(key: str, ttl: int) -> object | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if ttl >= 0 and time.monotonic() - ts > ttl:
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return value


def _cache_set(key: str, value: object) -> None:
    _cache[key] = (time.monotonic(), value)
    _cache.move_to_end(key)
    while len(_cache) > _cache_max:
        _cache.popitem(last=False)


async def _request_json(
    url: str,
    params: dict | None,
    headers: dict | None,
    method: str,
    ok_status: tuple[int, ...],
    timeout: float | None = None,
) -> tuple[object | None, str | None]:
    try:
        client = await get_client()
        request_timeout = (
            httpx.Timeout(
                timeout,
                connect=min(settings.http_connect_timeout, timeout),
            )
            if timeout is not None
            else None
        )
        async with _requests:
            if method == "POST":
                response = await client.post(
                    url, json=params, headers=headers, timeout=request_timeout
                )
            else:
                response = await client.get(
                    url, params=params, headers=headers, timeout=request_timeout
                )
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.HTTPError as exc:
        log.warning("HTTP error: %s", type(exc).__name__)
        return None, "network"

    if response.status_code >= 400 and response.status_code not in ok_status:
        detail = ""
        try:
            detail = (response.text or "").strip().replace("\n", " ")[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        if detail:
            return None, f"http_{response.status_code}: {detail}"
        return None, f"http_{response.status_code}"

    try:
        data = response.json()
    except ValueError:
        return None, "not_json"
    return data, None


async def get_json(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    ttl: int | None = None,
    method: str = "GET",
    ok_status: tuple[int, ...] = (),
    timeout: float | None = None,
) -> tuple[object | None, str | None]:
    """GET/POST JSON с кэшем. Возвращает (data, error)."""
    ttl = settings.http_cache_ttl if ttl is None else ttl
    key = _key(url, params, method, headers)
    cached = _cache_get(key, ttl)
    if cached is not None:
        return cached, None

    pending = _inflight.get(key)
    if pending is not None:
        return await asyncio.shield(pending)

    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    _inflight[key] = future
    result: tuple[object | None, str | None] = (None, "network")
    try:
        result = await _request_json(url, params, headers, method, ok_status, timeout)
        data, error = result
        if data is not None and error is None:
            _cache_set(key, data)
        return result
    finally:
        _inflight.pop(key, None)
        if not future.done():
            future.set_result(result)


async def fetch_text(
    url: str,
    *,
    method: str = "GET",
    params: dict | None = None,
    data: dict | None = None,
    headers: dict | None = None,
    timeout: float | None = None,
    max_bytes: int | None = 16 * 1024,
) -> tuple[int, str]:
    """Читает ответ как текст.

    max_bytes=None заменяется жёстким потолком MAX_BODY_BYTES (3 МБ),
    чтобы не вычитать многомегабайтный ответ в память.
    """
    if max_bytes is None:
        max_bytes = MAX_BODY_BYTES
    try:
        client = await get_client()
        read_timeout = (
            timeout if timeout is not None else settings.http_read_timeout
        )
        request_timeout = httpx.Timeout(
            read_timeout,
            connect=min(settings.http_connect_timeout, read_timeout),
        )
        async with _requests:
            async with client.stream(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=request_timeout,
            ) as response:
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    remaining = max_bytes - total
                    if remaining <= 0:
                        break
                    if len(chunk) > remaining:
                        chunk = chunk[:remaining]
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= max_bytes:
                        break
                raw = b"".join(chunks)
                encoding = response.encoding or "utf-8"
                return response.status_code, raw.decode(encoding, errors="replace")
    except httpx.TimeoutException:
        return 0, ""
    except httpx.HTTPError as exc:
        log.debug("HTTP text error: %s", type(exc).__name__)
        return 0, ""
