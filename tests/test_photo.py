"""Раздел Фото: метаданные и ИИ (без сети)."""
import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.services import photo_ai


def _jpeg(size=(100, 50), color=(10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG")
    return buffer.getvalue()


class _FakeExif(dict):
    def __init__(self, *a, shooting=None, gps=None, **kw):
        super().__init__(*a, **kw)
        self._shooting = shooting or {}
        self._gps = gps or {}

    def get_ifd(self, tag):
        if tag == 0x8769:
            return self._shooting
        if tag == 0x8825:
            return self._gps
        return {}


class _FakeImage:
    format = "JPEG"
    size = (4000, 3000)
    mode = "RGB"

    def __init__(self, exif):
        self._exif = exif

    def getexif(self):
        return self._exif

    def close(self):
        pass


def _use(monkeypatch, exif):
    object.__setattr__(exif, "__class__", _FakeExif)
    monkeypatch.setattr(photo_ai.Image, "open", lambda *a, **kw: _FakeImage(exif))


class TestMetadataBasics:
    def test_plain_photo_hint(self):
        text = photo_ai.build_metadata(_jpeg(), was_file=False)
        assert "Формат: JPEG" in text
        assert "Разрешение: 100x50" in text
        assert "EXIF удалён Telegram. Отправьте снимок как файл." in text

    def test_file_without_exif(self):
        text = photo_ai.build_metadata(_jpeg(), was_file=True)
        assert "В файле нет метаданных." in text

    def test_broken_file(self):
        assert photo_ai.build_metadata(b"nope", was_file=True) == "Это не изображение."


class TestMetadataExif:
    def _exif(self, **over):
        shooting = {
            0x9003: "2024:05:06 12:30:00",
            0x829A: 0.005,
            0x829D: 2.8,
            0x8827: (400, 800),
            0x920A: 35.0,
            0xA434: "Canon EF 35mm",
        }
        gps = {
            1: b"N",
            2: (55.0, 45.0, 21.0),
            3: b"E",
            4: (37.0, 37.0, 4.0),
            6: 150.5,
        }
        shooting.update(over.get("shooting", {}))
        gps.update(over.get("gps", {}))
        return _FakeExif(
            {0x0112: 6, 0x010F: "Canon", 0x0110: "EOS"},
            shooting=shooting,
            gps=gps,
        )

    def test_all_fields(self, monkeypatch):
        _use(monkeypatch, self._exif())
        text = photo_ai.build_metadata(b"x", was_file=True)
        assert "Ориентация: 6" in text
        assert "Canon" in text
        assert "Дата съёмки: 2024:05:06 12:30:00" in text
        assert "Выдержка: 1/200 с" in text
        assert "Диафрагма: f/2.8" in text
        assert "ISO: 400" in text
        assert "Фокусное расстояние: 35.0 мм" in text
        assert "Объектив: Canon EF 35mm" in text
        assert "Широта: 55.755833" in text
        assert "Долгота: 37.617778" in text
        assert "Высота: 150.5 м" in text
        assert "https://www.google.com/maps?q=55.755833,37.617778" in text
        assert "Внимание: в метаданных есть точные координаты места съёмки." in text
        assert "*" not in text and "_" not in text

    def test_south_west_negative(self, monkeypatch):
        exif = _FakeExif(
            {0x0110: "Phone"},
            gps={1: b"S", 2: (10.0, 0.0, 0.0), 3: b"W", 4: (20.0, 30.0, 0.0)},
        )
        _use(monkeypatch, exif)
        text = photo_ai.build_metadata(b"x", was_file=True)
        assert "Широта: -10.000000" in text
        assert "Долгота: -20.500000" in text

    def test_zero_exposure_no_crash(self, monkeypatch):
        _use(monkeypatch, self._exif(shooting={0x829A: 0}))
        text = photo_ai.build_metadata(b"x", was_file=True)
        assert "Выдержка" not in text


class TestOptimize:
    def test_jpeg_512(self):
        encoded = photo_ai.optimize_image_to_base64(_jpeg((3000, 2000)))
        raw = base64.b64decode(encoded)
        with Image.open(io.BytesIO(raw)) as img:
            assert img.format == "JPEG"
            assert max(img.size) <= 512

class _BadRequest(Exception):
    status_code = 400

    def __str__(self):
        return "400 image input is not supported"

class _Completions:
    def __init__(self, fail_times=0, text="Москва", error=None):
        self.calls = 0
        self.models: list[str] = []
        self.fail_times = fail_times
        self.text = text
        self.error = error

    async def create(self, **kwargs):
        self.calls += 1
        self.models.append(kwargs["model"])
        assert kwargs["max_tokens"] == 250
        assert kwargs["temperature"] == 0.2
        if self.calls <= self.fail_times:
            raise self.error or RuntimeError("boom")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.text))]
        )

class TestAI:
    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        object.__setattr__(photo_ai.settings, "ai_api_key", "test_key")
        object.__setattr__(photo_ai.settings, "ai_model", "deepseek-v4-flash")
        object.__setattr__(
            photo_ai.settings, "ai_fallback_models", ("gpt-5-4", "gemini-3.8-flash")
        )
        photo_ai._no_key_warned = False

    async def test_success(self, monkeypatch):
        completions = _Completions(fail_times=0)
        monkeypatch.setattr(photo_ai._client.chat, "completions", completions)
        text, error = await photo_ai.analyze_location(_jpeg(), user_id=1)
        assert text == "Москва" and error is None
        assert completions.models == ["deepseek-v4-flash"]

    async def test_next_model_on_error(self, monkeypatch):
        completions = _Completions(fail_times=1)
        monkeypatch.setattr(photo_ai._client.chat, "completions", completions)
        text, error = await photo_ai.analyze_location(_jpeg())
        assert text == "Москва" and error is None
        assert completions.calls == 2
        assert completions.models == ["deepseek-v4-flash", "gpt-5-4"]

    async def test_unavailable_after_all(self, monkeypatch):
        completions = _Completions(fail_times=99)
        monkeypatch.setattr(photo_ai._client.chat, "completions", completions)
        text, error = await photo_ai.analyze_location(_jpeg())
        assert text is None and error == "unavailable"
        assert completions.calls == 3
        assert completions.models == [
            "deepseek-v4-flash",
            "gpt-5-4",
            "gemini-3.8-flash",
        ]

    async def test_vision_error_tries_next_model(self, monkeypatch):
        completions = _Completions(fail_times=1, error=_BadRequest())
        monkeypatch.setattr(photo_ai._client.chat, "completions", completions)
        text, error = await photo_ai.analyze_location(_jpeg())
        assert text == "Москва" and error is None
        assert completions.calls == 2

    async def test_vision_error_all(self, monkeypatch):
        completions = _Completions(fail_times=99, error=_BadRequest())
        monkeypatch.setattr(photo_ai._client.chat, "completions", completions)
        text, error = await photo_ai.analyze_location(_jpeg())
        assert text is None and error == "no_vision"
        assert completions.calls == 3

    async def test_empty_key(self, monkeypatch):
        object.__setattr__(photo_ai.settings, "ai_api_key", "")
        completions = _Completions(fail_times=0)
        monkeypatch.setattr(photo_ai._client.chat, "completions", completions)
        text, error = await photo_ai.analyze_location(_jpeg())
        assert text is None and error == "unavailable"
        assert completions.calls == 0
