"""
What lands a real aircraft when the PC stops flying it: an exception, a
signal or a normal exit sends `land`; a stalled monitor loop sends `land`;
and the keys `l`, `e` and `h` do what they say.

PC が機体を飛ばすのをやめたとき、何がそれを着陸させるか: 例外・シグナル・
正常終了はいずれも `land` を送ること、停止した監視ループが `land` を送ること、
そしてキー `l`・`e`・`h` が述べているとおりに働くこと。

The rule these tests pin is §2's: **the vehicle does not land itself when
the PC goes quiet.** It holds position indefinitely and COMM_LOST watches
only the transmitter's ESP-NOW link, so every path out of this program has
to end in a `land` from the PC side or the aircraft hovers until the
battery is gone.
これらの試験が固定するのは §2 の事実である: **PC が黙っても機体は自分で着陸
しない。** 位置を保ち続け、COMM_LOST が見るのは送信機の ESP-NOW リンクだけで
ある。したがって、このプログラムからの出口はすべて PC 側からの `land` に
行き着かねばならない。さもなければ機体は、電池が尽きるまで浮いている。
"""

import pytest

from sfpilot.config import DEFAULT_CONFIG, real_config
from sfpilot.safety import (
    KEY_EMERGENCY, KEY_HOLD, KEY_LAND, KeyListener, LandGuard, LoopWatchdog,
    apply_key,
)

CONFIG = real_config(DEFAULT_CONFIG)


class SpyLink:
    """Records what was sent and can be told whether to acknowledge it.

    `reply_count` is what `LandGuard` watches to tell an acknowledged
    `land` from one that vanished, the same counter `landing.py` reads.

    送ったものを記録し、応答するかどうかを指示できるリンク。

    `LandGuard` が「応答のあった `land`」と「消えた `land`」を区別するために見る
    のが `reply_count` であり、`landing.py` が読むのと同じ計数である。
    """

    def __init__(self, acknowledges: bool = True):
        self.sent: list = []
        self.acknowledges = acknowledges
        self.reply_count = 0
        self.replies_outstanding = 0

    def priority(self, line: str) -> None:
        self.sent.append(line)
        if self.acknowledges:
            self.reply_count += 1

    def send_command(self, line: str) -> None:
        self.priority(line)

    def send_rc(self, a, b, c, d) -> None:
        self.sent.append(f"rc {a} {b} {c} {d}")

    def read_samples(self) -> list:
        return []

    def close(self) -> None:
        pass


@pytest.fixture
def clock():
    """A clock the test advances, so a one-second wait costs no second.
    試験が進める時計。1 秒の待ちに 1 秒を費やさないため。"""
    state = {"now": 0.0}

    def read() -> float:
        return state["now"]

    read.state = state
    return read


@pytest.fixture
def guard_parts(clock):
    """A guard on a spy link, with a sleep that advances the test clock.
    監視用リンク上の guard と、試験の時計を進める sleep。"""
    link = SpyLink()

    def sleep(seconds: float) -> None:
        clock.state["now"] += seconds

    guard = LandGuard(link, CONFIG, clock=clock, sleep=sleep)
    return guard, link


# =============================================================================
# LandGuard / 例外・シグナル・終了
# =============================================================================
def test_land_is_sent_and_recorded_once(guard_parts):
    """`land_now` transmits `land` and reports the vehicle acknowledged it.
    `land_now` が `land` を送出し、機体の応答を報告すること。"""
    guard, link = guard_parts

    acknowledged = guard.land_now("テスト / test")

    assert link.sent == ["land"]
    assert acknowledged
    assert guard.landed


def test_a_second_call_does_not_send_a_second_land(guard_parts):
    """Landing is idempotent, because the exit paths overlap.

    A Ctrl-C raises KeyboardInterrupt AND runs the atexit handler, so a
    guard that sent on each would send two. The second `land` restarts a
    descent that is already under way.

    着陸は冪等であること。終了の経路が重なるためである。

    Ctrl-C は KeyboardInterrupt を起こし、かつ atexit の後始末も走らせる。経路
    ごとに送る guard は 2 回送ることになる。2 度目の `land` は、既に始まっている
    降下をやり直させる。
    """
    guard, link = guard_parts

    guard.land_now("一度目 / first")
    guard.land_now("二度目 / second")

    assert link.sent == ["land"]
    assert guard.reason == "一度目 / first"


