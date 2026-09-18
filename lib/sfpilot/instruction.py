"""
sfpilot.instruction - a spoken instruction becomes a typed sequence of moves.
sfpilot.instruction - 話した指示を、型のついた動作の列にする。

The division of labour is the design's, and it is strict: **Jev names the
moves, code owns every number.** Jev answers "what is action number 3?"
and "how big is action number 3?" from a fixed set of options; the figures
the operator actually said ("70cm", "1m", "90 度") are extracted here by
pattern, and the conversion from a named size to centimetres comes from
`config.InstructionConfig`. The model is documented not to be a
calculator, so it is never asked to compare, convert or add anything.

分担は設計のとおりで、厳格である: **動作に名前を付けるのは Jev、数値は
すべてコードが持つ。** Jev は「3 番目の動作は何か」「3 番目の動作はどれだけか」に
決められた選択肢から答える。操作者が実際に言った数値（「70cm」「1m」「90 度」）は
ここでパターンとして抜き出し、名前の付いた量から cm への対応は
`config.InstructionConfig` が持つ。モデルは計算機ではないと文書化されている
ため、比較・換算・加算はいずれも頼まない。

What this module refuses to fly is as important as what it flies. Before
anything moves, the whole sequence is walked on paper from the takeoff
point and every step's resulting position is checked against the envelope
(`config.EnvelopeConfig`). A plan that would leave the box is reported with
the offending step named, and nothing is sent. The same goes for a step the
model was not confident about: an instruction that was not understood is
not a flight worth attempting.

飛ばさないものを決めることは、飛ばすものを決めるのと同じだけ重要である。
何かが動く前に、手順の列を離陸点から机上で積算し、各手順の到達位置を包絡
（`config.EnvelopeConfig`）と照合する。範囲を出る計画は、問題の手順を名指しして
報告し、何も送らない。モデルの確信度が低かった手順も同じ扱いである — 理解
できなかった指示は、試みる価値のある飛行ではない。
"""

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from .config import DEFAULT_CONFIG
from .judge import (
    AMOUNT_LARGE, AMOUNT_MEDIUM, AMOUNT_SMALL, AMOUNT_UNSPECIFIED,
    Q_STEP_AMOUNT, Q_STEP_MOVE,
    STEP_BACK, STEP_DOWN, STEP_FORWARD, STEP_LAND, STEP_LEFT, STEP_NONE,
    STEP_RETURN_HOME, STEP_RIGHT, STEP_TAKEOFF, STEP_TURN_LEFT,
    STEP_TURN_RIGHT, STEP_UP,
    step_questions,
)

# Moves that travel a distance, and what each does to the aircraft's
# position in its own body frame. Forward is +x along the nose, right is
# +y, up is +z -- the same frame the vehicle's `forward`/`right`/`up` verbs
# use (api_task.cpp cmdMove, Tello body frame).
# 距離を移動する動作と、それが機体座標で位置をどう変えるか。前が機首方向の
# +x、右が +y、上が +z — 機体の `forward`/`right`/`up` が使うのと同じ座標系
# （api_task.cpp の cmdMove、Tello 機体座標）。
_TRAVEL_BODY_AXIS = {
    STEP_FORWARD: (1.0, 0.0, 0.0),
    STEP_BACK: (-1.0, 0.0, 0.0),
    STEP_RIGHT: (0.0, 1.0, 0.0),
    STEP_LEFT: (0.0, -1.0, 0.0),
    STEP_UP: (0.0, 0.0, 1.0),
    STEP_DOWN: (0.0, 0.0, -1.0),
}

# Moves that rotate, and the sign of the yaw change. Clockwise seen from
# above is positive, matching the vehicle's `cw` verb.
# 回転する動作と、ヨー変化の符号。上から見て時計回りが正で、機体の `cw` と
# 一致する。
_TURN_SIGN = {STEP_TURN_RIGHT: 1.0, STEP_TURN_LEFT: -1.0}

