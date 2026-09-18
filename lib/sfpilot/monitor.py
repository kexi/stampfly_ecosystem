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
電池の危険域、包絡を大きく外れた高度、発散した位置推定。この規則だけが
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
    battery_level: str = "不明"
    battery_trend: str = "不明"

    # Immediate-safety outcome and its reason, both set by the same rule.
    # 即時安全則の結果と理由。同じ規則が両方を設定する。
    safety_action: str = SAFETY_NONE
    safety_reason: str = ""

    # Kept for the Arbiter's envelope check and for the trace. Never sent
    # to Jev -- these are the numbers the words above were derived from.
    # Arbiter の包絡照合と記録のために保持する。Jev には渡さない — 上の
    # 言葉の元になった数値そのものだからである。
    numeric: dict = field(default_factory=dict)
    recent_events: list = field(default_factory=list)


class Monitor:
    """Classify a 50Hz sample stream into words and immediate actions.
    50Hz のサンプル列を言葉の区分と即時動作に分類する。"""

    def __init__(self, config=DEFAULT_CONFIG, target_altitude_m: float = 0.8):
        self.cfg: MonitorConfig = config.monitor
        self.envelope: EnvelopeConfig = config.envelope
        self.target_altitude_m = target_altitude_m
        self._history: list = []          # (t, altitude_m) for trends / 傾向判定用
        self._battery_history: list = []  # (t, battery_pct) / 電池の傾向判定用
        self._events: list = []
        self._last_sample: Optional[dict] = None

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
        if "battery_pct" in sample:
            self._battery_history.append((t, sample["battery_pct"]))
        # Keep only what the longest window needs, so memory is bounded
        # during a long flight.
        # 最も長い窓に必要な分だけ残す。長時間飛行でも使用量が増え続けない。
        self._history = _window(self._history, t, self.cfg.trend_hold_s * 2.0)
        self._battery_history = _window(
            self._battery_history, t, self.cfg.battery_trend_window_s
        )

    # -- classification / 区分 -----------------------------------------

    def _assess(self, sample: dict) -> Assessment:
        """Build the full assessment from the newest sample plus history.
        最新サンプルと履歴から評価一式を組み立てる。"""
        assessment = Assessment()
        assessment.numeric = dict(sample)
        assessment.recent_events = list(self._events)
        assessment.phase = str(sample.get("flight_state", "飛行中"))

        altitude = sample.get("altitude_m")
        assessment.altitude = self._classify_altitude(altitude)
        assessment.altitude_trend = self._classify_altitude_trend()
        assessment.horizontal_drift = self._classify_drift(sample)
        assessment.attitude = self._classify_attitude(sample)
        assessment.position_estimate = self._classify_estimate(sample)
        assessment.ground_distance_sensor = self._classify_tof(sample)
        assessment.battery_level = self._classify_battery(sample)
        assessment.battery_trend = self._classify_battery_trend()

        action, reason = self._immediate_safety(sample, assessment)
        assessment.safety_action = action
        assessment.safety_reason = reason
        return assessment

    def _classify_altitude(self, altitude) -> str:
        if altitude is None:
            return "不明"
        error = altitude - self.target_altitude_m
        is_on_target = abs(error) <= self.cfg.altitude_on_target_m
        if is_on_target:
            return "目標どおり"
        return "目標より高い" if error > 0 else "目標より低い"

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
        if len(self._battery_history) < 2:
            return "不明"
        drop = self._battery_history[0][1] - self._battery_history[-1][1]
        is_fast_drop = drop >= self.cfg.battery_drop_fast_pct
        if is_fast_drop:
            return f"この {int(self.cfg.battery_trend_window_s)} 秒で急に低下"
        return "ゆるやかに低下" if drop > 0 else "安定"

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

        altitude = sample.get("altitude_m")
        if altitude is not None:
            is_far_below = altitude < self.envelope.altitude_min_m - self.cfg.altitude_deviation_m
            is_far_above = altitude > self.envelope.altitude_max_m + self.cfg.altitude_deviation_m
            if is_far_below or is_far_above:
                return SAFETY_STOP, "高度が包絡を大きく外れたため即時停止"

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


def _compass(north: float, east: float) -> str:
    """Dominant horizontal direction, in words. / 水平方向の主成分を言葉にする。"""
    is_north_south = abs(north) >= abs(east)
    if is_north_south:
        return "前" if north > 0 else "後ろ"
    return "右" if east > 0 else "左"


def _is_finite(value: float) -> bool:
    return value == value and abs(value) != float("inf")