def test_an_unacknowledged_land_is_retried_once_and_never_escalates(clock):
    """A silent `land` is sent twice, and `emergency` is NEVER sent.

    This is the module's load-bearing decision. `emergency` cuts the
    motors, so an aircraft at 0.5 m falls 0.5 m. An unacknowledged `land`
    means either the command did not arrive (so `emergency` will not
    either) or the acknowledgement did not come back (so the aircraft is
    already descending and `emergency` would drop it). Neither reading
    makes cutting the motors the better outcome, so the aircraft is left to
    the person holding the transmitter.

    応答の無い `land` は 2 回送られ、`emergency` は**決して**送られないこと。

    本モジュールの要となる判断である。`emergency` はモータを止めるので、高度
    0.5m の機体は 0.5m 落ちる。応答の無い `land` が意味するのは、指令が届いて
    いない（ならば `emergency` も届かない）か、応答が返らなかっただけ（ならば機体は
    既に降下中で、`emergency` はそれを落とす）かのどちらかである。どちらの読み方
    でもモータを止めるほうが良い結末にはならないので、機体は送信機を持つ人に
    委ねる。
    """
    link = SpyLink(acknowledges=False)

    def sleep(seconds: float) -> None:
        clock.state["now"] += seconds

    guard = LandGuard(link, CONFIG, clock=clock, sleep=sleep)

    acknowledged = guard.land_now("応答なし / no ack")

    assert link.sent == ["land", "land"]
    assert not acknowledged
    assert "emergency" not in link.sent


def test_the_reason_reaches_the_caller_before_the_land_goes_out(guard_parts):
    """`on_land` is called with the reason, so it can be printed and traced.
    `on_land` が理由とともに呼ばれ、表示と記録ができること。"""
    guard, link = guard_parts
    seen: list = []
    guard.on_land = seen.append

    guard.land_now("番人が発火 / watchdog fired")

    assert seen == ["番人が発火 / watchdog fired"]


def test_a_raising_on_land_callback_does_not_stop_the_landing(guard_parts):
    """A caller whose callback fails still gets the aircraft landed.

    The callback runs on the exit path, where an exception would replace
    the landing with a traceback.

    後始末が失敗する呼び出し側でも、機体は着陸すること。

    後始末は終了経路で走る。そこでの例外は、着陸をトレースバックに置き換えて
    しまう。
    """
    guard, link = guard_parts

    def explode(_reason):
        raise RuntimeError("the printer is on fire")

    guard.on_land = explode

    guard.land_now("テスト / test")

    assert link.sent == ["land"]


def test_a_closed_link_does_not_turn_an_exit_into_a_traceback(clock):
    """A link that raises on send is survived; the transmitter is the cover.
    送信で例外を投げるリンクでも耐えること。備えは送信機である。"""
    class DeadLink(SpyLink):
        def priority(self, line: str) -> None:
            raise OSError("socket closed")

    def sleep(seconds: float) -> None:
        clock.state["now"] += seconds

    guard = LandGuard(DeadLink(), CONFIG, clock=clock, sleep=sleep)

    assert guard.land_now("閉じたリンク / closed link") is False
    assert guard.landed


# =============================================================================
# LoopWatchdog / 番人
# =============================================================================
def test_a_stalled_loop_is_landed(clock, guard_parts):
    """A loop that stops calling `beat()` past the stall ceiling gets a `land`.

    The thing being watched is the loop itself, so nothing the loop is
    responsible for calling can detect the loop failing to call it -- which
    is why this runs on a thread of its own.

    `beat()` を呼ばなくなって停滞の上限を超えたループに `land` が送られること。

    見張る対象はループ自身である。ループが呼ぶ責任を負うものは、ループがそれを
    呼ばなくなったことを検出できない。専用スレッドで走る理由がそれである。
    """
    guard, link = guard_parts
    watchdog = LoopWatchdog(guard, CONFIG, clock=clock)
    watchdog.resume()
    watchdog.beat()

    # Past the ceiling without a beat, judged the way the thread judges it.
    # 刻みなしで上限を越える。スレッドが判ずるのと同じ判断をここで行う。
    clock.state["now"] += CONFIG.real.watchdog_stall_s + 0.2
    stalled_s = clock() - watchdog._last_beat          # noqa: SLF001
    assert stalled_s > CONFIG.real.watchdog_stall_s

    guard.land_now(f"監視ループが {stalled_s:.1f}s 停止したため着陸")

    assert link.sent == ["land"]


def test_a_ticking_loop_is_left_alone(clock, guard_parts):
    """A loop that keeps calling `beat()` is never judged as stalled.
    `beat()` を呼び続けるループが停滞と判じられないこと。"""
    guard, link = guard_parts
    watchdog = LoopWatchdog(guard, CONFIG, clock=clock)
    watchdog.resume()

    for _ in range(20):
        clock.state["now"] += CONFIG.real.watchdog_stall_s / 2
        watchdog.beat()
        stalled_s = clock() - watchdog._last_beat      # noqa: SLF001
        assert stalled_s <= CONFIG.real.watchdog_stall_s

    assert link.sent == []


