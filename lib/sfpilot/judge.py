"""
sfpilot.judge - ask Jev, in one request, and never wait for a retry.
sfpilot.judge - Jev へ 1 リクエストで問い、再試行は待たない。

Question definitions live in `QUESTIONS` below and nowhere else. Keeping
them in one table is what makes the set of actions the model may propose
reviewable: read the table, and you have read every action `sf pilot` can
take on Jev's advice.

質問の定義は下の `QUESTIONS` だけに置く。1 つの表にまとめることで、モデルが
提案しうる行動の一覧が点検可能になる — この表を読めば、Jev の助言によって
`sf pilot` が取りうる行動をすべて読んだことになる。

`emergency` appears in no question's criteria. Cutting the motors is a
crash; it is reachable only through monitor.py's immediate safety rules
(which cannot choose it either) or a human at the transmitter.

どの質問の選択肢にも `emergency` は無い。モータ停止は墜落であり、
monitor.py の即時安全則（そこでも選べない）か、送信機を持つ人からしか
到達できない。

Transport: the HTTP API is called directly (POST /v1/systemone) rather
than through `typesafe-sdk`. Why not the SDK: the pilot loop needs one
connection kept alive across judgements, a hard per-request deadline, and
retries disabled -- all three are plain httpx settings here, and the
optional dependency would otherwise have to be installed before any of
`sf pilot` runs. The request and response shapes follow
docs.typesafe.ai/api.md and are pinned by the tests.

通信: `typesafe-sdk` ではなく HTTP API（POST /v1/systemone）を直接呼ぶ。
SDK を使わない理由: 操縦ループは判断をまたいで接続を使い回すこと、
リクエストごとの厳格な期限、再試行の無効化の 3 点を必要とし、いずれも
httpx の素の設定で足りる。SDK に依存すると `sf pilot` 全体がその導入を
前提にしてしまう。リクエスト・レスポンスの形は docs.typesafe.ai/api.md に
従い、試験で固定する。
"""

import json
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from .config import JudgeConfig, DEFAULT_CONFIG
from .credentials import API_KEY_ENV, MissingApiKey, resolve_api_key
from .summarizer import assert_no_numbers

API_URL = "https://api.typesafe.ai/v1/systemone"

# Choice labels. Code compares against these constants, never against a
# literal string, so a renamed option cannot silently stop matching.
# 選択肢のラベル。コードはこの定数と比較し、文字列リテラルとは比較しない。
# 名前を変えたときに、静かに一致しなくなることを防ぐ。
ACT_CONTINUE = "continue"
ACT_HOLD = "hold"
ACT_LAND = "land"

MOVE_NEXT = "next_step"
MOVE_HOLD = "hold"
MOVE_REDO = "redo_step"
MOVE_SKIP = "skip_step"
MOVE_RETURN = "return_home"
MOVE_LAND = "land"

Q_SAFETY = "safety_action"
Q_ABNORMAL = "abnormal"
Q_NEXT_MOVE = "next_move"

# `sf pilot say`: the moves an instruction may be made of, and the named
# sizes a move may have. Code maps a size to centimetres or degrees
# (config.InstructionConfig) -- the model never handles a figure.
# `sf pilot say`: 指示を構成しうる動作と、動作が取りうる名前付きの量。
# 量から cm・度への対応はコードが持つ（config.InstructionConfig）。
# モデルは数値を一切扱わない。
STEP_TAKEOFF = "takeoff"
STEP_UP = "up"
STEP_DOWN = "down"
STEP_FORWARD = "forward"
STEP_BACK = "back"
STEP_LEFT = "left"
STEP_RIGHT = "right"
STEP_TURN_RIGHT = "turn_right"
STEP_TURN_LEFT = "turn_left"
STEP_RETURN_HOME = "return_home"
STEP_LAND = "land"
STEP_NONE = "none"

AMOUNT_SMALL = "small"
AMOUNT_MEDIUM = "medium"
AMOUNT_LARGE = "large"
AMOUNT_UNSPECIFIED = "unspecified"

