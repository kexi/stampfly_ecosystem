"""
sfpilot.mission - a route the code owns, with Jev choosing what to do between legs.
sfpilot.mission - コードが持つ経路。区間の境目で次の一手を Jev が選ぶ。

The division of labour here is the mirror image of `sf pilot say`. There,
the operator supplied the intent and Jev turned it into moves. Here **the
route is already known** -- it is a file, written by whoever set up the
flight -- and what is not known is whether the aircraft is in a fit state
to carry on with it. So Jev is never asked where to go; it is asked, at
each leg boundary, whether to go on, wait, do the leg again, leave it out,
come home, or land.

分担は `sf pilot say` の裏返しである。あちらでは操作者が意図を出し、Jev が
それを動作に変えた。こちらでは**経路は既に分かっている** — 飛行を用意した者が
書いたファイルである — 分かっていないのは、機体がそれを続けられる状態にあるか
どうかである。そこで Jev に「どこへ行くか」は問わない。区間の境目ごとに、
進む・待つ・やり直す・飛ばす・戻る・着陸する、のどれかを問う。

Everything numeric stays on this side of the line, as the design requires.
Whether a leg arrived is not a question anyone asks Jev: the code measures
the distance to the intended end point and classifies it as "as planned",
"stopped short" or "overshot". How many times a leg has been retried is a
count, not a judgement. The model sees only the words.

数値の扱いは設計のとおり、すべてこちら側に留める。区間が到達したかを Jev に
問うことはしない。意図した終点までの距離をコードが測り、「目標どおり」
「手前で止まった」「行き過ぎた」に区分する。やり直した回数も判断ではなく
計数である。モデルが見るのは言葉だけである。

Four limits are the code's alone and no answer overrides them: the retries
per leg, the mission's total time, the ban on going ON when the battery is
running low, and the envelope. Jev proposing `next_step` on a low battery
is not wrong of it -- it cannot see a battery gauge, only the word "running
low" -- but the aircraft still must not fly another leg, so the refusal
lives in code where it cannot be argued with.

4 つの上限はコードだけが持ち、どの答えもそれを覆さない: 区間あたりのやり直し
回数、ミッション全体の時間、電池が「残り少ない」ときに進むことの禁止、そして
包絡である。電池が少ないときに Jev が `next_step` を選ぶこと自体は誤りではない
（見えているのは「残り少ない」という語だけで、電池計ではない）。それでも機体は
次の区間を飛んではならないので、却下はコード側に置き、議論の余地を無くす。
"""

import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import DEFAULT_CONFIG
from .instruction import (
    Step, _FlightState, _GoStep, _TRAVEL_BODY_AXIS, _TURN_SIGN, check_envelope,
)
from .judge import (
    MOVE_HOLD, MOVE_LAND, MOVE_NEXT, MOVE_REDO, MOVE_RETURN, MOVE_SKIP,
    STEP_LAND, STEP_RETURN_HOME, STEP_TAKEOFF,
)

# What the code decided to do with a leg, for the summary and the trace.
# These are outcomes, not Jev's choices: `skipped` can come from Jev's
# `skip_step` or from a retry limit, and the summary must distinguish "the
# model chose to move on" from "the code stopped allowing retries".
# 区間をどう扱ったかの結果。集計と記録のためのもので、Jev の選択そのものでは
# ない: `skipped` は Jev の `skip_step` からも、やり直し上限からも生じる。
# 集計では「モデルが先へ進むことを選んだ」と「コードがやり直しを許さなくなった」
# を区別する必要がある。
LEG_FLOWN = "flown"
LEG_REDONE = "redone"
LEG_SKIPPED = "skipped"
LEG_NOT_REACHED = "not_reached"

# How a leg ended up relative to where it was meant to end. The words are
# English because they go to Jev; the thresholds behind them are in
# MissionConfig, and the model never sees a distance.
# 区間が、終わるはずだった場所に対してどう終わったか。Jev へ送るので語は英語に
# する。しきい値は MissionConfig にあり、モデルは距離を見ない。
ARRIVAL_AS_PLANNED = "as planned"
ARRIVAL_SHORT = "stopped short"
ARRIVAL_OVERSHOT = "overshot"
ARRIVAL_UNKNOWN = ""

# Why the mission stopped. One of these is always set when it ends early.
# ミッションが終わった理由。途中で終わった場合は必ずどれかが設定される。
STOP_COMPLETED = "completed"
STOP_LANDED = "landed"
STOP_RETURNED = "returned home"
STOP_TIME_LIMIT = "time limit"
STOP_SAFETY = "safety layer"

# Roughly how long one leg takes, used only to pace a rehearsal scene over
# the length of a route (see `_expected_duration_s`). Measured on the `line`
# route in SILS: six legs in about 40 s, including the takeoff's climb and
# the settling between legs. It is never used for a decision about the
# flight -- those read the clock.
# 1 区間のおおよその所要時間。予行の場面を経路の長さに合わせて進めるためだけに
# 使う（`_expected_duration_s` 参照）。SILS の `line` 経路での実測: 6 区間で約
# 40 秒（離陸の上昇と区間の間の静定を含む）。飛行についての判断には一切使わない。
# そちらは時計を読む。
_TYPICAL_LEG_S = 7.0


class MissionError(ValueError):
    """A mission file that cannot be flown, with the reason in the message.
    飛ばせないミッションファイル。理由を本文に持つ。"""


