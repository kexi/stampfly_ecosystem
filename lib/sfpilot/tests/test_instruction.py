"""
What turning a spoken instruction into a sequence of moves guarantees.

話した指示を動作の列に変えることが保証すること。

No API key and no network: a FakeJudge supplies the answers, which is the
point of the split -- the assembly rules, the numbers and the envelope
check are all code, so they can be pinned exactly. Whether Jev picks the
right move for a given sentence is a different question, measured against
the live model by `sf pilot say --eval`.

API キーも通信も不要。答えは FakeJudge が与える。それがこの分割の要点で
ある — 組み立て規則・数値・飛行領域の検査はすべてコードなので、厳密に固定できる。
ある文に対して Jev が正しい動作を選ぶかどうかは別の問いであり、
`sf pilot say --eval` が実際のモデルに対して測る。
"""

import time

import pytest

from sfpilot.config import DEFAULT_CONFIG
from sfpilot.instruction import (
    build_plan, check_envelope, extract_numbers, translate,
)
from sfpilot.judge import (
    AMOUNT_LARGE, AMOUNT_MEDIUM, AMOUNT_SMALL, AMOUNT_UNSPECIFIED,
    Answer, Judgement, Q_STEP_AMOUNT, Q_STEP_MOVE,
    STEP_BACK, STEP_DOWN, STEP_FORWARD, STEP_LAND, STEP_LEFT, STEP_NONE,
    STEP_RETURN_HOME, STEP_RIGHT, STEP_TAKEOFF, STEP_TURN_LEFT,
    STEP_TURN_RIGHT, STEP_UP,
)

CFG = DEFAULT_CONFIG.instruction


def _judgement(moves: list, amounts: list = None, confidence: float = 0.95,
               error: str = None) -> Judgement:
    """A Judgement answering `moves` for steps 1..N, padded with `none`.
    手順 1..N に `moves` を答える Judgement。残りは `none` で埋める。"""
    if error is not None:
        return Judgement(error=error)
    amounts = amounts or [AMOUNT_UNSPECIFIED] * len(moves)
    answers = {}
    for index in range(1, CFG.max_steps + 1):
        move = moves[index - 1] if index <= len(moves) else STEP_NONE
        amount = amounts[index - 1] if index <= len(amounts) else AMOUNT_UNSPECIFIED
        answers[Q_STEP_MOVE.format(index)] = Answer(
            kind="choice", choice=move, confidence=confidence,
            probabilities={move: confidence},
        )
        answers[Q_STEP_AMOUNT.format(index)] = Answer(
            kind="choice", choice=amount, confidence=confidence,
            probabilities={amount: confidence},
        )
    return Judgement(answers=answers, latency_ms=10.0)


def _plan(moves: list, instruction: str = "", amounts: list = None,
          confidence: float = 0.95, **kwargs):
    return build_plan(instruction, _judgement(moves, amounts, confidence),
                      **kwargs)


def _verbs(plan) -> list:
    return [step.verb for step in plan.steps]


# =============================================================================
# Numbers the operator said / 操作者が言った数値
# =============================================================================

@pytest.mark.parametrize("text,expected_cm", [
    ("前に70cm進んで", 70.0),
    ("前に 70 cm 進んで", 70.0),
    ("1m上がって", 100.0),
    ("1.5m進んで", 150.0),
    ("50センチ進んで", 50.0),
    ("１００ｃｍ進んで", 100.0),          # full-width digits and unit / 全角
    ("一メートル上がって", 100.0),        # kanji numeral / 漢数字
    ("五十センチ進んで", 50.0),
])
def test_a_distance_is_read_in_centimetres_however_it_was_written(text, expected_cm):
    """Half-width, full-width and kanji figures all reach the same centimetres.

    The operator writes the figure however they write it; the conversion to
    the vehicle's own unit is this layer's job, never the model's.

    半角・全角・漢数字のいずれで書かれても、同じ cm に到達すること。

    数値の書き方は操作者のもので、機体の単位への変換はこの層の仕事である
    （モデルの仕事ではない）。
    """
    numbers = extract_numbers(text)

    assert len(numbers) == 1, f"read {[n.text for n in numbers]}"
    assert numbers[0].value == pytest.approx(expected_cm)
    assert numbers[0].unit == "cm"


@pytest.mark.parametrize("text,expected_deg", [
    ("90度回って", 90.0),
    ("９０度回って", 90.0),
    ("九十度回って", 90.0),
    ("180°回って", 180.0),
])
def test_an_angle_is_read_in_degrees(text, expected_deg):
    """A figure with a degree unit becomes degrees, not a distance.
    度の単位を伴う数値は、距離ではなく度になること。"""
    numbers = extract_numbers(text)

    assert len(numbers) == 1
    assert numbers[0].value == pytest.approx(expected_deg)
    assert numbers[0].unit == "deg"


def test_a_bare_number_without_a_unit_is_not_read_as_an_amount():
    """"3回まわって" carries no distance, so no figure is taken.

    Inventing a unit for a bare number would put an amount into the plan
    that the operator never gave.

    「3回まわって」は距離を含まないので、数値を取らないこと。

    裸の数値に単位を推測すれば、操作者が与えていない量を計画に入れることに
    なる。
    """
    assert extract_numbers("3回まわって") == []