# Moves that carry no amount at all. Asking how far a landing goes has no
# answer, so any amount attached to one of these is dropped.
# 量を持たない動作。着陸が「どれだけ」かには答えが無いため、これらに付いた
# 量は捨てる。
_AMOUNTLESS = (STEP_TAKEOFF, STEP_LAND, STEP_RETURN_HOME, STEP_NONE)


@dataclass
class Step:
    """One move, with its amount already in the vehicle's own units.
    1 つの動作。量は機体自身の単位に変換済みである。"""

    verb: str
    # Centimetres for a travelling move, degrees for a turn, None for a
    # move that carries no amount.
    # 移動なら cm、旋回なら度、量を持たない動作なら None。
    amount: Optional[float] = None
    # Where the amount came from, for the operator to check and for the
    # trace: "spoken" (a figure in the instruction), "named" (Jev's size
    # band), "default" (nothing said), or "computed" (return_home).
    # 量の出どころ。操作者の確認用と記録用である: "spoken"（指示中の数値）・
    # "named"（Jev の量の区分）・"default"（指定なし）・"computed"（return_home）。
    amount_source: str = ""
    confidence: float = 1.0

    def command(self) -> str:
        """The API command line this step sends. / この手順が送る API 行。"""
        if self.verb in (STEP_TAKEOFF, STEP_LAND):
            return self.verb
        if self.verb in _TURN_SIGN:
            direction = "cw" if self.verb == STEP_TURN_RIGHT else "ccw"
            return f"{direction} {round(self.amount)}"
        return f"{self.verb} {round(self.amount)}"

    def describe(self) -> str:
        """One line an operator can check before saying yes.
        操作者が了承の前に確認できる 1 行。"""
        if self.amount is None:
            return self.verb
        unit = "deg" if self.verb in _TURN_SIGN else "cm"
        return f"{self.verb} {self.amount:g}{unit}"


@dataclass
class Plan:
    """A whole instruction, translated -- or the reason it was refused.

    A Plan is always returned, never an exception: a refusal is an outcome
    the operator needs the detail of (which step, and why), not an error.

    指示 1 つ分の変換結果、または拒否の理由。

    例外ではなく常に Plan を返す。拒否は、操作者が詳細（どの手順が、なぜ）を
    必要とする結果であって、エラーではないからである。
    """

    instruction: str = ""
    steps: list = field(default_factory=list)
    refusal: str = ""
    # Every raw answer, for the trace: {question_id: (choice, confidence)}.
    # 記録用の生の答え一式: {質問 ID: (選択肢, 確信度)}。
    answers: dict = field(default_factory=dict)
    spoken_numbers: list = field(default_factory=list)
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.refusal and bool(self.steps)

    def command_lines(self) -> list:
        return [step.command() for step in self.steps]


# =============================================================================
# Numbers the operator said / 操作者が言った数値
# =============================================================================
#
# Jev is never shown a figure and never asked to convert one. Everything
# below is ordinary text handling: find the numerals, find the unit, turn
# both into centimetres or degrees.
#
# Jev には数値を見せず、換算も頼まない。以下はすべて通常の文字列処理である:
# 数字を見つけ、単位を見つけ、cm または度に直す。

# Full-width digits map onto their ASCII counterparts one for one.
# 全角数字は半角に 1 対 1 で対応する。
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９．", "0123456789.")

# Kanji numerals, up to the range an indoor instruction plausibly uses
# ("一メートル", "九十度"). Beyond about a hundred the spoken form stops
# being how anyone gives a drone a distance, so the table stops there
# rather than growing a general kanji-numeral parser.
# 漢数字。屋内の指示が現実に使う範囲まで（「一メートル」「九十度」）。百を
# 超えると、その言い方でドローンに距離を伝える人はいなくなるため、汎用の
# 漢数字パーサに育てず、この表で止める。
_KANJI_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                 "六": 6, "七": 7, "八": 8, "九": 9}

