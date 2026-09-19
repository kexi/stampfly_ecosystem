"""Tests for the UDP:5005 telemetry decoder (`sf telemetry`).

What these guarantee:
  - a 104-byte v1 packet and a 140-byte v2 packet both decode, and v1 reports
    the appended v2 fields as absent (None) rather than as zero
  - anything that is not one of those two lengths, or carries the wrong magic,
    or claims a version other than 2 at 140 bytes, decodes to None
  - valid_flags maps to the documented per-sensor booleans
  - the flow fields are running totals and the difference between two of them
    is the movement in between -- positive, negative, and across the 0xFFFF
    wrap -- so that losing packets costs the total nothing
  - a vehicle restart (its clock going backwards) drops the reference instead
    of reporting the jump as one enormous movement
  - the Python struct formats agree with the C++ wire contract: the offsets
    asserted by static_assert(offsetof(...)) in telemetry.hpp are parsed out of
    that header and compared against what Python's format strings imply, so a
    field reordered on one side alone fails here instead of on a dashboard

保証する内容:
  - 104バイトの v1 と 140バイトの v2 がどちらも復号でき、v1 では追記部が 0 ではなく
    「無い」（None）として報告されること
  - その2つ以外の長さ、magic 不一致、140バイトなのに version が 2 でないものは
    None になること
  - valid_flags が文書どおりのセンサ別真偽値に対応すること
  - フローの 2 項目が累計であり、2 つの累計の差がその間の移動量になること
    （正・負・0xFFFF の折り返しをまたぐ場合）。パケットが失われても累計は何も
    失わないこと
  - 機体の再起動（時刻の巻き戻り）で基準を捨て、跳びを 1 回の巨大な移動として
    報告しないこと
  - Python の struct 書式が C++ の電文契約と一致すること。telemetry.hpp の
    static_assert(offsetof(...)) が固定するオフセットを同ヘッダから読み取り、
    Python の書式が示すオフセットと突き合わせる。片側だけ並べ替えると、
    ダッシュボードの表示ではなくこの試験が落ちる
"""

import re
import struct
from pathlib import Path

import pytest

from sfcli.commands import telemetry as telem


HPP_PATH = (Path(__file__).resolve().parents[3]
            / "firmware" / "vehicle" / "components" / "sf_telemetry"
            / "include" / "telemetry.hpp")

# The v2 wire contract, mirrored from detailed_design.md §10. Each entry is
# (field name in telemetry.hpp, byte offset, struct format for one field).
# detailed_design.md §10 の v2 電文契約の写し。各項目は
# (telemetry.hpp のフィールド名, バイトオフセット, 1項目分の struct 書式)。
V2_OFFSETS = [
    ("voltage", 104, "f"),
    ("tof_bottom", 108, "f"),
    ("tof_front", 112, "f"),
    ("flow_dx_total16", 116, "H"),
    ("flow_dy_total16", 118, "H"),
    ("flow_squal", 120, "B"),
    ("valid_flags", 121, "B"),
    ("reserved2", 122, "2x"),
    # mag_x is the pinned offset; y and z follow it contiguously, so the
    # format below covers all three and lands baro_altitude at 136.
    # 固定されているのは mag_x のオフセット。y と z が続くので、下の書式は3要素を
    # まとめて表し、baro_altitude が 136 に来る。
    ("mag_x", 124, "3f"),
    ("baro_altitude", 136, "f"),
]


def build_v1(version=1, magic=telem.TELEM_MAGIC, mode=5, t_us=1234):
    """A syntactically valid v1 packet with recognisable field values.
    見分けのつく値を入れた、形式的に正しい v1 パケット。"""
    floats = [float(i) for i in range(len(telem.FLOAT_NAMES))]
    return struct.pack(telem.TELEM_FMT, magic, version, 0x01, t_us, *floats, mode)


