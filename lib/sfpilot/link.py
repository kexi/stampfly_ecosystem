"""
sfpilot.link - the one place that talks to a vehicle (real, or recorded).
sfpilot.link - 機体（実機・記録）と通信する唯一の場所。

Three implementations share one `Link` interface so the layers above
(Monitor / Executor) never learn where samples come from:

  - `RealLink`   — the actual vehicle over UDP :8889 (commands) and
                   :8890 (10Hz state string). Moved here from
                   `sfcli/commands/blocks.py`, which now imports it:
                   `sf blocks` and `sf pilot` must not drift apart.
  - `ReplayLink` — a recorded flight (`lib/sflog` bundle) played back in
                   time order. Sends are recorded, never transmitted.
  - (SilsLink)   — NOT implemented. SILS needs a firmware-side change
                   (an `api <line>` stdin verb); that is P2 in
                   `docs/plans/jev-autopilot.md`. No placeholder class is
                   defined here: an importable stub that raises would let
                   `--sils` be typed and fail late, which is worse than
                   the option simply not existing.

3 つの実装が 1 つの `Link` インターフェースを共有し、上の層（Monitor /
Executor）はサンプルの出所を知らずに済む:

  - `RealLink`   — 実機と UDP :8889（コマンド）/ :8890（10Hz 状態文字列）で通信。
                   `sfcli/commands/blocks.py` から移設し、blocks 側は本モジュールを
                   import する。`sf blocks` と `sf pilot` の実装を分岐させないため。
  - `ReplayLink` — 記録済みの飛行（`lib/sflog` の一式）を時刻順に流す。送信は
                   記録するだけで、実際には送らない。
  - （SilsLink） — **未実装**。SILS 連携にはファーム側の変更（stdin の
                   `api <行>`）が必要で、これは計画文書の P2。例外を出すだけの
                   代替実装も置かない。import できてしまうと `--sils` を打てて
                   遅れて失敗することになり、選択肢が無いほうがまだ良いため。
"""

import queue
import socket
import threading
import time
from typing import Optional, Protocol, runtime_checkable

# Protocol constants — MUST match firmware/vehicle/tasks/api_task.cpp and
# firmware/vehicle/components/sf_telemetry/include/tello_state.hpp.
# プロトコル定数 — ファーム側と一致必須。
DEFAULT_DRONE_HOST = "192.168.10.1"   # StampFly AP-mode address / AP モード時の機体アドレス
API_PORT = 8889                       # ApiTask command port / コマンドポート
STATE_PORT = 8890                     # TelloStateTask state port / 状態ストリームポート
POLL_INTERVAL_S = 0.05                # reply/abort polling granularity / 待機ポーリング粒度


# =============================================================================
# Sample — one moment of the flight, in SI units, as the Monitor wants it.
# Sample — 飛行のある瞬間を SI 単位で表したもの。Monitor が受け取る形。
# =============================================================================
class Sample(dict):
    """One telemetry sample: a plain dict with documented keys.

    Keys (all optional except `t`; a missing key means "not measured in
    this source", which the Monitor reports as unknown rather than zero):
      t            monotonic-ish timestamp [s]
      altitude_m   height above the takeoff point [m], positive up
      pos_n, pos_e horizontal position from the takeoff point [m]
      vel_n, vel_e, vel_d  velocity [m/s], NED
      roll, pitch, yaw     attitude [rad]
      battery_pct  battery remaining [%]
      tof_m        ground distance sensor [m]
      flight_state firmware FlightState name, e.g. "FLYING"

    1 サンプル: キーを文書化した素の dict。`t` 以外はすべて省略可能で、
    キーが無いことは「この入力源では測っていない」を意味する。Monitor は
    それを 0 ではなく「不明」として扱う。

    Why a dict subclass and not a dataclass: sources have different
    subsets of fields (UDP:5005 has no battery; the 10Hz state string has
    no velocity), and `in` / `.get()` express "absent" directly, where a
    dataclass would need a sentinel per field.
    なぜ dataclass ではなく dict の派生か: 入力源ごとに持つ項目が違う
    （UDP:5005 に電池は無く、10Hz 状態文字列に速度は無い）。`in` と `.get()`
    なら「無い」をそのまま表せるが、dataclass では項目ごとに番人の値が要る。
    """


