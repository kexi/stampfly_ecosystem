#!/usr/bin/env python3
"""
`sf pilot run --sils` end to end, against the real emulator (P2).

`sf pilot run --sils` を実エミュレータに対して端から端まで確認する（P2）。

What is verified, all with `--fake` so no API key and no network are needed:

  (a) The stdin `api` verb reaches the REAL ApiTask parser: `api command`
      then `api takeoff` puts the unmodified firmware into FLYING, while the
      transmitter's sticks stay parked at neutral. This is also the INV-2
      check -- a parked stick must NOT cancel API guidance.
  (b) `nominal` does not talk itself into landing: a healthy hover stays up.
  (c) `battery_drop` reaches a landing.
  (d) A Judge that answers too slowly becomes holding, and a hold that will
      not end becomes a landing.

いずれも `--fake` で確認するため、API キーも通信も不要:
  (a) stdin の `api` が実 ApiTask パーサへ届くこと（`api command`→`api takeoff`
      で無改変ファームが FLYING になる。送信機のスティックは中立のまま）。
      これは INV-2 の確認でもある — 置いたままのスティックは API 誘導を
      解除してはならない。
  (b) `nominal` が自ら着陸を選ばないこと（健全なホバリングは飛び続ける）。
  (c) `battery_drop` が着陸に至ること。
  (d) 応答が遅すぎる Judge は待機になり、終わらない待機は着陸になること。

Prerequisite / 事前条件:
    source setup_env.sh && sf sils build
    pytest simulator/tests/test_pilot_sils.py -v
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sfcli.utils.paths import paths


def _exe(name: str) -> Path:
    suffix = ".exe" if sys.platform.startswith("win") else ""
    return paths.sils_build() / f"{name}{suffix}"


EMU_VEHICLE = _exe("emu_vehicle")
MODEL = paths.root() / "simulator" / "sils" / "models" / "stampfly.xml"

# The emulator runs in real time, so these tests cost real seconds. Each one
# is kept to the shortest flight that still reaches the property it checks.
# エミュレータは実時間で動くため、これらの試験は実時間を消費する。各試験は
# 確認したい性質に到達する最短の飛行に留めてある。
BOOT_SETTLE_S = 6.0      # boot calibration before ARM/takeoff is accepted
TAKEOFF_SETTLE_S = 8.0   # climb to the 0.5 m target and settle


pytestmark = pytest.mark.skipif(
    not EMU_VEHICLE.exists(),
    reason=f"{EMU_VEHICLE.name} not built — run 'source setup_env.sh && sf sils build'",
)


class _Emu:
    """A running emulator plus the SilsLink driving it.
    動作中のエミュレータと、それを駆動する SilsLink。"""

    def __init__(self, duration_s: float, extra_env: dict = None):
        from sfpilot.link import SilsLink

        env = dict(os.environ, SILS_EMU_REALTIME="1", SILS_EMU_RC_STDIN="1")
        env.update(extra_env or {})
        self.proc = subprocess.Popen(
            [str(EMU_VEHICLE), str(MODEL), str(int(duration_s * 1e6))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", bufsize=1, env=env,
        )
        self.link = SilsLink(self.proc)

    def hold(self, seconds: float) -> None:
        """Keep the sticks parked and drain samples for `seconds`.
        `seconds` の間スティックを置いたままにし、サンプルを捨て続ける。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.link.hold_sticks_neutral()
            self.link.read_samples()
            time.sleep(0.02)

    def collect(self, at_least: int = 1, timeout_s: float = 5.0) -> list:
        """Keep flying until `at_least` STATE samples have been collected.

        Why not a bare `read_samples()`: the reader thread queues samples as
        the emulator emits them (every 33 ms) while this loop reads every
        20 ms, so a single read lands in an empty queue more often than not.
        Waiting for a sample rather than for a wall-clock interval makes the
        caller's assertions depend on what arrived, not on which of the two
        periods happened to win the race.

        STATE サンプルが `at_least` 件集まるまで飛ばし続ける。

        素の `read_samples()` にしない理由: 読み取りスレッドはエミュレータが
        出すたび（33ms ごと）にサンプルを積むのに対し、この繰り返しは 20ms
        ごとに読む。そのため 1 回の読み取りはむしろ空振りのほうが多い。実時間で
        待つのではなくサンプルの到着を待つことで、呼び出し側の表明は「何が
        届いたか」だけに依存し、2 つの周期のどちらが先だったかには依存しなくなる。
        """
        collected: list = []
        deadline = time.monotonic() + timeout_s
        while len(collected) < at_least:
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f"only {len(collected)} STATE samples arrived in {timeout_s:g}s "
                    f"(wanted {at_least}) — the emulator stopped emitting them"
                )
            self.link.hold_sticks_neutral()
            collected += self.link.read_samples()
            time.sleep(0.02)
        return collected

    def close(self) -> None:
        self.link.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def _fly_to_hover(duration_s: float, extra_env: dict = None) -> _Emu:
    """Boot, enter SDK mode and take off; return the emulator at a hover.
    起動・SDK モード移行・離陸を済ませ、ホバリング状態のエミュレータを返す。"""
    emu = _Emu(duration_s, extra_env)
    emu.hold(BOOT_SETTLE_S)
    emu.link.takeoff()
    emu.hold(TAKEOFF_SETTLE_S)
    return emu


