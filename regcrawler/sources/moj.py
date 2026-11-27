"""法務部全國法規資料庫（#1）。

資料取得順序（皆透過 fetcher.get，不自行連網）：
1. 官方開放資料（Open API，ZIP 內含 JSON）
   - 法律：https://law.moj.gov.tw/api/ch/law/json   （ZIP 成員 ChLaw.json）
   - 命令：https://law.moj.gov.tw/api/ch/order/json （ZIP 成員為 .json，取第一個 .json）
   查證來源（2026-11-27）：
   - 全國法規資料庫頁尾「API文件」連結 https://law.moj.gov.tw/api/swagger
     （WebFetch https://law.moj.gov.tw/LawClass/LawOldVer.aspx?pcode=I0050021 的頁尾連結清單）
   - 政府資料開放平臺 https://data.gov.tw/dataset/18289 （中文法規_法律資料檔下載）、
     https://data.gov.tw/dataset/18290 （中文法規_命令資料檔下載）；欄位：法規性質、法規名稱、
     法規網址、法規類別、最新異動日期、生效日期、生效內容、廢止註記、是否英譯註記、英文法規名稱、
     附件、沿革內容、前言、法規內容（WebFetch https://data.gov.tw/en/datasets/18289）
   - 端點實際使用之開源專案：github.com/cormort/LawsGraphify src/fetch_laws.py（MOJ_URLS）、
     github.com/lcmami/rag-knowledge-assistant evaluation/corpus/moj_law.py（ZIP 成員 ChLaw.json /
     schema.csv / manifest.csv；UTF-8 with BOM；頂層 {"UpdateDate", "Laws"}）
   - JSON 欄位對照（官方 API 原樣鏡像 github.com/kong0107/mojLawSplitJSON split 分支，
     tag 20261113_swagger）：LawLevel、LawName、LawURL、LawCategory、LawModifiedDate(YYYYMMDD)、
     LawEffectiveDate(YYYYMMDD；"99991231" 表示官方標示「最後生效日期：未定」)、LawEffectiveNote、
     LawAbandonNote、LawHasEngVersion、EngLawName、LawAttachements、LawHistories、LawForeword、
     LawArticles[{ArticleType: "A" 條文 / "C" 編章節（行首空白 3 的倍數表深度）, ArticleNo, ArticleContent}]
   注意：law.moj.gov.tw 憑證缺 Subject Key Identifier，Python 3.13 的 VERIFY_X509_STRICT 會拒絕
   （LawsGraphify 註解）；由 #4 Fetcher 處理。
2. 開放資料不可得時，退回 HTML：
   - 全文：https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=<pcode>
     （WebFetch 查證：頁面有「法規名稱／修正日期／法規類別」表頭、條號如「第 1-1 條」，
      頁籤：所有條文、編章節、條號查詢、條文檢索、授權子法、沿革、立法歷程）
   - 沿革：https://law.moj.gov.tw/LawClass/LawHistory.aspx?pcode=<pcode>（LawAll 頁籤連結）
   - HTML 選擇器：#hlLawName、.row > .col-no / .law-article 取自開源爬蟲
     github.com/audi0417/LawCrawler-Refactored crawler/central_law_crawler.py；
     開發沙箱無法取得原始 HTML，故採多組候選選擇器並以文字樣式（「第 N 條」「修正日期：」）容錯。
3. 上一版（fetch_previous）：https://law.moj.gov.tw/LawClass/LawOldVer.aspx?pcode=<pcode>
   WebFetch 查證（2026-11-27）：不帶 lnndate 時顯示「歷史法規所有條文」，即緊接前一版
   （個人資料保護法顯示「修正日期：民國 112 年 05 月 31 日」，第 27 條為刪除前原文）。

函釋：WebFetch 查證 LawAll 頁籤（https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0030297、
I0050021）未提供函釋入口；全國法規資料庫「司法解釋」為大法官解釋／憲法法庭裁判，非行政函釋。
include_interpretations=True 的函釋改由主管機關網站（目前無已查證之列表頁）或臺北市法規查詢系統
（轉載中央函釋）取得，見檔尾「函釋」區段。
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urlparse

from ..models import (
    NA,
    OFFICIAL_API,
    Article,
    FetchResult,
    LawDocument,
    SeedEntry,
)
from ..normalize import cn_to_int

SOURCE_NAME = "法務部全國法規資料庫"

BASE = "https://law.moj.gov.tw"
# 官方開放資料端點（候選依序嘗試；第二個為大小寫不同之同一端點，見模組說明）
API_LAW_URLS = ("https://law.moj.gov.tw/api/ch/law/json", "https://law.moj.gov.tw/api/Ch/Law/JSON")
API_ORDER_URLS = ("https://law.moj.gov.tw/api/ch/order/json", "https://law.moj.gov.tw/api/Ch/Order/JSON")
LAWALL_URL = BASE + "/LawClass/LawAll.aspx?pcode={pcode}"
LAWHISTORY_URL = BASE + "/LawClass/LawHistory.aspx?pcode={pcode}"
LAWOLDVER_URL = BASE + "/LawClass/LawOldVer.aspx?pcode={pcode}"
# 搜尋頁：未能查證（WebFetch 被 robots 拒絕），僅作為 seed 無 pcode 且開放資料失敗時的最後手段，
# 解析失敗即回報 fetch_note，不猜 pcode。
SEARCH_URL_CANDIDATES = (
    BASE + "/Law/LawSearchResult.aspx?ty=ONEBAR&kw={kw}",
)

# 已由官方開放資料（UpdateDate 20261113，鏡像 kong0107/mojLawSplitJSON）查得之 pcode，
# 供 seed 未填 ids.pcode 且開放資料下載失敗時直接定位 HTML 頁。
KNOWN_PCODES: dict[str, str] = {
    "個人資料保護法": "I0050021",
    "個人資料保護法施行細則": "I0050022",
    "資通安全管理法": "A0030297",
    "資通安全管理法施行細則": "A0030303",
    "資通安全事件通報應變及演練辦法": "A0030305",
    "資通安全情資分享辦法": "A0030307",
    "資通安全維護計畫實施情形稽核辦法": "A0030306",
    "數位發展部所管特定非公務機關資通安全管理作業辦法": "K0010161",
    "金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法": "G0380233",
}

INTERPRETATION_NOTE = (
    "全國法規資料庫未提供函釋入口（LawAll 頁籤僅有所有條文、編章節、條號查詢、條文檢索、"
    "授權子法、沿革、立法歷程；查證：https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0030297）"
)

_CN_NUM = "〇零一二三四五六七八九十百"
_ROC_CN_DATE = re.compile(rf"中華民國([{_CN_NUM}]+)年([{_CN_NUM}]+)月([{_CN_NUM}]+)日")
_CN_DATE = re.compile(rf"([{_CN_NUM}]+)年([{_CN_NUM}]+)月([{_CN_NUM}]+)日")
_PAGE_DATE = r"民國\s*\d+\s*年\s*\d+\s*月\s*\d+\s*日"


# ───────────────────────── 共用工具 ─────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def roc_label(y: int, m: int, d: int) -> str:
    """官方網頁顯示格式，例如「民國 114 年 11 月 11 日」（見 LawAll 頁「修正日期」）。"""
    return f"民國 {y} 年 {m:02d} 月 {d:02d} 日"


def ymd_to_roc(ymd: str) -> str:
    """'20251111' → '民國 114 年 11 月 11 日'；無效或 99991231 → ''。"""
    ymd = (ymd or "").strip()
    if not re.fullmatch(r"\d{8}", ymd) or ymd.startswith("9999"):
        return ""
    y, m, d = int(ymd[:4]) - 1911, int(ymd[4:6]), int(ymd[6:8])
    if y <= 0:
        return ""
    return roc_label(y, m, d)


def _cn_date_label(m: re.Match) -> str:
    y, mo, d = (cn_to_int(g) for g in m.groups())
    if None in (y, mo, d):
        return m.group(0)
    return roc_label(y, mo, d)


def _pcode_from_url(url: str) -> str:
    try:
        q = parse_qs(urlparse(url).query)
    except ValueError:
        return ""
    for k, vals in q.items():
        if k.lower() == "pcode" and vals:
            return vals[0].strip()
    return ""


def _base_doc(entry: SeedEntry, url: str) -> LawDocument:
    return LawDocument(
        name=entry.name,
        source_name=SOURCE_NAME,
        source_url=url,
        status=entry.status,
        doc_type=entry.doc_type,
        category=entry.category,
        parent_law=entry.parent_law,
        fetched_at=_now(),
    )


def _add_note(doc: LawDocument, note: str) -> None:
    if not note:
        return
    doc.fetch_note = f"{doc.fetch_note}；{note}" if doc.fetch_note else note


def _result_note(res: FetchResult) -> str:
    notes = []
    if res.note:
        notes.append(res.note)
    if res.wayback_timestamp and res.wayback_timestamp not in (res.note or ""):
        notes.append(f"Wayback 快照 {res.wayback_timestamp}")
    return "；".join(notes)


# ───────────────────────── 沿革解析 ─────────────────────────
def split_history(history: str) -> list[str]:
    """沿革原文 → 各編號項（保留原文換行，\\r\\n 轉 \\n）。"""
    text = (history or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"(?m)^(?=\d+\.)", text)
    return [p.rstrip("\n") for p in parts if re.match(r"\d+\.", p)]


def _join_lines(s: str) -> str:
    """把官方固定寬度斷行接回（不插入任何字元）。"""
    return re.sub(r"\n[ \t　]*", "", s)


def _sub_events(entry: str) -> list[str]:
    """一個沿革項可能含多個事件（公布、施行令、管轄變更公告），以「中華民國…日」切開。"""
    joined = re.sub(r"^\d+\.", "", _join_lines(entry))
    starts = [m.start() for m in _ROC_CN_DATE.finditer(joined)]
    if not starts:
        return [joined]
    # 只在「事件開頭」切：位置 0 或前面不是公告內文（以前一字元判斷不可靠，故以句型判斷）
    cuts = [0]
    for s in starts[1:]:
        tail = joined[s:]
        if re.match(rf"中華民國[{_CN_NUM}]+年[{_CN_NUM}]+月[{_CN_NUM}]+日\S*?(字第|號)", tail):
            cuts.append(s)
    cuts.append(len(joined))
    return [joined[a:b] for a, b in zip(cuts, cuts[1:]) if joined[a:b]]


def entry_date(entry: str) -> str:
    m = _ROC_CN_DATE.search(_join_lines(entry))
    return _cn_date_label(m) if m else ""


def _effective_from_main(main: str, act_date: str) -> str:
    """從公布／發布事件本文取施行規定（原文組出，不推測）。"""
    main = re.sub(r"（原名稱：.*?）", "", main)
    parts = [p for p in re.split(r"[；;]", main) if "施行" in p]
    if not parts:
        return ""
    clause = "；".join(parts).strip()
    clause = re.sub(r"^並", "", clause)
    clause = re.sub(r"^依第\s*[\d\-－]+\s*條規定[:：]", "", clause).strip()
    if re.fullmatch(r"(並)?自(公布|發布)日施行", clause):
        return act_date or clause
    m = re.fullmatch(rf"(?:並)?定?自([{_CN_NUM}]+年[{_CN_NUM}]+月[{_CN_NUM}]+日)施行", clause)
    if m:
        dm = _CN_DATE.search(m.group(1))
        return _cn_date_label(dm) if dm else clause
    # 「施行日期，由X定之」→「未定（由X定之）」；多段以「；」分隔
    clause = re.sub(r"施行日期[，,]\s*由(\S+?)定之", r"未定（由\1定之）", clause)
    clause = re.sub(r"[，,](?=\s*第)", "；", clause)
    return clause


def _effective_from_order(ev: str) -> str:
    """施行令事件，例如「…號令發布定自一百零八年一月一日施行」。"""
    m = re.search(r"(?:號令)?發布(.*施行)", ev)
    if not m:
        return ""
    body = m.group(1).strip()
    m2 = re.fullmatch(rf"定自([{_CN_NUM}]+年[{_CN_NUM}]+月[{_CN_NUM}]+日)施行", body)
    if m2:
        dm = _CN_DATE.search(m2.group(1))
        return _cn_date_label(dm) if dm else body
    return body


def effective_from_entry(entry: str) -> str:
    """單一沿革項的施行日（原文組出）；無資料回傳 ''。"""
    events = _sub_events(entry)
    if not events:
        return ""
    act_date = entry_date(events[0])
    eff = _effective_from_main(events[0], act_date)
    for ev in events[1:]:
        if "施行" in ev and re.search(r"號令\s*發布", ev):
            later = _effective_from_order(ev)
            if later:
                # 施行令取代「未定」部分；若原規定含已施行部分，照錄兩者
                if eff and "未定" in eff and "；" in eff:
                    eff = re.sub(r"未定（由\S+?定之）", later, eff)
                else:
                    eff = later
    return eff


def dates_from_history(history: str) -> tuple[str, str, str]:
    """回傳（公布日, 修正日, 施行日）；以最後一個沿革項為現行版本。"""
    entries = split_history(history)
    if not entries:
        return NA, NA, NA
    prom = entry_date(entries[0]) or NA
    amend = entry_date(entries[-1]) if len(entries) > 1 else ""
    eff = effective_from_entry(entries[-1])
    return prom, (amend or NA), (eff or NA)


# ───────────────────────── 開放資料解析 ─────────────────────────
def parse_open_data_zip(content: bytes) -> tuple[str, list[dict]]:
    """ZIP → (UpdateDate, Laws)。結構不符丟 ValueError。"""
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise ValueError(f"回應不是 ZIP（前導位元組 {content[:16]!r}）")
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        members = [n for n in z.namelist() if n.lower().endswith(".json")]
        if not members:
            raise ValueError(f"ZIP 內無 JSON 成員：{z.namelist()}")
        member = "ChLaw.json" if "ChLaw.json" in members else members[0]
        data = json.loads(z.read(member).decode("utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("Laws"), list):
        raise ValueError("JSON 頂層結構不符（需有 Laws 陣列）")
    return _s(data.get("UpdateDate")), [x for x in data["Laws"] if isinstance(x, dict)]


def articles_from_api(raw_articles: Iterable[dict]) -> list[Article]:
    out: list[Article] = []
    stack: list[tuple[int, str]] = []   # (層級, 原文標題)；層級 = 行首空白數 // 3
    for a in raw_articles or []:
        typ = _s(a.get("ArticleType"))
        content = _s(a.get("ArticleContent")).replace("\r\n", "\n").replace("\r", "\n")
        if typ == "C":
            stripped = content.lstrip(" 　")
            level = (len(content) - len(stripped)) // 3
            stack = [x for x in stack if x[0] < level] + [(level, stripped.strip())]
            continue
        if typ != "A":
            continue
        out.append(Article(no=_s(a.get("ArticleNo")).strip(), text=content,
                           chapter="／".join(t for _, t in stack)))
    return out


def doc_from_api_record(entry: SeedEntry, rec: dict, update_date: str) -> LawDocument:
    url = _s(rec.get("LawURL")) or entry.url
    doc = _base_doc(entry, url)
    doc.history = _s(rec.get("LawHistories")).replace("\r\n", "\n")
    prom, amend, eff = dates_from_history(rec.get("LawHistories", ""))
    n_entries = len(split_history(rec.get("LawHistories", "")))
    api_mod = ymd_to_roc(_s(rec.get("LawModifiedDate")))
    if n_entries > 1 and api_mod:
        amend = api_mod  # 官方「修正日期」（LawModifiedDate）
    eff_ymd = _s(rec.get("LawEffectiveDate")).strip()
    if eff == NA:
        if eff_ymd.startswith("9999"):
            eff = "未定"
        elif ymd_to_roc(eff_ymd):
            eff = ymd_to_roc(eff_ymd)
    doc.promulgated_date, doc.amended_date, doc.effective_date = prom, amend, eff
    doc.articles = articles_from_api(rec.get("LawArticles") or [])
    doc.version_label = amend if amend != NA else prom
    doc.metadata = {
        "pcode": _pcode_from_url(url),
        "法規位階": _s(rec.get("LawLevel")),
        "法規類別": _s(rec.get("LawCategory")).strip(),
        "最新異動日期": _s(rec.get("LawModifiedDate")),
        "生效日期": eff_ymd,
        "生效內容": _s(rec.get("LawEffectiveNote")).replace("\r\n", "\n"),
        "廢止註記": _s(rec.get("LawAbandonNote")),
        "前言": _s(rec.get("LawForeword")),
        "開放資料UpdateDate": update_date,
    }
    if _s(rec.get("LawAbandonNote")).strip():
        _add_note(doc, f"官方標示廢止註記：{_s(rec.get('LawAbandonNote')).strip()}")
    return doc


def find_record(entry: SeedEntry, laws: list[dict]) -> tuple[dict | None, str]:
    """以法規名稱精確比對；其次 seed pcode；再其次沿革中的「原名稱：」。回傳 (record, 比對說明)。"""
    for rec in laws:
        if _s(rec.get("LawName")).strip() == entry.name:
            return rec, ""
    pcode = (entry.ids or {}).get("pcode", "")
    if pcode:
        for rec in laws:
            if _pcode_from_url(_s(rec.get("LawURL"))) == pcode:
                return rec, f"名稱不一致，依 pcode {pcode} 比對到「{_s(rec.get('LawName'))}」"
    key = f"原名稱：{entry.name}；"
    for rec in laws:
        if key in _join_lines(_s(rec.get("LawHistories")).replace("\r\n", "\n")):
            return rec, f"seed 名稱為舊名，官方現名「{_s(rec.get('LawName'))}」"
    return None, ""


# ───────────────────────── HTML 解析（備援） ─────────────────────────
_ART_NO_RE = re.compile(r"^第\s*[\d０-９]+(?:\s*[-－]\s*[\d０-９]+)?\s*條$")
_CHAPTER_RE = re.compile(rf"^第\s*[{_CN_NUM}\d]+\s*[編章節款目](?:之[{_CN_NUM}\d]+)?\s*\S*")


def parse_law_html(html: str) -> dict:
    """解析 LawAll.aspx / LawOldVer.aspx。回傳 dict(name, amended, promulgated, articles, effective_note)。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    name = ""
    for sel in ("#hlLawName", "a#hlLawName", ".law-title", "#lblLawName"):
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            name = el.get_text(strip=True)
            break
    page_text = soup.get_text("\n")
    if not name:
        m = re.search(r"法規名稱\s*[:：]\s*\n*\s*(\S+)", page_text)
        if m:
            name = m.group(1).strip()
    amended = ""
    m = re.search(rf"修正日期\s*[:：]\s*({_PAGE_DATE})", page_text)
    if m:
        amended = re.sub(r"\s+", " ", m.group(1))
    prom = ""
    m = re.search(rf"(?:公發布日|發布日期|公布日期)\s*[:：]\s*({_PAGE_DATE})", page_text)
    if m:
        prom = re.sub(r"\s+", " ", m.group(1))
    eff_note = ""
    m = re.search(r"(※本法規部分或全部條文尚未生效[^\n]*)", page_text)
    if m:
        eff_note = m.group(1).strip()

    articles: list[Article] = []
    container = None
    for sel in (".law-reg-content", "#pnLawFla", ".law-content"):
        container = soup.select_one(sel)
        if container:
            break
    chapter_stack: list[str] = []

    def row_article(row) -> Article | None:
        no_el = row.select_one(".col-no") or row.select_one(".article-no")
        body = row.select_one(".law-article") or row.select_one(".col-data") or row.select_one(".article-content")
        if not no_el or not body:
            return None
        no = re.sub(r"\s+", " ", no_el.get_text(" ", strip=True)).strip()
        if not _ART_NO_RE.match(no):
            return None
        lines = [d.get_text("", strip=True) for d in body.find_all("div", recursive=False)]
        lines = [x for x in lines if x]
        text = "\n".join(lines) if lines else body.get_text("\n", strip=True)
        return Article(no=no, text=text, chapter="／".join(chapter_stack))

    if container is not None:
        for el in container.find_all(True, recursive=False):
            classes = el.get("class") or []
            if "row" in classes:
                art = row_article(el)
                if art:
                    articles.append(art)
                continue
            t = el.get_text(" ", strip=True)
            if t and _CHAPTER_RE.match(t) and len(t) < 80:
                depth = 0
                for c in classes:
                    mm = re.match(r"char-(\d+)", c)
                    if mm:
                        depth = max(int(mm.group(1)) - 2, 0)
                chapter_stack = chapter_stack[:depth] + [t]
    if not articles:
        for row in soup.select(".row"):
            art = row_article(row)
            if art:
                articles.append(art)
    return {"name": name, "amended": amended, "promulgated": prom,
            "articles": articles, "effective_note": eff_note}


