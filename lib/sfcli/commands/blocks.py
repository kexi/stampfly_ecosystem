"""
sf blocks - Blockly block-programming bridge for StampFly

A local HTTP bridge between a browser-based Blockly UI (drag-and-drop
programming blocks, e.g. "takeoff" -> "forward 50" -> "land") and the
StampFly vehicle's Tello-compatible text API:
  - UDP :8889 — text commands, one datagram per command, blocking reply
    (firmware/vehicle/tasks/api_task.cpp)
  - UDP :8890 — 10Hz Tello-style state string, pushed unicast to whichever
    client last sent something on :8889
    (firmware/vehicle/components/sf_telemetry/include/tello_state.hpp)

ブラウザの Blockly UI（"takeoff"→"forward 50"→"land" のようにブロックを
組んでプログラムする画面）と、StampFly 機体の Tello 互換テキスト API を
つなぐローカル HTTP ブリッジ:
  - UDP :8889 — テキストコマンド。1データグラム=1コマンド、応答はブロッキング
    （firmware/vehicle/tasks/api_task.cpp）
  - UDP :8890 — 10Hz の Tello 風状態文字列。直近 :8889 へ送信したクライアント
    へユニキャストで送られる
    （firmware/vehicle/components/sf_telemetry/include/tello_state.hpp）

Classrooms are OFFLINE while the PC is joined to the drone's WiFi AP, so this
module is stdlib-only (no external dependencies) — same policy as
`sf telemetry --web` / the SILS GUI. HTTP server binds 127.0.0.1 ONLY: this is
a control channel (it can take off / land / cut motors), never a LAN service.

教室は PC がドローンの WiFi AP に接続している間インターネット無しで動く前提
なので、本モジュールは stdlib のみで書く（外部依存ゼロ）— `sf telemetry --web`
や SILS GUI と同じ方針。HTTP サーバは 127.0.0.1 のみに bind する: これは制御
チャネル（離陸・着陸・モータ停止ができる）であり、LAN サービスにしてはならない。

The UDP client is `sfpilot.link.RealLink` (moved out of this module on
2026-09-19 so `sf blocks` and `sf pilot` share one implementation). We do
NOT reuse tools/stampfly_py/stampfly.py — its client is strictly blocking
(one socket, sendto then recvfrom) and cannot support the priority
stop/emergency path (send immediately + abort whatever /api/cmd is currently
waiting); RealLink is a small non-blocking-friendly client instead
(receiver thread + queue.Queue).

UDP クライアントは `sfpilot.link.RealLink`（`sf blocks` と `sf pilot` で
実装を共有するため 2026-09-19 に本モジュールから移設）。
tools/stampfly_py/stampfly.py は使わない — そのクライアントは厳格な
ブロッキング設計（1ソケットで sendto→recvfrom）で、優先 stop/emergency
経路（即時送信＋現在待機中の /api/cmd を中断）を支えられない。RealLink は
その代わりの小さなクライアント（受信スレッド＋queue.Queue）である。
"""

import argparse
import json
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from ..utils import console
from sfpilot.link import (
    API_PORT,
    DEFAULT_DRONE_HOST,
    POLL_INTERVAL_S,
    RealLink,
    STATE_PORT,
    parse_state_line,
)

COMMAND_NAME = "blocks"
COMMAND_HELP = "Blockly block-programming bridge (browser UI <-> drone UDP API)"

# =============================================================================
# Protocol constants — MUST match firmware/vehicle/tasks/api_task.cpp and
# firmware/vehicle/components/sf_telemetry/include/tello_state.hpp.
# プロトコル定数 — ファーム側と一致必須。
# =============================================================================
DEFAULT_HTTP_PORT  = 5007             # this bridge's HTTP port / 本ブリッジの HTTP ポート

CONNECT_TIMEOUT_S = 5.0    # /api/connect handshake ("command" -> "ok") / 接続ハンドシェイク
QUERY_TIMEOUT_S   = 3.0    # queries, speed, stop / クエリ・speed・stop
MOVE_TIMEOUT_S    = 20.0   # takeoff/land/movement/turn / 離着陸・移動・回頭
EMERGENCY_GAP_S   = 0.1    # gap between the two "emergency" sends / emergency 二連送の間隔
SSE_PERIOD_S      = 0.2    # /events push period (contract: ~200ms) / SSE 配信周期

