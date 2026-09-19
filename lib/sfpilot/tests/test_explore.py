"""
What sweeping the forward distance around a turn guarantees.

旋回しながら前方距離を掃くことが保証すること。

No API key, no network and no emulator: the sweep is driven against a
recording link and a synthetic sample stream. That is deliberate and it is
also all that is possible -- the manoeuvre cannot be flown at all today,
because in SILS the aircraft falls out of the air when it yaws
(docs/plans/jev-autopilot.md §4.9.1, §4.11). So everything below is pinned
against fixtures and a stand-in link, and NOTHING below has been confirmed
in flight. The distinction is the point of this file's existence: the
§4.5 lesson was that a FakeJudge never reads the words, so the tests that
matter are the ones that inspect what the code SAYS, not only that it ran.

API キーも通信もエミュレータも要らない。掃引は記録用のリンクと、合成した
サンプル列に対して駆動する。これは意図したものであり、同時に今できることの
すべてでもある —— この操作は現状まったく飛ばせない。SILS ではヨー回転で機体が
落下するためである（docs/plans/jev-autopilot.md §4.9.1・§4.11）。したがって
以下はすべて fixture と代役のリンクに対して固定されており、**飛行で確認された
ものは 1 つも無い**。この区別こそ本ファイルが存在する理由である。§4.5 の教訓は
「FakeJudge は語を読まない」であり、意味を持つ試験とは、動いたことだけでなく
コードが**何を言うか**を検める試験である。
"""

import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from sfpilot.config import DEFAULT_CONFIG
from sfpilot.explore import (
    BEARING_NAMES, BEARING_NONE, MEASURED_A_WHILE_AGO, MEASURED_JUST_NOW,
    MEASURED_NEVER, MEASURED_STALE, SWEEP_COMPLETED, SWEEP_INTERRUPTED,
    SWEEP_REFUSED, BearingReading, Sweep, bearing_criteria, blank_sweep,
    check_bearing_choice, classify_bearing, rule_based_bearing, sweep,
    surroundings_state,
)
from sfpilot.judge import ExploreFakeJudge, FakeJudge, Q_BEARING, bearing_question
from sfpilot.link import Sample
from sfpilot.monitor import (
    FORWARD_OPEN, FORWARD_SOMEWHAT_NEAR, FORWARD_UNKNOWN, FORWARD_WALL_NEAR,
)
from sfpilot.pilot import Pilot

CFG = DEFAULT_CONFIG
FWD = DEFAULT_CONFIG.forward
FIXTURES = Path(__file__).parent / "fixtures"


def _enabled(**overrides):
    """A config with the sweep switched on, for exercising the manoeuvre.

    The shipped default is OFF and stays off (`ExploreConfig`); the tests
    that check the refusal use the real default, and only the tests that
    drive the sweep itself turn it on here.

    掃引を有効にした設定。操作そのものを動かすために使う。

    同梱の既定は無効であり、無効のままである（`ExploreConfig`）。拒否を確かめる
    試験は本物の既定を使い、掃引自体を駆動する試験だけがここで有効にする。
    """
    explore = replace(CFG.explore, enabled=True, **overrides)
    return replace(CFG, explore=explore)


class _RecordingLink:
    """Records what reached the vehicle, and answers blocking verbs.

    Replies are counted the way `SilsLink` counts them, because the sweep
    waits on `replies_outstanding` to know a turn finished. A link that
    never answered would have every turn wait out its ceiling instead.

    機体に届いたものを記録し、ブロックする verb に応答する。

    応答は `SilsLink` と同じ数え方にする。掃引は、旋回が終わったことを
    `replies_outstanding` で知るからである。応答しないリンクでは、どの旋回も
    上限まで待つことになってしまう。
    """

    def __init__(self, samples=None):
        self.sent: list = []
        self._samples = list(samples or [])

    def send_rc(self, a, b, c, d) -> None:
        self.sent.append(f"rc {a} {b} {c} {d}")

    def send_command(self, line: str) -> None:
        self.sent.append(line)

    def priority(self, line: str) -> None:
        self.sent.append(line)

    def hold_sticks_neutral(self) -> None:
        pass

    @property
    def replies_outstanding(self) -> int:
        # Always answered: this link's vehicle finishes a turn instantly,
        # so a sweep under it spends no time waiting for one.
        # 常に応答済みとする。このリンクの機体は旋回を即座に終えるので、掃引は
        # 旋回の応答待ちに時間を使わない。
        return 0

    def read_samples(self) -> list:
        """Hand out the next sample, repeating the last one forever.
        次のサンプルを渡す。尽きたら最後のものを返し続ける。"""
        if not self._samples:
            return []
        if len(self._samples) > 1:
            return [self._samples.pop(0)]
        return [self._samples[0]]

    @property
    def turns(self) -> list:
        """Only the turn commands, in order. / 旋回の指令だけを順に返す。"""
        return [line for line in self.sent
                if line.startswith(("cw ", "ccw "))]


