"""
sfpilot.explore - turn on the spot, look forward at each bearing, choose a way.
sfpilot.explore - その場で回り、各方位の前方を見て、進む方向を選ぶ。

The vehicle has ONE distance sensor that looks forward. To learn what is
around it, it must point the sensor around it -- so a sweep is a yaw
rotation with a measurement at each step, and the result is eight bearings
each classified into the same words the forward clearance already uses
(`monitor.FORWARD_*`). Jev is then asked which bearing to take, and the
code refuses any answer that names a bearing with a wall close to it.

機体が持つ前方を見る距離センサは **1 つ**である。周囲を知るには、そのセンサを
周囲へ向けるほかない —— そこで掃引とは、刻みごとに測距を伴うヨー回転であり、
結果は 8 方位それぞれを、前方の空きが既に使っているのと同じ語
（`monitor.FORWARD_*`）に区分したものになる。そのうえで「どの方位へ進むか」を
Jev に問い、壁が近い方位を名指しした答えはコードが却下する。

**This module is complete and disabled.** `ExploreConfig.enabled` is False
and must stay False until the release condition in `config.py` is met: in
SILS today the aircraft falls out of the air when it yaws at all, from any
of `cw 90`, `cw 45`, `cw 30` or an `rc` yaw rate as low as 0.1 rad/s
(docs/plans/jev-autopilot.md §4.9.1, §4.11). Everything here is therefore
verified against fixtures and a recording link, and NOTHING here has been
verified in flight. `sweep()` refuses to run while the switch is off, and
records the refusal rather than failing silently.

**本モジュールは完成しており、かつ無効である。** `ExploreConfig.enabled` は
False であり、`config.py` の解除条件が満たされるまで False のままにする。現状の
SILS では、`cw 90`・`cw 45`・`cw 30` のいずれでも、また 0.1 rad/s という低い
`rc` のヨー速度でも、機体はヨー回転しただけで落下する（jev-autopilot.md
§4.9.1・§4.11）。したがってここにあるものはすべて fixture と記録用リンクに対して
検証されており、**飛行による検証は 1 つも無い**。`sweep()` は切り替えが無効な間
実行を拒否し、黙って失敗するのではなく拒否を記録する。

Why not simply raise when disabled: the caller is a flight loop, and a
sweep that cannot be flown is a fact about this build, not an error in the
flight. It is reported the way a refused Jev answer is -- as an outcome
carrying its reason -- so the trace shows what was asked for and why it
did not happen.

無効時に例外にしない理由: 呼び出し側は飛行ループであり、「掃引が飛ばせない」
ことは、この版についての事実であって飛行の誤りではない。却下された Jev の答えと
同じように、理由を携えた**結果**として報告する。そうすれば記録に、何が求められ、
なぜ行われなかったかが残る。
"""

import math
import time
from dataclasses import dataclass, field

from .config import DEFAULT_CONFIG
from .monitor import (
    FORWARD_OPEN, FORWARD_SOMEWHAT_NEAR, FORWARD_UNKNOWN, FORWARD_WALL_NEAR,
    SAFETY_NONE,
)

# How a sweep ended. A sweep that was cut short still carries every bearing
# it did read: a partial look at the room is worth more than no look, and
# the bearings it never reached are "cannot be measured" like any other
# bearing nobody has seen.
# 掃引がどう終わったか。打ち切られた掃引も、読み取れた方位はすべて保持する。
# 部屋を部分的に見たことには、まったく見ないことより価値があり、到達しなかった
# 方位は、誰も見ていない他の方位と同じく「測定不能」である。
SWEEP_COMPLETED = "completed"
SWEEP_REFUSED = "refused"           # the feature is switched off / 機能が無効
SWEEP_INTERRUPTED = "interrupted"   # the safety layer stopped it / 安全層が止めた

# How long ago a sweep was taken, in words. The model is never given a
# clock (the design's constraint table), so the age of a measurement
# reaches it as one of these.
# 掃引をいつ取ったかを表す語。モデルに時計は渡さない（設計の制約表）ので、
# 測定の古さはこれらの語のどれかとして届く。
MEASURED_JUST_NOW = "just now"
MEASURED_A_WHILE_AGO = "a while ago"
MEASURED_STALE = "too long ago to rely on"
MEASURED_NEVER = "never"

