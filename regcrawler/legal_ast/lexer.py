"""Legal AST 第一階段：詞法分析（Lexing / Tokenization）。

把條文原文切成 token，每個 token 保留原文 span（start, end），
``text[tok.start:tok.end] == tok.text`` 恆成立，codegen 可據此回到原文。

Token 種類：
- ARTICLE_NO    條號標題：「第X條」「第X條之Y」「第 X-Y 條」「Article N」（lex_heading 使用）
- PARA_BREAK    項的分隔（換行）
- SUBPARA_MARK  款標記：行首「一、」「二、」…
- ITEM_MARK     目標記：行首「（一）」「(一)」…
- SUBITEM_MARK  行首「1.」「1、」「(1)」「（1）」
- PARA_MARK     英文條文（GDPR）行首項號「1.」
- POINT_MARK    英文條文（GDPR）行首點號「(a)」
- AMOUNT        金額：「新臺幣二萬元」「二十萬元」「EUR 20 000 000」
- DURATION      期間：「七十二小時」「三十六小時內」「一個月內」「十日」「即時」「立即」
- PENALTY_KW    罰鍰、罰金、有期徒刑、拘役、按次處罰
- NOTICE_KW     通知（對當事人）
- REPORT_KW     通報（對主管機關）
- REF           條文引用：「第二十條之一第一項」「第十二條第二項第三款」「前項」「Article 6(1)」
- TEXT          其他文字

公開函式 extract_amounts / extract_durations 供 #6 regcrawler.rules.semantic 使用。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ---- token 種類常數 ----
ARTICLE_NO = "ARTICLE_NO"
PARA_BREAK = "PARA_BREAK"
SUBPARA_MARK = "SUBPARA_MARK"
ITEM_MARK = "ITEM_MARK"
SUBITEM_MARK = "SUBITEM_MARK"
PARA_MARK = "PARA_MARK"
POINT_MARK = "POINT_MARK"
AMOUNT = "AMOUNT"
DURATION = "DURATION"
PENALTY_KW = "PENALTY_KW"
NOTICE_KW = "NOTICE_KW"
REPORT_KW = "REPORT_KW"
REF = "REF"
TEXT = "TEXT"

STRUCTURAL = (SUBPARA_MARK, ITEM_MARK, SUBITEM_MARK, PARA_MARK, POINT_MARK)


@dataclass
class Token:
    type: str
    text: str
    start: int
    end: int
    value: float | int | None = None     # AMOUNT=元（int）；DURATION=小時（float）；標記=序號
    line: int = 0                        # 第幾行（0 起算）

    def __repr__(self) -> str:  # 精簡輸出，方便除錯
        v = f", value={self.value}" if self.value is not None else ""
        return f"Token({self.type}, {self.text!r}, {self.start}:{self.end}{v})"


# ---- 數字 ----
_CN_NUM_CHARS = "零〇一二兩三四五六七八九十百千萬億壹貳參肆伍陸柒捌玖拾佰仟"
_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "貳": 2, "兩": 2, "三": 3, "參": 3,
             "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6, "陸": 6, "七": 7, "柒": 7,
             "八": 8, "捌": 8, "九": 9, "玖": 9}
_CN_SMALL_UNIT = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CN_BIG_UNIT = {"萬": 10_000, "億": 100_000_000}


def parse_number(s: str) -> float | None:
    """中文／阿拉伯／混合數字轉數值。
    「十五萬」→150000、「一千五百萬」→15000000、「1,500萬」→15000000、「三十六」→36、
    「2億」→200000000、「0.5」→0.5。無法辨識回傳 None。"""
    s = unicodedata.normalize("NFKC", s or "").strip().replace(",", "").replace("，", "").replace(" ", "")
    if not s:
        return None
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        pass
    total = 0.0      # 已結算的 億／萬 段
    section = 0.0    # 目前 萬 以下累計
    num: float | None = None
    i, seen = 0, False
    while i < len(s):
        ch = s[i]
        m = re.match(r"\d+(?:\.\d+)?", s[i:])
        if m:
            num = float(m.group(0))
            i += len(m.group(0))
            seen = True
            continue
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
            seen = True
        elif ch in _CN_SMALL_UNIT:
            section += (num if num is not None else 1) * _CN_SMALL_UNIT[ch]
            num = None
            seen = True
        elif ch in _CN_BIG_UNIT:
            unit = _CN_BIG_UNIT[ch]
            section += num or 0
            if section == 0:
                section = 1
            if unit == 100_000_000:
                total = (total + section) * unit
            else:
                total += section * unit
            section, num = 0.0, None
            seen = True
        else:
            return None
        i += 1
    if not seen:
        return None
    val = total + section + (num or 0)
    return int(val) if float(val).is_integer() else val


# ---- 正則 ----
_NUM = rf"[0-9０-９][0-9０-９,，.．]*[{_CN_NUM_CHARS}]*|[{_CN_NUM_CHARS}]+"
_CN_ORD = r"[一二三四五六七八九十百千零〇]+"
_ORD = r"[0-9０-９一二三四五六七八九十百千零〇]+"

# 金額：可選「新臺幣／新台幣／NT$」前綴 + 數字 + 元；英文 EUR / €
_AMOUNT_RE = re.compile(
    rf"(?:(?:新臺幣|新台幣|NT\$|NTD)\s*)?(?:{_NUM})\s*元"
    r"|(?:EUR|€)\s?\d{1,3}(?:[ , ]\d{3})+|(?:EUR|€)\s?\d+"
)
# 期間：數字 + 單位（小時、日、天、週、星期、個月、年）+ 可選「內」；以及「即時」「立即」
_DURATION_RE = re.compile(
    rf"(?:{_NUM})\s*(?:小時|個工作天|個工作日|工作天|工作日|日|天|週|星期|個月|年)(?:內|以內)?"
    r"|即時|立即"
    r"|(?:without undue delay)"
    r"|\d+\s*(?:hours?|days?|months?|years?)"
)
_PENALTY_RE = re.compile(r"按次處罰|按次連續處罰|罰鍰|罰金|有期徒刑|無期徒刑|拘役|administrative fines?")
_NOTICE_RE = re.compile(r"通知")
_REPORT_RE = re.compile(r"通報")
# 條文引用：第X條(之Y)?(第Y項)?(第Z款)?(第W目)?；或單獨的「第Y項(第Z款)」；前條／前項／本條…
_REF_RE = re.compile(
    rf"第\s*{_ORD}\s*條(?:\s*之\s*{_ORD})?(?:\s*第\s*{_ORD}\s*項)?(?:\s*第\s*{_ORD}\s*款)?(?:\s*第\s*{_ORD}\s*目)?"
    rf"|第\s*{_ORD}\s*[-－]\s*{_ORD}\s*條(?:\s*第\s*{_ORD}\s*項)?(?:\s*第\s*{_ORD}\s*款)?"
    rf"|第\s*{_ORD}\s*項(?:\s*第\s*{_ORD}\s*款)?(?:\s*第\s*{_ORD}\s*目)?"
    rf"|第\s*{_ORD}\s*款(?:\s*第\s*{_ORD}\s*目)?"
    r"|前[二三四五六七八九十]?(?:條|項|款)|本條|本項|同條|同項"
    r"|Articles?\s+\d+(?:\(\d+\))?(?:\([a-z]\))?"
)

# 條號標題
_HEADING_RE = re.compile(
    rf"第\s*{_ORD}\s*條(?:\s*之\s*{_ORD})?|第\s*{_ORD}\s*[-－‐]\s*{_ORD}\s*條|(?i:Art(?:icle|\.)?)\s*\d+"
)

# 行首結構標記（允許前置空白；span 不含空白）
_SUBPARA_RE = re.compile(rf"({_CN_ORD})、")
_ITEM_RE = re.compile(rf"[（(]({_CN_ORD})[）)]")
_SUBITEM_RE = re.compile(r"(?:([0-9０-９]+)[.．、](?![0-9０-９])|[（(]([0-9０-９]+)[）)])")
_EN_PARA_RE = re.compile(r"(\d+)\.\s")
_EN_POINT_RE = re.compile(r"\(([a-z]{1,3}|[ivx]+)\)\s")

_CJK_RE = re.compile(r"[一-鿿]")

# 依優先序掃描（先長後短：引用 > 金額 > 期間 > 罰則 > 通知 > 通報）
_INLINE_PATTERNS: list[tuple[str, re.Pattern]] = [
    (REF, _REF_RE),
    (AMOUNT, _AMOUNT_RE),
    (DURATION, _DURATION_RE),
    (PENALTY_KW, _PENALTY_RE),
    (NOTICE_KW, _NOTICE_RE),
    (REPORT_KW, _REPORT_RE),
]


def is_english(text: str) -> bool:
    return not _CJK_RE.search(text or "")


def _ordinal(s: str | None) -> int | None:
    if not s:
        return None
    if re.fullmatch(r"[a-z]", s):
        return ord(s) - ord("a") + 1
    v = parse_number(s)
    return int(v) if isinstance(v, (int, float)) else None


def _duration_hours(tok_text: str) -> float | None:
    t = unicodedata.normalize("NFKC", tok_text).replace(" ", "")
    if t in ("即時", "立即"):
        return 0.0
    if t == "withoutunduedelay":
        return None
    m = re.match(r"(.+?)(小時|個工作天|個工作日|工作天|工作日|日|天|週|星期|個月|年|hours?|days?|months?|years?)", t)
    if not m:
        return None
    n = parse_number(m.group(1))
    if n is None:
        return None
    unit = m.group(2)
    if unit.startswith("hour") or unit == "小時":
        mult = 1
    elif unit in ("日", "天", "工作天", "工作日", "個工作天", "個工作日") or unit.startswith("day"):
        mult = 24
    elif unit in ("週", "星期"):
        mult = 168
    elif unit == "個月" or unit.startswith("month"):
        mult = 720
    else:
        mult = 8760
    return float(n) * mult


def _amount_value(tok_text: str) -> int | float | None:
    t = unicodedata.normalize("NFKC", tok_text)
    t = re.sub(r"^(?:新臺幣|新台幣|NT\$|NTD|EUR|€)", "", t.strip()).strip()
    t = t.rstrip("元").replace(" ", "").replace(" ", "")
    return parse_number(t)


def _is_false_duration(text: str, m: re.Match) -> bool:
    """排除日期（民國一百十二年、五月三十一日）與序數（第十日）。"""
    before = text[max(0, m.start() - 3):m.start()]
    after = text[m.end():m.end() + 3]
    tok = m.group(0)
    if before.endswith("第"):
        return True
    if tok.endswith(("年", "年內")) and ("民國" in before or re.match(rf"\s*[{_CN_NUM_CHARS}0-9０-９]+\s*月", after)):
        return True
    if tok.endswith(("日", "日內")) and re.search(r"月\s*$", before):
        return True
    return False


def _lex_inline(text: str, start: int, end: int, line_no: int, out: list[Token]) -> None:
    """掃描 [start, end) 的行內 token，空隙為 TEXT。"""
    pos = start
    while pos < end:
        best: tuple[int, int, str, re.Match] | None = None
        for ttype, pat in _INLINE_PATTERNS:
            m = pat.search(text, pos, end)
            while m and ttype == DURATION and _is_false_duration(text, m):
                m = pat.search(text, m.end(), end)
            if not m or m.end() == m.start():
                continue
            # 最早出現者優先；同位置取較長者；再同則依優先序
            if best is None or m.start() < best[0] or (m.start() == best[0] and m.end() > best[1]):
                best = (m.start(), m.end(), ttype, m)
        if best is None:
            out.append(Token(TEXT, text[pos:end], pos, end, line=line_no))
            break
        s, e, ttype, m = best
        if s > pos:
            out.append(Token(TEXT, text[pos:s], pos, s, line=line_no))
        value = None
        if ttype == AMOUNT:
            value = _amount_value(m.group(0))
        elif ttype == DURATION:
            value = _duration_hours(m.group(0))
        out.append(Token(ttype, m.group(0), s, e, value, line=line_no))
        pos = e


def lex(text: str, *, english: bool | None = None) -> list[Token]:
    """把一條條文的原文切成 token 串列。行首結構標記（款／目／英文項、點）以 SUBPARA_MARK 等標示，
    換行以 PARA_BREAK 標示。保證每個 token 的 span 指回原文。"""
    text = text or ""
    if english is None:
        english = is_english(text)
    tokens: list[Token] = []
    pos, line_no = 0, 0
    for line in text.split("\n"):
        line_start, line_end = pos, pos + len(line)
        # 行首空白（含全形）不視為標記
        lead = len(line) - len(line.lstrip(" \t　\r"))
        cur = line_start + lead
        if lead:
            tokens.append(Token(TEXT, text[line_start:cur], line_start, cur, line=line_no))
        body = text[cur:line_end]
        mark: tuple[str, re.Match] | None = None
        if english:
            for ttype, pat in ((PARA_MARK, _EN_PARA_RE), (POINT_MARK, _EN_POINT_RE)):
                m = pat.match(body)
                if m:
                    mark = (ttype, m)
                    break
        else:
            for ttype, pat in ((SUBPARA_MARK, _SUBPARA_RE), (ITEM_MARK, _ITEM_RE), (SUBITEM_MARK, _SUBITEM_RE)):
                m = pat.match(body)
                if m:
                    mark = (ttype, m)
                    break
        if mark:
            ttype, m = mark
            mtext = m.group(0).rstrip()
            ordv = _ordinal(next((g for g in m.groups() if g), None))
            tokens.append(Token(ttype, mtext, cur, cur + len(mtext), ordv, line=line_no))
            cur += len(mtext)
        _lex_inline(text, cur, line_end, line_no, tokens)
        pos = line_end
        if pos < len(text):  # 換行字元
            tokens.append(Token(PARA_BREAK, "\n", pos, pos + 1, line=line_no))
            pos += 1
        line_no += 1
    return tokens


def lex_heading(no: str) -> list[Token]:
    """條號標題的詞法分析：回傳 ARTICLE_NO token（其餘為 TEXT）。"""
    out: list[Token] = []
    pos = 0
    for m in _HEADING_RE.finditer(no or ""):
        if m.start() > pos:
            out.append(Token(TEXT, no[pos:m.start()], pos, m.start()))
        out.append(Token(ARTICLE_NO, m.group(0), m.start(), m.end()))
        pos = m.end()
    if pos < len(no or ""):
        out.append(Token(TEXT, no[pos:], pos, len(no)))
    return out


def count_types(tokens: list[Token]) -> dict[str, int]:
    d: dict[str, int] = {}
    for t in tokens:
        d[t.type] = d.get(t.type, 0) + 1
    return d


# ---- 供 #6 使用的公開函式 ----
def extract_amounts(text: str) -> list[tuple[str, int]]:
    """回傳 [(原文, 元)]，依出現順序。例如「新臺幣十五萬元以上一千五百萬元以下」→
    [("新臺幣十五萬元", 150000), ("一千五百萬元", 15000000)]。"""
    out = []
    for t in lex(text):
        if t.type == AMOUNT and t.value is not None:
            out.append((t.text, int(t.value)))
    return out


def extract_durations(text: str) -> list[tuple[str, float]]:
    """回傳 [(原文, 小時數)]。「即時」「立即」=0；日=24；個月=720；年=8760。
    無法換算者（例如 without undue delay）不列入。"""
    out = []
    for t in lex(text):
        if t.type == DURATION and t.value is not None:
            out.append((t.text, float(t.value)))
    return out
