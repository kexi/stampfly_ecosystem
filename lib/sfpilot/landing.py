"""
sfpilot.landing - bring the aircraft to rest, then land it.
sfpilot.landing - 機体を止めてから着陸させる。

The firmware stops holding horizontal position for the whole of a descent.
That is a deliberate design decision, not a defect:
`sf_controller_pid/pid_controller.cpp` excludes `VerticalPhase::Landing`
from `computePositionHold()` so that a descent steers identically in every
flight mode -- a POS_HOLD landing handles like a STABILIZE one. The
consequence for anything that lands automatically is that the horizontal
velocity the craft carries INTO the descent is carried THROUGH it with
nothing opposing it.

ファームウェアは降下のあいだ、水平の位置保持を止める。これは欠陥ではなく
意図した設計判断である。`sf_controller_pid/pid_controller.cpp` は
`computePositionHold()` から `VerticalPhase::Landing` を除外し、降下の操縦則を
全ての飛行モードで同一にしている（POS_HOLD の着陸も STABILIZE と同じ操縦感に
なる）。自動で着陸する側から見た帰結は、降下に**持ち込んだ**水平速度が、
妨げるものなく降下中も**持ち越される**ことである。

Measured in SILS (truth.csv, horizontal travel from the moment `land` is
sent to touchdown):

SILS での実測（truth.csv。`land` を送った時点から接地までの水平移動）:

    land immediately after a move / 移動の直後に `land`        0.45 m
    land after a 3 s pause / 3 秒おいてから `land`             0.10 m
    land from a stationary hover / 静止したホバリングから      0.000 m

So this module does what the descent cannot: it ends any move that is
still running, commands a hover, waits for the MEASURED speed to stay low,
and only then asks for the landing. Two things it deliberately does not do:

そこで本モジュールは、降下ができないことを代わりに行う。実行中の移動を
終わらせ、その場での保持を指令し、**実測した**速度が低いまま続くのを待ち、
そのうえで着陸を求める。意図してやらないことが 2 つある:

  - It does not wait forever. A craft that is still being pushed never
    reads as stopped, and waiting indefinitely spends the battery that
    makes a landing possible. The wait has a ceiling and the landing goes
    out when it expires.
    無限には待たない。押され続けている機体はいつまでも「止まった」と読めず、
    待ち続けることは、着陸を可能にしている当の電池を使う。待ちには上限があり、
    切れた時点で着陸を送る。

  - It does not slow down an emergency. When the reason to land is one
    that cannot wait -- a battery in the danger band, a diverged estimate
    -- the same approach runs with a much shorter ceiling. It is not
    skipped even then, because half a second of `stop` already removes
    most of the approach speed, and the difference between touching down
    at rest and touching down at 0.4 m/s is not something a dangerous
    battery is made worse by.
    緊急の着陸を遅らせない。待てない理由での着陸 — 電池の危険域、推定の発散 —
    では、同じ手順を大幅に短い上限で走らせる。そのときも省略はしない。0.5 秒の
    `stop` で進入速度の大部分は落ちるうえ、静止して接地するか 0.4m/s で接地するか
    の違いで、危険域の電池が悪化することはないからである。

`emergency` (cutting the motors) is not a landing and does not pass
through here. It is not reachable from this package at all -- see
monitor.py.

`emergency`（モータ停止）は着陸ではなく、ここを通らない。そもそも本パッケージ
からは到達できない（monitor.py 参照）。
"""

import time

from .config import DEFAULT_CONFIG

# The stages the approach passes through, in order. Recorded per landing so
# a trace says which one the ceiling expired in -- "it landed while still
# moving" and "it waited the full six seconds first" are different flights.
# 着陸前手順が通る段階を順に並べたもの。着陸ごとに記録し、どの段階で上限に
# 達したかが記録から分かるようにする。「動いたまま着陸した」と「6 秒待ってから
# 着陸した」は別の飛行である。
STAGE_AWAIT_MOVE = "await_move"     # waiting for a running move to answer / 実行中の移動の応答待ち
STAGE_STOP = "stop"                 # commanding the hover / その場の保持を指令
STAGE_SETTLE = "settle"             # waiting for the craft to slow / 減速待ち
STAGE_LAND = "land"                 # the landing itself / 着陸そのもの


