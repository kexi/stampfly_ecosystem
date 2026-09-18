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

    def __init__(self, path=None, directory=None):
        self.path = Path(path) if path else _default_path(directory)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")
        self._counter = 0
        self._started = time.monotonic()

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
        return row

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
