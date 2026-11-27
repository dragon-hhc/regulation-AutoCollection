"""#2 臺灣證券交易所「法規分享知識庫」https://twse-regulation.twse.com.tw

Open API 查證：證交所 OpenAPI（https://openapi.twse.com.tw/v1/swagger.json，2026-11-27 WebFetch 全文檢視）
無法規／規章全文端點（僅 t187ap23_L、t187ap32_L 等申報彙總資料），故以網頁擷取。

以下 URL 樣式皆於 2026-11-27 以 WebFetch 實際開啟查證：
  - 首頁／導覽            https://twse-regulation.twse.com.tw/TW
  - 法令規章清單          https://twse-regulation.twse.com.tw/TW/LawLst.aspx （市場規章，306 筆單頁）
                          https://twse-regulation.twse.com.tw/TW/LawLst.aspx?type=1 （相關法規）
  - 所有條文              https://twse-regulation.twse.com.tw/TW/law/DAT0201.aspx?FLCODE=FL104387
                          https://twse-regulation.twse.com.tw/TW/law/DAT0201.aspx?FLCODE=FL007111
  - 歷史沿革              https://twse-regulation.twse.com.tw/TW/law/DAT01.aspx?FLCODE=FL104387
                          （各筆附「全文」連結 DAT06.aspx?FLCODE=..&FLDATE=YYYYMMDD&LSER=001、
                            「異動條文」DAT0601.aspx、「立法說明」GetFile.ashx?FileID=）
  - 歷史版本全文          https://twse-regulation.twse.com.tw/TW/law/DAT06.aspx?FLCODE=FL104387&FLDATE=20240910&LSER=001
頁面結構（WebFetch 所見）：標頭為表格「名　　稱：」「公發布日：」「修正日期：」「生效狀態：」「主題分類：」；
條號為連結「第 1 條」（href DOC01.aspx?FLCODE=..&FLNO=1），其後內文為預排格式區塊（<pre>）；
章節標題（例「第 一 章 總則」）為純文字。歷史版頁有「歷史名稱：上市上櫃公司資通安全管控指引(民國 113 年 09 月 10 日)」。
WebFetch 無法看到原始標記，故 DOM 解析失敗時退回逐行文字切分（fsc.split_articles）。
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import NavigableString, Tag

from ..models import NA, STATUS_CURRENT, Article, LawDocument, SeedEntry
from ..normalize import norm_text
from .fsc import (ART_HEAD_RE, CHAPTER_RE, ROC_DATE_RE, effect_clause, fail_note, header_fields,
                  is_ok, now_iso, roc_to_ymd, soup_of, split_articles, split_date_note, stop_index,
                  text_lines)

SOURCE_NAME = "臺灣證券交易所法規分享知識庫"
BASE = "https://twse-regulation.twse.com.tw"
ALL_URL = BASE + "/TW/law/DAT0201.aspx?FLCODE={c}"
HISTORY_URL = BASE + "/TW/law/DAT01.aspx?FLCODE={c}"
LIST_URLS = (BASE + "/TW/LawLst.aspx", BASE + "/TW/LawLst.aspx?type=1")

# 已查證之 FLCODE（https://twse-regulation.twse.com.tw/TW/LawLst.aspx 清單，2026-11-27）
KNOWN_FLCODES: dict[str, str] = {
    "上市上櫃公司資通安全管控指引": "FL104387",
    "臺灣證券交易所股份有限公司對有價證券上市公司重大訊息之查證暨公開處理程序": "FL007111",
    "證券期貨市場相關公會新興科技資通安全管控指引": "FL098969",
    "證券商辦理資通系統資通安全評估作業程序": "FL105274",
    "建立證券商資通安全檢查機制": "FL007290",
}

LABELS = ("名稱", "公發布日", "修正日期", "生效狀態", "主題分類", "歷史名稱", "歷次修正日期")
STOP_TEXT = ("回上方", "瀏覽人次", "最後修訂日期", "行動版")


def _resolve(entry: SeedEntry, fetcher, notes: list[str]) -> str:
    for k in ("flcode", "FLCODE", "twse_flcode"):
        if entry.ids.get(k):
            return str(entry.ids[k])
    if entry.name in KNOWN_FLCODES:
        return KNOWN_FLCODES[entry.name]
    if entry.url and "FLCODE=" in entry.url:
        return parse_qs(urlparse(entry.url).query).get("FLCODE", [""])[0]
    target = norm_text(entry.name)
    for u in LIST_URLS:
        res = fetcher.get(u)
        if not is_ok(res):
            continue
        for name, code in parse_law_list(res.content):
            if norm_text(name) == target:
                return code
    notes.append("未能於法規分享知識庫法令規章清單中以名稱定位（請於 seed 補 ids.flcode）")
    return ""


def parse_law_list(html: str | bytes) -> list[tuple[str, str]]:
    """LawLst.aspx → [(名稱原文, FLCODE)]（排除「英」連結）。"""
    soup = soup_of(html)
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/law/" not in href or "FLCODE=" not in href or "/ENG/" in href.upper():
            continue
        name = a.get_text(" ", strip=True)
        if not name or name in ("英", "English"):
            continue
        code = parse_qs(urlparse(href).query).get("FLCODE", [""])[0]
        out.append((name, code))
    return out


def _split_name(v: str) -> str:
    """「名稱」欄可能含英文名稱（同欄另起一行或以空白相接），只取中文名稱原文。"""
    v = v.strip()
    m = re.match(r"^(.*?[一-鿿）」』])\s+[A-Za-z].*$", v)
    return m.group(1).strip() if m else v


def parse_articles_dom(soup) -> list[Article]:
    """依 DOM 順序：章節文字 → 條號連結（DOC01.aspx）→ 內文 <pre>。"""
    arts: list[Article] = []
    chapter = ""
    cur: Optional[Article] = None
    for node in soup.descendants:
        if isinstance(node, NavigableString):
            if node.parent is not None and node.parent.name in ("a", "pre"):
                continue
            s = str(node).strip()
            if s and CHAPTER_RE.match(s) and len(s) <= 40:
                chapter = s
            continue
        if not isinstance(node, Tag):
            continue
        if node.name == "a" and "DOC01.aspx" in (node.get("href") or ""):
            t = node.get_text(" ", strip=True)
            m = ART_HEAD_RE.match(t)
            if m and not m.group(2):
                cur = Article(no=m.group(1), text="", chapter=chapter)
                arts.append(cur)
        elif node.name == "pre" and cur is not None and not cur.text:
            lines = [ln.rstrip() for ln in node.get_text().splitlines()]
            cur.text = "\n".join(ln.strip() for ln in lines if ln.strip())
    return [a for a in arts if a.text]


def parse_law_page(html: str | bytes) -> tuple[dict, list[Article]]:
    soup = soup_of(html)
    lines = text_lines(soup.body or soup)
    cut = len(lines)
    for i, ln in enumerate(lines):
        if any(ln.startswith(s) for s in STOP_TEXT):
            cut = i
            break
    lines = lines[:min(cut, stop_index(lines))]
    fields = header_fields(lines, LABELS)
    if "名稱" in fields:
        fields["名稱"] = _split_name(fields["名稱"])
    arts = parse_articles_dom(soup)
    if not arts:
        start = next((i for i, ln in enumerate(lines) if re.sub(r"[\s　]", "", ln).startswith("名稱")), 0)
        arts = [a for a in split_articles(lines[start:]) if a.text and a.text != "相關資訊"]
        for a in arts:
            a.text = "\n".join(x for x in a.text.split("\n") if x != "相關資訊")
    return fields, arts


def parse_history(html: str | bytes) -> tuple[str, list[tuple[str, str]]]:
    """DAT01 → (沿革原文, [(FLDATE, 全文 URL)])。"""
    soup = soup_of(html)
    links = []
    for a in soup.find_all("a", href=True):
        if "DAT06.aspx" in a["href"] and "FLDATE=" in a["href"]:
            q = parse_qs(urlparse(a["href"]).query)
            links.append((q.get("FLDATE", [""])[0], urljoin(BASE + "/TW/law/", a["href"])))
    texts = []
    for pre in soup.find_all("pre"):
        t = pre.get_text()
        if "中華民國" in t:
            texts.append("\n".join(ln.strip() for ln in t.splitlines() if ln.strip()))
    if not texts:
        lines = text_lines(soup.body or soup)
        texts = [ln for ln in lines if re.match(r"^\d+\s*\.\s*中華民國", ln)]
    return "\n".join(texts), links


def _latest_history_entry(history: str) -> str:
    """沿革中編號最大的一筆（原文，含折行合併）。"""
    entries = re.split(r"\n(?=\d+\s*\.\s*中華民國)", history.strip())
    def num(e: str) -> int:
        m = re.match(r"^(\d+)", e)
        return int(m.group(1)) if m else -1
    return max(entries, key=num) if entries and entries[0] else ""


def _doc(entry: SeedEntry, fields: dict, arts: list[Article], url: str, res, notes: list[str],
         history: str = "") -> LawDocument:
    amended, _ = split_date_note(fields.get("修正日期", ""))
    promulgated, _ = split_date_note(fields.get("公發布日", ""))
    eff = fields.get("生效狀態", "")
    if not eff and history:
        eff = effect_clause(re.sub(r"\s+", "", _latest_history_entry(history)))
    if res.method_used == "wayback" and res.note:
        notes.append(res.note)
    meta = {k: fields[k] for k in ("主題分類",) if fields.get(k)}
    return LawDocument(
        name=entry.name, source_name=SOURCE_NAME, source_url=url,
        status=entry.status or STATUS_CURRENT, doc_type=entry.doc_type,
        promulgated_date=promulgated or NA, amended_date=amended or NA, effective_date=eff or NA,
        history=history, articles=arts, metadata=meta, fetch_method=res.method_used,
        fetch_note="；".join(n for n in notes if n), fetched_at=now_iso(),
        category=entry.category, parent_law=entry.parent_law,
        version_label=amended if amended and amended != NA else "")


def _empty(entry: SeedEntry, url: str, note: str) -> LawDocument:
    return LawDocument(name=entry.name, source_name=SOURCE_NAME, source_url=url,
                       status=entry.status or STATUS_CURRENT, doc_type=entry.doc_type,
                       fetch_note=note, fetched_at=now_iso(), category=entry.category,
                       parent_law=entry.parent_law)


def fetch_one(entry: SeedEntry, fetcher) -> LawDocument:
    notes: list[str] = []
    code = _resolve(entry, fetcher, notes)
    if not code:
        return _empty(entry, BASE + "/TW/LawLst.aspx", "；".join(notes))
    url = ALL_URL.format(c=code)
    res = fetcher.get(url)
    if not is_ok(res):
        return _empty(entry, url, fail_note(res, "所有條文"))
    fields, arts = parse_law_page(res.content)
    if fields.get("名稱") and norm_text(fields["名稱"]) != norm_text(entry.name):
        notes.append(f"頁面名稱「{fields['名稱']}」與 seed 名稱不同")
    if not arts:
        notes.append("頁面已取得但無法解析條文（版面可能變更）")
    history = ""
    hres = fetcher.get(HISTORY_URL.format(c=code))
    if is_ok(hres):
        history, _ = parse_history(hres.content)
    doc = _doc(entry, fields, arts, url, res, notes, history)
    doc.metadata["flcode"] = code
    return doc


def fetch(entries: list[SeedEntry], fetcher) -> list[LawDocument]:
    out = []
    for e in entries:
        try:
            out.append(fetch_one(e, fetcher))
        except Exception as ex:  # noqa: BLE001 — 單筆失敗不可讓整批失敗
            out.append(_empty(e, BASE, f"解析例外：{type(ex).__name__}: {ex}"))
    return out


def fetch_previous(entry: SeedEntry, current: LawDocument, fetcher) -> Optional[LawDocument]:
    def note(msg: str) -> None:
        current.fetch_note = "；".join(x for x in (current.fetch_note, msg) if x)

    code = current.metadata.get("flcode") or entry.ids.get("flcode") or KNOWN_FLCODES.get(entry.name, "")
    if not code:
        note("前一版：無 FLCODE")
        return None
    res = fetcher.get(HISTORY_URL.format(c=code))
    if not is_ok(res):
        note("前一版：" + fail_note(res, "歷史沿革"))
        return None
    _, links = parse_history(res.content)
    cur = roc_to_ymd(current.amended_date) or roc_to_ymd(current.promulgated_date)
    older = sorted({(d, u) for d, u in links if d.isdigit() and (not cur or d < cur)}, reverse=True)
    if not older:
        note("前一版：歷史沿革無早於現行版本之全文連結")
        return None
    fldate, url = older[0]
    pres = fetcher.get(url)
    if not is_ok(pres):
        note("前一版：" + fail_note(pres, "歷史版本全文"))
        return None
    fields, arts = parse_law_page(pres.content)
    if not arts:
        note("前一版：頁面已取得但無法解析條文")
        return None
    # 歷史版頁的「修正日期」為現行版日期；該版日期取「歷史名稱：…(民國 113 年 09 月 10 日)」原文
    hname = fields.get("歷史名稱", "")
    m = ROC_DATE_RE.search(hname)
    fields = dict(fields)
    fields["修正日期"] = m.group(0) if m else NA
    fields.pop("生效狀態", None)
    notes = [] if m else [f"前一版日期未能自頁面取得（FLDATE={fldate}）"]
    prev = _doc(entry, fields, arts, url, pres, notes)
    prev.promulgated_date = current.promulgated_date if prev.promulgated_date == NA else prev.promulgated_date
    prev.metadata["flcode"] = code
    prev.metadata["fldate"] = fldate
    return prev