def build_v2(version=2, flags=0b111111, t_us=1234, **over):
    """A v1 prefix plus the appended v2 block.
    v1 部に v2 追記部を足したもの。"""
    values = {
        "voltage": 3.85, "tof_bottom": 0.42, "tof_front": -1.0,
        "flow_dx_total16": 65529, "flow_dy_total16": 11, "flow_squal": 90,
        "mag_x": 1.5, "mag_y": -2.5, "mag_z": 3.5, "baro_altitude": 1.25,
    }
    values.update(over)
    tail = struct.pack(
        telem.TELEM_V2_FMT, values["voltage"], values["tof_bottom"],
        values["tof_front"], values["flow_dx_total16"],
        values["flow_dy_total16"],
        values["flow_squal"], flags,
        values["mag_x"], values["mag_y"], values["mag_z"],
        values["baro_altitude"])
    return build_v1(version=version, t_us=t_us) + tail


# ---------------------------------------------------------------------------
# Sizes
# ---------------------------------------------------------------------------

def test_wire_sizes_are_104_and_140():
    """The two accepted lengths are exactly the documented ones.
    受け付ける2つの長さが文書どおりであること。"""
    assert telem.TELEM_SIZE == 104
    assert telem.TELEM_SIZE_V2 == 140
    assert len(build_v1()) == 104
    assert len(build_v2()) == 140


# ---------------------------------------------------------------------------
# Decoding both versions
# ---------------------------------------------------------------------------

def test_v1_decodes_and_reports_v2_fields_as_absent():
    """A 104B packet decodes; every v2 key is present but None (not zero).
    104B が復号でき、v2 の各キーは存在するが None（0 ではない）であること。"""
    pkt = telem.decode_packet(build_v1())
    assert pkt is not None
    assert pkt["version"] == 1
    assert pkt["mode"] == 5
    assert pkt["roll"] == 0.0 and pkt["m4"] == 22.0
    for name in telem.V2_NAMES + telem.V2_FLAG_NAMES:
        assert name in pkt, f"{name} missing — display/CSV shape must be stable"
        assert pkt[name] is None, f"{name} must be None on v1, not a zero value"


def test_v2_decodes_all_appended_fields():
    """A 140B packet decodes the appended block with correct units/values.
    140B が追記部を正しい値で復号すること。"""
    pkt = telem.decode_packet(build_v2())
    assert pkt is not None
    assert pkt["version"] == 2
    assert pkt["voltage"] == pytest.approx(3.85, rel=1e-6)
    assert pkt["tof_bottom"] == pytest.approx(0.42, rel=1e-6)
    assert pkt["tof_front"] == pytest.approx(-1.0)
    # The totals are decoded RAW, as unsigned: 65529 must not come back as -7.
    # Turning a pair of them into a movement is flow_delta()'s job, not the
    # decoder's.
    # 累計は符号なしのまま復号される: 65529 が -7 になってはならない。2 つの累計を
    # 移動量に変えるのは flow_delta() の仕事で、復号器の仕事ではない。
    assert pkt["flow_dx_total"] == 65529
    assert pkt["flow_dy_total"] == 11
    assert pkt["flow_squal"] == 90
    assert pkt["mag_x"] == pytest.approx(1.5)
    assert pkt["mag_z"] == pytest.approx(3.5)
    assert pkt["baro_altitude"] == pytest.approx(1.25)
    # The v1 prefix must be parsed identically in both versions.
    # v1 部は両版で同一に解釈されること。
    assert pkt["mode"] == 5
    assert pkt["m4"] == 22.0


def test_v1_prefix_is_byte_identical_inside_a_v2_packet():
    """v2 is an append, not a reshuffle: the first 104B are unchanged.
    v2 は追記であり並べ替えではない: 先頭 104B は変わらないこと。"""
    assert build_v2()[:104] == build_v1(version=2)


def test_legacy_decode_alias_still_works():
    """`_decode` stays available for callers that used the old private name.
    旧来の private 名 `_decode` を使う呼び出し側のために残すこと。"""
    assert telem._decode is telem.decode_packet
    assert telem._decode(build_v2()) is not None


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size", [0, 1, 103, 105, 139, 141, 2048])
def test_wrong_length_returns_none(size):
    """Any length other than 104/140 is not a telemetry packet.
    104/140 以外の長さはテレメトリではないこと。"""
    assert telem.decode_packet(b"\x00" * size) is None


