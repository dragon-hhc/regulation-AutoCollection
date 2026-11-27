"""正規化與 SHA-512（主控實作）。

正規化只用於 hash 與比對鍵，絕不用於輸出。
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from .models import LawDocument

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "萬": 10000}


def cn_to_int(s: str) -> int | None:
    """中文數字轉整數：「二十」→20、「一百零五」→105、「十五」→15、「一千五百」→1500。
    阿拉伯數字（含全形）直接轉換。無法辨識回傳 None。"""
    s = unicodedata.normalize("NFKC", s).strip().replace(",", "")
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, section, num = 0, 0, 0
    seen = False
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
            seen = True
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            seen = True
            if unit == 10000:
                section = (section + num) * unit
                total += section
                section = 0
            else:
                section += (num if num else 1) * unit
            num = 0
        else:
            return None
    return total + section + num if seen else None


_ART_RE = re.compile(r"第\s*([0-9０-９一二三四五六七八九十百千零〇]+)\s*條\s*(?:之\s*([0-9０-９一二三四五六七八九十]+))?")
_ART_DASH_RE = re.compile(r"第\s*([0-9０-９]+)\s*[-－‐]\s*([0-9０-９]+)\s*條")


def article_key(no: str) -> str:
    """條號正規化鍵：「第二十條之一」「第 20-1 條」「第20條之1」→ "20-1"；「第 27 條」→ "27"。
    非中文條號（例如 Article 33、Art. 33(1)）→ 小寫去空白。"""
    t = unicodedata.normalize("NFKC", no)
    m = _ART_DASH_RE.search(t)
    if m:
        return f"{int(m.group(1))}-{int(m.group(2))}"
    m = _ART_RE.search(t)
    if m:
        main = cn_to_int(m.group(1))
        sub = cn_to_int(m.group(2)) if m.group(2) else None
        if main is not None:
            return f"{main}-{sub}" if sub else str(main)
    m = re.search(r"(?i)art(?:icle|\.)?\s*(\d+)", t)
    if m:
        return f"art{int(m.group(1))}"
    return re.sub(r"\s+", "", t).lower()


def norm_text(s: str) -> str:
    """hash / 比對用：NFKC、去除所有空白（含全形空白與換行）。"""
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"\s+", "", s)


def content_hash(doc: LawDocument) -> str:
    h = hashlib.sha512()
    h.update(norm_text(doc.name).encode("utf-8"))
    if doc.metadata_only:
        for k in sorted(doc.metadata):
            h.update(f"|{k}={norm_text(str(doc.metadata[k]))}".encode("utf-8"))
    for a in doc.articles:
        h.update(f"|{article_key(a.no)}:{norm_text(a.text)}".encode("utf-8"))
    return h.hexdigest()


def date_tuple(doc: LawDocument) -> tuple[str, str, str]:
    return (norm_text(doc.promulgated_date), norm_text(doc.amended_date), norm_text(doc.effective_date))
