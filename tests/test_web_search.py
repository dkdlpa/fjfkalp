"""Тесты веб-поиска без сети (httpx замокан через get_json)."""
import pytest

from app.config import settings
from app.services import web_search


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    web_search._cache.clear()
    web_search._last_warn.clear()
    web_search._no_key_warned = False
    object.__setattr__(settings, "searchapi_key", "test_key")
    object.__setattr__(settings, "searchapi_locale", "ru-ru")


def _payload():
    return {
        "organic_results": [
            {"position": 1, "title": "Example", "link": "https://example.com", "snippet": "s"},
            {"position": 2, "title": "Two", "link": "https://two.example", "snippet": "s2"},
        ],
        "ai_overview": {"answer": "Short answer", "sources": [{"title": "Src", "link": "https://a.b"}]},
    }


class TestSearchDetailed:
    async def test_returns_results(self, monkeypatch):
        calls = {}

        async def fake_get_json(url, params=None, headers=None, **kw):
            calls["headers"] = headers
            calls["params"] = params
            calls["url"] = url
            return _payload(), None

        monkeypatch.setattr(web_search, "get_json", fake_get_json)
        result = await web_search.search_detailed("test query", max_results=5)
        assert result["results"][0]["url"] == "https://example.com"
        assert result["source"] == "searchapi"
        assert result["error"] is None
        assert result["ai_overview"]["answer"] == "Short answer"

    async def test_auth_header_no_key_in_params(self, monkeypatch):
        captured = {}

        async def fake_get_json(url, params=None, headers=None, **kw):
            captured["params"] = params or {}
            captured["headers"] = headers or {}
            return _payload(), None

        monkeypatch.setattr(web_search, "get_json", fake_get_json)
        await web_search.search_detailed("q")
        assert captured["headers"]["Authorization"] == "Bearer test_key"
        assert "api_key" not in captured["params"]
        assert "test_key" not in str(captured["params"])

    async def test_ttl_cache_no_second_request(self, monkeypatch):
        counter = {"n": 0}

        async def fake_get_json(url, **kw):
            counter["n"] += 1
            return _payload(), None

        monkeypatch.setattr(web_search, "get_json", fake_get_json)
        await web_search.search_detailed("same", max_results=5)
        await web_search.search_detailed("same", max_results=5)
        assert counter["n"] == 1


class TestLocale:
    def test_mappings(self):
        assert web_search._locale_for("ru") == "ru-ru"
        assert web_search._locale_for("ua") == "ua-uk"
        assert web_search._locale_for("uk") == "ua-uk"
        assert web_search._locale_for("en") == "us-en"
        assert web_search._locale_for("us") == "us-en"
        assert web_search._locale_for("wt-wt") == "ru-ru"

    def test_invalid_becomes_valid(self, monkeypatch):
        object.__setattr__(settings, "searchapi_locale", "ru_RU")
        assert web_search._locale_for("") == "wt-wt"


class TestSafe:
    def test_legacy_values(self):
        assert web_search._safe_for("1") == "moderate"
        assert web_search._safe_for("0") == "off"
        assert web_search._safe_for("2") == "on"

    def test_keeps_off(self):
        assert web_search._safe_for("off") == "off"


class TestFallbackOnError:
    @pytest.mark.parametrize("err", ["http_401: bad key", "http_429: quota"])
    async def test_warns_and_falls_back(self, monkeypatch, caplog, err):
        async def fake_get_json(url, **kw):
            return None, err

        async def fake_ddgs(query, max_results, region, timeout):
            return [{"title": "T", "url": "https://f.example", "snippet": ""}]

        async def fake_nothing(*a, **kw):
            return []

        monkeypatch.setattr(web_search, "get_json", fake_get_json)
        monkeypatch.setattr(web_search, "_search_ddgs", fake_ddgs)
        monkeypatch.setattr(web_search, "_search_ddg_lite", fake_nothing)
        monkeypatch.setattr(web_search, "_search_yahoo", fake_nothing)
        with caplog.at_level("WARNING"):
            result = await web_search.search_detailed("q")
        assert result["source"] == "ddgs"
        assert result["results"]
        assert any("SearchApi" in r.message for r in caplog.records)

    async def test_warning_throttled(self, monkeypatch, caplog):
        async def fake_get_json(url, **kw):
            return None, "http_401: nope"

        async def fake_nothing(*a, **kw):
            return []

        monkeypatch.setattr(web_search, "get_json", fake_get_json)
        monkeypatch.setattr(web_search, "_search_ddgs", fake_nothing)
        monkeypatch.setattr(web_search, "_search_ddg_lite", fake_nothing)
        monkeypatch.setattr(web_search, "_search_yahoo", fake_nothing)
        with caplog.at_level("WARNING"):
            await web_search.search_detailed("a")
            await web_search.search_detailed("b")
        auth_warnings = [r for r in caplog.records if "SearchApi" in r.message]
        assert len(auth_warnings) == 1


class TestNoKey:
    async def test_no_key_returns_fallback_or_no_key(self, monkeypatch):
        object.__setattr__(settings, "searchapi_key", "")

        async def fake_nothing(*a, **kw):
            return []

        monkeypatch.setattr(web_search, "_search_ddgs", fake_nothing)
        monkeypatch.setattr(web_search, "_search_ddg_lite", fake_nothing)
        monkeypatch.setattr(web_search, "_search_yahoo", fake_nothing)
        result = await web_search.search_detailed("q")
        assert result["error"] in {"no_key", "unavailable"}
        assert result["results"] == []


class TestSearchMany:
    async def test_merges_and_no_error_when_results(self, monkeypatch):
        async def fake_detailed(*a, **kw):
            return {"results": [{"title": "T", "url": "https://x.y", "snippet": ""}],
                    "knowledge_graph": {}, "top_stories": [], "related_searches": [],
                    "ai_overview": {}, "source": "searchapi", "error": None}

        monkeypatch.setattr(web_search, "search_detailed", fake_detailed)
        result = await web_search.search_many_detailed(["a", "b"])
        assert result["error"] is None
        assert result["source"] == "searchapi"
        assert len(result["results"]) == 1

    async def test_logs_exceptions(self, monkeypatch, caplog):
        async def boom(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(web_search, "search_detailed", boom)
        with caplog.at_level("WARNING"):
            result = await web_search.search_many_detailed(["a"])
        assert result["results"] == []
        assert result["error"] == "unavailable"
        assert any("group failed" in r.message for r in caplog.records)