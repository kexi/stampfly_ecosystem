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
  - `SilsLink`   — a real-time SILS run (emu_vehicle). Samples come from
                   the emulator's `STATE k=v` stdout line; commands go to
                   its stdin as `api <line>` (P2). SILS has no network, so
                   the pipe replaces both UDP ports.

3 つの実装が 1 つの `Link` インターフェースを共有し、上の層（Monitor /
Executor）はサンプルの出所を知らずに済む:

  - `RealLink`   — 実機と UDP :8889（コマンド）/ :8890（10Hz 状態文字列）で通信。
                   `sfcli/commands/blocks.py` から移設し、blocks 側は本モジュールを
                   import する。`sf blocks` と `sf pilot` の実装を分岐させないため。
  - `ReplayLink` — 記録済みの飛行（`lib/sflog` の一式）を時刻順に流す。送信は
                   記録するだけで、実際には送らない。
  - `SilsLink`   — 実時間の SILS 実行（emu_vehicle）。サンプルはエミュレータの
                   `STATE k=v` 行から、指令は stdin へ `api <行>` として送る（P2）。
                   SILS にネットワークは無く、パイプが 2 つの UDP ポートを兼ねる。
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
TELEM_PORT = 5005                     # 50Hz telemetry port / 50Hz テレメトリポート
POLL_INTERVAL_S = 0.05                # reply/abort polling granularity / 待機ポーリング粒度

# Battery: both the flight log and the telemetry packet carry pack voltage,
# but every threshold in config.py is a percentage. A 1S LiPo runs from about
# 4.2 V charged to 3.3 V empty; the mapping is linear between those, which is
# crude but monotonic -- enough to place a reading in a band, which is all the
# Monitor needs.
#
# The SAME two constants are hard-coded in simulator/sils/emu/emu_main.cpp
# (`kBatteryFullV` / `kBatteryEmptyV`), which computes the `batt=` field of
# the SILS STATE line. They are deliberately kept equal so a SILS run and a
# replayed log put the same voltage in the same band; the two cannot share
# code across the C++/Python boundary.
#
# 電池: フライトログもテレメトリパケットもパック電圧を持つが、config.py の
# しきい値は百分率。1S LiPo は満充電約 4.2 V、空で約 3.3 V。その間を線形に
# 対応させる。粗いが単調であり、区分に入れるには足りる（Monitor が必要と
# するのはそれだけ）。
#
# 同じ 2 つの定数が simulator/sils/emu/emu_main.cpp にも書かれている
#（`kBatteryFullV` / `kBatteryEmptyV`。SILS の STATE 行の `batt=` を計算する）。
# SILS の実行と再生したログが同じ電圧を同じ区分に置くよう、意図的に等しく
# 保っている。C++ と Python の境界を越えてコードは共有できないためである。
BATTERY_FULL_V = 4.2
BATTERY_EMPTY_V = 3.3


