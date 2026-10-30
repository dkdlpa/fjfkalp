"""Тексты интерфейса INFEX. Строго без эмодзи.

Один словарь на язык. Подстановки через str.format только там, где нужно.
"""
from __future__ import annotations

RU = {
     "main_title": (
        "<b>INFEX | Intelligence Search</b>\n\n"
        "Единая точка поиска информации.\n\n"
        "Выберите раздел для продолжения:"
    ),
     "btn_search": "Поиск",
    "btn_buy": "Купить запросы",
    "btn_profile": "Профиль",
     "btn_ref": "Реф система",
    "btn_info": "Информация",
    "btn_photo": "Фото",
    "btn_metadata": "Метаданные",
    "btn_geolocation": "Местоположение",
    "btn_photo_again": "Ещё фото",
    "photo_prompt": "Выберите режим.",
    "photo_meta_prompt": "Отправьте снимок КАК ФАЙЛ: скрепка, затем Файл (без сжатия). Обычное фото Telegram теряет метаданные.",
    "photo_geo_prompt": "Отправьте фото обычным способом или файлом. Чем чётче видны вывески, знаки и дорога, тем точнее результат.",
    "photo_analyzing": "Анализирую...",
    "photo_busy": "Предыдущий запрос ещё обрабатывается, подождите.",
    "photo_too_big": "Файл слишком большой (максимум 10 МБ).",
    "photo_not_image": "Это не изображение. Отправьте фото или файл с изображением.",
    "photo_error": "Сервис временно недоступен. Повторите запрос через минуту.",
    "photo_no_vision": "Модель не поддерживает анализ изображений.",

    "btn_back": "Назад",
    "btn_home": "В главное меню",
    "btn_check_sub": "Проверить подписку",
     "btn_language": "Язык",

    "sub_required": "Для доступа к боту подпишитесь на каналы:",
    "sub_still_required": "Подписка не подтверждена.",

     "ref_title": (
        "<b>Реф система</b>\n\n"
        "Ваша реферальная ссылка:\n"
        "{link}\n\n"
        "Приглашено пользователей: {count}\n"
        "Начислено запросов: {bonus}"
    ),
    "referral_joined": (
        "По вашей реферальной ссылке зарегистрировался новый пользователь. "
         "Вам начислено +{bonus} запросов."
    ),

    "profile_title": "<b>Ваш профиль</b>",
    "profile_body": (
        "ID: <code>{uid}</code>\n"
        "Имя: {name}\n"
        "Username: {username}\n"
        "Язык: {lang_name}\n\n"
        "Баланс запросов: {balance}\n"
        "Приглашено: {referrals}\n"
        "Пополнено: ${deposited}"
    ),

    "info_title": "<b>Информация</b>\nЧто здесь можно узнать:",
    "help": (
        "Собирает и структурирует информацию из открытых источников: Telegram, "
        "никнеймы, TikTok, email, телефон, IP и домены, Dork-поиск. Результат "
        "приходит отдельным HTML-отчетом."
    ),

     "btn_what": "Что умеет бот",
    "btn_dev": "Разработчик",
    "dev_title": (
        "Канал создателя: {creator}\n"
        "Создатель: {author}\n"
        "Новостной канал бота: {news}"
    ),

    "terms_title": "<b>Условия использования</b>\n\nПеред началом работы примите условия.",
    "terms_text": (
        "Бот собирает только открытые данные. Используйте его исключительно в "
        "законных целях и в рамках закона вашей юрисдикции. Запрещены преследование, "
        "сталкинг, травля и любые нарушения прав третьих лиц. Ответственность за "
        "использование результатов несёт пользователь.\n\nПолитика: {terms}"
    ),
    "btn_terms_accept": "Принимаю условия",
    "balance_short": "Баланс: {balance} запросов, триал: {trial} из {total}, до {ends}",
    "balance_full": "Баланс: {balance} запросов\nТриал: {trial} из {total}, до {ends}",
    "btn_repeat": "Повторить",
    "btn_other": "Другой тип поиска",
    "btn_buy_short": "Купить запросы",
    "btn_cancel": "Отмена",
    "btn_search_that": "Искать",
    "btn_not_that": "Это другое",
    "detected": "Определил: {kind} {value}",
    "choose_type": "Выберите тип поиска:",
    "search_cancelled": "Поиск отменён. Запрос не списан.",
    "no_charge": "Запрос не списан.",
    "sources_unavailable": "Источники временно недоступны. Запрос не списан.",
    "email_unknown": "Не удалось проверить",
    "admin_confirm": "Подтвердите действие",
    "btn_send": "Отправить",
    "btn_cancel_action": "Отмена",
    "summary_title": "<b>Кратко</b>",
    "summary_failed_sources": "Недоступные источники: {sources}",

    "lang_title": "Выберите язык интерфейса:",

    "buy_title": "<b>Купить запросы</b>\nВыберите пакет:",
    "buy_line": "{credits} запросов — ${price}",
    "pay_invoice": (
         "<b>Счёт создан</b>\n\n"
        "Пакет: {credits} запросов\n"
        "Сумма: ${price}\n\n"
        "После оплаты запросы начислятся автоматически."
    ),
    "btn_pay_open": "Оплатить",
    "btn_pay_check": "Я оплатил",
    "pay_success": "Оплата получена. Начислено {credits} запросов. Баланс: {balance}",
    "pay_pending": "Счёт ещё не оплачен.",
    "pay_expired": "Счёт истёк. Создайте новый.",
    "pay_already": "Этот счёт уже оплачен.",
    "pay_not_found": "Счёт не найден.",
    "pay_failed": "Не удалось создать счёт. Попробуйте позже.",
    "pay_unavailable": "Оплата временно недоступна.",
    "no_balance": "Запросы закончились. Пополните баланс в разделе «Купить запросы».",
    "trial_note": "Бесплатный пробный период истек.",

     "search_title": "<b>Поиск</b>\nВыберите инструмент:",
    "status_searching": "<b>Ищу информацию...</b>",
    "search_busy": "Предыдущий поиск ещё выполняется. Дождитесь результата.",
    "search_queued": "Вы в очереди. Поиск начнётся, как только освободится слот.",
    "report_caption": "Профиль: {target}",
    "telegram_hint": "Отправьте @username, ID или ссылку t.me/username.",
    "telegram_maintenance": "Поиск по Telegram временно на техобслуживании. Попробуйте позже.",
    "email_hint": "Отправьте email, например user@example.com.",
    "email_searching": "<b>Проверяю email...</b>",
    "email_invalid": "Некорректный email.",
    "tiktok_hint": "Отправьте @username или ссылку на профиль TikTok.",
    "tiktok_searching": "<b>Смотрю профиль TikTok...</b>",
    "tiktok_invalid": "Некорректный никнейм TikTok.",
    "username_hint": "Отправьте никнейм, например @username.",
    "username_searching": "<b>Проверяю площадки...</b>",
    "username_invalid": "Некорректный никнейм.",
    "username_report_caption": "Профиль @{username}. Найдено: {count}",
    "ip_hint": "Отправьте IP или домен, например 8.8.8.8 или example.com.",
    "ip_searching": "<b>Собираю данные...</b>",
    "ip_invalid": "Некорректный IP или домен.",
    "ip_private": "Приватный или зарезервированный IP не проверяется. Запрос не списан.",
    "phone_hint": "Отправьте номер в международном формате, например +7 999 000-00-00.",
    "phone_searching": "<b>Разбираю номер...</b>",
    "phone_invalid": "Некорректный номер.",
    "btn_telegram": "Telegram",
    "btn_dork": "Dork-поиск",
    "btn_email": "Email",
    "btn_tiktok": "TikTok",
    "btn_username": "Никнейм",
    "btn_ip_domain": "IP / Домен",
    "btn_phone": "Телефон",

    "dork_title": "<b>Dork-поиск</b>\nВыберите поисковую систему:",
    "dork_hint": "Отправьте email, никнейм, телефон или домен.",
    "dork_generating": "<b>Готовлю запросы...</b>",
    "dork_empty": "Не распознал данные.",
    "dork_failed": "Не удалось собрать запросы.",
    "dork_live_unavailable": "Поиск временно недоступен. Запросы сформированы, попробуйте позже.",
    "dork_report_caption": "Dork: {engine}. Запросов: {count}",
    "btn_dork_export": "Скачать отчет",
    "btn_dork_again": "Заново",
    "unknown_engine": "Неизвестная поисковая система.",

    "error_generic": "Что-то пошло не так. Попробуйте позже.",
    "invalid_input": "Некорректные данные.",
    "unavailable": "Источник не отвечает. Попробуйте позже.",
    "not_found": "Ничего не найдено.",
    "source_not_configured": "Источник не настроен.",
    "report_expired": "Сессия устарела. Запустите поиск заново.",
    "too_many": "Слишком часто. Подождите {seconds} сек.",
    "maintenance": "Бот на обслуживании. Загляните позже.",
    "private_link_missing": "Ссылка на канал не настроена. Сообщите администратору.",
    "channel_button": "Канал {n}",
    "lang_ru_name": "Русский",
    "lang_en_name": "English",
    "disclaimer_required": "Примите условия использования.",

    # ---- Новые модули раздела «Поиск» ----
    "btn_github": "GitHub",
    "btn_vk": "VK",
    "btn_ton": "TON-кошелёк",
    "btn_faces": "Лица на фото",
    "github_hint": "Отправьте никнейм GitHub или ссылку на профиль, например torvalds.",
    "github_invalid": "Некорректный никнейм GitHub.",
    "vk_hint": "Отправьте ID, никнейм или ссылку VK, например vk.com/id1.",
    "vk_invalid": "Некорректный VK ID.",
    "ton_hint": "Отправьте TON-адрес, например EQ... или UQ... .",
    "ton_invalid": "Некорректный TON-адрес.",
    "see_tg_expensive_disabled": "Этот тип поиска отключён администратором.",
    "source_maintenance": (
        "Поиск временно недоступен в связи с техническими работами. "
        "Попробуйте повторить запрос позже."
    ),
    "phone_extra_disabled": "Дополнительный источник не подключён.",
    "phone_extra_empty": "В дополнительном источнике совпадений не найдено.",
    "phone_extra_note": (
        "Это совпадения в источнике, а не подтверждение личности владельца номера."
    ),
    "btn_faces_detect": "Проверить лица",
    "btn_faces_compare": "Сравнить два фото",
    "caption_faces": "Лица: {count}",
    "caption_faces_compare": "Совпадение: {percent}%",
    "faces_prompt": "Выберите действие.",
    "faces_detect_prompt": "Отправьте фото. Покажу, есть ли на нём лица и сколько.",
    "faces_compare_first": "Отправьте первое фото для сравнения.",
    "faces_compare_second": "Теперь отправьте второе фото.",
    "faces_not_image": "Это не изображение. Отправьте фото или файл с изображением.",
    "faces_too_big": "Файл слишком большой (максимум 10 МБ).",
    "faces_cv_missing": (
        "Модуль распознавания лиц не установлен на сервере. "
        "Добавьте opencv-python-headless в окружение."
    ),
    "faces_error": "Не удалось обработать изображение.",
    "btn_open_report": "Открыть отчёт",
    "btn_delete_report": "Удалить отчёт",
    "report_link_caption": "Отчёт готов. Ссылка действует {minutes} мин.",
    "report_link_missing": "Ссылка на отчёт больше не доступна.",
    "report_deleted": "Отчёт удалён.",
    "report_delete_forbidden": "Этот отчёт вам не принадлежит.",
    "help_command": "Поддержка: {support}",
    "btn_support_open": "Написать в поддержку",
    "help_not_configured": "Бот поддержки не настроен.",
}

