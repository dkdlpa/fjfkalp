"""Phone OSINT на базе библиотеки phonenumbers.

Извлекает: валидность, международный (E.164) и национальный форматы,
страну/регион, базового оператора и часовой пояс. Дополнительно формирует
ссылки быстрых проверок в мессенджерах и поисковиках.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from urllib.parse import quote_plus

import phonenumbers
from phonenumbers import PhoneNumberType, carrier, geocoder, number_type, timezone

from app.services.profiler import profiled_sync

log = logging.getLogger(__name__)

_DIGITS_RE = re.compile(r"^[+]?[\d\s\-().]{7,20}$")

_DEFAULT_REGION = "RU"

_TYPE_NAMES = {
    PhoneNumberType.MOBILE: "mobile",
    PhoneNumberType.FIXED_LINE: "fixed_line",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_or_mobile",
    PhoneNumberType.TOLL_FREE: "toll_free",
    PhoneNumberType.PREMIUM_RATE: "premium_rate",
    PhoneNumberType.SHARED_COST: "shared_cost",
    PhoneNumberType.VOIP: "voip",
    PhoneNumberType.PERSONAL_NUMBER: "personal",
    PhoneNumberType.PAGER: "pager",
    PhoneNumberType.UAN: "uan",
    PhoneNumberType.VOICEMAIL: "voicemail",
    PhoneNumberType.UNKNOWN: "unknown",
}


def _parse(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    for region in (None, _DEFAULT_REGION):
        try:
            pn = phonenumbers.parse(raw, region)
            return pn
        except phonenumbers.NumberParseException:
            continue
    return None


def _links(e164: str) -> dict:
    digits = e164.lstrip("+")
    return {
        "whatsapp": f"https://wa.me/{digits}",
        "telegram": f"https://t.me/+{digits}",
        "viber": f"viber://chat?number=%2B{digits}",
        "google": f"https://www.google.com/search?q={quote_plus(e164)}",
        "yandex": f"https://yandex.ru/search/?text={quote_plus(e164)}",
    }


def analyze_sync(target: str) -> dict:
    """CPU-bound разбор номера (phonenumbers). Вызывается в отдельном потоке."""
    return _analyze_cached(target)


@profiled_sync("service.phone")
@lru_cache(maxsize=1024)
def _analyze_cached(target: str) -> dict:
    result = _analyze_number(target)
    return result


def _analyze_number(target: str) -> dict:
    result: dict = {
        "target": target,
        "error": None,
        "valid": False,
        "e164": None,
        "international": None,
        "national": None,
        "country_code": None,
        "region": None,
        "carrier": None,
        "timezones": [],
        "number_type": None,
        "links": {},
    }

    raw = (target or "").strip()
    if not _DIGITS_RE.match(raw):
        result["error"] = "invalid_input"
        return result

    pn = _parse(raw)
    if pn is None:
        result["error"] = "invalid_input"
        return result

    result["valid"] = phonenumbers.is_valid_number(pn)
    result["country_code"] = pn.country_code
    result["e164"] = phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.E164)
    result["international"] = phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    result["national"] = phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.NATIONAL)
    result["region"] = phonenumbers.region_code_for_number(pn)
    result["carrier"] = carrier.name_for_number(pn, "en") or carrier.name_for_number(pn, "ru") or None
    result["timezones"] = list(timezone.time_zones_for_number(pn))
    result["number_type"] = _TYPE_NAMES.get(number_type(pn), "unknown")
    result["geo_description"] = geocoder.description_for_number(pn, "en") or geocoder.description_for_number(pn, "ru") or None
    result["links"] = _links(result["e164"])

    if not result["valid"] and not result["e164"]:
        result["error"] = "invalid_input"
    return result

async def analyze(target: str) -> dict:
    """Основной блок + независимый дополнительный источник.

    Основная проверка (phonenumbers) выполняется в отдельном потоке.
    Дополнительный блок опрашивается параллельно и не может её сломать:
    любая его ошибка превращается в статус "error" и не влияет на отчёт.
    """
    import asyncio

    from app.services import phone_extra

    primary, extra = await asyncio.gather(
        asyncio.to_thread(analyze_sync, target),
        phone_extra.analyze(target),
        return_exceptions=True,
    )
    if isinstance(primary, Exception):
        return {"target": target, "error": "unavailable"}
    if not isinstance(primary, dict):
        primary = {"target": target, "error": "unavailable"}

    if isinstance(extra, Exception):
        extra = {"status": "error", "source": "", "matches": {}, "values_count": 0}
    primary = dict(primary)
    primary["extra"] = extra
    return primary
