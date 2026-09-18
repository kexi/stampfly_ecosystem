"""
sfpilot.arbiter - decide what actually runs. Code only, no model.
sfpilot.arbiter - 実際に何を実行するかを決める。コードのみ、モデルは介さない。

The Arbiter is the answer to "what if Jev is slow, or unsure, or wrong?".
Every one of those cases resolves to the same thing -- hover in place --
because hovering is the action that neither commits to a manoeuvre nor
gives up a controlled flight. The rule table below is the design's table,
in the same order.

Arbiter は「Jev が遅い・迷っている・間違っているときどうするか」への答えである。
いずれの場合も結論は同じ **その場で待機** になる。待機は、動作に踏み込むこと
も、制御された飛行を手放すこともしない唯一の行動だからである。下の規則表は
設計の表をその順のまま実装したもの。

| condition / 条件            | verdict / 扱い |
|-----------------------------|----------------|
| deadline exceeded / 期限超過 | hover / 待機   |
| signature changed / 鮮度切れ | hover / 待機   |
| low confidence / 低確信      | hover / 待機   |
| continue vs land tie / 拮抗  | hover / 待機   |
| outside envelope / 包絡外    | hover / 待機   |
| API error / API エラー       | hover / 待機   |
| hovering for N s / 待機継続  | land / 着陸    |

Hovering is not free: holding position burns battery and resolves
nothing on its own. So the last rule converts a hover that will not end
into a landing, rather than letting the aircraft wait until the battery
is gone.

待機は無償ではない: 位置保持は電池を消費し、それ自体では何も解決しない。
そこで最後の規則が、終わらない待機を着陸に変える。電池が尽きるまで浮いた
ままにしないためである。
"""

from dataclasses import dataclass, field
from typing import Optional

from .config import ArbiterConfig, EnvelopeConfig, DEFAULT_CONFIG
from .judge import ACT_CONTINUE, ACT_HOLD, ACT_LAND, Q_SAFETY, Judgement
from .monitor import Assessment, SAFETY_NONE

# What the Executor is told to do. / Executor に渡す行動。
VERDICT_CONTINUE = "continue"
VERDICT_HOVER = "hover"
VERDICT_LAND = "land"
VERDICT_STOP = "stop"


@dataclass
class Verdict:
    """The decision, plus why it was reached (for the trace).
    決定と、その理由（記録用）。"""

    action: str = VERDICT_HOVER
    reason: str = ""
    source: str = "arbiter"          # "monitor" | "judge" | "arbiter"
    accepted_answer: Optional[str] = None
    detail: dict = field(default_factory=dict)