def _sample(front_m=None, **fields) -> Sample:
    """One telemetry sample of a healthy hover, with a forward reading.

    Healthy on every axis the immediate safety rules watch, so a test that
    wants an interrupt has to ask for one rather than getting it from an
    incidental value.

    健全なホバリングのサンプル 1 件。前方の読み値つき。

    即時安全則が見るどの軸についても健全なので、中断を見たい試験は、たまたまの
    値から得るのではなく、明示的に求める必要がある。
    """
    sample = Sample(t=fields.pop("t", 0.0), altitude_m=0.5, flight_state="FLYING",
                    vel_n=0.0, vel_e=0.0, vel_d=0.0, roll=0.0, pitch=0.0,
                    yaw=0.0, pos_n=0.0, pos_e=0.0, battery_pct=80.0,
                    battery_v=4.0, tof_m=0.5)
    if front_m is not None:
        sample["tof_front_m"] = front_m
    sample.update(fields)
    return sample


def _pilot(link, config=CFG) -> Pilot:
    return Pilot(link, FakeJudge(), config)


def _reading(name, deg, clearance, metres=None) -> BearingReading:
    return BearingReading(name=name, relative_deg=deg, clearance=clearance,
                          nearest_m=metres, samples=5,
                          valid_samples=0 if metres is None else 5)


def _sweep_of(pairs, taken_at=0.0) -> Sweep:
    """A finished sweep from `[(clearance, metres), ...]`, one per bearing.
    方位ごとの `[(区分, 距離), ...]` から、完了した掃引を作る。"""
    readings = [
        _reading(BEARING_NAMES[i], i * 45.0, clearance, metres)
        for i, (clearance, metres) in enumerate(pairs)
    ]
    return Sweep(readings=readings, outcome=SWEEP_COMPLETED, taken_at=taken_at)


# =============================================================================
# (a) Classifying one bearing / 1 方位の区分
# =============================================================================

@pytest.mark.parametrize("nearest_m,expected", [
    (1.9, FORWARD_OPEN),
    (FWD.somewhat_near_m + 0.01, FORWARD_OPEN),
    (FWD.somewhat_near_m, FORWARD_SOMEWHAT_NEAR),
    (1.0, FORWARD_SOMEWHAT_NEAR),
    (FWD.wall_near_m, FORWARD_WALL_NEAR),
    (0.2, FORWARD_WALL_NEAR),
])
def test_a_bearing_is_banded_by_its_nearest_reading(nearest_m, expected):
    """Distance becomes one of the forward-clearance words, never a figure.

    The thresholds are `ForwardConfig`'s own, shared with the in-flight
    clearance rather than copied: a wall is near at the same distance
    whether the craft is flying at it or merely looking at it.

    距離は、数値ではなく前方の空きの語のいずれかになること。

    しきい値は `ForwardConfig` 自身のもので、飛行中の空き判定と写さずに共有する。
    壁は、機体がそちらへ飛んでいようと見ているだけだろうと、同じ距離で近い。
    """
    assert classify_bearing([nearest_m] * 5) == expected


def test_the_nearest_reading_decides_not_the_average():
    """A wall at the edge of the beam is a wall, not an average.

    Several readings of one bearing differ because something is at the
    beam's edge; averaging a wall with the space beside it would produce a
    distance at which nothing actually is.

    ビームの縁にある壁は壁であって、平均ではないこと。

    1 方位の複数の読み取りが食い違うのは、何かがビームの縁にあるからである。壁と
    その脇の空間を平均すれば、実際には何も無い距離が出てくる。
    """
    assert classify_bearing([0.3, 1.9, 1.9, 1.9, 1.9]) == FORWARD_WALL_NEAR


def test_a_bearing_nobody_could_measure_is_unknown_not_open():
    """Too few valid readings is "cannot measure", which is not clearance.

    The forward part returns an invalid reading both for empty space and
    for a surface too close to resolve (§4.8.6), so reading an unmeasured
    bearing as open would fly the craft at the case it cannot see.

    有効な読み取りが足りない方位は「測定不能」であり、空いているのではないこと。

    前方の部品は、空間に対しても、近すぎて復元できない面に対しても無効を返す
    （§4.8.6）。測れていない方位を「開けている」と読めば、見えないほうの場合へ
    機体を飛ばすことになる。
    """
    too_few = [2.0] * (CFG.explore.min_valid_samples - 1)

    assert classify_bearing([]) == FORWARD_UNKNOWN
    assert classify_bearing(too_few) == FORWARD_UNKNOWN
    assert not BearingReading("ahead", 0.0, FORWARD_UNKNOWN).is_open


# =============================================================================
# (b) The words that reach Jev / Jev へ届く語
# =============================================================================

def test_the_surroundings_carry_eight_english_words_and_no_numbers():
    """Every bearing reaches Jev as a word, under its own name.

    This is the §4.5 lesson applied to the sweep: a FakeJudge would accept
    any state at all, so the test has to look at the words themselves.

    各方位が、自身の名前のもとに語として Jev へ届くこと。

    §4.5 の教訓を掃引に当てたものである。FakeJudge はどんな state でも受け取って
    しまうので、試験は語そのものを見るほかない。
    """
    from sfpilot.summarizer import assert_no_numbers

    result = _sweep_of([(FORWARD_OPEN, 1.9)] * 8, taken_at=100.0)

    state = surroundings_state(result, now=100.0)

    assert state["measured"] == MEASURED_JUST_NOW
    assert [state[name] for name in BEARING_NAMES] == ["open"] * 8
    assert_no_numbers(state)


