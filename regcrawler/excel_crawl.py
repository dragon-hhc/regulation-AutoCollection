"""#4 subtask1 Excel：法規爬蟲結果_YYYYMMDD.xlsx（SPEC §7）。

rows：list[dict]，key 即 COLUMNS 欄名；缺漏的 key 輸出空白。
「本次新增／本次刪除／本次變動」可給字串，或原文條號 list（以「、」連接）。
文字一律原文照錄；僅移除 Excel 不接受的控制字元。
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import ANTI_BOT_FAIL_NOTE, NA

TPE = timezone(timedelta(hours=8))

COLUMNS = ["法規名稱", "法規狀態", "公布日", "修正日", "施行日", "本次新增", "本次刪除", "本次變動",
           "資料來源", "取得方式", "SHA-512", "備註"]
LIST_COLUMNS = ("本次新增", "本次刪除", "本次變動")
COL_WIDTHS = {"法規名稱": 36, "法規狀態": 9, "公布日": 18, "修正日": 18, "施行日": 22, "本次新增": 28,
              "本次刪除": 20, "本次變動": 36, "資料來源": 28, "取得方式": 16, "SHA-512": 44, "備註": 50}

FILL_ANTI_BOT = PatternFill("solid", start_color="FFFFC7CE", end_color="FFFFC7CE")   # 淡紅
FILL_CHANGED = PatternFill("solid", start_color="FFFFF2CC", end_color="FFFFF2CC")    # 淡黃
FILL_HEADER = PatternFill("solid", start_color="FFD9E1F2", end_color="FFD9E1F2")
WRAP = Alignment(wrap_text=True, vertical="top")
_MAX_CELL = 32767


def _cell_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = "、".join(str(x) for x in v if str(x))
    s = ILLEGAL_CHARACTERS_RE.sub("", str(v))
    if len(s) > _MAX_CELL:
        s = s[: _MAX_CELL - 20] + "…（超過 Excel 上限截斷）"
    return s


def _put(ws, row: int, col: int, value: str):
    c = ws.cell(row=row, column=col, value=value)
    if value.startswith("="):
        c.data_type = "s"   # 避免被當成公式
    c.alignment = WRAP
    return c


def _source_key(v: str) -> str:
    return "Wayback 快照" if v.startswith("Wayback 快照") else (v or "（未標示）")


def _is_success(r: dict) -> bool:
    sha = _cell_value(r.get("SHA-512")).strip()
    return _cell_value(r.get("備註")) != ANTI_BOT_FAIL_NOTE and sha not in ("", NA)


def _has_change(r: dict) -> bool:
    return any(_cell_value(r.get(k)).strip() for k in LIST_COLUMNS)


def write_crawl_excel(rows: list[dict], path, *, run_time: str | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "法規爬蟲結果"

    bold = Font(bold=True)
    for j, name in enumerate(COLUMNS, 1):
        c = ws.cell(row=1, column=j, value=name)
        c.font = bold
        c.fill = FILL_HEADER
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        ws.column_dimensions[get_column_letter(j)].width = COL_WIDTHS.get(name, 18)

    for i, r in enumerate(rows, 2):
        vals = [_cell_value(r.get(k)) for k in COLUMNS]
        fill = None
        if vals[COLUMNS.index("備註")] == ANTI_BOT_FAIL_NOTE:
            fill = FILL_ANTI_BOT
        elif vals[COLUMNS.index("本次變動")].strip():
            fill = FILL_CHANGED
        for j, v in enumerate(vals, 1):
            c = _put(ws, i, j, v)
            if fill is not None:
                c.fill = fill

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{max(1, len(rows) + 1)}"

    # ── 執行摘要 ──
    sm = wb.create_sheet("執行摘要")
    run_time = run_time or datetime.now(TPE).strftime("%Y-%m-%d %H:%M:%S（Asia/Taipei）")
    total = len(rows)
    ok = sum(1 for r in rows if _is_success(r))
    anti = sum(1 for r in rows if _cell_value(r.get("備註")) == ANTI_BOT_FAIL_NOTE)
    changed = sum(1 for r in rows if _has_change(r))
    items = [("執行時間", run_time), ("法規總數", total), ("成功數", ok), ("反爬失敗數", anti),
             ("其他失敗數", total - ok - anti), ("有變動數", changed)]
    sm.cell(row=1, column=1, value="項目").font = bold
    sm.cell(row=1, column=2, value="數值").font = bold
    for i, (k, v) in enumerate(items, 2):
        sm.cell(row=i, column=1, value=k)
        sm.cell(row=i, column=2, value=v)

    start = len(items) + 3
    hdr = ["資料來源", "法規數", "成功", "反爬失敗", "有變動"]
    for j, h in enumerate(hdr, 1):
        sm.cell(row=start, column=j, value=h).font = bold
    stats: "OrderedDict[str, list[int]]" = OrderedDict()
    for r in rows:
        s = stats.setdefault(_source_key(_cell_value(r.get("資料來源"))), [0, 0, 0, 0])
        s[0] += 1
        s[1] += _is_success(r)
        s[2] += _cell_value(r.get("備註")) == ANTI_BOT_FAIL_NOTE
        s[3] += _has_change(r)
    for i, (k, s) in enumerate(stats.items(), start + 1):
        _put(sm, i, 1, k)
        for j, v in enumerate(s, 2):
            sm.cell(row=i, column=j, value=v)

    start2 = start + len(stats) + 2
    sm.cell(row=start2, column=1, value="取得方式").font = bold
    sm.cell(row=start2, column=2, value="法規數").font = bold
    meth: "OrderedDict[str, int]" = OrderedDict()
    for r in rows:
        k = _cell_value(r.get("取得方式")) or "（無）"
        meth[k] = meth.get(k, 0) + 1
    for i, (k, n) in enumerate(meth.items(), start2 + 1):
        _put(sm, i, 1, k)
        sm.cell(row=i, column=2, value=n)

    sm.column_dimensions["A"].width = 34
    for col in "BCDE":
        sm.column_dimensions[col].width = 14
    sm.freeze_panes = "A2"

    wb.save(path)
    return path


__all__ = ["write_crawl_excel", "COLUMNS"]
