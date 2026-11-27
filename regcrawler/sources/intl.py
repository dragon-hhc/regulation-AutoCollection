"""#3 國際法規與標準追蹤：GDPR（EUR-Lex）、Digital Omnibus 立法進度、ISO/IEC 標準、BSI 標準。

所有網路存取一律透過 fetcher.get()（#4 五層備援鏈）。解析採「文字行」為主、HTML class 為輔，
因為開發沙箱無法直接連線 EUR-Lex / iso.org / BSI，HTML 結構只能透過 WebFetch 的文字輸出查證；
以文字行（區塊元素切行）解析，對 EUR-Lex 新舊兩種版面（oj-ti-art / title-article-norm / 表格式
點次）都能容錯。

─────────────── 查證紀錄（2026-11-27，WebSearch / WebFetch） ───────────────
[GDPR / EUR-Lex]
  * 原始 OJ 版全文：https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679
    - 表頭「4.5.2016 | EN | Official Journal of the European Union | L 119/1」、
      「of 27 April 2016」、「Whereas:」、recitals (1)…(173)、「HAVE ADOPTED THIS REGULATION:」、
      「CHAPTER I / General provisions」、「Article 99 / Entry into force and application」、
      「2. It shall apply from 25 May 2018.」、「Done at Brussels, 27 April 2016.」。
    - recital 編號與內文分屬相鄰表格儲存格。
  * consolidated version：https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02016R0679-20160504
    - 表頭「02016R0679 — EN — 04.05.2016 — 000.002」、「(OJ L 119 4.5.2016, p. 1)」、
      「Corrected by: ►C1 Corrigendum, OJ L 127, 23.5.2018, p. 2 ((EU) 2016/679)」；不含 recitals；
      點次 (a)(b) 與項次 1. 2. 各自獨立一行、內文在下一行；條末有「( 1 ) Directive (EU) 2015/1535 …」註腳。
    - ELI 對應：https://eur-lex.europa.eu/eli/reg/2016/679/2016-05-04/eng
  * consolidated CELEX 編號規則 0YYYYTNNNN-YYYYMMDD：
    https://eur-lex.europa.eu/content/help/eurlex-content/celex-number.html
  * CELLAR SPARQL 端點：https://publications.europa.eu/webapi/rdf/sparql
    （cdm:resource_legal_id_celex、須以 FILTER(STR(?celex)…) 比對，
     參考 https://smithery.ai/skills/matematicsolutions/eu-sparql-search ）
[Digital Omnibus COM(2025) 837 final, 2025/0360(COD)]
  * EUR-Lex procedure：https://eur-lex.europa.eu/procedure/EN/2025_360
    （「Status Ongoing」；事件 19/11/2025 Adoption of legislative proposal by the Commission …）
  * EP Legislative Observatory：https://oeil.europarl.europa.eu/oeil/en/procedure-file?reference=2025%2F0360%28COD%29
    （「Stage reached in procedure: Awaiting committee decision」；Key events 表格 Date/Event/Reference/Summary，
     日期格式 DD/MM/YYYY）
[ISO]
  * ISO/IEC 27001:2022 = iso.org id 82875（https://www.iso.org/standard/82875.html ；
    https://committee.iso.org/standard/27001 顯示 Status Published / Publication date 2022-10 / Edition 3 /
    Number of pages 19 / ISO/IEC JTC 1/SC 27 / ICS 35.030, 03.100.70 / Stage 60.60 /
    Amendments: ISO/IEC 27001:2022/Amd 1:2024 / Previously withdrawn ISO/IEC 27001:2013 (+Cor 1:2014, Cor 2:2015)）
  * ISO/IEC 27001:2022/Amd 1:2024 = id 88435（https://committee.iso.org/standard/88435.html ；
    Published / 2024-02 / “Climate action changes”）
  * ISO/IEC 27701:2025 = id 85819（https://committee.iso.org/standard/85819.html ；Published / 2025-10 /
    Edition 2 / 64 pages / ICS 35.030 / 前版 ISO/IEC 27701:2019 Withdrawn）
  * iso.org 對 WebFetch 回 client_error（疑似擋爬），committee.iso.org 可讀 → 兩者皆列為候選 URL。
[BSI]
  * BS 10012:2017+A1:2018：
    https://knowledge.bsigroup.com/products/data-protection-specification-for-a-personal-information-management-system
    （頁面顯示「Withdrawn」、「Published: 31 Jul 2018」、「Withdrawn: 24 Apr 2026」、ISBN 978 0 539 01173 9、
     Committee IDT/1、ICS 01.140.30 / 03.100.99 / 35.020；“BS 10012:2017+A1:2018 supersedes BS 10012:2017,
     which is withdrawn.”）
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from urllib.parse import quote

import lxml.html

from ..models import (
    ANTI_BOT_FAIL_NOTE,
    NA,
    OFFICIAL_API,
    STATUS_CURRENT,
    Article,
    LawDocument,
    SeedEntry,
)

SOURCE_NAME = "EUR-Lex／ISO／BSI 國際法規與標準"

EURLEX_HTML_FMT = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:{celex}"
EURLEX_TXT_FMT = "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}"
SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
EURLEX_PROCEDURE_FMT = "https://eur-lex.europa.eu/procedure/EN/{year}_{num}"
OEIL_FMT = "https://oeil.europarl.europa.eu/oeil/en/procedure-file?reference={ref}"
ISO_URL_FMTS = ("https://www.iso.org/standard/{id}.html", "https://committee.iso.org/standard/{id}.html")
BSI_PRODUCT_FMT = "https://knowledge.bsigroup.com/products/{slug}"

# 查證過的 consolidated 版本（SPARQL 與 EUR-Lex 頁面都失敗時才使用，並於 fetch_note 註明）
KNOWN_CONSOLIDATED = {"32016R0679": ["02016R0679-20160504"]}


# ═══════════════════════════ 共用：HTML → 文字行 ═══════════════════════════
_BLOCK = {
    "p", "div", "tr", "td", "th", "table", "tbody", "thead", "tfoot", "li", "ul", "ol", "dl", "dt", "dd",
    "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr", "section", "article", "header", "footer", "nav",
    "main", "aside", "form", "caption", "blockquote", "pre", "figure", "figcaption", "col", "colgroup",
}
_SKIP = {"script", "style", "noscript", "template", "head", "svg", "button", "select", "option"}
_WS = re.compile(r"[\s  -​ 　]+")
# EUR-Lex consolidated 文件的編輯標記（▼B、▼C1、►M2 … ◄）：非法律文字，移除以免影響比對
_MARKER = re.compile(r"[▼►]\s*[A-Z]{1,2}\d*\s*|◄")
_LABEL_ONLY = re.compile(r"^(?:\(\s*[0-9]{1,3}[a-z]?\s*\)|\([a-z]{1,2}\)|\([ivxlc]{1,6}\)|\d{1,3}[a-z]?\.|[a-z]\)|[—–])$")


def _clean(s: str) -> str:
    return _WS.sub(" ", s).strip()


def html_lines(html: str | bytes, *, merge_labels: bool = True) -> list[str]:
    """把 HTML 依區塊元素切成文字行；單獨成行的編號（1.、(a)、(12)）與下一行合併。"""
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    if not html.strip():
        return []
    root = lxml.html.fromstring(html)
    out: list[str] = []
    buf: list[str] = []

    def flush():
        s = _clean(_MARKER.sub("", "".join(buf)))
        buf.clear()
        if s:
            out.append(s)

    def walk(el):
        if not isinstance(el.tag, str):        # comment / PI：內容不取，tail 由父層處理
            return
        tag = el.tag.lower()
        if tag in _SKIP:
            return
        block = tag in _BLOCK
        if block:
            flush()
        if el.text:
            buf.append(el.text)
        for ch in el:
            walk(ch)
            if ch.tail:
                buf.append(ch.tail)
        if block:
            flush()

    walk(root)
    flush()
    if not merge_labels:
        return out
    merged: list[str] = []
    i = 0
    while i < len(out):
        cur = out[i]
        if _LABEL_ONLY.match(cur) and i + 1 < len(out) and not _LABEL_ONLY.match(out[i + 1]):
            i += 1
            cur = f"{cur} {out[i]}"
        merged.append(cur)
        i += 1
    return merged


def html_title(html: str | bytes) -> str:
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    try:
        root = lxml.html.fromstring(html)
    except Exception:  # noqa: BLE001
        return ""
    t = root.findtext(".//title") or ""
    if not t:
        m = root.xpath('//meta[@property="og:title"]/@content')
        t = m[0] if m else ""
    return _clean(t)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _note_from(res) -> str:
    """把 FetchResult 的 note（含 Wayback 時間戳記）轉為 fetch_note 片段。"""
    return res.note or ""


def _join_notes(*parts: str) -> str:
    return "；".join(p for p in parts if p)


_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], 1)}


def _parse_date(s: str) -> date | None:
    """僅用於比較先後；輸出一律保留原文。支援 4.5.2016、04/05/2016、2022-10、31 Jul 2018、25 May 2018。"""
    s = s.strip()
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    m = re.fullmatch(r"(\d{1,2}) ([A-Za-z]+) (\d{4})", s)
    if m:
        mon = next((v for k, v in _MONTHS.items() if k.startswith(m.group(2).lower()[:3])), None)
        if mon:
            return date(int(m.group(3)), mon, int(m.group(1)))
    return None


def _latest(dates: list[str]) -> str:
    best, best_d = "", None
    for s in dates:
        d = _parse_date(s)
        if d and (best_d is None or d > best_d):
            best, best_d = s, d
    return best


def _fail_doc(entry: SeedEntry, url: str, note: str, **kw) -> LawDocument:
    return LawDocument(
        name=entry.name, source_name=SOURCE_NAME, source_url=url, status=entry.status or STATUS_CURRENT,
        doc_type=entry.doc_type, metadata_only=entry.metadata_only, fetch_note=note, fetched_at=_now(),
        category=entry.category, parent_law=entry.parent_law, **kw)


# ═══════════════════════════ GDPR / EUR-Lex ═══════════════════════════
_ART_HEAD = re.compile(r"^Article\s+(\d+[a-z]?)$")
_CHAPTER = re.compile(r"^CHAPTER\s+[IVXLC]+$")
_SECTION = re.compile(r"^Section\s+\d+$")
_FOOTNOTE = re.compile(r"^\(\s+\d+\s+\)\s")          # consolidated 註腳「( 1 ) Directive …」
_DATE_DOT = r"\d{1,2}\.\d{1,2}\.\d{4}"


def _is_title(line: str) -> bool:
    return (len(line) < 250 and not re.match(r"^(\(\w{1,6}\)|\d{1,3}\.)\s", line)
            and not line.endswith((".", ":", ";", ",")) and not _ART_HEAD.match(line))


def parse_eurlex_act(html: str | bytes, celex: str = "") -> dict:
    """解析 EUR-Lex HTML（原始 OJ 版或 consolidated 版）。回傳 dict：
    articles, titles, promulgated_date, amended_date, effective_date, metadata。"""
    lines = html_lines(html)
    joined = " ".join(lines)
    meta: dict = {}
    if celex:
        meta["CELEX"] = celex

    # ── 區分前言 / 條文 ──
    first_art = next((i for i, l in enumerate(lines) if _ART_HEAD.match(l)), None)
    if first_art is None:
        return {"articles": [], "titles": {}, "promulgated_date": NA, "amended_date": NA,
                "effective_date": NA, "metadata": meta}
    head_end = first_art
    for j in range(first_art - 1, max(first_art - 6, -1), -1):   # 往前包含 CHAPTER I / 標題
        if _CHAPTER.match(lines[j]):
            head_end = j
            break
    head = lines[:head_end]
    head_joined = " ".join(head)

    # ── consolidated 表頭：02016R0679 — EN — 04.05.2016 — 000.002 ──
    cons_date = ""
    m = re.search(r"\b(0\d{4}[A-Z]\d{4})\s*[—–-]\s*EN\s*[—–-]\s*(\d{2}\.\d{2}\.\d{4})\s*[—–-]\s*(\d{3}\.\d{3})",
                  head_joined)
    if m:
        cons_date = m.group(2)
        meta["consolidated 版本"] = f"{m.group(1)} — {m.group(2)} — {m.group(3)}"
    elif re.match(r"^0\d{4}[A-Z]\d{4}-(\d{8})$", celex or ""):
        d = celex.split("-")[1]
        cons_date = f"{d[6:8]}.{d[4:6]}.{d[0:4]}"
        meta["consolidated 版本"] = celex

    # ── OJ 刊登（公布）日 ──
    promulgated = ""
    m = re.search(r"\(OJ\s+L\s*(\d+),?\s+(" + _DATE_DOT + r"),\s*p\.\s*\d+\)", head_joined)
    if m:                                     # consolidated：「(OJ L 119 4.5.2016, p. 1)」
        promulgated = m.group(2)
        meta["OJ 出處"] = m.group(0).strip("()")
    else:                                     # 原始 OJ：「4.5.2016 EN Official Journal of the European Union L 119/1」
        m = re.search(r"(" + _DATE_DOT + r")\s+EN\s+Official Journal of the European Union\s+(L\s*\d+/\d+)",
                      head_joined)
        if m:
            promulgated = m.group(1)
            meta["OJ 出處"] = f"OJ {m.group(2)}, {m.group(1)}"
    m = re.search(r"\bof (\d{1,2} [A-Z][a-z]+ \d{4})\b", head_joined)
    if m:
        meta["簽署日（act date）"] = m.group(1)
    title_line = next((l for l in head if re.match(r"^(REGULATION|DIRECTIVE|DECISION)\b", l)), "")
    if title_line:
        meta["標題"] = title_line

    # ── Amended by / Corrected by（consolidated 表頭）──
    amend_dates: list[str] = []
    for label, key in (("Amended by:", "Amended by"), ("Corrected by:", "Corrected by")):
        if label in head:
            idx = head.index(label)
            items = []
            for l in head[idx + 1:]:
                if l in ("Amended by:", "Corrected by:") or re.match(r"^(REGULATION|DIRECTIVE|DECISION)\b", l):
                    break
                items.append(l)
                amend_dates += re.findall(_DATE_DOT, l)
            meta[key] = " | ".join(items) if items else NA

    # ── Recitals：只計數，不納入條文 ──
    if "Whereas:" in head:
        nums = [int(x) for l in head[head.index("Whereas:") + 1:]
                for x in re.findall(r"^\((\d{1,3})\)\s", l)]
        meta["recitals 數量"] = str(max(nums)) if nums else NA
    else:
        meta["recitals 數量"] = "NA（本版本未含 recitals）"

    # ── 條文 ──
    articles: list[Article] = []
    titles: dict[str, str] = {}
    chapter = section = ""
    cur: Article | None = None
    body: list[str] = []
    i = head_end
    tail_lines: list[str] = []

    def close():
        if cur is not None:
            cur.text = "\n".join(body)
            articles.append(cur)

    while i < len(lines):
        l = lines[i]
        if l.startswith("Done at ") or l.startswith("This Regulation shall be binding in its entirety") \
                or l.startswith("This Directive is addressed") or _FOOTNOTE.match(l):
            tail_lines = lines[i:i + 3]
            break
        if _CHAPTER.match(l):
            t = lines[i + 1] if i + 1 < len(lines) and _is_title(lines[i + 1]) else ""
            chapter, section = _clean(f"{l} {t}"), ""
            i += 2 if t else 1
            continue
        if _SECTION.match(l):
            t = lines[i + 1] if i + 1 < len(lines) and _is_title(lines[i + 1]) else ""
            section = _clean(f"{l} {t}")
            i += 2 if t else 1
            continue
        m = _ART_HEAD.match(l)
        if m:
            close()
            body = []
            cur = Article(no=l, text="", chapter=" / ".join(x for x in (chapter, section) if x))
            if i + 1 < len(lines) and _is_title(lines[i + 1]):
                titles[l] = lines[i + 1]
                i += 2
            else:
                i += 1
            continue
        if cur is not None:
            body.append(l)
        i += 1
    close()
    for t in tail_lines:
        if t.startswith("Done at "):
            meta["Done at"] = t

    # ── 適用日 / 生效：從「Entry into force…」條或任何含 shall apply from 的條文解析 ──
    effective = ""
    cands = [a for a in articles if "entry into force" in titles.get(a.no, "").lower()] or articles
    for a in reversed(cands):
        m = re.search(r"shall apply from (\d{1,2} [A-Z][a-z]+ \d{4})", a.text)
        if m:
            effective = m.group(1)
            meta["適用日條文"] = a.no
            ef = next((p for p in a.text.split("\n") if "enter into force" in p), "")
            if ef:
                meta["生效規定"] = ef
            break

    amended = _latest(([cons_date] if cons_date else []) + amend_dates)
    meta["條文數"] = str(len(articles))
    return {"articles": articles, "titles": titles,
            "promulgated_date": promulgated or NA, "amended_date": amended or NA,
            "effective_date": effective or NA, "metadata": meta}


def _celex_sort_key(c: str) -> str:
    return c.split("-")[1] if "-" in c else "00000000"


def sparql_consolidated_url(base_celex: str) -> str:
    """CELLAR SPARQL：列出 base CELEX 的所有 consolidated CELEX（0YYYYTNNNN-YYYYMMDD）。"""
    cons_prefix = "0" + base_celex[1:] + "-"
    q = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
         "SELECT DISTINCT ?celex WHERE { ?w cdm:resource_legal_id_celex ?celex . "
         f'FILTER(STRSTARTS(STR(?celex), "{cons_prefix}")) }}')
    return f"{SPARQL_ENDPOINT}?query={quote(q)}&format={quote('application/sparql-results+json')}"


def parse_sparql_celex(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for b in data.get("results", {}).get("bindings", []):
        v = b.get("celex", {}).get("value", "")
        if re.fullmatch(r"0\d{4}[A-Z]\d{4}-\d{8}", v):
            out.append(v)
    return sorted(set(out), key=_celex_sort_key)


def list_consolidated(base_celex: str, fetcher, notes: list[str]) -> list[str]:
    """依序：CELLAR SPARQL（official_api）→ EUR-Lex 文件頁連結 → 已查證清單。"""
    res = fetcher.get(sparql_consolidated_url(base_celex), method_hint=OFFICIAL_API, expect="json",
                      wayback_ok=False)
    if res.ok:
        found = parse_sparql_celex(res.text)
        if found:
            return found
        notes.append("SPARQL 未回傳 consolidated 版本")
    else:
        notes.append("SPARQL 端點無法取得")
    cons_prefix = "0" + base_celex[1:]
    res = fetcher.get(EURLEX_TXT_FMT.format(celex=base_celex), expect="html")
    if res.ok:
        found = sorted(set(re.findall(re.escape(cons_prefix) + r"-\d{8}", res.text)), key=_celex_sort_key)
        if found:
            return found
    known = KNOWN_CONSOLIDATED.get(base_celex, [])
    if known:
        notes.append(f"consolidated 版本清單改用已查證清單 {', '.join(known)}（請人工確認是否有更新版本）")
    return list(known)


def _build_eurlex_doc(entry: SeedEntry, celex: str, html_text: str, res, label: str) -> LawDocument:
    p = parse_eurlex_act(html_text, celex)
    meta = p["metadata"]
    if p["titles"]:
        meta["條文標題"] = p["titles"]
    return LawDocument(
        name=entry.name, source_name=SOURCE_NAME, source_url=EURLEX_HTML_FMT.format(celex=celex),
        status=entry.status or STATUS_CURRENT, doc_type=entry.doc_type,
        promulgated_date=p["promulgated_date"], amended_date=p["amended_date"],
        effective_date=p["effective_date"], articles=p["articles"], metadata=meta,
        fetch_method=res.method_used, fetch_note=_note_from(res), fetched_at=_now(),
        category=entry.category, parent_law=entry.parent_law, version_label=label,
        history="；".join(x for x in (meta.get("OJ 出處", ""), meta.get("Corrected by", ""),
                                     meta.get("Amended by", "")) if x and x != NA),
    )


def _fetch_eurlex(entry: SeedEntry, celex: str, fetcher, label: str) -> LawDocument | None:
    url = EURLEX_HTML_FMT.format(celex=celex)
    res = fetcher.get(url, expect="html")
    if not res.ok:
        return _fail_doc(entry, url, res.note or ANTI_BOT_FAIL_NOTE)
    doc = _build_eurlex_doc(entry, celex, res.text, res, label)
    if not doc.articles:
        doc.fetch_note = _join_notes(doc.fetch_note, "已取得頁面但解析不到任何 Article（可能是 WAF 驗證頁或版面變更）")
    return doc


def fetch_gdpr(entry: SeedEntry, fetcher) -> LawDocument:
    base = entry.ids.get("celex", "32016R0679")
    notes: list[str] = []
    cons = list_consolidated(base, fetcher, notes)
    if cons:
        latest = cons[-1]
        doc = _fetch_eurlex(entry, latest, fetcher, f"consolidated {latest}")
    else:
        notes.append("查無 consolidated 版本，改抓原始 OJ 版本")
        doc = _fetch_eurlex(entry, base, fetcher, f"OJ {base}")
    doc.metadata["consolidated 版本清單"] = cons
    doc.fetch_note = _join_notes(doc.fetch_note, *notes)
    return doc


def fetch_previous_gdpr(entry: SeedEntry, current: LawDocument, fetcher) -> LawDocument | None:
    base = entry.ids.get("celex", "32016R0679")
    cons = list(current.metadata.get("consolidated 版本清單") or [])
    cur_celex = current.metadata.get("CELEX", "")
    prev = ""
    if cur_celex in cons and cons.index(cur_celex) > 0:
        prev = cons[cons.index(cur_celex) - 1]
    elif cur_celex != base:
        prev = base                              # 只有一個 consolidated 版本 → 上一版為原始 OJ 版本
    if not prev:
        current.fetch_note = _join_notes(current.fetch_note, "無上一版本可抓（目前即為原始 OJ 版本）")
        return None
    label = f"consolidated {prev}" if prev != base else f"OJ {base}"
    doc = _fetch_eurlex(entry, prev, fetcher, label)
    if doc is None or not doc.articles:
        current.fetch_note = _join_notes(current.fetch_note, f"上一版本 {prev} 抓取失敗：{doc.fetch_note if doc else ''}")
        return None
    return doc


# ═══════════════════════════ Digital Omnibus 立法進度 ═══════════════════════════
_DMY = re.compile(r"^(\d{2}/\d{2}/\d{4})\b\s*(.*)$")
_FINAL_ACT = re.compile(r"\b(Regulation|Directive)\s+(?:\(EU\)\s+)?(20\d{2}/\d{1,5})\b")


def parse_procedure_page(html: str | bytes) -> dict:
    """EUR-Lex procedure 頁或 OEIL procedure file：抽出狀態與事件（日期 DD/MM/YYYY）。"""
    lines = html_lines(html, merge_labels=False)
    out: dict = {"events": []}
    for i, l in enumerate(lines):
        if l in ("Stage reached in procedure", "Status") and i + 1 < len(lines) and "status" not in out:
            out["status"] = lines[i + 1]
        m = re.match(r"^(Stage reached in procedure|Status)\s*:?\s+(.+)$", l)
        if m and "status" not in out:
            out["status"] = m.group(2)
        m = _DMY.match(l)
        if m:
            ev = m.group(2).strip(" :-–")
            if not ev and i + 1 < len(lines) and not _DMY.match(lines[i + 1]):
                ev = lines[i + 1]
            if ev:
                ctx = " ".join(lines[i:i + 4])
                out["events"].append({"date": m.group(1), "event": ev, "context": ctx})
    # 正式法規編號：只在「Final act / Adopted act / Official Journal」附近找
    for i, l in enumerate(lines):
        if re.search(r"Final act|Adopted act|published in (the )?Official Journal", l, re.I):
            ctx = " ".join(lines[i:i + 4])
            m = _FINAL_ACT.search(ctx)
            if m and int(m.group(2)[:4]) >= 2026:
                out["final_act"] = f"{m.group(1)} (EU) {m.group(2)}" if "(EU)" in m.group(0) else m.group(0)
            if re.search(r"published in (the )?Official Journal", l, re.I):
                out["oj_published"] = True
    return out


def fetch_omnibus(entry: SeedEntry, fetcher) -> LawDocument:
    proc = entry.ids.get("procedure", "2025/0360(COD)")
    m = re.match(r"(\d{4})/0*(\d+)\(", proc)
    urls = []
    if m:
        urls.append(("EUR-Lex procedure", EURLEX_PROCEDURE_FMT.format(year=m.group(1), num=m.group(2))))
    urls.append(("EP Legislative Observatory", OEIL_FMT.format(ref=quote(proc, safe=""))))
    meta: dict = {"程序編號": proc}
    notes, methods, events, src_url = [], [], [], ""
    oj, final_act = False, ""
    for label, url in urls:
        res = fetcher.get(url, expect="html")
        if not res.ok:
            notes.append(f"{label}：{res.note or ANTI_BOT_FAIL_NOTE}")
            continue
        p = parse_procedure_page(res.text)
        src_url = src_url or url
        methods.append(res.method_used)
        if res.note:
            notes.append(f"{label}：{res.note}")
        meta[f"{label} 狀態"] = p.get("status", NA)
        for e in p["events"]:
            events.append({**e, "來源": label})
        oj = oj or p.get("oj_published", False)
        final_act = final_act or p.get("final_act", "")
    if not methods:
        return _fail_doc(entry, urls[0][1], "；".join(notes) or ANTI_BOT_FAIL_NOTE)
    # 去重、排序
    seen, uniq = set(), []
    for e in events:
        k = (e["date"], e["event"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    uniq.sort(key=lambda e: _parse_date(e["date"]) or date.min)
    latest = uniq[-1] if uniq else None
    meta["最新事件日期"] = latest["date"] if latest else NA
    meta["最新事件"] = latest["event"] if latest else NA
    meta["事件列表"] = " | ".join(f"{e['date']} {e['event']}" for e in uniq) or NA
    meta["已刊登OJ"] = "是" if oj else "否"
    meta["正式編號"] = final_act or NA
    proposal = next((e["date"] for e in uniq if re.search(r"proposal", e["event"], re.I)), NA)
    if oj or final_act:
        notes.append(f"偵測到已通過／刊登 OJ（{final_act or '編號待確認'}）：請主控將 status 改為「現行」並新增正式法規條目")
    return LawDocument(
        name=entry.name, source_name=SOURCE_NAME, source_url=src_url, status=entry.status,
        doc_type=entry.doc_type, promulgated_date=proposal, amended_date=meta["最新事件日期"],
        effective_date=NA, metadata=meta, metadata_only=True, fetch_method=methods[0],
        fetch_note="；".join(notes), fetched_at=_now(), category=entry.category,
        parent_law=entry.parent_law, version_label=f"{proc} @ {meta['最新事件日期']}",
    )


# ═══════════════════════════ ISO 標準（只抓後設資料） ═══════════════════════════
_ISO_LABELS = ("Status", "Publication date", "Stage", "Edition", "Number of pages", "Technical Committee",
               "ICS", "Previously", "Now", "Will be replaced by", "Amendments", "Corrigenda", "Revised by",
               "Withdrawn", "Under development")
_ICS = re.compile(r"\b\d{2}\.\d{3}(?:\.\d{2})?\b")


def _label_value(lines: list[str], label: str) -> str:
    for i, l in enumerate(lines):
        m = re.match(rf"^{re.escape(label)}\s*:\s*(.+)$", l, re.I)
        if m:
            return m.group(1).strip()
        if l.rstrip(" :").lower() == label.lower():
            for v in lines[i + 1:i + 4]:
                if v.strip(" :"):
                    return v.strip(" :")
    return ""


def _ref_pattern(ref: str) -> tuple[str, str]:
    """'ISO/IEC 27001:2022' → ('ISO/IEC', '27001')"""
    m = re.match(r"^(ISO(?:/IEC)?)\s+(\d+(?:-\d+)?)", ref)
    return (m.group(1), m.group(2)) if m else ("ISO", "")


def parse_iso_page(html: str | bytes, expected_ref: str, known_amendments: list[str] | None = None) -> dict:
    lines = html_lines(html, merge_labels=False)
    joined = " ".join(lines)
    org, num = _ref_pattern(expected_ref)
    meta: dict = {}
    ref_re = re.compile(rf"{re.escape(org)}\s+(?:(?:PWI|NP|AWI|WD|CD|DIS|FDIS)\s+)?{re.escape(num)}"
                        rf"(?::\d{{4}})?(?:/(?:Amd|Cor|DAmd|CD Amd|AWI Amd|FDAmd)\s*\d+(?::\d{{4}})?)?")
    refs = []
    for r in ref_re.findall(joined):
        r = _clean(r)
        if r not in refs:
            refs.append(r)
    # 本頁標準編號：第一個「完整版次」且不含 /Amd /Cor 的行
    page_ref = next((l for l in lines if re.fullmatch(rf"{re.escape(org)}\s+{re.escape(num)}:\d{{4}}", l)), "")
    if not page_ref:
        page_ref = next((r for r in refs if re.fullmatch(rf"{re.escape(org)}\s+{re.escape(num)}:\d{{4}}", r)), "")
    meta["標準編號"] = page_ref or NA
    title = html_title(html)
    if title and page_ref and title.startswith(page_ref):
        title = title[len(page_ref):].lstrip(" -–—|")
    elif page_ref and page_ref in lines:
        idx = lines.index(page_ref)
        title = " ".join(lines[idx + 1:idx + 3]) if idx + 1 < len(lines) else ""
    meta["標題"] = title or NA
    meta["版次 edition"] = _label_value(lines, "Edition") or NA
    meta["發布日期"] = _label_value(lines, "Publication date") or NA
    meta["狀態"] = _label_value(lines, "Status") or NA
    meta["頁數"] = _label_value(lines, "Number of pages") or NA
    meta["技術委員會"] = _label_value(lines, "Technical Committee") or NA
    stage = _label_value(lines, "Stage")
    sm = re.search(r"\b\d{2}\.\d{2}\b", stage or "")
    meta["Stage"] = stage or NA
    # ICS：從 ICS 標籤後收集代碼，直到下一個已知標籤
    ics = []
    for i, l in enumerate(lines):
        if l.rstrip(" :") == "ICS" or l.startswith("ICS :") or l.startswith("ICS:"):
            for v in [l] + lines[i + 1:i + 12]:
                if v is not l and v.rstrip(" :") in _ISO_LABELS:
                    break
                ics += [c for c in _ICS.findall(v) if c not in ics]
            break
    meta["ICS"] = ", ".join(ics) or NA
    cur_year = int(page_ref.split(":")[1]) if page_ref else 0
    amds = [r for r in refs if page_ref and r.startswith(page_ref + "/Amd")]
    cors = [r for r in refs if page_ref and r.startswith(page_ref + "/Cor")]
    older = [r for r in refs if re.search(r":(\d{4})", r) and int(re.search(r":(\d{4})", r).group(1)) < cur_year]
    newer = [r for r in refs if r not in amds and r not in cors and r != page_ref and r not in older
             and not r.startswith(page_ref + "/")]
    meta["Amendments"] = ", ".join(amds) or NA
    if cors:
        meta["Corrigenda"] = ", ".join(cors)
    meta["取代（前版）"] = ", ".join(older) or NA
    replaced = _label_value(lines, "Will be replaced by")
    meta["被取代（新版）"] = ", ".join(newer) or (replaced or NA)
    detect = []
    if page_ref and page_ref != expected_ref:
        detect.append(f"頁面標準編號為 {page_ref}（預期 {expected_ref}）")
    if newer:
        detect.append("新版／修訂中：" + ", ".join(newer))
    if replaced:
        detect.append(f"Will be replaced by {replaced}")
    known = set(known_amendments or [])
    new_amd = [a for a in amds if a not in known]
    if new_amd:
        detect.append("新 Amendment：" + ", ".join(new_amd))
    if detect:
        meta["新版本偵測"] = "；".join(detect)
    if meta["狀態"] != NA and sm is None and meta["Stage"] == NA:
        meta.pop("Stage")
    return meta


def fetch_iso(entry: SeedEntry, fetcher) -> LawDocument:
    iso_id = str(entry.ids.get("iso_id", ""))
    ref = entry.ids.get("reference", entry.name)
    known = list(entry.ids.get("known_amendments", []) or [])
    if not iso_id:
        return _fail_doc(entry, entry.url, "seed 未設定 ids.iso_id，無法定位 ISO 官方頁面")
    notes, res, url = [], None, ""
    for fmt in ISO_URL_FMTS:
        url = fmt.format(id=iso_id)
        res = fetcher.get(url, expect="html")
        if res.ok:
            break
        notes.append(f"{url}：{res.note or ANTI_BOT_FAIL_NOTE}")
    if not res or not res.ok:
        return _fail_doc(entry, url, ANTI_BOT_FAIL_NOTE if all(ANTI_BOT_FAIL_NOTE in n for n in notes)
                         else "；".join(notes))
    meta = parse_iso_page(res.text, ref, known)
    meta["ISO id"] = iso_id
    if res.note:
        notes.append(res.note)
    # Amendment 頁（若 seed 提供 id）：補狀態與發布日期
    amd_dates = []
    amd_info = []
    for amd_ref, amd_id in (entry.ids.get("amendment_ids") or {}).items():
        ar = None
        for fmt in ISO_URL_FMTS:
            ar = fetcher.get(fmt.format(id=amd_id), expect="html")
            if ar.ok:
                break
        if ar is None or not ar.ok:
            amd_info.append(f"{amd_ref}（NA：頁面無法取得）")
            continue
        al = html_lines(ar.text, merge_labels=False)
        d = _label_value(al, "Publication date") or NA
        st = _label_value(al, "Status") or NA
        amd_info.append(f"{amd_ref}（{st}, {d}）")
        if d != NA:
            amd_dates.append(d)
    if amd_info:
        meta["Amendments 明細"] = " | ".join(amd_info)
    if meta.get("標準編號") == NA:
        notes.append("頁面上找不到標準編號（可能為擋爬頁或版面變更）")
    return LawDocument(
        name=entry.name, source_name=SOURCE_NAME, source_url=url, status=entry.status or STATUS_CURRENT,
        doc_type=entry.doc_type, promulgated_date=meta.get("發布日期", NA),
        amended_date=_latest(amd_dates) or NA, effective_date=NA, metadata=meta, metadata_only=True,
        fetch_method=res.method_used, fetch_note="；".join(notes), fetched_at=_now(),
        category=entry.category, parent_law=entry.parent_law,
        version_label=meta.get("標準編號") if meta.get("標準編號") != NA else ref,
    )


# ═══════════════════════════ BSI Knowledge（只抓後設資料） ═══════════════════════════
_BSI_STATUS = ("Current", "Withdrawn", "Superseded", "Confirmed", "Revised", "Draft for public comment",
               "Work in hand", "Proposed for withdrawal")
_BSI_LABELS = ("Published", "Withdrawn", "ISBN", "Committee", "ICS Codes", "Publisher", "Pages",
               "Supersedes", "Superseded by", "Replaced by", "Replaces", "Amended by", "Amends",
               "International relationships", "Descriptors", "Format", "Status")


def parse_bsi_page(html: str | bytes, expected_ref: str) -> dict:
    lines = html_lines(html, merge_labels=False)
    meta: dict = {}
    num = re.match(r"^BS\s+(\d+)", expected_ref)
    ref_re = re.compile(rf"^BS\s+{num.group(1) if num else ''}:\d{{4}}(?:\+A\d+:\d{{4}})*$")
    page_ref = next((l for l in lines if ref_re.match(l)), "")
    meta["標準編號"] = page_ref or NA
    idx = lines.index(page_ref) if page_ref else -1
    meta["標題"] = lines[idx + 1] if idx >= 0 and idx + 1 < len(lines) else NA

    def val(label: str, bare: bool = False) -> str:
        names = (f"{label}:", label + " :") + ((label,) if bare else ())
        for i, l in enumerate(lines):
            if l.rstrip() in names:
                return lines[i + 1] if i + 1 < len(lines) else ""
            m = re.match(rf"^{re.escape(label)}:\s+(.+)$", l)
            if m:
                return m.group(1)
        return ""

    # 狀態：頁首單獨一行的狀態字（例如「Withdrawn」，無冒號）；或「Status: …」
    status = val("Status")
    if not status:
        status = next((l for l in lines[:max(idx, 0) + 15] if l in _BSI_STATUS), "")
    meta["狀態"] = status or NA
    meta["發布日期"] = val("Published") or NA
    w = val("Withdrawn")
    if w:
        meta["撤銷日期"] = w
    for lab, key in (("ISBN", "ISBN"), ("Committee", "技術委員會"), ("Publisher", "Publisher"),
                     ("Supersedes", "取代（前版）"), ("Replaced by", "被取代（新版）"),
                     ("Superseded by", "被取代（新版）"), ("Amended by", "Amendments")):
        v = val(lab, bare=True)
        if v:
            meta[key] = v
    # ICS Codes：標籤之後的代碼行
    for i, l in enumerate(lines):
        if l.rstrip(" :") == "ICS Codes":
            codes = []
            for v in lines[i + 1:i + 10]:
                if v.rstrip(" :") in _BSI_LABELS:
                    break
                if _ICS.match(v):
                    codes.append(v)
            meta["ICS"] = " | ".join(codes) or NA
            break
    sup = next((l for l in lines if re.search(r"\bsupersedes\b", l) and l.startswith("BS ")), "")
    if sup:
        meta["版本說明"] = sup
        meta.setdefault("取代（前版）", sup)
    if page_ref and page_ref != expected_ref:
        meta["新版本偵測"] = f"頁面標準編號為 {page_ref}（預期 {expected_ref}）"
    return meta


def fetch_bsi(entry: SeedEntry, fetcher) -> LawDocument:
    url = entry.url or BSI_PRODUCT_FMT.format(slug=entry.ids.get("bsi_slug", ""))
    ref = entry.ids.get("reference", entry.name)
    if not url or url.endswith("/products/"):
        return _fail_doc(entry, url, "seed 未設定 BSI 官方 URL；狀態 NA",
                         metadata={"標準編號": ref, "狀態": NA})
    res = fetcher.get(url, expect="html")
    if not res.ok:
        return _fail_doc(entry, url, _join_notes(res.note or ANTI_BOT_FAIL_NOTE,
                                                 "無法取得 BSI 官方頁面，狀態以 NA 記錄（經銷商標示不採用）"),
                         metadata={"標準編號": ref, "狀態": NA})
    meta = parse_bsi_page(res.text, ref)
    notes = [res.note] if res.note else []
    if meta["狀態"] == NA:
        notes.append("BSI 官方頁面未解析出狀態，狀態以 NA 記錄（經銷商標示不採用）")
    elif meta["狀態"] in ("Withdrawn", "Superseded"):
        notes.append(f"BSI 官方狀態為 {meta['狀態']}"
                     + (f"（撤銷日期 {meta['撤銷日期']}）" if meta.get("撤銷日期") else "")
                     + "；models 的 status 只允許 現行/草案/提案，暫保留 seed 設定，請主控決定是否調整")
    return LawDocument(
        name=entry.name, source_name=SOURCE_NAME, source_url=url, status=entry.status or STATUS_CURRENT,
        doc_type=entry.doc_type, promulgated_date=meta.get("發布日期", NA), amended_date=NA,
        effective_date=NA, metadata=meta, metadata_only=True, fetch_method=res.method_used,
        fetch_note="；".join(notes), fetched_at=_now(), category=entry.category,
        parent_law=entry.parent_law,
        version_label=meta["標準編號"] if meta["標準編號"] != NA else ref,
    )


# ═══════════════════════════ 介面（SPEC §3） ═══════════════════════════
def _kind(entry: SeedEntry) -> str:
    k = entry.ids.get("kind", "")
    if k:
        return k
    if entry.ids.get("celex"):
        return "eurlex"
    if entry.ids.get("procedure"):
        return "procedure"
    if entry.name.startswith("ISO"):
        return "iso"
    if entry.name.startswith("BS "):
        return "bsi"
    return ""


def fetch(entries: list[SeedEntry], fetcher) -> list[LawDocument]:
    out: list[LawDocument] = []
    for e in entries:
        try:
            k = _kind(e)
            if k == "eurlex":
                out.append(fetch_gdpr(e, fetcher))
            elif k == "procedure":
                out.append(fetch_omnibus(e, fetcher))
            elif k == "iso":
                out.append(fetch_iso(e, fetcher))
            elif k == "bsi":
                out.append(fetch_bsi(e, fetcher))
            else:
                out.append(_fail_doc(e, e.url, "intl 模組無法判斷來源類型（ids 缺 celex/procedure/iso_id）"))
        except Exception as ex:  # noqa: BLE001 — 單筆失敗不可拖垮整批
            out.append(_fail_doc(e, e.url, f"intl 解析例外：{type(ex).__name__}: {ex}"))
    return out


def fetch_previous(entry: SeedEntry, current: LawDocument, fetcher) -> LawDocument | None:
    """GDPR：上一個 consolidated 版本或原始 OJ 版本。後設資料型（ISO/BSI/提案）無「上一版全文」。"""
    if _kind(entry) == "eurlex":
        try:
            return fetch_previous_gdpr(entry, current, fetcher)
        except Exception as ex:  # noqa: BLE001
            current.fetch_note = _join_notes(current.fetch_note, f"上一版本抓取例外：{type(ex).__name__}: {ex}")
            return None
    current.fetch_note = _join_notes(current.fetch_note, "後設資料型條目無上一版全文，基準線以本次後設資料為準")
    return None
