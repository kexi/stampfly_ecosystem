"""
What the preflight guarantees before a real aircraft takes off: each of the
four judging checks passes only on the condition it names, a 104-byte v1
stream is refused rather than worked around, one failing check refuses the
whole flight, and the pack voltage is REPORTED without ever refusing one --
because the vehicle owns that decision and applies it itself.

飛行前点検が、実機が離陸する前に保証すること: 判定を行う 4 つの点検それぞれが、
自分が名指しする条件でだけ通ること、104 バイトの v1 の流れを回避せず拒否する
こと、1 つでも不合格なら飛行全体を拒否すること、そしてパック電圧は**報告する
だけ**で飛行を拒否しないこと —— その判断は機体が持ち、機体自身が下すからである。

The link here is a stand-in, not a socket: what is under test is the rule
each check applies, and a real socket would add the delivery rate of the
machine running the tests to every assertion.
ここでのリンクはソケットではなく代役である。試験の対象は各点検が適用する規則で
あり、実際のソケットを使えば、試験を走らせたマシンの到達率が、あらゆる表明に
混ざることになる。
"""

import pytest

from sfpilot.config import DEFAULT_CONFIG, real_config
from sfpilot.link import Sample
from sfpilot.preflight import (
    CHECK_BATTERY, CHECK_JEV, CHECK_STATE, CHECK_TELEMETRY, CHECK_VEHICLE_LINK,
    STATE_IDLE_GROUND, VEHICLE_ARM_REFUSED_V, VEHICLE_AUTO_LAND_V,
    format_table, run_preflight,
)

CONFIG = real_config(DEFAULT_CONFIG)

# A healthy 140-byte v2 sample: on the ground, idle, on a charged pack.
# 健全な 140 バイト v2 のサンプル: 接地・待機・充電されたパック。
def _healthy(**overrides) -> Sample:
    sample = Sample(
        t=0.0, altitude_m=0.0, pos_n=0.0, pos_e=0.0,
        vel_n=0.0, vel_e=0.0, vel_d=0.0,
        battery_v=4.05, battery_pct=83.0, tof_m=0.05,
        flight_state=STATE_IDLE_GROUND,
    )
    sample.update(overrides)
    return sample


class FakeLink:
    """A link that hands out prepared samples and answers `command`.

    `read_samples` returns the whole batch once and nothing afterwards,
    which is what a real link does when the probe outlasts the stream.

    用意したサンプルを渡し、`command` に応答するリンク。

    `read_samples` は 1 度だけ全件を返し、以後は何も返さない。点検が流れより
    長く続いたときに、実際のリンクがすることがそれである。
    """

    def __init__(self, samples=None, command_status="ok", command_ms=5.0,
                 answer_after=0):
        self._samples = list(samples or [])
        # The probe discards one read before it starts timing, so that
        # anything queued from before the check is not counted in its rate
        # (`preflight._listen`). This stand-in must survive that discard,
        # or every test would measure an empty stream.
        # 聴取は、計時を始める前に 1 回分を捨てる。点検より前から溜まっていたものを
        # 到達率に数えないためである（`preflight._listen`）。この代役はその捨てに
        # 耐えねばならない。さもなければ、どの試験も空の流れを測ることになる。
        self._discards_left = 1
        self._handed_out = False
        self.command_status = command_status
        self.command_ms = command_ms
        # How many `command` sends are answered before the rest time out.
        # 0 means "every one is answered" (the healthy case).
        # 何回目までの `command` に応答するか。0 は「すべてに応答する」（健全な場合）。
        self.answer_after = answer_after
        self.sent: list = []
        self.reply_count = 0
        self.replies_outstanding = 0

    def read_samples(self) -> list:
        if self._discards_left:
            self._discards_left -= 1
            return []
        if self._handed_out:
            return []
        self._handed_out = True
        return list(self._samples)

    def send(self, line: str, timeout: float):
        self.sent.append(line)
        is_beyond_the_answered_ones = (
            self.answer_after and len(self.sent) > self.answer_after
        )
        if is_beyond_the_answered_ones:
            return "timeout", "timeout"
        return self.command_status, "ok"

    def send_rc(self, a, b, c, d) -> None:
        self.sent.append(f"rc {a} {b} {c} {d}")

    def send_command(self, line: str) -> None:
        self.sent.append(line)

    def priority(self, line: str) -> None:
        self.sent.append(line)

    def close(self) -> None:
        pass


