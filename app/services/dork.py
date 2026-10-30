"""Генератор dork-запросов на локальных правилах (без внешних API)."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{6,18}\d")
_DOMAIN_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,24}\b", re.IGNORECASE)
_USERNAME_RE = re.compile(r"(?<![\w.+-])@([A-Za-z][A-Za-z0-9_]{4,31})")

ENGINES: dict[str, dict[str, str]] = {
    "google": {"name": "Google"},
    "yandex": {"name": "Yandex"},
    "bing": {"name": "Bing"},
    "duckduckgo": {"name": "DuckDuckGo"},
}

_ENGINE_URLS: dict[str, str] = {
    "google": "https://www.google.com/search?q=",
    "yandex": "https://yandex.ru/search/?text=",
    "bing": "https://www.bing.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
}


def engine_search_url(engine: str, query: str) -> str:
    """Прямая ссылка на поиск запроса в выбранном движке."""
    base = _ENGINE_URLS.get(engine, _ENGINE_URLS["google"])
    return base + quote_plus(query)


def extract_entities(text: str) -> dict[str, list[str]]:
    """Извлекает email, телефоны, никнеймы, домены и ключевые слова."""
    text = text or ""
    emails = list(dict.fromkeys(_EMAIL_RE.findall(text)))
    phones = list(
        dict.fromkeys(
            m.strip()
            for m in _PHONE_RE.findall(text)
            if 8 <= len(re.sub(r"\D", "", m)) <= 15
        )
    )
    usernames = list(dict.fromkeys(_USERNAME_RE.findall(text)))

    excluded = {e.lower() for e in emails} | {u.lower() for u in usernames}
    domains = list(dict.fromkeys(d.lower() for d in _DOMAIN_RE.findall(text) if d.lower() not in excluded))

    consumed = {e.lower() for e in emails + phones} | {u.lower() for u in usernames} | set(domains)
    keywords: list[str] = []
    for token in re.split(r"[\n,;]+", text):
        token = token.strip()
        if not token or len(token) > 100:
            continue
        low = token.lower()
        if low in consumed or any(c in low for c in consumed if c):
            continue
        keywords.append(token)
    return {
        "emails": emails,
        "phones": phones,
        "usernames": usernames,
        "domains": domains,
        "keywords": list(dict.fromkeys(keywords))[:10],
    }


def generate_dorks_fallback(entities: dict, engine: str) -> list[dict]:
    """Локальные правила: 25–35 точных запросов с операторами site/inurl/intitle/filetype.

    Без внешних API и тяжёлых вычислений: обычная сборка строк по правилам.
    """
    out: list[dict] = []
    file_op = "filetype:"
    extensions = ("pdf", "xlsx", "docx", "sql", "txt", "env", "log")

    def add(query: str, category: str, purpose: str, priority: int = 2) -> None:
        query = " ".join((query or "").split())
        if query and not any(x["query"] == query for x in out):
            out.append(
                {
                    "query": query,
                    "category": category,
                    "purpose": purpose,
                    "priority": priority,
                }
            )

    emails = entities.get("emails", [])
    usernames = entities.get("usernames", [])
    phones = entities.get("phones", [])
    domains = entities.get("domains", [])
    keywords = entities.get("keywords", [])

    for email in emails:
        add(f'"{email}"', "Email", "точное совпадение", 1)
        add(f'"{email}" {file_op}pdf', "Email", "документы с адресом", 2)
        add(f'"{email}" {file_op}xlsx', "Email", "таблицы с адресом", 3)
        add(f'"{email}" {file_op}sql', "Email", "дампы баз данных", 1)
        add(f'"{email}" {file_op}env', "Email", "конфигурации и секреты", 1)
        add(f'"{email}" {file_op}log', "Email", "логи и журналы", 2)
        add(f'"{email}" site:github.com', "Email", "публичные репозитории", 2)
        add(f'"{email}" site:pastebin.com', "Email", "публичные вставки", 2)
        add(f'"{email}" inurl:admin', "Email", "админ-панели", 3)

    for username in usernames:
        add(f'"{username}"', "Никнейм", "точное совпадение", 1)
        for site, purpose in (
            ("t.me", "профиль Telegram"),
            ("vk.com", "профиль VK"),
            ("github.com", "профиль GitHub"),
            ("instagram.com", "профиль Instagram"),
            ("x.com", "профиль X"),
        ):
            add(f'site:{site} "{username}"', "Соцсети", purpose, 2)
        add(f'inurl:{username}', "URL", "никнейм в адресе страницы", 2)
        add(f'intitle:"{username}"', "Заголовки", "никнейм в заголовке", 2)
        add(f'"{username}" {file_op}pdf', "Документы", "упоминания в PDF", 3)
        add(f'"{username}" {file_op}txt', "Документы", "упоминания в текстах", 3)

    for phone in phones:
        digits = re.sub(r"\D", "", phone)
        add(f'"{digits}"', "Телефон", "номер полностью", 1)
        add(f'"{digits[-7:]}"', "Телефон", "последние 7 цифр", 2)
        add(f'"{digits}" {file_op}pdf', "Телефон", "документы с номером", 3)
        add(f'"{digits}" site:avito.ru', "Телефон", "объявления", 3)
        add(f'"{digits}" inurl:contact', "Телефон", "контактные страницы", 3)

    for domain in domains:
        add(f"site:{domain}", "Домен", "все страницы домена", 1)
        add(f"site:{domain} inurl:admin", "Домен", "админ-панели", 1)
        add(f"site:{domain} inurl:login", "Домен", "страницы входа", 1)
        add(f'site:{domain} intitle:"index of"', "Домен", "открытые каталоги", 1)
        add(f"site:{domain} inurl:api", "Домен", "API-эндпоинты", 2)
        add(f'site:{domain} intitle:"phpinfo"', "Домен", "отладочные страницы", 2)
        for ext in extensions:
            add(f"site:{domain} {file_op}{ext}", "Документы", f"файлы {ext} на домене", 2)
        add(f'"{domain}" -site:{domain}', "Домен", "упоминания вне домена", 2)
        add(f'"{domain}" {file_op}sql', "Домен", "дампы базы данных", 1)
        add(f'"{domain}" {file_op}env', "Домен", "файлы конфигурации", 1)

    for keyword in keywords:
        add(f'"{keyword}"', "Ключевое слово", "контекстный поиск", 2)
        add(f'"{keyword}" {file_op}pdf', "Ключевое слово", "документы", 3)
        add(f'intitle:"{keyword}"', "Ключевое слово", "заголовки страниц", 3)
        add(f'inurl:{quote_plus(keyword)[:40]}', "Ключевое слово", "ключ в URL", 3)

    # Добираем до нижней границы 25, если исходных данных мало.
    if len(out) < 25:
        seed = (domains or emails or usernames or keywords or phones or ["open data"])[0]
        seed_slug = re.sub(r"[^A-Za-z0-9_.-]+", "", seed)[:32] or "profile"
        padding = (
            (f'"{seed}"', "Общее", "точное совпадение", 3),
            (f'"{seed}" {file_op}pdf', "Документы", "PDF-документы", 3),
            (f'"{seed}" {file_op}xlsx', "Документы", "таблицы", 3),
            (f'"{seed}" {file_op}docx', "Документы", "текстовые документы", 3),
            (f'"{seed}" {file_op}sql', "Документы", "SQL-дампы", 3),
            (f'"{seed}" {file_op}txt', "Документы", "текстовые файлы", 3),
            (f'"{seed}" {file_op}env', "Документы", "конфигурации", 3),
            (f'"{seed}" {file_op}log', "Документы", "логи", 3),
            (f'"{seed}" site:github.com', "Общее", "репозитории", 3),
            (f'"{seed}" site:pastebin.com', "Общее", "публичные вставки", 3),
            (f'"{seed}" site:medium.com', "Общее", "статьи и упоминания", 3),
            (f'intitle:"{seed}"', "Общее", "заголовки страниц", 3),
            (f'inurl:{seed_slug}', "Общее", "упоминание в URL", 3),
            (f'"{seed}" inurl:contact', "Общее", "контактные страницы", 3),
            (f'"{seed}" inurl:admin', "Общее", "админ-страницы", 3),
            (f'"{seed}" inurl:login', "Общее", "страницы входа", 3),
            (f'site:docs.google.com "{seed}"', "Общее", "открытые документы", 3),
            (f'site:trello.com "{seed}"', "Общее", "рабочие доски", 3),
            (f'site:notion.site "{seed}"', "Общее", "публичные страницы", 3),
            (f'"{seed}" {file_op}csv', "Документы", "таблицы CSV", 3),
            (f'"{seed}" {file_op}json', "Документы", "JSON-данные", 3),
            (f'"{seed}" {file_op}xml', "Документы", "XML-данные", 3),
            (f'"{seed}" {file_op}bak', "Документы", "резервные копии", 3),
            (f'"{seed}" {file_op}yml', "Документы", "YAML-конфигурации", 3),
            (f'"{seed}" {file_op}yaml', "Документы", "YAML-конфигурации", 3),
            (f'"{seed}" site:reddit.com', "Общее", "обсуждения", 3),
            (f'"{seed}" site:medium.com', "Общее", "статьи", 3),
            (f'"{seed}" site:pastebin.com', "Общее", "публичные вставки", 3),
            (f'"{seed}" site:notion.site', "Общее", "публичные страницы", 3),
            (f'intitle:login "{seed}"', "Общее", "страницы входа", 3),
            (f'inurl:user "{seed}"', "Общее", "профили пользователей", 3),
            (f'inurl:profile "{seed}"', "Общее", "страницы профилей", 3),
        )
        for args in padding:
            if len(out) >= 25:
                break
            add(*args)

    out.sort(key=lambda x: (x["priority"], x["category"]))
    return out[:35]

async def generate_dorks(entities: dict, engine: str) -> list[dict]:
    return generate_dorks_fallback(entities, engine)


def build_txt(
    entities: dict,
    engine_code: str,
    items: list[dict],
    web_results: list[dict] | None = None,
) -> bytes:
    engine_name = ENGINES.get(engine_code, {}).get("name", engine_code)
    lines = [
        "DORK REPORT",
        "=" * 50,
        f"Дата: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"Поисковик: {engine_name}",
        "",
        "Исходные данные:",
    ]
    for key, label in (
        ("emails", "Email"),
        ("phones", "Телефон"),
        ("usernames", "Никнейм"),
        ("domains", "Домен"),
        ("keywords", "Ключ"),
    ):
        for value in entities.get(key, []):
            lines.append(f"  {label}: {value}")

    lines.append("")
    by_category: dict[str, list[dict]] = {}
    for item in items:
        by_category.setdefault(item["category"], []).append(item)

    index = 0
    for category, dorks in by_category.items():
        lines.append(f"[{category}]")
        for dork in dorks:
            index += 1
            lines.append(f"{index}. {dork['query']}")
            if dork.get("purpose"):
                lines.append(f"   {dork['purpose']}")
        lines.append("")
    lines.append(f"Всего: {index}")

    if web_results:
        lines.append("")
        lines.append("ЖИВЫЕ РЕЗУЛЬТАТЫ (DuckDuckGo):")
        for i, r in enumerate(web_results, 1):
            lines.append(f"{i}. {r.get('title', '')}")
            lines.append(f"   {r.get('url', '')}")
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
    return ("\n".join(lines) + "\n").encode("utf-8")