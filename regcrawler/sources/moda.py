"""數位發展部資通安全署公告區／數位發展部法規／個人資料保護委員會籌備處「草案預告」（#1）。

處理 seed 中 source: moda 的條目（目前為個資會籌備處預告之兩部草案），status 保留「草案」。

查證紀錄（2026-11-27，WebFetch／WebSearch）：
- 個資會籌備處主管法規查詢系統「草案預告」：
  https://law.pdpc.gov.tw/DraftForum.aspx （預告中；查證時顯示「很抱歉，尚無預告中法規草案。」）
  https://law.pdpc.gov.tw/DraftForum.aspx?Type=H （歷史法規草案；表格欄位：草案名稱、公告日期、
  預告終止日；草案名稱連結指向 join.gov.tw）
  （網址來源：個資會籌備處 115.3.9 個資籌查字第1150500086B號函說明一，
   https://www.wljh.ptc.edu.tw/uploads/17737287601788wfBfDDd.pdf ）
- 公共政策網路參與平臺「眾開講」預告頁（含「請參見PDF」之行政院公報 PDF 連結）：
  * 個人資料事故通知通報及應變辦法草案：
    https://join.gov.tw/policies/detail/8bb40e4f-cf20-45f1-aa47-6a42958e323b
    （歷史草案列表連結 https://join.gov.tw/policies/gazette/metaId/163195）
    公告：中華民國115年1月22日 個資籌查字第1140500549號；預告終止日 115.03.23
    草案 PDF：https://gazette.nat.gov.tw/EG_FileManager/eguploadpub/eg032015/ch01/type3/gov79/num1/images/Eg01.pdf
  * 個人資料檔案安全維護管理辦法草案：
    https://join.gov.tw/policies/detail/5860d092-ac75-48ba-abd8-fdac12bd00df
    （歷史草案列表連結 https://join.gov.tw/policies/gazette/metaId/163201）
    公告：中華民國115年1月22日 個資籌科字第1150600008號；預告終止日 115.03.23
    草案 PDF：https://gazette.nat.gov.tw/EG_FileManager/eguploadpub/eg032015/ch01/type3/gov79/num4/images/Eg01.pdf
  草案 PDF 版面：標題「○○辦法草案總說明」後接「○○辦法草案」雙欄表（表頭「條文｜說明」）。
- 數位發展部法規網 https://law.moda.gov.tw/ （首頁列出公告，含類別「法規草案」，例如
  115.10.16「數位發展部資通安全署公告：預告「資通安全職能評量收費標準」修正草案」）；
  其列表 HTML 結構無法以 WebFetch 取得，故僅以「連結文字含「法規名稱」」之寬鬆規則嘗試定位。
- 數位發展部資通安全署公告區（moda.gov.tw/ACS/...）為 JS 渲染頁，WebFetch 無法取得列表結構；
  同上以寬鬆規則嘗試，失敗時在 fetch_note 說明。

PDF 解析使用 pdfplumber（表格優先，退回文字層）；DOCX 使用 python-docx。
"""
from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from ..models import NA, STATUS_DRAFT, Article, FetchResult, LawDocument, SeedEntry

SOURCE_NAME = "數位發展部資通安全署公告區／個人資料保護委員會籌備處草案預告"

# 依序嘗試的列表頁（皆已查證存在；結構見模組說明）
LIST_PAGES = (
    "https://law.pdpc.gov.tw/DraftForum.aspx",
    "https://law.pdpc.gov.tw/DraftForum.aspx?Type=H",
    "https://law.moda.gov.tw/",
)

# 已查證之草案預告紀錄（新→舊）。動態定位失敗時使用；欄位皆來自官方頁面。
KNOWN_DRAFTS: dict[str, list[dict]] = {
    "個人資料事故通知通報及應變辦法": [
        {
            "title": "個人資料保護委員會籌備處公告：預告「個人資料事故通知通報及應變辦法」草案",
            "announce_date": "115.01.22",
            "end_date": "115.03.23",
            "notice": "中華民國115年1月22日個資籌查字第1140500549號",
            "page": "https://join.gov.tw/policies/detail/8bb40e4f-cf20-45f1-aa47-6a42958e323b",
            "attachments": [
                "https://gazette.nat.gov.tw/EG_FileManager/eguploadpub/eg032015/ch01/type3/gov79/num1/images/Eg01.pdf",
            ],
        },
    ],
    "個人資料檔案安全維護管理辦法": [
        {
            "title": "個人資料保護委員會籌備處公告：預告「個人資料檔案安全維護管理辦法」草案",
            "announce_date": "115.01.22",
            "end_date": "115.03.23",
            "notice": "中華民國115年1月22日個資籌科字第1150600008號",
            "page": "https://join.gov.tw/policies/detail/5860d092-ac75-48ba-abd8-fdac12bd00df",
            "attachments": [
                "https://gazette.nat.gov.tw/EG_FileManager/eguploadpub/eg032015/ch01/type3/gov79/num4/images/Eg01.pdf",
            ],
        },
    ],
}