_UNIT_CM = "cm"
_UNIT_DEG = "deg"

# Unit spellings, longest first so "センチ" is not matched as a prefix of
# nothing and "メートル" is preferred over a bare "m" inside it.
# 単位の表記。長いものから並べ、「メートル」が中の「m」として拾われない
# ようにする。
_UNIT_PATTERNS = [
    (r"センチメートル|センチ|cm|CM|ｃｍ", _UNIT_CM, 1.0),
    (r"メートル|ｍ|m|M", _UNIT_CM, 100.0),
    (r"度|°|deg", _UNIT_DEG, 1.0),
]


@dataclass
class SpokenNumber:
    """A figure the operator said, already in the vehicle's units.
    操作者が言った数値。機体の単位に変換済み。"""

    value: float          # centimetres or degrees / cm または度
    unit: str             # _UNIT_CM | _UNIT_DEG
    position: int         # where it appeared in the text / 文中の出現位置
    text: str = ""        # what was matched, for the trace / 記録用の一致文字列


def extract_numbers(instruction: str, config=DEFAULT_CONFIG) -> list:
    """Every figure-with-unit in the instruction, in the order they appear.

    Only figures that carry a unit are taken. A bare number ("3 回まわって")
    is not a distance, and guessing a unit for it would be this layer
    inventing an amount the operator did not give.

    指示中の「数値＋単位」を出現順にすべて返す。

    単位を伴う数値だけを取る。裸の数値（「3 回まわって」）は距離ではなく、
    単位を推測すれば、この層が操作者の言っていない量を作ることになる。
    """
    normalized = instruction.translate(_FULLWIDTH_DIGITS)
    found = []
    for unit_pattern, unit, scale in _UNIT_PATTERNS:
        number_pattern = rf"((?:\d+(?:\.\d+)?)|(?:[〇零一二三四五六七八九十百]+))\s*(?:{unit_pattern})"
        for match in re.finditer(number_pattern, normalized):
            value = _to_number(match.group(1))
            if value is None:
                continue
            found.append(SpokenNumber(
                value=value * scale, unit=unit,
                position=match.start(), text=match.group(0),
            ))
    # One position can match two unit patterns ("メートル" also contains an
    # "m"); the longer, earlier-listed spelling wins.
    # 同じ位置が 2 つの単位パターンに一致しうる（「メートル」は「m」を含む）。
    # 先に並べた長い表記を優先する。
    return _drop_overlaps(found)


def _to_number(text: str) -> Optional[float]:
    """"70" or "九十" or "一" -> a float. / 「70」「九十」「一」を数値にする。"""
    is_arabic = text[0].isdigit()
    if is_arabic:
        return float(text)
    return _kanji_to_number(text)


def _kanji_to_number(text: str) -> Optional[float]:
    """Kanji numerals below a thousand. / 千未満の漢数字。

    Handles the three shapes an instruction actually uses: a bare digit
    (一), a multiple of ten (九十, 十), and a multiple of a hundred (百).
    指示が実際に使う 3 つの形を扱う: 裸の数字（一）・十の倍数（九十・十）・
    百の倍数（百）。
    """
    total, current = 0, 0
    for char in text:
        if char in _KANJI_DIGITS:
            current = _KANJI_DIGITS[char]
            continue
        if char == "十":
            total += (current or 1) * 10
            current = 0
            continue
        if char == "百":
            total += (current or 1) * 100
            current = 0
            continue
        return None
    total += current
    return float(total) if total else None


def _drop_overlaps(found: list) -> list:
    """Keep one match per span of text, preferring the longest.
    同じ範囲の一致は 1 つに絞り、長いものを残す。"""
    ordered = sorted(found, key=lambda n: (n.position, -len(n.text)))
    kept: list = []
    for number in ordered:
        end = number.position + len(number.text)
        overlaps = any(
            number.position < k.position + len(k.text) and k.position < end
            for k in kept
        )
        if not overlaps:
            kept.append(number)
    return kept