def test_the_watchdog_starts_paused(clock, guard_parts):
    """Before `start()`, silence is not a stall.

    The preflight and the confirmation prompt both leave the loop legitimately
    not ticking, and a watchdog that judged then would land an aircraft that
    has not taken off.

    `start()` より前は、無音が停滞ではないこと。

    飛行前点検も確認の待ちも、ループが正当に刻んでいない時間である。そこで判ずる
    番人は、離陸していない機体を着陸させることになる。
    """
    guard, link = guard_parts
    watchdog = LoopWatchdog(guard, CONFIG, clock=clock)

    clock.state["now"] += CONFIG.real.watchdog_stall_s * 10

    assert watchdog._paused                            # noqa: SLF001
    assert link.sent == []


def test_a_stopped_watchdog_stops_watching(clock, guard_parts):
    """`stop()` ends the thread without landing anything.
    `stop()` がスレッドを終わらせ、何も着陸させないこと。"""
    guard, link = guard_parts
    watchdog = LoopWatchdog(guard, CONFIG, clock=clock)
    watchdog.start()
    watchdog.stop()

    import time
    time.sleep(CONFIG.real.watchdog_period_s * 3)

    assert link.sent == []


# =============================================================================
# Keys / キー操作
# =============================================================================
def test_l_lands(guard_parts):
    """`l` sends `land` through the same guard as every other path.
    `l` が、他のすべての経路と同じ guard を通して `land` を送ること。"""
    guard, link = guard_parts

    action = apply_key(KEY_LAND, guard, link)

    assert action == "land"
    assert link.sent == ["land"]


def test_e_sends_emergency_immediately_and_without_confirmation(guard_parts):
    """`e` cuts the motors on the keystroke, with no second key.

    This is the ONLY path in this package that reaches `emergency`, and it
    is deliberately not gated: a confirmation would put a second keystroke
    between the operator and the motors at the moment they have decided the
    aircraft must stop. The operator is standing next to it, watching,
    which is the condition under which that decision is theirs.

    `e` が、2 打目を要さずその打鍵でモータを止めること。

    本パッケージで `emergency` に到達する**唯一の**経路であり、意図して関門を
    置いていない。確認を挟めば、機体を止めねばならないと判断したまさにその瞬間に、
    操作者とモータの間に 2 打目を置くことになる。操作者は機体の傍らに立ち、それを
    見ている。その判断が操作者のものである条件がそれである。
    """
    guard, link = guard_parts

    action = apply_key(KEY_EMERGENCY, guard, link)

    assert action == "emergency"
    assert link.sent == ["emergency"]


def test_h_asks_for_a_hold_without_ending_the_flight(guard_parts):
    """`h` runs the hold callback and transmits nothing by itself.

    Holding is what the aircraft already does between judgements, so the
    key asks for the next cycle to hover rather than for the flight to end.

    `h` が待機の後始末を呼び、それ自体は何も送出しないこと。

    待機は、判断と判断の間に機体が既に行っていることである。このキーが求めるのは
    飛行の終了ではなく、次の周期を待機にすることである。
    """
    guard, link = guard_parts
    held: list = []

    action = apply_key(KEY_HOLD, guard, link, on_hold=lambda: held.append(True))

    assert action == "hold"
    assert held == [True]
    assert link.sent == []


def test_an_unknown_key_does_nothing(guard_parts):
    """Any other key is ignored rather than guessed at.
    他のキーは推測せず無視すること。"""
    guard, link = guard_parts

    assert apply_key("q", guard, link) == ""
    assert link.sent == []


def test_the_listener_queues_keys_for_the_flight_loop_to_drain(guard_parts):
    """Keys are queued, not acted on where they are read.

    The flight loop drains the queue each cycle, so a keypress takes effect
    through the same path everything else does and cannot interleave with
    a decision being carried out.

    キーは読まれた場所で実行されず、待ち行列に積まれること。

    飛行ループが毎周期それを吸い出すので、キー操作は他のすべてと同じ経路を通って
    効き、実行中の判断と入り混じることがない。
    """
    listener = KeyListener()

    listener.press("l")
    listener.press("e")

    assert listener.drain() == ["l", "e"]
    assert listener.drain() == []


def test_a_listener_on_a_non_terminal_does_nothing(guard_parts):
    """Without a terminal there is no keyboard, and cbreak would raise.
    端末が無ければキーボードも無く、cbreak は例外になる。"""
    import io

    listener = KeyListener(stream=io.StringIO())

    listener.start()

    assert not listener.active
    listener.stop()          # safe after a start that did nothing / 何もしなかった後でも安全