# Question id shapes. The index is 1-based so `step_1_move` reads as "the
# first move" in a trace without mental arithmetic.
# 質問 ID の形。番号は 1 始まりにして、記録中の `step_1_move` が暗算なしに
# 「1 番目の動作」と読めるようにする。
Q_STEP_MOVE = "step_{}_move"
Q_STEP_AMOUNT = "step_{}_amount"

# The question set, exactly as the design's table defines it. `type` and
# `criteria` match the HTTP API's schema so this table is the request body.
# 設計の表どおりの質問一式。`type`・`criteria` は HTTP API のスキーマに
# 合わせてあり、この表がそのままリクエスト本体になる。
QUESTIONS = {
    Q_SAFETY: {
        "type": "choice",
        "instructions": (
            "You are supervising a small indoor quadcopter that is hovering under "
            "its own position-hold controller. Given the situation, what should the "
            "aircraft do right now?"
        ),
        "criteria": {
            ACT_CONTINUE: "The situation is normal; carry on with the current task.",
            ACT_HOLD: "Something is unclear or unsettled; stay still in place and wait.",
            ACT_LAND: "The situation is unsafe or getting worse; land now.",
        },
    },
    Q_ABNORMAL: {
        "type": "noul",
        "instructions": "Is this flight behaving abnormally?",
        "criteria": {
            "true": "The flight shows a problem: unexpected motion, a failing sensor, "
                    "or a condition that is getting worse.",
            "false": "The flight is behaving as a healthy hover or manoeuvre should.",
        },
    },
    Q_NEXT_MOVE: {
        "type": "choice",
        "instructions": (
            "The aircraft is flying a planned route, one step at a time. Given the "
            "situation and the current step, what should it do next?"
        ),
        "criteria": {
            MOVE_NEXT: "The current step is done and conditions are good; go to the next step.",
            MOVE_HOLD: "Stay still in place and wait before deciding.",
            MOVE_REDO: "The current step did not achieve what it should; do it again.",
            MOVE_SKIP: "The current step cannot be completed; leave it out and move on.",
            MOVE_RETURN: "Stop the route and fly back to the point it took off from.",
            MOVE_LAND: "Stop the route and land now.",
        },
    },
}

# Which questions to ask when there is no mission running. Asking
# `next_move` without a route would make the model answer about a step
# that does not exist -- irrelevant state, which lowers accuracy.
# ミッションが無いときに問う質問。経路が無いのに `next_move` を問うと、
# 存在しない区間について答えさせることになる — 無関係な state は精度を下げる。
SAFETY_ONLY = (Q_SAFETY, Q_ABNORMAL)
WITH_MISSION = (Q_SAFETY, Q_ABNORMAL, Q_NEXT_MOVE)


# =============================================================================
# `sf pilot say`: one instruction -> a sequence of typed moves
# `sf pilot say`: 1 つの指示 → 型のついた動作の列
# =============================================================================
#
# Every question below is asked in ONE request and none of them can see the
# others' answers (docs.typesafe.ai/patterns/fan-out). So each one restates
# which position in the instruction it is asking about, in full: "the Nth
# action". Writing "the next action" would be a reference to an answer this
# question cannot read.
#
# 以下の質問はすべて 1 リクエストで問われ、互いの答えは見られない
# （docs.typesafe.ai/patterns/fan-out）。そのため各質問は、自分が指示の
# 何番目について問うているかを毎回完結して書く（「N 番目の動作」）。
# 「次の動作」と書けば、この質問が読めない答えへの参照になってしまう。
#
# The criteria describe what each option MEANS rather than restating its
# name, as the Choice guidance asks (docs.typesafe.ai/primitives/choice):
# the match is on meaning, so "forward" is described as the direction the
# nose points, not as the word "forward".
#
# 選択肢の説明は、名前の言い換えではなく「その選択肢が何を意味するか」を
# 書く（docs.typesafe.ai/primitives/choice の指針）。一致するのは意味で
# あるため、`forward` は「forward という語」ではなく「機首が向いている方向」
# として説明する。

