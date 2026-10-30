"""HTML-отчёт по Email OSINT (чистый стиль, без эмодзи и источников)."""
from __future__ import annotations

from app.reports.base import render_report


def build(findings: dict, target: str, language: str = "ru") -> bytes:
    ru = language == "ru"
    validation = findings.get("validation") or {}
    services = findings.get("services") or []
    services_detail = findings.get("services_detail") or []
    breaches = findings.get("breaches") or []
    breaches_detail = findings.get("breaches_detail") or []
    infostealers = findings.get("infostealers") or []
    gravatar = findings.get("gravatar") or {}

    def yn(v):
        return ("да" if v else "нет") if ru else ("yes" if v else "no")

    sections: list[dict] = []

    # Валидация адреса
    rows = [("Email", target, True)]
    if validation.get("domain"):
        rows.append(("Домен" if ru else "Domain", validation["domain"]))
    if validation.get("mx") is not None:
        rows.append(("Принимает почту" if ru else "Accepts mail", yn(validation["mx"])))
    if validation.get("disposable") is not None:
        rows.append(("Одноразовый" if ru else "Disposable", yn(validation["disposable"])))
    if validation.get("domain_age_days"):
        yrs = round(validation["domain_age_days"] / 365, 1)
        rows.append(("Возраст домена" if ru else "Domain age", f"{validation['domain_age_days']} " + ("дн." if ru else "d") + f" (~{yrs} " + ("г." if ru else "y") + ")"))
    if validation.get("whitelist") is not None:
        rows.append(("Whitelist", yn(validation["whitelist"])))
    if validation.get("dns") is not None:
        rows.append(("DNS", ("есть" if ru else "present") if validation["dns"] else ("нет" if ru else "absent")))
    sections.append({"id": "validation", "title": "Валидация адреса" if ru else "Address validation", "rows": rows})

    # Gravatar
    if gravatar:
        g = []
        if gravatar.get("name"):
            g.append(("Имя" if ru else "Name", gravatar["name"]))
        if gravatar.get("username"):
            g.append(("Username", gravatar["username"], True))
        if gravatar.get("about"):
            g.append(("О себе" if ru else "About", gravatar["about"]))
        if gravatar.get("accounts"):
            g.append(("Аккаунты" if ru else "Accounts", ", ".join(gravatar["accounts"])))
        for lnk in (gravatar.get("links") or [])[:5]:
            g.append(("Ссылка" if ru else "Link", lnk))
        sections.append({"id": "gravatar", "title": "Gravatar", "rows": g or [("Gravatar", "—")]})
    else:
        sections.append({"id": "gravatar", "title": "Gravatar", "note": "Профиль Gravatar не найден" if ru else "Gravatar profile not found"})

    # Сервисы регистрации
    if services_detail or services:
        rows_s = []
        if services_detail:
            for s in services_detail:
                extra = ""
                if s.get("username"):
                    extra = "@" + str(s["username"])
                elif s.get("name"):
                    extra = str(s["name"])
                rows_s.append((s.get("service", ""), extra, bool(s.get("username"))))
        else:
            for s in services:
                rows_s.append((s, ""))
        count = len(services_detail) or len(services)
        sections.append(
            {"id": "services", "title": ("Зарегистрирован на сервисах" if ru else "Registered on services") + f" · {count}", "rows": rows_s}
        )
    else:
        sections.append({"id": "services", "title": "Зарегистрирован на сервисах" if ru else "Registered on services", "note": "Аккаунтов не обнаружено" if ru else "No accounts found"})

    # Утечки
    names = {d.get("name") for d in breaches_detail if isinstance(d, dict) and d.get("name")}
    count = len(breaches_detail) + len([b for b in breaches if b not in names])
    if count:
        rows_b = []
        for b in breaches_detail[:30]:
            meta = []
            if b.get("date"):
                meta.append(str(b["date"]))
            data = b.get("data") or []
            risk = b.get("password_risk")
            if data:
                meta.append(", ".join(str(x) for x in data))
            if b.get("records") is not None:
                meta.append(("записей: " if ru else "records: ") + str(b["records"]))
            cls = "value danger" if (risk and str(risk).lower() not in {"safe", "low"}) or data else "value"
            rows_b.append((b.get("name", "Unknown"), " · ".join(meta) if meta else "", False, cls))
        for name in breaches[:30]:
            if name not in names:
                rows_b.append((name, "", False, "value danger"))
        sections.append({"id": "breaches", "title": ("Обнаруженные утечки" if ru else "Detected breaches") + f" · {count}", "rows": rows_b})
    else:
        sections.append({"id": "breaches", "title": "Обнаруженные утечки" if ru else "Detected breaches", "rows": [("—", "В известных утечках не найден" if ru else "Not found in known breaches", False, "value ok")]})

    # Инфостилеры
    if infostealers:
        rows_i = []
        for st in infostealers[:20]:
            parts = []
            for k in ("date", "stealer", "os"):
                if st.get(k):
                    parts.append(str(st[k]))
            if st.get("ip"):
                parts.append("IP: " + str(st["ip"]))
            rows_i.append((st.get("computer") or ("Устройство" if ru else "Device"), " · ".join(parts), False, "value danger"))
        sections.append(
            {
                "id": "infostealers",
                "title": ("Инфостилеры" if ru else "Infostealers") + f" · {len(infostealers)}",
                "note": ("Обнаружено заражение инфостилером: учётные данные могли быть похищены." if ru else "Infostealer infection detected: credentials may have been stolen."),
                "rows": rows_i,
            }
        )

    stats = [
        (len(services), "сервисов" if ru else "services"),
        (len(breaches), "утечек" if ru else "breaches"),
        (len(infostealers), "инфостилеров" if ru else "infostealers"),
    ]

    return render_report(
        title="Email",
        kind="Email",
        subtitle=f"OSINT · {target}",
        language=language,
        sections=sections,
        stats=stats,
    )