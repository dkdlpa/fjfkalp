"""Отчёт по Telegram-профилю (данные see.tg).

Тонкая обёртка: выбирает нужный рендер по типу найденного объекта —
владелец, подарок или TON-кошелёк. Данные приходят только из API и не
додумываются.
"""
from __future__ import annotations

from app.reports import see_tg_report


def build(findings: dict, target: str, language: str = "ru") -> bytes:
    kind = findings.get("kind")
    if kind == "gift" or findings.get("gift"):
        return see_tg_report.build_gift(findings, target, language)
    return see_tg_report.build_profile(findings, target, language)