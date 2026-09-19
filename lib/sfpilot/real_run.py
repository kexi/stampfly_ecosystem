"""
sfpilot.real_run - take off, hover, watch, land, on a REAL aircraft.
sfpilot.real_run - 実機で離陸し、ホバリングし、見張り、着陸する。

This is P5's first stage and nothing more: `sf pilot run --real` flies use
(1) — watching the situation and judging safety — and nothing else.
`say`, `mission` and `explore` are refused on hardware, because each of
them commands the aircraft to MOVE and none of the moves has been flown on
hardware. A hover is the one flight whose every command has been:
`command`, `takeoff`, `rc 0 0 0 0`, `stop`, `land`.

これは P5 の第 1 段階であり、それ以上ではない。`sf pilot run --real` が飛ばすのは
用途①（状況の監視と安全判断）だけである。`say`・`mission`・`explore` は実機では
拒否する。いずれも機体に**移動**を指令するものであり、その移動はどれも実機で
飛ばされていないからである。ホバリングだけが、すべての指令が実機で確かめられて
いる飛行である: `command`・`takeoff`・`rc 0 0 0 0`・`stop`・`land`。

The loop is `sf pilot run --sils`'s loop with three things added, each of
which exists because of a fact §2 or §4.8 records about the hardware:

  - a telemetry-silence rule, because **the vehicle does not land itself
    when the PC goes quiet** (§2) and the broadcast loses 42% of its
    packets (§4.8.2)
  - a watchdog on a thread of its own, because a loop that hangs cannot
    detect that it has
  - the keyboard, because the operator standing next to the aircraft is
    the fastest judge in the system and needs one keystroke, not a menu

ループは `sf pilot run --sils` のものに 3 つを足しただけである。いずれも、機体に
ついて §2 か §4.8 が記録している事実ゆえに存在する:

  - テレメトリ無音の規則。**PC が黙っても機体は自分で着陸しない**（§2）うえ、
    放送はパケットの 42% を落とす（§4.8.2）ため
  - 専用スレッドの番人。止まったループは、自分が止まったことを検出できないため
  - キーボード。機体の傍らに立つ操作者がこの系で最も速い判断者であり、必要なのは
    献立ではなく 1 打だからである
"""

import time
from dataclasses import dataclass, field

from .config import DEFAULT_CONFIG
from .pilot import Pilot
from .preflight import run_preflight
from .safety import KEY_HELP_LINE, KeyListener, LandGuard, LoopWatchdog, apply_key

# Why the flight waits after `takeoff` before judging anything: the
# firmware's auto-takeoff climbs to whatever altitude it reaches and then
# holds THAT (api_task.cpp `cmdTakeoff` seeds the guidance target from the
# pose at FLYING). The Monitor adopts the held altitude as its target once
# the climb has levelled off (`_adopt_target_if_unset`), and judging before
# then would classify a normal climb as an altitude deviation. The figure
# is `SilsConfig.takeoff_settle_s`, which is where the wait is already
# defined -- the climb is the firmware's, not the simulator's, so the same
# number applies.
# 離陸の後、何かを判定する前に待つ理由: ファームの自動離陸は到達した高度をその
# まま保持する（api_task.cpp の `cmdTakeoff` は誘導目標を FLYING 到達時の姿勢から
# 作る）。Monitor は上昇が水平になった時点でその保持高度を目標として採用する
# （`_adopt_target_if_unset`）ので、それ以前に判定させると通常の上昇を高度の逸脱と
# 区分してしまう。値は `SilsConfig.takeoff_settle_s` を使う。待ちは既にそこで
# 定義されており、上昇はシミュレータのものではなくファームのものなので、同じ値が
# そのまま当てはまる。


