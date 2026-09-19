"""
What the pre-landing approach guarantees before a descent begins.

着陸前手順が、降下の開始前に保証すること。

The firmware stops holding horizontal position for the whole of a descent
(`sf_controller_pid/pid_controller.cpp` excludes `VerticalPhase::Landing`
from `computePositionHold()`, by design). Whatever speed the craft carries
into the descent is carried through it, so the craft is brought to rest
first. These tests pin the order that happens in, the ceilings that end the
wait, and the one case that is allowed to hurry.

ファームは降下のあいだ水平の位置保持をやめる（`pid_controller.cpp` が
`computePositionHold()` から `VerticalPhase::Landing` を除外している。設計
どおり）。降下に持ち込んだ速度はそのまま持ち越されるので、先に機体を止める。
本試験はその順序、待ちを終わらせる上限、そして急いでよい唯一の場合を固定する。
"""

import pytest

from sfpilot.arbiter import VERDICT_HOVER, VERDICT_LAND, Verdict
from sfpilot.config import DEFAULT_CONFIG
from sfpilot.executor import Executor
from sfpilot.landing import (
    STAGE_AWAIT_MOVE, STAGE_LAND, STAGE_SETTLE, LandingApproach,
)


class _RecordingLink:
    """A link that records lines and can pretend a move is outstanding.
    行を記録し、移動が実行中であるかのように振る舞えるリンク。"""

    def __init__(self, reply_count: int = 0, outstanding: int = 0):
        self.sent: list = []
        self.rc: list = []
        self.reply_count = reply_count
        # How many sent commands the vehicle has not answered, which is how
        # a real link reports "a move is still running" (link.py).
        # 送信済みで機体がまだ応答していない指令の数。実際のリンクが「移動が
        # まだ実行中である」を伝える手段である（link.py）。
        self.replies_outstanding = outstanding

    def send_command(self, line: str) -> None:
        self.sent.append(line)

    def priority(self, line: str) -> None:
        self.sent.append(line)

    def send_rc(self, a, b, c, d) -> None:
        self.rc.append((a, b, c, d))

    def hold_sticks_neutral(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeClock:
    """A clock the test moves on, so a ceiling in seconds costs none.
    試験が自分で進める時計。秒で測る上限に、秒を費やさず到達するため。"""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _moving(*_args):
    """A craft still travelling far faster than the settling threshold.
    静定のしきい値をはるかに超えて移動し続けている機体。"""
    return 1.0


def _stopped(*_args):
    """A craft at rest. / 静止した機体。"""
    return 0.0


# =============================================================================
# The order of the approach / 手順の順序
# =============================================================================

def test_the_landing_stops_the_craft_before_it_asks_for_a_descent():
    """`stop` reaches the vehicle before `land` ever does.

    This is the whole point of the module: a `land` sent first would begin
    a descent with position hold already off and the craft still moving.

    `land` より先に `stop` が機体へ届くこと。

    本モジュールの目的そのものである。先に `land` を送れば、位置保持が既に切れ、
    機体がまだ動いている状態で降下が始まる。
    """
    link = _RecordingLink()
    approach = LandingApproach(link, DEFAULT_CONFIG, speed_probe=_stopped)

    approach.start(now=0.0)
    approach.step(now=0.0)

    assert link.sent[0] == "stop"
    assert "land" not in link.sent, "the descent must not begin yet"


def test_the_landing_waits_for_the_craft_to_come_to_rest():
    """No `land` goes out while the measured speed is still high.

    Measured, not assumed: the speed is what the approach waits on, so a
    craft that keeps moving keeps the landing waiting.

    実測した速度が高いあいだは `land` を送らないこと。

    仮定ではなく実測である。手順が待っているのは速度そのものなので、動き続けて
    いる機体は着陸を待たせ続ける。
    """
    link = _RecordingLink()
    approach = LandingApproach(link, DEFAULT_CONFIG, speed_probe=_moving)

    approach.start(now=0.0)
    for tick in range(20):
        approach.step(now=tick * 0.02)

    assert "land" not in link.sent
    assert approach.stage == STAGE_SETTLE


def test_the_landing_goes_out_once_the_craft_has_stayed_slow():
    """A speed held below the threshold for the hold time ends the wait.

    Held, not merely touched: an approach crosses zero velocity as it
    overshoots and turns back, so a single slow reading is not rest.

    しきい値を下回る速度が所定の時間続けば、待ちが終わること。

    一瞬下回るだけでは足りない。進入は行き過ぎて戻る際に速度 0 を通過するので、
    1 回遅く読めただけでは静止ではない。
    """
    link = _RecordingLink()
    approach = LandingApproach(link, DEFAULT_CONFIG, speed_probe=_stopped)
    hold_s = DEFAULT_CONFIG.landing.settle_hold_s

    approach.start(now=0.0)
    approach.step(now=0.0)
    assert "land" not in link.sent, "one slow reading is not rest"

    approach.step(now=hold_s + 0.01)

    assert link.sent == ["stop", "land"]
    assert approach.settled and not approach.timed_out


def test_a_single_slow_reading_does_not_count_as_rest():
    """A speed that dips low and rises again restarts the hold.

    This is the overshoot case: the craft passes through zero velocity on
    its way back, and landing there would land it mid-reversal.

    一瞬遅くなってまた速くなる速度は、静定の計時をやり直させること。

    行き過ぎの場合である。機体は戻る途中で速度 0 を通過するので、そこで着陸
    させれば、向きを変えている最中に着陸させることになる。
    """
    link = _RecordingLink()
    speeds = iter([0.0, 1.0, 0.0])
    approach = LandingApproach(link, DEFAULT_CONFIG,
                               speed_probe=lambda: next(speeds, 0.0))
    hold_s = DEFAULT_CONFIG.landing.settle_hold_s

    approach.start(now=0.0)
    approach.step(now=0.0)          # slow / 遅い
    approach.step(now=0.5)          # fast again -- the hold restarts / 速い
    approach.step(now=0.5 + hold_s - 0.01)   # slow, but not yet long enough

    assert "land" not in link.sent


# =============================================================================
# The ceilings / 上限
# =============================================================================

def test_a_craft_that_never_settles_is_landed_at_the_ceiling():
    """The wait ends, one way or another.

    A craft still being pushed never reads as stopped, and waiting forever
    spends the battery that makes a landing possible at all. Landing with
    some speed left is worse than landing well, but it is far better than
    not landing.

    待ちは、どちらにせよ終わること。

    押され続けている機体はいつまでも「止まった」と読めず、待ち続けることは、
    着陸を可能にしている当の電池を使う。速度を残した着陸はきれいな着陸より
    悪いが、着陸しないことよりはるかによい。
    """
    link = _RecordingLink()
    approach = LandingApproach(link, DEFAULT_CONFIG, speed_probe=_moving)
    ceiling = DEFAULT_CONFIG.landing.settle_max_s

    approach.start(now=0.0)
    approach.step(now=0.0)
    approach.step(now=ceiling + 0.01)

    assert link.sent == ["stop", "land"]
    assert approach.timed_out and not approach.settled


def test_an_urgent_landing_uses_the_shorter_ceiling():
    """A landing that cannot wait settles briefly rather than not at all.

    Half a second of `stop` already takes the worst of the approach speed
    off, and a battery in the danger band is not made worse by it.

    待てない着陸は、静定を省かず短く済ませること。

    0.5 秒の `stop` でも進入速度の大部分は落ちるし、危険域の電池がそれで
    悪化することはない。
    """
    link = _RecordingLink()
    urgent = LandingApproach(link, DEFAULT_CONFIG, speed_probe=_moving, urgent=True)
    ordinary = LandingApproach(_RecordingLink(), DEFAULT_CONFIG, speed_probe=_moving)

    assert urgent.settle_ceiling_s < ordinary.settle_ceiling_s

    urgent.start(now=0.0)
    urgent.step(now=0.0)
    urgent.step(now=DEFAULT_CONFIG.landing.urgent_settle_max_s + 0.01)

    assert link.sent == ["stop", "land"]


def test_an_urgent_landing_does_not_wait_for_a_running_move():
    """`stop` overtakes an outstanding move instead of queueing behind it.

    The priority path exists for exactly this, and a dangerous battery has
    no time to wait for a 100 cm move to finish on its own.

    `stop` は実行中の移動の後ろに並ばず、追い越すこと。

    優先経路はまさにこのためにあり、危険域の電池には、100cm の移動が自然に
    終わるのを待つ時間が無い。
    """
    link = _RecordingLink(reply_count=0, outstanding=1)
    link.sent.append("forward 100")     # a move is outstanding / 移動が実行中
    approach = LandingApproach(link, DEFAULT_CONFIG, speed_probe=_stopped,
                               urgent=True)

    approach.start(now=0.0)
    approach.step(now=0.0)

    assert "stop" in link.sent


def test_an_ordinary_landing_waits_for_a_running_move_to_answer():
    """A move still in flight is ended deliberately, not interrupted.

    A `land` that interrupts a move leaves the guidance target standing:
    the descent begins, position hold is off, and the craft accelerates
    towards a target nobody will cancel.

    実行中の移動は、割り込まれるのではなく意図して終わらせること。

    移動に割り込む `land` は誘導目標を立てたまま残す。降下が始まり、位置保持は
    切れており、機体は誰も取り消さない目標へ向かって加速する。
    """
    link = _RecordingLink(reply_count=0, outstanding=1)
    link.sent.append("forward 100")
    approach = LandingApproach(link, DEFAULT_CONFIG, speed_probe=_stopped)

    approach.start(now=0.0)
    approach.step(now=0.0)

    assert approach.stage == STAGE_AWAIT_MOVE
    assert "stop" not in link.sent[1:], "the move has not answered yet"


def test_a_move_that_never_answers_is_overridden_at_its_own_ceiling():
    """Waiting for a wedged move must not prevent the landing entirely.
    詰まった移動を待つことが、着陸そのものを妨げてはならないこと。"""
    link = _RecordingLink(reply_count=0, outstanding=1)
    link.sent.append("forward 100")
    approach = LandingApproach(link, DEFAULT_CONFIG, speed_probe=_stopped)
    wait_s = DEFAULT_CONFIG.landing.move_reply_wait_s

    approach.start(now=0.0)
    approach.step(now=0.0)
    approach.step(now=wait_s + 0.01)

    assert "stop" in link.sent


# =============================================================================
# The Executor is the only way to land / 着陸経路は Executor だけ
# =============================================================================

def test_the_executor_routes_every_landing_through_the_approach():
    """A `land` verdict starts the approach; no bare `land` is ever sent.

    One place sends the landing, which is what makes the settling before it
    unskippable. A second caller with its own `link.send_command("land")`
    would be a landing that silently does not settle.

    `land` の判定は手順を開始し、素の `land` は決して送られないこと。

    着陸を送る場所を 1 か所にすることが、その前の静定を省略不可能にする。自前で
    `link.send_command("land")` を呼ぶ 2 人目の呼び出し側は、静かに静定しない
    着陸になってしまう。
    """
    link = _RecordingLink()
    executor = Executor(link, DEFAULT_CONFIG, speed_probe=_moving)

    executor.apply(Verdict(action=VERDICT_LAND, reason="test"))

    assert link.sent == ["stop"]
    assert executor.landing, "the flight is ending from the moment it starts"


def test_nothing_else_is_commanded_once_a_landing_is_under_way():
    """An `rc` alongside the settling would re-publish a velocity target.

    `rc` publishes a VELOCITY guidance target (api_task.cpp cmdRc, mode 2),
    which is exactly what the settling is trying to get rid of.

    静定と並行した `rc` は速度目標を publish し直してしまうこと。

    `rc` は**速度**の誘導目標を publish する（api_task.cpp の cmdRc、mode 2）。
    それこそ、静定が取り除こうとしているものである。
    """
    link = _RecordingLink()
    executor = Executor(link, DEFAULT_CONFIG, speed_probe=_moving)
    executor.apply(Verdict(action=VERDICT_LAND))

    executor.apply(Verdict(action=VERDICT_HOVER))
    executor.tick()

    assert link.rc == [], f"an rc reached the vehicle mid-landing: {link.rc}"


def test_a_monitor_verdict_lands_urgently_and_others_do_not():
    """Only the immediate safety rules get the shortened settling.

    They are the definition of "cannot wait" -- they exist for situations
    that may not spend 500 ms asking for an opinion, so they may not spend
    six seconds settling either. Every other landing has the time.

    短縮した静定を使うのは即時安全則だけであること。

    即時安全則は「待てない」の定義そのものである。意見を求めて 500ms を使って
    はならない状況のための規則であり、だとすれば静定に 6 秒を使ってよいはずも
    ない。それ以外の着陸には時間がある。
    """
    urgent = Executor(_RecordingLink(), DEFAULT_CONFIG, speed_probe=_moving)
    urgent.apply(Verdict(action=VERDICT_LAND, source="monitor"))

    deliberate = Executor(_RecordingLink(), DEFAULT_CONFIG, speed_probe=_moving)
    deliberate.apply(Verdict(action=VERDICT_LAND, source="judge"))

    assert urgent.approach.urgent
    assert not deliberate.approach.urgent


def test_the_trace_records_how_the_landing_was_reached():
    """A summary says whether it settled, or ran out of time, and how long.

    "It landed while still moving" and "it waited the full six seconds
    first" are different flights, and a trace that could not tell them
    apart could not explain a touchdown that missed.

    記録が、静定して着陸したか上限で打ち切ったか、どれだけ待ったかを残すこと。

    「動いたまま着陸した」と「6 秒待ってから着陸した」は別の飛行であり、両者を
    区別できない記録では、外れた接地を説明できない。
    """
    # A clock the test moves on itself, rather than a tight loop: a loop of
    # calls runs in microseconds and would never reach a ceiling measured
    # in seconds.
    # 詰めたループではなく、試験が自分で進める時計を使う。呼び出しを並べた
    # ループはマイクロ秒で回り、秒で測る上限には決して達しない。
    clock = _FakeClock()
    link = _RecordingLink()
    executor = Executor(link, DEFAULT_CONFIG, speed_probe=_moving, clock=clock)
    executor.apply(Verdict(action=VERDICT_LAND, reason="上限の確認"))

    clock.advance(DEFAULT_CONFIG.landing.settle_max_s + 0.01)
    executor.apply(Verdict(action=VERDICT_HOVER))

    summary = executor.last_landing_summary
    assert summary, "a completed landing must leave a summary"
    assert summary["timed_out"] is True
    assert summary["settled"] is False
    assert summary["reason"] == "上限の確認"
    assert summary["ceiling_s"] == DEFAULT_CONFIG.landing.settle_max_s
