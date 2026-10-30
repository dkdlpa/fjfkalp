"""HTML-отчёты see.tg: Telegram-профиль, подарки и TON-кошелёк.

Структура findings (см. app/services/telegram.py):
    {
      "profile": {...},          # карточка владельца
      "gifts": {"items": [...], "next": "..."},
      "history": {"items": [...]},
      "gift": {...},             # если искали конкретный подарок
      "gift_history": {"items": [...]},
      "ton_addresses": [...],
      "hidden": bool,
      "notes": [...],
      "error": None,
    }

Все числа и адреса выводятся только если API их вернул. Рядом с данными
обязательна подпись «Данные: see.tg» (требование API).
"""
from __future__ import annotations

from app.reports.base import render_report
from app.services.see_tg import ATTRIBUTION, fmt_ton, gift_title


def _gift_rows(items: list[dict]) -> list[tuple]:
    rows = []
    for gift in items:
        title = gift_title(gift)
        meta = []
        if gift.get("model"):
            meta.append(str(gift["model"]))
        if gift.get("backdrop"):
            meta.append(str(gift["backdrop"]))
        if gift.get("pattern"):
            meta.append(str(gift["pattern"]))
        if gift.get("collection"):
            meta.append(str(gift["collection"]))
        value = f"{title} ({', '.join(meta)})" if meta else title
        price = gift.get("price") or gift.get("sale_price")
        if isinstance(price, dict):
            ton = fmt_ton(price.get("amount")) if price.get("currency") in {"gram", "ton"} else ""
            if ton and ton != "—":
                value += f" · {ton}"
        rows.append(("Подарок", value))
    return rows


def _event_rows(items: list[dict]) -> list[tuple]:
    rows = []
    for event in items:
        when = str(event.get("at") or event.get("date") or "")[:19].replace("T", " ")
        kind = str(event.get("type") or event.get("event") or "")
        parts = []
        if event.get("from") or event.get("from_name"):
            parts.append("← " + str(event.get("from_name") or event.get("from")))
        if event.get("to") or event.get("to_name"):
            parts.append("→ " + str(event.get("to_name") or event.get("to")))
        if event.get("gift") and isinstance(event["gift"], dict):
            parts.append(gift_title(event["gift"]))
        elif event.get("gift_title"):
            parts.append(str(event["gift_title"]))
        sale = event.get("saleAction") or event.get("sale")
        if isinstance(sale, dict):
            amount = fmt_ton(sale.get("amountTon") or sale.get("amount"))
            if amount != "—":
                parts.append(amount)
            if sale.get("market"):
                parts.append(str(sale["market"]))
        price = event.get("price")
        if isinstance(price, dict):
            amount = fmt_ton(price.get("amount"))
            if amount != "—":
                parts.append(amount)
        label = " · ".join(p for p in parts if p) or "—"
        head = f"{when}  {kind}" if when or kind else "—"
        rows.append((head, label))
    return rows