class LandingApproach:
    """Run the pre-landing sequence without blocking the monitor loop.

    The approach is a state machine advanced one call per cycle rather
    than a function that sleeps. The 50Hz loop is what measures the speed
    this approach waits on, so a blocking wait would stop the very
    measurement it depends on -- and would stop the immediate safety rules
    at the same time, during a descent, which is when they matter most.

    監視ループを止めずに着陸前手順を進める。

    手順は、眠る関数ではなく、1 周期に 1 回呼んで進める状態機械にしてある。この
    手順が待っている速度を測っているのが 50Hz のループ自身であり、ブロックして
    待てば、依存している当の計測が止まる。しかも同時に即時安全則も止まる —
    降下中、それが最も重要な場面で、である。
    """

    def __init__(self, link, config=DEFAULT_CONFIG, speed_probe=None,
                 urgent: bool = False, reason: str = "", clock=None):
        self.link = link
        self.cfg = config.landing
        # Where "now" comes from. Injectable so a test can reach a ceiling
        # measured in seconds without spending them: a loop of `step()`
        # calls runs in microseconds and would never expire anything.
        # 「今」の出どころ。差し替え可能にしてある。秒で測る上限に、実際に秒を
        # 費やさずに到達できるようにするためである。`step()` を並べたループは
        # マイクロ秒で回り、どの上限にも達しない。
        self.clock = clock or time.monotonic
        # Reads the aircraft's horizontal speed [m/s], or None when it is
        # not known. Supplied by whoever owns the sample stream, because two
        # readers on one queue would take samples from each other.
        # 機体の水平速度 [m/s] を読む。分からなければ None。サンプル列を所有する
        # 側が渡す。1 つのキューを 2 つが読めば、互いのサンプルを奪い合うため。
        self.speed_probe = speed_probe
        self.urgent = urgent
        self.reason = reason
        self.stage = STAGE_AWAIT_MOVE
        self.landed = False
        # What the trace records: which stage each phase ended in, how long
        # the whole approach took, and whether it ran out of time.
        # 記録に残すもの: 各段階がどう終わったか、手順全体に要した時間、上限に
        # 達したかどうか。
        self.timed_out = False
        self.settled = False
        self.elapsed_s = 0.0
        self._started = None
        self._stage_started = None
        self._replies_at_start = 0
        self._slow_since = None

    @property
    def settle_ceiling_s(self) -> float:
        """How long the settling wait may take, given the urgency.
        緊急かどうかに応じた、静定待ちに許される時間。"""
        if self.urgent:
            return self.cfg.urgent_settle_max_s
        return self.cfg.settle_max_s

    def start(self, now: float = None) -> None:
        """Begin the approach. Safe to call once. / 手順を開始する。1 回だけ呼ぶ。"""
        now = self.clock() if now is None else now
        self._started = now
        self._stage_started = now
        self._replies_at_start = _reply_count(self.link)
        has_running_move = self._replies_at_start < _command_count(self.link)
        if not has_running_move:
            self._enter_stop(now)

    def step(self, now: float = None) -> bool:
        """Advance one cycle. Returns True once the landing has been sent.
        1 周期進める。着陸を送り終えたら True を返す。"""
        now = self.clock() if now is None else now
        if self.landed:
            return True
        if self._started is None:
            self.start(now)
        self.elapsed_s = now - self._started

        if self.stage == STAGE_AWAIT_MOVE:
            self._advance_await_move(now)
            return False
        if self.stage == STAGE_SETTLE:
            self._advance_settle(now)
        return self.landed

    # -- the stages / 各段階 --------------------------------------------

    def _advance_await_move(self, now: float) -> None:
        """Wait for a running blocking move to answer, then stop it.

        A `land` that interrupts a move leaves the guidance target standing:
        the descent begins, position hold is off, and the craft accelerates
        towards a target nobody is going to cancel. Waiting for the reply
        (or overriding it with `stop` when it does not come) ends the move
        deliberately instead.

        実行中のブロックする移動の応答を待ち、そのうえで止める。

        移動の途中に割り込む `land` は、誘導目標を立てたまま残す。降下が始まり、
        位置保持は切れており、機体は誰も取り消さない目標へ向かって加速する。
        応答を待つ（来なければ `stop` で上書きする）ことで、移動を意図して
        終わらせる。
        """
        answered = _reply_count(self.link) > self._replies_at_start
        waited_s = now - self._stage_started
        is_overdue = waited_s >= self.cfg.move_reply_wait_s
        # An urgent landing does not wait for a move at all: `stop` overtakes
        # it on the priority path, which is what the priority path is for.
        # 緊急の着陸は移動を待たない。`stop` は優先経路で追い越す — 優先経路は
        # そのためにある。
        if answered or is_overdue or self.urgent:
            self._enter_stop(now)

    def _enter_stop(self, now: float) -> None:
        """Command the hover that re-captures the present position.
        現在位置を捕捉し直す保持を指令する。"""
        self.link.priority("stop")
        self.stage = STAGE_SETTLE
        self._stage_started = now
        self._slow_since = None

    def _advance_settle(self, now: float) -> None:
        """Wait for the measured speed to stay low, or for the ceiling.
        実測した速度が低いまま続くのを待つ。上限に達したらそこで進む。"""
        if self._is_settled(now):
            self.settled = True
            self._send_land(now)
            return
        waited_s = now - self._stage_started
        is_out_of_time = waited_s >= self.settle_ceiling_s
        if is_out_of_time:
            self.timed_out = True
            self._send_land(now)

    def _is_settled(self, now: float) -> bool:
        """Whether the craft has been slow for long enough to count as at rest.
        静止とみなせるだけの時間、遅いままでいたか。"""
        speed = self.speed_probe() if self.speed_probe is not None else None
        if speed is None or speed >= self.cfg.settle_speed_mps:
            self._slow_since = None
            return False
        if self._slow_since is None:
            self._slow_since = now
        return (now - self._slow_since) >= self.cfg.settle_hold_s

    def _send_land(self, now: float) -> None:
        self.stage = STAGE_LAND
        self.elapsed_s = now - self._started
        self.link.send_command("land")
        self.landed = True

    # -- for the trace / 記録用 -----------------------------------------

    def summary(self) -> dict:
        """What this approach did, for the decision row.
        この手順が何をしたか。判断の行に載せる。"""
        return {
            "urgent": self.urgent,
            "reason": self.reason,
            "settled": self.settled,
            "timed_out": self.timed_out,
            "waited_s": round(self.elapsed_s, 2),
            "ceiling_s": self.settle_ceiling_s,
        }


def _reply_count(link) -> int:
    """How many commands the vehicle has answered, if the link counts them.
    機体が応答した指令の数（リンクが数えていれば）。"""
    return getattr(link, "reply_count", 0)


def _command_count(link) -> int:
    """How many commands have been sent, if the link records them.

    A link that records neither (a stand-in in a test) reports 0 for both,
    which reads as "no move is running" -- the approach then goes straight
    to `stop`, which is correct for a link that cannot have a move running.

    これまでに送った指令の数（リンクが記録していれば）。

    どちらも記録しないリンク（試験の代役）は双方 0 を返し、「実行中の移動は
    無い」と読める。手順はそのまま `stop` へ進むが、移動を実行しえないリンクに
    とってそれは正しい。
    """
    sent = getattr(link, "sent", None)
    if sent is None:
        return 0
    # Only blocking verbs get a reply; `rc` does not. Counting every sent
    # line against the reply count would make an `rc`-heavy flight look as
    # though a move were permanently outstanding.
    # 応答が返るのはブロックする verb だけで、`rc` には返らない。送った行を
    # すべて応答数と比べると、`rc` の多い飛行では移動が永久に実行中であるかの
    # ように見えてしまう。
    return sum(1 for line in sent if not line.startswith("rc "))