# =============================================================================
# Assembling the sequence / 手順の組み立て
# =============================================================================

def build_plan(instruction: str, judgement, on_ground: bool = True,
               auto_land: bool = True, config=DEFAULT_CONFIG) -> Plan:
    """Turn one instruction plus one set of answers into a Plan.

    Order matters and is the design's: read the moves, attach the amounts,
    add the takeoff and the landing, compute `return_home`, then check the
    envelope last -- the check has to see the sequence that would actually
    be flown, including the steps this function added.

    指示 1 つと答え一式から Plan を作る。

    順序には意味があり、設計のとおりである: 動作を読む → 量を当てる →
    離陸と着陸を補う → `return_home` を計算する → 最後に包絡を検査する。
    検査は、この関数が足した手順も含めて「実際に飛ぶ列」を見る必要がある。
    """
    plan = Plan(instruction=instruction, latency_ms=getattr(judgement, "latency_ms", 0.0))
    if judgement is None or judgement.error is not None:
        error = getattr(judgement, "error", "応答が無い")
        plan.refusal = f"Jev に問えなかったため実行しない（{error}）"
        return plan

    plan.answers = _answers_for_trace(judgement)
    verbs, unsure = _read_verbs(judgement, config)
    if unsure:
        plan.refusal = _unsure_refusal(unsure, config)
        return plan
    if not verbs:
        plan.refusal = (
            "指示から飛行動作を読み取れなかった（飛行の指示ではない可能性がある）"
        )
        return plan

    numbers = extract_numbers(instruction, config)
    plan.spoken_numbers = numbers
    steps = _attach_amounts(verbs, numbers, judgement, config)
    steps = _add_takeoff_and_landing(steps, on_ground, auto_land, config)
    steps = _resolve_return_home(steps, config)

    breach = check_envelope(steps, config)
    if breach:
        plan.refusal = breach
        return plan
    plan.steps = steps
    return plan


def _read_verbs(judgement, config) -> tuple:
    """The moves, truncated at the first `none`, plus any unsure step.

    Everything after the first `none` is discarded rather than kept: the
    model was told `none` means the instruction has no such action, so a
    move answered after one is an answer about a step that does not exist.

    最初の `none` で打ち切った動作の列と、確信度の低い手順。

    `none` の後ろは残さず捨てる。`none` は「その番号の動作は無い」という意味だと
    モデルに伝えてあるので、その後に出た動作は、存在しない手順についての答えで
    ある。
    """
    verbs: list = []
    unsure: list = []
    for index in range(1, config.instruction.max_steps + 1):
        answer = judgement.answers.get(Q_STEP_MOVE.format(index))
        if answer is None or answer.choice is None:
            break
        if answer.choice == STEP_NONE:
            break
        is_unsure = answer.confidence < config.instruction.min_step_confidence
        if is_unsure:
            unsure.append((index, answer.choice, answer.confidence))
        verbs.append((index, answer.choice, answer.confidence))
    return verbs, unsure


def _unsure_refusal(unsure: list, config) -> str:
    """Name the steps that were not understood well enough to fly.
    飛ばせるだけ理解できなかった手順を名指しする。"""
    detail = "、".join(
        f"{index} 番目（{verb}、確信度 {confidence:.2f}）"
        for index, verb, confidence in unsure
    )
    return (
        f"確信度が閾値 {config.instruction.min_step_confidence:.2f} 未満の手順が"
        f"あるため実行しない: {detail}。言い換えて試してください"
    )