@dataclass
class Leg:
    """One segment of the route: a move, and a name to call it by.

    The move reuses `instruction.Step` rather than a type of its own, so a
    leg and an instruction's step are flown by exactly the same code and
    checked against exactly the same envelope. A route that a `sf pilot
    say` sequence could not fly is not one a mission may fly either.

    経路の 1 区間: 1 つの動作と、それを呼ぶ名前。

    動作には専用の型を作らず `instruction.Step` を再利用する。区間と指示の手順が
    まったく同じコードで飛び、まったく同じ包絡で検査されるようにするためである。
    `sf pilot say` の手順として飛べない経路は、ミッションとしても飛ばせない。
    """

    step: Step
    label: str = ""

    def describe(self) -> str:
        """One line for the operator. / 操作者向けの 1 行。"""
        if self.label:
            return f"{self.label}（{self.step.describe()}）"
        return self.step.describe()

    def preview_command(self) -> str:
        """The API line this leg will send, as far as it is known now.

        A `return_home` has no line until the flight reaches it: the `go`
        that cancels the displacement depends on where the aircraft has
        actually got to, including any leg that was redone or skipped. The
        route listing says so rather than printing a line it would have to
        guess.

        この区間が送る API 行。現時点で分かる範囲で返す。

        `return_home` には、飛行がそこへ達するまで行が存在しない。変位を打ち消す
        `go` は、機体が実際にどこまで来たか（やり直した区間・飛ばした区間を
        含む）で決まるためである。経路の一覧は、推測した行を表示するのではなく
        その旨を書く。
        """
        if self.step.verb == STEP_RETURN_HOME:
            return "go …（飛行時に計算）"
        return self.step.command()


@dataclass
class Mission:
    """A whole route, already checked against the envelope.
    経路一式。包絡の検査は済んでいる。"""

    name: str = ""
    purpose: str = ""
    legs: list = field(default_factory=list)
    source: str = ""

    @property
    def total(self) -> int:
        return len(self.legs)


@dataclass
class LegResult:
    """What became of one attempt at one leg. / ある区間の 1 回の試行の結果。"""

    index: int                      # 1-based position in the route / 経路中の位置（1 始まり）
    label: str
    outcome: str                    # LEG_* / 区間の扱い
    arrival: str = ARRIVAL_UNKNOWN
    attempt: int = 1                # which try this was / 何回目の試行か
    move_choice: str = ""           # what Jev chose after it / 直後に Jev が選んだもの
    move_confidence: float = 0.0
    refusal: str = ""               # why the code overrode Jev / コードが覆した理由
    elapsed_s: float = 0.0


@dataclass
class MissionOutcome:
    """The whole flight, as the summary prints it.
    飛行全体。集計の表示に使う形。"""

    mission: str = ""
    results: list = field(default_factory=list)
    stop_reason: str = STOP_COMPLETED
    stop_detail: str = ""
    landed: bool = False
    decisions: int = 0
    flown_s: float = 0.0
    latencies: list = field(default_factory=list)
    # The decision rows themselves, for writing the timeline beside the
    # flight-log bundle. `decisions` stays the COUNT so the printed
    # summary is unchanged.
    # 判断の行そのもの。フライトログ一式の隣に時系列を書き出すために持つ。
    # `decisions` は**件数**のままとし、表示を変えない。
    decision_rows: list = field(default_factory=list)

    @property
    def completed(self) -> bool:
        """Whether every leg was flown or deliberately left out.
        全区間を飛ぶか、意図して飛ばし終えたか。"""
        return self.stop_reason == STOP_COMPLETED


# =============================================================================
# Loading a mission file / ミッションファイルの読み込み
# =============================================================================

def load_mission(path, config=DEFAULT_CONFIG) -> Mission:
    """Read a mission file and refuse it now if it could not be flown.

    Checking the envelope at load time rather than at the leg that breaches
    it is the same rule `sf pilot say` follows: a route that leaves the box
    should be refused while the aircraft is on the ground and the operator
    is still there to be told which leg was the problem.

    ミッションファイルを読み、飛べないものはこの時点で拒否する。

    違反する区間に達してからではなく読み込み時に包絡を検査するのは、
    `sf pilot say` と同じ規則である。範囲を出る経路は、機体がまだ地上にあり、
    どの区間が問題かを伝えられる操作者がそこにいるうちに拒否すべきである。
    """
    data = _read_mission_file(path)
    legs = _build_legs(data, config)
    if not legs:
        raise MissionError(f"{path}: 区間が 1 つも無い（`legs` を書いてください）")

    breach = _envelope_breach(legs, config)
    if breach:
        raise MissionError(f"{path}: 包絡の事前検査で拒否した — {breach}")

    return Mission(
        name=str(data.get("name") or Path(path).stem),
        purpose=str(data.get("purpose") or ""),
        legs=legs,
        source=str(path),
    )


def _envelope_breach(legs: list, config) -> str:
    """Why this route is refused, naming the leg the operator wrote.

    `instruction.check_envelope` does the walking and names the STEP, which
    for an instruction is all there is. A mission's legs have the operator's
    own labels ("1 辺目（北へ）"), and those are what the refusal has to say:
    a message naming "forward 300cm" leaves it to the reader to work out
    which of four identical sides is meant.

    この経路が拒否される理由。操作者が書いた区間名を添える。

    積算を行い、**手順**を名指しするのは `instruction.check_envelope` である。
    指示にとってはそれが全てだが、ミッションの区間には操作者自身が付けた名前が
    ある（「1 辺目（北へ）」）。拒否が告げるべきなのはそちらである。
    「forward 300cm」と名指しするだけでは、同じ 4 辺のどれを指すのかを読み手が
    突き止めることになる。
    """
    breach = check_envelope([leg.step for leg in legs], config)
    if not breach:
        return ""
    for position, leg in enumerate(legs, start=1):
        is_the_named_step = breach.startswith(f"{position} 番目の手順")
        if is_the_named_step and leg.label:
            return breach.replace(f"{position} 番目の手順",
                                  f"{position} 番目の区間「{leg.label}」の", 1)
    return breach


def _read_mission_file(path) -> dict:
    """Parse the file as YAML, falling back to JSON. / YAML として読む。無ければ JSON。"""
    import json

    file_path = Path(path)
    if not file_path.exists():
        raise MissionError(f"{path}: ファイルがありません")
    text = file_path.read_text(encoding="utf-8")
    try:
        import yaml
        data = yaml.safe_load(text)
    except ImportError:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MissionError(
                f"{path}: PyYAML が無く、JSON としても読めません: {exc}"
            ) from exc
    if not isinstance(data, dict):
        raise MissionError(f"{path}: 先頭は対応表（マップ）である必要があります")
    return data


