"""#5 Legal AST 測試。fixture 為個人資料保護法真實條文，來源見 tests/fixtures/legal_ast/README.txt。"""
import json
from pathlib import Path

import pytest

from regcrawler.models import Article, LawDocument, DIFF_ADDED, DIFF_DELETED, DIFF_MODIFIED
from regcrawler.legal_ast import lexer as L
from regcrawler.legal_ast.parser import parse, parse_article, ARTICLE, PARAGRAPH, SUBPARAGRAPH, CHAPTER
from regcrawler.legal_ast.diff import diff_laws, is_deletion_marker, UNCHANGED
from regcrawler.legal_ast.codegen import render_inline, render_rationale, parse_markup, diff_segments

FIX = Path(__file__).parent / "fixtures" / "legal_ast"


def load(name: str) -> LawDocument:
    d = json.loads((FIX / name).read_text(encoding="utf-8"))
    assert d.pop("_fixture_source")["urls"], "fixture 必須註明來源 URL"
    return LawDocument.from_dict(d)


@pytest.fixture(scope="module")
def v104():
    return load("pdpa_104_art48.json")


@pytest.fixture(scope="module")
def v112():
    return load("pdpa_112.json")


@pytest.fixture(scope="module")
def v114():
    return load("pdpa_114.json")


def art(doc, no):
    return next(a for a in doc.articles if a.no == no)


def only(doc, *nos):
    return LawDocument(name=doc.name, source_name=doc.source_name, source_url=doc.source_url,
                       articles=[a for a in doc.articles if a.no in nos])


# ---------------- Lexing ----------------
def test_lexer_spans_roundtrip(v114):
    for a in v114.articles:
        toks = L.lex(a.text)
        assert "".join(t.text for t in toks) == a.text
        for t in toks:
            assert a.text[t.start:t.end] == t.text


def test_lexer_token_types_art48_114(v114):
    text = art(v114, "第 48 條").text
    toks = L.lex(text)
    marks = [t for t in toks if t.type == L.SUBPARA_MARK]
    assert [t.text for t in marks] == ["一、", "二、", "三、", "四、"] * 2
    assert [t.value for t in marks] == [1, 2, 3, 4] * 2
    assert sum(t.type == L.PARA_BREAK for t in toks) == text.count("\n")
    amounts = [(t.text, t.value) for t in toks if t.type == L.AMOUNT]
    assert ("新臺幣十五萬元", 150000) in amounts
    assert ("一千五百萬元", 15000000) in amounts
    assert ("二百萬元", 2000000) in amounts
    kws = {t.text for t in toks if t.type == L.PENALTY_KW}
    assert {"罰鍰", "按次處罰"} <= kws
    assert any(t.type == L.NOTICE_KW for t in toks)
    assert any(t.type == L.REPORT_KW for t in toks)
    refs = [t.text for t in toks if t.type == L.REF]
    assert "第二十條之一第一項" in refs and "第五十一條之一第三項" in refs and "前項" in refs


def test_lexer_ref_span():
    text = "一、違反第二十條之一第一項規定。"
    toks = L.lex(text)
    ref = next(t for t in toks if t.type == L.REF)
    assert (ref.start, ref.end) == (4, 13)
    assert text[ref.start:ref.end] == "第二十條之一第一項"
    assert toks[0].type == L.SUBPARA_MARK and (toks[0].start, toks[0].end) == (0, 2)


def test_lexer_item_and_subitem_marks():
    # 標記判定用的最小字串（非條文內容）
    toks = L.lex("（一）甲\n(二)乙\n1.丙\n(1)丁")
    types = [t.type for t in toks if t.type in L.STRUCTURAL]
    assert types == [L.ITEM_MARK, L.ITEM_MARK, L.SUBITEM_MARK, L.SUBITEM_MARK]


def test_heading_article_no():
    for no in ("第二十條之一", "第 20-1 條", "第20條之1", "Article 33"):
        toks = L.lex_heading(no)
        assert toks[0].type == L.ARTICLE_NO and toks[0].text == no


def test_extract_amounts():
    t = art(load("pdpa_114.json"), "第 48 條").text
    vals = [v for _, v in L.extract_amounts(t)]
    assert vals[:2] == [20000, 200000]
    assert 15000000 in vals and 150000 in vals
    assert L.extract_amounts("處新臺幣一億元以下罰鍰") == [("新臺幣一億元", 100000000)]
    assert L.extract_amounts("1,500萬元") == [("1,500萬元", 15000000)]