@runtime_checkable
class Link(Protocol):
    """What every source of samples and sink of commands must provide.
    サンプルの入力源・指令の出力先が満たすべきインターフェース。"""

    def read_samples(self) -> list:
        """Return every Sample that arrived since the last call (possibly
        empty). Must not block longer than one monitor period.
        前回呼び出し以降に届いた Sample を全て返す（空もあり得る）。
        監視周期を超えてブロックしてはならない。"""
        ...

    def send_rc(self, a: int, b: int, c: int, d: int) -> None:
        """Send one `rc a b c d` velocity command. Fire-and-forget: this
        is called at 20Hz and must never wait for a reply.
        `rc a b c d` を 1 回送る。20Hz で呼ばれるため応答を待ってはならない。"""
        ...

    def send_command(self, line: str) -> None:
        """Send one text command (`land`, `takeoff`, ...) without waiting.
        テキスト指令（`land`・`takeoff` 等）を応答待ちせずに送る。"""
        ...

    def priority(self, line: str) -> None:
        """Send immediately, bypassing any busy check (`stop`).
        busy 判定を無視して即時送信する（`stop` 用）。"""
        ...

    def close(self) -> None:
        """Release sockets/files. Must be safe to call twice.
        ソケット・ファイルを解放する。2 回呼んでも安全であること。"""
        ...


# =============================================================================
# _parse_state_line — Tello state string -> {key: number}.
# Tello 状態文字列 -> {key: 数値}。
# =============================================================================
def parse_state_line(text: str) -> dict:
    """Parse "k:v;k:v;...\\r\\n" into numeric fields only (contract: {key:number}).
    Non-numeric fields (e.g. "mpry:0,0,0") are dropped, not stringified.
    "k:v;k:v;..." を数値フィールドのみに整形（契約: {key:数値}）。数値化できない
    フィールド（例 "mpry:0,0,0"）は文字列化せず捨てる。"""
    state = {}
    for field_text in text.strip().split(";"):
        if ":" not in field_text:
            continue
        key, _, raw = field_text.partition(":")
        key, raw = key.strip(), raw.strip()
        if not key or not raw:
            continue
        try:
            state[key] = int(raw)
        except ValueError:
            try:
                state[key] = float(raw)
            except ValueError:
                continue
    return state


