"""Компактный справочник стран: ISO-код -> флаг и русское название.

Без внешних библиотек и сетевых запросов: обычный dict, минимум RAM/CPU.
"""
from __future__ import annotations

COUNTRY_NAMES_RU: dict[str, str] = {
    "AD": "Андорра", "AE": "ОАЭ", "AF": "Афганистан", "AG": "Антигуа и Барбуда",
    "AL": "Албания", "AM": "Армения", "AO": "Ангола", "AR": "Аргентина",
    "AT": "Австрия", "AU": "Австралия", "AZ": "Азербайджан", "BA": "Босния и Герцеговина",
    "BB": "Барбадос", "BD": "Бангладеш", "BE": "Бельгия", "BF": "Буркина-Фасо",
    "BG": "Болгария", "BH": "Бахрейн", "BI": "Бурунди", "BJ": "Бенин",
    "BN": "Бруней", "BO": "Боливия", "BR": "Бразилия", "BS": "Багамы",
    "BT": "Бутан", "BW": "Ботсвана", "BY": "Беларусь", "BZ": "Белиз",
    "CA": "Канада", "CD": "ДР Конго", "CF": "ЦАР", "CG": "Конго",
    "CH": "Швейцария", "CI": "Кот-д’Ивуар", "CL": "Чили", "CM": "Камерун",
    "CN": "Китай", "CO": "Колумбия", "CR": "Коста-Рика", "CU": "Куба",
    "CV": "Кабо-Верде", "CY": "Кипр", "CZ": "Чехия", "DE": "Германия",
    "DJ": "Джибути", "DK": "Дания", "DM": "Доминика", "DO": "Доминикана",
    "DZ": "Алжир", "EC": "Эквадор", "EE": "Эстония", "EG": "Египет",
    "ER": "Эритрея", "ES": "Испания", "ET": "Эфиопия", "FI": "Финляндия",
    "FJ": "Фиджи", "FM": "Микронезия", "FR": "Франция", "GA": "Габон",
    "GB": "Великобритания", "GD": "Гренада", "GE": "Грузия", "GH": "Гана",
    "GM": "Гамбия", "GN": "Гвинея", "GQ": "Экваториальная Гвинея", "GR": "Греция",
    "GT": "Гватемала", "GW": "Гвинея-Бисау", "GY": "Гайана", "HN": "Гондурас",
    "HR": "Хорватия", "HT": "Гаити", "HU": "Венгрия", "ID": "Индонезия",
    "IE": "Ирландия", "IL": "Израиль", "IN": "Индия", "IQ": "Ирак",
    "IR": "Иран", "IS": "Исландия", "IT": "Италия", "JM": "Ямайка",
    "JO": "Иордания", "JP": "Япония", "KE": "Кения", "KG": "Кыргызстан",
    "KH": "Камбоджа", "KI": "Кирибати", "KM": "Коморы", "KN": "Сент-Китс и Невис",
    "KP": "КНДР", "KR": "Южная Корея", "KW": "Кувейт", "KZ": "Казахстан",
    "LA": "Лаос", "LB": "Ливан", "LC": "Сент-Люсия", "LI": "Лихтенштейн",
    "LK": "Шри-Ланка", "LR": "Либерия", "LS": "Лесото", "LT": "Литва",
    "LU": "Люксембург", "LV": "Латвия", "LY": "Ливия", "MA": "Марокко",
    "MC": "Монако", "MD": "Молдова", "ME": "Черногория", "MG": "Мадагаскар",
    "MH": "Маршалловы Острова", "MK": "Северная Македония", "ML": "Мали",
    "MM": "Мьянма", "MN": "Монголия", "MR": "Мавритания", "MT": "Мальта",
    "MU": "Маврикий", "MV": "Мальдивы", "MW": "Малави", "MX": "Мексика",
    "MY": "Малайзия", "MZ": "Мозамбик", "NA": "Намибия", "NE": "Нигер",
    "NG": "Нигерия", "NI": "Никарагуа", "NL": "Нидерланды", "NO": "Норвегия",
    "NP": "Непал", "NR": "Науру", "NZ": "Новая Зеландия", "OM": "Оман",
    "PA": "Панама", "PE": "Перу", "PG": "Папуа — Новая Гвинея", "PH": "Филиппины",
    "PK": "Пакистан", "PL": "Польша", "PT": "Португалия", "PW": "Палау",
    "PY": "Парагвай", "QA": "Катар", "RO": "Румыния", "RS": "Сербия",
    "RU": "Россия", "RW": "Руанда", "SA": "Саудовская Аравия", "SB": "Соломоновы Острова",
    "SC": "Сейшелы", "SD": "Судан", "SE": "Швеция", "SG": "Сингапур",
    "SI": "Словения", "SK": "Словакия", "SL": "Сьерра-Леоне", "SM": "Сан-Марино",
    "SN": "Сенегал", "SO": "Сомали", "SR": "Суринам", "SS": "Южный Судан",
    "ST": "Сан-Томе и Принсипи", "SV": "Сальвадор", "SY": "Сирия", "SZ": "Эсватини",
    "TD": "Чад", "TG": "Того", "TH": "Таиланд", "TJ": "Таджикистан",
    "TL": "Восточный Тимор", "TM": "Туркменистан", "TN": "Тунис", "TO": "Тонга",
    "TR": "Турция", "TT": "Тринидад и Тобаго", "TV": "Тувалу", "TW": "Тайвань",
    "TZ": "Танзания", "UA": "Украина", "UG": "Уганда", "US": "США",
    "UY": "Уругвай", "UZ": "Узбекистан", "VA": "Ватикан", "VC": "Сент-Винсент и Гренадины",
    "VE": "Венесуэла", "VN": "Вьетнам", "VU": "Вануату", "WS": "Самоа",
    "YE": "Йемен", "ZA": "ЮАР", "ZM": "Замбия", "ZW": "Зимбабве",
}

