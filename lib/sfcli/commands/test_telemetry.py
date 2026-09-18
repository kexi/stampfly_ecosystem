"""Tests for the UDP:5005 telemetry decoder (`sf telemetry`).

What these guarantee:
  - a 104-byte v1 packet and a 140-byte v2 packet both decode, and v1 reports
    the appended v2 fields as absent (None) rather than as zero
  - anything that is not one of those two lengths, or carries the wrong magic,
    or claims a version other than 2 at 140 bytes, decodes to None
  - valid_flags maps to the documented per-sensor booleans
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
    ("flow_dx_sum", 116, "h"),
    ("flow_dy_sum", 118, "h"),
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


def build_v1(version=1, magic=telem.TELEM_MAGIC, mode=5):
    """A syntactically valid v1 packet with recognisable field values.
    見分けのつく値を入れた、形式的に正しい v1 パケット。"""
    floats = [float(i) for i in range(len(telem.FLOAT_NAMES))]
    return struct.pack(telem.TELEM_FMT, magic, version, 0x01, 1234, *floats, mode)


def build_v2(version=2, flags=0b111111, **over):
    """A v1 prefix plus the appended v2 block.
    v1 部に v2 追記部を足したもの。"""
    values = {
        "voltage": 3.85, "tof_bottom": 0.42, "tof_front": -1.0,
        "flow_dx_sum": -7, "flow_dy_sum": 11, "flow_squal": 90,
        "mag_x": 1.5, "mag_y": -2.5, "mag_z": 3.5, "baro_altitude": 1.25,
    }
    values.update(over)
    tail = struct.pack(
        telem.TELEM_V2_FMT, values["voltage"], values["tof_bottom"],
        values["tof_front"], values["flow_dx_sum"], values["flow_dy_sum"],
        values["flow_squal"], flags,
        values["mag_x"], values["mag_y"], values["mag_z"],
        values["baro_altitude"])
    return build_v1(version=version) + tail


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
    assert pkt["flow_dx_sum"] == -7
    assert pkt["flow_dy_sum"] == 11
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
# CSV
# ---------------------------------------------------------------------------

def test_csv_columns_are_stable_across_versions():
    """One CSV can hold both versions; v1 leaves the v2 cells empty.
    1つの CSV に両版を収められ、v1 では v2 の升目が空欄であること。"""
    n_cols = len(telem.CSV_HEADER.strip().split(","))
    row_v1 = telem.csv_row(telem.decode_packet(build_v1()))
    row_v2 = telem.csv_row(telem.decode_packet(build_v2()))
    assert len(row_v1.strip().split(",")) == n_cols
    assert len(row_v2.strip().split(",")) == n_cols
    # v1 must not write zeros where it has no measurement.
    # v1 は測っていない場所に 0 を書かないこと。
    tail_v1 = row_v1.strip().split(",")[2 + len(telem.FLOAT_NAMES):]
    assert all(cell == "" for cell in tail_v1)
    tail_v2 = row_v2.strip().split(",")[2 + len(telem.FLOAT_NAMES):]
    assert all(cell != "" for cell in tail_v2)


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
