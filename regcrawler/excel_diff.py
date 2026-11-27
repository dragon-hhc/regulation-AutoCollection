"""subtask2 Excel：AST 條文差異比對（SPEC §7）。

write_diff_excel(records, linkages, path)
- 工作表「條文差異」：法規名稱｜法規狀態｜條號｜差異類型｜舊條文｜新條文｜逐字差異｜連動影響｜
  本次 AST 判斷新舊條文差異的依據及 rationale｜資料來源
  逐字差異以 rich text 呈現：刪除片段紅色刪除線、新增片段綠色底線（失敗則退回純文字標記）。
  列底色：罰則加重／通知時限縮短／通報時限縮短 → 淡橘；刪除條文 → 淡紅；新增條文 → 淡綠。
  diff_types 只有「未變動」（或為空）的紀錄不輸出。
- 工作表「連動影響」：受影響法規｜受影響條文｜所依據之母法條文｜母法異動類型｜說明
- 工作表「圖例與方法說明」：四階段 AST 方法與標記格式。
"""
from __future__ import annotations

import os
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import (DiffRecord, LinkageRecord, DIFF_ADDED, DIFF_DELETED, DIFF_PENALTY_UP,
                     DIFF_NOTICE_SHORTER, DIFF_REPORT_SHORTER)
from .legal_ast.codegen import parse_markup, EQUAL, DELETE, INSERT

DIFF_SHEET = "條文差異"
LINK_SHEET = "連動影響"
LEGEND_SHEET = "圖例與方法說明"

DIFF_HEADERS = ["法規名稱", "法規狀態", "條號", "差異類型", "舊條文", "新條文", "逐字差異", "連動影響",
                "本次 AST 判斷新舊條文差異的依據及 rationale", "資料來源"]
LINK_HEADERS = ["受影響法規", "受影響條文", "所依據之母法條文", "母法異動類型", "說明"]
_DIFF_WIDTHS = [22, 9, 12, 18, 50, 50, 60, 30, 70, 40]
_LINK_WIDTHS = [36, 14, 30, 18, 70]

FILL_ORANGE = PatternFill("solid", start_color="FFE5CC", end_color="FFE5CC")
FILL_RED = PatternFill("solid", start_color="FFD6D6", end_color="FFD6D6")
FILL_GREEN = PatternFill("solid", start_color="DDF2DD", end_color="DDF2DD")
FILL_HEADER = PatternFill("solid", start_color="DDE4EE", end_color="DDE4EE")
COLOR_DEL = "C00000"
COLOR_INS = "008000"
UNCHANGED = "未變動"
_EXCEL_CELL_MAX = 32767

_WRAP = Alignment(wrap_text=True, vertical="top")


def row_fill(diff_types: list[str]) -> PatternFill | None:
    t = set(diff_types or [])
    if t & {DIFF_PENALTY_UP, DIFF_NOTICE_SHORTER, DIFF_REPORT_SHORTER}:
        return FILL_ORANGE
    if DIFF_DELETED in t:
        return FILL_RED
    if DIFF_ADDED in t:
        return FILL_GREEN
    return None


def _clip(s: str) -> str:
    s = s or ""
    if len(s) > _EXCEL_CELL_MAX:
        note = "…（超過 Excel 儲存格上限，已截斷）"
        return s[:_EXCEL_CELL_MAX - len(note)] + note
    return s


def _illegal_free(s: str) -> str:
    """移除 openpyxl 不接受的控制字元（不改變可見原文）。"""
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    return ILLEGAL_CHARACTERS_RE.sub("", s or "")


def _rich_inline(markup: str):
    """把 [-x-]{+y+} 標記轉成 CellRichText；失敗回傳 None。"""
    try:
        from openpyxl.cell.rich_text import CellRichText, TextBlock
        from openpyxl.cell.text import InlineFont
    except Exception:  # pragma: no cover - openpyxl < 3.1
        return None
    try:
        segs = parse_markup(markup)
        if not any(op != EQUAL for op, _ in segs):
            return None
        f_del = InlineFont(color=COLOR_DEL, strike=True)
        f_ins = InlineFont(color=COLOR_INS, u="single")
        parts = []
        for op, t in segs:
            if not t:
                continue
            t = _illegal_free(t)
            if op == DELETE:
                parts.append(TextBlock(f_del, t))
            elif op == INSERT:
                parts.append(TextBlock(f_ins, t))
            else:
                parts.append(t)
        rt = CellRichText(*parts)
        if len(str(rt)) > _EXCEL_CELL_MAX:
            return None
        return rt
    except Exception:
        return None


def _setup_sheet(ws, headers, widths):
    ws.append(headers)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        c = ws.cell(row=1, column=i)
        c.font = Font(bold=True)
        c.fill = FILL_HEADER
        c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.freeze_panes = "A2"


def _is_unchanged(r: DiffRecord) -> bool:
    types = [t for t in (r.diff_types or []) if t and t != UNCHANGED]
    return not types