_MOVE_CRITERIA = {
    STEP_TAKEOFF: "Leave the ground and climb to a low hover. Only ever the "
                  "first action of an instruction.",
    STEP_UP: "Climb straight up, staying over the same spot on the floor.",
    STEP_DOWN: "Descend straight down, staying over the same spot on the "
               "floor, without touching down.",
    STEP_FORWARD: "Travel horizontally in the direction the nose is pointing.",
    # `back` and `return_home` are the pair the model confuses, because in
    # Japanese "戻る" (go back / return) reads as either one. They are told
    # apart here by CONTRAST and by example, as the Choice guidance asks:
    # `back` is about a DIRECTION of travel and ends somewhere new, while
    # `return_home` is about a DESTINATION and undoes the whole flight so
    # far. Measured 2026-09-19: "一メートル前に進んで戻ってきて" was read as
    # `back`, flying `back 50` instead of returning to the takeoff point.
    # `back` と `return_home` はモデルが取り違える組である。日本語の「戻る」が
    # どちらにも読めるためである。Choice の指針に従い、**対比**と**例**で
    # 区別する: `back` は進む**向き**の話で、終点は新しい場所である。
    # `return_home` は**行き先**の話で、それまでの飛行を帳消しにする。
    # 2026-09-19 実測:「一メートル前に進んで戻ってきて」が `back` と読まれ、
    # 離陸点へ戻る代わりに `back 50` を飛んだ。
    STEP_BACK: "Travel horizontally away from the direction the nose is "
               "pointing, without turning around first, ending up somewhere "
               "it has not been. This is reversing or backing away by some "
               "amount, as in 'back up a little' or 'move backwards'. It is "
               "NOT going back to where the flight started.",
    STEP_LEFT: "Travel horizontally sideways to the left, still facing the "
               "same way.",
    STEP_RIGHT: "Travel horizontally sideways to the right, still facing the "
                "same way.",
    STEP_TURN_RIGHT: "Rotate clockwise on the spot to face a new direction, "
                     "without travelling anywhere.",
    STEP_TURN_LEFT: "Rotate anticlockwise on the spot to face a new "
                    "direction, without travelling anywhere.",
    STEP_RETURN_HOME: "Fly back to the point it took off from, whatever "
                      "route it has taken since, cancelling the travel of "
                      "the whole flight rather than moving by some amount. "
                      "This is coming back, returning, or going home, as in "
                      "'come back here' or 'return to where you started'. "
                      "Choose this whenever the instruction says to come "
                      "back, without naming a direction or a distance.",
    STEP_LAND: "Descend and touch down, ending the flight.",
    STEP_NONE: "There is no such action: the instruction has fewer actions "
               "than this, or it does not ask the aircraft to fly at all.",
}

_AMOUNT_CRITERIA = {
    AMOUNT_SMALL: "A little: a short hop of roughly an arm's length, or a "
                  "slight turn well short of a quarter circle.",
    AMOUNT_MEDIUM: "A moderate, ordinary amount: about half a room's width, "
                   "or a quarter-circle turn.",
    AMOUNT_LARGE: "A lot: several paces across the room, or a turn of a half "
                  "circle to face the opposite way.",
    AMOUNT_UNSPECIFIED: "The instruction does not say how far or how much for "
                        "this action, or there is no such action at all.",
}


def step_move_question(index: int, total: int) -> dict:
    """The Choice asking what the `index`-th action of the instruction is.
    指示の `index` 番目の動作は何かを問う Choice。"""
    return {
        "type": "choice",
        "instructions": (
            f"An operator gave a small indoor drone a spoken instruction, which "
            f"is in the state as `operator_instruction`. Read it as a list of "
            f"actions to perform in order, and consider action number {index} of "
            f"that list, counting from 1. (The instruction may contain fewer "
            f"than {total} actions.) What is action number {index}?"
        ),
        "criteria": dict(_MOVE_CRITERIA),
    }


