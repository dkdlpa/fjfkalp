"""Анализ фото: локальные EXIF-метаданные и определение места через ИИ."""
from __future__ import annotations

import asyncio
import base64
import io
import logging

from openai import AsyncOpenAI
from PIL import Image, ExifTags

from app.config import settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — OSINT-аналитик по геолокации изображений. Главный принцип: точность важнее уверенного тона. Лучше честно указать широкую область, чем назвать точный адрес наугад.

ЖЁСТКИЕ ПРАВИЛА ЧЕСТНОСТИ:
1. Опирайся ТОЛЬКО на то, что реально видно на изображении. Не придумывай надписи, знаки, номера и детали. Если текст нечитаем или виден частично — так и скажи.
2. Различай три уровня: [ВИДНО] — прямое наблюдение; [ВЫВОД] — логическое следствие из наблюдения; [ДОГАДКА] — слабая гипотеза. Догадки не могут быть основанием для точных координат.
3. Одна улика почти никогда не доказывает локацию. Для высокой уверенности нужны минимум 3 независимых совпадающих маркера, и ни один не должен противоречить выводу.
4. Активно ищи опровержения: назови минимум одну альтернативную страну/регион и объясни, почему она менее вероятна.
5. Если маркеры противоречат друг другу, не скрывай это — укажи противоречие и снизь уверенность.
6. Не называй конкретные координаты точнее, чем позволяют улики. Нет привязки к конкретному объекту (читаемый адрес, уникальное здание, ориентир) — давай центр области и большой радиус.
7. Не подгоняй ответ под популярные места. Не опирайся на память о «похожих» панорамах без явных совпадений.
8. Если данных недостаточно, прямо напиши «Недостаточно данных» и перечисли, чего не хватает.

АЛГОРИТМ (выполняй последовательно, от общего к частному):
Шаг 1. Инвентаризация: перечисли всё различимое — текст, язык/алфавит, диакритику, домены, телефонные префиксы, номерные знаки, сторону движения, разметку, столбы, бордюры, архитектуру, растительность, почву, рельеф, положение солнца и теней (полушарие/широта), климат.
Шаг 2. Континент и полушарие — по самым надёжным маркерам (сторона движения, алфавит, растительность, солнце).
Шаг 3. Страна — отсей невозможные варианты, оставь 2–3 кандидата, сравни их по маркерам.
Шаг 4. Регион/город — только если есть узкие признаки (диалект надписей, региональные номера, названия, уникальные ориентиры).
Шаг 5. Проверка: что в изображении противоречит выбранной версии? Если есть что-то серьёзное — вернись и пересмотри.

КАЛИБРОВКА УВЕРЕННОСТИ:
• Высокая — есть читаемый текст/ориентир, однозначно указывающий место, и ≥3 согласующихся маркера. Радиус до ~5 км.
• Средняя — страна определена уверенно, регион вероятен по нескольким маркерам, точного ориентира нет. Радиус ~20–150 км.
• Низкая — только общие признаки (биом, сторона движения, стиль застройки). Радиус 200+ км или целая страна.
Не завышай уверенность. Если сомневаешься между уровнями — выбирай более низкий.

ФОРМАТ ОТВЕТА (без вводных слов):
Страна: <страна или «Не определена»> (вероятность ~X%)
Альтернативы: <страна 2 (~Y%), страна 3 (~Z%)>
Регион/Город: <значение или «Не определён»>
Координаты: <lat, lon — центр области поиска>
Радиус погрешности: <число км>
Уверенность: <Низкая / Средняя / Высокая>

Улики:
• [ВИДНО] <наблюдение> → <что оно означает>
• [ВЫВОД] <вывод> → <на каких наблюдениях основан>
• [ДОГАДКА] <гипотеза> (если есть)

Противоречия и слабые места: <что не сходится или не удалось проверить>
Что повысит точность: <какие доп. кадры/детали помогли бы>"""

_client = AsyncOpenAI(
    base_url=settings.ai_base_url,
    api_key=settings.ai_api_key or "none",
    timeout=settings.ai_timeout,
    max_retries=0,
)

_semaphore = asyncio.Semaphore(5)
_no_key_warned = False

UNAVAILABLE = "Сервис временно недоступен. Повторите запрос через минуту."
NO_VISION = "Модель не поддерживает анализ изображений."


def optimize_image_to_base64(data: bytes, max_side: int = 512, quality: int = 70) -> str:
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _is_vision_error(exc: Exception) -> bool:
    text = str(exc).lower()
    if "image" not in text and "vision" not in text and "multimodal" not in text:
        return False
    status = getattr(exc, "status_code", None)
    return status == 400 or "400" in text


def _model_chain() -> list[str]:
    """Основная модель, затем резервные — без повторов на одной модели."""
    chain: list[str] = []
    for model in (settings.ai_model, *settings.ai_fallback_models):
        model = (model or "").strip()
        if model and model not in chain:
            chain.append(model)
    return chain


async def analyze_location(
    data: bytes,
    user_id: int | None = None,
) -> tuple[str | None, str | None]:
    """Возвращает (текст, ошибка). Ошибки: None, \"unavailable\", \"no_vision\"."""
    global _no_key_warned

    if not settings.ai_api_key:
        if not _no_key_warned:
            _no_key_warned = True
            log.warning("AI_API_KEY пуст, анализ изображений недоступен")
        return None, "unavailable"

    try:
        encoded = await asyncio.to_thread(optimize_image_to_base64, data)
    except Exception:  # noqa: BLE001
        log.warning("Не удалось подготовить изображение (user_id=%s)", user_id)
        return None, "unavailable"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Определи место съёмки по фото."},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                },
            ],
        },
    ]

    models = _model_chain()
    vision_refusals = 0
    async with _semaphore:
        for model in models:
            try:
                response = await _client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=250,
                    temperature=0.2,
                )
            except Exception as exc:  # noqa: BLE001
                if _is_vision_error(exc):
                    vision_refusals += 1
                    log.warning(
                        "Модель %s отклонила изображение (user_id=%s)",
                        model,
                        user_id,
                    )
                else:
                    log.warning(
                        "Ошибка ИИ %s (user_id=%s): %s",
                        model,
                        user_id,
                        type(exc).__name__,
                    )
                continue
            try:
                text = (response.choices[0].message.content or "").strip()
            except Exception:  # noqa: BLE001
                text = ""
            if text:
                return text, None
            log.warning("Модель %s вернула пустой ответ (user_id=%s)", model, user_id)

    if models and vision_refusals == len(models):
        return None, "no_vision"
    return None, "unavailable"