def _attach_amounts(verbs: list, numbers: list, judgement, config) -> list:
    """Give every move its amount: what was said, else what Jev sized it as.

    A figure the operator actually said outranks the model's size band
    without exception. The model chose from four coarse bands precisely
    because it must not handle figures, so where a figure exists there is
    nothing for the band to add.

    各動作に量を与える: 言われた数値、無ければ Jev が選んだ量の区分。

    操作者が実際に言った数値は、例外なくモデルの区分に優先する。モデルが
    粗い 4 区分から選んでいるのは、まさに数値を扱わせないためであり、数値が
    ある場所で区分が足せるものは無い。
    """
    spoken = _match_numbers_to_verbs(verbs, numbers)
    steps = []
    for index, verb, confidence in verbs:
        if verb in _AMOUNTLESS:
            steps.append(Step(verb=verb, amount=None, amount_source="none",
                              confidence=confidence))
            continue
        number = spoken.get(index)
        if number is not None:
            steps.append(Step(verb=verb, amount=number.value,
                              amount_source="spoken", confidence=confidence))
            continue
        amount, source = _named_amount(index, verb, judgement, config)
        steps.append(Step(verb=verb, amount=amount, amount_source=source,
                          confidence=confidence))
    return steps


def _match_numbers_to_verbs(verbs: list, numbers: list) -> dict:
    """Decide which spoken figure belongs to which move.

    Matching is by unit and then by order: degrees can only belong to a
    turn and centimetres only to a travelling move, so the two streams are
    paired off independently, each in the order both were given. That is
    how the sentence is built -- "上がって前に70cm進んで90度回って" gives the
    70 to the only travel that follows it and the 90 to the only turn --
    and it needs no arithmetic from the model.

    どの数値がどの動作のものかを決める。

    単位で分け、次に順序で対応させる。度は旋回にしか、cm は移動にしか属し
    えないので、2 つの列を別々に、どちらも言われた順で突き合わせる。文はその
    ように組み立てられており（「上がって前に70cm進んで90度回って」なら 70 は
    後続の唯一の移動へ、90 は唯一の旋回へ）、モデルの計算を要しない。
    """
    travel_verbs = [i for i, verb, _ in verbs if verb in _TRAVEL_BODY_AXIS]
    turn_verbs = [i for i, verb, _ in verbs if verb in _TURN_SIGN]
    distances = [n for n in numbers if n.unit == _UNIT_CM]
    angles = [n for n in numbers if n.unit == _UNIT_DEG]

    matched: dict = {}
    for step_index, number in zip(travel_verbs, distances):
        matched[step_index] = number
    for step_index, number in zip(turn_verbs, angles):
        matched[step_index] = number
    return matched


def _named_amount(index: int, verb: str, judgement, config) -> tuple:
    """The size band Jev chose, in centimetres or degrees.

    An `unspecified` band, a missing answer and a low-confidence answer all
    become the default: the operator did not say, so the question of which
    band it is has no right answer to be unsure about.

    Jev が選んだ量の区分を cm または度で返す。

    `unspecified`・答えの欠落・低い確信度は、いずれも既定値になる。操作者が
    言っていない以上、どの区分かという問いに、迷うべき正解が存在しない。
    """
    is_turn = verb in _TURN_SIGN
    bands = _turn_bands(config) if is_turn else _distance_bands(config)
    answer = judgement.answers.get(Q_STEP_AMOUNT.format(index))
    has_band = (answer is not None and answer.choice in bands
                and answer.confidence >= config.instruction.min_step_confidence)
    if has_band:
        return bands[answer.choice], "named"
    return bands[AMOUNT_MEDIUM], "default"


def _distance_bands(config) -> dict:
    cfg = config.instruction
    return {
        AMOUNT_SMALL: cfg.distance_small_cm,
        AMOUNT_MEDIUM: cfg.distance_medium_cm,
        AMOUNT_LARGE: cfg.distance_large_cm,
    }


def _turn_bands(config) -> dict:
    cfg = config.instruction
    return {
        AMOUNT_SMALL: cfg.turn_small_deg,
        AMOUNT_MEDIUM: cfg.turn_medium_deg,
        AMOUNT_LARGE: cfg.turn_large_deg,
    }