# =============================================================================
# (a) The `api` stdin verb, and INV-2 / stdin の `api` と INV-2
# =============================================================================

def test_api_stdin_takes_off_while_the_sticks_stay_parked():
    """`api command` + `api takeoff` reach FLYING with neutral sticks held.

    Two properties in one flight, because they are the same situation: the
    API verb must drive the real firmware, AND the neutral stick stream
    that runs alongside it must not cancel that guidance (INV-2 cancels on
    stick MOVEMENT, not on a parked stick).

    中立のスティックを保持したまま `api command`＋`api takeoff` で FLYING に
    到達すること。

    1 回の飛行で 2 つの性質を見る（同じ状況だからである）: API の指令が実ファームを
    駆動すること、および並行して流れる中立のスティックがその誘導を解除しない
    こと（INV-2 が解除するのはスティックの「動き」であって、置いたままの
    スティックではない）。
    """
    emu = _fly_to_hover(duration_s=30.0)
    try:
        # A second of hovering at the 33 ms STATE period, waited for by
        # sample count rather than by the clock -- see `_Emu.collect`.
        # 33ms 周期の STATE でおよそ 1 秒ぶん。時計ではなくサンプル数で待つ
        # （`_Emu.collect` の説明を参照）。
        samples = emu.collect(at_least=25)
    finally:
        log = "\n".join(emu.link.log_tail)
        emu.close()

    assert "cmd: command" in log, "the api verb never reached the ApiTask parser"
    flying = [s for s in samples if s.get("flight_state") == "FLYING"]
    assert flying, f"never reached FLYING (states seen: " \
                   f"{ {s.get('flight_state') for s in samples} })"
    altitudes = [s["altitude_m"] for s in samples if "altitude_m" in s]
    assert max(altitudes) > 0.3, f"never climbed (max alt {max(altitudes):.3f} m)"


def test_the_state_line_carries_the_fields_the_pilot_needs():
    """Position, velocity, ToF and battery all arrive on the STATE line.

    These are the P2 additions; without them the Monitor cannot classify a
    drift, check the envelope, or see the battery at all.
    位置・速度・ToF・電池が STATE 行で届くこと。

    P2 で追加した項目である。これらが無いと Monitor は流れを区分できず、包絡も
    照合できず、電池も見られない。
    """
    emu = _fly_to_hover(duration_s=25.0)
    try:
        samples = emu.collect(at_least=1)
    finally:
        emu.close()

    newest = samples[-1]
    for key in ("altitude_m", "pos_n", "pos_e", "vel_n", "vel_e", "vel_d",
                "battery_pct", "tof_m", "roll", "pitch", "yaw"):
        assert key in newest, f"{key} missing from the STATE line"


# =============================================================================
# (b)-(d) The decision loop over a real flight / 実飛行上の判断ループ
# =============================================================================

def _run_pilot(scene_name: str, duration_s: float, judge, emu: _Emu) -> dict:
    """Run the decision loop over an already-hovering emulator.
    既にホバリング中のエミュレータに対して判断ループを回す。"""
    from sfpilot.config import DEFAULT_CONFIG
    from sfpilot.pilot import Pilot
    from sfpilot.scenes import get_scene

    scene = get_scene(scene_name)
    pilot = Pilot(emu.link, judge, DEFAULT_CONFIG)
    period = 1.0 / DEFAULT_CONFIG.monitor_hz
    started = time.monotonic()
    while True:
        cycle_start = time.monotonic()
        elapsed = cycle_start - started
        if elapsed >= duration_s or pilot.executor.landing:
            break
        if scene.drive is not None:
            scene.drive(emu.link, elapsed, duration_s)
        pilot.step()
        emu.link.hold_sticks_neutral()
        slack = period - (time.monotonic() - cycle_start)
        if slack > 0:
            time.sleep(slack)
    return {"pilot": pilot, "landed": pilot.executor.landing, "elapsed": elapsed}


