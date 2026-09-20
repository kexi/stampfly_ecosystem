"""
What `sf unity` guarantees / `sf unity` が保証すること

The Unity page does not exist yet, so a stand-in page (a thread that does what
the plan says the page's `.jslib` will do) is what these tests run the server
against: call `/api/hello`, wait on `/api/cmd/next`, answer on
`/api/cmd/result`, post its own log lines to `/api/log`.

Unity のページはまだ無いため、ここでは計画がページの `.jslib` に求める動きを
するスレッド（ページの代役）を相手にサーバを試す: `/api/hello` を呼び、
`/api/cmd/next` で待ち、`/api/cmd/result` に返し、自分のログの行を `/api/log`
へ送る。

Guaranteed here / ここで保証すること:
  (a) `sf unity cmd` completes a round trip through the page
  (b) one cmd_id appears on the cli, server and page lines alike
  (c) `sf unity logs --cmd <id>` recovers one command's flow in time order
  (d) a request from another origin or a non-loopback Host is refused
  (e) a command sent with no page connected fails with a clear reason
  (f) a malformed line, or one carrying another run's run_id, is rejected
  (g) `--level` and `--src` narrow the output as documented
  (h) the Unity CLI command lines are assembled as the README specifies
"""

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

# The sf CLI lives in lib/; tests run against the checkout, not an install.
# sf CLI は lib/ にある。試験は導入物ではなくチェックアウトを対象にする。
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "lib"))

from sfcli.commands import sim, unity  # noqa: E402
from sfcli.utils import jsonl_log  # noqa: E402
from urllib import error as urlerror  # noqa: E402
from urllib import request as urlrequest  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / 前準備
# ---------------------------------------------------------------------------

@pytest.fixture
def logs_root(tmp_path, monkeypatch):
    """Point `logs/unity/` at a temporary directory, so a test run neither
    reads nor writes the checkout's own logs.
    `logs/unity/` を一時ディレクトリに向ける。試験の実行がチェックアウトの
    ログを読むことも書くこともないようにする。"""
    fake_root = tmp_path / "repo"
    (fake_root / "logs" / "unity").mkdir(parents=True)
    monkeypatch.setattr(unity.paths, "root", lambda: fake_root)
    return fake_root


@pytest.fixture
def build_dir(tmp_path):
    """A minimal stand-in for a WebGL build: `serve` refuses to start without
    a directory, and the static path is exercised through `index.html`.
    WebGL のビルドの最小限の代役。ディレクトリが無いと `serve` は起動せず、
    静的配信の経路は `index.html` で確かめる。"""
    directory = tmp_path / "webgl"
    directory.mkdir()
    (directory / "index.html").write_text("<html>stand-in</html>", encoding="utf-8")
    (directory / "Build.wasm.br").write_bytes(b"\x00fake brotli wasm")
    return directory


def _free_port() -> int:
    """An unused port, so parallel or repeated runs do not collide.
    空いているポート。同時・連続の実行がぶつからないようにする。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ServerHandle:
    """A running `sf unity serve` in this process, plus what a test needs to
    talk to it: its port, run id and log path.
    このプロセスの中で動く `sf unity serve` と、試験がそれと話すために要る
    もの: ポート、run_id、ログのパス。"""

    def __init__(self, port: int, run_id: str, log_path: Path, thread: threading.Thread):
        self.port = port
        self.run_id = run_id
        self.log_path = log_path
        self.thread = thread

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def server(logs_root, build_dir):
    """Start `sf unity serve` on a free port in a background thread and stop
    it afterwards. `run_serve` blocks in `serve_forever`, so it is interrupted
    the way Ctrl-C interrupts it.
    空きポートで `sf unity serve` を裏のスレッドで起動し、終了後に止める。
    `run_serve` は `serve_forever` で止まるため、Ctrl-C と同じ形で中断する。"""
    port = _free_port()
    args = argparse.Namespace(
        dir=str(build_dir), port=port, no_browser=True, world=None, coi=False,
    )

    thread = threading.Thread(target=unity.run_serve, args=(args,), daemon=True)
    thread.start()

    # `logs/unity/latest` is the last thing `run_serve` writes before it
    # starts serving, so its appearance is what says the server is ready.
    # `run_serve` が配信を始める前に最後に書くのが `logs/unity/latest` なので、
    # その出現が準備完了の合図になる。
    run_id = None
    deadline = time.monotonic() + 10
    while run_id is None and time.monotonic() < deadline:
        run_id = unity._read_latest_run_id()
        if run_id is None:
            time.sleep(0.02)
    assert run_id is not None, "server did not start"

    # The port is bound before `latest` is written, and `serve_forever` starts
    # just after it, so one real request confirms the server answers.
    # ポートは `latest` より前に確保され、`serve_forever` はその直後に始まる。
    # 実際のリクエストを 1 つ送って応答を確かめる。
    _wait_until_answering(port)
    handle = ServerHandle(port, run_id, unity._run_log_path(run_id), thread)

    yield handle

    # Stop the way Ctrl-C does: `run_serve` catches KeyboardInterrupt around
    # `serve_forever`, so raising it in that thread runs the same shutdown.
    # Ctrl-C と同じ形で止める: `run_serve` は `serve_forever` の周りで
    # KeyboardInterrupt を捕まえるため、そのスレッドで送れば同じ終了処理が動く。
    _interrupt(thread)
    thread.join(timeout=5)


def _wait_until_answering(port: int, timeout: float = 10.0) -> None:
    """Poll `/api/hello` until the server answers. 応答するまで待つ。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _get(f"http://127.0.0.1:{port}/api/hello")
            return
        except Exception:
            time.sleep(0.02)
    raise AssertionError(f"server on port {port} never answered")


