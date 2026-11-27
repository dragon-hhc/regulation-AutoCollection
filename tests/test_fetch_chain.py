"""#4 擷取鏈測試：全部以 monkeypatch 模擬，不實際連網。"""
import json
import sys

import pytest

from regcrawler.fetch import chain
from regcrawler.fetch.chain import FetchError, Fetcher, RawResponse, validate
from regcrawler.models import ANTI_BOT_FAIL_NOTE, FETCH_METHODS, OFFICIAL_API

ALL = (OFFICIAL_API,) + FETCH_METHODS
GOOD_HTML = "<html><head><title>個人資料保護法</title></head><body>第 1 條</body></html>".encode("utf-8")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("測試不可實際連網")
    monkeypatch.setattr(chain.requests, "get", boom)
    monkeypatch.setattr(chain.requests.Session, "get", boom)


def make_fetcher(monkeypatch, behaviours: dict, calls: list):
    """behaviours: method → RawResponse | Exception | callable(n)->RawResponse"""
    f = Fetcher(timeout=1, min_interval=0, sleep=lambda s: None)
    for name in ALL:
        def fn(url, expect, _n=name):
            calls.append(_n)
            b = behaviours.get(_n, FetchError(f"{_n} failed"))
            if callable(b) and not isinstance(b, (RawResponse, Exception)):
                b = b(calls.count(_n))
            if isinstance(b, Exception):
                raise b
            return b
        monkeypatch.setattr(f, f"_m_{name}", fn)
    return f


def test_all_five_fail(monkeypatch):
    calls = []
    f = make_fetcher(monkeypatch, {}, calls)
    r = f.get("https://law.example.gov.tw/x")
    assert r.ok is False
    assert r.note == ANTI_BOT_FAIL_NOTE
    assert r.note == "本次搜尋的網頁有 反爬蟲機制，嘗試 5 種不同方法之後仍無法取得資料"
    assert [a["method"] for a in r.attempts] == list(FETCH_METHODS)
    assert len(r.attempts) == 5
    assert all(a["ok"] is False and a["error"] for a in r.attempts)
    assert OFFICIAL_API not in calls


def test_official_api_success_stops(monkeypatch):
    calls = []
    f = make_fetcher(monkeypatch, {OFFICIAL_API: RawResponse(200, b'{"a": 1}')}, calls)
    r = f.get("https://api.example/x.json", method_hint=OFFICIAL_API, expect="json")
    assert r.ok and r.method_used == OFFICIAL_API
    assert calls == [OFFICIAL_API]
    assert len(r.attempts) == 1 and r.attempts[0]["ok"] is True
    assert r.note == ""


def test_official_api_fail_then_all_fail_six_attempts(monkeypatch):
    calls = []
    f = make_fetcher(monkeypatch, {}, calls)
    r = f.get("https://api.example/x", method_hint=OFFICIAL_API)
    assert r.note == ANTI_BOT_FAIL_NOTE
    assert [a["method"] for a in r.attempts] == list(ALL)


def test_falls_through_to_playwright(monkeypatch):
    calls = []
    f = make_fetcher(monkeypatch, {
        "requests": RawResponse(403, b"<html>forbidden</html>"),
        "requests_alt": RawResponse(200, b"<html><title>Just a moment...</title></html>"),
        "playwright": RawResponse(200, GOOD_HTML),
    }, calls)
    r = f.get("https://x.example/law")
    assert r.ok and r.method_used == "playwright"
    assert [a["method"] for a in r.attempts] == ["requests", "requests_alt", "playwright"]
    assert "HTTP 403" in r.attempts[0]["error"]
    assert "Cloudflare" in r.attempts[1]["error"]
    assert "playwright_human" not in calls and "wayback" not in calls
    assert "個人資料保護法" in r.text


def test_wayback_note_and_timestamp(monkeypatch):
    calls = []
    f = make_fetcher(monkeypatch, {"wayback": RawResponse(200, GOOD_HTML, wayback_timestamp="20251112093000")}, calls)
    r = f.get("https://x.example/law")
    assert r.ok and r.method_used == "wayback"
    assert r.wayback_timestamp == "20251112093000"
    assert r.note == "Wayback 快照 20251112093000"
    assert len(r.attempts) == 5


def test_wayback_disabled(monkeypatch):
    calls = []
    f = make_fetcher(monkeypatch, {"wayback": RawResponse(200, GOOD_HTML, wayback_timestamp="20250101000000")}, calls)
    r = f.get("https://x.example/law", wayback_ok=False)
    # 實際只試 4 種方法，不可宣稱「嘗試 5 種」（獨立驗證 #3 修正）
    assert not r.ok and r.note != ANTI_BOT_FAIL_NOTE and "嘗試 4 種方法" in r.note
    assert "wayback" not in calls
    assert [a["method"] for a in r.attempts] == list(FETCH_METHODS)