# The bearing names, as the operator and Jev both see them. Relative to the
# heading the sweep STARTED from, not to north: the craft does not know
# which way north is any better than it knows where it is, and "ahead" is
# what a choice of direction actually means to whoever is flying it.
# 方位の名前。操作者と Jev の双方がこれを見る。北ではなく、掃引を**始めた
# ときの**機首方位に対する相対である。機体は、自分の位置を知っている程度にしか
# 北を知らないし、飛ばしている側にとって「方向を選ぶ」とはまさに「正面」から
# どれだけかということだからである。
BEARING_NAMES = (
    "ahead",
    "ahead and to the right",
    "to the right",
    "behind and to the right",
    "behind",
    "behind and to the left",
    "to the left",
    "ahead and to the left",
)

# The answer meaning "do not go anywhere". Offered alongside the bearings
# so that "none of these is worth flying into" is a thing the model can
# say, rather than being forced to name the least bad wall.
# 「どこへも行かない」を意味する答え。方位と並べて提示することで、「どれも進む
# 価値が無い」をモデルが**言える**ようにする。最もましな壁を名指しさせるしかない
# 状況を作らないためである。
BEARING_NONE = "stay put"


@dataclass
class BearingReading:
    """One bearing of a sweep: where it points and what was seen there.
    掃引の 1 方位。どちらを向いているかと、そこで見えたもの。"""

    name: str
    # Degrees clockwise from the heading the sweep started at.
    # 掃引開始時の機首方位からの時計回りの角度 [度]。
    relative_deg: float
    # One of monitor.FORWARD_*. Japanese, as every classification is;
    # `summarizer` translates it on the way to Jev.
    # monitor.FORWARD_* のいずれか。他の区分と同様に日本語であり、Jev へ渡る
    # 途中で `summarizer` が訳す。
    clearance: str = FORWARD_UNKNOWN
    # How many readings were taken here and how many were usable. Kept for
    # the trace and for `--web`; never sent to Jev, which sees the word.
    # ここで取った読み取りの数と、そのうち使えた数。記録と `--web` のために持つ。
    # Jev へは渡さない（Jev が見るのは語である）。
    samples: int = 0
    valid_samples: int = 0
    # The nearest valid distance seen here [m], or None if none was. Drawn
    # as the sector's radius by `--web`; never sent to Jev.
    # ここで見えた最も近い有効な距離 [m]。無ければ None。`--web` が扇形の半径と
    # して描く。Jev へは渡さない。
    nearest_m: float = None

    @property
    def is_open(self) -> bool:
        """Whether this bearing is one the craft may be sent along.

        `FORWARD_UNKNOWN` is not open. The forward part returns an invalid
        reading both for empty space and for a surface too close to
        resolve (§4.8.6), so flying at an unmeasured bearing is flying at
        the case that cannot be seen.

        この方位が、機体を向かわせてよい方位か。

        `FORWARD_UNKNOWN` は「開けている」ではない。前方の部品は、空間に対しても、
        近すぎて復元できない面に対しても無効を返す（§4.8.6）。測れていない方位へ
        飛ぶことは、見えないほうの場合へ飛ぶことである。
        """
        return self.clearance == FORWARD_OPEN


