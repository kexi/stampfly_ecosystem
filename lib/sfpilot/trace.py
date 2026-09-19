"""
sfpilot.trace - one decision, one line of JSON.
sfpilot.trace - 1 判断 1 行の JSON。

Format: JSON Lines at `logs/pilot/<datetime>.jsonl`, one object per
decision, written and flushed immediately. The point is that a flight can
be reconstructed afterwards with `grep` and `jq` alone -- if a landing
happened, one line says which situation, which answer and which rule
produced it.

形式: `logs/pilot/<日時>.jsonl` の JSON Lines。1 判断 1 オブジェクトを
その都度書き出して flush する。目的は、飛行後に `grep` と `jq` だけで
経過をたどれること — 着陸したなら、どの状況・どの答え・どの規則がそれを
生んだかが 1 行に収まっている。

What is never written: the API key, any environment variable, and the raw
`state` numerics beyond what the assessment already classified. A trace
file is something a user will attach to a bug report, so it must be safe
to share without redaction.

決して書かないもの: API キー、環境変数、評価が区分した以上の生の数値。
記録は利用者が不具合報告に添付するものなので、伏せ字なしで共有して
安全でなければならない。
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path


class Trace:
    """Append one JSON object per decision.
    1 判断につき JSON オブジェクトを 1 つ追記する。"""

    def __init__(self, path=None, directory=None, bus=None):
        self.path = Path(path) if path else _default_path(directory)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")
        self._counter = 0
        self._started = time.monotonic()
        # The live view branches from HERE, off the row that was just
        # written, rather than assembling its own from the same inputs.
        # Two assemblies would be two chances to disagree about what
        # happened, and the disagreement would show up only in the flight
        # someone was actually watching.
        # ライブ表示は**ここ**から、いま書いた行そのものを分岐させる。同じ入力
        # から別に組み立てはしない。組み立てが 2 つあれば、起きたことについて
        # 食い違う機会も 2 つになり、しかもその食い違いは、誰かが実際に見ている
        # 飛行でしか現れない。
        self.bus = bus

    def write(self, state: dict, judgement, verdict, command: str,
              question_ids=()) -> dict:
        """Record one decision and return the row that was written.
        1 判断を記録し、書き出した行を返す。"""
        self._counter += 1
        row = {
            "trace_id": f"{self._counter:06d}",
            "t_mono": round(time.monotonic() - self._started, 4),
            "state": state,
            "questions": list(question_ids),
            "answers": _answers_to_json(judgement),
            "latency_ms": round(judgement.latency_ms, 1) if judgement else None,
            "arbiter_verdict": {
                "action": verdict.action,
                "reason": verdict.reason,
                "source": verdict.source,
                "accepted_answer": verdict.accepted_answer,
            },
            "command": command,
        }
        # Flushing every line costs little at 1-4 decisions per second and
        # means a trace survives a crash or a Ctrl-C mid-flight -- which
        # is exactly the flight whose trace matters most.
        # 毎行 flush する。1〜4Hz では負担は小さく、飛行中の異常終了や Ctrl-C
        # でも記録が残る — その飛行の記録こそ最も必要なものである。
        self._file.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._file.flush()
        self._broadcast(row)
        return row

    def write_plan(self, plan, questions: dict) -> dict:
        """Record one instruction's translation, as one line.

        Everything needed to argue about a mistranslation afterwards is on
        this line: what was said, what was asked, what came back with what
        confidence, what the steps became, and -- if it was refused -- why.
        Without the questions, a trace showing a wrong step could not
        distinguish a bad answer from a badly worded question.

        指示 1 つの変換結果を 1 行で記録する。

        後から誤変換を検討するのに要るものをすべてこの行に載せる: 何を言われ、
        何を問い、どんな確信度で何が返り、手順がどうなり、拒否したならなぜか。
        質問が無ければ、誤った手順を示す記録から「答えが悪かった」のか
        「問い方が悪かった」のかを区別できない。
        """
        self._counter += 1
        row = {
            "trace_id": f"{self._counter:06d}",
            "t_mono": round(time.monotonic() - self._started, 4),
            "kind": "instruction",
            "operator_instruction": plan.instruction,
            "questions": {qid: body.get("instructions", "")
                          for qid, body in questions.items()},
            "answers": plan.answers,
            "spoken_numbers": [
                {"text": n.text, "value": n.value, "unit": n.unit}
                for n in plan.spoken_numbers
            ],
            "steps": [
                {"verb": s.verb, "amount": s.amount, "source": s.amount_source,
                 "confidence": round(s.confidence, 4), "command": s.command()}
                for s in plan.steps
            ],
            "refusal": plan.refusal,
            "latency_ms": round(plan.latency_ms, 1),
        }
        self._file.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._file.flush()
        self._broadcast(row)
        return row

    def write_recording(self, recording) -> dict:
        """Record where this flight's flight-log bundle landed, as one line.

        Written so the two records of one flight can be paired by reading
        either of them. They already share a datetime, but that pairing
        relies on the reader noticing that one spelling is lower-cased;
        a line naming the path leaves nothing to notice.

        この飛行のフライトログ一式の場所を 1 行で記録する。

        1 回の飛行についての 2 つの記録を、どちらからでも対応づけられるように
        する。両者は既に日時を共有しているが、その対応づけは「片方が小文字で
        ある」ことに読み手が気づくことに頼っている。パスを書いた行があれば、
        気づく必要が無くなる。
        """
        self._counter += 1
        row = {
            "trace_id": f"{self._counter:06d}",
            "t_mono": round(time.monotonic() - self._started, 4),
            "kind": "recording",
            "flight_log": str(recording.bundle_path) if recording.bundle_path else None,
            "decisions_csv": str(recording.bundle_dir / f"decisions_{recording.stamp}.csv")
                             if recording.bundle_path else None,
            "video_command": recording.video_command(),
        }
        self._file.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._file.flush()
        return row

    def _broadcast(self, row: dict) -> None:
        """Send the row just written to the live view, if one is listening.
        いま書いた行を、ライブ表示が聞いていれば送る。"""
        if self.bus is None:
            return
        from .events import EVENT_DECISION, decision_payload

        self.bus.publish(EVENT_DECISION, decision_payload(row))

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _default_path(directory=None) -> Path:
    """`logs/pilot/<datetime>.jsonl` under the repository root.
    リポジトリルート配下の `logs/pilot/<日時>.jsonl`。

    `logs/` is already ignored by .gitignore (`logs/*`), so traces stay
    out of the repository without a new rule.
    `logs/` は .gitignore 済み（`logs/*`）なので、新たな規則を足さずとも
    記録がリポジトリに入ることはない。
    """
    if directory is not None:
        base = Path(directory)
    else:
        base = _repository_root() / "logs" / "pilot"
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return base / f"{stamp}.jsonl"


def _repository_root() -> Path:
    """Walk up to the directory holding PROJECT_PLAN.md, else use cwd.
    PROJECT_PLAN.md を持つディレクトリまで遡る。見つからなければ現在地。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "PROJECT_PLAN.md").exists():
            return parent
    return Path(os.getcwd())


def _answers_to_json(judgement):
    """Answers with their probabilities, or the error that replaced them.
    確率つきの答え。答えが無ければ、その代わりのエラー。"""
    if judgement is None:
        return None
    if judgement.error is not None:
        return {"error": judgement.error}
    out = {}
    for qid, answer in judgement.answers.items():
        if answer.kind == "choice":
            out[qid] = {
                "choice": answer.choice,
                "confidence": round(answer.confidence, 4),
                "probabilities": {k: round(v, 4) for k, v in answer.probabilities.items()},
            }
            continue
        out[qid] = {"noul": round(answer.noul, 4) if answer.noul is not None else None}
    return out
