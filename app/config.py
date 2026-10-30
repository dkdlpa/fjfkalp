"""Конфигурация приложения.

Секреты и переключатели читаются только из .env.
Ничего не хардкодится в коде. Ключи никогда не пишутся в логи.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Final

from dotenv import load_dotenv

load_dotenv()

SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset({"ru", "en"})
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "ru").strip().lower()
if DEFAULT_LANGUAGE not in SUPPORTED_LANGUAGES:
    DEFAULT_LANGUAGE = "ru"


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


def _parse_int_list(raw: str) -> frozenset[int]:
    result: set[int] = set()
    for chunk in re.split(r"[;,\s]+", raw or ""):
        value = chunk.strip().lstrip("@")
        if value.isdigit():
            result.add(int(value))
    return frozenset(result)


def _env_secret(name: str) -> str:
    """Читает секрет и не считает заглушки из .env.example рабочими ключами."""
    value = os.getenv(name, "").strip()
    low = value.lower()
    if not value or low in {
        "your_token_here",
        "changeme",
        "change_me",
        "none",
        "null",
    } or low.startswith("your_"):
        return ""
    return value


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


_LOCALE_RE = re.compile(r"^[a-z]{2}-[a-z]{2,3}$")


def _searchapi_locale() -> str:
    """Валидная локаль SearchApi (xx-yy). Некорректная -> wt-wt с предупреждением."""
    raw = os.getenv("SEARCHAPI_LOCALE", "ru-ru").strip().lower()
    if _LOCALE_RE.match(raw):
        return raw
    logging.getLogger(__name__).warning(
        "SEARCHAPI_LOCALE=%r некорректен (нужно xx-yy), используется wt-wt", raw
    )
    return "wt-wt"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=256)
def normalize_channel(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if re.fullmatch(r"-?\d+", value):
        return value
    direct = re.fullmatch(r"@?([A-Za-z][A-Za-z0-9_]{4,})", value)
    if direct:
        return "@" + direct.group(1)
    link = re.search(
        r"(?:https?://)?(?:www\.)?t\.me/(?:s/)?(@?[A-Za-z][A-Za-z0-9_]{4,})/?$",
        value,
        flags=re.IGNORECASE,
    )
    if link:
        return "@" + link.group(1).lstrip("@")
    return None


def _normalize_link(value: str) -> str:
    """Ссылка на канал: принимаем https://t.me/... или t.me/... ."""
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("t.me/") or value.startswith("telegram.me/"):
        return "https://" + value
    if value.startswith(("http://", "https://")):
        return value
    return ""


def _get_channels() -> tuple[str, ...]:
    raw = os.getenv("CHANNELS", "")
    return tuple(dict.fromkeys(ch for item in raw.split(",") if (ch := normalize_channel(item.strip()))))


@dataclass(frozen=True)
class Channel:
    """Канал-спонсор: chat_id для get_chat_member, ссылка для кнопки, название."""

    chat_id: str
    link: str
    title: str


def _channel_title(chat_id: str, link: str, index: int) -> str:
    if chat_id.startswith("@"):
        return chat_id.lstrip("@")
    if link and "t.me/" in link:
        tail = link.rstrip("/").rsplit("/", 1)[-1].lstrip("+@")
        if tail and not tail.startswith("joinchat"):
            return tail
    return f"Канал {index}"


def _read_private_channels() -> list[tuple[str, str]]:
    """Пары (chat_id, link) для приватных каналов.

    Поддерживает как PRIVATE_CHANNEL_ID / PRIVATE_CHANNEL_LINK (без номера),
    так и нумерацию PRIVATE_CHANNEL_ID_1 .. _20. Пропуски не обрывают чтение.
    """
    result: list[tuple[str, str]] = []
    logger = logging.getLogger(__name__)
    for index in range(0, 21):
        suffix = "" if index == 0 else f"_{index}"
        raw_id = os.getenv(f"PRIVATE_CHANNEL_ID{suffix}", "").strip()
        raw_link = os.getenv(f"PRIVATE_CHANNEL_LINK{suffix}", "").strip()
        if not raw_id and not raw_link:
            continue
        if not re.fullmatch(r"-100\d+", raw_id):
            logger.warning(
                "PRIVATE_CHANNEL_ID%s=%r некорректен: ожидается -100..., пропущен",
                suffix,
                raw_id,
            )
            continue
        result.append((raw_id, _normalize_link(raw_link)))
    # также учитываем пары без суффикса, заданные только ссылкой
    return result