@dataclass
class Sweep:
    """Every bearing of one look around, and when it was taken.
    一度の見回しの全方位と、それを取った時刻。"""

    readings: list = field(default_factory=list)
    outcome: str = SWEEP_COMPLETED
    # Why it was refused or interrupted; empty when it completed.
    # 拒否・中断の理由。完了していれば空。
    detail: str = ""
    # `time.monotonic()` when the sweep finished. Compared only with
    # another monotonic reading, never shown or sent.
    # 掃引が終わった時点の `time.monotonic()`。他の単調時計の値とだけ比較し、
    # 表示も送信もしない。
    taken_at: float = 0.0

    @property
    def ok(self) -> bool:
        return self.outcome == SWEEP_COMPLETED

    def open_bearings(self) -> list:
        """The bearings worth flying towards, nearest-first by name order.
        向かう価値のある方位。名前の順（正面から時計回り）で返す。"""
        return [reading for reading in self.readings if reading.is_open]

    def find(self, name: str):
        """The reading for a bearing name, or None if the sweep lacks it.
        方位名に対応する読み取り。掃引に無ければ None。"""
        for reading in self.readings:
            if reading.name == name:
                return reading
        return None

    def age_word(self, now: float, config=DEFAULT_CONFIG) -> str:
        """How old this sweep is, as one of the `MEASURED_*` words.

        Banded rather than reported as seconds for the design's reason:
        the model is not a calculator and must not be handed a clock. The
        bands are the sweep's own freshness window and twice it -- fresh,
        ageing, and past the point where it describes a room the craft has
        since moved through.

        この掃引の古さを `MEASURED_*` のいずれかの語で返す。

        秒ではなく区分にするのは設計の理由による。モデルは計算機ではなく、時計を
        渡してはならない。区分は掃引自身の有効期限とその 2 倍である —— 新しい、
        古びつつある、そして「機体がその後を通り抜けてしまった部屋」を述べている
        段階、の 3 つ。
        """
        if not self.readings:
            return MEASURED_NEVER
        age_s = now - self.taken_at
        window_s = config.explore.freshness_s
        if age_s <= window_s:
            return MEASURED_JUST_NOW
        if age_s <= window_s * 2.0:
            return MEASURED_A_WHILE_AGO
        return MEASURED_STALE


def blank_sweep(outcome: str = SWEEP_REFUSED, detail: str = "") -> Sweep:
    """A sweep that never happened, carrying the reason it did not.
    行われなかった掃引。行われなかった理由を携える。"""
    return Sweep(readings=[], outcome=outcome, detail=detail)


# =============================================================================
# Taking a sweep / 掃引を取る
# =============================================================================

def sweep(link, pilot, config=DEFAULT_CONFIG, on_event=None) -> Sweep:
    """Turn all the way round, reading the forward distance at each step.

    Refused outright while `ExploreConfig.enabled` is False, which is the
    default and, today, the only correct setting -- see this module's
    docstring and `ExploreConfig`. The refusal carries the reason so that
    it reaches the trace and the operator rather than looking like a sweep
    that found nothing.

    The safety layer keeps running throughout: `pilot.step()` is called on
    every cycle of every wait, so the immediate rules (battery, altitude,
    forward distance) act during the turn exactly as they do in level
    flight. Anything they raise cuts the sweep short, and the bearings
    already read are kept.

    The craft is returned to the heading it started from before this
    returns, so a sweep leaves the aircraft as it found it. That matters
    for more than tidiness: the caller's own idea of where "forward" is
    (`instruction._FlightState`, `MissionWalk`) is not updated by a sweep,
    so a sweep that ended part-way round would silently invalidate it.

    ぐるりと一周し、刻みごとに前方距離を読む。

    `ExploreConfig.enabled` が False の間はそのまま拒否する。False が既定であり、
    今日のところ唯一正しい設定である —— 本モジュールの docstring と
    `ExploreConfig` を参照。拒否は理由を携えるので、「何も見つからなかった掃引」
    に見えることなく記録と操作者に届く。

    安全層は全体を通して動き続ける。待ちのあらゆる周期で `pilot.step()` を呼ぶ
    ので、即時則（電池・高度・前方距離）は、水平飛行のときとまったく同じように
    旋回中も働く。そこで上がったものは掃引を打ち切り、既に読んだ方位は保持する。

    この関数が戻る前に、機体は開始時の機首方位へ戻す。掃引が機体を、見つけたとき
    のままにして去るためである。これは整頓以上の意味を持つ。呼び出し側が持つ
    「前」の概念（`instruction._FlightState`・`MissionWalk`）は掃引で更新されない
    ので、途中で終わった掃引は、それを黙って無効にしてしまう。
    """
    cfg = config.explore
    if not cfg.enabled:
        _emit(on_event, f"explore: 実行しない — {cfg.disabled_reason}")
        return blank_sweep(SWEEP_REFUSED, cfg.disabled_reason)

    return _Sweeper(link, pilot, config, on_event).run()


