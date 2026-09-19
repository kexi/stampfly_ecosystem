"""
What the live browser view guarantees: the same events as the trace, no
secrets on the wire, a server that frees its port, and a flight that never
waits for a viewer.

ライブ表示が保証すること: 記録と同じ出来事であること、秘密が流れないこと、
サーバがポートを解放すること、飛行が閲覧者を待たないこと。

No emulator and no API key: every test here drives the pieces directly.
エミュレータも API キーも使わない。各試験は部品を直接動かす。
"""

import json
import queue
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from sfcli.commands.pilot_web import LiveStatus, PilotWebServer
from sfpilot.events import (
    EVENT_DECISION, EVENT_SAMPLE, EventBus, sample_payload,
)
from sfpilot.link import Sample
from sfpilot.trace import Trace


def _free_port() -> int:
    """A port the OS says is free right now. / いま空いているポート。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _get(url: str, timeout: float = 5.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


# =============================================================================
# The event stream carries what the trace carries / 記録と同じ内容が流れること
# =============================================================================

class _Verdict:
    def __init__(self, action="accept", reason="looks fine", source="jev"):
        self.action = action
        self.reason = reason
        self.source = source
        self.accepted_answer = "continue"


def test_a_decision_reaches_the_bus_as_the_row_the_trace_wrote(tmp_path):
    """The page and `logs/pilot/*.jsonl` show one row, not two assemblies.

    The event's payload must equal the row the Trace returned. If the two
    were built separately they could disagree about a flight, and the
    disagreement would surface only while someone was watching.

    ページと `logs/pilot/*.jsonl` が示すのは 1 つの行であり、別々に組み立てた
    2 つではないこと。

    出来事の中身は Trace が返した行と一致しなければならない。別々に組み立てて
    いれば飛行について食い違いうるし、その食い違いは誰かが見ているときにしか
    表に出ない。
    """
    bus = EventBus()
    sink = queue.Queue()
    bus.subscribe(sink)
    trace = Trace(path=tmp_path / "t.jsonl", bus=bus)

    row = trace.write({"flight": {"phase": "flying"}}, None, _Verdict(), "rc 0 0 0 0")
    trace.close()

    event = sink.get_nowait()
    assert event["kind"] == EVENT_DECISION
    for key, value in row.items():
        assert event[key] == value

    # And the file says the same thing / ファイルも同じことを述べる
    written = json.loads((tmp_path / "t.jsonl").read_text(encoding="utf-8").strip())
    assert written["arbiter_verdict"]["action"] == event["arbiter_verdict"]["action"]


def test_no_api_key_or_environment_reaches_the_stream(tmp_path, monkeypatch):
    """Nothing a viewer receives contains a key or an environment variable.

    The page is one screenshot away from being shared, so it follows the
    trace file's rule: the key is never written, never published.

    閲覧者が受け取るものに、キーも環境変数も含まれないこと。

    ページは画面の写真 1 枚で共有されうるので、記録ファイルと同じ規則に従う
    — キーは書かれず、配信もされない。
    """
    secret = "sk-live-do-not-leak-0123456789"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)

    bus = EventBus()
    sink = queue.Queue()
    bus.subscribe(sink)
    trace = Trace(path=tmp_path / "t.jsonl", bus=bus)
    trace.write({"flight": {"phase": "flying"}}, None, _Verdict(), "rc 0 0 0 0")
    trace.close()

    serialized = json.dumps(sink.get_nowait(), ensure_ascii=False)
    assert secret not in serialized
    assert "TYPESAFE_API_KEY" not in serialized


def test_a_sample_carries_only_the_named_fields():
    """A Sample is copied field by field, never wholesale.

    A Sample is a dict whose keys vary by source, so copying it as-is would
    put whatever the source happened to carry onto the page.

    Sample は項目ごとに写し、丸ごとは写さないこと。

    Sample は入力源ごとにキーの異なる dict なので、そのまま写すと入力源が
    たまたま持っていたものが画面に出てしまう。
    """
    sample = Sample(t=1.5, altitude_m=0.8, roll=0.01, battery_pct=77.0)
    sample["secret_debug_token"] = "must-not-appear"

    payload = sample_payload(sample)

    assert payload["altitude_m"] == 0.8
    assert payload["battery_pct"] == 77.0
    assert "secret_debug_token" not in payload


# =============================================================================
# The flight never waits for a viewer / 飛行が閲覧者を待たないこと
# =============================================================================

def test_publishing_with_no_subscribers_costs_nothing():
    """A flight nobody is watching still publishes without error.
    誰も見ていない飛行でも、配信は例外なく行えること。"""
    bus = EventBus()

    for index in range(1000):
        bus.publish(EVENT_SAMPLE, {"t": index})

    assert bus.subscriber_count == 0


def test_a_full_viewer_queue_drops_events_instead_of_blocking():
    """A viewer that stopped reading loses events; the publisher does not stall.

    This is the rule that keeps the 50Hz monitor loop at 50Hz: a live view
    that skipped a frame is correct, a loop that missed its deadline is not.

    読むのをやめた閲覧者は出来事を失い、配信側は止まらないこと。

    これが 50Hz の監視ループを 50Hz に保つ規則である。表示が 1 コマ飛ぶのは
    正しく、ループが周期を落とすのは正しくない。
    """
    bus = EventBus()
    tiny = queue.Queue(maxsize=2)
    bus.subscribe(tiny)

    started = time.monotonic()
    for index in range(500):
        bus.publish(EVENT_SAMPLE, {"t": index})
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, "publishing blocked on a full viewer queue"
    assert tiny.qsize() <= 2
    # The newest event survived; the oldest were dropped.
    # 残ったのは最新のもので、古いものが捨てられた。
    newest = None
    while not tiny.empty():
        newest = tiny.get_nowait()
    assert newest["t"] == 499


def test_a_slow_viewer_does_not_delay_the_publisher():
    """A viewer whose reader thread sleeps cannot slow the flight loop.
    読み取りスレッドが眠っている閲覧者が、飛行ループを遅らせられないこと。"""
    bus = EventBus()
    slow = queue.Queue(maxsize=4)
    bus.subscribe(slow)

    def publish_many():
        for index in range(2000):
            bus.publish(EVENT_SAMPLE, {"t": index})

    worker = threading.Thread(target=publish_many)
    started = time.monotonic()
    worker.start()
    worker.join(timeout=5)
    elapsed = time.monotonic() - started

    assert not worker.is_alive(), "the publisher never finished"
    assert elapsed < 2.0


# =============================================================================
# The server starts and cleans up / サーバの起動と後始末
# =============================================================================

def test_the_server_serves_the_page_and_then_frees_its_port():
    """The page is served, and the port is reusable the moment it stops.

    A port left bound would make the next `sf pilot --web` on the same port
    fail for as long as this process lived.

    ページが配信され、停止した時点でポートが再利用できること。

    ポートを握ったままだと、同じポートでの次の `sf pilot --web` が本プロセスの
    生存中ずっと失敗する。
    """
    port = _free_port()
    bus = EventBus()
    server = PilotWebServer(bus, port=port, open_browser=False).start()
    try:
        body = _get(f"http://127.0.0.1:{port}/")
        assert b"<html" in body.lower()
    finally:
        server.stop()

    # Binding again must succeed immediately / すぐに再び bind できること
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))


def test_the_server_binds_the_loopback_address_only():
    """A flight in progress is not put on the network by default.
    進行中の飛行を、既定で網の上に出さないこと。"""
    port = _free_port()
    server = PilotWebServer(EventBus(), port=port, open_browser=False).start()
    try:
        assert server.url.startswith("http://127.0.0.1:")
        # The listening socket is bound to loopback, not 0.0.0.0.
        # 待ち受けソケットは 0.0.0.0 ではなくループバックに bind されている。
        assert server._httpd.server_address[0] == "127.0.0.1"
    finally:
        server.stop()


def test_stopping_twice_is_safe():
    """Shutting a server down twice does not raise.
    サーバを 2 回止めても例外にならないこと。"""
    server = PilotWebServer(EventBus(), port=_free_port(), open_browser=False).start()
    server.stop()
    server.stop()


def test_the_event_stream_delivers_what_the_flight_published():
    """A viewer on `/events` receives the flight's events as SSE lines.
    `/events` の閲覧者が、飛行の出来事を SSE の行として受け取ること。"""
    port = _free_port()
    bus = EventBus()
    server = PilotWebServer(bus, port=port, open_browser=False).start()
    received: list = []
    try:
        stream = urllib.request.urlopen(f"http://127.0.0.1:{port}/events", timeout=5)
        # Publish only once a viewer is attached; the bus keeps no history.
        # 閲覧者が付いてから配信する。bus は履歴を持たないためである。
        deadline = time.monotonic() + 3.0
        while bus.subscriber_count == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        bus.publish(EVENT_SAMPLE, {"t": 12.5, "altitude_m": 0.9})

        while time.monotonic() < deadline:
            line = stream.readline()
            if line.startswith(b"data: "):
                received.append(json.loads(line[len(b"data: "):].decode("utf-8")))
                break
        stream.close()
    finally:
        server.stop()

    assert received, "no event reached the viewer"
    assert received[0]["kind"] == EVENT_SAMPLE
    assert received[0]["altitude_m"] == 0.9


def test_an_unknown_path_is_a_404():
    """A path the page does not own is refused, not served.
    ページの担当外のパスは、配信せず拒否すること。"""
    port = _free_port()
    server = PilotWebServer(EventBus(), port=port, open_browser=False).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _get(f"http://127.0.0.1:{port}/nothing-here")
        assert excinfo.value.code == 404
    finally:
        server.stop()


def test_a_traversal_attempt_is_refused_before_a_path_is_built():
    """`..` in an asset path is rejected, never resolved.
    資材のパスに含まれる `..` は、解決せずに拒否すること。"""
    port = _free_port()
    server = PilotWebServer(EventBus(), port=port, open_browser=False).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _get(f"http://127.0.0.1:{port}/vendor/three/../../../etc/passwd.js")
        assert excinfo.value.code in (400, 404)
    finally:
        server.stop()


# =============================================================================
# The running totals / 現在値
# =============================================================================

def test_the_trace_names_the_bundle_so_the_two_records_pair(tmp_path):
    """The trace says where the flight log went, and how to watch it.

    The two share a datetime already, but that pairing relies on a reader
    noticing that one spelling is lower-cased. A line naming the path
    leaves nothing to notice.

    記録が、フライトログの場所とその見方を述べること。

    両者は既に日時を共有しているが、その対応づけは「片方が小文字である」ことに
    読み手が気づくことに頼っている。パスを書いた行があれば、気づく必要が無い。
    """
    from sfpilot.recording import FlightRecording

    recording = FlightRecording("pilot", root=tmp_path, stamp="20260919t120000")
    recording.bundle_path = recording.bundle_dir / recording.bundle_name
    trace = Trace(path=tmp_path / "t.jsonl")

    row = trace.write_recording(recording)
    trace.close()

    assert row["flight_log"] == str(recording.bundle_path)
    assert row["video_command"] == "sf sils video -m pilot/20260919t120000"
    assert row["decisions_csv"].endswith("decisions_20260919t120000.csv")

    written = [json.loads(line) for line
               in (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()]
    assert written[-1]["kind"] == "recording"


def test_the_trace_records_a_flight_that_produced_no_bundle(tmp_path):
    """A flight with no bundle still says so, rather than saying nothing.
    束の無い飛行も、無言ではなくその旨を述べること。"""
    from sfpilot.recording import FlightRecording

    recording = FlightRecording("pilot", root=tmp_path)
    trace = Trace(path=tmp_path / "t.jsonl")

    row = trace.write_recording(recording)
    trace.close()

    assert row["flight_log"] is None
    assert row["decisions_csv"] is None


# =============================================================================
# The route drawn on the top view / 上から見た図に描く経路
# =============================================================================

def test_the_route_is_converted_from_centimetres_to_metres():
    """A leg's amount is centimetres; the top view is metres.

    `square` walks 60cm legs. Drawn without the conversion they would be
    60 METRES and the whole route would sit far outside the 2 m envelope
    the same view draws -- a picture that is wrong without looking broken.

    区間の amount はセンチメートルで、上から見た図はメートルであること。

    `square` は 60cm の区間を辿る。換算せずに描けば 60 **メートル**になり、
    同じ図が描く 2m の包絡のはるか外に経路が出る — 壊れて見えないまま誤った
    絵になる。
    """
    from sfcli.commands.pilot import _leg_points
    from sfpilot.config import DEFAULT_CONFIG
    from sfpilot.mission import builtin_mission_path, load_mission

    mission = load_mission(builtin_mission_path("square"), DEFAULT_CONFIG)

    points = _leg_points(mission)

    # A 60cm leg is 0.6m, and the route closes back on the takeoff point.
    # 60cm の区間は 0.6m であり、経路は離陸点へ戻って閉じる。
    assert [0.6, 0.0] in points
    assert [0.6, 0.6] in points
    assert points[-1] == [0.0, 0.0]
    for north, east in points:
        assert abs(north) <= 2.0 and abs(east) <= 2.0


def test_the_live_feed_counts_the_flight_s_own_decisions():
    """The totals on the page are counted from the Pilot's decision list.

    Counted from the SAME rows the end-of-flight summary prints, so the
    live figures and the printed ones cannot disagree.

    ページの現在値が、Pilot の判断の配列から数えられること。

    飛行終了時の集計が表示するのと**同じ**行から数えるので、ライブの値と
    表示される値が食い違うことはない。
    """
    from sfcli.commands.pilot import _LiveFeed
    from sfpilot.judge import Judgement
    from sfpilot.link import Sample

    bus = EventBus()
    sink = queue.Queue()
    bus.subscribe(sink)
    decisions: list = []
    feed = _LiveFeed(bus)
    feed.watch(decisions)

    decisions.append({"judgement": Judgement(latency_ms=120.0)})
    decisions.append({"judgement": Judgement(latency_ms=700.0,
                                             error="deadline exceeded")})
    # A sample is what carries the totals out, at the sample rate.
    # 現在値を運び出すのは標本であり、その周期で出る。
    feed.sample(Sample(t=1.0, altitude_m=0.8), now=100.0)

    events = []
    while not sink.empty():
        events.append(sink.get_nowait())
    totals = [e for e in events if e["kind"] == "status" and "decisions" in e]
    assert totals, "no running totals were published"
    assert totals[-1]["decisions"] == 2
    assert totals[-1]["overdue"] == 1


def test_the_status_counts_decisions_and_overdue_answers():
    """The figures on the page are the ones the summary prints.
    ページに出る値が、集計が表示するものと同じであること。"""
    bus = EventBus()
    sink = queue.Queue()
    bus.subscribe(sink)
    status = LiveStatus(bus)

    status.note_decision({"latency_ms": 100.0, "answers": {}})
    status.note_decision({"latency_ms": 300.0,
                          "answers": {"error": "deadline exceeded"}})
    status.publish()

    event = sink.get_nowait()
    assert event["decisions"] == 2
    assert event["overdue"] == 1
    assert event["latency_p50_ms"] is not None
