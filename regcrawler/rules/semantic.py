"""語意規則（#6）：罰則加重、通知時限縮短、通報時限縮短。

介面（SPEC §7.5，不可更改）：
    classify(old_text: str, new_text: str) -> list[tuple[str, str]]
回傳 [(差異類型, rationale)]，差異類型只會是 models.DIFF_PENALTY_UP / DIFF_NOTICE_SHORTER /
DIFF_REPORT_SHORTER 三者之一，每種最多一筆（多項理由以「；」串接）。

判斷原則
- 罰則：依「處罰類型」分組（罰鍰／罰金／有期徒刑），只在同一類型內比較，不跨類比較。
  * 罰鍰、罰金：抽出每一個罰則片段（例「處新臺幣二萬元以上二十萬元以下罰鍰」）的下限與上限。
    新舊片段數相同 → 依出現順序一對一比較；片段數不同 → 無法一對一對應，改以最高額（上限最大值、
    下限最大值）比較，並在 rationale 註明。
  * 有期徒刑：比較最重本刑（死刑 > 無期徒刑 > 有期徒刑上限）。
  * 新增處罰類型、罰則片段數增加、新增「按次處罰」、處罰對象增加、所列違反情事（原文「違反」）
    增加 → 皆視為罰則加重（「新增處罰對象或處罰態樣」）。
- 時限：抽出「即時」「立即」「立刻」「即刻」「X小時內」「X日內」等，依「同一句中距離最近的動詞」
  歸屬「通知」或「通報」（嚴格依原文用字，不互換）；同句兩者並存時於 rationale 說明。
  新值 < 舊值，或舊無時限而新增時限 → 時限縮短。

金額與期間換算優先使用 #5 的 regcrawler.legal_ast.lexer.extract_amounts / extract_durations，
若無法匯入則使用本模組的私有實作（_fallback_extract_amounts / _fallback_extract_durations）。
rationale 一律引用原文片段，不改寫原文。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..models import DIFF_NOTICE_SHORTER, DIFF_PENALTY_UP, DIFF_REPORT_SHORTER

try:  # 優先使用 #5 的實作
    from ..legal_ast.lexer import extract_amounts as _ext_amounts  # type: ignore
except Exception:  # pragma: no cover - 依環境而定
    _ext_amounts = None
try:
    from ..legal_ast.lexer import extract_durations as _ext_durations  # type: ignore
except Exception:  # pragma: no cover
    _ext_durations = None


# ---------------------------------------------------------------- 數字
_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "貳": 2, "兩": 2, "三": 3, "參": 3,
             "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6, "陸": 6, "七": 7, "柒": 7,
             "八": 8, "捌": 8, "九": 9, "玖": 9}
_CN_SMALL = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CN_BIG = {"萬": 10_000, "億": 100_000_000}
_CN_CHARS = "".join(_CN_DIGIT) + "".join(_CN_SMALL) + "".join(_CN_BIG)

_NUM = rf"[0-9０-９][0-9０-９,，]*(?:[.．][0-9０-９]+)?[{_CN_CHARS}]*|[{_CN_CHARS}]+"


def _parse_number(s: str) -> float | None:
    """「二十」→20、「一千五百萬」→15000000、「1,500萬」→15000000、「3.5」→3.5、「一億」→1e8。"""
    t = unicodedata.normalize("NFKC", s).replace(",", "").replace("，", "").strip()
    if not t:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(.*)", t)
    if m:  # 阿拉伯數字 + 可選中文單位（例 1,500萬）
        base = float(m.group(1))
        rest = m.group(2)
        for ch in rest:
            if ch in _CN_BIG:
                base *= _CN_BIG[ch]
            elif ch in _CN_SMALL:
                base *= _CN_SMALL[ch]
            else:
                return None
        return base
    total, section, num, seen = 0, 0, 0, False
    for ch in t:
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
            seen = True
        elif ch in _CN_SMALL:
            section += (num if num else 1) * _CN_SMALL[ch]
            num = 0
            seen = True
        elif ch in _CN_BIG:
            section += num
            total = (total + section) * _CN_BIG[ch] if ch == "億" else total + section * _CN_BIG[ch]
            section, num, seen = 0, 0, True
        else:
            return None
    return float(total + section + num) if seen else None


def _fallback_extract_amounts(text: str) -> list[tuple[str, int]]:
    """私有備援：[(原文, 元)]。"""
    out = []
    for m in re.finditer(rf"(?:新臺幣|新台幣)?\s*({_NUM})\s*元", text):
        v = _parse_number(m.group(1))
        if v is not None:
            out.append((m.group(0), int(v)))
    return out


_DUR_UNITS = {"分鐘": 1 / 60, "小時": 1, "個工作天": 24, "個工作日": 24, "工作天": 24, "工作日": 24,
              "日": 24, "天": 24, "週": 168, "星期": 168, "個月": 720}
_IMMEDIATE = ("即時", "立即", "立刻", "即刻")


def _fallback_extract_durations(text: str) -> list[tuple[str, float]]:
    """私有備援：[(原文, 小時數)]；即時／立即／立刻／即刻 = 0。"""
    return [(m.group(0), h) for m, h in _iter_durations(text)]


def _amount_value(raw_num: str) -> int | None:
    """金額數值：優先 #5，失敗時用備援。"""
    snippet = f"{raw_num}元"
    if _ext_amounts is not None:
        try:
            r = _ext_amounts(snippet)
            if r:
                return int(r[0][1])
        except Exception:
            pass
    v = _parse_number(raw_num)
    return int(v) if v is not None else None


