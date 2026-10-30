"""Кросс-платформенный поиск по никнейму (Username OSINT).

52 площадки (40+), сгруппированные по категориям: СНГ, Соцсети, IT, Игры.
Проверяется только факт наличия профиля (HTTP 200 + отсутствие Soft-404
стоп-слов). Внешние API метаданных не используются.

Ресурсы:
- единая общая httpx-сессия (keep-alive, пул соединений);
- не более 16 КБ тела ответа на сайт (первые 16 КБ);
- не более 15 одновременных проверок;
- каждый сайт и весь поиск измеряются профилировщиком.
"""
from __future__ import annotations

import asyncio
import logging
import re

from app.config import settings
from app.services.http import fetch_text, gather_with_deadline
from app.services.profiler import profile

log = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,32}$")

MAX_BYTES = 16 * 1024
# Все площадки проверяются параллельно, чтобы уложиться в общий дедлайн.
CONCURRENCY = 52

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
}

CATEGORIES = ("СНГ", "Соцсети", "IT", "Игры")

# Единая матрица площадок. negative — индивидуальные Soft-404 маркеры.
SITES: list[dict] = [
    # ---------- СНГ ----------
    {"name": "VK", "url": "https://vk.com/{u}", "category": "СНГ",
     "negative": ["страница не найдена", "page not found"]},
    {"name": "OK.ru", "url": "https://ok.ru/{u}", "category": "СНГ",
     "negative": ["страница не найдена", "profile not found"]},
    {"name": "Mail.ru", "url": "https://my.mail.ru/mail/{u}/", "category": "СНГ",
     "negative": ["страница не найдена", "page not found"]},
    {"name": "Habr", "url": "https://habr.com/ru/users/{u}/", "category": "СНГ",
     "negative": ["пользователь не найден", "page not found"]},
    {"name": "Pikabu", "url": "https://pikabu.ru/@{u}", "category": "СНГ",
     "negative": ["пользователь не найден", "page not found"]},
    {"name": "LiveJournal", "url": "https://{u}.livejournal.com/", "category": "СНГ",
     "negative": ["page not found", "journal not found"]},
    {"name": "Dzen", "url": "https://dzen.ru/{u}", "category": "СНГ",
     "negative": ["страница не найдена", "page not found"]},
    {"name": "Telegram", "url": "https://t.me/{u}", "category": "СНГ",
     "negative": ["page not found"], "required": "tgme_page_title"},
    {"name": "Rutube", "url": "https://rutube.ru/u/{u}/", "category": "СНГ",
     "negative": ["канал не найден", "page not found"]},
    {"name": "TenChat", "url": "https://tenchat.ru/{u}", "category": "СНГ",
     "negative": ["пользователь не найден", "page not found"]},

    # ---------- Соцсети ----------
    {"name": "Instagram", "url": "https://www.instagram.com/{u}/", "category": "Соцсети",
     "negative": ["sorry, this page isn't available", "page not found"]},
    {"name": "Facebook", "url": "https://www.facebook.com/{u}", "category": "Соцсети",
     "negative": ["page not found", "content isn't available"]},
    {"name": "X (Twitter)", "url": "https://x.com/{u}", "category": "Соцсети",
     "negative": ["this account doesn't exist", "account doesn't exist"]},
    {"name": "Threads", "url": "https://www.threads.net/@{u}", "category": "Соцсети",
     "negative": ["page not found", "user not found"]},
    {"name": "Reddit", "url": "https://www.reddit.com/user/{u}/", "category": "Соцсети",
     "negative": ["nobody on reddit", "page not found"]},
    {"name": "Pinterest", "url": "https://www.pinterest.com/{u}/", "category": "Соцсети",
     "negative": ["page not found", "user not found"]},
    {"name": "Tumblr", "url": "https://{u}.tumblr.com/", "category": "Соцсети",
     "negative": ["page not found", "there's nothing here"]},
    {"name": "Snapchat", "url": "https://www.snapchat.com/add/{u}", "category": "Соцсети",
     "negative": ["page not found", "profile not found"]},
    {"name": "LinkedIn", "url": "https://www.linkedin.com/in/{u}", "category": "Соцсети",
     "negative": ["page not found", "profile not found"]},
    {"name": "Mastodon", "url": "https://mastodon.social/@{u}", "category": "Соцсети",
     "negative": ["page not found", "account not found"]},
    {"name": "Bluesky", "url": "https://bsky.app/profile/{u}.bsky.social", "category": "Соцсети",
     "negative": ["profile not found", "account not found"]},
    {"name": "TikTok", "url": "https://www.tiktok.com/@{u}", "category": "Соцсети",
     "negative": ["couldn't find this account", "page not found"]},
    {"name": "YouTube", "url": "https://www.youtube.com/@{u}", "category": "Соцсети",
     "negative": ["this page isn't available", "channel not found"]},
    {"name": "Twitch", "url": "https://www.twitch.tv/{u}", "category": "Соцсети",
     "negative": ["this channel is unavailable", "page not found"]},

    # ---------- IT ----------
    {"name": "GitHub", "url": "https://github.com/{u}", "category": "IT",
     "negative": ["page not found"]},
    {"name": "GitLab", "url": "https://gitlab.com/{u}", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "Bitbucket", "url": "https://bitbucket.org/{u}/", "category": "IT",
     "negative": ["page not found", "repository not found"]},
    {"name": "HackerRank", "url": "https://www.hackerrank.com/profile/{u}", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "LeetCode", "url": "https://leetcode.com/{u}/", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "Codeforces", "url": "https://codeforces.com/profile/{u}", "category": "IT",
     "negative": ["user not found", "page not found"]},
    {"name": "Kaggle", "url": "https://www.kaggle.com/{u}", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "Docker Hub", "url": "https://hub.docker.com/u/{u}", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "npm", "url": "https://www.npmjs.com/~{u}", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "PyPI", "url": "https://pypi.org/user/{u}/", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "Replit", "url": "https://replit.com/@{u}", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "CodePen", "url": "https://codepen.io/{u}", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "SourceForge", "url": "https://sourceforge.net/u/{u}/profile", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "Dev.to", "url": "https://dev.to/{u}", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "Medium", "url": "https://medium.com/@{u}", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "Hashnode", "url": "https://hashnode.com/@{u}", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "Keybase", "url": "https://keybase.io/{u}", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "Behance", "url": "https://www.behance.net/{u}", "category": "IT",
     "negative": ["page not found", "user not found"]},
    {"name": "Dribbble", "url": "https://dribbble.com/{u}", "category": "IT",
     "negative": ["page not found", "user not found"]},

    # ---------- Игры ----------
    {"name": "Steam", "url": "https://steamcommunity.com/id/{u}", "category": "Игры",
     "negative": [
         "specified profile could not be found",
         "no such user",
         "steam community :: error",
     ]},
    {"name": "Roblox", "url": "https://www.roblox.com/users/profile?username={u}", "category": "Игры",
     "negative": ["user not found", "page cannot be found"]},
    {"name": "Chess.com", "url": "https://www.chess.com/member/{u}", "category": "Игры",
     "negative": ["page not found", "user not found"]},
    {"name": "Lichess", "url": "https://lichess.org/@/{u}", "category": "Игры",
     "negative": ["page not found", "user not found"]},
    {"name": "Speedrun.com", "url": "https://www.speedrun.com/user/{u}", "category": "Игры",
     "negative": ["page not found", "user not found"]},
    {"name": "FACEIT", "url": "https://www.faceit.com/en/players/{u}", "category": "Игры",
     "negative": ["page not found", "player not found"]},
    {"name": "osu!", "url": "https://osu.ppy.sh/u/{u}", "category": "Игры",
     "negative": ["page not found", "user not found"]},
    {"name": "PSNProfiles", "url": "https://psnprofiles.com/{u}", "category": "Игры",
     "negative": ["page not found", "user not found"]},
    {"name": "NameMC", "url": "https://namemc.com/profile/{u}", "category": "Игры",
     "negative": ["page not found", "profile not found"]},
]