def _interrupt(thread: threading.Thread) -> None:
    """Raise KeyboardInterrupt inside `thread`, which is how `serve_forever`
    is meant to end. `thread` の中で KeyboardInterrupt を起こす。
    `serve_forever` が終わる本来の形。"""
    import ctypes
    ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(thread.ident), ctypes.py_object(KeyboardInterrupt)
    )
    # `serve_forever` polls every 0.5s by default, so the exception is only
    # seen at the next poll; a request nudges it awake immediately.
    # `serve_forever` は既定で 0.5 秒ごとに確認するため、例外は次の確認時に
    # しか見られない。リクエストを 1 つ送って直ちに起こす。
    time.sleep(0.6)


# ---------------------------------------------------------------------------
# The stand-in page / ページの代役
# ---------------------------------------------------------------------------

class StandInPage:
    """What the plan requires of the page's `.jslib`, in Python.

    Calls `/api/hello` once, then polls `/api/cmd/next` in a loop; for each
    command it answers on `/api/cmd/result` and posts its own log lines --
    one `src: "fw"` line and one `src: "sim"` line, both carrying the command's
    cmd_id -- to `/api/log`.

    計画がページの `.jslib` に求める動きを Python で書いたもの。

    `/api/hello` を 1 回呼び、以後 `/api/cmd/next` で待ち続ける。命令ごとに
    `/api/cmd/result` へ返し、自分のログの行（`src: "fw"` 1 行と `src: "sim"`
    1 行。どちらも命令の cmd_id を持つ）を `/api/log` へ送る。
    """

    def __init__(self, base_url: str, answer=None):
        self.base_url = base_url
        self.run_id = None
        self.handled = []
        self._answer = answer or (lambda command, args: {"ok": True,
                                                         "data": {"echo": command}})
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    # -- lifecycle --------------------------------------------------------
    def start(self, timeout: float = 5.0) -> "StandInPage":
        self._thread.start()
        assert self._connected.wait(timeout=timeout), "stand-in page did not connect"
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *_exc):
        self.stop()

    # -- the page's own behaviour ----------------------------------------
    def _loop(self) -> None:
        hello = _get(self.base_url + "/api/hello")
        self.run_id = hello["run_id"]
        # The first `/api/cmd/next` is what makes the server consider a page
        # connected, so the test must not send a command before it lands.
        # サーバがページを接続中とみなすのは最初の `/api/cmd/next` なので、
        # 試験はそれが届く前に命令を送ってはならない。
        first = _get(self.base_url + "/api/cmd/next?wait=0.2")
        self._connected.set()
        self._handle(first)

        while not self._stop.is_set():
            try:
                answer = _get(self.base_url + "/api/cmd/next?wait=0.5")
            except Exception:
                return
            self._handle(answer)

    def _handle(self, answer) -> None:
        if not answer or not answer.get("command"):
            return
        cmd_id = answer["cmd_id"]
        command = answer["command"]
        self.handled.append((cmd_id, command, answer.get("args")))

        self._post_log(cmd_id)
        result = dict(self._answer(command, answer.get("args") or {}))
        result["cmd_id"] = cmd_id
        _post(self.base_url + "/api/cmd/result", result)

    def _post_log(self, cmd_id: str) -> None:
        """Post the two lines a page produces while handling a command: one
        the firmware emitted (`src: "fw"`, with a virtual time) and one the
        simulator emitted (`src: "sim"`). Both carry the cmd_id, which is what
        ties them to the CLI's and the server's lines.
        命令を処理する間にページが出す 2 行を送る: ファームウェアが出した行
        （`src: "fw"`、仮想時刻つき）と、シミュレータが出した行
        （`src: "sim"`）。どちらも cmd_id を持ち、これが CLI・サーバの行と
        結び付ける。"""
        lines = [
            {
                "ts": jsonl_log.utc_now(), "level": "info", "src": "fw",
                "event": "fw.log", "run_id": self.run_id, "cmd_id": cmd_id,
                "sim_us": 1_250_000, "tag": "flight_ctrl",
                "msg": "mode changed to ALT_HOLD",
            },
            {
                "ts": jsonl_log.utc_now(), "level": "warn", "src": "sim",
                "event": "sim.step_overrun", "run_id": self.run_id,
                "cmd_id": cmd_id, "sim_us": 1_252_500, "frame": 501,
                "msg": "step took longer than the budget",
                "data": {"budget_us": 2500, "actual_us": 3100},
            },
        ]
        _post(self.base_url + "/api/log", {"lines": lines})


# ---------------------------------------------------------------------------
# HTTP helpers / HTTP の補助
# ---------------------------------------------------------------------------

def _get(url: str, headers=None):
    request = urlrequest.Request(url, headers=headers or {}, method="GET")
    with urlrequest.urlopen(request, timeout=10) as response:
        return json.loads(response.read() or b"{}")


def _post(url: str, payload, headers=None):
    merged = {"Content-Type": "application/json"}
    merged.update(headers or {})
    request = urlrequest.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=merged, method="POST",
    )
    with urlrequest.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read() or b"{}")