# Command whitelist: verb -> (has_int_arg, min, max, reply_timeout_s).
# A single table instead of scattered literals — the ONLY place command
# grammar and timeouts are defined (no magic numbers elsewhere).
# コマンドのホワイトリスト: verb -> (整数引数を取るか, 最小, 最大, 応答待ちタイムアウト秒)。
# コマンド文法とタイムアウトを定義する唯一のテーブル（他所にマジックナンバーを置かない）。
CMD_TABLE = {
    "takeoff":   (False, None, None, MOVE_TIMEOUT_S),
    "land":      (False, None, None, MOVE_TIMEOUT_S),
    "stop":      (False, None, None, QUERY_TIMEOUT_S),
    "battery?":  (False, None, None, QUERY_TIMEOUT_S),
    "height?":   (False, None, None, QUERY_TIMEOUT_S),
    "attitude?": (False, None, None, QUERY_TIMEOUT_S),
    "speed?":    (False, None, None, QUERY_TIMEOUT_S),
    "tof?":      (False, None, None, QUERY_TIMEOUT_S),
    "up":        (True, 10, 300, MOVE_TIMEOUT_S),
    "down":      (True, 10, 300, MOVE_TIMEOUT_S),
    "left":      (True, 10, 300, MOVE_TIMEOUT_S),
    "right":     (True, 10, 300, MOVE_TIMEOUT_S),
    "forward":   (True, 10, 300, MOVE_TIMEOUT_S),
    "back":      (True, 10, 300, MOVE_TIMEOUT_S),
    "cw":        (True, 1, 360, MOVE_TIMEOUT_S),
    "ccw":       (True, 1, 360, MOVE_TIMEOUT_S),
    "speed":     (True, 10, 100, QUERY_TIMEOUT_S),
}

# Demo-mode simulation constants (kept out of the code body, same rationale
# as CMD_TABLE above). / デモモード用の擬似物理定数。
DEMO_BATTERY_START           = 95.0   # starting battery [%] / 開始時バッテリー
DEMO_BATTERY_FLOOR           = 10.0   # never simulate below this / 下限
DEMO_BATTERY_DRAIN_PER_TICK  = 0.02   # [%] per tick / 1ティックあたりの消費
DEMO_BATTERY_TICK_S          = 1.0    # drain tick period / 消費ティック周期
DEMO_TAKEOFF_LAND_S          = 2.0    # takeoff/land duration / 離着陸所要時間
DEMO_MOVE_MIN_S              = 0.5    # movement floor duration / 移動所要時間の下限
DEMO_MOVE_CM_PER_S           = 50.0   # simulated cruise speed / 移動速度
DEMO_TURN_DEG_PER_S          = 90.0   # simulated turn rate / 回頭速度
DEMO_HOVER_HEIGHT_CM         = 80.0   # takeoff target height / 離陸目標高度
DEMO_DEFAULT_SPEED_CMS       = 30     # speed? fallback / speed? の既定応答


def _validate_command(cmd_line: str):
    """Whitelist check. Returns (normalized_line, timeout_s) or None if rejected.
    ホワイトリスト照合。合格なら (正規化コマンド行, タイムアウト秒)、不合格なら None。"""
    parts = cmd_line.strip().split()
    if not parts:
        return None
    verb = parts[0]
    entry = CMD_TABLE.get(verb)
    if entry is None:
        return None
    has_arg, lo, hi, timeout_s = entry
    if has_arg:
        if len(parts) != 2:
            return None
        try:
            n = int(parts[1])
        except ValueError:
            return None
        if not (lo <= n <= hi):
            return None
        return f"{verb} {n}", timeout_s
    if len(parts) != 1:
        return None
    return verb, timeout_s


# =============================================================================
# RealLink and _parse_state_line moved to lib/sfpilot/link.py (2026-09-19)
# so that `sf blocks` and `sf pilot` drive the vehicle through ONE client
# instead of two copies that could drift apart. Imported above; the names
# below keep this module's existing spelling.
# RealLink と _parse_state_line は lib/sfpilot/link.py へ移設した
# (2026-09-19)。`sf blocks` と `sf pilot` が 2 つの写しに分岐せず、1 つの
# クライアントで機体を操作するため。import は冒頭。下の別名で本モジュール
# 内の既存の呼び名を保つ。
# =============================================================================
_parse_state_line = parse_state_line