def test_extract_durations():
    got = dict(L.extract_durations("七十二小時內、三十六小時內、一個月內、即時、立即、十日、一年"))
    assert got == {"七十二小時內": 72.0, "三十六小時內": 36.0, "一個月內": 720.0, "即時": 0.0,
                   "立即": 0.0, "十日": 240.0, "一年": 8760.0}
    # 日期與序數不是期間
    assert L.extract_durations("民國一百十二年五月三十一日") == []
    assert L.extract_durations("第十日") == []
    # 真實條文：第 12 條（114）含「即時有效之應變措施」
    assert ("即時", 0.0) in L.extract_durations(art(load("pdpa_114.json"), "第 12 條").text)


def test_parse_number():
    assert L.parse_number("十五萬") == 150000
    assert L.parse_number("一千五百萬") == 15000000
    assert L.parse_number("二十") == 20
    assert L.parse_number("一百零五") == 105
    assert L.parse_number("３６") == 36


# ---------------- Parsing ----------------
def test_parser_tree_art48_114(v114):
    a = parse_article(art(v114, "第 48 條"))
    assert a.kind == ARTICLE and a.key == "48"
    assert [p.kind for p in a.children] == [PARAGRAPH] * 4
    assert [len(p.children) for p in a.children] == [4, 0, 4, 0]
    s = a.find("48/p3/s1")
    assert s.kind == SUBPARAGRAPH and s.label == "一、" and s.own_text == "一、違反第二十條之一第一項規定。"
    assert s.describe() == "第3項第1款"
    assert a.text == art(v114, "第 48 條").text


def test_parser_tree_art12_114(v114):
    a = parse_article(art(v114, "第 12 條"))
    assert len(a.children) == 4
    assert [c.label for c in a.children[1].children] == ["一、", "二、"]
    assert a.children[2].own_text.startswith("第一項情形")


def test_parser_items_nested():
    a = parse_article(Article("第 1 條", "甲：\n一、乙\n（一）丙\n1.丁\n二、戊\n己"))
    assert a.find("1/p1/s1/i1").label == "（一）"
    assert a.find("1/p1/s1/i1/si1").label == "1."
    assert a.find("1/p1/s2").label == "二、"
    assert a.find("1/p2").own_text == "己"


def test_parser_gdpr_paragraph_point():
    # 結構標記用的佔位字串（非條文內容）
    a = parse_article(Article("Article 33", "1. Alpha text.\n3. Beta text:\n(a) gamma;\n(b) delta;"))
    assert a.key == "art33"
    assert [p.label for p in a.children] == ["1.", "3."]
    assert [c.label for c in a.children[1].children] == ["(a)", "(b)"]


def test_parse_law_with_chapter(v114):
    doc = LawDocument(name=v114.name, source_name="", source_url="",
                      articles=[Article(a.no, a.text, "第一章 總則" if i < 2 else "第五章 罰則")
                                for i, a in enumerate(v114.articles)])
    law = parse(doc)
    assert [c.kind for c in law.children] == [CHAPTER, CHAPTER]
    assert [len(c.children) for c in law.children] == [2, 2]


# ---------------- Traversal / diff ----------------
def test_article_key_alignment_cn_vs_dash(v112, v114):
    old = LawDocument(name="個人資料保護法", source_name="", source_url="",
                      articles=[Article("第二十條之一", art(v114, "第 20-1 條").text)])
    new = only(v114, "第 20-1 條")
    (d,) = diff_laws(old, new)
    assert d.key == "20-1" and d.change == UNCHANGED
    assert d.old_no == "第二十條之一" and d.new_no == "第 20-1 條"
    assert any("article_key=20-1" in r for r in d.rationale)


def test_added_deleted_modified_and_deletion_marker(v112, v114):
    diffs = {d.key: d for d in diff_laws(v112, v114)}
    assert diffs["20-1"].change == DIFF_ADDED and diffs["20-1"].old_no == ""
    assert diffs["27"].change == DIFF_DELETED          # 新條文為「（刪除）」
    assert any("刪除標示" in r for r in diffs["27"].rationale)
    assert diffs["12"].change == DIFF_MODIFIED
    assert diffs["48"].change == DIFF_MODIFIED
    assert list(diffs) == ["12", "20-1", "27", "48"]


def test_old_has_new_missing_is_deleted(v112):
    new = only(v112, "第 12 條", "第 48 條")
    diffs = {d.key: d for d in diff_laws(v112, new)}
    assert diffs["27"].change == DIFF_DELETED and diffs["27"].new_no == ""
    assert diffs["12"].change == UNCHANGED


