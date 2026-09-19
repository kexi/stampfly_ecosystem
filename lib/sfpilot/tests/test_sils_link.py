"""
What SilsLink guarantees: the emulator's STATE line becomes a Sample, and a
command becomes exactly one `api <line>` on stdin.

SilsLink が保証すること: エミュレータの STATE 行が Sample になること、指令が
stdin 上でちょうど 1 行の `api <行>` になること。

No emulator is started here. SilsLink talks to a `proc` through two
attributes only (`stdout` to iterate, `stdin` to write), so a pair of fake
streams exercises the whole class -- which is the point of the seam.
ここではエミュレータを起動しない。SilsLink が `proc` に触れるのは 2 つの属性
だけ（反復する `stdout` と書き込む `stdin`）なので、偽のストリーム 2 本で
クラス全体を動かせる。この継ぎ目はそのためにある。
"""

import io
import math
import time

import pytest

from sfpilot.link import SilsLink, battery_percent, sample_from_state_line

# A STATE line as simulator/sils/emu/emu_main.cpp prints it today, captured
# from a real run rather than hand-written, so a change to the emulator's
# format shows up here as a failing test.
# 現在の simulator/sils/emu/emu_main.cpp が出力する STATE 行。手で書かず実行から
# 採取したもの。エミュレータ側の書式が変わればこの試験が落ちて気づける。
# The yaw was re-captured on 2026-09-19: it used to read 90.00, which was the
# fingerprint of the SILS start-attitude bug (the MuJoCo identity quaternion is
# level but faces EAST, NED yaw=+90 deg). With the start attitude corrected to
# level-and-NORTH the emulator hovers at yaw~0, so a captured line no longer
# carries 90.00. Only the value changed; the parser reads keys by name.
# yaw は 2026-09-19 に再採取した。以前は 90.00 で、これは SILS 初期姿勢の不具合
# （MuJoCo の単位クォータニオンは水平だが東向き＝NED yaw=+90°）の痕跡だった。
# 初期姿勢を「水平かつ北向き」に直したのでエミュレータは yaw≒0 でホバーし、
# 採取した行に 90.00 は現れない。変わったのは値だけで、解釈は名前で行う。
STATE_LINE = (
    "STATE t=18.104 alt=0.484 roll=1.50 pitch=-2.25 yaw=0.00 "
    "mode=FLYING:POS_HOLD* vbatt=3.82 x=0.100 y=-0.200 "
    "vx=0.010 vy=0.020 vz=0.003 tof=0.484 tof_valid=1 batt=57.3"
)

# The same line as it was BEFORE the P2 tail was appended. Parsing must still
# succeed on it: the keys are read by name, never by position.
# P2 の追記より前の同じ行。名前で読み位置で読まないため、これも解釈できること。
STATE_LINE_V1 = (
    "STATE t=1.000 alt=0.500 roll=0.00 pitch=0.00 yaw=0.00 "
    "mode=FLYING:POS_HOLD* vbatt=3.80"
)


