"""
sfpilot.safety - what lands a real aircraft when the PC stops flying it.
sfpilot.safety - PC が飛ばすのをやめたとき、実機を着陸させるもの。

§2 records the fact this module exists for: **the vehicle does not land
itself when the PC goes quiet.** It holds position indefinitely, and
COMM_LOST watches only the transmitter's ESP-NOW link
(`sf_failsafe/failsafe.cpp`). So every way this program can stop flying
the aircraft — an exception, Ctrl-C, SIGTERM, a monitor loop that hangs —
has to end in a `land` sent from here, or the aircraft simply hovers until
the battery runs out.

§2 は、本モジュールが存在する理由そのものを記録している: **PC が黙っても機体は
自分で着陸しない。** 位置を保ち続け、COMM_LOST が見ているのは送信機の ESP-NOW
リンクだけである（`sf_failsafe/failsafe.cpp`）。したがって、このプログラムが
機体を飛ばすのをやめうる経路 —— 例外・Ctrl-C・SIGTERM・止まった監視ループ ——
のすべてが、ここから送る `land` に行き着かねばならない。さもなければ機体は、
電池が尽きるまでただ浮いている。

Three pieces, all of which end in the same `land`:

  - `LandGuard`     — exceptions, Ctrl-C and SIGTERM
  - `LoopWatchdog`  — a monitor loop that stopped ticking
  - `KeyListener`   — `l` / `e` / `h` from the keyboard

3 つの部品。いずれも同じ `land` に行き着く:

  - `LandGuard`     — 例外・Ctrl-C・SIGTERM
  - `LoopWatchdog`  — 刻まなくなった監視ループ
  - `KeyListener`   — キーボードからの `l` / `e` / `h`

**Why `emergency` is not the fallback when `land` goes unanswered.**
`emergency` cuts the motors, so an aircraft at 0.5 m falls 0.5 m. A `land`
that was not acknowledged has two possible causes and they call for
opposite actions: the command did not arrive (in which case `emergency`
will not arrive either, and sending it changes nothing), or the
acknowledgement did not come back (in which case the aircraft is already
descending, and `emergency` drops it from whatever height it has reached).
There is no reading of the situation in which cutting the motors is the
better outcome. So `land` is sent once more and, if that is also
unanswered, the aircraft is left to the person holding the transmitter —
who can take it with a stick movement at any time (INV-2), which is the
whole reason the checklist requires them to be holding it. `emergency`
stays where it has always been in this package: reachable by a person, and
by nothing else. The operator can still send it by pressing `e`.

**`land` に応答が無いとき `emergency` を退避先にしない理由。**
`emergency` はモータを止めるので、高度 0.5m の機体は 0.5m 落ちる。応答の無い
`land` には 2 つの原因がありえ、それぞれが**正反対の**行動を求める。指令が届いて
いない場合（そのときは `emergency` も届かず、送っても何も変わらない）か、応答が
返ってこなかっただけの場合（そのときは機体は既に降下中であり、`emergency` は
到達した高さから機体を落とす）である。モータを止めるほうが良い結末になる読み方は
存在しない。そこで `land` をもう 1 度だけ送り、それにも応答が無ければ、機体は
送信機を持っている人に委ねる —— その人はいつでもスティックの動きで奪える
（INV-2）。確認事項が送信機を手に持つことを求めているのは、そのためである。
`emergency` は本パッケージで常にそうであった場所に留まる: 人からは届き、他の
何からも届かない。操作者は `e` を押せば今でも送れる。
"""

import atexit
import os
import queue
import select
import signal
import sys
import termios
import threading
import time
import tty

from .config import DEFAULT_CONFIG

# The keys the operator may press, and what each one asks for. One letter
# each, acted on immediately with no Enter and no confirmation: a key that
# needs a second keystroke is a key that is not available at the moment it
# is needed.
# 操作者が押せるキーと、それぞれが求めるもの。1 文字ずつで、Enter も確認も無しに
# 即座に効く。2 打目を要するキーとは、必要になったその瞬間には使えないキーである。
KEY_LAND = "l"
KEY_EMERGENCY = "e"
KEY_HOLD = "h"

# The one-line reminder shown once a flight is under way. Here rather than
# at the place that prints it, so the terminal, the tests and the document
# quote the same line.
# 飛行が始まった時点で 1 度だけ示す案内。表示する側ではなくここに置き、端末・
# 試験・文書が同じ 1 行を引くようにする。
KEY_HELP_LINE = ("  キー / keys:  l = 着陸 land   e = 緊急停止 emergency（即時）"
                 "   h = 待機 hold")