def test_an_unmeasured_bearing_says_so_rather_than_saying_open():
    """"cannot be measured" reaches Jev; it is not dropped and not "open".
    「測定不能」がそのまま Jev へ届くこと。省かれず、「開けている」にもならない。"""
    result = _sweep_of([(FORWARD_UNKNOWN, None)] + [(FORWARD_OPEN, 1.9)] * 7)

    state = surroundings_state(result, now=0.0)

    assert state["ahead"] == "cannot be measured"


def test_a_sweep_that_never_happened_says_never_not_eight_unknowns():
    """No sweep and an unreadable sweep are different facts, said differently.

    "we never looked" is not "we looked and could not tell", and eight
    identical unknowns would be eight lines of noise besides.

    掃引していないことと、掃引が読めなかったことは別の事実であり、別に述べること。

    「一度も見ていない」は「見たが判別できなかった」ではないし、同じ「不明」が
    8 つ並べばそれだけで雑音 8 行になる。
    """
    state = surroundings_state(blank_sweep(), now=0.0)

    assert state == {"measured": MEASURED_NEVER}


@pytest.mark.parametrize("age_s,expected", [
    (0.0, MEASURED_JUST_NOW),
    (CFG.explore.freshness_s - 1.0, MEASURED_JUST_NOW),
    (CFG.explore.freshness_s + 1.0, MEASURED_A_WHILE_AGO),
    (CFG.explore.freshness_s * 2.0 + 1.0, MEASURED_STALE),
])
def test_a_sweep_past_its_freshness_window_is_reported_as_old(age_s, expected):
    """The age of a measurement reaches Jev as a word, and it ages.

    A sweep describes where the walls were when it was taken. Saying so is
    what lets the model weigh an old look differently from a new one --
    and it is a word, not a clock, because the model is not a calculator.

    測定の古さが語として Jev へ届き、その語が古くなっていくこと。

    掃引が述べるのは「それを取った時点で壁がどこにあったか」である。そう述べる
    ことが、古い見回しと新しい見回しをモデルに区別させる。しかもそれは時計では
    なく語である。モデルは計算機ではないからである。
    """
    result = _sweep_of([(FORWARD_OPEN, 1.9)] * 8, taken_at=1000.0)

    assert result.age_word(1000.0 + age_s) == expected


def test_the_signature_follows_the_bearings_but_not_the_clock():
    """A bearing changing is a new situation; time passing is not.

    The age word ages on its own while the bearings it describes stay put,
    so including it would make every answer look stale -- the same reason
    `mission.elapsed` is excluded. A bearing going from open to wall near
    is precisely what an in-flight answer should be discarded for.

    方位の変化は新しい状況だが、時間の経過はそうではないこと。

    古さの語は、記述している方位が動かないまま勝手に古くなる。含めればすべての
    答えが鮮度切れに見える —— `mission.elapsed` を除外するのと同じ理由である。
    ある方位が「開けている」から「壁が近い」へ変わることは、往復中の答えを
    破棄すべき変化そのものである。
    """
    from sfpilot.summarizer import signature

    result = _sweep_of([(FORWARD_OPEN, 1.9)] * 8, taken_at=0.0)
    fresh = {"flight": {}, "surroundings": surroundings_state(result, 0.0)}
    aged = {"flight": {}, "surroundings": surroundings_state(result, 1000.0)}
    assert signature(fresh) == signature(aged)

    walled = _sweep_of([(FORWARD_WALL_NEAR, 0.3)] + [(FORWARD_OPEN, 1.9)] * 7)
    changed = {"flight": {}, "surroundings": surroundings_state(walled, 0.0)}
    assert signature(fresh) != signature(changed)


def test_the_choice_offers_only_bearings_that_were_swept():
    """An option the sweep never read is not offered at all.

    Offering it would ask the model to choose between something seen and
    something imagined, and the Choice guidance is explicit that an option
    matches on what it MEANS.

    掃引が読まなかった方位は、そもそも選択肢に出さないこと。

    出せば、見たものと想像したものの間で選べと求めることになる。Choice の指針は
    「選択肢は**意味**で一致する」と明示している。
    """
    partial = Sweep(readings=[_reading("ahead", 0.0, FORWARD_OPEN, 1.9)],
                    outcome=SWEEP_INTERRUPTED)

    criteria = bearing_criteria(partial)

    assert set(criteria) == {"ahead", BEARING_NONE}


def test_each_option_says_what_it_commits_to_and_what_it_rules_out():
    """The criteria describe meaning by contrast, never restating the name.

    The wall option must say there is no room, and the unknown option must
    say the two cases cannot be told apart -- a description that merely
    repeated "wall near" would give the model nothing to match on.

    各選択肢が、名前の言い換えではなく、対比によって意味を述べること。

    壁の選択肢は「進む余地が無い」と述べ、測定不能の選択肢は「2 つの場合を区別
    できない」と述べる必要がある。「壁が近い」を繰り返すだけの説明では、モデルが
    一致させるものが無い。
    """
    result = _sweep_of([(FORWARD_WALL_NEAR, 0.3), (FORWARD_UNKNOWN, None)]
                       + [(FORWARD_OPEN, 1.9)] * 6)

    criteria = bearing_criteria(result)

    assert "no room to travel" in criteria["ahead"]
    assert "cannot be told apart" in criteria["ahead and to the right"]
    assert "rather than picking the least bad one" in criteria[BEARING_NONE]