@pytest.fixture
def clock():
    """A clock the tests advance themselves, so a 3-second probe costs none.
    試験が自分で進める時計。3 秒の聴取に 3 秒を費やさないため。"""
    state = {"now": 0.0}

    def read() -> float:
        return state["now"]

    read.state = state
    return read


@pytest.fixture
def sleep(clock):
    """A sleep that advances the test clock instead of the wall clock.
    実時計ではなく試験の時計を進める sleep。"""
    def advance(seconds: float) -> None:
        clock.state["now"] += seconds
    return advance


def _report(link, judge=None, config=CONFIG, clock=None, sleep=None):
    return run_preflight(link, judge, config, clock=clock, sleep=sleep)


# =============================================================================
# The whole report / 報告全体
# =============================================================================
def test_a_healthy_aircraft_passes_every_check(clock, sleep):
    """All five checks pass on a charged, idle aircraft with a v2 stream.
    v2 の流れを出す、充電済みで待機中の機体では 5 点検すべてが通ること。"""
    link = FakeLink(samples=[_healthy() for _ in range(100)])

    report = _report(link, clock=clock, sleep=sleep)

    assert report.ok
    assert [c.name for c in report.checks] == [
        CHECK_TELEMETRY, CHECK_BATTERY, CHECK_VEHICLE_LINK, CHECK_JEV, CHECK_STATE,
    ]


def test_one_failing_check_refuses_the_whole_flight(clock, sleep):
    """`ok` is false when any single check fails, not only when several do.

    The flight is all-or-nothing: each check guards something the flight
    relies on in the air, so a report that passed on four of five would be
    clearing a flight that is missing one of them.

    1 つでも不合格なら `ok` が false になること（複数のときだけではない）。

    飛行は全か無かである。各点検は、飛行が空中で依拠するものを守っており、
    5 つ中 4 つで通る報告とは、そのうち 1 つを欠いた飛行を許可することである。
    """
    # The vehicle is in the air under someone else's control. Everything
    # else about this link is healthy, so the only thing that can refuse the
    # flight is check (e).
    # 機体が誰か別の制御下で空中にある。このリンクの他はすべて健全なので、飛行を
    # 拒否しうるのは点検 (e) だけである。
    already_flying = [_healthy(flight_state="FLYING") for _ in range(100)]
    link = FakeLink(samples=already_flying)

    report = _report(link, clock=clock, sleep=sleep)

    assert not report.ok
    assert not report.get(CHECK_STATE).passed
    # The others still ran and still passed: a failing check must not make
    # the rest unreadable, because the table is what the operator acts on.
    # 他の点検は走り、通っている。1 つの不合格が残りを読めなくしてはならない。
    # 操作者が行動の根拠にするのはその表だからである。
    assert report.get(CHECK_TELEMETRY).passed
    assert report.get(CHECK_VEHICLE_LINK).passed


def test_the_table_names_every_check_and_the_verdict(clock, sleep):
    """`format_table` prints all five checks and whether the flight may start.
    `format_table` が 5 点検すべてと、飛行してよいかを表示すること。"""
    link = FakeLink(samples=[_healthy() for _ in range(100)])

    lines = format_table(_report(link, clock=clock, sleep=sleep))

    text = "\n".join(lines)
    for marker in ("(a)", "(b)", "(c)", "(d)", "(e)"):
        assert marker in text
    assert "離陸してよい" in text