class TakeoffRefused(RuntimeError):
    """The vehicle did not take off, and said so.

    Its own class rather than a bare RuntimeError so the caller can report a
    refusal as what it is -- a flight that never began -- instead of as a
    crash in the pilot. LandGuard still lands on it, which costs nothing on
    a machine that never left the floor and is right if it somehow did.

    機体が離陸せず、そう答えた。

    素の RuntimeError にしないのは、呼び出し側が拒否を「始まらなかった飛行」
    として報告できるようにするためである（自動操縦の異常終了としてではなく）。
    LandGuard はこれでも着陸を送るが、床にいる機体には無害であり、万一浮いて
    いたのなら正しい。
    """


@dataclass
class RealOutcome:
    """How the flight ended and what it measured.
    飛行がどう終わり、何を測ったか。"""

    pilot: object = None
    preflight: object = None
    flown_s: float = 0.0
    took_off: bool = False
    # Why the flight ended, in the operator's words. One of: the duration
    # elapsed, a landing was decided, telemetry went silent, the watchdog
    # fired, a key was pressed, an exception.
    # 飛行が終わった理由。操作者向けの語で。時間の経過・着陸の判断・テレメトリの
    # 途絶・番人の発火・キー操作・例外のいずれか。
    ended_because: str = ""
    landed: bool = False
    land_acknowledged: bool = False
    # Every command line actually transmitted, in order. Not the Executor's
    # list, which also records what it WOULD have sent: this is the wire.
    # 実際に送出した指令行を順に並べたもの。Executor の配列は「送ったはずの
    # もの」も記録するが、こちらは電線に出たものである。
    commands: list = field(default_factory=list)
    keys: list = field(default_factory=list)
    # Telemetry delivery over the flight itself, which is the number §4.8.2
    # measured on the bench and nobody has measured in the air.
    # 飛行そのものでのテレメトリ到達率。§4.8.2 が机上で測り、空中では誰も測って
    # いない数値である。
    samples_received: int = 0
    samples_expected: int = 0
    # PC<->vehicle round trips, measured again during the preflight and
    # carried here so the summary has them without re-reading the report.
    # PC↔機体の往復時間。飛行前点検で測ったものを、集計が報告を読み直さずに
    # 済むようここへ運ぶ。
    vehicle_rtt: dict = field(default_factory=dict)

    @property
    def delivery_rate(self) -> float:
        """Received over expected, or 0 when nothing was expected.
        期待に対する受信の割合。期待が 0 なら 0。"""
        if not self.samples_expected:
            return 0.0
        return self.samples_received / self.samples_expected


