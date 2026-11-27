"""#2 金融監督管理委員會主管法規查詢系統（含函令／函釋）。

系統：「金融監督管理委員會主管法規共用系統」https://law.fsc.gov.tw
（ASP.NET 網頁；查無官方 Open API／開放資料可直接取得本系統法規與函令全文，故以網頁擷取。
  查證：https://www.fsc.gov.tw/ch/home.jsp?id=128 僅列網頁資料庫連結，無資料集下載；
  data.gov.tw 於查證時 robots.txt 無法讀取，未能確認有無對應資料集。
  註：法務部全國法規資料庫 Open API 亦收錄金管會「法規命令」，但不含本系統的函令（FE/GL 編號），
  該 API 由 #1 moj.py 實作，本模組不重複。）

以下 URL 樣式皆於 2026-11-27 以 WebFetch 實際開啟查證：
  - 法規內容     https://law.fsc.gov.tw/LawContent.aspx?id=FL007032
                 https://law.fsc.gov.tw/LawContent.aspx?id=GL000933
  - 法規沿革     https://law.fsc.gov.tw/LawContentSource.aspx?id=GL000933
  - 歷史法規清單 https://law.fsc.gov.tw/LawContentHistoryList.aspx?id=FL007032
  - 歷史法規全文 https://law.fsc.gov.tw/LawContentHistory.aspx?hid=1299&id=FL007032
  - 法規體系清單 https://law.fsc.gov.tw/LawCategoryContentList.aspx?CategoryID=C001&size=40&page=2
                 （size 僅接受 10/20/30/40；size=50、100 會回「顯示列數錯誤，請重新選擇」）
  - 函令（行政規則）亦以 LawContent.aspx?id=GLxxxxxx / FExxxxxx 呈現，例：
                 https://law.fsc.gov.tw/LawContent.aspx?id=GL003330
頁面標頭欄位（查證所見）：「法規名稱：」「公發布日：」「修正日期：」「發文字號：」「法規體系：」
「立法理由：」「圖表附件：」；歷史版本頁為「歷史法規：」。條號顯示為「第 1 條」「第 10-1 條」。

HTML 結構限制：WebFetch 只回傳轉換後文字，無法看到 class 名稱，因此本模組先試多組候選選擇器
（CONTENT_SELECTORS，皆為未查證之候選），全部不中時退回「整頁文字 + 條號行切分」解析；
都解析不出內容時回傳空 articles 並在 fetch_note 說明。
"""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup

from ..models import ANTI_BOT_FAIL_NOTE, NA, STATUS_CURRENT, Article, FetchResult, LawDocument, SeedEntry
from ..normalize import norm_text

SOURCE_NAME = "金融監督管理委員會主管法規查詢系統"
BASE = "https://law.fsc.gov.tw"
LAW_URL = BASE + "/LawContent.aspx?id={id}"
LAW_SOURCE_URL = BASE + "/LawContentSource.aspx?id={id}"
HISTORY_LIST_URL = BASE + "/LawContentHistoryList.aspx?id={id}"
CATEGORY_LIST_URL = BASE + "/LawCategoryContentList.aspx?CategoryID={cat}&size=40&page={page}"

TPE = timezone(timedelta(hours=8))

# 已查證的法規 ID（seed 既有條目未填 ids，由此對應；新增條目請在 seed 填 ids: {fsc_id: ...}）
KNOWN_IDS: dict[str, str] = {
    # https://law.fsc.gov.tw/LawCategoryContentList.aspx?CategoryID=C001&size=40&page=2
    "公開發行公司建立內部控制制度處理準則": "FL021141",
    # https://law.fsc.gov.tw/LawCategoryContentList.aspx?CategoryID=C003&size=40&page=1
    "證券暨期貨市場各服務事業建立內部控制制度處理準則": "FL022569",
    # https://law.fsc.gov.tw/LawCategoryContentList.aspx?CategoryID=A103&size=40&page=1
    "金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法": "GL000933",
    # https://law.fsc.gov.tw/LawCategoryContentList.aspx?CategoryID=B017&size=40&page=1
    "金融控股公司及銀行業內部控制及稽核制度實施辦法": "FL049894",
    # https://law.fsc.gov.tw/LawCategoryContentList.aspx?CategoryID=A104&size=40&page=1
    "金融監督管理委員會所管特定非公務機關資通安全管理作業辦法": "GL002646",
    "金融業導入零信任架構參考指引": "GL003923",
    "金融業後量子密碼遷移參考指引": "GL004329",
}

