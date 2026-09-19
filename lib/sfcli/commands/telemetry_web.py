"""
sf telemetry --web — browser telemetry view (UDP -> SSE proxy)

Receives the vehicle 50Hz monitoring telemetry (UDP broadcast :5005,
104B v1 or 140B v2 binary packet — decoder shared with `sf telemetry`) and serves a
single-page browser dashboard. Zero external dependencies, matching the SILS
GUI policy: a stdlib ThreadingHTTPServer serves the embedded page and pushes
live JSON over Server-Sent Events (`/events`) — SSE is the stdlib-friendly
equivalent of the WebSocket proxy named in requirements §7 (one-way push is
all a monitor needs).

vehicle の 50Hz モニタ用テレメトリ（UDP ブロードキャスト :5005、104B の v1 または
140B の v2 バイナリ — デコーダは `sf telemetry` と共有）を受信し、ブラウザ用の
シングルページダッシュボードを提供する。SILS GUI と同じ「外部依存ゼロ」方針:
stdlib の ThreadingHTTPServer が埋め込みページを配信し、Server-Sent Events
（`/events`）でライブ JSON をプッシュする — SSE は requirements §7 の
WebSocket プロキシの stdlib 等価（モニタに必要なのは一方向プッシュのみ）。
"""

import json
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import telemetry as telem
from . import web_assets
from ..utils import console

# Latest decoded packet + arrival bookkeeping, shared between the UDP thread
# and the HTTP handler threads (GIL-atomic reference swap; no lock needed).
# 最新デコード済みパケット＋到着情報。UDP スレッドと HTTP ハンドラ間で共有
# （参照の差し替えは GIL でアトミック。ロック不要）。
_latest = {"pkt": None, "rx_monotonic": 0.0, "count": 0, "rate_hz": 0.0}


def _udp_listener(port: int, csv_path=None) -> None:
    """Background thread: UDP :port -> _latest (+ optional CSV).
    背景スレッド: UDP受信→_latest更新（＋任意で CSV 記録）"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Allow `sf telemetry` and `sf monitor web` to listen simultaneously:
    # broadcast reception by multiple processes needs SO_REUSEPORT on
    # macOS/Linux (Windows: SO_REUSEADDR alone suffices; the attr is absent).
    # `sf telemetry` と `sf monitor web` の同時リッスンを許可: 複数プロセスでの
    # ブロードキャスト受信は macOS/Linux では SO_REUSEPORT が必要
    # （Windows は SO_REUSEADDR のみで足り、属性自体が存在しない）。
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("", port))
    csv_file = None
    if csv_path:
        csv_file = open(csv_path, "a", buffering=1)
        if csv_file.tell() == 0:
            csv_file.write(telem.CSV_HEADER)
    window = []
    while True:
        data, _addr = sock.recvfrom(2048)
        pkt = telem.decode_packet(data)
        if pkt is None:
            continue
        now = time.monotonic()
        window.append(now)
        while window and now - window[0] > 2.0:
            window.pop(0)
        _latest["pkt"] = pkt
        _latest["rx_monotonic"] = now
        _latest["count"] += 1
        _latest["rate_hz"] = len(window) / 2.0
        if csv_file:
            csv_file.write(telem.csv_row(pkt))


# The dashboard page lives as a sibling asset; the 3D view it mounts, the STL
# body parts and three.js are all served by `web_assets`, shared with
# `sf pilot --web` (see that module for the no-CDN rule).
# ダッシュボードページは隣接アセット。そこに載せる 3D 表示・STL 本体パーツ・
# three.js はいずれも `web_assets` が配信し、`sf pilot --web` と共有する
#（CDN を使わない規則は同モジュール参照）。
_PAGE_PATH = web_assets.ASSET_DIR / "telemetry_web.html"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *fmt_args):          # silence per-request logging
        pass                                        # リクエスト毎ログを抑止

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            web_assets.send_page(self, _PAGE_PATH)
            return
        if web_assets.serve(self, self.path):
            return
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            # Push the latest packet at 10Hz until the browser disconnects.
            # ブラウザ切断まで最新パケットを 10Hz でプッシュ。
            try:
                while True:
                    payload = json.dumps({
                        "pkt": _latest["pkt"],
                        "count": _latest["count"],
                        "rate_hz": _latest["rate_hz"],
                        "age_s": (time.monotonic() - _latest["rx_monotonic"])
                                 if _latest["pkt"] else None,
                    })
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError,
                    ConnectionAbortedError):   # ConnectionAborted: Windows / Windows系
                return
            return
        self.send_error(404)


def serve(http_port: int, telemetry_port: int, open_browser: bool,
          csv_path=None) -> int:
    """Run the UDP->SSE proxy + page server (blocks until Ctrl-C).
    UDP→SSE プロキシ＋ページサーバを起動（Ctrl-C まで実行）。"""
    threading.Thread(target=_udp_listener, args=(telemetry_port, csv_path),
                     daemon=True).start()
    httpd = ThreadingHTTPServer(("", http_port), _Handler)
    url = f"http://localhost:{http_port}/"
    console.info(f"Telemetry web view: {url}  (UDP :{telemetry_port} -> SSE)")
    console.info("Ctrl-C to stop")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
        console.info("Stopped")
    finally:
        httpd.server_close()
    return 0
