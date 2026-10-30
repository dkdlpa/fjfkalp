"""HTML-отчёт VK OSINT."""
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
    profile = findings.get("profile") or {}
    sections: list[dict] = []

    name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
    rows = []
    if name:
        rows.append(("Имя" if ru else "Name", name))
    rows.append(("ID", profile.get("id", "—"), True))
    if profile.get("domain"):
        rows.append(("Username", "@" + str(profile["domain"]), True))
    if profile.get("url"):
        rows.append(("Профиль" if ru else "Profile", profile["url"]))
    for key, label in (
        ("sex", "Пол" if ru else "Sex"),
        ("bdate", "Дата рождения" if ru else "Birth date"),
        ("city", "Город" if ru else "City"),
        ("country", "Страна" if ru else "Country"),
        ("home_town", "Родной город" if ru else "Home town"),
        ("status", "Статус" if ru else "Status"),
        ("about", "О себе" if ru else "About"),
        ("activities", "Деятельность" if ru else "Activities"),
        ("interests", "Интересы" if ru else "Interests"),
        ("music", "Музыка" if ru else "Music"),
        ("movies", "Фильмы" if ru else "Movies"),
        ("books", "Книги" if ru else "Books"),
        ("quotes", "Цитаты" if ru else "Quotes"),
        ("site", "Сайт" if ru else "Website"),
        ("education", "Образование" if ru else "Education"),
        ("last_seen", "Последний визит" if ru else "Last seen"),
    ):
        value = profile.get(key)
        if value:
            rows.append((label, value, key in {"site"}))
    rows.append(
        (
            "Верификация" if ru else "Verified",
            ("да" if ru else "yes") if profile.get("verified") else ("нет" if ru else "no"),
        )
    )
    rows.append(
        (
            "Онлайн" if ru else "Online",
            ("да" if ru else "yes") if profile.get("online") else ("нет" if ru else "no"),
            False,
            "value ok" if profile.get("online") else "value",
        )
    )
    sections.append(
        {"id": "main", "title": "Профиль" if ru else "Profile", "rows": rows}
    )

    groups = findings.get("groups") or {}
    if groups.get("items"):
        cards = []
        for group in groups["items"]:
            cards.append(
                '<div class="result-card">'
                f'<a class="result-title" href="https://vk.com/{esc(group.get("screen_name") or "")}" '
                f'target="_blank">{esc(group.get("name", ""))}</a>'
                f'<div class="result-domain">{esc(_fmt(group.get("members", 0)))} '
                f'{"участников" if ru else "members"}</div>'
                + (f'<div class="result-snippet">{esc(group.get("description", ""))}</div>'
                   if group.get("description") else "")
                + "</div>"
            )
        sections.append(
            {
                "id": "groups",
                "title": ("Группы" if ru else "Groups") + f" · {groups.get('count', len(cards))}",
                "html": "".join(cards),
            }
        )
    elif groups.get("error") == "auth_error":
        sections.append(
            {
                "id": "groups",
                "title": "Группы" if ru else "Groups",
                "note": "Список групп недоступен для этого профиля."
                if ru
                else "Group list is not available for this profile.",
            }
        )

    friends = findings.get("friends") or {}
    if friends.get("items"):
        links = "".join(
            f'<a href="{esc(f.get("url", ""))}" target="_blank">'
            f'{"● " if f.get("online") else ""}{esc(f.get("name", ""))}</a>'
            for f in friends["items"]
        )
        sections.append(
            {
                "id": "friends",
                "title": ("Друзья" if ru else "Friends") + f" · {friends.get('count', 0)}",
                "html": f'<div class="list">{links}</div>',
            }
        )

    wall = findings.get("wall") or {}
    if wall.get("items"):
        cards = []
        for post in wall["items"]:
            cards.append(
                '<div class="result-card">'
                f'<div class="result-domain">{esc(post.get("date", ""))}</div>'
                + (f'<div class="result-snippet">{esc(post.get("text", ""))}</div>'
                   if post.get("text") else "")
                + f'<div class="result-domain">♥ {post.get("likes", 0)} · '
                  f'↻ {post.get("reposts", 0)} · 👁 {post.get("views", 0)} · '
                  f'💬 {post.get("comments", 0)}</div>'
                f'<div class="result-domain"><a href="{esc(post.get("url", ""))}" '
                f'target="_blank">{esc(post.get("url", ""))}</a></div>'
                "</div>"
            )
        sections.append(
            {
                "id": "wall",
                "title": ("Записи" if ru else "Posts") + f" · {wall.get('count', 0)}",
                "html": "".join(cards),
            }
        )

    photos = findings.get("photos") or {}
    if photos.get("items"):
        rows = [
            ("Фото" if ru else "Photo", p.get("url", ""), True)
            for p in photos["items"]
            if p.get("url")
        ]
        sections.append(
            {
                "id": "photos",
                "title": ("Фото профиля" if ru else "Profile photos")
                + f" · {photos.get('count', len(rows))}",
                "rows": rows,
                "collapsed": True,
            }
        )

    if not any(s.get("rows") or s.get("html") for s in sections[1:]):
        sections.append(
            {
                "id": "limits",
                "title": "Ограничения данных" if ru else "Data limitations",
                "note": (
                    "Дополнительные данные скрыты настройками приватности профиля "
                    "или недоступны для этого типа токена."
                    if ru
                    else "Additional data is hidden by the profile's privacy settings "
                    "or unavailable for this token type."
                ),
            }
        )

    stats = [
        (_fmt(profile.get("followers", 0)), "подписчиков" if ru else "followers"),
        (_fmt(friends.get("count", 0)), "друзей" if ru else "friends"),
        (_fmt(groups.get("count", 0)), "групп" if ru else "groups"),
        (_fmt(wall.get("count", 0)), "записей" if ru else "posts"),
    ]

    return render_report(
        title="VK",
        subtitle=profile.get("url") or f"vk.com/{target}",
        language=language,
        sections=sections,
        stats=stats,
        tags=["VK API"],
        source="VK API",
    )