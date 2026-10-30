"""HTML-отчёт по никнейму: активные профили по категориям."""
from __future__ import annotations

from app.reports.base import esc, render_report
from app.services.username_search import CATEGORIES


def build(findings: dict, language: str = "ru") -> bytes:
    ru = language == "ru"
    username = findings.get("username", "")
    found = findings.get("found") or []
    checked = findings.get("checked", 0)

    sections: list[dict] = []
    if found:
        grouped: dict[str, list[dict]] = {}
        for profile in found:
            grouped.setdefault(profile.get("category") or "Другое", []).append(profile)

        order = list(CATEGORIES) + [c for c in grouped if c not in CATEGORIES]
        for category in order:
            profiles = grouped.get(category)
            if not profiles:
                continue
            links = "".join(
                f'<a href="{esc(profile.get("url", ""))}" target="_blank">'
                f'{esc(profile.get("site", ""))}'
                f'<span class="domain">{esc(profile.get("url", ""))}</span></a>'
                for profile in profiles
            )
            sections.append(
                {
                    "id": f"cat-{category}",
                    "title": f"{category} · {len(profiles)}",
                    "html": f'<div class="list">{links}</div>',
                }
            )
    else:
        sections = [
            {
                "id": "found",
                "title": "Активные профили" if ru else "Active profiles",
                "note": "Профили не найдены" if ru else "No profiles found",
            }
        ]

    return render_report(
        title=f"@{username}",
        kind="Username",
        subtitle="OSINT · " + ("поиск по никнейму" if ru else "username search"),
        language=language,
        sections=sections,
        stats=[
            (len(found), "найдено" if ru else "found"),
            (checked, "проверено" if ru else "checked"),
        ],
    )