def test_several_figures_are_read_in_the_order_they_were_said():
    """Each figure keeps its place in the sentence, for matching to a move.
    各数値は文中の順序を保ち、動作との対応付けに使えること。"""
    numbers = extract_numbers("1m上がって前に70cm進んで90度回って")

    assert [(n.value, n.unit) for n in numbers] == [
        (100.0, "cm"), (70.0, "cm"), (90.0, "deg"),
    ]


# =============================================================================
# Assembly rules / 組み立て規則
# =============================================================================

def test_everything_after_the_first_none_is_dropped():
    """A move answered after `none` is about a step that does not exist.
    `none` の後に答えられた動作は、存在しない手順についての答えであること。"""
    plan = _plan([STEP_FORWARD, STEP_NONE, STEP_BACK])

    assert _verbs(plan) == [STEP_TAKEOFF, STEP_FORWARD, STEP_LAND]


def test_everything_after_the_first_land_is_dropped():
    """Landing ends the flight, so there is no action after it.

    The questions are a fan-out and cannot see each other's answers, so
    each is asked in full about its own position and none knows the flight
    has already ended. Asked for the 5th action of a 4-action instruction,
    the model may answer `land` again as the most plausible continuation
    rather than `none` -- a fair reading of the sentence, not a mistake to
    argue with in the criteria. Measured against the live Jev on
    2026-09-19: "上がって前に進んで戻ってきて着陸して" answered `land` at
    both step 4 and step 5, and the aircraft was sent `land` twice.

    着陸で飛行は終わるので、その後の動作は存在しないこと。

    質問は fan-out で互いの答えを見られないため、各質問は自分の位置について
    完結して問われ、どれも飛行が既に終わったことを知らない。動作 4 つの指示に
    ついて 5 番目を問われたモデルは、`none` ではなく最ももっともらしい続きとして
    再び `land` と答えうる。文の妥当な読み方であって、criteria で争うべき誤りでは
    ない。2026-09-19 の Jev 実測:「上がって前に進んで戻ってきて着陸して」が手順 4
    と手順 5 の双方で `land` を返し、機体へ `land` が 2 回送られた。
    """
    plan = _plan([STEP_FORWARD, STEP_LAND, STEP_LAND])

    assert _verbs(plan) == [STEP_TAKEOFF, STEP_FORWARD, STEP_LAND]
    assert plan.command_lines().count("land") == 1


def test_a_takeoff_is_added_when_the_aircraft_is_on_the_ground():
    """A move sent to a grounded vehicle is refused by the firmware, so the
    takeoff the instruction left implicit is supplied.
    地上の機体への移動はファームが拒否するため、指示が省いた離陸を補うこと。"""
    plan = _plan([STEP_FORWARD], on_ground=True)

    assert plan.steps[0].verb == STEP_TAKEOFF


def test_a_takeoff_is_not_added_when_the_instruction_already_starts_with_one():
    """Two takeoffs would make the second fail with `error not on ground`.
    離陸が 2 つあると、2 つ目は `error not on ground` で失敗すること。"""
    plan = _plan([STEP_TAKEOFF, STEP_FORWARD], on_ground=True)

    assert _verbs(plan).count(STEP_TAKEOFF) == 1


def test_no_takeoff_is_added_when_the_aircraft_is_already_flying():
    """Already airborne, the sequence starts with the instruction's own move.
    既に飛行中なら、指示自身の動作から始まること。"""
    plan = _plan([STEP_FORWARD], on_ground=False)

    assert plan.steps[0].verb == STEP_FORWARD


def test_a_landing_is_added_when_the_instruction_does_not_end_in_one():
    """A sequence ending in the air leaves the aircraft with nobody
    instructing it, so a landing closes it.
    空中で終わる列は指示する者のいない機体を残すため、着陸で閉じること。"""
    plan = _plan([STEP_FORWARD])

    assert plan.steps[-1].verb == STEP_LAND


def test_no_landing_is_added_when_auto_land_is_switched_off():
    """`--no-auto-land` leaves the aircraft hovering, as asked.
    `--no-auto-land` は求めのとおり機体を浮かせたままにすること。"""
    plan = _plan([STEP_FORWARD], auto_land=False)

    assert STEP_LAND not in _verbs(plan)


def test_an_instruction_that_already_lands_is_not_given_a_second_landing():
    """The second `land` would fail with `error not flying`.
    2 つ目の `land` は `error not flying` で失敗すること。"""
    plan = _plan([STEP_FORWARD, STEP_LAND])

    assert _verbs(plan).count(STEP_LAND) == 1


def test_an_unspecified_amount_becomes_the_default_band():
    """Saying nothing about how far gives the medium distance, not a guess.
    距離を言わなければ、推測ではなく既定の中間の距離になること。"""
    plan = _plan([STEP_FORWARD], amounts=[AMOUNT_UNSPECIFIED])

    forward = plan.steps[1]
    assert forward.amount == CFG.distance_medium_cm
    assert forward.amount_source == "default"


