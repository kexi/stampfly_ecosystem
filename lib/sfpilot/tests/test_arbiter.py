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
    ACT_HOLD,
    ACT_LAND,
    Answer,
    Judgement,
    Q_SAFETY,
)
from sfpilot.monitor import Assessment, SAFETY_LAND, SAFETY_NONE

SIG = "flight.altitude=on target"


def _healthy_assessment(**numeric):
    """An assessment with no immediate safety rule firing, inside the envelope.
    即時安全則が働かず、飛行領域内にある評価。"""
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
    速く・確信度が高く・飛行領域内の答えはそのまま実行されること。"""
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
    高度が飛行領域外のときの「継続」は却下されること。"""
    too_high = DEFAULT_CONFIG.envelope.altitude_max_m + 0.1
    verdict = _decide(
        Arbiter(), _confident_continue(),
        assessment=_healthy_assessment(altitude_m=too_high),
    )
    assert verdict.action == VERDICT_HOVER
    assert "飛行領域外" in verdict.reason


def test_outside_radius_becomes_hover():
    """A "continue" proposed beyond the radius limit is refused.
    離陸点からの距離が上限を超えるときの「継続」は却下されること。"""
    far = DEFAULT_CONFIG.envelope.radius_max_m + 0.5
    verdict = _decide(
        Arbiter(), _confident_continue(),
        assessment=_healthy_assessment(pos_n=far, pos_e=0.0),
    )
    assert verdict.action == VERDICT_HOVER
    assert "飛行領域外" in verdict.reason


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


# =============================================================================
# The cautious split: continue vs hold / 慎重な割れ方: continue と hold
# =============================================================================

def test_an_unconfident_split_between_continue_and_hold_is_held_not_discarded():
    """Torn between carrying on and waiting, the aircraft waits -- and that
    counts as an answer.

    Both halves are cautious readings of an unremarkable situation, and
    hovering is what the Arbiter does with no answer at all, so accepting
    the cautious half changes nothing about what the aircraft does. What it
    changes is the bookkeeping: the hold is no longer "no usable opinion",
    so it does not feed the hover-to-land timer.

    Measured on the `drift` scene against the live Jev (2026-09-19): splits
    of 0.45/0.40, 0.53/0.36 and 0.45/0.41 between continue and hold, with
    land a distant 0.11-0.15. Discarding those produced 13 consecutive
    holds and a landing on a flight that was never in danger.

    「続ける」と「待つ」で迷ったとき、機体は待つ。そしてそれは答えとして数える。

    どちらも「特筆すべきことのない状況」の慎重な読み方であり、答えがまったく
    無いときに Arbiter が取る行動も待機である。したがって慎重な側を採用しても、
    機体の動きは何も変わらない。変わるのは数え方で、この待機はもはや「使える
    意見が無い」ではなくなり、待機継続の計時に入らない。

    Jev 実走の `drift` 場面での実測（2026-09-19）: continue と hold の割れ方は
    0.45/0.40、0.53/0.36、0.45/0.41 で、land は 0.11〜0.15 と離れていた。これらを
    破棄したことが、危険でない飛行で 13 回連続の待機と着陸を生んだ。
    """
    split = Judgement(answers={Q_SAFETY: Answer(
        kind="choice", choice=ACT_CONTINUE, confidence=0.45,
        probabilities={ACT_CONTINUE: 0.45, ACT_HOLD: 0.40, ACT_LAND: 0.15},
    )}, latency_ms=10.0)

    verdict = _decide(Arbiter(), split)

    assert verdict.action == VERDICT_HOVER, "the cautious half is what is taken"
    assert verdict.source == "judge", "it is an answer, not a rejection"
    assert verdict.accepted_answer == ACT_HOLD, (
        "the confident-sounding half must not be read into an unsure answer"
    )


def test_a_cautious_split_does_not_run_down_the_hover_to_land_timer():
    """A held flight that keeps answering is not landed for hovering.

    The hover-to-land timer exists for a situation that is not resolving
    itself. A model that keeps saying "carry on, or wait" about a steady
    flight is resolving it -- into waiting -- and landing after ten seconds
    of that is the behaviour this fixes.

    答え続けている待機中の飛行が、待機を理由に着陸させられないこと。

    待機継続の計時は、自然に解消しない状況のためにある。安定した飛行について
    「続けるか、待つか」と答え続けるモデルは、その状況を（待機として）解消して
    いる。それを 10 秒で着陸させるのが、本修正の対象である。
    """
    split = Judgement(answers={Q_SAFETY: Answer(
        kind="choice", choice=ACT_CONTINUE, confidence=0.45,
        probabilities={ACT_CONTINUE: 0.45, ACT_HOLD: 0.40, ACT_LAND: 0.15},
    )}, latency_ms=10.0)
    arbiter = Arbiter()
    past_the_timer = DEFAULT_CONFIG.arbiter.hover_to_land_s + 1.0

    for now in (0.0, past_the_timer):
        verdict = _decide(arbiter, split, now=now)

    assert verdict.action == VERDICT_HOVER, "it must not have become a landing"


