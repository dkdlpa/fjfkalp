"""Безопасная обработка изображений: лица и визуальные характеристики.

Что делает:
- определяет наличие лица на фото;
- считает количество лиц;
- возвращает крупный кроп каждого лица для локального просмотра;
- описывает визуальные характеристики (размер, ориентация, доминирующие цвета);
- сравнивает два изображения локально (перцептивный хеш + гистограмма) —
  только для проверки «это одно и то же фото / похожие кадры».

Чего НЕ делает (осознанно, по требованиям ТЗ):
- не ищет личность по фото через сторонние базы;
- не устанавливает аккаунты в соцсетях;
- не ходит на search4faces / similarfaces / любые «поиски по лицу»;
- не сохраняет изображения на диск.

Детекция лиц реализована на OpenCV Haar cascade (входит в opencv-python,
локально, без сети). Если OpenCV не установлен, модуль честно сообщает,
что проверка лиц недоступна — без имитации результата.

Все CPU-bound операции вызываются через asyncio.to_thread на стороне
хэндлера, здесь — обычные синхронные функции.
"""
from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_MAX_SIDE = 1600
_THUMB_SIDE = 96


@dataclass
class FaceResult:
    available: bool = False
    faces: int = 0
    boxes: list[tuple[int, int, int, int]] = field(default_factory=list)
    width: int = 0
    height: int = 0
    error: str | None = None

    @property
    def has_faces(self) -> bool:
        return self.faces > 0


@dataclass
class ImageFacts:
    width: int = 0
    height: int = 0
    format: str = ""
    mode: str = ""
    size_kb: float = 0.0
    orientation: str = ""
    dominant_colors: list[str] = field(default_factory=list)
    faces: FaceResult | None = None
    error: str | None = None


def _open(data: bytes):
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))
    img.load()
    img = ImageOps.exif_transpose(img)
    return img


def _cv2():
    try:
        import cv2  # type: ignore

        return cv2
    except Exception:  # noqa: BLE001
        return None


def _detect_haar(cv2, np, array):
    """OpenCV 4.x: каскад Хаара из комплекта пакета."""
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        return None
    return cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
    )


def _detect_yunet(cv2, np, array):
    """OpenCV 5.x: YuNet из objdetect (если каскадов нет)."""
    objdetect = getattr(cv2, "objdetect", None)
    if objdetect is None or not hasattr(objdetect, "FaceDetectorYN"):
        return None
    model = os.getenv("FACES_YUNET_MODEL", "").strip()
    if not model or not os.path.isfile(model):
        return None
    height, width = array.shape[:2]
    detector = objdetect.FaceDetectorYN.create(model, "", (width, height), 0.8, 0.3, 5000)
    _, faces_found = detector.detect(array)
    boxes = []
    if faces_found is not None:
        for face in faces_found:
            x, y, w, h = face[:4]
            boxes.append((int(x), int(y), int(w), int(h)))
    return boxes


def detect_faces(data: bytes) -> FaceResult:
    """Детекция лиц. Без сети и без внешних сервисов."""
    cv2 = _cv2()
    if cv2 is None:
        return FaceResult(available=False, error="cv2_missing")
    try:
        import numpy as np  # type: ignore

        img = _open(data).convert("RGB")
    except Exception:  # noqa: BLE001
        return FaceResult(available=True, error="not_an_image")

    width, height = img.size
    scale = min(1.0, _MAX_SIDE / max(width, height))
    if scale < 1.0:
        img = img.resize((int(width * scale), int(height * scale)))

    try:
        array = np.array(img)
        boxes = None
        if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data"):
            boxes = _detect_haar(cv2, np, array)
        if boxes is None or len(boxes) == 0:
            yunet = _detect_yunet(cv2, np, array)
            if yunet:
                boxes = yunet
        if boxes is None:
            return FaceResult(available=False, error="detector_missing")
    except Exception as exc:  # noqa: BLE001
        log.warning("face detect failed: %s", type(exc).__name__)
        return FaceResult(available=True, error="detect_failed")

    normalized = []
    for x, y, w, h in boxes:
        normalized.append(
            (
                int(x / scale),
                int(y / scale),
                int(w / scale),
                int(h / scale),
            )
        )
    return FaceResult(
        available=True,
        faces=len(normalized),
        boxes=normalized,
        width=width,
        height=height,
    )


