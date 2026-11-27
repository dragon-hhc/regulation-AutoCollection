"""regcrawler.sources.moj 單元測試（離線；fixtures 見 tests/fixtures/moj/ 檔頭來源說明）。"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from regcrawler.models import ANTI_BOT_FAIL_NOTE, NA, FetchResult, SeedEntry
from regcrawler.normalize import article_key, content_hash
from regcrawler.sources import moj

FX = Path(__file__).parent / "fixtures" / "moj"


def _zip_from_fixture(fname: str, member: str) -> bytes:
    data = json.loads((FX / fname).read_text(encoding="utf-8"))
    data.pop("_source", None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        # 官方檔為 UTF-8 with BOM
        z.writestr(member, b"\xef\xbb\xbf" + json.dumps(data, ensure_ascii=False).encode("utf-8"))
        z.writestr("schema.csv", "")
    return buf.getvalue()


class FakeFetcher:
    def __init__(self, mapping: dict[str, bytes]):
        self.mapping = mapping
        self.calls: list[tuple[str, str | None, str]] = []

    def get(self, url, *, method_hint=None, expect="html", wayback_ok=True):
        self.calls.append((url, method_hint, expect))
        if url in self.mapping:
            method = "official_api" if method_hint == "official_api" else "requests"
            return FetchResult(ok=True, url=url, content=self.mapping[url], method_used=method, final_url=url)
        return FetchResult(ok=False, url=url, note=ANTI_BOT_FAIL_NOTE)


@pytest.fixture
def api_fetcher():
    return FakeFetcher({
        moj.API_LAW_URLS[0]: _zip_from_fixture("ChLaw_subset.json", "ChLaw.json"),
        moj.API_ORDER_URLS[0]: _zip_from_fixture("ChOrder_subset.json", "ChOrder.json"),
    })


def test_open_data_pdpa(api_fetcher):
    e = SeedEntry(name="個人資料保護法", source="moj", ids={"pcode": "I0050021"}, include_interpretations=True)
    [doc] = moj.fetch([e], api_fetcher)
    assert doc.name == "個人資料保護法"
    assert doc.source_name == moj.SOURCE_NAME
    assert doc.fetch_method == "official_api"
    assert doc.source_url == "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=I0050021"
    assert doc.promulgated_date == "民國 84 年 08 月 11 日"
    assert doc.amended_date == "民國 114 年 11 月 11 日"
    assert doc.effective_date == "未定（由行政院定之）"
    assert "華總一經字第 11400114521" in doc.history
    nos = {a.no for a in doc.articles}
    for n in ("第 1-2 條", "第 20-1 條", "第 21-5 條", "第 51-1 條", "第 53-1 條"):
        assert n in nos
    a27 = next(a for a in doc.articles if a.no == "第 27 條")
    assert a27.text == "（刪除）"
    a3 = next(a for a in doc.articles if a.no == "第 2 條")
    assert "\n" in a3.text and "\r" not in a3.text
    assert a3.chapter == "第 一 章 總則"
    a211 = next(a for a in doc.articles if a.no == "第 21-1 條")
    assert a211.chapter == "第 三 章之一 行政監督／第 一 節 對公務機關之監督"
    assert doc.metadata["pcode"] == "I0050021"
    assert doc.metadata["生效日期"] == "99991231"
    # 全國法規資料庫無函釋入口：只註記、不杜撰
    assert "函釋" in doc.fetch_note
    assert len(content_hash(doc)) == 128
    # 命令 ZIP 不必下載（法律 ZIP 已命中）
    assert all(u != moj.API_ORDER_URLS[0] for u, _, _ in api_fetcher.calls)
    assert api_fetcher.calls[0] == (moj.API_LAW_URLS[0], "official_api", "binary")


def test_open_data_order_and_dates(api_fetcher):
    entries = [
        SeedEntry(name="個人資料保護法施行細則", source="moj"),
        SeedEntry(name="資通安全事件通報應變及演練辦法", source="moj"),
        SeedEntry(name="電業與公用天然氣事業及加油站業個人資料檔案安全維護管理辦法", source="moj"),
        SeedEntry(name="資通安全管理法", source="moj"),
    ]
    d1, d2, d3, d4 = moj.fetch(entries, api_fetcher)
    assert (d1.promulgated_date, d1.amended_date, d1.effective_date) == (
        "民國 85 年 05 月 01 日", "民國 105 年 03 月 02 日", "民國 105 年 03 月 15 日")
    assert d2.effective_date == "民國 115 年 01 月 05 日"          # 並自發布日施行
    assert d2.articles[0].chapter == "第 一 章 總則"
    # 部分條文不同施行日：原文照錄
    assert d3.effective_date == "除第 2  條第 2  款自發布後六個月施行外，其餘自發布日施行"
    # 施行日期由行政院定之 → 其後施行令定自 114.12.1
    assert d4.effective_date == "民國 114 年 12 月 01 日"
    assert d4.amended_date == "民國 114 年 09 月 24 日"


def test_old_name_matches_by_history(api_fetcher):
    e = SeedEntry(name="資通安全事件通報及應變辦法", source="moj")
    [doc] = moj.fetch([e], api_fetcher)
    assert doc.articles
    assert "官方現名「資通安全事件通報應變及演練辦法」" in doc.fetch_note


def test_partial_effective_from_previous_history():
    data = json.loads((FX / "ChLaw_subset.json").read_text(encoding="utf-8"))
    rec = next(r for r in data["Laws"] if r["LawName"] == "個人資料保護法")
    entries = moj.split_history(rec["LawHistories"])
    prom, amend, eff = moj.dates_from_history("\n".join(entries[:-1]))   # 112 年版
    assert amend == "民國 112 年 05 月 31 日"
    assert eff == "第 48 條自公布日施行；第 1-1 條未定（由行政院定之）"


def test_api_failure_falls_back_to_html_and_notes():
    html = (FX / "LawOldVer_I0050021.html").read_bytes()
    f = FakeFetcher({"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=I0050021": html})
    [doc] = moj.fetch([SeedEntry(name="個人資料保護法", source="moj")], f)
    assert "官方開放資料無法取得" in doc.fetch_note
    assert doc.fetch_method == "requests"
    assert [a.no for a in doc.articles] == ["第 1 條", "第 1-1 條", "第 27 條", "第 48 條"]
    # 沿革頁取不到：記錄原因、日期不杜撰
    assert "沿革頁無法取得" in doc.fetch_note
    assert doc.effective_date == NA


def test_total_failure_single_entry_does_not_break_batch():
    f = FakeFetcher({})
    docs = moj.fetch([SeedEntry(name="不存在的辦法", source="moj"),
                      SeedEntry(name="資通安全管理法", source="moj")], f)
    assert len(docs) == 2
    assert all(not d.articles for d in docs)
    assert "搜尋頁亦無法定位" in docs[0].fetch_note
    assert ANTI_BOT_FAIL_NOTE in docs[1].fetch_note


def test_parse_lawoldver_html():
    parsed = moj.parse_law_html((FX / "LawOldVer_I0050021.html").read_text(encoding="utf-8"))
    assert parsed["name"] == "個人資料保護法"
    assert parsed["amended"] == "民國 112 年 05 月 31 日"
    a27 = parsed["articles"][2]
    assert a27.no == "第 27 條"
    assert a27.text.count("\n") == 2
    assert a27.chapter == "第 三 章 非公務機關對個人資料之蒐集、處理及利用"
    a48 = parsed["articles"][3]
    assert a48.text.splitlines()[1] == "一、違反第八條或第九條規定。"


def test_fetch_previous(api_fetcher):
    e = SeedEntry(name="個人資料保護法", source="moj", ids={"pcode": "I0050021"})
    [cur] = moj.fetch([e], api_fetcher)
    api_fetcher.mapping["https://law.moj.gov.tw/LawClass/LawOldVer.aspx?pcode=I0050021"] = \
        (FX / "LawOldVer_I0050021.html").read_bytes()
    prev = moj.fetch_previous(e, cur, api_fetcher)
    assert prev is not None
    assert prev.amended_date == "民國 112 年 05 月 31 日"
    assert prev.effective_date == "第 48 條自公布日施行；第 1-1 條未定（由行政院定之）"
    assert prev.promulgated_date == cur.promulgated_date
    assert "11400114521" not in prev.history
    old27 = next(a for a in prev.articles if article_key(a.no) == "27")
    assert old27.text.startswith("非公務機關保有個人資料檔案者")


def test_fetch_previous_unavailable(api_fetcher):
    e = SeedEntry(name="個人資料保護法", source="moj")
    [cur] = moj.fetch([e], api_fetcher)
    assert moj.fetch_previous(e, cur, api_fetcher) is None
    assert "上一版" in cur.fetch_note


def test_ymd_and_history_helpers():
    assert moj.ymd_to_roc("20251111") == "民國 114 年 11 月 11 日"
    assert moj.ymd_to_roc("99991231") == ""
    assert moj.ymd_to_roc("") == ""


LIA_LIST = "https://law.lia-roc.org.tw/Law/HistoryList?lsid=FL010627"
LIA_112 = "https://law.lia-roc.org.tw/Law/HisContent?lsid=FL010627&date=20230531"


def test_fetch_previous_falls_back_to_lia_mirror(api_fetcher):
    e = SeedEntry(name="個人資料保護法", source="moj", ids={"pcode": "I0050021"})
    [cur] = moj.fetch([e], api_fetcher)
    # 官方 LawOldVer 失敗（未放入 mapping）→ 改用壽險公會鏡像
    api_fetcher.mapping[LIA_LIST] = (FX / "lia_HistoryList_FL010627.html").read_bytes()
    api_fetcher.mapping[LIA_112] = (FX / "lia_HisContent_FL010627_20230531.html").read_bytes()
    prev = moj.fetch_previous(e, cur, api_fetcher)
    assert prev is not None
    assert prev.source_url == LIA_112
    assert "基準版本取自非官方鏡像：中華民國人壽保險商業同業公會 保險相關法規查詢系統" in prev.fetch_note
    assert "官方歷史法規頁失敗" in prev.fetch_note
    assert prev.amended_date == "民國 112 年 05 月 31 日"
    assert prev.effective_date == "第 48 條自公布日施行；第 1-1 條未定（由行政院定之）"
    assert [a.no for a in prev.articles] == ["第 1 條", "第 1-1 條", "第 26 條", "第 27 條", "第 28 條"]
    a27 = prev.articles[3]
    assert a27.text.split("\n")[0] == "非公務機關保有個人資料檔案者，應採行適當之安全措施，防止個人資料被竊取、竄改、毀損、滅失或洩漏。"
    assert a27.text.count("\n") == 2
    assert a27.chapter == "第 三 章 非公務機關對個人資料之蒐集、處理及利用"
    assert prev.articles[4].text.count("\n") == 5          # 第 28 條 6 項
    assert "上一版取自非官方鏡像" in cur.fetch_note
    # 官方頁仍為第一選擇
    urls = [u for u, _, _ in api_fetcher.calls]
    assert urls.index("https://law.moj.gov.tw/LawClass/LawOldVer.aspx?pcode=I0050021") < urls.index(LIA_LIST)


def test_taipei_export_only_used_if_older(api_fetcher):
    # 臺北市匯出只有最新版：修正日期與現行版相同 → 不可當上一版
    e = SeedEntry(name="個人資料保護法", source="moj")
    [cur] = moj.fetch([e], api_fetcher)
    export = "法規名稱：個人資料保護法\n修正日期：民國 114 年 11 月 11 日\n第 1 條\n為規範個人資料之蒐集、處理及利用，以避免人格權受侵害，並促進個人資料之合理利用，特制定本法。\n"
    api_fetcher.mapping[moj.TAIPEI_EXPORT_URL.format(lsid="FL010627")] = export.encode("utf-8")
    assert moj.fetch_previous(e, cur, api_fetcher) is None
    assert "未早於現行版" in cur.fetch_note


# ───────── 函釋（臺北市法規查詢系統轉載中央函釋） ─────────
TP = "https://laws.gov.taipei/Law/"


def _interp_fetcher(api_fetcher):
    m = api_fetcher.mapping
    m[TP + "LawSearch/LawArticleContent/FL010627"] = (FX / "taipei_LawArticleContent_FL010627.html").read_bytes()
    m[TP + "LawSearch/LawRelateInterpretation/FL010627?no=2"] = (FX / "taipei_interp_list_FL010627_no2.html").read_bytes()
    m[TP + "LawSearch/LawRelateInterpretation/FL010627?no=5"] = (FX / "taipei_interp_list_FL010627_no5.html").read_bytes()
    m[TP + "Interpretation/Content/FE395494"] = (FX / "taipei_interp_FE395494.html").read_bytes()
    m[TP + "LawSearch/LawArticleContent/FL088622"] = (FX / "taipei_LawArticleContent_FL088622.html").read_bytes()
    m[TP + "LawSearch/LawRelateInterpretation/FL088622?no=3"] = (FX / "taipei_interp_list_FL088622_no3.html").read_bytes()
    return api_fetcher


@pytest.fixture
def fixed_today(monkeypatch):
    from datetime import date
    monkeypatch.setattr(moj, "_today", lambda: date(2026, 11, 27))


def test_interpretations_from_taipei(api_fetcher, fixed_today):
    f = _interp_fetcher(api_fetcher)
    e = SeedEntry(name="個人資料保護法", source="moj", category="個資法體系", include_interpretations=True)
    docs = moj.fetch([e], f)
    law, interps = docs[0], docs[1:]
    assert law.doc_type == "法規" and law.articles
    assert "函釋取自臺北市法規查詢系統（轉載中央函釋）" in law.fetch_note
    assert "主管機關網站函釋專區" in law.fetch_note
    # no=2 的 10 則 + no=5 的 2 則新函釋（FE401872 兩條共用，去重）
    assert len(interps) == 12
    assert all(d.doc_type == "函釋" and d.parent_law == "個人資料保護法" for d in interps)
    assert interps[0].name == "個人資料保護委員會籌備處 115.10.27 個資籌法字第1150002720號函"   # 新→舊
    shared = next(d for d in interps if d.metadata["臺北市法規查詢系統編號"] == "FE401872")
    assert shared.metadata["相關條文"] == "第 2 條、第 5 條"
    d = next(d for d in interps if d.metadata["臺北市法規查詢系統編號"] == "FE395494")
    assert d.name == "個人資料保護委員會籌備處 115.01.23 個資籌法字第1150000083號"
    assert d.source_url == "https://laws.gov.taipei/Law/Interpretation/Content/FE395494"
    assert d.promulgated_date == "民國 115 年 01 月 23 日"
    assert (d.amended_date, d.effective_date) == (NA, NA)
    assert "函釋取自臺北市法規查詢系統（轉載中央函釋）" in d.fetch_note
    [art] = d.articles
    assert art.no == "函釋全文"
    lines = art.text.split("\n")
    assert lines[0].startswith("要") and lines[0].endswith("尚無個人資料保護法之適用。")
    assert lines[1].startswith("主旨：為貴處函詢獸醫診療機構")
    assert lines[-1] == "一、復貴處 115 年 1 月 8 日高市動保行字第 11570029800 號函。"
    assert "回上頁" not in art.text and "相關法條" not in art.text
    assert d.metadata["要旨"].startswith("個人資料保護法所稱個人")
    # 內容頁取不到者：保留標題與來源，不杜撰內文
    miss = next(x for x in interps if x.metadata["臺北市法規查詢系統編號"] == "FE401871")
    assert not miss.articles and "函釋內容頁無法取得" in miss.fetch_note
    assert miss.promulgated_date == "115.08.04"


def test_interpretations_three_year_window(api_fetcher, fixed_today):
    f = _interp_fetcher(api_fetcher)
    e = SeedEntry(name="資通安全管理法", source="moj", include_interpretations=True)
    docs = moj.fetch([e], f)
    assert len(docs) == 1                       # 唯一函釋為 111.03.29，超過 3 年
    assert "最近 3 年 0 則" in docs[0].fetch_note


def test_interpretations_cap(api_fetcher, fixed_today, monkeypatch):
    monkeypatch.setattr(moj, "MAX_INTERPRETATIONS", 3)
    docs = moj.fetch([SeedEntry(name="個人資料保護法", source="moj", include_interpretations=True)],
                     _interp_fetcher(api_fetcher))
    assert len(docs) == 4
    assert "僅取最新 3 則" in docs[0].fetch_note


def test_interpretations_no_lsid(api_fetcher):
    docs = moj.fetch([SeedEntry(name="資通安全情資分享辦法", source="moj", include_interpretations=True)], api_fetcher)
    assert len(docs) == 1 and "lsid" in docs[0].fetch_note