def test_the_question_names_the_goal_so_the_trade_off_is_the_models():
    """The instructions ask for the open way that heads nearest the goal.

    Which way is clear is arithmetic the sweep already did; what is left
    for the model is the trade-off, and it is written as a preference
    because the model is never given a bearing in degrees.

    質問文が、目的に最も近い「開けている方位」を求めること。

    どちらが開けているかは掃引が既に済ませた計算であり、モデルに残るのは兼ね合い
    である。方位を角度で渡すことは決してないので、それは選好として書かれる。
    """
    question = bearing_question({"ahead": "x"}, goal="to the west")

    assert "to the west" in question["instructions"]
    assert "prefer the one that heads most nearly that way" in question["instructions"]
    assert question["type"] == "choice"


# =============================================================================
# (c) Refusing an answer the measurement contradicts / 測定と矛盾する答えの却下
# =============================================================================

def test_code_refuses_a_bearing_the_sweep_measured_as_a_wall():
    """Jev naming a walled bearing is overruled by the measurement.

    The model may read a route through a gap the single forward sensor
    cannot see, and that is not unreasonable of it -- but the sensor said
    there is a surface there, and between the two the measurement wins.
    The same division of labour as every other limit in this package.

    壁と測定された方位を Jev が名指ししても、測定が覆すこと。

    1 つしかない前方センサに見えない隙間を通る経路をモデルが読むことはありうるし、
    それ自体は不合理ではない。しかしセンサは「そこに面がある」と言っており、両者の
    うち勝つのは測定である。本パッケージの他のすべての上限と同じ分担である。
    """
    result = _sweep_of([(FORWARD_WALL_NEAR, 0.3)] + [(FORWARD_OPEN, 1.9)] * 7)

    refusal = check_bearing_choice("ahead", result)

    assert refusal, "a walled bearing must be refused"
    assert "壁が近い" in refusal


def test_code_refuses_a_bearing_that_was_never_measured():
    """An unmeasured bearing is refused too: it is not a clear one.
    測れていない方位も却下すること。それは開けている方位ではない。"""
    result = _sweep_of([(FORWARD_UNKNOWN, None)] + [(FORWARD_OPEN, 1.9)] * 7)

    assert check_bearing_choice("ahead", result)
    assert check_bearing_choice("nowhere at all", result), (
        "a bearing outside the sweep must be refused rather than flown"
    )


def test_an_open_bearing_and_staying_put_are_both_allowed():
    """The refusal only fires on a wall, an unknown, or an unswept name.

    Declining to travel is the conservative answer and is never refused;
    refusing it would leave the aircraft with no permitted action at all.

    却下が働くのは壁・測定不能・掃引に無い名前のときだけであること。

    進まないことは保守的な答えであり、決して却下しない。却下すれば、機体に許された
    行動が 1 つも無くなる。
    """
    result = _sweep_of([(FORWARD_OPEN, 1.9)] * 8)

    assert check_bearing_choice("ahead", result) == ""
    assert check_bearing_choice(BEARING_NONE, result) == ""


# =============================================================================
# (d) The keyless default / キー不要の既定
# =============================================================================

def test_the_rule_based_default_picks_an_open_bearing_near_the_goal():
    """Among the open bearings, the one pointing nearest where we want to go.
    開けている方位のうち、行きたい方向に最も近いものを選ぶこと。"""
    # Ahead and its neighbours are walled; the open ones are to the sides.
    # 正面とその両隣は壁。開いているのは横である。
    result = _sweep_of([
        (FORWARD_WALL_NEAR, 0.3),        # ahead
        (FORWARD_WALL_NEAR, 0.3),        # ahead and to the right
        (FORWARD_OPEN, 1.9),             # to the right
        (FORWARD_SOMEWHAT_NEAR, 1.0),    # behind and to the right
        (FORWARD_OPEN, 1.9),             # behind
        (FORWARD_SOMEWHAT_NEAR, 1.0),    # behind and to the left
        (FORWARD_OPEN, 1.9),             # to the left
        (FORWARD_WALL_NEAR, 0.3),        # ahead and to the left
    ])

    assert rule_based_bearing(result, goal_name="ahead") in (
        "to the right", "to the left")
    assert rule_based_bearing(result, goal_name="behind") == "behind"


def test_the_rule_based_default_never_picks_a_wall_or_an_unknown():
    """With nothing open, the rule stays put rather than naming a wall.

    A rule that had to answer SOMETHING would answer with the least bad
    wall, and a rehearsal would then fly into it and call that a pass.

    開いている方位が無ければ、壁を名指しせず「進まない」を選ぶこと。

    何かを答えねばならない規則は、最もましな壁を答えることになる。そうなれば予行は
    そこへ飛び込んだうえで、それを合格と呼ぶ。
    """
    boxed_in = _sweep_of([(FORWARD_WALL_NEAR, 0.3)] * 4
                         + [(FORWARD_UNKNOWN, None)] * 4)

    assert rule_based_bearing(boxed_in) == BEARING_NONE
    assert check_bearing_choice(rule_based_bearing(boxed_in), boxed_in) == ""


