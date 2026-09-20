"""
sf unity - Unity WebGL simulator (Chrome only)

The PC-side entry point for the Unity simulator described in
`docs/plans/unity-simulator.md`. Everything the user needs is an `sf`
subcommand; the Unity CLI (`~/.unity/bin/unity`) and the validation module
under `tools/unity_world/` are backends, never run directly.

`docs/plans/unity-simulator.md` の Unity 版シミュレータの PC 側の入口。
利用者に必要なものは全て `sf` のサブコマンドとして公開し、Unity CLI
（`~/.unity/bin/unity`）や `tools/unity_world/` の検査モジュールは
バックエンドとして扱い、直接実行させない。

Subcommands / サブコマンド:
    serve           Serve a WebGL build on 127.0.0.1 and relay commands
    cmd             Send one command to the page the server is serving
    logs            Filter and show a run's JSON Lines log
    build / test    Call the Unity CLI (WebGL build, EditMode/PlayMode tests)
    open / setup    Open the project in the editor, install com.unity.pipeline
    world           Validate and list `*.world.json` space files

Three paths reach the running page (plan section 4): the page's own
JavaScript API, URL arguments, and this local server. The server holds a
long-poll: the page waits on `/api/cmd/next`, `sf unity cmd` posts to
`/api/cmd`, the page answers on `/api/cmd/result`.

動いているページへ届く経路は 3 つある（計画 §4）: ページ自身の JavaScript
API、URL の引数、そしてこのローカルサーバ。サーバは待ち受けを保持する:
ページが `/api/cmd/next` で待ち、`sf unity cmd` が `/api/cmd` に入れ、
ページが `/api/cmd/result` に返す。

stdlib only (no new dependency), like `sf blocks` and the SILS GUI: the
server binds 127.0.0.1 only and checks Origin and Host, because it is a
control channel for a running simulator, not a LAN service.
標準ライブラリだけで書く（依存を足さない）。`sf blocks`・SILS GUI と同じ
方針で、サーバは 127.0.0.1 だけに bind し Origin と Host を検査する。これは
動いているシミュレータの制御チャネルであり、LAN のサービスではない。
"""

import argparse
import json
import mimetypes
import os
import queue
import shutil
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit
from urllib import error as urlerror
from urllib import request as urlrequest

from .. import __version__ as _sfcli_version
from ..utils import console, paths
from ..utils import jsonl_log

COMMAND_NAME = "unity"
COMMAND_HELP = "Unity WebGL simulator: serve, command, logs, build, test"

# ---------------------------------------------------------------------------
# Constants / 定数
# ---------------------------------------------------------------------------

DEFAULT_PORT = 8770

# The Unity project and its WebGL output. `simulator/unity/README.md` section 2
# builds with `-o <path>`; `Build/` under the project is the default place and
# is excluded by the repository-root .gitignore.
# Unity プロジェクトと WebGL の出力先。`simulator/unity/README.md` §2 は
# `-o <path>` で出力先を渡す。既定はプロジェクト直下の `Build/` で、
# リポジトリ直下の .gitignore で除外されている。
UNITY_PROJECT_SUBPATH = "simulator/unity"
WEBGL_BUILD_SUBPATH = "Build/WebGL"

# The static method `unity build` must be told to call: WebGL has no built-in
# command-line build (README section 2, exit code 2 without it).
# `unity build` に呼ばせる static メソッド。WebGL には内蔵のコマンドライン用
# ビルドが無い（README §2。付けないと終了コード 2）。
WEBGL_BUILD_METHOD = "StampFly.Editor.Builders.WebGLBuilder.Build"

# What `sf unity build --release` puts on the editor's command line for the
# build method to read, the way `WebGLBuilder` already reads `-buildOutput`.
# A distributed build must not carry the command relay endpoint (plan
# section 4); the CLI only states the intent and the build method decides the
# define. Rename it here and in `WebGLBuilder` together.
# `sf unity build --release` がエディタのコマンド行へ置く引数。`WebGLBuilder`
# が `-buildOutput` を読むのと同じ仕組みでビルドの入口が読む。配布用ビルドには
# 命令の中継の受け口を入れない（計画 §4）。CLI は意図を伝えるだけで、定義の
# 判断はビルドの入口が行う。名前を変えるときは `WebGLBuilder` と揃える。
BUILD_RELEASE_FLAG = "-stampflyRelease"

# `unity test` exits 8 when any test fails (README section 4). Passed through
# unchanged so a caller's `&&` and CI see the real verdict.
# `unity test` は 1 件でも不合格なら 8 で終わる（README §4）。呼び出し側の
# `&&` や CI が本当の合否を見られるよう、そのまま返す。
UNITY_TEST_FAILURE_EXIT = 8

# How long `/api/cmd/next` holds a request open when nothing is queued. The
# page re-polls after this; a bounded wait keeps a stopped page from pinning a
# server thread forever.
# 命令が無いとき `/api/cmd/next` が待つ長さ。ページはこの後に待ち直す。
# 区切っておくことで、止まったページがサーバのスレッドを占め続けない。
CMD_POLL_DEFAULT_WAIT_S = 25.0
CMD_POLL_MAX_WAIT_S = 60.0

# A page counts as connected while its last `/api/cmd/next` is within this
# window. Longer than one poll, so the gap between two polls does not read as
# a disconnection.
# 直近の `/api/cmd/next` がこの時間内ならページは接続中とみなす。1 回の
# 待ち受けより長くして、待ち直しの合間を切断と読み違えないようにする。
PAGE_ALIVE_S = CMD_POLL_MAX_WAIT_S + 10.0

# The largest per-tick trace `POST /api/trace` will store. The ring holds 8192
# ticks at roughly 300 bytes each, so a full dump is about 2.5 MB; the cap is
# well above that and well below anything that would exhaust the server.
# `POST /api/trace` が保存する刻みごとのトレースの上限。輪は 8192 刻みを持ち
# 1 刻みおよそ 300 バイトなので、満杯の取り出しは約 2.5 MB である。上限は
# それより十分大きく、サーバを枯らす大きさより十分小さくしてある。
MAX_TRACE_BYTES = 64 * 1024 * 1024

# Compressed WebGL files. Unity's default is Brotli; the fallback loader also
# produces gzip. The server sets Content-Encoding so Chrome decompresses them.
# 圧縮された WebGL のファイル。Unity の既定は Brotli で、フォールバックの
# ローダは gzip も作る。Chrome が展開できるよう Content-Encoding を付ける。
CONTENT_ENCODINGS = {".br": "br", ".gz": "gzip"}

# Media types Unity's WebGL output needs that `mimetypes` does not know.
# `mimetypes` が知らない、Unity の WebGL 出力に必要な種別。
EXTRA_MEDIA_TYPES = {
    ".wasm": "application/wasm",
    ".data": "application/octet-stream",
    ".symbols.json": "application/json",
    ".unityweb": "application/octet-stream",
}

# `logs/unity/<run_id>.server.json` records the port and pid so that
# `sf unity cmd`, started later as its own process, can find the server.
# `logs/unity/<run_id>.server.json` にポートと pid を記録し、後から別の
# プロセスとして起動する `sf unity cmd` がサーバを見つけられるようにする。
LATEST_FILE = "latest"


# ---------------------------------------------------------------------------
# Event names / 事象の名前
# ---------------------------------------------------------------------------
# The vocabulary `sf unity logs --event <prefix>` filters on. Documented in
# `docs/commands/sf-unity.md` section 7; keep the two in step.
# `sf unity logs --event <接頭辞>` が絞り込む語彙。`docs/commands/sf-unity.md`
# §7 に同じ一覧があり、両方を揃えて保つ。
EVENTS: Dict[str, str] = {
    "server.start": "the server bound its port and opened the log",
    "server.stop": "the server stopped",
    "server.page_connected": "a page polled /api/cmd/next for the first time",
    "server.page_disconnected": "a page stopped polling for longer than the window",
    "server.hello": "a page called /api/hello and received the run_id",
    "server.rejected": "a request was refused (Origin, Host, path, method)",
    "cmd.issued": "sf unity cmd issued a cmd_id and posted the command",
    "cmd.received": "the server accepted a command from the CLI",
    "cmd.forwarded": "the server handed the command to the waiting page",
    "cmd.completed": "the page returned a result",
    "cmd.timeout": "no result arrived before the timeout",
    "cmd.no_page": "no page was connected, so the command was refused",
    "cmd.result": "sf unity cmd printed the result it received",
    "log.rejected": "a line the page posted failed validation",
    "log.accepted": "a batch of page lines was appended",
    "trace.stored": "a per-tick trace was written to <run_id>.trace.jsonl",
    "build.start": "a Unity CLI build was launched",
    "build.finished": "a Unity CLI build ended (exit code in data)",
    "test.start": "a Unity CLI test run was launched",
    "test.finished": "a Unity CLI test run ended (exit code in data)",
}


# ---------------------------------------------------------------------------
# Paths / 置き場
# ---------------------------------------------------------------------------

def _unity_logs_dir() -> Path:
    """`logs/unity/`, created on demand. This is outside git (the
    repository-root .gitignore ignores `logs/*`).
    `logs/unity/`。必要になった時点で作る。git 管理外（リポジトリ直下の
    .gitignore が `logs/*` を無視する）。"""
    directory = paths.root() / "logs" / "unity"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _unity_project_dir() -> Path:
    """`simulator/unity/`, the Unity project the CLI acts on.
    Unity CLI が対象にする Unity プロジェクト `simulator/unity/`。"""
    return paths.root() / UNITY_PROJECT_SUBPATH


def _default_build_dir() -> Path:
    """The WebGL output the README's `unity build ... -o` writes to.
    README の `unity build ... -o` が書く WebGL の出力先。"""
    return _unity_project_dir() / WEBGL_BUILD_SUBPATH