# =============================================================================
# RealLink — talks to the actual vehicle over UDP :8889 (commands) / :8890
# (state). Two sockets, two receiver threads.
#
# Moved verbatim from sfcli/commands/blocks.py (2026-09-19) so that
# `sf blocks` and `sf pilot` share one client. We do NOT reuse
# tools/stampfly_py/stampfly.py — its client is strictly blocking (one
# socket, sendto then recvfrom) and cannot support the priority
# stop/emergency path (send immediately + abort whatever is waiting).
#
# RealLink — 実機と UDP :8889（コマンド）/ :8890（状態）で通信。
# ソケット 2 つ・受信スレッド 2 つ。
#
# `sf blocks` と `sf pilot` でクライアントを共有するため、2026-09-19 に
# sfcli/commands/blocks.py からそのまま移設した。tools/stampfly_py/stampfly.py は
# 使わない — そのクライアントは厳格なブロッキング設計（1 ソケットで
# sendto→recvfrom）で、優先 stop/emergency 経路（即時送信＋待機中の中断）を
# 支えられない。
# =============================================================================
class RealLink:
    def __init__(self, host: str):
        self.host = host
        # Command socket: bound to an ephemeral local port. The firmware
        # replies to whatever (ip, port) the command datagram arrived FROM
        # (api_task.cpp: g_client = from), so no explicit local port is
        # required — matching tools/stampfly_py/stampfly.py's approach.
        # コマンドソケット: ローカルはエフェメラルポートで bind。ファームは
        # コマンド datagram の送信元 (ip, port) へ返信する（api_task.cpp の
        # g_client = from）ため、ローカルポートを明示指定する必要はない
        # — stampfly.py と同じ考え方。
        self._cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._cmd_sock.bind(("", 0))

        # State socket: the firmware's TelloStateTask sends unicast directly
        # to <client-ip>:8890 (its OWN ephemeral send socket, no local bind
        # needed on ITS side) — so on our side we must bind :8890 to receive
        # it (confirmed in api_task.cpp TelloStateTask, ~line 1300-1310).
        #
        # Deliberately NO SO_REUSEADDR here: UDP:8890 is contended on the PC
        # side (djitellopy scripts, another `sf blocks` session, etc. all
        # want it). With SO_REUSEADDR a second binder would silently steal
        # or split the 10Hz state stream — undefined which process receives
        # each packet — instead of failing loudly. We want the OSError.
        # 状態ソケット: ファームの TelloStateTask は自前のエフェメラル送信
        # ソケットから <client-ip>:8890 へユニキャスト送信する（送信側の bind は
        # 不要）— よってこちら側は :8890 を bind して受信する必要がある
        # （api_task.cpp TelloStateTask, 1300〜1310行付近で確認）。
        #
        # あえて SO_REUSEADDR は付けない: UDP:8890 は PC 側で競合しやすい
        # （djitellopy スクリプト・別の `sf blocks` セッション等が同じポートを
        # 欲しがる）。SO_REUSEADDR を付けると、2つ目の bind が 10Hz 状態
        # ストリームを黙って奪う／分け合ってしまう（どちらが受信するか不定）
        # — 静かに壊れるより、OSError で明確に失敗させたい。
        self._state_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._state_sock.bind(("", STATE_PORT))
        except OSError:
            # Don't leak the already-created command socket on this path.
            # このパスで既に作った command ソケットを漏らさない。
            self._state_sock.close()
            self._cmd_sock.close()
            raise OSError(
                f"UDP:{STATE_PORT} is already in use — close other djitellopy / "
                f"sf blocks sessions (UDP:{STATE_PORT} が使用中です — 他の "
                f"djitellopy / sf blocks を終了してください)"
            )

        self._reply_q: "queue.Queue[str]" = queue.Queue()
        self._send_lock = threading.Lock()     # serializes socket writes / 送信の直列化
        self._abort_evt = threading.Event()
        self._closed = threading.Event()
        self._latest_state: Optional[dict] = None
        self._latest_state_ts = 0.0

        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._state_thread = threading.Thread(target=self._state_loop, daemon=True)
        self._rx_thread.start()
        self._state_thread.start()

    def _rx_loop(self) -> None:
        """Push every command-socket reply into the queue. / 全応答をキューへ積む。"""
        while not self._closed.is_set():
            try:
                data, _addr = self._cmd_sock.recvfrom(1024)
            except OSError:
                return   # socket closed by close() / close() によるソケット破棄
            self._reply_q.put(data.decode(errors="replace").strip())

    def _state_loop(self) -> None:
        """Decode every state-socket packet into _latest_state. / 状態パケットをデコード。"""
        while not self._closed.is_set():
            try:
                data, _addr = self._state_sock.recvfrom(2048)
            except OSError:
                return
            self._latest_state = parse_state_line(data.decode(errors="replace"))
            self._latest_state_ts = time.monotonic()

    def handshake(self, timeout: float):
        """Enter SDK mode ("command"). Returns (ok, error|None).
        SDK モードへ移行（"command"）。(成功可否, エラー|None) を返す。"""
        status, text = self.send("command", timeout)
        return (True, None) if status == "ok" else (False, text)

    def send(self, cmd_line: str, timeout: float):
        """Send one command, wait for its reply. Returns (status, text) where
        status in {"ok","error","timeout","aborted"}.
        1コマンド送信→応答待ち。status は {"ok","error","timeout","aborted"} のいずれか。"""
        # Flush stale replies (e.g. a late reply from a previous timed-out
        # command) so we never pair this command with someone else's answer.
        # 古い応答（前回タイムアウトしたコマンドの遅延応答等）を捨て、
        # 今回のコマンドに他コマンドの応答が紛れ込まないようにする。
        while True:
            try:
                self._reply_q.get_nowait()
            except queue.Empty:
                break
        self._abort_evt.clear()
        with self._send_lock:
            try:
                self._cmd_sock.sendto(cmd_line.encode(), (self.host, API_PORT))
            except OSError as exc:
                return "error", f"send failed: {exc}"

        deadline = time.monotonic() + timeout
        while True:
            if self._abort_evt.is_set():
                return "aborted", "aborted"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout", "timeout"
            try:
                reply = self._reply_q.get(timeout=min(remaining, POLL_INTERVAL_S))
            except queue.Empty:
                continue
            return ("error", reply) if reply.startswith("error") else ("ok", reply)

    def priority(self, cmd_line: str) -> None:
        """Send immediately, bypassing any busy check (stop/emergency).
        busy チェックを無視して即時送信（stop/emergency 用）。"""
        with self._send_lock:
            try:
                self._cmd_sock.sendto(cmd_line.encode(), (self.host, API_PORT))
            except OSError:
                pass   # best-effort — priority sends must not raise / 失敗しても例外化しない

    def abort(self) -> None:
        """Make the in-flight send() (if any) return ("aborted", ...).
        待機中の send() があれば ("aborted", ...) で返させる。"""
        self._abort_evt.set()

    def snapshot_state(self):
        return self._latest_state, self._latest_state_ts

    # -- Link interface additions (sf pilot) / Link インターフェースの追加分 ----

    def send_rc(self, a: int, b: int, c: int, d: int) -> None:
        """Fire-and-forget velocity command. Uses priority() rather than
        send(): `rc` is the one command the firmware does not acknowledge,
        and at 20Hz we must never wait for a reply that will not come.
        応答待ちしない速度指令。send() ではなく priority() を使う: `rc` は
        ファームが応答を返さない唯一の指令であり、20Hz で来ない応答を
        待ってはならないため。"""
        self.priority(f"rc {a} {b} {c} {d}")

    def send_command(self, line: str) -> None:
        """Send a text command without waiting for its reply. The pilot
        loop must keep monitoring while the vehicle lands, so it cannot
        use the blocking send().
        応答待ちせずテキスト指令を送る。着陸中も監視を続ける必要があるため、
        ブロッキングの send() は使えない。"""
        self.priority(line)

    def read_samples(self) -> list:
        """Return the newest 10Hz state as at most one Sample.

        RealLink's UDP:5005 50Hz reader is P2 work (it belongs with SILS
        so both paths are tested together); until then the pilot reads the
        10Hz state string, which RealLink already receives for `sf blocks`.
        最新の 10Hz 状態を最大 1 件の Sample として返す。

        UDP:5005 の 50Hz 受信は P2 の作業（SILS と同時に整備して両経路を
        まとめて検証するため）。それまでは `sf blocks` 用に既に受信している
        10Hz 状態文字列を使う。
        """
        state, ts = self.snapshot_state()
        if state is None:
            return []
        return [_sample_from_state_dict(state, ts)]

    def close(self) -> None:
        self._closed.set()
        for sock in (self._cmd_sock, self._state_sock):
            try:
                sock.close()
            except OSError:
                pass


