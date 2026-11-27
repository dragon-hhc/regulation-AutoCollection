"""SPEC §8 驗收：個人資料保護法 112.05.31 → 114.11.11 修正（真實條文）。

Fixture 來源（見各檔檔頭）：
- tests/fixtures/rules/pdpa_114.txt  https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=I0050021
- tests/fixtures/rules/pdpa_112.txt  https://law.lia-roc.org.tw/Law/HisContent?lsid=FL010627&date=20230531
- tests/fixtures/rules/fsc_pdpa_security_rules.txt  https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=G0380233

執行 `python -m pytest tests/test_acceptance_pdpa_114.py -q -s` 可看到第 12、47～49 條的實際判定與理由。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from regcrawler.models import (DIFF_ADDED, DIFF_DELETED, DIFF_LINKAGE, DIFF_MODIFIED,
                               DIFF_NOTICE_SHORTER, DIFF_PENALTY_UP, DIFF_REPORT_SHORTER,
                               Article, LawDocument)
from regcrawler.normalize import article_key
from regcrawler.rules import linkage, semantic

FIX = Path(__file__).parent / "fixtures" / "rules"
PDPA = "個人資料保護法"
FSC_RULES = "金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法"

_HDR_RE = re.compile(r"^#\s*(?P<k>[^:：]+)[:：]\s*(?P<v>.*)$")


def load_fixture(path: Path, *, parent_law: str = "") -> LawDocument:
    """測試用解析器：「# 」檔頭、「## 」章節、「### 第 X 條」條號，其餘每行為一項／款（原文照錄）。"""
    meta: dict[str, str] = {}
    articles: list[Article] = []
    chapter = ""
    cur: Article | None = None
    lines: list[str] = []

    def flush():
        if cur is not None:
            cur.text = "\n".join(lines)
            articles.append(cur)

    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("### "):
            flush()
            cur, lines = Article(no=raw[4:].strip(), text="", chapter=chapter), []
        elif raw.startswith("## "):
            chapter = raw[3:].strip()
        elif raw.startswith("#"):
            m = _HDR_RE.match(raw)
            if m and cur is None:
                meta.setdefault(m.group("k").strip(), m.group("v").strip())
        elif raw.strip() and cur is not None:
            lines.append(raw)
    flush()
    return LawDocument(
        name=meta.get("法規名稱", path.stem),
        source_name="測試 fixture",
        source_url=meta.get("來源 URL", "").split(" ")[0],
        version_label=meta.get("版本", ""),
        articles=articles,
        parent_law=parent_law or meta.get("母法", ""),
    )


@pytest.fixture(scope="module")
def docs():
    old = load_fixture(FIX / "pdpa_112.txt")
    new = load_fixture(FIX / "pdpa_114.txt")
    sub = load_fixture(FIX / "fsc_pdpa_security_rules.txt")
    return old, new, sub


def test_fixtures_are_complete(docs):
    old, new, sub = docs
    assert old.name == new.name == PDPA
    assert sub.name == FSC_RULES and sub.parent_law == PDPA
    ok = {article_key(a.no) for a in old.articles}
    nk = {article_key(a.no) for a in new.articles}
    assert {str(i) for i in range(1, 57)} <= ok
    assert {str(i) for i in range(1, 57)} <= nk
    assert len(old.articles) == 57 and len(new.articles) == 66   # 舊 56 + 1-1；新再加 9 條增訂（第 27 條保留「（刪除）」）
    assert "20-1" not in ok and "20-1" in nk
    # 防杜撰：真實條文中沒有第 20-2～20-5 條
    for k in ("20-2", "20-3", "20-4", "20-5"):
        assert k not in ok and k not in nk
    assert sub.articles[0].text == "本辦法依個人資料保護法（以下簡稱本法）第二十七條第三項規定訂定之。"


# ---------------------------------------------------------------- AST diff（#5）
EXPECT_ADDED = {"1-2", "20-1", "21-1", "21-2", "21-3", "21-4", "21-5", "51-1", "53-1"}
EXPECT_DELETED = {"27"}
EXPECT_MODIFIED = {"1-1", "12", "18", "21", "22", "23", "24", "25", "26", "41", "47", "48", "49",
                   "52", "53", "55"}


