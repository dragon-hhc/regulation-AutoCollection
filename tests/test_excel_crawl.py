"""#4 subtask1 Excel 測試。"""
from openpyxl import load_workbook

from regcrawler.excel_crawl import COLUMNS, write_crawl_excel
from regcrawler.models import ANTI_BOT_FAIL_NOTE

EXPECTED = ["法規名稱", "法規狀態", "公布日", "修正日", "施行日", "本次新增", "本次刪除", "本次變動",
            "資料來源", "取得方式", "SHA-512", "備註"]


def rows():
    return [
        {"法規名稱": "個人資料保護法", "法規狀態": "現行", "公布日": "民國 84 年 08 月 11 日",
         "修正日": "民國 114 年 11 月 11 日", "施行日": "未定（由行政院定之）",
         "本次新增": ["第 1-2 條", "第 20-1 條"], "本次刪除": ["第 27 條"], "本次變動": "第 1-1 條、第 12 條",
         "資料來源": "法務部全國法規資料庫", "取得方式": "official_api", "SHA-512": "a" * 128, "備註": ""},
        {"法規名稱": "資通安全管理法", "法規狀態": "現行", "公布日": "NA", "修正日": "NA", "施行日": "NA",
         "資料來源": "法務部全國法規資料庫", "取得方式": "", "SHA-512": "", "備註": ANTI_BOT_FAIL_NOTE},
        {"法規名稱": "=SUM(A1)", "法規狀態": "草案", "資料來源": "Wayback 快照 20251112093000",
         "取得方式": "wayback", "SHA-512": "b" * 128, "備註": "Wayback 快照 20251112093000\x07"},
    ]


def test_columns_fixed():
    assert COLUMNS == EXPECTED


def test_write(tmp_path):
    p = write_crawl_excel(rows(), tmp_path / "out" / "法規爬蟲結果_20251201.xlsx")
    wb = load_workbook(p)
    assert wb.sheetnames == ["法規爬蟲結果", "執行摘要"]
    ws = wb["法規爬蟲結果"]
    assert [c.value for c in ws[1]] == EXPECTED
    assert all(c.font.bold for c in ws[1])
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref == "A1:L4"
    assert ws["B2"].alignment.wrap_text

    col = {n: i + 1 for i, n in enumerate(EXPECTED)}
    assert ws.cell(2, col["本次新增"]).value == "第 1-2 條、第 20-1 條"
    assert ws.cell(2, col["本次刪除"]).value == "第 27 條"
    assert ws.cell(2, col["施行日"]).value == "未定（由行政院定之）"
    assert ws.cell(3, col["本次新增"]).value in (None, "")
    # 淡黃：本次變動非空
    assert ws.cell(2, 1).fill.start_color.rgb == "FFFFF2CC"
    assert ws.cell(2, 12).fill.start_color.rgb == "FFFFF2CC"
    # 淡紅：反爬失敗整列
    assert all(ws.cell(3, j).fill.start_color.rgb == "FFFFC7CE" for j in range(1, 13))
    assert ws.cell(3, col["備註"]).value == ANTI_BOT_FAIL_NOTE
    # 公式注入防護、控制字元移除
    assert ws.cell(4, 1).value == "=SUM(A1)" and ws.cell(4, 1).data_type == "s"
    assert ws.cell(4, col["備註"]).value == "Wayback 快照 20251112093000"
    assert ws.cell(4, 1).fill.fill_type is None

    sm = {r[0]: r[1] for r in wb["執行摘要"].iter_rows(min_row=2, max_row=7, values_only=True)}
    assert sm["法規總數"] == 3 and sm["成功數"] == 2 and sm["反爬失敗數"] == 1
    assert sm["有變動數"] == 1 and sm["其他失敗數"] == 0 and sm["執行時間"]
    all_vals = [r for r in wb["執行摘要"].iter_rows(values_only=True)]
    src = {r[0]: r[1:5] for r in all_vals if r[0] in ("法務部全國法規資料庫", "Wayback 快照")}
    assert src["法務部全國法規資料庫"] == (2, 1, 1, 1)
    assert src["Wayback 快照"] == (1, 1, 0, 0)


def test_empty_rows(tmp_path):
    p = write_crawl_excel([], tmp_path / "e.xlsx", run_time="2025-12-01 09:17:00")
    wb = load_workbook(p)
    assert [c.value for c in wb.active[1]] == EXPECTED
    assert wb["執行摘要"]["B2"].value == "2025-12-01 09:17:00"