def _add_takeoff_and_landing(steps: list, on_ground: bool, auto_land: bool,
                             config) -> list:
    """Bracket the sequence with the steps that make it flyable.

    A move sent to a vehicle on the ground is refused by the firmware
    (`error not flying`, api_task.cpp cmdMove), and a sequence that ends in
    the air leaves the aircraft hovering with nobody instructing it. Both
    are added here rather than asked of the model: they follow from where
    the aircraft is, which is a fact the code has and the model does not.

    手順の列を、飛べる形にする手順で挟む。

    地上の機体に移動を送ってもファームが拒否し（`error not flying`、
    api_task.cpp の cmdMove）、空中で終わる列は、指示する者のいないまま機体を
    浮かせたままにする。どちらもモデルに問わずここで足す。これらは「機体が
    今どこにいるか」から決まることであり、それはコードが持っていてモデルが
    持たない事実だからである。
    """
    out = list(steps)
    needs_takeoff = on_ground and (not out or out[0].verb != STEP_TAKEOFF)
    if needs_takeoff:
        out.insert(0, Step(verb=STEP_TAKEOFF, amount_source="added"))
    needs_landing = auto_land and (not out or out[-1].verb != STEP_LAND)
    if needs_landing:
        out.append(Step(verb=STEP_LAND, amount_source="added"))
    return out


def _resolve_return_home(steps: list, config) -> list:
    """Replace each `return_home` with the `go` that actually flies it.

    `return_home` is a wish, not a command the vehicle has. Walking the
    sequence gives the displacement from the takeoff point at that moment,
    and rotating it into the body frame the aircraft is then facing gives
    the `go x y z speed` that cancels it. The arithmetic is here because
    the model cannot be asked to do it.

    各 `return_home` を、それを実際に飛ぶ `go` に置き換える。

    `return_home` は願いであって、機体が持つ指令ではない。手順を積算すれば
    その時点の離陸点からの変位が得られ、そのときの機首方向の機体座標へ回せば、
    変位を打ち消す `go x y z speed` になる。計算をここに置くのは、モデルに
    頼めないからである。
    """
    out = []
    state = _FlightState(config)
    for step in steps:
        if step.verb != STEP_RETURN_HOME:
            state.apply(step)
            out.append(step)
            continue
        out.append(_home_step(state, config))
        state.go_home()
    return out


def _home_step(state, config) -> "Step":
    """The `go` that returns to the takeoff point from where the walk is.
    積算中の位置から離陸点へ戻る `go`。"""
    return _GoStep(
        body=state.home_vector_body(),
        amount_source="computed",
    )


class _FlightState:
    """Where the aircraft would be, walking the sequence on paper.

    North/east/altitude in metres and a heading in radians, kept in the
    world frame so that a `forward` after a turn goes where the turn left
    the nose pointing. The SILS vehicle starts facing north (fixed in
    1d99538c), so a heading of zero is north for both this walk and the
    flight it predicts.

    手順を机上で積算したときの機体の位置。

    北・東・高度 [m] と機首方位 [rad] を世界座標で保持し、旋回の後の
    `forward` が、旋回で機首が向いた方向へ進むようにする。SILS の機体は北を
    向いて始まる（1d99538c で修正）ため、方位 0 はこの積算でも、それが予測する
    飛行でも北である。
    """

    def __init__(self, config):
        self.cfg = config
        self.north_m = 0.0
        self.east_m = 0.0
        self.altitude_m = config.instruction.takeoff_altitude_m
        self.heading_rad = 0.0

    def apply(self, step) -> None:
        """Move the paper aircraft by one step. / 机上の機体を 1 手順ぶん動かす。"""
        if step.verb in _TURN_SIGN:
            self.heading_rad += math.radians(_TURN_SIGN[step.verb] * step.amount)
            return
        if isinstance(step, _GoStep):
            forward_m, right_m, up_m = (v / 100.0 for v in step.body)
            self._translate(forward_m, right_m, up_m)
            return
        if step.verb not in _TRAVEL_BODY_AXIS:
            return
        forward, right, up = _TRAVEL_BODY_AXIS[step.verb]
        distance_m = step.amount / 100.0
        self._translate(forward * distance_m, right * distance_m, up * distance_m)

    def _translate(self, forward_m: float, right_m: float, up_m: float) -> None:
        """Apply a body-frame displacement in the current heading.
        機体座標の変位を、現在の機首方位で世界座標へ適用する。"""
        cos_h, sin_h = math.cos(self.heading_rad), math.sin(self.heading_rad)
        self.north_m += cos_h * forward_m - sin_h * right_m
        self.east_m += sin_h * forward_m + cos_h * right_m
        self.altitude_m += up_m

    def home_vector_body(self) -> tuple:
        """The body-frame displacement [cm] back to the takeoff point.
        離陸点へ戻る機体座標の変位 [cm]。"""
        cos_h, sin_h = math.cos(self.heading_rad), math.sin(self.heading_rad)
        north_to_home, east_to_home = -self.north_m, -self.east_m
        forward_m = cos_h * north_to_home + sin_h * east_to_home
        right_m = -sin_h * north_to_home + cos_h * east_to_home
        return (forward_m * 100.0, right_m * 100.0, 0.0)

    def go_home(self) -> None:
        self.north_m = 0.0
        self.east_m = 0.0


