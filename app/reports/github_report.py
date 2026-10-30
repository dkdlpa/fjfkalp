"""HTML-отчёт GitHub OSINT."""
from __future__ import annotations

from app.reports.base import esc, render_report


def _fmt(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def build(findings: dict, target: str, language: str = "ru") -> bytes:
    ru = language == "ru"
    sections: list[dict] = []

    main_rows = []
    username = findings.get("username") or target
    main_rows.append(("Username", "@" + str(username), True))
    for key, label in (
        ("name", "Имя" if ru else "Name"),
        ("bio", "Биография" if ru else "Bio"),
        ("company", "Компания" if ru else "Company"),
        ("location", "Локация" if ru else "Location"),
        ("email", "Email" if ru else "Email"),
        ("twitter", "Twitter / X" if ru else "Twitter / X"),
    ):
        value = findings.get(key)
        if value:
            mono = key in {"email", "twitter"}
            main_rows.append((label, ("@" + str(value)) if key == "twitter" else value, mono))
    if findings.get("blog"):
        main_rows.append(("Сайт" if ru else "Website", findings["blog"]))
    if findings.get("id"):
        main_rows.append(("ID", findings["id"], True))
    if findings.get("type"):
        main_rows.append(("Тип" if ru else "Type", findings["type"]))
    if findings.get("created_at"):
        main_rows.append(("Создан" if ru else "Created", findings["created_at"], True))
    if findings.get("url"):
        main_rows.append(("Профиль" if ru else "Profile", findings["url"]))
    sections.append({"id": "main", "title": "Профиль" if ru else "Profile", "rows": main_rows})

    emails = findings.get("emails_found") or []
    if emails:
        sections.append(
            {
                "id": "emails",
                "title": "Публичные email" if ru else "Public emails",
                "count": len(emails),
                "rows": [("Email", value, True) for value in emails[:30]],
                "note": (
                    "Email и имена взяты из публичных коммитов и профиля."
                    if ru
                    else "Emails come from public commits and the profile."
                ),
            }
        )

    languages = findings.get("languages") or {}
    if languages:
        total = sum(int(v) for v in languages.values()) or 1
        ordered = sorted(languages.items(), key=lambda kv: int(kv[1]), reverse=True)[:10]
        rows = [
            (name, f"{int(size) * 100 / total:.1f}%")
            for name, size in ordered
        ]
        sections.append(
            {"id": "langs", "title": "Языки" if ru else "Languages", "rows": rows}
        )

    orgs = findings.get("organizations") or []
    if orgs:
        links = "".join(
            f'<a href="{esc(o.get("url", ""))}" target="_blank">{esc(o.get("login", ""))}'
            f'<span class="domain">{esc(o.get("description", ""))}</span></a>'
            for o in orgs
            if o.get("url")
        )
        sections.append(
            {
                "id": "orgs",
                "title": ("Организации" if ru else "Organizations") + f" · {len(orgs)}",
                "html": f'<div class="list">{links}</div>',
            }
        )

    repos = findings.get("repos") or []
    if repos:
        cards = []
        for repo in repos:
            meta = []
            if repo.get("language"):
                meta.append(str(repo["language"]))
            meta.append(f'★ {_fmt(repo.get("stars", 0))}')
            meta.append(f'⑂ {_fmt(repo.get("forks", 0))}')
            if repo.get("updated"):
                meta.append(str(repo["updated"]))
            badges = []
            if repo.get("fork"):
                badges.append("fork")
            if repo.get("archived"):
                badges.append("archived")
            badges_html = (
                "".join(f'<span class="badge nft">{esc(b)}</span>' for b in badges)
            )
            description = repo.get("description") or ""
            cards.append(
                '<div class="result-card">'
                f'<a class="result-title" href="{esc(repo.get("url", ""))}" '
                f'target="_blank">{esc(repo.get("name", ""))}</a>'
                f'<div class="result-domain">{esc(" · ".join(meta))} {badges_html}</div>'
                + (f'<div class="result-snippet">{esc(description)}</div>' if description else "")
                + "</div>"
            )
        sections.append(
            {
                "id": "repos",
                "title": ("Репозитории" if ru else "Repositories") + f" · {len(repos)}",
                "html": "".join(cards),
                "note": (
                    "Показаны последние обновлённые публичные репозитории."
                    if ru
                    else "Most recently updated public repositories."
                ),
            }
        )
    else:
        sections.append(
            {
                "id": "repos",
                "title": "Репозитории" if ru else "Repositories",
                "note": "Публичных репозиториев нет." if ru else "No public repositories.",
            }
        )

    stats = [
        (_fmt(findings.get("repos_count", 0)), "репозиториев" if ru else "repos"),
        (_fmt(findings.get("followers", 0)), "подписчиков" if ru else "followers"),
        (_fmt(findings.get("total_stars", 0)), "звёзд" if ru else "stars"),
        (_fmt(findings.get("total_forks", 0)), "форков" if ru else "forks"),
    ]

    return render_report(
        title="GitHub",
        subtitle=(findings.get("url") or f"https://github.com/{username}"),
        language=language,
        sections=sections,
        stats=stats,
        tags=["GitHub"],
        source="GitHub API",
    )