# =============================================================================
# (a) telemetry / テレメトリ
# =============================================================================
def test_silence_on_5005_is_reported_as_not_checkable(clock, sleep):
    """No packets at all is a SKIP with the thing to go and look at.

    Distinguished from a check that ran and failed: the operator's next
    action differs (look at the aircraft's power and `wifi.mode`, rather
    than at the number the check measured).

    1 件も届かないことは SKIP とし、何を見に行くべきかを添えること。

    実施して不合格だった場合と区別する。操作者の次の行動が違うためである
    （点検が測った数値ではなく、機体の電源と `wifi.mode` を見に行く）。
    """
    link = FakeLink(samples=[])

    check = _report(link, clock=clock, sleep=sleep).get(CHECK_TELEMETRY)

    assert not check.passed
    assert check.skipped
    assert "5005" in check.detail


def test_a_v1_stream_is_refused_rather_than_worked_around(clock, sleep):
    """A 104-byte v1 packet, which carries no battery and no ToF, is refused.

    `RealLink` can fill those two from the 10 Hz state string and does, so
    this would otherwise "work" -- at a tenth of the rate the battery trend
    window was measured at, and with no forward distance at all. Flying the
    safety layer on that is flying something none of the measurements in
    the plan describe.

    電池も ToF も運ばない 104 バイトの v1 パケットを拒否すること。

    `RealLink` はその 2 項目を 10Hz の状態文字列から補えるし実際に補うので、
    これは放っておけば「動いて」しまう —— 電池の傾向の窓を実測したときの 1/10 の
    周期で、しかも前方距離は皆無で、である。その上で安全層を飛ばすことは、計画中の
    どの実測も述べていないものを飛ばすことである。
    """
    v1_like = Sample(t=0.0, altitude_m=0.0, vel_n=0.0, vel_e=0.0,
                     flight_state=STATE_IDLE_GROUND)
    link = FakeLink(samples=[v1_like for _ in range(100)])

    check = _report(link, clock=clock, sleep=sleep).get(CHECK_TELEMETRY)

    assert not check.passed
    assert "140B" in check.detail or "v2" in check.detail


def test_a_delivery_rate_below_the_floor_refuses_the_flight(clock, sleep):
    """Too few packets fails even though the ones that arrive are valid v2.

    The measured 58% (§4.8.2) is what the layers above were designed
    against; a rate far below it is a qualitatively different link, not a
    slightly worse one.

    届いたものが正しい v2 であっても、数が少なすぎれば不合格とすること。

    実測の 58%（§4.8.2）は、上の層がそれに対して設計されたものである。それを大きく
    下回る到達率は、少し悪いリンクではなく、質的に別のリンクである。
    """
    # 3 s probe expects about 150 packets at 50 Hz; 10 is under 10%.
    # 3 秒の聴取は 50Hz で約 150 個を期待する。10 個は 10% 未満である。
    link = FakeLink(samples=[_healthy() for _ in range(10)])

    check = _report(link, clock=clock, sleep=sleep).get(CHECK_TELEMETRY)

    assert not check.passed
    assert check.measured["received"] == 10
    assert check.measured["rate"] < CONFIG.real.telemetry_rate_min


def test_the_measured_rate_is_recorded_for_the_trace(clock, sleep):
    """The numbers behind the verdict are kept, not only the sentence.
    判定の背後の数値を、文だけでなく残すこと。"""
    link = FakeLink(samples=[_healthy() for _ in range(100)])

    check = _report(link, clock=clock, sleep=sleep).get(CHECK_TELEMETRY)

    assert check.measured["received"] == 100
    assert check.measured["expected"] == 150
    assert check.measured["rate"] == pytest.approx(100 / 150, abs=1e-3)