def parse_history_html(html: str) -> str:
    """LawHistory.aspx：取「1.中華民國…」開始的沿革文字（原文）。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for sel in (".law-reg-content", ".text-pre", "#divHistory", "main", "body"):
        el = soup.select_one(sel)
        if el is None:
            continue
        txt = el.get_text("\n")
        m = re.search(r"(?m)^\s*1\.\s*中華民國", txt)
        if m:
            block = txt[m.start():]
            lines = [ln.rstrip() for ln in block.split("\n")]
            out = []
            for ln in lines:
                if not ln.strip():
                    continue
                if re.match(r"\s*\d+\.", ln) or "中華民國" in ln or out:
                    if re.match(r"\s*(友善列印|回上方|訂閱電子報)", ln):
                        break
                    out.append(ln.strip())
            return "\n".join(out)
    return ""


def _search_pcode(name: str, fetcher) -> tuple[str, str]:
    from bs4 import BeautifulSoup

    for tmpl in SEARCH_URL_CANDIDATES:
        url = tmpl.format(kw=quote(name))
        res = fetcher.get(url, expect="html")
        if not res.ok:
            continue
        soup = BeautifulSoup(res.text, "lxml")
        for a in soup.select("a[href]"):
            if a.get_text(strip=True) == name and "pcode=" in a["href"].lower():
                return _pcode_from_url(a["href"]), url
    return "", ""


def fetch_html_document(entry: SeedEntry, fetcher, pcode: str) -> LawDocument:
    url = LAWALL_URL.format(pcode=pcode)
    doc = _base_doc(entry, url)
    res = fetcher.get(url, expect="html")
    doc.fetch_method = res.method_used
    _add_note(doc, _result_note(res))
    if not res.ok:
        if not res.note:
            _add_note(doc, f"無法取得 {url}")
        return doc
    parsed = parse_law_html(res.text)
    if parsed["name"] and parsed["name"] != entry.name:
        _add_note(doc, f"頁面法規名稱為「{parsed['name']}」，與 seed 不同")
    doc.articles = parsed["articles"]
    if not doc.articles:
        _add_note(doc, "LawAll 頁面無法解析出條文（選擇器全部失敗）")
    hres = fetcher.get(LAWHISTORY_URL.format(pcode=pcode), expect="html")
    history = parse_history_html(hres.text) if hres.ok else ""
    if history:
        doc.history = history
        prom, amend, eff = dates_from_history(history)
    else:
        _add_note(doc, "沿革頁無法取得，日期取自 LawAll 表頭")
        prom, amend, eff = NA, NA, NA
    doc.promulgated_date = parsed["promulgated"] or prom
    if parsed["amended"] and amend != NA:
        doc.amended_date = parsed["amended"]
    else:
        doc.amended_date = amend
    if eff == NA and "最後生效日期：未定" in parsed["effective_note"]:
        eff = "未定"
    doc.effective_date = eff
    doc.version_label = doc.amended_date if doc.amended_date != NA else doc.promulgated_date
    doc.metadata = {"pcode": pcode}
    return doc


# ───────────────────────── 介面 ─────────────────────────
class _OpenData:
    """單次 fetch() 期間快取兩個 ZIP。"""

    def __init__(self, fetcher):
        self.fetcher = fetcher
        self._cache: dict[str, tuple[str, list[dict], str, str]] = {}
        self.errors: dict[str, str] = {}

    def load(self, kind: str) -> tuple[str, list[dict], str, str] | None:
        if kind in self._cache:
            return self._cache[kind]
        if kind in self.errors:
            return None
        urls = API_LAW_URLS if kind == "law" else API_ORDER_URLS
        errs = []
        for url in urls:
            res = self.fetcher.get(url, method_hint=OFFICIAL_API, expect="binary")
            if not res.ok:
                errs.append(f"{url}：{res.note or '下載失敗'}")
                continue
            try:
                upd, laws = parse_open_data_zip(res.content)
            except (ValueError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
                errs.append(f"{url}：{exc}")
                continue
            self._cache[kind] = (upd, laws, res.method_used, _result_note(res))
            return self._cache[kind]
        self.errors[kind] = "；".join(errs)
        return None


def _fetch_one(entry: SeedEntry, fetcher, od: _OpenData) -> LawDocument:
    for kind in ("law", "order"):
        data = od.load(kind)
        if not data:
            continue
        upd, laws, method, note = data
        rec, how = find_record(entry, laws)
        if rec is not None:
            doc = doc_from_api_record(entry, rec, upd)
            doc.fetch_method = method
            _add_note(doc, note)
            _add_note(doc, how)
            return doc
    # 開放資料不可得或查無 → HTML 備援
    pcode = (entry.ids or {}).get("pcode") or KNOWN_PCODES.get(entry.name, "")
    pre_note = ""
    if od.errors:
        pre_note = "官方開放資料無法取得（" + "；".join(f"{k}: {v}" for k, v in od.errors.items()) + "），改抓網頁"
    elif od._cache:
        pre_note = "官方開放資料中查無此法規名稱，改抓網頁"
    if not pcode:
        pcode, surl = _search_pcode(entry.name, fetcher)
        if not pcode:
            doc = _base_doc(entry, entry.url or BASE)
            _add_note(doc, pre_note)
            _add_note(doc, "seed 未提供 pcode，資料庫搜尋頁亦無法定位此法規名稱")
            return doc
        pre_note = f"{pre_note}；pcode 由搜尋頁 {surl} 定位" if pre_note else f"pcode 由搜尋頁 {surl} 定位"
    doc = fetch_html_document(entry, fetcher, pcode)
    if pre_note:
        doc.fetch_note = f"{pre_note}；{doc.fetch_note}" if doc.fetch_note else pre_note
    return doc


def fetch(entries: list[SeedEntry], fetcher) -> list[LawDocument]:
    od = _OpenData(fetcher)
    out: list[LawDocument] = []
    for entry in entries:
        try:
            doc = _fetch_one(entry, fetcher, od)
        except Exception as exc:  # 單筆失敗不影響整批
            doc = _base_doc(entry, entry.url or BASE)
            _add_note(doc, f"解析失敗：{type(exc).__name__}: {exc}")
        out.append(doc)
        if entry.include_interpretations:
            try:
                interps, note = fetch_interpretations(entry, doc, fetcher)
            except Exception as exc:
                interps, note = [], f"函釋處理失敗：{type(exc).__name__}: {exc}"
            _add_note(doc, note)
            out.extend(interps)
    return out


# ───────────────────────── 上一版：非官方鏡像（次要替代來源） ─────────────────────────
# 官方 LawOldVer／LawHistory 在 robots.txt 被 Disallow（主控／#6 查證）；#4 Fetcher 只套用
# Crawl-delay，GitHub Actions 上仍會先試官方頁。失敗時依序改用：
# 1. 中華民國人壽保險商業同業公會「保險相關法規查詢系統」
#    版本清單 https://law.lia-roc.org.tw/Law/HistoryList?lsid=<lsid>
#    （WebFetch 2026-11-27：表格欄位「序｜異動日期｜法規名稱」，法規名稱連結
#     HisContent?lsid=FL010627&date=YYYYMMDD；個資法最後一筆 112.05.31）
#    歷史條文 https://law.lia-roc.org.tw/Law/HisContent?lsid=<lsid>&date=YYYYMMDD
#    （WebFetch：表頭「法規名稱：」「修正日期：民國 112 年 05 月 31 日」；章名「第 一 章 總則」獨立成行；
#     條號「第 N 條」「第 1-1 條」；條文有排版斷行）
# 2. 臺北市法規查詢系統匯出 https://laws.gov.taipei/law/LawSearch/LawExport/<lsid>?type=0
#    （WebFetch：純文字，「法規名稱：」「修正日期：」、沿革、「第 N 條」各起一行；
#     只提供最新版，故僅在其修正日期早於現行版時（鏡像尚未更新）才可作為上一版）
# lsid 為上述兩站共用之法規編號（FL…），與全國法規資料庫 pcode 不同；只用已查證者。
LIA_HISTORY_URL = "https://law.lia-roc.org.tw/Law/HistoryList?lsid={lsid}"
TAIPEI_EXPORT_URL = "https://laws.gov.taipei/law/LawSearch/LawExport/{lsid}?type=0"
KNOWN_LSIDS: dict[str, str] = {
    # tests/fixtures/rules/pdpa_112.txt（#6）與 law.lia-roc.org.tw/Law/HistoryList?lsid=FL010627
    "個人資料保護法": "FL010627",
    # seed verified_by（laws.gov.taipei LawExport）
    "資通安全管理法": "FL088622",
    "資通安全管理法施行細則": "FL089964",
    "資通安全事件通報應變及演練辦法": "FL089967",
    # WebFetch https://laws.gov.taipei/law/LawSearch/LawArticleContent/FL010628 法規名稱「個人資料保護法施行細則」
    "個人資料保護法施行細則": "FL010628",
}
_MIRROR_STOP = re.compile(r"(回上方|回頁首|網站導覽|隱私權|資訊安全政策|版權所有|Copyright|瀏覽人次|地址[:：]|電話[:：])")
_LINE_ART = re.compile(r"^第\s*\d+(?:\s*-\s*\d+)?\s*條$")
_LINE_CHAPTER = re.compile(rf"^第\s*[{_CN_NUM}\d]+\s*[編章節](?:之[{_CN_NUM}\d]+)?(?:\s+\S.*)?$")
_ITEM_START = re.compile(rf"^(?:[{_CN_NUM}]+、|（[{_CN_NUM}]+）|\d+\.)")


def _roc_label_to_ymd(label: str) -> str:
    m = re.search(r"民國\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", label or "")
    if not m:
        return ""
    y, mo, d = (int(x) for x in m.groups())
    return f"{y + 1911:04d}{mo:02d}{d:02d}"


def _join_layout_lines(lines: list[str]) -> str:
    """鏡像站排版斷行接回：前行以「。」「：」結尾或本行為款目編號時才視為新項／款（啟發式）。"""
    out: list[str] = []
    for ln in lines:
        if out and not (out[-1].endswith(("。", "：", ":")) or _ITEM_START.match(ln)):
            out[-1] += ln
        else:
            out.append(ln)
    return "\n".join(out)


def parse_mirror_text(text: str) -> dict:
    """鏡像頁文字（HTML 轉文字或純文字匯出）→ dict(name, amended, articles)。"""
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    name = amended = ""
    for i, ln in enumerate(lines):
        m = re.match(r"法規名稱\s*[:：]\s*(\S*)", ln)
        if m and not name:
            name = m.group(1) or (lines[i + 1] if i + 1 < len(lines) else "")
        m = re.search(rf"修正日期\s*[:：]\s*({_PAGE_DATE})?", ln)
        if m and not amended:
            cand = m.group(1) or (lines[i + 1] if i + 1 < len(lines) else "")
            mm = re.search(_PAGE_DATE, cand)
            amended = re.sub(r"\s+", " ", mm.group(0)) if mm else ""
    articles: list[Article] = []
    chapter: list[str] = []
    cur_no, buf = "", []

    def flush():
        if cur_no:
            articles.append(Article(no=cur_no, text=_join_layout_lines(buf), chapter="／".join(chapter)))

    for ln in lines:
        if _MIRROR_STOP.search(ln) and cur_no:
            break
        if _LINE_ART.match(ln):
            flush()
            cur_no, buf = re.sub(r"\s+", " ", ln), []
            continue
        if _LINE_CHAPTER.match(ln) and len(ln) < 80:
            flush()
            cur_no, buf = "", []
            head = re.match(rf"第\s*[{_CN_NUM}\d]+\s*([編章節])", ln)
            if head and head.group(1) == "節" and chapter:
                chapter = chapter[:1] + [ln]
            else:
                chapter = [ln]
            continue
        if cur_no:
            buf.append(ln)
    flush()
    return {"name": name, "amended": amended, "articles": articles}


def _html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "nav", "header", "footer"]):
        t.decompose()
    return soup.get_text("\n")


def _lia_versions(html: str, base: str) -> list[tuple[str, str]]:
    """HistoryList → [(YYYYMMDD, HisContent 絕對網址)]。"""
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select("a[href]"):
        href = a["href"]
        if "hiscontent" not in href.lower():
            continue
        m = re.search(r"[?&]date=(\d{8})", href)
        if m:
            out.append((m.group(1), urljoin(base, href)))
    return out


def _previous_from_mirrors(entry: SeedEntry, current: LawDocument, fetcher,
                           entries: list[str]) -> tuple[LawDocument | None, list[str]]:
    notes: list[str] = []
    lsid = (entry.ids or {}).get("lsid") or KNOWN_LSIDS.get(entry.name) or KNOWN_LSIDS.get(current.name, "")
    if not lsid:
        return None, ["無已查證之鏡像站法規編號（lsid），未使用非官方鏡像"]
    cur_ymd = _roc_label_to_ymd(current.amended_date) or _roc_label_to_ymd(current.promulgated_date)

    candidates: list[tuple[str, str, str, str, str]] = []   # (網站名, 網址, 文字, 方法, 擷取備註)
    lurl = LIA_HISTORY_URL.format(lsid=lsid)
    lres = fetcher.get(lurl, expect="html")
    if lres.ok:
        vers = [v for v in _lia_versions(lres.text, lres.final_url or lurl) if not cur_ymd or v[0] < cur_ymd]
        if vers:
            ymd, hurl = max(vers)
            hres = fetcher.get(hurl, expect="html")
            if hres.ok:
                candidates.append(("中華民國人壽保險商業同業公會 保險相關法規查詢系統", hurl,
                                   _html_to_text(hres.text), hres.method_used, _result_note(hres)))
            else:
                notes.append(f"{hurl}：{hres.note or '取得失敗'}")
        else:
            notes.append(f"{lurl} 無早於現行版之版本")
    else:
        notes.append(f"{lurl}：{lres.note or '取得失敗'}")
    turl = TAIPEI_EXPORT_URL.format(lsid=lsid)
    if not candidates:
        tres = fetcher.get(turl, expect="html")
        if tres.ok:
            text = tres.text if "<html" not in tres.text[:500].lower() else _html_to_text(tres.text)
            candidates.append(("臺北市法規查詢系統", turl, text, tres.method_used, _result_note(tres)))
        else:
            notes.append(f"{turl}：{tres.note or '取得失敗'}")

    for site, url, text, method, rnote in candidates:
        parsed = parse_mirror_text(text)
        if not parsed["articles"]:
            notes.append(f"{url} 無條文可解析")
            continue
        p_ymd = _roc_label_to_ymd(parsed["amended"])
        if not p_ymd or (cur_ymd and p_ymd >= cur_ymd):
            notes.append(f"{url} 修正日期（{parsed['amended'] or '無'}）未早於現行版，不作為上一版")
            continue
        if parsed["name"] and parsed["name"] != current.name and parsed["name"] != entry.name:
            notes.append(f"{url} 法規名稱為「{parsed['name']}」")
        prev = _base_doc(entry, url)
        prev.articles = parsed["articles"]
        prev.fetch_method = method
        _add_note(prev, f"基準版本取自非官方鏡像：{site}")
        _add_note(prev, "鏡像頁排版斷行以啟發式接回（句末「。」「：」或款目編號才分行）")
        _add_note(prev, rnote)
        prev.promulgated_date = current.promulgated_date
        prev.amended_date = parsed["amended"]
        # 沿革對齊：取修正日期相同之沿革項為上一版
        idx = next((i for i, e in enumerate(entries) if entry_date(e) == parsed["amended"]), None)
        if idx is not None:
            prev.history = "\n".join(entries[: idx + 1])
            prev.effective_date = effective_from_entry(entries[idx]) or NA
            if idx == 0:
                prev.amended_date = NA
        else:
            prev.effective_date = NA
        prev.version_label = parsed["amended"]
        prev.metadata = {"lsid": lsid, "鏡像來源": site}
        return prev, notes
    return None, notes


def fetch_previous(entry: SeedEntry, current: LawDocument, fetcher) -> LawDocument | None:
    """上一版全文：先官方「歷史法規」LawOldVer.aspx（不帶 lnndate 即上一版），失敗再用非官方鏡像。"""
    official_note = ""
    try:
        prev = _fetch_previous_official(entry, current, fetcher)
    except _NoPrevious as exc:
        _add_note(current, str(exc))
        return None
    except _OfficialFailed as exc:
        official_note = str(exc)
        prev = None
    if prev is not None:
        return prev
    entries = split_history(current.history)
    try:
        prev, notes = _previous_from_mirrors(entry, current, fetcher, entries)
    except Exception as exc:
        prev, notes = None, [f"鏡像來源處理失敗：{type(exc).__name__}: {exc}"]
    if prev is not None:
        _add_note(prev, f"官方歷史法規頁失敗：{official_note}" if official_note else "")
        _add_note(current, f"上一版取自非官方鏡像（{prev.source_url}）")
        return prev
    _add_note(current, official_note)
    _add_note(current, "上一版替代來源亦失敗：" + "；".join(notes))
    return None


class _NoPrevious(Exception):
    """確定沒有上一版（不必再試鏡像）。"""


class _OfficialFailed(Exception):
    """官方歷史頁失敗（可改試鏡像）。"""


def _fetch_previous_official(entry: SeedEntry, current: LawDocument, fetcher) -> LawDocument | None:
    pcode = (current.metadata or {}).get("pcode") or (entry.ids or {}).get("pcode") \
        or KNOWN_PCODES.get(entry.name, "") or _pcode_from_url(current.source_url)
    entries = split_history(current.history)
    if current.history and len(entries) < 2:
        raise _NoPrevious("沿革僅一個版本，無上一版")
    if not pcode:
        raise _OfficialFailed("無 pcode，無法抓取官方歷史法規頁")
    url = LAWOLDVER_URL.format(pcode=pcode)
    try:
        res = fetcher.get(url, expect="html")
        if not res.ok:
            raise _OfficialFailed(f"上一版（歷史法規 {url}）無法取得：{res.note or '下載失敗'}")
        parsed = parse_law_html(res.text)
    except _OfficialFailed:
        raise
    except Exception as exc:
        raise _OfficialFailed(f"上一版解析失敗：{type(exc).__name__}: {exc}") from exc
    if not parsed["articles"]:
        raise _OfficialFailed(f"上一版（{url}）無條文可解析")
    if parsed["amended"] and parsed["amended"] == current.amended_date:
        raise _OfficialFailed(f"歷史法規頁修正日期與現行版相同（{parsed['amended']}），不作為上一版")
    return _prev_from_official(entry, current, url, res, parsed, entries, pcode)


def _prev_from_official(entry, current, url, res, parsed, entries, pcode) -> LawDocument:
    prev = _base_doc(entry, url)
    prev.articles = parsed["articles"]
    prev.fetch_method = res.method_used
    _add_note(prev, _result_note(res))
    prev.promulgated_date = current.promulgated_date
    if len(entries) >= 2:
        prev_hist = "\n".join(entries[:-1])
        prev.history = prev_hist
        _, amend, eff = dates_from_history(prev_hist)
        prev.amended_date = parsed["amended"] or amend
        if len(entries) == 2:
            prev.amended_date = NA  # 上一版即制定／訂定版
        prev.effective_date = eff
    else:
        prev.amended_date = parsed["amended"] or NA
        prev.effective_date = NA
    prev.version_label = parsed["amended"] or prev.amended_date
    prev.metadata = {"pcode": pcode}
    return prev



# ───────────────────────── 函釋（include_interpretations=True） ─────────────────────────
# 首選：主管機關官方網站函釋專區。2026-11-27 查證結果：
#   - 個資會籌備處主管法規查詢系統 https://law.pdpc.gov.tw/ 選單僅有最新訊息、法規體系、法規檢索、
#     草案預告、相關網站（WebFetch），無函釋／解釋函入口；www.pdpc.gov.tw 為 JS 渲染，WebFetch 只得到空殼，
#     WebSearch 亦查無函釋列表頁網址。
#   - 數位發展部資通安全署 moda.gov.tw/ACS 法規頁 WebFetch 失敗（client_error），WebSearch 只得到附件檔，
#     查無「法規解釋／函釋」列表頁網址。
#   → 不猜網址：OFFICIAL_INTERPRETATION_PAGES 為空，日後查證後可填入（解析沿用寬鬆的連結規則）。
# 次選：臺北市法規查詢系統（轉載中央法規相關解釋令函），WebFetch 查證：
#   - 法規條文頁 https://laws.gov.taipei/Law/LawSearch/LawArticleContent/<lsid>：有函釋的條文旁有連結
#     LawRelateInterpretation/<lsid>?no=<條號>（例：no=2、no=51-1；無函釋之條文不提供此連結）
#   - 條文函釋列表 https://laws.gov.taipei/Law/LawSearch/LawRelateInterpretation/<lsid>?no=<條號>
#     標題「中央法規相關解釋令函」，編號清單，每則連結 /Law/Interpretation/Content/FE…，
#     連結文字如「個人資料保護委員會籌備處 115.10.15 個資籌法字第1150060402號函」，已廢止者標「(廢)」
#   - 函釋內容 https://laws.gov.taipei/Law/Interpretation/Content/FE…：欄位「發文字號：」「發文日期：
#     民國 115 年 10 月 15 日」「要　　旨：」，其後為主旨、說明全文
#     （另存 PDF：/Law/Interpretation/ContentExport/FE…?type=5，WebFetch 被 robots 拒絕，未採用）
OFFICIAL_INTERPRETATION_PAGES: dict[str, tuple[str, ...]] = {}
TAIPEI_ARTICLE_URL = "https://laws.gov.taipei/Law/LawSearch/LawArticleContent/{lsid}"
TAIPEI_INTERP_LIST_URL = "https://laws.gov.taipei/Law/LawSearch/LawRelateInterpretation/{lsid}?no={no}"
INTERPRETATION_YEARS = 3          # 只收最近 N 年（依發文日期）
MAX_INTERPRETATIONS = 150         # 每部法規上限（避免請求量失控）
TAIPEI_NOTE = "函釋取自臺北市法規查詢系統（轉載中央函釋）"
_TITLE_DATE = re.compile(r"(\d{2,3})\.(\d{1,2})\.(\d{1,2})")
_INTERP_STOP = re.compile(r"^(回上頁|回頁首|回上方|友善列印|另存PDF|網站導覽|隱私權|資訊安全政策|"
                          r"臺北市政府法務局|瀏覽人次|更新日期)")


def _today():
    from datetime import date

    return date.today()


def _cutoff_roc() -> tuple[int, int, int]:
    t = _today()
    y = t.year - INTERPRETATION_YEARS
    d = min(t.day, 28) if t.month == 2 else t.day
    return (y - 1911, t.month, d)


def _title_date(title: str) -> tuple[int, int, int] | None:
    m = _TITLE_DATE.search(title or "")
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def parse_taipei_article_links(html: str, base: str) -> list[str]:
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    out: list[str] = []
    for a in soup.select("a[href]"):
        if "lawrelateinterpretation" in a["href"].lower():
            u = urljoin(base, a["href"])
            if u not in out:
                out.append(u)
    return out


def parse_taipei_interp_list(html: str, base: str) -> list[dict]:
    """列表頁 → [{title, url, fe, abolished}]（頁面順序）。"""
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select("a[href]"):
        href = a["href"]
        m = re.search(r"/Interpretation/Content/(FE\w+)", href, re.I)
        if not m:
            continue
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        if not title:
            continue
        holder = a.find_parent("li") or a.parent
        ctx = holder.get_text(" ", strip=True) if holder is not None else title
        out.append({"title": title, "url": urljoin(base, href), "fe": m.group(1),
                    "abolished": "(廢)" in ctx or "（廢）" in ctx})
    return out


def parse_taipei_interp_content(html: str) -> dict:
    """內容頁 → {doc_no, date, gist, text}；text 為「要旨」起至內文結束之原文（逐行）。"""
    text = _html_to_text(html)
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    lines = [ln for ln in lines if ln]

    def value_after(label_re: str) -> tuple[str, int]:
        for i, ln in enumerate(lines):
            m = re.match(label_re + r"\s*[:：]\s*(.*)$", ln)
            if m:
                v = m.group(1).strip()
                if not v and i + 1 < len(lines):
                    return lines[i + 1], i + 1
                return v, i
        return "", -1

    doc_no, _ = value_after(r"發文字號")
    date, _ = value_after(r"發文日期")
    gist, gi = value_after(r"要\s*旨")
    start = -1
    for i, ln in enumerate(lines):
        if re.match(r"要\s*旨\s*[:：]", ln):
            start = i
            break
    if start < 0:
        for i, ln in enumerate(lines):
            if re.match(r"主\s*旨\s*[:：]", ln):
                start = i
                break
    body: list[str] = []
    if start >= 0:
        for ln in lines[start:]:
            if _INTERP_STOP.match(ln):
                break
            if ln in ("相關法條",):
                continue
            if body and re.fullmatch(r"\S{1,6}\s*[:：]", body[-1].replace("\u3000", "")) and \
                    not re.match(r"[一二三四五六七八九十]+、", ln):
                body[-1] += ln          # 「要　　旨：」與其值分屬兩格時接回同一行
                continue
            body.append(ln)
    return {"doc_no": doc_no, "date": re.sub(r"\s+", " ", date), "gist": gist, "text": "\n".join(body)}


def fetch_interpretations(entry: SeedEntry, law_doc: LawDocument, fetcher) -> tuple[list[LawDocument], str]:
    """回傳（函釋文件列表, 給主文件 fetch_note 的說明）。"""
    notes = [INTERPRETATION_NOTE]
    if not OFFICIAL_INTERPRETATION_PAGES.get(entry.name):
        notes.append("主管機關網站函釋專區：查無已查證之列表頁網址（個資會籌備處、資通安全署），未使用")
    lsid = (entry.ids or {}).get("lsid") or KNOWN_LSIDS.get(entry.name) or KNOWN_LSIDS.get(law_doc.name, "")
    if not lsid:
        notes.append("無臺北市法規查詢系統法規編號（lsid），未取得函釋")
        return [], "；".join(notes)

    aurl = TAIPEI_ARTICLE_URL.format(lsid=lsid)
    ares = fetcher.get(aurl, expect="html")
    if ares.ok:
        list_urls = parse_taipei_article_links(ares.text, ares.final_url or aurl)
    else:
        notes.append(f"{aurl}：{ares.note or '取得失敗'}，改依條號逐條查詢")
        list_urls = [TAIPEI_INTERP_LIST_URL.format(lsid=lsid, no=article_key(a.no)) for a in law_doc.articles
                     if re.fullmatch(r"\d+(-\d+)?", article_key(a.no))]

    cutoff = _cutoff_roc()
    found: dict[str, dict] = {}
    failed = 0
    for lu in list_urls:
        lres = fetcher.get(lu, expect="html")
        if not lres.ok:
            failed += 1
            continue
        m = re.search(r"[?&]no=([\w\-]+)", lu)
        art = f"第 {m.group(1)} 條" if m else ""
        for it in parse_taipei_interp_list(lres.text, lres.final_url or lu):
            d = _title_date(it["title"])
            if d is None or d < cutoff:
                continue
            rec = found.setdefault(it["fe"], {**it, "date": d, "articles": []})
            if art and art not in rec["articles"]:
                rec["articles"].append(art)
    if failed:
        notes.append(f"{failed} 個條文函釋列表頁取得失敗")
    recs = sorted(found.values(), key=lambda r: r["date"], reverse=True)
    if len(recs) > MAX_INTERPRETATIONS:
        notes.append(f"最近 {INTERPRETATION_YEARS} 年函釋 {len(recs)} 則，僅取最新 {MAX_INTERPRETATIONS} 則")
        recs = recs[:MAX_INTERPRETATIONS]

    docs: list[LawDocument] = []
    for r in recs:
        d = LawDocument(name=r["title"], source_name=SOURCE_NAME, source_url=r["url"], status=entry.status,
                        doc_type="函釋", category=entry.category, parent_law=entry.name, fetched_at=_now())
        d.promulgated_date = "{}.{:02d}.{:02d}".format(*r["date"])
        d.version_label = d.promulgated_date
        d.metadata = {"臺北市法規查詢系統編號": r["fe"], "相關條文": "、".join(r["articles"]), "法規編號": lsid}
        _add_note(d, TAIPEI_NOTE)
        if r["abolished"]:
            _add_note(d, "臺北市法規查詢系統標示「(廢)」")
        cres = fetcher.get(r["url"], expect="html")
        d.fetch_method = cres.method_used
        _add_note(d, _result_note(cres))
        if not cres.ok:
            _add_note(d, f"函釋內容頁無法取得：{cres.note or '取得失敗'}")
            docs.append(d)
            continue
        c = parse_taipei_interp_content(cres.text)
        if c["date"]:
            d.promulgated_date = c["date"]          # 發文日期原文
        if c["gist"]:
            d.metadata["要旨"] = c["gist"]
        if c["doc_no"] and c["doc_no"] != r["title"]:
            d.metadata["發文字號"] = c["doc_no"]
        if c["text"]:
            d.articles = [Article(no="函釋全文", text=c["text"])]
        else:
            _add_note(d, "函釋內容頁無法解析出全文")
        docs.append(d)
    notes.append(f"{TAIPEI_NOTE}：最近 {INTERPRETATION_YEARS} 年 {len(docs)} 則（{aurl}）")
    return docs, "；".join(notes)
