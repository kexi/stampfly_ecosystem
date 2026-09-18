"""
test_arbiter.py - the Arbiter's rule table: every uncertain case becomes
hovering, and hovering that will not end becomes a landing.
test_arbiter.py - Arbiter の規則表: 不確かな場合はすべて待機になり、
終わらない待機は着陸になること。
"""

from sfpilot.arbiter import Arbiter, VERDICT_HOVER, VERDICT_LAND
from sfpilot.config import DEFAULT_CONFIG
from sfpilot.judge import (
    ACT_CONTINUE,
    ACT_LAND,
    Answer,
    Judgement,
    Q_SAFETY,
)
from sfpilot.monitor import Assessment, SAFETY_LAND, SAFETY_NONE

SIG = "flight.altitude=on target"


def _healthy_assessment(**numeric):
    """An assessment with no immediate safety rule firing, inside the envelope.
    即時安全則が働かず、包絡内にある評価。"""
    values = {"altitude_m": 0.8, "pos_n": 0.0, "pos_e": 0.0}
    values.update(numeric)
    return Assessment(safety_action=SAFETY_NONE, numeric=values)


def _confident_continue(latency_ms=10.0):
    return Judgement(
        answers={Q_SAFETY: Answer(
            kind="choice", choice=ACT_CONTINUE, confidence=0.95,
            probabilities={ACT_CONTINUE: 0.95, ACT_LAND: 0.02},
        )},
        latency_ms=latency_ms,
    )


def _decide(arbiter, judgement, asked=SIG, current=SIG, now=0.0, assessment=None):
    return arbiter.decide(
        assessment or _healthy_assessment(),
        judgement,
        asked_signature=asked,
        current_signature=current,
        now=now,
    )


def test_confident_answer_is_accepted():
    """A fast, confident, in-envelope answer is carried out as given.
    速く・確信度が高く・包絡内の答えはそのまま実行されること。"""
    verdict = _decide(Arbiter(), _confident_continue())
    assert verdict.action == "continue"
    assert verdict.source == "judge"


def test_deadline_exceeded_becomes_hover():
    """An answer slower than the deadline is discarded in favour of hovering.
    期限より遅い答えは破棄され、待機になること。"""
    late = DEFAULT_CONFIG.judge.deadline_s * 1e3 + 1.0
    verdict = _decide(Arbiter(), _confident_continue(latency_ms=late))
    assert verdict.action == VERDICT_HOVER
    assert "期限超過" in verdict.reason


def test_signature_mismatch_discards_the_answer():
    """An answer about a situation that has since changed is not used.
    質問後に状況の区分が変わった場合、その答えは使われないこと。"""
    verdict = _decide(
        Arbiter(), _confident_continue(),
        asked="flight.altitude=on target",
        current="flight.altitude=below target",
    )
    assert verdict.action == VERDICT_HOVER
    assert "状況の区分が変わった" in verdict.reason


def test_low_confidence_becomes_hover():
    """A Choice below the confidence threshold is not acted on.
    確信度が閾値未満の Choice は実行されないこと。"""
    weak = Judgement(answers={Q_SAFETY: Answer(
        kind="choice", choice=ACT_CONTINUE,
        confidence=DEFAULT_CONFIG.judge.min_confidence - 0.01,
        probabilities={ACT_CONTINUE: 0.5, ACT_LAND: 0.1},
    )}, latency_ms=10.0)
    verdict = _decide(Arbiter(), weak)
    assert verdict.action == VERDICT_HOVER
    assert "確信度" in verdict.reason


def test_continue_land_tie_becomes_hover():
    """When "continue" and "land" are the close top two, neither is taken.
    「継続」と「着陸」が接近した上位 2 択のとき、どちらも実行しないこと。"""
    torn = Judgement(answers={Q_SAFETY: Answer(
        kind="choice", choice=ACT_CONTINUE, confidence=0.9,
        probabilities={ACT_CONTINUE: 0.46, ACT_LAND: 0.44, "hold": 0.10},
    )}, latency_ms=10.0)
    verdict = _decide(Arbiter(), torn)
    assert verdict.action == VERDICT_HOVER
    assert "拮抗" in verdict.reason


def test_outside_envelope_becomes_hover():
    """A "continue" proposed while outside the altitude envelope is refused.
    高度が包絡外のときの「継続」は却下されること。"""
    too_high = DEFAULT_CONFIG.envelope.altitude_max_m + 0.1
    verdict = _decide(
        Arbiter(), _confident_continue(),
        assessment=_healthy_assessment(altitude_m=too_high),
    )
    assert verdict.action == VERDICT_HOVER
    assert "包絡外" in verdict.reason


def test_outside_radius_becomes_hover():
    """A "continue" proposed beyond the radius limit is refused.
    離陸点からの距離が上限を超えるときの「継続」は却下されること。"""
    far = DEFAULT_CONFIG.envelope.radius_max_m + 0.5
    verdict = _decide(
        Arbiter(), _confident_continue(),
        assessment=_healthy_assessment(pos_n=far, pos_e=0.0),
    )
    assert verdict.action == VERDICT_HOVER
    assert "包絡外" in verdict.reason


def test_api_error_becomes_hover():
    """A failed API call produces hovering, not a guess.
    API 呼び出しの失敗は推測ではなく待機になること。"""
    verdict = _decide(Arbiter(), Judgement(latency_ms=10.0, error="HTTP 429"))
    assert verdict.action == VERDICT_HOVER
    assert "API エラー" in verdict.reason


def test_missing_answer_becomes_hover():
    """No answer yet is also hovering, never continuing by default.
    答えが未着の場合も待機であり、既定で継続はしないこと。"""
    verdict = _decide(Arbiter(), None)
    assert verdict.action == VERDICT_HOVER


def test_hovering_for_ten_seconds_becomes_landing():
    """Hovering held for the configured span turns into a landing.
    設定した時間だけ待機が続いたら着陸に変わること。"""
    arbiter = Arbiter()
    error = Judgement(latency_ms=10.0, error="HTTP 529")
    first = _decide(arbiter, error, now=0.0)
    assert first.action == VERDICT_HOVER

    limit = DEFAULT_CONFIG.arbiter.hover_to_land_s
    just_before = _decide(arbiter, error, now=limit - 0.1)
    assert just_before.action == VERDICT_HOVER

    at_limit = _decide(arbiter, error, now=limit)
    assert at_limit.action == VERDICT_LAND
    assert "待機が" in at_limit.reason


def test_hover_timer_resets_after_an_accepted_answer():
    """An accepted answer clears the hover timer, so unrelated hovering
    spells do not accumulate into a landing.
    答えが採用されると待機の計時が解除され、無関係な待機が積み上がって
    着陸にならないこと。"""
    arbiter = Arbiter()
    error = Judgement(latency_ms=10.0, error="HTTP 429")
    _decide(arbiter, error, now=0.0)
    _decide(arbiter, _confident_continue(), now=5.0)          # accepted / 採用
    late = _decide(arbiter, error, now=9.0)
    assert late.action == VERDICT_HOVER


def test_immediate_safety_outranks_the_judge():
    """The Monitor's immediate rule wins even against a confident "continue".
    Monitor の即時安全則は、確信度の高い「継続」よりも優先されること。"""
    assessment = _healthy_assessment()
    assessment.safety_action = SAFETY_LAND
    assessment.safety_reason = "電池が危険域のため即時着陸"
    verdict = _decide(Arbiter(), _confident_continue(), assessment=assessment)
    assert verdict.action == VERDICT_LAND
    assert verdict.source == "monitor"