def _build_legs(data: dict, config) -> list:
    """Turn the file's `legs` entries into checked Legs.
    ファイルの `legs` の各項目を、検査済みの Leg にする。"""
    raw_legs = data.get("legs")
    if not isinstance(raw_legs, list):
        raise MissionError("`legs` がリストではありません")
    legs = []
    for position, entry in enumerate(raw_legs, start=1):
        legs.append(_build_leg(position, entry, config))
    return legs


def _build_leg(position: int, entry, config) -> Leg:
    """One `legs` entry -> a Leg, or a refusal naming the entry.
    `legs` の 1 項目を Leg にする。できなければその項目を名指しして拒否する。"""
    if not isinstance(entry, dict):
        raise MissionError(f"{position} 番目の区間が対応表ではありません: {entry!r}")
    verb = entry.get("verb")
    if not verb:
        raise MissionError(f"{position} 番目の区間に `verb` がありません")

    amount = entry.get("amount")
    needs_amount = verb in _TRAVEL_BODY_AXIS or verb in _TURN_SIGN
    if needs_amount and amount is None:
        raise MissionError(f"{position} 番目の区間「{verb}」に `amount` がありません")
    if not needs_amount and verb not in (STEP_TAKEOFF, STEP_LAND, STEP_RETURN_HOME):
        raise MissionError(
            f"{position} 番目の区間の `verb` が不正です: {verb!r}"
            f"（使えるのは {', '.join(_known_verbs())}）"
        )

    step = _leg_step(verb, amount)
    return Leg(step=step, label=str(entry.get("label") or ""))


def _leg_step(verb: str, amount) -> Step:
    """The Step a leg flies. `return_home` becomes its `go` at run time.

    A mission's `return_home` cannot be resolved here the way an
    instruction's is: an instruction is walked on paper from a known
    takeoff point, while a mission may reach its `return_home` after legs
    that were redone or skipped, so the displacement is only known once
    the flight gets there.

    区間が飛ぶ Step。`return_home` は実行時に `go` になる。

    ミッションの `return_home` は、指示のときのようにここで解決できない。指示は
    既知の離陸点から机上で積算するが、ミッションは、やり直したり飛ばしたりした
    区間の後に `return_home` へ達しうる。変位が分かるのは、飛行がそこへ着いた
    ときだけである。
    """
    if amount is None:
        return Step(verb=verb, amount=None, amount_source="mission")
    return Step(verb=verb, amount=float(amount), amount_source="mission")


def _known_verbs() -> tuple:
    return tuple(sorted(
        set(_TRAVEL_BODY_AXIS) | set(_TURN_SIGN)
        | {STEP_TAKEOFF, STEP_LAND, STEP_RETURN_HOME}
    ))


def builtin_mission_path(name: str) -> Path:
    """Where a mission shipped with the package lives.
    パッケージに同梱したミッションの置き場所。"""
    return Path(__file__).parent / "missions" / f"{name}.yaml"


def resolve_mission_path(name_or_path: str) -> Path:
    """A file the operator named, or a shipped mission of that name.

    Accepting a bare name is what makes `sf pilot mission square` work in a
    classroom without anyone learning the package's directory layout; an
    existing path always wins, so a local `square.yaml` is never shadowed.

    操作者が指定したファイル、または同名の同梱ミッション。

    裸の名前を受け付けることで、教室で `sf pilot mission square` が、パッケージの
    ディレクトリ構成を覚えずに使える。既存のパスを常に優先するので、手元の
    `square.yaml` が隠されることはない。
    """
    candidate = Path(name_or_path)
    if candidate.exists():
        return candidate
    builtin = builtin_mission_path(name_or_path)
    if builtin.exists():
        return builtin
    return candidate


# =============================================================================
# Where the aircraft is meant to be / 機体がいるはずの場所
# =============================================================================

class MissionWalk:
    """The intended position, advanced one flown leg at a time.

    This is the same paper walk `instruction.check_envelope` does, kept
    running DURING the flight so that each leg's intended end point is
    known when the leg finishes. Comparing it with the measured position is
    how a leg's arrival is classified, and it is why the classification is
    code's work and not the model's.

    意図した位置。飛び終えた区間ごとに 1 つずつ進める。

    `instruction.check_envelope` が行う机上の積算と同じものを、飛行**中**も
    動かし続ける。各区間の意図した終点を、その区間が終わった時点で知るためで
    ある。これを実測位置と比べることが区間の到達の区分であり、その区分が
    モデルではなくコードの仕事である理由でもある。
    """

    def __init__(self, config=DEFAULT_CONFIG):
        self._state = _FlightState(config)

    @property
    def north_m(self) -> float:
        return self._state.north_m

    @property
    def east_m(self) -> float:
        return self._state.east_m

    @property
    def heading_rad(self) -> float:
        return self._state.heading_rad

    def apply(self, step) -> None:
        """Advance the walk by one leg that was actually flown.
        実際に飛んだ区間 1 つぶん、積算を進める。"""
        self._state.apply(step)
        if step.verb == STEP_RETURN_HOME:
            self._state.go_home()

    def home_step(self) -> "_GoStep":
        """The `go` that returns to the takeoff point from here.
        ここから離陸点へ戻る `go`。"""
        return _GoStep(body=self._state.home_vector_body(), amount_source="computed")

    def is_already_home(self, config=DEFAULT_CONFIG) -> bool:
        """Whether a `return_home` from here would be too short to send.

        The firmware refuses a move shorter than 10 cm outright (`error out
        of range`, api_task.cpp kMoveMinCm), and a route that closes on
        itself -- a square, for instance -- arrives back at the takeoff
        point by construction. Sending the `go` anyway would fail the leg
        for having succeeded.

        ここからの `return_home` が、送るには短すぎるかどうか。

        ファームは 10cm 未満の移動をはっきり拒否する（`error out of range`。
        api_task.cpp の kMoveMinCm）。そして自身で閉じる経路 — たとえば四角形 —
        は、作りからして離陸点へ戻ってくる。それでも `go` を送れば、成功したこと
        を理由に区間が失敗する。
        """
        forward_cm, right_cm, _ = self._state.home_vector_body()
        distance_cm = math.hypot(forward_cm, right_cm)
        return distance_cm < config.instruction.move_min_cm

    def target_after(self, step) -> tuple:
        """Where the aircraft would be once `step` is flown, as (north, east).

        Looked ahead WITHOUT committing, because the arrival classification
        needs the target while the walk must not advance until the leg is
        known to have been flown -- a leg that is redone must be measured
        against the same target the second time.

        `step` を飛んだ後に機体がいるはずの位置 (北, 東)。

        積算を進めずに先読みする。到達の区分には終点が要る一方、積算は区間が
        実際に飛べたと分かるまで進めてはならないからである。やり直した区間は、
        2 回目も同じ終点に対して測る必要がある。
        """
        probe = _FlightState(DEFAULT_CONFIG)
        probe.north_m = self._state.north_m
        probe.east_m = self._state.east_m
        probe.altitude_m = self._state.altitude_m
        probe.heading_rad = self._state.heading_rad
        probe.apply(step)
        if step.verb == STEP_RETURN_HOME:
            probe.go_home()
        return probe.north_m, probe.east_m