def step_amount_question(index: int, total: int) -> dict:
    """The Choice asking how far the `index`-th action goes.
    `index` 番目の動作がどれだけ動くかを問う Choice。"""
    return {
        "type": "choice",
        "instructions": (
            f"An operator gave a small indoor drone a spoken instruction, which "
            f"is in the state as `operator_instruction`. Read it as a list of "
            f"actions to perform in order, and consider action number {index} of "
            f"that list, counting from 1. (The instruction may contain fewer "
            f"than {total} actions.) How big is action number {index} — how far "
            f"does it travel, or how far around does it turn?"
        ),
        "criteria": dict(_AMOUNT_CRITERIA),
    }


def step_questions(total: int) -> dict:
    """The whole fan-out for one instruction: 2 questions per step.

    All of them go in one request. Response time barely changes with the
    number of questions (docs.typesafe.ai/primitives/choice), whereas
    asking step by step would multiply the round trips by `total`.

    1 つの指示に対する fan-out 一式: 1 手順につき 2 問。

    すべて 1 リクエストに載せる。質問数が増えても応答時間はほとんど変わらない
    （docs.typesafe.ai/primitives/choice）一方、手順ごとに問えば往復が
    `total` 倍になる。
    """
    questions = {}
    for index in range(1, total + 1):
        questions[Q_STEP_MOVE.format(index)] = step_move_question(index, total)
        questions[Q_STEP_AMOUNT.format(index)] = step_amount_question(index, total)
    return questions


@dataclass
class Answer:
    """One question's answer, in the shape the Arbiter reasons about.
    1 問の答え。Arbiter が扱う形に整えたもの。"""

    kind: str                       # "choice" | "noul"
    choice: Optional[str] = None
    confidence: float = 0.0
    probabilities: dict = field(default_factory=dict)
    noul: Optional[float] = None


@dataclass
class Judgement:
    """Everything one round trip produced, including how it failed.
    1 往復の結果一式。失敗した場合はその理由も含む。"""

    answers: dict = field(default_factory=dict)
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    error: Optional[str] = None     # set means: no usable answers / 値があれば使える答えは無い

    @property
    def ok(self) -> bool:
        return self.error is None


class Judge(Protocol):
    """What the pilot loop needs from a judging backend.
    操縦ループが判断層に求めるもの。"""

    def ask(self, state: dict, question_ids) -> Judgement:
        """Ask every question in one round trip and return the result.
        Must return within the configured deadline, and must not raise:
        a failed call is a Judgement with `error` set, because an
        exception escaping here would stop the 50Hz monitor loop.
        全質問を 1 往復で問い、結果を返す。設定した期限内に返ること。
        例外を投げてはならない — 失敗は `error` を設定した Judgement で表す。
        ここから例外が漏れると 50Hz の監視ループが止まってしまうため。"""
        ...


