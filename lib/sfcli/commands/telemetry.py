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

The v2 optical-flow fields are RUNNING TOTALS (modulo 2^16), not a movement:
UDP:5005 is a broadcast that WiFi does not retransmit, so packets are lost
(58% received, measured 2026-09-19) and a movement carried by a lost packet
would be gone for good. flow_delta() / FlowTracker turn two totals into the
movement between them, correctly across any number of missing packets.

vehicle のモニタ用テレメトリ（バイナリ、magic 0xCAFE、UDP ブロードキャスト
:5005、50Hz）を受信し、ターミナルにライブ表示します。--csv で記録も可能。
グラフ・オフライン解析には Data Stream（`sf log wifi` → `sf log viz`、
400Hz・全センサ）を使ってください。

電文は2版あり「全長」で判別します: 104バイト（v1）と 140バイト（v2。電池電圧・
ToF・オプティカルフロー・地磁気・気圧高度を追記）。v2 は純粋な追記なので先頭
104バイトは両版で同一で、旧ファームもそのまま動きます。

v2 のオプティカルフローの 2 項目は移動量ではなく「累計」（2^16 を法とする）です。
UDP:5005 は WiFi が再送しないブロードキャストなのでパケットが失われ（2026-09-19
の実測で受信 58%）、失われたパケットが運んでいた移動量は永久に戻らないためです。
flow_delta() / FlowTracker が累計 2 つをその間の移動量に変えます。何個パケットが
欠けていても正しく求まります。
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
TELEM_V2_FMT = "<3f2HBB2x4f"   # voltage, tof_b, tof_f, dx_total16, dy_total16, squal, flags, pad, mag*3, baro
TELEM_V2_SIZE = struct.calcsize(TELEM_V2_FMT)
TELEM_SIZE_V2 = TELEM_SIZE + TELEM_V2_SIZE
TELEM_VERSION_V2 = 2

# The flow totals are sent modulo this, so a difference is taken modulo it too.
# フロー累計はこの法で送られるので、差も同じ法で取る。
FLOW_TOTAL_MODULO = 1 << 16
FLOW_TOTAL_HALF = FLOW_TOTAL_MODULO // 2

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
    "flow_dx_total", "flow_dy_total", "flow_squal",
    "mag_x", "mag_y", "mag_z", "baro_altitude",
]
V2_FLAG_NAMES = list(VALID_BITS)