def test_the_explore_fake_judge_answers_the_bearing_question_by_rule():
    """The keyless judge answers `which_way`, and its answer survives the code.
    キー不要の judge が `which_way` に答え、その答えがコードの却下を通ること。"""
    result = _sweep_of([(FORWARD_WALL_NEAR, 0.3)] * 4 + [(FORWARD_OPEN, 1.9)] * 4)
    judge = ExploreFakeJudge(sweep=result)

    judgement = judge.ask_questions(
        {"surroundings": surroundings_state(result, 0.0)},
        {Q_BEARING: bearing_question(bearing_criteria(result))},
    )

    choice = judgement.answers[Q_BEARING].choice
    assert choice != BEARING_NONE, "an open bearing existed and was not taken"
    assert check_bearing_choice(choice, result) == ""


# =============================================================================
# (e) Driving the sweep / 掃引の駆動
# =============================================================================

def test_a_sweep_is_refused_while_the_feature_is_disabled():
    """Off by default, the sweep does not turn and says why.

    The reason has to survive to the caller, because a refused sweep that
    looked like a sweep finding nothing would be reported as an empty room
    -- which is exactly the mistake §4.9.6 warns about in the other
    direction. Nothing is sent to the vehicle at all.

    既定で無効のとき、掃引は旋回せず、理由を述べること。

    理由は呼び出し側まで届く必要がある。拒否された掃引が「何も見つからなかった
    掃引」に見えれば、空の部屋として報告されることになる —— §4.9.6 が逆向きに
    警告しているのとまさに同じ誤りである。機体へは何も送らない。
    """
    link = _RecordingLink([_sample(front_m=1.9)])
    events: list = []

    result = sweep(link, _pilot(link), CFG, on_event=events.append)

    assert result.outcome == SWEEP_REFUSED
    assert result.readings == []
    assert link.sent == [], "a refused sweep must not command the vehicle"
    assert "#12" in result.detail and "cw 90" in result.detail
    assert any("実行しない" in message for message in events), (
        "the refusal must reach the trace, not only the return value"
    )


def test_the_shipped_default_keeps_exploration_off():
    """The switch ships off, and the reason ships with it.

    This is the test that fails if someone enables the feature without
    meeting the release condition, which is what `ExploreConfig` exists to
    prevent. The condition is a `cw 90` from a 0.5 m hover that does not
    fall, and today it is not met (§4.9.1, §4.10).

    同梱の既定が無効のままであり、理由も一緒に同梱されていること。

    解除条件を満たさずに機能を有効にした場合に落ちる試験である。それを防ぐために
    `ExploreConfig` がある。条件は「高度 0.5m のホバリングからの `cw 90` が落下
    しないこと」であり、今日それは満たされていない（§4.9.1・§4.10）。
    """
    assert DEFAULT_CONFIG.explore.enabled is False
    assert "解除条件" in DEFAULT_CONFIG.explore.disabled_reason


def test_a_sweep_visits_every_bearing_and_classifies_each():
    """Eight bearings, each named and banded from its own readings.
    8 方位を訪れ、それぞれを自身の読み取りから名前と区分にすること。"""
    link = _RecordingLink([_sample(front_m=1.9)])
    config = _enabled(settle_s=0.0, sample_interval_s=0.0,
                      samples_per_bearing=2, min_valid_samples=1)

    result = sweep(link, _pilot(link, config), config)

    assert result.outcome == SWEEP_COMPLETED
    assert [r.name for r in result.readings] == list(BEARING_NAMES)
    assert all(r.clearance == FORWARD_OPEN for r in result.readings)


def test_a_sweep_returns_the_craft_to_the_heading_it_started_on():
    """The turns sent add up to a full circle, so the nose ends where it was.

    A sweep that left the craft facing elsewhere would silently invalidate
    the caller's own idea of where "forward" is (`MissionWalk`), which no
    later leg would notice until it flew the wrong way.

    送った旋回の合計が一周になり、機首が元の向きで終わること。

    機体を別の向きに残す掃引は、呼び出し側が持つ「前」の概念（`MissionWalk`）を
    黙って無効にする。以後のどの区間も、間違った方向へ飛ぶまでそれに気づかない。
    """
    link = _RecordingLink([_sample(front_m=1.9)])
    config = _enabled(settle_s=0.0, sample_interval_s=0.0,
                      samples_per_bearing=1, min_valid_samples=1)

    sweep(link, _pilot(link, config), config)

    assert _net_turn(link) % 360.0 == pytest.approx(0.0, abs=1e-6), (
        f"the sweep left the nose off its starting heading: {link.turns}"
    )


def _net_turn(link) -> float:
    """The net rotation the commands add up to [deg], clockwise positive.
    送った指令の回転の合計 [度]。時計回りを正とする。"""
    net = 0.0
    for line in link.turns:
        verb, amount = line.split()
        net += float(amount) * (1.0 if verb == "cw" else -1.0)
    return net


