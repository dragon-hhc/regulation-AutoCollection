"""#4 五層備援擷取鏈（SPEC §5、§7.5 定案）。

順序：
    （method_hint="official_api" 時）official_api
    → requests → requests_alt → playwright → playwright_human → wayback
5 種反爬對抗方法全失敗 → FetchResult(ok=False, note=ANTI_BOT_FAIL_NOTE)。

不做：CAPTCHA 破解、代理池、繞過登入或存取控制、隱藏自動化特徵（navigator.webdriver 等）。

Wayback Machine API 查證來源：
  - Availability API：https://archive.org/help/wayback_api.php
    GET https://archive.org/wayback/available?url=<url>&timestamp=<1–14 位 YYYYMMDDhhmmss>
    回應 {"archived_snapshots": {"closest": {"available": true, "url": ..., "timestamp": "YYYYMMDDhhmmss", "status": "200"}}}
    無快照時回應 {"archived_snapshots": {}}
  - 原始內容（不改寫連結、不加工具列）：https://web.archive.org/web/<timestamp>id_/<url>
    參考 https://skeptric.com/restoring-wayback-html/ 、
    https://archive.org/post/1044859/how-do-i-retrieve-the-original-form-of-a-page-from-the-wayback-machine
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import quote, urlsplit

import requests

from ..models import ANTI_BOT_FAIL_NOTE, FETCH_METHODS, OFFICIAL_API, FetchResult

TPE = timezone(timedelta(hours=8))  # Asia/Taipei（無日光節約時間）

UA_PRIMARY = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
UA_ALT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0"
ROBOTS_UA = "regcrawler"          # robots.txt 比對用的 user-agent token
MAX_CRAWL_DELAY = 30.0            # robots Crawl-delay 上限（秒），避免異常設定拖垮整批

WAYBACK_AVAILABLE_API = "https://archive.org/wayback/available"
WAYBACK_RAW_FMT = "https://web.archive.org/web/{ts}id_/{url}"

REAL_VIEWPORTS = [(1920, 1080), (1536, 864), (1366, 768), (1440, 900), (1600, 900), (1280, 720)]


# ───────────────────────── 反爬特徵（可擴充） ─────────────────────────
# (標籤, regex)。regex 對解碼後文字做不分大小寫搜尋；請盡量具體，避免誤判正常法規頁面。
ANTI_BOT_SIGNATURES: list[tuple[str, re.Pattern]] = []


def register_signature(label: str, pattern: str) -> None:
    """新增反爬特徵（例如來源模組發現新的 WAF 擋頁）。"""
    ANTI_BOT_SIGNATURES.append((label, re.compile(pattern, re.I | re.S)))


for _label, _pat in [
    ("Cloudflare challenge", r"<title>\s*Just a moment\.\.\.\s*</title>"),
    ("Cloudflare challenge", r"cf-chl-|/cdn-cgi/challenge-platform/|cf_chl_opt"),
    ("Cloudflare block", r"Attention Required!\s*\|\s*Cloudflare"),
    ("Cloudflare Turnstile", r"class=[\"'][^\"']*cf-turnstile"),
    ("Access Denied", r"<title>\s*Access Denied\s*</title>"),
    ("Access Denied (Akamai)", r"You don't have permission to access .{0,200}on this server"),
    ("Incapsula", r"Request unsuccessful\.\s*Incapsula"),
    ("Incapsula", r"_Incapsula_Resource|Incapsula incident ID"),
    ("F5 BIG-IP ASM", r"The requested URL was rejected\.\s*Please consult with your administrator"),
    ("reCAPTCHA", r"class=[\"'][^\"']*g-recaptcha|www\.google\.com/recaptcha/api\.js"),
    ("hCaptcha", r"class=[\"'][^\"']*h-captcha|hcaptcha\.com/1/api\.js"),
    ("驗證碼頁", r"<title>[^<]{0,80}(驗證碼|captcha|人機驗證|安全驗證)[^<]{0,80}</title>"),
    ("FortiGuard block", r"<title>\s*Web (Page|Filter) Block"),
    ("DDoS-Guard", r"<title>\s*DDoS-Guard\s*</title>"),
    ("Sucuri", r"Sucuri WebSite Firewall - Access Denied"),
    ("AWS WAF", r"<title>\s*403 Forbidden\s*</title>.{0,400}(awselb|cloudfront)"),
]:
    register_signature(_label, _pat)


def detect_anti_bot(text: str) -> Optional[str]:
    for label, rx in ANTI_BOT_SIGNATURES:
        if rx.search(text):
            return label
    return None


# ───────────────────────── 內部型別 ─────────────────────────

# ── TLS 相容性（主控）────────────────────────────────────────────────
# law.moj.gov.tw 憑證缺 Subject Key Identifier，Python 3.13 預設的
# VERIFY_X509_STRICT 會拒絕連線。此處只清除 STRICT 旗標，仍保留
# 憑證鏈驗證（CERT_REQUIRED）與主機名稱驗證（check_hostname），並非關閉 TLS 驗證。
import ssl as _ssl
from requests.adapters import HTTPAdapter as _HTTPAdapter


class _LenientStrictAdapter(_HTTPAdapter):
    def init_poolmanager(self, *a, **kw):
        ctx = _ssl.create_default_context()
        if hasattr(_ssl, "VERIFY_X509_STRICT"):
            ctx.verify_flags &= ~_ssl.VERIFY_X509_STRICT
        import os as _os
        bundle = _os.environ.get("REQUESTS_CA_BUNDLE") or _os.environ.get("SSL_CERT_FILE")
        if bundle and _os.path.exists(bundle):
            ctx.load_verify_locations(bundle)
        kw["ssl_context"] = ctx
        return super().init_poolmanager(*a, **kw)


def _make_session() -> "requests.Session":
    s = requests.Session()
    s.mount("https://", _LenientStrictAdapter())
    return s

class FetchError(Exception):
    def __init__(self, msg: str, retryable: bool = False, retry_after: float | None = None):
        super().__init__(msg)
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass
class RawResponse:
    status: int
    content: bytes
    final_url: str = ""
    headers: dict = field(default_factory=dict)
    wayback_timestamp: str = ""


def _decode(content: bytes) -> str:
    return FetchResult(ok=True, url="", content=content).text


def _looks_like_html(content: bytes) -> bool:
    head = content[:2048].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<html" in head[:512]


def validate(raw: RawResponse, expect: str) -> None:
    """失敗判定：非 2xx、空內容、反爬特徵、expect 不符。失敗時丟 FetchError。"""
    if not (200 <= raw.status < 300):
        retryable = raw.status == 429 or raw.status >= 500
        ra = None
        if raw.status == 429:
            try:
                ra = float(raw.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                ra = None
        raise FetchError(f"HTTP {raw.status}", retryable=retryable, retry_after=ra)
    if not raw.content or not raw.content.strip():
        raise FetchError("內容為空", retryable=True)
    if expect == "binary":
        if _looks_like_html(raw.content):
            label = detect_anti_bot(_decode(raw.content[:200_000]))
            raise FetchError(f"命中反爬特徵：{label}" if label else "預期二進位檔，卻取得 HTML 頁面")
        return
    text = _decode(raw.content)
    label = detect_anti_bot(text[:300_000])
    if label:
        raise FetchError(f"命中反爬特徵：{label}")
    if expect == "json":
        try:
            json.loads(text.lstrip("﻿"))
        except ValueError as e:
            raise FetchError(f"JSON 無法解析：{e}") from None


_META_CHARSET_RE = re.compile(r"(<meta\b[^>]*?charset\s*=\s*[\"']?)([\w.:-]+)", re.I)


def _force_utf8_meta(html: str) -> str:
    """page.content() 已是解碼後字串、輸出為 UTF-8 bytes；把 <meta charset=big5> 等宣告改為 utf-8，
    避免下游 BeautifulSoup 依 meta 誤解碼。只改宣告，不動內文。"""
    return _META_CHARSET_RE.sub(lambda m: m.group(1) + "utf-8", html)


def wayback_label(ts: str) -> str:
    """Excel 資料來源欄用：「Wayback 快照 YYYYMMDDhhmmss」（SPEC §5）。"""
    return f"Wayback 快照 {ts}"


def _bezier_points(p0, p3, n, rng: random.Random):
    """三次貝茲曲線，隨機控制點。"""
    (x0, y0), (x3, y3) = p0, p3
    dx, dy = x3 - x0, y3 - y0
    p1 = (x0 + dx * rng.uniform(0.1, 0.5) + rng.uniform(-120, 120), y0 + dy * rng.uniform(0.0, 0.5) + rng.uniform(-120, 120))
    p2 = (x0 + dx * rng.uniform(0.5, 0.9) + rng.uniform(-120, 120), y0 + dy * rng.uniform(0.5, 1.0) + rng.uniform(-120, 120))
    pts = []
    for i in range(1, n + 1):
        t = i / n
        t = t * t * (3 - 2 * t)  # ease-in-out
        u = 1 - t
        x = u**3 * x0 + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * x3
        y = u**3 * y0 + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * y3
        pts.append((x, y))
    return pts


# ───────────────────────── Fetcher ─────────────────────────
class Fetcher:
    """SPEC §7.5：Fetcher(timeout=30, min_interval=2.0).get(url, *, method_hint=None, expect="html", wayback_ok=True)"""

    def __init__(self, timeout: float = 30, min_interval: float = 2.0, *,
                 retries: int = 2, headful: bool | None = None,
                 sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None):
        self.timeout = timeout
        self.min_interval = min_interval
        self.retries = retries                       # 每種方法內最多重試次數（不含首次）
        self.headful = (os.environ.get("REGCRAWL_HEADFUL") == "1") if headful is None else headful
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._last: dict[str, float] = {}
        self._robots: dict[str, float] = {}         # netloc → crawl delay（0 = 未指定/讀不到）
        self._lock = threading.Lock()
        self._session = _make_session()
        self._session.headers.update({
            "User-Agent": UA_PRIMARY,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.6,en;q=0.5",
        })

    # ── 公開介面 ──
    def get(self, url: str, *, method_hint: str | None = None, expect: str = "html",
            wayback_ok: bool = True) -> FetchResult:
        if expect not in ("html", "json", "xml", "binary"):
            raise ValueError(f"expect 不支援：{expect}")
        attempts: list[dict] = []
        methods: list[str] = []
        if method_hint == OFFICIAL_API:
            methods.append(OFFICIAL_API)
        methods.extend(FETCH_METHODS)
        for name in methods:
            if name == "wayback" and not wayback_ok:
                attempts.append({"method": name, "ok": False, "tries": 0,
                                 "error": "wayback_ok=False，略過"})
                continue
            raw, rec = self._run_method(name, url, expect)
            attempts.append(rec)
            if raw is not None:
                note = wayback_label(raw.wayback_timestamp) if name == "wayback" else ""
                return FetchResult(ok=True, url=url, content=raw.content, method_used=name,
                                   attempts=attempts, note=note, final_url=raw.final_url or url,
                                   wayback_timestamp=raw.wayback_timestamp)
        tried = sum(1 for a in attempts if a.get("method") in FETCH_METHODS and a.get("tries", 1) != 0)
        note = ANTI_BOT_FAIL_NOTE if tried >= len(FETCH_METHODS) else \
            f"嘗試 {tried} 種方法（Wayback 不適用於此類請求）仍無法取得資料"
        return FetchResult(ok=False, url=url, attempts=attempts, note=note)

    # ── 重試／退避 ──
    def _run_method(self, name: str, url: str, expect: str) -> tuple[Optional[RawResponse], dict]:
        fn = getattr(self, f"_m_{name}")
        rec = {"method": name, "ok": False, "tries": 0, "error": ""}
        t0 = time.monotonic()
        for i in range(self.retries + 1):
            rec["tries"] = i + 1
            try:
                raw = fn(url, expect)
                validate(raw, expect)
                rec.update(ok=True, error="", status=raw.status)
                if raw.wayback_timestamp:
                    rec["wayback_timestamp"] = raw.wayback_timestamp
                rec["elapsed"] = round(time.monotonic() - t0, 2)
                return raw, rec
            except FetchError as e:
                rec["error"] = str(e)
                if not e.retryable or i >= self.retries:
                    break
                wait = e.retry_after if e.retry_after else (2 ** i) * 2.0
                self._sleep(min(wait, 60.0) + self._rng.uniform(0, 1.0))
            except Exception as e:  # 任何非預期錯誤都只記為此方法失敗，不讓程式崩潰
                rec["error"] = f"{type(e).__name__}: {e}"[:500]
                break
        rec["elapsed"] = round(time.monotonic() - t0, 2)
        return None, rec

    # ── 禮貌性：robots Crawl-delay ＋ 每網域間隔 ──
    def _crawl_delay(self, url: str) -> float:
        parts = urlsplit(url)
        key = parts.netloc
        if key in self._robots:
            return self._robots[key]
        delay = 0.0
        try:
            r = requests.get(f"{parts.scheme}://{key}/robots.txt", timeout=min(self.timeout, 10),
                             headers={"User-Agent": UA_PRIMARY})
            if r.status_code == 200 and r.text:
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(r.text.splitlines())
                cd = rp.crawl_delay(ROBOTS_UA)
                if cd is None:
                    cd = rp.crawl_delay("*")
                if cd:
                    delay = min(float(cd), MAX_CRAWL_DELAY)
        except Exception:
            delay = 0.0   # 讀不到 robots.txt 不阻擋
        self._robots[key] = delay
        return delay

    def _throttle(self, url: str) -> None:
        key = urlsplit(url).netloc
        interval = max(self.min_interval, self._crawl_delay(url))
        interval += self._rng.uniform(0, max(0.5, self.min_interval * 0.5))  # 隨機抖動
        with self._lock:
            last = self._last.get(key)
            now = time.monotonic()
            if last is not None and now - last < interval:
                self._sleep(interval - (now - last))
            self._last[key] = time.monotonic()

    # ── requests 共用 ──
    def _http_get(self, session: requests.Session, url: str, headers: dict | None = None) -> RawResponse:
        self._throttle(url)
        try:
            r = session.get(url, headers=headers, timeout=self.timeout, allow_redirects=True)
        except requests.exceptions.SSLError as e:
            raise FetchError(f"SSL 錯誤：{e}"[:300]) from None
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            raise FetchError(f"連線錯誤：{type(e).__name__}", retryable=True) from None
        except requests.exceptions.RequestException as e:
            raise FetchError(f"{type(e).__name__}: {e}"[:300]) from None
        return RawResponse(status=r.status_code, content=r.content or b"", final_url=r.url,
                           headers=dict(r.headers))

    @staticmethod
    def _accept_for(expect: str) -> str:
        return {
            "json": "application/json, text/plain;q=0.9, */*;q=0.5",
            "xml": "application/xml, text/xml;q=0.9, */*;q=0.5",
            "binary": "application/pdf, application/octet-stream, application/zip, */*;q=0.8",
        }.get(expect, "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")

    # ── 方法 0：official_api ──
    def _m_official_api(self, url: str, expect: str) -> RawResponse:
        return self._http_get(self._session, url, headers={"Accept": self._accept_for(expect)})

    # ── 方法 1：requests ──
    def _m_requests(self, url: str, expect: str) -> RawResponse:
        return self._http_get(self._session, url, headers={"Accept": self._accept_for(expect)})

    # ── 方法 2：requests_alt ──
    def _m_requests_alt(self, url: str, expect: str) -> RawResponse:
        """不同 UA、HTTP/1.1 風格 header、關閉壓縮、Referer=網域首頁、先 GET 首頁取得 cookie。
        （requests/urllib3 本身即為 HTTP/1.1。）"""
        parts = urlsplit(url)
        home = f"{parts.scheme}://{parts.netloc}/"
        s = _make_session()
        s.headers.clear()
        s.headers.update({
            "User-Agent": UA_ALT,
            "Accept": self._accept_for(expect),
            "Accept-Language": "zh-TW,zh;q=0.8,en-US;q=0.5,en;q=0.3",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        })
        try:
            self._http_get(s, home)          # 只為取得 cookie，結果不判定
        except FetchError:
            pass
        try:
            return self._http_get(s, url, headers={"Referer": home})
        finally:
            s.close()

    # ── 方法 3、4：playwright ──
    def _m_playwright(self, url: str, expect: str) -> RawResponse:
        return self._browser_fetch(url, expect, human=False)

    def _m_playwright_human(self, url: str, expect: str) -> RawResponse:
        return self._browser_fetch(url, expect, human=True)

    def _browser_fetch(self, url: str, expect: str, human: bool) -> RawResponse:
        try:
            from playwright.sync_api import sync_playwright  # 延遲匯入
        except Exception as e:
            raise FetchError(f"playwright 未安裝：{e}") from None
        self._throttle(url)
        tmo = int(self.timeout * 1000)
        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(headless=not (human and self.headful))
                except Exception as e:
                    raise FetchError(f"瀏覽器無法啟動（未安裝？）：{str(e).splitlines()[0][:200]}") from None
                try:
                    ctx_kwargs = {"user_agent": UA_PRIMARY, "locale": "zh-TW",
                                  "extra_http_headers": {"Accept-Language": "zh-TW,zh;q=0.9,en;q=0.5"},
                                  "accept_downloads": True}
                    if human:
                        w, h = self._rng.choice(REAL_VIEWPORTS)
                        ctx_kwargs.update(viewport={"width": w, "height": h}, timezone_id="Asia/Taipei",
                                          screen={"width": w, "height": h}, device_scale_factor=1)
                    ctx = browser.new_context(**ctx_kwargs)
                    page = ctx.new_page()
                    page.set_default_timeout(tmo)
                    if expect == "binary":
                        parts = urlsplit(url)
                        home = f"{parts.scheme}://{parts.netloc}/"
                        try:
                            page.goto(home, wait_until="networkidle", timeout=tmo)  # 取得 cookie
                            if human:
                                self._human_actions(page)
                        except Exception:
                            pass
                        resp = page.request.get(url, timeout=tmo, headers={"Referer": home})
                        return RawResponse(status=resp.status, content=resp.body(), final_url=resp.url)
                    resp = page.goto(url, wait_until="networkidle", timeout=tmo)
                    if human:
                        self._human_actions(page)
                    status = resp.status if resp else 0
                    if expect in ("json", "xml") and resp is not None:
                        body = resp.body()
                    else:
                        body = _force_utf8_meta(page.content()).encode("utf-8")
                    return RawResponse(status=status, content=body, final_url=page.url)
                finally:
                    browser.close()
        except FetchError:
            raise
        except Exception as e:
            msg = str(e).splitlines()[0] if str(e) else ""
            retryable = "Timeout" in type(e).__name__ or "timeout" in msg.lower()
            raise FetchError(f"{type(e).__name__}: {msg}"[:300], retryable=retryable) from None

    def _human_actions(self, page) -> None:
        """隨機貝茲曲線滑鼠移動、分段捲動、1.5–6 秒隨機停頓。"""
        rng = self._rng
        vp = page.viewport_size or {"width": 1366, "height": 768}
        w, h = vp["width"], vp["height"]
        pos = (rng.uniform(0, w), rng.uniform(0, h))
        page.wait_for_timeout(rng.uniform(1.5, 6.0) * 1000)
        for _ in range(rng.randint(2, 4)):
            target = (rng.uniform(w * 0.05, w * 0.95), rng.uniform(h * 0.05, h * 0.95))
            for x, y in _bezier_points(pos, target, rng.randint(18, 40), rng):
                page.mouse.move(max(0, min(w - 1, x)), max(0, min(h - 1, y)))
                page.wait_for_timeout(rng.uniform(8, 30))
            pos = target
            page.wait_for_timeout(rng.uniform(300, 1200))
        for _ in range(rng.randint(3, 7)):
            page.mouse.wheel(0, rng.randint(250, 700))
            page.wait_for_timeout(rng.uniform(1.5, 6.0) * 1000 if rng.random() < 0.3 else rng.uniform(400, 1500))
        if rng.random() < 0.5:
            page.mouse.wheel(0, -rng.randint(200, 600))
        page.wait_for_timeout(rng.uniform(1.5, 6.0) * 1000)

    # ── 方法 5：wayback ──
    def _m_wayback(self, url: str, expect: str) -> RawResponse:
        ts_now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        api = f"{WAYBACK_AVAILABLE_API}?url={quote(url, safe='')}&timestamp={ts_now}"
        meta = self._http_get(self._session, api, headers={"Accept": "application/json"})
        if meta.status != 200:
            raise FetchError(f"Wayback available API HTTP {meta.status}",
                             retryable=meta.status == 429 or meta.status >= 500)
        try:
            closest = json.loads(_decode(meta.content)).get("archived_snapshots", {}).get("closest") or {}
        except ValueError:
            raise FetchError("Wayback available API 回應無法解析", retryable=True) from None
        ts = str(closest.get("timestamp") or "")
        if not closest.get("available") or not re.fullmatch(r"\d{14}", ts):
            raise FetchError("Wayback 無可用快照")
        raw = self._http_get(self._session, WAYBACK_RAW_FMT.format(ts=ts, url=url),
                             headers={"Accept": self._accept_for(expect)})
        raw.wayback_timestamp = ts
        return raw


__all__ = ["Fetcher", "FetchError", "RawResponse", "ANTI_BOT_SIGNATURES", "register_signature",
           "detect_anti_bot", "validate", "wayback_label"]