# 函令篩選關鍵字（標題或全文命中任一即收錄；可調整）
INTERP_KEYWORDS: tuple[str, ...] = ("資通安全", "資訊安全", "個人資料", "資安")

# 函令掃描的法規體系分類（LawCategoryMain.aspx?type=M&CategoryID=A000/B000/C000 查證所列）
DEFAULT_INTERP_CATEGORIES: tuple[str, ...] = (
    "C001", "C002", "C003", "C004", "C005", "C006",   # 證券期貨局
    "A103", "A104",                                    # 本會 法律事務處、資訊服務處
    "B010", "B017",                                    # 銀行局 金融控股公司、其他
)
INTERP_CATEGORIES: dict[str, tuple[str, ...]] = {
    "公開發行公司建立內部控制制度處理準則": ("C001", "C002", "C003", "C006"),
    "證券暨期貨市場各服務事業建立內部控制制度處理準則": ("C001", "C002", "C003", "C004", "C005", "C006"),
    "金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法": ("A103", "A104", "B017", "C003", "C006"),
    "金融控股公司及銀行業內部控制及稽核制度實施辦法": ("B010", "B017"),
}
# 函令標題中常見的法規簡稱（原文用語），用於判斷「與該法規相關」
LAW_ALIASES: dict[str, tuple[str, ...]] = {
    "金融控股公司及銀行業內部控制及稽核制度實施辦法": ("內控辦法",),
    "金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法": ("個人資料檔案安全維護辦法",),
}
MAX_LIST_PAGES = 20           # 每個分類最多翻頁數（每頁 40 筆）
MAX_INTERP_CANDIDATES = 60    # 每部法規最多開啟的候選函令數

# 未查證的候選內容區選擇器（依序嘗試；都不中則用整頁文字）
CONTENT_SELECTORS: tuple[str, ...] = (
    "div.law-reg-content", "div#lawContent", "div.law-content", "div.LawContent",
    "div#pnLawFla", "div.text-pre", "div#content", "main", "div#AC",
)

HEADER_LABELS: tuple[str, ...] = (
    "法規名稱", "公發布日", "修正日期", "發文字號", "法規體系", "立法理由", "圖表附件",
    "施行日期", "生效日期", "生效狀態", "歷史法規", "法規類別", "有效狀態", "廢止日期",
)
# 頁尾／導覽文字：遇到即停止收集內文
STOP_MARKERS: tuple[str, ...] = (
    "回上方", "政府網站資料開放宣告", "隱私權保護宣告", "資訊安全政策", "瀏覽人次",
    "金管會電子地圖", "金融監督管理委員會 版權所有", "版權所有",
)
NAV_LINES: frozenset[str] = frozenset({
    "法規內容", "條文檢索", "法規沿革", "歷史法規", "友善列印", "英", "English", ":::",
    "跳到主要內容區塊", "網站導覽", "使用手冊", "回首頁", "最新訊息", "法規體系", "法規檢索",
    "草案預告", "相關網站", "所有條文", "編章節", "條號查詢", "相關資訊",
})

ART_HEAD_RE = re.compile(
    r"^(第\s*[0-9０-９]+(?:\s*[-－]\s*[0-9０-９]+)?\s*[條点點](?:\s*之\s*[0-9０-９一二三四五六七八九十]+)?)\s*(.*)$")
