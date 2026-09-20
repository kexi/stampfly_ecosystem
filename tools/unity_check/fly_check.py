#!/usr/bin/env python3
"""
The automated flight check `sf unity check fly` runs.
`sf unity check fly` が実行する自動の飛行確認。

Flies the stage 3 pass criterion against a page that is already open in
Chrome and served by a running `sf unity serve`: load a world, put the
vehicle back at its spawn, ARM, climb, hold in ALT_HOLD, descend, land,
DISARM -- and decide from `vehicle.state` whether it worked.

動いている `sf unity serve` が配信し、Chrome で既に開いているページに対して、
段階 3 の合格基準を飛ばす。空間を読み、機体を出発点へ戻し、ARM し、上昇し、
ALT_HOLD で保ち、下降し、着地し、DISARM する。そして `vehicle.state` から
成否を判定する。

Every command goes through the same route `sf unity cmd` uses
(`POST /api/cmd` on the local server), so this check exercises the path a
person exercises rather than a private one of its own. The virtual clock,
not the wall clock, decides when each step happens: `sim.wait` holds until
a virtual time arrives, so the script flies the same flight on a fast
machine and a slow one.

命令は全て `sf unity cmd` と同じ経路（ローカルサーバの `POST /api/cmd`）を
通る。この確認が使うのは、人が使うのと同じ道であって、自前の裏道ではない。
各手順の時機を決めるのは実時間ではなく仮想時計である。`sim.wait` が仮想時刻の
到来まで待つので、速い機械でも遅い機械でも同じ飛行になる。

The stick values and the moments come from the two scripts that already fly
this: the PlayMode test `FirmwareFlightTest` and the native smoke checks'
`simulator/unity/native/bridge/sfu_rc_script.hpp`.
スティックの値と時点は、既にこれを飛ばしている 2 つの台本から取った。PlayMode
試験 `FirmwareFlightTest` と、ネイティブの最小動作確認の
`simulator/unity/native/bridge/sfu_rc_script.hpp` である。

Standard library only, like the rest of `sf unity`.
`sf unity` の他の部分と同じく、標準ライブラリだけで書く。

@design docs/plans/unity-simulator.md section 5 stage 3
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# The script / 台本
# ---------------------------------------------------------------------------

# Raw 12-bit ADC, centre 2048, exactly what a real transmitter puts on the
# wire (`sfu_rc_script.hpp`).
# 12 bit の生 ADC、中央 2048。実際の送信機が電文に載せるものそのまま
# （`sfu_rc_script.hpp`）。
ADC_CENTRE = 2048
ADC_MINIMUM = 0

# The throttle that takes the vehicle off the ground, from the same script.
# 機体を地面から離すスロットル。同じ台本から取った。
CLIMB_THROTTLE = 3243

# The moments, in simulated seconds. The firmware needs a few seconds of boot
# and calibration before it reaches IDLE_GROUND, and the ARM edge must arrive
# after that.
# 時点。シミュレーションの秒で表す。ファームは IDLE_GROUND に達するまで数秒の
# 起動と校正を要り、ARM のエッジはその後に来なければならない。
ARM_AT_S = 4.0
CLIMB_AT_S = 5.0
HOLD_AT_S = 6.3

# How long the hold lasts before the descent begins. The caller may change it
# (`--hold-seconds`); everything after it shifts with it.
# 下降を始めるまで保持する長さ。呼び出し側が変えられる（`--hold-seconds`）。
# それ以降の全ては一緒にずれる。
DEFAULT_HOLD_SECONDS = 10.0

# How long the descent is given before the check looks for a landed vehicle.
# ALT_HOLD with the throttle at the bottom commands a descent, and the firmware
# lands from it; from about 0.6 m this takes a few seconds.
# 着地したかを見るまでに下降へ与える長さ。ALT_HOLD でスロットルを一番下にすると
# 下降の指令になり、ファームはそこから着地する。0.6 m ほどからなら数秒かかる。
DESCEND_SECONDS = 6.0

# Samples taken during the hold, evenly spaced in virtual time. Enough to see
# the settling and to catch an altitude that walks out of the band, without
# making the run longer than the flight itself.
# 保持の間に取る標本。仮想時間で等間隔に置く。落ち着く様子が見え、帯から歩き
# 出る高度を捕まえられるだけあり、飛行そのものより長くはならない数にしてある。
HOLD_SAMPLES = 10

# How long a `--disturb` gust blows for, in simulated seconds. Long enough for
# the attitude loop to lean into it and settle, short enough that the recovery
# still happens inside the hold.
# `--disturb` の突風が吹く長さ。シミュレーションの秒で表す。姿勢のループが
# 風に傾いて落ち着くだけ長く、回復が保持の中で終わるだけ短い。
GUST_SECONDS = 2.0

# How long one `sim.wait` is allowed to hold, in real seconds. Below the page's
# own cap (`SimRemoteCommands.MaxWaitSeconds`, 60 s) and well below the
# server's 70 s connection window, so the page is still polling throughout and
# never reads as disconnected.
# 1 回の `sim.wait` が保持してよい実時間。ページ自身の上限
# （`SimRemoteCommands.MaxWaitSeconds`、60 秒）より下で、サーバの 70 秒の接続の
# 窓より十分に短い。ページは通して待ち受けを続け、切断と読まれることはない。
WAIT_CHUNK_SECONDS = 45.0

# How many such waits one virtual time may take. An automated tab runs at a
# fraction of real time, so a 25 s flight can need several minutes; six chunks
# is about four and a half minutes per step, past which the page is not merely
# slow but stopped.
# 1 つの仮想時刻に費やしてよい待ちの回数。自動操作のタブは実時間の何分の 1 かで
# 走るので、25 秒の飛行に数分かかることがある。6 回で 1 手順あたり 4 分半ほどで、
# それを越えたページは単に遅いのではなく止まっている。
MAX_WAIT_ATTEMPTS = 6

# ---------------------------------------------------------------------------
# The verdict / 合否の基準
# ---------------------------------------------------------------------------

# The band the altitude must stay inside while holding. The smoke checks settle
# around 0.58 m from a 0.81 m peak, so this is wide enough for the settling and
# narrow enough to catch a vehicle that never left the floor or one that flew
# away (the same band `FirmwareFlightTest` uses).
# 保持の間、高度が収まっていなければならない帯。最小動作確認は 0.81 m の頂点から
# 0.58 m ほどに落ち着くので、落ち着きを許すだけ広く、床を離れなかった機体や飛び
# 去った機体を捕まえるだけ狭い（`FirmwareFlightTest` と同じ帯）。
HOLD_MINIMUM_M = 0.15
HOLD_MAXIMUM_M = 3.0

# How near the floor counts as landed. Above the vehicle's own resting height
# (0.0103 m), so a body settled on its collider passes.
# どれだけ床に近ければ着地とみなすか。機体自身の静止高（0.0103 m）より上に取って
# あり、コライダの上で落ち着いた機体が通る。
LANDED_M = 0.08

# How far from level the vehicle may lean while holding. A hover that is
# drifting sideways leans to do it; one that has lost attitude control tips
# much further than this.
# 保持の間に機体が傾いてよい角度。横へ流れているホバリングはそのために傾く。
# 姿勢の制御を失ったものはこれよりずっと大きく倒れる。
MAX_TILT_DEG = 25.0

# The real-time ratio below which the host was not keeping up. `?raf=worker`
# drives the page from a 16 ms worker timer rather than Chrome's own vsync, and
# an automated tab is treated as backgrounded, so this is deliberately loose:
# it catches a host that cannot run the firmware at all, not one whose tab is
# not being painted. The tight 1.0 is checked by a person in a foreground tab
# (simulator/unity/README.md section 9).
# ホストが追いつけていなかったとみなす実時間比。`?raf=worker` はページを Chrome
# 自身の垂直同期ではなく 16 ms の Worker のタイマーで駆動し、自動操作のタブは背面
# 扱いになるので、この値は意図して緩くしてある。捕まえたいのは、ファームを走らせ
# ること自体ができないホストであって、タブが描かれていないことではない。厳しい
# 1.0 は人が前面のタブで確かめる（simulator/unity/README.md §9）。
MIN_REAL_TIME_RATIO = 0.05

# The most one firmware tick may cost. The tick is 2500 us long; measured on an
# M2 Max it is about 42 us, so a host ten times slower still passes and one that
# cannot run in real time at all does not.
# 1 刻みに許す最大の所要時間。刻みの長さは 2500 us で、M2 Max での実測は約 42 us
# である。10 倍遅いホストでも通り、実時間で回せないホストは通らない。
MAX_US_PER_TICK = 2500.0


class FlyCheckError(Exception):
    """A step could not be carried out at all -- no page, a command refused,
    a malformed answer. Distinct from a flight that flew and failed.
    手順そのものが行えなかった。ページが無い、命令が断られた、答えが読めない。
    飛んだ上で不合格になった飛行とは別に扱う。"""


# ---------------------------------------------------------------------------
# One step of the script / 台本の 1 手順
# ---------------------------------------------------------------------------

class FlightRecorder:
    """What the flight did, as the script walks through it.

    Holds the altitude samples, the flight states seen with their virtual
    times, and the cmd_ids issued, so the report can say both "it flew" and
    "here is the command to look up in the log".

    台本が進む間に飛行が行ったこと。高度の標本、見た飛行状態とその仮想時刻、
    発行した cmd_id を持つ。報告が「飛んだ」と「ログで引くべき命令はこれ」の
    両方を言えるようにするためである。
    """

    def __init__(self) -> None:
        self.samples: List[Dict[str, Any]] = []
        self.transitions: List[Dict[str, Any]] = []
        self.commands: List[Dict[str, Any]] = []
        self._last_state: Optional[str] = None

    def note_command(self, command: str, cmd_id: Optional[str], ok: bool) -> None:
        self.commands.append({"command": command, "cmd_id": cmd_id, "ok": ok})

    def note_state(self, phase: str, state: Dict[str, Any]) -> None:
        """Record one `vehicle.state`, and a transition when the flight state
        changed since the last one.
        `vehicle.state` を 1 つ記録し、前回から飛行状態が変わっていれば遷移も
        記録する。"""
        sample = {
            "phase": phase,
            "sim_us": state.get("sim_us"),
            "altitude_m": _altitude(state),
            "tilt_deg": _tilt(state),
            "flight_state": state.get("flight_state"),
            "flight_mode": state.get("flight_mode"),
            "armed": state.get("armed"),
            "battery_v": state.get("battery_v"),
            "range_down_m": state.get("range_down_m"),
            "range_down_valid": state.get("range_down_valid"),
            "real_time_ratio": state.get("real_time_ratio"),
            "us_per_tick": state.get("us_per_tick"),
            "fps": state.get("fps"),
        }
        self.samples.append(sample)

        changed = sample["flight_state"] != self._last_state
        if changed:
            self.transitions.append({
                "from": self._last_state,
                "to": sample["flight_state"],
                "sim_us": sample["sim_us"],
                "mode": sample["flight_mode"],
                "armed": sample["armed"],
            })
            self._last_state = sample["flight_state"]

    def phase(self, name: str) -> List[Dict[str, Any]]:
        """The samples taken during one phase. / ある段階で取った標本。"""
        return [sample for sample in self.samples if sample["phase"] == name]


def _altitude(state: Dict[str, Any]) -> Optional[float]:
    """The truth altitude out of a `vehicle.state` answer.
    `vehicle.state` の答えから真の高度を取り出す。"""
    truth = state.get("truth")
    if not isinstance(truth, dict):
        return None
    value = truth.get("altitude_m")
    return float(value) if isinstance(value, (int, float)) else None


def _tilt(state: Dict[str, Any]) -> Optional[float]:
    """How far from level the vehicle is leaning, in degrees.
    機体が水平からどれだけ傾いているか。度で表す。"""
    truth = state.get("truth")
    if not isinstance(truth, dict):
        return None
    value = truth.get("tilt_deg")
    return float(value) if isinstance(value, (int, float)) else None


# ---------------------------------------------------------------------------
# The flight / 飛行
# ---------------------------------------------------------------------------

# A command sender: takes a command name and its arguments, returns
# (ok, data, cmd_id). `sf unity check fly` passes one that posts to the running
# server; a test passes one that talks to a stand-in page.
# 命令を送るもの。命令名と引数を受け、(ok, data, cmd_id) を返す。
# `sf unity check fly` は動いているサーバへ送るものを渡し、試験はページの代役と
# 話すものを渡す。
Sender = Callable[[str, Dict[str, Any], float], Tuple[bool, Any, Optional[str]]]


class FlightScript:
    """The stage 3 flight, step by step, against one sender.
    段階 3 の飛行を、1 つの送り手に対して 1 手順ずつ進める。"""

    def __init__(self, send: Sender, world: str, hold_seconds: float,
                 disturb: float = 0.0) -> None:
        self.send = send
        self.world = world
        self.hold_seconds = hold_seconds
        self.disturb = disturb
        self.recorder = FlightRecorder()

    # -- the primitives ---------------------------------------------------
    def command(self, name: str, args: Optional[Dict[str, Any]] = None,
                timeout: float = 15.0) -> Any:
        """Send one command and return its data, raising when it did not run.
        A step that cannot be carried out is not a failed flight: it means the
        check itself could not proceed, and saying so plainly is more useful
        than a verdict drawn from half a flight.
        命令を 1 つ送り、その data を返す。実行できなかったら例外にする。行えな
        かった手順は不合格の飛行ではない。確認そのものが進めなかったという意味で
        あり、半分の飛行から引いた合否より、そう言うほうが役に立つ。"""
        ok, data, cmd_id = self.send(name, args or {}, timeout)
        self.recorder.note_command(name, cmd_id, ok)
        if not ok:
            raise FlyCheckError(f"{name} failed: {data}")
        return data

    def read_state(self, phase: str) -> Dict[str, Any]:
        """`vehicle.state`, recorded under this phase's name.
        `vehicle.state` を、この段階の名前で記録しながら読む。"""
        state = self.command("vehicle.state")
        if not isinstance(state, dict):
            raise FlyCheckError(f"vehicle.state answered {state!r}, not an object")
        self.recorder.note_state(phase, state)
        return state

    def wait_until(self, seconds: float, phase: str) -> Dict[str, Any]:
        """Run the simulation to a virtual time, then read the state there.

        A page in an automated tab runs well below real time: `?raf=worker`
        drives it from a 16 ms timer rather than Chrome's vsync, and a frame
        runs at most twelve ticks, so a few virtual seconds can take tens of
        real ones. One `sim.wait` may hold only as long as the page allows
        (`MaxWaitSeconds`, below the server's connection window), so a long
        span is asked for in as many waits as it takes. Each one names the same
        absolute virtual time, so a wait that ran out simply resumes rather
        than losing its place.

        シミュレーションをある仮想時刻まで進め、そこで状態を読む。

        自動操作のタブのページは実時間よりかなり遅く走る。`?raf=worker` は
        Chrome の垂直同期ではなく 16 ms のタイマーで駆動し、1 フレームは多くとも
        12 刻みなので、数秒の仮想時間が実時間の数十秒になりうる。1 回の
        `sim.wait` が保持できるのはページが許す長さまで（`MaxWaitSeconds`。
        サーバの接続の窓より短い）なので、長い区間は必要な回数だけ待つ。どの回も
        同じ絶対の仮想時刻を名指しするので、尽きた待ちは場所を失わずそのまま
        再開する。
        """
        target_us = int(round(seconds * 1e6))
        for attempt in range(MAX_WAIT_ATTEMPTS):
            ok, data, cmd_id = self.send(
                "sim.wait",
                {"sim_us": target_us, "timeout_s": WAIT_CHUNK_SECONDS},
                WAIT_CHUNK_SECONDS + 10.0)
            self.recorder.note_command("sim.wait", cmd_id, ok)
            if ok:
                return self.read_state(phase)

            # A timeout says the clock has not got there YET, which is a reason
            # to wait again; anything else is a refusal and must stop the check.
            # 待ちが尽きたことは、時計が**まだ**そこへ達していないという意味で、
            # もう一度待つ理由になる。それ以外は拒否であり、確認を止めねばならない。
            timed_out = isinstance(data, str) and "timed out" in data
            if not timed_out:
                raise FlyCheckError(f"sim.wait failed: {data}")

        raise FlyCheckError(
            f"the simulation did not reach {seconds:.2f} s of virtual time "
            f"in {MAX_WAIT_ATTEMPTS} waits of {WAIT_CHUNK_SECONDS:.0f} s: "
            "the page is running far below real time")

    def sticks(self, throttle: int, arm: bool, alt_hold: bool) -> None:
        """Hold these stick values until the next `rc.set`.
        次の `rc.set` まで、このスティックの値を保つ。"""
        self.command("rc.set", {
            "throttle": throttle,
            "roll": ADC_CENTRE,
            "pitch": ADC_CENTRE,
            "yaw": ADC_CENTRE,
            "arm": arm,
            "alt_hold": alt_hold,
        })

    # -- the flight -------------------------------------------------------
    def fly(self) -> Dict[str, Any]:
        """Fly the whole thing and return the report.
        全体を飛ばし、報告を返す。"""
        started = time.monotonic()

        self.prepare()
        self.arm()
        self.climb()
        hold_end = self.hold()
        self.land(hold_end)

        report = self.judge()
        report["wall_clock_s"] = round(time.monotonic() - started, 2)
        return report

    def prepare(self) -> None:
        """Load the world, start a fresh firmware, put the vehicle at its
        spawn and centre the sticks.

        The power cycle is what makes the moments below mean anything: a page
        that has been open for a minute already has a virtual clock at 60 s,
        and every wait for "4 s" would return at once, on a vehicle that had
        long since finished booting. `sim.power_cycle` discards the firmware
        and starts a new one from INIT with the clock back at zero, so the
        script flies the same flight whether the page was opened a second ago
        or an hour ago. `sim.reset` afterwards puts the body back at the
        spawn, which the power cycle does too but which must also hold if
        somebody had flown this page by hand first.

        空間を読み、新しいファームを起こし、機体を出発点へ置き、スティックを中央に
        する。

        以下の時点に意味を与えるのが、この電源の入れ直しである。1 分開いていた
        ページの仮想時計は既に 60 秒を指しており、「4 秒」を待つ命令は全てその場で
        返る。しかも機体はとうに起動を終えている。`sim.power_cycle` はファームを
        捨て、時計を 0 に戻して INIT から新しいものを起こす。これにより台本は、
        ページが 1 秒前に開かれていようと 1 時間前だろうと同じ飛行をする。後続の
        `sim.reset` は機体を出発点へ戻す。電源の入れ直しも同じことをするが、誰かが
        先にこのページを手で飛ばしていた場合にも効いてほしいからである。
        """
        self.command("world.load", {"name": self.world})
        self.command("sim.power_cycle")
        self.command("sim.reset")
        self.sticks(ADC_CENTRE, arm=False, alt_hold=False)

        start = self.read_state("start")
        clock = start.get("sim_us")
        began_at_zero = isinstance(clock, (int, float)) and clock < 1_000_000
        if not began_at_zero:
            raise FlyCheckError(
                f"the virtual clock is at {clock} us after a power cycle, not "
                "near zero: the script's moments would mean nothing")

    def arm(self) -> None:
        """Wait for the boot to reach IDLE_GROUND, then press ARM once.
        起動が IDLE_GROUND に達するのを待ち、ARM を 1 回押す。"""
        self.wait_until(ARM_AT_S, "before_arm")
        self.command("rc.arm")
        self.read_state("armed")

    def climb(self) -> None:
        """Throttle up in STABILIZE until the vehicle is airborne, then switch
        to ALT_HOLD with the throttle centred, which means "hold this height".
        STABILIZE でスロットルを上げて機体を浮かせ、スロットルを中央にした
        ALT_HOLD へ移る。中央は「この高さを保て」の意味である。"""
        self.wait_until(CLIMB_AT_S, "before_climb")
        self.sticks(CLIMB_THROTTLE, arm=True, alt_hold=False)

        self.wait_until(HOLD_AT_S, "climbed")
        self.sticks(ADC_CENTRE, arm=True, alt_hold=True)

    def hold(self) -> float:
        """Sample the altitude across the hold and return the virtual time it
        ended at. The first second is left out of the band check: the vehicle
        is still settling from the climb's overshoot then, and a band tight
        enough to be useful would reject a healthy flight.
        保持の間、高度の標本を取り、終わった仮想時刻を返す。最初の 1 秒は帯の
        検査から外す。そこではまだ上昇の行き過ぎから落ち着いている途中で、役に
        立つだけ狭い帯は健全な飛行を落としてしまう。"""
        settled_at = HOLD_AT_S + 1.0
        hold_end = HOLD_AT_S + self.hold_seconds
        span = hold_end - settled_at

        # A flight that is never pushed off symmetry never asks the attitude
        # or yaw loops to do anything, and a check made only of that case
        # cannot see a fault in them -- which is how a yaw-axis instability
        # reached the browser unnoticed. `--disturb` puts a lateral gust in
        # the middle of the hold and requires the flight to survive it.
        # 対称から一度も押し出されない飛行は、姿勢のループにもヨーのループにも
        # 何も求めない。その場合だけで作った検査は、そこにある誤りを見られない。
        # ヨー軸の不安定が気付かれずブラウザまで届いたのはそのためである。
        # `--disturb` は保持の途中で横風を当て、飛行がそれを生き延びることを求める。
        gust_at = settled_at + span * 0.4
        gust_off = gust_at + GUST_SECONDS

        for index in range(1, HOLD_SAMPLES + 1):
            moment = settled_at + span * index / HOLD_SAMPLES

            wants_gust = self.disturb > 0.0 and gust_at <= moment < gust_off
            if wants_gust:
                self.command("plant.wind", {"x": self.disturb, "y": 0.0, "z": 0.0})

            wants_calm = self.disturb > 0.0 and moment >= gust_off
            if wants_calm:
                self.command("plant.wind", {"x": 0.0, "y": 0.0, "z": 0.0})

            self.wait_until(moment, "holding")

        return hold_end

    def land(self, hold_end: float) -> None:
        """Throttle to the bottom in ALT_HOLD, which commands a descent the
        firmware lands from, then DISARM once it is down.
        ALT_HOLD でスロットルを一番下にする。これが下降の指令になり、ファームは
        そこから着地する。降りたら DISARM する。"""
        self.sticks(ADC_MINIMUM, arm=True, alt_hold=True)
        self.wait_until(hold_end + DESCEND_SECONDS, "landed")

        self.command("rc.arm", {"armed": False})
        self.wait_until(hold_end + DESCEND_SECONDS + 1.0, "disarmed")

    # -- the verdict ------------------------------------------------------
    def judge(self) -> Dict[str, Any]:
        """Turn what was recorded into a pass or a fail with its numbers.
        記録したものを、数値を添えた合否にする。"""
        holding = self.recorder.phase("holding")
        altitudes = [s["altitude_m"] for s in holding
                     if isinstance(s["altitude_m"], (int, float))]
        tilts = [s["tilt_deg"] for s in holding
                 if isinstance(s["tilt_deg"], (int, float))]
        ratios = [s["real_time_ratio"] for s in self.recorder.samples
                  if isinstance(s["real_time_ratio"], (int, float))
                  and s["real_time_ratio"] > 0.0]
        costs = [s["us_per_tick"] for s in self.recorder.samples
                 if isinstance(s["us_per_tick"], (int, float))
                 and s["us_per_tick"] > 0.0]

        final = self.recorder.phase("disarmed")
        last = final[-1] if final else {}
        peak = [s["altitude_m"] for s in self.recorder.phase("climbed")
                if isinstance(s["altitude_m"], (int, float))]

        checks = [
            _check("held_the_altitude_band",
                   bool(altitudes)
                   and min(altitudes) > HOLD_MINIMUM_M
                   and max(altitudes) < HOLD_MAXIMUM_M,
                   f"{min(altitudes):.3f}..{max(altitudes):.3f} m"
                   if altitudes else "no altitude samples",
                   f"{HOLD_MINIMUM_M}..{HOLD_MAXIMUM_M} m"),
            _check("stayed_flying",
                   bool(holding)
                   and all(s["flight_state"] == "FLYING" for s in holding),
                   ",".join(sorted({str(s["flight_state"]) for s in holding}))
                   or "no samples",
                   "FLYING"),
            _check("stayed_near_level",
                   bool(tilts) and max(tilts) < MAX_TILT_DEG,
                   f"{max(tilts):.1f} deg" if tilts else "no tilt samples",
                   f"< {MAX_TILT_DEG} deg"),
            _check("reached_altitude_hold",
                   any(s["flight_mode"] == "ALT_HOLD" for s in holding),
                   ",".join(sorted({str(s["flight_mode"]) for s in holding}))
                   or "no samples",
                   "ALT_HOLD"),
            _check("came_back_down",
                   isinstance(last.get("altitude_m"), (int, float))
                   and last["altitude_m"] < LANDED_M,
                   f"{last.get('altitude_m')} m",
                   f"< {LANDED_M} m"),
            _check("ended_idle_and_disarmed",
                   last.get("flight_state") in ("IDLE_GROUND", "IDLE_HELD", "INIT")
                   and last.get("armed") is False,
                   f"{last.get('flight_state')}, armed={last.get('armed')}",
                   "IDLE_GROUND and disarmed"),
            _check("kept_up_with_real_time",
                   bool(ratios) and min(ratios) >= MIN_REAL_TIME_RATIO,
                   f"{min(ratios):.3f}" if ratios else "not reported",
                   f">= {MIN_REAL_TIME_RATIO}"),
            _check("a_tick_cost_less_than_its_length",
                   bool(costs) and max(costs) < MAX_US_PER_TICK,
                   f"{max(costs):.1f} us" if costs else "not reported",
                   f"< {MAX_US_PER_TICK} us"),
        ]

        passed = all(check["pass"] for check in checks)
        return {
            "pass": passed,
            "world": self.world,
            "hold_seconds": self.hold_seconds,
            "checks": checks,
            "altitude": {
                "peak_m": max(peak) if peak else None,
                "hold_min_m": min(altitudes) if altitudes else None,
                "hold_max_m": max(altitudes) if altitudes else None,
                "final_m": last.get("altitude_m"),
            },
            "pacing": {
                "real_time_ratio_min": min(ratios) if ratios else None,
                "us_per_tick_max": max(costs) if costs else None,
                "fps_last": last.get("fps"),
            },
            "transitions": self.recorder.transitions,
            "samples": self.recorder.samples,
            "commands": self.recorder.commands,
        }


def _check(name: str, passed: bool, measured: str, wanted: str) -> Dict[str, Any]:
    """One line of the verdict: what was looked at, what was seen, what was
    wanted. Both the number and the bound are kept, so a report explains
    itself without the reader going back to the source.
    合否の 1 行。何を見たか、何が見えたか、何を求めたか。数と境の両方を残し、
    報告が読み手を出典へ戻らせずに自分を説明できるようにする。"""
    return {"name": name, "pass": bool(passed),
            "measured": measured, "wanted": wanted}


def cmd_id_of(report: Dict[str, Any], command: str) -> Optional[str]:
    """The cmd_id of the first `command` the flight issued, which is what
    `sf unity logs --cmd <id>` is given to show that command's whole flow.
    飛行が出した最初の `command` の cmd_id。`sf unity logs --cmd <id>` に渡して、
    その命令の流れ全体を見せるためのもの。"""
    for entry in report.get("commands", []):
        if entry.get("command") == command and entry.get("cmd_id"):
            return entry["cmd_id"]
    return None


def summarise(report: Dict[str, Any]) -> List[str]:
    """The report as lines a person reads, for the terminal. The JSON stays
    the machine-readable answer; this is the part somebody skims.
    報告を人が読む行にする。端末のため。機械が読む答えは JSON のままで、これは
    人が目を通す部分である。"""
    lines: List[str] = []
    verdict = "PASS" if report.get("pass") else "FAIL"
    lines.append(f"{verdict}  world={report.get('world')} "
                 f"hold={report.get('hold_seconds')}s "
                 f"({report.get('wall_clock_s')}s of wall clock)")

    altitude = report.get("altitude", {})
    lines.append(f"  altitude  peak {_metres(altitude.get('peak_m'))}  "
                 f"hold {_metres(altitude.get('hold_min_m'))}"
                 f"..{_metres(altitude.get('hold_max_m'))}  "
                 f"final {_metres(altitude.get('final_m'))}")

    pacing = report.get("pacing", {})
    lines.append(f"  pacing    real-time {_number(pacing.get('real_time_ratio_min'))}  "
                 f"{_number(pacing.get('us_per_tick_max'))} us/tick  "
                 f"{_number(pacing.get('fps_last'))} fps")

    states = " -> ".join(
        f"{t.get('to')}@{_seconds(t.get('sim_us'))}"
        for t in report.get("transitions", []))
    lines.append(f"  states    {states or 'none seen'}")

    for check in report.get("checks", []):
        mark = "ok  " if check["pass"] else "FAIL"
        lines.append(f"  [{mark}] {check['name']}: "
                     f"{check['measured']} (wanted {check['wanted']})")
    return lines


def _metres(value: Any) -> str:
    return f"{value:.3f}m" if isinstance(value, (int, float)) else "-"


def _number(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "-"


def _seconds(sim_us: Any) -> str:
    return f"{sim_us / 1e6:.2f}s" if isinstance(sim_us, (int, float)) else "-"


def run(send: Sender, world: str, hold_seconds: float,
        disturb: float = 0.0) -> Dict[str, Any]:
    """Fly the check with the given sender, returning the report. A step that
    could not be carried out becomes a failing report with its reason rather
    than an exception reaching the caller, so `sf unity check fly` always has
    something to print and something to exit non-zero on.
    渡された送り手で確認を飛ばし、報告を返す。行えなかった手順は、呼び出し側へ
    届く例外ではなく、理由を持つ不合格の報告にする。`sf unity check fly` が常に
    出すものと、非 0 で終わる根拠を持てるようにするためである。"""
    script = FlightScript(send, world, hold_seconds, disturb)
    try:
        return script.fly()
    except FlyCheckError as failure:
        return {
            "pass": False,
            "world": world,
            "hold_seconds": hold_seconds,
            "error": str(failure),
            "checks": [_check("the_flight_ran", False, str(failure),
                              "every command to be carried out")],
            "altitude": {},
            "pacing": {},
            "transitions": script.recorder.transitions,
            "samples": script.recorder.samples,
            "commands": script.recorder.commands,
            "wall_clock_s": None,
        }


def main(argv: Optional[List[str]] = None) -> int:
    """Standalone use. `sf unity check fly` is the supported entry point; this
    exists so the module can be run while it is being worked on.
    単体実行。公開の入口は `sf unity check fly` である。これは、この部品に手を
    入れている間に走らせられるようにするためにある。"""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--world", default="empty_room")
    parser.add_argument("--hold-seconds", type=float, default=DEFAULT_HOLD_SECONDS)
    parser.add_argument("--disturb", type=float, default=0.0,
                        help="lateral gust during the hold [N]; 0 disables it")
    parsed = parser.parse_args(argv)

    # Importing here keeps this module free of an sfcli dependency for the
    # tests, which drive `run()` with a sender of their own.
    # ここで import することで、このモジュールは試験に対して sfcli へ依存しない
    # ままでいられる。試験は自前の送り手で `run()` を動かす。
    import os
    import sys
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "lib"))
    from sfcli.commands import unity

    report = run(unity.make_sender(), parsed.world, parsed.hold_seconds,
                 parsed.disturb)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