def _fmt_float(value) -> str:
    try:
        return str(float(value))
    except Exception:  # noqa: BLE001
        return ""


def _as_text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("ascii", "ignore").strip()
        except Exception:  # noqa: BLE001
            return ""
    return str(value).strip()


def _dms_to_deg(values) -> float | None:
    try:
        degrees = float(values[0])
        minutes = float(values[1])
        seconds = float(values[2])
    except Exception:  # noqa: BLE001
        return None
    return degrees + minutes / 60 + seconds / 3600


def _tag_name(tag: int) -> str:
    return ExifTags.TAGS.get(tag, str(tag))


def _first(value):
    if isinstance(value, (tuple, list)):
        return value[0] if value else None
    return value


def build_metadata(data: bytes, was_file: bool) -> str:
    try:
        img = Image.open(io.BytesIO(data))
    except Exception:  # noqa: BLE001
        return "Это не изображение."

    lines: list[str] = []
    gps_found = False
    try:
        if img.format:
            lines.append(f"Формат: {img.format}")
        lines.append(f"Разрешение: {img.size[0]}x{img.size[1]}")
        lines.append(f"Размер файла: {len(data) / 1024:.1f} КБ")
        if img.mode:
            lines.append(f"Режим: {img.mode}")

        exif = img.getexif()
        has_exif = bool(exif)

        if exif:
            orientation = exif.get(0x0112)
            if orientation:
                lines.append(f"Ориентация: {orientation}")
            for tag in (0x010F, 0x0110, 0x0131, 0x0132):
                value = exif.get(tag)
                if value:
                    lines.append(f"{_tag_name(tag)}: {value}")

            try:
                shooting = exif.get_ifd(0x8769)
            except Exception:  # noqa: BLE001
                shooting = {}
            if shooting:
                original = shooting.get(0x9003) or shooting.get(0x9004)
                if original:
                    lines.append(f"Дата съёмки: {original}")
                exposure = shooting.get(0x829A)
                if exposure:
                    try:
                        value = float(exposure)
                        if value >= 1:
                            lines.append(f"Выдержка: {value:g} с")
                        elif value > 0:
                            lines.append(f"Выдержка: 1/{round(1 / value)} с")
                    except Exception:  # noqa: BLE001
                        lines.append(f"Выдержка: {exposure}")
                fnumber = shooting.get(0x829D)
                if fnumber:
                    lines.append(f"Диафрагма: f/{_fmt_float(fnumber)}")
                iso = _first(shooting.get(0x8827) or shooting.get(0x8833))
                if iso:
                    lines.append(f"ISO: {iso}")
                focal = shooting.get(0x920A)
                if focal:
                    lines.append(f"Фокусное расстояние: {_fmt_float(focal)} мм")
                lens = shooting.get(0xA434) or exif.get(0xA434)
                if lens:
                    lines.append(f"Объектив: {_as_text(lens)}")

            try:
                gps = exif.get_ifd(0x8825)
            except Exception:  # noqa: BLE001
                gps = {}
            if gps:
                lat = _dms_to_deg(gps.get(2) or ())
                lon = _dms_to_deg(gps.get(4) or ())
                if lat is not None and lon is not None:
                    if _as_text(gps.get(1, "")).upper() == "S":
                        lat = -lat
                    if _as_text(gps.get(3, "")).upper() == "W":
                        lon = -lon
                    lines.append(f"Широта: {lat:.6f}")
                    lines.append(f"Долгота: {lon:.6f}")
                    lines.append(f"Карта: https://www.google.com/maps?q={lat:.6f},{lon:.6f}")
                    altitude = gps.get(6)
                    if altitude is not None:
                        try:
                            lines.append(f"Высота: {float(altitude):.1f} м")
                        except Exception:  # noqa: BLE001
                            pass
                    gps_found = True

        if gps_found:
            lines.append(
                "Внимание: в метаданных есть точные координаты места съёмки."
            )

        if not has_exif:
            if was_file:
                lines.append("В файле нет метаданных.")
            else:
                lines.append("EXIF удалён Telegram. Отправьте снимок как файл.")
    except Exception:  # noqa: BLE001
        log.warning("Не удалось прочитать метаданные")
        return "Не удалось прочитать метаданные."
    finally:
        try:
            img.close()
        except Exception:  # noqa: BLE001
            pass

    return "\n".join(line for line in lines if line)