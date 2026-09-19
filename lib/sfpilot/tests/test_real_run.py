"""
What `sf pilot run --real` guarantees: a failing preflight takes off
nothing, an exception on the way through still lands the aircraft, the
real flight area is narrower than the SILS one and the Arbiter enforces
it, and `say` / `mission` / `explore` refuse hardware.

`sf pilot run --real` が保証すること: 飛行前点検に落ちたら何も離陸しないこと、
途中の例外でも機体が着陸すること、実機の飛行領域が SILS のものより狭く、
Arbiter がそれを強制すること、`say`・`mission`・`explore` が実機を拒否すること。

Nothing here opens a socket or starts an emulator: the flight is driven
through a stand-in link, which is what makes it possible to reach the
exception path and the empty-preflight path at all.
ここでは socket も emulator も開かない。飛行は代役のリンクで駆動する。例外の
経路と、点検に落ちる経路に到達できるのは、そうしているからである。
"""

import pytest

from sfpilot.config import DEFAULT_CONFIG, RealEnvelopeConfig, real_config
from sfpilot.link import Sample
from sfpilot.preflight import STATE_IDLE_GROUND
from sfpilot.real_run import RecordingLink, fly_real, preflight_only

CONFIG = real_config(DEFAULT_CONFIG)


def _quick(config=CONFIG):
    """`config` with the preflight's real-time waits shortened.

    Only the durations a flight SPENDS are changed -- the listening window,
    the `land` acknowledgement wait, and the climb settle. Every threshold
    a check JUDGES on (the rate floor, the voltage minimum, the round-trip
    budget) is left alone, because those are what these tests are about;
    shortening a wait changes how long a test takes, while changing a
    threshold would change what it proves.

    飛行が実時間で待つ時間だけを短くした `config`。

    変えるのは飛行が**費やす**時間 ―― 聴取の窓、`land` の応答待ち、上昇の静定 ――
    だけである。点検が**判定に使う**しきい値（到達率の下限、電圧の下限、往復の
    予算）はそのままにする。それらこそ、これらの試験が対象にしているものだから
    である。待ちを短くすれば試験の所要時間が変わるだけだが、しきい値を変えれば
    試験が何を示すかが変わる。
    """
    from dataclasses import replace

    return replace(
        config,
        real=replace(config.real, telemetry_probe_s=0.05, land_ack_wait_s=0.05),
        sils=replace(config.sils, takeoff_settle_s=0.05),
    )


QUICK = _quick()


def _ground_sample(**overrides) -> Sample:
    sample = Sample(
        t=0.0, altitude_m=0.0, pos_n=0.0, pos_e=0.0,
        vel_n=0.0, vel_e=0.0, vel_d=0.0,
        battery_v=4.05, battery_pct=83.0, tof_m=0.05,
        flight_state=STATE_IDLE_GROUND,
    )
    sample.update(overrides)
    return sample


class StubLink:
    """A link that always has telemetry and always answers.

    Enough for a preflight to pass, so the tests that need a flight to
    START can reach it without a vehicle.

    常にテレメトリがあり、常に応答するリンク。

    飛行前点検が通るだけのものを備える。飛行が**始まる**必要のある試験が、機体
    なしでそこへ到達できるようにするためである。
    """

    def __init__(self, healthy: bool = True):
        self.healthy = healthy
        self.sent: list = []
        self.reply_count = 0
        self.replies_outstanding = 0
        self._discards_left = 1

    def read_samples(self) -> list:
        if not self.healthy:
            return []
        if self._discards_left:
            self._discards_left -= 1
            return []
        # Stamped with the live clock, as `RealLink` stamps a UDP:5005
        # packet. A fixed timestamp would read to the silence rule as
        # telemetry that stopped arriving at the epoch, and the flight
        # would end on its first cycle.
        # `RealLink` が UDP:5005 のパケットに押すのと同じく、いまの時計で押す。
        # 固定の時刻は、無音の規則には「起点の時刻から届かなくなったテレメトリ」と
        # 読め、飛行は最初の周期で終わってしまう。
        import time

        now = time.monotonic()
        # A batch big enough to clear the delivery-rate floor.
        # 到達率の下限を越えるだけの数のまとまり。
        return [_ground_sample(t=now) for _ in range(100)]

    def send(self, line: str, timeout: float):
        self.sent.append(line)
        self.reply_count += 1
        return ("ok", "ok") if self.healthy else ("timeout", "timeout")

    def send_rc(self, a, b, c, d) -> None:
        self.sent.append(f"rc {a} {b} {c} {d}")

    def send_command(self, line: str) -> None:
        self.sent.append(line)
        self.reply_count += 1

    def priority(self, line: str) -> None:
        self.sent.append(line)
        self.reply_count += 1

    def close(self) -> None:
        pass


