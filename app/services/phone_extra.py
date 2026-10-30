"""Дополнительный источник по номеру телефона (второй блок отчёта).

Логика перенесена из приложенного check.py и сохранена:
1. нормализовать номер (только цифры, убрать ведущую 7/8 у 11-значных);
2. сформировать URL по шаблону из окружения;
3. скачать CSV (с ретраями на таймаут/сеть);
4. найти строку с точным совпадением нормализованного номера;
5. извлечь значения по колонкам.

ЧТО ИЗМЕНЕНО И ПОЧЕМУ:
- адрес источника НЕ захардкожен. В TZ прямо запрещено подключать базы
  утечек персональных данных. Поэтому шаблон задаётся администратором через
  PHONE_EXTRA_URL_TEMPLATE и по умолчанию блок выключен
  (PHONE_EXTRA_ENABLED=0). Включать только при наличии законного основания и
  разрешённого доступа;
- блок независим от основного анализа номера: его ошибка/пустой результат не
  ломает основной отчёт;
- источник не выдаёт себя за подтверждение личности: результат подписан как
  «совпадения в источнике», а не «владелец номера».

Статусы:
    disabled  — источник не настроен/выключен;
    ok        — данные получены;
    empty     — источник ответил, совпадений нет;
    error     — сетевая ошибка/таймаут/HTTP-сбой.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from collections import OrderedDict

from app.config import settings
from app.services.http import fetch_text

log = logging.getLogger(__name__)

_DIGITS = re.compile(r"\D+")
_MAX_BYTES = 4 * 1024 * 1024
_MAX_VALUES = 200


def norm_phone(value: str) -> str:
    """Цифры без ведущей 7/8 у 11-значного номера (как в check.py)."""
    digits = _DIGITS.sub("", str(value or ""))
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    return digits


def build_url(template: str, phone_digits: str) -> str:
    """Подставляет номер в шаблон. Поддерживает {phone} и {phone_raw}."""
    url = template.replace("{phone}", phone_digits)
    url = url.replace("{phone_raw}", "+" + phone_digits if phone_digits else "")
    return url


def parse_csv_records(text: str) -> tuple[list[str], list[list[str]]]:
    reader = csv.reader(io.StringIO(text))
    try:
        headers = [h.strip() for h in next(reader)]
    except StopIteration:
        return [], []
    if not headers:
        return [], []
    rows: list[list[str]] = []
    for raw in reader:
        if not raw or not any(x.strip() for x in raw):
            continue
        if len(raw) < len(headers):
            raw = raw + [""] * (len(headers) - len(raw))
        elif len(raw) > len(headers):
            raw = raw[: len(headers)]
        rows.append([v.strip() for v in raw])
    return headers, rows


def search_in_rows(headers: list[str], rows: list[list[str]], query: str) -> OrderedDict:
    """Точное совпадение по нормализованному телефону; уникальные значения по колонкам."""
    q = norm_phone(query)
    if not q:
        return OrderedDict()
    found: "OrderedDict[str, list[str]]" = OrderedDict()
    seen: dict[str, set[str]] = {}

    for values in rows:
        row_has_phone = any(
            len(norm_phone(v)) >= 7 and norm_phone(v) == q for v in values
        )
        if not row_has_phone:
            continue
        for index, value in enumerate(values):
            if not value:
                continue
            header = headers[index] if index < len(headers) else f"col_{index}"
            bucket = seen.setdefault(header, set())
            if value in bucket:
                continue
            if len(found.get(header, [])) >= _MAX_VALUES:
                continue
            bucket.add(value)
            found.setdefault(header, []).append(value)
    return found


async def _fetch(url: str, retries: int = 2) -> tuple[str | None, str | None]:
    """Текст CSV или (None, reason). Ретраи на таймаут/сеть."""
    for attempt in range(retries + 1):
        status, text = await fetch_text(
            url,
            timeout=settings.phone_extra_timeout,
            max_bytes=_MAX_BYTES,
        )
        if status == 404:
            return None, "empty"
        if status == 200:
            return text, None
        if status == 0:
            if attempt < retries:
                await asyncio.sleep(1.5)
                continue
            return None, "network"
        if status in (403, 429, 500, 502, 503, 504):
            if attempt < retries:
                await asyncio.sleep(1.5)
                continue
            return None, f"http_{status}"
        return None, f"http_{status}"
    return None, "network"


async def analyze(phone: str) -> dict:
    """Результат дополнительного блока. Никогда не бросает исключений."""
    result = {
        "status": "disabled",
        "source": settings.phone_extra_source_label,
        "matches": OrderedDict(),
        "values_count": 0,
        "error": None,
        "url_host": "",
    }
    if not settings.phone_extra_ready:
        return result

    digits = norm_phone(phone)
    if len(digits) < 7:
        result.update(status="empty", error="invalid_phone")
        return result

    try:
        url = build_url(settings.phone_extra_url_template, digits)
    except Exception:  # noqa: BLE001
        log.warning("phone_extra: некорректный шаблон URL")
        result.update(status="error", error="template")
        return result

    # Хост в отчёт (без query), чтобы было видно, откуда данные.
    try:
        result["url_host"] = url.split("://", 1)[-1].split("/", 1)[0]
    except Exception:  # noqa: BLE001
        result["url_host"] = ""

    try:
        text, reason = await _fetch(url)
    except Exception as exc:  # noqa: BLE001
        log.warning("phone_extra: %s", type(exc).__name__)
        result.update(status="error", error="network")
        return result

    if text is None:
        if reason == "empty":
            result.update(status="empty")
        else:
            log.warning("phone_extra: сбой источника (%s)", reason)
            result.update(status="error", error=reason)
        return result

    try:
        headers, rows = parse_csv_records(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("phone_extra: CSV не разобран (%s)", type(exc).__name__)
        result.update(status="error", error="parse")
        return result

    if not headers or not rows:
        result.update(status="empty")
        return result

    found = search_in_rows(headers, rows, phone)
    if not found:
        result.update(status="empty")
        return result

    result["matches"] = found
    result["values_count"] = sum(len(v) for v in found.values())
    result["status"] = "ok"
    return result


__all__ = [
    "analyze",
    "build_url",
    "norm_phone",
    "parse_csv_records",
    "search_in_rows",
]