"""
sf telemetry - Live 50Hz telemetry monitor (vehicle)

Receives the vehicle monitoring telemetry (binary packet, magic 0xCAFE,
UDP broadcast :5005, 50Hz) and shows a live terminal dashboard. Optionally
records to CSV. For graphs/offline analysis use the Data Stream instead:
`sf log wifi` -> `sf log viz` (400Hz, full sensors).

Two wire versions are accepted, told apart by TOTAL LENGTH: 104 bytes (v1)
and 140 bytes (v2, which appends battery voltage, ToF, optical flow,
magnetometer and pressure altitude). v2 is a pure append, so the first 104
bytes are identical in both and old firmware keeps working.

vehicle のモニタ用テレメトリ（バイナリ、magic 0xCAFE、UDP ブロードキャスト
:5005、50Hz）を受信し、ターミナルにライブ表示します。--csv で記録も可能。
グラフ・オフライン解析には Data Stream（`sf log wifi` → `sf log viz`、
400Hz・全センサ）を使ってください。

電文は2版あり「全長」で判別します: 104バイト（v1）と 140バイト（v2。電池電圧・
ToF・オプティカルフロー・地磁気・気圧高度を追記）。v2 は純粋な追記なので先頭
104バイトは両版で同一で、旧ファームもそのまま動きます。
"""

import argparse
import math
import os
import socket
import struct
import sys
import time
from ..utils import console

COMMAND_NAME = "telemetry"
COMMAND_HELP = "Live 50Hz telemetry — terminal dashboard, or browser with --web"

# Wire format — MUST match firmware/vehicle/components/sf_telemetry/
# include/telemetry.hpp TelemetryPacket (static_assert 140 bytes + offsetof
# assertions). The authoritative description is detailed_design.md §10;
# test_telemetry.py checks these formats against that offset table.
# 電文形式 — ファームの TelemetryPacket（140B static_assert ＋ offsetof 検査）と
# 一致必須。様式の基準は detailed_design.md §10。test_telemetry.py が本書式を
# 同じオフセット表と突き合わせる。
TELEM_PORT = 5005
TELEM_MAGIC = 0xCAFE
TELEM_FMT = "<HBBI23fB3x"   # magic, version, type, t_us, 23 floats, mode, pad
TELEM_SIZE = struct.calcsize(TELEM_FMT)

# v2 appended block (offsets 104..139), decoded separately so the v1 prefix
# parse stays byte-identical between versions.
# v2 追記部（オフセット 104〜139）。v1 部の解釈を両版で完全に同一に保つため別に復号する。
TELEM_V2_FMT = "<3f2hBB2x4f"   # voltage, tof_b, tof_f, dx, dy, squal, flags, pad, mag*3, baro
TELEM_V2_SIZE = struct.calcsize(TELEM_V2_FMT)
TELEM_SIZE_V2 = TELEM_SIZE + TELEM_V2_SIZE
TELEM_VERSION_V2 = 2

# Bit positions in valid_flags — mirror of TELEM_VALID_* in telemetry.hpp.
# valid_flags のビット位置 — telemetry.hpp の TELEM_VALID_* と対応。
VALID_BITS = {
    "tof_bottom_valid": 1 << 0,
    "tof_front_valid": 1 << 1,
    "flow_valid": 1 << 2,
    "mag_valid": 1 << 3,
    "baro_valid": 1 << 4,
    "power_valid": 1 << 5,
}

FLOAT_NAMES = [
    "roll", "pitch", "yaw",
    "gyro_x", "gyro_y", "gyro_z",
    "accel_x", "accel_y", "accel_z",
    "pos_x", "pos_y", "pos_z",
    "vel_x", "vel_y", "vel_z",
    "thrust", "tau_roll", "tau_pitch", "tau_yaw",
    "m1", "m2", "m3", "m4",
]

# v2 field names in CSV/display order. Absent (None) when a v1 packet arrives.
# CSV・表示順の v2 項目名。v1 パケットでは存在しない（None）。
V2_NAMES = [
    "voltage", "tof_bottom", "tof_front",
    "flow_dx_sum", "flow_dy_sum", "flow_squal",
    "mag_x", "mag_y", "mag_z", "baro_altitude",
]
V2_FLAG_NAMES = list(VALID_BITS)