# =============================================================================
# DemoLink — same interface as RealLink, no sockets. Lets a classroom PC with
# no drone attached still exercise the whole Blockly UI.
# DemoLink — RealLink と同じインタフェースをソケット無しで提供。機体が無い
# 教室 PC でも Blockly UI 一式を動かせる。
# =============================================================================
class DemoLink:
    def __init__(self):
        self._lock = threading.Lock()          # guards the simulated physical state / 擬似状態の保護
        self._abort_evt = threading.Event()
        self._closed = threading.Event()
        self._battery = DEMO_BATTERY_START
        self._height_cm = 0.0
        self._flying_since: Optional[float] = None
        self._drain_thread = threading.Thread(target=self._drain_loop, daemon=True)
        self._drain_thread.start()

    def handshake(self, timeout: float):
        return True, None   # demo always succeeds / デモは常に成功

    def send(self, cmd_line: str, timeout: float):
        self._abort_evt.clear()
        parts = cmd_line.split()
        verb = parts[0]
        arg = int(parts[1]) if len(parts) > 1 else None
        duration, result = self._simulate(verb, arg)
        # A command whose simulated duration exceeds the caller's timeout
        # times out, mirroring how the real link would behave.
        # 擬似所要時間が呼び出し側のタイムアウトを超える場合はタイムアウトとし、
        # 実機リンクの挙動を模す。
        timed_out = duration > timeout
        deadline = time.monotonic() + min(duration, timeout)
        while True:
            if self._abort_evt.is_set():
                return "aborted", "aborted"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, POLL_INTERVAL_S))
        return ("timeout", "timeout") if timed_out else result

    def _simulate(self, verb: str, arg: Optional[int]):
        """Apply `verb` to the simulated drone state. Returns (duration_s, (status,text)).
        擬似ドローン状態に verb を適用。(所要時間, (status, text)) を返す。"""
        with self._lock:
            if verb == "takeoff":
                self._flying_since = time.monotonic()
                self._height_cm = DEMO_HOVER_HEIGHT_CM
                return DEMO_TAKEOFF_LAND_S, ("ok", "ok")
            if verb == "land":
                self._flying_since = None
                self._height_cm = 0.0
                return DEMO_TAKEOFF_LAND_S, ("ok", "ok")
            if verb == "stop":
                return 0.0, ("ok", "ok")
            if verb == "speed":
                return 0.0, ("ok", "ok")
            if verb in ("up", "down"):
                delta = arg if verb == "up" else -arg
                self._height_cm = max(0.0, self._height_cm + delta)
                return max(DEMO_MOVE_MIN_S, arg / DEMO_MOVE_CM_PER_S), ("ok", "ok")
            if verb in ("left", "right", "forward", "back"):
                return max(DEMO_MOVE_MIN_S, arg / DEMO_MOVE_CM_PER_S), ("ok", "ok")
            if verb in ("cw", "ccw"):
                return arg / DEMO_TURN_DEG_PER_S, ("ok", "ok")
            if verb == "battery?":
                return 0.0, ("ok", f"{int(self._battery)}")
            if verb == "height?":
                return 0.0, ("ok", f"{int(self._height_cm)}")
            if verb == "attitude?":
                return 0.0, ("ok", "pitch:0;roll:0;yaw:0")
            if verb == "speed?":
                return 0.0, ("ok", f"{DEMO_DEFAULT_SPEED_CMS}")
            if verb == "tof?":
                return 0.0, ("ok", f"{int(self._height_cm)}")
        return 0.0, ("error", "error unsupported")   # unreachable: HTTP layer whitelists first

    def priority(self, cmd_line: str) -> None:
        if cmd_line == "emergency":
            with self._lock:
                self._flying_since = None
                self._height_cm = 0.0
        # "stop" has no extra simulated effect beyond unblocking the waiter
        # (handled by abort(), called right after this by the bridge).
        # "stop" は待機解除（abort() が担う。ブリッジがこの直後に呼ぶ）以外の
        # 擬似的な効果を持たない。

    def abort(self) -> None:
        self._abort_evt.set()

    def snapshot_state(self):
        with self._lock:
            flying = self._flying_since is not None
            time_s = int(time.monotonic() - self._flying_since) if flying else 0
            state = {
                "pitch": 0, "roll": 0, "yaw": 0,
                "vgx": 0, "vgy": 0, "vgz": 0,
                "templ": 25, "temph": 30,
                "tof": int(self._height_cm), "h": int(self._height_cm),
                "bat": int(self._battery), "baro": round(self._height_cm, 2),
                "time": time_s,
                "agx": 0.0, "agy": 0.0, "agz": 1.0,
            }
        return state, time.monotonic()

    def _drain_loop(self) -> None:
        """Slow battery drain so the demo dashboard looks alive over a session.
        セッション中もダッシュボードが動いて見えるよう緩やかにバッテリーを消費。"""
        while not self._closed.is_set():
            with self._lock:
                if self._battery > DEMO_BATTERY_FLOOR:
                    self._battery -= DEMO_BATTERY_DRAIN_PER_TICK
            time.sleep(DEMO_BATTERY_TICK_S)

    def close(self) -> None:
        self._closed.set()