def _duration_value(snippet: str) -> float | None:
    if _ext_durations is not None:
        try:
            r = _ext_durations(snippet)
            if r:
                return float(r[0][1])
        except Exception:
            pass
    return _fallback_hours(snippet)


def _fallback_hours(snippet: str) -> float | None:
    t = unicodedata.normalize("NFKC", snippet).replace(" ", "")
    if t in _IMMEDIATE:
        return 0.0
    m = re.match(rf"({_NUM})(分鐘|小時|個工作天|個工作日|工作天|工作日|日|天|週|星期|個月)", t)
    if not m:
        return None
    n = _parse_number(m.group(1))
    return None if n is None else n * _DUR_UNITS[m.group(2)]


# ---------------------------------------------------------------- 罰則抽取
_CUR = r"(?:新臺幣|新台幣)?\s*"
_FINE_RE = re.compile(
    rf"(?P<per>按次)?(?:處|科或併科|或併科|併科|科)?{_CUR}(?P<a>{_NUM})\s*元"
    rf"(?:(?P<above>以上)(?:{_CUR}(?P<b>{_NUM})\s*元)?)?(?P<below>以下)?之?(?P<kind>罰鍰|罰金)"
)
_PRISON_RE = re.compile(
    rf"(?:(?P<lo>{_NUM})\s*(?:個)?(?:年|月)以上)?(?P<hi>{_NUM})\s*(?:個)?(?P<unit>年|月)以下有期徒刑"
)
_PER_RE = re.compile(r"按次(?:連續)?處")
_SUBJECT_RE = re.compile(r"非公務機關|(?<!非)公務機關|公務員|代表人|管理人|負責人|受委託者|受託者|行為人")
_SENT_SPLIT = re.compile(r"[。；;\n]")


@dataclass
class _Fine:
    kind: str              # 罰鍰 | 罰金
    raw: str               # 原文片段
    lower_raw: str
    lower: int | None
    upper_raw: str
    upper: int | None
    per_instance: bool


