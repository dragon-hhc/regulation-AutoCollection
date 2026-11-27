"""regcrawler.sources.tpex 單元測試（離線；fixtures 見 tests/fixtures/tpex/ 與 tests/fixtures/twse/ 檔頭來源說明）。"""
from __future__ import annotations

from pathlib import Path

from regcrawler.models import ANTI_BOT_FAIL_NOTE, NA, FetchResult, SeedEntry
from regcrawler.sources import tpex, twse

FX = Path(__file__).parent / "fixtures"


class FakeFetcher:
    def __init__(self, mapping: dict[str, str]):
        self.mapping = {u: (FX / f).read_bytes() for u, f in mapping.items()}
        self.calls: list[str] = []

    def get(self, url, *, method_hint=None, expect="html", wayback_ok=True):
        self.calls.append(url)
        if url in self.mapping:
            return FetchResult(ok=True, url=url, content=self.mapping[url], method_used="requests", final_url=url)
        return FetchResult(ok=False, url=url, note=ANTI_BOT_FAIL_NOTE)


MI = "財團法人中華民國證券櫃檯買賣中心對有價證券上櫃公司重大訊息之查證暨公開處理程序"


def test_current_page_changed_only_is_not_emitted():
    url = tpex.LAW_URL.format(sn="LW10812093")
    f = FakeFetcher({url: "tpex/LW10812093_current.html"})
    [doc] = tpex.fetch([SeedEntry(name=MI, source="tpex", doc_type="規章")], f)
    assert doc.source_url == url
    assert doc.amended_date == "民國115年9月30日"
    assert doc.promulgated_date == NA
    assert doc.effective_date == "自公告日起實施"
    assert doc.history.startswith("中華民國115年9月30日財團法人中華民國證券櫃檯買賣中心證櫃監字第11500743441號")
    assert doc.articles == []
    assert "異動條文" in doc.fetch_note and "第4條" in doc.fetch_note


def test_historical_page_full_text():
    p = tpex.parse_page((FX / "tpex/LW10812093_20190312.html").read_bytes())
    assert p["current"] is False
    assert p["fields"]["法規名稱"] == MI
    assert p["changed_only"] is False
    assert p["articles"][0].no == "第1條"
    assert len(p["articles"][0].text.split("\n")) == 3


def test_joint_guide_from_twse():
    f = FakeFetcher({twse.ALL_URL.format(c="FL104387"): "twse/FL104387_DAT0201.html",
                     twse.HISTORY_URL.format(c="FL104387"): "twse/FL104387_DAT01.html"})
    [doc] = tpex.fetch([SeedEntry(name="上市上櫃公司資通安全管控指引", source="tpex", doc_type="指引")], f)
    assert doc.source_name == tpex.SOURCE_NAME
    assert doc.source_url == twse.ALL_URL.format(c="FL104387")
    assert "共同制定" in doc.fetch_note
    assert [a.no for a in doc.articles] == ["第 1 條", "第 2 條"]


def test_unknown_entry_and_previous():
    [doc] = tpex.fetch([SeedEntry(name="某未查證規章", source="tpex")], FakeFetcher({}))
    assert doc.articles == [] and "sysNumber" in doc.fetch_note
    e = SeedEntry(name=MI, source="tpex")
    assert tpex.fetch_previous(e, doc, FakeFetcher({})) is None
    assert "前一版" in doc.fetch_note


def test_failure_note():
    [doc] = tpex.fetch([SeedEntry(name=MI, source="tpex")], FakeFetcher({}))
    assert ANTI_BOT_FAIL_NOTE in doc.fetch_note


def test_anti_bot_note_is_exact():
    for name in (MI, "上市上櫃公司資通安全管控指引"):
        [doc] = tpex.fetch([SeedEntry(name=name, source="tpex")], FakeFetcher({}))
        assert doc.fetch_note == ANTI_BOT_FAIL_NOTE
