"""VK OSINT через официальный VK API (метод users.get, groups.get и др.).

Адаптация приложенного VK-модуля:
- синхронный requests.Session заменён на единый httpx-клиент проекта;
- токен берётся только из окружения (VK_TOKEN), в коде его нет;
- никакого скрейпинга сторонних «пробивных» сайтов: только публичные данные,
  которые VK API отдаёт легально (профиль, подписки, друзья, посты, фото);
- часть полей доступна только с токеном пользователя/сервиса и с учётом
  приватности настроек конкретного профиля. Чего API не вернул — того в
  отчёте нет, ничего не додумываем.

Ошибки:
- VK error_code 5 (auth failed) / 18 / 27 / 28 / 113 -> auth_error;
- 6 (too many requests), 10 (internal), 1/2 -> temporary (нейтральный текст);
- 100 (invalid params) -> invalid_input;
- 7 (not found / deleted) -> not_found.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.config import settings
from app.services.http import gather_with_deadline, get_json

log = logging.getLogger(__name__)

API = "https://api.vk.com/method/"
_MAX_CONCURRENCY = 4
_sem: asyncio.Semaphore | None = None


class VKError(RuntimeError):
    def __init__(self, kind: str, *, code: int | None = None, method: str = ""):
        super().__init__(kind)
        self.kind = kind
        self.code = code
        self.method = method


def user_key(error: VKError) -> str:
    if error.kind == "not_found":
        return "not_found"
    if error.kind == "invalid_input":
        return "invalid_input"
    if error.kind == "not_configured":
        return "source_not_configured"
    return "source_maintenance"


_AUTH_CODES = {5, 18, 27, 28, 113}
_TEMP_CODES = {1, 2, 6, 10}


def _map_code(code: int) -> str:
    if code in _AUTH_CODES:
        return "auth_error"
    if code in _TEMP_CODES:
        return "temporary"
    if code == 100:
        return "invalid_input"
    if code in (7, 15, 30):
        return "not_found"
    return "temporary"


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    return _sem


async def _call(method: str, params: dict[str, Any]) -> Any:
    if not settings.vk_ready:
        raise VKError("not_configured", method=method)
    payload = {
        **{k: v for k, v in params.items() if v not in (None, "")},
        "access_token": settings.vk_token,
        "v": settings.vk_api_version,
        "lang": "ru",
    }
    async with _semaphore():
        data, error = await get_json(
            f"{API}{method}",
            params=payload,
            ttl=60,
            timeout=settings.vk_timeout,
        )
    if error:
        status: int | None = None
        if error.startswith("http_"):
            try:
                status = int(error.split(":", 1)[0].split("_", 1)[1])
            except (IndexError, ValueError):
                status = None
        log.warning("VK %s: kind=temporary status=%s method=%s", "temporary", status, method)
        raise VKError("temporary", code=status, method=method)

    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        err = data["error"]
        code = err.get("error_code")
        try:
            code = int(code) if code is not None else None
        except (TypeError, ValueError):
            code = None
        kind = _map_code(code) if code is not None else "temporary"
        # Внутренний лог: код и метод, без токена и параметров.
        log.warning("VK %s: kind=%s code=%s method=%s", kind, kind, code, method)
        raise VKError(kind, code=code, method=method)

    return data.get("response") if isinstance(data, dict) else None


def normalize_vk_id(value: str) -> str:
    """vk.com/id123, https://vk.com/user, id123, 123, user -> строка для API."""
    value = (value or "").strip()
    low = value.lower()
    if "vk.com/" in low:
        tail = value.split("vk.com/", 1)[1]
        value = tail.split("?", 1)[0].split("#", 1)[0].strip("/")
    value = value.strip().lstrip("@")
    match = re.fullmatch(r"id(\d+)", value, flags=re.I)
    if match:
        return match.group(1)
    if value.isdigit():
        return value
    return value


def is_valid_vk_id(value: str) -> bool:
    if not value:
        return False
    if value.isdigit():
        return True
    return bool(re.fullmatch(r"[A-Za-z0-9_.]{2,64}", value))


_USER_FIELDS = (
    "photo_max,verified,sex,bdate,city,country,home_town,status,education,"
    "universities,schools,occupation,career,interests,music,movies,tv,books,"
    "games,about,activities,quotes,site,domain,followers_count,online,last_seen,"
    "counters"
)


async def _user(vk_id: str) -> dict:
    data = await _call("users.get", {"user_ids": vk_id, "fields": _USER_FIELDS})
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise VKError("not_found", method="users.get")
    return data[0]


async def _groups(vk_id: str) -> dict:
    try:
        data = await _call(
            "groups.get",
            {"user_id": vk_id, "extended": 1, "count": 50, "fields": "screen_name,members_count,description"},
        )
    except VKError as exc:
        if exc.kind in {"auth_error", "not_found"}:
            return {"items": [], "count": 0, "error": exc.kind}
        return {"items": [], "count": 0, "error": exc.kind}
    if not isinstance(data, dict):
        return {"items": [], "count": 0}
    return data


async def _friends(vk_id: str, count: int = 20) -> dict:
    try:
        data = await _call(
            "friends.get",
            {"user_id": vk_id, "count": count, "fields": "photo_50,online"},
        )
    except VKError as exc:
        return {"items": [], "count": 0, "error": exc.kind}
    return data if isinstance(data, dict) else {"items": [], "count": 0}


async def _wall(vk_id: str, count: int = 10) -> dict:
    try:
        data = await _call("wall.get", {"owner_id": vk_id, "count": count})
    except VKError as exc:
        return {"items": [], "count": 0, "error": exc.kind}
    return data if isinstance(data, dict) else {"items": [], "count": 0}