class _Sweeper:
    """One sweep, held as an object so each bearing sees the same state.
    掃引 1 回分。各方位が同じ状態を見られるよう、オブジェクトとして持つ。"""

    def __init__(self, link, pilot, config, on_event):
        self.link = link
        self.pilot = pilot
        self.cfg = config
        self.explore = config.explore
        self.on_event = on_event
        self.readings: list = []
        # Degrees turned away from the starting heading so far, so the
        # return is whatever is left of a full circle rather than an
        # assumption that the sweep completed.
        # これまでに開始時の機首方位から回った角度 [度]。戻す量を、掃引が完了した
        # という前提ではなく「円の残り」として出すためである。
        self.turned_deg = 0.0

    def run(self) -> Sweep:
        """Read every bearing, then face the way the sweep started.
        全方位を読み、掃引開始時の向きへ戻す。"""
        interrupt = self._read_all_bearings()
        # Returned to the start heading whatever happened, including after
        # an interrupt: the safety layer stopping the sweep is a reason to
        # stop turning, not a reason to leave the craft facing a direction
        # nobody upstream knows about.
        # 何があっても開始時の機首方位へ戻す。中断後も同じである。安全層が掃引を
        # 止めたことは、回るのをやめる理由ではあっても、上位の誰も知らない向きに
        # 機体を残す理由ではない。
        self._face_start_heading()
        if interrupt:
            return Sweep(readings=self.readings, outcome=SWEEP_INTERRUPTED,
                         detail=interrupt, taken_at=time.monotonic())
        return Sweep(readings=self.readings, outcome=SWEEP_COMPLETED,
                     taken_at=time.monotonic())

    def _read_all_bearings(self) -> str:
        """Read each bearing in turn. Returns why it stopped early, or "".
        各方位を順に読む。早く終わった理由を返す。最後まで読めば ""。"""
        for index in range(self.explore.bearings):
            relative_deg = index * self.explore.step_deg
            interrupt = self._settle_at_bearing()
            if interrupt:
                return interrupt
            self.readings.append(self._read_bearing(index, relative_deg))
            is_last = index == self.explore.bearings - 1
            if is_last:
                return ""
            interrupt = self._turn_one_step()
            if interrupt:
                return interrupt
        return ""

    def _read_bearing(self, index: int, relative_deg: float) -> BearingReading:
        """Take this bearing's readings and classify them into one word.
        この方位の読み取りを取り、1 つの語に区分する。"""
        distances = self._collect_distances()
        reading = BearingReading(
            name=BEARING_NAMES[index % len(BEARING_NAMES)],
            relative_deg=relative_deg,
            samples=self.explore.samples_per_bearing,
            valid_samples=len(distances),
        )
        reading.clearance = classify_bearing(distances, self.cfg)
        if distances:
            reading.nearest_m = min(distances)
        self._emit(f"explore: {reading.name} — {reading.clearance}")
        return reading

    def _collect_distances(self) -> list:
        """Every valid forward distance seen at this bearing [m].

        The loop keeps the safety layer turning between readings, so a wall
        that comes into range during the pause is acted on by the immediate
        rules rather than waiting for the sweep to finish.

        この方位で見えた有効な前方距離 [m] をすべて返す。

        読み取りの合間も安全層を回し続けるので、待ちの間に射程へ入ってきた壁は、
        掃引の終了を待たずに即時則が処理する。
        """
        distances = []
        for _ in range(self.explore.samples_per_bearing):
            self._pump(self.explore.sample_interval_s)
            distance = self._forward_distance()
            if distance is not None:
                distances.append(distance)
        return distances

    def _forward_distance(self):
        """The newest forward distance [m], or None when it is not valid.

        Read from the Monitor's own latest sample rather than from the
        link, because the monitor loop owns the sample stream: reading the
        link here would take samples away from the classification (the
        same rule `say.StepRunner` follows for speed).

        最新の前方距離 [m]。有効でなければ None。

        リンクではなく Monitor 自身の最新サンプルから読む。サンプル列は監視ループ
        のものであり、ここでリンクを読めば区分からサンプルを奪うことになる
        （速度について `say.StepRunner` が従うのと同じ規則）。
        """
        sample = self.pilot.monitor.latest_sample or {}
        distance = sample.get("tof_front_m")
        if distance is None:
            return None
        is_finite = distance == distance and abs(distance) != float("inf")
        return distance if is_finite else None

    def _settle_at_bearing(self) -> str:
        """Let the airframe stop swinging before the readings are believed.
        読み値を信じる前に、機体の振れが収まるのを待つ。"""
        return self._pump(self.explore.settle_s)

    def _turn_one_step(self) -> str:
        """Turn by one step, whichever way the config says to turn.
        設定が指定する方法で、1 刻みぶん旋回する。"""
        step_deg = self.explore.step_deg
        if self.explore.turn_with_rc:
            interrupt = self._turn_by_rc(step_deg)
        else:
            interrupt = self._turn_by_command(step_deg)
        self.turned_deg += step_deg
        return interrupt

    def _turn_by_command(self, degrees: float) -> str:
        """Turn with the vehicle's own `cw`, which answers when it arrives.
        機体自身の `cw` で旋回する。到達時に応答が返る。"""
        self.link.send_command(f"cw {round(degrees)}")
        return self._await_reply()

    def _turn_by_rc(self, degrees: float) -> str:
        """Turn by holding a yaw rate for as long as the angle needs.

        Dead reckoning, and named as such: the duration is the angle over
        the rate, and nothing confirms the craft actually turned that far.
        This is why `cw` is the default (`ExploreConfig.turn_with_rc`).

        角度に必要な時間だけヨー速度を保って旋回する。

        推測航法であり、そう名指ししておく。時間は「角度÷速度」であって、機体が
        実際にそこまで回ったことを確かめるものは何も無い。`cw` を既定にしている
        のはこのためである（`ExploreConfig.turn_with_rc`）。
        """
        rate = self.explore.rc_yaw_rate_rad_s
        duration_s = math.radians(degrees) / rate
        stick = round(100.0 * rate / self.explore.rc_yaw_rate_max_rad_s)
        self.link.send_rc(0, 0, 0, stick)
        interrupt = self._pump(duration_s)
        self.link.send_rc(0, 0, 0, 0)
        return interrupt

    def _face_start_heading(self) -> None:
        """Turn back to where the sweep began, by the shortest way round.

        Sent even when the sweep completed a whole circle: a completed
        sweep has turned 360 degrees, and the vehicle's `cw` moves to a yaw
        TARGET, so the remainder is zero and nothing is sent. What this
        actually covers is the interrupted sweep, which stopped facing
        somewhere nobody upstream knows about.

        掃引を始めた向きへ、近いほうの回り方で戻す。

        掃引が一周し切った場合も呼ぶ。一周した掃引は 360 度回っており、機体の
        `cw` はヨー**目標**への移動なので、残りは 0 で何も送られない。実際にこれが
        効くのは中断された掃引であり、そちらは上位の誰も知らない向きを向いたまま
        止まっている。
        """
        remaining = self.turned_deg % 360.0
        is_already_facing_start = remaining <= 0.0
        if is_already_facing_start:
            return
        # Back the short way: turning 45 deg back beats 315 deg on round
        # the other side, and the two end facing identically.
        # 近いほうへ戻す。45 度戻るほうが、反対回りに 315 度回るより良く、
        # 向きの結果は同一である。
        is_shorter_anticlockwise = remaining <= 180.0
        if is_shorter_anticlockwise:
            self.link.send_command(f"ccw {round(remaining)}")
        else:
            self.link.send_command(f"cw {round(360.0 - remaining)}")
        self._await_reply()

    def _await_reply(self) -> str:
        """Wait for the vehicle to answer the turn, watching for a stop.

        Bounded by the same ceiling a step's reply is (`move_reply_wait_s`
        plus the settle), for the same reason: an answer that is never
        coming must not leave the sweep turning forever.

        旋回に対する機体の応答を待つ。その間も停止の理由を見張る。

        1 手順の応答と同じ上限で打ち切る（`move_reply_wait_s` に静定時間を足した
        もの）。理由も同じで、来ない応答が掃引を永久に回し続けてはならない。
        """
        ceiling_s = self.cfg.landing.move_reply_wait_s + self.explore.settle_s
        deadline = time.monotonic() + ceiling_s
        while time.monotonic() < deadline:
            interrupt = self._pump(1.0 / self.cfg.monitor_hz)
            if interrupt:
                return interrupt
            has_answered = getattr(self.link, "replies_outstanding", 0) <= 0
            if has_answered:
                return ""
        return ""

    def _pump(self, seconds: float) -> str:
        """Run the safety layer for `seconds`. Returns why to stop, or "".

        This is the whole reason a sweep is not just a sequence of
        commands: the aircraft is rotating next to something it cannot see
        all of, and the layer that stops it must keep its 50Hz.

        `seconds` のあいだ安全層を回す。止めるべき理由を返す。無ければ ""。

        掃引が単なる指令の列でない理由がこれである。機体は、全体を見ることが
        できない何かの脇で回っており、それを止める層は 50Hz を保ち続ける必要が
        ある。
        """
        period = 1.0 / self.cfg.monitor_hz
        deadline = time.monotonic() + seconds
        while True:
            self.pilot.step()
            self.link.hold_sticks_neutral()
            interrupt = interrupt_reason(self.pilot)
            if interrupt:
                return interrupt
            if time.monotonic() >= deadline:
                return ""
            time.sleep(period)

    def _emit(self, message: str) -> None:
        _emit(self.on_event, message)