def _fines(text: str) -> list[_Fine]:
    out = []
    for m in _FINE_RE.finditer(text):
        a_raw, b_raw = m.group("a"), m.group("b")
        a = _amount_value(a_raw)
        if m.group("above") and b_raw:          # X元以上Y元以下
            lo_raw, lo, up_raw, up = f"{a_raw}元", a, f"{b_raw}元", _amount_value(b_raw)
        elif m.group("above"):                  # X元以上
            lo_raw, lo, up_raw, up = f"{a_raw}元", a, "", None
        elif m.group("below"):                  # X元以下
            lo_raw, lo, up_raw, up = "", None, f"{a_raw}元", a
        else:                                   # 定額
            lo_raw, lo, up_raw, up = f"{a_raw}元", a, f"{a_raw}元", a
        out.append(_Fine(m.group("kind"), m.group(0), lo_raw, lo, up_raw, up, bool(m.group("per"))))
    return out


def _prison(text: str) -> tuple[float, str] | None:
    """最重本刑分數（月）與原文：死刑 > 無期徒刑 > 有期徒刑上限。"""
    cands: list[tuple[float, str]] = []
    if "死刑" in text:
        cands.append((1e9, "死刑"))
    if "無期徒刑" in text:
        cands.append((1e8, "無期徒刑"))
    for m in _PRISON_RE.finditer(text):
        n = _parse_number(m.group("hi"))
        if n is not None:
            cands.append((n * (12 if m.group("unit") == "年" else 1), m.group(0)))
    return max(cands, key=lambda c: c[0]) if cands else None


def _has_penalty(text: str) -> bool:
    return bool(_FINE_RE.search(text) or _prison(text) or _PER_RE.search(text)
                or "拘役" in text)


def _penalty_subjects(text: str) -> set[str]:
    subs: set[str] = set()
    for sent in _SENT_SPLIT.split(text):
        if _has_penalty(sent):
            subs.update(_SUBJECT_RE.findall(sent))
    return subs


def _fmt_amt(raw: str, val: int | None) -> str:
    return f"{raw}（{val:,} 元）" if val is not None else raw