@pytest.mark.parametrize("band,expected", [
    (AMOUNT_SMALL, CFG.distance_small_cm),
    (AMOUNT_MEDIUM, CFG.distance_medium_cm),
    (AMOUNT_LARGE, CFG.distance_large_cm),
])
def test_a_named_size_becomes_the_distance_the_config_gives_it(band, expected):
    """The bands are the only amounts Jev may choose, and config maps them.
    Jev が選べる量は区分だけで、cm への対応は設定が持つこと。"""
    plan = _plan([STEP_FORWARD], amounts=[band])

    assert plan.steps[1].amount == expected


def test_a_turn_uses_the_turn_bands_not_the_distance_bands():
    """A turn's "medium" is a quarter circle, not half a metre.
    旋回の「medium」は 0.5m ではなく 90 度であること。"""
    plan = _plan([STEP_TURN_RIGHT], amounts=[AMOUNT_MEDIUM])

    assert plan.steps[1].amount == CFG.turn_medium_deg


def test_a_spoken_figure_overrides_the_size_jev_chose():
    """What the operator actually said outranks the model's band, always.

    The bands exist because the model must not handle figures; where a
    figure exists there is nothing left for a band to contribute.

    操作者が実際に言った数値は、常にモデルの区分に優先すること。

    区分があるのはモデルに数値を扱わせないためであり、数値がある場所で区分が
    足せるものは無い。
    """
    plan = build_plan("前に70cm進んで",
                      _judgement([STEP_FORWARD], [AMOUNT_LARGE]))

    forward = plan.steps[1]
    assert forward.amount == 70.0
    assert forward.amount_source == "spoken"


def test_a_figure_goes_to_the_move_the_sentence_names_beside_it():
    """A lone figure reaches the move the operator named, not the first one.

    Measured 2026-09-19: "前に80cm進んで" was read as `up` then `forward` --
    a climb the model added, plus the travel that was asked for -- and
    pairing by order alone handed the only 80 cm to the `up`. The aircraft
    flew `up 80` and then `forward 50`, the default band, having been told
    80 cm and forward. The sentence never names a climb, so the figure
    belongs to the travel it does name.

    数値が 1 つのとき、最初の動作ではなく、文が名指しした動作に届くこと。

    2026-09-19 実測:「前に80cm進んで」が `up` → `forward` と読まれ（モデルが
    足した上昇と、求められた移動）、順序だけの対応付けが唯一の 80cm を `up` へ
    渡した。機体は「前に 80cm」と言われて `up 80` を飛び、続けて既定の区分で
    ある `forward 50` を飛んだ。文は上昇を名指ししていないので、その数値は、
    文が名指しした移動のものである。
    """
    plan = build_plan("前に80cm進んで", _judgement([STEP_UP, STEP_FORWARD]))

    climb, forward = plan.steps[1], plan.steps[2]
    assert (forward.verb, forward.amount) == (STEP_FORWARD, 80.0)
    assert forward.amount_source == "spoken"
    assert climb.amount != 80.0, "the climb was never given an amount to fly"


def test_a_figure_for_every_move_is_still_paired_in_order():
    """When the sentence names every move, the pairing is the order given.

    The rule above only withholds a figure from a move nobody mentioned.
    A sentence that mentions both must keep the straightforward reading,
    or fixing the case above would have broken the ordinary one.

    文が全ての動作を名指ししていれば、対応付けは言われた順のままであること。

    上の規則が数値を差し控えるのは、誰も言及していない動作に対してだけである。
    両方に言及している文は素直な読み方を保つ必要がある。そうでなければ、上の
    事例を直したことが普通の事例を壊したことになる。
    """
    plan = build_plan("1m上がって前に70cm進んで",
                      _judgement([STEP_UP, STEP_FORWARD]))

    climb, forward = plan.steps[1], plan.steps[2]
    assert (climb.verb, climb.amount) == (STEP_UP, 100.0)
    assert (forward.verb, forward.amount) == (STEP_FORWARD, 70.0)


def test_a_distance_and_an_angle_go_to_the_moves_they_can_belong_to():
    """Centimetres reach the travel and degrees reach the turn, by unit.
    cm は移動へ、度は旋回へ、単位に従って割り当てられること。"""
    plan = build_plan("前に70cm進んで90度回って",
                      _judgement([STEP_FORWARD, STEP_TURN_RIGHT]))

    forward, turn = plan.steps[1], plan.steps[2]
    assert (forward.verb, forward.amount) == (STEP_FORWARD, 70.0)
    assert (turn.verb, turn.amount) == (STEP_TURN_RIGHT, 90.0)


def test_a_move_that_carries_no_amount_is_given_none():
    """Asking how far a landing goes has no answer, so it carries none.
    着陸が「どれだけ」かには答えが無いので、量を持たないこと。"""
    plan = _plan([STEP_FORWARD])

    assert plan.steps[-1].amount is None