def test_bad_magic_returns_none():
    """A correct length with the wrong magic is rejected, in both versions.
    長さが正しくても magic が違えば両版とも拒否すること。"""
    assert telem.decode_packet(build_v1(magic=0x1234)) is None
    bad_v2 = struct.pack("<H", 0x1234) + build_v2()[2:]
    assert telem.decode_packet(bad_v2) is None


@pytest.mark.parametrize("version", [0, 1, 3, 255])
def test_v2_length_with_wrong_version_returns_none(version):
    """140 bytes must claim version 2; version is the validation field.
    140バイトなら version は 2 であること（version は検証用）。"""
    assert telem.decode_packet(build_v2(version=version)) is None


def test_v1_length_accepts_any_version():
    """Length, not version, selects the layout — v1 stays permissive.
    版ではなく長さでレイアウトを選ぶ。v1 は版を問わず受け入れること。"""
    assert telem.decode_packet(build_v1(version=1)) is not None
    assert telem.decode_packet(build_v1(version=7)) is not None


# ---------------------------------------------------------------------------
# valid_flags
# ---------------------------------------------------------------------------

def test_valid_flags_all_set_and_all_clear():
    """Every documented bit maps to its own boolean.
    文書化された各ビットがそれぞれの真偽値に対応すること。"""
    on = telem.decode_packet(build_v2(flags=0b111111))
    off = telem.decode_packet(build_v2(flags=0))
    for name in telem.V2_FLAG_NAMES:
        assert on[name] is True, name
        assert off[name] is False, name


@pytest.mark.parametrize("name,bit", list(telem.VALID_BITS.items()))
def test_each_valid_bit_is_independent(name, bit):
    """Setting one bit sets exactly one flag — no bit-position mix-ups.
    1ビットを立てるとちょうど1つのフラグが立つこと（ビット位置の取り違え検出）。"""
    pkt = telem.decode_packet(build_v2(flags=bit))
    assert pkt[name] is True
    for other in telem.V2_FLAG_NAMES:
        if other != name:
            assert pkt[other] is False, f"{other} should stay clear"


def test_front_tof_is_reported_invalid_by_the_bit_not_the_value():
    """Current firmware sends -1.0 with bit1 clear; the BIT is the authority.
    現行ファームは bit1 を落として -1.0 を送る。正はビットであること。"""
    pkt = telem.decode_packet(build_v2(flags=0b111101, tof_front=-1.0))
    assert pkt["tof_front_valid"] is False
    # A positive reading with the bit clear is still invalid — value must not
    # be used to infer validity.
    # ビットが落ちていれば正値でも無効。値から有効性を推測しないこと。
    pkt2 = telem.decode_packet(build_v2(flags=0b111101, tof_front=1.5))
    assert pkt2["tof_front_valid"] is False


# ---------------------------------------------------------------------------
# Terminal display (_sensor_lines)
# 端末表示
# ---------------------------------------------------------------------------

def _tof_line(pkt: dict) -> str:
    """The dashboard's `tof` row, for asserting on what the operator reads.
    ダッシュボードの `tof` 行。操作者が読む内容を検査するために取り出す。"""
    lines = [line for line in telem._sensor_lines(pkt) if line.startswith("tof")]
    assert len(lines) == 1, f"expected exactly one tof row, got {lines}"
    return lines[0]


def test_a_driven_front_tof_shows_its_distance():
    """With bit1 set the front reading is a distance, not a "not supported" note.

    The firmware drives the forward sensor now, so the display must show what it
    measured. This is the regression guard for the old hard-coded annotation.
    bit1 が立っていれば前方は距離として表示され、「未対応」の注記ではないこと。

    ファームが前方センサを駆動するようになったので、表示は測った値を出さねば
    ならない。以前のベタ書きの注記に戻っていないことを見張る。
    """
    pkt = telem.decode_packet(build_v2(flags=0b111111, tof_front=0.85))
    line = _tof_line(pkt)

    assert "0.850 m" in line
    assert "not supported" not in line
    assert "未対応" not in line