# Displacement columns derived on this side: the difference between a row and
# the one before it. Recorded next to the raw totals rather than instead of
# them, so an analysis can either read a movement directly or re-derive it
# across a gap the recorder itself did not see.
# こちら側で導く変位の列: ある行と1つ前の行との差。生の累計を置き換えるのではなく
# 並べて記録する。解析側が、移動量をそのまま読むことも、記録側が見ていない欠損を
# またいで取り直すこともできるようにするためである。
V2_DERIVED_NAMES = ["flow_dx_delta", "flow_dy_delta"]

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

    The two flow fields are RUNNING TOTALS modulo 2^16, not a movement: use
    flow_delta() or FlowTracker to turn two of them into a displacement.

    For a v1 packet every v2 key is present but None, so callers can use a
    single code path and treat None as "this firmware does not send it".

    1 パケットを復号し dict を返す。テレメトリでなければ None。

    判別は「全長」で行う（104=v1、140=v2）。v2 部は挿入ではなく追記だからで、
    Data Stream のステータスパケットが 17/53/57B の版で採る規則と同じ。`version`
    は検証にのみ使い、140バイトなのに version != 2 なら異常として None を返す。

    フローの 2 項目は移動量ではなく 2^16 を法とする「累計」である。2 つの累計を
    変位にするには flow_delta() か FlowTracker を使うこと。

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

    (voltage, tof_bottom, tof_front, flow_dx_total, flow_dy_total, squal, flags,
     mag_x, mag_y, mag_z, baro_altitude) = struct.unpack(
        TELEM_V2_FMT, data[TELEM_SIZE:])
    out.update({
        "voltage": voltage,
        "tof_bottom": tof_bottom,
        "tof_front": tof_front,
        # The RAW totals, exactly as the wire carried them. Turning two of
        # these into a movement is the caller's job, through flow_delta() or
        # FlowTracker -- this function stays a pure decode of one packet and
        # keeps no memory of the packet before it.
        # 生の累計を、電文が運んだそのままの形で入れる。2 つの累計を移動量に
        # 変えるのは呼び出し側の仕事で、flow_delta() か FlowTracker を通す。
        # 本関数は 1 パケットの純粋な復号にとどめ、1 つ前のパケットを覚えない。
        "flow_dx_total": flow_dx_total,
        "flow_dy_total": flow_dy_total,
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


def flow_delta(prev_total16: int, cur_total16: int) -> int:
    """Displacement between two wire flow totals, as a signed 16-bit wrap.

    The firmware sends the low 16 bits of a total that never resets, so the
    movement between any two packets is their difference taken modulo 2^16 and
    read as signed. That is what makes a lost packet harmless: the totals of
    the packets either side of the gap still differ by the whole movement
    across it, however many packets vanished in between.

    The one limit is ±32767 counts of true movement between two RECEIVED
    packets, beyond which the wrap looks like a shorter move the other way.
    Hand-waving the vehicle measured at most about 900 counts per second on
    2026-09-19, so that is roughly 36 seconds of unbroken loss.

    電文のフロー累計 2 つの間の変位を、符号つき 16 ビットの折り返しとして返す。

    ファームが送るのは、決して 0 に戻らない累計の下位 16 ビットである。よって
    任意の 2 パケット間の移動量は、その差を 2^16 で法として取り符号つきと読んだ
    ものになる。欠損が無害なのはこれによる。間で何個パケットが消えても、欠損の
    両側のパケットの累計の差は、その間の移動量の全量のままである。

    唯一の限界は、「受信できた」2 パケット間の真の移動量が ±32767 カウントまで
    という点である。それを超えると折り返しは逆向きの短い移動に見える。2026-09-19
    の実測では手で振っても毎秒約 900 カウントだったので、およそ 36 秒の連続欠損に
    あたる。
    """
    difference = (cur_total16 - prev_total16) % FLOW_TOTAL_MODULO
    is_negative_move = difference >= FLOW_TOTAL_HALF
    if is_negative_move:
        return difference - FLOW_TOTAL_MODULO
    return difference


class FlowTracker:
    """Turns a stream of packets into per-packet flow displacements.

    One object per stream, so the terminal display, the CSV writer, the browser
    view and `sf pilot` all take the difference the same way instead of each
    inventing one.

    It holds the previous totals and the previous `t_us`, and drops its
    reference when `t_us` goes BACKWARDS. The vehicle's timestamp counts
    microseconds since ITS boot, so a reboot restarts both it and the flow
    totals at zero; without this check the first packet after a reboot would
    report the jump from the old totals as one enormous movement.

    パケットの流れを、1 パケットごとの変位に変える。

    流れ 1 つにつき 1 個。端末表示・CSV・ブラウザ表示・`sf pilot` が、それぞれ
    独自に差を取るのではなく同じ取り方をするためである。

    前回の累計と前回の `t_us` を持ち、`t_us` が「巻き戻ったら」基準を捨てる。
    機体のタイムスタンプは「その機体の起動」からのマイクロ秒なので、再起動すれば
    タイムスタンプもフロー累計も 0 から始まる。この判定が無いと、再起動後の最初の
    パケットが、古い累計からの跳びを 1 回の巨大な移動として報告してしまう。
    """

    def __init__(self) -> None:
        self._prev_totals = None    # (dx_total, dy_total) / 前回の累計
        self._prev_t_us = None      # previous vehicle clock / 前回の機体時刻

    def update(self, pkt: dict):
        """Displacement (dx, dy) since the previous packet, or (None, None).

        None means there is nothing to compare against: a v1 packet with no
        flow field, the first packet of a stream, or the first packet after
        the vehicle restarted.
        前回パケットからの変位 (dx, dy)。比較する相手が無ければ (None, None)。
        v1 でフロー項目が無い場合・流れの最初の 1 件・機体の再起動直後がそれに
        あたる。
        """
        totals = (pkt.get("flow_dx_total"), pkt.get("flow_dy_total"))
        if totals[0] is None or totals[1] is None:
            return None, None       # v1 firmware / 旧ファーム

        t_us = pkt.get("t_us")
        if self._restarted(t_us):
            self._prev_totals = None
        self._prev_t_us = t_us

        previous = self._prev_totals
        self._prev_totals = totals
        if previous is None:
            return None, None       # no reference yet / 基準がまだ無い
        return (flow_delta(previous[0], totals[0]),
                flow_delta(previous[1], totals[1]))

    def _restarted(self, t_us) -> bool:
        """Whether the vehicle's clock went backwards since the last packet.
        前回パケット以降に機体の時刻が巻き戻ったか。

        Why not also treat a forward jump as a restart: the clock is a uint32
        of microseconds and wraps to zero about every 71 minutes on its own,
        which is a backwards step too. Both cases cost one displacement and
        nothing more, so one rule covers them.
        なぜ前方への跳びは再起動と見なさないか: この時刻は uint32 のマイクロ秒で、
        放っておいても約 71 分で 0 に戻る — それも巻き戻りである。どちらの場合も
        失うのは変位 1 回分だけなので、1 つの規則で足りる。
        """
        if t_us is None or self._prev_t_us is None:
            return False
        return t_us < self._prev_t_us


def _state_name(mode: int) -> str:
    return STATE_NAMES[mode] if 0 <= mode < len(STATE_NAMES) else f"?{mode}"


# CSV columns are fixed across firmware versions so one file stays loadable
# even if the vehicle is reflashed mid-session; v1 rows leave the v2 columns
# empty rather than writing a zero that would read as a real measurement.
# CSV の列はファーム版によらず固定する。途中で書き換えても1つのファイルを読み続け
# られるようにするため。v1 の行は v2 列を空欄にする（0 を書くと実測値に見えるため）。
CSV_HEADER = ("t_us,mode," + ",".join(FLOAT_NAMES) + ","
              + ",".join(V2_NAMES) + "," + ",".join(V2_DERIVED_NAMES) + ","
              + ",".join(V2_FLAG_NAMES) + "\n")


def csv_row(pkt: dict, flow: tuple = (None, None)) -> str:
    """One CSV line for a decoded packet (v1 leaves the v2 columns empty).

    `flow` is the (dx, dy) a FlowTracker returned for this packet: the movement
    since the PREVIOUS RECORDED ROW. Both it and the raw totals are written,
    because they answer different questions -- the delta is what a reader
    plots, while the totals let a reader re-derive the movement across rows the
    recorder skipped, or across a gap in a file that was concatenated.

    復号済みパケット1件の CSV 行（v1 では v2 列を空欄にする）。

    `flow` は本パケットについて FlowTracker が返した (dx, dy)、すなわち「前の
    記録行」からの移動量である。これと生の累計の両方を書くのは、答える問いが
    違うためである。変位はそのまま図に描ける量で、累計は記録側が飛ばした行を
    またいだ移動量や、連結されたファイルの切れ目をまたいだ移動量を、読み手が
    取り直すためにある。
    """
    cells = [str(pkt["t_us"]), str(pkt["mode"])]
    cells += [f"{pkt[name]:.6g}" for name in FLOAT_NAMES]
    for name in V2_NAMES:
        value = pkt.get(name)
        cells.append("" if value is None else f"{value:.6g}")
    cells += ["" if value is None else str(value) for value in flow]
    for name in V2_FLAG_NAMES:
        value = pkt.get(name)
        cells.append("" if value is None else ("1" if value else "0"))
    return ",".join(cells) + "\n"


def _bar(duty: float, width: int = 10) -> str:
    """duty 0..1 -> text bar / duty をテキストバーに"""
    n = max(0, min(width, int(round(duty * width))))
    return "#" * n + "." * (width - n)


def _dashboard(pkt: dict, rate_hz: float, n_packets: int,
               flow: tuple = (None, None),
               flow_since_start: tuple = (None, None)) -> str:
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
    lines += _sensor_lines(pkt, flow, flow_since_start)
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


def _flow_line(pkt: dict, flow: tuple, flow_since_start: tuple) -> str:
    """The `flow` row: movement since the last packet, and since we started.

    Two numbers because they answer different questions. The delta says how
    fast the surface is moving under the vehicle right now; the running sum
    says how far it has travelled in total, and it stays correct across the
    packets that UDP broadcast loses -- which is the whole reason the wire
    carries a total rather than a difference.

    `flow` 行: 前回パケットからの移動量と、受信開始からの累計。

    答える問いが違うので 2 つ出す。変位は「今どれだけの速さで面が流れているか」を、
    累計は「合計でどれだけ進んだか」を示す。累計は UDP ブロードキャストが落とした
    パケットをまたいでも正しいままで、電文が差分ではなく累計を運ぶ理由そのもので
    ある。
    """
    if pkt.get("flow_dx_total") is None:
        return "flow  : --"

    if flow[0] is None:
        movement = "dx ----- dy -----"      # no reference yet / 基準がまだ無い
    else:
        movement = f"dx {flow[0]:+5d} dy {flow[1]:+5d}"
    text = f"{movement}  q {pkt['flow_squal']:3d}"
    if flow_since_start[0] is not None:
        text += (f"   since start dx {flow_since_start[0]:+7d} "
                 f"dy {flow_since_start[1]:+7d}")
    if not pkt.get("flow_valid"):
        text += " (stale)"
    return f"flow  : {text}"


def _sensor_lines(pkt: dict, flow: tuple = (None, None),
                  flow_since_start: tuple = (None, None)) -> list:
    """Sensor rows for the dashboard; a v1 packet shows blanks, not zeros.
    ダッシュボードのセンサ行。v1 パケットでは 0 ではなく空欄を表示する。"""
    if pkt.get("voltage") is None:
        return ["sensor: -- (firmware sends the 104B v1 packet; no sensor block)"]

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
        _flow_line(pkt, flow, flow_since_start),
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
    # One tracker for this stream, so the CSV and the dashboard take the flow
    # difference the same way. Summing its deltas gives "since we started
    # listening", which is NOT the vehicle's own total -- the vehicle had been
    # moving before we joined, and its total is only known modulo 2^16 anyway.
    # この流れにつき 1 個。CSV とダッシュボードが同じ取り方で差を出すためである。
    # 変位を足し上げたものが「受信開始から」であり、機体自身の累計ではない —
    # 参加前にも機体は動いていたし、そもそも累計は 2^16 を法としてしか分からない。
    flow_tracker = FlowTracker()
    flow_since_start = [0, 0]
    have_flow = False
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

            flow = flow_tracker.update(pkt)
            if flow[0] is not None:
                have_flow = True
                flow_since_start[0] += flow[0]
                flow_since_start[1] += flow[1]

            if csv_file:
                csv_file.write(csv_row(pkt, flow))

            if args.once:
                for key in ["t_us", "mode"] + FLOAT_NAMES + V2_NAMES + V2_FLAG_NAMES:
                    print(f"{key} = {pkt[key]}")
                return 0

            # Redraw at ~10Hz, not per packet (terminal I/O is the bottleneck).
            # 再描画は約10Hz（端末 I/O がボトルネックのためパケット毎にしない）。
            if now - last_draw >= 0.1:
                last_draw = now
                since_start = tuple(flow_since_start) if have_flow else (None, None)
                sys.stdout.write(
                    "\x1b[H\x1b[2J"
                    + _dashboard(pkt, rate_hz, n_packets, flow, since_start) + "\n")
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