def battery_percent(voltage: float) -> float:
    """Pack voltage [V] -> remaining percentage, clamped to 0..100.
    パック電圧 [V] → 残量の百分率（0〜100 に丸める）。"""
    ratio = (voltage - BATTERY_EMPTY_V) / (BATTERY_FULL_V - BATTERY_EMPTY_V)
    return max(0.0, min(100.0, ratio * 100.0))


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
    subsets of fields (the 10Hz state string has no velocity; UDP:5005
    carries the battery only from the 140-byte v2 packet, so older firmware
    omits it), and `in` / `.get()` express "absent" directly, where a
    dataclass would need a sentinel per field.
    なぜ dataclass ではなく dict の派生か: 入力源ごとに持つ項目が違う
    （10Hz 状態文字列に速度は無く、UDP:5005 の電池は 140バイトの v2 パケットに
    しか無いので旧ファームでは欠ける）。`in` と `.get()` なら「無い」をそのまま
    表せるが、dataclass では項目ごとに番人の値が要る。
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

        # Telemetry socket (UDP:5005, 50Hz). Bound separately from :8890 and
        # allowed to fail: `sf blocks` never needed it, and firmware older
        # than the v2 packet still flies -- losing it costs the fast sample
        # rate, not the flight, so read_samples() falls back to the 10Hz
        # state string rather than refusing to start.
        # テレメトリソケット（UDP:5005、50Hz）。:8890 とは別に bind し、失敗して
        # よいものとする。`sf blocks` は使っておらず、v2 パケット以前のファームでも
        # 飛行はできる — 失うのは速いサンプル周期であって飛行ではないため、
        # read_samples() は起動を拒まず 10Hz 状態文字列へ退く。
        self._telem_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._telem_sock.bind(("", TELEM_PORT))
        except OSError:
            self._telem_sock.close()
            self._telem_sock = None

        self._reply_q: "queue.Queue[str]" = queue.Queue()
        self._send_lock = threading.Lock()     # serializes socket writes / 送信の直列化
        self._abort_evt = threading.Event()
        self._closed = threading.Event()
        self._latest_state: Optional[dict] = None
        self._latest_state_ts = 0.0
        # Every telemetry sample since the last read_samples(), so the Monitor
        # sees the whole 50Hz stream rather than only the newest packet: the
        # trend rules need the intervening samples to measure a duration.
        # 前回の read_samples() 以降のテレメトリを全て溜める。Monitor が最新の
        # 1 件ではなく 50Hz の流れ全体を見られるようにするためである。傾向の
        # 判定は、継続時間を測るために間のサンプルを必要とする。
        self._telem_q: "queue.Queue[Sample]" = queue.Queue()

        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._state_thread = threading.Thread(target=self._state_loop, daemon=True)
        self._rx_thread.start()
        self._state_thread.start()
        if self._telem_sock is not None:
            threading.Thread(target=self._telem_loop, daemon=True).start()

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

    def _telem_loop(self) -> None:
        """Decode every UDP:5005 packet into a Sample on the queue.
        UDP:5005 の各パケットを Sample にしてキューへ積む。"""
        from sfcli.commands.telemetry import decode_packet

        while not self._closed.is_set():
            try:
                data, _addr = self._telem_sock.recvfrom(2048)
            except OSError:
                return
            packet = decode_packet(data)
            if packet is None:
                continue   # not a telemetry packet / テレメトリではない
            self._telem_q.put(_sample_from_packet(packet, time.monotonic()))

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
        """Return every 50Hz telemetry Sample that arrived since the last
        call, falling back to the newest 10Hz state when :5005 is silent.

        Two sources, one preference order. UDP:5005 is the primary: it runs
        at the Monitor's own rate and, from the 140-byte v2 packet, carries
        the battery and the downward ToF directly. A 104-byte v1 packet has
        neither, so those two fields are filled in from the 10Hz state
        string this link already receives -- that is why the fallback is a
        merge and not a replacement.

        前回の呼び出し以降に届いた 50Hz テレメトリを全て返す。:5005 が無音の
        ときは最新の 10Hz 状態へ退く。

        入力源は 2 つ、優先順位は 1 つ。主となるのは UDP:5005 である。Monitor と
        同じ周期で届き、140 バイトの v2 パケットなら電池と下向き ToF をそのまま
        載せている。104 バイトの v1 パケットはどちらも持たないため、この 2 項目は
        本リンクが既に受けている 10Hz 状態文字列から補う — 退避が置き換えではなく
        併合なのはそのためである。
        """
        samples = []
        while True:
            try:
                samples.append(self._telem_q.get_nowait())
            except queue.Empty:
                break

        state, ts = self.snapshot_state()
        if not samples:
            # Nothing on :5005 (older firmware, or the port was taken).
            # :5005 に何も来ていない（旧ファーム、またはポートが取られている）。
            return [] if state is None else [_sample_from_state_dict(state, ts)]

        # Judged on the newest sample alone, because one batch is never a
        # mix of versions: the firmware does not change packet format
        # mid-flight. Each sample is still filled in individually below
        # (`if key not in sample`), so even if that ever stopped holding,
        # a v2 sample keeps its own battery rather than being overwritten.
        # 判定には最新の 1 件だけを使う。1 回分のまとまりに版が混在することは
        # 無いためである（ファームは飛行中にパケット様式を変えない）。それでも
        # 補完は下で 1 件ずつ行うので（`if key not in sample`）、仮にその前提が
        # 崩れても、v2 のサンプルは自分の電池の値を上書きされない。
        has_v1_gap = state is not None and (
            "battery_pct" not in samples[-1] or "tof_m" not in samples[-1]
        )
        if has_v1_gap:
            supplement = _sample_from_state_dict(state, ts)
            for sample in samples:
                for key in ("battery_pct", "tof_m"):
                    if key not in sample and key in supplement:
                        sample[key] = supplement[key]
        return samples

    def close(self) -> None:
        self._closed.set()
        for sock in (self._cmd_sock, self._state_sock, self._telem_sock):
            if sock is None:
                continue
            try:
                sock.close()
            except OSError:
                pass