def test_the_command_line_is_the_one_the_vehicle_accepts():
    """Each step becomes the API line `tello-api-reference.md` documents.
    各手順が `tello-api-reference.md` の API 行になること。"""
    plan = build_plan("前に70cm進んで90度回って",
                      _judgement([STEP_FORWARD, STEP_TURN_RIGHT]))

    assert plan.command_lines() == ["takeoff", "forward 70", "cw 90", "land"]


def test_a_left_turn_becomes_ccw():
    """Anticlockwise is `ccw` in the vehicle's vocabulary.
    反時計回りは機体の語彙では `ccw` であること。"""
    plan = _plan([STEP_TURN_LEFT], amounts=[AMOUNT_MEDIUM])

    assert "ccw 90" in plan.command_lines()


# =============================================================================
# Confidence / 確信度
# =============================================================================

def test_a_step_below_the_confidence_threshold_is_not_flown():
    """An instruction that was not understood is not a flight to attempt.
    理解できなかった指示は、試みる飛行ではないこと。"""
    low = CFG.min_step_confidence - 0.1
    plan = _plan([STEP_FORWARD], confidence=low)

    assert not plan.ok
    assert plan.steps == []


def test_the_refusal_names_which_step_was_uncertain():
    """The operator is told where to rephrase, not merely that it failed.
    操作者に「失敗した」ではなく「どこを言い換えるか」を伝えること。"""
    low = CFG.min_step_confidence - 0.1
    plan = _plan([STEP_FORWARD, STEP_BACK], confidence=low)

    assert "1 番目" in plan.refusal and "2 番目" in plan.refusal


def test_a_failed_request_refuses_rather_than_flying_a_guess():
    """With no answer there is no instruction, so nothing is flown.
    答えが無ければ指示も無く、何も飛ばさないこと。"""
    plan = build_plan("前に進んで", _judgement([], error="timeout"))

    assert not plan.ok
    assert "timeout" in plan.refusal


def test_an_instruction_with_no_flying_in_it_is_refused():
    """A sentence that asks for no action gets no steps invented for it.
    動作を求めない文に対して、手順をでっち上げないこと。"""
    plan = _plan([STEP_NONE], instruction="今日の天気は？")

    assert not plan.ok
    assert plan.steps == []


# =============================================================================
# The envelope, checked before anything moves / 動く前の飛行領域検査
# =============================================================================

def test_a_plan_that_would_climb_out_of_the_envelope_is_refused():
    """Two large climbs pass the ceiling, and it is caught on paper.
    大きな上昇 2 回で上限を超え、それを机上で捕まえること。"""
    plan = _plan([STEP_UP, STEP_UP], amounts=[AMOUNT_LARGE, AMOUNT_LARGE])

    assert not plan.ok
    assert "高度" in plan.refusal


def test_a_plan_that_would_descend_below_the_envelope_is_refused():
    """Descending below the floor is refused as surely as climbing above it.
    下限を割る降下も、上限を超える上昇と同様に拒否されること。"""
    plan = _plan([STEP_DOWN], amounts=[AMOUNT_LARGE])

    assert not plan.ok
    assert "高度" in plan.refusal


def test_a_plan_that_would_leave_the_radius_is_refused():
    """Three 100 cm hops forward pass the 2 m radius from the takeoff point.
    100cm の前進 3 回で離陸点から半径 2m を超えること。"""
    plan = _plan([STEP_FORWARD] * 3, amounts=[AMOUNT_LARGE] * 3)

    assert not plan.ok
    assert "半径" in plan.refusal


def test_the_refusal_names_the_step_that_left_the_envelope():
    """Naming the step is what lets the operator fix the instruction.

    The number counts the sequence that would be FLOWN, including the
    takeoff added at the front, because that is the sequence printed above
    the refusal for the operator to read against.

    手順を名指しすることが、操作者が指示を直せる条件であること。

    番号は先頭に足した離陸も含む「実際に飛ぶ列」で数える。拒否の上に表示され、
    操作者が突き合わせる列がそれだからである。
    """
    plan = _plan([STEP_FORWARD] * 3, amounts=[AMOUNT_LARGE] * 3)

    # takeoff, forward, forward, forward -> the third hop is step 4.
    # takeoff・forward・forward・forward なので、3 回目の前進は 4 番目。
    assert "4 番目" in plan.refusal


def test_a_plan_inside_the_envelope_is_accepted():
    """The check refuses what leaves the box and nothing else.
    検査は範囲を出るものだけを拒否し、それ以外は通すこと。"""
    plan = _plan([STEP_FORWARD, STEP_BACK], amounts=[AMOUNT_MEDIUM] * 2)

    assert plan.ok, plan.refusal


def test_a_move_shorter_than_the_vehicle_accepts_is_refused():
    """Under 10 cm the firmware replies `error out of range`, so it is
    caught here where the operator can be told which step.
    10cm 未満はファームが `error out of range` を返すため、どの手順かを
    操作者に伝えられるここで捕まえること。"""
    plan = build_plan("前に5cm進んで", _judgement([STEP_FORWARD]))

    assert not plan.ok
    assert "10" in plan.refusal