class LandGuard:
    """Send `land` however this program stops flying.

    Registered with `atexit` and with the two signals a terminal sends, so
    the aircraft is landed whether the flight ended normally, raised, was
    interrupted with Ctrl-C, or was terminated.

    このプログラムがどのように飛行をやめても `land` を送る。

    `atexit` と、端末が送る 2 つのシグナルに登録する。飛行が正常に終わっても、
    例外を投げても、Ctrl-C で中断されても、終了させられても、機体は着陸する。

    Idempotent: `land` goes out at most once from here however many of
    those paths fire, because they overlap (a Ctrl-C raises
    KeyboardInterrupt AND runs the atexit handler). A second `land` would
    restart a descent that is already under way.
    冪等である。上の経路がいくつ発火しても、ここから出る `land` は高々 1 回で
    ある。経路は重なるからである（Ctrl-C は KeyboardInterrupt を起こし、かつ
    atexit の後始末も走らせる）。2 度目の `land` は、既に始まっている降下を
    やり直させる。
    """

    def __init__(self, link, config=DEFAULT_CONFIG, on_land=None, clock=None,
                 sleep=None):
        self.link = link
        self.cfg = config.real
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        # Called with the reason just before `land` goes out, so the caller
        # can record and print it. It must not raise: this runs on the exit
        # path, where an exception would replace the landing with a
        # traceback.
        # `land` を送る直前に理由とともに呼ぶ。呼び出し側が記録・表示できるように
        # するためである。例外を投げてはならない。ここは終了経路であり、例外は
        # 着陸をトレースバックに置き換えてしまう。
        self.on_land = on_land
        self.landed = False
        self.reason = ""
        self.attempts = 0
        self.acknowledged = False
        self._lock = threading.Lock()
        self._previous_handlers: dict = {}
        self._installed = False

    def install(self) -> None:
        """Take over exit and the two terminating signals.
        終了処理と、2 つの終了シグナルを引き受ける。"""
        if self._installed:
            return
        self._installed = True
        atexit.register(self._on_exit)
        for number in (signal.SIGINT, signal.SIGTERM):
            try:
                self._previous_handlers[number] = signal.getsignal(number)
                signal.signal(number, self._on_signal)
            except (ValueError, OSError):
                # Not the main thread, or a platform without the signal.
                # The atexit path still covers a normal or raised exit.
                # メインスレッドでない、またはそのシグナルが無い環境。正常終了と
                # 例外による終了は atexit 側が引き続き賄う。
                self._previous_handlers.pop(number, None)

    def release(self) -> None:
        """Give the signal handlers back. Safe to call twice.
        シグナルハンドラを返す。2 回呼んでも安全。"""
        if not self._installed:
            return
        self._installed = False
        for number, handler in self._previous_handlers.items():
            try:
                signal.signal(number, handler)
            except (ValueError, OSError):
                pass
        self._previous_handlers.clear()
        try:
            atexit.unregister(self._on_exit)
        except Exception:                             # noqa: BLE001
            pass

    def note_landed(self, reason: str) -> None:
        """Record that somebody ELSE already landed the aircraft.

        The guard sends `land` on every exit path, which is right when it
        is the only one that would. It is not right when the Executor's
        landing approach has already sent one: that `land` was preceded by
        the settling `landing.py` exists to perform, and a second one on
        top of the descent restarts it.

        Marking it landed is what makes the exit path pass through, and it
        is deliberately something the caller declares rather than something
        this class works out -- the guard has to work on exit paths where
        the Executor no longer exists.

        機体を**他の誰かが**既に着陸させたことを記録する。

        番人はあらゆる出口で `land` を送る。それを送るのが番人だけであるなら、
        それが正しい。Executor の着陸前手順が既に送っている場合には正しくない。
        その `land` の前には、`landing.py` が行うために存在する静定があり、降下の
        上に重ねる 2 通目は、それをやり直させる。

        着陸済みと印を付けることで出口が素通りになる。これを本クラスが推し量るの
        ではなく呼び出し側が申告するのは意図的である ―― 番人は、Executor がもはや
        存在しない出口でも働かねばならないからである。
        """
        with self._lock:
            if self.landed:
                return
            self.landed = True
            self.reason = reason
            # Someone else's `land` was acknowledged by the vehicle through
            # its own path, so this is not an unacknowledged landing.
            # 他者の `land` は、その経路で機体に受理されている。これは「応答の
            # 無い着陸」ではない。
            self.acknowledged = True

    def land_now(self, reason: str) -> bool:
        """Send `land`, once, and wait briefly for the acknowledgement.

        Returns True when the vehicle answered. A `land` that goes
        unanswered is sent one more time and then given up on — see the
        module docstring for why `emergency` does not follow it.

        `land` を 1 度だけ送り、応答を短く待つ。

        機体が応答したら True を返す。応答の無い `land` はもう 1 度だけ送り、
        そこで諦める。`emergency` を続けない理由は本モジュールの docstring 参照。
        """
        with self._lock:
            if self.landed:
                return self.acknowledged
            self.landed = True
            self.reason = reason

        if self.on_land is not None:
            try:
                self.on_land(reason)
            except Exception:                         # noqa: BLE001
                pass

        # Two attempts, not a loop: the second covers a single lost
        # datagram on a link measured to lose 42% of its broadcasts
        # (§4.8.2), and a third would only keep re-announcing a landing to
        # a vehicle that is either already descending or not listening.
        # ループではなく 2 回にする。2 回目が賄うのは、放送の 42% を落とすと実測
        # された（§4.8.2）リンクでの 1 個の取りこぼしである。3 回目は、既に降下中
        # か聞いていないかのどちらかの機体に、着陸を告げ直すだけである。
        for _ in range(2):
            # The reply counter is read BEFORE the send, not inside the
            # wait. A reply that arrives while `sendto` is still returning
            # -- or, on a loopback link, synchronously with it -- would
            # otherwise land after the snapshot was taken, and this would
            # send a second `land` to a vehicle that had already answered
            # the first. The second one restarts a descent already under
            # way, which is exactly what the idempotence above exists to
            # prevent.
            # 応答の計数は、待ちの中ではなく送信の**前**に読む。`sendto` が返る
            # 最中に届いた応答は ―― ループバックのリンクなら、それと同期して届く
            # 応答は ―― さもなければ snapshot を取った後に数えられることになり、
            # 既に 1 通目へ答えた機体へ 2 通目の `land` を送ってしまう。2 通目は
            # 既に始まっている降下をやり直させる。上の冪等性が防ぐためにあるのが、
            # まさにそれである。
            before = _reply_count(self.link)
            self.attempts += 1
            self._send_land()
            if self._await_ack(before):
                self.acknowledged = True
                return True
        return False

    def _send_land(self) -> None:
        """Put `land` on the priority path. / `land` を優先経路で送る。

        `priority` rather than `send`: the blocking `send` waits for a
        reply, and this may be called from a signal handler or from atexit
        where nothing may block. The acknowledgement is collected
        separately below.
        `send` ではなく `priority` を使う。ブロックする `send` は応答を待つが、
        ここはシグナルハンドラや atexit から呼ばれうる場所で、何もブロックしては
        ならない。応答は下で別に拾う。
        """
        try:
            self.link.priority("land")
        except Exception:                             # noqa: BLE001
            # A link that is already closed must not turn an exit into a
            # traceback; the operator's transmitter is the remaining cover.
            # 既に閉じたリンクが、終了をトレースバックに変えてはならない。
            # 残る備えは操作者の送信機である。
            pass

    def _await_ack(self, before) -> bool:
        """Whether the vehicle answered within `land_ack_wait_s`.

        `before` is the reply count as it stood BEFORE the `land` went out
        (see `land_now` for why it is read there rather than here).
        `before` は `land` を送出する**前**の応答の計数である（なぜここではなく
        あちらで読むかは `land_now` を参照）。

        Reads the link's reply counter rather than a specific reply text:
        the counter is what `landing.py` already uses to tell that a move
        was answered, and matching on the wording would break the first
        time the firmware rephrases it.

        `land_ack_wait_s` の内に機体が応答したかどうか。

        特定の応答文ではなくリンクの応答計数を読む。計数は `landing.py` が既に
        「移動に応答があった」を判定するのに使っているものであり、文言で照合すれば、
        ファームが言い回しを変えた最初のときに壊れる。
        """
        if before is None:
            # A link that does not count replies cannot confirm anything.
            # Reported as unacknowledged, which is the cautious reading and
            # costs only the one extra `land`.
            # 応答を数えないリンクは何も確認できない。未応答として報告する。
            # 慎重な読み方であり、代償は `land` 1 回分だけである。
            return False
        deadline = self.clock() + self.cfg.land_ack_wait_s
        while self.clock() < deadline:
            if _reply_count(self.link) > before:
                return True
            self.sleep(0.02)
        return False

    def _on_signal(self, number, _frame) -> None:
        """Land, then let the previous handler have the signal.
        着陸させてから、シグナルを元のハンドラへ渡す。"""
        name = "Ctrl-C" if number == signal.SIGINT else "SIGTERM"
        self.land_now(f"{name} を受けたため着陸 / landing on {name}")
        previous = self._previous_handlers.get(number)
        if callable(previous):
            previous(number, _frame)
            return
        # The default for SIGINT is to raise KeyboardInterrupt, which the
        # flight's own `finally` blocks then unwind normally. Re-raising it
        # here keeps that behaviour rather than swallowing the interrupt.
        # SIGINT の既定は KeyboardInterrupt を起こすことであり、飛行側の
        # `finally` がそれを通常どおり巻き戻す。ここで投げ直すのは、割り込みを
        # 飲み込まずその挙動を保つためである。
        raise KeyboardInterrupt()

    def _on_exit(self) -> None:
        """Land if the flight ended without doing so. / 着陸せずに終わったなら着陸させる。"""
        self.land_now("プロセス終了時に未着陸だったため着陸 / landing at exit")


