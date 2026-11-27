"""#3 intl 來源模組測試（離線 fixtures + 假 Fetcher）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from regcrawler.models import ANTI_BOT_FAIL_NOTE, NA, FetchResult, SeedEntry
from regcrawler.normalize import content_hash
from regcrawler.sources import intl

FX = Path(__file__).parent / "fixtures" / "intl"


def _read(name: str) -> bytes:
    return (FX / name).read_bytes()


class FakeFetcher:
    """依 URL 片段回傳 fixture；未對應者回傳 ANTI_BOT 失敗。"""

    def __init__(self, routes: dict[str, bytes | None]):
        self.routes = routes
        self.calls: list[tuple[str, str | None, str]] = []

    def get(self, url, *, method_hint=None, expect="html", wayback_ok=True):
        self.calls.append((url, method_hint, expect))
        for frag, content in self.routes.items():
            if frag in url and content is not None:
                return FetchResult(ok=True, url=url, content=content,
                                   method_used=method_hint or "requests", final_url=url)
        return FetchResult(ok=False, url=url, note=ANTI_BOT_FAIL_NOTE)


GDPR = SeedEntry(name="Regulation (EU) 2016/679 (General Data Protection Regulation)", source="intl",
                 category="國際", ids={"celex": "32016R0679"})
OMNIBUS = SeedEntry(name="Digital Omnibus Regulation Proposal COM(2025) 837 final", source="intl",
                    category="國際", status="提案", doc_type="草案", metadata_only=True,
                    ids={"procedure": "2025/0360(COD)"})
ISO27001 = SeedEntry(name="ISO/IEC 27001:2022", source="intl", doc_type="標準", metadata_only=True,
                     ids={"iso_id": 82875, "reference": "ISO/IEC 27001:2022",
                          "known_amendments": ["ISO/IEC 27001:2022/Amd 1:2024"],
                          "amendment_ids": {"ISO/IEC 27001:2022/Amd 1:2024": 88435}})
ISO27701 = SeedEntry(name="ISO/IEC 27701:2025", source="intl", doc_type="標準", metadata_only=True,
                     ids={"iso_id": 85819, "reference": "ISO/IEC 27701:2025", "known_amendments": []})
BS10012 = SeedEntry(name="BS 10012:2017+A1:2018", source="intl", doc_type="標準", metadata_only=True,
                    ids={"reference": "BS 10012:2017+A1:2018"},
                    url="https://knowledge.bsigroup.com/products/"
                        "data-protection-specification-for-a-personal-information-management-system")


def gdpr_routes(sparql=True):
    return {
        "webapi/rdf/sparql": _read("sparql_consolidated_32016R0679.json") if sparql else None,
        "TXT/HTML/?uri=CELEX:02016R0679-20160504": _read("gdpr_consolidated_02016R0679-20160504.html"),
        "TXT/HTML/?uri=CELEX:32016R0679": _read("gdpr_oj_32016R0679.html"),
    }


# ───────────── GDPR ─────────────
def test_gdpr_consolidated_parse():
    f = FakeFetcher(gdpr_routes())
    [doc] = intl.fetch([GDPR], f)
    assert f.calls[0][1] == "official_api" and f.calls[0][2] == "json"
    assert doc.ok and doc.source_url.endswith("CELEX:02016R0679-20160504")
    assert [a.no for a in doc.articles] == ["Article 1", "Article 2", "Article 33", "Article 99"]
    a33 = doc.articles[2]
    paras = a33.text.split("\n")
    assert paras[0].startswith("1. In the case of a personal data breach")
    assert "not later than 72 hours" in paras[0]
    assert paras[3].startswith("(a) describe the nature of the personal data breach")
    assert paras[6] == "(d) describe the measures taken or proposed to be taken by the controller to address " \
                       "the personal data breach, including, where appropriate, measures to mitigate its possible " \
                       "adverse effects."
    assert paras[-1].startswith("5. The controller shall document")
    assert a33.chapter == "CHAPTER IV Controller and processor / Section 2 Security of personal data"
    assert doc.metadata["條文標題"]["Article 33"] == \
        "Notification of a personal data breach to the supervisory authority"
    # 標題不混入條文
    assert "Notification of a personal data breach" not in a33.text.split("\n")[0][:40]
    # Article 99 不含 binding 句與註腳；consolidated 標記 ▼B ►C1 已移除
    a99 = doc.articles[-1]
    assert a99.text == ("1. This Regulation shall enter into force on the twentieth day following that of its "
                        "publication in the Official Journal of the European Union.\n"
                        "2. It shall apply from 25 May 2018.")
    assert all("▼" not in a.text and "►" not in a.text for a in doc.articles)
    # 日期皆由內容解析
    assert doc.promulgated_date == "4.5.2016"
    assert doc.amended_date == "23.5.2018"          # 最新 corrigendum 晚於 consolidated 04.05.2016
    assert doc.effective_date == "25 May 2018"
    assert doc.metadata["簽署日（act date）"] == "27 April 2016"
    assert doc.metadata["consolidated 版本"] == "02016R0679 — 04.05.2016 — 000.002"
    assert "Corrigendum, OJ L 127, 23.5.2018" in doc.metadata["Corrected by"]
    assert doc.metadata["recitals 數量"].startswith("NA")
    assert doc.metadata["consolidated 版本清單"] == ["02016R0679-20160504"]
    assert doc.version_label == "consolidated 02016R0679-20160504"
    assert len(content_hash(doc)) == 128


def test_gdpr_point_lines_in_article2():
    [doc] = intl.fetch([GDPR], FakeFetcher(gdpr_routes()))
    a2 = doc.articles[1].text.split("\n")
    assert a2[1] == "2. This Regulation does not apply to the processing of personal data:"
    assert [p[:3] for p in a2[2:]] == ["(a)", "(b)", "(c)", "(d)"]


def test_gdpr_previous_is_original_oj():
    f = FakeFetcher(gdpr_routes())
    [cur] = intl.fetch([GDPR], f)
    prev = intl.fetch_previous(GDPR, cur, f)
    assert prev is not None and prev.version_label == "OJ 32016R0679"
    assert prev.promulgated_date == "4.5.2016"
    assert prev.amended_date == NA
    assert prev.effective_date == "25 May 2018"
    assert prev.metadata["recitals 數量"] == "173"
    assert prev.metadata["Done at"] == "Done at Brussels, 27 April 2016."
    assert [a.no for a in prev.articles] == ["Article 1", "Article 33", "Article 99"]
    # 同一條文在兩版本的正規化文字一致（原 OJ 「1.   」多空白不影響）
    assert cur.articles[0].text == prev.articles[0].text
    assert not any("HENNIS" in a.text or "OJ C 229" in a.text for a in prev.articles)


def test_gdpr_sparql_fallback_to_known_list():
    f = FakeFetcher(gdpr_routes(sparql=False))
    [doc] = intl.fetch([GDPR], f)
    assert doc.articles
    assert "已查證清單" in doc.fetch_note


def test_gdpr_total_failure_single_doc():
    [doc] = intl.fetch([GDPR], FakeFetcher({}))
    assert not doc.ok and doc.articles == []
    assert ANTI_BOT_FAIL_NOTE in doc.fetch_note


# ───────────── Digital Omnibus ─────────────
def test_omnibus_progress():
    f = FakeFetcher({"procedure/EN/2025_360": _read("omnibus_eurlex_procedure.html"),
                     "oeil.europarl.europa.eu": _read("omnibus_oeil.html")})
    [doc] = intl.fetch([OMNIBUS], f)
    m = doc.metadata
    assert doc.metadata_only and doc.ok and doc.status == "提案"
    assert m["EUR-Lex procedure 狀態"] == "Ongoing"
    assert m["EP Legislative Observatory 狀態"] == "Awaiting committee decision"
    assert m["最新事件日期"] == "07/05/2026"
    assert m["最新事件"] == "European Committee of the Regions opinion"
    assert m["已刊登OJ"] == "否" and m["正式編號"] == NA
    assert "19/01/2026 Committee referral announced in Parliament, 1st reading" in m["事件列表"]
    assert doc.promulgated_date == "19/11/2025"
    assert "現行" not in doc.fetch_note


def test_omnibus_detects_final_act():
    html = _read("omnibus_oeil.html").replace(
        b"</table>\n</body>",
        b"<tr><td>01/12/2026</td><td>Final act published in Official Journal</td><td></td><td></td></tr>"
        b"</table><h2>Final act</h2><p>Regulation 2026/9999</p></body>")
    f = FakeFetcher({"oeil.europarl.europa.eu": html})
    [doc] = intl.fetch([OMNIBUS], f)
    assert doc.metadata["已刊登OJ"] == "是"
    assert doc.metadata["正式編號"] == "Regulation 2026/9999"
    assert "請主控將 status 改為「現行」" in doc.fetch_note


# ───────────── ISO ─────────────
def test_iso_27001_metadata():
    f = FakeFetcher({"82875": _read("iso_82875_27001_2022.html"),
                     "88435": _read("iso_88435_27001_2022_amd1_2024.html")})
    [doc] = intl.fetch([ISO27001], f)
    m = doc.metadata
    assert doc.metadata_only and doc.articles == [] and doc.ok
    assert m["標準編號"] == "ISO/IEC 27001:2022"
    assert m["標題"] == ("Information security, cybersecurity and privacy protection — Information security "
                       "management systems — Requirements")
    assert m["版次 edition"] == "3"
    assert m["發布日期"] == "2022-10"
    assert m["狀態"] == "Published"
    assert m["ICS"] == "35.030, 03.100.70"
    assert m["技術委員會"] == "ISO/IEC JTC 1/SC 27"
    assert "60.60" in m["Stage"]
    assert m["Amendments"] == "ISO/IEC 27001:2022/Amd 1:2024"
    assert m["Amendments 明細"] == "ISO/IEC 27001:2022/Amd 1:2024（Published, 2024-02）"
    assert "ISO/IEC 27001:2013" in m["取代（前版）"]
    assert m["被取代（新版）"] == NA
    assert "新版本偵測" not in m
    assert doc.promulgated_date == "2022-10" and doc.amended_date == "2024-02"
    assert doc.version_label == "ISO/IEC 27001:2022"
    assert f.calls[0][0] == "https://www.iso.org/standard/82875.html"


def test_iso_fallback_to_committee_and_new_amendment_detection():
    html = _read("iso_82875_27001_2022.html").replace(
        b"<li><a href=\"/standard/88435.html\">ISO/IEC 27001:2022/Amd 1:2024</a></li>",
        b"<li><a href=\"/standard/88435.html\">ISO/IEC 27001:2022/Amd 1:2024</a></li>"
        b"<li><a href=\"#\">ISO/IEC 27001:2022/Amd 2:2027</a></li>")
    f = FakeFetcher({"committee.iso.org/standard/82875": html})
    entry = SeedEntry(**{**ISO27001.__dict__, "ids": {k: v for k, v in ISO27001.ids.items()
                                                      if k != "amendment_ids"}})
    [doc] = intl.fetch([entry], f)
    assert doc.source_url == "https://committee.iso.org/standard/82875.html"
    assert "新 Amendment：ISO/IEC 27001:2022/Amd 2:2027" in doc.metadata["新版本偵測"]


def test_iso_27701_metadata():
    [doc] = intl.fetch([ISO27701], FakeFetcher({"85819": _read("iso_85819_27701_2025.html")}))
    m = doc.metadata
    assert m["標準編號"] == "ISO/IEC 27701:2025"
    assert m["版次 edition"] == "2" and m["發布日期"] == "2025-10" and m["頁數"] == "64"
    assert m["ICS"] == "35.030"
    assert m["取代（前版）"] == "ISO/IEC 27701:2019"
    assert m["Amendments"] == NA


def test_iso_all_blocked():
    [doc] = intl.fetch([ISO27701], FakeFetcher({}))
    assert not doc.ok and doc.fetch_note == ANTI_BOT_FAIL_NOTE


# ───────────── BSI ─────────────
def test_bsi_official_status():
    [doc] = intl.fetch([BS10012], FakeFetcher({"knowledge.bsigroup.com": _read("bsi_10012_2017_a1_2018.html")}))
    m = doc.metadata
    assert m["標準編號"] == "BS 10012:2017+A1:2018"
    assert m["標題"] == "Data protection. Specification for a personal information management system"
    assert m["狀態"] == "Withdrawn"
    assert m["發布日期"] == "31 Jul 2018" and m["撤銷日期"] == "24 Apr 2026"
    assert m["ISBN"] == "978 0 539 01173 9" and m["技術委員會"] == "IDT/1"
    assert m["ICS"].startswith("01.140.30 Documents in administration")
    assert "BS 10012:2017, which is withdrawn" in m["取代（前版）"]
    assert "Withdrawn" in doc.fetch_note
    assert doc.version_label == "BS 10012:2017+A1:2018"


def test_bsi_unreachable_status_na():
    [doc] = intl.fetch([BS10012], FakeFetcher({}))
    assert doc.metadata["狀態"] == NA
    assert ANTI_BOT_FAIL_NOTE in doc.fetch_note and "NA" in doc.fetch_note


def test_metadata_entries_have_no_previous():
    [doc] = intl.fetch([ISO27701], FakeFetcher({"85819": _read("iso_85819_27701_2025.html")}))
    assert intl.fetch_previous(ISO27701, doc, FakeFetcher({})) is None


def test_html_lines_label_merge():
    lines = intl.html_lines("<div><p>(a)</p><p>text a</p><p>1.</p><p>para</p></div>")
    assert lines == ["(a) text a", "1. para"]


@pytest.mark.parametrize("s,ok", [("4.5.2016", True), ("2022-10", True), ("31 Jul 2018", True), ("x", False)])
def test_parse_date(s, ok):
    assert (intl._parse_date(s) is not None) == ok
