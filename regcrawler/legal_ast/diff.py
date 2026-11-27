"""Legal AST 第三階段：遍歷與轉換（Traversal / Transformation）。

diff_laws(old, new) -> list[ArticleDiff]（含 change="未變動"）
1. 以 normalize.article_key 對齊新舊條文（第二十條之一 = 第 20-1 條）。
2. 條文層級判定：舊無新有 → 新增條文；舊有新無 → 刪除條文；新條文為「（刪除）」標示 → 刪除條文；
   norm_text 不同 → 修正條文；相同 → 未變動。
   （全國法規資料庫對已刪除條文保留條號、內文顯示為「（刪除）」，例如個人資料保護法第 27 條，
    查證：https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=I0050021 ，2026-11-27）
3. 修正條文做節點層級遞迴比對（Tree diff）：項／款／目／子目逐層以內容對齊（去除款目標記後的正規化文字，
   difflib 序列比對 + 相似度配對），產生 node_changes。
4. 條次移動：舊條消失（或改為（刪除））而另一新條 norm_text 相似度 ≥ 0.85 → rationale 註記
   「疑似條次移列：舊第X條 → 新第Y條」，change 仍依條號判定。
"""
from __future__ import annotations

import difflib
import re
import unicodedata

from ..models import (Article, ArticleDiff, LawDocument, DIFF_ADDED, DIFF_DELETED, DIFF_MODIFIED)
from ..normalize import article_key, norm_text
from . import lexer as L
from .codegen import render_inline, diff_segments, DELETE, INSERT
from .parser import Node, parse_article, structure_summary, position_label

UNCHANGED = "未變動"
MOVE_THRESHOLD = 0.85
PAIR_THRESHOLD = 0.5          # 節點配對（視為「修正」而非「刪除＋新增」）的最低相似度

# 刪除標示：「（刪除）」「(刪除)」「刪除」「（本條刪除）」「（刪除）。」等，NFKC 後比對
_DELETED_RE = re.compile(r"^[(（\[【]?\s*(?:本條)?(?:已)?刪除\s*[)）\]】]?[。.]?$")
_EN_DELETED_RE = re.compile(r"^\(?(?:deleted|repealed)\)?\.?$", re.I)

_SEMANTIC_TYPES = (L.AMOUNT, L.DURATION, L.PENALTY_KW, L.NOTICE_KW, L.REPORT_KW)


def is_deletion_marker(text: str) -> bool:
    t = norm_text(text)
    return bool(t) and bool(_DELETED_RE.match(t) or _EN_DELETED_RE.match(t))


# ---------------- 節點比對 ----------------
def _content(node: Node) -> str:
    """節點自身內容（去除行首款目標記後正規化），作為對齊依據。"""
    own = node.own_text
    if node.label and own.lstrip(" 　\t").startswith(node.label):
        own = own.lstrip(" 　\t")[len(node.label):]
    return norm_text(own)


def _sig(node: Node) -> str:
    return _content(node) + "{" + "|".join(_sig(c) for c in node.children) + "}"


def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < PAIR_THRESHOLD * 0.8:
        return sm.real_quick_ratio()
    return sm.ratio()


def _node_sim(o: Node, w: Node) -> float:
    """節點相似度：以自身內容（去標記）為主；兩者皆無自身內容時改用完整文字。"""
    a, b = _content(o), _content(w)
    if a or b:
        return _ratio(a, b)
    return _ratio(norm_text(o.text), norm_text(w.text))


def _pair_block(olds: list[Node], news: list[Node]) -> list[tuple[Node | None, Node | None]]:
    """在 replace 區塊內，以動態規劃找出保序、相似度總和最大的配對。"""
    n, m = len(olds), len(news)
    sim = [[_node_sim(o, w) for w in news] for o in olds]
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best = max(dp[i + 1][j], dp[i][j + 1])
            if sim[i][j] >= PAIR_THRESHOLD:
                best = max(best, sim[i][j] + dp[i + 1][j + 1])
            dp[i][j] = best
    out: list[tuple[Node | None, Node | None]] = []
    i = j = 0
    while i < n and j < m:
        if sim[i][j] >= PAIR_THRESHOLD and abs(dp[i][j] - (sim[i][j] + dp[i + 1][j + 1])) < 1e-9:
            out.append((olds[i], news[j]))
            i += 1
            j += 1
        elif dp[i][j] == dp[i + 1][j]:
            out.append((olds[i], None))
            i += 1
        else:
            out.append((None, news[j]))
            j += 1
    out += [(o, None) for o in olds[i:]] + [(None, w) for w in news[j:]]
    return out