def build_profile(
    findings: dict,
    target: str,
    language: str = "ru",
) -> bytes:
    ru = language == "ru"
    profile = findings.get("profile") or {}
    gifts = findings.get("gifts") or {}
    history = findings.get("history") or {}
    notes = findings.get("notes") or []
    sections: list[dict] = []

    rows: list[tuple] = []
    name = " ".join(
        p for p in (profile.get("first_name") or profile.get("name") or "", profile.get("last_name") or "") if p
    ).strip()
    if name:
        rows.append(("Имя" if ru else "Name", name))
    usernames = profile.get("usernames") or []
    primary_username = profile.get("username") or (usernames[0] if usernames else "")
    if primary_username:
        rows.append(("Username", "@" + str(primary_username).lstrip("@"), True))
    if profile.get("see_id") or profile.get("id"):
        rows.append(("ID", profile.get("see_id") or profile.get("id"), True))
    if profile.get("type"):
        rows.append(("Тип" if ru else "Type", str(profile["type"])))
    if profile.get("ton_address") or profile.get("tonAddress"):
        rows.append(
            ("TON-адрес" if ru else "TON address",
             profile.get("ton_address") or profile.get("tonAddress"), True)
        )
    if profile.get("gifts_count") is not None:
        rows.append(("Подарков" if ru else "Gifts", profile["gifts_count"], True))
    if profile.get("transfers_count") is not None:
        rows.append(("Передач" if ru else "Transfers", profile["transfers_count"], True))
    if profile.get("premium"):
        rows.append(("Premium", ("да" if ru else "yes")))
    if profile.get("views") is not None:
        rows.append(("Просмотров" if ru else "Views", profile["views"], True))
    if profile.get("created_at"):
        rows.append(("Аккаунт создан" if ru else "Account created",
                     str(profile["created_at"])[:10], True))

    if rows:
        sections.append(
            {"id": "profile", "title": "Профиль" if ru else "Profile", "rows": rows}
        )

    if findings.get("hidden") or profile.get("hiddenByOwner"):
        sections.append(
            {
                "id": "hidden",
                "title": "Приватность" if ru else "Privacy",
                "note": (
                    "Владелец скрыл историю профиля. Часть данных недоступна по API."
                    if ru
                    else "The owner hides profile history. Some data is unavailable."
                ),
            }
        )

    gift_items = gifts.get("items") or []
    if gift_items:
        sections.append(
            {
                "id": "gifts",
                "title": ("Подарки" if ru else "Gifts") + f" · {gifts.get('total') or len(gift_items)}",
                "rows": _gift_rows(gift_items),
                "collapsed": True,
                "more_label": "Показать все" if ru else "Show all",
            }
        )
    else:
        sections.append(
            {
                "id": "gifts",
                "title": "Подарки" if ru else "Gifts",
                "note": (
                    "API не вернул список подарков (нет подарков или данные закрыты)."
                    if ru
                    else "The API returned no gifts (no gifts or data is restricted)."
                ),
            }
        )

    history_items = history.get("items") or []
    if history_items:
        sections.append(
            {
                "id": "history",
                "title": ("История профиля" if ru else "Profile history")
                + f" · {len(history_items)}",
                "rows": _event_rows(history_items),
                "collapsed": True,
            }
        )

    for note in notes:
        sections.append({"id": f"note-{abs(hash(note)) % 10000}", "title": "Примечание" if ru else "Note", "note": note})

    stats = []
    if profile.get("gifts_count") is not None:
        stats.append((profile["gifts_count"], "подарков" if ru else "gifts"))
    if profile.get("transfers_count") is not None:
        stats.append((profile["transfers_count"], "передач" if ru else "transfers"))
    if gifts.get("total") is not None:
        stats.append((gifts["total"], "в списке" if ru else "listed"))

    return render_report(
        title="Telegram — see.tg",
        subtitle=f"{target}",
        language=language,
        sections=sections,
        stats=stats,
        tags=["Telegram", "see.tg"],
        source="see.tg API",
        attribution=ATTRIBUTION,
    )