def test_a_move_longer_than_the_vehicle_accepts_is_refused():
    """Over 300 cm the firmware silently clamps, which would fly a
    different plan from the one that was confirmed.
    300cm 超はファームが黙ってクランプし、了承したものと違う計画を飛ぶことに
    なるため拒否すること。"""
    plan = build_plan("前に4m進んで", _judgement([STEP_FORWARD]))

    assert not plan.ok
    assert "300" in plan.refusal


def test_the_landing_is_allowed_to_reach_the_ground():
    """Touching down is the one time the altitude floor must not apply.
    接地は、高度の下限を適用してはならない唯一の場面であること。"""
    plan = _plan([STEP_FORWARD, STEP_LAND], amounts=[AMOUNT_SMALL, None])

    assert plan.ok, plan.refusal


# =============================================================================
# return_home / 帰還の計算
# =============================================================================

def test_return_home_becomes_a_go_that_cancels_the_displacement():
    """After 100 cm forward, home is 100 cm back along the same axis.
    100cm 前進した後の帰還は、同じ軸を 100cm 戻ることであること。"""
    plan = _plan([STEP_FORWARD, STEP_RETURN_HOME], amounts=[AMOUNT_LARGE, None])

    home = plan.command_lines()[2]
    assert home == "go -100 0 0 50", home


def test_return_home_accounts_for_a_turn_made_along_the_way():
    """After going north, turning right and going east, home is behind AND
    to the side -- the `go` is diagonal, not a straight reversal.

    This is the case a body-frame plan gets wrong if it ignores heading. The
    aircraft is displaced 50 cm north and 50 cm east while facing EAST, so
    home lies 50 cm behind it and 50 cm to its right (south is off the right
    wing when facing east). Ignoring the turn would compute a straight
    `go -50 0 0` and leave the aircraft 50 cm from home.

    北へ進み・右へ回り・東へ進んだ後、離陸点は後方かつ横にあり、`go` は
    まっすぐな引き返しではなく斜めになること。

    機首方位を無視した機体座標の計算が誤る場面である。機体は北へ 50cm・東へ
    50cm 変位し、向きは**東**なので、離陸点は後方 50cm・右 50cm にある（東を
    向くと南は右舷側）。旋回を無視すればまっすぐな `go -50 0 0` を計算し、
    離陸点から 50cm 離れたところで終わってしまう。
    """
    plan = _plan(
        [STEP_FORWARD, STEP_TURN_RIGHT, STEP_FORWARD, STEP_RETURN_HOME],
        amounts=[AMOUNT_MEDIUM, AMOUNT_MEDIUM, AMOUNT_MEDIUM, None],
    )

    # `go x y z speed` takes y as LEFT, so 50 cm to the right is y = -50.
    # `go x y z speed` の y は**左**なので、右 50cm は y = -50 になる。
    assert plan.command_lines()[4] == "go -50 -50 0 50"


def test_the_walk_that_computes_home_is_the_walk_that_checks_the_envelope():
    """A returning plan is measured from where it actually got to.

    If the envelope walk ignored `return_home`, a plan that goes out 100 cm,
    comes home and goes out again would be measured as 200 cm out and
    wrongly refused.

    帰還する計画は、実際に到達した位置から測られること。

    飛行領域の積算が `return_home` を無視すると、100cm 出て・戻って・また出る計画が
    「200cm 出た」と測られ、誤って拒否されてしまう。
    """
    plan = _plan(
        [STEP_FORWARD, STEP_RETURN_HOME, STEP_FORWARD, STEP_RETURN_HOME],
        amounts=[AMOUNT_LARGE, None, AMOUNT_LARGE, None],
    )

    assert plan.ok, plan.refusal


# =============================================================================
# Asking / 問い合わせ
# =============================================================================

def test_the_state_sent_with_an_instruction_carries_only_the_instruction():
    """The flight's condition is irrelevant to what a sentence means, and
    irrelevant state lowers the model's accuracy.
    文の意味に飛行の状況は無関係であり、無関係な state は精度を下げること。"""
    from sfpilot.judge import FakeJudge

    judge = FakeJudge(answers=_judgement([STEP_FORWARD]).answers)
    translate("前に進んで", judge)

    assert judge.calls[0]["state"] == {"operator_instruction": "前に進んで"}


def test_every_step_is_asked_about_in_one_request():
    """All questions go in one round trip, as the fan-out pattern asks.
    全質問を 1 往復で問うこと（fan-out の方式のとおり）。"""
    from sfpilot.judge import FakeJudge

    judge = FakeJudge(answers=_judgement([STEP_FORWARD]).answers)
    translate("前に進んで", judge)

    assert len(judge.calls) == 1
    asked = judge.calls[0]["questions"]
    assert len(asked) == CFG.max_steps * 2, asked


# =============================================================================
# Flying a plan while the safety layer watches / 安全層の下での実行
# =============================================================================