def write_diff_excel(records: list[DiffRecord], linkages: list[LinkageRecord], path) -> str:
    path = Path(path)
    if path.parent and not path.parent.exists():
        os.makedirs(path.parent, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = DIFF_SHEET
    _setup_sheet(ws, DIFF_HEADERS, _DIFF_WIDTHS)

    for r in records or []:
        if r is None or _is_unchanged(r):
            continue
        types = [t for t in r.diff_types if t and t != UNCHANGED]
        values = [r.law_name, r.law_status, r.article_no, "；".join(dict.fromkeys(types)),
                  r.old_text, r.new_text, r.inline_diff, r.linkage, r.rationale, r.source]
        ws.append([_clip(_illegal_free(v if isinstance(v, str) else ("" if v is None else str(v)))) for v in values])
        row = ws.max_row
        rich = _rich_inline(r.inline_diff or "")
        if rich is not None:
            ws.cell(row=row, column=7).value = rich
        fill = row_fill(types)
        for col in range(1, len(DIFF_HEADERS) + 1):
            c = ws.cell(row=row, column=col)
            c.alignment = _WRAP
            if fill is not None:
                c.fill = fill
    ws.auto_filter.ref = f"A1:{get_column_letter(len(DIFF_HEADERS))}{max(ws.max_row, 1)}"

    wl = wb.create_sheet(LINK_SHEET)
    _setup_sheet(wl, LINK_HEADERS, _LINK_WIDTHS)
    for lk in linkages or []:
        if lk is None:
            continue
        wl.append([_clip(_illegal_free(x or "")) for x in
                   (lk.affected_law, lk.affected_article, lk.parent_article, lk.parent_change, lk.explanation)])
        for col in range(1, len(LINK_HEADERS) + 1):
            wl.cell(row=wl.max_row, column=col).alignment = _WRAP
    wl.auto_filter.ref = f"A1:{get_column_letter(len(LINK_HEADERS))}{max(wl.max_row, 1)}"

    _write_legend(wb.create_sheet(LEGEND_SHEET))
    wb.save(path)
    return str(path)


_LEGEND = [
    ("項目", "說明"),
    ("方法總覽", "本表以法律條文抽象語法樹（Legal AST）比對新舊版本，分為四個明確分離的階段："
              "Lexing → Parsing → Traversal → Generation，最後再套用語意規則。"),
    ("1. Lexing（詞法分析）", "把條文原文切成 token 並保留原文起訖位置：ARTICLE_NO（條號）、PARA_BREAK（項，以換行分隔）、"
                         "SUBPARA_MARK（款「一、」）、ITEM_MARK（目「（一）」）、SUBITEM_MARK（「1.」「(1)」）、"
                         "AMOUNT（金額，如「新臺幣十五萬元」）、DURATION（期間，如「七十二小時內」「即時」）、"
                         "PENALTY_KW（罰鍰、罰金、有期徒刑、拘役、按次處罰）、NOTICE_KW（通知）、REPORT_KW（通報）、"
                         "REF（條文引用，如「第二十條之一第一項」）、TEXT。"),
    ("2. Parsing（語法分析）", "依 token 建樹：法規 →（章）→ 條 → 項 → 款 → 目 → 子目；英文 GDPR 為 Article → paragraph「1.」→ point「(a)」。"
                          "中文條文的項沒有編號，以換行判定。每個節點有正規化鍵，例如 20-1/p2/s3 = 第20-1條第2項第3款。"
                          "條號以正規化鍵對齊：第二十條之一 = 第 20-1 條；輸出仍用原文條號。"),
    ("3. Traversal（遍歷與轉換）", "以條號鍵對齊新舊條文：舊無新有＝新增條文；舊有新無或新條文為「（刪除）」＝刪除條文；"
                              "正規化文字不同＝修正條文。修正條文再逐層（項→款→目）以內容對齊做樹狀比對，"
                              "列出「第2項 修正」「第3項 新增」「第1項第2款 刪除」「次序變更」等節點異動。"
                              "舊條消失而另一新條相似度 ≥ 0.85 時，註記「疑似條次移列」，但差異類型仍依條號判定。"),
    ("4. Generation（產生輸出）", "以字元為單位產生逐字差異並合併連續片段；同時組出本欄 rationale。"),
    ("5. 語意規則", "罰則加重（AMOUNT／PENALTY_KW 比較）、通知時限縮短（NOTICE_KW + DURATION）、通報時限縮短（REPORT_KW + DURATION）。"
                 "「通知」= 對當事人；「通報」= 對主管機關，依原文用字判斷，不互換。"),
    ("逐字差異標記", "[-刪除文字-]：舊版有、新版刪除（Excel 中以紅色刪除線顯示）；{+新增文字+}：新版新增（綠色底線顯示）。"),
    ("列底色：淡橘", "差異類型含「罰則加重」「通知時限縮短」或「通報時限縮短」。"),
    ("列底色：淡紅", "刪除條文。"),
    ("列底色：淡綠", "新增條文。"),
    ("差異類型", "固定用語：新增條文、刪除條文、修正條文、罰則加重、通知時限縮短、通報時限縮短、連動影響；多個以「；」連接。"),
    ("連動影響工作表", "子法／授權命令所依據之母法條文被刪除或修正時，列出受影響法規與條文。"),
    ("原文原則", "法規名稱、條號、條文一律原文照錄；正規化僅用於比對與雜湊，不用於輸出。"),
]


def _write_legend(ws):
    for i, row in enumerate(_LEGEND):
        ws.append(list(row))
        for col in (1, 2):
            c = ws.cell(row=i + 1, column=col)
            c.alignment = _WRAP
            if i == 0:
                c.font = Font(bold=True)
                c.fill = FILL_HEADER
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 110
    for r, fill in ((9, FILL_ORANGE), (10, FILL_RED), (11, FILL_GREEN)):
        ws.cell(row=r, column=1).fill = fill
    ws.freeze_panes = "A2"