def _penalty_reasons(old: str, new: str) -> list[str]:
    reasons: list[str] = []
    old_has, new_has = _has_penalty(old), _has_penalty(new)
    if not new_has:
        return reasons
    of, nf = _fines(old), _fines(new)

    for kind in ("罰鍰", "罰金"):
        o = [f for f in of if f.kind == kind]
        n = [f for f in nf if f.kind == kind]
        if not n:
            continue
        if not o:
            detail = "、".join(f"「{f.raw}」" for f in n)
            if not old.strip():
                reasons.append(f"舊條文無此規定（新增條文），新增{kind}：{detail}")
            elif old_has:
                reasons.append(f"新增處罰類型「{kind}」：{detail}（舊條文無{kind}；不同處罰類型不跨類比較）")
            else:
                reasons.append(f"舊條文無罰則規定，新增{kind}：{detail}")
            continue
        if len(o) == len(n):
            for i, (fo, fn) in enumerate(zip(o, n), 1):
                tag = f"{kind}（第 {i} 處）" if len(o) > 1 else kind
                if fo.upper is not None and fn.upper is not None and fn.upper > fo.upper:
                    reasons.append(f"{tag}上限：{_fmt_amt(fo.upper_raw, fo.upper)} → "
                                   f"{_fmt_amt(fn.upper_raw, fn.upper)}（提高）；原文「{fo.raw}」→「{fn.raw}」")
                elif fo.upper is None and fo.lower is not None and fn.upper is not None:
                    pass  # 舊僅有下限、新有上限：無從比較上限
                if fo.lower is not None and fn.lower is not None and fn.lower > fo.lower:
                    reasons.append(f"{tag}下限：{_fmt_amt(fo.lower_raw, fo.lower)} → "
                                   f"{_fmt_amt(fn.lower_raw, fn.lower)}（提高）；原文「{fo.raw}」→「{fn.raw}」")
                elif fo.lower is None and fn.lower is not None:
                    reasons.append(f"{tag}新增下限：{_fmt_amt(fn.lower_raw, fn.lower)}；原文「{fo.raw}」→「{fn.raw}」")
                if fn.per_instance and not fo.per_instance:
                    reasons.append(f"{tag}新增按次處罰；原文「{fo.raw}」→「{fn.raw}」")
        else:
            note = f"{kind}罰則片段數 {len(o)} → {len(n)}，無法一對一對應，判斷依據為最高額比較"
            sub: list[str] = []
            ou = [f for f in o if f.upper is not None]
            nu = [f for f in n if f.upper is not None]
            if ou and nu:
                fo = max(ou, key=lambda f: f.upper)
                fn = max(nu, key=lambda f: f.upper)
                if fn.upper > fo.upper:
                    sub.append(f"最高上限 {_fmt_amt(fo.upper_raw, fo.upper)} → {_fmt_amt(fn.upper_raw, fn.upper)}（提高）")
            ol = [f for f in o if f.lower is not None]
            nl = [f for f in n if f.lower is not None]
            if ol and nl:
                fo = max(ol, key=lambda f: f.lower)
                fn = max(nl, key=lambda f: f.lower)
                if fn.lower > fo.lower:
                    sub.append(f"最高下限 {_fmt_amt(fo.lower_raw, fo.lower)} → {_fmt_amt(fn.lower_raw, fn.lower)}（提高）")
            if len(n) > len(o):
                sub.append(f"新增{kind}罰則條款（{len(o)} 處 → {len(n)} 處）："
                           + "、".join(f"「{f.raw}」" for f in n))
            if sub:
                reasons.append(note + "：" + "；".join(sub))

    # 有期徒刑（刑度）
    po, pn = _prison(old), _prison(new)
    if pn and not po:
        if not old.strip():
            reasons.append(f"舊條文無此規定（新增條文），新增刑罰：「{pn[1]}」")
        else:
            reasons.append(f"新增刑罰「{pn[1]}」（舊條文無徒刑規定；不與罰鍰／罰金跨類比較）")
    elif pn and po and pn[0] > po[0]:
        reasons.append(f"刑度提高：「{po[1]}」→「{pn[1]}」")

    if old.strip() and old_has:
        # 按次處罰
        co, cn = len(_PER_RE.findall(old)), len(_PER_RE.findall(new))
        if cn > co:
            reasons.append("新增按次處罰" if co == 0 else f"按次處罰規定增加（原文「按次處」{co} 處 → {cn} 處）")
        # 處罰對象
        added_sub = _penalty_subjects(new) - _penalty_subjects(old)
        if added_sub:
            reasons.append("新增處罰對象：" + "、".join(f"「{s}」" for s in sorted(added_sub)))
        # 處罰態樣（啟發式：原文「違反」所列情事數）
        vo, vn = old.count("違反"), new.count("違反")
        if vn > vo:
            reasons.append(f"處罰態樣增加：原文所列「違反」情事 {vo} 處 → {vn} 處"
                           "（啟發式計數，條次調整可能影響，建議人工覆核）")
    elif old.strip() and not old_has and not any(r.startswith("舊條文無罰則") for r in reasons):
        if _PER_RE.search(new):
            reasons.append("舊條文無罰則規定，新增按次處罰")
    return reasons


# ---------------------------------------------------------------- 時限抽取
_DUR_RE = re.compile(
    rf"即時|立即|立刻|即刻"
    rf"|(?P<n>{_NUM})\s*(?P<u>分鐘|小時|個工作天|個工作日|工作天|工作日|日|天|週|星期|個月)\s*(?:內|以內)"
)
_VERB_RE = re.compile(r"通知|通報")
_MAX_DIST = 40


