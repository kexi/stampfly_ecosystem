"""
What `sf unity check fly` guarantees / `sf unity check fly` が保証すること

The Unity page cannot be run from a test, so these run the flight script
against a stand-in page that models the vehicle the way the firmware plus
PhysX behave in outline: a boot sequence that reaches IDLE_GROUND, an ARM
edge that arms, a throttle that lifts, an ALT_HOLD that holds, and a bottomed
throttle that lands. The script under test is the real one -- the same
`tools/unity_check/fly_check.py` `sf unity check fly` loads -- reached through
the real `POST /api/cmd` route.

Unity のページは試験から動かせないので、ここでは飛行の台本を、ファームと PhysX
の振る舞いを粗く写したページの代役に対して走らせる。IDLE_GROUND に達する起動、
ARM で ARM するエッジ、持ち上げるスロットル、保持する ALT_HOLD、一番下で着地する
スロットルである。試験されるのは本物の台本 ―
`sf unity check fly` が読み込むのと同じ `tools/unity_check/fly_check.py` ― で、
本物の `POST /api/cmd` の経路を通って届く。

Guaranteed here / ここで保証すること:
  (a) a flight that works is judged a pass, with its numbers
  (b) a vehicle that never leaves the floor is judged a fail, naming why
  (c) every command of the flight carries its own cmd_id into the log
  (d) `sf unity check fly` exits 0 on a pass and non-zero on a fail
  (e) a page that refuses a command stops the check with that reason
  (f) `sim.wait` drives the script, so the flight is paced by virtual time
"""

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "lib"))

from sfcli.commands import unity  # noqa: E402
from sfcli.utils import jsonl_log  # noqa: E402
from urllib import request as urlrequest  # noqa: E402