class RecordingLink:
    """Wraps a Link and remembers every line that went out.

    A wrapper rather than a counter inside `RealLink`, because what the
    summary must show is the sequence a person can check against
    `tello-api-reference.md` -- "these are the commands that reached the
    aircraft, in this order" -- and the one place every command passes
    through is the boundary between this package and the link.

    Link を包み、送出した行をすべて覚える。

    `RealLink` の中の計数ではなく包むものにする。集計が示さねばならないのは、
    人が `tello-api-reference.md` と突き合わせられる列 ――「これらの指令が、
    この順に機体へ届いた」―― であり、すべての指令が通る唯一の場所が、本
    パッケージとリンクの境目だからである。
    """

    def __init__(self, link):
        self.link = link
        self.sent: list = []
        # Counted here rather than inside the Monitor, because the Monitor
        # trims its history to the trend windows -- a count taken from it
        # would report how much history it currently holds, not how much
        # arrived. This is the one place every sample passes through.
        # Monitor の中ではなくここで数える。Monitor は履歴を傾向の窓に刈り込む
        # ので、そこから取った数は「いま保持している履歴の量」であって「届いた
        # 量」ではない。すべてのサンプルが通る唯一の場所がここである。
        self.samples_read = 0

    def read_samples(self) -> list:
        samples = self.link.read_samples()
        self.samples_read += len(samples)
        return samples

    def send_rc(self, a: int, b: int, c: int, d: int) -> None:
        self._record(f"rc {a} {b} {c} {d}")
        self.link.send_rc(a, b, c, d)

    def send_command(self, line: str) -> None:
        self._record(line)
        self.link.send_command(line)

    def priority(self, line: str) -> None:
        self._record(line)
        self.link.priority(line)

    def send(self, line: str, timeout: float):
        self._record(line)
        return self.link.send(line, timeout)

    def close(self) -> None:
        self.link.close()

    @property
    def reply_count(self) -> int:
        return getattr(self.link, "reply_count", 0)

    @property
    def replies_outstanding(self) -> int:
        return getattr(self.link, "replies_outstanding", 0)

    def _record(self, line: str) -> None:
        """Keep the line, collapsing a repeated `rc` into a count.

        A 20-second hover sends about 400 identical `rc 0 0 0 0` lines, and
        400 identical rows tell the reader nothing 1 row and a count does
        not. Every other verb is kept individually, because those are the
        ones whose ORDER is the thing being checked.

        行を残す。繰り返しの `rc` は回数にまとめる。

        20 秒のホバリングは同一の `rc 0 0 0 0` を約 400 行送る。同じ行 400 個が
        読み手に伝えることは、1 行と回数が伝えること以上には無い。他の verb は
        1 件ずつ残す。確かめたいのは、そちらの**順序**だからである。
        """
        is_repeat = bool(self.sent) and self.sent[-1][0] == line
        if is_repeat:
            self.sent[-1][1] += 1
            return
        self.sent.append([line, 1])

    def forget_commands(self) -> None:
        """Start the recorded sequence again from here.

        Used once, at the boundary between the preflight and the flight
        (see `fly_real`). The sample counter is deliberately NOT reset:
        that one is read as a difference at the start of the hover, so it
        needs its history.

        記録している列を、ここから取り直す。

        使うのは 1 度だけ、飛行前点検と飛行の境目である（`fly_real` 参照）。
        サンプルの計数は意図して戻さない。あちらはホバリング開始時点との差として
        読むので、履歴が要る。
        """
        self.sent = []

    def command_sequence(self) -> list:
        """The commands as "line" or "line x N" strings, in order.
        指令を「行」または「行 x N」の文字列として順に並べたもの。"""
        return [line if count == 1 else f"{line} x{count}"
                for line, count in self.sent]


def preflight_only(link, judge, config=DEFAULT_CONFIG, trace=None):
    """Run the checks and stop. Nothing takes off.

    The dry-run entry point (`--preflight-only`). It exists so the checks
    can be exercised against a real aircraft before anyone is willing to
    fly one -- which is exactly the state P5's first stage is in.

    点検だけを走らせて終える。何も離陸しない。

    予行の入口（`--preflight-only`）である。誰かが実機を飛ばす気になる前に、
    実機に対して点検を働かせられるようにするために置く —— P5 の第 1 段階が
    まさにその状態にある。
    """
    report = run_preflight(link, judge, config)
    if trace is not None:
        _write_preflight(trace, report)
    return report


def fly_real(link, judge, config=DEFAULT_CONFIG, trace=None, duration_s=None,
             on_cycle=None, on_decisions=None, on_event=None,
             key_listener=None) -> RealOutcome:
    """Preflight, take off, hover under the judging layers, land.

    Every exit from this function lands the aircraft. That is not a
    property of the code below being careful -- it is `LandGuard`, which
    holds the exit path and the two signals and sends `land` from whichever
    of them fires (safety.py).

    飛行前点検・離陸・判断層の下でのホバリング・着陸。

    この関数からのどの出口も機体を着陸させる。それは下のコードが注意深いことの
    帰結ではない。終了経路と 2 つのシグナルを押さえ、そのどれが発火しても `land` を
    送る `LandGuard` の帰結である（safety.py）。
    """
    announce = on_event or (lambda _message: None)
    duration_s = _duration(duration_s, config)
    recording_link = RecordingLink(link)
    outcome = RealOutcome()

    guard = LandGuard(recording_link, config,
                      on_land=lambda reason: announce(f"land: {reason}"))
    watchdog = LoopWatchdog(guard, config)
    keys = key_listener if key_listener is not None else KeyListener()

    guard.install()
    try:
        is_clear_to_fly = _passed_preflight(recording_link, judge, config,
                                            trace, outcome)
        if not is_clear_to_fly:
            # Nothing has taken off, so there is nothing to land, and the
            # guard is released below without ever having sent anything.
            # 何も離陸していないので着陸させるものも無い。番人は下で、一度も
            # 何かを送ることなく解放される。
            return outcome
        _take_off(recording_link, config, announce)
        outcome.took_off = True
        keys.start()
        _hover(recording_link, judge, config, trace, duration_s, outcome,
               guard, watchdog, keys, announce, on_cycle, on_decisions)
        return outcome
    finally:
        watchdog.stop()
        keys.stop()
        _land_if_still_up(guard, outcome)
        outcome.landed = guard.landed
        outcome.land_acknowledged = guard.acknowledged
        if not outcome.ended_because and guard.reason:
            outcome.ended_because = guard.reason
        outcome.commands = recording_link.command_sequence()
        guard.release()