# =============================================================================
# The preflight gate / 点検の関門
# =============================================================================
def test_a_failing_preflight_takes_off_nothing():
    """When a check fails, no `takeoff` is sent and nothing has to be landed.

    Refusing before the takeoff is what makes the refusal cost nothing:
    there is no emulator, no climb, and so nothing to bring back down.

    点検に落ちたら `takeoff` を送らず、着陸させるべきものも生じないこと。

    離陸の前に拒否することが、その拒否の代償を無くす。エミュレータも上昇も無く、
    したがって降ろすべきものも無い。
    """
    link = StubLink(healthy=False)

    outcome = fly_real(link, judge=None, config=QUICK, duration_s=1.0)

    assert not outcome.took_off
    assert "takeoff" not in link.sent
    assert "land" not in link.sent
    assert not outcome.preflight.ok


def test_preflight_only_sends_command_and_never_takes_off():
    """The dry-run entry point transmits `command` and stops there.

    This is the entry point that exists so the checks can be exercised
    against a real aircraft before anyone is willing to fly one.

    予行の入口が `command` を送出し、そこで終わること。

    誰かが実機を飛ばす気になる前に、実機に対して点検を働かせられるようにする
    ために存在する入口である。
    """
    link = StubLink()

    report = preflight_only(link, judge=None, config=QUICK)

    assert report.ok
    assert set(link.sent) == {"command"}
    assert "takeoff" not in link.sent


# =============================================================================
# Every exit lands / どの出口も着陸する
# =============================================================================
def test_an_exception_during_the_flight_still_lands_the_aircraft():
    """A raise on the way through does not leave the aircraft hovering.

    §2 records that the vehicle does NOT land itself when the PC goes
    quiet, so an exception that escaped without a `land` would leave it
    holding position until the battery was gone.

    途中の例外が、機体を浮かせたままにしないこと。

    §2 は「PC が黙っても機体は自分で着陸しない」と記録している。`land` を出さずに
    抜けた例外は、電池が尽きるまで機体に位置を保たせることになる。
    """
    link = StubLink()

    def explode(_sample, _now):
        raise RuntimeError("the live view fell over")

    with pytest.raises(RuntimeError):
        fly_real(link, judge=None, config=QUICK, duration_s=1.0,
                 on_cycle=explode)

    assert "takeoff" in link.sent
    assert link.sent[-1] == "land"


def test_a_judge_that_answers_with_nothing_fails_the_check_rather_than_crashing():
    """A judge returning None is a failed ask, not an AttributeError.

    This check decides whether the aircraft flies, so it must not be the
    thing that raises: a crash here comes out as an abort on the ground
    rather than as a check that did not pass, and the operator is left
    reading a traceback instead of a table.

    None を返す judge は、AttributeError ではなく「失敗した問い合わせ」とすること。

    この点検は機体が飛ぶかどうかを決めるものなので、例外を投げる当のもので
    あってはならない。ここでの異常終了は「通らなかった点検」ではなく「地上での
    中断」として現れ、操作者は表ではなくトレースバックを読むことになる。
    """
    from sfpilot.preflight import CHECK_JEV

    class SilentJudge:
        def ask(self, state, question_ids):
            return None

    report = preflight_only(StubLink(), judge=SilentJudge(), config=QUICK)

    assert not report.get(CHECK_JEV).passed
    assert not report.ok