class _RecordingLink:
    """Records what reached the vehicle, without one.
    機体に届いたものを、機体無しで記録する。"""

    def __init__(self):
        self.sent: list = []

    def send_rc(self, a, b, c, d) -> None:
        self.sent.append(f"rc {a} {b} {c} {d}")

    def send_command(self, line: str) -> None:
        self.sent.append(line)

    def priority(self, line: str) -> None:
        self.sent.append(line)

    def hold_sticks_neutral(self) -> None:
        pass

    def read_samples(self) -> list:
        return []


def test_a_hovering_rc_is_withheld_while_a_plan_drives_the_vehicle():
    """The safety layer's hover must not cancel the move in progress.

    `rc` publishes a VELOCITY guidance target which replaces the POSITION
    target a `forward` just set (api_task.cpp cmdRc, mode 2). Sending the
    routine hover alongside a move would therefore stop that move dead.

    計画が機体を駆動している間、安全層の待機 `rc` を送らないこと。

    `rc` は**速度**誘導目標を publish し、`forward` が設定した**位置**目標を
    置き換える（api_task.cpp の cmdRc、mode 2）。移動と並行して待機を送れば、
    その移動はそこで止まってしまう。
    """
    from sfpilot.arbiter import Verdict, VERDICT_HOVER
    from sfpilot.executor import Executor

    link = _RecordingLink()
    executor = Executor(link)
    executor.hold_commands_silently = True

    executor.apply(Verdict(action=VERDICT_HOVER))

    assert link.sent == [], f"an rc reached the vehicle: {link.sent}"


def test_a_landing_is_still_begun_while_a_plan_drives_the_vehicle():
    """Withholding the hover must not withhold the landing.

    The safety layer running at all is only worth anything if it can still
    end the flight, so a landing begins whatever else is driving. It begins
    with `stop` rather than `land`, because the firmware stops holding
    horizontal position for the whole descent (landing.py) -- the craft is
    brought to rest first, and `land` follows.

    待機を抑えることが、着陸まで抑えてはならないこと。

    安全層が動いていることに意味があるのは、飛行を終わらせられる場合だけ
    である。着陸は、何が駆動していても始まる。始まりが `land` ではなく `stop`
    なのは、ファームが降下のあいだ水平の位置保持をやめるためである
    （landing.py）。先に機体を止め、`land` はその後に続く。
    """
    from sfpilot.arbiter import Verdict, VERDICT_LAND
    from sfpilot.executor import Executor

    link = _RecordingLink()
    executor = Executor(link)
    executor.hold_commands_silently = True

    executor.apply(Verdict(action=VERDICT_LAND))

    assert link.sent == ["stop"], "the landing must start by stopping the craft"
    assert executor.landing, "the flight is ending from this moment"


def test_a_step_waits_for_the_vehicle_to_answer_before_the_next_one():
    """Each blocking verb is sent only after the previous one is done.

    The vehicle answers a move when it is REACHED (api_task.cpp cmdMove), so
    waiting on the reply is what keeps a sequence from piling every target
    onto the craft at once.

    ブロックする verb は、前の verb が終わってから送られること。

    機体は移動の**到達時**に応答するので（api_task.cpp の cmdMove）、応答を
    待つことが、機体に全目標を一度に積み上げないための条件である。
    """
    from sfpilot.say import StepRunner

    link = _CountingLink()
    steps = [_step(STEP_FORWARD, 50), _step(STEP_BACK, 50)]
    runner = StepRunner(link, steps, speed_probe=lambda: 0.0)
    runner.SETTLE_HOLD_S = 0.0

    runner.start()
    _wait_until(lambda: len(runner.sent) == 1)
    assert link.sent == ["forward 50"], "the second step was sent too early"

    link.answer()
    _wait_until(lambda: len(link.sent) == 2)
    assert link.sent == ["forward 50", "back 50"]

    link.answer()
    _wait_until(lambda: runner.done)