COUNTRY_NAMES_EN: dict[str, str] = {
    "RU": "Russia", "US": "United States", "GB": "United Kingdom", "DE": "Germany",
    "FR": "France", "IT": "Italy", "ES": "Spain", "TR": "Turkey", "UA": "Ukraine",
    "KZ": "Kazakhstan", "BY": "Belarus", "RO": "Romania", "CN": "China",
    "JP": "Japan", "KR": "South Korea", "IN": "India", "BR": "Brazil",
    "CA": "Canada", "AU": "Australia", "NL": "Netherlands", "PL": "Poland",
    "CZ": "Czechia", "SE": "Sweden", "NO": "Norway", "FI": "Finland",
    "CH": "Switzerland", "AT": "Austria", "BE": "Belgium", "PT": "Portugal",
    "GR": "Greece", "IE": "Ireland", "IL": "Israel", "AE": "UAE",
    "SA": "Saudi Arabia", "EG": "Egypt", "ZA": "South Africa", "MX": "Mexico",
    "AR": "Argentina", "CL": "Chile", "CO": "Colombia", "PE": "Peru",
    "ID": "Indonesia", "TH": "Thailand", "VN": "Vietnam", "PH": "Philippines",
    "MY": "Malaysia", "SG": "Singapore", "PK": "Pakistan", "BD": "Bangladesh",
    "IR": "Iran", "IQ": "Iraq", "GE": "Georgia", "AM": "Armenia",
    "AZ": "Azerbaijan", "UZ": "Uzbekistan", "KG": "Kyrgyzstan", "TJ": "Tajikistan",
    "TM": "Turkmenistan", "MD": "Moldova", "LT": "Lithuania", "LV": "Latvia",
    "EE": "Estonia", "BG": "Bulgaria", "RS": "Serbia", "HR": "Croatia",
    "SK": "Slovakia", "SI": "Slovenia", "HU": "Hungary", "CY": "Cyprus",
    "MT": "Malta", "IS": "Iceland", "LU": "Luxembourg", "NZ": "New Zealand",
}


def flag_emoji(code: str | None) -> str:
    """ISO-3166 alpha-2 -> эмодзи-флаг (региональные индикаторы)."""
    code = (code or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code)


def country_label(code: str | None, language: str = "ru", fallback: str | None = None) -> str:
    """Возвращает "Румыния (RO)" (или английское название)."""
    code = (code or "").strip().upper()
    if not code:
        return fallback or "—"
    if language == "en":
        name = COUNTRY_NAMES_EN.get(code) or fallback or code
    else:
        name = COUNTRY_NAMES_RU.get(code) or fallback or code
    prefix = flag_emoji(code)
    return f"{prefix} {name} ({code})".strip()