class LoopWatchdog:
    """Land the aircraft when the monitor loop stops ticking.

    The loop calls `beat()` every cycle. A separate thread checks how long
    it has been since the last one, and lands the aircraft when that
    exceeds `watchdog_stall_s`.

    Why a separate thread is the only way: the thing being watched is the
    loop itself, so anything the loop is responsible for calling cannot
    detect the loop failing to call it. A hung `judge.ask` on the loop's
    thread, a blocking socket read, a GC pause long enough to matter — in
    all of them the aircraft keeps holding position and nothing on this
    side notices.

    監視ループが刻まなくなったら機体を着陸させる。

    ループは毎周期 `beat()` を呼ぶ。別のスレッドが、最後の刻みからの経過を確かめ、
    `watchdog_stall_s` を超えたら機体を着陸させる。

    別スレッドでなければならない理由: 見張る対象がループ自身だからである。ループが
    呼ぶ責任を負っているものは、ループがそれを呼ばなくなったことを検出できない。
    ループのスレッド上で戻らない `judge.ask`、ブロックしたソケット読み取り、無視
    できない長さの GC の停止 —— いずれの場合も機体は位置を保ち続け、こちら側の
    誰もそれに気づかない。
    """

    def __init__(self, guard: LandGuard, config=DEFAULT_CONFIG, clock=None):
        self.guard = guard
        self.cfg = config.real
        self.clock = clock or time.monotonic
        self.fired = False
        self._last_beat = self.clock()
        self._stop = threading.Event()
        self._thread = None
        # Set by `pause()` while the loop is legitimately not ticking (the
        # confirmation prompt, the preflight). Without it the watchdog
        # would land an aircraft that has not taken off.
        # ループが正当に刻んでいない間（確認の待ち、飛行前点検）、`pause()` が
        # 立てる。これが無ければ、番人は離陸していない機体を着陸させる。
        self._paused = True

    def start(self) -> None:
        """Begin watching. The first beat starts the clock.
        見張りを始める。最初の刻みが計時を始める。"""
        if self._thread is not None:
            return
        self._paused = False
        self._last_beat = self.clock()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def beat(self) -> None:
        """Record that the loop completed a cycle. / ループが 1 周したことを記録する。"""
        self._last_beat = self.clock()

    def pause(self) -> None:
        """Stop judging the loop's silence as a stall. / 無音を停滞と判じなくする。"""
        self._paused = True

    def resume(self) -> None:
        """Judge again, from now rather than from the last beat.
        再び判ずる。起点は最後の刻みではなく今にする。"""
        self._last_beat = self.clock()
        self._paused = False

    def stop(self) -> None:
        """Stop watching. Safe to call twice. / 見張りを止める。2 回呼んでも安全。"""
        self._stop.set()

    def _watch(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.cfg.watchdog_period_s)
            if self._paused or self._stop.is_set():
                continue
            stalled_s = self.clock() - self._last_beat
            has_stalled = stalled_s > self.cfg.watchdog_stall_s
            if not has_stalled:
                continue
            self.fired = True
            self.guard.land_now(
                f"監視ループが {stalled_s:.1f}s 停止したため着陸 "
                f"/ the monitor loop stalled for {stalled_s:.1f}s"
            )
            return


