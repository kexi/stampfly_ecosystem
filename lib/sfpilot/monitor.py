"""
sfpilot.monitor - turn numbers into words, and catch what must not wait.
sfpilot.monitor - 数値を言葉の区分に変え、待てないものはここで捕まえる。

Everything numeric happens here and nowhere else: thresholds, trends,
durations, comparisons. The design's first constraint is that Jev is not
a calculator, so by the time a situation reaches the Judge it must already
be a set of words ("目標どおり", "残り少ない") with no number attached.

数値の扱いはすべてここで完結する: しきい値・傾向・継続時間・比較。設計の
第一の制約は「Jev は計算機ではない」ことなので、Judge に届く時点で状況は
数値を含まない言葉の区分（「目標どおり」「残り少ない」）になっている必要がある。

The Monitor also owns the immediate safety rules. They exist because some
situations must not wait 500ms for an opinion: a dangerous battery, an
altitude far outside the envelope, a diverged position estimate. These
rules, and only these, may command `land`/`stop` without the Judge.

Monitor は即時安全則も担う。500ms の判断を待てない状況があるため:
電池の危険域、飛行領域を大きく外れた高度、発散した位置推定。この規則だけが
Judge を介さず `land`/`stop` を出せる。
"""

from dataclasses import dataclass, field
from typing import Optional

from .config import MonitorConfig, EnvelopeConfig, DEFAULT_CONFIG

# Immediate-safety verdicts. `emergency` is deliberately absent from this
# module and from judge.py: cutting the motors in flight means a crash,
# and no automatic rule in this package is trusted with that. It stays a
# human action through `sf blocks` / the transmitter.
# 即時安全則の判定。`emergency` は本モジュールにも judge.py にも無い:
# 飛行中のモータ停止は墜落を意味し、本パッケージのどの自動規則にもその
# 権限を与えない。人が `sf blocks`・送信機から出す操作のままにする。
SAFETY_NONE = "none"
SAFETY_STOP = "stop"
SAFETY_LAND = "land"

# The battery trend classifications, as named constants.
#
# The "fell sharply" one used to have its window length formatted into it
# ("この 10 秒で急に低下"), which made the summarizer's translation table
# key on a string that changed whenever `battery_trend_window_s` did. The
# lookup then missed, the untranslated Japanese went to Jev, and nothing
# failed loudly. The classification names the SHAPE; how long the window is
# stays in config.py, where the other numbers are.
#
# 電池の傾向の区分。名前付きの定数にする。
#
# 「急に低下」の区分は以前、窓の長さを文字列に埋め込んでいた（「この 10 秒で
# 急に低下」）。そのため summarizer の変換表が、`battery_trend_window_s` を
# 変えるたびに変わる文字列を鍵にしていた。変更すれば引きが外れ、訳されない
# 日本語が Jev へ渡り、しかもどこも失敗しなかった。区分が名指しするのは
# **形**である。窓の長さは、他の数値と同じく config.py に置く。
BATTERY_TREND_STEADY = "安定"
BATTERY_TREND_GRADUAL = "ゆるやかに低下"
BATTERY_TREND_SHARP = "急に低下"

# The firmware FlightState that means the craft is airborne under guidance.
# Named here because the altitude target may only be adopted in it: every
# other state (on the ground, climbing) holds an altitude that is not the
# one the flight will be judged against.
# 機体が誘導下で飛行中であることを表すファームの FlightState。高度の目標を
# 採用してよいのはこの状態だけなので、名前を付けてここに置く。他の状態
#（地上・上昇中）が保つ高度は、以後の飛行を判定する基準にはならない。
FLIGHT_STATE_FLYING = "FLYING"

# The forward-clearance classifications.
#
# "測定不能" (cannot measure) is deliberately NOT a synonym for "開けている"
# (open). The forward part reports an invalid reading both when nothing is
# ahead and when a surface is too close to resolve (§4.8.6 measured both),
# so treating an absent reading as clearance would fly the craft INTO the
# one case it cannot see. Unknown is its own word, and the immediate rule
# below never clears a stop on it.
#
# 前方の空きの区分。
#
# 「測定不能」は「開けている」の言い換えでは「ない」。前方の部品は、前に何も
# 無いときも、面が近すぎて復元できないときも無効を返す（§4.8.6 で両方を実測）。
# したがって無い読み値を「開けている」と扱うことは、見えていない方の場合へ機体を
# 突っ込ませることになる。「不明」は独立した語であり、下の即時則がそれで停止を
# 解除することはない。
FORWARD_OPEN = "開けている"
FORWARD_SOMEWHAT_NEAR = "やや近い"
FORWARD_WALL_NEAR = "壁が近い"
FORWARD_UNKNOWN = "測定不能"