def _land_if_still_up(guard, outcome) -> None:
    """Land the aircraft unless some other layer already did.

    Called from `fly_real`'s `finally`, so the landing goes out on every
    path that reaches it without one -- the duration elapsing, an exception
    on the way through, an early `return`.

    `LandGuard` only knows about the landings IT sent, so a landing the
    Executor's approach already completed has to be DECLARED, or this puts
    a second `land` on top of a descent that is under way. It is declared
    rather than worked out here because the guard is the one thing that
    must work on every exit path, including ones where the Executor is
    gone: giving it a rule about somebody else's state would make it depend
    on that somebody still being there.

    機体をまだ降ろしていなければ降ろす。別の層が既に降ろしていれば何もしない。

    `fly_real` の `finally` から呼ぶ。着陸せずにそこへ至るすべての経路 ―― 時間の
    経過、途中の例外、早い `return` ―― で着陸が出るようにするためである。

    `LandGuard` が知っているのは**自分が**送った着陸だけなので、Executor の手順が
    既に完了させた着陸は**申告**せねばならない。さもなければ、進行中の降下の上に
    2 通目の `land` を置くことになる。ここで推し量らず申告にするのは、番人が、
    Executor がもう居ない経路も含めあらゆる出口で働かねばならない唯一のものだから
    である。他人の状態についての規則を持たせれば、その他人がまだ居ることに依存する。
    """
    if _already_landed(outcome):
        guard.note_landed("着陸前手順が着陸を完了 / the approach landed it")
    if outcome.took_off:
        guard.land_now("飛行を終えるため着陸 / landing at the end of the flight")


def _already_landed(outcome) -> bool:
    """Whether the Executor's approach has sent the landing itself.

    True only when the approach both STARTED and FINISHED: `landing` alone
    turns True when it begins, with the craft still settling and no `land`
    transmitted yet (see `_reason_to_stop`).

    Executor の着陸前手順が、自分で着陸を送り終えたかどうか。

    手順が**始まり**かつ**終わった**ときだけ True になる。`landing` だけなら手順の
    開始時点で True であり、そのとき機体はまだ静定中で `land` は出ていない
    （`_reason_to_stop` 参照）。
    """
    pilot = outcome.pilot
    if pilot is None:
        return False
    executor = pilot.executor
    return executor.landing and executor.approach is None


def _passed_preflight(link, judge, config, trace, outcome) -> bool:
    """Run the checks, record them on the outcome, and say whether to fly.

    The recorded command sequence is restarted here when the checks pass.
    The preflight's ten `command` probes are not part of the flight, and
    leaving them in would collapse with the flight's own `command` into one
    "command x11" row -- which reads as though the flight opened by sending
    it eleven times. What the summary is for is the sequence a person
    checks against the API reference, so it starts where the flight does;
    the preflight's own sends are reported by its own check, with timings.

    点検を走らせ、結果を outcome に記録し、飛んでよいかを返す。

    点検を通ったとき、記録している指令列をここで取り直す。飛行前点検の 10 回の
    `command` は飛行の一部ではなく、残せば飛行自身の `command` と 1 行
    「command x11」にまとまる ―― 飛行が 11 回送って始まったように読める。集計の
    目的は、人が API リファレンスと突き合わせる列を示すことなので、飛行が始まる
    ところから始める。点検の送信は、点検自身の項目が時間とともに報告する。
    """
    report = run_preflight(link, judge, config)
    outcome.preflight = report
    outcome.vehicle_rtt = _rtt_of(report)
    if trace is not None:
        _write_preflight(trace, report)
    if not report.ok:
        outcome.ended_because = "飛行前点検に通らなかった / preflight failed"
        return False
    link.forget_commands()
    return True


