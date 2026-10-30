"""Отчёт по номеру телефона: два независимых блока в одном документе.

Блок 1 — основная проверка (phonenumbers): валидность, страна, регион,
оператор, тип, часовые пояса, ссылки быстрых проверок.
Блок 2 — дополнительные результаты из второго источника (см. phone_extra).

Ошибка/пустота одного блока не отменяет второй: отчёт формируется всегда.
"""
from __future__ import annotations

from app.reports.base import esc, render_report
from app.reports.locales import rl

_TYPE_LABELS = {
    "mobile": ("мобильный", "mobile"),
    "fixed_line": ("стационарный", "fixed line"),
    "fixed_or_mobile": ("мобильный/стационарный", "fixed or mobile"),
    "toll_free": ("бесплатный", "toll-free"),
    "premium_rate": ("премиум", "premium rate"),
    "shared_cost": ("shared cost", "shared cost"),
    "voip": ("VoIP", "VoIP"),
    "personal": ("персональный", "personal"),
    "pager": ("пейджер", "pager"),
    "uan": ("UAN", "UAN"),
    "voicemail": ("голосовая почта", "voicemail"),
    "unknown": ("неизвестно", "unknown"),
}


def _type_label(code: str | None, language: str) -> str:
    if not code:
        return "—"
    pair = _TYPE_LABELS.get(code, (code, code))
    return pair[0] if language == "ru" else pair[1]


def _main_section(findings: dict, language: str) -> dict:
    yes = rl(language, "yes")
    no = rl(language, "no")
    main_rows = []
    if findings.get("valid") is not None:
        main_rows.append(
            (
                rl(language, "f_valid"),
                yes if findings["valid"] else no,
                False,
                "value ok" if findings["valid"] else "value danger",
            )
        )
    if findings.get("e164"):
        main_rows.append((rl(language, "f_e164"), findings["e164"], True))
    if findings.get("international"):
        main_rows.append((rl(language, "f_intl"), findings["international"]))
    if findings.get("national"):
        main_rows.append((rl(language, "f_national"), findings["national"]))
    if findings.get("country_code") is not None:
        main_rows.append((rl(language, "f_country"), f"+{findings['country_code']}"))
    if findings.get("region"):
        main_rows.append((rl(language, "f_region"), findings["region"], True))
    if findings.get("geo_description"):
        main_rows.append((rl(language, "f_geo"), findings["geo_description"]))
    if findings.get("carrier"):
        main_rows.append((rl(language, "f_carrier"), findings["carrier"]))
    if findings.get("timezones"):
        main_rows.append((rl(language, "f_timezones"), ", ".join(findings["timezones"])))
    if findings.get("number_type"):
        main_rows.append(
            (rl(language, "f_phone_type"), _type_label(findings["number_type"], language))
        )
    return {
        "id": "main",
        "title": rl(language, "sec_phone"),
        "rows": main_rows,
        "source": "phonenumbers (open numbering plan data)",
    }


def _extra_section(findings: dict, language: str) -> dict:
    extra = findings.get("extra") or {}
    status = extra.get("status") or "disabled"
    ru = language == "ru"
    title = rl(language, "sec_phone_extra")
    source_label = extra.get("source") or ("дополнительный источник" if ru else "extra source")

    if status == "disabled":
        return {
            "id": "extra",
            "title": title,
            "note": rl(language, "phone_extra_disabled"),
        }
    if status == "error":
        return {
            "id": "extra",
            "title": title,
            "note": rl(language, "source_maintenance"),
        }
    if status == "empty":
        return {
            "id": "extra",
            "title": title,
            "note": rl(language, "phone_extra_empty"),
            "source": f"{rl(language, 'f_source')}: {esc(source_label)}",
        }

    matches = extra.get("matches") or {}
    rows = []
    for header, values in matches.items():
        for index, value in enumerate(values):
            label = header if index == 0 else str(header) + f" [{index + 1}]"
            rows.append((label, value, True))
    host = extra.get("url_host")
    src = rl(language, "f_source") + f": {source_label}"
    if host:
        src += f" · {host}"
    return {
        "id": "extra",
        "title": title,
        "count": extra.get("values_count"),
        "rows": rows,
        "note": rl(language, "phone_extra_note"),
        "source": src,
        "collapsed": True,
        "more_label": "Показать все" if ru else "Show all",
    }


def build(findings: dict, target: str, language: str = "ru") -> bytes:
    sections: list[dict] = [_main_section(findings, language)]

    links = findings.get("links") or {}
    if links:
        items = [
            ("WhatsApp", links.get("whatsapp")),
            ("Telegram", links.get("telegram")),
            ("Viber", links.get("viber")),
            ("Google", links.get("google")),
            ("Yandex", links.get("yandex")),
        ]
        links = "".join(
            f'<a href="{esc(url)}" target="_blank">{esc(name)}'
            f'<span class="domain">{esc(url)}</span></a>'
            for name, url in items
            if url
        )
        sections.append(
            {
                "id": "links",
                "title": rl(language, "sec_links"),
                "html": f'<div class="list">{links}</div>',
            }
        )

    sections.append(_extra_section(findings, language))

    extra = findings.get("extra") or {}
    stats = []
    if findings.get("valid") is not None:
        stats.append(
            ("yes" if findings["valid"] else "no", rl(language, "f_valid"))
        )
    if extra.get("values_count"):
        stats.append((extra["values_count"], rl(language, "stat_matches")))

    return render_report(
        title=rl(language, "phone_title"),
        subtitle=rl(language, "phone_subtitle") + f" · {target}",
        language=language,
        sections=sections,
        stats=stats,
        tags=["Phone"],
        source="open numbering plan data",
    )