# FlightState enum order, mirrored from sfcli.commands.telemetry.STATE_NAMES
# (itself mirrored from the firmware). Imported at call time rather than
# copied, so this cannot drift from the decoder it belongs to.
# FlightState の列挙順は sfcli.commands.telemetry.STATE_NAMES（ファーム側の
# 写し）を使う。複製せず呼び出し時に import し、復号器と食い違わないようにする。
def _sample_from_packet(packet: dict, ts: float) -> Sample:
    """One decoded UDP:5005 packet -> Sample in SI units.
    復号済みの UDP:5005 パケット 1 件を SI 単位の Sample に変換する。

    The packet is already in SI (metres, radians, NED), so unlike the 10Hz
    state string this is a rename rather than a conversion. The one piece
    of arithmetic is the battery percentage, which the packet does not
    carry -- it sends voltage, and every threshold is a percentage.
    パケットは既に SI 単位（メートル・ラジアン・NED）なので、10Hz 状態文字列と
    違いここでの作業は変換ではなく名前の付け替えである。唯一の計算は電池残量の
    百分率で、パケットは電圧しか持たず、しきい値はすべて百分率だからである。
    """
    from sfcli.commands.telemetry import STATE_NAMES

    sample = Sample(t=ts)
    # Position is NED (down-positive); altitude is up-positive.
    # 位置は NED（下向き正）、高度は上向き正。
    if packet.get("pos_z") is not None:
        sample["altitude_m"] = -float(packet["pos_z"])
    for packet_key, sample_key in (
        ("pos_x", "pos_n"), ("pos_y", "pos_e"),
        ("vel_x", "vel_n"), ("vel_y", "vel_e"), ("vel_z", "vel_d"),
        ("roll", "roll"), ("pitch", "pitch"), ("yaw", "yaw"),
    ):
        if packet.get(packet_key) is not None:
            sample[sample_key] = float(packet[packet_key])

    mode = packet.get("mode")
    if mode is not None and 0 <= mode < len(STATE_NAMES):
        sample["flight_state"] = STATE_NAMES[mode]

    # v2 fields. Validity is carried by the flag bits alone -- never inferred
    # from the value, because a live sensor may report a negative reading on
    # error (telemetry.py's decode_packet documents this).
    # v2 の項目。有効性はフラグビットだけが持ち、値からは推測しない。実センサは
    # 異常時に負値を返しうるためである（telemetry.py の decode_packet に明記）。
    voltage = packet.get("voltage")
    if voltage is not None and packet.get("power_valid"):
        sample["battery_v"] = float(voltage)
        sample["battery_pct"] = battery_percent(float(voltage))
    tof = packet.get("tof_bottom")
    if tof is not None and packet.get("tof_bottom_valid"):
        sample["tof_m"] = float(tof)
    return sample


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


# =============================================================================
# SilsLink — a real-time SILS run (emu_vehicle), driven over its stdin/stdout.
# SilsLink — 実時間の SILS 実行（emu_vehicle）を stdin/stdout で駆動する。
# =============================================================================

# Prefix of the emulator's HUD line (simulator/sils/emu/emu_main.cpp). Chosen
# there precisely so a reader can pick it out of the mixed firmware log with a
# startswith() check.
# エミュレータの HUD 行の接頭辞（emu_main.cpp）。混在するファームログから
# startswith() だけで拾えるよう、あちらで選ばれたものである。
STATE_LINE_PREFIX = "STATE "

# `api rc a b c d` is the API's velocity command (-100..100), NOT the
# transmitter's sticks (`rc`, raw ADC 0..4095). Mixing the two verbs up would
# silently send stick values into a velocity command, so both spellings are
# named here rather than formatted inline.
# `api rc a b c d` は API の速度指令（-100..100）であり、送信機のスティック
#（`rc`、ADC 生値 0..4095）ではない。取り違えるとスティック値が速度指令として
# 送られてしまうため、その場で組み立てず両方の綴りをここで名前にしておく。
API_PREFIX = "api "