def test_a_normal_flight_ends_with_a_land():
    """A flight that runs its duration out lands rather than just stopping.
    時間を走り切った飛行が、ただ止まるのではなく着陸すること。"""
    link = StubLink()

    outcome = fly_real(link, judge=None, config=QUICK, duration_s=0.2)

    assert outcome.took_off
    assert outcome.landed
    assert link.sent[-1] == "land"
    assert "所定の時間" in outcome.ended_because


def test_the_command_sequence_is_reported_in_order():
    """The summary carries every command that reached the aircraft, in order.

    A repeated `rc` is collapsed to a count, because 400 identical rows
    tell a reader nothing that one row and a count does not -- and the
    other verbs are the ones whose ORDER is being checked.

    集計が、機体に届いた指令を順に載せること。

    繰り返しの `rc` は回数にまとめる。同じ行 400 個が読み手に伝えることは、1 行と
    回数が伝えること以上には無く、確かめたいのは他の verb の**順序**だからである。
    """
    link = StubLink()

    outcome = fly_real(link, judge=None, config=QUICK, duration_s=0.2)

    sequence = outcome.commands
    assert sequence[0] == "command"
    assert "takeoff" in sequence
    assert sequence[-1] == "land"


def test_a_repeated_rc_is_collapsed_to_a_count():
    """`RecordingLink` records `rc x N` rather than N identical rows.
    `RecordingLink` が同じ行 N 個ではなく `rc x N` を記録すること。"""
    link = RecordingLink(StubLink())

    for _ in range(5):
        link.send_rc(0, 0, 0, 0)
    link.send_command("land")

    assert link.command_sequence() == ["rc 0 0 0 0 x5", "land"]


def test_the_delivery_rate_is_measured_over_the_flight():
    """The summary reports how much telemetry arrived while flying.

    §4.8.2's 58% is one measurement from one room, so every real flight
    measures its own -- this is the number that says whether this room is
    like that one.

    集計が、飛行中にどれだけテレメトリが届いたかを報告すること。

    §4.8.2 の 58% は 1 つの部屋での 1 回の測定なので、実機の飛行はそれぞれ自分の
    ぶんを測る。この部屋があの部屋と同じかを述べるのがこの数値である。
    """
    link = StubLink()

    outcome = fly_real(link, judge=None, config=QUICK, duration_s=0.2)

    assert outcome.samples_expected > 0
    assert outcome.samples_received > 0


# =============================================================================
# The real flight area / 実機の飛行領域
# =============================================================================
def test_the_real_flight_area_is_narrower_than_the_sils_one():
    """Every limit is tighter on hardware, because the cost of reaching one is.

    In SILS the cost of touching a limit is a number in a CSV; in a room it
    is the airframe and whoever is in the room.

    実機ではどの上限も狭いこと。上限に達した代償が違うためである。

    SILS で上限に触れた代償は CSV の数値だが、部屋では機体と、その部屋にいる人で
    ある。
    """
    sils = DEFAULT_CONFIG.envelope
    real = CONFIG.envelope

    assert real.altitude_max_m < sils.altitude_max_m
    assert real.radius_max_m < sils.radius_max_m
    assert real.speed_max_mps < sils.speed_max_mps
    assert real.climb_rate_max_mps < sils.climb_rate_max_mps
    assert isinstance(real, RealEnvelopeConfig)