class _FakeStdin:
    """Collects written lines, and refuses writes once closed.

    Keeps the record outside the buffer so it survives close(): the link
    closes stdin on the way out, and the test still has to read back what
    was sent before that.
    書き込まれた行を集め、閉じた後の書き込みは拒む。

    記録はバッファの外に持つ。リンクは終了時に stdin を閉じるが、試験はその
    前に送られた内容を読み返す必要があるためである。
    """

    def __init__(self):
        self.lines: list = []
        self.closed = False

    def write(self, text: str) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file")
        self.lines.extend(line for line in text.split("\n") if line)

    def flush(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file")

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    """Stands in for a Popen: an stdout to read, an stdin to collect writes.
    Popen の代役: 読む stdout と、書き込みを集める stdin。"""

    def __init__(self, lines=()):
        self.stdout = io.StringIO("".join(f"{line}\n" for line in lines))
        self.stdin = _FakeStdin()

    def writes(self) -> list:
        return list(self.stdin.lines)


def _settled_link(proc, timeout_s: float = 2.0) -> SilsLink:
    """A link whose reader has consumed the canned stdout.
    読み取りスレッドが用意した stdout を読み終えたリンクを返す。"""
    link = SilsLink(proc)
    deadline = time.monotonic() + timeout_s
    while link._reader.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    return link


# =============================================================================
# Parsing the STATE line / STATE 行の解釈
# =============================================================================

def test_state_line_becomes_a_sample_in_si_units():
    """Every STATE field lands on its Sample key, converted to SI.
    STATE の各項目が SI 単位で対応する Sample のキーに入ること。"""
    sample = sample_from_state_line(STATE_LINE, ts=7.0)

    assert sample["t"] == 7.0          # the link's clock, not the emulator's
    assert sample["altitude_m"] == pytest.approx(0.484)
    assert sample["pos_n"] == pytest.approx(0.100)
    assert sample["pos_e"] == pytest.approx(-0.200)
    assert sample["vel_n"] == pytest.approx(0.010)
    assert sample["vel_e"] == pytest.approx(0.020)
    assert sample["vel_d"] == pytest.approx(0.003)
    assert sample["tof_m"] == pytest.approx(0.484)
    assert sample["battery_pct"] == pytest.approx(57.3)
    # Attitude is printed in degrees and every threshold is in radians.
    # 姿勢は度で出力され、しきい値はラジアン。
    assert sample["roll"] == pytest.approx(math.radians(1.50))
    assert sample["pitch"] == pytest.approx(math.radians(-2.25))
    assert sample["yaw"] == pytest.approx(math.radians(0.0))


def test_flight_state_drops_the_sub_mode_and_armed_marker():
    """"FLYING:POS_HOLD*" is reported as the state alone.
    「FLYING:POS_HOLD*」は状態だけに落として報告されること。"""
    assert sample_from_state_line(STATE_LINE, 0.0)["flight_state"] == "FLYING"


def test_a_line_without_the_p2_tail_still_parses():
    """An older emulator's shorter line yields a Sample, minus the new keys.
    追記前の短い行でも Sample になり、新しいキーだけが欠けること。"""
    sample = sample_from_state_line(STATE_LINE_V1, 0.0)

    assert sample["altitude_m"] == pytest.approx(0.500)
    for absent in ("pos_n", "pos_e", "vel_n", "tof_m", "battery_pct"):
        assert absent not in sample, f"{absent} must be absent, not zero"


def test_an_invalid_tof_is_omitted_rather_than_reported_as_a_distance():
    """tof_valid=0 means no ToF reading at all, not a -1 m reading.

    The emulator prints -1.0 for an invalid ToF, which would pass as a
    number. Validity is a separate field precisely so the value is never
    second-guessed.
    tof_valid=0 は「ToF の測定値が無い」であって「-1m の測定値」ではないこと。

    エミュレータは無効時に -1.0 を出力し、これは数値として通ってしまう。値を
    推測しないよう有効性は別項目になっている。
    """
    line = STATE_LINE.replace("tof=0.484 tof_valid=1", "tof=-1.000 tof_valid=0")

    assert "tof_m" not in sample_from_state_line(line, 0.0)


def test_a_line_carrying_nothing_usable_is_rejected():
    """A bare or malformed STATE line yields None, not an empty Sample.
    中身の無い・壊れた STATE 行は空の Sample ではなく None になること。"""
    assert sample_from_state_line("STATE", 0.0) is None
    assert sample_from_state_line("STATE garbage nonsense", 0.0) is None


def test_unknown_keys_are_ignored_so_the_tail_can_grow_again():
    """A future key appended by the emulator does not break the parse.
    将来エミュレータが末尾に足すキーで解釈が壊れないこと。"""
    sample = sample_from_state_line(STATE_LINE + " brand_new_key=42.0", 0.0)

    assert sample["altitude_m"] == pytest.approx(0.484)
    assert "brand_new_key" not in sample


def test_battery_percent_matches_the_emulators_own_mapping():
    """The Python and C++ voltage->percent mappings agree at both ends.

    emu_main.cpp hard-codes the same two constants; they cannot share code
    across the language boundary, so this pins the agreement.
    Python と C++ の電圧→百分率が両端で一致すること。emu_main.cpp が同じ 2 つの
    定数を持つ。言語を跨いでコードは共有できないため、一致をここで固定する。
    """
    assert battery_percent(4.2) == pytest.approx(100.0)
    assert battery_percent(3.3) == pytest.approx(0.0)
    assert battery_percent(3.75) == pytest.approx(50.0)
    # Outside the modelled range the answer is clamped, never negative.
    # 想定範囲の外では丸める。負にはしない。
    assert battery_percent(2.0) == pytest.approx(0.0)
    assert battery_percent(5.0) == pytest.approx(100.0)


# =============================================================================
# Reading through the link / リンク経由の読み取り
# =============================================================================

def test_read_samples_returns_every_state_line_and_drains():
    """All STATE lines since the last call are returned, once each.
    前回以降の STATE 行が全件、1 回ずつ返ること。"""
    proc = _FakeProc([STATE_LINE, "[INFO] not a state line", STATE_LINE])
    link = _settled_link(proc)

    first = link.read_samples()
    assert len(first) == 2
    # A second call must not hand back the same samples again.
    # 2 回目の呼び出しで同じサンプルが再び返らないこと。
    assert link.read_samples() == []


def test_non_state_output_is_kept_as_a_bounded_log_tail():
    """Firmware log lines are retained for a post-mortem, not discarded.
    ファームのログ行は事後診断用に保持され、捨てられないこと。"""
    proc = _FakeProc(["[INFO] ApiTask started", STATE_LINE, "[WARN] something"])
    link = _settled_link(proc)

    assert "[INFO] ApiTask started" in link.log_tail
    assert "[WARN] something" in link.log_tail
    assert all(not line.startswith("STATE ") for line in link.log_tail)


# =============================================================================
# Writing through the link / リンク経由の書き込み
# =============================================================================

def test_send_rc_is_written_as_an_api_velocity_command():
    """`rc` goes out as `api rc ...`, never as a bare transmitter `rc`.

    A bare `rc` line is the TRANSMITTER's sticks in raw ADC (0..4095); the
    API's is a velocity in -100..100. Writing the wrong one would feed a
    velocity into the stick channel.
    `rc` は `api rc ...` として出ること。素の `rc` にならないこと。

    素の `rc` 行は「送信機」のスティック（ADC 生値 0..4095）であり、API のそれは
    -100..100 の速度である。取り違えると速度がスティック側へ流れてしまう。
    """
    link = _settled_link(_FakeProc())

    link.send_rc(0, 20, 0, 0)

    assert "api rc 0 20 0 0" in link.proc.writes()


def test_takeoff_enters_sdk_mode_before_taking_off():
    """`command` precedes `takeoff`: the firmware gates every other verb.
    `command` が `takeoff` に先立つこと。ファームは他の指令をその後ろに置く。"""
    link = _settled_link(_FakeProc())

    link.takeoff()

    api_lines = [w for w in link.proc.writes() if w.startswith("api ")]
    assert api_lines[:2] == ["api command", "api takeoff"]


def test_commands_are_recorded_without_the_api_prefix():
    """`sent` holds what was asked for, the pipe holds how it was sent.
    `sent` は依頼された内容を、パイプは送られ方を保持すること。"""
    link = _settled_link(_FakeProc())

    link.send_command("land")

    assert "land" in link.sent
    assert "api land" in link.proc.writes()


def test_neutral_sticks_are_rate_limited():
    """Holding the sticks neutral does not write one line per call.

    The pilot loop calls this at 50Hz while the emulator re-injects on its
    own anyway; an unlimited write would fill the pipe with lines that
    change nothing.
    中立保持が呼び出しごとに 1 行書かないこと。

    操縦ループは 50Hz でこれを呼ぶが、エミュレータ側も自前で再注入する。
    制限しないと、何も変えない行でパイプが埋まる。
    """
    link = _settled_link(_FakeProc())

    for _ in range(50):
        link.hold_sticks_neutral()

    stick_lines = [w for w in link.proc.writes() if w.startswith("rc ")]
    assert len(stick_lines) < 50


def test_neutral_sticks_are_centred():
    """The held sticks are the ADC centre, i.e. a parked transmitter.

    A parked stick does not cancel API guidance; a MOVING one does
    (INV-2). The whole SILS flight depends on this staying centred.
    保持するスティックが ADC の中央であること（＝置いたままの送信機）。

    置いたままのスティックは API 誘導を解除しないが、「動いた」スティックは
    解除する（INV-2）。SILS 飛行全体がこれに依存している。
    """
    link = _settled_link(_FakeProc())

    link.hold_sticks_neutral()

    assert f"rc {SilsLink.STICK_CENTRE} {SilsLink.STICK_CENTRE} " \
           f"{SilsLink.STICK_CENTRE} {SilsLink.STICK_CENTRE}" in link.proc.writes()


def test_close_asks_the_emulator_to_quit():
    """Closing sends `quit` so the emulator shuts down cleanly.
    close が `quit` を送り、エミュレータが綺麗に終了できるようにすること。"""
    link = _settled_link(_FakeProc())

    link.close()

    assert "quit" in link.proc.writes()


def test_close_is_safe_to_call_twice():
    """A second close neither raises nor sends a second `quit`.
    2 回目の close が例外にならず、`quit` も 2 度送らないこと。"""
    link = _settled_link(_FakeProc())

    link.close()
    link.close()

    assert link.proc.writes().count("quit") == 1


def test_a_broken_pipe_ends_the_flight_instead_of_raising():
    """A dead emulator must not raise into the 50Hz loop.

    The loop's own contract is that nothing below it throws; a write to a
    closed pipe is the most likely way that would happen.
    死んだエミュレータが 50Hz ループへ例外を投げないこと。

    ループの契約は「下の層は例外を投げない」であり、閉じたパイプへの書き込みは
    それが起きる最もありそうな経路である。
    """
    link = _settled_link(_FakeProc())
    link.proc.stdin.close()

    link.send_command("land")   # must not raise / 例外にならないこと
    assert "land" in link.sent, "the attempt is still recorded"


# =============================================================================
# Outstanding replies / 未応答の指令
# =============================================================================
#
# A blocking verb answers only when its move is REACHED, so callers wait on
# the reply. The link has to say more than HOW MANY replies arrived: it has
# to say whether one is still owed. Counting alone let a reply that arrived
# late -- after the NEXT command had gone out -- satisfy that command's
# wait instantly, which is how a mission leg was declared finished the
# moment it started (say.StepRunner, measured 2026-09-19).
#
# ブロックする verb は移動の**到達時**にのみ応答するので、呼び出し側は応答を
# 待つ。リンクは「応答が何件届いたか」以上のことを言えなければならない。
# 「まだ返っていない応答があるか」である。個数だけでは、遅れて届いた応答 ——
# **次の**指令を送った後に届いたもの —— がその指令の待ちを即座に満たしてしまう。
# ミッションの区間が始まった瞬間に「終わった」と宣言されたのはこれである
# （say.StepRunner。2026-09-19 実測）。

def test_a_command_that_expects_a_reply_is_outstanding_until_it_arrives():
    """`replies_outstanding` tracks the answer a blocking verb still owes.
    ブロックする verb がまだ返していない応答を `replies_outstanding` が追うこと。"""
    link = _settled_link(_FakeProc())
    assert link.replies_outstanding == 0

    link.send_command("forward 60")
    assert link.replies_outstanding == 1, "the vehicle has not answered yet"

    link._replies += 1                     # the reply arrives / 応答が届く
    assert link.replies_outstanding == 0


def test_rc_never_counts_as_outstanding():
    """`rc` is fire-and-forget, so it is never waited for.

    `rc` is sent at 20Hz and the neutral sticks at 50Hz. If either counted,
    a flight would look as though hundreds of moves were permanently
    unanswered and every wait would run to its ceiling.

    `rc` は応答待ちをしないので、待つ対象にならないこと。

    `rc` は 20Hz、中立スティックは 50Hz で送られる。どちらかを数えれば、飛行は
    数百件の移動が永久に未応答であるかのように見え、あらゆる待ちが上限まで
    走ってしまう。
    """
    link = _settled_link(_FakeProc())

    for _ in range(50):
        link.send_rc(0, 0, 0, 0)
    link.hold_sticks_neutral()

    assert link.replies_outstanding == 0


def test_a_late_reply_cannot_be_credited_to_the_next_command():
    """The reply owed by one command does not satisfy the next one.

    This is the mission fault in miniature: `takeoff`'s reply arrived 0.7 s
    after `forward 60` had been sent, so a wait that only watched the reply
    COUNT ended immediately and the leg was judged to have stopped short of
    a target it had not begun flying towards.

    ある指令が負っている応答が、次の指令の待ちを満たさないこと。

    ミッションの不具合を小さくしたものである。`takeoff` の応答が `forward 60`
    送信の 0.7 秒**後**に届いたため、応答の**個数**だけを見る待ちは即座に終わり、
    区間は「向かってもいない終点の手前で止まった」と判定された。
    """
    link = _settled_link(_FakeProc())
    link.send_command("takeoff")

    # The next command goes out while the first is still unanswered.
    # 最初の指令が未応答のまま、次の指令が出る。
    link.send_command("forward 60")
    assert link.replies_outstanding == 2

    # `takeoff` finally answers. `forward 60` is still owed one.
    # ここで `takeoff` がようやく応答する。`forward 60` の応答はまだである。
    link._replies += 1
    assert link.replies_outstanding == 1, (
        "the late reply belongs to takeoff, not to the move now in flight"
    )