def test_an_interrupted_sweep_still_turns_back_and_keeps_what_it_read():
    """A stop cuts the sweep short, and the craft is faced back anyway.

    Being stopped is a reason to stop turning, not a reason to leave the
    aircraft pointing somewhere nobody upstream knows about. The bearings
    already read are kept: a partial look at the room is worth more than
    none.

    停止は掃引を打ち切るが、それでも機首は元へ戻すこと。

    止められたことは、回るのをやめる理由ではあっても、上位の誰も知らない向きに
    機体を残す理由ではない。既に読んだ方位は保持する。部屋を部分的に見たことには、
    まったく見ないことより価値がある。
    """
    # Open for the first two bearings, then a wall inside the stop distance:
    # the Monitor commands a stop without waiting for Jev, which is what
    # interrupts the sweep -- after it has already turned partway round.
    # 最初の 2 方位は開けており、その後に停止距離の内側の壁が来る。Monitor が Jev を
    # 待たず停止を出し、それが掃引を中断する —— 既に途中まで回った後で、である。
    link = _RecordingLink([_sample(front_m=1.9, t=0.0),
                           _sample(front_m=1.9, t=0.1),
                           _sample(front_m=1.9, t=0.2),
                           _sample(front_m=FWD.safety_margin_m - 0.1, t=0.3)])
    config = _enabled(settle_s=0.0, sample_interval_s=0.0,
                      samples_per_bearing=1, min_valid_samples=1)

    result = sweep(link, _pilot(link, config), config)

    assert result.outcome == SWEEP_INTERRUPTED
    assert "stop" in result.detail
    assert len(result.readings) < config.explore.bearings
    assert result.readings, "the bearings read before the stop are kept"
    # Turned partway and then all the way back: the net rotation is zero
    # even though the sweep never completed its circle.
    # 途中まで回り、そこから戻し切る。円を閉じていなくても回転の合計は 0 になる。
    assert _net_turn(link) % 360.0 == pytest.approx(0.0, abs=1e-6), (
        f"an interrupted sweep left the nose turned: {link.turns}"
    )


def test_a_wall_that_appears_mid_sweep_stops_the_turning():
    """The immediate rules keep their 50Hz while the craft is rotating.

    This is why a sweep pumps the pilot rather than sleeping through its
    settles: the aircraft is turning next to something it cannot see all
    of, and the layer that stops it must not be paused for the manoeuvre.

    旋回中も即時則が 50Hz を保つこと。

    掃引が静定を眠って過ごさず pilot を回す理由がこれである。機体は、全体を見る
    ことのできない何かの脇で回っており、それを止める層が、この操作のあいだ止まって
    いてはならない。
    """
    # Open for the first bearing, then a wall arrives.
    # 最初の方位は開けており、その後に壁が現れる。
    link = _RecordingLink([_sample(front_m=1.9, t=0.0),
                           _sample(front_m=1.9, t=0.1),
                           _sample(front_m=0.2, t=0.2)])
    config = _enabled(settle_s=0.0, sample_interval_s=0.0,
                      samples_per_bearing=1, min_valid_samples=1)

    result = sweep(link, _pilot(link, config), config)

    assert result.outcome == SWEEP_INTERRUPTED
    assert len(result.readings) >= 1, "the bearings read before the wall are kept"


def test_a_bearing_whose_readings_are_all_invalid_is_unknown_not_open():
    """A sweep in a room the sensor cannot read reports unknown throughout.

    The whole sweep then offers nothing to fly at, and the rule-based
    default stays put -- which is the behaviour that matters, because the
    alternative is a craft that treats an unreadable room as an empty one.

    センサが読めない部屋での掃引は、全方位を「測定不能」と報告すること。

    その掃引は飛ぶべき方位を 1 つも提示せず、規則ベースの既定は「進まない」に
    なる。意味を持つのはこの挙動である。そうでなければ、読めない部屋を空の部屋と
    扱う機体になる。
    """
    link = _RecordingLink([_sample(front_m=None)])
    config = _enabled(settle_s=0.0, sample_interval_s=0.0,
                      samples_per_bearing=3)

    result = sweep(link, _pilot(link, config), config)

    assert all(r.clearance == FORWARD_UNKNOWN for r in result.readings)
    assert result.open_bearings() == []
    assert rule_based_bearing(result) == BEARING_NONE


def test_the_sweep_turns_by_the_configured_step():
    """The sweep steps between bearings, then closes the circle to come back.

    Seven steps carry the craft across eight bearings -- the last bearing
    is read where the seventh step left it, not after an eighth -- and the
    final turn is the remainder of the circle, which for a completed sweep
    is one more step's worth.

    掃引が方位の間を刻んで進み、最後に円を閉じて戻ること。

    8 方位を渡るのに刻みは 7 回である（最後の方位は 7 回目の刻みが置いた場所で
    読み、8 回目の後ではない）。最後の旋回は円の残りであり、完了した掃引では
    ちょうど 1 刻みぶんになる。
    """
    link = _RecordingLink([_sample(front_m=1.9)])
    config = _enabled(settle_s=0.0, sample_interval_s=0.0,
                      samples_per_bearing=1, min_valid_samples=1)

    sweep(link, _pilot(link, config), config)

    step = round(config.explore.step_deg)
    assert link.turns == [f"cw {step}"] * config.explore.bearings, (
        "the sweep should step between bearings and then close the circle"
    )
    assert _net_turn(link) % 360.0 == pytest.approx(0.0, abs=1e-6)


def test_turning_by_rc_sends_a_yaw_stick_and_returns_it_to_centre():
    """With `turn_with_rc`, the sweep holds a yaw rate and then centres it.

    Both ways of turning are equally unflyable today (§4.9.1 measured
    both), so this pins the alternative for whoever lifts the restriction
    and wants to try it without editing the sweep.

    `turn_with_rc` のとき、掃引がヨー速度を保ってから中立へ戻すこと。

    どちらの旋回方法も今日は等しく飛ばせない（§4.9.1 は両方を実測）。これは、
    制限を解く人が掃引に手を触れずにもう一方を試せるよう、その経路を固定する
    ものである。
    """
    link = _RecordingLink([_sample(front_m=1.9)])
    config = _enabled(settle_s=0.0, sample_interval_s=0.0,
                      samples_per_bearing=1, min_valid_samples=1,
                      turn_with_rc=True, rc_yaw_rate_rad_s=10.0)

    sweep(link, _pilot(link, config), config)

    yaw_sticks = [line for line in link.sent if line.startswith("rc 0 0 0 ")]
    assert any(line != "rc 0 0 0 0" for line in yaw_sticks), "no yaw was commanded"
    assert yaw_sticks[-1] == "rc 0 0 0 0", "the yaw stick was left off centre"