def _status_of(call):
    """The HTTP status a call produced, whether it succeeded or raised.
    呼び出しが返した HTTP の状態。成功でも例外でも取り出す。"""
    try:
        status, _ = call()
        return status
    except urlerror.HTTPError as error:
        return error.code


def _read_log(handle: ServerHandle):
    records, broken = jsonl_log.read_records(handle.log_path)
    return records, broken


def _run_cmd(command: str, arg=None, timeout: float = 5.0, json_arg=None):
    """Invoke `sf unity cmd` the way the CLI does, and capture its exit code.
    CLI と同じ形で `sf unity cmd` を呼び、終了コードを受け取る。"""
    args = argparse.Namespace(
        command=command, arg=arg, json=json_arg, timeout=timeout,
    )
    return unity.run_cmd(args)


def _run_logs(capsys, **overrides):
    """Invoke `sf unity logs` and return its printed lines.
    `sf unity logs` を呼び、出力された行を返す。"""
    args = argparse.Namespace(
        run="latest", cmd=None, src=None, level=None, event=None,
        since_sim_us=None, follow=False, json=False,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    # Drain whatever earlier steps printed (the server's startup banner, a
    # `sf unity cmd` result), so what comes back is this call's output alone.
    # 先の段階が出したもの（サーバの起動の表示、`sf unity cmd` の結果）を
    # 捨てる。返すのはこの呼び出しの出力だけにする。
    capsys.readouterr()
    exit_code = unity.run_logs(args)
    printed = capsys.readouterr().out.strip().splitlines()
    return exit_code, printed


# ---------------------------------------------------------------------------
# (a) The round trip / 往復
# ---------------------------------------------------------------------------

def test_cmd_round_trip_succeeds(server, capsys):
    """`sf unity cmd` reaches the page, and the page's result reaches stdout
    as JSON with exit code 0.
    `sf unity cmd` がページに届き、ページの結果が JSON として標準出力に出て、
    終了コードは 0 になる。"""
    with StandInPage(server.base_url) as page:
        exit_code = _run_cmd("sim.pause")
        output = capsys.readouterr().out

    assert exit_code == 0
    printed = json.loads(output[output.index("{"):output.rindex("}") + 1])
    assert printed["ok"] is True
    assert printed["data"] == {"echo": "sim.pause"}
    assert [command for _id, command, _args in page.handled] == ["sim.pause"]


def test_cmd_arguments_reach_the_page(server):
    """`--arg key=value` and `--json` arrive merged, with `--arg` values left
    as strings.
    `--arg key=value` と `--json` がまとまって届き、`--arg` の値は文字列の
    ままである。"""
    with StandInPage(server.base_url) as page:
        exit_code = _run_cmd(
            "world.load", arg=["name=gate_course", "year=2026"],
            json_arg='{"reset": true}',
        )

    assert exit_code == 0
    _cmd_id, _command, args = page.handled[0]
    assert args == {"reset": True, "name": "gate_course", "year": "2026"}


def test_failing_page_result_exits_non_zero(server):
    """A result the page marks as failed makes the CLI exit non-zero.
    ページが失敗と示した結果は、CLI を非 0 で終わらせる。"""
    def refuse(_command, _args):
        return {"ok": False, "error": "no such world"}

    with StandInPage(server.base_url, answer=refuse):
        exit_code = _run_cmd("world.load", arg=["name=nowhere"])

    assert exit_code == 1


# ---------------------------------------------------------------------------
# (b) One cmd_id across all three sides / 3 者に共通の cmd_id
# ---------------------------------------------------------------------------

def test_one_cmd_id_appears_on_cli_server_and_page_lines(server):
    """The cmd_id the CLI issued is on the cli lines, the server's relay lines
    and the lines the page posted -- the whole point of the correlation key.
    CLI が発行した cmd_id が、cli の行・サーバの中継の行・ページが送った行の
    全てに付いている。相関の鍵の目的そのもの。"""
    with StandInPage(server.base_url):
        assert _run_cmd("sim.pause") == 0
        time.sleep(0.4)  # let the page's /api/log land / ページのログの到着を待つ

    records, broken = _read_log(server)
    assert broken == 0

    cmd_ids = {record["cmd_id"] for record in records if "cmd_id" in record}
    assert len(cmd_ids) == 1, f"expected exactly one cmd_id, got {cmd_ids}"
    cmd_id = cmd_ids.pop()
    assert jsonl_log.is_cmd_id(cmd_id)

    by_source = {}
    for record in records:
        if record.get("cmd_id") == cmd_id:
            by_source.setdefault(record["src"], []).append(record["event"])

    assert "cli" in by_source, "the CLI's own lines are missing"
    assert "server" in by_source, "the server's relay lines are missing"
    assert "fw" in by_source and "sim" in by_source, "the page's lines are missing"
    assert "cmd.issued" in by_source["cli"]
    assert "cmd.received" in by_source["server"]
    assert "cmd.forwarded" in by_source["server"]
    assert "cmd.completed" in by_source["server"]


def test_every_line_carries_the_required_keys(server):
    """Every line in the file passes the required-key check, whoever wrote it.
    ファイルの全ての行が、書き手によらず必須の鍵の検査を通る。"""
    with StandInPage(server.base_url):
        assert _run_cmd("sim.pause") == 0
        time.sleep(0.4)

    records, _ = _read_log(server)
    assert records
    for record in records:
        reason = jsonl_log.validate_record(record, expect_run_id=server.run_id)
        assert reason is None, f"{reason}: {record}"


# ---------------------------------------------------------------------------
# (c) One command's flow, in time order / 1 つの命令の流れ
# ---------------------------------------------------------------------------

def test_logs_recovers_one_commands_flow_in_time_order(server, capsys):
    """`sf unity logs --cmd <id>` shows that one command's lines and no
    others, ordered by time.
    `sf unity logs --cmd <id>` が、その命令の行だけを時刻順に出す。"""
    with StandInPage(server.base_url):
        assert _run_cmd("sim.pause") == 0
        time.sleep(0.3)
        assert _run_cmd("sim.resume") == 0
        time.sleep(0.4)

    records, _ = _read_log(server)
    pause_id = next(record["cmd_id"] for record in records
                    if record.get("event") == "cmd.received"
                    and record["data"]["command"] == "sim.pause")

    exit_code, printed = _run_logs(capsys, cmd=pause_id, json=True)
    assert exit_code == 0
    assert printed, "no lines selected"

    selected = [json.loads(line) for line in printed]
    assert all(record["cmd_id"] == pause_id for record in selected)
    assert all("sim.resume" not in json.dumps(record) for record in selected)

    stamps = [record["ts"] for record in selected]
    assert stamps == sorted(stamps), "lines are not in time order"

    events = [record["event"] for record in selected]
    assert events.index("cmd.issued") < events.index("cmd.received")
    assert events.index("cmd.received") < events.index("cmd.forwarded")
    assert events.index("cmd.forwarded") < events.index("cmd.completed")


def test_logs_default_display_is_one_line_per_event(server, capsys):
    """Without `--json` each event is one readable line carrying its cmd_id.
    `--json` 無しでは、1 事象が cmd_id を含む読める 1 行になる。"""
    with StandInPage(server.base_url):
        assert _run_cmd("sim.pause") == 0
        time.sleep(0.4)

    exit_code, printed = _run_logs(capsys, event="cmd")
    assert exit_code == 0
    assert any("cmd.issued" in line and "cmd_id=" in line for line in printed)
    assert all(not line.startswith("{") for line in printed)


# ---------------------------------------------------------------------------
# (d) Origin and Host / Origin と Host の検査
# ---------------------------------------------------------------------------

def test_cross_origin_post_is_refused(server):
    """A POST carrying another site's Origin is refused with 403, so a page
    the user happens to have open cannot drive the simulator.
    他のサイトの Origin を持つ POST は 403 で拒否する。利用者がたまたま開いて
    いるページがシミュレータを操作できてはならない。"""
    status = _status_of(lambda: _post(
        server.base_url + "/api/cmd",
        {"cmd_id": jsonl_log.new_cmd_id(), "command": "sim.pause"},
        headers={"Origin": "http://evil.example"},
    ))
    assert status == 403


def test_matching_origin_is_allowed(server):
    """The page's own same-origin fetch, whose Origin equals the Host, passes.
    ページ自身の同一オリジンの取得（Origin が Host と一致する）は通る。"""
    origin = f"http://127.0.0.1:{server.port}"
    answer = _get(server.base_url + "/api/hello",
                  headers={"Origin": origin, "Host": f"127.0.0.1:{server.port}"})
    assert answer["run_id"] == server.run_id


def test_non_loopback_host_is_refused(server):
    """A Host header naming something other than loopback is refused, which
    closes the DNS name that resolves to 127.0.0.1.
    ループバック以外を指す Host ヘッダは拒否する。127.0.0.1 を指す DNS 名の
    経路を塞ぐ。"""
    status = _status_of(lambda: _post(
        server.base_url + "/api/log", {"lines": []},
        headers={"Host": "stampfly.example"},
    ))
    assert status == 403


def test_a_refusal_is_recorded(server):
    """Every refusal leaves a `server.rejected` line, so a page failing
    silently in the browser can still be explained from the log.
    拒否は必ず `server.rejected` の行を残す。ブラウザ側で静かに失敗した
    ページの原因をログから説明できる。"""
    _status_of(lambda: _post(
        server.base_url + "/api/log", {"lines": []},
        headers={"Origin": "http://evil.example"},
    ))
    time.sleep(0.2)
    records, _ = _read_log(server)
    rejections = [record for record in records if record["event"] == "server.rejected"]
    assert rejections
    assert rejections[-1]["level"] == "warn"
    assert "origin" in rejections[-1]["data"]["reason"]


# ---------------------------------------------------------------------------
# (e) No page connected / ページがいないとき
# ---------------------------------------------------------------------------

def test_cmd_without_a_page_fails_with_a_clear_reason(server, capsys):
    """With no page polling, the command is refused immediately and the reason
    says to open the simulator -- it does not wait out the timeout.
    ページが待ち受けていなければ、命令は直ちに拒否され、理由はシミュレータを
    開くよう伝える。タイムアウトまで待たない。"""
    started = time.monotonic()
    exit_code = _run_cmd("sim.pause", timeout=30.0)
    elapsed = time.monotonic() - started
    output = capsys.readouterr()

    assert exit_code == 1
    assert elapsed < 10, "it waited out the timeout instead of failing fast"
    assert "no page connected" in (output.out + output.err)

    records, _ = _read_log(server)
    assert any(record["event"] == "cmd.no_page" for record in records)


def test_cmd_without_a_server_reports_how_to_start_one(logs_root, capsys):
    """With no `sf unity serve` running at all, the CLI says so and names the
    command that starts one.
    `sf unity serve` が全く動いていなければ、CLI はその旨を伝え、起動する
    コマンドを示す。"""
    exit_code = _run_cmd("sim.pause")
    output = capsys.readouterr()
    assert exit_code == 1
    assert "sf unity serve" in output.out + output.err


def test_cmd_times_out_when_the_page_never_answers(server):
    """A page that takes the command but never answers ends in a timeout, and
    the timeout is recorded.
    命令を受け取ったまま返事をしないページはタイムアウトになり、そのことが
    記録される。"""
    class SilentPage(StandInPage):
        def _handle(self, answer):
            if answer and answer.get("command"):
                self.handled.append((answer["cmd_id"], answer["command"], None))

    with SilentPage(server.base_url):
        exit_code = _run_cmd("sim.pause", timeout=1.0)

    assert exit_code == 1
    records, _ = _read_log(server)
    assert any(record["event"] == "cmd.timeout" for record in records)


# ---------------------------------------------------------------------------
# (f) Rejected log lines / 拒否されるログの行
# ---------------------------------------------------------------------------

def test_a_line_missing_a_required_key_is_rejected(server):
    """A line without every required key is counted, refused and not stored.
    必須の鍵が欠けた行は数えられ、拒否され、保存されない。"""
    status, answer = _post(server.base_url + "/api/log", {"lines": [
        {"ts": jsonl_log.utc_now(), "level": "info", "src": "sim",
         "run_id": server.run_id, "msg": "no event key"},
    ]})
    assert status == 200
    assert answer == {"ok": True, "accepted": 0, "rejected": 1}

    records, _ = _read_log(server)
    assert not any(record.get("msg") == "no event key" for record in records)
    rejections = [record for record in records if record["event"] == "log.rejected"]
    assert "event" in rejections[-1]["data"]["reason"]


def test_a_line_from_another_run_is_rejected(server):
    """A line whose run_id is not this run's is refused, so one run's file
    cannot be polluted by another page.
    この実行の run_id でない行は拒否する。1 回の実行のファイルが別のページに
    汚されないようにする。"""
    _, answer = _post(server.base_url + "/api/log", {"lines": [
        {"ts": jsonl_log.utc_now(), "level": "info", "src": "sim",
         "event": "sim.tick", "run_id": jsonl_log.new_run_id(),
         "msg": "from another run"},
    ]})
    assert answer["rejected"] == 1

    records, _ = _read_log(server)
    rejections = [record for record in records if record["event"] == "log.rejected"]
    assert rejections[-1]["data"]["reason"] == "run_id mismatch"


def test_an_unknown_source_or_level_is_rejected(server):
    """`src` and `level` are fixed vocabularies; anything else is refused
    rather than quietly widening what a filter must know about.
    `src` と `level` は決めた語彙である。それ以外は拒否し、絞り込みが知るべき
    範囲が黙って広がらないようにする。"""
    _, answer = _post(server.base_url + "/api/log", {"lines": [
        {"ts": jsonl_log.utc_now(), "level": "info", "src": "mystery",
         "event": "x.y", "run_id": server.run_id, "msg": "bad src"},
        {"ts": jsonl_log.utc_now(), "level": "critical", "src": "sim",
         "event": "x.y", "run_id": server.run_id, "msg": "bad level"},
    ]})
    assert answer == {"ok": True, "accepted": 0, "rejected": 2}


def test_an_oversize_line_is_rejected(server):
    """One runaway line must not make the file unreadable, so a line over the
    cap is refused whole.
    暴走した 1 行でファイルが読めなくなってはならない。上限を超える行は
    まるごと拒否する。"""
    _, answer = _post(server.base_url + "/api/log", {"lines": [
        {"ts": jsonl_log.utc_now(), "level": "info", "src": "sim",
         "event": "sim.dump", "run_id": server.run_id, "msg": "big",
         "data": {"blob": "x" * (jsonl_log.MAX_LINE_BYTES + 100)}},
    ]})
    assert answer["rejected"] == 1


def test_a_malformed_file_line_is_counted_not_fatal(server, capsys):
    """A line cut short by a process that died mid-write is counted and
    reported, and the rest of the log is still shown.
    書いている途中で終了した処理が切った行は、数えて報告し、残りのログは
    そのまま表示する。"""
    with server.log_path.open("a", encoding="utf-8") as handle:
        handle.write('{"ts": "2026-09-20T00:00:00.000Z", "level": "in\n')

    exit_code, printed = _run_logs(capsys)
    assert exit_code == 0
    assert printed, "the sound lines were dropped along with the broken one"

    records, broken = _read_log(server)
    assert broken == 1
    assert records


# ---------------------------------------------------------------------------
# (g) Filters / 絞り込み
# ---------------------------------------------------------------------------

def test_level_filter_keeps_that_level_and_above(server, capsys):
    """`--level warn` keeps warn and error and drops info and debug.
    `--level warn` は warn と error を残し、info と debug を落とす。"""
    with StandInPage(server.base_url):
        assert _run_cmd("sim.pause") == 0
        time.sleep(0.4)

    exit_code, printed = _run_logs(capsys, level="warn", json=True)
    assert exit_code == 0
    levels = {json.loads(line)["level"] for line in printed}
    assert levels <= {"warn", "error"}
    assert "warn" in levels, "the page's sim.step_overrun line is missing"


def test_src_filter_selects_the_named_sources(server, capsys):
    """`--src fw,sim` keeps only the page's own lines.
    `--src fw,sim` はページ自身の行だけを残す。"""
    with StandInPage(server.base_url):
        assert _run_cmd("sim.pause") == 0
        time.sleep(0.4)

    exit_code, printed = _run_logs(capsys, src="fw,sim", json=True)
    assert exit_code == 0
    sources = {json.loads(line)["src"] for line in printed}
    assert sources == {"fw", "sim"}


def test_event_and_sim_time_filters_narrow_further(server, capsys):
    """`--event` matches the dotted name's prefix and `--since-sim-us` keeps
    only lines at or after a virtual time.
    `--event` は点区切りの名前の先頭に一致し、`--since-sim-us` は指定した
    仮想時刻以降の行だけを残す。"""
    with StandInPage(server.base_url):
        assert _run_cmd("sim.pause") == 0
        time.sleep(0.4)

    _code, by_event = _run_logs(capsys, event="cmd.", json=True)
    assert by_event
    assert all(json.loads(line)["event"].startswith("cmd.") for line in by_event)

    _code, by_time = _run_logs(capsys, since_sim_us=1_251_000, json=True)
    assert [json.loads(line)["event"] for line in by_time] == ["sim.step_overrun"]


def test_logs_without_a_run_reports_it(logs_root, capsys):
    """With no run recorded, `sf unity logs` says so and exits non-zero rather
    than printing nothing and succeeding.
    実行が 1 つも記録されていなければ、`sf unity logs` はその旨を伝えて非 0 で
    終わる。何も出さずに成功してはならない。"""
    exit_code, _printed = _run_logs(capsys)
    assert exit_code == 1


# ---------------------------------------------------------------------------
# The served files / 配信されるファイル
# ---------------------------------------------------------------------------

def test_wasm_and_compression_headers(server):
    """`.wasm` is served as `application/wasm`, and a `.br` copy keeps that
    type while declaring `Content-Encoding: br` so Chrome decompresses it.
    `.wasm` は `application/wasm` で配信し、`.br` の写しは種別を保ったまま
    `Content-Encoding: br` を示して Chrome に展開させる。"""
    with urlrequest.urlopen(server.base_url + "/Build.wasm.br", timeout=10) as response:
        assert response.headers["Content-Type"] == "application/wasm"
        assert response.headers["Content-Encoding"] == "br"


def test_coi_headers_are_off_by_default(server):
    """COOP/COEP are absent unless `--coi` was given: the decided plan needs
    no special headers, and sending them would break other loads.
    `--coi` を付けない限り COOP/COEP は付かない。決定した案は特別なヘッダを
    要さず、付けると他の読み込みが動かなくなる。"""
    with urlrequest.urlopen(server.base_url + "/index.html", timeout=10) as response:
        assert response.headers.get("Cross-Origin-Opener-Policy") is None


def test_a_path_escaping_the_build_directory_is_refused(server):
    """`..` cannot read outside the build directory, loopback-only or not.
    ループバック限定であっても、`..` でビルドのディレクトリの外は読めない。"""
    status = _status_of(lambda: (
        urlrequest.urlopen(server.base_url + "/../../etc/hosts", timeout=10).status, None
    ))
    assert status in (403, 404)


def test_serve_without_a_build_directory_explains_the_fix(logs_root, tmp_path, capsys):
    """Pointed at a directory that does not exist, `serve` names the command
    that produces one.
    存在しないディレクトリを指された `serve` は、それを作るコマンドを示す。"""
    args = argparse.Namespace(
        dir=str(tmp_path / "absent"), port=_free_port(), no_browser=True,
        world=None, coi=False,
    )
    assert unity.run_serve(args) == 1
    output = capsys.readouterr()
    assert "sf unity build" in output.out + output.err


# ---------------------------------------------------------------------------
# (h) The Unity CLI command lines / Unity CLI のコマンド行
# ---------------------------------------------------------------------------

def test_webgl_build_command_carries_the_static_method():
    """WebGL has no built-in command-line build, so `--execute-method` is
    mandatory (`simulator/unity/README.md` section 2: `--target WebGL` alone
    exits 2).
    WebGL には内蔵のコマンドライン用ビルドが無いため `--execute-method` は
    必須（`simulator/unity/README.md` §2。`--target WebGL` だけでは終了
    コード 2）。"""
    command = unity.build_webgl_command(
        "/u/unity", Path("/proj"), Path("/out"), release=False,
    )
    assert command[:3] == ["/u/unity", "build", "/proj"]
    assert command[command.index("--target") + 1] == "WebGL"
    assert command[command.index("--execute-method") + 1] == \
        "StampFly.Editor.Builders.WebGLBuilder.Build"
    assert command[command.index("-o") + 1] == "/out"
    assert "--non-interactive" in command and "--no-tail" in command
    assert command[command.index("--format") + 1] == "ndjson"
    assert unity.BUILD_RELEASE_FLAG not in command


def test_release_build_reaches_the_editors_command_line():
    """`--release` travels inside `--args`, which is how `unity build` puts
    something on the editor's own command line, where `WebGLBuilder` reads it
    (the same place it reads `-buildOutput`). Passing the bare flag instead
    fails: the CLI rejects an unknown option rather than forwarding it.
    `--release` は `--args` の中を通る。`unity build` がエディタ自身のコマンド行
    へ何かを置く方法がそれで、`WebGLBuilder` はそこから読む（`-buildOutput` と
    同じ場所）。素の引数をそのまま渡すと失敗する。CLI は未知の引数を転送せずに
    拒否するためである。"""
    command = unity.build_webgl_command(
        "/u/unity", Path("/proj"), Path("/out"), release=True,
    )
    assert command[-2:] == ["--args", unity.BUILD_RELEASE_FLAG]
    assert unity.BUILD_RELEASE_FLAG.startswith("-")
    assert not unity.BUILD_RELEASE_FLAG.startswith("--")
    assert "--release" not in command


def test_a_development_build_passes_no_release_argument():
    """Without `--release` nothing extra is forwarded, so a development build
    keeps whatever the editor's define symbols already say.
    `--release` が無ければ余分な引数は渡らない。開発用ビルドは、エディタの定義
    記号が既に述べているものをそのまま使う。"""
    command = unity.build_webgl_command(
        "/u/unity", Path("/proj"), Path("/out"), release=False,
    )
    assert "--args" not in command
    assert unity.BUILD_RELEASE_FLAG not in command


def test_test_command_maps_the_mode_to_unitys_spelling():
    """`--mode edit|play` becomes Unity's `EditMode`/`PlayMode`.
    `--mode edit|play` を Unity の `EditMode`／`PlayMode` に対応付ける。"""
    play = unity.build_test_command("/u/unity", Path("/proj"), Path("/r.xml"), "play")
    edit = unity.build_test_command("/u/unity", Path("/proj"), Path("/r.xml"), "edit")
    assert play[play.index("--mode") + 1] == "PlayMode"
    assert edit[edit.index("--mode") + 1] == "EditMode"
    assert play[play.index("--output") + 1] == "/r.xml"
    assert "--non-interactive" in play


def test_test_failure_exit_code_is_eight():
    """`unity test` exits 8 when any test fails, and `sf unity test` passes
    that through unchanged (README section 4).
    `unity test` は 1 件でも不合格なら 8 で終わり、`sf unity test` はそれを
    そのまま返す（README §4）。"""
    assert unity.UNITY_TEST_FAILURE_EXIT == 8


def test_missing_unity_cli_explains_how_to_install(monkeypatch, capsys):
    """With no Unity CLI on the machine, the command says where it is expected
    and which version this project uses.
    Unity CLI が無いときは、想定している置き場とこのプロジェクトが使う版を
    示す。"""
    monkeypatch.setattr(unity, "_unity_executable", lambda: None)
    assert unity._require_unity() is None
    output = capsys.readouterr()
    assert "~/.unity/bin/unity" in output.out + output.err


# ---------------------------------------------------------------------------
# The world subcommand / world のサブコマンド
# ---------------------------------------------------------------------------

def test_world_validate_reports_a_missing_validation_module(logs_root, capsys):
    """With `tools/unity_world/validate.py` absent, the error says the
    validation module is missing rather than failing somewhere obscure.
    `tools/unity_world/validate.py` が無いとき、検査のモジュールが見つから
    ないと分かるエラーになる。"""
    args = argparse.Namespace(files=["room.world.json"])
    assert unity.run_world_validate(args) == 1
    output = capsys.readouterr()
    assert "World validation module not found" in output.out + output.err


def test_world_validate_calls_the_modules_contract(logs_root, capsys):
    """`sf unity world validate` calls `validate_file(path) -> list[str]` and
    exits 1 when any file has a problem.
    `sf unity world validate` は `validate_file(path) -> list[str]` を呼び、
    問題のあるファイルが 1 つでもあれば 1 で終わる。"""
    module_dir = logs_root / "tools" / "unity_world"
    module_dir.mkdir(parents=True)
    (module_dir / "validate.py").write_text(
        "def validate_file(path):\n"
        "    return [] if str(path).endswith('good.world.json') else ['bad shape']\n"
        "def list_worlds(root):\n"
        "    return [{'name': 'empty_room', 'path': 'a/empty_room.world.json'}]\n",
        encoding="utf-8",
    )

    assert unity.run_world_validate(argparse.Namespace(files=["good.world.json"])) == 0
    assert unity.run_world_validate(argparse.Namespace(files=["bad.world.json"])) == 1
    assert "bad shape" in capsys.readouterr().out

    assert unity.run_world_list(argparse.Namespace()) == 0
    assert "empty_room" in capsys.readouterr().out


@pytest.mark.skipif(
    not (REPO_ROOT / "tools" / "unity_world" / "validate.py").is_file(),
    reason="the validation module is written by another task",
)
def test_world_commands_work_against_the_real_module(capsys):
    """Against the checkout's own `tools/unity_world/validate.py`, the bundled
    world files pass and a missing file is reported as a problem.
    チェックアウトの `tools/unity_world/validate.py` を相手に、同梱の空間
    ファイルが合格し、存在しないファイルが問題として報告される。"""
    bundled = sorted(
        (REPO_ROOT / "simulator" / "unity" / "Assets" / "StampFly" / "Worlds")
        .glob("*.world.json")
    )
    if not bundled:
        pytest.skip("no bundled world files yet")

    assert unity.run_world_validate(
        argparse.Namespace(files=[str(path) for path in bundled])
    ) == 0

    assert unity.run_world_validate(
        argparse.Namespace(files=[str(REPO_ROOT / "absent.world.json")])
    ) == 1

    capsys.readouterr()
    assert unity.run_world_list(argparse.Namespace()) == 0
    listed = capsys.readouterr().out
    # `list_worlds` returns a per-language description object, which must be
    # reduced to one line rather than printed as a dict.
    # `list_worlds` は言語ごとの説明のオブジェクトを返す。dict のまま出さず
    # 1 行にまとめる必要がある。
    assert "{'ja'" not in listed and '{"ja"' not in listed


# ---------------------------------------------------------------------------
# Registration and `sf sim` / 登録と `sf sim`
# ---------------------------------------------------------------------------

def test_register_builds_every_subcommand():
    """`sf unity --help` lists the subcommands, and each one has a runner.
    `sf unity --help` にサブコマンドが並び、それぞれに実行の関数がある。"""
    parser = argparse.ArgumentParser()
    unity.register(parser.add_subparsers())
    for line in ("serve", "cmd", "logs", "build", "test", "open", "setup", "world"):
        parsed = parser.parse_args(["unity", line] if line != "cmd"
                                   else ["unity", "cmd", "sim.pause"])
        assert callable(parsed.func)


def test_sim_list_includes_the_unity_backend():
    """`sf sim list` knows the Unity backend, and its kind marks it as the one
    that runs in Chrome.
    `sf sim list` が Unity のバックエンドを知っており、その `kind` が Chrome
    で動くものだと示す。"""
    assert "unity" in sim.BACKENDS
    assert sim.BACKENDS["unity"]["kind"] == "unity"
    assert sim._backend_kind(sim.BACKENDS["vpython"]) == "python"
    assert sim._backend_kind({}) == "python", "an absent kind must mean python"


def test_sim_run_unity_delegates_to_serve(monkeypatch):
    """`sf sim run unity` hands over to `sf unity serve` instead of running a
    Python script.
    `sf sim run unity` は Python スクリプトを実行せず `sf unity serve` へ
    委譲する。"""
    captured = {}

    def fake_serve(args):
        captured["args"] = args
        return 0

    monkeypatch.setattr(unity, "run_serve", fake_serve)
    exit_code = sim.run_sim(argparse.Namespace(
        backend="unity", world="voxel", seed=None, mode="rate", no_joystick=False,
    ))
    assert exit_code == 0
    assert captured["args"].port == unity.DEFAULT_PORT
    assert captured["args"].world is None, "the Python backends' default world leaked"


def test_sim_run_python_backends_are_unchanged(monkeypatch):
    """The two original backends still go through the Python launcher path.
    元からの 2 つのバックエンドは、これまでどおり Python の起動経路を通る。"""
    calls = []
    monkeypatch.setattr(sim, "_get_python_cmd", lambda backend: None)
    monkeypatch.setattr(unity, "run_serve", lambda args: calls.append(args) or 0)

    # `_get_python_cmd` returning None is the "dependency missing" path, which
    # returns 1 -- reaching it proves the Unity delegation was not taken.
    # `_get_python_cmd` が None を返すのは依存が無いときの経路で 1 を返す。
    # そこへ至ること自体が、Unity への委譲が起きていない証拠になる。
    exit_code = sim.run_sim(argparse.Namespace(
        backend="vpython", world="voxel", seed=None, mode="rate", no_joystick=True,
    ))
    assert exit_code == 1
    assert not calls


# ---------------------------------------------------------------------------
# The identifiers / 識別子
# ---------------------------------------------------------------------------

def test_a_run_id_sorts_by_time():
    """A run id starts with its UTC stamp, so sorting by name sorts by time.
    run_id は先頭が UTC の日時なので、名前順が時刻順になる。"""
    first = jsonl_log.new_run_id()
    time.sleep(1.05)
    second = jsonl_log.new_run_id()
    assert first < second
    assert jsonl_log.is_run_id(first) and jsonl_log.is_run_id(second)


def test_run_and_command_ids_are_distinguishable():
    """A cmd_id is never mistaken for a run_id and the other way round.
    cmd_id を run_id と取り違えることがなく、その逆もない。"""
    run_id = jsonl_log.new_run_id()
    cmd_id = jsonl_log.new_cmd_id()
    assert not jsonl_log.is_cmd_id(run_id)
    assert not jsonl_log.is_run_id(cmd_id)


def test_two_run_ids_in_the_same_second_differ():
    """The random tail keeps two runs started in the same second apart.
    後ろの乱数が、同じ秒に始まった 2 つの実行を分ける。"""
    ids = {jsonl_log.new_run_id() for _ in range(50)}
    assert len(ids) == 50


def test_an_unknown_correlation_key_is_a_mistake(tmp_path):
    """A typo in a correlation key raises instead of silently producing a key
    no filter looks at.
    相関の鍵の打ち間違いは例外になる。どの絞り込みも見ない鍵が黙って増える
    ことを防ぐ。"""
    with jsonl_log.JsonlLogger(tmp_path / "a.jsonl", jsonl_log.new_run_id()) as logger:
        with pytest.raises(TypeError):
            logger.log("x.y", "typo", cmdid="c1")


def test_concurrent_writes_produce_whole_lines(tmp_path):
    """Several threads appending at once leave complete, parseable lines.
    複数のスレッドが同時に追記しても、行は完全で読める形で残る。"""
    path = tmp_path / "concurrent.jsonl"
    run_id = jsonl_log.new_run_id()
    with jsonl_log.JsonlLogger(path, run_id, src="sim") as logger:
        def write_many(index):
            for step in range(50):
                logger.log("sim.tick", f"thread {index} step {step}", tick=step)

        threads = [threading.Thread(target=write_many, args=(index,))
                   for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    records, broken = jsonl_log.read_records(path)
    assert broken == 0
    assert len(records) == 8 * 50