def test_a_forward_move_is_shortened_to_stop_short_of_a_wall():
    """A move that would end inside a wall is cut to stop clear of it.

    `forward N` is a step to a position target N cm ahead and the craft
    accelerates towards it, so a move that ENDS at the wall arrives there at
    speed -- exactly the approach the immediate rule then has to arrest
    without brakes. Shortening the move is what keeps that rule a last
    resort rather than the thing every approach depends on.

    壁の中で終わる移動が、手前で止まるように切り詰められること。

    `forward N` は N cm 先の位置目標へのステップであり、機体はそこへ向かって加速
    する。したがって壁で終わる移動は、そこへ速度を乗せて到達する ―― 制動手段を
    持たない即時則がその後で止める羽目になる進入そのものである。移動を短く刻む
    ことが、その規則を「最後の砦」に留め、あらゆる接近が頼る当てにしない条件で
    ある。
    """
    from sfpilot.say import StepRunner

    link = _CountingLink()
    # A wall 1.5 m ahead, and an instruction to fly 1.4 m: unshortened, the
    # move ends 0.1 m from it.
    # 1.5m 先に壁があり、1.4m 進めという指示である。刻まなければ、移動は壁の
    # 0.1m 手前で終わる。
    runner = StepRunner(link, [_step(STEP_FORWARD, 140)],
                        speed_probe=lambda: 0.0,
                        forward_probe=lambda: 1.5)
    runner.SETTLE_HOLD_S = 0.0

    runner.start()
    _wait_until(lambda: len(link.sent) == 1)

    sent_cm = int(link.sent[0].split()[1])
    assert sent_cm < 140, "the move was sent at full length into the wall"
    # The move AND the stopping distance of the speed it reaches must both
    # fit in the gap. The speed is bounded by the travel itself, so a short
    # move is not charged the envelope ceiling's stopping distance.
    # 移動と、それが到達する速度の停止距離の**両方**が隙間に収まること。速度は
    # 移動距離自身で抑えられるので、短い移動に飛行領域の上限での停止距離が
    # 課されることはない。
    cfg = DEFAULT_CONFIG.forward
    travel_m = sent_cm / 100.0
    reached = min(travel_m / cfg.coast_per_speed_s,
                  DEFAULT_CONFIG.envelope.speed_max_mps)
    needed_m = min(cfg.safety_margin_m + cfg.coast_per_speed_s * reached,
                   cfg.stop_distance_max_m)
    assert travel_m + needed_m <= 1.5 + 1e-6, (
        f"the move travels {travel_m:.2f} m and then needs {needed_m:.2f} m "
        f"to stop, which overruns the 1.5 m available")

    link.answer()
    _wait_until(lambda: runner.done)


def test_a_forward_move_with_open_space_ahead_is_left_alone():
    """Nothing is shortened when the wall is far enough away.

    The limit must not tax ordinary flight: an instruction that fits
    comfortably in the room available is flown exactly as it was given.
    壁が十分に遠ければ、何も刻まれないこと。

    この制限が通常の飛行に税を課してはならない。空間に余裕をもって収まる指示は、
    与えられたとおりに飛ぶ。
    """
    from sfpilot.say import StepRunner

    link = _CountingLink()
    runner = StepRunner(link, [_step(STEP_FORWARD, 50)],
                        speed_probe=lambda: 0.0,
                        forward_probe=lambda: 8.0)
    runner.SETTLE_HOLD_S = 0.0

    runner.start()
    _wait_until(lambda: len(link.sent) == 1)

    assert link.sent == ["forward 50"]
    link.answer()
    _wait_until(lambda: runner.done)


def test_a_forward_move_with_no_room_left_is_not_sent_at_all():
    """Inside its own stopping distance, the craft is not sent forward.

    The vehicle refuses a move under `move_min_cm` anyway (`error out of
    range`), and a craft already too close to stop should not be asked to
    travel further towards the wall.
    自身の停止距離の内側にいる機体を、前へ進ませないこと。

    機体はどのみち `move_min_cm` 未満の移動を拒否するし（`error out of range`）、
    既に止まれないほど近い機体に、さらに壁へ向かえと求めるべきではない。
    """
    from sfpilot.say import StepRunner

    link = _CountingLink()
    runner = StepRunner(link, [_step(STEP_FORWARD, 100)],
                        speed_probe=lambda: 0.0,
                        forward_probe=lambda: 0.3)
    runner.SETTLE_HOLD_S = 0.0

    runner.start()
    _wait_until(lambda: runner.done)

    assert link.sent == [], f"a move was sent with no room: {link.sent}"
    # The step is still recorded, so the operator's summary shows it was
    # reached rather than silently skipped.
    # 手順は記録に残す。操作者の要約に、黙って飛ばされたのではなく到達したことが
    # 出るようにするためである。
    assert len(runner.sent) == 1


def test_a_move_is_left_alone_when_the_forward_distance_is_unknown():
    """With no forward reading, the move goes out at its full length.

    An unknown distance is not a small one. Shortening on an absent reading
    would cripple every flight without a forward sensor -- which is every
    flight the regression suite runs -- and the immediate rule remains the
    protection there, as it was before.
    前方距離が分からないとき、移動は元の長さで出ること。

    分からない距離は、小さい距離ではない。無い読み値で刻めば、前方センサの無い
    全ての飛行が不自由になる ―― 回帰一式が走らせる飛行は全てそれである。そこでの
    防護は従来どおり即時則である。
    """
    from sfpilot.say import StepRunner

    link = _CountingLink()
    runner = StepRunner(link, [_step(STEP_FORWARD, 100)],
                        speed_probe=lambda: 0.0,
                        forward_probe=lambda: None)
    runner.SETTLE_HOLD_S = 0.0

    runner.start()
    _wait_until(lambda: len(link.sent) == 1)

    assert link.sent == ["forward 100"]
    link.answer()
    _wait_until(lambda: runner.done)