def _run_log_path(run_id: str) -> Path:
    """`logs/unity/<run_id>.jsonl` -- one run's whole log: the server's own
    events, the CLI's lines and everything the page posted.
    `logs/unity/<run_id>.jsonl` -- 実行 1 回の全てのログ。サーバ自身の事象、
    CLI の行、ページが送った行が同じファイルに入る。"""
    return _unity_logs_dir() / f"{run_id}.jsonl"


def _run_trace_path(run_id: str) -> Path:
    """`logs/unity/<run_id>.trace.jsonl` -- one run's per-tick diagnostic dump.

    Beside the run's log rather than inside it, because a trace is thousands
    of per-tick records and the log is defined not to carry those.
    `logs/unity/<run_id>.trace.jsonl` -- 実行 1 回の刻みごとの診断の取り出し。

    実行のログの中ではなく隣に置く。トレースは刻みごとの記録が数千件であり、
    ログはそれを運ばないと定めているためである。"""
    return _unity_logs_dir() / f"{run_id}.trace.jsonl"


def _server_info_path(run_id: str) -> Path:
    """`logs/unity/<run_id>.server.json` -- port and pid, written at start and
    removed at stop. How `sf unity cmd` reaches a server it did not start.
    `logs/unity/<run_id>.server.json` -- ポートと pid。起動時に書き、終了時に
    消す。`sf unity cmd` が自分で起動していないサーバへ届く手掛かり。"""
    return _unity_logs_dir() / f"{run_id}.server.json"


def _latest_path() -> Path:
    """`logs/unity/latest` -- holds the most recent run_id, one line.
    `logs/unity/latest` -- 直近の run_id を 1 行で持つ。"""
    return _unity_logs_dir() / LATEST_FILE


def _read_latest_run_id() -> Optional[str]:
    """The run_id in `logs/unity/latest`, or None when the file is absent or
    holds something that is not a run id.
    `logs/unity/latest` の run_id。ファイルが無い、または run_id の形でない
    ものが入っていれば None。"""
    try:
        value = _latest_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if jsonl_log.is_run_id(value) else None


def _resolve_run_id(requested: Optional[str]) -> Optional[str]:
    """Turn `--run latest|RUN_ID` into a run id. `latest` and no value both
    read `logs/unity/latest`.
    `--run latest|RUN_ID` を run_id にする。`latest` と未指定はどちらも
    `logs/unity/latest` を読む。"""
    if requested in (None, "latest"):
        return _read_latest_run_id()
    return requested


# ---------------------------------------------------------------------------
# Server state / サーバの状態
# ---------------------------------------------------------------------------

class _PendingCommand:
    """One command in flight: the CLI's request, the page's answer, and the
    event that releases the waiting CLI thread.
    往復の途中にある 1 つの命令。CLI の要求、ページの返事、待っている CLI の
    スレッドを解放する合図を持つ。"""

    def __init__(self, cmd_id: str, command: str, args: Dict[str, Any]) -> None:
        self.cmd_id = cmd_id
        self.command = command
        self.args = args
        self.done = threading.Event()
        self.result: Optional[Dict[str, Any]] = None


class _Relay:
    """The server's shared state: the queue of commands waiting for a page,
    the commands in flight, and when a page was last seen.

    Every method is safe to call from several request threads
    (ThreadingHTTPServer serves each request on its own thread).

    サーバが共有する状態。ページを待っている命令の列、往復の途中にある命令、
    ページを最後に見た時刻を持つ。どのメソッドも複数のリクエストスレッドから
    呼んで安全（ThreadingHTTPServer は 1 リクエスト 1 スレッド）。
    """

    def __init__(self, logger: jsonl_log.JsonlLogger, run_id: str) -> None:
        self.logger = logger
        self.run_id = run_id
        self._queue: "queue.Queue[_PendingCommand]" = queue.Queue()
        self._pending: Dict[str, _PendingCommand] = {}
        self._lock = threading.Lock()
        self._last_page_seen: Optional[float] = None
        self._page_connected = False

    # -- page presence ----------------------------------------------------
    def note_page_poll(self) -> None:
        """Record that a page polled. The first poll, and the first poll after
        a gap longer than the window, is logged as a connection.
        ページが待ち受けに来たことを記録する。最初の 1 回、および窓より長い
        間隔の後の最初の 1 回は接続として記録する。"""
        now = time.monotonic()
        with self._lock:
            was_connected = self._page_connected and self._within_window(now)
            self._last_page_seen = now
            self._page_connected = True
        if not was_connected:
            self.logger.log(
                "server.page_connected",
                "page started polling for commands",
                src="server",
            )

    def _within_window(self, now: float) -> bool:
        """True when the last poll is recent enough to count as connected.
        直近の待ち受けが接続とみなせる新しさか。"""
        if self._last_page_seen is None:
            return False
        return (now - self._last_page_seen) <= PAGE_ALIVE_S

    def page_connected(self) -> bool:
        """Whether a page is currently connected. Logs the transition once
        when a previously connected page has gone quiet.
        ページが今つながっているか。つながっていたページの応答が途絶えた
        ときは、その変化を 1 回だけ記録する。"""
        now = time.monotonic()
        with self._lock:
            connected = self._within_window(now)
            went_quiet = self._page_connected and not connected
            if went_quiet:
                self._page_connected = False
        if went_quiet:
            self.logger.log(
                "server.page_disconnected",
                "page stopped polling",
                level="warn",
                src="server",
            )
        return connected

    # -- command flow -----------------------------------------------------
    def submit(self, cmd_id: str, command: str, args: Dict[str, Any]) -> _PendingCommand:
        """Queue a command for the page and return the handle to wait on.
        命令をページ向けの列に入れ、待つための取っ手を返す。"""
        pending = _PendingCommand(cmd_id, command, args)
        with self._lock:
            self._pending[cmd_id] = pending
        self._queue.put(pending)
        return pending

    def take_next(self, wait_s: float) -> Optional[_PendingCommand]:
        """Block up to `wait_s` for the next queued command (the page's
        long-poll). None means nothing arrived and the page should poll again.
        次の命令を最大 `wait_s` 待つ（ページの待ち受け）。None は何も来な
        かったことを表し、ページは待ち直す。"""
        try:
            return self._queue.get(timeout=wait_s)
        except queue.Empty:
            return None

    def complete(self, cmd_id: str, result: Dict[str, Any]) -> bool:
        """Hand the page's answer to the waiting CLI thread. False when the
        cmd_id is unknown -- a late answer to a command that already timed out,
        or a made-up id.
        ページの返事を待っている CLI のスレッドへ渡す。cmd_id を知らない場合
        は False（既にタイムアウトした命令への遅れた返事、または偽の識別子）。"""
        with self._lock:
            pending = self._pending.pop(cmd_id, None)
        if pending is None:
            return False
        pending.result = result
        pending.done.set()
        return True

    def drop(self, cmd_id: str) -> None:
        """Forget a command whose CLI thread gave up waiting, so a later
        answer is refused rather than matched to nothing.
        CLI 側が待つのをやめた命令を忘れる。後から来た返事が宛先を失ったまま
        残らないようにする。"""
        with self._lock:
            self._pending.pop(cmd_id, None)


