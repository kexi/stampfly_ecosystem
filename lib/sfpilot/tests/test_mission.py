"""
What a mission guarantees: the route it will refuse, the words it sends to
Jev, and the limits no answer can override.

ミッションが保証すること: 拒否する経路、Jev へ送る言葉、そしてどの答えも
覆せない上限。

No emulator is started here. Loading, classifying and the limit rules are
all code, so they are pinned without a key, a network or a flight; the
SILS rehearsal in `simulator/tests/test_mission_sils.py` covers what only a
real flight can show.

ここではエミュレータを起動しない。読み込み・区分・上限の規則はいずれもコード
なので、キーも通信も飛行も無しに固定できる。実際に飛ばさなければ分からない
ことは `simulator/tests/test_mission_sils.py` の SILS 予行が扱う。
"""

import json
from pathlib import Path

import pytest

from sfpilot.config import DEFAULT_CONFIG
from sfpilot.judge import (
    MOVE_HOLD, MOVE_LAND, MOVE_NEXT, MOVE_REDO, MOVE_RETURN, MOVE_SKIP,
    MissionFakeJudge, Q_NEXT_MOVE,
)
from sfpilot.mission import (
    ARRIVAL_AS_PLANNED, ARRIVAL_OVERSHOT, ARRIVAL_SHORT,
    MissionError, MissionWalk, builtin_mission_path, classify_arrival,
    load_mission, mission_state, resolve_mission_path,
)
from sfpilot.summarizer import assert_no_numbers


def _write(tmp_path: Path, data: dict) -> Path:
    """A mission file on disk, as JSON (which the loader also reads).
    ディスク上のミッションファイル。JSON で書く（読み込み側は JSON も読む）。"""
    path = tmp_path / "mission.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# =============================================================================
# Loading and refusing / 読み込みと拒否
# =============================================================================

def test_the_shipped_square_loads_and_closes_on_itself():
    """`square.yaml` is a real square: it returns to where it started.

    Walked on paper, four 0.6 m sides with right-angle turns must end at
    the takeoff point. A route that did not close would draw something
    other than a square in `truth.csv`, and the SILS check of the shape
    would be checking the wrong shape.

    `square.yaml` が本当に四角形であり、出発点へ戻ること。

    机上で積算すると、直角の旋回を挟んだ 0.6m の 4 辺は離陸点で終わるはずで
    ある。閉じない経路は `truth.csv` に四角形以外の何かを描き、SILS での形の
    確認が別の形を確認することになってしまう。
    """
    mission = load_mission(builtin_mission_path("square"))
    walk = MissionWalk()

    for leg in mission.legs:
        if leg.step.verb in ("return_home", "land"):
            break
        walk.apply(leg.step)

    assert abs(walk.north_m) < 1e-6, f"north {walk.north_m}"
    assert abs(walk.east_m) < 1e-6, f"east {walk.east_m}"


def test_the_shipped_square_has_four_equal_sides():
    """Four travelling legs of the same length, so the shape is checkable.
    等しい長さの移動が 4 区間あり、形を確認できること。"""
    mission = load_mission(builtin_mission_path("square"))

    sides = [leg.step.amount for leg in mission.legs
             if leg.step.verb in ("forward", "back", "left", "right")]

    assert len(sides) == 4
    assert len(set(sides)) == 1, f"sides differ: {sides}"


def test_the_shipped_square_contains_no_rotation():
    """The route avoids `cw`/`ccw`, which the SILS craft cannot survive.

    `api cw 90` disarms the emulated vehicle about a second into the
    rotation, through the impact detector, with none of this package
    involved (docs/plans/jev-autopilot.md §4.4). Until that is fixed, a
    shipped route containing a turn would be a route that always crashes,
    so the absence is pinned rather than left to be reintroduced by
    someone tidying the file.

    経路が `cw`/`ccw` を含まないこと。SILS の機体がそれに耐えられないため。

    `api cw 90` は、回転を始めて 1 秒ほどで衝撃検出により機体を解除する。本
    パッケージは一切関与しない（docs/plans/jev-autopilot.md §4.4）。それが
    直るまで、旋回を含む同梱の経路は「必ず墜落する経路」である。ファイルを
    整えた誰かが戻してしまわないよう、含まないことを固定する。
    """
    mission = load_mission(builtin_mission_path("square"))

    verbs = {leg.step.verb for leg in mission.legs}

    assert not (verbs & {"turn_right", "turn_left"}), f"a turn is back: {verbs}"