def test_an_unconfident_split_involving_land_is_still_discarded():
    """A model torn over ending the flight is not acted on at all.

    `land` in the top two means the model cannot separate carrying on from
    ending the flight, and neither action may rest on that -- the rule the
    design states, and the one case the change above deliberately leaves
    alone.

    飛行の終了で迷っているモデルの答えは、そもそも実行しないこと。

    上位 2 択に `land` があることは、モデルが「続ける」と「終える」を区別できて
    いないことを意味し、どちらの行動もその上には置けない。設計が述べる規則で
    あり、上記の変更が意図して手を触れない唯一の場合である。
    """
    torn = Judgement(answers={Q_SAFETY: Answer(
        kind="choice", choice=ACT_LAND, confidence=0.45,
        probabilities={ACT_LAND: 0.45, ACT_CONTINUE: 0.40, ACT_HOLD: 0.15},
    )}, latency_ms=10.0)

    verdict = _decide(Arbiter(), torn)

    assert verdict.action == VERDICT_HOVER
    assert verdict.source == "arbiter", "it is a rejection, not an accepted answer"
    assert "確信度" in verdict.reason


def test_a_derived_hold_is_marked_apart_from_one_jev_chose():
    """A hold the Arbiter derived is distinguishable from one Jev asked for.

    Callers that walk a sequence (`say.fly_plan`, `mission`) stop on a hold
    the model CHOSE, because that is the model asking them to wait. A hold
    derived from an answer too unsure to act on is not such a request, and
    the two must be told apart in the verdict rather than by guessing from
    the reason text.

    Arbiter が導いた待機と、Jev が求めた待機を区別できること。

    手順の列を進める側（`say.fly_plan`・`mission`）は、モデルが**選んだ**待機で
    止まる。それは待てという要請だからである。確信度が足りない答えから導いた
    待機はその要請ではなく、両者は理由の文面から推測するのではなく、判定そのもの
    で区別できなければならない。
    """
    chosen = Judgement(answers={Q_SAFETY: Answer(
        kind="choice", choice=ACT_HOLD, confidence=0.9,
        probabilities={ACT_HOLD: 0.9, ACT_CONTINUE: 0.08, ACT_LAND: 0.02},
    )}, latency_ms=10.0)
    derived = Judgement(answers={Q_SAFETY: Answer(
        kind="choice", choice=ACT_CONTINUE, confidence=0.45,
        probabilities={ACT_CONTINUE: 0.45, ACT_HOLD: 0.40, ACT_LAND: 0.15},
    )}, latency_ms=10.0)

    chosen_verdict = _decide(Arbiter(), chosen)
    derived_verdict = _decide(Arbiter(), derived)

    assert chosen_verdict.action == VERDICT_HOVER
    assert derived_verdict.action == VERDICT_HOVER
    assert chosen_verdict.detail["chosen_hold"] is True
    assert derived_verdict.detail["chosen_hold"] is False


def test_a_derived_hold_does_not_interrupt_a_sequence():
    """A sequence carries on through a hold the model did not ask for.

    Measured on `sf pilot mission` against the live Jev (2026-09-19): the
    route ended at its last leg on answers that PREFERRED carrying on
    (continue 0.63-0.72 against hold 0.23-0.30), because every derived hold
    read as an interrupt.

    モデルが求めていない待機では、手順の列が止まらないこと。

    Jev 実走の `sf pilot mission` での実測（2026-09-19）: むしろ継続を選好して
    いた答え（continue 0.63〜0.72 対 hold 0.23〜0.30）で経路が最後の区間で
    終わった。導かれた待機がすべて中断として読まれていたためである。
    """
    from sfpilot.say import _interrupt_reason

    class _Pilot:
        def __init__(self, verdict):
            self.decisions = [{"verdict": verdict}]

    derived = Judgement(answers={Q_SAFETY: Answer(
        kind="choice", choice=ACT_CONTINUE, confidence=0.45,
        probabilities={ACT_CONTINUE: 0.45, ACT_HOLD: 0.40, ACT_LAND: 0.15},
    )}, latency_ms=10.0)
    chosen = Judgement(answers={Q_SAFETY: Answer(
        kind="choice", choice=ACT_HOLD, confidence=0.9,
        probabilities={ACT_HOLD: 0.9, ACT_CONTINUE: 0.08, ACT_LAND: 0.02},
    )}, latency_ms=10.0)

    assert _interrupt_reason(_Pilot(_decide(Arbiter(), derived))) == ""
    assert _interrupt_reason(_Pilot(_decide(Arbiter(), chosen))) != ""