def classify_arrival(start: tuple, target: tuple, measured: tuple,
                     config=DEFAULT_CONFIG) -> str:
    """Turn "how close did it get" into one of three words.

    Short and overshot are told apart by projecting the error onto the leg's
    OWN direction (start -> target) rather than by distance alone: a leg
    that ended 20 cm to the SIDE is neither short nor long of its target,
    and calling it "overshot" would describe a different problem than the
    one that happened. Distance alone decides whether it arrived at all.

    A leg with no horizontal travel -- a turn, a climb -- is judged on
    distance alone. It was never going anywhere horizontally, so "short"
    and "overshot" have no meaning for it; what is being asked is only
    whether the craft stayed where it was, and any error is sideways by
    definition. Such a leg is reported as `stopped short` only when it
    drifted out of tolerance, never merely for being a turn.

    「どれだけ近づいたか」を 3 つの語のどれかにする。

    「手前」と「行き過ぎ」は、距離だけでなく区間**自身**の進行方向（始点→終点）
    へ誤差を射影して区別する。終点の 20cm **横**で終わった区間は、手前でも
    行き過ぎでもない。それを「行き過ぎ」と呼べば、実際に起きたのとは別の問題を
    述べることになる。到達したかどうかは距離だけが決める。

    水平移動を伴わない区間（旋回・上昇）には射影する方向が無く、水平の誤差は
    定義上すべて横方向になる。これは `stopped short`（手前で止まった）として
    報告する — 行くはずの場所に行けていないのは事実であり、「行き過ぎ」と
    言えば、存在しない方向を知っているかのように述べることになるためである。
    """
    target_n, target_e = target
    measured_n, measured_e = measured
    error_n, error_e = measured_n - target_n, measured_e - target_e
    distance = math.hypot(error_n, error_e)
    is_close_enough = distance <= config.mission.arrival_tolerance_m
    if is_close_enough:
        return ARRIVAL_AS_PLANNED

    leg_n, leg_e = target_n - start[0], target_e - start[1]
    has_no_direction = math.hypot(leg_n, leg_e) <= 0.0
    if has_no_direction:
        return ARRIVAL_SHORT
    # Positive projection means the craft is FURTHER along the leg's own
    # direction than the target was.
    # 射影が正なら、機体は区間の進行方向で見て終点より先にいる。
    along = error_n * leg_n + error_e * leg_e
    return ARRIVAL_OVERSHOT if along > 0 else ARRIVAL_SHORT


# =============================================================================
# The state Jev is told about the mission / ミッションについて Jev に伝える状況
# =============================================================================

def mission_state(mission: Mission, index: int, arrival: str, retries: int,
                  elapsed_s: float, config=DEFAULT_CONFIG) -> dict:
    """The `mission` section of the state, in words and nothing else.

    "3/4" is a position, not a figure to compute with, and `assert_no_numbers`
    accepts it because it is a string. That is the line the design draws:
    the model may be TOLD where it is in the route, because knowing it is
    near the end is part of judging whether to press on; it may not be given
    a distance, a percentage or a clock, because it would have to compare
    them and it is documented not to be able to.

    Elapsed time is a word for the same reason, and the signature excludes
    it (summarizer.signature) so that a mission does not look like a new
    situation every second merely because time passed.

    state の `mission` の部分。言葉だけで組み立てる。

    「3/4」は位置であって計算に使う数値ではなく、文字列なので
    `assert_no_numbers` は通す。設計が引く線はそこである。経路のどこにいるかを
    モデルに**伝えて**よい — 終わりが近いと分かることは、押し通すかどうかの判断の
    一部だからである。距離・百分率・時計は渡さない。渡せば比較させることになり、
    それができないと文書化されているからである。

    経過時間を言葉にするのも同じ理由による。指紋からは除外してあり
    （summarizer.signature）、時間が過ぎただけでミッションが毎秒新しい状況に
    見えることはない。
    """
    leg = mission.legs[index]
    state = {
        "purpose": mission.purpose or mission.name,
        "current_leg": f"{index + 1}/{mission.total}",
        "current_leg_is": leg.describe(),
        "elapsed": _elapsed_word(elapsed_s, config),
        "retries_on_this_leg": _retry_word(retries, config),
    }
    if arrival:
        state["leg_arrival"] = arrival
    return state


def _elapsed_word(elapsed_s: float, config) -> str:
    """How far through its time budget the mission is, as a word.
    ミッションが時間の予算のどのあたりにいるかを 1 語で表す。"""
    limit = config.mission.time_limit_s
    if limit <= 0:
        return "just started"
    fraction = elapsed_s / limit
    if fraction < 0.33:
        return "plenty of time left"
    if fraction < 0.75:
        return "about halfway through the time allowed"
    return "close to the time limit"