# FlightState enum order (firmware/vehicle/components/sf_state flight_state.hpp)
# FlightState の列挙順（ファーム側と一致必須）
STATE_NAMES = ["INIT", "IDLE_GROUND", "IDLE_HELD", "ARMED_GROUND",
               "TAKEOFF", "FLYING", "LANDING"]


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register command with CLI"""
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help=COMMAND_HELP,
        description=__doc__,
    )
    parser.add_argument(
        "-p", "--port", type=int, default=TELEM_PORT,
        help=f"UDP listen port (default: {TELEM_PORT})",
    )
    parser.add_argument(
        "--csv", metavar="FILE", default=None,
        help="Also append every packet to a CSV file",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Print a single decoded packet and exit (for scripting/tests)",
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0,
        help="Give up if no packet arrives for this many seconds (default: 10)",
    )
    parser.add_argument(
        "--web", action="store_true",
        help="Browser view instead of the terminal (UDP -> SSE proxy + charts)",
    )
    parser.add_argument(
        "--http-port", type=int, default=5006,
        help="HTTP port for --web (default: 5006)",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="Do not auto-open the browser (--web only)",
    )
    parser.set_defaults(func=run)


def decode_packet(data: bytes):
    """Decode one telemetry packet; return a dict, or None if it is not one.

    Dispatches on TOTAL LENGTH (104 = v1, 140 = v2) because the v2 block is
    appended, never inserted — the same rule the Data Stream's status packet
    uses for its 17/53/57B revisions. `version` is then only validated, so a
    140-byte packet claiming version != 2 is rejected as malformed.

    For a v1 packet every v2 key is present but None, so callers can use a
    single code path and treat None as "this firmware does not send it".

    1 パケットを復号し dict を返す。テレメトリでなければ None。

    判別は「全長」で行う（104=v1、140=v2）。v2 部は挿入ではなく追記だからで、
    Data Stream のステータスパケットが 17/53/57B の版で採る規則と同じ。`version`
    は検証にのみ使い、140バイトなのに version != 2 なら異常として None を返す。

    v1 パケットでは v2 の各キーは存在するが None になる。呼び出し側は分岐を
    増やさず、None を「このファームは送らない」と扱えばよい。
    """
    is_v2 = len(data) == TELEM_SIZE_V2
    if len(data) != TELEM_SIZE and not is_v2:
        return None
    fields = struct.unpack(TELEM_FMT, data[:TELEM_SIZE])
    magic, version, ptype, t_us = fields[0], fields[1], fields[2], fields[3]
    if magic != TELEM_MAGIC:
        return None
    if is_v2 and version != TELEM_VERSION_V2:
        return None
    out = {"version": version, "type": ptype, "t_us": t_us}
    out.update(dict(zip(FLOAT_NAMES, fields[4:4 + len(FLOAT_NAMES)])))
    out["mode"] = fields[4 + len(FLOAT_NAMES)]

    if not is_v2:
        # Old firmware: the keys exist so the display and CSV stay one shape.
        # 旧ファーム: 表示と CSV の形を一定に保つためキーだけ用意する。
        out.update({name: None for name in V2_NAMES})
        out.update({name: None for name in V2_FLAG_NAMES})
        return out

    (voltage, tof_bottom, tof_front, flow_dx, flow_dy, squal, flags,
     mag_x, mag_y, mag_z, baro_altitude) = struct.unpack(
        TELEM_V2_FMT, data[TELEM_SIZE:])
    out.update({
        "voltage": voltage,
        "tof_bottom": tof_bottom,
        "tof_front": tof_front,
        "flow_dx_sum": flow_dx,
        "flow_dy_sum": flow_dy,
        "flow_squal": squal,
        "mag_x": mag_x, "mag_y": mag_y, "mag_z": mag_z,
        "baro_altitude": baro_altitude,
    })
    # Validity is carried by the flag bits alone. Never infer it from a value
    # (e.g. tof_front == -1.0): a live sensor may report a negative reading on
    # error, and the firmware documents the bit as the authority.
    # 有効性はフラグビットだけが持つ。値から推測しないこと（例: tof_front == -1.0）。
    # 実センサは異常時に負値を返しうるし、ファーム側も「正はビット」と明記している。
    out.update({name: bool(flags & bit) for name, bit in VALID_BITS.items()})
    return out


# Kept as the historical name; telemetry_web.py and external callers may use
# either. decode_packet is the public spelling (lib/sfpilot uses it).
# 旧来の名前を残す。telemetry_web.py や外部からはどちらでも呼べる。公開名は
# decode_packet（lib/sfpilot はこちらを使う）。
_decode = decode_packet


def _state_name(mode: int) -> str:
    return STATE_NAMES[mode] if 0 <= mode < len(STATE_NAMES) else f"?{mode}"


# CSV columns are fixed across firmware versions so one file stays loadable
# even if the vehicle is reflashed mid-session; v1 rows leave the v2 columns
# empty rather than writing a zero that would read as a real measurement.
# CSV の列はファーム版によらず固定する。途中で書き換えても1つのファイルを読み続け
# られるようにするため。v1 の行は v2 列を空欄にする（0 を書くと実測値に見えるため）。
CSV_HEADER = ("t_us,mode," + ",".join(FLOAT_NAMES) + ","
              + ",".join(V2_NAMES) + "," + ",".join(V2_FLAG_NAMES) + "\n")


def csv_row(pkt: dict) -> str:
    """One CSV line for a decoded packet (v1 leaves the v2 columns empty).
    復号済みパケット1件の CSV 行（v1 では v2 列を空欄にする）。"""
    cells = [str(pkt["t_us"]), str(pkt["mode"])]
    cells += [f"{pkt[name]:.6g}" for name in FLOAT_NAMES]
    for name in V2_NAMES:
        value = pkt.get(name)
        cells.append("" if value is None else f"{value:.6g}")
    for name in V2_FLAG_NAMES:
        value = pkt.get(name)
        cells.append("" if value is None else ("1" if value else "0"))
    return ",".join(cells) + "\n"


def _bar(duty: float, width: int = 10) -> str:
    """duty 0..1 -> text bar / duty をテキストバーに"""
    n = max(0, min(width, int(round(duty * width))))
    return "#" * n + "." * (width - n)


def _dashboard(pkt: dict, rate_hz: float, n_packets: int) -> str:
    r2d = 180.0 / math.pi
    alt = -pkt["pos_z"]
    lines = [
        f"StampFly telemetry  :{rate_hz:5.1f} Hz   packets {n_packets}   "
        f"t={pkt['t_us'] / 1e6:9.2f}s",
        f"state : {_state_name(pkt['mode']):<14}",
        f"att   : roll {pkt['roll'] * r2d:+7.2f}  pitch {pkt['pitch'] * r2d:+7.2f}  "
        f"yaw {pkt['yaw'] * r2d:+7.2f}  [deg]",
        f"gyro  : p {pkt['gyro_x'] * r2d:+8.2f}  q {pkt['gyro_y'] * r2d:+8.2f}  "
        f"r {pkt['gyro_z'] * r2d:+8.2f}  [deg/s]",
        f"pos   : N {pkt['pos_x']:+7.2f}  E {pkt['pos_y']:+7.2f}  "
        f"alt {alt:+7.2f}  [m]",
        f"vel   : N {pkt['vel_x']:+6.2f}  E {pkt['vel_y']:+6.2f}  "
        f"D {pkt['vel_z']:+6.2f}  [m/s]",
        f"ctrl  : thrust {pkt['thrust']:6.3f} N   tau "
        f"[{pkt['tau_roll']*1e3:+6.2f} {pkt['tau_pitch']*1e3:+6.2f} "
        f"{pkt['tau_yaw']*1e3:+6.2f}] mNm",
        f"motor : M1 {_bar(pkt['m1'])} {pkt['m1']:4.2f}   "
        f"M2 {_bar(pkt['m2'])} {pkt['m2']:4.2f}",
        f"        M3 {_bar(pkt['m3'])} {pkt['m3']:4.2f}   "
        f"M4 {_bar(pkt['m4'])} {pkt['m4']:4.2f}",
    ]
    lines += _sensor_lines(pkt)
    lines += [
        "",
        "Ctrl-C to quit. For graphs use the Data Stream: sf log wifi -> sf log viz",
    ]
    return "\n".join(lines)


def _format_reading(pkt: dict, key: str, valid_key: str, fmt: str, unit: str) -> str:
    """Format one v2 reading, or say why there is no number to show.
    v2 の値を1つ整形する。数値が無い場合はその理由を示す。"""
    value = pkt.get(key)
    if value is None:
        return "--"                       # v1 firmware / 旧ファーム
    if valid_key and not pkt.get(valid_key):
        return "invalid"                  # flag bit clear / 有効ビットが立っていない
    return f"{value:{fmt}}{unit}"


def _sensor_lines(pkt: dict) -> list:
    """Sensor rows for the dashboard; a v1 packet shows blanks, not zeros.
    ダッシュボードのセンサ行。v1 パケットでは 0 ではなく空欄を表示する。"""
    if pkt.get("voltage") is None:
        return ["sensor: -- (firmware sends the 104B v1 packet; no sensor block)"]

    flow = "--"
    if pkt.get("flow_dx_sum") is not None:
        flow = (f"dx {pkt['flow_dx_sum']:+5d} dy {pkt['flow_dy_sum']:+5d} "
                f"q {pkt['flow_squal']:3d}")
        if not pkt.get("flow_valid"):
            flow += " (stale)"
    return [
        f"power : {_format_reading(pkt, 'voltage', 'power_valid', '5.2f', ' V')}",
        # Both ToF readings go through the same formatter: each shows its
        # distance when its own flag bit is set, and "invalid" when it is not.
        # The front sensor is Optional (absent, switched off, or failed to start
        # on USB-only power), so "invalid" is an ordinary reading here, not a
        # fault — but it is never dressed up as a distance.
        # ToF は 2 つとも同じ整形関数を通す: それぞれ自分のフラグビットが立っていれば
        # 距離を、立っていなければ "invalid" を表示する。前方は Optional（非搭載・
        # 無効化・USB 給電のみでの起動失敗）なので、ここでの "invalid" は異常ではなく
        # 通常の表示である — ただし距離であるかのように見せることはしない。
        f"tof   : down {_format_reading(pkt, 'tof_bottom', 'tof_bottom_valid', '5.3f', ' m')}"
        f"   front {_format_reading(pkt, 'tof_front', 'tof_front_valid', '5.3f', ' m')}",
        f"baro  : {_format_reading(pkt, 'baro_altitude', 'baro_valid', '+7.2f', ' m')}",
        f"mag   : {_format_reading(pkt, 'mag_x', 'mag_valid', '+7.1f', '')} "
        f"{_format_reading(pkt, 'mag_y', 'mag_valid', '+7.1f', '')} "
        f"{_format_reading(pkt, 'mag_z', 'mag_valid', '+7.1f', '')} [uT]",
        f"flow  : {flow}",
    ]


def run(args: argparse.Namespace) -> int:
    if args.web:
        # Browser view (requirements §7 browser display) — UDP -> SSE proxy.
        # Same decoder, same --csv; only the front-end differs.
        # ブラウザ表示（requirements §7）— UDP → SSE プロキシ。デコーダも --csv も
        # 共通で、フロントエンドだけが異なる。
        from . import telemetry_web
        return telemetry_web.serve(http_port=args.http_port,
                                   telemetry_port=args.port,
                                   open_browser=not args.no_browser,
                                   csv_path=args.csv)

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
    try:
        # The vehicle broadcasts to 255.255.255.255:5005 — bind the port on
        # all interfaces; no vehicle IP needed.
        # 機体は 255.255.255.255:5005 へブロードキャスト — 全 IF で bind すれば
        # 機体 IP の指定は不要。
        sock.bind(("", args.port))
    except OSError as exc:
        console.error(f"bind :{args.port} failed: {exc}")
        return 1
    sock.settimeout(args.timeout)

    csv_file = None
    if args.csv:
        csv_file = open(args.csv, "a", buffering=1)
        if csv_file.tell() == 0:
            csv_file.write(CSV_HEADER)

    # The dashboard redraw uses ANSI escapes. Legacy Windows consoles need VT
    # processing enabled first — the empty system() call is the documented
    # stdlib-only trick (Windows Terminal / macOS / Linux need nothing).
    # ダッシュボード再描画は ANSI エスケープを使う。旧来の Windows コンソールは
    # VT 処理の有効化が必要 — 空 system() は stdlib のみでそれを行う既知の手法
    # （Windows Terminal / macOS / Linux では不要）。
    if os.name == "nt":
        os.system("")

    console.info(f"Listening for telemetry on UDP :{args.port} "
                 f"(timeout {args.timeout:.0f}s)...")

    n_packets = 0
    window = []          # arrival times for the measured-rate display
    last_draw = 0.0
    try:
        while True:
            try:
                data, _addr = sock.recvfrom(2048)
            except socket.timeout:
                if n_packets == 0:
                    console.error(
                        "No telemetry received. Checklist: same WiFi network as "
                        "the vehicle (AP mode: join StampFly-XXXX), vehicle "
                        "powered and out of INIT, no `sf log wifi` capture "
                        "running (exclusive-log mode suppresses telemetry).")
                    return 1
                console.warn("Telemetry stream stopped (timeout)")
                return 1

            pkt = _decode(data)
            if pkt is None:
                continue
            n_packets += 1

            now = time.monotonic()
            window.append(now)
            while window and now - window[0] > 2.0:
                window.pop(0)
            rate_hz = len(window) / 2.0

            if csv_file:
                csv_file.write(csv_row(pkt))

            if args.once:
                for key in ["t_us", "mode"] + FLOAT_NAMES + V2_NAMES + V2_FLAG_NAMES:
                    print(f"{key} = {pkt[key]}")
                return 0

            # Redraw at ~10Hz, not per packet (terminal I/O is the bottleneck).
            # 再描画は約10Hz（端末 I/O がボトルネックのためパケット毎にしない）。
            if now - last_draw >= 0.1:
                last_draw = now
                sys.stdout.write("\x1b[H\x1b[2J" + _dashboard(pkt, rate_hz, n_packets) + "\n")
                sys.stdout.flush()
    except KeyboardInterrupt:
        print()
        console.info(f"Stopped. {n_packets} packets received"
                     + (f", CSV: {args.csv}" if args.csv else ""))
        return 0
    finally:
        if csv_file:
            csv_file.close()
        sock.close()