def _duration(requested, config) -> float:
    """The flight's length, clamped to the real ceiling.

    Clamped rather than refused: a `--duration 120` is a request for a
    longer flight, not a mistake, and the ceiling is what this stage is
    willing to give. Refusing would make the operator re-type the command
    to get the flight they can have.

    飛行の長さ。実機の上限で切り詰める。

    拒否せず切り詰める。`--duration 120` は「もっと長く飛ばしたい」という要望で
    あって誤りではなく、上限はこの段階が応じられる範囲である。拒否すれば、
    得られる飛行を得るために操作者がコマンドを打ち直すことになる。
    """
    cfg = config.real
    if requested is None:
        return cfg.default_duration_s
    return max(0.0, min(float(requested), cfg.max_duration_s))


def _take_off(link, config, announce) -> None:
    """`command`, `takeoff`, then wait out the climb. Raises if it refuses.

    The wait is not a courtesy: the Monitor adopts its altitude target from
    the hover the climb settles into, and judging during the climb would
    classify it as a deviation (see the note at the top of this module).

    `takeoff` is sent with `send`, which waits for the vehicle's reply, not
    with the fire-and-forget `send_command`. Why not fire-and-forget: the
    vehicle refuses to ARM on a low pack and answers `error takeoff timeout`
    (api_task.cpp, kTakeoffTimeoutMs = 12 s). Unheard, this program would
    watch a machine sitting on the floor and report a flight. Preflight no
    longer judges the voltage -- the vehicle decides -- so hearing the
    vehicle's decision is how that decision reaches us.

    `command`・`takeoff` を送り、上昇を待ち切る。拒まれたら例外を投げる。

    この待ちは気遣いではない。Monitor は、上昇が落ち着いたホバリングから高度の
    目標を採用するのであり、上昇中に判定させればそれを逸脱と区分してしまう
    （本モジュール冒頭の注記を参照）。

    `takeoff` は撃ちっぱなしの `send_command` ではなく、応答を待つ `send` で
    送る。撃ちっぱなしにしない理由: 電圧の低いパックでは機体が ARM を拒み、
    `error takeoff timeout` を返す（api_task.cpp、kTakeoffTimeoutMs = 12 秒）。
    それを聞かなければ、この処理は床に置かれたままの機体を監視し、飛行したと
    報告する。飛行前点検はもう電圧を判定せず機体に委ねているのだから、機体の
    判断がこちらへ届く経路はこの応答である。
    """
    announce("command を送信 / entering SDK mode")
    link.send("command", config.real.command_timeout_s)
    announce("takeoff を送信 / taking off")
    status, text = link.send("takeoff", config.real.takeoff_timeout_s)
    took_off = status == "ok"
    if not took_off:
        raise TakeoffRefused(
            f"機体が離陸しなかった（{status}: {text or '応答なし'}）。"
            f"電池・ARM の可否・機体の状態を確認する"
            f" / the vehicle did not take off ({status}: {text or 'no reply'})"
        )
    settle_s = config.sils.takeoff_settle_s
    announce(f"上昇の静定を {settle_s:g}s 待つ / letting the climb settle")
    deadline = time.monotonic() + settle_s
    while time.monotonic() < deadline:
        # Drained so the Monitor's first cycle does not fold in the whole
        # climb at once, which would put a climb's worth of altitude trend
        # into the first classification.
        # 吸い出しておく。Monitor の最初の周期が上昇の全体を一度に取り込み、
        # 最初の区分に上昇ぶんの高度傾向を入れてしまわないようにするため。
        link.read_samples()
        time.sleep(0.02)