@dataclass
class _GoStep(Step):
    """A `return_home` after it has become a concrete `go`.

    It keeps the name `return_home` so the operator sees what they asked
    for, while carrying the vector that is actually sent. `go`'s three
    axes cannot be expressed as one `amount`, which is why this is a
    subclass rather than another verb in the table.

    `return_home` を具体的な `go` に直したもの。

    操作者が頼んだものが見えるよう名前は `return_home` のままにし、実際に
    送るベクトルを別に持つ。`go` の 3 軸は 1 つの `amount` では表せないため、
    表の中の別の動作ではなく派生クラスにしてある。
    """

    body: tuple = (0.0, 0.0, 0.0)   # forward, right, up [cm] / 前・右・上 [cm]

    def __init__(self, body: tuple, amount_source: str = "computed",
                 confidence: float = 1.0):
        super().__init__(verb=STEP_RETURN_HOME, amount=None,
                         amount_source=amount_source, confidence=confidence)
        self.body = body

    def command(self) -> str:
        forward, right, up = self.body
        # `go x y z speed`: x forward, y LEFT, z up [cm], speed [cm/s]
        # (api_task.cpp). The walk carries `right`, so the sign flips here.
        # `go x y z speed` は x 前・y 左・z 上 [cm]、speed [cm/s]（api_task.cpp）。
        # 積算は「右」で持っているので、ここで符号を反転する。
        speed_cm_s = round(DEFAULT_CONFIG.envelope.speed_max_mps * 100.0)
        return (f"go {round(forward)} {round(-right)} {round(up)} {speed_cm_s}")

    def describe(self) -> str:
        forward, right, _ = self.body
        distance = math.hypot(forward, right)
        return f"{STEP_RETURN_HOME} ({distance:.0f}cm)"


# =============================================================================
# The envelope, checked before anything moves / 動く前に検査する包絡
# =============================================================================

def check_envelope(steps: list, config=DEFAULT_CONFIG) -> str:
    """Walk the plan and return why it is refused, or "" if it is flyable.

    Refusing on paper is the whole point. The Arbiter's envelope check
    (arbiter.py) protects a flight already under way, one cycle at a time;
    this one refuses an instruction that was never going to stay inside
    the box, while the aircraft is still on the ground and the operator is
    still there to be told which step was the problem.

    計画を積算し、拒否の理由を返す。飛べるなら "" を返す。

    机上で拒否することに意味がある。Arbiter の包絡照合（arbiter.py）は、既に
    始まった飛行を 1 周期ずつ守る。こちらは、そもそも範囲に収まらない指示を、
    機体がまだ地上にあり、どの手順が問題かを伝えられる操作者がまだそこにいる
    うちに拒否する。
    """
    envelope = config.envelope
    state = _FlightState(config)
    for position, step in enumerate(steps, start=1):
        too_small, too_large = _amount_out_of_range(step, config)
        if too_small or too_large:
            return _range_refusal(position, step, too_small, config)
        state.apply(step)
        if step.verb == STEP_RETURN_HOME:
            state.go_home()
        # A landing ends the flight, so the altitude floor stops applying:
        # touching down is the one time the aircraft is meant to reach zero.
        # 着陸で飛行は終わるので、高度の下限はそこで適用をやめる。接地は、
        # 機体が 0 に達してよい唯一の場面である。
        if step.verb == STEP_LAND:
            break
        breach = _position_breach(state, envelope)
        if breach:
            return f"{position} 番目の手順「{step.describe()}」で{breach}"
    return ""