def test_an_undriven_front_tof_reads_invalid_not_a_distance():
    """Bit1 clear shows "invalid" — never the -1.0 placeholder as a distance.

    The front sensor is optional (absent, switched off, or unstarted on USB-only
    power), so this is the ordinary case and must not look like a reading of
    -1 m or of 0 m.
    bit1 が落ちていれば "invalid" と表示し、placeholder の -1.0 を距離として
    出さないこと。

    前方は任意の装備（非搭載・無効化・USB 給電のみで未起動）なのでこれが通常の
    状態であり、-1m や 0m の測定値のようには見せてはならない。
    """
    line = _tof_line(telem.decode_packet(
        build_v2(flags=0b111101, tof_front=-1.0)))

    assert "invalid" in line
    assert "-1.000" not in line


def test_the_two_tof_readings_are_reported_independently():
    """Each ToF follows its OWN flag bit; one being invalid never hides the other.

    Bottom and front once shared a variable and the bottom sensor's reported rate
    doubled when the front one initialised (docs/architecture/udp-telemetry-design.md
    §2). The bottom ToF is the only vertical observation, so its reading must stay
    untouched by anything the front sensor does.
    2つの ToF はそれぞれ「自分の」フラグビットに従い、片方が無効でももう片方を
    隠さないこと。

    かつて底面と前方が変数を共有し、前方の初期化成功で底面の報告レートが 2 倍に
    なった（docs/architecture/udp-telemetry-design.md §2）。底面は唯一の鉛直観測
    であり、前方が何をしようとその表示は影響を受けてはならない。
    """
    bottom_only = telem.decode_packet(build_v2(
        flags=telem.VALID_BITS["tof_bottom_valid"],
        tof_bottom=0.42, tof_front=-1.0))
    front_only = telem.decode_packet(build_v2(
        flags=telem.VALID_BITS["tof_front_valid"],
        tof_bottom=9.99, tof_front=1.20))

    assert "0.420 m" in _tof_line(bottom_only)      # bottom shown / 底面は表示
    assert "invalid" in _tof_line(bottom_only)      # front not   / 前方は非表示
    assert "1.200 m" in _tof_line(front_only)       # front shown / 前方は表示
    assert "9.990" not in _tof_line(front_only)     # bottom not  / 底面は非表示


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def test_csv_columns_are_stable_across_versions():
    """One CSV can hold both versions; v1 leaves the v2 cells empty.
    1つの CSV に両版を収められ、v1 では v2 の升目が空欄であること。"""
    n_cols = len(telem.CSV_HEADER.strip().split(","))
    row_v1 = telem.csv_row(telem.decode_packet(build_v1()))
    row_v2 = telem.csv_row(telem.decode_packet(build_v2()), flow=(-7, 11))
    assert len(row_v1.strip().split(",")) == n_cols
    assert len(row_v2.strip().split(",")) == n_cols
    # v1 must not write zeros where it has no measurement.
    # v1 は測っていない場所に 0 を書かないこと。
    tail_v1 = row_v1.strip().split(",")[2 + len(telem.FLOAT_NAMES):]
    assert all(cell == "" for cell in tail_v1)
    tail_v2 = row_v2.strip().split(",")[2 + len(telem.FLOAT_NAMES):]
    assert all(cell != "" for cell in tail_v2)


def _csv_cell(row: str, column: str):
    """The value under one CSV column name, for asserting on a recorded row.
    CSV の 1 列の値。記録された行を検査するために取り出す。"""
    header = telem.CSV_HEADER.strip().split(",")
    return row.strip().split(",")[header.index(column)]


