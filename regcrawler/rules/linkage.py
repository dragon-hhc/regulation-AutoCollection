"""連動影響偵測（#6）。

介面（SPEC §7.5，不可更改）：
    detect(changes: dict[str, list[ArticleDiff]], docs: list[LawDocument]) -> list[LinkageRecord]

- changes 的 key 為母法名稱（原文），值為 #5 diff_laws 的輸出。
- 掃描 docs（子法、函釋）每一條條文，找出對母法條文的引用，例如：
    「本辦法依個人資料保護法（以下簡稱本法）第二十七條第三項規定訂定之。」
    「本法第十二條」「個人資料保護法第二十七條」「同法第X條」不處理（無法確定指涉）。
  「本法」指涉 doc.parent_law（子法中「本法」即母法；文件本身若為「法」而非「辦法」等則不解析「本法」）。
- 被引用之母法條文於 changes 中為「刪除條文」或「修正條文」→ 產生 LinkageRecord。
  修正條文另檢查被引用之「項」是否仍存在（項數減少則註明）。
- 條號比對使用 normalize.article_key；輸出一律用原文條號與原文引用字串。
- explanation 用語中性、陳述事實，不做法律結論（使用「應檢視」）。
"""
from __future__ import annotations

import re

from ..models import DIFF_DELETED, DIFF_MODIFIED, ArticleDiff, LawDocument, LinkageRecord
from ..normalize import article_key, cn_to_int, norm_text

_ORD = r"[0-9０-９一二三四五六七八九十百千零〇]+"
_ART = rf"第\s*{_ORD}\s*條(?:\s*之\s*{_ORD})?"
_PARA = rf"第\s*{_ORD}\s*項"
_SUBP = rf"第\s*{_ORD}\s*款"
# 引用片段（接在法規名稱之後）：可選「（以下簡稱本法）」括號、條、項、款，以及「至第Y條」範圍
_TAIL_RE = re.compile(
    rf"\s*(?P<paren>[（(][^（）()]{{0,20}}[）)])?\s*"
    rf"(?P<art>{_ART})(?:\s*(?P<para>{_PARA}))?(?:\s*(?P<subp>{_SUBP}))?"
    rf"(?:\s*(?:至|到)\s*(?P<art2>{_ART}))?"
)
_ITEM_LINE_RE = re.compile(r"^\s*(?:[一二三四五六七八九十百]+、|[（(][一二三四五六七八九十]+[）)])")
_SUBLAW_SUFFIX = ("辦法", "細則", "準則", "規則", "標準", "要點", "規程", "須知", "辦法草案", "函", "令")


def _paras(text: str) -> list[str]:
    """把條文切成「項」：每行為一項，但以「一、」「（一）」開頭的款／目行併入前一項。"""
    out: list[str] = []
    for line in (text or "").split("\n"):
        if not line.strip():
            continue
        if out and _ITEM_LINE_RE.match(line):
            out[-1] += "\n" + line
        else:
            out.append(line)
    return out


def _ord(s: str | None) -> int | None:
    if not s:
        return None
    m = re.search(_ORD, s)
    return cn_to_int(m.group(0)) if m else None


def _range_keys(a1: str, a2: str) -> list[str]:
    k1, k2 = article_key(a1), article_key(a2)
    try:
        s, e = int(k1.split("-")[0]), int(k2.split("-")[0])
    except ValueError:
        return [k1, k2]
    if e < s or e - s > 200:
        return [k1, k2]
    return [str(i) for i in range(s, e + 1)]


def _resolves_self_as_benfa(doc: LawDocument) -> bool:
    """文件自身是否為「法」（此時條文中的「本法」指自己，不指母法）。"""
    name = doc.name.strip()
    if name.endswith(_SUBLAW_SUFFIX):
        return False
    return name.endswith(("法", "條例", "通則"))