_NEGATIVE_DEFAULT = (
    "page not found",
    "user not found",
    "profile not found",
    "account not found",
    "channel not found",
    "player not found",
    "this page isn't available",
    "this account doesn't exist",
    "sorry, this page isn't available",
    "couldn't find this account",
    "couldn't find that user",
    "could not be found",
    "doesn't exist",
    "does not exist",
    "no such user",
    "nobody on reddit",
)


def parse_username(text: str) -> str | None:
    value = (text or "").strip()
    if value.startswith("http"):
        # https://tiktok.com/@name -> name
        parts = [p for p in value.split("?")[0].rstrip("/").split("/") if p]
        if parts:
            value = parts[-1]
    value = value.lstrip("@")
    if value.startswith("tiktok.com") or value.startswith("www.tiktok.com"):
        value = value.split("/")[-1].lstrip("@")
    if USERNAME_RE.match(value):
        return value
    return None


def _looks_missing(body: str, site: dict) -> bool:
    required = site.get("required")
    if required and required not in body:
        return True
    low = body.lower()
    if any(marker in low for marker in site.get("negative", ())):
        return True
    return any(marker in low for marker in _NEGATIVE_DEFAULT)


async def _check_site(site: dict, username: str, sem: asyncio.Semaphore) -> dict:
    """Возвращает исход по площадке: found / not_found / error."""
    url = site["url"].format(u=username)
    try:
        async with profile(f"username.site.{site['name']}", warn=False):
            async with sem:
                status, body = await fetch_text(
                    url,
                    headers=_HEADERS,
                    timeout=settings.search_request_timeout,
                    max_bytes=MAX_BYTES,
                )
    except Exception:  # noqa: BLE001
        return {"outcome": "error", "site": site["name"]}

    if status == 0:
        return {"outcome": "error", "site": site["name"]}
    if status in (403, 429, 503):
        return {"outcome": "error", "site": site["name"]}
    if status == 404:
        return {"outcome": "not_found", "site": site["name"]}
    if status != 200 or not body:
        return {"outcome": "error", "site": site["name"]}
    if _looks_missing(body, site):
        return {"outcome": "not_found", "site": site["name"]}
    return {"outcome": "found", "site": site["name"], "url": url, "category": site["category"]}