def _sponsor_channels() -> tuple[Channel, ...]:
    """Единый список каналов-спонсоров: публичные + приватные."""
    channels: list[Channel] = []
    seen: set[str] = set()
    for raw in _get_channels():
        norm = normalize_channel(raw)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        link = _get_channel_links().get(norm, "")
        if not link and norm.startswith("@"):
            link = f"https://t.me/{norm[1:]}"
        channels.append(Channel(chat_id=norm, link=link, title=_channel_title(norm, link, len(channels) + 1)))
    for chat_id, link in _read_private_channels():
        if chat_id in seen:
            continue
        seen.add(chat_id)
        channels.append(
            Channel(chat_id=chat_id, link=link, title=_channel_title(chat_id, link, len(channels) + 1))
        )
    return tuple(channels)


def _private_channels() -> tuple[tuple[str, str], ...]:
    """Совместимость: пары (chat_id, link) приватных каналов."""
    return tuple((c.chat_id, c.link) for c in _sponsor_channels() if c.chat_id.startswith("-"))


def _get_channel_links() -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in os.getenv("CHANNEL_LINKS", "").split(";"):
        if "=" not in pair:
            continue
        key, url = pair.split("=", 1)
        ch = normalize_channel(key)
        url = url.strip()
        norm = _normalize_link(url)
        if ch and norm:
            result[ch] = norm
    return result


def _on_error_mode() -> str:
    """SUBSCRIPTION_ON_ERROR=closed|grace; алиас SUBSCRIPTION_FAIL_OPEN=1/0."""
    raw = os.getenv("SUBSCRIPTION_ON_ERROR", "").strip().lower()
    if raw in {"closed", "grace"}:
        return raw
    if raw:
        logging.getLogger(__name__).warning(
            "SUBSCRIPTION_ON_ERROR=%r некорректен, используется grace", raw
        )
    alias = os.getenv("SUBSCRIPTION_FAIL_OPEN", "").strip().lower()
    if alias in {"0", "false", "no"}:
        return "closed"
    if alias in {"1", "true", "yes"}:
        return "grace"
    return "grace"


def _get_admins() -> frozenset[int]:
    return _parse_int_list(os.getenv("ADMIN_IDS") or os.getenv("ADMIN_ID") or "")