def _retry_word(retries: int, config) -> str:
    """How many times this leg has been redone, as a word.

    "already twice" rather than "2" because the decision that follows from
    the count -- may it be redone again? -- is taken in code. What the model
    does with the word is weigh how well the route is going, and for that a
    word is enough.

    この区間を何回やり直したかを 1 語で表す。

    「2」ではなく「already twice」とするのは、計数から従う判断 —— もう一度
    やり直してよいか —— をコードが取るからである。モデルがこの語でするのは
    「経路がどれだけ順調か」の重み付けであり、それには語で足りる。
    """
    if retries <= 0:
        return "not yet"
    if retries == 1:
        return "once already"
    is_at_limit = retries >= config.mission.max_retries_per_leg
    if is_at_limit:
        return "already at the limit for this leg"
    return "already twice"


# =============================================================================
# Flying the route / 経路を飛ぶ
# =============================================================================

def fly_mission(link, judge, mission: Mission, config=DEFAULT_CONFIG,
                trace=None, scene=None, on_event=None,
                on_cycle=None, on_decisions=None) -> MissionOutcome:
    """Fly the route, asking Jev what to do at every leg boundary.

    The safety layer of use (1) runs throughout, unchanged and unweakened:
    the same Monitor, Summarizer, Judge and Arbiter `sf pilot run` uses,
    with the same rules. A mission being under way is not a reason to let
    the aircraft carry on with something the safety layer would stop.

    経路を飛び、区間の境目ごとに次の一手を Jev に問う。

    用途①の安全層は飛行中ずっと動き続ける。`sf pilot run` と同じ Monitor・
    Summarizer・Judge・Arbiter を、同じ規則で使い、緩めない。ミッションが
    進行中であることは、安全層が止めるはずのものを機体に続けさせる理由に
    ならない。
    """
    flight = _MissionFlight(link, judge, mission, config, trace, scene, on_event,
                            on_cycle=on_cycle, on_decisions=on_decisions)
    return flight.run()