def test_csv_records_both_the_raw_total_and_the_movement():
    """A row carries the total as sent AND the movement since the row before.

    Both are needed: the movement is what an analysis plots directly, while the
    total lets it re-derive a movement across rows this recorder never saw.
    1 行が「送られたままの累計」と「1 つ前の行からの移動量」の両方を持つこと。

    両方が要る。移動量はそのまま図に描ける量であり、累計は記録側が見なかった行を
    またいだ移動量を取り直すためにある。
    """
    row = telem.csv_row(telem.decode_packet(build_v2()), flow=(-7, 11))

    assert _csv_cell(row, "flow_dx_total") == "65529"
    assert _csv_cell(row, "flow_dx_delta") == "-7"
    assert _csv_cell(row, "flow_dy_delta") == "11"


def test_csv_leaves_the_movement_empty_when_there_is_no_reference():
    """The first row of a stream has nothing to difference against, so the
    movement cells are empty rather than zero -- a zero would claim the surface
    had not moved.
    流れの最初の行には差を取る相手が無いので、移動量の升目は 0 ではなく空欄に
    すること。0 は「面が動かなかった」と主張することになる。
    """
    row = telem.csv_row(telem.decode_packet(build_v2()))

    assert _csv_cell(row, "flow_dx_delta") == ""
    assert _csv_cell(row, "flow_dx_total") == "65529"


# ---------------------------------------------------------------------------
# Flow totals -> movement (flow_delta / FlowTracker)
# フローの累計から移動量へ
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("previous,current,expected", [
    (100, 150, 50),            # plain forward / 素直な前進
    (150, 100, -50),           # plain backward / 素直な後退
    (100, 100, 0),             # standing still / 静止
    (65530, 10, 16),           # forward across 0xFFFF / 折り返しをまたぐ前進
    (10, 65530, -16),          # backward across 0xFFFF / 折り返しをまたぐ後退
    (0, 32767, 32767),         # the largest movement that stays unambiguous
    (0, 32768, -32768),        # one count further reads as the other direction
])
def test_flow_delta_reads_the_difference_as_a_signed_wrap(previous, current,
                                                          expected):
    """The movement between two totals is their difference modulo 2^16, signed.

    The 0xFFFF cases are the point of the format: the totals are only ever sent
    as their low 16 bits, so every reader must cross that boundary without
    seeing a 65000-count jump. The last two rows state the limit exactly --
    32767 counts is the most that can be told apart from a move the other way.

    2 つの累計の間の移動量は、その差を 2^16 で法として符号つきと読んだもの。

    0xFFFF をまたぐ場合こそがこの様式の要点である。累計は常に下位 16 ビットしか
    送られないので、どの読み手もその境界を「65000 カウントの跳び」と見ずに
    越えねばならない。最後の 2 行が限界を正確に示す — 逆向きの移動と区別できるのは
    32767 カウントまでである。
    """
    assert telem.flow_delta(previous, current) == expected


def test_lost_packets_do_not_lose_any_displacement():
    """The movement recovered across a gap equals the sum of the movements of
    the packets that went missing.

    This is the whole reason the wire carries a total. UDP:5005 is a broadcast
    that WiFi never retransmits, and runs of 4 to 10 consecutive losses were
    measured on hardware on 2026-09-19; a difference carried by a lost packet
    would be gone for good.

    欠損をまたいで復元した移動量が、失われたパケットの移動量の合計に等しいこと。

    電文が累計を運ぶ理由そのものである。UDP:5005 は WiFi が再送しない
    ブロードキャストで、2026-09-19 の実機実測では連続 4〜10 個の欠損が起きた。
    失われたパケットが運んでいた差分なら永久に戻らない。
    """
    moves = [37, -12, 250, -4, 19, 900, -333]
    totals = [1000]
    for move in moves:
        totals.append((totals[-1] + move) % 65536)
    packets = [build_v2(flow_dx_total16=total, t_us=1000 * i)
               for i, total in enumerate(totals)]

    # Keep only the first and last: every packet in between is "lost".
    # 最初と最後だけ残す。間のパケットは全て「失われた」ことにする。
    tracker = telem.FlowTracker()
    tracker.update(telem.decode_packet(packets[0]))
    recovered, _ = tracker.update(telem.decode_packet(packets[-1]))

    assert recovered == sum(moves)

    # And the unbroken stream reports the same total movement, packet by
    # packet -- losing packets changes when the movement is reported, not how
    # much of it there is.
    # 欠損の無い流れも、1 パケットずつで同じ総移動量を報告すること — 欠損が変える
    # のは移動量が報告される時点であって、その量ではない。
    unbroken = telem.FlowTracker()
    unbroken.update(telem.decode_packet(packets[0]))
    stepwise = [unbroken.update(telem.decode_packet(p))[0]
                for p in packets[1:]]
    assert stepwise == moves
    assert sum(stepwise) == recovered


