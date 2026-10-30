"""Email OSINT.

Валидация адреса — бесплатные публичные API без ключей:
- mailcheck.ai  (документация: https://api.mailcheck.ai) — MX, одноразовость,
  возраст домена, роль, бесплатный провайдер.
- disify.com   (документация: https://www.disify.com) — формат, DNS, whitelist.

Регистрация в сервисах — прямые HTTP-запросы к публичным endpoint-ам
(Duolingo, Spotify, Firefox Accounts, WordPress.com, Chess.com, Twitter/X,
Gravatar, GitHub). Утечки — leakcheck.io + XposedOrNot, инфостилеры — Hudson
Rock Cavalier. Внешние утилиты (holehe/h8mail) и subprocess не используются.
"""
from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import quote

import httpx

from app.config import settings
from app.services.http import gather_with_deadline, get_client, get_json
from app.services.profiler import profiled

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def is_valid_email(email: str) -> bool:
    """Строгая проверка: длина, локальная часть, домен без крайних дефисов."""
    email = (email or "").strip()
    if len(email) > 254 or email.count("@") != 1:
        return False
    local, domain = email.rsplit("@", 1)
    if not local or len(local) > 64:
        return False
    if ".." in email or local.startswith(".") or local.endswith("."):
        return False
    if not domain or ".." in domain:
        return False
    for label in domain.split("."):
        if not label or label.startswith("-") or label.endswith("-"):
            return False
    return bool(EMAIL_RE.match(email))


async def _validate_mailcheck(email: str) -> dict:
    data, err = await get_json(f"https://api.mailcheck.ai/email/{quote(email, safe='')}", ttl=300)
    if err or not isinstance(data, dict) or data.get("status") != 200:
        return {}
    return {
        "domain": data.get("domain"),
        "mx": data.get("mx"),
        "disposable": data.get("disposable"),
        "free": data.get("free"),
        "role": data.get("role"),
        "domain_age_days": data.get("domain_age_in_days"),
        "source": "mailcheck.ai",
    }


async def _validate_disify(email: str) -> dict:
    data, err = await get_json(f"https://disify.com/api/email/{quote(email, safe='')}", ttl=300)
    if err or not isinstance(data, dict):
        return {}
    return {
        "domain": data.get("domain"),
        "format_valid": data.get("format"),
        "dns": data.get("dns"),
        "disposable": data.get("disposable"),
        "whitelist": data.get("whitelist"),
        "source": "disify.com",
    }


async def _validate(email: str) -> tuple[dict, bool]:
    # Оба источника опрашиваются параллельно: суммарная задержка = max, а не sum.
    primary, extra = await gather_with_deadline(
        _validate_mailcheck(email),
        _validate_disify(email),
    )
    if not isinstance(primary, dict):
        primary = {}
    if not isinstance(extra, dict):
        extra = {}
    merged: dict = {}
    for source in (extra, primary):  # primary перекрывает резерв
        for key, value in source.items():
            if value is not None and key not in merged:
                merged[key] = value
    if primary.get("source"):
        merged["source"] = primary["source"]
    elif extra.get("source"):
        merged["source"] = extra["source"]
    # validation_ok = True, если ответил хотя бы один источник (сбой одного не ломает отчёт)
    return merged, bool(primary or extra)


async def _check_gravatar(email: str) -> dict:
    """Gravatar — аватар и публичный профиль по MD5 хешу email. Бесплатно, без ключа."""
    try:
        h = hashlib.md5(email.strip().lower().encode()).hexdigest()
        data, err = await get_json(f"https://www.gravatar.com/{h}.json", ttl=300)
        if err or not isinstance(data, dict):
            return {}
        entry = (data.get("entry") or [{}])[0]
        result = {}
        if entry.get("displayName"):
            result["name"] = entry["displayName"]
        if entry.get("preferredUsername"):
            result["username"] = entry["preferredUsername"]
        if entry.get("thumbnailUrl"):
            result["avatar_url"] = entry["thumbnailUrl"]
        urls = entry.get("urls") or []
        if urls:
            result["links"] = [u.get("value") for u in urls if u.get("value")]
        accounts = entry.get("accounts") or []
        if accounts:
            result["accounts"] = [a.get("shortname") for a in accounts if a.get("shortname")]
        about = entry.get("aboutMe") or (entry.get("ims") or [{}])[0].get("value")
        if about:
            result["about"] = about
        return result
    except Exception:  # noqa: BLE001
        return {}


async def _svc_duolingo(client: httpx.AsyncClient, email: str) -> dict | None:
    try:
        r = await client.get(
            "https://www.duolingo.com/2017-06-30/users",
            params={"email": email},
            timeout=settings.search_request_timeout,
        )
        if r.status_code != 200:
            return None
        users = (r.json() or {}).get("users") or []
        if not users:
            return None
        first = users[0] if isinstance(users[0], dict) else {}
        entry = {"service": "Duolingo", "exists": True}
        if first.get("username"):
            entry["username"] = first["username"]
        if first.get("name"):
            entry["name"] = first["name"]
        return entry
    except Exception:  # noqa: BLE001
        return None