def _emit(on_event, message: str) -> None:
    if on_event is not None:
        on_event(message)


def interrupt_reason(pilot) -> str:
    """Why a sweep must stop now, or "" to carry on.

    The immediate safety rules only: a sweep is a manoeuvre the code
    started, so it is not stopped by Jev choosing to wait the way a
    sequence of instruction steps is (`say._interrupt_reason`). Waiting is
    what the craft is doing -- it is turning on the spot, not travelling --
    and a hold that will not end still becomes a landing through the
    Arbiter's own timer, which does stop the sweep.

    掃引を今すぐ止めるべき理由。続けてよければ ""。

    即時安全則だけを見る。掃引はコードが始めた操作なので、指示の手順の列
    （`say._interrupt_reason`）のように「Jev が待機を選んだこと」では止めない。
    機体がしているのはまさに待つことだからである —— その場で回っているだけで、
    移動していない。終わらない待機は、Arbiter 自身の計時で着陸に変わり、そちらは
    掃引を止める。
    """
    from .arbiter import VERDICT_BACK, VERDICT_LAND, VERDICT_STOP

    if not pilot.decisions:
        return ""
    verdict = pilot.decisions[-1]["verdict"]
    if verdict.action not in (VERDICT_LAND, VERDICT_STOP, VERDICT_BACK):
        return ""
    return f"{verdict.action}: {verdict.reason}"


