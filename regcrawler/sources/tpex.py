"""#2 財團法人中華民國證券櫃檯買賣中心規章。

來源選擇（2026-11-27 查證）：
  - 櫃買中心官網 www.tpex.org.tw 在 WebFetch 一律回 client_error（含 /openapi/swagger.json），
    未能查證櫃買中心自有「法規查詢系統」之網址或 Open API 法規端點，故不猜測其 URL。
  - 櫃買中心官網法規頁明載「相關法規請至「證券暨期貨法令判解查詢系統」網站查詢」並連到
    https://www.selaw.com.tw/Home （查證頁：https://www.tpex.org.tw/zh-tw/bond/service/law.html）。
    該系統「由財團法人中華民國證券暨期貨市場發展基金會提供」，收錄櫃買中心規章（分類 8134–8143）。
    因此本模組以 selaw.com.tw 為櫃買中心規章之擷取來源。
  - 查證之 URL 樣式：
      規章（現行版）  https://www.selaw.com.tw/Chinese/RegulatoryInformationResult?sysNumber=LW10812093
      規章（指定版本）https://www.selaw.com.tw/Chinese/RegulatoryInformationResult?sysNumber=LW10812093&releaseDate=2019-03-12
      分類清單        https://www.selaw.com.tw/Chinese/LegalSystemInquiry/LawSystemQueryList/8135 （分頁為 JS，href="#"）
    頁面欄位：「法規名稱」（後綴「(現行法規)」或「(非現行法規)」）、「發佈日期」、「沿革資訊」。
    已知限制：現行版頁面預設只顯示「異動條文」區（所有條文／歷史法規分頁需 JS 互動載入，
    WebFetch 未見對應 href）。為避免把「只有異動條文」誤當全文（會造成大量假性「刪除條文」），
    偵測到僅異動條文時不輸出條文，並在 fetch_note 說明。
  - 「上市上櫃公司資通安全管控指引」由證交所與櫃買中心共同制定
    （https://wantrich.chinatimes.com/news/20211224900311-420101 ：「與證券交易所共同制定」），
    selaw 查無此指引；其官方全文以證交所法規分享知識庫（FLCODE=FL104387）取得，並於 fetch_note 註明。
"""
from __future__ import annotations

import re
from typing import Optional

from ..models import ANTI_BOT_FAIL_NOTE, NA, STATUS_CURRENT, Article, LawDocument, SeedEntry
from ..normalize import norm_text
from . import twse
from .fsc import (ROC_DATE_RE, effect_clause, fail_note, header_fields, is_ok, now_iso, soup_of,
                  split_articles, stop_index, text_lines)

SOURCE_NAME = "證券櫃檯買賣中心規章（證券暨期貨法令判解查詢系統）"
BASE = "https://www.selaw.com.tw"
LAW_URL = BASE + "/Chinese/RegulatoryInformationResult?sysNumber={sn}"
LAW_VER_URL = BASE + "/Chinese/RegulatoryInformationResult?sysNumber={sn}&releaseDate={d}"

# 已查證之 selaw sysNumber
KNOWN_SYSNUMBERS: dict[str, str] = {
    # https://www.selaw.com.tw/Chinese/RegulatoryInformationResult?sysNumber=LW10812093 （現行法規，發佈日期 民國115年9月30日）
    "財團法人中華民國證券櫃檯買賣中心對有價證券上櫃公司重大訊息之查證暨公開處理程序": "LW10812093",
}
# 與證交所共同制定、全文取自證交所法規分享知識庫者
JOINT_WITH_TWSE: dict[str, str] = {
    "上市上櫃公司資通安全管控指引": "FL104387",
}

LABELS = ("法規名稱", "發佈日期", "沿革資訊", "施行日期", "修正日期")
SECTION_CHANGED = "異動條文"
TAB_LINES = frozenset({"所有條文", "編章節", "條號查詢", "關鍵字查詢", "法規沿革", "歷史法規",
                       "附件下載", "英文版", "EN", "法規資訊"})
NAME_SUFFIX_RE = re.compile(r"\s*[（(](?:非)?現行法規[)）]\s*$")


def _resolve(entry: SeedEntry) -> str:
    for k in ("selaw_sysnumber", "sysNumber", "sysnumber"):
        if entry.ids.get(k):
            return str(entry.ids[k])
    return KNOWN_SYSNUMBERS.get(entry.name, "")


def parse_page(html: str | bytes) -> dict:
    """→ {"fields", "articles", "changed_only", "history", "current"}"""
    soup = soup_of(html)
    lines = text_lines(soup.body or soup)
    lines = lines[: stop_index(lines)]
    fields = header_fields(lines, LABELS)
    raw_name = fields.get("法規名稱", "")
    current = "非現行" not in raw_name
    fields["法規名稱"] = NAME_SUFFIX_RE.sub("", raw_name).strip()
    # 沿革資訊可能跨多行：取「沿革資訊」之後、到第一個條號或分頁標籤前的行
    hist_lines: list[str] = []
    for i, ln in enumerate(lines):
        if re.sub(r"[\s　]", "", ln).startswith("沿革資訊"):
            first = re.sub(r"^沿革資訊[\s　]*[:：]?", "", ln.strip()).strip()
            if first:
                hist_lines.append(first)
            for ln2 in lines[i + 1:]:
                if ln2 in TAB_LINES or ln2 == SECTION_CHANGED or re.match(r"^第\s*\d+\s*條", ln2):
                    break
                hist_lines.append(ln2)
            break
    body = [ln for ln in lines if ln not in TAB_LINES]
    changed_idx = [i for i, ln in enumerate(body) if ln == SECTION_CHANGED]
    arts = split_articles(body)
    history = "\n".join(hist_lines)
    changed_only = False
    if changed_idx and arts:
        # 頁面有「異動條文」區，且本次沿革不是「訂定／修正全文」→ 所見條文僅為異動條文，非全文
        changed_only = "全文" not in history
    return {"fields": fields, "articles": arts, "changed_only": changed_only,
            "history": history, "current": current}


