"""
test_judge.py - the model can never propose cutting the motors, the
question set is the one the design fixed, and FakeJudge is deterministic.
test_judge.py - モデルがモータ停止を提案できないこと、質問一式が設計どおり
であること、FakeJudge が決定的であること。
"""

import pytest

from sfpilot import credentials
from sfpilot import judge as judge_module
from sfpilot.judge import (
    ACT_CONTINUE,
    ACT_HOLD,
    ACT_LAND,
    FakeJudge,
    MOVE_LAND,
    MOVE_NEXT,
    MOVE_REDO,
    MOVE_RETURN,
    MOVE_SKIP,
    QUESTIONS,
    Q_ABNORMAL,
    Q_NEXT_MOVE,
    Q_SAFETY,
    SAFETY_ONLY,
    WITH_MISSION,
)

STATE = {"flight": {"altitude": "on target"}}


def test_emergency_is_not_offered_to_the_model_anywhere():
    """No question lets Jev choose to cut the motors. Cutting the motors
    in flight is a crash, so it is reachable only by a human.
    どの質問でも Jev がモータ停止を選べないこと。飛行中のモータ停止は墜落
    であり、人からしか到達できない。"""
    for question in QUESTIONS.values():
        criteria = question.get("criteria") or {}
        assert "emergency" not in criteria
        for label in criteria:
            assert "emergency" not in str(label).lower()


def test_emergency_never_appears_in_a_judgement():
    """Even a FakeJudge run cannot yield an emergency through this path.
    FakeJudge を通しても、この経路から emergency は出てこないこと。"""
    judgement = FakeJudge().ask(STATE, WITH_MISSION)
    choices = [a.choice for a in judgement.answers.values() if a.kind == "choice"]
    assert "emergency" not in choices


def test_emergency_is_absent_from_the_whole_module():
    """The word does not appear as an action constant in judge.py at all.
    judge.py に emergency という行動定数が存在しないこと。"""
    action_names = [n for n in dir(judge_module) if n.startswith(("ACT_", "MOVE_"))]
    values = [getattr(judge_module, n) for n in action_names]
    assert "emergency" not in values


def test_the_question_set_matches_the_design():
    """safety_action, abnormal and next_move exist with the design's options.
    safety_action・abnormal・next_move が設計どおりの選択肢で存在すること。"""
    assert set(QUESTIONS) == {Q_SAFETY, Q_ABNORMAL, Q_NEXT_MOVE}
    assert set(QUESTIONS[Q_SAFETY]["criteria"]) == {ACT_CONTINUE, ACT_HOLD, ACT_LAND}
    assert set(QUESTIONS[Q_NEXT_MOVE]["criteria"]) == {
        MOVE_NEXT, ACT_HOLD, MOVE_REDO, MOVE_SKIP, MOVE_RETURN, MOVE_LAND,
    }
    assert QUESTIONS[Q_ABNORMAL]["type"] == "noul"


def test_next_move_is_only_asked_when_a_mission_is_running():
    """Without a route, the route question is not asked -- an irrelevant
    question would lower the accuracy of the ones that matter.
    経路が無ければ経路の質問はしないこと。無関係な質問は、必要な質問の
    精度を下げるため。"""
    assert Q_NEXT_MOVE not in SAFETY_ONLY
    assert Q_NEXT_MOVE in WITH_MISSION


def test_fake_judge_is_deterministic_and_records_its_calls():
    """The same input yields the same answer, and the calls are inspectable.
    同じ入力に同じ答えを返し、呼び出しを検査できること。"""
    fake = FakeJudge()
    first = fake.ask(STATE, SAFETY_ONLY)
    second = fake.ask(STATE, SAFETY_ONLY)
    assert first.answers[Q_SAFETY].choice == second.answers[Q_SAFETY].choice
    assert len(fake.calls) == 2
    assert fake.calls[0]["questions"] == list(SAFETY_ONLY)


def test_fake_judge_can_be_told_to_fail():
    """A configured error comes back as a Judgement, not an exception.
    指定したエラーは例外ではなく Judgement として返ること。"""
    judgement = FakeJudge(error="HTTP 429").ask(STATE, SAFETY_ONLY)
    assert not judgement.ok
    assert judgement.error == "HTTP 429"


def test_fake_judge_can_be_told_to_raise():
    """A configured exception is actually raised, so the loop's handling
    of a broken backend can be tested.
    指定した例外が実際に送出されること。壊れた backend への対処を試験できる
    ようにするため。"""
    with pytest.raises(RuntimeError):
        FakeJudge(raises=RuntimeError("boom")).ask(STATE, SAFETY_ONLY)


def test_the_judge_refuses_to_send_numbers():
    """A state carrying a number is rejected before it reaches the wire.
    数値を含む state は、送信される前に拒否されること。"""
    with pytest.raises(ValueError):
        FakeJudge().ask({"flight": {"altitude_m": 0.8}}, SAFETY_ONLY)


def test_answer_parsing_matches_the_live_response_shape():
    """The parser reads the shape the API actually returns (verified
    against a live Choice response).
    実際に API が返す形を解釈できること（実応答で確認した Choice の形）。"""
    raw = {
        "safety_action": {
            "type": "choice", "choice": "land", "confidence": 0.99,
            "probabilities": {"hold": 0.0, "land": 1.0, "continue": 0.0},
        },
        "abnormal": {"type": "noul", "noul": 0.87},
    }
    answers = judge_module._parse_answers(raw)
    assert answers["safety_action"].choice == "land"
    assert answers["safety_action"].confidence == pytest.approx(0.99)
    assert answers["safety_action"].probabilities["land"] == pytest.approx(1.0)
    assert answers["abnormal"].noul == pytest.approx(0.87)


def test_missing_api_key_is_a_clear_error(monkeypatch):
    """With no key in either source, JevJudge fails with a named error
    rather than a stack trace from deep inside an HTTP library.

    The keychain is stubbed out as well as the environment: on a developer
    machine that actually holds the key, leaving it in place would make
    this test assert the opposite of what it says.

    どちらの取得元にもキーが無いとき、JevJudge が HTTP ライブラリ内部の例外では
    なく、名前のついたエラーで失敗すること。

    環境変数だけでなくキーチェーンも差し替える。実際にキーを持つ開発機では、
    そのままにすると本試験が題目と逆のことを確かめてしまうためである。
    """
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(credentials, "read_keychain_key", lambda service: "")
    with pytest.raises(judge_module.MissingApiKey):
        judge_module.JevJudge()