# =============================================================================
# Classifying one bearing / 1 方位の区分
# =============================================================================

def classify_bearing(distances: list, config=DEFAULT_CONFIG) -> str:
    """Turn one bearing's readings into one of the `FORWARD_*` words.

    The thresholds are the forward clearance's own
    (`config.ForwardConfig`), deliberately shared rather than copied: a
    wall is near at the same distance whether the craft is flying at it or
    looking at it, and two tables would drift apart into a craft that
    stops at one distance and calls the same distance open at another.

    Too few valid readings is `FORWARD_UNKNOWN`, never `FORWARD_OPEN`. An
    invalid forward reading means empty space OR a surface too close to
    resolve (§4.8.6), so a bearing nobody could measure is a bearing not
    to fly at.

    The NEAREST reading decides, not the average: several readings of the
    same bearing differ because something is at the edge of the beam, and
    averaging a wall with the empty space beside it produces a distance at
    which nothing actually is.

    1 方位の読み取りを `FORWARD_*` のいずれかの語にする。

    しきい値は前方の空きが持つもの（`config.ForwardConfig`）で、写さず共有する。
    壁は、機体がそちらへ飛んでいようと見ているだけだろうと、同じ距離で「近い」
    のであり、表が 2 つあれば、ある距離で止まる一方で同じ距離を「開けている」と
    呼ぶ機体へと食い違っていく。

    有効な読み取りが足りない場合は `FORWARD_UNKNOWN` であって、決して
    `FORWARD_OPEN` ではない。無効な前方の読み値は「空間」または「近すぎて復元
    できない面」を意味するので（§4.8.6）、誰にも測れなかった方位とは、飛んでは
    ならない方位である。

    決めるのは平均ではなく**最も近い**読み取りである。同じ方位の複数の読み取りが
    食い違うのは、何かがビームの縁にあるからであり、壁とその脇の空間を平均すれば、
    実際には何も無い距離が出てくる。
    """
    has_enough = len(distances) >= config.explore.min_valid_samples
    if not has_enough:
        return FORWARD_UNKNOWN

    nearest = min(distances)
    forward = config.forward
    if nearest <= forward.wall_near_m:
        return FORWARD_WALL_NEAR
    if nearest <= forward.somewhat_near_m:
        return FORWARD_SOMEWHAT_NEAR
    return FORWARD_OPEN


