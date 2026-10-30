"""HTML-отчёт Dork: запросы + живые результаты в виде карточек."""
from __future__ import annotations

from urllib.parse import urlparse

from app.reports.base import esc, render_report
from app.services.dork import ENGINES, engine_search_url


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:  # noqa: BLE001
        return ""


def _cards(items: list[dict], language: str = "ru") -> str:
    cards = []
    for item in items:
        url = item.get("url") or item.get("link") or ""
        if not url.startswith("http"):
            continue
        title = item.get("title") or url
        snippet = item.get("snippet") or ""
        meta = []
        source = item.get("source")
        date = item.get("date")
        if source:
            meta.append(str(source))
        if date:
            meta.append(str(date))
        meta.append(_domain(url))
        cards.append(
            '<div class="result-card">'
            f'<a class="result-title" href="{esc(url)}" target="_blank">{esc(title)}</a>'
            f'<div class="result-domain">{esc(" · ".join(meta))}</div>'
            + (f'<div class="result-snippet">{esc(snippet)}</div>' if snippet else "")
            + "</div>"
        )
    return "".join(cards)


def _knowledge_card(knowledge: dict, language: str) -> str:
    if not knowledge:
        return ""
    title = knowledge.get("title") or knowledge.get("name")
    description = knowledge.get("description") or knowledge.get("snippet")
    source = knowledge.get("source") or knowledge.get("url")
    if not (title or description):
        return ""
    parts = ['<div class="result-card">']
    if title:
        parts.append(f'<div class="result-title">{esc(title)}</div>')
    if description:
        parts.append(f'<div class="result-snippet">{esc(description)}</div>')
    if source:
        parts.append(f'<div class="result-domain">{esc(source)}</div>')
    parts.append("</div>")
    return "".join(parts)


def build(
    entities: dict,
    engine_code: str,
    items: list[dict],
    web_results: dict | list | None = None,
    language: str = "ru",
) -> bytes:
    ru = language == "ru"
    engine_name = ENGINES.get(engine_code, {}).get("name", engine_code)
    sections: list[dict] = []

    if isinstance(web_results, dict):
        live = web_results
    else:
        live = {"results": web_results or []}
    results = live.get("results") or []
    live_error = live.get("error")
    knowledge = live.get("knowledge_graph") or {}
    stories = live.get("top_stories") or []
    related = live.get("related_searches") or []

    src_rows = []
    for key, label in (
        ("emails", "Email"),
        ("phones", "Телефон" if ru else "Phone"),
        ("usernames", "Никнейм" if ru else "Username"),
        ("domains", "Домен" if ru else "Domain"),
        ("keywords", "Ключевое слово" if ru else "Keyword"),
    ):
        for value in entities.get(key, []):
            src_rows.append((label, value, True))
    if src_rows:
        sections.append(
            {
                "id": "source",
                "title": "Исходные данные" if ru else "Input data",
                "rows": src_rows,
            }
        )

    by_category: dict[str, list[dict]] = {}
    for item in items:
        by_category.setdefault(item["category"], []).append(item)

    cards = []
    for category, group in by_category.items():
        links = "".join(
            f'<a href="{esc(engine_search_url(engine_code, it["query"]))}" target="_blank">'
            f'{esc(it["query"])}<span class="domain">{esc(it.get("purpose", ""))}</span></a>'
            for it in group
        )
        cards.append({"title": category, "rows": [], "html": f'<div class="list">{links}</div>'})
    if cards:
        sections.append(
            {
                "id": "dorks",
                "title": (
                    f"Запросы {engine_name} · {len(items)}"
                    if ru
                    else f"{engine_name} queries · {len(items)}"
                ),
                "cards": cards,
            }
        )

    live_html = ""
    knowledge_html = _knowledge_card(knowledge, language)
    if knowledge_html:
        live_html += f'<div class="card-title">{"Knowledge" if not ru else "Справка"}</div>{knowledge_html}'
    if results:
        live_html += _cards(results, language)
    if stories:
        live_html += (
            f'<div class="card-title">{"Top stories" if not ru else "Свежие публикации"}</div>'
            + _cards(stories, language)
        )
    if related:
        chips = "".join(
            f'<span class="chip">{esc(item.get("query", ""))}</span>'
            for item in related
            if item.get("query")
        )
        if chips:
            live_html += (
                f'<div class="card-title">{"Related searches" if not ru else "Похожие запросы"}</div>'
                f'<div class="chips">{chips}</div>'
            )

    if live_html:
        sections.append(
            {
                "id": "live",
                "title": (
                    f"Живой поиск · {len(results)}"
                    if ru
                    else f"Live results · {len(results)}"
                ),
                "html": live_html,
            }
        )
    else:
        sections.append(
            {
                "id": "live",
                "title": "Живой поиск" if ru else "Live results",
                "note": (
                    "Поиск временно недоступен. Запросы сформированы, "
                    "попробуйте повторить позже."
                    if live_error
                    else (
                        "Живой поиск не вернул результатов."
                        if ru
                        else "Live search returned no results."
                    )
                ),
            }
        )

    return render_report(
        title="Dork" if ru else "Dork report",
        kind="Dork",
        subtitle=(
            f"{engine_name} · генератор поисковых запросов"
            if ru
            else f"{engine_name} · search query generator"
        ),
        language=language,
        sections=sections,
        stats=[
            (len(items), "запросов" if ru else "queries"),
            (len(results), "результатов" if ru else "results"),
        ],
    )