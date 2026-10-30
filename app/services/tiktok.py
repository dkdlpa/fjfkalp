"""TikTok OSINT — публичный профиль без сторонних API.

Страница профиля читается полностью: JSON-блок
``<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">`` находится в конце
большого HTML (150–350 КБ), поэтому лимит 16 КБ здесь не применяется.

Основной запрос идёт через primp с браузерным TLS-impersonation, что
позволяет TikTok отдавать профиль без капчи. Если primp недоступен,
используется обычный HTTP-клиент с полными заголовками браузера.
"""
from __future__ import annotations

import asyncio
import datetime
import html
import json
import logging
import re

from app.config import settings
from app.services.http import fetch_text, gather_with_deadline
from app.services.profiler import profiled

try:  # primp установлен в окружении; импорт не должен ломать бота
    import primp  # type: ignore
except Exception:  # noqa: BLE001
    primp = None  # type: ignore

log = logging.getLogger(__name__)


TIKTOK_USERNAME_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?tiktok\.com/@([A-Za-z0-9_.]+)/?$"
)
_PLAIN_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{2,30}$")
_REHYDRATION_RE = re.compile(
    r'<script[^>]+id=["\']__UNIVERSAL_DATA_FOR_REHYDRATION__["\'][^>]*>(.*?)</script>',
    re.S,
)

# Полные заголовки браузера — резервный путь, если TLS-impersonation недоступен.
_MAX_BODY = 3 * 1024 * 1024

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.tiktok.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

_PRIMP_IMPERSONATE = "chrome_128"
_PRIMP_FALLBACKS = ("chrome", "edge", "safari")


def parse_tiktok_input(text: str) -> str | None:
    text = (text or "").strip().lstrip("@")
    m = TIKTOK_USERNAME_RE.match(text)
    if m:
        return m.group(1)
    if _PLAIN_USERNAME_RE.match(text):
        return text
    return None


def _format_timestamp(value: str | int | None) -> str | None:
    """Unix-время (секунды или миллисекунды) -> YYYY-MM-DD HH:MM:SS (UTC)."""
    try:
        ts = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if ts > 10_000_000_000:  # миллисекунды
        ts //= 1000
    if ts < 1_000_000_000 or ts > 4_000_000_000:
        return None
    try:
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _date_from_user_id(user_id: str | int | None) -> str | None:
    """Дата создания из числового ID: timestamp = int(user_id) >> 31.

    Для старых коротких ID сдвиг даёт 0; для современных TikTok-ID результат
    иногда уезжает в будущее, поэтому дополнительно проверяем << 32.
    """
    try:
        raw = int(user_id)
    except (TypeError, ValueError):
        return None
    timestamp = raw >> 31
    if timestamp > datetime.datetime.now(tz=datetime.timezone.utc).timestamp() + 366 * 86400:
        timestamp = raw >> 32
    return _format_timestamp(timestamp)


def _as_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "1", "yes"}:
            return True
        if low in {"false", "0", "no"}:
            return False
    return None


def _json_unescape(value: str) -> str:
    try:
        return json.loads('"' + value.replace('"', '\\"') + '"')
    except Exception:  # noqa: BLE001
        return html.unescape(
            value.replace("\\u002F", "/").replace("\\/", "/").replace("\\n", "\n")
        )