def _load_fly_check():
    """Load the module under test the way `sf unity` loads it, by path.
    試験対象のモジュールを、`sf unity` と同じくパスから読み込む。"""
    import importlib.util
    path = REPO_ROOT / "tools" / "unity_check" / "fly_check.py"
    spec = importlib.util.spec_from_file_location("fly_check_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fly_check = _load_fly_check()


# ---------------------------------------------------------------------------
# The vehicle the stand-in page flies / 代役のページが飛ばす機体
# ---------------------------------------------------------------------------

class _Refused(Exception):
    """A command the page declines, with the reason the real page gives.
    ページが断る命令。本物のページが返すのと同じ理由を持つ。"""


class FakeVehicle:
    """A vehicle just detailed enough to be judged.

    It holds a virtual clock, an altitude and a flight state, and moves them
    the way the real pair does in outline: boot to IDLE_GROUND at 3 s, ARM on
    the rising edge of the flag, climb while the throttle is high, hold the
    height reached once ALT_HOLD is asked for, and come down when the throttle
    is at the bottom. Nothing here models physics -- it models the SHAPE of a
    flight, which is what the script's verdict reads.

    判定を受けられるだけの細かさを持つ機体。

    仮想時計・高度・飛行状態を持ち、本物の 2 者が粗く行うとおりに動かす。3 秒で
    IDLE_GROUND へ起動し、フラグの立ち上がりで ARM し、スロットルが高い間は上昇
    し、ALT_HOLD を求められたらその高さを保ち、スロットルが一番下なら降りる。
    ここに物理は無い。あるのは飛行の**形**で、台本の判定が読むのはそれである。
    """

    # How fast the vehicle climbs at the script's climb throttle [m/s].
    # 台本の上昇スロットルでの上昇の速さ [m/s]。
    CLIMB_RATE = 0.45

    # How fast it comes down with the throttle at the bottom in ALT_HOLD [m/s].
    # ALT_HOLD でスロットルが一番下のときの下降の速さ [m/s]。
    DESCENT_RATE = 0.30

    # Where the body rests on the floor [m], the vehicle's own half thickness.
    # 機体が床に静止する高さ [m]。自身の厚みの半分。
    RESTING_M = 0.0103

    def __init__(self, lifts: bool = True):
        self.sim_us = 0
        self.altitude = self.RESTING_M
        self.state = "INIT"
        self.mode = "STABILIZE"
        self.armed = False
        self.throttle = fly_check.ADC_CENTRE
        self.alt_hold = False
        self.hold_target = None
        # A vehicle that will not leave the floor, for the failing case.
        # 床を離れない機体。不合格の場合のため。
        self.lifts = lifts

    def advance_to(self, target_us: int) -> None:
        """Run the vehicle forward to a virtual time, in 2.5 ms ticks like the
        real loop does, so a stick change part way through is honoured.
        機体を、本物の輪と同じ 2.5 ms 刻みで、ある仮想時刻まで進める。途中の
        スティックの変更が効くようにするためである。"""
        while self.sim_us < target_us:
            step = min(2500, target_us - self.sim_us)
            self.sim_us += step
            self._tick(step * 1e-6)

    def _tick(self, seconds: float) -> None:
        self._advance_state()
        self._advance_altitude(seconds)

    def _advance_state(self) -> None:
        """The firmware's state machine in outline. DISARM is looked at first
        because it wins from any state -- a disarmed vehicle on the floor is
        IDLE_GROUND, whatever it was doing a moment ago.
        ファームの状態機械の粗い写し。DISARM を最初に見るのは、どの状態からでも
        それが勝つためである。DISARM された床の上の機体は、直前に何をしていようと
        IDLE_GROUND である。"""
        on_the_floor = self.altitude <= self.RESTING_M + 1e-6

        if not self.armed:
            booted = self.state != "INIT" or self.sim_us >= 3_000_000
            self.state = "IDLE_GROUND" if booted else "INIT"
            return

        if self.state in ("INIT", "IDLE_GROUND"):
            self.state = "ARMED_GROUND"
            return
        if self.state == "ARMED_GROUND" and self.altitude > 0.05:
            self.state = "TAKEOFF"
            return
        if self.state == "TAKEOFF" and self.altitude > 0.15:
            self.state = "FLYING"
            return
        landed = self.state in ("FLYING", "LANDING") and on_the_floor
        if landed:
            self.state = "ARMED_GROUND"

    def _advance_altitude(self, seconds: float) -> None:
        airborne = self.armed and self.lifts
        if not airborne:
            self.altitude = max(self.RESTING_M, self.altitude - self.DESCENT_RATE * seconds)
            return

        climbing = self.throttle > fly_check.ADC_CENTRE + 500
        if climbing:
            self.altitude += self.CLIMB_RATE * seconds
            self.hold_target = None
            return

        descending = self.alt_hold and self.throttle <= fly_check.ADC_CENTRE - 500
        if descending:
            self.altitude = max(self.RESTING_M,
                                self.altitude - self.DESCENT_RATE * seconds)
            self.hold_target = None
            return

        if self.alt_hold:
            # ALT_HOLD with the throttle centred means "hold this height", and
            # the first centred tick is the height it holds.
            # スロットル中央の ALT_HOLD は「この高さを保て」で、中央になった最初の
            # 刻みの高さがそれになる。
            if self.hold_target is None:
                self.hold_target = self.altitude
            self.altitude = self.hold_target
            return

        self.altitude = max(self.RESTING_M, self.altitude - 0.05 * seconds)

    # -- the commands the script sends ------------------------------------
    def set_sticks(self, args) -> dict:
        """`rc.set` carries the SWITCHES only. `arm` is refused, because the ARM
        flag is a momentary button the firmware toggles on its rising edge, not a
        state a frame can hold (`firmware/vehicle/tasks/state_task.cpp:339-357`).
        `rc.set` が運ぶのは**スイッチ**だけである。`arm` は断る。ARM のフラグは、
        ファームが立ち上がりでトグルするモーメンタリボタンであり、フレームが保持できる
        状態ではない（`firmware/vehicle/tasks/state_task.cpp:339-357`）。"""
        if args.get("arm"):
            raise _Refused(
                "rc.set cannot hold `arm`: the ARM flag is a momentary button "
                "the firmware toggles on its rising edge, not a state — use rc.arm")
        self.throttle = int(args.get("throttle", fly_check.ADC_CENTRE))
        self.alt_hold = bool(args.get("alt_hold", False))
        self.mode = "ALT_HOLD" if self.alt_hold else "STABILIZE"
        return {}

    def press_arm(self, args) -> dict:
        """Bring ARM to the state asked for by PRESSING the button, and only when
        the current state differs -- which is what makes `rc.arm` idempotent.

        The firmware TOGGLES on each rising edge and decides what a press means
        from its own state, so there is no bit to set to "armed": pressing at an
        already-armed vehicle would disarm it.

        求められた状態へ、ボタンを**押す**ことで持っていく。押すのは現在の状態が違う
        ときだけで、これが `rc.arm` を冪等にしている。

        ファームは立ち上がりごとに**トグル**し、押下の意味を自分の状態から決めるので、
        「armed にするために立てるビット」は無い。既に ARM された機体で押せば DISARM に
        なる。"""
        wants_armed = bool(args.get("armed", True))
        pressed = wants_armed != self.armed
        was_armed = self.armed
        if pressed:
            self.armed = wants_armed          # one press toggles / 1 回の押下でトグル
        return {"armed_requested": wants_armed,
                "pressed": pressed,
                "was_armed": was_armed,
                "sim_us": self.sim_us}

    def wait(self, args) -> dict:
        target = int(round(float(args.get("seconds", 0)) * 1e6)) \
            if not args.get("sim_us") else int(args["sim_us"])
        self.advance_to(target)
        return {"reached": True, "requested_sim_us": target,
                "sim_us": self.sim_us, "ticks": self.sim_us // 2500}

    def reset(self, _args) -> dict:
        self.altitude = self.RESTING_M
        self.hold_target = None
        return {}

    def power_cycle(self, _args) -> dict:
        """A new firmware from INIT with the clock back at zero, which is what
        lets the script's absolute virtual times mean the same thing on a page
        that has been open for a while.
        時計を 0 に戻し、INIT から新しいファームを起こす。しばらく開いていた
        ページでも、台本の絶対の仮想時刻が同じ意味を持つようになるのはこれに
        よる。"""
        self.sim_us = 0
        self.altitude = self.RESTING_M
        self.state = "INIT"
        self.mode = "STABILIZE"
        self.armed = False
        self.hold_target = None
        return {"power_cycles": 2}

    def state_json(self) -> dict:
        return {
            "sim_us": self.sim_us,
            "ticks": self.sim_us // 2500,
            "paused": False,
            "flight_state": self.state,
            "flight_mode": self.mode,
            "armed": self.armed,
            "battery_v": 4.19,
            "truth": {
                "position": [0.0, self.altitude, 0.0],
                "altitude_m": round(self.altitude, 4),
                "euler_deg": [0.0, 0.0, 0.0],
                "tilt_deg": 1.2,
                "velocity": [0.0, 0.0, 0.0],
                "angular_velocity": [0.0, 0.0, 0.0],
            },
            "estimate": {
                "position": [0.0, self.altitude, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
            },
            "range_down_m": round(self.altitude, 4),
            "range_down_valid": self.altitude > 0.030,
            "flow_quality": 0.90,
            "real_time_ratio": 0.98,
            "us_per_tick": 41.7,
            "fps": 60.0,
            "behind": False,
            "power_cycles": 1,
        }


# ---------------------------------------------------------------------------
# The stand-in page / ページの代役
# ---------------------------------------------------------------------------

class FlyingPage:
    """A page that answers the flight script's commands from a FakeVehicle,
    and posts the log lines the real page would (`src: "sim"` for a flight
    state, `src: "fw"` for the firmware's own).
    飛行の台本の命令に FakeVehicle から答え、本物のページが出すログの行を送る
    ページ（飛行状態には `src: "sim"`、ファーム自身のものには `src: "fw"`）。"""

    def __init__(self, base_url: str, vehicle: FakeVehicle, refuse=None):
        self.base_url = base_url
        self.vehicle = vehicle
        self.refuse = refuse or set()
        self.run_id = None
        self.handled = []
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self):
        self._thread.start()
        assert self._connected.wait(timeout=5), "stand-in page did not connect"
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join(timeout=5)

    def _loop(self):
        self.run_id = _get(self.base_url + "/api/hello")["run_id"]
        first = _get(self.base_url + "/api/cmd/next?wait=0.2")
        self._connected.set()
        self._handle(first)
        while not self._stop.is_set():
            try:
                self._handle(_get(self.base_url + "/api/cmd/next?wait=0.5"))
            except Exception:
                return

    def _handle(self, answer):
        if not answer or not answer.get("command"):
            return
        cmd_id, command = answer["cmd_id"], answer["command"]
        args = answer.get("args") or {}
        self.handled.append((cmd_id, command, args))

        before = self.vehicle.state
        result = self._run(command, args)
        self._post_log(cmd_id, command, before)
        result["cmd_id"] = cmd_id
        _post(self.base_url + "/api/cmd/result", result)

    def _run(self, command, args):
        if command in self.refuse:
            return {"ok": False, "error": f"{command} is refused by this page"}

        handlers = {
            "world.load": lambda a: {"name": a.get("name"), "obstacles": 0},
            "sim.reset": self.vehicle.reset,
            "sim.power_cycle": self.vehicle.power_cycle,
            "sim.wait": self.vehicle.wait,
            "rc.set": self.vehicle.set_sticks,
            "rc.arm": self.vehicle.press_arm,
            "vehicle.state": lambda _a: self.vehicle.state_json(),
        }
        handler = handlers.get(command)
        if handler is None:
            return {"ok": False, "error": f"unknown command: {command}"}
        try:
            return {"ok": True, "data": handler(args)}
        except _Refused as refused:
            return {"ok": False, "error": str(refused)}

    def _post_log(self, cmd_id, command, state_before):
        """One `src: "sim"` line per command, plus a flight-state line and a
        firmware line when the state changed -- the shape the page produces, so
        `sf unity logs --cmd <id>` has something to recover.
        命令ごとに `src: "sim"` の行を 1 つ、状態が変わったときは飛行状態の行と
        ファームの行を送る。ページが出す形であり、
        `sf unity logs --cmd <id>` が拾うものになる。"""
        lines = [{
            "ts": jsonl_log.utc_now(), "level": "debug", "src": "cmd",
            "event": "cmd.finished", "run_id": self.run_id, "cmd_id": cmd_id,
            "sim_us": self.vehicle.sim_us, "msg": f"{command} succeeded",
        }]

        if self.vehicle.state != state_before:
            lines.append({
                "ts": jsonl_log.utc_now(), "level": "info", "src": "sim",
                "event": "sim.flight_state", "run_id": self.run_id,
                "cmd_id": cmd_id, "sim_us": self.vehicle.sim_us,
                "msg": f"{state_before} -> {self.vehicle.state}",
                "data": {"state": self.vehicle.state,
                         "previous_state": state_before},
            })
            lines.append({
                "ts": jsonl_log.utc_now(), "level": "info", "src": "fw",
                "event": "fw.log", "run_id": self.run_id, "cmd_id": cmd_id,
                "sim_us": self.vehicle.sim_us, "tag": "StateManager",
                "msg": f"state -> {self.vehicle.state}",
            })

        _post(self.base_url + "/api/log", {"lines": lines})


# ---------------------------------------------------------------------------
# Fixtures and helpers / 前準備と補助
# ---------------------------------------------------------------------------

def _get(url):
    with urlrequest.urlopen(urlrequest.Request(url, method="GET"),
                            timeout=10) as response:
        return json.loads(response.read() or b"{}")


def _post(url, payload):
    request = urlrequest.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urlrequest.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read() or b"{}")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def logs_root(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo"
    (fake_root / "logs" / "unity").mkdir(parents=True)
    monkeypatch.setattr(unity.paths, "root", lambda: fake_root)
    return fake_root


@pytest.fixture
def server(logs_root, tmp_path):
    """A running `sf unity serve`, the same fixture shape `test_unity.py` uses.
    動いている `sf unity serve`。`test_unity.py` と同じ形の前準備。"""
    build_dir = tmp_path / "webgl"
    build_dir.mkdir()
    (build_dir / "index.html").write_text("<html>stand-in</html>", encoding="utf-8")

    port = _free_port()
    args = argparse.Namespace(dir=str(build_dir), port=port, no_browser=True,
                              world=None, coi=False)
    thread = threading.Thread(target=unity.run_serve, args=(args,), daemon=True)
    thread.start()

    run_id, deadline = None, time.monotonic() + 10
    while run_id is None and time.monotonic() < deadline:
        run_id = unity._read_latest_run_id()
        if run_id is None:
            time.sleep(0.02)
    assert run_id is not None, "server did not start"

    while time.monotonic() < deadline:
        try:
            _get(f"http://127.0.0.1:{port}/api/hello")
            break
        except Exception:
            time.sleep(0.02)

    yield argparse.Namespace(port=port, run_id=run_id,
                             base_url=f"http://127.0.0.1:{port}",
                             log_path=unity._run_log_path(run_id))

    import ctypes
    ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(thread.ident), ctypes.py_object(KeyboardInterrupt))
    time.sleep(0.6)
    thread.join(timeout=5)


def _fly(hold_seconds=2.0, world="empty_room"):
    """Run the flight script through the real sender. A short hold keeps the
    test quick; the script's shape is the same at any length.
    本物の送り手を通して飛行の台本を走らせる。保持を短くして試験を速く保つ。
    台本の形は長さによらず同じである。"""
    return fly_check.run(unity.make_sender(), world, hold_seconds)


# ---------------------------------------------------------------------------
# (a) A flight that works is a pass / 成立する飛行は合格
# ---------------------------------------------------------------------------

def test_a_working_flight_passes_with_its_numbers(server):
    with FlyingPage(server.base_url, FakeVehicle()):
        report = _fly()

    assert report["pass"], [c for c in report["checks"] if not c["pass"]]
    assert report["altitude"]["peak_m"] > fly_check.HOLD_MINIMUM_M
    assert report["altitude"]["final_m"] < fly_check.LANDED_M
    assert report["pacing"]["us_per_tick_max"] == pytest.approx(41.7)


def test_the_flight_goes_through_the_expected_states(server):
    with FlyingPage(server.base_url, FakeVehicle()):
        report = _fly()

    seen = [t["to"] for t in report["transitions"]]
    for expected in ("IDLE_GROUND", "ARMED_GROUND", "FLYING"):
        assert expected in seen, f"{expected} missing from {seen}"

    # Every transition carries the virtual time it happened at, which is what
    # makes the log's `sim_us` and this report line up.
    # どの遷移も起きた仮想時刻を持つ。ログの `sim_us` とこの報告が揃うのはこれに
    # よる。
    assert all(isinstance(t["sim_us"], int) for t in report["transitions"])


def test_the_hold_is_in_altitude_hold_mode(server):
    with FlyingPage(server.base_url, FakeVehicle()):
        report = _fly()

    holding = [s for s in report["samples"] if s["phase"] == "holding"]
    assert holding, "no samples were taken during the hold"
    assert all(s["flight_mode"] == "ALT_HOLD" for s in holding)
    assert all(s["flight_state"] == "FLYING" for s in holding)


# ---------------------------------------------------------------------------
# (b) A vehicle that never lifts is a fail / 浮かない機体は不合格
# ---------------------------------------------------------------------------

def test_a_vehicle_that_never_leaves_the_floor_fails(server):
    with FlyingPage(server.base_url, FakeVehicle(lifts=False)):
        report = _fly()

    assert not report["pass"]
    failed = {c["name"] for c in report["checks"] if not c["pass"]}
    assert "held_the_altitude_band" in failed
    assert "stayed_flying" in failed


def test_a_failing_flight_still_reports_what_it_measured(server):
    """A fail must say the numbers too: "it did not fly" is not actionable,
    "it held 0.010 m when 0.15 m was wanted" is.
    不合格も数値を述べなければならない。「飛ばなかった」では手が出ず、
    「0.15 m を求めたのに 0.010 m だった」なら手が出る。"""
    with FlyingPage(server.base_url, FakeVehicle(lifts=False)):
        report = _fly()

    band = next(c for c in report["checks"] if c["name"] == "held_the_altitude_band")
    assert "m" in band["measured"]
    assert band["wanted"] == f"{fly_check.HOLD_MINIMUM_M}..{fly_check.HOLD_MAXIMUM_M} m"


# ---------------------------------------------------------------------------
# (c) Each command carries a cmd_id into the log / 各命令が cmd_id をログへ運ぶ
# ---------------------------------------------------------------------------

def test_every_command_of_the_flight_has_its_own_cmd_id(server):
    with FlyingPage(server.base_url, FakeVehicle()):
        report = _fly()

    ids = [entry["cmd_id"] for entry in report["commands"]]
    assert all(jsonl_log.is_cmd_id(value) for value in ids), ids
    assert len(set(ids)) == len(ids), "two commands shared a cmd_id"


def test_one_commands_cmd_id_recovers_its_flow_from_the_log(server):
    with FlyingPage(server.base_url, FakeVehicle()):
        report = _fly()

    arm_id = fly_check.cmd_id_of(report, "rc.arm")
    assert arm_id is not None

    records, _broken = jsonl_log.read_records(server.log_path)
    flow = [r for r in records if r.get("cmd_id") == arm_id]
    sources = {r["src"] for r in flow}
    assert {"cli", "server"} <= sources, sources
    assert any(r["event"] == "cmd.issued" for r in flow)
    assert any(r["event"] == "cmd.forwarded" for r in flow)
    assert any(r["event"] == "cmd.completed" for r in flow)


def test_the_firmware_line_for_a_transition_carries_the_same_cmd_id(server):
    """The point of the whole scheme: a firmware line raised while a command
    was being handled is findable by that command's id.
    この仕組みの要点。命令の処理中にファームが出した行が、その命令の識別子で
    引けること。"""
    with FlyingPage(server.base_url, FakeVehicle()):
        report = _fly()

    records, _broken = jsonl_log.read_records(server.log_path)
    firmware = [r for r in records if r.get("src") == "fw" and r.get("cmd_id")]
    assert firmware, "no firmware line carried a cmd_id"
    assert all(isinstance(r.get("sim_us"), int) for r in firmware)

    issued = {entry["cmd_id"] for entry in report["commands"]}
    assert {r["cmd_id"] for r in firmware} <= issued


def test_no_line_the_page_posted_was_rejected(server):
    with FlyingPage(server.base_url, FakeVehicle()):
        _fly()

    records, _broken = jsonl_log.read_records(server.log_path)
    rejected = [r for r in records if r.get("event") == "log.rejected"]
    assert rejected == [], rejected


# ---------------------------------------------------------------------------
# (d) The subcommand's exit code / サブコマンドの終了コード
# ---------------------------------------------------------------------------

def _run_check_fly(capsys, hold_seconds=2.0, world="empty_room"):
    args = argparse.Namespace(world=world, hold_seconds=hold_seconds)
    capsys.readouterr()
    exit_code = unity.run_check_fly(args)
    return exit_code, capsys.readouterr().out


def test_check_fly_exits_zero_and_prints_the_report_as_json(server, capsys):
    with FlyingPage(server.base_url, FakeVehicle()):
        exit_code, printed = _run_check_fly(capsys)

    assert exit_code == 0
    # The JSON report is on stdout: `console` writes elsewhere, so what is left
    # on stdout must parse on its own.
    # JSON の報告は標準出力にある。`console` は別へ書くので、標準出力に残った
    # ものはそれだけで読めなければならない。
    start = printed.index("{")
    report = json.loads(printed[start:printed.rindex("}") + 1])
    assert report["pass"] is True
    assert report["world"] == "empty_room"


def test_check_fly_exits_non_zero_when_the_flight_fails(server, capsys):
    with FlyingPage(server.base_url, FakeVehicle(lifts=False)):
        exit_code, printed = _run_check_fly(capsys)

    assert exit_code == 1
    assert '"pass": false' in printed


def test_check_fly_without_a_server_says_how_to_start_one(logs_root, capsys):
    exit_code = unity.run_check_fly(
        argparse.Namespace(world="empty_room", hold_seconds=2.0))
    printed = capsys.readouterr().out
    assert exit_code == 1
    assert "sf unity serve" in printed


# ---------------------------------------------------------------------------
# (e) A refused command stops the check / 断られた命令は確認を止める
# ---------------------------------------------------------------------------

def test_a_page_that_refuses_a_command_stops_the_check_with_that_reason(server):
    with FlyingPage(server.base_url, FakeVehicle(), refuse={"rc.arm"}):
        report = _fly()

    assert not report["pass"]
    assert "rc.arm" in report["error"]
    ran = next(c for c in report["checks"] if c["name"] == "the_flight_ran")
    assert not ran["pass"]


# ---------------------------------------------------------------------------
# The ARM flag is a momentary button / ARM のフラグはモーメンタリボタン
# ---------------------------------------------------------------------------

def test_rc_arm_presses_only_when_the_state_differs():
    """`rc.arm` is idempotent: it presses the button only when the firmware is
    not already in the state asked for. Asking twice for `armed=true` must leave
    the vehicle ARMED, not armed and then disarmed -- the firmware TOGGLES on
    each rising edge, so a second press at an armed vehicle would disarm it.

    `rc.arm` は冪等である。ファームが既に求められた状態でないときだけボタンを押す。
    `armed=true` を 2 回求めても機体は ARM のままでなければならない。ARM したうえで
    DISARM されるのではない。ファームは立ち上がりごとに**トグル**するので、ARM された
    機体での 2 回目の押下は DISARM になる。"""
    vehicle = FakeVehicle()

    first = vehicle.press_arm({"armed": True})
    assert first["pressed"] is True
    assert first["was_armed"] is False
    assert vehicle.armed is True

    second = vehicle.press_arm({"armed": True})
    assert second["pressed"] is False, "rc.arm pressed at an already-armed vehicle"
    assert vehicle.armed is True, "the second rc.arm disarmed the vehicle"

    off = vehicle.press_arm({"armed": False})
    assert off["pressed"] is True
    assert vehicle.armed is False


def test_rc_set_refuses_to_hold_the_arm_flag():
    """`rc.set` cannot carry `arm`. Holding the bit down is ONE press to the
    firmware, so a script that wrote `arm: true` got a single press whose effect
    depended on what the vehicle happened to be doing.
    `rc.set` は `arm` を運べない。ビットを押し下げ続けることはファームには**1 回**の
    押下なので、`arm: true` と書いた台本は、機体がたまたま何をしていたかで効果の変わる
    押下 1 回を得ていた。"""
    vehicle = FakeVehicle()

    with pytest.raises(_Refused) as refusal:
        vehicle.set_sticks({"throttle": fly_check.ADC_CENTRE, "arm": True})

    assert "rc.arm" in str(refusal.value)


def test_the_flight_script_never_puts_arm_in_rc_set(server):
    """The real script must press ARM with `rc.arm` alone. A page that refuses
    `arm` in `rc.set` therefore still flies a complete pass.
    本物の台本は ARM を `rc.arm` だけで押さねばならない。よって `rc.set` の `arm` を
    断るページでも、飛行は最後まで合格する。"""
    with FlyingPage(server.base_url, FakeVehicle()):
        report = _fly()

    assert report["pass"], report.get("error")


def test_a_clock_that_did_not_go_back_to_zero_stops_the_check(server):
    """A page whose power cycle left the clock where it was would make every
    wait return at once, on a vehicle that had long since booted. The check
    must say so rather than judge that flight.
    電源を入れ直しても時計が動かなかったページでは、どの待ちもその場で返り、
    しかも機体はとうに起動を終えている。確認はその飛行を判定するのではなく、
    その旨を述べねばならない。"""
    vehicle = FakeVehicle()
    vehicle.advance_to(30_000_000)
    vehicle.power_cycle = lambda _args: {"power_cycles": 2}  # forgets the clock

    with FlyingPage(server.base_url, vehicle):
        report = _fly()

    assert not report["pass"]
    assert "virtual clock" in report["error"]


def test_a_page_missing_vehicle_state_is_reported_not_guessed_at(server):
    with FlyingPage(server.base_url, FakeVehicle(), refuse={"vehicle.state"}):
        report = _fly()

    assert not report["pass"]
    assert "vehicle.state" in report["error"]


# ---------------------------------------------------------------------------
# (f) The flight is paced by virtual time / 飛行は仮想時刻で刻まれる
# ---------------------------------------------------------------------------

def test_the_script_waits_on_virtual_time_not_the_wall_clock(server):
    vehicle = FakeVehicle()
    with FlyingPage(server.base_url, vehicle) as page:
        report = _fly(hold_seconds=2.0)

    waits = [args for (_id, command, args) in page.handled
             if command == "sim.wait"]
    assert waits, "the script never called sim.wait"

    # Every wait names an ABSOLUTE virtual time, not a duration: a wait that
    # ran out has to be resumable by asking for the same moment again, and a
    # duration would restart the clock from wherever it happened to be.
    # どの待ちも、長さではなく**絶対の**仮想時刻を名指しする。尽きた待ちは同じ
    # 時点をもう一度求めて再開できなければならず、長さで渡すと、時計がたまたま
    # 居た場所から数え直してしまう。
    assert all("sim_us" in w for w in waits), waits
    assert all(w["timeout_s"] <= fly_check.WAIT_CHUNK_SECONDS for w in waits)

    # They only ever go forward: a script that asked to go back would be
    # reading a state from before the step it just took.
    # 前へしか進まない。戻るよう求める台本は、いま踏んだ手順より前の状態を読む
    # ことになる。
    targets = [int(w["sim_us"]) for w in waits]
    assert targets == sorted(targets), targets
    assert targets[0] == int(fly_check.ARM_AT_S * 1e6)

    # The flight covered the virtual span the script asked for, which a
    # wall-clock sleep of the same duration would not have guaranteed.
    # 飛行は台本が求めた仮想の時間幅を進んだ。同じ長さの実時間の待ちでは、それは
    # 保証されない。
    assert vehicle.sim_us >= targets[-1]


def test_a_longer_hold_takes_more_samples_over_more_virtual_time(server):
    with FlyingPage(server.base_url, FakeVehicle()):
        short = _fly(hold_seconds=2.0)
    with FlyingPage(server.base_url, FakeVehicle()):
        longer = _fly(hold_seconds=6.0)

    short_span = _hold_span(short)
    longer_span = _hold_span(longer)
    assert longer_span > short_span + 3_000_000, (short_span, longer_span)


def _hold_span(report) -> int:
    """How much virtual time the hold covered [us]. / 保持が覆った仮想時間 [us]。"""
    holding = [s["sim_us"] for s in report["samples"] if s["phase"] == "holding"]
    return max(holding) - min(holding) if len(holding) > 1 else 0


# ---------------------------------------------------------------------------
# The release-build inspection / 配布用ビルドの検査
# ---------------------------------------------------------------------------

def test_a_clean_release_build_passes_inspection(tmp_path):
    build = tmp_path / "release"
    build.mkdir()
    (build / unity.BUILD_MANIFEST).write_text(json.dumps({
        "release": True,
        "define_symbols": "",
        "assemblies": ["StampFly.App", "StampFly.Core", "StampFly.Sim"],
    }), encoding="utf-8")
    (build / "index.html").write_text("<html>a page</html>", encoding="utf-8")

    assert unity.inspect_release_build(build) == []


def test_a_release_build_carrying_the_relay_assembly_fails(tmp_path):
    build = tmp_path / "release"
    build.mkdir()
    (build / unity.BUILD_MANIFEST).write_text(json.dumps({
        "release": True,
        "define_symbols": "",
        "assemblies": ["StampFly.App", "StampFly.Remote"],
    }), encoding="utf-8")

    problems = unity.inspect_release_build(build)
    assert any("StampFly.Remote" in problem for problem in problems), problems


def test_a_release_build_that_defined_the_symbol_fails(tmp_path):
    build = tmp_path / "release"
    build.mkdir()
    (build / unity.BUILD_MANIFEST).write_text(json.dumps({
        "release": True,
        "define_symbols": "SOMETHING;STAMPFLY_REMOTE",
        "assemblies": ["StampFly.App"],
    }), encoding="utf-8")

    problems = unity.inspect_release_build(build)
    assert any("STAMPFLY_REMOTE" in problem for problem in problems), problems


def test_a_release_build_whose_page_mentions_the_jslib_fails(tmp_path):
    build = tmp_path / "release"
    build.mkdir()
    (build / unity.BUILD_MANIFEST).write_text(json.dumps({
        "release": True, "define_symbols": "", "assemblies": ["StampFly.App"],
    }), encoding="utf-8")
    (build / "Build").mkdir()
    (build / "Build" / "WebGL.loader.js").write_text(
        "function SfuRemoteStart(){}", encoding="utf-8")

    problems = unity.inspect_release_build(build)
    assert any("SfuRemoteStart" in problem for problem in problems), problems


def test_a_build_with_no_manifest_cannot_pass(tmp_path):
    """A missing manifest means the check had nothing to look at. Passing on
    that would make a broken check indistinguishable from a clean build.
    目録が無いことは、検査が見るものを持たなかったことを意味する。それで合格に
    すると、壊れた検査と綺麗なビルドが見分けられなくなる。"""
    build = tmp_path / "release"
    build.mkdir()
    problems = unity.inspect_release_build(build)
    assert problems and unity.BUILD_MANIFEST in problems[0]