# =============================================================================
# The state Jev is told about the surroundings / 周囲について Jev に伝える状況
# =============================================================================

def surroundings_state(sweep_result: Sweep, now: float,
                       config=DEFAULT_CONFIG) -> dict:
    """The `surroundings` section of the state: eight words and an age.

    Words only, as everywhere else. What the model is being asked is which
    way to go, and for that "open" against "wall near" is the whole of the
    useful information -- a distance would only invite the comparison the
    model is documented not to do.

    An empty sweep produces `{"measured": "never"}` rather than eight
    "cannot be measured" entries: the difference between "we looked and
    could not tell" and "we never looked" is one the model should see, and
    eight identical unknowns would be eight lines of noise besides.

    state の `surroundings` の部分。8 つの語と、測定の古さ 1 つ。

    他と同じく語だけで構成する。モデルに問うているのは「どちらへ行くか」であり、
    それには「開けている」対「壁が近い」で必要な情報は尽きている。距離を渡せば、
    モデルにはできないと文書化されている比較を誘発するだけである。

    空の掃引は、8 つの「測定不能」ではなく `{"measured": "never"}` を返す。
    「見たが判別できなかった」と「一度も見ていない」の違いはモデルが見るべき
    ものであり、同じ「不明」が 8 つ並べばそれだけで雑音 8 行になるからである。
    """
    from .summarizer import forward_word

    age = sweep_result.age_word(now, config)
    if not sweep_result.readings:
        return {"measured": age}

    state = {"measured": age}
    for reading in sweep_result.readings:
        state[reading.name] = forward_word(reading.clearance)
    return state


def bearing_criteria(sweep_result: Sweep) -> dict:
    """The options for the "which way" Choice, described by contrast.

    Only the bearings this sweep actually read are offered, plus staying
    put. Offering a bearing nobody measured would ask the model to choose
    between something seen and something imagined, and the Choice guidance
    is explicit that an option is matched on what it MEANS -- so an option
    that means nothing here should not be in the list at all.

    Each description says what taking that bearing would commit the
    aircraft to, and what it rules out, rather than restating the name
    (docs.typesafe.ai/primitives/choice).

    「どちらへ」の Choice の選択肢。対比で説明する。

    この掃引が実際に読んだ方位と、「進まない」だけを提示する。誰も測っていない
    方位を差し出すことは、見たものと想像したものの間で選べと求めることである。
    Choice の指針は「選択肢は**意味**で一致する」と明示しており、ここで意味を
    持たない選択肢は、そもそも並べるべきではない。

    各説明は、名前の言い換えではなく、その方位を取ることが機体に何を約束させ、
    何を排除するかを述べる（docs.typesafe.ai/primitives/choice）。
    """
    criteria = {}
    for reading in sweep_result.readings:
        criteria[reading.name] = _bearing_description(reading)
    criteria[BEARING_NONE] = (
        "Do not travel at all: hold this position. Choose this when no "
        "direction is clear enough to be worth entering, rather than "
        "picking the least bad one."
    )
    return criteria


def _bearing_description(reading: BearingReading) -> str:
    """One bearing's option text: where it goes and what was seen there.
    1 方位の選択肢の文。どこへ向かうかと、そこで何が見えたか。"""
    where = _where_phrase(reading.name)
    seen = _seen_phrase(reading.clearance)
    return f"Travel {where}. {seen}"


def _where_phrase(name: str) -> str:
    """How to say "go this way" for a bearing name.
    方位名に対する「こちらへ行く」の言い方。"""
    if name == "ahead":
        return "straight on, the way the aircraft is already facing"
    return f"{name}, turning to face that way first"