async def _svc_spotify(client: httpx.AsyncClient, email: str) -> dict | None:
    try:
        r = await client.get(
            "https://spclient.wg.spotify.com/signup/public/v1/account",
            params={"validate": "1", "email": email},
            timeout=settings.search_request_timeout,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if isinstance(data, dict) and data.get("status") == 20:
            return {"service": "Spotify", "exists": True}
    except Exception:  # noqa: BLE001
        return None
    return None


async def _svc_firefox(client: httpx.AsyncClient, email: str) -> dict | None:
    try:
        r = await client.post(
            "https://api.accounts.firefox.com/v1/account/status",
            json={"email": email},
            timeout=settings.search_request_timeout,
        )
        if r.status_code != 200:
            return None
        if (r.json() or {}).get("exists") is True:
            return {"service": "Firefox Accounts", "exists": True}
    except Exception:  # noqa: BLE001
        return None
    return None


async def _svc_wordpress(client: httpx.AsyncClient, email: str) -> dict | None:
    try:
        r = await client.get(
            f"https://public-api.wordpress.com/rest/v1.1/users/{email}/auth-options",
            timeout=settings.search_request_timeout,
        )
        if r.status_code == 200:
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            entry = {"service": "WordPress.com", "exists": True}
            if isinstance(data, dict):
                for key in ("email", "username"):
                    if data.get(key):
                        entry[key] = data[key]
            return entry
    except Exception:  # noqa: BLE001
        return None
    return None


async def _svc_chess(client: httpx.AsyncClient, email: str) -> dict | None:
    """Chess.com: публичный autocomplete. Эндпоинт не принимает email,
    поэтому считаем совпадением только точное совпадение логина с локальной частью."""
    try:
        local = email.split("@", 1)[0]
        r = await client.get(
            "https://www.chess.com/callback/autocomplete/member",
            params={"query": local},
            timeout=settings.search_request_timeout,
        )
        if r.status_code != 200:
            return None
        items = r.json() or []
        if not isinstance(items, list):
            return None
        for item in items:
            if isinstance(item, dict):
                username = item.get("username") or item.get("value")
                if isinstance(username, str) and username.lower() == local.lower():
                    return {"service": "Chess.com", "exists": True, "username": username}
    except Exception:  # noqa: BLE001
        return None
    return None


async def _svc_twitter(client: httpx.AsyncClient, email: str) -> dict | None:
    try:
        r = await client.get(
            "https://api.twitter.com/i/users/email_available.json",
            params={"email": email},
            timeout=settings.search_request_timeout,
        )
        if r.status_code == 200 and r.json().get("valid") is False:
            return {"service": "Twitter/X", "exists": True}
    except Exception:  # noqa: BLE001
        return None
    return None


async def _svc_github(client: httpx.AsyncClient, email: str) -> dict | None:
    try:
        r = await client.get(
            "https://api.github.com/search/users",
            params={"q": f"{email} in:email"},
            headers={"Accept": "application/vnd.github+json", "User-Agent": _UA},
            timeout=settings.search_request_timeout,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if isinstance(data, dict) and data.get("total_count", 0) > 0:
            item = (data.get("items") or [{}])[0]
            entry = {"service": "GitHub", "exists": True}
            if isinstance(item, dict) and item.get("login"):
                entry["username"] = item["login"]
            return entry
    except Exception:  # noqa: BLE001
        return None
    return None


# Набор публичных проверок регистрации (без ключей, cookies и капчи).
_SERVICE_CHECKS = (
    ("Duolingo", _svc_duolingo),
    ("Spotify", _svc_spotify),
    ("Firefox Accounts", _svc_firefox),
    ("WordPress.com", _svc_wordpress),
    ("Chess.com", _svc_chess),
    ("Twitter/X", _svc_twitter),
    ("GitHub", _svc_github),
)


async def _check_services(email: str) -> list[dict]:
    """Параллельно проверяет регистрацию email в публичных сервисах.

    Возвращает список деталей: {"service", "exists", "username"?, "name"?}.
    """
    found: list[dict] = []
    client = await get_client()
    tasks = [checker(client, email) for _, checker in _SERVICE_CHECKS]
    results = await gather_with_deadline(*tasks)
    for result in results:
        if isinstance(result, dict) and result.get("service"):
            found.append(result)
    return found


async def _check_leakcheck(client: httpx.AsyncClient, email: str) -> list[str]:
    try:
        r = await client.get("https://leakcheck.io/api/public", params={"check": email}, timeout=settings.search_request_timeout)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict) or not data.get("success"):
        return []
    sources = data.get("sources") or []
    names = [s.get("name", "Unknown") for s in sources if isinstance(s, dict) and s.get("name")]
    return list(dict.fromkeys(names))[:20]


async def _check_xposed(client: httpx.AsyncClient, email: str) -> list[dict]:
    """XposedOrNot — детали утечек (сервис, год, типы слитых данных)."""
    try:
        r = await client.get(
            "https://api.xposedornot.com/v1/breach-analytics",
            params={"email": email},
            timeout=settings.search_request_timeout,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []

    details = (
        ((data.get("ExposedBreaches") or {}).get("breaches_details"))
        or data.get("breaches_details")
        or []
    )
    out: list[dict] = []
    for item in details:
        if not isinstance(item, dict):
            continue
        raw_data = item.get("xposed_data") or item.get("data") or ""
        if isinstance(raw_data, list):
            leaked = [str(x).strip() for x in raw_data if str(x).strip()]
        else:
            leaked = [p.strip() for p in str(raw_data).replace(",", ";").split(";") if p.strip()]
        out.append({
            "name": item.get("breach") or item.get("name") or "Unknown",
            "date": item.get("xposed_date") or item.get("date") or "",
            "data": leaked,
            "password_risk": item.get("password_risk") or "",
            "records": item.get("xposed_records") or item.get("records"),
        })
    return out[:30]


async def _check_infostealers(client: httpx.AsyncClient, email: str) -> list[dict]:
    """Hudson Rock Cavalier — проверка на заражение инфостилерами."""
    try:
        r = await client.get(
            "https://cavalier.hudsonrock.com/api/json/v2/osint-tools/search-by-email",
            params={"email": email},
            timeout=settings.search_request_timeout,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []

    stealers = data.get("stealers") or []
    out: list[dict] = []
    for s in stealers:
        if not isinstance(s, dict):
            continue
        out.append({
            "date": s.get("date_compromised") or "",
            "os": s.get("operating_system") or "",
            "stealer": s.get("stealer_family") or "",
            "computer": s.get("computer_name") or "",
            "ip": s.get("ip") or "",
            "antiviruses": s.get("antiviruses") or "",
            "top_logins": s.get("top_logins") or [],
            "top_passwords": s.get("top_passwords") or [],
        })
    return out[:20]


async def _check_breaches(email: str) -> dict:
    """Агрегирует утечки из leakcheck.io, XposedOrNot и инфостилеры Hudson Rock."""
    client = await get_client()
    leakcheck, xposed, stealers = await gather_with_deadline(
        _check_leakcheck(client, email),
        _check_xposed(client, email),
        _check_infostealers(client, email),
    )

    sources = list(leakcheck) if isinstance(leakcheck, list) else []
    detail = list(xposed) if isinstance(xposed, list) else []
    # имена из XposedOrNot, которых нет в leakcheck
    for item in detail:
        name = item.get("name")
        if name and name not in sources:
            sources.append(name)

    return {
        "sources": sources[:30],
        "detail": detail,
        "infostealers": list(stealers) if isinstance(stealers, list) else [],
    }


@profiled("service.email")
async def analyze(email: str) -> dict:
    """Полный анализ email: валидация + сервисы + утечки + инфостилеры."""
    result: dict = {
        "email": email,
        "error": None,
        "validation": {},
        "validation_ok": False,
        "services": [],           # обратная совместимость: список названий
        "services_detail": [],    # детали (username и т.д.)
        "breaches": [],           # обратная совместимость: список названий
        "breaches_detail": [],    # детали утечек (год, типы данных)
        "infostealers": [],       # Hudson Rock
        "gravatar": {},
    }

    if not is_valid_email(email):
        result["error"] = "invalid_input"
        return result

    validation, services, breaches, gravatar = await gather_with_deadline(
        _validate(email),
        _check_services(email),
        _check_breaches(email),
        _check_gravatar(email),
    )

    if isinstance(validation, tuple) and len(validation) == 2:
        merged, ok = validation
        if isinstance(merged, dict):
            result["validation"] = merged
        result["validation_ok"] = bool(ok)

    if isinstance(services, list):
        result["services_detail"] = [s for s in services if isinstance(s, dict)]

    gravatar_info = gravatar if isinstance(gravatar, dict) else {}
    if gravatar_info:
        result["services_detail"].append(
            {
                "service": "Gravatar",
                "exists": True,
                "username": gravatar_info.get("username"),
                "name": gravatar_info.get("name"),
            }
        )
    if result["services_detail"]:
        result["services"] = [s["service"] for s in result["services_detail"] if s.get("service")]

    if isinstance(breaches, dict):
        result["breaches"] = [b for b in (breaches.get("sources") or []) if isinstance(b, str)]
        result["breaches_detail"] = [d for d in (breaches.get("detail") or []) if isinstance(d, dict)]
        result["infostealers"] = [s for s in (breaches.get("infostealers") or []) if isinstance(s, dict)]

    result["gravatar"] = gravatar_info
    result["exists"] = _classify_exists(result["validation"])
    return result


def _classify_exists(validation: dict) -> bool | None:
    """True / False / None(unknown) по данным валидации."""
    for key in ("mx", "dns", "format_valid"):
        if key in validation and validation[key] is not None:
            value = validation[key]
            return bool(value)
    return None