# =============================================================================
# _Bridge — the server's single piece of mutable state: which Link (if any)
# is active, connected/busy flags. One instance per `sf blocks` process.
# _Bridge — サーバの可変状態一式: 現在の Link・接続/busy フラグ。
# `sf blocks` プロセスにつき1インスタンス。
# =============================================================================
class _Bridge:
    def __init__(self, default_host: str, demo: bool):
        self.default_host = default_host
        self.demo = demo
        self._lock = threading.Lock()
        self.link = None            # RealLink | DemoLink | None
        self.connected = False
        self.busy = False
        self.host: Optional[str] = None

    def status(self) -> dict:
        with self._lock:
            return {"connected": self.connected, "demo": self.demo,
                    "busy": self.busy, "host": self.host}

    def connect(self, host: Optional[str]):
        """(Re)connect — idempotent, always tears down any prior link first.
        (再)接続 — 冪等。既存リンクは必ず先に破棄する。"""
        host = host or self.default_host
        with self._lock:
            old_link = self.link
            self.link, self.connected, self.host = None, False, None
        if old_link is not None:
            old_link.close()
        try:
            new_link = DemoLink() if self.demo else RealLink(host)
        except OSError as exc:
            return False, str(exc)
        ok, err = new_link.handshake(CONNECT_TIMEOUT_S)
        if not ok:
            new_link.close()
            return False, err or "connect failed"
        with self._lock:
            self.link, self.connected, self.host, self.busy = new_link, True, host, False
        return True, None

    def disconnect(self) -> None:
        with self._lock:
            link, self.link = self.link, None
            self.connected, self.busy, self.host = False, False, None
        if link is not None:
            link.close()

    def run_command(self, cmd_line: str, timeout: float):
        """Returns (http_status, body) — body already matches the /api/cmd contract.
        (HTTP ステータス, レスポンス本体) を返す — 本体は /api/cmd の契約通り。"""
        with self._lock:
            if not self.connected or self.link is None:
                return 409, {"ok": False, "error": "not connected"}
            if self.busy:
                return 409, {"ok": False, "error": "busy"}
            self.busy = True
            link = self.link
        try:
            status, text = link.send(cmd_line, timeout)
        finally:
            with self._lock:
                self.busy = False
        if status == "ok":
            return 200, {"ok": True, "response": text}
        if status in ("timeout", "aborted"):
            return 200, {"ok": False, "error": status}
        return 200, {"ok": False, "error": text}

    def stop(self) -> None:
        with self._lock:
            link = self.link
        if link is not None:
            link.priority("stop")
            link.abort()

    def emergency(self) -> None:
        with self._lock:
            link = self.link
        if link is not None:
            link.priority("emergency")
            link.abort()                    # unblock any waiter ASAP / 待機を即解除
            time.sleep(EMERGENCY_GAP_S)
            link.priority("emergency")

    def state_snapshot(self):
        """Returns (connected, state|None, age_ms|None) for /events.
        /events 用に (接続可否, 状態|None, 経過ms|None) を返す。"""
        with self._lock:
            connected, link = self.connected, self.link
        if link is None:
            return connected, None, None
        state, ts = link.snapshot_state()
        if state is None:
            return connected, None, None
        return connected, state, int(max(0.0, time.monotonic() - ts) * 1000)


