"""Legal AST 第四階段：程式碼生成（Code Generation）。

- render_inline(old, new)：逐字差異，格式 ``[-刪除文字-]{+新增文字+}``；以字元為單位（中文），
  連續片段合併為一段，夾在兩段異動之間、僅 1 個字元的相同片段也併入，避免碎片化。
- diff_segments(old, new)：同上的結構化結果 [(op, text)]，op ∈ equal / delete / insert，供 Excel rich text 使用。
- render_rationale(article_diff, semantic_findings)：組出 Excel「本次 AST 判斷新舊條文差異的依據及 rationale」欄位文字。
"""
from __future__ import annotations

import difflib

from ..models import ArticleDiff

EQUAL, DELETE, INSERT = "equal", "delete", "insert"
DEL_OPEN, DEL_CLOSE, INS_OPEN, INS_CLOSE = "[-", "-]", "{+", "+}"

STAGES = ("Lexing", "Parsing", "Traversal", "Generation")


def _raw_blocks(old: str, new: str) -> list[list[str]]:
    """回傳 [[op, old_part, new_part]]，op ∈ equal / change。"""
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    blocks: list[list[str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        op = "equal" if tag == "equal" else "change"
        a, b = old[i1:i2], new[j1:j2]
        if blocks and blocks[-1][0] == op:
            blocks[-1][1] += a
            blocks[-1][2] += b
        else:
            blocks.append([op, a, b])
    return blocks


def _merge_small_equal(blocks: list[list[str]], max_len: int) -> list[list[str]]:
    if max_len <= 0:
        return blocks
    out: list[list[str]] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if (b[0] == "equal" and 0 < len(b[1]) <= max_len and "\n" not in b[1]
                and out and out[-1][0] == "change" and i + 1 < len(blocks) and blocks[i + 1][0] == "change"):
            nxt = blocks[i + 1]
            out[-1][1] += b[1] + nxt[1]
            out[-1][2] += b[2] + nxt[2]
            i += 2
            continue
        if out and out[-1][0] == b[0] == "change":
            out[-1][1] += b[1]
            out[-1][2] += b[2]
        else:
            out.append(list(b))
        i += 1
    return out


def diff_segments(old: str, new: str, *, merge_equal_le: int = 1) -> list[tuple[str, str]]:
    """結構化逐字差異：[(op, text)]，刪除片段在新增片段之前。"""
    old, new = old or "", new or ""
    segs: list[tuple[str, str]] = []
    for op, a, b in _merge_small_equal(_raw_blocks(old, new), merge_equal_le):
        if op == "equal":
            if a:
                segs.append((EQUAL, a))
        else:
            if a:
                segs.append((DELETE, a))
            if b:
                segs.append((INSERT, b))
    return segs


def segments_to_markup(segs: list[tuple[str, str]]) -> str:
    out = []
    for op, t in segs:
        if op == EQUAL:
            out.append(t)
        elif op == DELETE:
            out.append(f"{DEL_OPEN}{t}{DEL_CLOSE}")
        else:
            out.append(f"{INS_OPEN}{t}{INS_CLOSE}")
    return "".join(out)


def render_inline(old: str, new: str) -> str:
    """逐字差異：``[-刪除文字-]{+新增文字+}``。"""
    return segments_to_markup(diff_segments(old, new))


def parse_markup(markup: str) -> list[tuple[str, str]]:
    """把 ``[-x-]{+y+}`` 標記字串還原為 [(op, text)]（Excel 寫入 rich text 時使用）。"""
    segs: list[tuple[str, str]] = []
    i, buf = 0, []
    n = len(markup or "")
    while i < n:
        for open_, close, op in ((DEL_OPEN, DEL_CLOSE, DELETE), (INS_OPEN, INS_CLOSE, INSERT)):
            if markup.startswith(open_, i):
                j = markup.find(close, i + 2)
                if j != -1:
                    if buf:
                        segs.append((EQUAL, "".join(buf)))
                        buf = []
                    segs.append((op, markup[i + 2:j]))
                    i = j + 2
                    break
        else:
            buf.append(markup[i])
            i += 1
    if buf:
        segs.append((EQUAL, "".join(buf)))
    return segs


def render_rationale(article_diff: ArticleDiff, semantic_findings: list[tuple[str, str]] | None = None) -> str:
    """條列 Lexing／Parsing／Traversal／Generation 四段 + 語意規則結果。"""
    groups: dict[str, list[str]] = {s: [] for s in STAGES}
    other: list[str] = []
    for line in article_diff.rationale or []:
        for s in STAGES:
            if line.startswith(s + "：") or line.startswith(s + ":"):
                groups[s].append(line.split("：", 1)[-1] if "：" in line else line.split(":", 1)[-1])
                break
        else:
            other.append(line)
    if not groups["Generation"] and article_diff.inline_diff:
        n_del = article_diff.inline_diff.count(DEL_OPEN)
        n_ins = article_diff.inline_diff.count(INS_OPEN)
        groups["Generation"].append(f"逐字差異輸出 {n_del} 處刪除、{n_ins} 處新增（格式 [-刪除-]{{+新增+}}）")
    out = [f"差異類型判定：{article_diff.change}（條號鍵 {article_diff.key}）"]
    for i, s in enumerate(STAGES, 1):
        out.append(f"{i}. {s}")
        items = groups[s] or ["（無）"]
        out += [f"  - {x.strip()}" for x in items]
    if article_diff.node_changes:
        out.append("節點異動：" + "；".join(article_diff.node_changes))
    out.append("5. 語意規則")
    if semantic_findings:
        out += [f"  - {t}：{r}" for t, r in semantic_findings]
    else:
        out.append("  - 未觸發罰則加重／通知時限縮短／通報時限縮短")
    if other:
        out.append("備註：" + "；".join(other))
    return "\n".join(out)