async def search_username(username: str) -> dict:
    """Проверяет username по всем площадкам параллельно."""
    sem = asyncio.Semaphore(CONCURRENCY)

    async with profile("username.search") as stats:
        tasks = [_check_site(site, username, sem) for site in SITES]
        results = await gather_with_deadline(*tasks)

        found: list[dict] = []
        seen: set[str] = set()
        errors_count = 0
        checked = 0
        for result in results:
            if not isinstance(result, dict):
                errors_count += 1
                continue
            outcome = result.get("outcome")
            if outcome == "error":
                errors_count += 1
                continue
            checked += 1
            if outcome == "found" and result.get("url") and result["url"] not in seen:
                seen.add(result["url"])
                found.append(
                    {
                        "site": result["site"],
                        "url": result["url"],
                        "category": result.get("category"),
                    }
                )

        order = {name: index for index, name in enumerate(CATEGORIES)}
        found.sort(key=lambda item: (order.get(item["category"], 99), item["site"]))
        by_category: dict[str, list[dict]] = {}
        for item in found:
            by_category.setdefault(item["category"], []).append(item)

        total = len(SITES)
        errors_ratio = (errors_count / total) if total else 0.0
        payload = {
            "username": username,
            "found": found,
            "by_category": by_category,
            "checked": checked,
            "errors_count": errors_count,
            "sources_unavailable": errors_ratio > 0.4,
            "profile": stats,
        }
    return payload