def test_the_first_packet_of_a_stream_reports_no_movement():
    """With no earlier total there is nothing to difference against, so the
    tracker says "unknown" rather than treating the vehicle's own total since
    boot as one enormous movement.
    より前の累計が無ければ差を取る相手が無いので、tracker は「不明」と答える。
    機体の起動からの累計を 1 回の巨大な移動として扱わないこと。
    """
    tracker = telem.FlowTracker()
    assert tracker.update(telem.decode_packet(build_v2())) == (None, None)


def test_a_vehicle_restart_drops_the_reference():
    """When the vehicle's clock goes backwards its flow totals restarted too,
    so the jump must be discarded, not reported.

    The timestamp counts microseconds since the vehicle's own boot, so a reboot
    restarts both it and the totals at zero. Without this the first packet
    after a reboot would report the difference against the pre-reboot total.

    機体の時刻が巻き戻ったときは、フローの累計も 0 から始まっている。よってその
    跳びは報告せず捨てること。

    タイムスタンプは「その機体の起動」からのマイクロ秒なので、再起動すれば時刻も
    累計も 0 から始まる。この処理が無いと、再起動後の最初のパケットが再起動前の
    累計との差を報告してしまう。
    """
    tracker = telem.FlowTracker()
    tracker.update(telem.decode_packet(
        build_v2(flow_dx_total16=40000, t_us=9_000_000)))

    after_reboot = tracker.update(telem.decode_packet(
        build_v2(flow_dx_total16=3, t_us=120_000)))

    assert after_reboot == (None, None)

    # The packet after that one is measured against the post-reboot total, so
    # the stream recovers on the very next packet.
    # その次のパケットは再起動後の累計を基準に測られる。流れは次の 1 件で復帰する。
    assert tracker.update(telem.decode_packet(
        build_v2(flow_dx_total16=9, t_us=140_000)))[0] == 6


def test_a_v1_packet_reports_no_flow_movement():
    """Old firmware sends no flow field, so the tracker must not invent one.
    旧ファームはフロー項目を送らないので、tracker が値を作り出さないこと。"""
    tracker = telem.FlowTracker()
    assert tracker.update(telem.decode_packet(build_v1())) == (None, None)
    assert tracker.update(telem.decode_packet(build_v1())) == (None, None)


# ---------------------------------------------------------------------------
# Flow display
# フローの表示
# ---------------------------------------------------------------------------

def _flow_line(pkt: dict, flow=(None, None), since_start=(None, None)) -> str:
    """The dashboard's `flow` row, for asserting on what the operator reads.
    ダッシュボードの `flow` 行。操作者が読む内容を検査するために取り出す。"""
    lines = [line for line in telem._sensor_lines(pkt, flow, since_start)
             if line.startswith("flow")]
    assert len(lines) == 1, f"expected exactly one flow row, got {lines}"
    return lines[0]


def test_the_flow_row_shows_the_movement_not_the_raw_total():
    """The operator reads a movement and a running sum, never the wire total.

    The wire total is a number modulo 2^16 with no meaning on its own -- 65529
    is a movement of -7, not of 65529 -- so showing it would be misleading.
    操作者が読むのは移動量と累計であって、電文の生の累計ではないこと。

    電文の累計は 2^16 を法とする数で単独では意味を持たない（65529 は 65529 の
    移動ではなく -7 の移動である）ため、そのまま出すと誤解を招く。
    """
    line = _flow_line(telem.decode_packet(build_v2()),
                      flow=(-7, 11), since_start=(120, -30))

    assert "dx    -7" in line and "dy   +11" in line
    assert "65529" not in line
    assert "+120" in line and "-30" in line