def test_real_config_changes_only_the_flight_area():
    """The bands, rules and landing approach are the SAME as the rehearsal's.

    The layers that decide are the ones that were exercised in SILS, so
    giving them different numbers on hardware would mean flying something
    that was never rehearsed. Only the envelope moves.

    区分・規則・着陸前手順は予行と**同一**であること。

    判断する層は SILS で働かせた当のものである。実機で別の数値を与えれば、予行して
    いないものを飛ばすことになる。動くのは飛行領域だけである。
    """
    assert CONFIG.monitor == DEFAULT_CONFIG.monitor
    assert CONFIG.arbiter == DEFAULT_CONFIG.arbiter
    assert CONFIG.landing == DEFAULT_CONFIG.landing
    assert CONFIG.judge == DEFAULT_CONFIG.judge
    assert CONFIG.envelope != DEFAULT_CONFIG.envelope


def test_the_arbiter_refuses_a_climb_the_real_area_forbids():
    """An altitude legal in SILS is refused by the Arbiter on hardware.

    0.9 m sits inside the SILS envelope (0.3-1.5 m) and outside the real
    one (0.3-0.8 m), so the same situation and the same answer produce a
    hover on hardware and a continue in SILS -- which is the whole point of
    the narrower box.

    SILS では許される高度を、実機では Arbiter が却下すること。

    0.9m は SILS の飛行領域（0.3〜1.5m）の内側で、実機のもの（0.3〜0.8m）の外側に
    ある。したがって、同じ状況・同じ答えが、実機では待機を、SILS では継続を生む ――
    狭い箱の目的がそれである。
    """
    from sfpilot.arbiter import VERDICT_CONTINUE, VERDICT_HOVER

    above_the_real_ceiling = _assessment_at(0.9)
    judgement = _continue_judgement()

    in_sils = _decide(DEFAULT_CONFIG, above_the_real_ceiling, judgement)
    on_hardware = _decide(CONFIG, above_the_real_ceiling, judgement)

    assert in_sils.action == VERDICT_CONTINUE
    assert on_hardware.action == VERDICT_HOVER


def test_the_arbiter_refuses_a_position_beyond_the_real_radius():
    """1.5 m out is inside the SILS radius (2 m) and outside the real one (1 m).
    1.5m は SILS の半径 2m の内側、実機の 1m の外側である。"""
    from sfpilot.arbiter import VERDICT_CONTINUE, VERDICT_HOVER

    far_out = _assessment_at(0.5, north=1.5)
    judgement = _continue_judgement()

    assert _decide(DEFAULT_CONFIG, far_out, judgement).action == VERDICT_CONTINUE
    assert _decide(CONFIG, far_out, judgement).action == VERDICT_HOVER


def _assessment_at(altitude_m: float, north: float = 0.0, east: float = 0.0):
    """A healthy flying assessment at a given place.
    与えられた場所で健全に飛んでいる評価。"""
    from sfpilot.monitor import Assessment

    return Assessment(
        phase="飛行中", altitude="目標どおり", altitude_trend="安定",
        horizontal_drift="ほぼ静止", attitude="ほぼ水平",
        position_estimate="信頼できる", ground_distance_sensor="正常",
        battery_level="十分", battery_trend="安定",
        numeric={"altitude_m": altitude_m, "pos_n": north, "pos_e": east},
    )


def _continue_judgement():
    """A confident `continue` from the safety question.
    安全の質問に対する、確信度の高い `continue`。"""
    from sfpilot.judge import ACT_CONTINUE, Answer, Judgement, Q_SAFETY

    return Judgement(answers={Q_SAFETY: Answer(
        kind="choice", choice=ACT_CONTINUE, confidence=0.97,
        probabilities={ACT_CONTINUE: 0.97},
    )}, latency_ms=200.0)


def _decide(config, assessment, judgement):
    """One Arbiter decision under `config`. / `config` の下での Arbiter の判定 1 件。"""
    from sfpilot.arbiter import Arbiter

    return Arbiter(config).decide(
        assessment, judgement, asked_signature="s", current_signature="s", now=0.0,
    )