async def _photos(vk_id: str, count: int = 6) -> dict:
    try:
        data = await _call(
            "photos.get",
            {"owner_id": vk_id, "album_id": "profile", "count": count, "extended": 1},
        )
    except VKError as exc:
        return {"items": [], "count": 0, "error": exc.kind}
    return data if isinstance(data, dict) else {"items": [], "count": 0}


def _clean(text: Any, limit: int = 300) -> str:
    if not text:
        return ""
    value = re.sub(r"\s+", " ", str(text)).strip()
    return value[:limit] + ("…" if len(value) > limit else "")


def _date(ts: Any) -> str:
    try:
        import datetime

        return datetime.datetime.fromtimestamp(int(ts)).strftime("%d.%m.%Y %H:%M")
    except Exception:  # noqa: BLE001
        return ""


def _sex(value: Any) -> str:
    return {1: "женский", 2: "мужской"}.get(value, "не указан")


async def analyze(target: str) -> dict:
    vk_id = normalize_vk_id(target)
    if not is_valid_vk_id(vk_id):
        return {"error": "invalid_input", "target": target}

    try:
        user = await _user(vk_id)
    except VKError as exc:
        return {"error": exc.kind, "target": target}

    resolved_id = str(user.get("id") or vk_id)
    groups, friends, wall, photos = await gather_with_deadline(
        _groups(resolved_id),
        _friends(resolved_id),
        _wall(resolved_id),
        _photos(resolved_id),
        timeout=settings.search_deadline,
    )

    def as_dict(value) -> dict:
        return value if isinstance(value, dict) else {"items": [], "count": 0}

    groups = as_dict(groups)
    friends = as_dict(friends)
    wall = as_dict(wall)
    photos = as_dict(photos)

    counters = user.get("counters") or {}
    city = (user.get("city") or {}).get("title", "")
    country = (user.get("country") or {}).get("title", "")
    last_seen = (user.get("last_seen") or {}).get("time")

    profile = {
        "id": user.get("id"),
        "first_name": user.get("first_name", ""),
        "last_name": user.get("last_name", ""),
        "domain": user.get("domain", ""),
        "sex": _sex(user.get("sex")),
        "bdate": user.get("bdate", ""),
        "city": city,
        "country": country,
        "home_town": user.get("home_town", ""),
        "status": _clean(user.get("status"), 200),
        "about": _clean(user.get("about"), 400),
        "activities": _clean(user.get("activities"), 200),
        "interests": _clean(user.get("interests"), 300),
        "music": _clean(user.get("music"), 200),
        "movies": _clean(user.get("movies"), 200),
        "books": _clean(user.get("books"), 200),
        "quotes": _clean(user.get("quotes"), 200),
        "site": user.get("site", ""),
        "verified": bool(user.get("verified")),
        "online": bool(user.get("online")),
        "last_seen": _date(last_seen) if last_seen else "",
        "followers": user.get("followers_count") or counters.get("followers") or 0,
        "photo": user.get("photo_max", ""),
        "url": f"https://vk.com/{user.get('domain') or 'id' + resolved_id}",
        "education": (user.get("education") or {}).get("university_name", ""),
    }

    group_items = [
        {
            "name": g.get("name", ""),
            "screen_name": g.get("screen_name", ""),
            "members": g.get("members_count", 0),
            "description": _clean(g.get("description"), 160),
        }
        for g in (groups.get("items") or [])[:20]
        if isinstance(g, dict)
    ]
    friend_items = [
        {
            "id": f.get("id"),
            "name": f"{f.get('first_name', '')} {f.get('last_name', '')}".strip(),
            "online": bool(f.get("online")),
            "url": f"https://vk.com/id{f.get('id')}",
        }
        for f in (friends.get("items") or [])[:20]
        if isinstance(f, dict)
    ]
    post_items = []
    for post in (wall.get("items") or [])[:10]:
        if not isinstance(post, dict) or post.get("marked_as_ads") == 1:
            continue
        post_items.append(
            {
                "date": _date(post.get("date")),
                "text": _clean(post.get("text"), 300),
                "likes": (post.get("likes") or {}).get("count", 0),
                "reposts": (post.get("reposts") or {}).get("count", 0),
                "views": (post.get("views") or {}).get("count", 0),
                "comments": (post.get("comments") or {}).get("count", 0),
                "url": f"https://vk.com/wall{post.get('owner_id')}_{post.get('id')}",
            }
        )
    photo_items = []
    for photo in (photos.get("items") or [])[:6]:
        if not isinstance(photo, dict):
            continue
        sizes = photo.get("sizes") or []
        best = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0)) if sizes else {}
        photo_items.append(
            {
                "url": best.get("url", ""),
                "likes": (photo.get("likes") or {}).get("count", 0),
            }
        )

    return {
        "error": None,
        "target": target,
        "profile": profile,
        "groups": {"count": groups.get("count", len(group_items)), "items": group_items,
                   "error": groups.get("error")},
        "friends": {"count": friends.get("count", len(friend_items)), "items": friend_items,
                    "error": friends.get("error")},
        "wall": {"count": wall.get("count", len(post_items)), "items": post_items,
                 "error": wall.get("error")},
        "photos": {"count": photos.get("count", len(photo_items)), "items": photo_items,
                   "error": photos.get("error")},
    }


__all__ = ["VKError", "analyze", "is_valid_vk_id", "normalize_vk_id", "user_key"]