class KeyListener:
    """Read single keypresses without Enter, on a thread of its own.

    The terminal is put in cbreak mode so a key is delivered as it is
    pressed. Keys are queued rather than acted on here: the flight loop
    drains the queue each cycle, so a keypress takes effect through the
    same path everything else does and cannot interleave with it.

    Enter を要さず 1 打ずつキーを読む。専用のスレッドで動く。

    端末を cbreak モードにして、押された時点でキーが届くようにする。ここでは
    実行せず待ち行列に積む。飛行ループが毎周期それを吸い出すので、キー操作は
    他のすべてと同じ経路を通って効き、それらと入り混じることがない。

    Does nothing at all when stdin is not a terminal: a non-interactive run
    has no keyboard, and putting a pipe into cbreak mode would raise.
    stdin が端末でなければ何もしない。非対話の実行にキーボードは無く、パイプを
    cbreak モードにしようとすれば例外になる。
    """

    def __init__(self, stream=None):
        self.stream = stream or sys.stdin
        self.active = False
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._saved_settings = None

    def start(self) -> None:
        """Take the terminal into cbreak mode and start reading.
        端末を cbreak モードにして読み取りを始める。"""
        if not _is_a_terminal(self.stream):
            return
        try:
            self._saved_settings = termios.tcgetattr(self.stream.fileno())
            tty.setcbreak(self.stream.fileno())
        except (termios.error, OSError, ValueError):
            self._saved_settings = None
            return
        self.active = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Give the terminal back. Safe to call twice and after a failed start.
        端末を返す。2 回呼んでも、起動に失敗した後に呼んでも安全。"""
        self._stop.set()
        self.active = False
        if self._saved_settings is None:
            return
        try:
            termios.tcsetattr(self.stream.fileno(), termios.TCSADRAIN,
                              self._saved_settings)
        except (termios.error, OSError, ValueError):
            pass
        self._saved_settings = None

    def drain(self) -> list:
        """Every key pressed since the last call. / 前回以降に押されたキー全件。"""
        keys: list = []
        while True:
            try:
                keys.append(self._queue.get_nowait())
            except queue.Empty:
                return keys

    def press(self, key: str) -> None:
        """Queue a key as though it had been typed. For the tests.
        打たれたものとしてキーを積む。試験のためにある。"""
        self._queue.put(key)

    def _read_loop(self) -> None:
        descriptor = self.stream.fileno()
        while not self._stop.is_set():
            # A timed select rather than a blocking read, so `stop()` is
            # noticed within one timeout instead of waiting for a keypress
            # that may never come.
            # ブロックする読み取りではなく待ち時間つきの select にする。来ないかも
            # しれないキーを待つのではなく、`stop()` を 1 回の待ち時間内に気づく
            # ためである。
            try:
                readable, _, _ = select.select([descriptor], [], [], 0.1)
            except (OSError, ValueError):
                return
            if not readable:
                continue
            try:
                data = os.read(descriptor, 1)
            except OSError:
                return
            if not data:
                return
            self._queue.put(data.decode(errors="replace").lower())


def apply_key(key: str, guard: LandGuard, link, on_hold=None) -> str:
    """Carry out one keypress. Returns what it did, for the log.

    `e` sends `emergency` with no confirmation and no delay, which is the
    only way it is ever sent from this package. A confirmation would put a
    second keystroke between the operator and the motors at the moment
    they have decided the aircraft must stop — and the operator is
    standing next to it, watching, which is the condition under which that
    decision is theirs to make.

    キー操作を 1 つ実行する。何をしたかを返す（記録用）。

    `e` は確認も遅延も無しに `emergency` を送る。本パッケージからこれが送られる
    唯一の経路である。確認を挟めば、機体を止めねばならないと操作者が判断した
    まさにその瞬間に、操作者とモータの間に 2 打目を置くことになる —— そして操作者は
    機体の傍らに立ち、それを見ている。その判断が操作者のものである条件がそれである。
    """
    if key == KEY_LAND:
        guard.land_now("操作者が l を押したため着陸 / operator pressed l")
        return "land"
    if key == KEY_EMERGENCY:
        link.priority("emergency")
        return "emergency"
    if key == KEY_HOLD:
        if on_hold is not None:
            on_hold()
        return "hold"
    return ""


def _is_a_terminal(stream) -> bool:
    """Whether this stream is an interactive terminal.
    この入力が対話端末かどうか。"""
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _reply_count(link):
    """The link's reply counter, or None when it does not keep one.
    リンクの応答計数。持っていなければ None。"""
    return getattr(link, "reply_count", None)