def _amount_out_of_range(step, config) -> tuple:
    """Whether a single move is shorter or longer than the vehicle accepts.
    1 回の移動が機体の受け付ける範囲より短いか長いか。"""
    if step.amount is None or step.verb in _TURN_SIGN:
        return False, False
    cfg = config.instruction
    return step.amount < cfg.move_min_cm, step.amount > cfg.move_max_cm


def _range_refusal(position: int, step, too_small: bool, config) -> str:
    cfg = config.instruction
    bound = f"{cfg.move_min_cm:g}cm 未満" if too_small else f"{cfg.move_max_cm:g}cm 超"
    return (
        f"{position} 番目の手順「{step.describe()}」が 1 回の移動として"
        f"{bound}である（機体が受け付ける範囲は "
        f"{cfg.move_min_cm:g}〜{cfg.move_max_cm:g}cm）"
    )


def _position_breach(state, envelope) -> str:
    """Why this position is outside the box, or "" if it is inside.
    この位置が範囲外である理由。範囲内なら ""。"""
    is_too_low = state.altitude_m < envelope.altitude_min_m
    is_too_high = state.altitude_m > envelope.altitude_max_m
    if is_too_low or is_too_high:
        return (
            f"高度が {state.altitude_m:.2f}m になり、許された "
            f"{envelope.altitude_min_m:g}〜{envelope.altitude_max_m:g}m を外れる"
        )
    radius = math.hypot(state.north_m, state.east_m)
    if radius > envelope.radius_max_m:
        return (
            f"離陸点から {radius:.2f}m 離れ、許された半径 "
            f"{envelope.radius_max_m:g}m を超える"
        )
    return ""


def _answers_for_trace(judgement) -> dict:
    """Every answer as {question_id: {choice, confidence}}, for the trace.
    記録用に、全答えを {質問 ID: {choice, confidence}} にする。"""
    return {
        qid: {"choice": answer.choice, "confidence": round(answer.confidence, 4)}
        for qid, answer in judgement.answers.items()
        if answer.kind == "choice"
    }


# =============================================================================
# Asking / 問い合わせ
# =============================================================================

def translate(instruction: str, judge, on_ground: bool = True,
              auto_land: bool = True, config=DEFAULT_CONFIG) -> Plan:
    """Ask Jev about one instruction and assemble the answer into a Plan.

    The state carries the instruction and nothing else. The design's state
    rules still hold -- no numbers, and nothing irrelevant -- and for this
    question the flight's condition IS irrelevant: the aircraft is on the
    ground and what is being decided is what the sentence means.

    指示 1 つを Jev に問い、答えを Plan に組み立てる。

    state に載せるのは指示だけである。設計の state の規則はここでも成り立つ
    （数値を載せない・無関係なものを載せない）。そしてこの問いにとって、飛行の
    状況は無関係である — 機体は地上にあり、決めるのは「その文が何を意味するか」
    だからである。
    """
    questions = step_questions(config.instruction.max_steps)
    state = {"operator_instruction": instruction}
    judgement = judge.ask_questions(state, questions)
    return build_plan(instruction, judgement, on_ground=on_ground,
                      auto_land=auto_land, config=config)
