"""
sfpilot.executor - turn a verdict into commands the vehicle accepts.
sfpilot.executor - 判定を機体が受け付ける指令に変える。

The vehicle's text API is blocking for every command except `rc a b c d`,
which is a fire-and-forget velocity command (api_task.cpp). So this layer
speaks `rc` continuously and uses the blocking verbs only at the moments
that end a flight phase.

機体のテキスト API は `rc a b c d` 以外すべて応答待ちでブロックする。
`rc` だけが応答を待たない速度指令である（api_task.cpp）。そのため本層は
常時 `rc` を送り、ブロックする指令は飛行段階の切れ目でだけ使う。

`rc` is resent at 20Hz even when the command has not changed. Why not
send only on change: the link is UDP with no retransmission, so a single
lost datagram would otherwise leave the vehicle holding a stale velocity
until the next change -- which, for a hover command, might be never.

指令が変わらなくても `rc` を 20Hz で送り続ける。変化時のみ送らない理由:
通信は再送の無い UDP であり、1 つの datagram が落ちただけで、機体は次の
変化まで古い速度を持ち続けてしまう。待機指令の場合、その「次」は永遠に
来ないかもしれない。
"""

from .arbiter import VERDICT_CONTINUE, VERDICT_HOVER, VERDICT_LAND, VERDICT_STOP
from .config import EnvelopeConfig, DEFAULT_CONFIG

# `rc` takes four integers in [-100, 100]: roll, pitch, throttle, yaw.
# All zero is "hold position" -- the vehicle's own position-hold
# controller keeps it in place (the API is POS_HOLD only).
# `rc` は [-100, 100] の整数 4 つ（roll・pitch・throttle・yaw）を取る。
# すべて 0 が「その場で保持」— 機体側の位置保持制御が位置を保つ
# （API は POS_HOLD 固定）。
RC_SCALE = 100
RC_HOVER = (0, 0, 0, 0)


class Executor:
    """Send the commands one verdict implies.
    1 つの判定が意味する指令を送る。"""

    def __init__(self, link, config=DEFAULT_CONFIG):
        self.link = link
        self.envelope: EnvelopeConfig = config.envelope
        self.last_rc = RC_HOVER
        self.landing = False          # a land was issued / 着陸指令を出した
        self.commands: list = []      # what was sent, for the trace / 記録用
        # When another layer is driving the vehicle (`sf pilot say` walking
        # an instruction's steps), the routine hovering `rc` must not go
        # out: `rc` publishes a VELOCITY guidance target (api_task.cpp
        # cmdRc, mode 2) which REPLACES the position target a `forward` or
        # `up` just set, so a hover sent alongside a move would cancel that
        # move. The stopping commands still go out -- `land` and `stop` are
        # the whole reason the safety layer is running at all.
        # 別の層が機体を駆動している間（`sf pilot say` が指示の手順を進めて
        # いる間）、待機の `rc` を出してはならない。`rc` は**速度**誘導目標を
        # publish し（api_task.cpp の cmdRc、mode 2）、`forward` や `up` が
        # 設定した位置目標を**置き換える**ためである。移動と並行して待機を
        # 送れば、その移動を打ち消してしまう。止める指令は出し続ける —
        # `land` と `stop` こそ、安全層が動いている理由そのものだからである。
        self.hold_commands_silently = False

    def apply(self, verdict, velocity=None) -> str:
        """Carry out one verdict; returns the command line that was sent.
        判定を 1 つ実行し、送った指令行を返す。"""
        if verdict.action == VERDICT_LAND:
            return self._land()
        if verdict.action == VERDICT_STOP:
            return self._stop()
        if verdict.action == VERDICT_HOVER:
            return self._rc(RC_HOVER)
        if verdict.action == VERDICT_CONTINUE:
            return self._rc(self._clamp(velocity or (0.0, 0.0, 0.0, 0.0)))
        # An unknown verdict must not become motion. Hovering is the one
        # safe interpretation of "I do not understand this instruction".
        # 未知の判定を動作にしてはならない。「この指示が分からない」の唯一
        # 安全な解釈は待機である。
        return self._rc(RC_HOVER)

    def tick(self) -> str:
        """Resend the current `rc`, called at 20Hz. Does nothing once a
        landing is under way -- `rc` during a landing would fight the
        vehicle's own descent controller.
        現在の `rc` を再送する。20Hz で呼ばれる。着陸開始後は何もしない —
        着陸中の `rc` は機体側の降下制御と競合するため。"""
        if self.landing:
            return ""
        return self._rc(self.last_rc)

    # -- command forms / 指令の形 ---------------------------------------

    def _rc(self, rc) -> str:
        self.last_rc = tuple(rc)
        line = "rc {} {} {} {}".format(*self.last_rc)
        if self.hold_commands_silently:
            # Recorded but not sent, so the trace still shows what the
            # safety layer would have commanded while another layer drove.
            # 記録はするが送らない。別の層が駆動している間に安全層が何を
            # 指令したはずかは、記録に残しておく。
            self.commands.append(f"(withheld) {line}")
            return line
        self.link.send_rc(*self.last_rc)
        self.commands.append(line)
        return line

    def _land(self) -> str:
        self.landing = True
        self.link.send_command("land")
        self.commands.append("land")
        return "land"

    def _stop(self) -> str:
        # `stop` goes on the priority path: it must overtake anything the
        # link is currently waiting on.
        # `stop` は優先経路で送る。リンクが待機中の処理を追い越す必要がある。
        self.link.priority("stop")
        self.last_rc = RC_HOVER
        self.commands.append("stop")
        return "stop"

    def _clamp(self, velocity):
        """Velocity in m/s -> `rc` integers, limited by the envelope.
        速度 [m/s] を包絡で制限した `rc` の整数に変換する。"""
        north, east, up, yaw_rate = velocity
        horizontal_max = self.envelope.speed_max_mps
        vertical_max = self.envelope.climb_rate_max_mps
        return (
            _to_rc(east, horizontal_max),
            _to_rc(north, horizontal_max),
            _to_rc(up, vertical_max),
            _to_rc(yaw_rate, 1.0),
        )


def _to_rc(value: float, limit: float) -> int:
    """Scale to [-100, 100], clipped at the limit.
    上限で切り詰めたうえで [-100, 100] に正規化する。"""
    if limit <= 0:
        return 0
    clipped = max(-limit, min(limit, value))
    return int(round(clipped / limit * RC_SCALE))