def test_a_healthy_hover_is_not_talked_into_landing():
    """`nominal` flies its full length without choosing to land.

    The costly failure mode for a safety layer is not missing a hazard --
    it is landing a perfectly good flight. This is the test for that.
    `nominal` が着陸を選ばずに所定の時間を飛び切ること。

    安全層にとって高くつく失敗は、危険を見逃すことよりも、健全な飛行を着陸
    させてしまうことである。その確認である。
    """
    from sfpilot.judge import FakeJudge

    flight_s = 60.0
    emu = _fly_to_hover(duration_s=BOOT_SETTLE_S + TAKEOFF_SETTLE_S + flight_s + 15.0)
    try:
        result = _run_pilot("nominal", flight_s, FakeJudge(), emu)
    finally:
        emu.close()

    assert not result["landed"], (
        f"landed after {result['elapsed']:.1f}s of a healthy hover — "
        f"the judging layers turned a good flight into a landing"
    )
    assert result["pilot"].decisions, "no decisions were made at all"


def test_a_falling_battery_leads_to_a_landing():
    """`battery_drop` ends in a landing rather than flying to exhaustion.
    `battery_drop` が電池切れまで飛ばずに着陸で終わること。"""
    from sfpilot.judge import FakeJudge

    flight_s = 45.0
    emu = _fly_to_hover(duration_s=BOOT_SETTLE_S + TAKEOFF_SETTLE_S + flight_s + 15.0)
    try:
        result = _run_pilot("battery_drop", flight_s, FakeJudge(), emu)
    finally:
        emu.close()

    assert result["landed"], (
        f"flew the whole {flight_s:g}s with a battery falling into the danger "
        f"band without landing"
    )


def test_a_slow_judge_holds_and_then_lands():
    """A Judge slower than the deadline hovers, and a hold that will not end
    becomes a landing.

    This is the Arbiter's whole contract exercised against a real flight:
    an answer that is always late is never usable, holding is the safe
    reading of that, and holding forever is itself unsafe.

    期限より遅い Judge は待機になり、終わらない待機は着陸になること。

    Arbiter の契約を実飛行に対して一通り動かす: 常に遅れる答えは使えず、その
    安全な解釈は待機であり、待機し続けること自体が安全ではない。
    """
    from sfpilot.config import DEFAULT_CONFIG
    from sfpilot.judge import FakeJudge

    # One second is well past the 500 ms deadline, so every answer is late.
    # 1 秒は 500ms の期限を大きく超えるため、全ての答えが遅延となる。
    slow_judge = _SlowJudge(delay_s=1.0)
    # Long enough for the hover-to-land timer to expire, plus margin.
    # 待機継続の計時が満了するのに十分な長さ＋余裕。
    flight_s = DEFAULT_CONFIG.arbiter.hover_to_land_s + 10.0
    emu = _fly_to_hover(duration_s=BOOT_SETTLE_S + TAKEOFF_SETTLE_S + flight_s + 15.0)
    try:
        result = _run_pilot("nominal", flight_s, slow_judge, emu)
    finally:
        emu.close()

    verdicts = [row["verdict"] for row in result["pilot"].decisions]
    assert any(v.action == "hover" for v in verdicts), "a late answer was not held on"
    assert result["landed"], (
        f"held for {result['elapsed']:.1f}s without ever converting the hold "
        f"into a landing (limit {DEFAULT_CONFIG.arbiter.hover_to_land_s:g}s)"
    )


class _SlowJudge:
    """A Judge that always answers, but always too late.

    Wraps FakeJudge rather than reimplementing it, so what comes back is a
    real, well-formed Judgement -- the lateness is the only thing under
    test.
    必ず答えるが、必ず遅すぎる Judge。

    FakeJudge を作り直さず包む。返るものは正しい形の Judgement であり、試験の
    対象は「遅いこと」だけになる。
    """

    def __init__(self, delay_s: float):
        from sfpilot.judge import FakeJudge

        self._inner = FakeJudge()
        self._delay_s = delay_s

    def ask(self, state: dict, question_ids):
        time.sleep(self._delay_s)
        judgement = self._inner.ask(state, question_ids)
        judgement.latency_ms = self._delay_s * 1e3
        return judgement

    def close(self) -> None:
        self._inner.close()
