"""
sf pilot ... --web — watch the autopilot fly, and watch it decide.

Serves one page on 127.0.0.1 that shows the aircraft in 3D (the same view
`sf telemetry --web` uses, from the shared `stampfly3d.js`) beside the
decisions as they are made. Both halves come over one Server-Sent Events
stream fed by `sfpilot.events.EventBus`, which is the same point the trace
file is written from -- the page and `logs/pilot/*.jsonl` cannot disagree.

sf pilot ... --web — 自動操縦の飛行と、その判断を見る。

127.0.0.1 にページを 1 つ出し、機体を 3D で（`sf telemetry --web` と同じ表示。
共有の `stampfly3d.js` を使う）、その隣に判断を起きた順に表示する。どちらも
`sfpilot.events.EventBus` が供給する 1 本の Server-Sent Events で届く。記録
ファイルを書くのと同じ地点なので、ページと `logs/pilot/*.jsonl` が食い違う
ことはない。

The flight must not wait for the browser. The monitor loop keeps 50Hz on
its own thread while this server runs on another; a viewer that cannot
keep up drops events (EventBus's rule), and no viewer at all costs
nothing. Binding 127.0.0.1 only is deliberate: a flight in progress is
not something to put on the network by default.

飛行がブラウザを待ってはならない。監視ループは自分のスレッドで 50Hz を保ち、
本サーバは別のスレッドで動く。追随できない閲覧者は出来事を失い（EventBus の
規則）、閲覧者が 0 でも費用はかからない。127.0.0.1 だけに bind するのは意図的
である — 進行中の飛行を、既定で網の上に出すべきではない。
"""

import json
import queue
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import web_assets
from ..utils import console

# Only the loopback address. See this module's docstring.
# ループバックのみ。理由は本モジュールの冒頭参照。
BIND_HOST = "127.0.0.1"

# How many events one viewer may fall behind before the oldest are dropped.
# Roughly a second of samples at the rate they are published, which is far
# more slack than a browser on the same machine ever needs.
# 1 人の閲覧者が、古いものを捨てられるまでに何件遅れてよいか。公開周期での
# 約 1 秒分にあたり、同一機上のブラウザに必要な余裕をはるかに上回る。
VIEWER_QUEUE_MAX = 240

# How long a viewer's stream waits for the next event before writing a
# keep-alive comment. Without it a silent flight (nothing decided, no
# samples) would look to the browser like a dropped connection.
# 閲覧者の流れが、次の出来事を待つ時間。これを過ぎたら生存確認の注釈を書く。
# 無いと、静かな飛行（判断も標本も無い）が切断に見えてしまう。
KEEPALIVE_S = 10.0

_PAGE_PATH = web_assets.ASSET_DIR / "pilot_web.html"


