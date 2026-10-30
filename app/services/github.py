"""GitHub OSINT: публичный профиль, репозитории, языки, активность.

Адаптация приложенного github_full.py под архитектуру бота:
- единый httpx-клиент проекта (keep-alive, DNS-кэш, лимиты);
- токен опционален и берётся ТОЛЬКО из окружения (GITHUB_TOKEN);
- без токена GitHub отдаёт 60 запросов/час, поэтому число запросов жёстко
  ограничено (профиль + репозитории + агрегаты), а не «всё, что можно»;
- 403/429 (rate limit) не маскируются под «не найдено»: пользователь видит
  нейтральное «технические работы», во внутренний лог уходит статус и метод;
- тяжёлый перебор коммитов/подписчиков урезан: это не выгрузка базы, а
  справка о публичном профиле.
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from app.config import settings
from app.services.http import gather_with_deadline, get_json

log = logging.getLogger(__name__)

API = "https://api.github.com"
_MAX_PAGES = 3  # users/{name}/repos: 3 страницы по 100 = 300 репозиториев


class GitHubError(RuntimeError):
    def __init__(self, kind: str, *, status: int | None = None, path: str = ""):
        super().__init__(kind)
        self.kind = kind
        self.status = status
        self.path = path


def user_key(error: GitHubError) -> str:
    if error.kind == "not_found":
        return "not_found"
    if error.kind == "invalid_input":
        return "invalid_input"
    return "source_maintenance"


def _headers() -> dict[str, str]:
    headers = {
        "User-Agent": "INFEX-OSINT-Bot",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


async def _get(path: str, params: dict | None = None) -> Any:
    """GET api.github.com. Возвращает JSON или бросает GitHubError."""
    url = f"{API}/{path.lstrip('/')}"
    data, error = await get_json(
        url,
        params=params,
        headers=_headers(),
        ttl=60,
        timeout=settings.github_timeout,
    )
    if error:
        status: int | None = None
        if error.startswith("http_"):
            try:
                status = int(error.split(":", 1)[0].split("_", 1)[1])
            except (IndexError, ValueError):
                status = None
        if status == 404:
            raise GitHubError("not_found", status=status, path=path)
        if status == 422:
            raise GitHubError("invalid_input", status=status, path=path)
        if status in (403, 429):
            # rate limit или запрет — это НЕ «ничего не найдено»
            log.warning("GitHub rate limit/forbidden: status=%s path=%s", status, path)
            raise GitHubError("rate_limited", status=status, path=path)
        log.warning("GitHub error: kind=%s status=%s path=%s", "temporary", status, path)
        raise GitHubError("temporary", status=status, path=path)
    return data


def normalize_query(value: str) -> str:
    """@user, github.com/user, https://github.com/user/repo -> user."""
    value = (value or "").strip()
    low = value.lower()
    if "github.com/" in low:
        tail = value.split("github.com/", 1)[1]
        value = tail.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    value = value.strip().lstrip("@")
    return value


def is_valid_username(value: str) -> bool:
    if not value or len(value) > 39:
        return False
    if value.startswith("-") or value.endswith("-"):
        return False
    if "--" in value:
        return False
    return all(ch.isalnum() or ch == "-" for ch in value)


async def _repos(username: str) -> list[dict]:
    out: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        data = await _get(
            f"users/{username}/repos",
            {"per_page": 100, "page": page, "sort": "updated"},
        )
        if not isinstance(data, list) or not data:
            break
        out.extend(r for r in data if isinstance(r, dict))
        if len(data) < 100 or len(out) >= settings.github_max_repos:
            break
    return out[: settings.github_max_repos]


async def _languages(full_name: str) -> dict:
    try:
        data = await _get(f"repos/{full_name}/languages")
    except GitHubError:
        return {}
    return data if isinstance(data, dict) else {}


async def _contributors(full_name: str, limit: int = 5) -> list[dict]:
    try:
        data = await _get(f"repos/{full_name}/contributors", {"per_page": limit})
    except GitHubError:
        return []
    if not isinstance(data, list):
        return []
    return [
        {
            "login": c.get("login", ""),
            "commits": c.get("contributions", 0),
            "url": c.get("html_url", ""),
        }
        for c in data[:limit]
        if isinstance(c, dict)
    ]


async def _readme(full_name: str, limit: int = 600) -> str:
    try:
        data = await _get(f"repos/{full_name}/readme")
    except GitHubError:
        return ""
    if not isinstance(data, dict) or not data.get("content"):
        return ""
    try:
        raw = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""
    return raw[:limit]


async def _orgs(username: str) -> list[dict]:
    try:
        data = await _get(f"users/{username}/orgs", {"per_page": 30})
    except GitHubError:
        return []
    if not isinstance(data, list):
        return []
    return [
        {
            "login": o.get("login", ""),
            "url": o.get("html_url", ""),
            "description": (o.get("description") or "")[:160],
        }
        for o in data
        if isinstance(o, dict)
    ]