_CN_NUM = "〇零一二三四五六七八九十百"
_ART_HEAD = re.compile(rf"^第\s*[{_CN_NUM}\d]+\s*條(?:之\s*[{_CN_NUM}\d]+)?")
_ITEM_HEAD = re.compile(rf"^(?:[{_CN_NUM}]+、|（[{_CN_NUM}]+）|\([{_CN_NUM}]+\)|\d+\.)")
_DATE_ROC = re.compile(r"\d{2,3}[./]\d{1,2}[./]\d{1,2}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _add_note(doc: LawDocument, note: str) -> None:
    if note:
        doc.fetch_note = f"{doc.fetch_note}；{note}" if doc.fetch_note else note


def _result_note(res: FetchResult) -> str:
    notes = [res.note] if res.note else []
    if res.wayback_timestamp and res.wayback_timestamp not in (res.note or ""):
        notes.append(f"Wayback 快照 {res.wayback_timestamp}")
    return "；".join(notes)


# ───────────────────────── 列表頁定位 ─────────────────────────
def find_announcements(html: str, name: str, base_url: str) -> list[dict]:
    """在列表頁中找出標題含「name」的草案預告。回傳 [{title, announce_date, end_date, page}]（頁面順序）。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    seen = set()
    for a in soup.select("a[href]"):
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        quoted = re.findall(r"「(.+?)」", title)
        # 標題有「」時必須精確等於法規名稱（避免「個人資料保護法」誤配到其他子法草案）
        if quoted and name not in quoted:
            continue
        if not quoted and name not in title:
            continue
        if "草案" not in title:
            continue
        href = urljoin(base_url, a["href"])
        key = (title, href)
        if key in seen:
            continue
        seen.add(key)
        row = a.find_parent("tr")
        dates: list[str] = []
        if row is not None:
            dates = _DATE_ROC.findall(row.get_text(" ", strip=True))
        out.append({
            "title": title,
            "announce_date": dates[0] if dates else "",
            "end_date": dates[1] if len(dates) > 1 else "",
            "notice": "",
            "page": href,
            "attachments": [],
        })
    return out


def find_attachments(html: str, base_url: str) -> list[str]:
    """預告頁中的草案附件（行政院公報 PDF 優先，其次 pdf/docx/odt）。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    gazette, others = [], []
    for a in soup.select("a[href]"):
        href = urljoin(base_url, a["href"])
        low = href.lower()
        if "公共政策網路參與實施要點" in href or "%E5%85%AC%E5%85%B1%E6%94%BF%E7%AD%96" in href:
            continue
        if "gazette.nat.gov.tw" in low and low.endswith(".pdf"):
            if href not in gazette:
                gazette.append(href)
        elif re.search(r"\.(pdf|docx|odt)(\?|$)", low) or "/file/get/" in low:
            if href not in others:
                others.append(href)
    # 公報：images/Eg01.pdf 為草案本文，Eg.pdf 為公告本文 → Eg01 優先
    gazette.sort(key=lambda u: 0 if re.search(r"/images/Eg\d+\.pdf$", u, re.I) else 1)
    return gazette + others


# ───────────────────────── 附件解析 ─────────────────────────
def _clean_cell(s: str | None) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def join_wrapped(text: str) -> str:
    """PDF 欄位內固定寬度斷行接回：行尾為「。」「：」或下一行為款目編號時才保留換行。"""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    out: list[str] = []
    for ln in lines:
        if out and not (out[-1].endswith(("。", "：", ":")) or _ITEM_HEAD.match(ln)):
            out[-1] += ln
        else:
            out.append(ln)
    return "\n".join(out)


def parse_draft_rows(rows: list[list[str | None]]) -> list[Article]:
    """「條文｜說明」表格列 → 條文（只取左欄；跨頁續行併入前一條）。"""
    arts: list[Article] = []
    for row in rows:
        if not row:
            continue
        cell = _clean_cell(row[0])
        if not cell or re.fullmatch(r"條\s*文", cell):
            continue
        m = _ART_HEAD.match(cell)
        if m:
            no = m.group(0).strip()
            body = cell[m.end():].lstrip(" 　\n")
            arts.append(Article(no=no, text=join_wrapped(body)))
        elif arts:
            arts[-1].text = join_wrapped(arts[-1].text + "\n" + cell)
    return arts


def parse_draft_text(text: str) -> list[Article]:
    """文字層退回解析：行首「第X條」起新條。雙欄版面時可能混入說明欄，呼叫端須於 fetch_note 註明。"""
    arts: list[Article] = []
    started = False
    for ln in text.replace("\r\n", "\n").split("\n"):
        s = ln.strip()
        if not s:
            continue
        if not started:
            if re.fullmatch(r"條\s*文\s*說\s*明", s):
                started = True
            elif _ART_HEAD.match(s):
                started = True
            else:
                continue
        m = _ART_HEAD.match(s)
        if m:
            arts.append(Article(no=m.group(0).strip(), text=s[m.end():].lstrip(" 　")))
        elif arts and not re.fullmatch(r"條\s*文\s*說\s*明", s):
            arts[-1].text += "\n" + s
    for a in arts:
        a.text = join_wrapped(a.text)
    return arts


def extract_pdf(content: bytes) -> tuple[list[Article], str]:
    import pdfplumber

    rows: list[list[str | None]] = []
    texts: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            for tbl in page.extract_tables() or []:
                rows.extend(tbl)
            texts.append(page.extract_text() or "")
    arts = parse_draft_rows(rows)
    if arts:
        return arts, "PDF 表格（條文欄）解析"
    arts = parse_draft_text("\n".join(texts))
    return arts, "PDF 無可辨識表格，以文字層解析（雙欄版面可能混入說明欄文字）" if arts else "PDF 無法解析出條文"


def extract_docx(content: bytes) -> tuple[list[Article], str]:
    import docx  # python-docx

    d = docx.Document(io.BytesIO(content))
    rows: list[list[str | None]] = []
    for t in d.tables:
        for r in t.rows:
            rows.append([c.text for c in r.cells])
    arts = parse_draft_rows(rows)
    if arts:
        return arts, "DOCX 表格（條文欄）解析"
    arts = parse_draft_text("\n".join(p.text for p in d.paragraphs))
    return arts, "DOCX 以段落解析" if arts else "DOCX 無法解析出條文"


def extract_articles(content: bytes, url: str) -> tuple[list[Article], str]:
    head = content[:8]
    if head.startswith(b"%PDF"):
        return extract_pdf(content)
    if head.startswith(b"PK"):
        if b"word/" in content[:4000] or url.lower().endswith(".docx"):
            return extract_docx(content)
        return [], "附件為 ODT 或其他壓縮格式，本模組未支援解析"
    return [], "附件格式無法辨識（非 PDF/DOCX）"


# ───────────────────────── 主流程 ─────────────────────────
def _base_doc(entry: SeedEntry, url: str) -> LawDocument:
    return LawDocument(
        name=entry.name, source_name=SOURCE_NAME, source_url=url,
        status=entry.status or STATUS_DRAFT, doc_type=entry.doc_type,
        category=entry.category, parent_law=entry.parent_law, fetched_at=_now(),
        promulgated_date=NA, amended_date=NA, effective_date=NA,
    )


def locate(entry: SeedEntry, fetcher) -> tuple[list[dict], list[str]]:
    """回傳（預告紀錄 新→舊, 定位過程說明）。"""
    notes: list[str] = []
    found: list[dict] = []
    for url in LIST_PAGES:
        try:
            res = fetcher.get(url, expect="html")
        except Exception as exc:
            notes.append(f"{url} 取得失敗：{exc}")
            continue
        if not res.ok:
            notes.append(f"{url}：{res.note or '取得失敗'}")
            continue
        for ann in find_announcements(res.text, entry.name, res.final_url or url):
            if all(ann["page"] != f["page"] for f in found):
                found.append(ann)
    known = KNOWN_DRAFTS.get(entry.name, [])
    if not found and known:
        notes.append("列表頁未定位到，改用已查證之預告紀錄")
        found = [dict(k) for k in known]
    else:
        # 合併已查證的公文字號／附件資訊（以預告日期對應）
        for f in found:
            for k in known:
                if f["announce_date"] and f["announce_date"] == k["announce_date"]:
                    f["notice"] = f["notice"] or k["notice"]
                    f["attachments"] = f["attachments"] or list(k["attachments"])
    found.sort(key=lambda f: _date_key(f.get("announce_date", "")), reverse=True)
    return found, notes


def _date_key(s: str) -> tuple[int, int, int]:
    m = re.match(r"(\d+)[./](\d+)[./](\d+)", s or "")
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)  # type: ignore[return-value]