def test_deletion_marker_variants():
    for t in ("（刪除）", "(刪除)", "刪除", "（刪除）。", " （ 刪除 ） "):
        assert is_deletion_marker(t)
    assert not is_deletion_marker("中央目的事業主管機關得指定非公務機關訂定個人資料檔案安全維護計畫")


def test_node_changes_art48_104_to_112(v104, v112):
    (d,) = diff_laws(v104, only(v112, "第 48 條"))
    assert d.change == DIFF_MODIFIED
    assert d.node_changes == ["第1項第4款 刪除", "第2項 新增", "第3項 新增"]
    assert any(r.startswith("Lexing：舊條文 1 個項、4 個款") for r in d.rationale)
    assert any(r.startswith("Lexing：新條文 3 個項、3 個款") for r in d.rationale)
    assert any("第2項為新增節點" in r for r in d.rationale)


def test_node_changes_art48_112_to_114(v112, v114):
    d = next(x for x in diff_laws(v112, v114) if x.key == "48")
    nc = d.node_changes
    assert "第1項 修正" in nc
    assert "第1項第2款 修正" in nc             # 刪去「、第十二條」
    assert "第1項第3款 新增" in nc             # 新增違反第十二條第一項（通知）
    assert "第1項第4款 次序變更（原第1項第3款）" in nc
    assert "第2項 新增" in nc                  # 新增違反通報規定之罰則
    assert "第3項 修正（原第2項）" in nc
    assert "第4項 修正（原第3項）" in nc


def test_node_changes_art12(v112, v114):
    d = next(x for x in diff_laws(v112, v114) if x.key == "12")
    assert d.node_changes == ["第1項 修正", "第2項 新增", "第3項 新增", "第4項 新增"]
    assert any("REPORT_KW token 異動" in r for r in d.rationale)


def test_first_run_no_previous(v114):
    diffs = diff_laws(None, v114)
    assert len(diffs) == 4 and all(d.change == DIFF_ADDED for d in diffs)
    assert all(any("無前版可比對" in r for r in d.rationale) for d in diffs)


def test_move_detection_keeps_change_by_number(v112):
    a27 = art(v112, "第 27 條")
    old = LawDocument(name="x", source_name="", source_url="", articles=[a27])
    new = LawDocument(name="x", source_name="", source_url="",
                      articles=[Article("第 27 條", "（刪除）"), Article("第 20-1 條", a27.text)])
    diffs = {d.key: d for d in diff_laws(old, new)}
    assert diffs["27"].change == DIFF_DELETED and diffs["20-1"].change == DIFF_ADDED
    note = "疑似條次移列：舊第 27 條 → 新第 20-1 條"
    assert any(note in r for r in diffs["27"].rationale)
    assert any(note in r for r in diffs["20-1"].rationale)


# ---------------- Generation ----------------
def test_render_inline_format(v104, v112):
    old = art(v104, "第 48 條").text
    new = art(v112, "第 48 條").text
    out = render_inline(old, new)
    assert "[-四、-]{+非公務機關+}" in out
    # 還原：去掉新增 = 舊；去掉刪除 = 新
    segs = parse_markup(out)
    assert "".join(t for op, t in segs if op != "insert") == old
    assert "".join(t for op, t in segs if op != "delete") == new


def test_render_inline_simple_and_merge():
    assert render_inline("二十萬元", "二百萬元") == "二[-十-]{+百+}萬元"
    assert render_inline("", "（刪除）") == "{+（刪除）+}"
    assert render_inline("abc", "abc") == "abc"
    # 連續片段合併為一段
    assert render_inline("由中央目的事業主管機關", "由主管機關") == "由[-中央目的事業-]主管機關"


def test_render_inline_real_art12(v112, v114):
    out = render_inline(art(v112, "第 12 條").text, art(v114, "第 12 條").text)
    assert out.startswith("公務機關或非公務機關[-違反本法規定，致-]{+知悉所保有之+}個人資料被竊取、")


def test_render_rationale(v112, v114):
    d = next(x for x in diff_laws(v112, v114) if x.key == "48")
    txt = render_rationale(d, [("罰則加重", "測試用 rationale")])
    for s in ("1. Lexing", "2. Parsing", "3. Traversal", "4. Generation", "5. 語意規則", "罰則加重：測試用 rationale"):
        assert s in txt
    assert "未觸發" in render_rationale(d, [])