def _seen_phrase(clearance: str) -> str:
    """What the sweep saw at a bearing, and what that rules in or out.
    掃引がその方位で見たものと、それが何を許し何を排除するか。"""
    if clearance == FORWARD_OPEN:
        return ("Nothing was detected close this way, so there is room to "
                "travel before anything is reached.")
    if clearance == FORWARD_SOMEWHAT_NEAR:
        return ("Something was detected at a moderate distance this way: "
                "there is some room, but not a clear run.")
    if clearance == FORWARD_WALL_NEAR:
        return ("A surface is close this way, with no room to travel "
                "before reaching it.")
    return ("Nothing could be measured this way, which means either open "
            "space or a surface too close to read — the two cannot be "
            "told apart, so this direction is unverified rather than clear.")


# =============================================================================
# Refusing an answer the sweep contradicts / 掃引と矛盾する答えを却下する
# =============================================================================

def check_bearing_choice(choice: str, sweep_result: Sweep) -> str:
    """Why this bearing may not be flown, or "" if it may.

    Code's refusal, not the model's: Jev is shown "wall near" and may
    still name that bearing (a reasonable model might read a route through
    a gap the sensor cannot see), but the aircraft has exactly one forward
    sensor and it said there is a surface there. Between the two, the
    measurement wins -- the same division of labour every other limit in
    this package follows.

    `stay put` always passes: declining to travel is the conservative
    answer and is never refused.

    この方位を飛んではならない理由。飛んでよければ ""。

    モデルではなくコードの却下である。Jev は「壁が近い」を見せられたうえでなお
    その方位を名指ししうる（センサに見えない隙間を通る経路を読むモデルは、
    もっともらしくありうる）。しかし機体が持つ前方センサはちょうど 1 つであり、
    それが「そこに面がある」と言っている。両者のうち勝つのは測定である —— 本
    パッケージの他のすべての上限が従うのと同じ分担である。

    `stay put` は常に通る。進まないことは保守的な答えであり、却下しない。
    """
    if choice == BEARING_NONE:
        return ""

    reading = sweep_result.find(choice)
    if reading is None:
        return f"掃引に無い方位「{choice}」を選んだため却下する"
    if reading.clearance == FORWARD_WALL_NEAR:
        return (f"「{choice}」は壁が近いと測定されているため却下する"
                f"（前方センサの測距による）")
    if reading.clearance == FORWARD_UNKNOWN:
        return (f"「{choice}」は測定できていないため却下する"
                f"（測れない方位は、開けている方位ではない）")
    return ""


def rule_based_bearing(sweep_result: Sweep, goal_name: str = "ahead") -> str:
    """The keyless default: the open bearing closest to where we want to go.

    This is `FakeJudge`'s answer, and it is a rule rather than a model. It
    picks among the OPEN bearings only -- never a wall, never an unmeasured
    one -- and among those the one whose heading is nearest the goal's, so
    a rehearsal without a key still exercises "prefer open, and prefer the
    way we were going".

    It is not a model of Jev and does not claim to be: what a rehearsal
    under it shows is that the plumbing is right, never that Jev would
    choose the same (the same caveat `MissionFakeJudge` carries).

    キー不要の既定: 目的の方向に最も近い、開けている方位。

    これは `FakeJudge` の答えであり、モデルではなく規則である。選ぶのは**開けて
    いる**方位だけで、壁も、測れていない方位も決して選ばない。そのうえで、方位が
    目的に最も近いものを取る。キー無しの予行でも「開けているほうを選び、向かって
    いた方向を選ぶ」ところまでは動くようにするためである。

    Jev のモデルではないし、そう主張もしない。この規則の下での予行が示すのは配線が
    正しいことであって、Jev が同じものを選ぶことではない（`MissionFakeJudge` が
    持つのと同じ但し書き）。
    """
    open_bearings = sweep_result.open_bearings()
    if not open_bearings:
        return BEARING_NONE
    goal = sweep_result.find(goal_name)
    goal_deg = 0.0 if goal is None else goal.relative_deg
    nearest = min(open_bearings,
                  key=lambda r: _angle_between(r.relative_deg, goal_deg))
    return nearest.name


def _angle_between(one_deg: float, other_deg: float) -> float:
    """The smaller angle between two bearings [deg], 0..180.
    2 つの方位の間の小さいほうの角度 [度]。0〜180。"""
    difference = abs(one_deg - other_deg) % 360.0
    return min(difference, 360.0 - difference)