class JevJudge:
    """Ask the real Jev model over HTTPS, on one kept-alive connection.
    実際の Jev モデルへ HTTPS で問う。接続は 1 本を使い回す。

    Why keep-alive: a fresh TLS handshake costs roughly as much as the
    judgement itself (measured: about 0.51 s for a cold call against
    about 0.27 s warm), which alone would exceed the 500 ms deadline.
    keep-alive にする理由: TLS の確立は判断そのものと同程度の時間を要する
    （実測: 初回約 0.51 秒に対し 2 回目以降約 0.27 秒）。接続を毎回張り直すと
    それだけで 500ms の期限を超えてしまう。

    Why no retries: a retried answer arrives against a situation that has
    already changed, and the Arbiter would discard it as stale. The next
    cycle re-asks anyway.
    再試行しない理由: 再試行の答えが届く頃には状況が変わっており、Arbiter が
    鮮度切れとして破棄する。どのみち次の周期で問い直される。
    """

    def __init__(self, config=DEFAULT_CONFIG, api_key: str = None):
        self.cfg: JudgeConfig = config.judge
        # One resolution path for every entry point, so there is one place
        # where a key is read and one place to audit. See credentials.py.
        # どの入口も取得経路は 1 本にする。キーを読む場所も点検する場所も 1 つ
        # で済む。credentials.py 参照。
        self._api_key = api_key or resolve_api_key(config)
        self._client = _open_client(self.cfg.deadline_s)

    def ask(self, state: dict, question_ids=SAFETY_ONLY) -> Judgement:
        """Ask `question_ids` about `state`; never raises.
        `state` について `question_ids` を問う。例外は投げない。"""
        return self.ask_questions(
            state, {qid: QUESTIONS[qid] for qid in question_ids}
        )

    def ask_questions(self, state: dict, questions: dict) -> Judgement:
        """Ask a question set given by body rather than by id; never raises.

        `sf pilot say` builds its questions per instruction (one pair per
        step), so they cannot come from the fixed `QUESTIONS` table. The
        transport, the deadline and the failure handling are identical --
        only where the question text comes from differs.

        ID ではなく本体で与えられた質問一式を問う。例外は投げない。

        `sf pilot say` は指示ごとに質問を組み立てる（手順 1 つにつき 1 組）ため、
        固定表 `QUESTIONS` からは取れない。通信・期限・失敗の扱いは同一で、
        違うのは質問文の出どころだけである。
        """
        assert_no_numbers(state)
        body = {
            "state": state,
            "model": self.cfg.model,
            "questions": questions,
        }
        started = time.monotonic()
        try:
            payload = self._post(body)
        except Exception as exc:                      # noqa: BLE001 - see docstring
            # Every failure mode (timeout, 429/529, connection reset, bad
            # JSON) reaches the Arbiter the same way -- as "no answer",
            # which it already turns into hovering. Distinguishing them
            # here would not change what the aircraft does.
            # どの失敗（期限超過・429/529・接続断・不正 JSON）も Arbiter には
            # 同じ「答え無し」として届き、Arbiter はそれを待機に変える。
            # ここで区別しても機体の動きは変わらない。
            return Judgement(
                latency_ms=(time.monotonic() - started) * 1e3,
                error=f"{type(exc).__name__}: {exc}",
            )
        latency_ms = (time.monotonic() - started) * 1e3
        usage = payload.get("usage") or {}
        return Judgement(
            answers=_parse_answers(payload.get("answers") or {}),
            latency_ms=latency_ms,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            model=str(payload.get("model", "")),
        )

    def _post(self, body: dict) -> dict:
        """One POST against the deadline. Raises on any failure.
        期限つきの POST を 1 回。失敗時は例外。"""
        response = self._client.post(
            API_URL,
            json=body,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self.cfg.deadline_s,
        )
        if response.status_code != 200:
            raise ApiError(f"HTTP {response.status_code}")
        return response.json()

    def close(self) -> None:
        self._client.close()


class ApiError(RuntimeError):
    """The API answered with something other than 200.
    API が 200 以外を返した。"""


def _open_client(deadline_s: float):
    """Open the kept-alive HTTP client, preferring httpx.

    httpx is not a declared dependency of the base install, so fall back
    to a small urllib-based client. The fallback opens a connection per
    call, which is why the optional `pilot` extra installs httpx.
    keep-alive の HTTP クライアントを開く。httpx があればそれを使う。

    httpx は基本インストールの依存ではないため、無ければ urllib による
    小さな代替を使う。代替は呼び出しごとに接続を張るので、任意依存
    `pilot` で httpx を入れる形にしてある。
    """
    try:
        import httpx
    except ImportError:
        return _UrllibClient()
    return httpx.Client(timeout=deadline_s, http2=False)


class _UrllibClient:
    """Minimal stand-in for httpx.Client, standard library only.
    httpx.Client の最小限の代替。標準ライブラリのみ。"""

    def post(self, url: str, json=None, headers=None, timeout=None):
        import urllib.error
        import urllib.request

        data = json_dumps(json).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST")
        request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return _UrllibResponse(response.status, response.read())
        except urllib.error.HTTPError as exc:
            return _UrllibResponse(exc.code, exc.read())

    def close(self) -> None:
        pass


