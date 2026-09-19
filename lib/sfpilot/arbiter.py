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
| outside envelope / 飛行領域外    | hover / 待機   |
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
        choice = self._effective_choice(answer)
        is_outside_envelope, envelope_reason = self._envelope_breach(choice, assessment)
        if is_outside_envelope:
            return self._hover(envelope_reason, now)

        self._hover_since = None
        action = VERDICT_CONTINUE if choice == ACT_CONTINUE else choice
        if choice == ACT_HOLD:
            # Jev choosing to wait is still waiting; count it toward the
            # hover-to-land timer, or a model that always says "hold"
            # would keep the aircraft up indefinitely.
            # Jev が待機を選んだ場合も待機である。待機継続の計時に含める。
            # そうしないと、常に「待機」と答えるモデルが機体を浮かせ続ける。
            #
            # A hold DERIVED from an unsure answer is marked apart from one
            # Jev actually chose. Callers that walk a sequence stop on a
            # chosen hold, because the model asked them to wait -- but an
            # unsure answer is not a request to stop, and treating it as
            # one would end a route over an answer that mildly PREFERRED
            # carrying on (measured on the mission: continue 0.63-0.72
            # against hold 0.23-0.30 ended a route at its last leg).
            # 確信度の低い答えから**導いた**待機は、Jev が実際に選んだ待機と
            # 区別して印を付ける。手順の列を進める側は、選ばれた待機では止まる
            # （モデルが待てと言っているため）。しかし確信度の低い答えは停止の
            # 要請ではなく、それを停止として扱えば、むしろ継続をやや選好して
            # いた答えで経路を終わらせることになる（ミッションでの実測:
            # continue 0.63〜0.72 対 hold 0.23〜0.30 で、最後の区間が終わった）。
            was_chosen = answer.choice == ACT_HOLD
            reason = "Jev が待機を選択" if was_chosen else "確信度が低いため待機"
            verdict = self._hover(reason, now, source="judge", accepted=choice)
            verdict.detail["chosen_hold"] = was_chosen
            return verdict
        return Verdict(
            action=action,
            reason="Jev の判断を採用",
            source="judge",
            accepted_answer=choice,
            detail={"confidence": answer.confidence},
        )

    def _effective_choice(self, answer) -> str:
        """The choice actually acted on, which may be more cautious than Jev's.

        An unconfident answer torn between `continue` and `hold` is accepted
        as `hold`: `_reject` lets it through precisely because both halves
        are cautious, and taking the confident-sounding half of an answer
        the model was unsure about would be reading more into it than it
        said. Holding is what the Arbiter does with no answer at all, so
        this changes nothing about the aircraft -- it only stops the hold
        being counted as "no usable opinion", which is what turned a drifting
        but safe flight into a landing (`_reject`).

        実際に行動の根拠とする選択。Jev の選択より慎重な側になることがある。

        確信度が低く `continue` と `hold` で割れた答えは `hold` として採用する。
        `_reject` がそれを通すのは、まさに両側とも慎重だからであり、モデルが
        迷っていた答えから自信のありそうな側だけを取れば、言われていないことを
        読み取ることになる。待機は、答えがまったく無いときに Arbiter が取る行動
        そのものなので、これで機体の動きは変わらない。変わるのは、その待機が
        「使える意見が無い」と数えられなくなることだけである。それこそが、
        流されてはいるが安全な飛行を着陸に変えていた（`_reject` 参照）。
        """
        is_unconfident = answer.confidence < self.min_confidence
        if not is_unconfident:
            return answer.choice
        return ACT_HOLD

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
            # An unconfident answer split between `continue` and `hold` is
            # not the model failing to tell safe from unsafe: both of those
            # are cautious readings of an unremarkable situation, and the
            # Arbiter's own response to "no usable answer" is to hover,
            # which IS `hold`. So the answer is downgraded to the cautious
            # half of what it was torn between, rather than discarded.
            #
            # Measured on the `drift` scene: the splits were 0.45/0.40,
            # 0.53/0.36 and 0.45/0.41 between continue and hold, with land
            # a distant 0.11-0.15. Discarding those is what produced 13
            # consecutive holds and a landing on a flight that was never in
            # danger. Anything involving `land` keeps the old treatment --
            # a model that cannot separate carrying on from ending the
            # flight is not one to act on.
            #
            # 確信度の低い答えが `continue` と `hold` で割れている場合、それは
            # モデルが安全と危険を区別できていないのではない。どちらも「特筆
            # すべきことのない状況」の慎重な読み方であり、そもそも Arbiter が
            # 「使える答えが無い」ときに取る行動は待機、すなわち `hold` そのもの
            # である。したがって答えを破棄せず、迷っていた 2 つのうち慎重な側へ
            # 格下げして採用する。
            #
            # `drift` 場面での実測: continue と hold の割れ方は 0.45/0.40、
            # 0.53/0.36、0.45/0.41 で、land は 0.11〜0.15 と大きく離れていた。
            # これらを破棄したことが、危険でない飛行で 13 回連続の待機と着陸を
            # 生んだ。`land` が絡む拮抗は従来どおり扱う — 飛行の継続と終了を
            # 区別できていないモデルの答えは、行動の根拠にしない。
            is_cautious_split = self._is_top_two(
                answer.probabilities, {ACT_CONTINUE, ACT_HOLD})
            if is_cautious_split:
                return None
            return f"確信度が閾値未満（{answer.confidence:.2f}）"

        if self._is_continue_land_tie(answer.probabilities):
            return "「継続」と「着陸」が拮抗"

        return None

    def _is_top_two(self, probabilities: dict, expected: set) -> bool:
        """Whether the two most likely options are exactly `expected`.
        最も確からしい 2 つの選択肢が、ちょうど `expected` と一致するか。"""
        if not probabilities or len(probabilities) < 2:
            return False
        ranked = sorted(probabilities.items(), key=lambda kv: kv[1], reverse=True)
        return {ranked[0][0], ranked[1][0]} == expected

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
        提案された行動を物理的な飛行領域と照合する。

        照合するのは `continue` のみ。待機と着陸はどちらも逸脱を小さくする
        方向であり、飛行領域外を理由に却下すると、外に出たまま取れる行動が
        無くなってしまう。
        """
        if choice != ACT_CONTINUE:
            return False, ""

        altitude = assessment.numeric.get("altitude_m")
        if altitude is not None:
            is_too_low = altitude < self.envelope.altitude_min_m
            is_too_high = altitude > self.envelope.altitude_max_m
            if is_too_low or is_too_high:
                return True, f"高度が飛行領域外（{altitude:.2f} m）"

        north = assessment.numeric.get("pos_n")
        east = assessment.numeric.get("pos_e")
        if north is not None and east is not None:
            radius = (north * north + east * east) ** 0.5
            is_too_far = radius > self.envelope.radius_max_m
            if is_too_far:
                return True, f"離陸点からの距離が飛行領域外（{radius:.2f} m）"

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
