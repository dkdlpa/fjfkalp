"""Живой веб-поиск: SearchApi (DuckDuckGo) + резервные источники.

Основной путь — документированный SearchApi:
    GET https://www.searchapi.io/api/v1/search
    engine=duckduckgo, q, locale (xx-yy), safe (on/moderate/off), time_period
Ключ передаётся ТОЛЬКО заголовком Authorization: Bearer, в query его нет.

Резервная цепочка (ddgs -> DuckDuckGo Lite -> Yahoo) запускается только если
SearchApi вернул пусто/ошибку. В отчётах технические названия API не выводятся.

Кэш: локальный in-memory TTL-кэш (OrderedDict, 256 записей, 300 c). SQLite-кэш
app/services/cache.py здесь не используется — у него другая сигнатура.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import urllib.parse
import warnings
from collections import OrderedDict

warnings.filterwarnings("ignore", category=RuntimeWarning)

from app.config import settings
from app.services.http import fetch_text, gather_with_deadline, get_json
from app.services.profiler import profiled

log = logging.getLogger(__name__)

SEARCHAPI_URL = "https://www.searchapi.io/api/v1/search"
SEARCHAPI_TIMEOUT = 4.0

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_FORM_HEADERS = {
    "User-Agent": _UA,
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}

try:  # актуальное имя пакета
    from ddgs import DDGS as _DDGS  # type: ignore
except Exception:  # noqa: BLE001
    _DDGS = None  # type: ignore

# --- локальный TTL-кэш -------------------------------------------------------
_CACHE_TTL = 300.0
_CACHE_MAX = 256
_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()

# --- троттлинг WARNING (не чаще раза в 10 минут на тип ошибки) ---------------
_WARN_INTERVAL = 600.0
_last_warn: dict[str, float] = {}
_no_key_warned = False


def _cache_key(locale: str, safe: str, time_period: str | None, max_results: int, query: str) -> str:
    raw = "|".join(
        [locale or "", safe or "", time_period or "", str(max_results), query or ""]
    )
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()


def _cache_get(key: str) -> dict | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _CACHE_TTL:
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return value


def _cache_set(key: str, value: dict) -> None:
    _cache[key] = (time.monotonic(), value)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def _warn_once(kind: str, message: str) -> None:
    now = time.monotonic()
    if now - _last_warn.get(kind, 0.0) < _WARN_INTERVAL:
        return
    _last_warn[kind] = now
    log.warning("%s", message)


_VALID_LOCALES = {
    "ru-ru", "ua-uk", "us-en", "uk-en", "de-de", "wt-wt",
}


def _locale_for(region: str) -> str:
    """Приводит регион к валидной локали SearchApi (формат xx-yy)."""
    value = (region or "").strip().lower()
    mapping = {
        "ru": "ru-ru", "ru-ru": "ru-ru",
        "ua": "ua-uk", "uk": "ua-uk", "ua-uk": "ua-uk",
        "en": "us-en", "us": "us-en", "us-en": "us-en",
    }
    if value in mapping:
        return mapping[value]
    if value in {"", "wt-wt", "world"}:
        default = (settings.searchapi_locale or "").strip().lower()
        return default if default in _VALID_LOCALES else "wt-wt"
    return settings.searchapi_locale if settings.searchapi_locale in _VALID_LOCALES else "wt-wt"


_SAFE_MAP = {"1": "moderate", "0": "off", "2": "on", "moderate": "moderate",
             "off": "off", "on": "on"}


def _safe_for(value: str | None) -> str:
    """on/moderate/off. Старые 1/0/2: 1->moderate, 0->off, 2->on."""
    return _SAFE_MAP.get((value or "").strip().lower(), "moderate")


def _clean_url(url: str) -> str:
    """Разворачивает редиректы поисковых систем."""
    if not url:
        return ""
    if "uddg=" in url:
        try:
            query = urllib.parse.urlparse(url).query
            target = urllib.parse.parse_qs(query).get("uddg", [""])[0]
            if target:
                return urllib.parse.unquote(target)
        except Exception:  # noqa: BLE001
            pass
    if "RU=" in url:
        try:
            query = urllib.parse.urlparse(url).query
            target = urllib.parse.parse_qs(query).get("RU", [""])[0]
            if target:
                return urllib.parse.unquote(target)
        except Exception:  # noqa: BLE001
            pass
    if url.startswith("//"):
        return "https:" + url
    return url


def _normalize(raw: dict) -> dict | None:
    url = _clean_url(raw.get("link") or raw.get("href") or raw.get("url") or "")
    if not url or not url.startswith("http"):
        return None
    item = {
        "title": (raw.get("title") or url).strip(),
        "url": url,
        "snippet": (raw.get("snippet") or raw.get("body") or raw.get("content") or "").strip(),
    }
    if raw.get("position") is not None:
        item["position"] = raw["position"]
    if raw.get("favicon"):
        item["favicon"] = raw["favicon"]
    return item


def _parse_searchapi(data: object, max_results: int) -> dict:
    if not isinstance(data, dict):
        return {}
    results: list[dict] = []
    for raw in data.get("organic_results") or []:
        if isinstance(raw, dict):
            item = _normalize(raw)
            if item:
                results.append(item)
        if len(results) >= max_results:
            break

    knowledge = data.get("knowledge_graph") or data.get("knowledge_panel") or {}
    if not isinstance(knowledge, dict):
        knowledge = {}

    stories = []
    for raw in data.get("top_stories") or []:
        if not isinstance(raw, dict):
            continue
        item = _normalize(raw)
        if item:
            if raw.get("source"):
                item["source"] = raw["source"]
            if raw.get("date"):
                item["date"] = raw["date"]
            stories.append(item)

    related = []
    for raw in data.get("related_searches") or []:
        if isinstance(raw, str):
            related.append({"query": raw})
        elif isinstance(raw, dict):
            query = raw.get("query") or raw.get("title") or raw.get("name")
            if query:
                related.append({"query": str(query), "link": raw.get("link") or raw.get("url")})

    ai_overview = {}
    raw_ai = data.get("ai_overview")
    if isinstance(raw_ai, dict):
        answer = raw_ai.get("answer") or raw_ai.get("text") or ""
        sources = []
        for src in raw_ai.get("sources") or []:
            if isinstance(src, dict) and (src.get("link") or src.get("url")):
                sources.append({
                    "title": (src.get("title") or src.get("name") or "").strip(),
                    "url": _clean_url(src.get("link") or src.get("url") or ""),
                })
        if answer or sources:
            ai_overview = {"answer": answer, "sources": sources[:5]}

    return {
        "results": results,
        "knowledge_graph": knowledge,
        "top_stories": stories[:10],
        "related_searches": related[:12],
        "ai_overview": ai_overview,
    }


def _error_code(error: str | None) -> str:
    """Переводит текст ошибки http.get_json в код результата."""
    if not error:
        return "unavailable"
    if error.startswith(("http_401", "http_403")):
        return "auth"
    if error.startswith(("http_402", "http_429")):
        return "quota"
    if error.startswith("http_400"):
        return "bad_params"
    return "unavailable"


async def _search_searchapi(
    query: str,
    max_results: int,
    locale: str,
    safe: str,
    time_period: str | None,
    timeout: int,
) -> tuple[dict | None, str | None]:
    """Возвращает (payload, error_code). error_code None — успех."""
    if not settings.searchapi_key:
        return None, "no_key"
    params: dict = {
        "engine": "duckduckgo",
        "q": query,
        "locale": locale,
        "safe": safe,
    }
    if time_period:
        params["time_period"] = time_period
    headers = {"Authorization": f"Bearer {settings.searchapi_key}"}
    data, error = await get_json(
        SEARCHAPI_URL,
        params=params,
        headers=headers,
        ttl=0,
        timeout=min(max(timeout, 3), SEARCHAPI_TIMEOUT),
    )
    if error or not isinstance(data, dict):
        code = _error_code(error)
        _log_searchapi_failure(error, code)
        return None, code
    parsed = _parse_searchapi(data, max_results)
    if not parsed.get("results") and not parsed.get("ai_overview"):
        return parsed, None
    return parsed, None


def _log_searchapi_failure(error: str | None, code: str) -> None:
    reason = {
        "auth": "неверный ключ (401/403)",
        "quota": "лимит или кредиты исчерпаны (402/429)",
        "bad_params": "неверные параметры запроса (400)",
        "unavailable": "сервис недоступен",
    }.get(code, "неизвестная ошибка")
    if error and error.startswith("timeout"):
        reason = "таймаут"
    _warn_once(code, f"SearchApi: {reason}")


def _search_ddgs_sync(query: str, max_results: int, region: str, timeout: int) -> list[dict]:
    if _DDGS is None:
        return []
    out: list[dict] = []
    ddgs = _DDGS(timeout=timeout)
    try:
        for raw in ddgs.text(query, region=region, safesearch="moderate", max_results=max_results) or []:
            if isinstance(raw, dict):
                norm = _normalize(raw)
                if norm:
                    out.append(norm)
    finally:
        close = getattr(ddgs, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
    return out


async def _search_ddgs(query: str, max_results: int, region: str, timeout: int) -> list[dict]:
    if _DDGS is None:
        return []
    try:
        return await asyncio.to_thread(_search_ddgs_sync, query, max_results, region, timeout)
    except Exception as exc:  # noqa: BLE001
        log.debug("ddgs library failed: %s", type(exc).__name__)
        return []


def _parse_ddg_lite(html_text: str, max_results: int) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    links = soup.select("a.result-link")
    snippets = soup.select(".result-snippet")
    out: list[dict] = []
    for i, a in enumerate(links):
        url = _clean_url(a.get("href") or "")
        if not url.startswith("http"):
            continue
        snippet = snippets[i].get_text(" ", strip=True) if i < len(snippets) else ""
        out.append({"title": a.get_text(strip=True) or url, "url": url, "snippet": snippet})
        if len(out) >= max_results:
            break
    return out


async def _search_ddg_lite(query: str, max_results: int, timeout: int) -> list[dict]:
    status, html_text = await fetch_text(
        "https://lite.duckduckgo.com/lite/",
        method="POST",
        data={"q": query},
        headers=_FORM_HEADERS,
        timeout=timeout,
        max_bytes=256 * 1024,
    )
    if status != 200 or not html_text:
        return []
    try:
        return await asyncio.to_thread(_parse_ddg_lite, html_text, max_results)
    except Exception as exc:  # noqa: BLE001
        log.debug("ddg lite parse failed: %s", type(exc).__name__)
        return []


def _parse_yahoo(html_text: str, max_results: int) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    out: list[dict] = []
    for block in soup.select("div.algo, div.dd.algo"):
        a = block.select_one("h3.title a") or block.select_one("a")
        if not a:
            continue
        url = _clean_url(a.get("href") or "")
        if not url.startswith("http"):
            continue
        snippet_el = block.select_one("div.compText, p")
        out.append({
            "title": a.get_text(" ", strip=True) or url,
            "url": url,
            "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
        })
        if len(out) >= max_results:
            break
    return out


async def _search_yahoo(query: str, max_results: int, timeout: int) -> list[dict]:
    status, html_text = await fetch_text(
        "https://search.yahoo.com/search",
        params={"p": query},
        headers={"User-Agent": _UA},
        timeout=timeout,
        max_bytes=256 * 1024,
    )
    if status != 200 or not html_text:
        return []
    try:
        return await asyncio.to_thread(_parse_yahoo, html_text, max_results)
    except Exception as exc:  # noqa: BLE001
        log.debug("yahoo parse failed: %s", type(exc).__name__)
        return []


def _empty(error: str | None = "none") -> dict:
    return {
        "results": [],
        "knowledge_graph": {},
        "top_stories": [],
        "related_searches": [],
        "ai_overview": {},
        "source": "none",
        "error": error,
    }


async def search_detailed(
    query: str,
    max_results: int = 8,
    region: str = "wt-wt",
    timeout: int = 5,
    safe: str = "moderate",
    time_period: str | None = None,
) -> dict:
    """Результаты выдачи с полями source/error и блоками справки и AI."""
    global _no_key_warned
    query = (query or "").strip()
    if not query:
        return _empty("bad_params")

    locale = _locale_for(region)
    safe_value = _safe_for(safe)
    key = _cache_key(locale, safe_value, time_period, max_results, query)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    if not settings.searchapi_key and not _no_key_warned:
        _no_key_warned = True
        log.warning("SEARCHAPI_KEY пуст: работает только резервный поиск")

    # Основной и резервные источники опрашиваются одновременно: результат
    # берём от самого приоритетного, остальные не тормозят выдачу.
    searchapi, ddgs, lite, yahoo = await gather_with_deadline(
        _search_searchapi(query, max_results, locale, safe_value, time_period, timeout),
        _search_ddgs(query, max_results, region, timeout),
        _search_ddg_lite(query, max_results, timeout),
        _search_yahoo(query, max_results, timeout),
    )

    error_code = "unavailable"
    if isinstance(searchapi, tuple):
        payload, error_code = searchapi
        if payload is not None and (payload.get("results") or payload.get("ai_overview")):
            result = {**payload, "source": "searchapi", "error": None}
            _cache_set(key, result)
            return result
    elif isinstance(searchapi, Exception):
        log.debug("SearchApi failed: %s", type(searchapi).__name__)

    for source, results in zip(("ddgs", "lite", "yahoo"), (ddgs, lite, yahoo)):
        if isinstance(results, Exception):
            log.debug("web_search fallback %s failed: %s", source, type(results).__name__)
            continue
        if results:
            result = _empty(None)
            result.update({"results": results[:max_results], "source": source})
            _cache_set(key, result)
            return result
        log.debug("web_search fallback %s returned nothing", source)

    _warn_once("all_empty", "Все источники веб-поиска вернули пусто")
    return _empty(error_code or "unavailable")


async def search(
    query: str,
    max_results: int = 8,
    region: str = "wt-wt",
    timeout: int = 5,
) -> list[dict]:
    """Совместимый интерфейс: только список органических результатов."""
    payload = await search_detailed(
        query, max_results=max_results, region=region, timeout=timeout
    )
    return payload.get("results") or []


def _merge_unique(target: list[dict], items: list[dict]) -> None:
    seen = {item.get("url") for item in target if item.get("url")}
    for item in items:
        url = item.get("url") or item.get("link")
        if not url or url in seen:
            continue
        seen.add(url)
        target.append(item)


@profiled("web.search_many")
async def search_many_detailed(
    queries: list[str],
    max_results_each: int = 6,
    region: str = "wt-wt",
    timeout: int = 5,
    limit: int = 3,
    safe: str = "moderate",
    time_period: str | None = None,
) -> dict:
    """Параллельно ищет несколько запросов и собирает единый набор."""
    picked = [q for q in queries if q][:limit]
    if not picked:
        return _empty("bad_params")

    groups = await gather_with_deadline(
        *(
            search_detailed(
                q,
                max_results=max_results_each,
                region=region,
                timeout=timeout,
                safe=safe,
                time_period=time_period,
            )
            for q in picked
        )
    )

    merged: dict = _empty(None)
    merged["ai_overview"] = {}
    seen_related: set[str] = set()
    errors: list[str] = []
    sources: set[str] = set()
    for group in groups:
        if isinstance(group, Exception):
            log.warning("web_search group failed: %s: %s", type(group).__name__, str(group)[:200])
            continue
        _merge_unique(merged["results"], group.get("results") or [])
        _merge_unique(merged["top_stories"], group.get("top_stories") or [])
        if not merged["knowledge_graph"] and group.get("knowledge_graph"):
            merged["knowledge_graph"] = group["knowledge_graph"]
        if not merged["ai_overview"] and group.get("ai_overview"):
            merged["ai_overview"] = group["ai_overview"]
        for item in group.get("related_searches") or []:
            query = item.get("query")
            if query and query not in seen_related:
                seen_related.add(query)
                merged["related_searches"].append(item)
        if group.get("source"):
            sources.add(group["source"])
        if group.get("error"):
            errors.append(group["error"])

    if merged["results"]:
        merged["source"] = next(iter(sources)) if len(sources) == 1 else "searchapi"
        merged["error"] = None
    else:
        merged["source"] = "none"
        merged["error"] = errors[0] if errors else "unavailable"
    return merged


async def search_many(
    queries: list[str],
    max_results_each: int = 6,
    region: str = "wt-wt",
    timeout: int = 5,
    limit: int = 3,
) -> list[dict]:
    payload = await search_many_detailed(
        queries,
        max_results_each=max_results_each,
        region=region,
        timeout=timeout,
        limit=limit,
    )
    return payload.get("results") or []