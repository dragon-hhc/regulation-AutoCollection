"""regcrawler.rules.semantic 單元測試（邊界案例）。

註：以下測試字串為「規則邊界案例」的合成輸入，不是任何法規的條文；真實條文驗收見
tests/test_acceptance_pdpa_114.py。
"""
from __future__ import annotations

import pytest

from regcrawler.models import DIFF_NOTICE_SHORTER, DIFF_PENALTY_UP, DIFF_REPORT_SHORTER
from regcrawler.rules import semantic
from regcrawler.rules.semantic import classify


def _types(res):
    return [t for t, _ in res]


def _get(res, t):
    return dict(res).get(t, "")


# ---------------------------------------------------------------- 數字
@pytest.mark.parametrize("s,v", [
    ("二十", 20), ("十五", 15), ("一百零五", 105), ("二百萬", 2_000_000), ("一千五百萬", 15_000_000),
    ("十五萬", 150_000), ("1,500萬", 15_000_000), ("２０", 20), ("一億", 100_000_000),
    ("二億五千萬", 250_000_000), ("五十萬", 500_000),
])
def test_parse_number(s, v):
    assert semantic._parse_number(s) == v


def test_fallback_extractors_match_lexer_if_present():
    text = "處新臺幣二萬元以上二百萬元以下罰鍰，並於七十二小時內通報，即時通知"
    fb_a = semantic._fallback_extract_amounts(text)
    fb_d = semantic._fallback_extract_durations(text)
    assert [v for _, v in fb_a] == [20_000, 2_000_000]
    assert sorted(v for _, v in fb_d) == [0.0, 72.0]
    if semantic._ext_amounts is not None:
        assert [v for _, v in semantic._ext_amounts(text)] == [20_000, 2_000_000]


@pytest.mark.parametrize("use_lexer", [True, False])
def test_works_with_and_without_lexer(monkeypatch, use_lexer):
    if not use_lexer:
        monkeypatch.setattr(semantic, "_ext_amounts", None)
        monkeypatch.setattr(semantic, "_ext_durations", None)
    res = classify("處新臺幣二萬元以上二十萬元以下罰鍰。應於七十二小時內通報主管機關。",
                   "處新臺幣二萬元以上二百萬元以下罰鍰。應於二十四小時內通報主管機關。")
    assert _types(res) == [DIFF_PENALTY_UP, DIFF_REPORT_SHORTER]


# ---------------------------------------------------------------- 罰則
def test_fine_upper_raised_with_quote():
    res = classify("由主管機關處新臺幣二萬元以上二十萬元以下罰鍰。",
                   "由主管機關處新臺幣二萬元以上二百萬元以下罰鍰。")
    assert _types(res) == [DIFF_PENALTY_UP]
    r = _get(res, DIFF_PENALTY_UP)
    assert "罰鍰上限" in r and "二十萬元" in r and "二百萬元" in r and "（提高）" in r
    assert "200,000" in r and "2,000,000" in r


def test_fine_lower_raised():
    res = classify("處新臺幣二萬元以上二十萬元以下罰鍰", "處新臺幣五萬元以上二十萬元以下罰鍰")
    assert "罰鍰下限" in _get(res, DIFF_PENALTY_UP)


def test_fine_unchanged_or_lowered_is_not_penalty_up():
    assert classify("處新臺幣五萬元以上五十萬元以下罰鍰", "處新臺幣五萬元以上五十萬元以下罰鍰") == []
    assert classify("處新臺幣五萬元以上五十萬元以下罰鍰", "處新臺幣一萬元以上十萬元以下罰鍰") == []


def test_arabic_and_wan_amounts():
    res = classify("處新臺幣20萬元以下罰鍰", "處新臺幣1,500萬元以下罰鍰")
    assert DIFF_PENALTY_UP in _types(res)


def test_criminal_fine_raised():
    res = classify("處五年以下有期徒刑，得併科新臺幣一百萬元以下罰金。",
                   "處五年以下有期徒刑，得併科新臺幣一千萬元以下罰金。")
    r = _get(res, DIFF_PENALTY_UP)
    assert "罰金上限" in r and "一百萬元" in r and "一千萬元" in r


def test_prison_term_raised():
    res = classify("處二年以下有期徒刑", "處五年以下有期徒刑")
    assert "刑度提高" in _get(res, DIFF_PENALTY_UP)


def test_cross_type_not_compared():
    # 舊為罰金 200 萬、新為罰鍰 300 萬：不可把 200 萬 → 300 萬當成「上限提高」
    res = classify("處新臺幣二百萬元以下罰金。", "處新臺幣三百萬元以下罰鍰。")
    r = _get(res, DIFF_PENALTY_UP)
    assert "上限" not in r
    assert "不同處罰類型不跨類比較" in r
    # 反向：舊罰鍰 300 萬、新罰金 100 萬，亦不得以金額比較
    r2 = _get(classify("處新臺幣三百萬元以下罰鍰。", "處新臺幣一百萬元以下罰金。"), DIFF_PENALTY_UP)
    assert "上限" not in r2


def test_fine_vs_prison_not_compared():
    # 罰鍰金額與徒刑不可互比；但新增徒刑屬刑罰新增
    res = classify("處新臺幣十萬元以下罰鍰", "處新臺幣十萬元以下罰鍰；處一年以下有期徒刑")
    r = _get(res, DIFF_PENALTY_UP)
    assert "新增刑罰" in r and "罰鍰上限" not in r


