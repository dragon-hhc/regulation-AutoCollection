"""regcrawler.sources.twse 單元測試（離線；fixtures 見 tests/fixtures/twse/ 檔頭來源說明）。"""
from __future__ import annotations

from pathlib import Path

from regcrawler.models import ANTI_BOT_FAIL_NOTE, NA, FetchResult, LawDocument, SeedEntry
from regcrawler.sources import twse

FX = Path(__file__).parent / "fixtures" / "twse"


class FakeFetcher:
    def __init__(self, mapping: dict[str, str]):
        self.mapping = {u: (FX / f).read_bytes() for u, f in mapping.items()}
        self.calls: list[str] = []

    def get(self, url, *, method_hint=None, expect="html", wayback_ok=True):
        self.calls.append(url)
        if url in self.mapping:
            return FetchResult(ok=True, url=url, content=self.mapping[url], method_used="requests", final_url=url)
        return FetchResult(ok=False, url=url, note=ANTI_BOT_FAIL_NOTE)


GUIDE = "上市上櫃公司資通安全管控指引"
MI = "臺灣證券交易所股份有限公司對有價證券上市公司重大訊息之查證暨公開處理程序"


def test_fetch_guide():
    f = FakeFetcher({twse.ALL_URL.format(c="FL104387"): "FL104387_DAT0201.html",
                     twse.HISTORY_URL.format(c="FL104387"): "FL104387_DAT01.html"})
    [doc] = twse.fetch([SeedEntry(name=GUIDE, source="twse", doc_type="指引")], f)
    assert doc.source_name == twse.SOURCE_NAME
    assert doc.amended_date == "民國 115 年 11 月 09 日"
    assert doc.promulgated_date == NA
    assert [a.no for a in doc.articles] == ["第 1 條", "第 2 條"]
    assert doc.articles[0].chapter == "第 一 章 總則"
    assert doc.articles[1].text.split("\n")[0] == "名詞定義"
    assert doc.articles[1].text.split("\n")[5].startswith("五、機敏性資料")
    assert "3.中華民國一百十五年十一月九日" in doc.history
    assert doc.metadata["flcode"] == "FL104387"
    assert doc.doc_type == "指引"


def test_material_info_name_and_effect_clause():
    f = FakeFetcher({twse.ALL_URL.format(c="FL007111"): "FL007111_DAT0201.html",
                     twse.HISTORY_URL.format(c="FL007111"): "FL007111_DAT01.html"})
    [doc] = twse.fetch([SeedEntry(name=MI, source="twse", doc_type="規章")], f)
    assert doc.fetch_note == ""          # 名稱（去除英文名稱後）與 seed 相符
    assert doc.metadata["主題分類"] == "發行市場 > 管理 > 資訊公開"
    assert doc.effective_date == "自公告日起實施"
    assert "資通安全事件" in doc.articles[0].text


def test_resolve_via_list():
    f = FakeFetcher({twse.LIST_URLS[0]: "LawLst_excerpt.html"})
    e = SeedEntry(name="不存在於已知清單的名稱", source="twse")
    assert twse._resolve(e, f, []) == ""
    pairs = twse.parse_law_list((FX / "LawLst_excerpt.html").read_bytes())
    assert ("證券期貨市場相關公會新興科技資通安全管控指引", "FL098969") in pairs
    assert all(n != "英" for n, _ in pairs)


def test_fetch_previous_guide():
    f = FakeFetcher({
        twse.HISTORY_URL.format(c="FL104387"): "FL104387_DAT01.html",
        "https://twse-regulation.twse.com.tw/TW/law/DAT06.aspx?FLCODE=FL104387&FLDATE=20240910&LSER=001":
            "FL104387_DAT06_20240910.html",
    })
    cur = LawDocument(name=GUIDE, source_name=twse.SOURCE_NAME, source_url="x",
                      amended_date="民國 115 年 11 月 09 日", metadata={"flcode": "FL104387"})
    prev = twse.fetch_previous(SeedEntry(name=GUIDE, source="twse"), cur, f)
    assert prev is not None
    assert prev.amended_date == "民國 113 年 09 月 10 日"
    assert prev.metadata["fldate"] == "20240910"
    assert prev.articles[0].no == "第 1 條"


def test_fetch_previous_selects_older_than_current():
    f = FakeFetcher({twse.HISTORY_URL.format(c="FL007111"): "FL007111_DAT01.html"})
    cur = LawDocument(name=MI, source_name=twse.SOURCE_NAME, source_url="x",
                      amended_date="民國 115 年 09 月 30 日", metadata={"flcode": "FL007111"})
    assert twse.fetch_previous(SeedEntry(name=MI, source="twse"), cur, f) is None   # 全文頁不在 fixtures
    assert "https://twse-regulation.twse.com.tw/TW/law/DAT06.aspx?FLCODE=FL007111&FLDATE=20260205&LSER=001" in f.calls
    assert "前一版" in cur.fetch_note


def test_failure_note():
    [doc] = twse.fetch([SeedEntry(name=GUIDE, source="twse")], FakeFetcher({}))
    assert doc.articles == [] and ANTI_BOT_FAIL_NOTE in doc.fetch_note


def test_anti_bot_note_is_exact():
    [doc] = twse.fetch([SeedEntry(name=MI, source="twse")], FakeFetcher({}))
    assert doc.fetch_note == ANTI_BOT_FAIL_NOTE