_bridge: Optional[_Bridge] = None   # set by serve() / serve() が設定

# Frontend assets — blocks.html and the vendored Blockly library. blocks.html
# is written by a separate agent/PR; until it exists we 404 with a clear
# message rather than crash (Phase 0 backend must stand alone).
# フロントエンド資産 — blocks.html と同梱済み Blockly ライブラリ。blocks.html は
# 別エージェント/PR が作成する。存在しない間はクラッシュせず、明確なメッセージ
# 付きの 404 を返す（Phase 0 バックエンドは単独で成立させる）。
_PAGE_PATH = Path(__file__).resolve().parent.parent / "assets" / "blocks.html"
_VENDOR_DIR = (Path(__file__).resolve().parent.parent / "assets" / "vendor" / "blockly").resolve()
_VENDOR_NAME_RE = re.compile(r"[A-Za-z0-9._/-]+")
_VENDOR_MIME = {".js": "application/javascript", ".css": "text/css", ".txt": "text/plain"}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *fmt_args):      # silence per-request logging
        pass                                    # リクエスト毎ログを抑止

    # -- helpers --------------------------------------------------------
    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return obj if isinstance(obj, dict) else {}

    # -- GET --------------------------------------------------------------
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self._serve_page()
        elif path.startswith("/vendor/blockly/"):
            self._serve_vendor_file(path[len("/vendor/blockly/"):])
        elif path == "/api/status":
            self._send_json(200, _bridge.status())
        elif path == "/events":
            self._serve_events()
        else:
            self.send_error(404)

    def _serve_page(self) -> None:
        if not _PAGE_PATH.exists():
            self.send_error(404, "blocks.html not found — frontend not built yet "
                                  "(blocks.html は未作成です)")
            return
        body = _PAGE_PATH.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_vendor_file(self, rel: str) -> None:
        # Whitelist regex + explicit ".." / leading-"/" rejection: a leading
        # "/" would make Path(base / rel) discard `base` entirely (pathlib
        # treats an absolute second operand as anchoring to root) — reject
        # it here rather than rely on the regex (the charset alone permits
        # sequences like "../"). Path.relative_to() below is the final check.
        # ホワイトリスト正規表現 + ".."・先頭"/" の明示拒否: 先頭が "/" だと
        # Path(base / rel) が base を無視してしまう（pathlib は絶対パスの
        # 第2オペランドをルート起点として扱う）ため、文字集合だけに頼らずここで
        # 拒否する。最終防衛線は下の Path.relative_to()。
        if not rel or ".." in rel or rel.startswith("/") or not _VENDOR_NAME_RE.fullmatch(rel):
            self.send_error(400, "bad vendor path")
            return
        file_path = (_VENDOR_DIR / rel).resolve()
        try:
            file_path.relative_to(_VENDOR_DIR)
        except ValueError:
            self.send_error(400, "bad vendor path")
            return
        if not file_path.is_file():
            self.send_error(404)
            return
        body = file_path.read_bytes()
        content_type = _VENDOR_MIME.get(file_path.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                connected, state, age_ms = _bridge.state_snapshot()
                payload = json.dumps({"connected": connected, "state": state, "age_ms": age_ms})
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
                time.sleep(SSE_PERIOD_S)
        except (BrokenPipeError, ConnectionResetError,
                ConnectionAbortedError):   # ConnectionAborted: Windows / Windows系
            return

    # -- POST -------------------------------------------------------------
    def _origin_allowed(self) -> bool:
        """Reject cross-origin POSTs before dispatching.

        A cross-origin "simple request" (e.g. fetch() with a text/plain
        body, no custom headers) is NOT preflighted by the browser — any
        webpage the user happens to have open could silently POST
        /api/cmd (takeoff!) to this localhost control channel. JSON
        fetches with Content-Type: application/json WOULD be preflighted
        and blocked by CORS, but we can't rely on every caller using that
        header, so we check Origin explicitly here instead.
        クロスオリジン "simple request"（例: text/plain ボディの fetch、
        カスタムヘッダ無し）はブラウザの preflight 対象外 — ユーザーが開いて
        いる無関係な Web ページが、この localhost 制御チャネルの /api/cmd
        （離陸！）へ黙って POST できてしまう。application/json 指定の fetch
        は preflight されて CORS で弾かれるが、全呼び出し元がそのヘッダを
        付ける前提には出来ないため、ここで Origin を直接検証する。

        Non-browser tools (curl, scripts) send no Origin header at all —
        allowed. Our own page's same-origin fetch sends an Origin that
        matches this request's Host header exactly — allowed. Anything
        else is rejected.
        非ブラウザツール（curl・スクリプト）は Origin ヘッダ自体を送らない
        — 許可する。自ページからの同一オリジン fetch は、この Origin が
        リクエストの Host ヘッダと厳密一致する — 許可する。それ以外は拒否。
        """
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return origin == f"http://{self.headers.get('Host')}"

    def do_POST(self):
        if not self._origin_allowed():
            self._send_json(403, {"ok": False, "error": "forbidden origin"})
            return
        path = urlsplit(self.path).path
        if path == "/api/connect":
            ok, err = _bridge.connect(self._body_json().get("host"))
            self._send_json(200, {"ok": True} if ok else {"ok": False, "error": err})
        elif path == "/api/disconnect":
            _bridge.disconnect()
            self._send_json(200, {"ok": True})
        elif path == "/api/cmd":
            self._handle_cmd(self._body_json().get("cmd"))
        elif path == "/api/stop":
            _bridge.stop()
            self._send_json(200, {"ok": True})
        elif path == "/api/emergency":
            _bridge.emergency()
            self._send_json(200, {"ok": True})
        else:
            self.send_error(404)

    def _handle_cmd(self, cmd_line) -> None:
        parsed = _validate_command(cmd_line) if isinstance(cmd_line, str) else None
        if parsed is None:
            self._send_json(400, {"ok": False, "error": "unsupported command"})
            return
        normalized, timeout_s = parsed
        status, body = _bridge.run_command(normalized, timeout_s)
        self._send_json(status, body)


def serve(http_port: int, host: str, demo: bool, open_browser: bool) -> int:
    """Run the bridge server (blocks until Ctrl-C).
    ブリッジサーバを起動（Ctrl-C まで実行）。"""
    global _bridge
    _bridge = _Bridge(default_host=host, demo=demo)

    # 127.0.0.1 ONLY — this is a control channel (can fly/land/cut motors),
    # never a LAN-exposed service. / 制御チャネルのため 127.0.0.1 限定。
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", http_port), _Handler)
    except OSError:
        # Most common cause: another `sf blocks` (or anything else) already
        # bound this port. Fail with a clear, actionable message instead of
        # a raw traceback.
        # 主な原因: 別の `sf blocks`（または他プロセス）が既にこのポートを
        # bind 済み。生のトレースバックではなく、対処法つきの明確な
        # メッセージで失敗させる。
        console.error(
            f"Port {http_port} is already in use — is another sf blocks "
            f"running? (--port for a different port) / ポート {http_port} "
            f"は使用中です — 別の sf blocks が起動していませんか？"
            f"（--port で変更可）"
        )
        return 1
    url = f"http://127.0.0.1:{http_port}/"
    mode = "DEMO (no drone)" if demo else f"REAL (drone @ {host})"
    console.info(f"Blockly bridge: {url}  [{mode}]")
    console.info("Ctrl-C to stop")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
        console.info("Stopped")
    finally:
        _bridge.disconnect()
        httpd.server_close()
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register command with CLI"""
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help=COMMAND_HELP,
        description=__doc__,
    )
    parser.add_argument(
        "--host", default=DEFAULT_DRONE_HOST,
        help=f"Drone address (default: {DEFAULT_DRONE_HOST})",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_HTTP_PORT,
        help=f"HTTP port for the bridge/UI (default: {DEFAULT_HTTP_PORT})",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Demo mode — fake drone, no UDP (classroom PC with no hardware attached)",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="Do not auto-open the browser",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    return serve(http_port=args.port, host=args.host, demo=args.demo,
                 open_browser=not args.no_browser)