def test_retry_with_backoff_then_success(monkeypatch):
    calls = []
    sleeps = []

    def flaky(n):
        return RawResponse(503, b"busy") if n == 1 else RawResponse(200, GOOD_HTML)
    f = make_fetcher(monkeypatch, {"requests": flaky}, calls)
    f._sleep = sleeps.append
    r = f.get("https://x.example/law")
    assert r.ok and r.method_used == "requests"
    assert calls == ["requests", "requests"]
    assert r.attempts[0]["tries"] == 2
    assert len(sleeps) == 1 and sleeps[0] >= 2.0


def test_retry_max_two_per_method(monkeypatch):
    calls = []
    f = make_fetcher(monkeypatch, {m: FetchError("timeout", retryable=True) for m in FETCH_METHODS}, calls)
    r = f.get("https://x.example/law")
    assert not r.ok
    for m in FETCH_METHODS:
        assert calls.count(m) == 3          # 首次 + 最多重試 2 次
    assert all(a["tries"] == 3 for a in r.attempts)


def test_non_retryable_not_retried(monkeypatch):
    calls = []
    f = make_fetcher(monkeypatch, {"requests": RawResponse(404, b"nf")}, calls)
    f.get("https://x.example/law")
    assert calls.count("requests") == 1


def test_unexpected_exception_does_not_crash(monkeypatch):
    calls = []
    f = make_fetcher(monkeypatch, {"requests": RuntimeError("weird"),
                                   "requests_alt": RawResponse(200, GOOD_HTML)}, calls)
    r = f.get("https://x.example/law")
    assert r.ok and r.method_used == "requests_alt"
    assert "RuntimeError" in r.attempts[0]["error"]


def test_json_expect_invalid(monkeypatch):
    calls = []
    f = make_fetcher(monkeypatch, {m: RawResponse(200, b"<html>not json</html>") for m in FETCH_METHODS}, calls)
    r = f.get("https://x.example/a.json", expect="json")
    assert not r.ok and r.note == ANTI_BOT_FAIL_NOTE
    assert "JSON" in r.attempts[0]["error"]


@pytest.mark.parametrize("body,expect", [
    (b"", "html"),
    (b"   \n", "html"),
    (b"<html><title>Access Denied</title>You don't have permission to access", "html"),
    (b"<html>Request unsuccessful. Incapsula incident ID: 123</html>", "html"),
    (b"<html><body>The requested URL was rejected. Please consult with your administrator.</body></html>", "html"),
    (b'<html><div class="g-recaptcha" data-sitekey="x"></div></html>', "html"),
    ("<html><title>驗證碼</title></html>".encode("utf-8"), "html"),
    (b"<!DOCTYPE html><html><body>login</body></html>", "binary"),
    (b"{bad json", "json"),
])
def test_validate_failures(body, expect):
    with pytest.raises(FetchError):
        validate(RawResponse(200, body), expect)


@pytest.mark.parametrize("body,expect", [
    (GOOD_HTML, "html"),
    (b"%PDF-1.7\n...", "binary"),
    (b"PK\x03\x04zip", "binary"),
    ('﻿{"法規": "個人資料保護法"}'.encode("utf-8"), "json"),
    (b"<?xml version='1.0'?><LAWS/>", "xml"),
])
def test_validate_ok(body, expect):
    validate(RawResponse(200, body), expect)


def test_register_signature():
    chain.register_signature("測試WAF", r"BLOCKED-BY-TEST-WAF")
    try:
        with pytest.raises(FetchError, match="測試WAF"):
            validate(RawResponse(200, b"<html>BLOCKED-BY-TEST-WAF</html>"), "html")
    finally:
        chain.ANTI_BOT_SIGNATURES.pop()


def test_playwright_missing_is_failure_not_crash(monkeypatch):
    """playwright 匯入失敗 → 該方法記為失敗並繼續下一個。"""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("playwright"):
            raise ImportError("no playwright")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(sys.modules, "playwright.sync_api", raising=False)
    f = Fetcher(timeout=1, min_interval=0, sleep=lambda s: None)
    f._robots["x.example"] = 0.0
    for m in ("requests", "requests_alt"):
        monkeypatch.setattr(f, f"_m_{m}", lambda u, e: (_ for _ in ()).throw(FetchError("blocked")))
    monkeypatch.setattr(f, "_m_wayback", lambda u, e: RawResponse(200, GOOD_HTML, wayback_timestamp="20240101000000"))
    r = f.get("https://x.example/law")
    assert r.ok and r.method_used == "wayback"
    assert "playwright" in r.attempts[2]["error"] and r.attempts[2]["ok"] is False
    assert r.attempts[3]["method"] == "playwright_human" and r.attempts[3]["ok"] is False