async def _emails(username: str, repos: list[dict]) -> set[str]:
    """Публичные email из коммитов (без noreply). Ограниченно: топ-8 репозиториев."""
    emails: set[str] = set()
    targets = [r.get("full_name") for r in repos[:8] if r.get("full_name")]
    if not targets:
        return emails

    async def one(full_name: str) -> set[str]:
        found: set[str] = set()
        try:
            commits = await _get(f"repos/{full_name}/commits", {"per_page": 30})
        except GitHubError:
            return found
        if not isinstance(commits, list):
            return found
        for commit in commits:
            if not isinstance(commit, dict):
                continue
            body = commit.get("commit") or {}
            for part in ("author", "committer"):
                email = (body.get(part) or {}).get("email")
                if email and "@" in email and "noreply" not in email.lower():
                    found.add(str(email).lower())
        return found

    results = await gather_with_deadline(
        *(one(name) for name in targets), timeout=settings.search_deadline
    )
    for result in results:
        if isinstance(result, set):
            emails |= result
    return emails


async def analyze(query: str) -> dict:
    """Полный публичный профиль GitHub. Возвращает структуру отчёта."""
    username = normalize_query(query)
    if not is_valid_username(username):
        return {"error": "invalid_input", "target": query}

    try:
        profile = await _get(f"users/{username}")
    except GitHubError as exc:
        return {"error": exc.kind, "target": query}

    if not isinstance(profile, dict):
        return {"error": "not_found", "target": query}

    login = profile.get("login") or username

    repos, orgs = await gather_with_deadline(
        _repos(login), _orgs(login), timeout=settings.search_deadline
    )
    if not isinstance(repos, list):
        repos = []
    if not isinstance(orgs, list):
        orgs = []

    # Детали топ-8 репозиториев + их языки. Больше не нужно для справки.
    top = repos[:8]
    lang_results = await gather_with_deadline(
        *(_languages(r.get("full_name", "")) for r in top if r.get("full_name")),
        timeout=settings.search_deadline,
    )

    languages: dict[str, int] = {}
    detailed: list[dict] = []
    for index, repo in enumerate(top):
        langs = lang_results[index] if index < len(lang_results) else {}
        if not isinstance(langs, dict):
            langs = {}
        for name, size in langs.items():
            try:
                languages[str(name)] = languages.get(str(name), 0) + int(size)
            except (TypeError, ValueError):
                continue
        detailed.append(
            {
                "name": repo.get("name", ""),
                "full_name": repo.get("full_name", ""),
                "description": (repo.get("description") or "")[:300],
                "language": repo.get("language") or "",
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "open_issues": repo.get("open_issues_count", 0),
                "created": (repo.get("created_at") or "")[:10],
                "updated": (repo.get("updated_at") or "")[:10],
                "url": repo.get("html_url", ""),
                "homepage": repo.get("homepage") or "",
                "fork": bool(repo.get("fork")),
                "archived": bool(repo.get("archived")),
                "license": (repo.get("license") or {}).get("name", ""),
                "topics": (repo.get("topics") or [])[:8],
            }
        )

    emails = await _emails(login, repos)
    if profile.get("email"):
        emails.add(str(profile["email"]).lower())

    total_stars = sum(int(r.get("stargazers_count") or 0) for r in repos)
    total_forks = sum(int(r.get("forks_count") or 0) for r in repos)

    return {
        "error": None,
        "target": query,
        "username": login,
        "id": profile.get("id"),
        "name": profile.get("name") or "",
        "avatar": profile.get("avatar_url") or "",
        "url": profile.get("html_url") or f"https://github.com/{login}",
        "bio": profile.get("bio") or "",
        "company": profile.get("company") or "",
        "blog": profile.get("blog") or "",
        "location": profile.get("location") or "",
        "email": profile.get("email") or "",
        "twitter": profile.get("twitter_username") or "",
        "type": profile.get("type") or "",
        "hireable": profile.get("hireable"),
        "site_admin": bool(profile.get("site_admin")),
        "public_repos": int(profile.get("public_repos") or 0),
        "public_gists": int(profile.get("public_gists") or 0),
        "followers": int(profile.get("followers") or 0),
        "following": int(profile.get("following") or 0),
        "created_at": (profile.get("created_at") or "")[:10],
        "updated_at": (profile.get("updated_at") or "")[:10],
        "repos_count": len(repos),
        "total_stars": total_stars,
        "total_forks": total_forks,
        "repos": detailed,
        "languages": languages,
        "organizations": orgs,
        "emails_found": sorted(emails),
        "rate_limited": False,
    }


async def contributors_of(full_name: str) -> list[dict]:
    """Дополнительно: участники конкретного репозитория (для карточки)."""
    return await _contributors(full_name)


async def readme_of(full_name: str) -> str:
    return await _readme(full_name)


__all__ = [
    "GitHubError",
    "analyze",
    "contributors_of",
    "is_valid_username",
    "normalize_query",
    "readme_of",
    "user_key",
]