class Arbiter:
    """Apply the rule table to one Jev answer in one situation.
    1 つの状況における 1 つの Jev の答えに規則表を適用する。"""

    def __init__(self, config=DEFAULT_CONFIG):
        self.cfg: ArbiterConfig = config.arbiter
        self.envelope: EnvelopeConfig = config.envelope
        self.deadline_ms = config.judge.deadline_s * 1e3
        self.min_confidence = config.judge.min_confidence
        self.tie_margin = config.judge.tie_margin
        self._hover_since: Optional[float] = None

    def decide(
        self,
        assessment: Assessment,
        judgement: Optional[Judgement],
        asked_signature: str,
        current_signature: str,
        now: float,
    ) -> Verdict:
        """Return what the aircraft should do right now.

        `judgement` may be None, meaning the answer has not arrived yet.
        機体が今すべきことを返す。`judgement` が None なら答えが未着ということ。
        """
        # The Monitor's immediate safety rules outrank everything: they
        # exist precisely for situations that cannot wait for an opinion.
        # Monitor の即時安全則が最優先。意見を待てない状況のための規則だからである。
        has_immediate_action = assessment.safety_action != SAFETY_NONE
        if has_immediate_action:
            self._hover_since = None
            return Verdict(
                action=assessment.safety_action,
                reason=assessment.safety_reason,
                source="monitor",
            )

        rejection = self._reject(judgement, asked_signature, current_signature)
        if rejection is not None:
            return self._hover(rejection, now)

        answer = judgement.answers[Q_SAFETY]
        is_outside_envelope, envelope_reason = self._envelope_breach(answer.choice, assessment)
        if is_outside_envelope:
            return self._hover(envelope_reason, now)

        self._hover_since = None
        action = VERDICT_CONTINUE if answer.choice == ACT_CONTINUE else answer.choice
        if answer.choice == ACT_HOLD:
            # Jev choosing to wait is still waiting; count it toward the
            # hover-to-land timer, or a model that always says "hold"
            # would keep the aircraft up indefinitely.
            # Jev が待機を選んだ場合も待機である。待機継続の計時に含める。
            # そうしないと、常に「待機」と答えるモデルが機体を浮かせ続ける。
            return self._hover("Jev が待機を選択", now, source="judge",
                               accepted=answer.choice)
        return Verdict(
            action=action,
            reason="Jev の判断を採用",
            source="judge",
            accepted_answer=answer.choice,
            detail={"confidence": answer.confidence},
        )

    # -- rejection rules / 却下規則 -------------------------------------

    def _reject(self, judgement, asked_signature: str, current_signature: str):
        """Return the reason to reject this answer, or None to accept it.
        この答えを却下する理由を返す。採用してよければ None。"""
        if judgement is None:
            return "応答が未着"

        is_late = judgement.latency_ms > self.deadline_ms
        if is_late:
            return f"期限超過（{judgement.latency_ms:.0f}ms）"

        if judgement.error is not None:
            return f"API エラー: {judgement.error}"

        is_stale = asked_signature != current_signature
        if is_stale:
            return "質問時から状況の区分が変わった"

        answer = judgement.answers.get(Q_SAFETY)
        if answer is None:
            return "safety_action の答えが無い"

        is_unconfident = answer.confidence < self.min_confidence
        if is_unconfident:
            return f"確信度が閾値未満（{answer.confidence:.2f}）"

        if self._is_continue_land_tie(answer.probabilities):
            return "「継続」と「着陸」が拮抗"

        return None

    def _is_continue_land_tie(self, probabilities: dict) -> bool:
        """True when continue and land are the top two and close together.

        These two are opposites: one commits to the task, the other ends
        the flight. A near-tie between them is not a weak preference, it
        is the model being unable to tell a safe flight from an unsafe
        one, and neither action may be taken on that basis.
        「継続」と「着陸」が上位 2 択で接近しているとき True。

        この 2 つは正反対である（一方は任務を続け、他方は飛行を終える）。
        両者の拮抗は弱い選好ではなく、モデルが安全な飛行と危険な飛行を
        区別できていないということであり、どちらの行動もその上には置けない。
        """
        if not probabilities:
            return False
        ranked = sorted(probabilities.items(), key=lambda kv: kv[1], reverse=True)
        if len(ranked) < 2:
            return False
        top_two = {ranked[0][0], ranked[1][0]}
        is_opposite_pair = top_two == {ACT_CONTINUE, ACT_LAND}
        if not is_opposite_pair:
            return False
        return abs(ranked[0][1] - ranked[1][1]) <= self.tie_margin

    def _envelope_breach(self, choice: str, assessment: Assessment):
        """Check the proposed action against the physical envelope.

        Only `continue` is checked: hovering and landing both reduce the
        aircraft's excursion, so refusing them for being out of the
        envelope would strand it outside with nothing left to do.
        提案された行動を物理的な包絡と照合する。

        照合するのは `continue` のみ。待機と着陸はどちらも逸脱を小さくする
        方向であり、包絡外を理由に却下すると、外に出たまま取れる行動が
        無くなってしまう。
        """
        if choice != ACT_CONTINUE:
            return False, ""

        altitude = assessment.numeric.get("altitude_m")
        if altitude is not None:
            is_too_low = altitude < self.envelope.altitude_min_m
            is_too_high = altitude > self.envelope.altitude_max_m
            if is_too_low or is_too_high:
                return True, f"高度が包絡外（{altitude:.2f} m）"

        north = assessment.numeric.get("pos_n")
        east = assessment.numeric.get("pos_e")
        if north is not None and east is not None:
            radius = (north * north + east * east) ** 0.5
            is_too_far = radius > self.envelope.radius_max_m
            if is_too_far:
                return True, f"離陸点からの距離が包絡外（{radius:.2f} m）"

        return False, ""

    # -- hovering, and the hover-to-land timer / 待機と待機継続の計時 ----

    def _hover(self, reason: str, now: float, source: str = "arbiter",
               accepted: str = None) -> Verdict:
        """Hover, unless hovering has gone on long enough to mean landing.
        待機する。ただし待機が十分に続いていれば着陸に変える。"""
        if self._hover_since is None:
            self._hover_since = now
        held_s = now - self._hover_since
        is_hover_exhausted = held_s >= self.cfg.hover_to_land_s
        if is_hover_exhausted:
            self._hover_since = None
            return Verdict(
                action=VERDICT_LAND,
                reason=f"待機が {held_s:.1f} 秒続いたため着陸（直前の理由: {reason}）",
                source="arbiter",
            )
        return Verdict(
            action=VERDICT_HOVER,
            reason=reason,
            source=source,
            accepted_answer=accepted,
            detail={"hover_held_s": round(held_s, 2)},
        )