@dataclass
class Assessment:
    """The Monitor's view of the present moment, in words.
    Monitor から見た現在の状況を言葉で表したもの。"""

    phase: str = "不明"
    altitude: str = "不明"
    altitude_trend: str = "不明"
    horizontal_drift: str = "不明"
    attitude: str = "不明"
    position_estimate: str = "不明"
    ground_distance_sensor: str = "不明"
    forward_clearance: str = "不明"
    battery_level: str = "不明"
    battery_trend: str = "不明"

    # Immediate-safety outcome and its reason, both set by the same rule.
    # 即時安全則の結果と理由。同じ規則が両方を設定する。
    safety_action: str = SAFETY_NONE
    safety_reason: str = ""

    # Kept for the Arbiter's envelope check and for the trace. Never sent
    # to Jev -- these are the numbers the words above were derived from.
    # Arbiter の飛行領域照合と記録のために保持する。Jev には渡さない — 上の
    # 言葉の元になった数値そのものだからである。
    numeric: dict = field(default_factory=dict)
    recent_events: list = field(default_factory=list)


class Monitor:
    """Classify a 50Hz sample stream into words and immediate actions.
    50Hz のサンプル列を言葉の区分と即時動作に分類する。"""

    def __init__(self, config=DEFAULT_CONFIG, target_altitude_m: float = None):
        self.cfg: MonitorConfig = config.monitor
        self.envelope: EnvelopeConfig = config.envelope
        self.forward = config.forward
        # The last VALID forward reading and when it arrived, so an invalid
        # one can be read as "the wall is still there, briefly unseen" rather
        # than as an empty room. `None` until the first valid reading.
        # 最後に有効だった前方の読み値と、その到着時刻。無効な読み値を「空の部屋」では
        # なく「壁はまだそこにあり、一時的に見えていない」と読めるようにするため。
        # 最初の有効な読み値まで `None`。
        self._forward_last_valid = None      # (t, metres)
        # True while the stop rule is latched, so the release threshold (not
        # the stop threshold) governs coming back out of it.
        # 停止則がラッチされている間 True。抜けるときに効くのは停止しきい値ではなく
        # 解除しきい値である。
        self._forward_stopped = False
        # The altitude "on target" is measured against. `None` means nobody
        # has said, and the first settled hover supplies it (`_adopt_target`).
        #
        # Why not a fixed default: the firmware holds WHATEVER altitude the
        # auto-takeoff climb reached -- `cmdTakeoff` seeds the guidance
        # target from the pose at FLYING rather than from a constant
        # (api_task.cpp, "Takeoff altitude is no longer an API constant").
        # A hard-coded figure is therefore wrong by however much the climb
        # differs from it, permanently and in one direction: with 0.8 m
        # against a measured hover of 0.441-0.483 m, every cycle of every
        # healthy nominal flight reported "below target" (measured
        # 2026-09-19, and the reason `run nominal` landed at 7.4 s).
        #
        # 「目標どおり」を測る基準の高度。`None` は誰も指定していないという
        # 意味で、最初に静定したホバリングがそれを与える（`_adopt_target`）。
        #
        # 固定の既定値にしない理由: ファームは自動離陸の上昇が**到達した高度を
        # そのまま**保持する。`cmdTakeoff` は誘導目標を定数からではなく FLYING
        # 到達時の姿勢から作る（api_task.cpp「Takeoff altitude is no longer an
        # API constant」）。したがって固定値は、上昇の到達高度との差だけ、恒久的に
        # かつ一方向へ誤る。実測 0.441〜0.483m のホバリングに対し 0.8m を置いた
        # 結果、健全な nominal 飛行の全周期が「目標より低い」と報告された
        # （2026-09-19 実測。`run nominal` が 7.4 秒で着陸した原因）。
        self.target_altitude_m = target_altitude_m
        self._history: list = []          # (t, altitude_m) for trends / 傾向判定用
        self._battery_history: list = []  # (t, battery_v) / 電池の傾向判定用
        self._events: list = []
        self._last_sample: Optional[dict] = None
        # When the first sample arrived, so the takeoff load transient can
        # be excluded from the battery trend (`_absorb_battery`).
        # 最初のサンプルが届いた時刻。離陸時の電圧降下を電池の傾向から除くため
        #（`_absorb_battery`）。
        self._first_sample_t: Optional[float] = None
        # Per field: (reported value, when the candidate started, candidate).
        # The debounce that keeps a band edge from redefining the situation
        # every sample -- see `_held`.
        # 項目ごとに (報告中の値, 候補が始まった時刻, 候補)。境目が毎サンプル
        # 状況を定義し直すのを防ぐ保持機構である（`_held` 参照）。
        self._reported: dict = {}

    @property
    def latest_sample(self) -> Optional[dict]:
        """The newest sample folded in, or None before the first one.

        Exposed because a caller sometimes needs the FIGURE rather than the
        classification: `sf pilot say` waits for the aircraft to come to
        rest between steps, and "drifting slowly" spans a range too wide to
        settle on. Everything sent to Jev still goes through the words.

        取り込んだ最新のサンプル。最初の 1 件より前は None。

        呼び出し側が区分ではなく**数値**を必要とする場合があるため公開する:
        `sf pilot say` は手順の間に機体が静止するのを待つが、「ゆっくり流されて
        いる」が表す幅は静定の判定には広すぎる。Jev へ送るものは従来どおり
        すべて言葉を通す。
        """
        return self._last_sample

    def update(self, samples: list) -> Assessment:
        """Fold new samples in and return the current assessment.
        新しいサンプルを取り込み、現在の評価を返す。"""
        for sample in samples:
            self._absorb(sample)
        if self._last_sample is None:
            return Assessment(safety_reason="サンプル未受信")
        return self._assess(self._last_sample)

    # -- ingestion / 取り込み ------------------------------------------

    def _absorb(self, sample: dict) -> None:
        """Record one sample and trim the history windows.
        1 サンプルを記録し、履歴の窓を刈り込む。"""
        self._last_sample = sample
        t = sample.get("t", 0.0)
        if "altitude_m" in sample:
            self._history.append((t, sample["altitude_m"]))
        self._absorb_battery(sample, t)
        # Keep only what the longest window needs, so memory is bounded
        # during a long flight.
        # 最も長い窓に必要な分だけ残す。長時間飛行でも使用量が増え続けない。
        self._history = _window(self._history, t, self.cfg.trend_hold_s * 2.0)
        # Keep slightly MORE than the window the trend measures over. If the
        # history were trimmed to exactly the window, its span would sit on
        # the "is the window full?" threshold and cross it back and forth as
        # the oldest sample fell in and out each cycle -- so the trend
        # alternated between "steady" and a real classification about twice a
        # second, on a pack that was simply draining (measured 2026-09-19).
        # The margin keeps the span comfortably past the threshold, and
        # `_trend_window` then selects the exact span to measure.
        # 傾向が測る窓より少しだけ長く保持する。窓ちょうどに刈り込むと、履歴の
        # 長さが「窓が満ちたか」の判定境界に乗り、最古のサンプルが毎周期出入り
        # するたびに境界を行き来する。その結果、ただ放電しているだけのパックに
        # ついて、傾向が毎秒 2 回ほど「安定」と本来の区分を往復した（2026-09-19
        # 実測）。余裕を持たせれば長さは判定境界から十分離れ、測る範囲は
        # `_trend_window` が厳密に選ぶ。
        self._battery_history = _window(
            self._battery_history, t,
            self.cfg.battery_trend_window_s + self.cfg.battery_window_margin_s,
        )

    def _absorb_battery(self, sample: dict, t: float) -> None:
        """Record the pack voltage, skipping the takeoff load transient.

        The first `battery_settle_s` of samples are dropped rather than
        recorded, because they span the step from an unloaded pack to a
        hovering one: 4.19 V to 3.79 V within a second, measured. Keeping
        them would put that step inside the trend window and report a full
        pack as collapsing (MonitorConfig.battery_drop_fast_v).

        The clock is the sample's own, so a replayed log behaves like the
        flight it recorded.

        パック電圧を記録する。離陸時の負荷による電圧降下は取り込まない。

        最初の `battery_settle_s` ぶんのサンプルは記録せず捨てる。無負荷の
        パックからホバリング中のパックへの段差（実測で 1 秒以内に 4.19V →
        3.79V）をまたぐためである。残せばその段差が傾向の窓に入り、満充電の
        パックを「崩れている」と報告することになる
        （MonitorConfig.battery_drop_fast_v）。

        時刻はサンプル自身のものを使う。再生したログが、記録した飛行と同じに
        振る舞うようにするためである。
        """
        voltage = _battery_volts(sample)
        if voltage is None:
            return
        if self._first_sample_t is None:
            self._first_sample_t = t
        is_still_settling = (t - self._first_sample_t) < self.cfg.battery_settle_s
        if is_still_settling:
            return
        self._battery_history.append((t, voltage))

    # -- classification / 区分 -----------------------------------------

    def _assess(self, sample: dict) -> Assessment:
        """Build the full assessment from the newest sample plus history.
        最新サンプルと履歴から評価一式を組み立てる。"""
        assessment = Assessment()
        assessment.numeric = dict(sample)
        assessment.recent_events = list(self._events)
        assessment.phase = str(sample.get("flight_state", "飛行中"))

        altitude = sample.get("altitude_m")
        # Adopt before classifying, so the cycle the hover settles on is
        # already reported against it rather than one cycle later.
        # 区分の前に採用する。静定したその周期から、その高度を基準に報告する
        # ため（1 周期遅れないようにする）。
        self._adopt_target_if_unset(sample, altitude)
        now = sample.get("t", 0.0)
        # Every band is debounced through `_held`, so a quantity sitting on
        # a band edge cannot make the situation look new each cycle. The
        # phase is not: it is the firmware's own discrete state, which does
        # not oscillate and whose changes are exactly what a judge should
        # see at once.
        # どの区分も `_held` で保持する。境目上の量が、毎周期「新しい状況」に
        # 見えないようにするためである。phase だけは通さない。ファーム自身の
        # 離散状態であって振動せず、その変化こそ即座に判断に届くべきもので
        # あるため。
        assessment.altitude = self._held("altitude", self._classify_altitude(altitude), now)
        assessment.altitude_trend = self._held(
            "altitude_trend", self._classify_altitude_trend(), now)
        assessment.horizontal_drift = self._held(
            "horizontal_drift", self._classify_drift(sample), now)
        assessment.attitude = self._held("attitude", self._classify_attitude(sample), now)
        assessment.position_estimate = self._held(
            "position_estimate", self._classify_estimate(sample), now)
        assessment.ground_distance_sensor = self._held(
            "ground_distance_sensor", self._classify_tof(sample), now)
        # Deliberately NOT passed through `_held`. The hold time exists to stop
        # a band edge chattering, but it does so by reporting the PREVIOUS
        # classification for up to a second -- and a second of "open" while a
        # wall is closing is the one second that matters. The forward reading
        # has its own hysteresis instead (`_classify_forward`), which damps the
        # boundary without ever delaying the approach of a wall.
        # ここでは意図して `_held` を通さない。保持時間は境界のばたつきを抑えるための
        # ものだが、その方法は「直前の区分を最大 1 秒報告し続ける」ことである ―― そして
        # 壁が迫る間の「開けている」の 1 秒こそ、唯一問題になる 1 秒である。前方の読み値は
        # 代わりに自前のヒステリシスを持ち（`_classify_forward`）、壁の接近を遅らせること
        # なく境界を落ち着かせる。
        assessment.forward_clearance = self._classify_forward(sample, now)
        assessment.battery_level = self._held(
            "battery_level", self._classify_battery(sample), now)
        assessment.battery_trend = self._held(
            "battery_trend", self._classify_battery_trend(), now)

        action, reason = self._immediate_safety(sample, assessment)
        assessment.safety_action = action
        assessment.safety_reason = reason
        return assessment

    def _held(self, field: str, candidate: str, now: float) -> str:
        """The reported classification for `field`, debounced.

        A new value replaces the reported one only after it has been the
        candidate continuously for `classification_hold_s`. A value that
        flickers back before then is never reported at all, so a quantity
        resting on a band edge produces one classification rather than a
        new situation every sample (MonitorConfig.classification_hold_s).

        Two changes are reported at once, without waiting:

          - the FIRST value, so a flight does not begin by reporting
            nothing for a second;
          - any change out of "不明", which is not a band edge but the
            arrival of a measurement that was missing.

        `field` について報告する区分。ばたつきを抑えたもの。

        新しい値が報告中の値を置き換えるのは、`classification_hold_s` のあいだ
        続けて候補であり続けた後だけである。それより早く戻った値は報告されない。
        そのため、境目の上にある量が生む区分は 1 つで済み、サンプルごとに新しい
        状況にはならない（MonitorConfig.classification_hold_s）。

        次の 2 つだけは待たずに反映する:

          - **最初の**値。飛行の冒頭で 1 秒間なにも報告しないことを避けるため。
          - 「不明」からの変化。これは境目のばたつきではなく、欠けていた計測が
            届いたということだからである。
        """
        reported, candidate_since, pending = self._reported.get(field, (None, None, None))
        is_first = reported is None
        if is_first:
            self._reported[field] = (candidate, now, candidate)
            return candidate

        is_unchanged = candidate == reported
        if is_unchanged:
            # Reset the candidate: whatever was building up has gone away.
            # 候補を戻す。積み上がっていたものは消えたということ。
            self._reported[field] = (reported, now, reported)
            return reported

        was_unknown = reported == "不明"
        if was_unknown:
            self._reported[field] = (candidate, now, candidate)
            return candidate

        is_same_candidate = candidate == pending
        if not is_same_candidate:
            self._reported[field] = (reported, now, candidate)
            return reported

        has_held = (now - candidate_since) >= self.cfg.classification_hold_s
        if not has_held:
            return reported
        self._reported[field] = (candidate, now, candidate)
        return candidate

    def _classify_altitude(self, altitude) -> str:
        """Where this altitude sits relative to the one being held.

        Reports "unknown" rather than guessing while no target is known: an
        aircraft whose hold altitude nobody has established yet is not
        below anything, and saying it is would be this layer inventing a
        problem for Jev to react to.

        この高度が、保持している高度に対してどこにあるか。

        目標が未確定のあいだは推測せず「不明」を返す。保持高度がまだ定まって
        いない機体は何かより低いわけではなく、低いと言えば、この層が Jev に
        反応させるための問題を作り出すことになる。
        """
        if altitude is None:
            return "不明"
        if self.target_altitude_m is None:
            return "不明"
        error = altitude - self.target_altitude_m
        is_on_target = abs(error) <= self.cfg.altitude_on_target_m
        if is_on_target:
            return "目標どおり"
        return "目標より高い" if error > 0 else "目標より低い"

    def _adopt_target_if_unset(self, sample: dict, altitude) -> None:
        """Take the altitude currently being held as the target, once.

        Three conditions, and all three are needed:

          - no target has been set yet;
          - the craft is FLYING. On the ground the altitude is a perfectly
            steady zero, and adopting it would fix the target at 0 m and
            then call the whole flight "above target";
          - the climb has levelled off (the trend reads "steady", which
            already requires `trend_hold_s` of history), so what is adopted
            is a hover the guidance is holding rather than a height the
            craft passed through on the way up.

        保持中の高度を目標として一度だけ採用する。

        条件は 3 つで、3 つとも必要である:

          - まだ目標が定まっていないこと。
          - 機体が FLYING であること。地上では高度が完璧に安定した 0 であり、
            それを採用すれば目標が 0m に固定され、以後の飛行すべてが
            「目標より高い」になってしまう。
          - 上昇が水平になっていること（傾向が「安定」であること。これには既に
            `trend_hold_s` ぶんの履歴が要る）。採用するのは、誘導が保持して
            いるホバリングであって、上昇の途中で通過した高度ではない。
        """
        if self.target_altitude_m is not None or altitude is None:
            return
        is_flying = str(sample.get("flight_state", "")) == FLIGHT_STATE_FLYING
        if not is_flying:
            return
        is_levelled_off = self._classify_altitude_trend() == "安定"
        if not is_levelled_off:
            return
        self.target_altitude_m = altitude

    def _classify_altitude_trend(self) -> str:
        rate = _slope(self._history, self.cfg.trend_hold_s)
        if rate is None:
            return "不明"
        is_steady = abs(rate) < self.cfg.trend_rate_mps
        if is_steady:
            return "安定"
        return "上昇中" if rate > 0 else "下降中"

    def _classify_drift(self, sample: dict) -> str:
        north, east = sample.get("vel_n"), sample.get("vel_e")
        if north is None or east is None:
            return "不明"
        speed = (north * north + east * east) ** 0.5
        is_still = speed < self.cfg.drift_slow_mps
        if is_still:
            return "ほぼ静止"
        direction = _compass(north, east)
        is_fast = speed >= self.cfg.drift_fast_mps
        return f"{direction}へ速く流されている" if is_fast else f"{direction}へゆっくり流されている"

    def _classify_attitude(self, sample: dict) -> str:
        roll, pitch = sample.get("roll"), sample.get("pitch")
        if roll is None or pitch is None:
            return "不明"
        tilt = max(abs(roll), abs(pitch))
        if tilt <= self.cfg.attitude_level_rad:
            return "水平に近い"
        if tilt <= self.cfg.attitude_steep_rad:
            return "やや傾いている"
        return "大きく傾いている"

    def _classify_estimate(self, sample: dict) -> str:
        components = [sample.get(k) for k in ("vel_n", "vel_e", "vel_d")]
        present = [v for v in components if v is not None]
        if not present:
            return "不明"
        has_nonfinite = any(not _is_finite(v) for v in present)
        if has_nonfinite:
            return "発散している"
        speed = sum(v * v for v in present) ** 0.5
        is_diverged = speed > self.cfg.estimate_diverged_speed_mps
        return "発散している" if is_diverged else "信頼できる"

    def _classify_tof(self, sample: dict) -> str:
        tof = sample.get("tof_m")
        if tof is None:
            return "不明"
        is_plausible = self.cfg.tof_min_m <= tof <= self.cfg.tof_max_m and _is_finite(tof)
        return "正常" if is_plausible else "当てにならない値"

    def _classify_forward(self, sample: dict, now: float) -> str:
        """Forward clearance in words, with hysteresis and a grace period.

        An invalid reading does not mean clear space. Within
        `invalid_grace_s` of the last valid one it keeps reporting what was
        last seen, because a dropped sample in front of a wall is still a
        wall; after that it becomes "cannot measure", which is its own word
        and never an invitation to fly on.

        前方の空きを語で返す。ヒステリシスと猶予つき。

        無効な読み値は「空いている」ではない。最後の有効な読み値から
        `invalid_grace_s` の間は最後に見たものを報告し続ける ―― 壁の前での
        1 サンプルの取りこぼしは、依然として壁だからである。それを過ぎれば
        「測定不能」になる。これは独立した語であって、進んでよいという意味を
        決して持たない。
        """
        forward = sample.get("tof_front_m")
        is_valid = forward is not None and _is_finite(forward)
        if is_valid:
            self._forward_last_valid = (now, forward)
            return self._forward_word(forward)

        has_recent = self._forward_last_valid is not None
        if not has_recent:
            return FORWARD_UNKNOWN
        seen_at, last = self._forward_last_valid
        is_stale = (now - seen_at) > self.forward.invalid_grace_s
        if is_stale:
            # Nothing believable for a while. Drop the latch too: holding a
            # stop on a memory this old would freeze the craft indefinitely.
            # しばらく信じられる値が無い。ラッチも解く: これほど古い記憶で停止を
            # 保持し続けると、機体は永久に固まる。
            self._forward_stopped = False
            return FORWARD_UNKNOWN
        return self._forward_word(last)

    def _forward_word(self, metres: float) -> str:
        """Band a forward distance, latching the stop band with hysteresis.
        前方距離を帯域に落とす。停止帯域はヒステリシス付きでラッチする。"""
        cfg = self.forward
        is_within_stop = metres <= cfg.stop_distance_m
        if is_within_stop:
            self._forward_stopped = True
            return FORWARD_WALL_NEAR
        # Once stopped, stay stopped until the reading clears the RELEASE
        # distance -- otherwise the classification flips back and forth on a
        # reading that sits on the threshold, and the craft brakes and
        # accelerates alternately at a wall.
        # 一度止まったら、読み値が「解除」距離を超えるまでは止まったままにする ――
        # さもないと、しきい値上に乗った読み値で区分が行き来し、機体は壁の前で
        # 制動と加速を交互に繰り返す。
        is_still_latched = self._forward_stopped and metres < cfg.release_distance_m
        if is_still_latched:
            return FORWARD_WALL_NEAR
        self._forward_stopped = False
        if metres <= cfg.wall_near_m:
            return FORWARD_WALL_NEAR
        if metres <= cfg.somewhat_near_m:
            return FORWARD_SOMEWHAT_NEAR
        return FORWARD_OPEN

    def _classify_battery(self, sample: dict) -> str:
        battery = sample.get("battery_pct")
        if battery is None:
            return "不明"
        if battery <= self.cfg.battery_danger_pct:
            return "危険"
        if battery <= self.cfg.battery_low_pct:
            return "残り少ない"
        return "十分"

    def _classify_battery_trend(self) -> str:
        """How the pack is behaving over the trend window, in words.

        Judged on VOLTAGE and only once the window is full. A window that
        is still filling is reported as "steady" rather than extrapolated:
        the first seconds of any flight contain the takeoff load step,
        which is the one thing this classification must not mistake for a
        discharge (see MonitorConfig.battery_drop_fast_v).

        傾向の窓を通して見たパックの様子を言葉にする。

        判定は**電圧**で行い、窓が満ちてからのみ行う。満ちていない窓は外挿せず
        「安定」と報告する。どの飛行でも最初の数秒には離陸の負荷段差が含まれ、
        それこそが、この区分が放電と取り違えてはならない唯一のものだからである
        （MonitorConfig.battery_drop_fast_v 参照）。
        """
        if len(self._battery_history) < 2:
            return "不明"
        oldest, newest = self._trend_window()
        if oldest is None:
            return BATTERY_TREND_STEADY

        drop_v = oldest[1] - newest[1]
        is_fast_drop = drop_v >= self.cfg.battery_drop_fast_v
        if is_fast_drop:
            return BATTERY_TREND_SHARP
        is_gradual_drop = drop_v >= self.cfg.battery_drop_gradual_v
        return BATTERY_TREND_GRADUAL if is_gradual_drop else BATTERY_TREND_STEADY

    def _trend_window(self):
        """The oldest and newest samples a full trend window apart.

        Returns `(None, None)` until the history covers the window. The
        oldest end is the OLDEST sample that is still no further back than
        the window, chosen by walking rather than by taking `history[0]`:
        the history is kept slightly longer than the window (`_absorb_battery`),
        so its first entry is usually just outside it.

        Choosing the endpoint this way is also what stops the measurement
        jittering. Taking `history[0]` made the measured span depend on
        exactly which samples had been trimmed that cycle, which put it on
        the threshold of "is the window full?" and flipped the answer twice
        a second.

        傾向の窓 1 つぶん離れた、最古と最新のサンプル。

        履歴が窓を覆うまでは `(None, None)` を返す。最古側は「窓より古くない
        範囲で最も古い」サンプルで、`history[0]` を取るのではなく走査して選ぶ。
        履歴は窓より少しだけ長く保持しているため（`_absorb_battery`）、先頭の
        項目はたいてい窓の外にあるからである。

        この選び方は、測定のばたつきを止めるものでもある。`history[0]` を
        取ると、測る長さがその周期にどのサンプルが刈り込まれたかで変わり、
        「窓が満ちたか」の判定境界に乗って、毎秒 2 回答えが反転していた。
        """
        newest = self._battery_history[-1]
        cutoff = newest[0] - self.cfg.battery_trend_window_s
        oldest = None
        for entry in self._battery_history:
            if entry[0] <= cutoff:
                oldest = entry
                continue
            break
        if oldest is None:
            return None, None
        return oldest, newest

    # -- immediate safety rules / 即時安全則 ----------------------------

    def _immediate_safety(self, sample: dict, assessment: Assessment):
        """Decide whether this situation may not wait for the Judge.
        この状況が Judge を待てないかどうかを決める。

        Ordered by severity: a dangerous battery outranks an altitude
        excursion, because landing also resolves the altitude problem.
        重大さの順に並べる: 電池の危険は高度の逸脱に優先する。着陸すれば
        高度の問題も同時に解消するため。
        """
        is_battery_dangerous = assessment.battery_level == "危険"
        if is_battery_dangerous:
            return SAFETY_LAND, "電池が危険域のため即時着陸"

        is_estimate_diverged = assessment.position_estimate == "発散している"
        if is_estimate_diverged:
            return SAFETY_LAND, "位置推定が発散したため即時着陸"

        # An obstacle close ahead stops the craft WITHOUT asking Jev. A round
        # trip is 230ms at the median and has no guaranteed upper bound
        # (§4 measurements), and at the envelope's 0.5 m/s that is a tenth of
        # a metre of travel before an answer could even arrive. Stopping is
        # also the cheap direction to be wrong in: a needless stop costs a
        # pause, and the craft holds position and can be asked again next
        # cycle, while a needless approach costs the airframe.
        #
        # This rule STOPS and does not land. Landing is for situations that
        # get worse by staying up (a dying battery, a diverged estimate); a
        # wall ahead is not one of them, and descending next to an unknown
        # obstacle is not obviously safer than holding still away from it.
        #
        # 前方の障害物が近ければ、Jev を待たずに停止する。往復は中央値で 230ms、
        # 上限の保証は無く（§4 の実測）、飛行領域の 0.5m/s では答えが届くより前に 0.1m
        # 進む。誤る向きとしても停止のほうが安い: 不要な停止の代償は一拍の間であり、
        # 機体は位置を保って次の周期にまた問える。一方、不要な接近の代償は機体である。
        #
        # この規則は「停止」であって着陸ではない。着陸は、飛び続けることで悪化する
        # 状況（電池の枯渇、推定の発散）のためのものである。前方の壁はそれに当たらず、
        # 未知の障害物の脇で降下することが、離れて静止することより安全とは言えない。
        is_obstacle_close = assessment.forward_clearance == FORWARD_WALL_NEAR
        if is_obstacle_close:
            return SAFETY_STOP, "前方に障害物が近いため即時停止"

        altitude = sample.get("altitude_m")
        if altitude is not None:
            is_far_below = altitude < self.envelope.altitude_min_m - self.cfg.altitude_deviation_m
            is_far_above = altitude > self.envelope.altitude_max_m + self.cfg.altitude_deviation_m
            if is_far_below or is_far_above:
                return SAFETY_STOP, "高度が飛行領域を大きく外れたため即時停止"

        return SAFETY_NONE, ""