# ---------------------------------------------------------------------------
# HTTP handler / HTTP の処理
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    """Serves the WebGL build and the command/log API on 127.0.0.1.

    `relay`, `logger`, `build_dir` and `coi` are set on the subclass built in
    `_make_handler` -- BaseHTTPRequestHandler is instantiated per request, so
    there is nowhere else to put per-server state.

    WebGL のビルドと、命令・ログの API を 127.0.0.1 で配信する。`relay`・
    `logger`・`build_dir`・`coi` は `_make_handler` が作る派生クラスに置く。
    BaseHTTPRequestHandler はリクエストごとに作られるため、サーバ単位の状態を
    置ける場所が他に無い。
    """

    relay: _Relay
    logger: jsonl_log.JsonlLogger
    build_dir: Path
    coi: bool
    server_version_string: str

    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, *args: Any) -> None:
        """Silence the per-request stderr line. Requests worth recording go to
        the JSON Lines log instead, so the two do not disagree.
        リクエストごとの標準エラーへの 1 行を止める。記録すべきものは JSON
        Lines のログへ書き、2 つの記録が食い違わないようにする。"""
        return

    def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
        """Answer with a JSON object. ensure_ascii=False keeps Japanese
        messages readable in the browser's network view.
        JSON のオブジェクトで応答する。ensure_ascii=False で日本語の本文が
        ブラウザの通信表示でも読める形になる。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_isolation_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_isolation_headers(self) -> None:
        """COOP/COEP, only under `--coi`. They are needed only by the retired
        plan 1 (`-pthread`, SharedArrayBuffer); plan 2 -- the decided one --
        runs without them, and sending them by default would break any page
        that loads a resource from elsewhere.
        COOP/COEP を `--coi` のときだけ付ける。これが要るのは退避先の案 1
        （`-pthread`・SharedArrayBuffer）だけで、決定した案 2 は無しで動く。
        既定で付けると、外部の資源を読み込むページが動かなくなる。"""
        if self.coi:
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")

    def _body_json(self) -> Optional[Any]:
        """Decode the request body as JSON, or None when it is not JSON.
        リクエストの本文を JSON として読む。JSON でなければ None。"""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0:
            return None
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return None

    # -- access control ---------------------------------------------------
    def _access_denied_reason(self) -> Optional[str]:
        """Why this request must be refused, or None when it may proceed.

        Two checks, both from `sf blocks` (`_origin_allowed`, blocks.py):

        Origin -- a cross-origin "simple request" is not preflighted, so any
        page the user has open could post to this control channel. A non-
        browser tool sends no Origin and is allowed; our own page sends an
        Origin equal to the request's Host.

        Host -- the server binds 127.0.0.1, but a DNS name that resolves to
        127.0.0.1 would still reach it and would carry that name as the
        Origin, which the first check would then accept. Requiring the Host to
        be a loopback literal closes that.

        このリクエストを拒否する理由。進めてよければ None。

        検査は 2 つで、どちらも `sf blocks`（blocks.py の `_origin_allowed`）
        と同じ考え方である。

        Origin -- クロスオリジンの "simple request" は preflight の対象外
        なので、利用者が開いている無関係なページがこの制御チャネルへ POST
        できてしまう。非ブラウザの道具は Origin を送らないので許可し、自分の
        ページはリクエストの Host と一致する Origin を送るので許可する。

        Host -- サーバは 127.0.0.1 に bind するが、127.0.0.1 を指す DNS 名は
        それでも届き、その名前を Origin として持つため、1 つ目の検査を通って
        しまう。Host をループバックの表記に限ることでこれを塞ぐ。
        """
        host = self.headers.get("Host", "")
        hostname = host.rsplit(":", 1)[0].strip("[]") if host else ""
        if hostname not in ("127.0.0.1", "localhost", "::1"):
            return f"host not loopback: {host!r}"

        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{host}":
            return f"origin mismatch: {origin!r}"
        return None

    def _reject(self, code: int, reason: str) -> None:
        """Refuse a request and record it, so a page failing silently in the
        browser can still be explained from the log.
        リクエストを拒否し、その事実を記録する。ブラウザ側で静かに失敗した
        ページの原因を、ログから説明できるようにする。"""
        self.logger.log(
            "server.rejected",
            f"refused {self.command} {self.path}: {reason}",
            level="warn",
            src="server",
            data={"reason": reason, "path": self.path, "method": self.command},
        )
        self._send_json(code, {"ok": False, "error": reason})

    # -- GET --------------------------------------------------------------
    def do_GET(self) -> None:
        denial = self._access_denied_reason()
        if denial is not None:
            self._reject(403, denial)
            return

        split = urlsplit(self.path)
        path = split.path

        if path == "/api/hello":
            self._handle_hello()
            return
        if path == "/api/cmd/next":
            self._handle_cmd_next(parse_qs(split.query))
            return
        if path.startswith("/api/"):
            self._reject(404, f"unknown api path: {path}")
            return
        self._serve_static(path)

    def _handle_hello(self) -> None:
        """`GET /api/hello` -- how the page learns it is served locally and
        which run_id to tag its lines with. A page opened from GitHub Pages
        gets no answer here and falls back to its URL-argument path.
        `GET /api/hello` -- ページがローカル配信かを知り、自分の行に付ける
        run_id を受け取る経路。GitHub Pages から開いたページはここで応答を
        得られず、URL の引数の経路に落ちる。"""
        self.logger.log(
            "server.hello",
            "page asked for the run id",
            src="server",
            data={"user_agent": self.headers.get("User-Agent", "")},
        )
        self._send_json(200, {
            "ok": True,
            "run_id": self.relay.run_id,
            "server_version": self.server_version_string,
        })

    def _handle_cmd_next(self, query: Dict[str, List[str]]) -> None:
        """`GET /api/cmd/next?wait=25` -- the page's long-poll. Answers with
        one command, or with `{"ok": true, "command": null}` when the wait
        elapsed and the page should poll again.
        `GET /api/cmd/next?wait=25` -- ページの待ち受け。命令 1 つを返すか、
        待ち時間が尽きたときは `{"ok": true, "command": null}` を返し、ページ
        は待ち直す。"""
        self.relay.note_page_poll()
        wait_s = _clamp_wait(query.get("wait", [None])[0])

        pending = self.relay.take_next(wait_s)
        if pending is None:
            self._send_json(200, {"ok": True, "command": None})
            return

        self.logger.log(
            "cmd.forwarded",
            f"handed {pending.command} to the page",
            src="server",
            cmd_id=pending.cmd_id,
            data={"command": pending.command, "args": pending.args},
        )
        self._send_json(200, {
            "ok": True,
            "cmd_id": pending.cmd_id,
            "command": pending.command,
            "args": pending.args,
        })

    def _serve_static(self, path: str) -> None:
        """Serve one file from the WebGL build directory.

        The requested path is resolved and required to stay inside the build
        directory, so `..` cannot read the rest of the disk even though the
        server is loopback-only.

        WebGL のビルドのディレクトリから 1 つのファイルを配信する。

        要求されたパスを解決し、ビルドのディレクトリの中に留まることを
        必須にする。ループバック限定であっても、`..` でディスクの他の場所を
        読めてはならない。
        """
        relative = path.lstrip("/") or "index.html"
        try:
            target = (self.build_dir / relative).resolve()
            target.relative_to(self.build_dir.resolve())
        except (ValueError, OSError):
            self._reject(403, f"path escapes the build directory: {path}")
            return

        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self.send_error(404, "not found")
            return

        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _media_type(target))
        encoding = CONTENT_ENCODINGS.get(target.suffix)
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(len(body)))
        self._send_isolation_headers()
        self.end_headers()
        self.wfile.write(body)

    # -- POST -------------------------------------------------------------
    def do_POST(self) -> None:
        denial = self._access_denied_reason()
        if denial is not None:
            self._reject(403, denial)
            return

        path = urlsplit(self.path).path

        # The trace is JSON Lines, not a JSON object, and it is large: it is
        # read as raw text before the JSON body parse below would reject it.
        # トレースは JSON の物体ではなく JSON Lines で、しかも大きい。下の JSON
        # の解析が拒む前に、生のテキストとして読む。
        if path == "/api/trace":
            self._handle_trace()
            return

        body = self._body_json()
        if not isinstance(body, dict):
            self._reject(400, "body is not a JSON object")
            return

        if path == "/api/cmd":
            self._handle_cmd(body)
        elif path == "/api/cmd/result":
            self._handle_cmd_result(body)
        elif path == "/api/log":
            self._handle_log(body)
        else:
            self._reject(404, f"unknown api path: {path}")

    def _handle_cmd(self, body: Dict[str, Any]) -> None:
        """`POST /api/cmd` -- `sf unity cmd` puts a command in and waits here
        for the page's answer. The cmd_id is issued by the CLI, so the CLI's
        own lines already carry it before the server sees the command.
        `POST /api/cmd` -- `sf unity cmd` が命令を入れ、ページの返事をここで
        待つ。cmd_id は CLI が発行するため、サーバが命令を見る前から CLI 側の
        行に同じ値が付いている。"""
        cmd_id = body.get("cmd_id")
        command = body.get("command")
        args = body.get("args") or {}
        timeout_s = float(body.get("timeout", 10.0))

        if not jsonl_log.is_cmd_id(cmd_id) or not isinstance(command, str) or not command:
            self._reject(400, "cmd_id or command missing or malformed")
            return
        if not isinstance(args, dict):
            self._reject(400, "args is not an object")
            return

        self.logger.log(
            "cmd.received",
            f"accepted {command} from the CLI",
            src="server",
            cmd_id=cmd_id,
            data={"command": command, "args": args, "timeout_s": timeout_s},
        )

        if not self.relay.page_connected():
            self.logger.log(
                "cmd.no_page",
                "no page is connected, so the command cannot be delivered",
                level="error",
                src="server",
                cmd_id=cmd_id,
            )
            self._send_json(409, {
                "ok": False,
                "cmd_id": cmd_id,
                "error": "no page connected: open the simulator in Chrome first",
            })
            return

        pending = self.relay.submit(cmd_id, command, args)
        delivered = pending.done.wait(timeout=timeout_s)
        if not delivered:
            self.relay.drop(cmd_id)
            self.logger.log(
                "cmd.timeout",
                f"no result for {command} within {timeout_s}s",
                level="error",
                src="server",
                cmd_id=cmd_id,
                data={"command": command, "timeout_s": timeout_s},
            )
            self._send_json(504, {
                "ok": False,
                "cmd_id": cmd_id,
                "error": f"timeout after {timeout_s}s",
            })
            return

        result = pending.result or {}
        self._send_json(200, {
            "ok": bool(result.get("ok")),
            "cmd_id": cmd_id,
            "data": result.get("data"),
            "error": result.get("error"),
        })

    def _handle_cmd_result(self, body: Dict[str, Any]) -> None:
        """`POST /api/cmd/result` -- the page's answer to one command.
        `POST /api/cmd/result` -- ページが 1 つの命令に返す結果。"""
        cmd_id = body.get("cmd_id")
        if not jsonl_log.is_cmd_id(cmd_id):
            self._reject(400, "cmd_id missing or malformed")
            return

        result = {
            "ok": bool(body.get("ok")),
            "data": body.get("data"),
            "error": body.get("error"),
        }
        matched = self.relay.complete(cmd_id, result)
        if not matched:
            self._reject(409, f"no command is waiting for {cmd_id}")
            return

        self.logger.log(
            "cmd.completed",
            "page returned a result",
            level="info" if result["ok"] else "error",
            src="server",
            cmd_id=cmd_id,
            data={"ok": result["ok"], "error": result["error"]},
        )
        self._send_json(200, {"ok": True})

    def _handle_log(self, body: Dict[str, Any]) -> None:
        """`POST /api/log` -- a batch of the page's own JSON Lines.

        Each line is validated (required keys, the fixed vocabularies, a
        run_id equal to this run's, and the per-line size cap) before being
        appended as it arrived. A line is stored unchanged: rebuilding it here
        would lose the page's `sim_us`, `frame` and `tag`.

        `POST /api/log` -- ページが送る JSON Lines のまとまり。

        1 行ずつ検査（必須の鍵、決めた語彙、この実行の run_id と一致するか、
        1 行の上限）してから、届いたままの形で追記する。ここで作り直すと
        ページ側の `sim_us`・`frame`・`tag` が失われるため、行は変えない。
        """
        lines = body.get("lines")
        if not isinstance(lines, list):
            self._reject(400, "lines is not an array")
            return

        accepted = 0
        rejected = 0
        for entry in lines:
            reason = self._store_page_line(entry)
            if reason is None:
                accepted += 1
            else:
                rejected += 1

        self.logger.log(
            "log.accepted",
            f"stored {accepted} page lines ({rejected} rejected)",
            level="warn" if rejected else "debug",
            src="server",
            data={"accepted": accepted, "rejected": rejected},
        )
        self._send_json(200, {"ok": True, "accepted": accepted, "rejected": rejected})

    def _handle_trace(self) -> None:
        """`POST /api/trace` -- the page's per-tick diagnostic ring.

        Kept apart from `/api/log` on purpose. A trace is thousands of
        per-tick records, which is exactly what `AGENTS.md` keeps out of the
        log, and it is written verbatim to its own file rather than validated
        line by line: it is a diagnostic dump for `trace_diff.py`, not part of
        the run's narrative, and the whole point is that it crosses unchanged.

        `POST /api/trace` -- ページが持つ刻みごとの診断の輪。

        意図して `/api/log` とは分けてある。トレースは刻みごとの記録が数千件で、
        それこそ `AGENTS.md` がログから外しているものである。1 行ずつ検査せず
        そのままファイルへ書くのは、これが `trace_diff.py` のための診断の取り出し
        であって実行の筋書きの一部ではなく、変えずに渡ることに意味があるため。
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reject(400, "Content-Length is not a number")
            return

        if length <= 0:
            self._reject(400, "the trace body is empty")
            return

        if length > MAX_TRACE_BYTES:
            self._reject(
                413, f"the trace exceeds {MAX_TRACE_BYTES} bytes")
            return

        raw = self.rfile.read(length)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            self._reject(400, "the trace is not UTF-8")
            return

        path = _run_trace_path(self.relay.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

        ticks = max(0, text.count("\n") - 1)   # the header line is not a tick
        self.logger.log(
            "trace.stored",
            f"stored {ticks} per-tick samples in {path.name}",
            src="server",
            data={"ticks": ticks, "bytes": len(raw), "path": str(path)},
        )
        self._send_json(200, {"ok": True, "ticks": ticks, "bytes": len(raw)})

    def _store_page_line(self, entry: Any) -> Optional[str]:
        """Validate and append one page line. Returns None when stored, or the
        reason it was refused (also written as a `log.rejected` event).
        ページの行を 1 つ検査して追記する。保存できたら None、拒否したときは
        その理由を返す（`log.rejected` の行としても記録する）。"""
        oversize = len(json.dumps(entry, ensure_ascii=False).encode("utf-8")) > \
            jsonl_log.MAX_LINE_BYTES
        if oversize:
            reason = f"line exceeds {jsonl_log.MAX_LINE_BYTES} bytes"
        else:
            reason = jsonl_log.validate_record(entry, expect_run_id=self.relay.run_id)

        if reason is not None:
            self.logger.log(
                "log.rejected",
                f"page line refused: {reason}",
                level="warn",
                src="server",
                data={"reason": reason},
            )
            return reason

        self.logger.write_record(entry)
        return None


def _clamp_wait(raw: Optional[str]) -> float:
    """Read the `wait` query value, falling back to the default and never
    exceeding the cap. A page asking for an unbounded wait must not be able to
    hold a server thread indefinitely.
    問い合わせの `wait` を読む。読めなければ既定、上限は超えない。際限なく
    待つよう求めるページが、サーバのスレッドを無期限に押さえられないように
    する。"""
    if raw is None:
        return CMD_POLL_DEFAULT_WAIT_S
    try:
        value = float(raw)
    except ValueError:
        return CMD_POLL_DEFAULT_WAIT_S
    return max(0.0, min(value, CMD_POLL_MAX_WAIT_S))


def _media_type(path: Path) -> str:
    """The media type for a served file. A compression suffix is stripped
    first, so `Build.wasm.br` is `application/wasm` with
    `Content-Encoding: br` rather than a type of its own.
    配信するファイルの種別。圧縮の拡張子を先に外すので、`Build.wasm.br` は
    `Content-Encoding: br` 付きの `application/wasm` になり、独自の種別には
    ならない。"""
    name = path.name
    for suffix in CONTENT_ENCODINGS:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break

    for extension, media_type in EXTRA_MEDIA_TYPES.items():
        if name.endswith(extension):
            return media_type

    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def _make_handler(
    relay: _Relay,
    logger: jsonl_log.JsonlLogger,
    build_dir: Path,
    coi: bool,
) -> type:
    """Build the handler subclass carrying this server's state.
    このサーバの状態を持つ処理クラスを作る。"""
    return type("_UnityHandler", (_Handler,), {
        "relay": relay,
        "logger": logger,
        "build_dir": build_dir,
        "coi": coi,
        "server_version_string": _sfcli_version,
    })


# ---------------------------------------------------------------------------
# sf unity serve
# ---------------------------------------------------------------------------

def run_serve(args: argparse.Namespace) -> int:
    """Serve the WebGL build and relay commands until interrupted.
    WebGL のビルドを配信し、中断されるまで命令を中継する。"""
    build_dir = Path(args.dir).resolve() if args.dir else _default_build_dir()
    if not build_dir.is_dir():
        console.error(f"WebGL build not found: {build_dir}")
        console.print("  Build it first:")
        console.print("    sf unity build")
        console.print("  Or point at an existing build:")
        console.print("    sf unity serve --dir <path>")
        return 1

    run_id = jsonl_log.new_run_id()
    log_path = _run_log_path(run_id)
    logger = jsonl_log.JsonlLogger(log_path, run_id, src="server")
    relay = _Relay(logger, run_id)

    handler = _make_handler(relay, logger, build_dir, bool(args.coi))
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError as error:
        console.error(f"Cannot bind 127.0.0.1:{args.port}: {error}")
        console.print("  Another sf unity serve may already be running.")
        console.print("  Use a different port:  sf unity serve --port <n>")
        logger.log(
            "server.stop", f"bind failed: {error}", level="error", src="server",
        )
        logger.close()
        return 1

    port = httpd.server_address[1]
    url = _page_url(port, args.world)
    # The port/pid record is written first and `latest` second: `sf unity cmd`
    # reads `latest` and then that run's record, so by the time `latest` names
    # this run, the record it will look for is already on disk.
    # ポートと pid の記録を先に、`latest` を後に書く。`sf unity cmd` は
    # `latest` を読んでからその実行の記録を読むため、`latest` がこの実行を
    # 指した時点で、探しに行く記録は既に置かれている。
    _write_server_info(run_id, port, build_dir)
    _latest_path().write_text(run_id + "\n", encoding="utf-8")

    logger.log(
        "server.start",
        f"serving {build_dir} at http://127.0.0.1:{port}/",
        src="server",
        data={
            "port": port,
            "pid": os.getpid(),
            "build_dir": str(build_dir),
            "coi": bool(args.coi),
            "world": args.world,
            "host": jsonl_log.hostname(),
        },
    )

    console.info(f"Unity simulator server: {url}")
    console.print(f"  Build : {build_dir}")
    console.print(f"  Log   : {log_path}")
    console.print(f"  Run   : {run_id}")
    console.print("  Send commands from another terminal:  sf unity cmd <command>")
    console.print("  Ctrl-C to stop")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    exit_code = 0
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        console.print()
        console.info("Server stopped")
    finally:
        httpd.shutdown()
        httpd.server_close()
        logger.log("server.stop", "server stopped", src="server")
        logger.close()
        _server_info_path(run_id).unlink(missing_ok=True)
    return exit_code


def _page_url(port: int, world: Optional[str]) -> str:
    """The page's URL, with `?world=<name>` when one was named. The URL
    argument is the plan's third path into the page and works on the public
    site too, so the server uses the same spelling.
    ページの URL。空間を指定したときは `?world=<名前>` を付ける。URL の引数は
    計画のもう 1 つの経路で公開サイトでも使えるため、同じ綴りを使う。"""
    base = f"http://127.0.0.1:{port}/"
    return f"{base}?world={world}" if world else base


def _write_server_info(run_id: str, port: int, build_dir: Path) -> None:
    """Record port and pid so `sf unity cmd` can find this server.
    `sf unity cmd` がこのサーバを見つけられるよう、ポートと pid を記録する。"""
    _server_info_path(run_id).write_text(
        json.dumps({
            "run_id": run_id,
            "port": port,
            "pid": os.getpid(),
            "build_dir": str(build_dir),
            "started": jsonl_log.utc_now(),
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# sf unity cmd
# ---------------------------------------------------------------------------

def _find_server() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Locate the running server: read `logs/unity/latest` for the run_id, then
    that run's `<run_id>.server.json` for the port and pid. Returns
    (info, error_message).
    動いているサーバを見つける: `logs/unity/latest` から run_id を読み、その
    実行の `<run_id>.server.json` からポートと pid を読む。
    (情報, エラー本文) を返す。"""
    run_id = _read_latest_run_id()
    if run_id is None:
        return None, "no run recorded in logs/unity/latest"

    info_path = _server_info_path(run_id)
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, f"no running server recorded for run {run_id}"
    if not isinstance(info, dict) or "port" not in info:
        return None, f"server record for run {run_id} is malformed"
    return info, None


def run_cmd(args: argparse.Namespace) -> int:
    """Issue a cmd_id, post the command to the running server, print the
    result as JSON on stdout, and exit non-zero on failure.
    cmd_id を発行してサーバへ命令を送り、結果を JSON で標準出力に出す。
    失敗なら非 0 で終わる。"""
    parsed_args, parse_error = _collect_command_args(args)
    if parse_error is not None:
        console.error(parse_error)
        return 2

    info, error = _find_server()
    if info is None:
        console.error(f"No running simulator server: {error}")
        console.print("  Start one first:")
        console.print("    sf unity serve")
        return 1

    run_id = info["run_id"]
    cmd_id = jsonl_log.new_cmd_id()
    port = int(info["port"])

    # The CLI's own lines go through the server (POST /api/log) rather than
    # straight to the file: one process owns the file, so appends from several
    # processes cannot tear a line.
    # CLI 自身の行もサーバ経由（POST /api/log）で書く。ファイルを持つのは
    # 1 つのプロセスだけとし、複数プロセスの追記で行が壊れないようにする。
    _post_cli_line(port, run_id, cmd_id, "cmd.issued",
                   f"issued {args.command}", data={"command": args.command,
                                                   "args": parsed_args})

    payload = {
        "cmd_id": cmd_id,
        "command": args.command,
        "args": parsed_args,
        "timeout": args.timeout,
    }
    status, response = _post_json(port, "/api/cmd", payload,
                                  timeout_s=args.timeout + 5.0)

    if response is None:
        console.error(f"Cannot reach the server on port {port}")
        return 1

    ok = status == 200 and bool(response.get("ok"))
    _post_cli_line(
        port, run_id, cmd_id, "cmd.result",
        "command succeeded" if ok else "command failed",
        level="info" if ok else "error",
        data={"status": status, "error": response.get("error")},
    )

    print(json.dumps(response, ensure_ascii=False, indent=2))
    if not ok:
        console.error(response.get("error") or f"server returned {status}")
        return 1
    return 0


def make_sender():
    """A callable that sends one command the same way `sf unity cmd` does, for
    a script that issues many of them.

    `sf unity check fly` posts through `POST /api/cmd` on the running server
    rather than over a private channel, so the automated check exercises the
    route a person exercises. The server is located once, when this is built,
    and every command afterwards reuses that; a `cmd_id` is still issued per
    command, which is what keeps `sf unity logs --cmd <id>` working for each
    step of the flight.

    `sf unity cmd` と同じやり方で命令を 1 つ送る呼び出し可能なものを返す。命令を
    何度も出す台本のため。

    `sf unity check fly` は自前の経路ではなく、動いているサーバの
    `POST /api/cmd` を通す。自動の確認が、人が使うのと同じ道を使うためである。
    サーバを探すのはこれを作るときの 1 回だけで、以後の命令はその結果を使い回す。
    `cmd_id` は命令ごとに発行するので、飛行のどの手順も
    `sf unity logs --cmd <id>` で引ける。

    Raises RuntimeError when no server is running, because a sender with
    nowhere to send is not something a caller can use.
    サーバが動いていなければ RuntimeError にする。送り先の無い送り手は、呼び出し
    側に使えるものではないためである。
    """
    info, error = _find_server()
    if info is None:
        raise RuntimeError(f"no running simulator server: {error}")

    run_id = info["run_id"]
    port = int(info["port"])

    def send(command: str, args: Dict[str, Any], timeout: float):
        cmd_id = jsonl_log.new_cmd_id()
        _post_cli_line(port, run_id, cmd_id, "cmd.issued",
                       f"issued {command}",
                       data={"command": command, "args": args})

        status, response = _post_json(
            port, "/api/cmd",
            {"cmd_id": cmd_id, "command": command, "args": args,
             "timeout": timeout},
            timeout_s=timeout + 5.0,
        )

        if response is None:
            _post_cli_line(port, run_id, cmd_id, "cmd.result",
                           "the server could not be reached", level="error")
            return False, f"cannot reach the server on port {port}", cmd_id

        ok = status == 200 and bool(response.get("ok"))
        _post_cli_line(
            port, run_id, cmd_id, "cmd.result",
            "command succeeded" if ok else "command failed",
            level="info" if ok else "error",
            data={"status": status, "error": response.get("error")},
        )
        return ok, response.get("data") if ok else response.get("error"), cmd_id

    return send


def _collect_command_args(args: argparse.Namespace) -> Tuple[Dict[str, Any], Optional[str]]:
    """Merge `--json '{...}'` and the `--arg key=value` pairs into one object.
    A `--arg` value is kept as a string: the page's command handlers know
    their own types, and guessing here would turn a world named `2026` into a
    number. Returns (args, error_message).
    `--json '{...}'` と `--arg key=value` を 1 つのオブジェクトにまとめる。
    `--arg` の値は文字列のままにする。型はページ側の命令が知っており、ここで
    推測すると `2026` という名前の空間が数値になってしまう。
    (引数, エラー本文) を返す。"""
    merged: Dict[str, Any] = {}

    if args.json:
        try:
            decoded = json.loads(args.json)
        except ValueError as error:
            return {}, f"--json is not valid JSON: {error}"
        if not isinstance(decoded, dict):
            return {}, "--json must be a JSON object"
        merged.update(decoded)

    for pair in args.arg or []:
        if "=" not in pair:
            return {}, f"--arg must be key=value, got {pair!r}"
        key, _, value = pair.partition("=")
        merged[key.strip()] = value
    return merged, None


def _post_json(
    port: int,
    path: str,
    payload: Dict[str, Any],
    timeout_s: float,
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """POST a JSON object to the local server and decode the answer.

    An error status still carries a JSON body (the server always answers with
    one), so HTTPError is decoded rather than treated as a failure to reach
    the server -- a 409 "no page connected" must print its own message.

    ローカルサーバへ JSON を POST し、応答を復号する。

    エラーの状態でも本文は JSON である（サーバは常に JSON で答える）ため、
    HTTPError は復号する。サーバへ届かなかった場合とは区別する必要がある
    （409「ページが繋がっていない」は自分の本文を出す必要がある）。
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urlrequest.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(request, timeout=timeout_s) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urlerror.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b"{}")
        except ValueError:
            return error.code, {"ok": False, "error": error.reason}
    except (urlerror.URLError, OSError, ValueError):
        return 0, None


def _post_cli_line(
    port: int,
    run_id: str,
    cmd_id: str,
    event: str,
    msg: str,
    level: str = "info",
    data: Optional[Dict[str, Any]] = None,
) -> None:
    """Send one `src: "cli"` line to the server's log. Best effort: a command
    must not fail because its own log line did not land.
    `src: "cli"` の行を 1 つサーバのログへ送る。届かなくても命令自体は失敗
    させない。"""
    record: Dict[str, Any] = {
        "ts": jsonl_log.utc_now(),
        "level": level,
        "src": "cli",
        "event": event,
        "run_id": run_id,
        "cmd_id": cmd_id,
        "msg": msg,
    }
    if data:
        record["data"] = data
    _post_json(port, "/api/log", {"lines": [record]}, timeout_s=5.0)


# ---------------------------------------------------------------------------
# sf unity logs
# ---------------------------------------------------------------------------

def run_logs(args: argparse.Namespace) -> int:
    """Show one run's log, narrowed by the given conditions.
    1 回の実行のログを、渡した条件で絞り込んで表示する。"""
    run_id = _resolve_run_id(args.run)
    if run_id is None:
        console.error("No run found: logs/unity/latest is missing or unreadable")
        console.print("  Start a server first:  sf unity serve")
        return 1

    log_path = _run_log_path(run_id)
    if not log_path.is_file():
        console.error(f"Log file not found: {log_path}")
        return 1

    sources = [part.strip() for part in args.src.split(",") if part.strip()] \
        if args.src else None

    if args.follow:
        return _follow_log(log_path, args, sources)

    records, broken = jsonl_log.read_records(log_path)
    selected = jsonl_log.sort_by_time(jsonl_log.filter_records(
        records,
        cmd_id=args.cmd,
        sources=sources,
        level=args.level,
        event_prefix=args.event,
        since_sim_us=args.since_sim_us,
    ))

    for record in selected:
        _print_record(record, as_json=args.json)

    if broken:
        console.warning(f"{broken} malformed line(s) skipped in {log_path}")
    return 0


def _print_record(record: Dict[str, Any], as_json: bool) -> None:
    """One record to stdout: the raw line under `--json`, otherwise the
    one-line human-readable form.
    1 行を標準出力へ。`--json` は元の行のまま、既定は人が読む 1 行表示。"""
    if as_json:
        print(json.dumps(record, ensure_ascii=False))
    else:
        print(jsonl_log.format_line(record))


def _follow_log(
    log_path: Path,
    args: argparse.Namespace,
    sources: Optional[List[str]],
) -> int:
    """Print matching lines as they are appended, until interrupted. The file
    handle stays open and is re-read from its previous offset, which is what
    `tail -f` does and what a long simulator session needs.
    追記された行を、中断されるまで出し続ける。ファイルを開いたまま前回の位置
    から読み直す（`tail -f` と同じやり方で、長く続くシミュレータの実行に必要）。"""
    broken_total = 0
    try:
        with log_path.open("r", encoding="utf-8") as handle:
            while True:
                line = handle.readline()
                if not line:
                    time.sleep(0.2)
                    continue
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except ValueError:
                    broken_total += 1
                    continue
                if not isinstance(record, dict):
                    broken_total += 1
                    continue
                matched = jsonl_log.filter_records(
                    [record],
                    cmd_id=args.cmd,
                    sources=sources,
                    level=args.level,
                    event_prefix=args.event,
                    since_sim_us=args.since_sim_us,
                )
                if matched:
                    _print_record(record, as_json=args.json)
    except KeyboardInterrupt:
        console.print()
        if broken_total:
            console.warning(f"{broken_total} malformed line(s) skipped")
    return 0


# ---------------------------------------------------------------------------
# Unity CLI wrappers / Unity CLI の包み
# ---------------------------------------------------------------------------

def _unity_executable() -> Optional[str]:
    """The Unity CLI: `~/.unity/bin/unity` (the documented location), or
    whatever `unity` resolves to on PATH. None when neither exists.
    Unity CLI。`~/.unity/bin/unity`（文書にある置き場）か、PATH 上の `unity`。
    どちらも無ければ None。"""
    home_copy = Path.home() / ".unity" / "bin" / "unity"
    if home_copy.is_file() and os.access(home_copy, os.X_OK):
        return str(home_copy)
    return shutil.which("unity")


def _require_unity() -> Optional[str]:
    """The Unity CLI, or None after printing how to install it.
    Unity CLI。無ければ導入方法を表示して None を返す。"""
    executable = _unity_executable()
    if executable is None:
        console.error("Unity CLI not found (expected ~/.unity/bin/unity or `unity` on PATH)")
        console.print("  Install it, then check the version:")
        console.print("    unity --version        # this project uses 1.0.0-beta.8")
        console.print("  The editor version is pinned in "
                      "simulator/unity/ProjectSettings/ProjectVersion.txt")
        return None
    return executable


def build_webgl_command(executable: str, project_dir: Path, output_dir: Path,
                        release: bool) -> List[str]:
    """Assemble the `unity build` command line for WebGL.

    Kept as a pure function so a unit test can check the shape without an
    editor: WebGL has no built-in command-line build, so `--execute-method`
    is mandatory (README section 2: `--target WebGL` alone exits 2), and
    `--non-interactive --no-tail` are what the README shows for a scripted
    build.

    WebGL 用の `unity build` のコマンド行を組み立てる。

    エディタ無しで単体試験から形を確かめられるよう、純粋な関数にしてある。
    WebGL には内蔵のコマンドライン用ビルドが無いので `--execute-method` は
    必須（README §2。`--target WebGL` だけでは終了コード 2）。
    `--non-interactive --no-tail` は README が端末からのビルドで示す形。
    """
    command = [
        executable, "build", str(project_dir),
        "--target", "WebGL",
        "--execute-method", WEBGL_BUILD_METHOD,
        "-o", str(output_dir),
        "--non-interactive", "--no-tail",
    ]
    command.extend(["--format", "ndjson"])
    if release:
        # The distributed build must not carry the command relay endpoint
        # (plan section 4). `unity build` has no `--release` of its own and
        # rejects an unknown option outright, so the flag travels inside
        # `--args`, which is the CLI's documented way of putting something on
        # the editor's own command line. `WebGLBuilder` reads it from there,
        # the same place it reads `-buildOutput`.
        # 配布用ビルドには命令の中継の受け口を入れない（計画 §4）。`unity build`
        # 自身に `--release` は無く、未知の引数はその場で拒否されるので、この引数は
        # `--args` の中を通す。エディタ自身のコマンド行へ何かを置く、CLI が定める
        # 方法がそれである。`WebGLBuilder` はそこから読む。`-buildOutput` を読むのと
        # 同じ場所である。
        command.extend(["--args", BUILD_RELEASE_FLAG])
    return command


def build_test_command(executable: str, project_dir: Path, output_path: Path,
                       mode: str) -> List[str]:
    """Assemble the `unity test` command line.

    `--mode` takes `PlayMode` or `EditMode` (README section 2's spelling, which
    is not what the `--mode edit|play` option accepts, so it is mapped here).
    The NUnit report goes to `--output`.

    `unity test` のコマンド行を組み立てる。

    `--mode` は `PlayMode` ／ `EditMode`（README §2 の綴り。`--mode edit|play`
    の受け取りとは違うため、ここで対応付ける）。NUnit 形式の報告書は
    `--output` に書かれる。
    """
    unity_mode = {"play": "PlayMode", "edit": "EditMode"}[mode]
    return [
        executable, "test", str(project_dir),
        "--mode", unity_mode,
        "--output", str(output_path),
        "--non-interactive",
        "--format", "ndjson",
    ]


def _run_unity(command: List[str], ndjson_path: Path, logger: jsonl_log.JsonlLogger,
               start_event: str, finish_event: str, src: str) -> int:
    """Run a Unity CLI command, saving its `--format ndjson` output to
    `ndjson_path`, and return its exit code unchanged.

    The output is teed: written to the file line by line and echoed to the
    terminal, so a long build shows progress while still leaving a machine-
    readable record.

    Unity CLI のコマンドを実行し、`--format ndjson` の出力を `ndjson_path` へ
    保存して、終了コードをそのまま返す。

    出力は二手に分ける: 1 行ずつファイルへ書き、端末にも出す。長いビルドの
    進み具合が見え、かつ機械で読める記録も残る。
    """
    logger.log(start_event, " ".join(command), src=src,
               data={"command": command, "ndjson": str(ndjson_path)})
    console.info(f"Running: {' '.join(command)}")

    ndjson_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ndjson_path.open("w", encoding="utf-8") as sink:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                sink.write(line)
                sink.flush()
                console.print(line.rstrip())
            exit_code = process.wait()
    except OSError as error:
        console.error(f"Failed to run the Unity CLI: {error}")
        logger.log(finish_event, f"failed to launch: {error}", level="error", src=src)
        return 1

    level = "info" if exit_code == 0 else "error"
    logger.log(finish_event, f"exit code {exit_code}", level=level, src=src,
               data={"exit_code": exit_code, "ndjson": str(ndjson_path)})
    return exit_code


def run_build(args: argparse.Namespace) -> int:
    """`sf unity build` -- WebGL build through the Unity CLI.
    `sf unity build` -- Unity CLI による WebGL ビルド。"""
    executable = _require_unity()
    if executable is None:
        return 1

    project_dir = _unity_project_dir()
    if not project_dir.is_dir():
        console.error(f"Unity project not found: {project_dir}")
        return 1

    output_dir = Path(args.output).resolve() if args.output else _default_build_dir()
    run_id = jsonl_log.new_run_id()
    ndjson_path = _unity_logs_dir() / f"build-{run_id}.jsonl"

    with jsonl_log.JsonlLogger(_run_log_path(run_id), run_id, src="build") as logger:
        command = build_webgl_command(executable, project_dir, output_dir,
                                      bool(args.release))
        exit_code = _run_unity(command, ndjson_path, logger,
                               "build.start", "build.finished", src="build")

    if exit_code != 0:
        console.error(f"Build failed (exit code {exit_code})")
        console.print(f"  Unity CLI output: {ndjson_path}")
        return exit_code

    if args.release:
        problems = inspect_release_build(output_dir)
        if problems:
            console.error("The release build carries the command relay:")
            for problem in problems:
                console.print(f"  - {problem}")
            console.print("  A distributed build must not (plan section 4).")
            console.print("  Check that StampFly.Remote's defineConstraints and")
            console.print("  WebGLBuilder's -stampflyRelease handling still agree.")
            return 1
        console.success("The release build carries no command relay")

    console.success(f"WebGL build written to {output_dir}")
    console.print(f"  Serve it:  sf unity serve --dir {output_dir}")
    return 0


# What `WebGLBuilder` writes beside the player, saying which assemblies the
# build actually included. Rename it here and in `WebGLBuilder` together.
# `WebGLBuilder` がプレイヤーの隣に書くファイル。ビルドが実際に含めたアセンブリを
# 述べる。名前を変えるときは `WebGLBuilder` と揃える。
BUILD_MANIFEST = "stampfly-build-manifest.json"

# The assembly a distributed build must not carry, and the define symbol that
# would put it there. Plan section 4: the relay endpoint goes into development
# builds only.
# 配布用ビルドが持ってはならないアセンブリと、それを入れてしまう定義記号。
# 計画 §4「中継の受け口は開発用ビルドだけに入れる」。
RELAY_ASSEMBLY = "StampFly.Remote"
RELAY_SYMBOL = "STAMPFLY_REMOTE"

# Names from the relay's browser end. A .jslib is merged into the player's
# framework JavaScript, which the build compresses; only the files that stay
# plain (the loader and the page) are searched for these, so this is a second
# look rather than the main one.
# 中継のブラウザ側の名前。.jslib はプレイヤーのフレームワークの JavaScript へ
# 統合され、ビルドがそれを圧縮する。ここで探すのは非圧縮のまま残るファイル
# （ローダとページ）だけなので、これは主たる検査ではなく二の矢である。
RELAY_JSLIB_NAMES = ("SfuRemoteStart", "SfuRemoteAnswer", "/api/cmd/next")


def inspect_release_build(output_dir: Path) -> List[str]:
    """What a distributed build must not contain, and does. Empty means clean.

    Plan section 4 puts the command relay in development builds only, and
    `StampFly.Remote`'s `defineConstraints` are what enforce it. This looks at
    what was produced rather than trusting the constraint, because the two ways
    it could quietly stop holding -- somebody adding `STAMPFLY_REMOTE` to the
    release define symbols, or a reference from an unconstrained assembly
    dragging the code back in -- both leave the constraint looking correct.

    The evidence is the build manifest `WebGLBuilder` writes, which names the
    managed assemblies IL2CPP was handed. It is read rather than the player
    itself because a WebGL player's code lands inside a Brotli-compressed
    `.unityweb` that the standard library cannot open; a missing manifest is
    therefore a problem in its own right, not a pass by default.

    配布用ビルドが含んではならず、実際には含んでいるもの。空なら綺麗である。

    計画 §4 は命令の中継を開発用ビルドだけに入れると定め、それを強制するのが
    `StampFly.Remote` の `defineConstraints` である。ここでは制約を信じず、出来た
    ものを見る。制約が静かに効かなくなる 2 つの道 ― 誰かが配布用の定義記号に
    `STAMPFLY_REMOTE` を足す、制約の無いアセンブリからの参照がコードを引き戻す ―
    は、どちらも制約自体は正しく見えたままだからである。

    根拠は `WebGLBuilder` が書くビルドの目録で、IL2CPP に渡されたマネージドの
    アセンブリを名指しする。プレイヤー自身ではなくこちらを読むのは、WebGL の
    プレイヤーのコードが、標準ライブラリでは開けない Brotli 圧縮の `.unityweb` の
    中に入るためである。よって目録が無いこと自体が問題であり、既定で合格には
    しない。
    """
    problems: List[str] = []

    manifest_path = output_dir / BUILD_MANIFEST
    if not manifest_path.is_file():
        return [f"{BUILD_MANIFEST} is missing from {output_dir}: the build did "
                "not say what it contains, so this check cannot pass it"]

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"{BUILD_MANIFEST} could not be read: {error}"]

    if manifest.get("release") is not True:
        problems.append(f"{BUILD_MANIFEST} says release={manifest.get('release')!r}: "
                        "this is not a distributed build")

    assemblies = manifest.get("assemblies") or []
    if RELAY_ASSEMBLY in assemblies:
        problems.append(f"the build included the assembly {RELAY_ASSEMBLY}")

    symbols = str(manifest.get("define_symbols", ""))
    if RELAY_SYMBOL in symbols.split(";"):
        problems.append(f"the build defined {RELAY_SYMBOL}, which compiles the "
                        "relay in")

    problems.extend(_relay_names_in_plain_files(output_dir))
    return problems


def _relay_names_in_plain_files(output_dir: Path) -> List[str]:
    """The relay's browser-side names found in whatever the build left
    uncompressed -- the loader script and the page. The framework JavaScript a
    `.jslib` is merged into is compressed, so this catches only the easy cases;
    the manifest above is what the verdict actually rests on.
    ビルドが非圧縮のまま残したもの ― ローダの script とページ ― の中に見つかった、
    中継のブラウザ側の名前。`.jslib` が統合されるフレームワークの JavaScript は
    圧縮されるので、ここで捕まるのは易しい場合だけである。判定が実際に拠るのは
    上の目録である。"""
    found: List[str] = []
    for path in sorted(output_dir.rglob("*")):
        is_plain_text = path.is_file() and path.suffix in (".js", ".html", ".json") \
            and "StreamingAssets" not in path.parts and path.name != BUILD_MANIFEST
        if not is_plain_text:
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for name in RELAY_JSLIB_NAMES:
            if name in text:
                found.append(f"{path.relative_to(output_dir)} contains {name!r} "
                             "from the relay's .jslib")
    return found


def run_test(args: argparse.Namespace) -> int:
    """`sf unity test` -- EditMode or PlayMode tests through the Unity CLI.
    Exit code 8 means at least one test failed (README section 4).
    `sf unity test` -- Unity CLI による EditMode ／ PlayMode 試験。終了コード
    8 は 1 件以上の不合格を表す（README §4）。"""
    executable = _require_unity()
    if executable is None:
        return 1

    project_dir = _unity_project_dir()
    if not project_dir.is_dir():
        console.error(f"Unity project not found: {project_dir}")
        return 1

    run_id = jsonl_log.new_run_id()
    ndjson_path = _unity_logs_dir() / f"test-{run_id}.jsonl"
    report_path = _unity_logs_dir() / f"test-{run_id}.xml"

    with jsonl_log.JsonlLogger(_run_log_path(run_id), run_id, src="test") as logger:
        command = build_test_command(executable, project_dir, report_path, args.mode)
        exit_code = _run_unity(command, ndjson_path, logger,
                               "test.start", "test.finished", src="test")

    if exit_code == 0:
        console.success(f"All {args.mode}Mode tests passed")
    elif exit_code == UNITY_TEST_FAILURE_EXIT:
        console.error(f"Tests failed. NUnit report: {report_path}")
    else:
        console.error(f"Unity CLI exited with {exit_code}")
    console.print(f"  Unity CLI output: {ndjson_path}")
    return exit_code


def run_open(args: argparse.Namespace) -> int:
    """`sf unity open` -- open the project in the Unity editor.
    `sf unity open` -- Unity エディタでプロジェクトを開く。"""
    executable = _require_unity()
    if executable is None:
        return 1
    project_dir = _unity_project_dir()
    if not project_dir.is_dir():
        console.error(f"Unity project not found: {project_dir}")
        return 1
    console.info(f"Opening {project_dir} in the Unity editor")
    return subprocess.run([executable, "open", str(project_dir)]).returncode


def run_setup(args: argparse.Namespace) -> int:
    """`sf unity setup` -- add `com.unity.pipeline`, which is what lets the
    terminal drive a running editor (`unity command`). Run once per checkout.
    `sf unity setup` -- `com.unity.pipeline` を追加する。端末から動いている
    エディタを操作する（`unity command`）ために要る。チェックアウトごとに
    1 回だけ実行する。"""
    executable = _require_unity()
    if executable is None:
        return 1
    project_dir = _unity_project_dir()
    if not project_dir.is_dir():
        console.error(f"Unity project not found: {project_dir}")
        return 1
    console.info("Installing com.unity.pipeline into the Unity project")
    return subprocess.run(
        [executable, "pipeline", "install", "--project-path", str(project_dir)]
    ).returncode


# ---------------------------------------------------------------------------
# sf unity check
# ---------------------------------------------------------------------------

def _load_fly_check():
    """Import `tools/unity_check/fly_check.py` and return it, or None after
    saying it is missing.

    Loaded by path inside the function for the same reason the world validator
    is (see `_load_world_validator`): `serve`, `cmd` and `logs` must keep
    working even if this backend is absent, and a module-level import would
    take the whole `unity` command down with it.

    `tools/unity_check/fly_check.py` を読み込んで返す。無ければその旨を出して
    None を返す。

    空間の検査と同じ理由で、モジュールの先頭ではなく関数の中でパスから読み込む
    （`_load_world_validator` を見よ）。このバックエンドが無くても `serve`・
    `cmd`・`logs` は動き続けなければならず、先頭で import すると `unity`
    コマンド全体を道連れにしてしまう。
    """
    # Found from this file's own location rather than from `paths.root()`:
    # the backend ships with this CLI, in the same checkout, and must be found
    # even when the caller has pointed `logs/` somewhere else.
    # `paths.root()` ではなくこのファイル自身の場所から探す。バックエンドはこの
    # CLI と同じチェックアウトで配られるものであり、呼び出し側が `logs/` を他所へ
    # 向けていても見つからなければならない。
    check_path = Path(__file__).resolve().parents[3] / \
        "tools" / "unity_check" / "fly_check.py"
    if not check_path.is_file():
        console.error(f"Flight check module not found: {check_path}")
        console.print("  It provides run(send, world, hold_seconds) -> dict.")
        return None

    import importlib.util
    spec = importlib.util.spec_from_file_location("sfcli_unity_fly_check",
                                                  check_path)
    if spec is None or spec.loader is None:
        console.error(f"Flight check module could not be loaded: {check_path}")
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        console.error(f"Flight check module failed to load: {error}")
        return None
    return module


def run_check_fly(args: argparse.Namespace) -> int:
    """`sf unity check fly` -- fly the stage 3 criterion against the open page.

    ARM, take off, hold in ALT_HOLD, land, DISARM, all through `POST /api/cmd`,
    and judge from `vehicle.state`. Prints the report as JSON on stdout and a
    readable summary on stderr, so a script can pipe the JSON while a person
    still sees what happened; exits non-zero when the flight failed.

    `sf unity check fly` -- 開いているページに対して段階 3 の基準を飛ばす。

    ARM・離陸・ALT_HOLD での保持・着地・DISARM を全て `POST /api/cmd` で行い、
    `vehicle.state` から判定する。報告を標準出力に JSON で、人が読む要約を標準
    エラーに出すので、台本は JSON をそのまま流しつつ人も何が起きたかを見られる。
    飛行が不合格なら非 0 で終わる。
    """
    module = _load_fly_check()
    if module is None:
        return 1

    try:
        send = make_sender()
    except RuntimeError as error:
        console.error(f"No running simulator server: {error}")
        console.print("  Start one and open the page in Chrome first:")
        console.print("    sf unity serve")
        return 1

    console.info(f"Flying the stage 3 check in '{args.world}' "
                 f"({args.hold_seconds}s of hold)")
    report = module.run(send, args.world, args.hold_seconds)

    print(json.dumps(report, ensure_ascii=False, indent=2))

    for line in module.summarise(report):
        console.print(line)

    arm_id = module.cmd_id_of(report, "rc.arm")
    if arm_id:
        console.print(f"  Follow one command end to end:  "
                      f"sf unity logs --cmd {arm_id}")

    if not report.get("pass"):
        console.error("The flight check failed")
        return 1
    console.success("The flight check passed")
    return 0


# ---------------------------------------------------------------------------
# sf unity world
# ---------------------------------------------------------------------------

def _load_world_validator():
    """Import `tools/unity_world/validate.py` and return it, or None after
    saying it is missing.

    Imported inside the function, not at module level: `sf unity serve`, `cmd`
    and `logs` must keep working while the validation module is being written
    by someone else, and a module-level import would make `sf` drop the whole
    `unity` command if it were absent (see `commands/__init__.py`).

    `tools/unity_world/validate.py` を読み込んで返す。無ければその旨を出して
    None を返す。

    モジュールの先頭ではなく関数の中で import する。検査のモジュールは別の
    担当が作っている途中で、先頭で import すると無い間は `sf` が `unity`
    コマンド全体を落としてしまう（`commands/__init__.py` 参照）。
    """
    validate_path = paths.root() / "tools" / "unity_world" / "validate.py"
    if not validate_path.is_file():
        console.error(f"World validation module not found: {validate_path}")
        console.print("  It provides validate_file(path) -> list[str] and")
        console.print("  list_worlds(root) -> list[dict].")
        return None

    import importlib.util
    spec = importlib.util.spec_from_file_location("sfcli_unity_world_validate",
                                                  validate_path)
    if spec is None or spec.loader is None:
        console.error(f"World validation module could not be loaded: {validate_path}")
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:  # the module is someone else's; report, don't crash
        console.error(f"World validation module failed to load: {error}")
        return None
    return module


def run_world_validate(args: argparse.Namespace) -> int:
    """`sf unity world validate <file...>` -- report each file's problems.
    Exit code 1 when any file has at least one problem.
    `sf unity world validate <file...>` -- ファイルごとに問題を報告する。
    1 つでも問題があれば終了コード 1。"""
    module = _load_world_validator()
    if module is None:
        return 1

    total_problems = 0
    for name in args.files:
        path = Path(name)
        problems = module.validate_file(path)
        if problems:
            total_problems += len(problems)
            console.error(f"{path}: {len(problems)} problem(s)")
            for problem in problems:
                console.print(f"  - {problem}")
        else:
            console.success(f"{path}: OK")

    if total_problems:
        console.error(f"{total_problems} problem(s) in total")
        return 1
    return 0


def run_world_list(args: argparse.Namespace) -> int:
    """`sf unity world list` -- the space files the project ships with.
    `sf unity world list` -- プロジェクトが同梱する空間ファイルの一覧。"""
    module = _load_world_validator()
    if module is None:
        return 1

    worlds = module.list_worlds(paths.root())
    if not worlds:
        console.info("No world files found")
        return 0

    for world in worlds:
        name = world.get("name", "?")
        path = world.get("path", "?")
        count = world.get("obstacle_count")
        suffix = f"  ({count} obstacles)" if isinstance(count, int) else ""
        console.print(f"  {name:20s} {path}{suffix}")
        description = _describe(world.get("description"))
        if description:
            console.print(f"  {'':20s} {description}")
    return 0


def _describe(description: Any) -> str:
    """One line of description from the validation module's entry. The module
    returns a per-language object (`{"ja": ..., "en": ...}`), so Japanese is
    preferred and English is the fallback; a plain string is used as it is.
    検査のモジュールの項目から説明の 1 行を作る。言語ごとのオブジェクト
    （`{"ja": ..., "en": ...}`）を返すため、日本語を優先し、無ければ英語を
    使う。文字列で来たものはそのまま使う。"""
    if isinstance(description, str):
        return description
    if isinstance(description, dict):
        for language in ("ja", "en"):
            text = description.get(language)
            if isinstance(text, str) and text:
                return text
    return ""


# ---------------------------------------------------------------------------
# Registration / 登録
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    """Register command with CLI"""
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help=COMMAND_HELP,
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    unity_subparsers = parser.add_subparsers(
        dest="unity_command",
        title="subcommands",
        metavar="<subcommand>",
    )

    # --- serve ---
    serve_parser = unity_subparsers.add_parser(
        "serve",
        help="Serve the WebGL build on 127.0.0.1 and relay commands",
        description="Serve a Unity WebGL build locally and relay `sf unity cmd` "
                    "commands to the page.",
    )
    serve_parser.add_argument(
        "--dir",
        help=f"WebGL build directory (default: {UNITY_PROJECT_SUBPATH}/"
             f"{WEBGL_BUILD_SUBPATH})",
    )
    serve_parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"Port on 127.0.0.1 (default: {DEFAULT_PORT})",
    )
    serve_parser.add_argument(
        "--no-browser", action="store_true",
        help="Do not open Chrome automatically",
    )
    serve_parser.add_argument(
        "--world",
        help="World to load on startup (passed as ?world=<name>)",
    )
    serve_parser.add_argument(
        "--coi", action="store_true",
        help="Send COOP/COEP headers (only needed by the -pthread build)",
    )
    serve_parser.set_defaults(func=run_serve)

    # --- cmd ---
    cmd_parser = unity_subparsers.add_parser(
        "cmd",
        help="Send one command to the running simulator page",
        description="Issue a cmd_id, post the command to the running "
                    "`sf unity serve`, and print the page's result as JSON.",
    )
    cmd_parser.add_argument("command", help="Command name, e.g. `sim.pause`")
    cmd_parser.add_argument(
        "--arg", action="append", metavar="KEY=VALUE",
        help="One argument; repeatable. Values stay strings.",
    )
    cmd_parser.add_argument(
        "--json", metavar="JSON",
        help="Arguments as a JSON object (merged with --arg)",
    )
    cmd_parser.add_argument(
        "--timeout", type=float, default=10.0,
        help="Seconds to wait for the page's result (default: 10)",
    )
    cmd_parser.set_defaults(func=run_cmd)

    # --- logs ---
    logs_parser = unity_subparsers.add_parser(
        "logs",
        help="Show a run's JSON Lines log",
        description="Filter and show `logs/unity/<run_id>.jsonl`.",
    )
    logs_parser.add_argument(
        "--run", default="latest",
        help="Run to read: `latest` (default) or a run_id",
    )
    logs_parser.add_argument("--cmd", help="Only lines carrying this cmd_id")
    logs_parser.add_argument(
        "--src", help="Comma-separated sources, e.g. `fw,sim`",
    )
    logs_parser.add_argument(
        "--level", choices=list(jsonl_log.LEVELS),
        help="Minimum level (`warn` keeps warn and error)",
    )
    logs_parser.add_argument("--event", help="Event name prefix, e.g. `cmd`")
    logs_parser.add_argument(
        "--since-sim-us", type=int, dest="since_sim_us",
        help="Only lines at or after this virtual time (microseconds)",
    )
    logs_parser.add_argument(
        "--follow", action="store_true", help="Keep printing new lines",
    )
    logs_parser.add_argument(
        "--json", action="store_true", help="Print the raw JSON lines",
    )
    logs_parser.set_defaults(func=run_logs)

    # --- build ---
    build_parser = unity_subparsers.add_parser(
        "build",
        help="Build the WebGL player through the Unity CLI",
        description="Run the Unity CLI's WebGL build.",
    )
    build_parser.add_argument(
        "--release", action="store_true",
        help="Build for distribution (no command relay endpoint)",
    )
    build_parser.add_argument("-o", "--output", help="Output directory")
    build_parser.set_defaults(func=run_build)

    # --- test ---
    test_parser = unity_subparsers.add_parser(
        "test",
        help="Run the Unity tests (exit code 8 on failure)",
        description="Run EditMode or PlayMode tests through the Unity CLI.",
    )
    test_parser.add_argument(
        "--mode", choices=["edit", "play"], default="play",
        help="Test platform (default: play)",
    )
    test_parser.set_defaults(func=run_test)

    # --- open / setup ---
    open_parser = unity_subparsers.add_parser(
        "open", help="Open the project in the Unity editor",
    )
    open_parser.set_defaults(func=run_open)

    setup_parser = unity_subparsers.add_parser(
        "setup", help="Install com.unity.pipeline into the project",
    )
    setup_parser.set_defaults(func=run_setup)

    # --- check ---
    check_parser = unity_subparsers.add_parser(
        "check", help="Run an automated check against the open page",
    )
    check_subparsers = check_parser.add_subparsers(
        dest="check_command", title="subcommands", metavar="<subcommand>",
    )
    fly_parser = check_subparsers.add_parser(
        "fly",
        help="ARM, take off, hold in ALT_HOLD and land, then judge the flight",
        description="Fly the stage 3 pass criterion against the page a running "
                    "`sf unity serve` is serving, and print the verdict as JSON.",
    )
    fly_parser.add_argument(
        "--world", default="empty_room",
        help="World to fly in (default: empty_room)",
    )
    fly_parser.add_argument(
        "--hold-seconds", type=float, default=10.0, dest="hold_seconds",
        help="How long to hold altitude, in simulated seconds (default: 10)",
    )
    fly_parser.set_defaults(func=run_check_fly)
    check_parser.set_defaults(func=run_check_help)

    # --- world ---
    world_parser = unity_subparsers.add_parser(
        "world", help="Validate and list `*.world.json` space files",
    )
    world_subparsers = world_parser.add_subparsers(
        dest="world_command", title="subcommands", metavar="<subcommand>",
    )
    validate_parser = world_subparsers.add_parser(
        "validate", help="Check `*.world.json` files against the schema",
    )
    validate_parser.add_argument("files", nargs="+", help="World files to check")
    validate_parser.set_defaults(func=run_world_validate)

    list_parser = world_subparsers.add_parser(
        "list", help="List the bundled world files",
    )
    list_parser.set_defaults(func=run_world_list)
    world_parser.set_defaults(func=run_world_help)

    parser.set_defaults(func=run_help)


def run_help(args: argparse.Namespace) -> int:
    """Show help when no subcommand specified"""
    console.print("Usage: sf unity <subcommand> [options]")
    console.print()
    console.print("Subcommands:")
    console.print("  serve   Serve the WebGL build on 127.0.0.1 and relay commands")
    console.print("  cmd     Send one command to the running simulator page")
    console.print("  logs    Show a run's JSON Lines log")
    console.print("  build   Build the WebGL player (Unity CLI)")
    console.print("  test    Run the Unity tests (Unity CLI)")
    console.print("  open    Open the project in the Unity editor")
    console.print("  setup   Install com.unity.pipeline into the project")
    console.print("  check   Run an automated check against the open page")
    console.print("  world   Validate and list `*.world.json` space files")
    console.print()
    console.print("Examples:")
    console.print("  sf unity serve                       # serve and open Chrome")
    console.print("  sf unity cmd sim.pause               # pause the simulation")
    console.print("  sf unity cmd world.load --arg name=gate_course")
    console.print("  sf unity check fly                   # fly ARM -> hold -> land")
    console.print("  sf unity logs --cmd c20260920T044500Z-1a2b3c")
    console.print("  sf unity logs --src fw --level warn --follow")
    console.print()
    console.print("Run 'sf unity <subcommand> --help' for details.")
    return 0


def run_check_help(args: argparse.Namespace) -> int:
    """Show help when `sf unity check` is given no subcommand"""
    console.print("Usage: sf unity check <subcommand>")
    console.print()
    console.print("Subcommands:")
    console.print("  fly   ARM, take off, hold in ALT_HOLD and land, then judge")
    console.print()
    console.print("The page must already be open in Chrome against a running")
    console.print("`sf unity serve`.")
    return 0


def run_world_help(args: argparse.Namespace) -> int:
    """Show help when `sf unity world` is given no subcommand"""
    console.print("Usage: sf unity world <subcommand>")
    console.print()
    console.print("Subcommands:")
    console.print("  validate <file...>   Check world files against the schema")
    console.print("  list                 List the bundled world files")
    return 0