def _hover(link, judge, config, trace, duration_s, outcome, guard, watchdog,
           keys, announce, on_cycle, on_decisions) -> None:
    """The 50Hz loop: judge, watch for silence, read keys, until it ends.
    50Hz のループ: 判定し、無音を見張り、キーを読み、終わるまで回す。"""
    pilot = Pilot(link, judge, config, trace=trace)
    if on_decisions is not None:
        on_decisions(pilot.decisions)

    period = 1.0 / config.monitor_hz
    # The count at the start of the hover, so the delivery rate covers the
    # flight and not the preflight's own three seconds of listening.
    # ホバリング開始時点の計数。到達率が、飛行前点検の 3 秒の聴取ではなく飛行を
    # 対象とするようにするためである。
    samples_at_start = link.samples_read
    started = time.monotonic()
    last_sample_at = started
    watchdog.start()
    announce(f"{duration_s:g}s ホバリングして監視する / hovering and watching")
    announce_keys = True

    while True:
        cycle_start = time.monotonic()
        if _is_time_up(pilot, cycle_start - started, duration_s):
            outcome.ended_because = "所定の時間が経過 / the flight time elapsed"
            break

        pilot.step()
        watchdog.beat()
        if announce_keys:
            announce(KEY_HELP_LINE)
            announce_keys = False

        silence = _silence(pilot, cycle_start, last_sample_at)
        last_sample_at = silence["last_sample_at"]
        reason = _reason_to_stop(pilot, silence, keys, guard, watchdog, link,
                                 outcome, announce)
        if reason:
            outcome.ended_because = reason
            break

        if on_cycle is not None:
            on_cycle(pilot.monitor.latest_sample, cycle_start)

        slack = period - (time.monotonic() - cycle_start)
        if slack > 0:
            time.sleep(slack)

    _record_flight(outcome, pilot, config,
                   flown_s=time.monotonic() - started,
                   samples=link.samples_read - samples_at_start)


def _record_flight(outcome, pilot, config, flown_s: float, samples: int) -> None:
    """Put what the hover measured onto the outcome.

    `samples_expected` is what 50 Hz would have delivered over the time
    ACTUALLY flown, so the delivery rate is measured against this flight
    rather than against the duration that was asked for -- a flight that
    ended early would otherwise report a rate diluted by the seconds it
    never spent flying.

    ホバリングが測ったものを outcome に載せる。

    `samples_expected` は、**実際に飛んだ**時間に対して 50Hz なら届いていたはずの
    数である。到達率を、求めた長さではなくこの飛行に対して測るためである。さもな
    ければ、早く終わった飛行は、飛びもしなかった秒数で薄めた到達率を報告する。
    """
    outcome.pilot = pilot
    outcome.samples_received = samples
    outcome.flown_s = flown_s
    outcome.samples_expected = max(1, round(flown_s * config.monitor_hz))


def _is_time_up(pilot, elapsed_s: float, duration_s: float) -> bool:
    """Whether the hover has run its length, with a landing allowed to finish.

    A landing already under way runs past the duration. The flight's length
    bounds how long it HOVERS, not how abruptly it comes down: cutting the
    loop mid-approach would hand `fly_real`'s `finally` an unsettled
    aircraft to send a raw `land` to, which is the slide `landing.py`
    measures at 0.45 m across the floor (0.000 m from a settled hover). The
    approach carries its own ceilings (`settle_max_s`,
    `urgent_settle_max_s`), so this cannot wait indefinitely.

    ホバリングが所定の長さを終えたか。ただし着陸は終わらせてやる。

    既に始まっている着陸は、所定の時間を過ぎても走り切らせる。飛行の長さが上限を
    与えるのは**ホバリング**の長さであって、降り方の急さではない。手順の途中で
    ループを切れば、静定していない機体を `fly_real` の `finally` に渡して素の
    `land` を送らせることになる ―― `landing.py` が床を 0.45m 滑ると実測している
    ものである（静定したホバリングからは 0.000m）。手順自身が上限を持つ
    （`settle_max_s`・`urgent_settle_max_s`）ので、際限なく待つことはない。
    """
    is_landing_in_progress = pilot.executor.approach is not None
    return elapsed_s >= duration_s and not is_landing_in_progress