def _sample_from_state_dict(state: dict, ts: float) -> Sample:
    """Tello state fields -> Sample in SI units.
    Tello 状態のフィールドを SI 単位の Sample に変換。

    Why the conversions: the state string reports height and ToF in cm and
    attitude in degrees (tello_state.hpp), while every threshold in
    config.py is in metres and radians. Converting here keeps the unit
    change in one place instead of at each comparison.
    変換する理由: 状態文字列は高度・ToF を cm、姿勢を度で送る
    （tello_state.hpp）が、config.py のしきい値はメートルとラジアン。
    ここで変換し、比較のたびに単位を変えないようにする。
    """
    import math

    sample = Sample(t=ts)
    if "h" in state:
        sample["altitude_m"] = state["h"] / 100.0
    if "tof" in state:
        sample["tof_m"] = state["tof"] / 100.0
    if "bat" in state:
        sample["battery_pct"] = float(state["bat"])
    for state_key, sample_key in (("roll", "roll"), ("pitch", "pitch"), ("yaw", "yaw")):
        if state_key in state:
            sample[sample_key] = math.radians(state[state_key])
    for state_key, sample_key in (("vgx", "vel_n"), ("vgy", "vel_e"), ("vgz", "vel_d")):
        if state_key in state:
            # The state string reports velocity in dm/s (Tello convention).
            # 状態文字列の速度は dm/s（Tello の慣習）。
            sample[sample_key] = state[state_key] / 10.0
    return sample