@pytest.fixture(scope="module")
def diffs(docs):
    diff_mod = pytest.importorskip("regcrawler.legal_ast.diff")
    old, new, _ = docs
    return diff_mod.diff_laws(old, new)


def _keys(diffs, change):
    return {d.key for d in diffs if d.change == change}


def test_added_set(diffs):
    assert _keys(diffs, DIFF_ADDED) == EXPECT_ADDED


def test_deleted_set(diffs):
    assert _keys(diffs, DIFF_DELETED) == EXPECT_DELETED


def test_modified_set(diffs):
    assert _keys(diffs, DIFF_MODIFIED) == EXPECT_MODIFIED


def test_no_fabricated_articles(diffs):
    all_keys = {d.key for d in diffs}
    nos = {d.old_no for d in diffs} | {d.new_no for d in diffs}
    for k in ("20-2", "20-3", "20-4", "20-5"):
        assert k not in all_keys
        assert not any(n and article_key(n) == k for n in nos)


# ---------------------------------------------------------------- 語意規則（#6）
def _art(doc: LawDocument, key: str) -> str:
    for a in doc.articles:
        if article_key(a.no) == key:
            return a.text
    return ""


def test_article_12_notice_report(docs):
    old, new, _ = docs
    res = semantic.classify(_art(old, "12"), _art(new, "12"))
    print("\n[第 12 條] classify =", res if res else "（無：罰則加重／通知時限縮短／通報時限縮短 均未判定）")
    types = [t for t, _ in res]
    # 依原文：舊法「應查明後以適當方式通知當事人」、新法「應通知當事人」「應通報下列機關」，
    # 兩版條文本身均未載明具體時限（新法第 4 項授權主管機關以辦法定「時限」），
    # 第 3 項之「即時」修飾「應變措施」且該句無「通知／通報」，不得歸屬為通知或通報時限。
    assert DIFF_NOTICE_SHORTER not in types
    assert DIFF_REPORT_SHORTER not in types
    assert DIFF_PENALTY_UP not in types


@pytest.mark.parametrize("key", ["47", "48", "49"])
def test_articles_47_49_penalty(docs, key):
    old, new, _ = docs
    res = semantic.classify(_art(old, key), _art(new, key))
    print(f"\n[第 {key} 條] classify =", res if res else "（無）")
    types = [t for t, _ in res]
    if key == "48":
        # 新法第 48 條第 2 項新增違反通報義務之罰鍰規定（罰鍰片段 4 → 5），屬新增處罰態樣
        assert types == [DIFF_PENALTY_UP]
        rat = res[0][1]
        assert "無法一對一對應，判斷依據為最高額比較" in rat
        assert "4 處 → 5 處" in rat
    else:
        # 第 47、49 條罰鍰額度（五萬～五十萬；二萬～二十萬）新舊相同，裁處機關改為「主管機關」
        assert DIFF_PENALTY_UP not in types


def test_new_articles_no_spurious_deadline(docs):
    old, new, _ = docs
    for key in EXPECT_ADDED:
        res = semantic.classify("", _art(new, key))
        assert all(t != DIFF_NOTICE_SHORTER and t != DIFF_REPORT_SHORTER for t, _ in res), (key, res)


# ---------------------------------------------------------------- 連動（#6）
def test_linkage_fsc_rules(docs, diffs):
    _, _, sub = docs
    recs = linkage.detect({PDPA: diffs}, [sub])
    print("\n[連動] ", [(r.affected_law, r.affected_article, r.parent_article, r.parent_change,
                         r.explanation) for r in recs])
    assert len(recs) == 1
    r = recs[0]
    assert r.affected_law == FSC_RULES
    assert r.affected_article == "第 1 條"
    assert r.parent_article == "個人資料保護法第二十七條第三項"
    assert r.parent_change == DIFF_DELETED
    assert "第 27 條" in r.explanation or "第27條" in r.explanation
    assert "應檢視" in r.explanation
    assert DIFF_LINKAGE == "連動影響"