class FakeResp:
    def __init__(self, status=200, content=b"", url="", text=""):
        self.status_code = status
        self.content = content
        self.url = url
        self.text = text or content.decode("utf-8", "ignore")
        self.headers = {}


def test_wayback_method_uses_available_api_and_id_url(monkeypatch):
    f = Fetcher(timeout=1, min_interval=0, sleep=lambda s: None)
    f._robots["archive.org"] = 0.0
    f._robots["web.archive.org"] = 0.0
    seen = []
    api_body = json.dumps({"archived_snapshots": {"closest": {
        "available": True, "status": "200", "timestamp": "20251201123456",
        "url": "http://web.archive.org/web/20251201123456/https://x.example/law"}}}).encode()

    def fake_get(self, url, headers=None, timeout=None, allow_redirects=True):
        seen.append(url)
        if url.startswith("https://archive.org/wayback/available"):
            return FakeResp(200, api_body, url)
        return FakeResp(200, GOOD_HTML, url)
    monkeypatch.setattr(chain.requests.Session, "get", fake_get)
    raw = f._m_wayback("https://x.example/law?a=1", "html")
    assert "url=https%3A%2F%2Fx.example%2Flaw%3Fa%3D1" in seen[0]
    assert "&timestamp=" in seen[0]
    assert seen[1] == "https://web.archive.org/web/20251201123456id_/https://x.example/law?a=1"
    assert raw.wayback_timestamp == "20251201123456"


def test_wayback_no_snapshot(monkeypatch):
    f = Fetcher(timeout=1, min_interval=0, sleep=lambda s: None)
    f._robots["archive.org"] = 0.0
    monkeypatch.setattr(chain.requests.Session, "get",
                        lambda self, url, **k: FakeResp(200, b'{"archived_snapshots": {}}', url))
    with pytest.raises(FetchError, match="無可用快照"):
        f._m_wayback("https://x.example/law", "html")


def test_throttle_min_interval_and_crawl_delay(monkeypatch):
    sleeps = []
    f = Fetcher(timeout=1, min_interval=2.0, sleep=sleeps.append)
    monkeypatch.setattr(chain.requests, "get",
                        lambda url, **k: FakeResp(200, b"User-agent: *\nCrawl-delay: 5\n", url))
    f._throttle("https://a.example/1")
    f._throttle("https://a.example/2")
    assert f._robots["a.example"] == 5.0
    assert len(sleeps) == 1 and sleeps[0] >= 4.5   # ≥ crawl-delay（扣掉極短經過時間）
    f._throttle("https://b.example/1")             # 不同網域不需等待
    assert len(sleeps) == 1


def test_robots_unreadable_does_not_block(monkeypatch):
    f = Fetcher(timeout=1, min_interval=0, sleep=lambda s: None)

    def boom(*a, **k):
        raise chain.requests.exceptions.ConnectionError("x")
    monkeypatch.setattr(chain.requests, "get", boom)
    assert f._crawl_delay("https://c.example/") == 0.0


def test_requests_alt_headers(monkeypatch):
    f = Fetcher(timeout=1, min_interval=0, sleep=lambda s: None)
    f._robots["x.example"] = 0.0
    seen = []

    def fake_get(self, url, headers=None, timeout=None, allow_redirects=True):
        merged = dict(self.headers)
        merged.update(headers or {})
        seen.append((url, merged))
        return FakeResp(200, GOOD_HTML, url)
    monkeypatch.setattr(chain.requests.Session, "get", fake_get)
    raw = f._m_requests_alt("https://x.example/law/a", "html")
    assert raw.status == 200
    assert seen[0][0] == "https://x.example/"
    url, h = seen[1]
    assert url == "https://x.example/law/a"
    assert h["Referer"] == "https://x.example/"
    assert h["Accept-Encoding"] == "identity"
    assert h["User-Agent"] == chain.UA_ALT != chain.UA_PRIMARY


def test_bezier_points_end_at_target():
    import random
    pts = chain._bezier_points((0, 0), (500, 300), 20, random.Random(1))
    assert len(pts) == 20
    assert pts[-1] == pytest.approx((500, 300))


def test_force_utf8_meta():
    h = '<html><head><meta http-equiv="Content-Type" content="text/html; charset=big5"><meta charset="BIG5"></head><body>第 1 條</body></html>'
    out = chain._force_utf8_meta(h)
    assert "big5" not in out.lower() and out.count("utf-8") == 2 and "第 1 條" in out