def build_document(entry: SeedEntry, ann: dict, fetcher) -> LawDocument:
    doc = _base_doc(entry, ann.get("page") or entry.url or LIST_PAGES[0])
    doc.version_label = f"預告 {ann['announce_date']}" if ann.get("announce_date") else ""
    hist = [ann.get("title", "")]
    if ann.get("notice"):
        hist.append(ann["notice"])
    if ann.get("announce_date"):
        hist.append(f"公告日期 {ann['announce_date']}")
    if ann.get("end_date"):
        hist.append(f"預告終止日 {ann['end_date']}")
    doc.history = "\n".join(h for h in hist if h)
    doc.metadata = {k: v for k, v in ann.items() if k != "attachments" and v}

    attachments = list(ann.get("attachments") or [])
    if ann.get("page"):
        pres = fetcher.get(ann["page"], expect="html")
        if pres.ok:
            for u in find_attachments(pres.text, pres.final_url or ann["page"]):
                if u not in attachments:
                    attachments.append(u)
        else:
            _add_note(doc, f"預告頁 {ann['page']} 無法取得：{pres.note or '取得失敗'}")
    if not attachments:
        _add_note(doc, "預告頁未找到草案附件")
        return doc
    for url in attachments:
        res = fetcher.get(url, expect="binary")
        if not res.ok:
            _add_note(doc, f"附件 {url}：{res.note or '下載失敗'}")
            continue
        try:
            arts, how = extract_articles(res.content, url)
        except Exception as exc:
            _add_note(doc, f"附件 {url} 解析失敗：{type(exc).__name__}: {exc}")
            continue
        if arts:
            doc.articles = arts
            doc.fetch_method = res.method_used
            doc.metadata["草案附件"] = url
            _add_note(doc, _result_note(res))
            _add_note(doc, f"{how}（{url}）")
            return doc
        _add_note(doc, f"{how}（{url}）")
    return doc