def _reason_to_stop(pilot, silence, keys, guard, watchdog, link, outcome,
                    announce) -> str:
    """Why this cycle should be the last, or "" to carry on.

    Ordered by who is most entitled to end the flight. The operator's key
    comes first, because a person who has decided the aircraft must stop
    should not wait behind a rule. The silence rule comes next, because it
    is the one §2 says the FIRMWARE does not have -- the vehicle holds
    position indefinitely when the PC goes quiet, so a PC that has lost
    sight of the aircraft must end the flight itself rather than leave it
    hovering unwatched. The last two report a landing that some other layer
    has already begun.

    この周期を最後にすべき理由。続けるなら ""。

    飛行を終わらせる資格の順に並べる。操作者のキーが最初である。機体を止めねば
    ならないと判断した人が、規則の後ろで待たされるべきではない。次が無音の規則で、
    これは §2 が「**ファーム**は持たない」と言っているものである ―― PC が黙っても
    機体は位置を保ち続けるので、機体を見失った PC は、見張られないまま浮かせて
    おくのではなく、自分で飛行を終わらせねばならない。最後の 2 つは、別の層が既に
    始めた着陸を報告するだけである。
    """
    key_reason = _key_action(keys, guard, link, outcome, announce)
    if key_reason:
        return key_reason

    if silence["too_long"]:
        guard.land_now(
            f"テレメトリが {silence['silent_s']:.1f}s 途絶えたため着陸 "
            f"/ telemetry silent for {silence['silent_s']:.1f}s"
        )
        return "テレメトリ途絶 / telemetry went silent"

    if watchdog.fired:
        return "番人が監視ループの停止を検出 / watchdog fired"

    # The judging layer's landing ends the loop only once its approach has
    # actually SENT the `land`, not when it started.
    #
    # `executor.landing` turns True the moment `LandingApproach` BEGINS --
    # at which point only `stop` has gone out and the craft is still being
    # settled (measured: stage `settle`, nothing but `stop` transmitted).
    # Leaving here on that flag would end the loop mid-approach, and
    # `fly_real`'s `finally` would then send a raw `land` of its own,
    # skipping exactly the settling that `landing.py` exists to perform.
    # That module measured what such a landing costs: 0.45 m of slide
    # across the floor against 0.000 m from a settled hover.
    #
    # So the loop keeps turning while the approach runs -- each cycle
    # advances it through `Executor.apply` -- and leaves when the approach
    # is done. `_hover`'s caller then finds the flight already landed and
    # `land_now` passes through idempotently.
    #
    # 判断層の着陸は、着陸前手順が実際に `land` を**送り終えた**ときにだけ
    # ループを終える。始めた時点では終えない。
    #
    # `executor.landing` は `LandingApproach` が**始まった**瞬間に True になる。
    # その時点で出ているのは `stop` だけで、機体はまだ静定の最中である（実測:
    # 段階は `settle`、送出は `stop` のみ）。このフラグで抜ければ手順の途中で
    # ループが終わり、`fly_real` の `finally` が自前の素の `land` を送る ――
    # まさに `landing.py` が行うために存在する静定を飛ばして、である。その
    # 着陸の代償は同モジュールが実測している: 静定したホバリングからの 0.000m に
    # 対し、床を 0.45m 滑る。
    #
    # そこで、手順が走っている間はループを回し続け（毎周期 `Executor.apply` が
    # 手順を進める）、手順が終わってから抜ける。`_hover` の呼び出し元は着陸済みの
    # 飛行を見ることになり、`land_now` は冪等にそのまま通り抜ける。
    has_landed = pilot.executor.landing and pilot.executor.approach is None
    if has_landed:
        return "判断層が着陸を決定 / the judging layer landed"
    return ""