@dataclass
class _UrllibResponse:
    status_code: int
    _body: bytes

    def json(self) -> dict:
        return json.loads(self._body.decode("utf-8"))


def json_dumps(obj) -> str:
    """Compact JSON. Non-ASCII is kept as-is so a Japanese instruction
    costs the tokens it actually costs, not six per character.
    詰めた JSON。非 ASCII はそのまま出す。日本語の指示文が 1 文字 6 文字分の
    トークンにならないようにするため。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _parse_answers(raw: dict) -> dict:
    """Response `answers` -> {question_id: Answer}.

    Shape confirmed against a live response:
      {"type":"choice","choice":"land","confidence":0.99,
       "probabilities":{"hold":0.0,"land":1.0,"continue":0.0}}
    レスポンスの `answers` を {質問 ID: Answer} に変換する。形は実応答で確認済み。
    """
    answers = {}
    for qid, value in raw.items():
        kind = value.get("type")
        if kind == "choice":
            answers[qid] = Answer(
                kind="choice",
                choice=value.get("choice"),
                confidence=float(value.get("confidence", 0.0)),
                probabilities=dict(value.get("probabilities") or {}),
            )
            continue
        if kind == "noul":
            answers[qid] = Answer(kind="noul", noul=float(value.get("noul", 0.0)))
    return answers


class FakeJudge:
    """A deterministic Judge for tests and `--fake` replay.

    Behaviour is declared up front rather than computed: the caller
    supplies the answers, the latency and the exception to raise. Tests
    that check "a late answer becomes hovering" must be able to produce a
    late answer without waiting for a real network.
    試験と `--fake` 再生のための決定的な Judge。

    挙動は計算せず、あらかじめ宣言する: 応答・遅延・投げる例外を呼び出し側が
    指定する。「遅れた答えは待機になる」という試験は、実際の通信を待たずに
    遅れた答えを作れなければならない。
    """

    def __init__(self, answers: dict = None, latency_ms: float = 10.0,
                 error: str = None, raises: Exception = None, script: list = None):
        self.answers = answers if answers is not None else default_fake_answers()
        self.latency_ms = latency_ms
        self.error = error
        self.raises = raises
        # `script` lets one test walk a judge through a sequence of
        # answers; when it runs out, the last entry repeats.
        # `script` は 1 つの試験で応答を順に変えるためのもの。使い切ったら
        # 最後の項目を繰り返す。
        self.script = list(script) if script else None
        self.calls: list = []

    def ask_questions(self, state: dict, questions: dict) -> Judgement:
        """Answer a question set given by body, as JevJudge.ask_questions does.

        The ids are what the answers are keyed by, so a fake set up with
        `step_1_move` answers serves an instruction translation without
        knowing anything about instructions.

        JevJudge.ask_questions と同じく、本体で与えられた質問一式に答える。

        答えの対応付けに使うのは ID なので、`step_1_move` の答えを持たせた
        FakeJudge は、指示について何も知らないまま指示の変換に使える。
        """
        return self.ask(state, tuple(questions))

    def ask(self, state: dict, question_ids=SAFETY_ONLY) -> Judgement:
        assert_no_numbers(state)
        self.calls.append({"state": state, "questions": list(question_ids)})
        if self.raises is not None:
            # Raising is what a broken backend does; the pilot loop is
            # required to survive it, so tests must be able to cause it.
            # 壊れた backend は例外を投げる。操縦ループはそれに耐える必要が
            # あるため、試験から起こせるようにしてある。
            raise self.raises
        if self.error is not None:
            return Judgement(latency_ms=self.latency_ms, error=self.error)
        answers = self._next_answers()
        return Judgement(
            answers={qid: answers[qid] for qid in question_ids if qid in answers},
            latency_ms=self.latency_ms,
            input_tokens=len(json_dumps(state)) // 4,
            output_tokens=len(question_ids) * 2,
            model="fake",
        )

    def _next_answers(self) -> dict:
        if not self.script:
            return self.answers
        entry = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return entry

    def close(self) -> None:
        pass


class MissionFakeJudge(FakeJudge):
    """A rule-based stand-in for Jev's mission judgement, keyless.

    The rules are the obvious reading of the state, written out so that a
    SILS rehearsal exercises the whole mission path -- the leg boundaries,
    the code's limits, the summary -- without a key or a network. They are
    not a model of Jev and are not claimed to be: what a rehearsal under
    this judge shows is that the PLUMBING is right, never that Jev would
    have chosen the same.

    Jev のミッション判断の代役。規則で書いてあり、キーは要らない。

    規則は state の素直な読み方を書き下したもので、SILS の予行がミッションの
    経路全体（区間の境目・コードの上限・集計）をキーも通信も無しで動かせるように
    するためのものである。Jev のモデルではないし、そう主張もしない。この judge で
    の予行が示すのは**配線**が正しいことであって、Jev が同じものを選ぶことでは
    決してない。
    """

    def ask(self, state: dict, question_ids=SAFETY_ONLY) -> Judgement:
        judgement = super().ask(state, question_ids)
        wants_next_move = Q_NEXT_MOVE in question_ids and judgement.error is None
        if wants_next_move:
            judgement.answers[Q_NEXT_MOVE] = _rule_based_next_move(state)
        return judgement


def _rule_based_next_move(state: dict) -> Answer:
    """Pick a move from the state's words, in the order that matters.

    Ordered by how much each condition overrides the others: a battery that
    is running low ends the route wherever it is, a leg that did not arrive
    is worth another attempt, and anything else carries on. The code's own
    limits still apply afterwards -- this answer is a proposal like any
    other, and `_MissionFlight._allow` may replace it.

    state の語から次の一手を選ぶ。順序には意味がある。

    どれがどれを上書きするかの順に並べてある: 電池が残り少なければ、どこにいても
    経路を終える。到達しなかった区間はもう一度試す価値がある。それ以外は進む。
    この後もコード自身の上限は効く — この答えも他と同じ提案であり、
    `_MissionFlight._allow` が置き換えうる。
    """
    battery = (state.get("battery") or {}).get("level", "")
    is_battery_low = battery in ("running low", "dangerously low")
    if is_battery_low:
        return _confident_move(MOVE_RETURN)

    arrival = (state.get("mission") or {}).get("leg_arrival", "")
    did_not_arrive = arrival in ("stopped short", "overshot")
    if did_not_arrive:
        return _confident_move(MOVE_REDO)

    drift = (state.get("flight") or {}).get("horizontal_drift", "")
    is_drifting_fast = drift.startswith("drifting fast")
    if is_drifting_fast:
        return _confident_move(MOVE_HOLD)

    return _confident_move(MOVE_NEXT)


def _confident_move(choice: str) -> Answer:
    """A `next_move` answer the Arbiter's confidence gate will accept.
    Arbiter の確信度の関門を通る `next_move` の答え。"""
    return Answer(kind="choice", choice=choice, confidence=0.9,
                  probabilities={choice: 0.9})


def default_fake_answers() -> dict:
    """A confident "carry on" for every question. / 全問「継続」で高確信。"""
    return {
        Q_SAFETY: Answer(
            kind="choice", choice=ACT_CONTINUE, confidence=0.95,
            probabilities={ACT_CONTINUE: 0.95, ACT_HOLD: 0.04, ACT_LAND: 0.01},
        ),
        Q_ABNORMAL: Answer(kind="noul", noul=0.05),
        Q_NEXT_MOVE: Answer(
            kind="choice", choice=MOVE_NEXT, confidence=0.9,
            probabilities={MOVE_NEXT: 0.9, MOVE_HOLD: 0.1},
        ),
    }