EN = {
     "main_title": (
        "<b>INFEX | Intelligence Search</b>\n\n"
        "A single point of information search.\n\n"
        "Choose a section to continue:"
    ),
     "btn_search": "Search",
    "btn_buy": "Buy requests",
    "btn_profile": "Profile",
     "btn_ref": "Ref system",
    "btn_info": "Information",
    "btn_photo": "Photo",
    "btn_metadata": "Metadata",
    "btn_geolocation": "Location",
    "btn_photo_again": "Another photo",
    "photo_prompt": "Choose a mode.",
    "photo_meta_prompt": "Send the picture AS A FILE: paperclip, then File (uncompressed). A regular photo loses metadata in Telegram.",
    "photo_geo_prompt": "Send a photo normally or as a file. The sharper the signs, road markings and text, the more accurate the result.",
    "photo_analyzing": "Analyzing...",
    "photo_busy": "The previous request is still being processed, please wait.",
    "photo_too_big": "The file is too large (maximum 10 MB).",
    "photo_not_image": "This is not an image. Send a photo or an image file.",
    "photo_error": "The service is temporarily unavailable. Try again in a minute.",
    "photo_no_vision": "The model does not support image analysis.",

    "btn_back": "Back",
    "btn_home": "Main menu",
    "btn_check_sub": "Check subscription",
     "btn_language": "Language",

    "sub_required": "Subscribe to the channels to access the bot:",
    "sub_still_required": "Subscription is not confirmed.",

     "ref_title": (
        "<b>Ref system</b>\n\n"
        "Your referral link:\n"
        "{link}\n\n"
        "Invited users: {count}\n"
        "Requests credited: {bonus}"
    ),
    "referral_joined": (
        "A new user registered via your referral link. "
        "You have been credited +{bonus} requests."
    ),

    "profile_title": "<b>Your profile</b>",
    "profile_body": (
        "ID: <code>{uid}</code>\n"
        "Name: {name}\n"
        "Username: {username}\n"
        "Language: {lang_name}\n\n"
        "Request balance: {balance}\n"
        "Invited: {referrals}\n"
        "Deposited: ${deposited}"
    ),

    "info_title": "<b>Information</b>\nWhat you can learn here:",
    "help": (
        "Collects and structures public information: Telegram, usernames, TikTok, "
        "email, phone, IP and domains, Dork search. Results are delivered as an "
        "HTML report."
    ),

     "btn_what": "What the bot can do",
    "btn_dev": "Developer",
    "dev_title": (
        "Creator channel: {creator}\n"
        "Creator: {author}\n"
        "Bot news channel: {news}"
    ),

    "terms_title": "<b>Terms of use</b>\n\nPlease accept the terms before use.",
    "terms_text": (
        "The bot collects public data only. Use it strictly for lawful purposes "
        "and in compliance with the law of your jurisdiction. Stalking, harassment "
        "and any violation of third-party rights are prohibited. The user bears "
        "responsibility for how the results are used.\n\nPolicy: {terms}"
    ),
    "btn_terms_accept": "I accept the terms",
    "balance_short": "Balance: {balance} requests, trial: {trial} of {total}, until {ends}",
    "balance_full": "Balance: {balance} requests\nTrial: {trial} of {total}, until {ends}",
    "btn_repeat": "Repeat",
    "btn_other": "Other search type",
    "btn_buy_short": "Buy requests",
    "btn_cancel": "Cancel",
    "btn_search_that": "Search",
    "btn_not_that": "Not this",
    "detected": "Detected: {kind} {value}",
    "choose_type": "Choose the search type:",
    "search_cancelled": "Search cancelled. No request was charged.",
    "no_charge": "No request was charged.",
    "sources_unavailable": "Sources are temporarily unavailable. No request was charged.",
    "email_unknown": "Could not verify",
    "admin_confirm": "Confirm the action",
    "btn_send": "Send",
    "btn_cancel_action": "Cancel",
    "summary_title": "<b>Summary</b>",
    "summary_failed_sources": "Unavailable sources: {sources}",

    "lang_title": "Choose interface language:",

    "buy_title": "<b>Buy requests</b>\nChoose a package:",
    "buy_line": "{credits} requests — ${price}",
    "pay_invoice": (
        "<b>Invoice created</b>\n\n"
        "Package: {credits} requests\n"
        "Amount: ${price}\n\n"
        "Requests are credited automatically after payment."
    ),
    "btn_pay_open": "Pay",
    "btn_pay_check": "I paid",
    "pay_success": "Payment received. Credited {credits} requests. Balance: {balance}",
    "pay_pending": "The invoice is not paid yet.",
    "pay_expired": "The invoice expired. Create a new one.",
    "pay_already": "This invoice is already paid.",
    "pay_not_found": "Invoice not found.",
    "pay_failed": "Could not create the invoice. Try later.",
    "pay_unavailable": "Payments are temporarily unavailable.",
    "no_balance": "Out of requests. Top up in the Buy requests section.",
    "trial_note": "The free trial period has ended.",

     "search_title": "<b>Search</b>\nChoose a tool:",
    "status_searching": "<b>Searching...</b>",
    "search_busy": "A previous search is still running. Please wait for the result.",
    "search_queued": "You are in the queue. The search starts as soon as a slot frees up.",
    "report_caption": "Profile: {target}",
    "telegram_hint": "Send @username, ID or a t.me/username link.",
    "telegram_maintenance": "Telegram search is temporarily under maintenance. Please try again later.",
    "email_hint": "Send an email, e.g. user@example.com.",
    "email_searching": "<b>Checking email...</b>",
    "email_invalid": "Invalid email.",
    "tiktok_hint": "Send @username or a TikTok profile link.",
    "tiktok_searching": "<b>Reading TikTok profile...</b>",
    "tiktok_invalid": "Invalid TikTok username.",
    "username_hint": "Send a username, e.g. @username.",
    "username_searching": "<b>Checking platforms...</b>",
    "username_invalid": "Invalid username.",
    "username_report_caption": "Profile @{username}. Found: {count}",
    "ip_hint": "Send an IP or domain, e.g. 8.8.8.8 or example.com.",
    "ip_searching": "<b>Collecting data...</b>",
    "ip_invalid": "Invalid IP or domain.",
    "ip_private": "Private or reserved IP is not checked. No request was charged.",
    "phone_hint": "Send a number in international format, e.g. +1 555 000-0000.",
    "phone_searching": "<b>Parsing the number...</b>",
    "phone_invalid": "Invalid number.",
    "btn_telegram": "Telegram",
    "btn_dork": "Dork search",
    "btn_email": "Email",
    "btn_tiktok": "TikTok",
    "btn_username": "Username",
    "btn_ip_domain": "IP / Domain",
    "btn_phone": "Phone",

    "dork_title": "<b>Dork search</b>\nChoose a search engine:",
    "dork_hint": "Send an email, username, phone or domain.",
    "dork_generating": "<b>Building queries...</b>",
    "dork_empty": "Could not recognize the data.",
    "dork_failed": "Could not build the queries.",
    "dork_live_unavailable": "Search is temporarily unavailable. Queries are ready, try again later.",
    "dork_report_caption": "Dork: {engine}. Queries: {count}",
    "btn_dork_export": "Download report",
    "btn_dork_again": "Again",
    "unknown_engine": "Unknown search engine.",

    "error_generic": "Something went wrong. Try later.",
    "invalid_input": "Invalid data.",
    "unavailable": "The source is not responding. Try later.",
    "not_found": "Nothing found.",
    "source_not_configured": "The source is not configured.",
    "report_expired": "Session expired. Start the search again.",
    "too_many": "Too many requests. Wait {seconds} sec.",
    "maintenance": "The bot is under maintenance. Check back later.",
    "private_link_missing": "Channel link is not configured. Please contact the administrator.",
    "channel_button": "Channel {n}",
    "lang_ru_name": "Russian",
    "lang_en_name": "English",
    "disclaimer_required": "Please accept the terms of use.",

    # ---- New modules in the Search section ----
    "btn_github": "GitHub",
    "btn_vk": "VK",
    "btn_ton": "TON wallet",
    "btn_faces": "Faces in photo",
    "github_hint": "Send a GitHub username or profile link, e.g. torvalds.",
    "github_invalid": "Invalid GitHub username.",
    "vk_hint": "Send a VK ID, username or link, e.g. vk.com/id1.",
    "vk_invalid": "Invalid VK ID.",
    "ton_hint": "Send a TON address, e.g. EQ... or UQ... .",
    "ton_invalid": "Invalid TON address.",
    "see_tg_expensive_disabled": "This search type is disabled by the administrator.",
    "source_maintenance": (
        "Search is temporarily unavailable due to maintenance. "
        "Please try again later."
    ),
    "phone_extra_disabled": "The additional source is not configured.",
    "phone_extra_empty": "No matches found in the additional source.",
    "phone_extra_note": (
        "These are matches in the source, not confirmation of the number owner."
    ),
    "btn_faces_detect": "Detect faces",
    "btn_faces_compare": "Compare two photos",
    "caption_faces": "Faces: {count}",
    "caption_faces_compare": "Match: {percent}%",
    "faces_prompt": "Choose an action.",
    "faces_detect_prompt": "Send a photo. I will show whether it has faces and how many.",
    "faces_compare_first": "Send the first photo to compare.",
    "faces_compare_second": "Now send the second photo.",
    "faces_not_image": "This is not an image. Send a photo or an image file.",
    "faces_too_big": "The file is too large (maximum 10 MB).",
    "faces_cv_missing": (
        "The face module is not installed on the server. "
        "Add opencv-python-headless to the environment."
    ),
    "faces_error": "Could not process the image.",
    "btn_open_report": "Open report",
    "btn_delete_report": "Delete report",
    "report_link_caption": "The report is ready. The link is valid for {minutes} min.",
    "report_link_missing": "The report link is no longer available.",
    "report_deleted": "The report was deleted.",
    "report_delete_forbidden": "This report does not belong to you.",
    "help_command": "Support: {support}",
    "btn_support_open": "Contact support",
    "help_not_configured": "The support bot is not configured.",
}

BY_LANG = {"ru": RU, "en": EN}