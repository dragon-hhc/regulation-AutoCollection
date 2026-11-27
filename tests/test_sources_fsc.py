"""regcrawler.sources.fsc 單元測試（離線；fixtures 見 tests/fixtures/fsc/ 檔頭來源說明）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from regcrawler.models import ANTI_BOT_FAIL_NOTE, NA, FetchResult, SeedEntry
from regcrawler.normalize import article_key
from regcrawler.sources import fsc

FX = Path(__file__).parent / "fixtures" / "fsc"


class FakeFetcher:
    def __init__(self, mapping: dict[str, str]):
        self.mapping = {u: (FX / f).read_bytes() for u, f in mapping.items()}
        self.calls: list[str] = []

    def get(self, url, *, method_hint=None, expect="html", wayback_ok=True):
        self.calls.append(url)
        if url in self.mapping:
            return FetchResult(ok=True, url=url, content=self.mapping[url], method_used="requests", final_url=url)
        return FetchResult(ok=False, url=url, note=ANTI_BOT_FAIL_NOTE)


def test_parse_law_page_articles_and_dates():
    url = fsc.LAW_URL.format(id="FL007032")
    f = FakeFetcher({url: "FL007032_LawContent.html"})
    e = SeedEntry(name="公開發行公司年報應行記載事項準則", source="fsc", ids={"fsc_id": "FL007032"})
    [doc] = fsc.fetch([e], f)
    assert doc.source_name == fsc.SOURCE_NAME
    assert doc.source_url == url
    assert doc.promulgated_date == "民國 77 年 06 月 07 日"
    assert doc.amended_date == "民國 114 年 12 月 19 日"
    assert doc.effective_date == NA
    assert [a.no for a in doc.articles] == ["第 1 條", "第 2 條", "第 3 條", "第 4 條"]
    assert doc.articles[0].text == "本準則依證券交易法（以下簡稱本法）第三十六條第四項規定訂定之。"
    # 第 2 條兩項、第 3 條（序文＋二款）、第 4 條三項 → 以 \n 分隔
    assert doc.articles[1].text.count("\n") == 1
    assert doc.articles[2].text.split("\n")[1].startswith("一、年報所載事項")
    assert doc.articles[3].text.split("\n")[2] == "第一項公司名稱變更者，應於當年度及以後連續二年度以新舊名稱對照揭露。"
    assert doc.metadata["發文字號"] == "金管證交字第1140385797號 令"
    assert doc.metadata["attachments"][0]["url"].startswith("https://law.fsc.gov.tw/Download.ashx?FileID=25198")
    # 沿革頁擷取失敗不影響本體
    assert doc.ok and doc.fetch_method == "requests"


def test_amended_date_with_not_yet_effective_note():
    url = fsc.LAW_URL.format(id="FL049894")
    f = FakeFetcher({url: "FL049894_LawContent.html"})
    e = SeedEntry(name="金融控股公司及銀行業內部控制及稽核制度實施辦法", source="fsc")
    [doc] = fsc.fetch([e], f)
    assert f.calls[0] == url                       # 由 KNOWN_IDS 定位
    assert doc.promulgated_date == "民國 99 年 03 月 29 日"
    assert doc.amended_date == "民國 115 年 05 月 06 日"
    assert doc.effective_date == "※本次發布之條文全部或部分尚未施行（實施）"
    assert article_key(doc.articles[0].no) == "24"
    assert "資訊安全長" in doc.articles[0].text


def test_history_source_page():
    html = (FX / "GL000933_LawContentSource.html").read_bytes()
    h = fsc.parse_source_page(html).split("\n")
    assert len(h) == 4
    assert h[0] == ("1.中華民國一百零二年十一月八日金融監督管理委員會金管法字第 10200 704150 號令"
                    "訂定發布全文 16 條；並自發布日施行")
    assert h[3].startswith("4.中華民國一百十年十二月十四日")


def test_fetch_failure_returns_note():
    f = FakeFetcher({})
    e = SeedEntry(name="金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法", source="fsc")
    [doc] = fsc.fetch([e], f)
    assert doc.articles == []
    assert ANTI_BOT_FAIL_NOTE in doc.fetch_note
    assert doc.source_url == fsc.LAW_URL.format(id="GL000933")


def test_interpretations_keyword_filter(monkeypatch):
    monkeypatch.setitem(fsc.INTERP_CATEGORIES, "公開發行公司建立內部控制制度處理準則", ("C001", "C006"))
    f = FakeFetcher({
        fsc.CATEGORY_LIST_URL.format(cat="C001", page=1): "C001_list_page2.html",
        fsc.CATEGORY_LIST_URL.format(cat="C006", page=1): "C006_list_page1.html",
        fsc.LAW_URL.format(id="GL003330"): "GL003330_LawContent.html",
        fsc.LAW_URL.format(id="FL021141"): "FL049894_LawContent.html",   # 本體內容不在此測試範圍
    })
    e = SeedEntry(name="公開發行公司建立內部控制制度處理準則", source="fsc", include_interpretations=True)
    docs = fsc.fetch([e], f)
    interps = [d for d in docs if d.doc_type == "函釋"]
    assert len(interps) == 1
    d = interps[0]
    assert d.name == ("有關公開發行公司建立內部控制制度處理準則第九條之一規定之令"
                      "（民國 110 年 12 月 28 日 金管證審字第11003656544號 令）")
    assert [a.no for a in d.articles] == ["函釋全文"]
    assert "資訊安全長" in d.articles[0].text
    assert d.articles[0].text.endswith("四、本令自一百十一年一月一日生效。")
    assert d.promulgated_date == "民國 110 年 12 月 28 日"
    assert d.parent_law == "公開發行公司建立內部控制制度處理準則"
    # 候選中與本法規無關（GL003751 為另一準則）者不開啟；GL003822 標題提及本法規 → 會嘗試開啟（此處擷取失敗被略過）
    assert fsc.LAW_URL.format(id="GL003751") not in f.calls
    assert fsc.LAW_URL.format(id="GL003822") in f.calls
    # 分頁：C001 第 1 頁宣告最末頁 7 → 繼續要第 2 頁（失敗即停止）
    assert fsc.CATEGORY_LIST_URL.format(cat="C001", page=2) in f.calls


def test_keywords_constant():
    assert set(fsc.INTERP_KEYWORDS) >= {"資通安全", "資訊安全", "個人資料", "資安"}


def test_fetch_previous_picks_latest_older_version():
    f = FakeFetcher({
        fsc.HISTORY_LIST_URL.format(id="FL007032"): "FL007032_LawContentHistoryList.html",
        "https://law.fsc.gov.tw/LawContentHistory.aspx?hid=1299&id=FL007032": "FL007032_LawContentHistory_hid1299.html",
    })
    e = SeedEntry(name="公開發行公司年報應行記載事項準則", source="fsc", ids={"fsc_id": "FL007032"})
    cur = fsc.LawDocument(name=e.name, source_name=fsc.SOURCE_NAME, source_url="x",
                          promulgated_date="民國 77 年 06 月 07 日", amended_date="民國 114 年 12 月 19 日",
                          metadata={"fsc_id": "FL007032"})
    prev = fsc.fetch_previous(e, cur, f)
    assert prev is not None
    assert prev.amended_date == "民國 114 年 11 月 07 日"
    assert prev.version_label == "民國 114 年 11 月 07 日"
    assert prev.promulgated_date == "民國 77 年 06 月 07 日"
    assert [a.no for a in prev.articles] == ["第 1 條", "第 2 條"]


def test_fetch_previous_unavailable_sets_note():
    e = SeedEntry(name="公開發行公司年報應行記載事項準則", source="fsc", ids={"fsc_id": "FL007032"})
    cur = fsc.LawDocument(name=e.name, source_name=fsc.SOURCE_NAME, source_url="x",
                          metadata={"fsc_id": "FL007032"})
    assert fsc.fetch_previous(e, cur, FakeFetcher({})) is None
    assert "前一版" in cur.fetch_note and ANTI_BOT_FAIL_NOTE in cur.fetch_note


def test_date_helpers():
    assert fsc.roc_to_ymd("民國 115 年 09 月 30 日") == "20260930"
    assert fsc.roc_to_ymd("098.12.22") == "20091222"
    assert fsc.split_date_note("") == (NA, "")


@pytest.mark.parametrize("line,no", [("第 1 條", "第 1 條"), ("第 10-1 條", "第 10-1 條"),
                                     ("第 1 條 本辦法依個人資料保護法", "第 1 條")])
def test_article_heading_regex(line, no):
    assert fsc.ART_HEAD_RE.match(line).group(1) == no


def test_anti_bot_note_is_exact():
    [doc] = fsc.fetch([SeedEntry(name="公開發行公司建立內部控制制度處理準則", source="fsc")], FakeFetcher({}))
    assert doc.fetch_note == ANTI_BOT_FAIL_NOTE


def test_extract_attachment_text_docx_roundtrip():
    """附件解析（python-docx）；以程式產生的 DOCX 驗證流程，文字取自 FL007032 第 1 條官方原文。"""
    docx = pytest.importorskip("docx")
    import io
    d = docx.Document()
    d.add_paragraph("第 1 條")
    d.add_paragraph("本準則依證券交易法（以下簡稱本法）第三十六條第四項規定訂定之。")
    buf = io.BytesIO()
    d.save(buf)
    txt = fsc.extract_attachment_text(buf.getvalue(), "x.docx")
    arts = fsc.split_articles(txt.split("\n"))
    assert arts[0].no == "第 1 條" and arts[0].text.startswith("本準則依證券交易法")
