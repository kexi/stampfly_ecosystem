"""
What RealLink's 50Hz path guarantees: a UDP:5005 packet becomes a Sample,
a 140-byte v2 packet supplies the battery and ToF by itself, a
104-byte v1 packet omits them rather than inventing zeros, and the flow
totals on the wire reach the Sample as a movement.

RealLink の 50Hz 経路が保証すること: UDP:5005 のパケットが Sample になること、
140 バイトの v2 パケットは電池と ToF を単独で供給すること、104 バイトの v1
パケットはそれらを 0 で捏造せず省くこと、電文のフロー累計が Sample には移動量
として届くこと。

Packets are built here with the SAME struct format the firmware encoder and
`sfcli.commands.telemetry` share, so a format change breaks this test rather
than silently producing wrong numbers in flight.
パケットはファームの符号化器と `sfcli.commands.telemetry` が共有するのと同じ
struct 書式で組み立てる。書式が変われば飛行中に静かに誤った数値が出るのではなく、
この試験が落ちる。
"""

import socket
import struct
import time

import pytest

from sfcli.commands.telemetry import (
    FLOAT_NAMES, TELEM_FMT, TELEM_MAGIC, TELEM_SIZE, TELEM_SIZE_V2,
    TELEM_V2_FMT, TELEM_VERSION_V2, VALID_BITS,
)
from sfpilot.link import RealLink

# A hovering craft, 0.8 m up, sliding slowly north-east.
# 0.8m でホバリングし、北東へゆっくり流れている機体。
FLOATS = {
    "roll": 0.02, "pitch": -0.01, "yaw": 1.57,
    "pos_x": 0.30, "pos_y": -0.40, "pos_z": -0.80,   # NED: z down / z は下向き
    "vel_x": 0.12, "vel_y": 0.05, "vel_z": -0.01,
}
FLYING_MODE = 5   # STATE_NAMES index for "FLYING" / 「FLYING」の番号
# kTelemTofFrontUnavailable in telemetry.hpp: what the firmware puts in the
# tof_front slot when there is no reading. The flag bit — not this value — is
# what says whether the reading counts.
# telemetry.hpp の kTelemTofFrontUnavailable。測定値が無いときにファームが
# tof_front 欄へ入れる値。採否を決めるのはこの値ではなくフラグビットである。
TOF_FRONT_UNAVAILABLE = -1.0


def _v1_packet(mode: int = FLYING_MODE) -> bytes:
    """A 104-byte v1 telemetry packet. / 104 バイトの v1 テレメトリパケット。"""
    values = [FLOATS.get(name, 0.0) for name in FLOAT_NAMES]
    return struct.pack(TELEM_FMT, TELEM_MAGIC, 1, 0, 1234, *values, mode)


def _v2_packet(voltage: float = 3.9, tof: float = 0.79,
               valid: bool = True, mode: int = FLYING_MODE,
               tof_front: float = None,
               flow_total: int = 0, t_us: int = 1234) -> bytes:
    """A 140-byte v2 packet: v1 prefix plus the appended block.

    `tof_front=None` is the common real case — the forward sensor is optional
    and needs battery power — so the packet then carries the firmware's
    "no reading" placeholder with bit1 clear.
    140 バイトの v2 パケット: v1 の前半に追記部を足したもの。

    `tof_front=None` が実際によくある状態 — 前方センサは任意でバッテリー電源を
    要する — なので、そのときファーム側の「測定値なし」の placeholder を
    bit1 を落として載せる。
    """
    values = [FLOATS.get(name, 0.0) for name in FLOAT_NAMES]
    head = struct.pack(TELEM_FMT, TELEM_MAGIC, TELEM_VERSION_V2, 0, t_us, *values, mode)
    flags = 0
    if valid:
        flags = VALID_BITS["tof_bottom_valid"] | VALID_BITS["power_valid"]
    front_value = TOF_FRONT_UNAVAILABLE if tof_front is None else tof_front
    if tof_front is not None:
        flags |= VALID_BITS["tof_front_valid"]
    # `flow_total` goes into BOTH flow slots: the wire carries running totals
    # modulo 2^16, so what a test states here is a position, not a movement.
    # `flow_total` はフローの 2 枠の両方へ入れる。電文が運ぶのは 2^16 を法とする
    # 累計なので、ここで書く値は移動量ではなく位置である。
    tail = struct.pack(
        TELEM_V2_FMT, voltage, tof, front_value,
        flow_total % 65536, flow_total % 65536, 0, flags,
        0.0, 0.0, 0.0, 0.80,
    )
    return head + tail