def test_the_flow_row_says_so_when_there_is_no_movement_yet():
    """Before a second packet arrives there is no movement to show, and the row
    must not print a zero that would read as "not moving".
    2 件目が届くまでは表示できる移動量が無い。「動いていない」と読める 0 を
    出さないこと。
    """
    line = _flow_line(telem.decode_packet(build_v2()))

    assert "dx -----" in line
    assert "+0" not in line


# ---------------------------------------------------------------------------
# C++ / Python wire-contract agreement
# ---------------------------------------------------------------------------

def parse_hpp_offsets():
    """Read the offsets pinned by static_assert(offsetof(...)) in telemetry.hpp.
    telemetry.hpp の static_assert(offsetof(...)) が固定するオフセットを読む。"""
    text = HPP_PATH.read_text(encoding="utf-8")
    found = re.findall(
        r"static_assert\(\s*offsetof\(TelemetryPacket,\s*(\w+)\s*\)\s*==\s*(\d+)",
        text)
    return {name: int(offset) for name, offset in found}


def test_hpp_offset_table_matches_this_test_table():
    """The firmware header pins exactly the offsets this file encodes.
    ファームのヘッダが、本ファイルの表と同じオフセットを固定していること。"""
    assert HPP_PATH.exists(), f"firmware header not found: {HPP_PATH}"
    hpp = parse_hpp_offsets()
    assert hpp, "no offsetof static_asserts found — did the header change?"
    expected = {name: off for name, off, _ in V2_OFFSETS}
    assert hpp == expected


def test_python_format_implies_the_same_offsets_as_the_hpp():
    """Python's struct format must land every field on the C++ offset.
    Python の struct 書式が、各項目を C++ と同じオフセットに置くこと。"""
    hpp = parse_hpp_offsets()
    cursor = telem.TELEM_SIZE          # the v2 block starts after v1 / v2 部は v1 の後
    for name, _expected, fmt in V2_OFFSETS:
        if name in hpp:
            assert cursor == hpp[name], (
                f"{name}: python format puts it at {cursor}, "
                f"telemetry.hpp pins it at {hpp[name]}")
        cursor += struct.calcsize("<" + fmt)
    assert cursor == telem.TELEM_SIZE_V2


def test_hpp_size_assert_matches_python():
    """telemetry.hpp's size static_assert and TELEM_SIZE_V2 agree.
    telemetry.hpp のサイズ static_assert と TELEM_SIZE_V2 が一致すること。"""
    text = HPP_PATH.read_text(encoding="utf-8")
    match = re.search(r"static_assert\(sizeof\(TelemetryPacket\)\s*==\s*(\d+)", text)
    assert match, "size static_assert not found in telemetry.hpp"
    assert int(match.group(1)) == telem.TELEM_SIZE_V2


def test_hpp_valid_bits_match_python():
    """TELEM_VALID_* bit positions in the header match VALID_BITS.
    ヘッダの TELEM_VALID_* のビット位置が VALID_BITS と一致すること。"""
    text = HPP_PATH.read_text(encoding="utf-8")
    found = dict(re.findall(
        r"TELEM_VALID_(\w+)\s*=\s*1u\s*<<\s*(\d+)", text))
    assert found, "no TELEM_VALID_* constants found in telemetry.hpp"
    hpp_bits = {name.lower(): 1 << int(shift) for name, shift in found.items()}
    python_bits = {
        "tof_bottom": telem.VALID_BITS["tof_bottom_valid"],
        "tof_front": telem.VALID_BITS["tof_front_valid"],
        "flow": telem.VALID_BITS["flow_valid"],
        "mag": telem.VALID_BITS["mag_valid"],
        "baro": telem.VALID_BITS["baro_valid"],
        "power": telem.VALID_BITS["power_valid"],
    }
    assert hpp_bits == python_bits
