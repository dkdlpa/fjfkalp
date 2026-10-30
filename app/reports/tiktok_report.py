"""HTML-отчёт TikTok OSINT (чистый тёмный стиль)."""
from __future__ import annotations

from app.reports.base import render_report
from app.reports.countries import country_label


def fmt_num(n) -> str:
    try:
        n = int(n)
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)
    except Exception:  # noqa: BLE001
        return str(n)


def build(findings: dict, target: str, language: str = "ru") -> bytes:
    ru = language == "ru"
    p = findings.get("profile") or {}
    username = p.get("username") or findings.get("username") or target

    def yn(v):
        return ("да" if v else "нет") if ru else ("yes" if v else "no")

    rows: list[tuple] = [("Username", f"@{username}", True)]
    if p.get("nickname"):
        rows.append(("Никнейм" if ru else "Nickname", p["nickname"]))
    if p.get("bio"):
        rows.append(("Биография" if ru else "Bio", p["bio"]))
    if p.get("verified") is not None:
        rows.append(("Верификация" if ru else "Verified", yn(p["verified"])))
    if p.get("private_account") is not None:
        rows.append(("Приватный аккаунт" if ru else "Private account", yn(p["private_account"])))
    if p.get("sec_uid"):
        rows.append(("SecUID", p["sec_uid"], True))
    if p.get("user_id"):
        rows.append(("User ID", p["user_id"], True))

    region = p.get("region")
    rows.append(
        (
            "Страна" if ru else "Country",
            country_label(region, language) if region else "—",
        )
    )
    rows.append(("Язык" if ru else "Language", p.get("language") or "—"))

    if p.get("registration_date"):
        value = p["registration_date"]
        note = p.get("registration_note", "")
        if note:
            value = f"{value} ({note})"
        rows.append(("Дата регистрации" if ru else "Registration date", value, True))

    if p.get("avatar_url"):
        rows.append(("Аватар" if ru else "Avatar", p["avatar_url"], True))
    rows.append(("Профиль" if ru else "Profile", f"https://tiktok.com/@{username}", True))

    sections = [{"id": "profile", "title": "Профиль" if ru else "Profile", "rows": rows}]

    stats = [
        (fmt_num(p.get("followers", 0)), "подписчиков" if ru else "followers"),
        (fmt_num(p.get("following", 0)), "подписок" if ru else "following"),
        (fmt_num(p.get("likes", 0)), "лайков" if ru else "likes"),
        (fmt_num(p.get("video_count", 0)), "видео" if ru else "videos"),
    ]

    return render_report(
        title="TikTok",
        kind="TikTok",
        subtitle=f"@{username}",
        language=language,
        sections=sections,
        stats=stats,
    )