def test_a_route_that_leaves_the_envelope_is_refused_by_name(tmp_path):
    """The refusal names the leg, while the aircraft is still on the ground.

    Refusing at the leg that breaches would find it out in the air, with
    nobody to tell. This is the same rule `sf pilot say` follows.

    飛行領域を出る経路は、機体が地上にあるうちに、その区間を名指しして拒否される
    こと。

    違反する区間に達してから拒否すれば、空中で、伝える相手がいないところで
    判明する。`sf pilot say` と同じ規則である。
    """
    far = DEFAULT_CONFIG.envelope.radius_max_m * 100.0 + 100.0
    path = _write(tmp_path, {"legs": [
        {"verb": "takeoff"},
        {"verb": "forward", "amount": far, "label": "行きすぎる辺"},
    ]})

    with pytest.raises(MissionError) as excinfo:
        load_mission(path)

    assert "行きすぎる辺" in str(excinfo.value)


def test_a_route_that_climbs_out_of_the_envelope_is_refused(tmp_path):
    """The altitude ceiling is checked on paper too, not only the radius.
    半径だけでなく高度の上限も机上で検査されること。"""
    high = DEFAULT_CONFIG.envelope.altitude_max_m * 100.0 + 100.0
    path = _write(tmp_path, {"legs": [
        {"verb": "takeoff"},
        {"verb": "up", "amount": high},
    ]})

    with pytest.raises(MissionError):
        load_mission(path)


def test_a_leg_with_no_verb_is_refused_by_position(tmp_path):
    """A malformed entry says which one it is, so the file can be fixed.
    不正な項目は何番目かを告げ、ファイルを直せるようにすること。"""
    path = _write(tmp_path, {"legs": [{"verb": "takeoff"}, {"amount": 50}]})

    with pytest.raises(MissionError) as excinfo:
        load_mission(path)

    assert "2 番目" in str(excinfo.value)


def test_a_travelling_leg_with_no_amount_is_refused(tmp_path):
    """`forward` with nothing to go by is a mistake, not a default.

    Supplying a default here would fly a distance nobody wrote down, which
    is the one thing a route file exists to prevent.

    量の無い `forward` は誤りであって既定値ではないこと。

    ここで既定値を当てれば、誰も書いていない距離を飛ぶことになる。経路ファイル
    が存在する理由は、まさにそれを防ぐことである。
    """
    path = _write(tmp_path, {"legs": [{"verb": "forward"}]})

    with pytest.raises(MissionError):
        load_mission(path)


def test_an_unknown_verb_is_refused_with_the_list_of_valid_ones(tmp_path):
    """A typo is answered with what would have worked.
    綴り違いには、通ったはずの選択肢を添えて答えること。"""
    path = _write(tmp_path, {"legs": [{"verb": "fly_forwards", "amount": 50}]})

    with pytest.raises(MissionError) as excinfo:
        load_mission(path)

    assert "forward" in str(excinfo.value)


def test_an_empty_route_is_refused(tmp_path):
    """A mission with no legs is not a mission. / 区間の無いミッションは拒否する。"""
    path = _write(tmp_path, {"legs": []})

    with pytest.raises(MissionError):
        load_mission(path)


def test_a_missing_file_is_refused_rather_than_ignored(tmp_path):
    """A name nobody can resolve is an error, not an empty flight.
    解決できない名前は、空の飛行ではなく誤りとすること。"""
    with pytest.raises(MissionError):
        load_mission(tmp_path / "no_such_mission.yaml")


def test_a_bare_name_resolves_to_a_shipped_mission():
    """`sf pilot mission square` works without a path.
    `sf pilot mission square` がパス無しで動くこと。"""
    assert resolve_mission_path("square") == builtin_mission_path("square")


