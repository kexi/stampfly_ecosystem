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

**Every `land` in this package goes out from here, through the approach in
`landing.py`.** The firmware stops holding horizontal position the moment a
descent begins (by design -- see LandingConfig), so a `land` sent while the
craft is still moving slides across the floor for the whole descent. Having
one place that sends it is what makes the settling before it unskippable:
a second caller with its own `link.send_command("land")` would be a landing
that silently does not settle.

**本パッケージの `land` はすべてここから、`landing.py` の着陸前手順を通って
出る。** ファームは降下を始めた瞬間に水平の位置保持をやめる（設計どおり。
LandingConfig 参照）ため、機体が動いているうちに送った `land` は降下のあいだ
ずっと床の上を滑る。送る場所を 1 か所にすることが、その前の静定を省略不可能に
する。自前で `link.send_command("land")` を呼ぶ 2 人目の呼び出し側は、静かに
静定しない着陸になってしまうからである。
"""

from .arbiter import (
    VERDICT_BACK, VERDICT_CONTINUE, VERDICT_HOVER, VERDICT_LAND, VERDICT_STOP,
)
from .config import EnvelopeConfig, DEFAULT_CONFIG
from .landing import LandingApproach

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

    def __init__(self, link, config=DEFAULT_CONFIG, speed_probe=None, clock=None,
                 forward_probe=None):
        self.link = link
        self.cfg = config
        # Handed to each landing approach. Injectable for the same reason it
        # is there: a test must be able to reach a ceiling measured in
        # seconds without spending them.
        # 各着陸前手順へ渡す時計。差し替え可能にしている理由も同じである —
        # 秒で測る上限に、実際に秒を費やさず到達できる必要がある。
        self.clock = clock
        self.envelope: EnvelopeConfig = config.envelope
        self.last_rc = RC_HOVER
        self.landing = False          # a landing is under way / 着陸手順に入った
        # True while a retreat that has been sent is still being flown, so
        # the next cycles wait for it instead of stacking another on top.
        # Cleared by the first verdict that is not a retreat (`apply`).
        # 送信済みの後退がまだ飛ばされている間 True。次以降の周期が、上へさらに
        # 積むのではなく、それを待つようにするため。後退以外の判定が来た時点で
        # 解除する（`apply`）。
        self.backing_off = False
        self.commands: list = []      # what was sent, for the trace / 記録用
        # The approach that is settling the craft before `land` goes out,
        # or None once it has. `landing` turns True when the approach
        # STARTS, not when the `land` line is sent: the flight is ending
        # from that moment, and a caller that kept commanding until the
        # line went out would be commanding against the settling.
        # `land` を送る前に機体を静定させている手順。送り終えたら None。
        # `landing` が True になるのは手順の**開始**時であって `land` 行の送信時
        # ではない。その時点から飛行は終わりに向かっており、行が出るまで指令を
        # 続ける呼び出し側は、静定に逆らって指令することになるからである。
        self.approach = None
        self.last_landing_summary: dict = {}
        # Reads the craft's horizontal speed [m/s] for the settling wait.
        # Without one the wait cannot end early and runs to its ceiling,
        # which is slower but never wrong.
        # 静定待ちのために機体の水平速度 [m/s] を読む。無ければ待ちは早く終われず
        # 上限まで走る。遅くはなるが、誤りにはならない。
        self.speed_probe = speed_probe
        # Reads the forward distance for the landing approach, which backs
        # away from a wall before descending: the firmware holds no
        # horizontal position during a descent, so a landing started facing
        # a wall drifts into it (landing.py).
        # 着陸前手順が使う前方距離の読み取り。手順は降下の前に壁から離れる。
        # ファームは降下中に水平位置を保持しないので、壁を向いたまま始めた着陸は
        # そちらへ流れていくからである（landing.py）。
        self.forward_probe = forward_probe
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
        # Once a landing is under way nothing else may be commanded. The
        # approach owns the aircraft from here: an `rc` sent alongside it
        # would re-publish a velocity target and undo the settling, and a
        # second `land` would restart the sequence that is already running.
        # 着陸手順に入ったら、他のどの指令も出さない。以後、機体はこの手順のもの
        # である。並行して送る `rc` は速度目標を publish し直して静定を無に帰し、
        # 2 度目の `land` は既に走っている手順を最初からやり直させる。
        if self.landing:
            return self._advance_landing()
        # Any verdict other than a retreat ends the one in progress: the
        # situation has moved on, and a latch left set would swallow the
        # next retreat as "already backing off".
        # 後退以外の判定は、進行中の後退を終わらせる。状況が変わったということで
        # あり、ラッチを立てたままにすると、次の後退が「すでに後退中」として
        # 飲み込まれてしまう。
        is_retreat = verdict.action == VERDICT_BACK
        if not is_retreat:
            self.backing_off = False
        if verdict.action == VERDICT_LAND:
            return self._land(verdict)
        if verdict.action == VERDICT_STOP:
            return self._stop()
        if verdict.action == VERDICT_BACK:
            return self._back()
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
        landing is under way -- `rc` during the settling would re-publish a
        velocity target and undo it, and `rc` during the descent itself
        would fight the vehicle's own descent controller.
        現在の `rc` を再送する。20Hz で呼ばれる。着陸開始後は何もしない —
        静定中の `rc` は速度目標を publish し直してそれを無に帰し、降下中の `rc`
        は機体側の降下制御と競合するため。

        Silent during a retreat too, and for the same reason: `rc` publishes
        a velocity target that would replace the retreat's position target
        and cancel the move away from the wall.
        後退中も同様に何もしない。理由も同じで、`rc` は速度目標を publish し、
        後退の位置目標を置き換えて、壁から離れる移動を打ち消してしまう。
        """
        if self.landing or self.backing_off:
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

    def _land(self, verdict=None) -> str:
        """Begin the pre-landing approach; the `land` line follows it.

        Nothing about the craft's motion is assumed here. Whether it is
        moving, and how fast, is what the approach measures -- so the same
        call is correct after a completed hover and in the middle of a
        100 cm move.

        着陸前手順を開始する。`land` の行はその後に出る。

        ここで機体の運動について何も仮定しない。動いているか、どれだけ速いかは
        手順が測る — そのため、静定したホバリングの後でも 100cm の移動の途中でも、
        この呼び出しは同じように正しい。
        """
        self.landing = True
        self.approach = LandingApproach(
            self.link, self.cfg, speed_probe=self.speed_probe,
            urgent=_is_urgent(verdict), reason=getattr(verdict, "reason", ""),
            clock=self.clock, forward_probe=self.forward_probe,
        )
        self.approach.start()
        return self._advance_landing()

    def _advance_landing(self) -> str:
        """Push the approach one cycle and report what it did.
        手順を 1 周期進め、行った内容を返す。"""
        if self.approach is None:
            return "land"
        sent = self.approach.step()
        line = f"landing:{self.approach.stage}"
        self.commands.append(line)
        if not sent:
            return line
        self.last_landing_summary = self.approach.summary()
        self.approach = None
        return "land"

    def _back(self) -> str:
        """Retreat one step from an obstacle the stop did not open up.

        A `stop` goes out first and on the same cycle, because the retreat
        is a MOVE and the vehicle is very likely still holding the position
        target of the move that carried it in. Sending `back` without
        cancelling that first would set a new target while the old one is
        still being pursued, which is how the craft came to be here.

        Sent once per retreat, not every cycle: `back` is a blocking move
        the vehicle answers when it arrives, so re-sending it each 50Hz
        cycle would queue dozens of retreats and fly the craft backwards
        into whatever is behind it -- which it cannot see at all. The latch
        clears when the situation stops calling for a retreat, so a wall
        that is still closing after one hop gets another.

        停止では距離が開かなかった障害物から、1 段だけ後退する。

        同じ周期でまず `stop` を送る。後退は**移動**であり、機体はここまで運んできた
        移動の位置目標をなお保持している可能性が高いからである。それを取り消さずに
        `back` を送れば、古い目標をなお追いかけている最中に新しい目標を置くことに
        なる ―― そもそも機体がここに至った経緯がそれである。

        送るのは 1 回の後退につき 1 度であり、毎周期ではない。`back` は機体が到達時に
        応答するブロックする移動なので、50Hz の周期ごとに送り直せば後退が何十個も
        積まれ、機体は「まったく見えていない」後方の何かへ向かって飛ぶことになる。
        ラッチは、状況が後退を求めなくなった時点で解ける。1 段下がってもなお壁が
        詰まってくるなら、もう 1 段下がることになる。
        """
        if self.backing_off:
            return self._advance_backoff()
        self.backing_off = True
        self.link.priority("stop")
        self.last_rc = RC_HOVER
        step_cm = round(self.cfg.forward.backoff_step_cm)
        line = f"back {step_cm}"
        self.link.send_command(line)
        self.commands.append(line)
        return line

    def _advance_backoff(self) -> str:
        """Hold still while a retreat already sent is being flown.

        Nothing is sent. An `rc` here would replace the retreat's position
        target with a velocity one and cancel the move (the same reason
        `hold_commands_silently` exists), and a second `back` would stack
        another retreat on top of the one in progress.

        送信済みの後退が飛ばされている間、何もせず待つ。

        何も送らない。ここで `rc` を送れば、後退の位置目標を速度目標で置き換えて
        移動を打ち消すことになり（`hold_commands_silently` が存在するのと同じ
        理由）、2 度目の `back` は進行中の後退の上にもう 1 つ後退を積む。
        """
        self.commands.append("(backing off)")
        return "back"

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
        速度 [m/s] を飛行領域で制限した `rc` の整数に変換する。"""
        north, east, up, yaw_rate = velocity
        horizontal_max = self.envelope.speed_max_mps
        vertical_max = self.envelope.climb_rate_max_mps
        return (
            _to_rc(east, horizontal_max),
            _to_rc(north, horizontal_max),
            _to_rc(up, vertical_max),
            _to_rc(yaw_rate, 1.0),
        )


def _is_urgent(verdict) -> bool:
    """Whether this landing is one that must not wait to settle.

    The Monitor's immediate safety rules are the definition of "cannot
    wait": they exist precisely for situations that may not spend 500 ms
    asking for an opinion (a battery in the danger band, a diverged
    estimate), so they may not spend six seconds settling either. Every
    other landing -- Jev's, the hover-to-land timer's, the end of a mission
    -- has the time, and taking it is what keeps the touchdown where it was
    meant to be.

    この着陸が、静定を待てないものかどうか。

    「待てない」の定義は Monitor の即時安全則そのものである。意見を求めて 500ms を
    使ってはならない状況（電池の危険域、推定の発散）のための規則であり、だとすれば
    静定に 6 秒を使ってよいはずもない。それ以外の着陸 — Jev の判断、待機継続の
    計時、ミッションの終了 — には時間があり、それを使うことが、接地点を意図した
    場所に保つ。
    """
    return getattr(verdict, "source", "") == "monitor"


def _to_rc(value: float, limit: float) -> int:
    """Scale to [-100, 100], clipped at the limit.
    上限で切り詰めたうえで [-100, 100] に正規化する。"""
    if limit <= 0:
        return 0
    clipped = max(-limit, min(limit, value))
    return int(round(clipped / limit * RC_SCALE))