# =============================================================================
# (b) battery / 電池
# =============================================================================
def test_the_voltage_is_reported_and_never_refuses_the_flight(clock, sleep):
    """The voltage check passes on ANY readable voltage, including a flat pack.

    The vehicle owns this decision and applies it in three layers of its
    own: `requestArm` refuses an ARM at or below 3.3 V, the 3.4 V warning
    does not end a flight, and the vehicle lands itself at 3.0 V. A gate
    here could only duplicate that or — as the removed 3.85 V threshold
    did — refuse flights the vehicle was willing to make.

    電圧の点検が、読める電圧であればどんな値でも通ること（空のパックでも）。

    この判断は機体が持ち、しかも自前の 3 層で適用している。`requestArm` は 3.3V
    以下で ARM を拒否し、3.4V の警告は飛行を終わらせず、3.0V で機体は自ら着陸する。
    ここに関門を置いても、その複製になるか、あるいは撤去した 3.85V がそうであった
    ように、機体が応じる気のある飛行を拒むかにしかならない。
    """
    # Below every one of the vehicle's own thresholds -- it would not even
    # arm on this pack. The preflight still reports it and still passes,
    # because refusing here is the vehicle's job and it does it.
    # 機体自身のどのしきい値よりも下であり、このパックでは ARM すらしない。それ
    # でも点検は報告して通す。ここで拒むのは機体の仕事であり、機体はそれをする。
    flat = 3.0 - 0.2
    link = FakeLink(samples=[_healthy(battery_v=flat, battery_pct=0.0)
                             for _ in range(100)])

    report = _report(link, clock=clock, sleep=sleep)
    check = report.get(CHECK_BATTERY)

    assert check.passed
    assert report.ok
    assert check.measured["lowest_v"] == pytest.approx(flat, abs=1e-3)


def test_the_voltage_reported_is_the_lowest_reading_seen(clock, sleep):
    """The row shows the worst reading, not the mean.

    The aircraft is on the ground with the motors off, so there is no load
    transient to average away, and a pack that dipped once will dip again
    under a hover. The honest number to put in front of the operator is the
    worst one that was seen.

    表示するのが平均ではなく**最も低い**読みであること。

    機体は接地しモータは止まっており、均して消すべき負荷の過渡は無い。一度
    落ち込んだパックは、ホバリングでも落ち込む。操作者の前に置くべき正直な数値は、
    見えたうちで最も悪いものである。
    """
    dip = 3.71
    samples = [_healthy(battery_v=4.05) for _ in range(99)]
    samples.append(_healthy(battery_v=dip))
    link = FakeLink(samples=samples)

    check = _report(link, clock=clock, sleep=sleep).get(CHECK_BATTERY)

    assert check.measured["lowest_v"] == pytest.approx(dip, abs=1e-3)
    assert f"{dip:.2f}" in check.detail


def test_the_row_names_the_vehicles_own_thresholds(clock, sleep):
    """The reported line says whose decision the voltage is.

    Without the vehicle's numbers beside it, a bare voltage invites the
    reader to supply a threshold of their own -- which is how the removed
    3.85 V gate came to exist in the first place.

    表示する行が、その電圧が誰の判断に属するかを述べること。

    機体側の数値を添えずに電圧だけを出せば、読み手は自分のしきい値を当てはめたく
    なる。撤去した 3.85V の関門が生まれた経緯がまさにそれである。
    """
    link = FakeLink(samples=[_healthy() for _ in range(100)])

    check = _report(link, clock=clock, sleep=sleep).get(CHECK_BATTERY)

    assert check.measured["vehicle_arm_refused_at_v"] == VEHICLE_ARM_REFUSED_V
    assert check.measured["vehicle_auto_lands_at_v"] == VEHICLE_AUTO_LAND_V
    assert "機体が判断" in check.detail


def test_the_table_marks_the_voltage_row_as_information(clock, sleep):
    """`format_table` prints INFO, not PASS, beside the voltage.

    A row that cannot fail must not wear the mark of a row that could have:
    five PASS marks would tell the operator that five things were verified,
    when only four were.

    `format_table` が電圧の行に PASS ではなく INFO を表示すること。

    不合格になりえない行が、なりえた行と同じ印を付けてはならない。PASS が 5 つ
    並べば、操作者には 5 件が確かめられたと読める。実際に確かめたのは 4 件である。
    """
    link = FakeLink(samples=[_healthy() for _ in range(100)])

    lines = format_table(_report(link, clock=clock, sleep=sleep))

    voltage_row = next(line for line in lines if "電池電圧" in line)
    assert "[INFO]" in voltage_row
    assert "[PASS]" not in voltage_row