# How the firmware logs an answer to a command (`reply()` in api_task.cpp).
# Counting these is how a caller knows a blocking move has been reached.
# ファームが指令への応答を記録する形（api_task.cpp の `reply()`）。これを数える
# ことで、ブロックする移動の到達を呼び出し側が知る。
REPLY_MARKER = "reply: "


class SilsLink:
    """Fly a real-time SILS emulator through its stdin/stdout pipes.

    SILS has no network: there is no UDP:5005 to receive and no UDP:8889 to
    send to. The emulator instead prints a `STATE k=v ...` line at ~30Hz and
    accepts line commands on stdin, so this link reads samples from the
    former and writes `api <line>` to the latter. Everything above the Link
    interface -- Monitor, Arbiter, Executor -- is unchanged by that.

    実時間の SILS エミュレータを stdin/stdout のパイプ越しに飛ばす。

    SILS にネットワークは無い（受信する UDP:5005 も、送信先の UDP:8889 も無い）。
    代わりにエミュレータが ~30Hz で `STATE k=v ...` 行を出力し、stdin で行指令を
    受け付ける。本リンクは前者からサンプルを読み、後者へ `api <行>` を書く。
    Link インターフェースより上（Monitor・Arbiter・Executor）はこれに影響されない。

    Why the transmitter sticks are re-sent as neutral and not left alone:
    the emulator's rc_stdin re-injects the last stick values at 50Hz anyway,
    and a *parked* stick does not cancel API guidance (INV-2 cancels on stick
    MOVEMENT). Writing neutral explicitly documents the configuration this
    link flies in -- a safety pilot holding a centred transmitter, the same
    one scenarios/api_flight.scn uses.

    送信機のスティックを放置せず中立で送り続ける理由: エミュレータの rc_stdin は
    どのみち最後のスティック値を 50Hz で再注入し、「置いたままの」スティックは
    API 誘導を解除しない（INV-2 が解除するのはスティックの「動き」）。中立を明示
    して書くことで、本リンクが飛ばす構成 — 安全要員が中立の送信機を保持している
    状態、scenarios/api_flight.scn と同じ — を文書として残す。
    """

    # Neutral transmitter sticks (raw ADC, centre 2048) — scenario_inject.hpp's
    # kAdcCentre, the same scale *.scn `rc` events use.
    # 中立の送信機スティック（ADC 生値、中央 2048）— scenario_inject.hpp の
    # kAdcCentre。*.scn の `rc` 事象と同じスケール。
    STICK_CENTRE = 2048

    def __init__(self, proc, stick_hz: float = 50.0):
        self.proc = proc
        self.sent: list = []            # every command, in order / 送信指令を順に記録
        self._samples: "queue.Queue[Sample]" = queue.Queue()
        self._log_tail: list = []
        self._closed = threading.Event()
        self._write_lock = threading.Lock()
        self._stick_period_s = 1.0 / stick_hz
        self._next_stick = 0.0
        # Counts the vehicle's `reply:` lines. A blocking verb (`forward`,
        # `up`, `go`) answers only once the move is REACHED (api_task.cpp
        # cmdMove), so a caller that waits for this count to advance waits
        # exactly as long as the move takes, instead of guessing a duration.
        # 機体の `reply:` 行を数える。ブロックする verb（`forward`・`up`・`go`）は
        # 移動の**到達後**にはじめて応答する（api_task.cpp の cmdMove）ため、この
        # 値の増加を待つ側は、移動に要する時間だけをちょうど待てる。所要時間を
        # 推測する必要がない。
        self._replies = 0
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # -- reading / 読み取り ---------------------------------------------

    def _read_loop(self) -> None:
        """Split the emulator's stdout into STATE samples and a log tail.
        エミュレータの stdout を STATE サンプルとログ末尾に振り分ける。"""
        for raw_line in self.proc.stdout:
            if self._closed.is_set():
                return
            line = raw_line.rstrip("\n")
            if line.startswith(STATE_LINE_PREFIX):
                sample = sample_from_state_line(line, time.monotonic())
                if sample is not None:
                    self._samples.put(sample)
                continue
            if REPLY_MARKER in line:
                self._replies += 1
            # Bounded: a long run must not grow this without limit, and only
            # the tail is ever shown (after an unexpected exit).
            # 有界にする。長時間の実行で無制限に増やさない。表示するのは末尾
            # だけである（想定外の終了時）。
            self._log_tail.append(line)
            if len(self._log_tail) > 200:
                del self._log_tail[0]

    @property
    def reply_count(self) -> int:
        """How many commands the vehicle has answered so far.

        Counted rather than matched to a specific command: the caller sends
        one blocking verb at a time and waits for the count to move, which
        needs no parsing of the reply text and cannot be confused by a
        reply whose wording changes.

        機体がこれまでに応答した指令の数。

        特定の指令に対応付けず数えるだけにする。呼び出し側はブロックする verb を
        1 つずつ送り、この値が動くのを待つので、応答文の解釈は要らず、文言が
        変わっても壊れない。
        """
        return self._replies

    def read_samples(self) -> list:
        """Every STATE sample since the last call. / 前回以降の STATE サンプル全件。"""
        out = []
        while True:
            try:
                out.append(self._samples.get_nowait())
            except queue.Empty:
                return out

    @property
    def log_tail(self) -> list:
        """The emulator's recent non-STATE output, for a post-mortem.
        エミュレータの直近の非 STATE 出力（事後診断用）。"""
        return list(self._log_tail)

    # -- writing / 書き込み ----------------------------------------------

    def _write(self, line: str) -> None:
        """Write one stdin line; a broken pipe ends the flight, not the process.
        stdin へ 1 行書く。パイプが切れても例外にはせず、飛行の終わりとして扱う。"""
        with self._write_lock:
            try:
                self.proc.stdin.write(line + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                self._closed.set()

    def send_rc(self, a: int, b: int, c: int, d: int) -> None:
        """Send one velocity command as `api rc a b c d`.
        速度指令を `api rc a b c d` として 1 回送る。"""
        self.send_command(f"rc {a} {b} {c} {d}")

    def send_command(self, line: str) -> None:
        """Send one API command line (`command`, `takeoff`, `land`, ...).
        API のコマンド行を 1 行送る（`command`・`takeoff`・`land` 等）。"""
        self.sent.append(line)
        self._write(API_PREFIX + line)
        self.hold_sticks_neutral()

    def priority(self, line: str) -> None:
        """Same path as send_command: the pipe has no reply to overtake.
        send_command と同じ経路。パイプには追い越すべき応答待ちが無い。"""
        self.send_command(line)

    def hold_sticks_neutral(self) -> None:
        """Re-send neutral transmitter sticks, at most at the stick rate.

        Called from the command path and from the pilot loop so the
        emulator keeps seeing a parked, centred transmitter for the whole
        flight (see the class docstring for why that does not cancel API
        guidance). Rate-limited because this is called at 50Hz but the
        emulator re-injects on its own anyway.

        中立の送信機スティックを再送する（送信レートを上限とする）。

        指令の経路と操縦ループの両方から呼ぶ。エミュレータが飛行中ずっと
        「置いたままの中立の送信機」を見続けるようにするためである（それが
        API 誘導を解除しない理由はクラスの docstring 参照）。50Hz で呼ばれる
        が、エミュレータ側も自前で再注入するのでレート制限をかけている。
        """
        now = time.monotonic()
        if now < self._next_stick:
            return
        self._next_stick = now + self._stick_period_s
        centre = self.STICK_CENTRE
        self._write(f"rc {centre} {centre} {centre} {centre}")

    def set_battery_voltage(self, volts: float) -> None:
        """Override the pack voltage the emulator reports (SILS only).

        Not part of the Link interface: no real vehicle can be told what
        its own battery reads. It exists for `--scene battery_drop`, which
        rehearses a low-battery decision without waiting for a real
        discharge (simulator/sils/devices/virtual_board.hpp).

        エミュレータが報告するパック電圧を上書きする（SILS 専用）。

        Link インターフェースには含めない。実機に「自分の電池はこう読め」とは
        言えないからである。`--scene battery_drop` のためのもので、実際の放電を
        待たずに電池低下の判断を予行する（virtual_board.hpp）。
        """
        self._write(f"vbatt {volts:.3f}")

    def set_wind(self, north_n: float, east_n: float, down_n: float) -> None:
        """Apply a sustained external force in NED [N] (SILS only).

        Like set_battery_voltage, this is not part of the Link interface:
        there is no wind knob on a real flight. It drives `--scene drift`
        through the Plant's existing wind hook, the same one a *.scn
        `wind` event uses.

        NED の定常外乱力 [N] をかける（SILS 専用）。

        set_battery_voltage と同じく Link インターフェースには含めない。実際の
        飛行に風のつまみは無いからである。`--scene drift` を、*.scn の `wind`
        事象と同じ Plant の既存フック経由で駆動する。
        """
        self._write(f"wind {north_n:.4f} {east_n:.4f} {down_n:.4f}")

    def takeoff(self) -> None:
        """Enter SDK mode, then take off. Two lines, in this order.

        `command` must precede everything: api_task.cpp gates every other
        verb behind SDK mode, so a `takeoff` sent first is refused.
        SDK モードへ入ってから離陸する。この順で 2 行。

        `command` が全てに先立つ。api_task.cpp は他の全ての指令を SDK モードの
        後ろに置いているため、先に `takeoff` を送っても受け付けられない。
        """
        self.send_command("command")
        self.send_command("takeoff")

    def close(self) -> None:
        """Ask the emulator to shut down, and stop reading. Safe twice.
        エミュレータに終了を求め、読み取りを止める。2 回呼んでも安全。"""
        if not self._closed.is_set():
            self._write("quit")
        self._closed.set()
        try:
            self.proc.stdin.close()
        except (OSError, ValueError, BrokenPipeError):
            pass


def sample_from_state_line(line: str, ts: float) -> Optional[Sample]:
    """One emulator `STATE k=v ...` line -> Sample in SI units.

    Parsed as unordered `k=v` tokens and by name only, never by position:
    emu_main.cpp appends new keys to the tail of this line, and a
    positional parse would break the next time it does. An unknown key is
    ignored for the same reason.

    エミュレータの `STATE k=v ...` 行 1 行を SI 単位の Sample にする。

    順序に依らない `k=v` の並びとして、名前だけで解釈する（位置では解釈しない）。
    emu_main.cpp はこの行の末尾に項目を追記していくため、位置で読むと次の追記で
    壊れる。未知の項目を無視するのも同じ理由である。
    """
    import math

    fields = {}
    for token in line.split()[1:]:   # skip the "STATE" token / 先頭の "STATE" を飛ばす
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        fields[key] = value

    sample = Sample(t=ts)
    # Attitude is printed in degrees; every threshold in config.py is radians.
    # 姿勢は度で出力される。config.py のしきい値はすべてラジアン。
    for key, sample_key in (("roll", "roll"), ("pitch", "pitch"), ("yaw", "yaw")):
        value = _state_float(fields, key)
        if value is not None:
            sample[sample_key] = math.radians(value)
    for key, sample_key in (
        ("alt", "altitude_m"), ("x", "pos_n"), ("y", "pos_e"),
        ("vx", "vel_n"), ("vy", "vel_e"), ("vz", "vel_d"),
        ("batt", "battery_pct"), ("vbatt", "battery_v"),
    ):
        value = _state_float(fields, key)
        if value is not None:
            sample[sample_key] = value

    # ToF only when the emulator says it is valid. Validity is a separate
    # field precisely so the value is never second-guessed (the line prints
    # -1.0 when invalid, which is a plausible-looking number).
    # ToF はエミュレータが有効と言ったときだけ採る。値を推測しないよう有効性を
    # 別項目にしてある（無効時は -1.0 を出力し、これは値として通ってしまう）。
    is_tof_valid = _state_float(fields, "tof_valid") == 1.0
    if is_tof_valid:
        tof = _state_float(fields, "tof")
        if tof is not None:
            sample["tof_m"] = tof

    # "FLYING:POS_HOLD*" -> "FLYING". The trailing "*" marks armed and the
    # part after ":" is the sub-mode; the Monitor classifies on the state.
    # 「FLYING:POS_HOLD*」→「FLYING」。末尾の「*」は ARM 済み、「:」より後ろは
    # 副モード。Monitor が区分に使うのは状態のほうである。
    mode = fields.get("mode")
    if mode:
        sample["flight_state"] = mode.split(":")[0].rstrip("*")

    is_empty = len(sample) <= 1   # only `t` / `t` しか無い
    return None if is_empty else sample


def _state_float(fields: dict, key: str):
    """Field as a float, or None when absent or not a number.
    項目を float で返す。無い場合・数値でない場合は None。"""
    raw = fields.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


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


def _add_battery_percent(samples: list) -> None:
    """Derive a battery percentage from the logged pack voltage.
    記録されたパック電圧から電池残量の百分率を導く。"""
    for sample in samples:
        voltage = sample.get("battery_v")
        if voltage is None:
            continue
        sample["battery_pct"] = battery_percent(voltage)


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
