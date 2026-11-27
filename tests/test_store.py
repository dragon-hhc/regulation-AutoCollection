"""#4 快照儲存測試（tmp_path，不寫入 repo 的 snapshots/）。"""
import json
from datetime import datetime, timezone

import pytest

from regcrawler import store
from regcrawler.models import Article, LawDocument
from regcrawler.normalize import content_hash

SRC = "法務部全國法規資料庫"
NAME = "個人資料保護法"


def doc(text="為規範個人資料之蒐集、處理及利用", ok=True, name=NAME):
    return LawDocument(name=name, source_name=SRC, source_url="https://law.moj.gov.tw/",
                       amended_date="民國 114 年 11 月 11 日",
                       articles=[Article(no="第 1 條", text=text)] if ok else [],
                       fetched_at="2025-11-20T09:00:00+08:00")


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("REGCRAWL_SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    return tmp_path / "snapshots"


def test_new_run_id_taipei():
    assert store.new_run_id(datetime(2025, 11, 30, 17, 5, 9, tzinfo=timezone.utc)) == "20251201-010509"
    assert store.RUN_ID_RE.match(store.new_run_id())


def test_save_layout_and_content(root):
    d = doc()
    p = store.save_snapshot(d, "20251201-091700")
    assert p == root / SRC / NAME / "20251201-091700.json"
    raw = p.read_text(encoding="utf-8")
    assert "個人資料保護法" in raw and "\\u" not in raw          # ensure_ascii=False
    data = json.loads(raw)
    assert data["content_hash"] == content_hash(d) and len(data["content_hash"]) == 128
    assert data["fetched_at"] == "2025-11-20T09:00:00+08:00"
    assert data["run_id"] == "20251201-091700" and data["ok"] is True
    assert LawDocument.from_dict(data["document"]) == d


def test_bad_run_id():
    with pytest.raises(ValueError):
        store.save_snapshot(doc(), "2025-12-01")


def test_load_previous_skips_failed_and_later(root):
    store.save_snapshot(doc("v1"), "20251101-091700")
    store.save_snapshot(doc("v2"), "20251115-091700")
    store.save_snapshot(doc(ok=False), "20251201-091700")      # 失敗快照
    store.save_snapshot(doc("v4"), "20251215-091700")
    prev = store.load_previous(NAME, SRC, "20251215-091700")
    assert prev is not None and prev.articles[0].text == "v2"
    assert store.load_previous(NAME, SRC, "20251115-091700").articles[0].text == "v1"
    assert store.load_previous(NAME, SRC, "20251101-091700") is None
    assert store.load_previous(NAME, SRC, None).articles[0].text == "v4"
    assert store.load_previous("不存在的法規", SRC, "20991231-000000") is None


def test_load_previous_ignores_corrupt(root):
    store.save_snapshot(doc("v1"), "20251101-091700")
    bad = root / SRC / NAME / "20251102-091700.json"
    bad.write_text("{not json", encoding="utf-8")
    assert store.load_previous(NAME, SRC, "20251201-000000").articles[0].text == "v1"


def test_list_runs(root):
    assert store.list_runs() == []
    store.save_snapshot(doc(), "20251115-091700")
    store.save_snapshot(doc(name="資通安全管理法"), "20251101-091700")
    store.save_snapshot(doc(name="資通安全管理法"), "20251115-091700")
    assert store.list_runs() == ["20251101-091700", "20251115-091700"]


def test_safe_name_keeps_chinese_and_strips_unsafe():
    assert store.safe_name("金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法") == \
        "金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法"
    assert store.safe_name("ISO/IEC 27701:2025") == "ISO_IEC 27701_2025"
    assert store.safe_name("../../etc") not in ("..", "")
    assert "/" not in store.safe_name("../../etc")
    long = "個資" * 200
    s = store.safe_name(long)
    assert len(s.encode("utf-8")) <= 180 and s != store.safe_name("個資" * 199)


def test_metadata_only_snapshot(root):
    d = LawDocument(name="ISO/IEC 27001", source_name="ISO", source_url="https://www.iso.org/",
                    metadata_only=True, metadata={"edition": "3"})
    p = store.save_snapshot(d, "20251201-091700")
    assert p.parent.name == "ISO_IEC 27001"
    assert store.load_previous("ISO/IEC 27001", "ISO", "20251231-000000").metadata == {"edition": "3"}