def _key_action(keys, guard, link, outcome, announce) -> str:
    """Carry out every key pressed since the last cycle.

    Returns a reason to end the flight, or "" to carry on. `h` does not end
    it: holding is what the aircraft already does between judgements, so
    the key asks for the next cycle to hover rather than for the flight to
    stop.

    前の周期以降に押されたキーをすべて実行する。

    飛行を終える理由を返す。続けるなら ""。`h` は飛行を終わらせない。待機は
    判断と判断の間に機体が既に行っていることであり、このキーが求めるのは、
    飛行の停止ではなく次の周期を待機にすることである。
    """
    for key in keys.drain():
        action = apply_key(key, guard, link, on_hold=None)
        if not action:
            continue
        outcome.keys.append(action)
        announce(f"キー入力 / key: {key} → {action}")
        if action == "land":
            return "操作者が l を押した / operator pressed l"
        if action == "emergency":
            return "操作者が e を押した（emergency） / operator pressed e"
    return ""


def _silence(pilot, now: float, last_sample_at: float) -> dict:
    """How long telemetry has been silent, and whether that is too long.

    The rule the FIRMWARE does not have (§2): the vehicle holds position
    indefinitely when the PC goes quiet, so if this side stops seeing the
    aircraft it must end the flight itself rather than leave it hovering
    unwatched.

    テレメトリが何秒途絶えているか、そしてそれが長すぎるか。

    **ファームが持たない**規則である（§2）。PC が黙っても機体は位置を保ち続ける
    ので、こちら側が機体を見失ったなら、見張られないまま浮かせておくのではなく、
    自分で飛行を終わらせねばならない。
    """
    from .config import DEFAULT_CONFIG

    config = getattr(pilot, "cfg", DEFAULT_CONFIG)
    sample = pilot.monitor.latest_sample
    if sample is not None and sample.get("t", 0.0) > last_sample_at:
        last_sample_at = sample["t"]
    silent_s = now - last_sample_at
    return {
        "last_sample_at": last_sample_at,
        "silent_s": silent_s,
        "too_long": silent_s > config.real.telemetry_silence_land_s,
    }


def _rtt_of(report) -> dict:
    """The two round trips the preflight measured, keyed for the summary.

    Taken from the report rather than measured again: the preflight's
    measurement is the one the decision to fly was made on, so the summary
    should show that one and not a second opinion taken later.

    飛行前点検が測った 2 つの往復時間を、集計のために取り出す。

    測り直さず報告から取る。飛ぶという判断の根拠になったのは点検の測定であり、
    集計が示すべきはそれであって、後から取った別の見解ではない。
    """
    from .preflight import CHECK_JEV, CHECK_VEHICLE_LINK

    out: dict = {}
    for name, key in ((CHECK_VEHICLE_LINK, "vehicle"), (CHECK_JEV, "jev")):
        check = report.get(name)
        if check is not None:
            out[key] = dict(check.measured)
    return out


def _write_preflight(trace, report) -> None:
    """Put the preflight report in the trace, as its own line.

    Written through the trace's own file handle rather than as a decision
    row: it is not a decision, and shaping it like one would make a `jq`
    that counts decisions count this too.

    飛行前点検の報告を、記録に 1 行として残す。

    判断の行としてではなく、記録自身のファイルへ書く。これは判断ではなく、判断の
    形にすれば、判断を数える `jq` がこれも数えてしまうためである。
    """
    import json

    row = {"kind": "preflight", "preflight": report.to_row()}
    try:
        trace._file.write(json.dumps(row, ensure_ascii=False) + "\n")   # noqa: SLF001
        trace._file.flush()                                             # noqa: SLF001
    except (OSError, ValueError, AttributeError):
        # A trace that cannot be written must not stop a preflight. The
        # report is still returned and printed.
        # 書けない記録が点検を止めてはならない。報告は返され、表示もされる。
        pass