def fetch(entries: list[SeedEntry], fetcher) -> list[LawDocument]:
    out: list[LawDocument] = []
    for entry in entries:
        try:
            anns, notes = locate(entry, fetcher)
            if not anns:
                doc = _base_doc(entry, entry.url or LIST_PAGES[0])
                _add_note(doc, "；".join(notes))
                _add_note(doc, "各列表頁皆查無此草案預告")
            else:
                doc = build_document(entry, anns[0], fetcher)
                if notes:
                    doc.fetch_note = "；".join(notes + ([doc.fetch_note] if doc.fetch_note else []))
                doc.metadata["預告次數"] = len(anns)
        except Exception as exc:
            doc = _base_doc(entry, entry.url or LIST_PAGES[0])
            _add_note(doc, f"處理失敗：{type(exc).__name__}: {exc}")
        out.append(doc)
    return out


def fetch_previous(entry: SeedEntry, current: LawDocument, fetcher) -> LawDocument | None:
    """前次預告版本（同名草案較早一次預告）；沒有則回傳 None 並於 current.fetch_note 註明。"""
    try:
        anns, _ = locate(entry, fetcher)
    except Exception as exc:
        _add_note(current, f"前次預告定位失敗：{exc}")
        return None
    older = [a for a in anns if a.get("page") != current.source_url]
    cur_date = (current.metadata or {}).get("announce_date", "")
    if cur_date:
        older = [a for a in older if _date_key(a.get("announce_date", "")) < _date_key(cur_date)]
    if not older:
        _add_note(current, "查無前次預告版本")
        return None
    prev = build_document(entry, older[0], fetcher)
    if not prev.articles:
        _add_note(current, f"前次預告（{older[0].get('page')}）無法取得條文")
        return None
    return prev