# =============================================================================
# Helpers / 補助関数
# =============================================================================
def _window(history: list, now: float, span_s: float) -> list:
    """Drop entries older than `span_s`. / `span_s` より古い項目を捨てる。"""
    cutoff = now - span_s
    return [entry for entry in history if entry[0] >= cutoff]


def _slope(history: list, hold_s: float):
    """Rate of change [unit/s] over the history, or None if the history is
    too short to have held for `hold_s`.

    Why require the duration: a trend reported from two adjacent 50Hz
    samples is noise amplified by 50, and would make the vehicle react to
    sensor jitter as if it were sinking.
    履歴上の変化率 [単位/s]。`hold_s` の間続いたと言えるだけの履歴が無ければ None。

    継続時間を要求する理由: 50Hz の隣り合う 2 サンプルから出した傾向は、
    ノイズを 50 倍に拡大したものでしかない。センサの揺らぎを下降と取り違える。
    """
    if len(history) < 2:
        return None
    span = history[-1][0] - history[0][0]
    if span < hold_s:
        return None
    return (history[-1][1] - history[0][1]) / span


def _battery_volts(sample: dict):
    """The pack voltage for this sample, or None if it cannot be had.

    Prefers the measured voltage and falls back to inverting the
    percentage, because not every source carries volts: the 140-byte v2
    telemetry packet and the SILS STATE line do, but the 10Hz Tello state
    string reports only a percentage (`_sample_from_state_dict`). Since
    that percentage is itself a linear map of the voltage
    (link.battery_percent), inverting it recovers the figure exactly, and
    the trend keeps working on a source that would otherwise go silently
    blind.

    このサンプルのパック電圧。得られなければ None。

    実測した電圧を優先し、無ければ百分率から逆算する。すべての入力源が電圧を
    持つわけではないためである。140 バイトの v2 テレメトリと SILS の STATE 行は
    持つが、10Hz の Tello 状態文字列は百分率しか報告しない
    （`_sample_from_state_dict`）。その百分率自体が電圧の線形写像なので
    （link.battery_percent）、逆算すれば値は厳密に復元でき、そうしなければ
    静かに傾向が効かなくなる入力源でも動き続ける。
    """
    voltage = sample.get("battery_v")
    if voltage is not None:
        return voltage
    percent = sample.get("battery_pct")
    if percent is None:
        return None
    from .link import BATTERY_EMPTY_V, BATTERY_FULL_V

    return BATTERY_EMPTY_V + (percent / 100.0) * (BATTERY_FULL_V - BATTERY_EMPTY_V)


def _compass(north: float, east: float) -> str:
    """Dominant horizontal direction, in words. / 水平方向の主成分を言葉にする。"""
    is_north_south = abs(north) >= abs(east)
    if is_north_south:
        return "前" if north > 0 else "後ろ"
    return "右" if east > 0 else "左"


def _is_finite(value: float) -> bool:
    return value == value and abs(value) != float("inf")
