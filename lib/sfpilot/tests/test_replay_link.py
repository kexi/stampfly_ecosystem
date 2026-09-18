"""
test_replay_link.py - a recorded flight from the repository plays back
through the judging layers and produces one JSON object per decision.
test_replay_link.py - リポジトリ内の記録済み飛行を判断層に通し、1 判断に
つき JSON オブジェクトが 1 つ出ること。
"""

import json
from pathlib import Path

import pytest

from sfpilot.config import DEFAULT_CONFIG
from sfpilot.judge import FakeJudge
from sfpilot.link import ReplayLink
from sfpilot.pilot import replay
from sfpilot.trace import Trace

pytest.importorskip("pandas", reason="sflog needs pandas / sflog は pandas を必要とする")

REPO_ROOT = Path(__file__).resolve().parents[3]
FLIGHT_LOG = REPO_ROOT / "analysis" / "datasets" / "flightlog" / \
    "vehicle_hover_20260908T121243.sflog.zip"

pytestmark = pytest.mark.skipif(
    not FLIGHT_LOG.exists(),
    reason=f"flight log not present: {FLIGHT_LOG}",
)


def test_the_log_loads_into_time_ordered_samples():
    """Samples come out in time order with an altitude to judge.
    サンプルが時刻順に、判断できる高度つきで出てくること。"""
    link = ReplayLink(FLIGHT_LOG)
    assert link.sample_count > 0
    samples = []
    while len(samples) < 200:
        batch = link.read_samples()
        if not batch:
            break
        samples.extend(batch)
    assert samples
    times = [s["t"] for s in samples]
    assert times == sorted(times)
    assert any("altitude_m" in s for s in samples)


def test_sends_are_recorded_and_never_transmitted():
    """ReplayLink keeps every command in `sent` and opens no socket.
    ReplayLink は全指令を `sent` に残し、ソケットを開かないこと。"""
    link = ReplayLink(FLIGHT_LOG)
    link.send_rc(0, 0, 0, 0)
    link.send_command("land")
    assert link.sent == ["rc 0 0 0 0", "land"]


def test_replaying_the_log_writes_one_json_object_per_decision(tmp_path):
    """Replaying a real flight produces a trace whose every line is one
    complete JSON decision record.
    実際の飛行を再生すると、各行が 1 判断分の完全な JSON になっている記録が
    得られること。"""
    link = ReplayLink(FLIGHT_LOG)
    trace = Trace(path=tmp_path / "replay.jsonl")
    pilot = replay(link, FakeJudge(), DEFAULT_CONFIG, trace=trace, max_steps=300)
    trace.close()

    assert pilot.decisions
    lines = [ln for ln in trace.path.read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == len(pilot.decisions)
    for line in lines:
        row = json.loads(line)
        assert row["trace_id"]
        assert "flight" in row["state"]
        assert row["arbiter_verdict"]["action"]
