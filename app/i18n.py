"""Локализация: доступ к текстам по ключу с безопасной подстановкой."""
from __future__ import annotations

import logging
from functools import lru_cache

from app.config import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from app.texts import BY_LANG

log = logging.getLogger(__name__)


def normalize_language(language: str | None) -> str:
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


@lru_cache(maxsize=256)
def _plain(lang: str, key: str) -> str:
    return BY_LANG[lang].get(key) or BY_LANG[DEFAULT_LANGUAGE].get(key) or key


def t(language: str | None, key: str, **kwargs) -> str:
    lang = normalize_language(language)
    # Абсолютное большинство вызовов — без подстановок: кэшируем их,
    # чтобы не пересобирать dict.get-цепочку на каждый апдейт.
    if not kwargs:
        return _plain(lang, key)
    text = _plain(lang, key)
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        log.warning("i18n: не удалось отформатировать ключ %r", key)
        return text