def _empty(entry: SeedEntry, url: str, note: str, method: str = "") -> LawDocument:
    return LawDocument(name=entry.name, source_name=SOURCE_NAME, source_url=url,
                       status=entry.status or STATUS_CURRENT, doc_type=entry.doc_type,
                       fetch_method=method, fetch_note=note, fetched_at=now_iso(),
                       category=entry.category, parent_law=entry.parent_law)


def _doc_from(entry: SeedEntry, parsed: dict, url: str, res, notes: list[str]) -> LawDocument:
    f = parsed["fields"]
    rel = f.get("發佈日期", "")
    m = ROC_DATE_RE.search(rel) or re.search(r"民國\s*\d+\s*年\s*\d+\s*月\s*\d+\s*日", rel)
    amended = m.group(0) if m else (rel or NA)
    eff = f.get("施行日期") or effect_clause(parsed["history"]) or NA
    arts = parsed["articles"]
    if parsed["changed_only"]:
        notes.append("selaw 頁面僅提供「異動條文」（"
                     + "、".join(a.no for a in arts)
                     + "），所有條文需 JS 互動載入，未取得全文；為避免誤判刪除條文，本次不輸出條文")
        arts = []
    if not arts and not parsed["changed_only"]:
        notes.append("頁面已取得但無法解析條文（版面可能變更）")
    if res.method_used == "wayback" and res.note:
        notes.append(res.note)
    return LawDocument(
        name=entry.name, source_name=SOURCE_NAME, source_url=url,
        status=entry.status or STATUS_CURRENT, doc_type=entry.doc_type,
        promulgated_date=NA,   # selaw 頁面無原始公發布日欄位
        amended_date=amended, effective_date=eff, history=parsed["history"], articles=arts,
        fetch_method=res.method_used, fetch_note="；".join(n for n in notes if n),
        fetched_at=now_iso(), category=entry.category, parent_law=entry.parent_law,
        version_label=amended if amended != NA else "")


def _fetch_joint(entry: SeedEntry, fetcher, flcode: str) -> LawDocument:
    e2 = SeedEntry(name=entry.name, source="twse", category=entry.category, status=entry.status,
                   doc_type=entry.doc_type, ids={"flcode": flcode}, parent_law=entry.parent_law)
    d = twse.fetch_one(e2, fetcher)
    d.source_name = SOURCE_NAME
    msg = ("本指引由證交所與櫃買中心共同制定；全文取自臺灣證券交易所法規分享知識庫"
           f"（FLCODE={flcode}），未能查證櫃買中心自有法規查詢系統網址")
    if d.fetch_note != ANTI_BOT_FAIL_NOTE:     # 全失敗時備註須完全等於 ANTI_BOT_FAIL_NOTE
        d.fetch_note = "；".join(x for x in (msg, d.fetch_note) if x)
    d.metadata["joint_with"] = "twse"
    return d


def fetch_one(entry: SeedEntry, fetcher) -> LawDocument:
    sn = _resolve(entry)
    if not sn:
        if entry.name in JOINT_WITH_TWSE:
            return _fetch_joint(entry, fetcher, JOINT_WITH_TWSE[entry.name])
        return _empty(entry, BASE, "未設定 selaw sysNumber（請於 seed 補 ids.selaw_sysnumber），"
                                   "且未能查證櫃買中心自有法規查詢系統網址")
    url = LAW_URL.format(sn=sn)
    res = fetcher.get(url)
    if not is_ok(res):
        return _empty(entry, url, fail_note(res, "規章"))
    parsed = parse_page(res.content)
    notes: list[str] = []
    if parsed["fields"].get("法規名稱") and norm_text(parsed["fields"]["法規名稱"]) != norm_text(entry.name):
        notes.append(f"頁面法規名稱「{parsed['fields']['法規名稱']}」與 seed 名稱不同")
    if not parsed["current"]:
        notes.append("頁面標示為非現行法規")
    doc = _doc_from(entry, parsed, url, res, notes)
    doc.metadata["selaw_sysnumber"] = sn
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

    if current.metadata.get("joint_with") == "twse" or (entry.name in JOINT_WITH_TWSE and not _resolve(entry)):
        e2 = SeedEntry(name=entry.name, source="twse", ids={"flcode": JOINT_WITH_TWSE.get(entry.name, "")},
                       category=entry.category, doc_type=entry.doc_type, parent_law=entry.parent_law)
        prev = twse.fetch_previous(e2, current, fetcher)
        if prev is not None:
            prev.source_name = SOURCE_NAME
        return prev
    note("前一版：selaw「歷史法規」需 JS 互動載入，且頁面未列出可用之歷史版本 releaseDate，無法取得上一版")
    return None