def align_nodes(olds: list[Node], news: list[Node]) -> list[tuple[Node | None, Node | None]]:
    """同層節點對齊：先以完整內容簽章做序列比對（equal 直接配對），replace 區塊再以相似度配對。"""
    sm = difflib.SequenceMatcher(None, [_sig(o) for o in olds], [_sig(w) for w in news], autojunk=False)
    pairs: list[tuple[Node | None, Node | None]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            pairs += list(zip(olds[i1:i2], news[j1:j2]))
        elif tag == "delete":
            pairs += [(o, None) for o in olds[i1:i2]]
        elif tag == "insert":
            pairs += [(None, w) for w in news[j1:j2]]
        else:
            pairs += _pair_block(olds[i1:i2], news[j1:j2])
    return pairs


def _loc(node: Node) -> str:
    return position_label(node.key, node.english) or node.key


def tree_diff(old: Node, new: Node) -> tuple[list[str], list[str]]:
    """遞迴比對兩棵（子）樹。回傳 (node_changes, traversal_notes)。"""
    changes: list[str] = []
    notes: list[str] = []
    for o, w in align_nodes(old.children, new.children):
        if o is None:
            changes.append(f"{_loc(w)} 新增")
            notes.append(f"{_loc(w)}為新增節點（{w.kind}）")
        elif w is None:
            changes.append(f"{_loc(o)} 刪除")
            notes.append(f"舊{_loc(o)}在新版無對應節點，判為刪除（{o.kind}）")
        elif _sig(o) == _sig(w):
            if _loc(o) != _loc(w):
                changes.append(f"{_loc(w)} 次序變更（原{_loc(o)}）")
                notes.append(f"舊{_loc(o)}內容未變，因前序節點增刪移為{_loc(w)}")
        else:
            if _content(o) != _content(w):
                label = f"{_loc(w)} 修正" + (f"（原{_loc(o)}）" if _loc(o) != _loc(w) else "")
                changes.append(label)
                r = _ratio(_content(o), _content(w))
                notes.append(f"舊{_loc(o)}與新{_loc(w)}對齊（相似度 {r:.2f}），自身文字不同，判為修正")
            sub_c, sub_n = tree_diff(o, w)
            changes += sub_c
            notes += sub_n
    return changes, notes


# ---------------- 條文層級 ----------------
def _sort_key(key: str, idx: int):
    m = re.fullmatch(r"(?:art)?(\d+)(?:-(\d+))?", key)
    if m:
        return (0, int(m.group(1)), int(m.group(2) or 0), idx)
    return (1, 0, 0, idx)


def _index(doc: LawDocument | None) -> tuple[dict[str, Article], list[str], list[str]]:
    """key → Article；重複條號保留第一筆並加註。"""
    out: dict[str, Article] = {}
    order: list[str] = []
    dup: list[str] = []
    if doc is None:
        return out, order, dup
    for a in doc.articles:
        k = article_key(a.no)
        if k in out:
            dup.append(a.no)
            k = f"{k}#dup{sum(1 for x in order if x.startswith(k))}"
        out[k] = a
        order.append(k)
    return out, order, dup


def _lex_summary(label: str, node: Node) -> str:
    toks = []
    for n in node.walk():
        toks += n.tokens if n.kind != "Article" else []
    counts = L.count_types(toks)
    st = structure_summary(node)
    struct = "、".join(f"{v} 個{k}" for k, v in st.items() if v or k in ("項",))
    sem = "、".join(f"{t} {counts.get(t, 0)}" for t in _SEMANTIC_TYPES if counts.get(t))
    return (f"Lexing：{label}條文 {struct}（token {len(toks)} 個"
            + (f"；{sem}" if sem else "") + "）")


def _semantic_token_changes(old: Node, new: Node) -> list[str]:
    def bag(n: Node) -> dict[str, list[str]]:
        d: dict[str, list[str]] = {}
        for x in n.walk():
            for t in x.tokens if x.kind != "Article" else []:
                if t.type in _SEMANTIC_TYPES or t.type == L.REF:
                    d.setdefault(t.type, []).append(unicodedata.normalize("NFKC", t.text).replace(" ", ""))
        return d
    bo, bn = bag(old), bag(new)
    out = []
    for t in _SEMANTIC_TYPES + (L.REF,):
        o, w = bo.get(t, []), bn.get(t, [])
        removed = [x for x in dict.fromkeys(o) if o.count(x) > w.count(x)]
        added = [x for x in dict.fromkeys(w) if w.count(x) > o.count(x)]
        if removed or added:
            parts = []
            if removed:
                parts.append("移除「" + "」「".join(removed) + "」")
            if added:
                parts.append("新增「" + "」「".join(added) + "」")
            out.append(f"Traversal：{t} token 異動：" + "，".join(parts))
    return out


def _generation_note(old_text: str, new_text: str) -> str:
    segs = diff_segments(old_text, new_text)
    nd = sum(1 for op, _ in segs if op == DELETE)
    ni = sum(1 for op, _ in segs if op == INSERT)
    return f"Generation：以字元層級 SequenceMatcher 產生逐字差異，{nd} 處刪除、{ni} 處新增（格式 [-刪除-]{{+新增+}}，連續片段合併）"


def diff_article(old: Article | None, new: Article | None) -> ArticleDiff:
    """單條比對（old/new 至少一個非 None）。"""
    ref = new or old
    key = article_key(ref.no)
    o_no = old.no if old else ""
    n_no = new.no if new else ""
    o_text = old.text if old else ""
    n_text = new.text if new else ""
    rat: list[str] = []
    o_node = parse_article(old) if old else None
    n_node = parse_article(new) if new else None
    if o_node:
        rat.append(_lex_summary("舊", o_node))
    if n_node:
        rat.append(_lex_summary("新", n_node))
    if old and new:
        rat.append(f"Parsing：以 article_key={key} 對齊（舊「{o_no}」↔ 新「{n_no}」）")
    elif new:
        rat.append(f"Parsing：新版「{n_no}」（article_key={key}）在舊版無對應條號")
    else:
        rat.append(f"Parsing：舊版「{o_no}」（article_key={key}）在新版無對應條號")

    node_changes: list[str] = []
    inline = ""
    if old is None:
        change = DIFF_ADDED
        rat.append("Traversal：舊無新有，判為新增條文")
        inline = render_inline("", n_text)
    elif new is None:
        change = DIFF_DELETED
        rat.append("Traversal：舊有新無，判為刪除條文")
        inline = render_inline(o_text, "")
    elif is_deletion_marker(n_text) and not is_deletion_marker(o_text):
        change = DIFF_DELETED
        rat.append(f"Traversal：新條文內容為刪除標示「{n_text.strip()}」，判為刪除條文")
        inline = render_inline(o_text, n_text)
    elif is_deletion_marker(o_text) and not is_deletion_marker(n_text):
        change = DIFF_ADDED
        rat.append(f"Traversal：舊條文為刪除標示「{o_text.strip()}」，新版恢復條文內容，判為新增條文")
        inline = render_inline(o_text, n_text)
    elif norm_text(o_text) == norm_text(n_text):
        change = UNCHANGED
        rat.append("Traversal：新舊條文 norm_text 相同，未變動")
    else:
        change = DIFF_MODIFIED
        node_changes, notes = tree_diff(o_node, n_node)
        if not node_changes:
            node_changes = ["全條 修正（僅標點或格式差異）"]
            notes = ["各節點內容簽章相同但 norm_text 不同（可能為標點／格式），判為修正"]
        rat.append("Traversal：norm_text 不同，判為修正條文；逐層（項→款→目→子目）遞迴比對")
        rat += [f"Traversal：{x}" for x in notes]
        rat += _semantic_token_changes(o_node, n_node)
        inline = render_inline(o_text, n_text)
    if change != UNCHANGED:
        rat.append(_generation_note(o_text, n_text))
    return ArticleDiff(key=key, old_no=o_no, new_no=n_no, change=change, old_text=o_text, new_text=n_text,
                       node_changes=node_changes, inline_diff=inline, rationale=rat)


def diff_laws(old: LawDocument | None, new: LawDocument) -> list[ArticleDiff]:
    """比對兩版法規，回傳每一條的 ArticleDiff（含 change="未變動"），依條號排序。"""
    new_map, new_order, new_dup = _index(new)
    if old is None:
        out = []
        for k in new_order:
            d = diff_article(None, new_map[k])
            d.rationale.insert(0, "Parsing：無前版可比對（首次執行且無前版），全部條文判為新增條文")
            out.append(d)
        return out

    old_map, old_order, old_dup = _index(old)
    keys = list(dict.fromkeys(old_order + new_order))
    pos = {k: i for i, k in enumerate(new_order)}
    pos_old = {k: i for i, k in enumerate(old_order)}
    keys.sort(key=lambda k: _sort_key(k.split("#")[0], pos.get(k, pos_old.get(k, 0))))

    out: list[ArticleDiff] = []
    for k in keys:
        out.append(diff_article(old_map.get(k), new_map.get(k)))
        if "#dup" in k:
            out[-1].rationale.append(f"Parsing：同一版本出現重複條號（{k.split('#')[0]}），以出現順序區分")

    # 條次移動偵測
    vanished = [d for d in out if d.change == DIFF_DELETED]
    candidates = [d for d in out if d.change in (DIFF_ADDED, DIFF_MODIFIED) and d.new_text
                  and not is_deletion_marker(d.new_text)]
    for v in vanished:
        vn = norm_text(v.old_text)
        if not vn:
            continue
        for c in candidates:
            if c.key == v.key:
                continue
            cn = norm_text(c.new_text)
            sm = difflib.SequenceMatcher(None, vn, cn, autojunk=False)
            if sm.real_quick_ratio() < MOVE_THRESHOLD or sm.quick_ratio() < MOVE_THRESHOLD:
                continue
            r = sm.ratio()
            if r >= MOVE_THRESHOLD:
                note = f"Traversal：疑似條次移列：舊{v.old_no} → 新{c.new_no}（相似度 {r:.2f}；差異類型仍依條號判定）"
                v.rationale.append(note)
                c.rationale.append(note)
    return out