def _free_udp_port() -> int:
    """A UDP port the OS says is free right now. / いま空いている UDP ポート。

    The port is chosen by binding :0 and reading back what the OS picked, then
    closing that probe. A real vehicle broadcasts to :5005 and nothing else, so
    a port chosen this way carries only what this test sends into it.
    :0 で bind して OS が選んだ番号を読み取り、その調べ用ソケットを閉じて得る。
    実機が放送するのは :5005 だけなので、こうして選んだポートには本試験が
    送ったものしか流れてこない。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("", 0))
        return probe.getsockname()[1]


@pytest.fixture
def link():
    """A RealLink listening on a port of this test's own, or the test is skipped.

    NOT :5005, deliberately. Nothing else holds that port, so binding it
    succeeds even with a vehicle on the same network -- and the vehicle's own
    50Hz broadcast is then delivered here alongside the synthetic packets
    below, which made two to five of these tests fail at random whenever the
    Mac was on the craft's WiFi (measured 2026-09-19: 140-byte packets from
    192.168.10.1 arriving throughout the run). The port is the only thing
    that separates the two streams.

    本試験専用のポートで受ける RealLink。取れなければ試験を飛ばす。

    :5005 は**あえて**使わない。そのポートは他の誰も握っていないので、同じ網に
    機体が居ても bind は成功し、機体自身の 50Hz 放送が下の合成パケットと混ざって
    ここへ届く。そのため Mac が機体の WiFi につながっていると、本ファイルの試験が
    実行のたびに 2〜5 件、無作為に落ちていた（2026-09-19 実測: 192.168.10.1 からの
    140 バイトのパケットが実行中ずっと届いていた）。2 つの流れを分けられるのは
    ポートだけである。

    Skipped rather than failed when the ports cannot be bound: another session
    or a djitellopy script legitimately holds :8890, and that is not a defect
    in this code.
    ポートを bind できないときは失敗ではなく skip とする。別のセッションや
    djitellopy のスクリプトが :8890 を正当に握っている場合があり、本コードの
    不具合ではないためである。
    """
    port = _free_udp_port()
    # BOTH ports, because a Sample is built from both streams: leaving the
    # state port at :8890 lets the vehicle's own 10 Hz string be folded into
    # a Sample this test is about to assert is empty (measured 2026-09-20).
    # 両方のポートを与える。Sample は両方の流れから組まれるので、状態ポートを
    # :8890 のままにすると、機体自身の 10Hz 文字列が、この試験が「空のはず」と
    # 確かめようとしている Sample に併合される（2026-09-20 実測）。
    state_port = _free_udp_port()
    try:
        real_link = RealLink("127.0.0.1", telem_port=port, state_port=state_port)
    except OSError as exc:
        pytest.skip(f"cannot bind the telemetry ports: {exc}")
    if real_link._telem_sock is None:
        real_link.close()
        pytest.skip(f"UDP:{port} is already in use")
    yield real_link
    real_link.close()


def _deliver(link, packets, timeout_s: float = 2.0) -> list:
    """Send packets to the link's own port and return what read_samples() makes
    of them.
    パケットをリンク自身のポートへ送り、read_samples() の結果を返す。"""
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for packet in packets:
            sender.sendto(packet, ("127.0.0.1", link.telem_port))
    finally:
        sender.close()

    # The receiver runs on its own thread; poll rather than sleep a fixed
    # time, so the test is neither flaky nor slower than it has to be.
    # 受信は別スレッドで走る。固定時間待たずに待ち合わせる。不安定にも、
    # 必要以上に遅くもしないためである。
    deadline = time.monotonic() + timeout_s
    collected: list = []
    while time.monotonic() < deadline and len(collected) < len(packets):
        collected.extend(link.read_samples())
        time.sleep(0.01)
    return collected


def test_a_v2_packet_supplies_position_velocity_battery_and_tof(link):
    """One 140-byte packet carries everything the Monitor needs, alone.
    140 バイトのパケット 1 つで Monitor に必要な情報が揃うこと。"""
    samples = _deliver(link, [_v2_packet(voltage=3.75, tof=0.79)])

    assert len(samples) == 1
    sample = samples[0]
    # NED position is down-positive; altitude is up-positive.
    # NED の位置は下向き正、高度は上向き正。
    assert sample["altitude_m"] == pytest.approx(0.80, abs=1e-5)
    assert sample["pos_n"] == pytest.approx(0.30, abs=1e-5)
    assert sample["pos_e"] == pytest.approx(-0.40, abs=1e-5)
    assert sample["vel_n"] == pytest.approx(0.12, abs=1e-5)
    assert sample["tof_m"] == pytest.approx(0.79, abs=1e-5)
    assert sample["battery_pct"] == pytest.approx(50.0, abs=0.5)
    assert sample["flight_state"] == "FLYING"


def test_a_v1_packet_omits_battery_and_tof_rather_than_sending_zero(link):
    """104-byte firmware reports no battery and no ToF, not 0% and 0 m.

    A zero would read as a flat battery and a craft on the ground, either
    of which would trigger an immediate safety rule on a healthy flight.
    104 バイトのファームでは電池と ToF を「無し」とすること（0%・0m ではない）。

    0 は「電池切れ」「接地」として読まれ、健全な飛行で即時安全則を作動させて
    しまう。
    """
    samples = _deliver(link, [_v1_packet()])

    assert len(samples) == 1
    assert samples[0]["altitude_m"] == pytest.approx(0.80, abs=1e-5)
    assert "battery_pct" not in samples[0]
    assert "tof_m" not in samples[0]


def test_an_invalid_flag_suppresses_the_value_it_guards(link):
    """v2 fields whose validity bit is clear are omitted, not trusted.
    有効ビットが立っていない v2 の項目は採用せず省くこと。"""
    samples = _deliver(link, [_v2_packet(valid=False)])

    assert len(samples) == 1
    assert "tof_m" not in samples[0]
    assert "battery_pct" not in samples[0]


def test_a_driven_front_tof_arrives_under_its_own_key(link):
    """The forward distance reaches the Sample as tof_front_m, beside tof_m.

    Nothing decides on it yet — it is carried so the monitor and the recording
    can show it (docs/plans/jev-autopilot.md P2b).
    前方距離が tof_m とは別の tof_front_m というキーで Sample に届くこと。

    まだ何の判断にも使わない。監視と記録が表示できるように運ぶだけである
    （docs/plans/jev-autopilot.md P2b）。
    """
    samples = _deliver(link, [_v2_packet(tof=0.79, tof_front=1.35)])

    assert len(samples) == 1
    assert samples[0]["tof_front_m"] == pytest.approx(1.35, abs=1e-5)
    # The downward reading is untouched by the forward one.
    # 下向きの値は前方の値に影響されない。
    assert samples[0]["tof_m"] == pytest.approx(0.79, abs=1e-5)


def test_an_absent_front_tof_omits_the_key_rather_than_reporting_minus_one(link):
    """No forward reading means no tof_front_m key — not a -1 m obstacle.

    This is the usual state: the sensor is optional and needs battery power, so
    on USB alone it never starts. A -1.0 left in the Sample would read as an
    obstacle 1 m BEHIND the craft to anything that later uses the value.
    前方の測定値が無ければ tof_front_m というキー自体を持たないこと（-1m の
    障害物ではない）。

    これが通常の状態である: センサは任意でバッテリー電源を要し、USB のみでは
    起動しない。-1.0 を Sample に残すと、後でこの値を使う側には「機体の 1m
    後方に障害物」と読まれてしまう。
    """
    samples = _deliver(link, [_v2_packet(tof_front=None)])

    assert len(samples) == 1
    assert "tof_front_m" not in samples[0]
    # The downward ToF still arrives — an absent front sensor hides nothing else.
    # 下向き ToF は届く — 前方が無くても他を隠さない。
    assert samples[0]["tof_m"] == pytest.approx(0.79, abs=1e-5)


def test_every_packet_since_the_last_call_is_returned(link):
    """The whole 50Hz stream reaches the Monitor, not just the newest packet.

    The trend rules measure a duration, so they need the intervening
    samples; keeping only the latest would erase the history they read.
    最新の 1 件ではなく 50Hz の流れ全体が Monitor に届くこと。

    傾向の判定は継続時間を測るため間のサンプルを必要とする。最新だけを保持
    すると、その履歴が消えてしまう。
    """
    samples = _deliver(link, [_v2_packet() for _ in range(5)])

    assert len(samples) == 5


def test_flow_reaches_the_sample_as_a_movement_not_as_the_wire_total(link):
    """The Sample carries the movement since the previous packet, and the first
    packet — having nothing to difference against — carries no flow key at all.

    The wire total is meaningless alone (it is only known modulo 2^16), so
    passing it through unchanged would put a number in the Sample that no
    reader could interpret.

    Sample が運ぶのは前回パケットからの移動量であること。最初のパケットは差を
    取る相手が無いので、フローのキーを持たないこと。

    電文の累計は単独では意味を持たない（2^16 を法としてしか分からない）ので、
    そのまま通せば、どの読み手も解釈できない数を Sample に置くことになる。
    """
    samples = _deliver(link, [
        _v2_packet(flow_total=65530, t_us=1000),
        _v2_packet(flow_total=10, t_us=21000),     # +16 across the wrap / 折り返し
    ])

    assert len(samples) == 2
    assert "flow_dx" not in samples[0]
    assert samples[1]["flow_dx"] == 16
    assert samples[1]["flow_dy"] == 16


def test_a_v1_packet_carries_no_flow_key(link):
    """Old firmware sends no flow field, so the Sample must not invent one.
    旧ファームはフロー項目を送らないので、Sample が値を作り出さないこと。"""
    samples = _deliver(link, [_v1_packet()])

    assert len(samples) == 1
    assert "flow_dx" not in samples[0]


def test_a_packet_that_is_not_telemetry_is_ignored(link):
    """Stray datagrams on the telemetry port do not become Samples.
    テレメトリのポートに紛れ込んだ datagram が Sample にならないこと。"""
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sender.sendto(b"not a telemetry packet at all", ("127.0.0.1", link.telem_port))
        sender.sendto(b"\x00" * TELEM_SIZE, ("127.0.0.1", link.telem_port))  # bad magic
    finally:
        sender.close()

    time.sleep(0.3)
    assert link.read_samples() == []


def test_packet_sizes_match_the_shared_format():
    """The packets this test builds are the sizes the decoder dispatches on.
    本試験が組むパケットが、復号器が判別に使う大きさと一致すること。"""
    assert len(_v1_packet()) == TELEM_SIZE
    assert len(_v2_packet()) == TELEM_SIZE_V2
