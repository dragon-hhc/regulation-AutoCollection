"""#5 subtask2 Excel 測試（以真實條文 fixture 產生 DiffRecord）。"""
import json
from pathlib import Path

from openpyxl import load_workbook

from regcrawler.models import DiffRecord, LinkageRecord, LawDocument
from regcrawler.legal_ast.diff import diff_laws, UNCHANGED
from regcrawler.legal_ast.codegen import render_rationale
from regcrawler.excel_diff import (write_diff_excel, DIFF_HEADERS, LINK_HEADERS, DIFF_SHEET, LINK_SHEET,
                                   LEGEND_SHEET, FILL_ORANGE, FILL_RED, FILL_GREEN)

FIX = Path(__file__).parent / "fixtures" / "legal_ast"


def load(name):
    d = json.loads((FIX / name).read_text(encoding="utf-8"))
    d.pop("_fixture_source")
    return LawDocument.from_dict(d)


def make_records():
    old, new = load("pdpa_112.json"), load("pdpa_114.json")
    recs = []
    for d in diff_laws(old, new):
        types = [d.change]
        if d.key == "48":
            types.append("罰則加重")   # 模擬 #6 語意規則輸出，只測上色
        recs.append(DiffRecord(law_name=new.name, law_status="現行", article_no=d.new_no or d.old_no,
                               diff_types=types, old_text=d.old_text, new_text=d.new_text,
                               inline_diff=d.inline_diff, linkage="",
                               rationale=render_rationale(d, []), source=new.source_url))
    recs.append(DiffRecord(law_name=new.name, law_status="現行", article_no="第 1 條", diff_types=[UNCHANGED],
                           old_text="x", new_text="x", inline_diff="x"))
    return recs


def test_write_and_reopen(tmp_path):
    links = [LinkageRecord(affected_law="金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法",
                           affected_article="第 1 條", parent_article="個人資料保護法第 27 條",
                           parent_change="刪除條文",
                           explanation="本辦法依個人資料保護法(以下簡稱本法)第二十七條第三項規定訂定之。")]
    p = write_diff_excel(make_records(), links, tmp_path / "out" / "AST條文差異比對_20261127.xlsx")
    wb = load_workbook(p, rich_text=True)
    assert wb.sheetnames == [DIFF_SHEET, LINK_SHEET, LEGEND_SHEET]
    ws = wb[DIFF_SHEET]
    assert [c.value for c in ws[1]] == DIFF_HEADERS
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref.startswith("A1:J")
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert [r[2] for r in rows] == ["第 12 條", "第 20-1 條", "第 27 條", "第 48 條"]   # 未變動不輸出
    by_no = {ws.cell(row=i, column=3).value: i for i in range(2, ws.max_row + 1)}
    assert ws.cell(row=by_no["第 48 條"], column=4).value == "修正條文；罰則加重"
    assert ws.cell(row=by_no["第 48 條"], column=1).fill.start_color.rgb.endswith(FILL_ORANGE.start_color.rgb[-6:])
    assert ws.cell(row=by_no["第 27 條"], column=5).fill.start_color.rgb.endswith(FILL_RED.start_color.rgb[-6:])
    assert ws.cell(row=by_no["第 20-1 條"], column=1).fill.start_color.rgb.endswith(FILL_GREEN.start_color.rgb[-6:])
    assert ws.cell(row=by_no["第 12 條"], column=6).alignment.wrap_text

    # 逐字差異欄：rich text 紅色刪除線 / 綠色底線
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    v = ws.cell(row=by_no["第 12 條"], column=7).value
    assert isinstance(v, CellRichText)
    blocks = [b for b in v if isinstance(b, TextBlock)]
    assert any(b.font.strike and b.text == "違反本法規定，致" for b in blocks)
    assert any(b.font.u == "single" and b.text == "知悉所保有之" for b in blocks)

    wl = wb[LINK_SHEET]
    assert [c.value for c in wl[1]] == LINK_HEADERS
    assert wl.cell(row=2, column=4).value == "刪除條文"
    assert "Lexing" in "".join(str(c.value) for c in wb[LEGEND_SHEET]["B"])


def test_rationale_column_and_plain_fallback(tmp_path):
    recs = make_records()
    recs[0].inline_diff = "沒有差異標記"
    p = write_diff_excel(recs, [], tmp_path / "x.xlsx")
    ws = load_workbook(p)[DIFF_SHEET]
    assert ws.cell(row=2, column=7).value == "沒有差異標記"
    rat = ws.cell(row=2, column=9).value
    for s in ("1. Lexing", "2. Parsing", "3. Traversal", "4. Generation", "5. 語意規則"):
        assert s in rat


def test_empty_inputs(tmp_path):
    p = write_diff_excel([], [], tmp_path / "empty.xlsx")
    wb = load_workbook(p)
    assert wb[DIFF_SHEET].max_row == 1 and wb[LINK_SHEET].max_row == 1