def test_per_instance_added():
    res = classify("處新臺幣五萬元以上五十萬元以下罰鍰。",
                   "處新臺幣五萬元以上五十萬元以下罰鍰，並令限期改正，屆期未改正者，按次處罰之。")
    assert "新增按次處罰" in _get(res, DIFF_PENALTY_UP)


def test_paragraph_count_mismatch_uses_max():
    old = "處新臺幣二萬元以上二十萬元以下罰鍰。"
    new = "處新臺幣二萬元以上二十萬元以下罰鍰。\n情節重大者，處新臺幣十五萬元以上一千五百萬元以下罰鍰。"
    r = _get(classify(old, new), DIFF_PENALTY_UP)
    assert "無法一對一對應，判斷依據為最高額比較" in r
    assert "最高上限" in r and "1 處 → 2 處" in r


def test_new_subject_added():
    old = "公務員違反第五條規定者，處新臺幣十萬元以下罰鍰。"
    new = "公務員或受委託者違反第五條規定者，處新臺幣十萬元以下罰鍰。"
    assert "新增處罰對象" in _get(classify(old, new), DIFF_PENALTY_UP)


def test_new_article_with_penalty():
    res = classify("", "違反前條規定者，處新臺幣十萬元以上一百萬元以下罰鍰。")
    r = _get(res, DIFF_PENALTY_UP)
    assert "新增條文" in r and "新增罰鍰" in r


def test_old_without_penalty_new_with_penalty():
    r = _get(classify("非公務機關應採行適當之安全措施。",
                      "非公務機關應採行適當之安全措施；違反者，處新臺幣二萬元以下罰鍰。"), DIFF_PENALTY_UP)
    assert "舊條文無罰則規定" in r


def test_damages_amount_is_not_penalty():
    # 損害賠償金額（非罰鍰／罰金）不得判為罰則加重
    assert classify("以每人每一事件新臺幣五百元以上二萬元以下計算。",
                    "以每人每一事件新臺幣一千元以上五萬元以下計算。") == []


# ---------------------------------------------------------------- 時限
def test_notice_shortened_days_to_hours():
    res = classify("應於三日內通知當事人。", "應於二十四小時內通知當事人。")
    assert _types(res) == [DIFF_NOTICE_SHORTER]
    r = _get(res, DIFF_NOTICE_SHORTER)
    assert "三日內" in r and "二十四小時內" in r and "72" in r and "24" in r


def test_report_shortened_to_immediate():
    res = classify("應於一小時內通報主管機關。", "應立即通報主管機關。")
    assert _types(res) == [DIFF_REPORT_SHORTER]
    assert "立即" in _get(res, DIFF_REPORT_SHORTER)


def test_immediate_is_zero_hours():
    res = classify("應於十日內通知當事人。", "應即時通知當事人。")
    assert "0 小時" in _get(res, DIFF_NOTICE_SHORTER)


def test_deadline_lengthened_not_reported():
    assert classify("應於二十四小時內通報主管機關。", "應於七十二小時內通報主管機關。") == []


def test_notice_and_report_not_interchangeable():
    # 只有「通報」縮短，「通知」不得被判定
    res = classify("應於七十二小時內通報主管機關，並於七日內通知當事人。",
                   "應於二十四小時內通報主管機關，並於七日內通知當事人。")
    assert _types(res) == [DIFF_REPORT_SHORTER]


def test_same_sentence_nearest_verb_and_rationale():
    res = classify("應通知當事人並通報主管機關。",
                   "應通知當事人，並於一小時內通報主管機關。")
    assert _types(res) == [DIFF_REPORT_SHORTER]
    r = _get(res, DIFF_REPORT_SHORTER)
    assert "舊法無此時限規定，新增" in r
    assert "同句同時出現「通知」與「通報」" in r and "判定為「通報」" in r


def test_same_sentence_notice_nearest():
    res = classify("知悉後應通報主管機關，並通知當事人。",
                   "知悉後應通報主管機關，並於二十四小時內通知當事人。")
    assert _types(res) == [DIFF_NOTICE_SHORTER]


def test_immediate_without_verb_ignored():
    # 「即時」修飾應變措施，且該句沒有「通知／通報」→ 不得判定
    assert classify("應採取適當措施。", "應採取即時有效之應變措施。") == []


def test_new_article_with_deadline():
    res = classify("", "非公務機關知悉資安事件後，應於七十二小時內通報主管機關。")
    r = _get(res, DIFF_REPORT_SHORTER)
    assert r.startswith("舊法無此時限規定，新增")
    assert "七十二小時內" in r


def test_ordinal_day_not_deadline():
    # 「第十日」為序數，「逾十五日」無「內」，皆非時限
    assert classify("", "於第十日內通知當事人。") == []
    assert classify("", "延長之期間不得逾十五日，並應將其原因以書面通知請求人。") == []


def test_working_days_and_fullwidth_digits():
    res = classify("應於５個工作日內通報。", "應於２個工作日內通報。")
    assert _types(res) == [DIFF_REPORT_SHORTER]


def test_empty_inputs():
    assert classify("", "") == []
    assert classify(None, None) == []  # type: ignore[arg-type]
