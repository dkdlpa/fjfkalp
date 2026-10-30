"""Инлайн-клавиатуры INFEX. Строгий текстовый интерфейс без эмодзи.

Навигация: на каждом внутреннем экране ровно ОДНА кнопка "Назад", которая
ведет на предыдущий по иерархии экран. Главное меню - корень иерархии.
"""
from __future__ import annotations

from functools import lru_cache

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import normalize_channel
from app.i18n import t


def _back(builder: InlineKeyboardBuilder, language: str, back_to: str) -> InlineKeyboardBuilder:
    """Единственная навигационная кнопка."""
    builder.button(text=t(language, "btn_back"), callback_data=back_to)
    return builder


@lru_cache(maxsize=8)
def get_main_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_search"), callback_data="m:search")
    kb.button(text=t(language, "btn_buy"), callback_data="buy:menu")
    kb.button(text=t(language, "btn_profile"), callback_data="m:profile")
    kb.button(text=t(language, "btn_ref"), callback_data="m:ref")
    kb.button(text=t(language, "btn_info"), callback_data="m:info")
    kb.button(text=t(language, "btn_photo"), callback_data="ph:start")
    kb.adjust(1, 2, 2, 1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_photo_actions(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_metadata"), callback_data="ph:mode:meta")
    kb.button(text=t(language, "btn_geolocation"), callback_data="ph:mode:geo")
    _back(kb, language, "m:main")
    kb.adjust(2, 1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_photo_prompt_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    _back(kb, language, "ph:start")
    kb.adjust(1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_photo_result_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_photo_again"), callback_data="ph:again")
    _back(kb, language, "ph:start")
    kb.adjust(2)
    return kb.as_markup()


def get_channels_menu(
    language: str, channels: list[str], links: dict[str, str] | None = None
) -> InlineKeyboardMarkup:
    links = links or {}
    kb = InlineKeyboardBuilder()
    index = 0
    for raw in channels:
        ch = normalize_channel(raw)
        if not ch:
            continue
        index += 1
        url = links.get(ch) or (f"https://t.me/{ch[1:]}" if ch.startswith("@") else None)
        # Понятное название кнопки: для публичных — ник, для приватных — «Канал N».
        label = ch.lstrip("@") if ch.startswith("@") else t(language, "channel_button", n=index)
        if url:
            kb.button(text=label, url=url)
        else:
            kb.button(text=label, callback_data="sub:no_link")
    kb.button(text=t(language, "btn_check_sub"), callback_data="sub:check")
    kb.adjust(1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_ref_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    _back(kb, language, "m:main")
    kb.adjust(1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_profile_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_language"), callback_data="m:language")
    _back(kb, language, "m:main")
    kb.adjust(1, 1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_language_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "lang_ru_name"), callback_data="lang:ru")
    kb.button(text=t(language, "lang_en_name"), callback_data="lang:en")
    _back(kb, language, "m:profile")
    kb.adjust(2, 1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_info_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_what"), callback_data="m:help")
    kb.button(text=t(language, "btn_dev"), callback_data="m:dev")
    _back(kb, language, "m:main")
    kb.adjust(1, 1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_simple_back(language: str, back_to: str = "m:main") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    _back(kb, language, back_to)
    kb.adjust(1)
    return kb.as_markup()


def get_back_menu(language: str, back_to: str = "m:main") -> InlineKeyboardMarkup:
    return get_simple_back(language, back_to)


def get_packages_menu(
    language: str, packages: list[tuple[str, int, str]]
) -> InlineKeyboardMarkup:
    """packages = [(package_id, credits, price_str), ...]."""
    kb = InlineKeyboardBuilder()
    for package_id, credits, price in packages:
        kb.button(
            text=t(language, "buy_line", credits=credits, price=price),
            callback_data=f"pay:{package_id}",
        )
    _back(kb, language, "m:main")
    kb.adjust(1)
    return kb.as_markup()


def get_invoice_menu(language: str, url: str, invoice_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_pay_open"), url=url)
    kb.button(text=t(language, "btn_pay_check"), callback_data=f"pay:check:{invoice_id}")
    _back(kb, language, "buy:menu")
    kb.adjust(1, 1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_no_balance_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_buy"), callback_data="buy:menu")
    _back(kb, language, "m:main")
    kb.adjust(1, 1)
    return kb.as_markup()


# ---- Раздел ПОИСК ----
@lru_cache(maxsize=8)
def get_search_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_telegram"), callback_data="m:telegram")
    kb.button(text=t(language, "btn_username"), callback_data="m:username")
    kb.button(text=t(language, "btn_email"), callback_data="m:email")
    kb.button(text=t(language, "btn_phone"), callback_data="m:phone")
    kb.button(text=t(language, "btn_tiktok"), callback_data="m:tiktok")
    kb.button(text=t(language, "btn_ip_domain"), callback_data="m:ip")
    kb.button(text=t(language, "btn_github"), callback_data="m:github")
    kb.button(text=t(language, "btn_vk"), callback_data="m:vk")
    kb.button(text=t(language, "btn_ton"), callback_data="m:ton")
    kb.button(text=t(language, "btn_dork"), callback_data="dork:start")
    kb.button(text=t(language, "btn_photo"), callback_data="ph:start")
    kb.button(text=t(language, "btn_faces"), callback_data="m:faces")
    _back(kb, language, "m:main")
    kb.adjust(2, 2, 2, 2, 2, 1, 1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_consent_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_terms_accept"), callback_data="terms:accept")
    kb.adjust(1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_cancel_menu(language: str, back_to: str = "m:search") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_cancel"), callback_data="m:cancel")
    _back(kb, language, back_to)
    kb.adjust(1, 1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_report_menu(language: str) -> InlineKeyboardMarkup:
    """Клавиатура после отчёта (E4)."""
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_repeat"), callback_data="r:repeat")
    kb.button(text=t(language, "btn_other"), callback_data="m:search")
    kb.button(text=t(language, "btn_buy_short"), callback_data="buy:menu")
    kb.button(text=t(language, "btn_home"), callback_data="m:main")
    kb.adjust(2, 2)
    return kb.as_markup()


def get_report_link_menu(language: str, url: str, token: str) -> InlineKeyboardMarkup:
    """Клавиатура отчёта со временной ссылкой."""
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_open_report"), url=url)
    kb.button(text=t(language, "btn_delete_report"), callback_data=f"rp:del:{token}")
    kb.button(text=t(language, "btn_repeat"), callback_data="r:repeat")
    kb.button(text=t(language, "btn_other"), callback_data="m:search")
    kb.button(text=t(language, "btn_home"), callback_data="m:main")
    kb.adjust(1, 1, 2, 1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_username_menu(language: str) -> InlineKeyboardMarkup:
    return get_simple_back(language, "m:search")


@lru_cache(maxsize=8)
def get_ip_menu(language: str) -> InlineKeyboardMarkup:
    return get_simple_back(language, "m:search")


@lru_cache(maxsize=8)
def get_phone_menu(language: str) -> InlineKeyboardMarkup:
    return get_simple_back(language, "m:search")


@lru_cache(maxsize=8)
def get_telegram_menu(language: str) -> InlineKeyboardMarkup:
    return get_simple_back(language, "m:search")


@lru_cache(maxsize=8)
def get_email_menu(language: str) -> InlineKeyboardMarkup:
    return get_simple_back(language, "m:search")


@lru_cache(maxsize=8)
def get_tiktok_menu(language: str) -> InlineKeyboardMarkup:
    return get_simple_back(language, "m:search")


@lru_cache(maxsize=8)
def get_dork_engine_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Google", callback_data="dork:engine:google")
    kb.button(text="Yandex", callback_data="dork:engine:yandex")
    kb.button(text="Bing", callback_data="dork:engine:bing")
    kb.button(text="DuckDuckGo", callback_data="dork:engine:duckduckgo")
    _back(kb, language, "m:search")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_dork_result_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_dork_export"), callback_data="dork:export")
    kb.button(text=t(language, "btn_dork_again"), callback_data="dork:start")
    _back(kb, language, "m:search")
    kb.adjust(1, 1, 1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_admin_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Статистика", callback_data="a:stats")
    kb.button(text="Рассылка", callback_data="a:broadcast")
    kb.button(text="Начислить запросы", callback_data="a:grant")
    kb.button(text="Обслуживание", callback_data="a:maintenance")
    kb.button(text="Пользователи", callback_data="a:users")
    kb.button(text="Каналы", callback_data="a:channels")
    kb.button(text="Ошибки", callback_data="a:errors")
    kb.button(text="Очистка кэша и БД", callback_data="a:cleanup")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_admin_back_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад", callback_data="a:panel")
    kb.adjust(1)
    return kb.as_markup()

# ---- GitHub / VK / TON / Лица ----
@lru_cache(maxsize=8)
def get_github_menu(language: str) -> InlineKeyboardMarkup:
    return get_simple_back(language, "m:search")


@lru_cache(maxsize=8)
def get_vk_menu(language: str) -> InlineKeyboardMarkup:
    return get_simple_back(language, "m:search")


@lru_cache(maxsize=8)
def get_ton_menu(language: str) -> InlineKeyboardMarkup:
    return get_simple_back(language, "m:search")


@lru_cache(maxsize=8)
def get_faces_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_faces_detect"), callback_data="fc:detect")
    kb.button(text=t(language, "btn_faces_compare"), callback_data="fc:compare")
    _back(kb, language, "m:search")
    kb.adjust(2, 1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_faces_prompt_menu(language: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_cancel"), callback_data="m:cancel")
    _back(kb, language, "m:faces")
    kb.adjust(1, 1)
    return kb.as_markup()


@lru_cache(maxsize=8)
def get_support_menu(language: str, support: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t(language, "btn_support_open"), url=f"https://t.me/{support.lstrip('@')}")
    _back(kb, language, "m:main")
    kb.adjust(1, 1)
    return kb.as_markup()