def build_gift(findings: dict, target: str, language: str = "ru") -> bytes:
    ru = language == "ru"
    gift = findings.get("gift") or {}
    history = findings.get("gift_history") or {}
    notes = findings.get("notes") or []
    sections: list[dict] = []

    rows: list[tuple] = []
    title = gift_title(gift)
    if title and title != "—":
        rows.append(("Название" if ru else "Title", title))
    for key, label in (
        ("slug", "Коллекция" if ru else "Collection"),
        ("num", "Номер" if ru else "Number"),
        ("model", "Модель" if ru else "Model"),
        ("backdrop", "Фон" if ru else "Backdrop"),
        ("pattern", "Узор" if ru else "Pattern"),
    ):
        if gift.get(key) not in (None, ""):
            mono = key in {"slug", "num"}
            rows.append((label, gift[key], mono))
    owner = gift.get("owner") if isinstance(gift.get("owner"), dict) else None
    if owner:
        owner_name = owner.get("name") or owner.get("username") or owner.get("see_id")
        if owner_name:
            rows.append(("Владелец" if ru else "Owner", str(owner_name)))
        if owner.get("hidden"):
            rows.append(("Владелец" if ru else "Owner", "скрыт" if ru else "hidden"))
    if gift.get("owner_name"):
        rows.append(("Владелец" if ru else "Owner", str(gift["owner_name"])))
    price = gift.get("price") or gift.get("sale_price")
    if isinstance(price, dict):
        amount = fmt_ton(price.get("amount"))
        if amount != "—":
            rows.append(("Цена" if ru else "Price", f"{amount} · {price.get('currency', '')}", True))
    estimate = gift.get("estimate")
    if isinstance(estimate, dict):
        if estimate.get("ton") is not None:
            rows.append(("Оценка" if ru else "Estimate", fmt_ton(estimate.get("ton")), True))
        elif estimate.get("low") is not None and estimate.get("high") is not None:
            rows.append(
                ("Оценка" if ru else "Estimate",
                 f"{fmt_ton(estimate['low'])} – {fmt_ton(estimate['high'])}", True)
            )
    if gift.get("minted"):
        rows.append(("NFT" if ru else "NFT", ("да" if ru else "yes")))
    if gift.get("url"):
        rows.append(("Ссылка" if ru else "Link", gift["url"]))

    if rows:
        sections.append({"id": "gift", "title": "Подарок" if ru else "Gift", "rows": rows})

    history_items = history.get("items") or []
    if history_items:
        sections.append(
            {
                "id": "history",
                "title": ("История подарка" if ru else "Gift history") + f" · {len(history_items)}",
                "rows": _event_rows(history_items),
                "collapsed": True,
            }
        )
    else:
        sections.append(
            {
                "id": "history",
                "title": "История подарка" if ru else "Gift history",
                "note": ("История не возвращена API." if ru else "No history returned by the API."),
            }
        )

    for note in notes:
        sections.append(
            {"id": f"note-{abs(hash(note)) % 10000}", "title": "Примечание" if ru else "Note", "note": note}
        )

    return render_report(
        title="Подарок — see.tg" if ru else "Gift — see.tg",
        subtitle=target,
        language=language,
        sections=sections,
        tags=["Gift", "see.tg"],
        source="see.tg API",
        attribution=ATTRIBUTION,
    )


def build_ton(findings: dict, target: str, language: str = "ru") -> bytes:
    ru = language == "ru"
    wallet = findings.get("wallet") or {}
    owner = findings.get("profile") or {}
    sections: list[dict] = []

    rows: list[tuple] = [("TON-адрес" if ru else "TON address", target, True)]
    for key, label in (
        ("balance", "Баланс" if ru else "Balance"),
        ("balanceTon", "Баланс (TON)" if ru else "Balance (TON)"),
        ("owner_name", "Владелец" if ru else "Owner"),
        ("owner_username", "Username", ),
        ("see_id", "see_id"),
        ("type", "Тип" if ru else "Type"),
    ):
        value = wallet.get(key)
        if value not in (None, ""):
            if key in {"balance", "balanceTon"} and str(value).replace(".", "").isdigit():
                value = fmt_ton(value) if key == "balanceTon" else value
            mono = key in {"owner_username", "see_id"}
            rows.append((label, value, mono))
    if wallet.get("url"):
        rows.append(("Ссылка" if ru else "Link", wallet["url"]))

    sections.append({"id": "wallet", "title": "TON-кошелёк" if ru else "TON wallet", "rows": rows})

    if owner:
        owner_rows = []
        name = owner.get("name") or owner.get("first_name")
        if name:
            owner_rows.append(("Имя" if ru else "Name", name))
        if owner.get("username"):
            owner_rows.append(("Username", "@" + str(owner["username"]).lstrip("@"), True))
        if owner.get("see_id"):
            owner_rows.append(("see_id", owner["see_id"], True))
        if owner_rows:
            sections.append(
                {
                    "id": "owner",
                    "title": "Владелец" if ru else "Owner",
                    "rows": owner_rows,
                }
            )

    notes = findings.get("notes") or []
    if notes:
        sections.append(
            {
                "id": "notes",
                "title": "Примечания" if ru else "Notes",
                "note": " ".join(str(n) for n in notes),
            }
        )

    return render_report(
        title="TON — see.tg",
        subtitle=target,
        language=language,
        sections=sections,
        tags=["TON", "see.tg"],
        source="see.tg API",
        attribution=ATTRIBUTION,
    )