def test_no_voltage_at_all_points_at_the_telemetry_check(clock, sleep):
    """Without a voltage the check is skipped and says which check to fix first.
    電圧が無ければ点検を飛ばし、先にどの点検を直すべきかを述べること。"""
    link = FakeLink(samples=[])

    check = _report(link, clock=clock, sleep=sleep).get(CHECK_BATTERY)

    assert not check.passed
    assert check.skipped


# =============================================================================
# (c) the link to the vehicle / 機体へのリンク
# =============================================================================
def test_command_is_the_only_thing_sent_to_the_vehicle(clock, sleep):
    """The preflight transmits `command` and nothing else.

    This is the property that makes a preflight safe to run next to an
    aircraft with propellers on: `command` enters SDK mode and the API
    reference lists it as having no effect on the motors. Any other verb
    here would either move the aircraft or need interpreting.

    飛行前点検が送出するのは `command` だけであること。

    プロペラを付けた機体の傍らで点検を走らせても安全である根拠がこれである。
    `command` は SDK モードへの移行で、API リファレンスはモータに影響しないと
    している。ここに他の verb があれば、機体を動かすか、解釈を要するかのどちらかに
    なる。
    """
    link = FakeLink(samples=[_healthy() for _ in range(100)])

    _report(link, clock=clock, sleep=sleep)

    assert set(link.sent) == {"command"}
    assert len(link.sent) == CONFIG.real.command_probe_count


def test_an_unanswered_command_refuses_the_flight(clock, sleep):
    """No reply to `command` fails: a `land` could not be delivered either.

    §2 records that the firmware does NOT land itself when the PC goes
    quiet, so a link this side cannot reach is a flight nobody can end
    from the PC.

    `command` に応答が無ければ不合格とすること。`land` も届けられないためである。

    §2 は「PC が黙ってもファームは自分で着陸しない」と記録している。こちらから
    届かないリンクとは、PC からは誰も終わらせられない飛行のことである。
    """
    link = FakeLink(samples=[_healthy() for _ in range(100)], command_status="timeout")

    check = _report(link, clock=clock, sleep=sleep).get(CHECK_VEHICLE_LINK)

    assert not check.passed
    assert check.measured["answered"] == 0


def test_a_link_that_drops_some_replies_fails_too(clock, sleep):
    """Partial answers fail: a link that loses `command` loses `land`.
    一部しか応答しないリンクも不合格とすること。`command` を落とすリンクは
    `land` も落とす。"""
    link = FakeLink(samples=[_healthy() for _ in range(100)], answer_after=6)

    check = _report(link, clock=clock, sleep=sleep).get(CHECK_VEHICLE_LINK)

    assert not check.passed
    assert check.measured["answered"] == 6
    assert check.measured["failures"] == 4


def test_a_round_trip_over_budget_fails(clock, sleep):
    """A p95 past `command_p95_max_s` fails even when every send is answered.

    Answered-but-slow is its own failure: a stop command that arrives a
    fifth of a second behind the situation that called for it is a stop
    the situation has already moved past.

    すべてに応答があっても、p95 が `command_p95_max_s` を超えれば不合格とすること。

    「応答はあるが遅い」はそれ自体が不合格である。それを求めた状況より 0.2 秒
    遅れて届く停止の指令とは、状況が既に通り過ぎた後の停止である。
    """
    # Each send advances the clock past the budget, since `send` is
    # measured with the same injected clock the probe uses.
    # 各送信で時計を予算の先まで進める。`send` は、聴取が使うのと同じ注入した
    # 時計で測られるためである。
    class SlowLink(FakeLink):
        def send(self, line: str, timeout: float):
            clock.state["now"] += CONFIG.real.command_p95_max_s * 2
            return super().send(line, timeout)

    link = SlowLink(samples=[_healthy() for _ in range(100)])

    check = _report(link, clock=clock, sleep=sleep).get(CHECK_VEHICLE_LINK)

    assert not check.passed
    assert check.measured["p95_ms"] > check.measured["budget_ms"]


