"""主控端到端整合測試：以真實條文 fixture 模擬一次完整執行（不連網）。

情境：個人資料保護法現行為 114.11.11 版，首次執行無快照 → fetch_previous 取 112.05.31 版為基準
→ subtask1 Excel、subtask2 AST 差異 Excel、連動影響（金管會個資安全維護辦法 第 1 條）。
另模擬一筆 5 種方法全失敗的來源，驗證反爬註記。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import openpyxl
import pytest

from regcrawler import cli
from regcrawler.models import ANTI_BOT_FAIL_NOTE, LawDocument
from tests.test_acceptance_pdpa_114 import FIX, load_fixture

PDPA = "個人資料保護法"
FSC_RULE = "金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法"


def _fake_module(name, docs_by_seed, previous=None):
    m = types.ModuleType(f"regcrawler.sources.{name}")
    m.SOURCE_NAME = "法務部全國法規資料庫" if name == "moj" else name

    def fetch(entries, fetcher):
        out = []
        for e in entries:
            d = docs_by_seed.get(e.name)
            if d is None:
                d = LawDocument(name=e.name, source_name=m.SOURCE_NAME, source_url=e.verified_by,
                                fetch_note=ANTI_BOT_FAIL_NOTE)
            out.append(d)
        return out

    def fetch_previous(entry, current, fetcher):
        return (previous or {}).get(entry.name)

    m.fetch, m.fetch_previous = fetch, fetch_previous
    return m


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("REGCRAWL_SNAPSHOT_DIR", str(tmp_path / "snap"))
    new = load_fixture(FIX / "pdpa_114.txt")
    old = load_fixture(FIX / "pdpa_112.txt")
    fsc = load_fixture(FIX / "fsc_pdpa_security_rules.txt", parent_law=PDPA)
    for d, m in ((new, "official_api"), (old, "official_api"), (fsc, "official_api")):
        d.source_name = "法務部全國法規資料庫"
        d.fetch_method = m
    new.amended_date, old.amended_date = "民國 114 年 11 月 11 日", "民國 112 年 05 月 31 日"
    new.effective_date = "未定（由行政院定之）"

    seed = tmp_path / "seed.yaml"
    seed.write_text(
        "laws:\n"
        f"  - {{name: {PDPA}, source: moj}}\n"
        f"  - {{name: {FSC_RULE}, source: moj, parent_law: {PDPA}}}\n"
        "  - {name: 測試用不存在網頁, source: moj}\n", encoding="utf-8")
    fake = _fake_module("moj", {PDPA: new, FSC_RULE: fsc}, previous={PDPA: old})
    monkeypatch.setitem(sys.modules, "regcrawler.sources.moj", fake)
    import regcrawler.fetch.chain as chain
    monkeypatch.setattr(chain, "Fetcher", lambda *a, **k: object())
    out = tmp_path / "out"
    assert cli.main(["--config", str(seed), "--out", str(out), "--sources", "moj"]) == 0
    return out


def _rows(path, sheet=None):
    wb = openpyxl.load_workbook(path)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    return rows[0], [dict(zip(rows[0], r)) for r in rows[1:]]


def test_crawl_excel(run):
    p = next(run.glob("法規爬蟲結果_*.xlsx"))
    header, rows = _rows(p)
    assert list(header) == ["法規名稱", "法規狀態", "公布日", "修正日", "施行日", "本次新增", "本次刪除",
                            "本次變動", "資料來源", "取得方式", "SHA-512", "備註"]
    pdpa = next(r for r in rows if r["法規名稱"] == PDPA)
    assert pdpa["施行日"] == "未定（由行政院定之）"
    assert "第 20-1 條" in pdpa["本次新增"] and "第 21-5 條" in pdpa["本次新增"]
    assert pdpa["本次刪除"] == "第 27 條"
    assert len(pdpa["SHA-512"]) == 128
    assert "首次執行：以法規沿革之前一版本為比對基準" in pdpa["備註"]
    fail = next(r for r in rows if r["法規名稱"] == "測試用不存在網頁")
    assert fail["備註"] == ANTI_BOT_FAIL_NOTE
    fsc = next(r for r in rows if r["法規名稱"] == FSC_RULE)
    assert "連動影響" in (fsc["備註"] or "")


def test_diff_excel(run):
    p = next(run.glob("AST條文差異比對_*.xlsx"))
    header, rows = _rows(p, "條文差異")
    assert "本次 AST 判斷新舊條文差異的依據及 rationale" in header
    by_no = {}
    for r in rows:
        by_no.setdefault((r["法規名稱"], r["條號"]), r)
    assert "罰則加重" in by_no[(PDPA, "第 48 條")]["差異類型"]
    assert by_no[(PDPA, "第 27 條")]["差異類型"].startswith("刪除條文")
    assert "Lexing" in by_no[(PDPA, "第 48 條")]["本次 AST 判斷新舊條文差異的依據及 rationale"]
    assert not any("20-2" in str(k[1]) for k in by_no)
    assert FSC_RULE in (by_no[(PDPA, "第 27 條")]["連動影響"] or "")
    assert not by_no[(PDPA, "第 48 條")]["連動影響"]
    _, links = _rows(p, "連動影響")
    assert any(l["受影響法規"] == FSC_RULE and l["母法異動類型"] == "刪除條文" for l in links)


class _AlwaysBlocked:
    """每個請求都被反爬機制擋下（5 種方法全失敗）的假 Fetcher。"""
    def get(self, url, *, method_hint=None, expect="html", wayback_ok=True):
        from regcrawler.models import FetchResult
        return FetchResult(ok=False, url=url, note=ANTI_BOT_FAIL_NOTE,
                           attempts=[{"method": m, "ok": False} for m in
                                     ("requests", "requests_alt", "playwright", "playwright_human", "wayback")])


def test_all_blocked_note_is_exact(tmp_path, monkeypatch):
    """使用真實種子清單與真實來源模組：全部被擋時，每一列備註都必須完全等於規定字串。"""
    monkeypatch.setenv("REGCRAWL_SNAPSHOT_DIR", str(tmp_path / "snap"))
    import regcrawler.fetch.chain as chain
    monkeypatch.setattr(chain, "Fetcher", lambda *a, **k: _AlwaysBlocked())
    out = tmp_path / "out"
    assert cli.main(["--out", str(out)]) == 0
    _, rows = _rows(next(out.glob("法規爬蟲結果_*.xlsx")))
    assert len(rows) >= 30
    bad = [(r["法規名稱"], r["備註"]) for r in rows if r["備註"] != ANTI_BOT_FAIL_NOTE]
    assert not bad, bad
