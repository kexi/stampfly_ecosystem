"""
test_summarizer.py - the state sent to Jev holds words only, omits what
it does not know, and its signature tracks category changes.
test_summarizer.py - Jev へ送る state は言葉のみを持ち、不明な項目は省き、
指紋が区分の変化に追随すること。
"""

import pytest

from sfpilot.link import Sample
from sfpilot.monitor import Monitor
from sfpilot.summarizer import assert_no_numbers, signature, summarize


def _assessment(**fields):
    values = {"t": 0.0, "altitude_m": 0.8, "vel_n": 0.0, "vel_e": 0.0, "vel_d": 0.0,
              "roll": 0.0, "pitch": 0.0, "battery_pct": 80.0, "tof_m": 0.8}
    values.update(fields)
    return Monitor().update([Sample(**values)])


def test_state_contains_no_numbers_at_all():
    """No value anywhere in the state is a number — the model is not a
    calculator, so it must never be handed one to compare.
    state のどの値も数値でないこと — モデルは計算機ではないため、比較させる
    数値を渡してはならない。"""
    state = summarize(_assessment())
    assert_no_numbers(state)          # raises if a number slipped through / 数値があれば例外


def test_assert_no_numbers_actually_catches_one():
    """The guard is not vacuous: a planted number is rejected.
    この検査が空振りでないこと: 混入させた数値は拒否されること。"""
    with pytest.raises(ValueError, match="numeric value"):
        assert_no_numbers({"flight": {"altitude_m": 0.82}})


def test_assert_no_numbers_catches_a_number_inside_a_list():
    """Numbers hidden in `recent_events` are caught too.
    `recent_events` の中に隠れた数値も捕まえること。"""
    with pytest.raises(ValueError):
        assert_no_numbers({"recent_events": ["fine", 42]})


def test_unknown_fields_are_omitted_not_sent_as_unknown():
    """A field the Monitor could not classify is left out of the state,
    because irrelevant content lowers the model's accuracy.
    Monitor が区分できなかった項目は state から省くこと。無関係な内容は
    モデルの精度を下げるため。"""
    bare = Monitor().update([Sample(t=0.0, altitude_m=0.8)])
    state = summarize(bare)
    assert "battery" not in state
    assert "horizontal_drift" not in state["flight"]


def test_state_words_are_english():
    """Values are the English vocabulary, since the model is most
    accurate in English and these words are machine-generated.
    値は英語の語彙であること。モデルは英語で最も正確であり、これらの語は
    コードが生成するものだから。"""
    state = summarize(_assessment())
    assert state["flight"]["altitude"] == "on target"
    assert state["battery"]["level"] == "plenty left"


def test_operator_instruction_is_passed_through_unchanged():
    """The operator's own Japanese sentence is not translated or reworded.
    操作者自身の日本語の文は訳したり言い換えたりしないこと。"""
    instruction = "1m まで上がって前に少し進んで戻ってきて"
    state = summarize(_assessment(), operator_instruction=instruction)
    assert state["operator_instruction"] == instruction


def test_signature_is_stable_while_the_category_holds():
    """Two different numbers in the same band give the same signature.
    同じ区分に入る異なる数値は、同じ指紋になること。"""
    first = summarize(_assessment(altitude_m=0.80))
    second = summarize(_assessment(altitude_m=0.84))
    assert signature(first) == signature(second)


def test_signature_changes_when_the_category_changes():
    """Crossing into another band changes the signature, which is what
    makes a pending answer count as stale.
    別の区分に移れば指紋が変わること。これが未回答の答えを鮮度切れと
    判定する根拠になる。"""
    on_target = summarize(_assessment(altitude_m=0.80))
    below = summarize(_assessment(altitude_m=0.30))
    assert signature(on_target) != signature(below)


def test_signature_ignores_elapsed_time():
    """Mission elapsed time does not change the signature, or every
    answer would look stale on arrival.
    ミッションの経過時間で指紋が変わらないこと。変わると、届いた答えが
    すべて鮮度切れに見えてしまう。"""
    assessment = _assessment()
    a = summarize(assessment, mission={"goal": "square", "elapsed": "40 s"})
    b = summarize(assessment, mission={"goal": "square", "elapsed": "41 s"})
    assert signature(a) == signature(b)