def _first(patterns: tuple[str, ...], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group(1)
            if value:
                return value
    return None


def _parse_scope(text: str) -> tuple[dict, dict]:
    """Достаёт user и stats из __DEFAULT_SCOPE__.webapp.user-detail.userInfo."""
    match = _REHYDRATION_RE.search(text)
    if not match:
        return {}, {}
    try:
        payload = json.loads(match.group(1))
    except (TypeError, ValueError):
        return {}, {}
    if not isinstance(payload, dict):
        return {}, {}
    scope = payload.get("__DEFAULT_SCOPE__") or {}
    detail = scope.get("webapp.user-detail") or {}
    info = detail.get("userInfo") or {}
    if not isinstance(info, dict):
        return {}, {}
    user = info.get("user") or {}
    stats = info.get("stats") or info.get("statsV2") or {}
    return (user if isinstance(user, dict) else {}), (stats if isinstance(stats, dict) else {})


def _parse_regex(username: str, text: str) -> dict:
    """Резервный парсинг сырого HTML, если JSON-блок недоступен/неполный."""
    result: dict = {}

    uid_match = re.search(
        r'"uniqueId":"' + re.escape(username) + r'"[^}]*?"id":"(\d+)"', text
    )
    if not uid_match:
        uid_match = re.search(r'"author":\{"id":"(\d+)"', text)
    if not uid_match:
        uid_match = re.search(r'"userInfo":\{[^}]*?"id":"(\d+)"', text)
    if not uid_match:
        uid_match = re.search(r'"id":"(\d{5,25})"', text)
    if uid_match:
        result["user_id"] = uid_match.group(1)

    sec_uid = _first(
        (
            r'"secUid":"([^"]+)"',
            r'"sec_uid":"([^"]+)"',
            r'"secUserId":"([^"]+)"',
        ),
        text,
    )
    if sec_uid:
        result["sec_uid"] = _json_unescape(sec_uid)

    unique_id = _first((r'"uniqueId":"([^"]+)"',), text)
    if unique_id:
        result["username"] = _json_unescape(unique_id)

    nick = _first((r'"nickname":"([^"]+)"', r'"nickName":"([^"]+)"'), text)
    if nick:
        result["nickname"] = _json_unescape(nick).strip()

    bio = _first((r'"signature":"([^"]*)"', r'"bio":"([^"]*)"'), text)
    if bio is not None:
        result["bio"] = _json_unescape(bio).strip()

    verified = _first((r'"verified":(true|false)', r'"isVerified":(true|false)'), text)
    if verified is not None:
        result["verified"] = verified == "true"

    private = _first((r'"privateAccount":(true|false)',), text)
    if private is not None:
        result["private_account"] = private == "true"

    for field, key in (
        ("followerCount", "followers"),
        ("followingCount", "following"),
        ("heartCount", "likes"),
        ("heart", "likes"),
        ("videoCount", "video_count"),
    ):
        match = re.search(r'"' + field + r'":(\d+)', text)
        if match and key not in result:
            result[key] = int(match.group(1))

    avatar = _first(
        (
            r'"avatarLarger":"([^"]+)"',
            r'"avatarMedium":"([^"]+)"',
            r'"avatarThumb":"([^"]+)"',
        ),
        text,
    )
    if avatar:
        result["avatar_url"] = _json_unescape(avatar).replace("\\u002F", "/")

    create_time = _first(
        (
            r'"createTime":(\d{1,13})',
            r'"create_time":(\d{1,13})',
            r'"accountCreated":(\d{1,13})',
            r'"createdAt":(\d{1,13})',
        ),
        text,
    )
    if create_time:
        result["create_time"] = create_time
    return result


def _add_registration(result: dict) -> None:
    create_time = _as_int(result.pop("create_time", None))
    date = _format_timestamp(create_time) if create_time else None
    note = ""
    if not date:
        date = _date_from_user_id(result.get("user_id"))
        note = "рассчитано по ID" if date else ""
    if date:
        result["registration_date"] = date
        result["registration_exact"] = True
        if note:
            result["registration_note"] = note


def _parse_profile(username: str, text: str) -> dict:
    """JSON из __DEFAULT_SCOPE__ + regex-fallback по сырому HTML."""
    user, stats = _parse_scope(text)
    result: dict = {}

    if user:
        user_id = user.get("id")
        if user_id:
            result["user_id"] = str(user_id)
        if user.get("secUid"):
            result["sec_uid"] = str(user["secUid"])
        if user.get("uniqueId"):
            result["username"] = str(user["uniqueId"])
        if user.get("nickname"):
            result["nickname"] = str(user["nickname"])
        if user.get("signature") is not None:
            result["bio"] = str(user["signature"])
        verified = _as_bool(user.get("verified"))
        if verified is not None:
            result["verified"] = verified
        private = _as_bool(user.get("privateAccount"))
        if private is not None:
            result["private_account"] = private
        region = user.get("region") or user.get("country")
        if region:
            result["region"] = str(region).upper()
        if user.get("language"):
            result["language"] = str(user["language"])
        if user.get("avatarLarger"):
            result["avatar_url"] = str(user["avatarLarger"])
        if user.get("createTime") is not None:
            result["create_time"] = user["createTime"]

    if stats:
        for field, key in (
            ("followerCount", "followers"),
            ("followingCount", "following"),
            ("heartCount", "likes"),
            ("heart", "likes"),
            ("videoCount", "video_count"),
        ):
            value = _as_int(stats.get(field))
            if value is not None and key not in result:
                result[key] = value

    # JSON мог быть неполным или отсутствовать — добираем регулярками.
    fallback = _parse_regex(username, text)
    for key, value in fallback.items():
        result.setdefault(key, value)

    _add_registration(result)
    if not result.get("username"):
        result["username"] = username
    return result


def _primp_fetch(url: str) -> tuple[int, str]:
    """Синхронный primp-запрос (вызывается через asyncio.to_thread).

    Одна попытка без перебора профилей: медленный профиль не должен съедать
    дедлайн, резервный обычный HTTP-запрос идёт параллельно.
    """
    if primp is None:
        return 0, ""
    try:
        with primp.Client(
            impersonate=_PRIMP_IMPERSONATE,
            follow_redirects=True,
            timeout=settings.search_request_timeout,
        ) as client:
            response = client.get(url, headers=HEADERS)
            text = response.text
            if len(text) > _MAX_BODY:
                text = text[:_MAX_BODY]
            return response.status_code, text
    except Exception as exc:  # noqa: BLE001
        log.debug("primp %s failed: %s", _PRIMP_IMPERSONATE, type(exc).__name__)
        return 0, ""


async def _fetch_html(username: str) -> tuple[int, str]:
    """Полная страница профиля: primp и обычный HTTP-запрос параллельно."""
    url = f"https://www.tiktok.com/@{username}"
    primp_result, http_result = await gather_with_deadline(
        asyncio.to_thread(_primp_fetch, url),
        fetch_text(
            url,
            headers=HEADERS,
            timeout=settings.search_request_timeout,
            max_bytes=None,
        ),
    )
    for result in (primp_result, http_result):
        if isinstance(result, tuple) and result[0] == 200 and result[1]:
            return result
    for result in (primp_result, http_result):
        if isinstance(result, tuple):
            return result
    return 0, ""


async def _fetch_profile(username: str) -> dict:
    try:
        status, text = await _fetch_html(username)
        if status == 0 or not text:
            return {"error": "unavailable"}
        if status == 404:
            return {"error": "not_found"}
        if status != 200:
            return {"error": "unavailable"}

        result = await asyncio.to_thread(_parse_profile, username, text)
    except Exception:  # noqa: BLE001
        log.exception("TikTok scrape error")
        return {"error": "unavailable"}

    if not result or (not result.get("user_id") and not result.get("sec_uid")):
        return {"error": "not_found"}
    return result


@profiled("service.tiktok")
async def analyze(target: str) -> dict:
    username = parse_tiktok_input(target)
    if not username:
        return {"error": "invalid_input"}

    profile = await _fetch_profile(username)
    return {
        "username": username,
        "error": profile.get("error"),
        "profile": {k: v for k, v in profile.items() if k != "error"},
        "source": "tiktok.com",
    }
