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

import dataclasses

from .arbiter import VERDICT_BACK, VERDICT_HOVER, VERDICT_LAND, VERDICT_STOP, Verdict
from .config import DEFAULT_CONFIG
from .judge import STEP_FORWARD
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
    # The decision rows themselves, for writing the timeline beside the
    # flight-log bundle. `decisions` stays the COUNT so existing callers
    # and their printed summaries are unchanged.
    # 判断の行そのもの。フライトログ一式の隣に時系列を書き出すために持つ。
    # `decisions` は**件数**のままとし、既存の呼び出し側と表示を変えない。
    decision_rows: list = field(default_factory=list)

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

    def __init__(self, link, steps: list, config=DEFAULT_CONFIG, speed_probe=None,
                 forward_probe=None):
        self.link = link
        self.steps = list(steps)
        self.cfg = config
        # Reads the latest valid forward distance [m], or None when there is
        # none. Used to shorten a forward move that would end inside a wall,
        # so the craft never builds the speed the safety rule would then have
        # to arrest. Optional: without it moves go out at their full length
        # and the immediate rule remains the only protection, which is the
        # behaviour this class had before.
        # 最新の有効な前方距離 [m] を読む。無ければ None。壁の中で終わる前進を
        # 短く刻むために使い、そもそも安全則が止める羽目になる速度を機体に
        # 付けさせない。省略可能で、無ければ移動は元の長さのまま出て、即時則だけが
        # 防護になる ―― 本クラスの従来の挙動である。
        self.forward_probe = forward_probe
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
                # Take the count AFTER draining any reply still owed by an
                # earlier command, so this step waits for its OWN reply.
                # See `_drain_stale_replies`.
                # 直前の指令がまだ返していない応答を捨ててから数える。この手順が
                # **自分の**応答を待つようにするためである（`_drain_stale_replies`）。
                self._drain_stale_replies()
                answered_before = self._reply_count()
                flown = self._limited(step)
                # A move limited down to nothing is not sent: the vehicle
                # refuses anything under `move_min_cm` (`error out of
                # range`), and there is no distance left to travel anyway.
                # It stays in `sent` so the operator's summary still shows
                # the step was reached and what became of it.
                # 0 まで刻まれた移動は送らない。機体は `move_min_cm` 未満を拒否
                # するし（`error out of range`）、そもそも進む距離が残っていない。
                # `sent` には残す。操作者の要約に、その手順に到達したことと、
                # どうなったかが出るようにするためである。
                is_flyable = (flown.amount is None
                              or flown.amount >= self.cfg.instruction.move_min_cm)
                self.sent.append(flown)
                if not is_flyable:
                    continue
                self.link.send_command(flown.command())
                self._await_step(flown, answered_before)
        finally:
            self._done.set()

    def _limited(self, step):
        """The step as it will actually be flown, shortened if a wall is near.

        Only a `forward` is limited, and only when the forward distance is
        known. A `forward N` is a step to a position target N centimetres
        ahead, and the craft accelerates towards it up to the envelope's
        ceiling -- so a move that ENDS inside a wall is a move that arrives
        at the wall at speed, which is exactly the approach the immediate
        rule then has to arrest without any brakes.

        The move is cut to stop short of the wall by the distance the craft
        will need to stop from the speed the move will reach
        (`ForwardConfig`). That keeps the wall outside the stopping distance
        throughout, so the immediate rule stays what it is meant to be --
        the last resort -- rather than the thing every approach relies on.

        A move can be shortened to nothing, and then nothing is sent: the
        vehicle refuses a move below `move_min_cm` anyway, and a craft
        already inside its own stopping distance should not be asked to
        travel further forward at all. The step is kept in the sequence with
        a zero amount so the trace still shows it was asked for.

        実際に飛ぶ形の手順。壁が近ければ短く刻む。

        刻むのは `forward` だけであり、前方距離が分かっている場合だけである。
        `forward N` は N cm 先の位置目標への移動であり、機体は飛行領域の上限まで
        加速する。したがって壁の**中で終わる**移動とは、壁へ速度を乗せて到達する
        移動のことであり、それこそが、制動手段を持たない即時則がその後で止める
        羽目になる進入そのものである。

        移動は、「その移動が到達する速度から止まるのに要る距離」だけ壁の手前で
        終わるように切り詰める（`ForwardConfig`）。こうすれば壁は常に停止距離の
        外に留まり、即時則は本来あるべきもの ―― 最後の砦 ―― のままでいられる。
        あらゆる接近が頼る当てにはならない。

        刻んだ結果が 0 になることもあり、そのときは何も送らない。機体はどのみち
        `move_min_cm` 未満の移動を拒否するし、既に自身の停止距離の内側にいる機体に、
        これ以上前へ進めと求めるべきではない。手順は量 0 のまま列に残す。求められた
        こと自体は記録に残すためである。
        """
        is_forward = step.verb == STEP_FORWARD and step.amount is not None
        if not is_forward:
            return step
        if self.forward_probe is None:
            return step
        ahead_m = self.forward_probe()
        if ahead_m is None:
            return step

        allowed_cm = self._room_to_travel_cm(ahead_m)
        is_within_limit = step.amount <= allowed_cm
        if is_within_limit:
            return step
        return dataclasses.replace(
            step, amount=round(allowed_cm), amount_source="limited")

    def _room_to_travel_cm(self, ahead_m: float) -> float:
        """How far forward the craft may be sent with `ahead_m` of room.

        The move and its own stopping distance have to fit inside the gap:

            travel + stop_distance(speed the travel reaches) <= ahead

        The speed term is what makes this more than a subtraction. A move is
        a position step, so the craft accelerates towards the target and
        then slows for it -- a long move reaches the envelope's ceiling, but
        a SHORT one never does, and charging it the ceiling's stopping
        distance would be wrong in the expensive direction: at 0.5 m/s the
        requirement is 1.8 m, so a wall 1.5 m away would forbid all forward
        motion, including the 1.0 m walls the SILS scenes are built around.
        The craft would simply stop flying rather than fly carefully.

        So the reachable speed is bounded by the travel itself. Over a
        distance `d` the craft must both accelerate and stop, and the coast
        coefficient is the only measured description of how it slows, so
        `d / coast_per_speed_s` is the speed whose stopping distance is
        exactly `d`. Taking the smaller of that and the envelope's ceiling
        is the speed the move can actually reach, and it makes the bound
        self-consistent: the answer never claims room the move would then
        use up getting there.

        `ahead_m` の余地があるとき、機体を前へどれだけ送ってよいか。

        移動と、その移動自身の停止距離が、隙間に収まらねばならない:

            移動距離 + stop_distance(その移動が到達する速度) <= 前方距離

        これを単なる引き算以上のものにしているのが速度の項である。移動は位置の
        ステップなので、機体は目標へ加速し、そして減速する ―― 長い移動は飛行領域の
        上限に達するが、**短い**移動は決して達しない。短い移動に上限での停止距離を
        課すのは、高くつく向きに誤ることである。0.5m/s では所要は 1.8m なので、
        1.5m 先の壁は前進を一切禁じることになり、SILS の場面が拠って立つ 1.0m の壁も
        そこに含まれる。機体は慎重に飛ぶのではなく、単に飛ばなくなる。

        そこで、到達しうる速度を移動距離自身で抑える。距離 `d` の間に機体は加速も
        減速もせねばならず、減速の仕方についての唯一の実測的な記述が惰走係数なので、
        `d / coast_per_speed_s` は「停止距離がちょうど `d` になる速度」である。これと
        飛行領域の上限の小さいほうが、その移動が実際に到達しうる速度であり、これに
        より上限は自己無撞着になる ―― 答えが、移動がそこへ至る過程で使い切る余地を
        主張することは無くなる。
        """
        cfg = self.cfg.forward
        ceiling = self.cfg.envelope.speed_max_mps
        # Solve travel + margin + k*min(travel/k, ceiling) <= ahead for travel.
        # Below the ceiling the speed term is travel/k * k = travel itself, so
        # the gap splits in two; above it the term is the constant k*ceiling.
        # travel + margin + k*min(travel/k, ceiling) <= ahead を travel について
        # 解く。上限より下では速度の項は travel/k * k すなわち travel そのものに
        # なるので隙間は 2 等分され、上限より上では項は定数 k*ceiling になる。
        room_m = ahead_m - cfg.safety_margin_m
        if room_m <= 0.0:
            return 0.0
        travel_m = room_m / 2.0
        reaches_ceiling = (travel_m / cfg.coast_per_speed_s) > ceiling
        if reaches_ceiling:
            stopping_m = min(cfg.safety_margin_m + cfg.coast_per_speed_s * ceiling,
                             cfg.stop_distance_max_m)
            travel_m = ahead_m - stopping_m
        return max(0.0, travel_m * 100.0)

    def _drain_stale_replies(self) -> None:
        """Wait out a reply an earlier command has not delivered yet.

        The vehicle answers every blocking verb, but the answer reaches
        this side through the emulator's stdout, and it can arrive AFTER
        the next command has already gone out. `_await_step` only watches a
        COUNT, so such a late reply satisfies the next step's wait
        immediately: the step is declared finished the moment it starts,
        and `classify_arrival` then correctly reports the craft as having
        stopped short of a target it never flew towards.

        Measured 2026-09-19 (`sf pilot mission line --sils`): `takeoff`'s
        reply arrived 0.7 s after `forward 60` had been sent, and legs 3
        and 4 each "flew" in about a second and were reported `stopped
        short`, which cost two retries apiece and skipped both legs. Under
        FakeJudge the same route took 52.6 s with every leg `as planned`;
        the difference was purely the timing the Jev round trip introduced.

        Bounded, because a reply that is never coming must not stall the
        route: the ceiling is the one the landing already uses for the same
        question (`move_reply_wait_s`).

        直前の指令がまだ返していない応答を待って捨てる。

        機体はブロックする verb すべてに応答するが、その応答はエミュレータの
        stdout を通ってこちらへ届くため、**次の指令を送った後**に届きうる。
        `_await_step` が見ているのは**個数**だけなので、その遅れた応答が次の手順の
        待ちを即座に満たしてしまう。手順は始まった瞬間に「終わった」と宣言され、
        `classify_arrival` は（正しく）「向かってもいない終点の手前で止まった」と
        報告する。

        2026-09-19 実測（`sf pilot mission line --sils`）: `takeoff` の応答が
        `forward 60` 送信の 0.7 秒**後**に届き、区間 3 と 4 はそれぞれ約 1 秒で
        「飛び」、`stopped short` と報告された。その結果それぞれ 2 回やり直し、
        双方とも飛ばされた。同じ経路を FakeJudge で飛ばすと 52.6 秒かかり、全区間が
        `as planned` だった。差は、Jev の往復が持ち込んだ時間だけである。

        上限を設ける。来ない応答が経路を止めてはならないためで、上限は同じ問いに
        ついて着陸が既に使っているもの（`move_reply_wait_s`）と同じにする。
        """
        deadline = time.monotonic() + self.cfg.landing.move_reply_wait_s
        while time.monotonic() < deadline:
            if self._stop.is_set():
                return
            if not self._reply_outstanding():
                return
            time.sleep(self.POLL_S)

    def _reply_outstanding(self) -> bool:
        """Whether the vehicle still owes a reply to something already sent.

        Counted on the LINK rather than on this runner, because a mission
        builds a new runner per leg while the link and its counters span
        the whole flight -- a reply owed by the previous leg is exactly the
        one this must not let the next leg consume.

        A link that cannot say (a recording stand-in in a test) reports
        nothing outstanding, which skips the drain entirely -- correct for
        a link whose commands are never answered at all.

        機体が、既に送った指令に対してまだ応答を返していないか。

        この runner ではなく**リンク**で数える。ミッションは区間ごとに新しい
        runner を作る一方、リンクとその計数は飛行全体にまたがるからである。次の
        区間に消費させてはならないのは、まさに前の区間が負っている応答である。

        判定できないリンク（試験の記録用の代役）は「未応答なし」を返し、待ちは
        丸ごと省かれる。そもそも応答が返らないリンクにとってそれが正しい。
        """
        return getattr(self.link, "replies_outstanding", 0) > 0

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
             scene=None, on_step=None, on_cycle=None,
             on_decisions=None) -> SayOutcome:
    """Fly the plan's steps under the use-(1) safety layer, and report.

    The loop below is `sf pilot run`'s loop with one addition: it also
    watches the step runner, and it hands the runner a stop as soon as the
    Arbiter's verdict is anything other than carrying on. The Arbiter's
    own rules are untouched -- a plan being under way is not a reason to
    weaken them.

    `on_cycle(sample, now)` is called once per monitor cycle when given; the
    live browser view uses it to draw the aircraft. It must not block: it
    runs inside the 50Hz loop.

    計画の手順を用途①の安全層の下で飛ばし、結果を返す。

    下のループは `sf pilot run` のループに 1 つ足しただけである: 手順の進行も
    見張り、Arbiter の判定が「続行」以外になった時点で進行に停止を伝える。
    Arbiter の規則自体には手を触れない — 計画が進行中であることは、規則を
    緩める理由にならない。

    `on_cycle(sample, now)` は、与えられていれば監視周期ごとに 1 回呼ばれる。
    ブラウザのライブ表示が機体を描くのに使う。50Hz ループの中で動くため、
    ブロックしてはならない。

    `on_decisions(rows)` is called once, before the first cycle, with the
    list the Pilot appends its decisions to.
    `on_decisions(rows)` は最初の周期の前に 1 度だけ呼ばれ、Pilot が判断を
    追記していく配列を渡す。
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
                        speed_probe=pilot.horizontal_speed,
                        forward_probe=pilot.forward_distance)
    outcome = SayOutcome()
    started = time.monotonic()
    period = 1.0 / config.monitor_hz
    # Hand the decision list over before the first cycle, so a watcher's
    # totals count from the first decision rather than from whenever it
    # first looked.
    # 最初の周期の前に判断の配列を渡す。見ている側の現在値が、最初に覗いた
    # 時点からではなく最初の判断から数えられるようにするためである。
    if on_decisions is not None:
        on_decisions(pilot.decisions)
    runner.start()

    while not runner.done:
        cycle_start = time.monotonic()
        pilot.step()
        link.hold_sticks_neutral()
        # Drive the scene on the same cycle as everything else. A scene that
        # places obstacles needs a cycle to place them on, and the first one
        # is the earliest at which the Plant is certainly attached.
        # 他の全てと同じ周期で場面を駆動する。障害物を置く場面には置くための周期が
        # 要り、最初の周期が Plant の接続を確実に待てる最も早い時点である。
        if scene is not None and scene.drive is not None:
            scene.drive(link, cycle_start - started, config.mission.time_limit_s)
        if on_cycle is not None:
            on_cycle(pilot.monitor.latest_sample, cycle_start)
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
    outcome.decision_rows = list(pilot.decisions)
    outcome.flown_s = time.monotonic() - started
    _finish(link, pilot, outcome, config)
    return outcome


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
    # A retreat interrupts for the same reason a stop does, and more so: the
    # craft is being driven AWAY from the obstacle, so letting the sequence
    # send its next move would command it back towards the thing it is
    # escaping.
    # 後退も停止と同じ理由で中断させる。むしろ理由は強い。機体は障害物から
    # **離される**最中であり、ここで列の次の移動を送れば、逃れている当のものへ
    # 向かって指令することになるからである。
    is_unsafe = verdict.action in (VERDICT_LAND, VERDICT_STOP, VERDICT_BACK)
    # Only a hold Jev actually CHOSE stops the sequence. The Arbiter also
    # reports a hold derived from an answer it was not confident enough to
    # act on (arbiter.py), and that is not the model asking to wait -- such
    # an answer often PREFERRED carrying on. Treating it as an interrupt
    # ended a mission at its last leg on answers of continue 0.63-0.72
    # against hold 0.23-0.30 (measured 2026-09-19).
    # 手順を止めるのは、Jev が**選んだ**待機だけである。Arbiter は、確信度が
    # 足りず行動の根拠にしなかった答えから導いた待機も報告するが（arbiter.py）、
    # それはモデルが待てと言っているのではない。そうした答えはむしろ継続を
    # 選好していることが多い。これを中断として扱った結果、continue 0.63〜0.72 対
    # hold 0.23〜0.30 の答えでミッションが最後の区間で終わった（2026-09-19 実測）。
    is_chosen_hold = (verdict.action == VERDICT_HOVER
                      and verdict.source == "judge"
                      and verdict.detail.get("chosen_hold", True))
    if not (is_unsafe or is_chosen_hold):
        return ""
    return f"{verdict.action}: {verdict.reason}"


def _finish(link, pilot, outcome: SayOutcome, config) -> None:
    """Leave the aircraft somewhere safe, whatever happened.

    An interrupted sequence has stopped mid-move with the vehicle still
    holding the target it was given, so the aircraft is brought to rest and
    then landed. Landing rather than hovering is the design's rule for a
    situation that is not resolving itself (arbiter.py), and an instruction
    that was cut short is exactly that.

    The landing goes through the Executor, like every other landing in this
    package, so the craft settles before the descent begins -- the firmware
    stops holding horizontal position for the whole descent, so a `land`
    sent with speed still on slides across the floor (landing.py).

    何が起きたかに関わらず、機体を安全な状態にして終える。

    中断した手順は移動の途中で止まっており、機体は与えられた目標を保持した
    ままである。そこで機体を静定させてから着陸させる。解消しない状況で待機では
    なく着陸を選ぶのは設計の規則であり（arbiter.py）、打ち切られた指示はまさに
    その状況である。

    着陸は本パッケージの他の全ての着陸と同じく Executor を通す。降下が始まる前に
    機体を静定させるためである — ファームは降下のあいだ水平の位置保持をやめる
    ので、速度を残したまま送った `land` は床の上を滑る（landing.py）。
    """
    if outcome.landed:
        _settle_landing(link, pilot, outcome, config)
        return
    outcome.landed = True
    land_land = _land_verdict(outcome.interrupt_reason)
    pilot.executor.apply(land_land)
    _settle_landing(link, pilot, outcome, config)


def _land_verdict(interrupt_reason: str):
    """A verdict asking for an ordinary, unhurried landing.

    `source` is what tells the Executor how long it may settle, and this
    landing is never the urgent kind: the sequence ended or was cut short
    by a rule that had already decided the flight should stop, not by a
    battery about to give out. The immediate safety rules reach the
    Executor on their own path, already marked urgent.

    通常の、急がない着陸を求める判定。

    Executor が静定に許す時間を決めるのは `source` である。この着陸は緊急の類では
    ない。手順が終わったか、「飛行を止めるべき」と既に判断した規則が打ち切ったの
    であって、尽きかけた電池が理由ではない。即時安全則は自身の経路で、緊急の印を
    付けて Executor に届く。
    """
    return Verdict(
        action=VERDICT_LAND,
        reason=interrupt_reason or "手順を完了したため着陸",
        source="say",
    )


def _settle_landing(link, pilot, outcome: SayOutcome, config) -> None:
    """Keep the loop turning until the landing is sent, then let it descend.

    The approach is a state machine advanced one cycle at a time, so it
    needs cycles: returning as soon as it was asked for would leave the
    craft hovering with a landing half-issued. The ceiling is the
    approach's own, plus the grace the descent itself needs.

    着陸が送られるまでループを回し続け、その後に降下を待つ。

    着陸前手順は 1 周期ずつ進める状態機械なので、周期が要る。求めた直後に戻れば、
    着陸を出しかけたまま機体を浮かせたままにしてしまう。上限は手順自身の上限に、
    降下そのものに要る猶予を足したものである。
    """
    period = 1.0 / config.monitor_hz
    deadline = time.monotonic() + config.landing.settle_max_s + config.landing.move_reply_wait_s
    while pilot.executor.approach is not None and time.monotonic() < deadline:
        pilot.step()
        link.hold_sticks_neutral()
        time.sleep(period)
    time.sleep(config.sils.land_grace_s)
