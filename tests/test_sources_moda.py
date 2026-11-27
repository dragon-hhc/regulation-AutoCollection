"""regcrawler.sources.moda 單元測試（離線；fixtures 見 tests/fixtures/moda/ 檔頭來源說明）。"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from regcrawler.models import ANTI_BOT_FAIL_NOTE, NA, STATUS_DRAFT, FetchResult, SeedEntry
from regcrawler.sources import moda

FX = Path(__file__).parent / "fixtures" / "moda"
INCIDENT = "個人資料事故通知通報及應變辦法"
GAZETTE_INCIDENT = ("https://gazette.nat.gov.tw/EG_FileManager/eguploadpub/eg032015/ch01/type3/gov79/"
                    "num1/images/Eg01.pdf")


class FakeFetcher:
    def __init__(self, mapping: dict[str, bytes]):
        self.mapping = mapping
        self.calls: list[str] = []

    def get(self, url, *, method_hint=None, expect="html", wayback_ok=True):
        self.calls.append(url)
        if url in self.mapping:
            return FetchResult(ok=True, url=url, content=self.mapping[url], method_used="requests", final_url=url)
        return FetchResult(ok=False, url=url, note=ANTI_BOT_FAIL_NOTE)


def _rows():
    return json.loads((FX / "draft_incident_rows.json").read_text(encoding="utf-8"))["rows"]


def _docx_from_rows(rows) -> bytes:
    docx = pytest.importorskip("docx")
    d = docx.Document()
    t = d.add_table(rows=0, cols=2)
    for r in rows:
        cells = t.add_row().cells
        cells[0].text = r[0] or ""
        cells[1].text = r[1] or ""
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_find_announcements_history_page():
    html = (FX / "DraftForum_TypeH.html").read_text(encoding="utf-8")
    anns = moda.find_announcements(html, INCIDENT, "https://law.pdpc.gov.tw/DraftForum.aspx?Type=H")
    assert len(anns) == 1
    a = anns[0]
    assert a["page"] == "https://join.gov.tw/policies/gazette/metaId/163195"
    assert (a["announce_date"], a["end_date"]) == ("115.01.22", "115.03.23")
    # 不可誤配到施行細則或其他草案
    assert moda.find_announcements(html, "個人資料保護法", "https://law.pdpc.gov.tw/") [0]["announce_date"] == "113.12.20"


def test_find_attachments_prefers_gazette_draft():
    html = (FX / "join_8bb40e4f.html").read_text(encoding="utf-8")
    att = moda.find_attachments(html, "https://join.gov.tw/policies/detail/8bb40e4f-cf20-45f1-aa47-6a42958e323b")
    assert att == [GAZETTE_INCIDENT]


def test_parse_draft_rows_keeps_text_and_merges_page_break():
    arts = moda.parse_draft_rows(_rows())
    assert [a.no for a in arts] == ["第一條", "第二條", "第七條"]
    assert arts[0].text == "本辦法依個人資料保護法（以下簡稱本法）第十二條第四項規定訂定之。"
    lines = arts[1].text.split("\n")
    assert lines[0].startswith("公務機關或非公務機關（以下簡稱事故機關）")
    assert "三、所採個別通知當事人之方式需費過鉅，將影響事故機關之營運。" in lines  # 跨頁續列併入第二條
    assert lines[-1].startswith("事故機關有正當事由未能於第一項所定期限內通知當事人者")
    assert arts[2].text == "本辦法施行日期，由主管機關定之。"


def test_join_wrapped():
    assert moda.join_wrapped("本辦法依個人資料保護法（以下簡\n稱本法）規定訂定之。\n一、甲。") == \
        "本辦法依個人資料保護法（以下簡稱本法）規定訂定之。\n一、甲。"


def test_fetch_draft_end_to_end_with_docx_attachment():
    docx_bytes = _docx_from_rows(_rows())
    f = FakeFetcher({
        "https://law.pdpc.gov.tw/DraftForum.aspx": (FX / "DraftForum_current_empty.html").read_bytes(),
        "https://law.pdpc.gov.tw/DraftForum.aspx?Type=H": (FX / "DraftForum_TypeH.html").read_bytes(),
        "https://join.gov.tw/policies/gazette/metaId/163195": (FX / "join_8bb40e4f.html").read_bytes(),
        GAZETTE_INCIDENT: docx_bytes,
    })
    e = SeedEntry(name=INCIDENT, source="moda", status=STATUS_DRAFT, doc_type="草案",
                  parent_law="個人資料保護法")
    [doc] = moda.fetch([e], f)
    assert doc.status == STATUS_DRAFT
    assert doc.source_name == moda.SOURCE_NAME
    assert doc.source_url == "https://join.gov.tw/policies/gazette/metaId/163195"
    assert [a.no for a in doc.articles] == ["第一條", "第二條", "第七條"]
    assert doc.version_label == "預告 115.01.22"
    assert "個資籌查字第1140500549號" in doc.history     # 由已查證紀錄補上
    assert (doc.promulgated_date, doc.amended_date, doc.effective_date) == (NA, NA, NA)
    assert doc.metadata["草案附件"] == GAZETTE_INCIDENT
    assert doc.fetch_method == "requests"
    assert doc.parent_law == "個人資料保護法"
    # 只有一次預告 → 無前版
    assert moda.fetch_previous(e, doc, f) is None
    assert "查無前次預告版本" in doc.fetch_note


def test_fetch_uses_known_record_when_list_pages_blocked():
    docx_bytes = _docx_from_rows(_rows())
    f = FakeFetcher({GAZETTE_INCIDENT: docx_bytes})
    [doc] = moda.fetch([SeedEntry(name=INCIDENT, source="moda", status=STATUS_DRAFT)], f)
    assert doc.articles
    assert "改用已查證之預告紀錄" in doc.fetch_note
    assert ANTI_BOT_FAIL_NOTE in doc.fetch_note          # 列表頁與預告頁皆失敗的原因有記錄


def test_fetch_failure_is_reported_not_raised():
    f = FakeFetcher({})
    docs = moda.fetch([SeedEntry(name=INCIDENT, source="moda", status=STATUS_DRAFT),
                       SeedEntry(name="不存在的草案辦法", source="moda", status=STATUS_DRAFT)], f)
    assert len(docs) == 2
    assert not docs[0].articles and ANTI_BOT_FAIL_NOTE in docs[0].fetch_note
    assert "查無此草案預告" in docs[1].fetch_note


def test_extract_articles_rejects_unknown_format():
    arts, how = moda.extract_articles(b"<html></html>", "https://example.invalid/x")
    assert arts == [] and "無法辨識" in how
