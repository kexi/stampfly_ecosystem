"""
sfpilot.say - fly a translated instruction while the safety layer watches.
sfpilot.say - 変換した指示を、安全層に見張らせながら飛ぶ。

The instruction decides WHERE the aircraft goes; the safety layer of use
(1) decides WHETHER it keeps going. Both run at once, and the safety layer
wins: it is the same Monitor, Summarizer, Judge and Arbiter that `sf pilot
run` uses, with the same rules, and a verdict of hovering or landing
interrupts the sequence wherever it had got to.

指示が決めるのは機体が**どこへ**行くかであり、用途①の安全層が決めるのは
**行き続けてよいか**である。両者は同時に動き、勝つのは安全層である。用途①と
同じ Monitor・Summarizer・Judge・Arbiter を同じ規則で使い、待機または着陸の
判定が出れば、手順はその時点で中断する。

Why the moves are not sent from the monitor loop's own thread: `forward`
and its siblings block on the VEHICLE side until the move is reached
(api_task.cpp cmdMove waits and then replies), which for a 100 cm move is
seconds. Sending one from the loop would stop the 50Hz monitoring for
exactly as long as the aircraft is actually moving -- the part of the
flight that most needs watching. So a worker thread walks the sequence
and the loop keeps sampling, judging and, if it must, cutting the
sequence short.

移動を監視ループ自身のスレッドから送らない理由: `forward` などは**機体側**で
到達までブロックする（api_task.cpp の cmdMove が待ってから応答する）。100cm の
移動なら数秒である。ループから送れば、機体が実際に動いている間 —— 最も見張る
必要のある区間 —— ちょうどその長さだけ 50Hz の監視が止まる。そこで手順の列は
作業スレッドが進め、ループは採取と判断を続け、必要なら手順を打ち切る。
"""

import threading
import time
from dataclasses import dataclass, field

from .arbiter import VERDICT_HOVER, VERDICT_LAND, VERDICT_STOP
from .config import DEFAULT_CONFIG
from .pilot import Pilot


@dataclass
class SayOutcome:
    """What became of the instruction. / 指示がどうなったか。"""

    completed: list = field(default_factory=list)   # steps actually sent / 実際に送った手順
    interrupted_at: int = 0     # 1-based step the safety layer cut / 中断した手順（1 始まり）
    interrupt_reason: str = ""
    landed: bool = False
    decisions: int = 0
    flown_s: float = 0.0

    @property
    def finished(self) -> bool:
        """Whether every step was sent. / 全手順を送り終えたか。"""
        return not self.interrupt_reason