# =============================================================================
# ReplayLink — plays a recorded flight back in time order. Sends are
# recorded, never transmitted: replay exists to check the judging layers
# against a real flight without anything moving.
# ReplayLink — 記録済みの飛行を時刻順に流す。送信は記録するだけで実際には
# 送らない。何も動かさずに判断層を実際の飛行で確認するための仕組み。
# =============================================================================
class ReplayLink:
    """Feed Samples from a `lib/sflog` flight-log bundle.

    Time is driven by the caller, not by the wall clock: each
    `read_samples()` returns the samples whose timestamps fall in the next
    step. Replaying faster than real time is the point -- a 90-second
    flight should be judged in a second or two.

    `lib/sflog` のフライトログ一式から Sample を供給する。

    時間は実時計ではなく呼び出し側が進める: `read_samples()` は次の 1 歩に
    入るサンプルを返す。実時間より速く再生できることに意味がある（90 秒の
    飛行を 1〜2 秒で判断させたい）。
    """

    def __init__(self, path, step_s: float = 1.0 / 50.0):
        self.path = str(path)
        self.step_s = step_s
        self.sent: list = []          # every command, in order / 送信指令を順に記録
        self._samples = _load_samples(path)
        self._index = 0
        self._clock = self._samples[0]["t"] if self._samples else 0.0

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def read_samples(self) -> list:
        """Return the samples belonging to the next step, advancing the
        replay clock. An empty list means the log is exhausted.
        次の 1 歩に属するサンプルを返し、再生時刻を進める。空リストは
        ログを流し終えたことを意味する。"""
        if self._index >= len(self._samples):
            return []
        self._clock += self.step_s
        out = []
        while self._index < len(self._samples) and self._samples[self._index]["t"] <= self._clock:
            out.append(self._samples[self._index])
            self._index += 1
        # A step that lands between samples would return nothing forever
        # if the log's period is longer than step_s; take one sample so
        # replay always makes progress.
        # ログの周期が step_s より長い場合、サンプルの谷間に落ちた 1 歩は
        # 永遠に空を返す。再生が必ず進むよう、その場合は 1 件取り出す。
        if not out and self._index < len(self._samples):
            out.append(self._samples[self._index])
            self._clock = self._samples[self._index]["t"]
            self._index += 1
        return out

    def send_rc(self, a: int, b: int, c: int, d: int) -> None:
        self.sent.append(f"rc {a} {b} {c} {d}")

    def send_command(self, line: str) -> None:
        self.sent.append(line)

    def priority(self, line: str) -> None:
        self.sent.append(line)

    def close(self) -> None:
        self._samples = []


def _load_samples(path) -> list:
    """Read a flight-log bundle into time-ordered Samples.

    Only the streams the Monitor needs are read. `posvel` carries position
    and velocity; `status` carries the battery; `tof_b` carries the ground
    distance. They are sampled at different rates, so each is merged into
    the nearest earlier position sample rather than interpolated -- the
    Monitor classifies into bands, where sub-sample precision is noise.

    フライトログ一式を時刻順の Sample として読む。

    Monitor が使うストリームだけを読む。`posvel` に位置・速度、`status` に
    電池、`tof_b` に対地距離がある。周期が異なるため、補間はせず直前の位置
    サンプルへ併合する — Monitor は区分に落とすので、サンプル未満の精度は
    意味を持たない。
    """
    import sflog

    log = sflog.load(path)
    streams = log.streams
    if "posvel" not in streams:
        raise ValueError(
            f"{path}: no 'posvel' stream — cannot replay a log without position "
            f"('posvel' ストリームが無いため再生できません)"
        )

    samples = []
    posvel = streams["posvel"]
    for row in posvel.to_dict("records"):
        t = _seconds(row)
        sample = Sample(t=t)
        # The log stores position down-positive (NED); altitude is up.
        # ログの位置は下向き正（NED）。高度は上向き。
        _copy_if(row, sample, "pos_x", "pos_n")
        _copy_if(row, sample, "pos_y", "pos_e")
        if "pos_z" in row:
            sample["altitude_m"] = -float(row["pos_z"])
        _copy_if(row, sample, "vel_x", "vel_n")
        _copy_if(row, sample, "vel_y", "vel_e")
        _copy_if(row, sample, "vel_z", "vel_d")
        samples.append(sample)

    # Stream and column names follow protocol/spec/flight_log.yaml (via
    # lib/sflog/schema.py), not the telemetry packet's names.
    # ストリーム名・列名は protocol/spec/flight_log.yaml（lib/sflog/schema.py
    # 経由）に従う。テレメトリパケット側の名前とは異なる。
    _merge_stream(samples, streams.get("status"),
                  {"voltage": "battery_v", "flight_state": "flight_state_code"})
    _merge_stream(samples, streams.get("tof_bottom"), {"distance": "tof_m"})
    _merge_attitude(samples, streams.get("attitude"))
    _add_battery_percent(samples)
    samples.sort(key=lambda s: s["t"])
    return samples