def test_a_judged_landing_is_allowed_to_settle_before_the_flight_ends():
    """The loop waits for the landing approach to send `land` itself.

    `executor.landing` turns True the moment `LandingApproach` BEGINS, at
    which point only `stop` has gone out and the craft is still settling.
    Ending the loop on that flag would let `fly_real`'s `finally` send a
    raw `land` instead, skipping the settling -- which `landing.py`
    measures at 0.45 m of slide across the floor, against 0.000 m from a
    settled hover.

    So the sequence must contain the `stop` that begins the approach, and
    the `land` must come after it rather than in place of it.

    判断層の着陸では、着陸前手順が自分で `land` を送るまでループが待つこと。

    `executor.landing` は `LandingApproach` が**始まった**瞬間に True になる。
    その時点で出ているのは `stop` だけで、機体はまだ静定中である。このフラグで
    ループを終えれば、代わりに `fly_real` の `finally` が素の `land` を送り、静定を
    飛ばすことになる ―― `landing.py` はその滑りを 0.45m と実測している（静定した
    ホバリングからは 0.000m）。

    したがって指令列には手順を始める `stop` が含まれ、`land` はその**後**に来なければ
    ならない（`stop` の代わりであってはならない）。
    """
    from sfpilot.judge import ACT_LAND, Answer, Judgement, Q_SAFETY

    class LandingJudge:
        """Always answers `land`, confidently. / 常に確信を持って `land` と答える。"""

        def ask(self, state, question_ids):
            return Judgement(answers={Q_SAFETY: Answer(
                kind="choice", choice=ACT_LAND, confidence=0.99,
                probabilities={ACT_LAND: 0.99},
            )}, latency_ms=10.0)

        def close(self):
            pass

    link = StubLink()

    outcome = fly_real(link, judge=LandingJudge(), config=QUICK, duration_s=5.0)

    # A repeated command is recorded as "land x2", so the rows are matched
    # on their verb rather than compared whole.
    # 繰り返しの指令は "land x2" として記録されるので、行は丸ごと比べずに verb で
    # 照合する。
    verbs = [row.split()[0] for row in outcome.commands]
    assert "stop" in verbs, "the approach must have begun"
    assert "land" in verbs, "the approach must have sent the landing itself"
    assert verbs.index("stop") < verbs.index("land"), (
        "the settling `stop` must precede the `land`, not be replaced by it"
    )


def test_a_landing_the_approach_completed_is_not_sent_a_second_time():
    """The exit path does not stack its own `land` on the approach's.

    `LandGuard` sends `land` on every exit, which is right when it is the
    only one that would. After the Executor's approach has sent one -- that
    one preceded by the settling -- a second `land` restarts a descent
    already under way, so the exit path is told the aircraft is down.

    手順が完了させた着陸に、出口がもう 1 通を重ねないこと。

    `LandGuard` はあらゆる出口で `land` を送る。送るのが番人だけなら、それが
    正しい。Executor の手順が ―― 静定を前に置いたうえで ―― 既に送った後では、
    2 通目は進行中の降下をやり直させる。そこで出口には、機体は降りたと伝える。
    """
    from sfpilot.judge import ACT_LAND, Answer, Judgement, Q_SAFETY

    class LandingJudge:
        def ask(self, state, question_ids):
            return Judgement(answers={Q_SAFETY: Answer(
                kind="choice", choice=ACT_LAND, confidence=0.99,
                probabilities={ACT_LAND: 0.99},
            )}, latency_ms=10.0)

        def close(self):
            pass

    outcome = fly_real(StubLink(), judge=LandingJudge(), config=QUICK,
                       duration_s=5.0)

    assert outcome.commands.count("land") == 1, outcome.commands
    assert "land x2" not in outcome.commands
    assert outcome.landed