CHAPTER_RE = re.compile(r"^第\s*[0-9０-９一二三四五六七八九十百]+\s*[編章節](?:\s|　|$)")
ROC_DATE_RE = re.compile(r"民國\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
DOT_DATE_RE = re.compile(r"(?<!\d)(\d{2,3})\.(\d{2})\.(\d{2})(?!\d)")
EFFECT_CLAUSE_RE = re.compile(r"(自[^，。；,;]{0,40}?(?:施行|實施|生效))")
ATTACH_LINE_RE = re.compile(r"\.(pdf|odt|ods|doc|docx|xls|xlsx|zip)\s*$", re.I)


# ───────────────────────── 共用文字工具（twse/tpex 亦使用） ─────────────────────────
def now_iso() -> str:
    return datetime.now(TPE).isoformat(timespec="seconds")


def soup_of(html: str | bytes) -> BeautifulSoup:
    s = BeautifulSoup(html, "lxml")
    for t in s(["script", "style", "noscript", "template"]):
        t.decompose()
    return s


def text_lines(node) -> list[str]:
    """節點文字逐行（去頭尾空白、去空行）。保留行內原文（含全形空白）。"""
    raw = node.get_text("\n")
    out = []
    for ln in raw.splitlines():
        s = ln.strip().strip("　").strip()
        if s:
            out.append(s)
    return out


def _label_key(s: str) -> str:
    return re.sub(r"[\s　:：]", "", unicodedata.normalize("NFKC", s))


def header_fields(lines: list[str], labels: Iterable[str]) -> dict[str, str]:
    """在逐行文字中找「標籤：值」。值可在同一行或下一行（表格兩欄）。只取第一次出現。"""
    labels = list(labels)
    pats = {lab: _label_re(lab) for lab in labels}
    found: dict[str, str] = {}
    for i, ln in enumerate(lines):
        for lab, pat in pats.items():
            if lab in found:
                continue
            m = pat.match(ln)
            if not m:
                continue
            val = m.group(1).strip()
            j = i + 1
            if not val and j < len(lines) and not _is_label_line(lines[j], pats.values()):
                val = lines[j].strip()
                j += 1
            # 值可能被拆成多行（例如「民國 99 年…」＋「※本次發布之條文…」）
            extra = []
            while j < len(lines) and lines[j].startswith("※"):
                extra.append(lines[j])
                j += 1
            if extra:
                val = (val + " " + " ".join(extra)).strip()
            found[lab] = val
            break
    return found


def _label_re(label: str) -> re.Pattern:
    k = _label_key(label)
    return re.compile(r"^[\s　]*" + r"[\s　]*".join(map(re.escape, k)) + r"[\s　]*[:：][\s　]*(.*)$")


def _is_label_line(ln: str, pats: Iterable[re.Pattern]) -> bool:
    return any(p.match(ln) for p in pats)


def split_date_note(val: str) -> tuple[str, str]:
    """「民國 115 年 05 月 06 日 ※本次發布之條文全部或部分尚未施行（實施）」→ (日期原文, 註記原文)。"""
    if not val:
        return NA, ""
    m = ROC_DATE_RE.search(val)
    if not m:
        return val.strip() or NA, ""
    note = val[m.end():].strip()
    return m.group(0), note


def roc_to_ymd(s: str) -> str:
    """「民國 115 年 09 月 30 日」或「115.09.30」→ "20260930"（僅供比較，不輸出）。無法辨識回傳 ""。"""
    m = ROC_DATE_RE.search(s or "") or DOT_DATE_RE.search(s or "")
    if not m:
        return ""
    y, mo, d = (int(x) for x in m.groups())
    return f"{y + 1911:04d}{mo:02d}{d:02d}"


def stop_index(lines: list[str]) -> int:
    for i, ln in enumerate(lines):
        if any(ln.startswith(s) for s in STOP_MARKERS):
            return i
    return len(lines)


def split_articles(lines: list[str]) -> list[Article]:
    """依「第 N 條」行切分條文。條號行可單獨一行，或與內文同一行（「第 1 條 本辦法…」）。
    編章節行記入 chapter；項以 \\n 分隔（逐行原文）。"""
    arts: list[Article] = []
    chapter = ""
    cur: Optional[Article] = None
    buf: list[str] = []

    def flush():
        nonlocal cur, buf
        if cur is not None:
            cur.text = "\n".join(buf).strip()
            arts.append(cur)
        cur, buf = None, []

    for ln in lines:
        if ln in NAV_LINES:
            continue
        if CHAPTER_RE.match(ln) and len(ln) <= 40:
            flush()
            chapter = ln
            continue
        m = ART_HEAD_RE.match(ln)
        if m:
            flush()
            cur = Article(no=m.group(1).strip(), text="", chapter=chapter)
            rest = m.group(2).strip()
            if rest:
                buf.append(rest)
            continue
        if cur is not None:
            buf.append(ln)
    flush()
    return arts


def effect_clause(text: str) -> str:
    m = EFFECT_CLAUSE_RE.search(text or "")
    return m.group(1) if m else ""


def is_ok(res: Any) -> bool:
    return bool(res is not None and getattr(res, "ok", False) and getattr(res, "content", b""))


def fail_note(res: Any, what: str) -> str:
    """5 種方法全失敗時回傳值必須「完全等於」ANTI_BOT_FAIL_NOTE（SPEC §0.3），不加任何前綴。"""
    note = getattr(res, "note", "") or "未知錯誤"
    if note == ANTI_BOT_FAIL_NOTE:
        return ANTI_BOT_FAIL_NOTE
    return f"{what}擷取失敗：{note}"


def extract_attachment_text(content: bytes, filename: str) -> str:
    """解析 DOCX / PDF 附件文字（python-docx / pdfplumber；未安裝則回傳 ""）。"""
    fn = filename.lower()
    try:
        if fn.endswith(".docx") or content[:2] == b"PK":
            import docx  # python-docx
            d = docx.Document(io.BytesIO(content))
            return "\n".join(p.text.strip() for p in d.paragraphs if p.text.strip())
        if fn.endswith(".pdf") or content[:5] == b"%PDF-":
            import pdfplumber
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                return "\n".join((pg.extract_text() or "") for pg in pdf.pages).strip()
    except Exception:  # noqa: BLE001 — 附件解析失敗不可中斷整批
        return ""
    return ""


# ───────────────────────── 頁面解析 ─────────────────────────
@dataclass
class ParsedPage:
    fields: dict
    articles: list[Article]
    body: str
    attachments: list[tuple[str, str]]   # (檔名, 絕對 URL)


def _content_root(soup: BeautifulSoup):
    for sel in CONTENT_SELECTORS:
        node = soup.select_one(sel)
        if node is not None and ("法規名稱" in node.get_text() or "名稱" in node.get_text()):
            return node
    return soup.body or soup


def parse_law_page(html: str | bytes, base_url: str = BASE) -> ParsedPage:
    soup = soup_of(html)
    root = _content_root(soup)
    lines = text_lines(root)
    lines = lines[: stop_index(lines)]
    fields = header_fields(lines, HEADER_LABELS)
    # 名稱行之前的導覽文字不列入
    start = 0
    for i, ln in enumerate(lines):
        if _label_key(ln).startswith("法規名稱"):
            start = i
            break
    lines = lines[start:]
    arts = split_articles(lines)
    attachments = []
    for a in root.find_all("a", href=True):
        if "Download.ashx" in a["href"]:
            attachments.append((a.get_text(" ", strip=True), urljoin(base_url + "/", a["href"])))
    body = _body_text(lines)
    return ParsedPage(fields=fields, articles=arts, body=body, attachments=attachments)


def _body_text(lines: list[str]) -> str:
    """無條號結構（函令、以「點」編排之指引）時的內文：略過標頭欄位與附件檔名行。"""
    keys = [_label_key(x) for x in HEADER_LABELS]
    out: list[str] = []
    skip_next_value = False
    for ln in lines:
        k = _label_key(ln)
        is_label = any(k.startswith(x) for x in keys) and re.match(r"^[^：:]{1,8}[：:]", ln.replace(" ", ""))
        if is_label:
            # 「標籤：值」同行 → 值已在 fields；「標籤：」獨立一行 → 下一行為值
            skip_next_value = bool(re.match(r"^[^：:]{1,8}[：:]\s*$", ln))
            continue
        if skip_next_value:
            skip_next_value = False
            continue
        if ATTACH_LINE_RE.search(ln) or ln in NAV_LINES:
            continue
        if ln.startswith("※"):
            continue
        out.append(ln)
    return "\n".join(out).strip()


@dataclass
class ListItem:
    id: str
    title: str
    date: str
    kind: str
    url: str


_KINDS = ("法律", "命令", "行政規則", "實質法規", "其他", "其　　他")


def parse_category_list(html: str | bytes) -> tuple[list[ListItem], int]:
    """LawCategoryContentList.aspx 清單 → (項目, 最末頁頁碼；不明時 0)。"""
    soup = soup_of(html)
    items: list[ListItem] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "LawContent.aspx" not in href or "media=print" in href:
            continue
        q = parse_qs(urlparse(href).query)
        lid = (q.get("id") or [""])[0]
        if not lid or lid in seen:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        seen.add(lid)
        row = a.find_parent("tr") or a.find_parent("li") or a.parent
        rtext = row.get_text(" ", strip=True) if row is not None else ""
        dm = DOT_DATE_RE.search(rtext)
        kind = next((k for k in _KINDS if k in rtext.replace(title, "")), "")
        items.append(ListItem(id=lid, title=title, date=dm.group(0) if dm else "",
                              kind=kind.replace("　", ""), url=urljoin(BASE + "/", href)))
    last = 0
    for a in soup.find_all("a", href=True):
        if "LawCategoryContentList.aspx" in a["href"]:
            pq = parse_qs(urlparse(a["href"]).query)
            try:
                last = max(last, int((pq.get("page") or ["0"])[0]))
            except ValueError:
                pass
    return items, last


def parse_history_list(html: str | bytes) -> list[tuple[str, str]]:
    """LawContentHistoryList.aspx → [(異動日期原文, 絕對 URL)]，依頁面順序。"""
    soup = soup_of(html)
    out = []
    for a in soup.find_all("a", href=True):
        if "LawContentHistory.aspx" not in a["href"]:
            continue
        row = a.find_parent("tr") or a.parent
        rtext = row.get_text(" ", strip=True) if row is not None else ""
        dm = DOT_DATE_RE.search(rtext)
        out.append((dm.group(0) if dm else "", urljoin(BASE + "/", a["href"])))
    return out


def parse_source_page(html: str | bytes) -> str:
    """LawContentSource.aspx → 沿革原文（各筆以 \\n 分隔）。"""
    soup = soup_of(html)
    root = _content_root(soup)
    lines = text_lines(root)
    lines = lines[: stop_index(lines)]
    hist = [ln for ln in lines if re.match(r"^\d+\s*[\.、．]\s*中華民國", ln) or ln.startswith("中華民國")]
    # 一筆沿革可能折成多行：把非「數字.」開頭、緊接在沿革行之後的行併入
    if not hist:
        return ""
    merged: list[str] = []
    capture = False
    for ln in lines:
        if re.match(r"^\d+\s*[\.、．]\s*中華民國", ln) or ln.startswith("中華民國"):
            merged.append(ln)
            capture = True
        elif capture and not _is_label_line(ln, [_label_re(x) for x in HEADER_LABELS]) \
                and ln not in NAV_LINES and not ATTACH_LINE_RE.search(ln):
            merged[-1] = merged[-1] + ln
        else:
            capture = False
    return "\n".join(merged)


# ───────────────────────── 對外介面 ─────────────────────────
def _resolve_id(entry: SeedEntry, fetcher, notes: list[str]) -> str:
    for k in ("fsc_id", "id", "lawid"):
        if entry.ids.get(k):
            return str(entry.ids[k])
    if entry.name in KNOWN_IDS:
        return KNOWN_IDS[entry.name]
    if entry.url and "law.fsc.gov.tw" in entry.url:
        q = parse_qs(urlparse(entry.url).query)
        if q.get("id"):
            return q["id"][0]
    # 依名稱於法規體系清單搜尋（完全相符）
    target = norm_text(entry.name)
    for cat in DEFAULT_INTERP_CATEGORIES:
        for it in _iter_category(cat, fetcher, {}):
            if norm_text(it.title) == target:
                return it.id
    notes.append("未能於金管會法規體系清單中以名稱定位此法規（請於 seed 補 ids.fsc_id）")
    return ""


_LIST_CACHE: dict[tuple[str, int], tuple[list[ListItem], int]] = {}


def _iter_category(cat: str, fetcher, cache: dict) -> Iterable[ListItem]:
    page, last = 1, 1
    while page <= min(max(last, 1), MAX_LIST_PAGES):
        key = (cat, page)
        if key in cache:
            items, lp = cache[key]
        else:
            res = fetcher.get(CATEGORY_LIST_URL.format(cat=cat, page=page))
            if not is_ok(res):
                cache[key] = ([], 0)
                return
            items, lp = parse_category_list(res.content)
            cache[key] = (items, lp)
        if not items:
            return
        last = max(last, lp)
        yield from items
        page += 1


def _doc_from_parsed(entry: SeedEntry, p: ParsedPage, url: str, res: FetchResult,
                     notes: list[str], *, name: str | None = None) -> LawDocument:
    f = p.fields
    amended, amend_note = split_date_note(f.get("修正日期", ""))
    promulgated, _ = split_date_note(f.get("公發布日", ""))
    eff = f.get("施行日期") or f.get("生效日期") or f.get("生效狀態") or ""
    if not eff and amend_note:
        eff = amend_note                      # 例：「※本次發布之條文全部或部分尚未施行（實施）」
    arts = p.articles
    if not arts and p.body:
        arts = [Article(no="全文", text=p.body)]
        notes.append("頁面無條號結構，以全文為單一條目")
    if not arts and p.attachments:
        notes.append("頁面無內文，僅有附件；附件請見 metadata.attachments")
    meta = {k: f[k] for k in ("發文字號", "法規體系") if f.get(k)}
    if p.attachments:
        meta["attachments"] = [{"name": n, "url": u} for n, u in p.attachments]
    if res.method_used == "wayback" and res.note:
        notes.append(res.note)
    return LawDocument(
        name=name or f.get("法規名稱") or entry.name,
        source_name=SOURCE_NAME,
        source_url=url,
        status=entry.status or STATUS_CURRENT,
        doc_type=entry.doc_type,
        promulgated_date=promulgated or NA,
        amended_date=amended or NA,
        effective_date=eff or NA,
        articles=arts,
        metadata=meta,
        fetch_method=res.method_used,
        fetch_note="；".join(n for n in notes if n),
        fetched_at=now_iso(),
        category=entry.category,
        parent_law=entry.parent_law,
        version_label=(amended if amended != NA else promulgated) or "",
    )


def _empty(entry: SeedEntry, url: str, note: str, method: str = "") -> LawDocument:
    return LawDocument(name=entry.name, source_name=SOURCE_NAME, source_url=url,
                       status=entry.status or STATUS_CURRENT, doc_type=entry.doc_type,
                       fetch_method=method, fetch_note=note, fetched_at=now_iso(),
                       category=entry.category, parent_law=entry.parent_law)


def fetch_one(entry: SeedEntry, fetcher) -> tuple[LawDocument, str]:
    notes: list[str] = []
    lid = _resolve_id(entry, fetcher, notes)
    if not lid:
        return _empty(entry, BASE, "；".join(notes)), ""
    url = LAW_URL.format(id=lid)
    res = fetcher.get(url)
    if not is_ok(res):
        return _empty(entry, url, fail_note(res, "法規內容")), lid
    p = parse_law_page(res.content)
    if p.fields.get("法規名稱") and norm_text(p.fields["法規名稱"]) != norm_text(entry.name):
        notes.append(f"頁面法規名稱「{p.fields['法規名稱']}」與 seed 名稱不同")
    if not p.articles and not p.body and p.attachments:
        # 指引／規章本文僅以 DOCX/PDF 附件提供時，解析附件（python-docx / pdfplumber）
        for fname, aurl in p.attachments:
            if not re.search(r"\.(docx|pdf)\s*$", fname, re.I):
                continue
            ares = fetcher.get(aurl, expect="binary")
            if not is_ok(ares):
                continue
            txt = extract_attachment_text(ares.content, fname)
            if txt:
                alines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
                p.articles = split_articles(alines) or [Article(no="全文", text="\n".join(alines))]
                notes.append(f"條文取自附件「{fname}」")
                break
    if not p.articles and not p.body:
        notes.append("頁面已取得但無法解析條文（版面可能變更，候選選擇器皆未命中）")
    doc = _doc_from_parsed(entry, p, url, res, notes, name=entry.name)
    doc.metadata["fsc_id"] = lid
    # 沿革（失敗不影響本體）
    sres = fetcher.get(LAW_SOURCE_URL.format(id=lid))
    if is_ok(sres):
        doc.history = parse_source_page(sres.content)
    return doc, lid


def fetch(entries: list[SeedEntry], fetcher) -> list[LawDocument]:
    docs: list[LawDocument] = []
    cache: dict = {}
    for e in entries:
        try:
            doc, lid = fetch_one(e, fetcher)
        except Exception as ex:  # noqa: BLE001 — 單筆失敗不可讓整批失敗
            docs.append(_empty(e, BASE, f"解析例外：{type(ex).__name__}: {ex}"))
            continue
        docs.append(doc)
        if e.include_interpretations and lid:
            try:
                docs.extend(fetch_interpretations(e, lid, fetcher, cache))
            except Exception as ex:  # noqa: BLE001
                doc.fetch_note = "；".join(x for x in (doc.fetch_note, f"函令擷取例外：{ex}") if x)
    return docs


# ───────────────────────── 函令／函釋 ─────────────────────────
def _norm_title(s: str) -> str:
    return re.sub(r"[「」『』\"“”〈〉《》]", "", norm_text(s))


def _mentions_law(text: str, law_name: str) -> bool:
    t = _norm_title(text)
    names = (law_name,) + LAW_ALIASES.get(law_name, ())
    return any(_norm_title(n) in t for n in names)


def _has_keyword(text: str) -> bool:
    return any(k in (text or "") for k in INTERP_KEYWORDS)


def interpretation_name(title: str, fields: dict) -> str:
    """name = 原文標題（原文發文日期 原文發文字號）。"""
    date = fields.get("公發布日", "") or fields.get("發文日期", "")
    no = fields.get("發文字號", "")
    tail = " ".join(x for x in (date, no) if x)
    return f"{title}（{tail}）" if tail else title


def fetch_interpretations(entry: SeedEntry, law_id: str, fetcher, cache: dict | None = None) -> list[LawDocument]:
    cache = {} if cache is None else cache
    cats = INTERP_CATEGORIES.get(entry.name, DEFAULT_INTERP_CATEGORIES)
    cands: dict[str, ListItem] = {}
    weak: dict[str, ListItem] = {}       # 標題含關鍵字但未提及法規名稱 → 需看全文是否提及
    for cat in cats:
        for it in _iter_category(cat, fetcher, cache):
            if it.id == law_id or it.kind in ("法律", "命令"):
                continue
            if _mentions_law(it.title, entry.name):
                cands.setdefault(it.id, it)
            elif _has_keyword(it.title):
                weak.setdefault(it.id, it)
    out: list[LawDocument] = []
    opened = 0
    for it, need_mention in [(x, False) for x in cands.values()] + [(x, True) for x in weak.values()]:
        if opened >= MAX_INTERP_CANDIDATES:
            break
        opened += 1
        res = fetcher.get(it.url)
        if not is_ok(res):
            continue
        p = parse_law_page(res.content)
        body = p.body or "\n".join(a.text for a in p.articles)
        title = p.fields.get("法規名稱") or it.title
        if not (_has_keyword(title) or _has_keyword(body)):
            continue
        if need_mention and not _mentions_law(body, entry.name):
            continue
        name = interpretation_name(title, p.fields)
        e2 = SeedEntry(name=name, source="fsc", category=entry.category, status=STATUS_CURRENT,
                       doc_type="函釋", parent_law=entry.name)
        d = _doc_from_parsed(e2, p, it.url, res, [], name=name)
        d.articles = [Article(no="函釋全文", text=body)] if body else []
        d.fetch_note = "；".join(n for n in d.fetch_note.split("；") if n and not n.startswith("頁面無條號結構"))
        d.metadata["fsc_id"] = it.id
        d.metadata["related_law"] = entry.name
        out.append(d)
    return out


# ───────────────────────── 前一版 ─────────────────────────
def fetch_previous(entry: SeedEntry, current: LawDocument, fetcher) -> Optional[LawDocument]:
    def note(msg: str) -> None:
        current.fetch_note = "；".join(x for x in (current.fetch_note, msg) if x)

    if current.doc_type == "函釋":
        note("函釋無版本沿革，不抓取前一版")
        return None
    lid = current.metadata.get("fsc_id") or entry.ids.get("fsc_id") or KNOWN_IDS.get(entry.name, "")
    if not lid:
        note("無法規 ID，無法取得前一版")
        return None
    res = fetcher.get(HISTORY_LIST_URL.format(id=lid))
    if not is_ok(res):
        note("前一版：" + fail_note(res, "歷史法規清單"))
        return None
    rows = parse_history_list(res.content)
    if not rows:
        note("前一版：歷史法規清單無資料（可能為首次發布）")
        return None
    cur_ymd = roc_to_ymd(current.amended_date) or roc_to_ymd(current.promulgated_date)
    dated = [(roc_to_ymd(d), d, u) for d, u in rows]
    older = [x for x in dated if x[0] and (not cur_ymd or x[0] < cur_ymd)]
    pick = max(older, key=lambda x: x[0]) if older else (dated[0] if not cur_ymd else None)
    if pick is None:
        note("前一版：歷史法規清單中無早於現行版本之版本")
        return None
    _, raw_date, url = pick
    hres = fetcher.get(url)
    if not is_ok(hres):
        note("前一版：" + fail_note(hres, "歷史法規"))
        return None
    p = parse_law_page(hres.content)
    notes: list[str] = []
    if not p.articles and not p.body:
        note("前一版：頁面已取得但無法解析條文")
        return None
    prev = _doc_from_parsed(entry, p, url, hres, notes, name=current.name)
    ver = p.fields.get("歷史法規", "")
    vdate, _ = split_date_note(ver) if ver else (NA, "")
    # 歷史版頁面無「修正日期」欄位，以「歷史法規：」所載日期為該版修正日（原文）
    if prev.amended_date == NA and vdate != NA:
        prev.amended_date = vdate
    prev.promulgated_date = current.promulgated_date if prev.promulgated_date == NA else prev.promulgated_date
    prev.version_label = vdate if vdate != NA else raw_date
    prev.metadata["fsc_id"] = lid
    prev.metadata["history_list_date"] = raw_date
    return prev