def test_only_forward_moves_are_limited_by_the_wall_ahead():
    """A retreat or a climb is not shortened by a wall in front.

    The forward sensor constrains travel along the nose and nothing else.
    Limiting a `back` by the distance ahead would refuse the craft the one
    move that opens that distance up.
    前方の壁が、後退や上昇を刻まないこと。

    前方センサが制約するのは機首方向の移動だけである。`back` を前方の距離で
    刻めば、その距離を開ける唯一の移動を機体から取り上げることになる。
    """
    from sfpilot.say import StepRunner

    link = _CountingLink()
    runner = StepRunner(link, [_step(STEP_BACK, 100)],
                        speed_probe=lambda: 0.0,
                        forward_probe=lambda: 0.2)
    runner.SETTLE_HOLD_S = 0.0

    runner.start()
    _wait_until(lambda: len(link.sent) == 1)

    assert link.sent == ["back 100"]
    link.answer()
    _wait_until(lambda: runner.done)


def test_a_settling_craft_is_waited_for_before_the_next_step():
    """A craft still carrying its approach speed is not "arrived".

    `ok` fires when the estimate enters the tolerance sphere, not when the
    craft stops. Measured: a `land` sent at that moment keeps travelling
    through the descent and touches down well past the takeoff point.

    進入速度をまだ持つ機体を「到着した」としないこと。

    `ok` は推定が許容球に入った時点で出るのであって、機体が止まった時点では
    ない。実測: その瞬間に送った `land` は降下中も進み続け、離陸点を大きく
    行き過ぎて接地する。
    """
    from sfpilot.say import StepRunner

    link = _CountingLink()
    speed = {"mps": 0.5}
    runner = StepRunner(link, [_step(STEP_FORWARD, 50), _step(STEP_BACK, 50)],
                        speed_probe=lambda: speed["mps"])
    runner.SETTLE_HOLD_S = 0.05

    runner.start()
    _wait_until(lambda: len(runner.sent) == 1)
    link.answer()
    time.sleep(0.3)
    assert link.sent == ["forward 50"], "moved on while still travelling fast"

    speed["mps"] = 0.0
    _wait_until(lambda: len(link.sent) == 2)


class _CountingLink(_RecordingLink):
    """A link whose replies the test controls. / 応答を試験が制御する link。"""

    def __init__(self):
        super().__init__()
        self._replies = 0

    @property
    def reply_count(self) -> int:
        return self._replies

    def answer(self) -> None:
        self._replies += 1


def _step(verb: str, amount: float):
    from sfpilot.instruction import Step

    return Step(verb=verb, amount=amount)


def _wait_until(condition, timeout_s: float = 5.0) -> None:
    """Wait for a worker thread to get somewhere, or fail loudly.
    作業スレッドが所定の状態に至るのを待つ。至らなければ明示的に失敗する。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError(f"condition not met within {timeout_s:g}s")


def test_a_hover_with_no_answer_yet_does_not_interrupt_the_sequence():
    """Waiting for the first answer is not a reason to abandon the plan.

    Most hovers mean "no usable opinion yet" (late, stale or unanswered),
    and several occur during any takeoff. Interrupting on those would cut
    every instruction off at its first step.

    答え待ちの待機が、計画を中止する理由にならないこと。

    待機の多くは「まだ使える意見が無い」（期限超過・鮮度切れ・未着）であり、
    どの離陸でも何度か起きる。それで中断すれば、あらゆる指示が最初の手順で
    打ち切られる。
    """
    from sfpilot.arbiter import Verdict, VERDICT_HOVER
    from sfpilot.say import _interrupt_reason

    pilot = _PilotWithVerdict(Verdict(
        action=VERDICT_HOVER, source="arbiter", reason="応答が未着"))

    assert _interrupt_reason(pilot) == ""


def test_a_hold_jev_chose_interrupts_the_sequence():
    """When the model says to wait, the instruction stops.
    モデルが待てと言ったら、指示は止まること。"""
    from sfpilot.arbiter import Verdict, VERDICT_HOVER
    from sfpilot.say import _interrupt_reason

    pilot = _PilotWithVerdict(Verdict(
        action=VERDICT_HOVER, source="judge", reason="Jev が待機を選択"))

    assert "Jev" in _interrupt_reason(pilot)


def test_a_landing_verdict_interrupts_the_sequence():
    """A flight that must end does not finish its errand first.
    終えるべき飛行が、用事を先に済ませないこと。"""
    from sfpilot.arbiter import Verdict, VERDICT_LAND
    from sfpilot.say import _interrupt_reason

    pilot = _PilotWithVerdict(Verdict(
        action=VERDICT_LAND, source="monitor", reason="電池が危険域"))

    assert "電池" in _interrupt_reason(pilot)


class _PilotWithVerdict:
    """A stand-in carrying one decision, for the interrupt rule.
    中断規則のために、判断を 1 つだけ持つ代役。"""

    def __init__(self, verdict):
        self.decisions = [{"verdict": verdict}]


def test_each_question_says_which_step_it_is_about():
    """The questions cannot see each other's answers, so none of them may
    refer to "the next action".
    質問は互いの答えを見られないため、「次の動作」と書かないこと。"""
    from sfpilot.judge import step_questions

    questions = step_questions(CFG.max_steps)

    for index in range(1, CFG.max_steps + 1):
        for template in (Q_STEP_MOVE, Q_STEP_AMOUNT):
            text = questions[template.format(index)]["instructions"]
            assert f"number {index}" in text, text