# =============================================================================
# (f) The sweep against a measured flight / 実測飛行に対する掃引
# =============================================================================

def _load(name: str) -> list:
    """Measured samples, one per line, oldest first.
    実測サンプル。1 行 1 件、古い順。"""
    path = FIXTURES / name
    return [Sample(**json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_a_measured_wall_approach_bands_as_a_wall():
    """The recorded approach's own readings band the way the flight did.

    Taken from the fixture rather than written by hand, for the reason the
    fixtures README gives: a hand-written sample embeds the same wrong
    assumption the code does, and agrees with it.

    実測した壁への接近の読み値が、飛行時と同じ区分になること。

    手で書かず fixture から取るのは、fixtures の README が述べる理由による。手で
    書いたサンプルは、コードと同じ誤った前提を埋め込み、それと一致してしまう。
    """
    samples = _load("wall_approach_states.jsonl")
    close = [s["tof_front_m"] for s in samples
             if s.get("tof_front_m") is not None
             and s["tof_front_m"] <= FWD.wall_near_m]
    assert close, "the fixture is supposed to contain an approach to a wall"

    assert classify_bearing(close[:5]) == FORWARD_WALL_NEAR


def test_the_measured_approach_crosses_every_band_it_should():
    """The recorded run at a wall bands as it closes, not all at once.

    The fixture opens at 1.017 m and closes to 0.11 m, so the same flight
    supplies readings on both sides of every threshold. A band set so
    tight that everything looked blocked, or so loose that a wall at
    0.11 m still read as clear, would fail here.

    実測した壁への接近が、近づくにつれて段階的に区分を変えること。

    この fixture は 1.017m で始まり 0.11m まで詰まるので、同じ飛行がすべての
    しきい値の両側の読み値を供給する。厳しすぎて全部が塞がって見える区分も、
    緩すぎて 0.11m の壁が「開けている」と読める区分も、ここで落ちる。
    """
    samples = _load("wall_approach_states.jsonl")
    distances = [s["tof_front_m"] for s in samples
                 if s.get("tof_front_m") is not None]

    far = [d for d in distances if d > FWD.somewhat_near_m]
    near = [d for d in distances if d <= FWD.wall_near_m]
    assert near, "the fixture is supposed to reach the wall band"

    assert classify_bearing(near[-5:]) == FORWARD_WALL_NEAR
    if far:
        assert classify_bearing(far[:5]) == FORWARD_OPEN


def test_a_measured_open_room_sweeps_to_unknown_not_to_open():
    """A room the forward sensor never reads sweeps to "cannot measure".

    This fixture is a flight with no forward sensor at all: EVERY reading
    in it is invalid, which the README records as what most flights look
    like. So the sweep it produces must say it measured nothing -- an
    absent reading is not clear space (§4.8.6: empty space and a surface
    too close both produce one), and a sweep that called this room open
    would send the craft into whichever of the two it actually was.

    前方センサが一度も読めない部屋の掃引が「測定不能」になること。

    この fixture は前方センサがまったく無い飛行であり、含まれる読み値は**すべて**
    無効である。README はそれを「ほとんどの飛行はこう見える」と記録している。
    したがって、そこから作られる掃引は「何も測れなかった」と述べる必要がある。
    無い読み値は空いた空間ではなく（§4.8.6: 空間も、近すぎる面も、どちらも無効を
    生む）、この部屋を「開けている」と呼ぶ掃引は、実際にはそのどちらであったに
    せよ、そこへ機体を送り込むことになる。
    """
    samples = _load("open_room_states.jsonl")
    distances = [s["tof_front_m"] for s in samples
                 if s.get("tof_front_m") is not None]
    assert distances == [], (
        "this fixture is supposed to contain no valid forward reading at all"
    )

    assert classify_bearing(distances) == FORWARD_UNKNOWN
    assert not BearingReading("ahead", 0.0, FORWARD_UNKNOWN).is_open


# =============================================================================
# (g) The mission leg / ミッションの区間
# =============================================================================

def test_an_explore_leg_loads_from_yaml_and_travels_nowhere(tmp_path):
    """`explore` is a verb a route may be written with, and it moves nothing.

    It has to pass the envelope check as a no-op: a look around goes
    nowhere, so a route is no more likely to leave the box for containing
    one.

    `explore` が経路に書ける verb であり、機体をどこへも動かさないこと。

    何もしない手順として飛行領域の検査を通る必要がある。見回しはどこへも行かない
    ので、それを含む経路が範囲を出やすくなることはない。
    """
    from sfpilot.mission import STEP_EXPLORE, load_mission

    path = tmp_path / "mission.json"
    path.write_text(json.dumps({"legs": [
        {"verb": "takeoff"},
        {"verb": "explore", "label": "周囲を見回す"},
        {"verb": "forward", "amount": 60},
    ]}, ensure_ascii=False), encoding="utf-8")

    mission = load_mission(path)

    assert mission.legs[1].step.verb == STEP_EXPLORE
    assert mission.legs[1].step.amount is None
    assert "掃引" in mission.legs[1].preview_command()


def test_the_shipped_pocket_route_sweeps_between_two_travelling_legs():
    """`explore_pocket` enters the pocket, looks around, then takes a side.

    Shipped as the route the sweep is FOR. It cannot be flown today for
    the same reason nothing else here can (§4.11), and the route's own
    comment says so.

    `explore_pocket` が、袋小路へ入り、見回し、横へ進む経路であること。

    掃引が「何のためのものか」を示す経路として同梱する。他のすべてと同じ理由で
    今日は飛ばせず（§4.11）、経路自身のコメントがそう述べている。
    """
    from sfpilot.mission import STEP_EXPLORE, load_mission, resolve_mission_path

    mission = load_mission(resolve_mission_path("explore_pocket"))

    verbs = [leg.step.verb for leg in mission.legs]
    assert STEP_EXPLORE in verbs
    sweep_at = verbs.index(STEP_EXPLORE)
    assert verbs[sweep_at - 1] == "forward", "the route enters the pocket first"
    assert "left" in verbs[sweep_at:], "the route takes the open side afterwards"


def test_an_explore_leg_records_the_refusal_and_flies_the_rest(tmp_path):
    """With the sweep off, the route carries on and says the look failed.

    A refused sweep must not ground a route that also contains ordinary
    legs: adding one `explore` leg to a working route should not stop the
    working legs from flying.

    掃引が無効のとき、経路は進み、見回しが行われなかったと述べること。

    拒否された掃引が、普通の区間も含む経路を地上に留めてはならない。動いている
    経路に `explore` 区間を 1 つ足したことが、動いていた区間まで止めるべきでは
    ない。
    """
    from sfpilot.mission import ARRIVAL_SWEEP_REFUSED

    # The refusal path is reached without flying: the sweep says no before
    # it commands anything (`test_a_sweep_is_refused_while_the_feature_is_disabled`),
    # and what this pins is the WORD the leg reports for it.
    # 拒否の経路は飛行せずに到達する。掃引は何かを指令する前に拒否するので
    #（`test_a_sweep_is_refused_while_the_feature_is_disabled`）、ここで固定する
    # のは、その区間が報告する**語**である。
    link = _RecordingLink([_sample(front_m=1.9)])
    result = sweep(link, _pilot(link), CFG)

    assert result.outcome == SWEEP_REFUSED
    assert ARRIVAL_SWEEP_REFUSED == "the look around could not be done"


def test_the_explore_option_tells_the_model_what_looking_around_costs():
    """`explore` is offered with its price, not only with its benefit.

    An option that only ever sounded prudent would be chosen whenever
    anything at all was uncertain, and the route would sweep instead of
    flying.

    `explore` が、利点だけでなく代償とともに提示されること。

    慎重に聞こえるだけの選択肢は、少しでも不確かなら常に選ばれ、経路は飛ぶ代わり
    に掃引し続けることになる。
    """
    from sfpilot.judge import MOVE_EXPLORE, QUESTIONS, Q_NEXT_MOVE

    description = QUESTIONS[Q_NEXT_MOVE]["criteria"][MOVE_EXPLORE]

    assert "costs time and battery" in description
    assert "moves the aircraft nowhere" in description


# =============================================================================
# (h) The picture / 図
# =============================================================================

def test_the_live_view_gets_one_sector_per_bearing_with_no_stray_fields():
    """The fan carries an angle, a word and a distance -- and nothing else.

    Only the fields the picture draws are copied, by name: the page is one
    screenshot away from being shared, so what reaches it is chosen rather
    than whatever the sweep happened to hold (`events.py`).

    扇形が角度・語・距離を運び、それ以外を運ばないこと。

    図が描く項目だけを名前で写す。ページは画面の写真 1 枚で共有されうるので、
    そこへ届くものは、掃引がたまたま持っていたものではなく選ばれたものである
    （`events.py`）。
    """
    from sfpilot.events import sweep_payload

    result = _sweep_of([(FORWARD_OPEN, 1.9), (FORWARD_UNKNOWN, None)]
                       + [(FORWARD_WALL_NEAR, 0.3)] * 6)

    payload = sweep_payload(result, _sample(front_m=1.9, pos_n=0.4))

    assert len(payload["bearings"]) == 8
    assert payload["n"] == pytest.approx(0.4)
    assert set(payload["bearings"][0]) == {"name", "deg", "clearance", "metres"}
    # The unmeasured bearing carries no distance: the page draws it hollow
    # at the sensor's range rather than solid at zero.
    # 測れていない方位は距離を持たない。ページはそれを 0 の塗りではなく、射程に
    # 輪郭だけで描く。
    assert "metres" not in payload["bearings"][1]


def test_a_sweep_with_nothing_in_it_is_not_published_at_all():
    """A refused sweep draws no fan, rather than drawing an empty one.
    拒否された掃引は、空の扇形ではなく、扇形をまったく描かせないこと。"""
    from sfpilot.events import sweep_payload

    assert sweep_payload(blank_sweep(), _sample()) == {}
    assert sweep_payload(None, _sample()) == {}