@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "").strip()
    admin_ids: frozenset[int] = field(default_factory=_get_admins)
    admin_command: str = os.getenv("ADMIN_COMMAND", "/en69").strip()
    default_language: str = DEFAULT_LANGUAGE

    channels: tuple[str, ...] = field(default_factory=_get_channels)
    channel_links: dict[str, str] = field(default_factory=_get_channel_links)
    private_channels: tuple[tuple[str, str], ...] = field(default_factory=_private_channels)
    sponsor_channels: tuple[Channel, ...] = field(default_factory=_sponsor_channels)
    subscription_cache_ttl: float = _env_float("SUBSCRIPTION_CACHE_TTL", 60.0)
    subscription_check_timeout: float = _env_float("SUBSCRIPTION_CHECK_TIMEOUT", 5.0)
    subscription_on_error: str = field(default_factory=_on_error_mode)
    subscription_grace_seconds: float = _env_float("SUBSCRIPTION_GRACE_SECONDS", 1800.0)

    @property
    def subscription_fail_open(self) -> bool:
        """Алиас: grace -> True, closed -> False."""
        return self.subscription_on_error == "grace"

    db_path: str = os.getenv("DB_PATH", "data/bot.db")
    log_file: str = os.getenv("LOG_FILE", "data/bot.log")
    rate_limit_seconds: float = _env_float("RATE_LIMIT_SECONDS", 2.0)
    maintenance_mode: bool = _env_bool("MAINTENANCE_MODE", False)
    retention_days: int = _env_int("RETENTION_DAYS", 2)

    # --- Профилирование нагрузки ---
    profile_cpu_warn_ms: float = _env_float("PROFILE_CPU_WARN_MS", 1500.0)
    profile_wall_warn_ms: float = _env_float("PROFILE_WALL_WARN_MS", 12000.0)
    profile_memory_warn_kb: int = _env_int("PROFILE_MEMORY_WARN_KB", 4096)
    profile_tracemalloc: bool = _env_bool("PROFILE_TRACEMALLOC", False)

    # --- Telegram / подарки OSINT (see.tg Public API, kartoshka.free) ---
    # Токен создаётся в мини-приложении see.tg -> «Полезное» -> «API».
    see_tg_token: str = _env_secret("SEE_TG_TOKEN") or _env_secret("KARTOSHKA_TOKEN")
    see_tg_base_url: str = os.getenv("SEE_TG_BASE_URL", "https://kartoshka.free/v1").rstrip("/")
    see_tg_timeout: float = _env_float("SEE_TG_TIMEOUT", 8.0)
    see_tg_page_size: int = min(100, max(1, _env_int("SEE_TG_PAGE_SIZE", 25)))
    # Дорогие методы (/owners/search, /who, /gift/{ref}/who) стоят 0.01 TON
    # за запрос и не входят в бесплатную квоту. По умолчанию выключены.
    see_tg_expensive: bool = _env_bool("SEE_TG_ALLOW_EXPENSIVE", False)

    # --- GitHub OSINT ---
    # Токен необязателен: без него лимит 60 запросов в час.
    github_token: str = _env_secret("GITHUB_TOKEN")
    github_timeout: float = _env_float("GITHUB_TIMEOUT", 6.0)
    github_max_repos: int = _env_int("GITHUB_MAX_REPOS", 300)

    # --- VK OSINT ---
    # Токен пользователя/сервиса VK API; без него поиск недоступен.
    vk_token: str = _env_secret("VK_TOKEN")
    vk_api_version: str = os.getenv("VK_API_VERSION", "5.199").strip() or "5.199"
    vk_timeout: float = _env_float("VK_TIMEOUT", 6.0)

    # --- Дополнительный источник по номеру (внутри сценария «Телефон») ---
    # Выключен по умолчанию. Включать только при наличии законного основания
    # и разрешённого доступа к источнику.
    phone_extra_enabled: bool = _env_bool("PHONE_EXTRA_ENABLED", False)
    phone_extra_url_template: str = os.getenv("PHONE_EXTRA_URL_TEMPLATE", "").strip()
    phone_extra_source_label: str = (
        os.getenv("PHONE_EXTRA_SOURCE_LABEL", "").strip() or "дополнительный источник"
    )
    phone_extra_timeout: float = _env_float("PHONE_EXTRA_TIMEOUT", 8.0)

    # --- Временные ссылки на HTML-отчёты ---
    # Файлы живут REPORT_TTL_MINUTES и удаляются автоматически.
    # Публичный адрес нужен только для ссылки в Telegram (HTTPS/Nginx).
    reports_web_enabled: bool = _env_bool("REPORTS_WEB_ENABLED", False)
    reports_public_base: str = os.getenv("REPORTS_PUBLIC_BASE", "").strip().rstrip("/")
    reports_bind_host: str = os.getenv("REPORTS_BIND_HOST", "127.0.0.1").strip()
    reports_bind_port: int = _env_int("REPORTS_BIND_PORT", 8089)
    reports_dir: str = os.getenv("REPORTS_DIR", "data/reports").strip()
    reports_ttl_minutes: int = max(1, _env_int("REPORTS_TTL_MINUTES", 20))
    reports_max_files: int = max(10, _env_int("REPORTS_MAX_FILES", 500))

    # --- Общий HTTP-клиент ---
    http_timeout: float = _env_float("HTTP_TIMEOUT", 4.0)
    http_connect_timeout: float = _env_float("HTTP_CONNECT_TIMEOUT", 2.0)
    http_read_timeout: float = _env_float("HTTP_READ_TIMEOUT", 4.0)
    # Дедлайн на весь поиск и таймаут одного внешнего запроса (сек).
    search_deadline: float = _env_float("SEARCH_DEADLINE", 5.0)
    search_request_timeout: float = _env_float("SEARCH_REQUEST_TIMEOUT", 4.0)
    # TTL кэша готовых результатов поиска (сек): повторные запросы мгновенные.
    search_cache_ttl: int = _env_int("SEARCH_CACHE_TTL", 600)
    http_cache_ttl: int = _env_int("HTTP_CACHE_TTL", 600)
    max_outbound_concurrency: int = _env_int("MAX_OUTBOUND_CONCURRENCY", 30)
    max_heavy_searches: int = _env_int("MAX_HEAVY_SEARCHES", 8)
    http_max_connections: int = _env_int("HTTP_MAX_CONNECTIONS", 100)
    http_keepalive_connections: int = _env_int("HTTP_KEEPALIVE_CONNECTIONS", 50)

    # --- Веб-поиск (SearchApi, engine=duckduckgo) ---
    searchapi_key: str = os.getenv("SEARCHAPI_KEY", "").strip()
    searchapi_locale: str = field(default_factory=_searchapi_locale)

    # --- Монетизация: CryptoBot (CryptoPay API) ---
    # Токен создаётся в @CryptoBot -> Crypto Pay -> Create App.
    cryptopay_token: str = _env_secret("CRYPTOPAY_TOKEN")
    cryptopay_base_url: str = os.getenv("CRYPTOPAY_BASE_URL", "https://pay.crypt.bot").rstrip("/")
    cryptopay_timeout: float = _env_float("CRYPTOPAY_TIMEOUT", 15.0)
    # Интервал поллинга неоплаченных счетов и время их жизни (CryptoPay: 1–7 дней).
    payment_poll_interval: float = _env_float("PAYMENT_POLL_INTERVAL", 10.0)
    invoice_ttl_minutes: int = _env_int("INVOICE_TTL_MINUTES", 60)
    # Валюта, в которой выставляются счета (USDT/BTC/TON/…). Пусто — любая.
    cryptopay_asset: str = os.getenv("CRYPTOPAY_ASSET", "USDT").strip().upper()

    # --- Реферальная программа ---
    # За каждого приглашенного (после проверки подписки) начисляется 3 запроса.
    referral_bonus: int = _env_int("REFERRAL_BONUS", 3)
    referral_daily_limit: int = _env_int("REFERRAL_DAILY_LIMIT", 10)
    admin_grant_limit: int = _env_int("ADMIN_GRANT_LIMIT", 10000)

    # --- Бесплатный пробный баланс ---
    # 10 бесплатных запросов на 24 часа, отсчет с первого списания.
    trial_requests: int = _env_int("TRIAL_REQUESTS", 10)
    trial_hours: int = _env_int("TRIAL_HOURS", 24)

    # --- ИИ для анализа фото ---
    ai_base_url: str = os.getenv("AI_BASE_URL", "https://www.ai-funpay.ru/v1").rstrip("/")
    ai_api_key: str = _env_secret("AI_API_KEY")
    ai_model: str = os.getenv("AI_MODEL", "deepseek-v4-flash").strip()
    ai_fallback_models: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            model.strip()
            for model in os.getenv(
                "AI_MODEL_FALLBACKS", "gpt-5-4,gemini-3.8-flash"
            ).split(",")
            if model.strip()
        )
    )
    ai_timeout: float = _env_float("AI_TIMEOUT", 20.0)

    # --- Интерфейс ---
    banner_path: str = os.getenv("BANNER_PATH", "www.png")
    creator_link: str = os.getenv("CREATOR_LINK", "https://t.me/")
    support_bot: str = os.getenv("SUPPORT_BOT", "").strip()
    author_link: str = os.getenv("AUTHOR_LINK", "https://t.me/")
    news_link: str = os.getenv("NEWS_LINK", "https://t.me/")
    terms_link: str = os.getenv("TERMS_LINK", "").strip()

    @property
    def all_channels(self) -> tuple[str, ...]:
        """Все каналы для проверки подписки (публичные + приватные)."""
        return self.channels + tuple(cid for cid, _ in self.private_channels)

    @property
    def see_tg_ready(self) -> bool:
        return bool(self.see_tg_token)

    @property
    def github_ready(self) -> bool:
        return True

    @property
    def vk_ready(self) -> bool:
        return bool(self.vk_token)

    @property
    def reports_web_ready(self) -> bool:
        return bool(
            self.reports_web_enabled
            and self.reports_public_base.startswith(("http://", "https://"))
        )

    @property
    def phone_extra_ready(self) -> bool:
        return bool(self.phone_extra_enabled and self.phone_extra_url_template)

    @property
    def payments_ready(self) -> bool:
        return bool(self.cryptopay_token)

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids


settings = Settings()

if not settings.bot_token:
    sys.exit("[ERROR] BOT_TOKEN не задан в .env")