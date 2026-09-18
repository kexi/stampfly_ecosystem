"""
test_pilot_loop.py - the loop survives a broken Judge, records one JSON
object per decision, and replays a recorded flight without transmitting.
test_pilot_loop.py - 壊れた Judge でもループが止まらないこと、1 判断につき
JSON を 1 行記録すること、記録済みの飛行を送信せずに再生できること。
"""

import json

from sfpilot.arbiter import VERDICT_HOVER, VERDICT_LAND
from sfpilot.config import DEFAULT_CONFIG
from sfpilot.judge import FakeJudge
from sfpilot.link import Sample
from sfpilot.pilot import Pilot, replay
from sfpilot.trace import Trace


class StubLink:
    """A Link that serves a fixed list of samples and records sends.
    決まったサンプル列を供給し、送信を記録するだけの Link。"""

    def __init__(self, samples):
        self._samples = list(samples)
        self.sent = []
        self._index = 0
        self.sample_count = len(self._samples)

    def read_samples(self):
        if self._index >= len(self._samples):
            return []
        sample = self._samples[self._index]
        self._index += 1
        return [sample]

    def send_rc(self, a, b, c, d):
        self.sent.append(f"rc {a} {b} {c} {d}")

    def send_command(self, line):
        self.sent.append(line)

    def priority(self, line):
        self.sent.append(line)

    def close(self):
        pass


def _healthy(count=40, **fields):
    values = {"altitude_m": 0.8, "vel_n": 0.0, "vel_e": 0.0, "vel_d": 0.0,
              "roll": 0.0, "pitch": 0.0, "battery_pct": 80.0, "tof_m": 0.8}
    values.update(fields)
    return [Sample(t=i * 0.02, **values) for i in range(count)]


def test_a_judge_that_raises_does_not_stop_the_loop():
    """An exception from the judging backend becomes hovering, and the
    monitor keeps running -- a broken backend must not end a flight.
    判断層の例外は待機になり、監視は動き続けること。壊れた backend が
    飛行を終わらせてはならない。"""
    link = StubLink(_healthy(30))
    pilot = Pilot(link, FakeJudge(raises=RuntimeError("backend down")), DEFAULT_CONFIG)
    for step in range(30):
        pilot.step(now=step * 0.02)
    actions = {row["verdict"].action for row in pilot.decisions}
    assert actions <= {VERDICT_HOVER}


def test_dangerous_battery_lands_without_consulting_the_judge():
    """The immediate safety rule commands a landing and the Judge is
    never asked to approve it.
    即時安全則が着陸を指示し、その可否を Judge に問わないこと。"""
    danger = DEFAULT_CONFIG.monitor.battery_danger_pct - 1.0
    link = StubLink(_healthy(5, battery_pct=danger))
    fake = FakeJudge()
    pilot = Pilot(link, fake, DEFAULT_CONFIG)
    row = pilot.step(now=0.0)
    assert row["verdict"].action == VERDICT_LAND
    assert row["verdict"].source == "monitor"
    assert "land" in link.sent


def test_emergency_never_reaches_the_link():
    """Across a whole run, no command sent to the vehicle is `emergency`.
    実行を通して、機体へ送る指令に `emergency` が一度も出ないこと。"""
    link = StubLink(_healthy(60))
    pilot = Pilot(link, FakeJudge(), DEFAULT_CONFIG)
    for step in range(60):
        pilot.step(now=step * 0.02)
    assert all(not line.startswith("emergency") for line in link.sent)


def test_each_decision_is_one_json_object_on_its_own_line(tmp_path):
    """The trace is JSON Lines: every line parses on its own, so `grep`
    and `jq` can read a flight without a parser for the whole file.
    記録が JSON Lines であること: 各行が単独で解釈でき、ファイル全体を
    解析せずとも `grep`・`jq` で飛行を追えること。"""
    link = StubLink(_healthy(20))
    trace = Trace(path=tmp_path / "trace.jsonl")
    pilot = Pilot(link, FakeJudge(), DEFAULT_CONFIG, trace=trace)
    for step in range(20):
        pilot.step(now=step * 0.02)
    trace.close()

    lines = [ln for ln in trace.path.read_text(encoding="utf-8").splitlines() if ln]
    assert lines
    for line in lines:
        row = json.loads(line)
        assert set(row) >= {"trace_id", "t_mono", "state", "questions",
                            "answers", "latency_ms", "arbiter_verdict", "command"}


def test_the_trace_records_no_secrets(tmp_path, monkeypatch):
    """No API key or environment variable reaches the trace file, because
    users attach these files to bug reports.
    API キーや環境変数が記録に入らないこと。利用者がこのファイルを不具合
    報告に添付するため。"""
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-should-never-be-written")
    link = StubLink(_healthy(10))
    trace = Trace(path=tmp_path / "trace.jsonl")
    pilot = Pilot(link, FakeJudge(), DEFAULT_CONFIG, trace=trace)
    for step in range(10):
        pilot.step(now=step * 0.02)
    trace.close()
    text = trace.path.read_text(encoding="utf-8")
    assert "sk-should-never-be-written" not in text
    assert "TYPESAFE_API_KEY" not in text


def test_replay_records_commands_without_transmitting():
    """Replay drives the whole decision chain while the link only records
    what would have been sent.
    再生は判断の流れ全体を動かすが、リンクは送られるはずだった指令を記録
    するだけであること。"""
    link = StubLink(_healthy(50))
    pilot = replay(link, FakeJudge(), DEFAULT_CONFIG)
    assert pilot.decisions
    assert link.sent
    assert all(line.startswith(("rc ", "land", "stop")) for line in link.sent)