# =============================================================================
# (d) Jev / Jev への疎通
# =============================================================================
def test_a_rule_based_judge_is_recorded_as_not_asked(clock, sleep):
    """`--fake` records that Jev was not reached, rather than a 0 ms round trip.

    A rule-based judge answers instantly from a table. Timing that and
    printing it would show a passing network check to anyone reading the
    table -- the exact misreading this check exists to prevent.

    `--fake` は「Jev へは問い合わせなかった」と記録すること（0ms の往復ではなく）。

    規則ベースの judge は表から即答する。それを計時して表示すれば、表を読む人には
    網の点検が通ったように見える —— この点検が防ぐためにある、まさにその読み違い
    である。
    """
    from sfpilot.judge import FakeJudge

    link = FakeLink(samples=[_healthy() for _ in range(100)])

    check = _report(link, judge=FakeJudge(), clock=clock, sleep=sleep).get(CHECK_JEV)

    assert check.passed
    assert check.measured["asked"] == 0
    assert "--fake" in check.detail


def test_an_unreachable_jev_refuses_the_flight(clock, sleep):
    """A judge that raises every time fails the check with the first reason.

    Refused rather than warned: without Jev the judging layer hovers on
    every cycle and the hover-to-land timer lands the aircraft anyway, so
    the flight ends either way -- better on the ground.

    毎回例外を投げる judge は、最初の理由とともに不合格とすること。

    警告ではなく拒否にする。Jev が無ければ判断層は毎周期待機し、待機継続の計時が
    どのみち機体を着陸させる。どちらにせよ飛行は終わるので、地上で終わるほうが
    よい。
    """
    class BrokenJudge:
        def ask(self, state, question_ids):
            raise ConnectionError("no route to api.typesafe.ai")

    link = FakeLink(samples=[_healthy() for _ in range(100)])

    check = _report(link, judge=BrokenJudge(), clock=clock, sleep=sleep).get(CHECK_JEV)

    assert not check.passed
    assert "typesafe" in str(check.measured["errors"]).lower()


# =============================================================================
# (e) the vehicle's own state / 機体自身の状態
# =============================================================================
def test_only_idle_ground_clears_the_state_check(clock, sleep):
    """IDLE_GROUND passes; FLYING does not.

    Any other state means the aircraft is not where the operator believes
    it is, and a takeoff that would be refused anyway should be refused
    BEFORE the operator is told the checks passed.

    IDLE_GROUND は通り、FLYING は通らないこと。

    他の状態は、機体が操作者の思っている場所に居ないことを意味する。どのみち拒否
    される離陸は、点検を通ったと操作者に告げる**前に**拒否されるべきである。
    """
    on_the_ground = FakeLink(samples=[_healthy() for _ in range(100)])
    assert _report(on_the_ground, clock=clock, sleep=sleep).get(CHECK_STATE).passed

    clock.state["now"] = 0.0
    in_the_air = FakeLink(
        samples=[_healthy(flight_state="FLYING") for _ in range(100)]
    )

    check = _report(in_the_air, clock=clock, sleep=sleep).get(CHECK_STATE)

    assert not check.passed
    assert check.measured["state"] == "FLYING"


def test_the_report_serialises_for_the_trace(clock, sleep):
    """`to_row` gives a JSON-able object carrying every check's verdict.
    `to_row` が、各点検の判定を載せた JSON 化できる物を返すこと。"""
    import json

    link = FakeLink(samples=[_healthy() for _ in range(100)])

    row = _report(link, clock=clock, sleep=sleep).to_row()

    assert json.loads(json.dumps(row, ensure_ascii=False))["ok"] is True
    assert len(row["checks"]) == 5
