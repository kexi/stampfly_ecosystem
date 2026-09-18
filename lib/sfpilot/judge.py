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
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from .config import JudgeConfig, DEFAULT_CONFIG
from .summarizer import assert_no_numbers

API_URL = "https://api.typesafe.ai/v1/systemone"
API_KEY_ENV = "TYPESAFE_API_KEY"

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
        self._api_key = api_key or os.environ.get(API_KEY_ENV)
        if not self._api_key:
            raise MissingApiKey(
                f"environment variable {API_KEY_ENV} is not set "
                f"（環境変数 {API_KEY_ENV} が設定されていません）"
            )
        self._client = _open_client(self.cfg.deadline_s)

    def ask(self, state: dict, question_ids=SAFETY_ONLY) -> Judgement:
        """Ask `question_ids` about `state`; never raises.
        `state` について `question_ids` を問う。例外は投げない。"""
        assert_no_numbers(state)
        body = {
            "state": state,
            "model": self.cfg.model,
            "questions": {qid: QUESTIONS[qid] for qid in question_ids},
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


class MissingApiKey(RuntimeError):
    """The API key environment variable is absent.
    API キーの環境変数が無い。"""


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