def crop_face(data: bytes, box: tuple[int, int, int, int], margin: float = 0.25) -> bytes:
    """Кроп лица с полями. Возвращает JPEG-байты."""
    img = _open(data).convert("RGB")
    x, y, w, h = box
    mx, my = int(w * margin), int(h * margin)
    left = max(0, x - mx)
    top = max(0, y - my)
    right = min(img.width, x + w + mx)
    bottom = min(img.height, y + h + my)
    if right <= left or bottom <= top:
        crop = img
    else:
        crop = img.crop((left, top, right, bottom))
    crop.thumbnail((_THUMB_SIDE, _THUMB_SIDE))
    buffer = io.BytesIO()
    crop.save(buffer, format="JPEG", quality=80, optimize=True)
    return buffer.getvalue()


def _orientation(width: int, height: int) -> str:
    if width == height:
        return "square"
    return "landscape" if width > height else "portrait"


def _dominant_colors(img, limit: int = 4) -> list[str]:
    try:
        small = img.convert("RGB").resize((64, 64))
        quant = small.quantize(colors=limit, method=2)
        palette = quant.getpalette() or []
        counts = quant.getcolors() or []
        counts.sort(reverse=True)
        colors = []
        for _, index in counts[:limit]:
            base = index * 3
            if base + 2 >= len(palette):
                continue
            r, g, b = palette[base], palette[base + 1], palette[base + 2]
            colors.append(f"#{r:02x}{g:02x}{b:02x}")
        return colors
    except Exception:  # noqa: BLE001
        return []


def describe(data: bytes, *, with_faces: bool = True) -> ImageFacts:
    """Визуальные характеристики без идентификации человека."""
    try:
        img = _open(data)
    except Exception:  # noqa: BLE001
        return ImageFacts(error="not_an_image")

    facts = ImageFacts(
        width=img.width,
        height=img.height,
        format=(img.format or "").upper(),
        mode=img.mode,
        size_kb=round(len(data) / 1024, 1),
        orientation=_orientation(img.width, img.height),
        dominant_colors=_dominant_colors(img),
    )
    if with_faces:
        facts.faces = detect_faces(data)
    return facts


def _dhash(img, size: int = 8) -> int:
    gray = img.convert("L").resize((size + 1, size))
    pixels = list(gray.getdata())
    bits = 0
    index = 0
    for row in range(size):
        for col in range(size):
            left = pixels[row * (size + 1) + col]
            right = pixels[row * (size + 1) + col + 1]
            if left > right:
                bits |= 1 << index
            index += 1
    return bits


def _histogram(img) -> list[float]:
    small = img.convert("RGB").resize((48, 48))
    hist = small.histogram()
    # 768 значений -> 3 канала по 256
    total = sum(hist) or 1
    return [value / total for value in hist]


def compare(data_a: bytes, data_b: bytes) -> dict:
    """Локальное сравнение двух изображений. Ничего не ищет в сети."""
    try:
        img_a = _open(data_a)
        img_b = _open(data_b)
    except Exception:  # noqa: BLE001
        return {"error": "not_an_image"}

    hash_a = _dhash(img_a)
    hash_b = _dhash(img_b)
    distance = bin(hash_a ^ hash_b).count("1")
    similarity = round((1 - distance / 64) * 100, 1)

    hist_a = _histogram(img_a)
    hist_b = _histogram(img_b)
    # Пересечение гистограмм: 1.0 = одинаковое распределение цветов.
    overlap = sum(min(a, b) for a, b in zip(hist_a, hist_b))

    if similarity >= 90:
        verdict = "same"
    elif similarity >= 75:
        verdict = "similar"
    else:
        verdict = "different"

    return {
        "error": None,
        "distance": distance,
        "similarity": similarity,
        "color_overlap": round(overlap * 100, 1),
        "verdict": verdict,
        "size_a": [img_a.width, img_a.height],
        "size_b": [img_b.width, img_b.height],
        "faces_a": detect_faces(data_a).faces,
        "faces_b": detect_faces(data_b).faces,
    }


def cv_available() -> bool:
    return _cv2() is not None


__all__ = [
    "FaceResult",
    "ImageFacts",
    "compare",
    "crop_face",
    "cv_available",
    "describe",
    "detect_faces",
]