"""Проверка пользовательского ввода перед запросами к внешним API."""
from __future__ import annotations

import re

_TG_URL_RE = re.compile(r"t\.me/(?:s/)?(@?[A-Za-z0-9_]{3,})", re.I)
_TG_USER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
# служебные пути t.me, которые не являются username
_TG_RESERVED = {"joinchat", "c", "s", "addstickers", "share", "iv", "setlanguage", "proxy"}


def clean(text: str) -> str:
    return (text or "").strip().strip("`\"' ").strip()


def parse_telegram(value: str) -> tuple[str, str] | None:
    """('username'|'id', value) из @name, id или ссылки t.me."""
    value = clean(value)
    if not value:
        return None
    match = _TG_URL_RE.search(value)
    if match:
        candidate = match.group(1).lstrip("@")
        if candidate.lower() in _TG_RESERVED:
            return None
        if candidate.isdigit():
            return ("id", candidate)
        if _TG_USER_RE.fullmatch(candidate):
            return ("username", candidate)
    stripped = value.lstrip("@").split("?")[0].split("#")[0].strip()
    if re.fullmatch(r"-?\d+", stripped):
        return ("id", stripped)
    stripped = stripped.split("/")[0]
    if _TG_USER_RE.fullmatch(stripped):
        return ("username", stripped)
    return None