def _iter_durations(text: str):
    for m in _DUR_RE.finditer(text):
        if text[max(0, m.start() - 1):m.start()] == "第":
            continue
        h = _fallback_hours(m.group(0)) if m.group("n") is None else None
        if m.group("n") is not None:
            n = _parse_number(m.group("n"))
            if n is None:
                continue
            h = n * _DUR_UNITS[m.group("u")]
        if h is None:
            continue
        yield m, h


@dataclass
class _Deadline:
    verb: str        # 通知 | 通報
    raw: str         # 時限原文
    hours: float
    sentence: str
    note: str = ""


def _deadlines(text: str) -> list[_Deadline]:
    out: list[_Deadline] = []
    for sent in _SENT_SPLIT.split(text):
        verbs = list(_VERB_RE.finditer(sent))
        if not verbs:
            continue
        kinds = {v.group(0) for v in verbs}
        for m, h_fb in _iter_durations(sent):
            best = None
            for v in verbs:
                if v.start() >= m.end():
                    dist, after = v.start() - m.end(), 0
                elif v.end() <= m.start():
                    dist, after = m.start() - v.end(), 1
                else:
                    dist, after = 0, 0
                key = (dist, after)
                if best is None or key < best[0]:
                    best = (key, v)
            if best is None or best[0][0] > _MAX_DIST:
                continue
            verb = best[1].group(0)
            h = _duration_value(m.group(0))
            if h is None:
                h = h_fb
            note = ""
            if len(kinds) > 1:
                note = (f"同句同時出現「通知」與「通報」，依距離時限詞「{m.group(0)}」最近之動詞判定為「{verb}」"
                        f"（句：「{sent.strip()}」）")
            out.append(_Deadline(verb, m.group(0), float(h), sent.strip(), note))
    return out


def _fmt_h(h: float) -> str:
    if h == 0:
        return "0 小時（即時）"
    return f"{h:g} 小時"


def _deadline_reason(verb: str, old: str, new: str) -> str | None:
    od = [d for d in _deadlines(old) if d.verb == verb]
    nd = [d for d in _deadlines(new) if d.verb == verb]
    if not nd:
        return None
    notes = [d.note for d in nd if d.note]
    if not od:
        detail = "、".join(f"「{d.raw}」（{_fmt_h(d.hours)}；原文句「{d.sentence}」）" for d in nd)
        r = f"舊法無此時限規定，新增{verb}時限 {detail}"
    elif len(od) == len(nd):
        parts = [f"{verb}時限：「{a.raw}」（{_fmt_h(a.hours)}）→「{b.raw}」（{_fmt_h(b.hours)}）（縮短）"
                 for a, b in zip(od, nd) if b.hours < a.hours]
        if not parts:
            return None
        r = "；".join(parts)
    else:
        a = min(od, key=lambda d: d.hours)
        b = min(nd, key=lambda d: d.hours)
        if not b.hours < a.hours:
            return None
        r = (f"{verb}時限無法一對一對應（舊 {len(od)} 處、新 {len(nd)} 處），依最短時限比較："
             f"「{a.raw}」（{_fmt_h(a.hours)}）→「{b.raw}」（{_fmt_h(b.hours)}）（縮短）")
    if notes:
        r += "；" + "；".join(dict.fromkeys(notes))
    return r


# ---------------------------------------------------------------- 公開介面
def classify(old_text: str, new_text: str) -> list[tuple[str, str]]:
    """比較新舊條文，回傳 [(差異類型, rationale)]（只含罰則加重／通知時限縮短／通報時限縮短）。"""
    old_text = old_text or ""
    new_text = new_text or ""
    out: list[tuple[str, str]] = []
    reasons = _penalty_reasons(old_text, new_text)
    if reasons:
        out.append((DIFF_PENALTY_UP, "；".join(reasons)))
    r = _deadline_reason("通知", old_text, new_text)
    if r:
        out.append((DIFF_NOTICE_SHORTER, r))
    r = _deadline_reason("通報", old_text, new_text)
    if r:
        out.append((DIFF_REPORT_SHORTER, r))
    return out