class _MissionFlight:
    """One mission from takeoff to touchdown.

    Held as an object rather than one long function because the loop needs
    several pieces of state that every part of it reads -- where the walk
    has got to, which leg is current, how many times it has been redone --
    and threading those through nested functions would hide what the limits
    actually apply to.

    離陸から接地までのミッション 1 回分。

    長い関数ではなくオブジェクトにしてあるのは、ループのどの部分もいくつかの
    状態を読むためである（積算がどこまで進んだか、今どの区間か、何回やり直した
    か）。入れ子の関数に引き回すと、上限が実際に何に対して効いているのかが
    見えなくなる。
    """

    def __init__(self, link, judge, mission, config, trace, scene, on_event,
                 on_cycle=None, on_decisions=None):
        from .pilot import Pilot

        self.link = link
        self.mission = mission
        self.cfg = config
        self.scene = scene
        self.on_event = on_event
        # Called once per monitor cycle when set (the live browser view uses
        # it to draw the aircraft). Must not block: it runs at 50Hz.
        # 設定されていれば監視周期ごとに 1 回呼ばれる（ブラウザのライブ表示が
        # 機体を描くのに使う）。50Hz で動くのでブロックしてはならない。
        self.on_cycle = on_cycle
        # Called once with the Pilot's decision list, before the first leg.
        # 最初の区間の前に、Pilot の判断の配列を 1 度だけ渡す先。
        self.on_decisions = on_decisions
        self.walk = MissionWalk(config)
        self.pilot = Pilot(link, judge, config, trace=trace,
                           mission=mission_state(mission, 0, ARRIVAL_UNKNOWN, 0, 0.0, config))
        # The mission drives the vehicle; the safety layer watches and, if
        # it must, stops. A hovering `rc` sent alongside a leg would publish
        # a velocity target and cancel the leg's position target -- the same
        # finding P3 recorded for `sf pilot say` (Executor.hold_commands_silently).
        # 機体を駆動するのはミッションで、安全層は見張って必要なら止める。区間と
        # 並行して送る待機の `rc` は速度目標を publish し、区間の位置目標を打ち
        # 消す — P3 が `sf pilot say` について記録したのと同じ事実である。
        self.pilot.executor.hold_commands_silently = True
        self.outcome = MissionOutcome(mission=mission.name)
        self.started = 0.0
        self.index = 0
        self.retries = 0

    # -- the loop / ループ -----------------------------------------------

    def _step_pilot(self, now: float = None) -> None:
        """One monitor cycle: judge, hold the sticks, feed the live view.

        Every loop in this class goes through here rather than calling
        `pilot.step()` itself, so a new loop cannot silently be the one
        that forgets to keep the sticks centred or to update the view.

        監視周期を 1 つ進める（判断し、スティックを中立に保ち、ライブ表示へ渡す）。

        本クラスの全てのループは `pilot.step()` を自分で呼ばず、ここを通る。
        新しいループが、スティックの中立保持や表示の更新を黙って忘れた 1 つに
        なりえないようにするためである。
        """
        self.pilot.step()
        self.link.hold_sticks_neutral()
        if self.on_cycle is not None:
            self.on_cycle(self.pilot.monitor.latest_sample,
                          time.monotonic() if now is None else now)

    def run(self) -> MissionOutcome:
        """Walk the legs until the route ends or something stops it.
        経路が終わるか、何かが止めるまで区間を進める。"""
        # Hand the decision list over before the first leg, so a watcher's
        # totals count from the first decision.
        # 最初の区間の前に判断の配列を渡す。見ている側の現在値が最初の判断から
        # 数えられるようにするためである。
        if self.on_decisions is not None:
            self.on_decisions(self.pilot.decisions)
        self.started = time.monotonic()
        while self.index < self.mission.total:
            stop = self._check_limits()
            if stop:
                self._stop(*stop)
                break
            if not self._fly_current_leg():
                break
            if not self._decide_next():
                break
        self._land_if_still_flying()
        self.outcome.decisions = len(self.pilot.decisions)
        self.outcome.decision_rows = list(self.pilot.decisions)
        self.outcome.flown_s = time.monotonic() - self.started
        return self.outcome

    def _check_limits(self):
        """The code's own reasons to stop, checked before each leg.

        Checked BEFORE the leg rather than after, because a limit reached
        while a leg is in the air would be found out one whole leg too late
        -- the point of a time limit is not to notice the overrun.

        コード自身が持つ、止める理由。各区間の前に確認する。

        区間の後ではなく前に確認する。空中で上限に達すると、まるまる 1 区間
        遅れて判明するからである。時間の上限は、超過に気づくためのものではない。
        """
        elapsed = time.monotonic() - self.started
        is_out_of_time = elapsed >= self.cfg.mission.time_limit_s
        if is_out_of_time:
            return STOP_TIME_LIMIT, (
                f"ミッションの時間上限 {self.cfg.mission.time_limit_s:g} 秒に達した"
            )
        return None

    def _fly_current_leg(self) -> bool:
        """Send one leg and wait for it, watched by the safety layer.
        区間を 1 つ送り、安全層に見張らせながら待つ。返り値は続行可否。"""
        from .say import StepRunner

        leg = self.mission.legs[self.index]
        leg_started = time.monotonic()
        if self._is_a_no_op(leg):
            self.last_arrival = ARRIVAL_AS_PLANNED
            self.last_leg_elapsed = time.monotonic() - leg_started
            return True

        step = self._resolved_step(leg)
        start = (self.walk.north_m, self.walk.east_m)
        target = self.walk.target_after(step)

        runner = StepRunner(self.link, [step], self.cfg,
                            speed_probe=self.pilot.horizontal_speed)
        runner.start()
        interrupt = self._watch(runner)
        if interrupt:
            self._record(leg, LEG_NOT_REACHED, ARRIVAL_UNKNOWN,
                         refusal=interrupt, elapsed_s=time.monotonic() - leg_started)
            self._stop(STOP_SAFETY, interrupt)
            return False

        self.walk.apply(step)
        self.last_arrival = classify_arrival(start, target, self._measured(), self.cfg)
        self.last_leg_elapsed = time.monotonic() - leg_started
        self.last_step = step
        return True

    def _is_a_no_op(self, leg: Leg) -> bool:
        """Whether this leg has nothing left to fly.

        Only `return_home` can be one: a route that closes on itself is
        already home when it reaches that leg, and the `go` would be
        shorter than the firmware accepts. Treating it as arrived is what
        it is -- the leg's purpose was to be at the takeoff point, and the
        aircraft is.

        この区間に飛ぶものが残っていないか。

        そうなりうるのは `return_home` だけである。自身で閉じる経路は、その区間に
        達した時点で既に離陸点にいて、`go` はファームが受け付ける長さに満たない。
        到達として扱うのは事実そのままである — この区間の目的は離陸点にいること
        であり、機体はそこにいる。
        """
        if leg.step.verb != STEP_RETURN_HOME:
            return False
        return self.walk.is_already_home(self.cfg)

    def _resolved_step(self, leg: Leg):
        """The Step this leg actually sends, `return_home` included.
        この区間が実際に送る Step。`return_home` も解決済み。"""
        if leg.step.verb != STEP_RETURN_HOME:
            return leg.step
        return self.walk.home_step()

    def _watch(self, runner) -> str:
        """Run the safety layer while a leg flies; return why it must stop.
        区間の飛行中に安全層を回す。止めるべき理由を返す。続けてよければ ""。"""
        from .say import _interrupt_reason

        period = 1.0 / self.cfg.monitor_hz
        while not runner.done:
            cycle_start = time.monotonic()
            self._step_pilot(cycle_start)
            self._drive_scene(cycle_start)
            interrupt = _interrupt_reason(self.pilot)
            if interrupt:
                runner.stop()
                return interrupt
            slack = period - (time.monotonic() - cycle_start)
            if slack > 0:
                time.sleep(slack)
        return ""

    def _drive_scene(self, now: float) -> None:
        """Let the rehearsal scene act, if there is one.

        The scene is walked against the time the ROUTE is expected to take,
        not against the mission's time limit. A scene is a fault that
        develops over a flight (`battery_drop` walks the pack down), and
        pacing it by the limit would mean a short route lands before the
        fault has developed at all -- measured: a 40 s route under a 60 s
        limit finished with the battery still healthy, so the scene
        rehearsed nothing.

        予行の場面があれば作用させる。

        場面は、ミッションの時間上限ではなく**経路**の所要見込みに対して進める。
        場面は飛行を通じて進行する故障であり（`battery_drop` はパックを下げていく）、
        上限を基準に進めると、短い経路では故障が進む前に着陸してしまう。実測では、
        60 秒の上限のもとで 40 秒の経路が、電池が健全なまま終わった。場面は何も
        予行していなかったことになる。
        """
        if self.scene is None or self.scene.drive is None:
            return
        self.scene.drive(self.link, now - self.started, self._expected_duration_s())

    def _expected_duration_s(self) -> float:
        """Roughly how long this route should take, for pacing a scene.

        A rough figure is enough and a precise one is not available: how
        long a leg takes depends on how the flight goes. The estimate is
        the legs multiplied by a typical leg, bounded by the mission's own
        limit, so a scene always completes within a flight that completes.

        この経路のおおよその所要時間。場面の進み具合を決めるためのもの。

        おおよそで足り、正確な値は得られない。1 区間の所要時間は飛行の経過に
        よるからである。見積りは「区間数 × 標準的な 1 区間」とし、ミッション自身の
        上限で頭打ちにする。完走する飛行の中で、場面も必ず進み切るようにする。
        """
        estimate = self.mission.total * _TYPICAL_LEG_S
        return min(estimate, self.cfg.mission.time_limit_s)

    def _measured(self) -> tuple:
        """Where the firmware believes the aircraft is, as (north, east).

        The firmware's estimate rather than the truth, deliberately: the
        truth is not available in flight and will not be on real hardware
        either. The arrival classification has to be one the aircraft can
        make about itself.

        ファームが機体の位置だと考えている場所 (北, 東)。

        真値ではなく推定を使う。意図的である。真値は飛行中には手に入らず、実機でも
        手に入らない。到達の区分は、機体が自分自身について下せるものでなければ
        ならない。
        """
        sample = self.pilot.monitor.latest_sample or {}
        north, east = sample.get("pos_n"), sample.get("pos_e")
        if north is None or east is None:
            return (self.walk.north_m, self.walk.east_m)
        return (north, east)

    # -- what to do next / 次の一手 --------------------------------------

    def _decide_next(self) -> bool:
        """Ask Jev, apply the code's limits, and move the index. Continue?
        Jev に問い、コードの上限を当て、区間を進める。続行するかを返す。"""
        leg = self.mission.legs[self.index]
        elapsed = time.monotonic() - self.started
        self.pilot.mission = mission_state(
            self.mission, self.index, self.last_arrival, self.retries, elapsed, self.cfg
        )
        choice, confidence = self._ask_next_move(elapsed)
        allowed, refusal = self._allow(choice)
        self._announce(leg, allowed, refusal)

        if allowed == MOVE_REDO:
            self.retries += 1
            self._record(leg, LEG_REDONE, self.last_arrival, choice, confidence, refusal)
            return True
        if allowed in (MOVE_RETURN, MOVE_LAND):
            self._record(leg, LEG_FLOWN, self.last_arrival, choice, confidence, refusal)
            return self._finish_early(allowed, refusal or f"Jev が {choice} を選択")

        outcome = LEG_SKIPPED if allowed == MOVE_SKIP else LEG_FLOWN
        self._record(leg, outcome, self.last_arrival, choice, confidence, refusal)
        self.index += 1
        self.retries = 0
        return True

    def _ask_next_move(self, elapsed: float) -> tuple:
        """An answer about THIS leg boundary, or a hold if none arrives.

        The answer comes from the SAME request the safety layer already
        makes -- one request per situation change, carrying every question
        (judge.WITH_MISSION). Asking separately would double the round
        trips and the token cost for an answer the loop is about to hold.

        But it has to be an answer to the question that includes this leg's
        arrival. The loop's newest answer at this instant was asked before
        the leg finished, so it describes the PREVIOUS boundary: accepting
        it would decide each leg on the one before it, and a leg that
        stopped short would be answered as though it had arrived. So the
        state is updated first, then the loop is turned until an answer
        asked under it comes back.

        A missing or unconfident answer becomes `hold`, which is the
        Arbiter's rule for every uncertain case, applied here to the
        mission question instead of the safety one.

        **この**区間の境目についての答え。届かなければ待機。

        答えは、安全層が既に出している**同じ**リクエストから来る — 状況の区分が
        変わるごとに 1 リクエスト、全質問を載せて（judge.WITH_MISSION）。別に
        問えば、ループがこれから持つ答えのために往復とトークン費用を倍にする
        ことになる。

        ただし、それは**この区間の到達を含む**質問への答えでなければならない。
        この瞬間にループが持つ最新の答えは、区間が終わる前に問うたものであり、
        1 つ前の境目を述べている。それを採れば、各区間を 1 つ前の区間で判断する
        ことになり、手前で止まった区間が「到達した」ものとして答えられてしまう。
        そこで先に state を更新し、その state の下で問われた答えが返るまでループを
        回す。

        答えが無い・確信度が低い場合は `hold` になる。不確かな場合はすべて待機と
        するのは Arbiter の規則であり、それを安全の質問ではなくミッションの質問に
        当てたものである。
        """
        from .judge import Q_NEXT_MOVE

        judgement = self._await_fresh_judgement()
        if judgement is None or judgement.error is not None:
            return MOVE_HOLD, 0.0
        answer = judgement.answers.get(Q_NEXT_MOVE)
        if answer is None or answer.choice is None:
            return MOVE_HOLD, 0.0
        is_unconfident = answer.confidence < self.cfg.judge.min_confidence
        if is_unconfident:
            return MOVE_HOLD, answer.confidence
        return answer.choice, answer.confidence

    def _await_fresh_judgement(self):
        """Turn the loop until an answer asked under the current state returns.

        Bounded by the same ceiling a hold is: if nothing usable arrives in
        that time the Arbiter has been hovering throughout and its own
        timer is about to land the craft, so waiting longer would only
        spend battery to reach the same place.

        The loop keeps running while waiting -- the safety layer, the stick
        stream and the immediate safety rules all depend on it, and a leg
        boundary is not a reason to stop watching the flight.

        現在の state の下で問われた答えが返るまでループを回す。

        待機と同じ上限で打ち切る。その時間内に使える答えが来ないなら、Arbiter は
        その間ずっと待機しており、自身の計時がまもなく機体を着陸させる。それ以上
        待っても、同じ結末に電池を使って到達するだけである。

        待つあいだもループは回し続ける。安全層・スティックの送信・即時安全則は
        いずれもループに依存しており、区間の境目は飛行の監視をやめる理由に
        ならない。
        """
        started = len(self.pilot.decisions)
        period = 1.0 / self.cfg.monitor_hz
        deadline = time.monotonic() + self.cfg.arbiter.hover_to_land_s
        while time.monotonic() < deadline:
            self._step_pilot()
            fresh = self._judgement_after(started)
            if fresh is not None:
                return fresh
            if self.pilot.executor.landing:
                return None
            time.sleep(period)
        return None

    def _judgement_after(self, index: int):
        """The first answer recorded after `index`, or None so far.
        `index` 以降に記録された最初の答え。まだ無ければ None。"""
        for row in self.pilot.decisions[index:]:
            if row["judgement"] is not None:
                return row["judgement"]
        return None

    def _allow(self, choice: str) -> tuple:
        """Apply the code's limits to Jev's choice. Returns (action, reason).

        Each rule replaces the choice rather than vetoing it outright,
        because refusing without substituting would leave the aircraft with
        no action at all. What replaces it is always more conservative than
        what was asked for -- never the other way round.

        Jev の選択にコードの上限を当て、(行動, 理由) を返す。

        各規則は選択を拒否するのではなく置き換える。置き換えずに拒否すれば、機体に
        取るべき行動が無くなるからである。置き換え先は必ず、求められたものより
        保守的な側である。その逆は無い。
        """
        # A hold is resolved into a concrete action FIRST, so that the limits
        # below see what the aircraft would actually do. Holding is the
        # safety layer's business, not the mission's -- the Arbiter is
        # already hovering the craft and its own timer turns a hold that
        # will not end into a landing -- so here it means "do not move the
        # route on past a leg that did not arrive".
        #
        # Resolving it first is what the measurement forced: with the limits
        # keyed on Jev's raw choice instead, a `hold` after a failed leg
        # became a retry that the retry counter never recognised as one, and
        # the `drift` scene redid its first leg 63 times.
        #
        # 待機は先に具体的な行動へ解決する。下の上限が「機体が実際に何をするか」を
        # 見られるようにするためである。待機は安全層の担当であってミッションの担当
        # ではない（Arbiter が既に機体を待機させ、その計時が終わらない待機を着陸に
        # 変える）ので、ここでの意味は「到達しなかった区間を飛ばして経路を進めない」
        # ことである。
        #
        # 先に解決するのは実測がそうさせた。上限を Jev の生の選択で判定していた
        # 版では、失敗した区間の後の `hold` が、やり直しの計数に数えられないやり直し
        # になり、`drift` の場面が最初の区間を 63 回やり直した。
        proposed = choice
        if proposed == MOVE_HOLD:
            has_arrived = self.last_arrival == ARRIVAL_AS_PLANNED
            proposed = MOVE_NEXT if has_arrived else MOVE_REDO

        is_out_of_retries = (proposed == MOVE_REDO
                             and self.retries >= self.cfg.mission.max_retries_per_leg)
        if is_out_of_retries:
            return MOVE_SKIP, (
                f"やり直しの上限 {self.cfg.mission.max_retries_per_leg} 回に達したため"
                f"この区間を飛ばす"
            )

        goes_on = proposed in (MOVE_NEXT, MOVE_REDO, MOVE_SKIP)
        if goes_on and self._battery_is_low():
            return MOVE_RETURN, "電池が残り少ないため、先へ進まず離陸点へ戻る"

        return proposed, ""

    def _battery_is_low(self) -> bool:
        """Whether the battery has reached the band that ends a mission.

        The Monitor's own classification is used, not a fresh comparison:
        one place decides what "running low" means, and the mission obeys
        the same threshold the state shown to Jev was built from.

        電池が、ミッションを終わらせる区分に達したか。

        新たに比較せず Monitor 自身の区分を使う。「残り少ない」の意味を決める
        場所を 1 つにし、Jev に見せた state の元になったのと同じしきい値に
        ミッションも従うためである。
        """
        assessment = self.pilot.monitor.update([])
        return assessment.battery_level in ("残り少ない", "危険")

    # -- ending / 終わり方 -----------------------------------------------

    def _finish_early(self, action: str, reason: str) -> bool:
        """Fly home or land, as asked, and end the mission. Always False.
        求められたとおり帰還または着陸し、ミッションを終える。常に False。"""
        if action == MOVE_LAND:
            self._stop(STOP_LANDED, reason)
            return False
        self._fly_home()
        self._stop(STOP_RETURNED, reason)
        return False

    def _fly_home(self) -> None:
        """Fly the `go` back to the takeoff point, watched as a leg is.
        離陸点へ戻る `go` を、区間と同じように見張りながら飛ぶ。"""
        from .say import StepRunner

        if self.walk.is_already_home(self.cfg):
            return
        step = self.walk.home_step()
        runner = StepRunner(self.link, [step], self.cfg,
                            speed_probe=self.pilot.horizontal_speed)
        runner.start()
        interrupt = self._watch(runner)
        if not interrupt:
            self.walk.apply(step)

    def _stop(self, reason: str, detail: str) -> None:
        self.outcome.stop_reason = reason
        self.outcome.stop_detail = detail
        self._emit(f"mission ends: {reason} — {detail}")

    def _land_if_still_flying(self) -> None:
        """Land through the Executor, so the craft settles first.

        Never `link.send_command("land")`: the firmware stops holding
        horizontal position for the whole descent (landing.py), so a
        landing that skipped the settling would slide across the floor.
        Routing every landing through the Executor is what makes that
        impossible to forget.

        Executor を通して着陸する。機体が先に静定するようにするためである。

        `link.send_command("land")` は使わない。ファームは降下のあいだ水平の
        位置保持をやめる（landing.py）ので、静定を省いた着陸は床の上を滑る。
        全ての着陸を Executor へ通すことが、それを忘れられなくする。
        """
        from .arbiter import VERDICT_LAND, Verdict

        if not self.pilot.executor.landing:
            self.pilot.executor.apply(Verdict(
                action=VERDICT_LAND,
                reason=self.outcome.stop_detail or "ミッションを完了したため着陸",
                source="mission",
            ))
        self._pump_landing()
        self.outcome.landed = True

    def _pump_landing(self) -> None:
        """Keep the loop turning until the landing has gone out.
        着陸が送られるまでループを回し続ける。"""
        period = 1.0 / self.cfg.monitor_hz
        ceiling = (self.cfg.landing.settle_max_s + self.cfg.landing.move_reply_wait_s)
        deadline = time.monotonic() + ceiling
        while self.pilot.executor.approach is not None and time.monotonic() < deadline:
            self._step_pilot()
            time.sleep(period)
        time.sleep(self.cfg.sils.land_grace_s)

    # -- recording / 記録 -------------------------------------------------

    def _record(self, leg: Leg, outcome: str, arrival: str, choice: str = "",
                confidence: float = 0.0, refusal: str = "",
                elapsed_s: float = None) -> None:
        self.outcome.results.append(LegResult(
            index=self.index + 1,
            label=leg.describe(),
            outcome=outcome,
            arrival=arrival,
            attempt=self.retries + 1,
            move_choice=choice,
            move_confidence=confidence,
            refusal=refusal,
            elapsed_s=(getattr(self, "last_leg_elapsed", 0.0)
                       if elapsed_s is None else elapsed_s),
        ))

    def _announce(self, leg: Leg, allowed: str, refusal: str) -> None:
        detail = f" [{refusal}]" if refusal else ""
        self._emit(
            f"leg {self.index + 1}/{self.mission.total} {leg.describe()}: "
            f"{self.last_arrival or '—'} -> {allowed}{detail}"
        )

    def _emit(self, message: str) -> None:
        if self.on_event is not None:
            self.on_event(message)
