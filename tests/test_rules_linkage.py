"""regcrawler.rules.linkage 單元測試。

真實條文：金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法第 1 條
（tests/fixtures/rules/fsc_pdpa_security_rules.txt，來源 https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=G0380233）。
母法條文以 ArticleDiff 直接構造（第 27 條舊條文取自 tests/fixtures/rules/pdpa_112.txt），
不依賴 #5 的 diff_laws；其餘「XX法」「XX辦法」為邊界案例的合成名稱，非真實法規。
"""
from __future__ import annotations

from pathlib import Path

from regcrawler.models import (DIFF_ADDED, DIFF_DELETED, DIFF_MODIFIED, Article, ArticleDiff,
                               LawDocument)
from regcrawler.rules.linkage import detect
from tests.test_acceptance_pdpa_114 import load_fixture

FIX = Path(__file__).parent / "fixtures" / "rules"
PDPA = "個人資料保護法"

OLD_27 = ("非公務機關保有個人資料檔案者，應採行適當之安全措施，防止個人資料被竊取、竄改、毀損、滅失或洩漏。\n"
          "中央目的事業主管機關得指定非公務機關訂定個人資料檔案安全維護計畫或業務終止後個人資料處理方法。\n"
          "前項計畫及處理方法之標準等相關事項之辦法，由中央目的事業主管機關定之。")


def _d(key, change, old_no="", new_no="", old="", new=""):
    return ArticleDiff(key=key, old_no=old_no, new_no=new_no, change=change, old_text=old, new_text=new)


def _sub(name, parent, *arts):
    return LawDocument(name=name, source_name="t", source_url="", parent_law=parent,
                       articles=[Article(no=n, text=t) for n, t in arts])


def test_real_fsc_rules_deleted_parent():
    sub = load_fixture(FIX / "fsc_pdpa_security_rules.txt")
    changes = {PDPA: [_d("27", DIFF_DELETED, old_no="第 27 條", old=OLD_27),
                      _d("1", "未變動", "第 1 條", "第 1 條")]}
    recs = detect(changes, [sub])
    assert len(recs) == 1
    r = recs[0]
    assert (r.affected_law, r.affected_article, r.parent_article, r.parent_change) == (
        "金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法", "第 1 條",
        "個人資料保護法第二十七條第三項", DIFF_DELETED)
    assert "已刪除" in r.explanation and "應檢視" in r.explanation


def test_halfwidth_parenthesis_as_in_spec():
    sub = _sub("某辦法", PDPA, ("第 1 條", "本辦法依個人資料保護法(以下簡稱本法)第二十七條第三項規定訂定之。"))
    recs = detect({PDPA: [_d("27", DIFF_DELETED, old_no="第 27 條", old=OLD_27)]}, [sub])
    assert [r.parent_article for r in recs] == ["個人資料保護法第二十七條第三項"]


def test_benfa_refers_to_parent_law():
    sub = _sub("甲辦法", "甲法", ("第 3 條", "依本法第十二條規定所為之通知，應以書面為之。"))
    recs = detect({"甲法": [_d("12", DIFF_MODIFIED, "第 12 條", "第 12 條", "舊。", "新。")]}, [sub])
    assert len(recs) == 1
    assert recs[0].parent_article == "甲法第十二條"
    assert recs[0].parent_change == DIFF_MODIFIED
    assert "「本法」指甲法" in recs[0].explanation


def test_benfa_in_a_law_itself_not_resolved_to_parent():
    # 文件本身是「法」：條文中的「本法」指自己，不得連動到 parent_law
    doc = _sub("乙條例", "甲法", ("第 2 條", "本法第五條所定事項，由主管機關定之。"))
    assert detect({"甲法": [_d("5", DIFF_DELETED, "第 5 條")]}, [doc]) == []


def test_unchanged_or_added_parent_no_record():
    sub = _sub("甲辦法", "甲法", ("第 1 條", "本辦法依甲法第八條規定訂定之。"))
    assert detect({"甲法": [_d("8", "未變動", "第 8 條", "第 8 條")]}, [sub]) == []
    assert detect({"甲法": [_d("8", DIFF_ADDED, "", "第 8 條")]}, [sub]) == []


def test_article_with_zhi_and_chinese_numerals():
    sub = _sub("甲辦法", "甲法", ("第 1 條", "本辦法依甲法第二十條之一第二項規定訂定之。"))
    recs = detect({"甲法": [_d("20-1", DIFF_DELETED, "第 20-1 條")]}, [sub])
    assert len(recs) == 1 and recs[0].parent_article == "甲法第二十條之一第二項"


def test_modified_parent_paragraph_removed():
    old = "第一項內容。\n第二項內容：\n一、款一。\n二、款二。\n第三項內容。"
    new = "第一項內容。\n第二項內容：\n一、款一。\n二、款二。"
    sub = _sub("甲辦法", "甲法", ("第 1 條", "本辦法依甲法第十條第三項規定訂定之。"))
    recs = detect({"甲法": [_d("10", DIFF_MODIFIED, "第 10 條", "第 10 條", old, new)]}, [sub])
    assert len(recs) == 1
    e = recs[0].explanation
    assert "由 3 項減為 2 項" in e and "第3項於新版已不存在" in e


def test_modified_parent_paragraph_still_exists():
    old = "第一項。\n第二項舊。"
    new = "第一項。\n第二項新。\n第三項新增。"
    sub = _sub("甲辦法", "甲法", ("第 1 條", "本辦法依甲法第十條第二項規定訂定之。"))
    e = detect({"甲法": [_d("10", DIFF_MODIFIED, "第 10 條", "第 10 條", old, new)]}, [sub])[0].explanation
    assert "第2項仍存在" in e and "該項文字已變動" in e


def test_other_laws_not_in_changes_ignored():
    sub = load_fixture(FIX / "fsc_pdpa_security_rules.txt")
    # 第 2 條引用金融控股公司法、信用合作社法等，不在 changes 中 → 不產生紀錄
    recs = detect({PDPA: [_d("4", DIFF_DELETED, "第 4 條")]}, [sub])
    assert recs == []


def test_range_reference():
    sub = _sub("甲辦法", "甲法", ("第 1 條", "依甲法第二十二條至第二十六條規定辦理。"))
    recs = detect({"甲法": [_d("24", DIFF_MODIFIED, "第 24 條", "第 24 條", "a", "b"),
                           _d("30", DIFF_DELETED, "第 30 條")]}, [sub])
    assert [r.parent_change for r in recs] == [DIFF_MODIFIED]


def test_dedupe_and_multiple_laws():
    sub = _sub("甲辦法", "甲法", ("第 1 條", "依甲法第三條及本法第三條，並參照乙法第四條。"))
    changes = {"甲法": [_d("3", DIFF_DELETED, "第 3 條")], "乙法": [_d("4", DIFF_DELETED, "第 4 條")]}
    recs = detect(changes, [sub])
    assert sorted(r.parent_article for r in recs) == ["乙法第四條", "甲法第三條"]


def test_empty_inputs():
    assert detect({}, []) == []
    assert detect({PDPA: []}, [_sub("x辦法", PDPA)]) == []