class StepRunner:
    """Walk a plan's steps on a worker thread, stoppable at any point.

    Each step is one API line and then a wait for the vehicle to finish
    it. The wait is polled rather than slept through in one piece, so a
    `stop` from the safety layer takes effect within a poll interval
    instead of after the move completes.

    計画の手順を作業スレッドで進める。いつでも止められる。

    1 手順は API 行 1 つと、機体がそれを終えるのを待つ時間である。待ちは
    一括で眠らずに刻んで見張る。安全層からの停止が、移動の完了後ではなく
    1 刻みのうちに効くようにするためである。
    """

    # How often the worker checks whether it has been told to stop, and
    # how long it allows one step before giving up on it. The vehicle's own
    # move timeout is the path length over the speed plus 6 s
    # (api_task.cpp), so this ceiling sits above the longest legal move.
    # 作業スレッドが停止指示を確認する間隔と、1 手順に許す上限時間。機体側の
    # 移動タイムアウトは経路長÷速度＋6 秒（api_task.cpp）なので、この上限は
    # 許される最長の移動より上に置いてある。
    POLL_S = 0.05
    STEP_TIMEOUT_S = 25.0

    # After a move answers, wait for the aircraft to actually come to rest
    # before sending the next command.
    #
    # `ok` means the ESTIMATE entered the 0.15 m tolerance sphere around the
    # target (api_task.cpp kReachRadiusM), not that the aircraft has stopped
    # -- it is still carrying its approach speed. Measured: `land` issued
    # straight after a 50 cm return keeps travelling through the descent and
    # touches down 0.7 m past the takeoff point, while a `land` from a
    # genuinely stationary hover drifts 0.000 m. So the wait ends on the
    # MEASURED speed falling below the threshold, not on a guessed duration
    # -- a longer move carries more speed and needs longer, and no single
    # fixed figure is right for both.
    #
    # 移動が応答した後、次の指令を送る前に機体が実際に止まるまで待つ。
    #
    # `ok` は**推定**が目標まわりの許容球 0.15m に入ったという意味であり
    # （api_task.cpp の kReachRadiusM）、機体が止まったという意味ではない —
    # 進入時の速度をまだ持っている。実測: 50cm の帰還の直後に `land` を送ると
    # 降下中も進み続け、離陸点を 0.7m 行き過ぎて接地する。一方、本当に静止した
    # ホバリングからの `land` の横流れは 0.000m である。そこで待ちは、推測した
    # 時間ではなく**実測した速度**がしきい値を下回ることで終える — 長い移動ほど
    # 速度を持つので長く要り、どちらにも正しい固定値は存在しない。
    SETTLE_SPEED_MPS = 0.05
    SETTLE_MAX_S = 8.0

    # The speed must stay low for this long before the craft counts as
    # settled. A single slow reading is not rest: the approach crosses zero
    # velocity as it overshoots and turns back, so a one-shot check passes
    # at exactly the moment the craft is about to accelerate the other way.
    # Measured on a plain `forward 50`: the estimate's speed first dips
    # under the threshold around the tolerance sphere but does not STAY
    # under it until about 6 s, which is where the craft actually stops.
    # 機体が静定したとみなすには、速度がこの時間だけ低いままである必要がある。
    # 1 回遅く読めただけでは静止ではない。進入は行き過ぎて戻る際に速度 0 を
    # 通過するので、1 回きりの確認は「これから逆向きに加速する」まさにその瞬間に
    # 通ってしまう。素の `forward 50` での実測: 推定速度は許容球のあたりで最初に
    # しきい値を下回るが、**下回り続ける**のは約 6 秒後で、そこが実際に機体が
    # 止まる時点である。
    SETTLE_HOLD_S = 1.0

    def __init__(self, link, steps: list, config=DEFAULT_CONFIG, speed_probe=None):
        self.link = link
        self.steps = list(steps)
        self.cfg = config
        # Reads the aircraft's current horizontal speed [m/s], or None when
        # it is not known. The monitor loop owns the sample stream, so the
        # runner asks it rather than reading the link itself -- two readers
        # on one queue would take samples from each other.
        # 機体の現在の水平速度 [m/s] を読む。分からなければ None。サンプル列は
        # 監視ループのものなので、runner は自分で link を読まずループに尋ねる。
        # 1 つのキューを 2 つが読めば、互いのサンプルを奪い合うためである。
        self.speed_probe = speed_probe
        self.sent: list = []
        self._stop = threading.Event()
        self._done = threading.Event()
        self._thread = None

    def start(self) -> None:
        # Daemon thread: a step still waiting on the vehicle must never
        # keep the CLI alive at exit (the same rule pilot.py applies to an
        # outstanding question).
        # daemon スレッドにする: 機体を待っている手順が CLI の終了を妨げては
        # ならない（未完了の質問に対して pilot.py が置いているのと同じ規則）。
        self._thread = threading.Thread(target=self._walk, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ask the walk to end after the step it is on. / 進行中の手順の後で終える。"""
        self._stop.set()

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def _walk(self) -> None:
        """Send each step in turn, stopping if asked. / 各手順を順に送る。"""
        try:
            for step in self.steps:
                if self._stop.is_set():
                    return
                answered_before = self._reply_count()
                self.link.send_command(step.command())
                self.sent.append(step)
                self._await_step(step, answered_before)
        finally:
            self._done.set()

    def _await_step(self, step, answered_before: int) -> None:
        """Wait until the vehicle answers this step, or the ceiling is hit.

        Every verb the plan sends blocks on the VEHICLE side and answers
        only when it is done -- `forward` when the move is reached,
        `takeoff` when FLYING (api_task.cpp). So the wait ends on the
        vehicle's own answer rather than on a guessed duration: a 20 cm hop
        takes its second and a 100 cm hop takes its several, and neither is
        padded to the same fixed budget.

        The ceiling remains for the case the answer never comes (a refused
        verb still answers, but a wedged one would not), and the stop flag
        is checked throughout so the safety layer is never waited out.

        この手順に機体が応答するまで待つ。上限に達したらそこで打ち切る。

        計画が送る verb はいずれも**機体側**でブロックし、完了してはじめて
        応答する（`forward` は到達時、`takeoff` は FLYING 到達時。api_task.cpp）。
        そこで待ちは、推測した所要時間ではなく機体自身の応答で終える。20cm の
        移動は 1 秒ほど、100cm の移動は数秒かかり、どちらも同じ固定の枠に
        水増しされない。

        上限は、応答が来ない場合のために残す（拒否された verb も応答は返すが、
        詰まった場合は返らない）。停止指示は待っている間ずっと確認するので、
        安全層が待たされることはない。
        """
        deadline = time.monotonic() + self.STEP_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._stop.is_set():
                return
            if self._reply_count() > answered_before:
                self._settle()
                return
            time.sleep(self.POLL_S)

    def _settle(self) -> None:
        """Wait until the aircraft is travelling slowly, or the ceiling is hit.

        The ceiling matters: a craft still being pushed (a disturbance, a
        drifting estimate) would otherwise never read as stopped and the
        sequence would stall. Stalling is worse than a slightly early next
        command, because the safety layer's own hold-to-land timer is not
        what should be ending an instruction.

        機体がゆっくりになるまで待つ。上限に達したらそこで進む。

        上限には意味がある。押され続けている機体（外乱、推定の流れ）は、
        いつまでも「止まった」と読めず、手順が進まなくなってしまう。止まって
        しまうほうが、次の指令が少し早いことより悪い — 指示を終わらせるのは、
        安全層の待機継続の計時であるべきではないからである。
        """
        deadline = time.monotonic() + self.SETTLE_MAX_S
        slow_since = None
        while time.monotonic() < deadline:
            if self._stop.is_set():
                return
            now = time.monotonic()
            if not self._is_at_rest():
                slow_since = None
                time.sleep(self.POLL_S)
                continue
            if slow_since is None:
                slow_since = now
            has_held = (now - slow_since) >= self.SETTLE_HOLD_S
            if has_held:
                return
            time.sleep(self.POLL_S)

    def _is_at_rest(self) -> bool:
        """Whether the aircraft has slowed below the settling threshold.
        機体が静定のしきい値より遅くなったか。"""
        if self.speed_probe is None:
            return False
        speed = self.speed_probe()
        if speed is None:
            return False
        return speed < self.SETTLE_SPEED_MPS

    def _reply_count(self) -> int:
        """How many commands the vehicle has answered, if it can say.

        A Link that does not count replies (a recording stand-in in a test)
        reports zero forever, which turns the wait back into the timeout --
        correct, if slow, rather than an AttributeError mid-flight.

        機体がこれまでに応答した指令の数（分かる場合）。

        応答を数えない Link（試験の記録用の代役）は常に 0 を返し、待ちは
        上限まで待つ形に戻る。飛行中に AttributeError を出すより、遅くとも
        正しく動くほうがよい。
        """
        return getattr(self.link, "reply_count", 0)


def fly_plan(link, judge, plan, config=DEFAULT_CONFIG, trace=None,
             scene=None, on_step=None) -> SayOutcome:
    """Fly the plan's steps under the use-(1) safety layer, and report.

    The loop below is `sf pilot run`'s loop with one addition: it also
    watches the step runner, and it hands the runner a stop as soon as the
    Arbiter's verdict is anything other than carrying on. The Arbiter's
    own rules are untouched -- a plan being under way is not a reason to
    weaken them.

    計画の手順を用途①の安全層の下で飛ばし、結果を返す。

    下のループは `sf pilot run` のループに 1 つ足しただけである: 手順の進行も
    見張り、Arbiter の判定が「続行」以外になった時点で進行に停止を伝える。
    Arbiter の規則自体には手を触れない — 計画が進行中であることは、規則を
    緩める理由にならない。
    """
    pilot = Pilot(link, judge, config, trace=trace,
                  operator_instruction=plan.instruction)
    # The plan drives the vehicle; the safety layer only watches and, if it
    # must, stops. See Executor.hold_commands_silently for why a hovering
    # `rc` alongside a move would cancel the move.
    # 機体を駆動するのは計画であり、安全層は見張って必要なら止めるだけである。
    # 移動と並行した待機の `rc` が移動を打ち消す理由は
    # Executor.hold_commands_silently を参照。
    pilot.executor.hold_commands_silently = True
    runner = StepRunner(link, plan.steps, config,
                        speed_probe=lambda: _horizontal_speed(pilot))
    outcome = SayOutcome()
    started = time.monotonic()
    period = 1.0 / config.monitor_hz
    runner.start()

    while not runner.done:
        cycle_start = time.monotonic()
        pilot.step()
        link.hold_sticks_neutral()
        _report_progress(runner, outcome, on_step)
        interrupt = _interrupt_reason(pilot)
        if interrupt:
            outcome.interrupt_reason = interrupt
            outcome.interrupted_at = len(runner.sent)
            runner.stop()
            break
        slack = period - (time.monotonic() - cycle_start)
        if slack > 0:
            time.sleep(slack)

    outcome.completed = list(runner.sent)
    outcome.landed = pilot.executor.landing
    outcome.decisions = len(pilot.decisions)
    outcome.flown_s = time.monotonic() - started
    _finish(link, pilot, outcome, config)
    return outcome


def _horizontal_speed(pilot):
    """The aircraft's horizontal speed [m/s] from the latest sample, or None.

    Read from the Monitor's numerics, which is where the loop already keeps
    the figures it classified from. The runner needs the NUMBER, not the
    classification ("drifting slowly" spans a range too wide to settle on).

    最新サンプルから見た機体の水平速度 [m/s]。分からなければ None。

    Monitor の numeric から読む。ループが区分の元にした数値を既にそこへ置いて
    いるためである。runner に必要なのは区分ではなく**数値**である（「ゆっくり
    流されている」が表す幅は、静定の判定には広すぎる）。
    """
    sample = pilot.monitor.latest_sample
    if not sample:
        return None
    north, east = sample.get("vel_n"), sample.get("vel_e")
    if north is None or east is None:
        return None
    return (north * north + east * east) ** 0.5


def _report_progress(runner, outcome: SayOutcome, on_step) -> None:
    """Tell the caller about steps that have gone out since last time.
    前回以降に送られた手順を呼び出し側へ伝える。"""
    if on_step is None:
        return
    already = len(outcome.completed)
    for index in range(already, len(runner.sent)):
        outcome.completed.append(runner.sent[index])
        on_step(index + 1, runner.sent[index])


def _interrupt_reason(pilot) -> str:
    """Why the sequence must stop now, or "" to carry on.

    A landing or a stop always interrupts: those are the outcomes the
    safety layer reaches when the flight must not continue as it is.

    Hovering is different, and the difference is the Arbiter's own. Most
    hovers mean "no usable opinion yet" -- the answer has not arrived, or
    arrived late, or described a situation that had already moved on
    (arbiter.py's rejection rules). Those happen routinely, several times
    during any takeoff, and treating each one as an interrupt would cut
    every instruction off at its first step. What DOES interrupt is a hover
    Jev deliberately chose, which is the model saying the aircraft should
    wait. A hover that will not end is not lost either: the Arbiter's own
    hover-to-land timer turns it into a landing, which interrupts here.

    今すぐ手順を止めるべき理由。続けてよければ ""。

    着陸と停止は常に中断する。飛行をこのまま続けてはならないときに安全層が
    到達する結果だからである。

    待機は別で、その区別は Arbiter 自身のものである。待機の多くは「まだ使える
    意見が無い」を意味する — 答えが未着・期限超過・状況が変わった後に到着
    （arbiter.py の却下規則）。これらはどの離陸でも何度か普通に起きるので、
    ひとつずつ中断として扱えば、あらゆる指示が最初の手順で打ち切られる。
    中断すべきなのは **Jev が選んだ**待機で、これは「待つべきだ」というモデルの
    判断である。終わらない待機も見落とさない: Arbiter 自身の待機継続の計時が
    それを着陸に変え、着陸はここで中断になる。
    """
    if not pilot.decisions:
        return ""
    verdict = pilot.decisions[-1]["verdict"]
    is_unsafe = verdict.action in (VERDICT_LAND, VERDICT_STOP)
    is_chosen_hold = verdict.action == VERDICT_HOVER and verdict.source == "judge"
    if not (is_unsafe or is_chosen_hold):
        return ""
    return f"{verdict.action}: {verdict.reason}"


def _finish(link, pilot, outcome: SayOutcome, config) -> None:
    """Leave the aircraft somewhere safe, whatever happened.

    An interrupted sequence has stopped mid-move with the vehicle still
    holding the target it was given, so the aircraft is told to hold where
    it is and then land. Landing rather than hovering is the design's rule
    for a situation that is not resolving itself (arbiter.py), and an
    instruction that was cut short is exactly that.

    何が起きたかに関わらず、機体を安全な状態にして終える。

    中断した手順は移動の途中で止まっており、機体は与えられた目標を保持した
    ままである。そこで、その場で保持させたうえで着陸させる。解消しない状況で
    待機ではなく着陸を選ぶのは設計の規則であり（arbiter.py）、打ち切られた
    指示はまさにその状況である。
    """
    if outcome.landed:
        return
    if outcome.interrupt_reason:
        link.priority("stop")
    link.send_command("land")
    outcome.landed = True
    time.sleep(config.sils.land_grace_s)