class _Handler(BaseHTTPRequestHandler):
    """Serves the page, the shared 3D assets, and the event stream.
    ページ・共有 3D 資材・出来事の流れを配信する。"""

    # Set by `PilotWebServer` before the server starts.
    # `PilotWebServer` がサーバ起動前に設定する。
    bus = None
    context = None

    def log_message(self, fmt, *fmt_args):      # silence per-request logging
        pass                                    # リクエスト毎ログを抑止

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            web_assets.send_page(self, _PAGE_PATH)
            return
        if web_assets.serve(self, self.path):
            return
        if self.path == "/context":
            self._send_json(self.context or {})
            return
        if self.path == "/events":
            self._stream_events()
            return
        self.send_error(404)

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream_events(self) -> None:
        """Push this viewer's events until the browser goes away.
        ブラウザが去るまで、この閲覧者の出来事を送り続ける。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        sink = queue.Queue(maxsize=VIEWER_QUEUE_MAX)
        self.bus.subscribe(sink)
        try:
            while True:
                try:
                    event = sink.get(timeout=KEEPALIVE_S)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                payload = json.dumps(event, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError,
                ConnectionAbortedError, OSError):
            # The browser closed the tab, or the flight ended under it.
            # Either way this viewer is gone; the flight is unaffected.
            # ブラウザが閉じられたか、飛行が先に終わった。いずれにせよこの
            # 閲覧者は去った。飛行には影響しない。
            return
        finally:
            self.bus.unsubscribe(sink)


class PilotWebServer:
    """The live view's HTTP server, on its own thread.

    Started before the flight and stopped after it, so the page is already
    open when the aircraft lifts off and the port is free the moment the
    command returns.

    ライブ表示の HTTP サーバ。専用のスレッドで動く。

    飛行の前に開始し、後に停止する。機体が浮くときには既にページが開いており、
    コマンドが戻った時点でポートは解放されている。
    """

    def __init__(self, bus, port: int, open_browser: bool = True,
                 context: dict = None):
        self.bus = bus
        self.port = port
        self.open_browser = open_browser
        self.context = context or {}
        self._httpd = None
        self._thread = None

    @property
    def url(self) -> str:
        return f"http://{BIND_HOST}:{self.port}/"

    def start(self) -> "PilotWebServer":
        """Bind, serve on a background thread, and open a browser.
        bind し、背景スレッドで配信し、ブラウザを開く。"""
        handler = type("_BoundHandler", (_Handler,),
                       {"bus": self.bus, "context": self.context})
        self._httpd = ThreadingHTTPServer((BIND_HOST, self.port), handler)
        # Daemon thread: an open browser tab must never keep `sf pilot` alive
        # after the flight has ended and the summary has printed.
        # daemon スレッドにする。開いたままのタブが、飛行終了・集計表示の後も
        # `sf pilot` を生かし続けてはならない。
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        kwargs={"poll_interval": 0.2},
                                        daemon=True)
        self._thread.start()
        console.info(f"Live view: {self.url}  (Jev の判断と機体を表示)")
        if self.open_browser:
            webbrowser.open(self.url)
        return self

    def stop(self) -> None:
        """Shut the server down and release the port. Safe to call twice.
        サーバを止め、ポートを解放する。2 回呼んでも安全。"""
        if self._httpd is None:
            return
        self._httpd.shutdown()
        # `server_close` releases the listening socket; without it a second
        # `sf pilot --web` on the same port fails while this process lives.
        # `server_close` が待ち受けソケットを解放する。これが無いと、同じ
        # ポートでの次の `sf pilot --web` が本プロセスの生存中は失敗する。
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()


class LiveStatus:
    """The running totals the page shows along its top.

    Kept here rather than computed in the browser because they are the
    same figures the end-of-flight summary prints: one definition, shown
    live and again at the end.

    ページ上部に表示する現在値。

    ブラウザ側で数えずここに置く。飛行終了時の集計が表示するのと同じ値で
    あり、定義を 1 つにして、ライブと終了時の両方で見せるためである。
    """

    def __init__(self, bus):
        self.bus = bus
        self.decisions = 0
        self.overdue = 0
        self.latencies: list = []
        self.phase = "on the ground"
        self.step = ""
        self._last_published = 0.0

    def note_decision(self, row: dict) -> None:
        """Fold one decision into the totals. / 判断 1 件を現在値に取り込む。"""
        self.decisions += 1
        latency = row.get("latency_ms")
        if latency is not None:
            self.latencies.append(latency)
        answers = row.get("answers") or {}
        if answers.get("error") == "deadline exceeded":
            self.overdue += 1

    def set_phase(self, phase: str, step: str = "") -> None:
        self.phase = phase
        self.step = step
        self.publish()

    def publish(self) -> None:
        """Send the current totals to the page. / 現在値をページへ送る。"""
        from sfpilot.events import EVENT_STATUS

        self.bus.publish(EVENT_STATUS, {
            "phase": self.phase,
            "step": self.step,
            "decisions": self.decisions,
            "overdue": self.overdue,
            "latency_p50_ms": _percentile(self.latencies, 50),
            "latency_p95_ms": _percentile(self.latencies, 95),
        })

    def publish_throttled(self, now: float, period_s: float = 0.5) -> None:
        """Publish at most every `period_s`, for use inside the 50Hz loop.
        `period_s` に 1 回までに抑えて送る。50Hz ループ内から呼ぶためのもの。"""
        if now - self._last_published < period_s:
            return
        self._last_published = now
        self.publish()


def _percentile(values: list, percent: float):
    """Nearest-rank percentile, or None when nothing has been measured.
    最近傍順位の百分位数。計測がなければ None。"""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int(round(percent / 100.0 * len(ordered))))
    return round(ordered[min(rank, len(ordered)) - 1], 1)


def page_path() -> Path:
    """Where the page lives (for the tests). / ページの場所（試験用）。"""
    return _PAGE_PATH