# Battery: the log records pack voltage, but every threshold in config.py
# is a percentage. A 1S LiPo runs from about 4.2 V charged to 3.3 V empty;
# the mapping is linear between those, which is crude but monotonic --
# enough to place a reading in a band, which is all the Monitor needs.
# 電池: ログはパック電圧を記録するが、config.py のしきい値は百分率。
# 1S LiPo は満充電約 4.2 V、空で約 3.3 V。その間を線形に対応させる。
# 粗いが単調であり、区分に入れるには足りる（Monitor が必要とするのはそれだけ）。
BATTERY_FULL_V = 4.2
BATTERY_EMPTY_V = 3.3


def _add_battery_percent(samples: list) -> None:
    """Derive a battery percentage from the logged pack voltage.
    記録されたパック電圧から電池残量の百分率を導く。"""
    span = BATTERY_FULL_V - BATTERY_EMPTY_V
    for sample in samples:
        voltage = sample.get("battery_v")
        if voltage is None:
            continue
        ratio = (voltage - BATTERY_EMPTY_V) / span
        sample["battery_pct"] = max(0.0, min(100.0, ratio * 100.0))


def _merge_attitude(samples: list, frame) -> None:
    """Convert the logged quaternion to roll/pitch/yaw on each sample.
    記録された四元数を各サンプルの roll/pitch/yaw に変換する。

    The log stores attitude as a quaternion (`attitude` stream); the
    Monitor classifies tilt in Euler angles, so the conversion happens
    once here rather than in the classifier.
    ログは姿勢を四元数で持つ（`attitude` ストリーム）。Monitor は傾きを
    オイラー角で区分するため、変換は分類側ではなくここで 1 度だけ行う。
    """
    import math

    if frame is None or not samples:
        return
    rows = sorted(frame.to_dict("records"), key=_seconds)
    if not rows:
        return
    row_index = 0
    carried: dict = {}
    for sample in samples:
        while row_index < len(rows) and _seconds(rows[row_index]) <= sample["t"]:
            row = rows[row_index]
            row_index += 1
            quaternion = [row.get(k) for k in ("quat_w", "quat_x", "quat_y", "quat_z")]
            if any(v is None for v in quaternion):
                continue
            w, x, y, z = (float(v) for v in quaternion)
            carried = {
                "roll": math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)),
                "pitch": math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x)))),
                "yaw": math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)),
            }
        sample.update(carried)


# Timestamp column -> seconds. `timestamp_us` is what flight_log v1
# actually writes (protocol/spec/flight_log.yaml); the others are
# accepted so a hand-made or converted frame also replays.
# 時刻列 -> 秒。フライトログ v1 が実際に書くのは `timestamp_us`
# （protocol/spec/flight_log.yaml）。手作り・変換済みのデータも再生できる
# よう、他の綴りも受け付ける。
_TIME_COLUMNS = (
    ("timestamp_us", 1e-6),
    ("t_us", 1e-6),
    ("timestamp_ms", 1e-3),
    ("t_ms", 1e-3),
    ("t", 1.0),
)


def _seconds(row: dict) -> float:
    """Row timestamp in seconds. Logs store microseconds since boot.
    行の時刻を秒で返す。ログは起動からのマイクロ秒で記録している。

    Raises rather than defaulting to 0: a frame whose time column we do
    not recognise would otherwise collapse to a single instant, and the
    whole log would be consumed in one read with no error shown.
    0 を既定にせず例外にする。時刻列を判別できないデータを 0 にすると、
    全行が同一時刻に潰れ、エラーも出ないまま 1 回の読み出しでログを
    読み切ってしまうため。
    """
    for key, scale in _TIME_COLUMNS:
        if key in row:
            return float(row[key]) * scale
    raise ValueError(
        f"no known timestamp column in {sorted(row)[:8]} — expected one of "
        f"{[k for k, _ in _TIME_COLUMNS]}（時刻列が判別できません）"
    )


def _copy_if(row: dict, sample: Sample, row_key: str, sample_key: str) -> None:
    if row_key in row:
        sample[sample_key] = float(row[row_key])


def _merge_stream(samples: list, frame, mapping: dict) -> None:
    """Carry a slower stream's fields onto the position samples.
    低速ストリームの項目を位置サンプルへ持ち込む。"""
    if frame is None or not samples:
        return
    rows = sorted(frame.to_dict("records"), key=_seconds)
    if not rows:
        return
    row_index = 0
    carried: dict = {}
    for sample in samples:
        while row_index < len(rows) and _seconds(rows[row_index]) <= sample["t"]:
            row = rows[row_index]
            for row_key, sample_key in mapping.items():
                if row_key in row:
                    carried[sample_key] = float(row[row_key])
            row_index += 1
        sample.update(carried)