def _find_refs(text: str, law_names: list[str], parent: str, benfa_ok: bool):
    """產生 (母法名稱, 原文引用字串(不含括號說明), 條原文, 項序或 None, [article_key...])。"""
    names = sorted(set(n for n in law_names if n), key=len, reverse=True)
    alts = [re.escape(n) for n in names]
    if benfa_ok and parent:
        alts.append("本法")
    if not alts:
        return
    head_re = re.compile("(" + "|".join(alts) + ")")
    for hm in head_re.finditer(text):
        tm = _TAIL_RE.match(text, hm.end())
        if not tm:
            continue
        token = hm.group(1)
        law = parent if token == "本法" else token
        if token != "本法" and law not in law_names:
            continue
        art = tm.group("art")
        keys = _range_keys(art, tm.group("art2")) if tm.group("art2") else [article_key(art)]
        cite = token + "".join(
            g for g in (tm.group("art"), tm.group("para"), tm.group("subp")) if g)
        if tm.group("art2"):
            cite = token + tm.group("art") + "至" + tm.group("art2")
        if token == "本法":
            cite = f"{law}{cite[len('本法'):]}"
        yield law, cite, token, art, _ord(tm.group("para")), keys


def _fmt_key(k: str) -> str:
    return f"第{k}條" if "-" not in k else f"第{k}條"


def detect(changes: dict[str, list[ArticleDiff]], docs: list[LawDocument]) -> list[LinkageRecord]:
    index: dict[str, dict[str, ArticleDiff]] = {}
    for law, diffs in (changes or {}).items():
        index[law] = {}
        for d in diffs or []:
            index[law][d.key or article_key(d.new_no or d.old_no)] = d

    records: list[LinkageRecord] = []
    seen: set[tuple] = set()
    for doc in docs or []:
        parent = (doc.parent_law or "").strip()
        benfa_ok = bool(parent) and not _resolves_self_as_benfa(doc)
        law_names = [n for n in index if n != doc.name]
        if parent and parent not in law_names and parent in index:
            law_names.append(parent)
        for art in doc.articles:
            for law, cite, token, art_raw, para, keys in _find_refs(art.text, law_names, parent, benfa_ok):
                if law not in index:
                    continue
                for k in keys:
                    d = index[law].get(k)
                    if d is None or d.change not in (DIFF_DELETED, DIFF_MODIFIED):
                        continue
                    sig = (doc.name, art.no, law, k, para)
                    if sig in seen:
                        continue
                    seen.add(sig)
                    records.append(LinkageRecord(
                        affected_law=doc.name,
                        affected_article=art.no,
                        parent_article=cite,
                        parent_change=d.change,
                        explanation=_explain(doc, art.no, law, cite, token, para, k, d),
                    ))
    return records


def _self_label(doc: LawDocument) -> str:
    name = doc.name.strip()
    for suf in ("辦法", "細則", "準則", "規則", "標準", "要點", "規程", "須知"):
        if name.endswith(suf):
            return f"本{suf}"
    return f"「{name}」"


def _explain(doc: LawDocument, art_no: str, law: str, cite: str, token: str,
             para: int | None, key: str, d: ArticleDiff) -> str:
    me = _self_label(doc)
    parent_no = d.old_no or d.new_no or _fmt_key(key)
    src = f"（原文引用「{token}…{cite[len(law):]}」，「本法」指{law}）" if token == "本法" else ""
    if d.change == DIFF_DELETED:
        return (f"母法「{law}」{parent_no}已刪除；{me}{art_no}所引用之「{cite}」{src}"
                f"於母法新版已無對應條文，應檢視{me}是否需配合修正或廢止。")
    # 修正條文
    msg = f"母法「{law}」{parent_no}已修正；{me}{art_no}引用「{cite}」{src}"
    if para:
        op, np_ = _paras(d.old_text), _paras(d.new_text)
        if op and np_ and len(np_) < len(op) and para > len(np_):
            msg += (f"，母法該條項數由 {len(op)} 項減為 {len(np_)} 項，第{para}項於新版已不存在"
                    f"，應檢視{me}所引用之依據是否仍然有效。")
            return msg
        if para <= len(np_) and para <= len(op):
            same = norm_text(op[para - 1]) == norm_text(np_[para - 1])
            msg += (f"，母法該條第{para}項仍存在（項數 {len(op)} → {len(np_)}），"
                    + ("該項文字未變動" if same else "該項文字已變動")
                    + f"，應檢視{me}相關規定是否需配合調整。")
            return msg
    return msg + f"，應檢視{me}相關規定是否需配合調整。"