def test_a_local_file_is_never_shadowed_by_a_shipped_one(tmp_path, monkeypatch):
    """An existing path always wins over a name of the same spelling.

    Otherwise a local `square.yaml` would silently fly the packaged route
    instead, which is the sort of surprise a route file must not produce.

    既存のパスは、同じ綴りの名前より常に優先されること。

    そうでなければ、手元の `square.yaml` が静かに同梱の経路を飛ばすことになる。
    経路ファイルが生んではならない類の意外性である。
    """
    local = tmp_path / "square.yaml"
    local.write_text("legs: []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert resolve_mission_path("square.yaml") == Path("square.yaml")


# =============================================================================
# Classifying a leg's arrival / 区間の到達の区分
# =============================================================================

def test_landing_on_the_target_is_as_planned():
    """Inside the tolerance is arrival, however it got there.
    許容の内側は、どう着いたにせよ到達であること。"""
    arrival = classify_arrival(start=(0.0, 0.0), target=(1.0, 0.0),
                               measured=(1.0, 0.0))

    assert arrival == ARRIVAL_AS_PLANNED


def test_stopping_before_the_target_is_short():
    """Less far along the leg's own direction than the target.
    区間の進行方向で見て、終点より手前にいること。"""
    arrival = classify_arrival(start=(0.0, 0.0), target=(1.0, 0.0),
                               measured=(0.5, 0.0))

    assert arrival == ARRIVAL_SHORT


def test_going_past_the_target_is_overshot():
    """Further along the leg's own direction than the target.
    区間の進行方向で見て、終点より先にいること。"""
    arrival = classify_arrival(start=(0.0, 0.0), target=(1.0, 0.0),
                               measured=(1.5, 0.0))

    assert arrival == ARRIVAL_OVERSHOT


def test_ending_sideways_of_the_target_is_not_called_an_overshoot():
    """An error across the leg is short of the target, not past it.

    Calling it "overshot" would describe a different problem than the one
    that happened -- the craft did not go too far, it went the wrong way.

    区間を横切る誤差は、終点を越えたのではなく届いていないこと。

    それを「行き過ぎ」と呼べば、実際に起きたのとは別の問題を述べることになる。
    機体は行き過ぎたのではなく、違う方向へ行ったのである。
    """
    arrival = classify_arrival(start=(0.0, 0.0), target=(1.0, 0.0),
                               measured=(1.0, 1.0))

    assert arrival == ARRIVAL_SHORT


def test_the_tolerance_comes_from_the_config_and_not_a_literal():
    """A classification just inside the configured tolerance is arrival.

    Pinned to the config so that changing the figure changes the behaviour
    in one place, and a test cannot quietly disagree with the vehicle's own
    arrival radius.

    設定した許容のすぐ内側は到達と区分されること。

    設定に紐づけることで、値の変更が 1 か所で効き、試験が機体自身の到達半径と
    静かに食い違うことを防ぐ。
    """
    tolerance = DEFAULT_CONFIG.mission.arrival_tolerance_m

    just_inside = classify_arrival((0.0, 0.0), (1.0, 0.0),
                                   (1.0 + tolerance * 0.9, 0.0))
    just_outside = classify_arrival((0.0, 0.0), (1.0, 0.0),
                                    (1.0 + tolerance * 1.1, 0.0))

    assert just_inside == ARRIVAL_AS_PLANNED
    assert just_outside == ARRIVAL_OVERSHOT


# =============================================================================
# The state Jev is told / Jev に伝える状況
# =============================================================================

def test_the_mission_state_carries_no_numbers():
    """The design's first constraint holds for the mission fields too.

    Jev is documented not to be a calculator, so a figure in the state
    would invite exactly the comparison it cannot do reliably.
    `assert_no_numbers` is what makes that mechanical.

    設計の第一の制約が、ミッションの項目にも成り立つこと。

    Jev は計算機ではないと文書化されているので、state 中の数値は、確実には
    行えない比較を誘発する。それを機械的に守るのが `assert_no_numbers` である。
    """
    mission = load_mission(builtin_mission_path("square"))

    state = mission_state(mission, index=2, arrival=ARRIVAL_SHORT,
                          retries=1, elapsed_s=40.0)

    assert_no_numbers({"mission": state})


def test_the_state_says_where_in_the_route_the_aircraft_is():
    """"3/10" is a position, which the model may be told.

    Knowing it is near the end is part of judging whether to press on, and
    a position is not a figure to compute with.

    「3/10」は位置であり、モデルに伝えてよいこと。

    終わりが近いと分かることは、押し通すかどうかの判断の一部であり、位置は計算に
    使う数値ではない。
    """
    mission = load_mission(builtin_mission_path("square"))

    state = mission_state(mission, index=2, arrival="", retries=0, elapsed_s=0.0)

    assert state["current_leg"] == f"3/{mission.total}"


def test_the_retry_count_reaches_the_model_as_a_word():
    """A count becomes "once already", never the number 1.

    What follows from the count -- may it be redone again? -- is decided in
    code, so the model needs the weight of it, not the arithmetic.

    計数は「once already」になり、数値の 1 にはならないこと。

    計数から従う判断 —— もう一度やり直してよいか —— はコードが決めるので、
    モデルに要るのはその重みであって計算ではない。
    """
    mission = load_mission(builtin_mission_path("square"))

    none = mission_state(mission, 0, "", retries=0, elapsed_s=0.0)
    once = mission_state(mission, 0, "", retries=1, elapsed_s=0.0)
    limit = mission_state(mission, 0, "",
                          retries=DEFAULT_CONFIG.mission.max_retries_per_leg,
                          elapsed_s=0.0)

    assert none["retries_on_this_leg"] == "not yet"
    assert "once" in once["retries_on_this_leg"]
    assert "limit" in limit["retries_on_this_leg"]


def test_elapsed_time_reaches_the_model_as_a_word():
    """A clock becomes "close to the time limit", never a number of seconds.
    時計は「close to the time limit」になり、秒数にはならないこと。"""
    mission = load_mission(builtin_mission_path("square"))
    limit = DEFAULT_CONFIG.mission.time_limit_s

    early = mission_state(mission, 0, "", 0, elapsed_s=limit * 0.1)
    late = mission_state(mission, 0, "", 0, elapsed_s=limit * 0.9)

    assert early["elapsed"] != late["elapsed"]
    assert "limit" in late["elapsed"]


def test_an_unknown_arrival_is_left_out_rather_than_sent_as_unknown():
    """Before the first leg finishes there is no arrival to report.

    Irrelevant state lowers accuracy, so a field with nothing to say is
    omitted entirely rather than sent as "unknown" (summarizer.py).

    最初の区間が終わる前は、報告すべき到達が存在しないこと。

    無関係な state は精度を下げるので、言うことの無い項目は「unknown」として
    送らず、項目ごと省く（summarizer.py）。
    """
    mission = load_mission(builtin_mission_path("square"))

    state = mission_state(mission, 0, arrival="", retries=0, elapsed_s=0.0)

    assert "leg_arrival" not in state


# =============================================================================
# The rule-based judge / 規則による代役の judge
# =============================================================================

def _ask(state: dict) -> str:
    """What the keyless judge proposes for this state.
    キー不要の judge がこの state に対して提案するもの。"""
    from sfpilot.judge import WITH_MISSION

    judgement = MissionFakeJudge().ask(state, WITH_MISSION)
    return judgement.answers[Q_NEXT_MOVE].choice


def test_the_fake_judge_carries_on_when_a_leg_arrived():
    """Nothing wrong and the leg arrived: go to the next one.
    異常がなく区間も到達していれば、次へ進むこと。"""
    assert _ask({"flight": {"phase": "flying"},
                 "battery": {"level": "plenty left"},
                 "mission": {"leg_arrival": ARRIVAL_AS_PLANNED}}) == MOVE_NEXT


def test_the_fake_judge_redoes_a_leg_that_did_not_arrive():
    """A leg that stopped short is worth another attempt.
    手前で止まった区間は、もう一度試す価値があること。"""
    assert _ask({"flight": {"phase": "flying"},
                 "battery": {"level": "plenty left"},
                 "mission": {"leg_arrival": ARRIVAL_SHORT}}) == MOVE_REDO


def test_the_fake_judge_comes_home_on_a_low_battery():
    """A low battery ends the route wherever it is.
    電池が少なければ、どこにいても経路を終えること。"""
    assert _ask({"flight": {"phase": "flying"},
                 "battery": {"level": "running low"},
                 "mission": {"leg_arrival": ARRIVAL_AS_PLANNED}}) == MOVE_RETURN


def test_the_fake_judge_holds_while_the_craft_is_being_pushed():
    """Fast drift is a reason to wait rather than start another leg.
    速く流されていることは、次の区間を始めるより待つ理由であること。"""
    assert _ask({"flight": {"phase": "flying",
                            "horizontal_drift": "drifting fast to the right"},
                 "battery": {"level": "plenty left"},
                 "mission": {"leg_arrival": ARRIVAL_AS_PLANNED}}) == MOVE_HOLD


def test_the_fake_judge_still_answers_the_safety_question():
    """The same judge serves the mission and the 50Hz safety layer.

    A judge that knew only about missions would leave every safety question
    unanswered, the Arbiter would hover on "no answer", and the
    hover-to-land timer would land the aircraft mid-route.

    同じ judge が、ミッションと 50Hz の安全層の両方に応じること。

    ミッションのことしか知らない judge では安全の質問が全て未回答になり、
    Arbiter は「答え無し」で待機し、待機継続の計時が経路の途中で機体を着陸させて
    しまう。
    """
    from sfpilot.judge import Q_SAFETY, WITH_MISSION

    judgement = MissionFakeJudge().ask({"flight": {"phase": "flying"}},
                                       WITH_MISSION)

    assert judgement.answers[Q_SAFETY].choice is not None


# =============================================================================
# The limits the code keeps to itself / コードだけが持つ上限
# =============================================================================
#
# Each of these is a rule Jev's answer cannot override. They are tested
# against `_allow`, which is the one place an answer is turned into an
# action -- testing them through a whole flight would confirm the same rule
# while also depending on an emulator.
#
# いずれも、Jev の答えが覆せない規則である。答えが行動に変わる唯一の場所である
# `_allow` に対して確認する。飛行全体を通して確かめても同じ規則を確認するだけで、
# そのうえエミュレータに依存することになる。

class _StubFlight:
    """Just enough of a flight for the limit rules to be applied.

    The rules read three things -- the retry count, the battery band and
    the leg's arrival -- so this stands in for the rest. A real flight
    would need an emulator, which would test the emulator as much as the
    rules.

    上限の規則を当てるのに必要なだけの飛行。

    規則が読むのは 3 つ（やり直しの回数・電池の区分・区間の到達）なので、残りは
    これで代える。実際の飛行にはエミュレータが要り、それでは規則と同じだけ
    エミュレータを確認することになる。
    """

    def __init__(self, retries: int = 0, battery: str = "十分",
                 arrival: str = ARRIVAL_AS_PLANNED, config=DEFAULT_CONFIG):
        self.cfg = config
        self.retries = retries
        self.last_arrival = arrival
        self._battery = battery

    def _battery_is_low(self) -> bool:
        return self._battery in ("残り少ない", "危険")


def _allow(choice: str, **kwargs) -> tuple:
    """Apply the code's limits to one choice. / 1 つの選択に上限を当てる。"""
    from sfpilot.mission import _MissionFlight

    return _MissionFlight._allow(_StubFlight(**kwargs), choice)


def test_a_leg_may_be_redone_up_to_the_limit():
    """Below the limit, a redo is allowed through unchanged.
    上限に達するまでは、やり直しがそのまま通ること。"""
    allowed, refusal = _allow(MOVE_REDO, retries=0, arrival=ARRIVAL_SHORT)

    assert allowed == MOVE_REDO
    assert refusal == ""


def test_a_leg_past_the_retry_limit_is_skipped_instead_of_redone():
    """The code stops allowing retries, whatever Jev keeps choosing.

    A leg that fails repeatedly is failing for a reason repeating it will
    not fix, and every retry costs battery the mission still needs. The
    refusal is recorded so the summary can say the code overrode the model.

    Jev が何を選び続けても、コードがやり直しを許さなくなること。

    繰り返し失敗する区間は、繰り返しても直らない理由で失敗している。やり直しは
    そのたびに、ミッションがまだ必要とする電池を消費する。却下は記録し、集計が
    「コードがモデルを覆した」と言えるようにする。
    """
    limit = DEFAULT_CONFIG.mission.max_retries_per_leg

    allowed, refusal = _allow(MOVE_REDO, retries=limit, arrival=ARRIVAL_SHORT)

    assert allowed == MOVE_SKIP
    assert "上限" in refusal


def test_a_low_battery_refuses_to_go_on_and_comes_home():
    """`next_step` on a low battery becomes `return_home`, in code.

    Jev choosing to press on is not wrong of it -- it is shown the words
    "running low", not a gauge, and it is documented not to compare figures.
    The aircraft still must not start another leg, so the substitution is
    made here, where it cannot be argued with.

    電池が少ないときの `next_step` が、コード側で `return_home` になること。

    Jev が押し通すことを選ぶこと自体は誤りではない。見えているのは「running low」
    という語であって電池計ではなく、数値を比べられないと文書化されている。それでも
    機体は次の区間を始めてはならないので、置き換えはここで行う。議論の余地の
    無い場所である。
    """
    allowed, refusal = _allow(MOVE_NEXT, battery="残り少ない")

    assert allowed == MOVE_RETURN
    assert "電池" in refusal


def test_a_low_battery_refuses_a_redo_and_a_skip_as_well():
    """Any action that keeps flying the route is refused, not just `next`.

    A redo is another leg's worth of flying and a skip starts the leg after
    it; neither is less flying than carrying on.

    経路を飛び続ける行動はすべて却下されること。`next` だけではない。

    やり直しは 1 区間ぶんの飛行であり、飛ばすことは次の区間を始めることである。
    どちらも「進む」より飛行が少ないわけではない。
    """
    for choice in (MOVE_REDO, MOVE_SKIP):
        allowed, _ = _allow(choice, battery="残り少ない", arrival=ARRIVAL_SHORT)
        assert allowed == MOVE_RETURN, f"{choice} was allowed on a low battery"


def test_landing_is_never_refused_by_the_battery_rule():
    """The one action a low battery must not prevent is stopping.

    Substituting `return_home` for a `land` would make the aircraft fly
    further on the battery that prompted the decision.

    電池の規則が妨げてはならない唯一の行動は、止まることであること。

    `land` を `return_home` に置き換えれば、その判断を促した当の電池で、機体を
    さらに飛ばすことになる。
    """
    allowed, _ = _allow(MOVE_LAND, battery="危険")

    assert allowed == MOVE_LAND


def test_holding_after_a_failed_leg_retries_it_rather_than_advancing():
    """A hold does not move the route on past a leg that did not arrive.

    Advancing would treat "wait before deciding" as "the leg was fine",
    which is the opposite of what it says.

    待機が、到達しなかった区間を飛ばして経路を進めないこと。

    進めてしまえば、「決める前に待つ」を「その区間は問題なかった」として扱う
    ことになり、言っていることの逆である。
    """
    allowed, _ = _allow(MOVE_HOLD, arrival=ARRIVAL_SHORT)

    assert allowed == MOVE_REDO


def test_holding_after_a_good_leg_carries_on():
    """With the leg arrived and nothing else wrong, a hold moves on.

    The Arbiter is already hovering the aircraft on its own account and its
    timer turns an endless hold into a landing; the mission does not need a
    second waiting mechanism on top of it.

    区間が到達し、他に異常が無ければ、待機は先へ進むこと。

    Arbiter は自身の判断で既に機体を待機させており、その計時が終わらない待機を
    着陸に変える。ミッションがその上にもう 1 つ待つ仕組みを持つ必要はない。
    """
    allowed, _ = _allow(MOVE_HOLD, arrival=ARRIVAL_AS_PLANNED)

    assert allowed == MOVE_NEXT


def test_the_time_limit_stops_the_route_before_the_next_leg():
    """A mission past its time budget does not start another leg.

    Checked BEFORE a leg rather than after, because a limit reached while a
    leg is in the air would be found out one whole leg too late -- the point
    of a time limit is not to notice the overrun.

    時間の予算を超えたミッションが、次の区間を始めないこと。

    区間の後ではなく前に確認する。空中で上限に達すると、まるまる 1 区間遅れて
    判明するからである。時間の上限は、超過に気づくためのものではない。
    """
    import time as clock
    from dataclasses import replace

    from sfpilot.mission import STOP_TIME_LIMIT, _MissionFlight

    class _Elapsed:
        """A flight that started longer ago than the limit allows.
        上限より前に始まった飛行。"""
        cfg = replace(DEFAULT_CONFIG,
                      mission=replace(DEFAULT_CONFIG.mission, time_limit_s=1.0))
        started = clock.monotonic() - 10.0

    stop = _MissionFlight._check_limits(_Elapsed())

    assert stop is not None, "an overrun mission must be stopped"
    assert stop[0] == STOP_TIME_LIMIT


def test_a_mission_inside_its_time_budget_carries_on():
    """The limit does not fire merely for having been checked.
    上限が、確認しただけで作動しないこと。"""
    import time as clock

    from sfpilot.mission import _MissionFlight

    class _JustStarted:
        cfg = DEFAULT_CONFIG
        started = clock.monotonic()

    assert _MissionFlight._check_limits(_JustStarted()) is None


def test_a_hold_after_a_failed_leg_counts_against_the_retry_limit():
    """A hold resolved into a retry is still a retry.

    Found by measurement: with the limits keyed on Jev's raw choice, a
    `hold` after a failed leg became a retry the counter never recognised,
    and the `drift` scene redid its first leg 63 times before anything
    stopped it.

    やり直しに解決された待機も、やり直しとして数えること。

    実測で判明した。上限を Jev の生の選択で判定していた版では、失敗した区間の
    後の `hold` が、計数に数えられないやり直しになり、`drift` の場面が最初の
    区間を 63 回やり直してから、ようやく何かが止めた。
    """
    limit = DEFAULT_CONFIG.mission.max_retries_per_leg

    allowed, refusal = _allow(MOVE_HOLD, retries=limit, arrival=ARRIVAL_SHORT)

    assert allowed == MOVE_SKIP
    assert "上限" in refusal
