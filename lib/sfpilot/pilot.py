"""
sfpilot.pilot - the loop: Link -> Monitor -> Summarizer -> Judge -> Arbiter -> Executor.
sfpilot.pilot - ループ本体: Link → Monitor → Summarizer → Judge → Arbiter → Executor。

The loop runs at the monitor rate (50Hz). The Judge does not: it is asked
when the situation's classification changes, and at least once a second
otherwise. Its answer arrives on a worker thread, so a slow or hanging
request never delays the next sample, the next `rc`, or an immediate
safety rule.

ループは監視周期（50Hz）で回る。Judge はその周期では呼ばない — 状況の区分が
変わったとき、それ以外は少なくとも 1 秒に 1 回問う。答えは別スレッドで受け取る
ので、応答が遅い・返らないリクエストがあっても、次のサンプル・次の `rc`・
即時安全則が遅れることはない。

Why a thread and not asyncio: the Link implementations are blocking
socket readers with their own threads already (RealLink), and the loop
must keep 20ms timing while a request is outstanding. One worker thread
per outstanding question is simpler here than making the whole CLI async.

asyncio ではなくスレッドにする理由: Link 実装は既に自前のスレッドで
ブロッキング受信している（RealLink）し、リクエストが未完了の間もループは
20ms の刻みを保つ必要がある。CLI 全体を非同期にするより、未完了の質問 1 つに
つきスレッド 1 本のほうが単純である。
"""

import threading
import time
from dataclasses import dataclass
from typing import Optional

from .arbiter import Arbiter, VERDICT_LAND
from .config import DEFAULT_CONFIG
from .executor import Executor
from .judge import SAFETY_ONLY, WITH_MISSION, Judgement
from .monitor import Monitor
from .summarizer import signature, summarize


@dataclass
class _Pending:
    """A question that has been sent and not yet answered.
    送信済みで未回答の質問。"""

    signature: str
    question_ids: tuple
    started: float
    judgement: Optional[Judgement] = None
    done: bool = False


class Pilot:
    """Run the decision loop over any Link.
    任意の Link に対して判断ループを回す。"""

    def __init__(self, link, judge, config=DEFAULT_CONFIG, trace=None,
                 mission=None, operator_instruction=None):
        self.link = link
        self.judge = judge
        self.cfg = config
        self.trace = trace
        self.mission = mission
        self.operator_instruction = operator_instruction
        self.monitor = Monitor(config)
        self.arbiter = Arbiter(config)
        # The Executor's pre-landing settling needs the craft's measured
        # speed, and the Monitor is what holds it: the loop already folds
        # every sample in there, so reading the link again would take
        # samples away from the classification.
        # Executor の着陸前の静定には機体の実測速度が要り、それを持っているのは
        # Monitor である。ループが全サンプルをそこへ取り込んでいるので、リンクを
        # もう一度読めば、区分からサンプルを奪うことになる。
        self.executor = Executor(link, config, speed_probe=self.horizontal_speed)
        self.decisions: list = []
        self._pending: Optional[_Pending] = None
        self._last_asked = 0.0
        self._last_signature = ""
        self._lock = threading.Lock()

    @property
    def question_ids(self) -> tuple:
        return WITH_MISSION if self.mission else SAFETY_ONLY

    def horizontal_speed(self):
        """The craft's horizontal speed [m/s] from the latest sample, or None.

        Read from the Monitor's numerics, where the loop already keeps the
        figures it classified from. Callers that wait for the craft to stop
        need the NUMBER, not the classification: "drifting slowly" spans a
        range far too wide to settle on.

        最新サンプルから見た機体の水平速度 [m/s]。分からなければ None。

        Monitor の numeric から読む。ループが区分の元にした数値を既にそこへ置いて
        いるためである。機体が止まるのを待つ側に必要なのは区分ではなく**数値**で
        ある（「ゆっくり流されている」が表す幅は、静定の判定には広すぎる）。
        """
        sample = self.monitor.latest_sample
        if not sample:
            return None
        north, east = sample.get("vel_n"), sample.get("vel_e")
        if north is None or east is None:
            return None
        return (north * north + east * east) ** 0.5

    def step(self, now: float = None) -> Optional[dict]:
        """One turn of the loop. Returns the decision row if one was made.
        ループ 1 周。判断が成立した場合はその行を返す。"""
        now = time.monotonic() if now is None else now
        samples = self.link.read_samples()
        assessment = self.monitor.update(samples)
        state = summarize(assessment, self.mission, self.operator_instruction)
        current_signature = signature(state)

        # `_collect` returns the answer together with the signature that
        # was current when the question went out. They must travel as a
        # pair: the Arbiter's staleness rule compares exactly those two,
        # and reading the signature separately afterwards would read it
        # from the next pending question instead.
        # `_collect` は答えと「質問を出した時点の指紋」を組で返す。この 2 つは
        # 必ず一緒に扱う: Arbiter の鮮度判定はまさにこの 2 つを比べるもので、
        # 後から別に指紋を読むと、次の質問の指紋を読んでしまう。
        judgement, asked_signature = self._collect(now)
        verdict = self.arbiter.decide(
            assessment,
            judgement,
            asked_signature=asked_signature,
            current_signature=current_signature,
            now=now,
        )
        command = self.executor.apply(verdict)
        # Ask BEFORE recording the new signature: `_maybe_ask` decides on
        # "did the classification change since the last cycle?", so it
        # needs the previous value still in place.
        # 新しい指紋を記録する前に問う: `_maybe_ask` は「前周期から区分が
        # 変わったか」で判断するため、前の値が残っている必要がある。
        self._maybe_ask(state, current_signature, now)
        self._last_signature = current_signature

        is_decision = judgement is not None or verdict.source == "monitor"
        if not is_decision:
            return None
        row = {
            "state": state,
            "verdict": verdict,
            "judgement": judgement,
            "command": command,
        }
        self.decisions.append(row)
        if self.trace is not None:
            self.trace.write(state, judgement, verdict, command, self.question_ids)
        return row

    def run(self, max_steps: int = None, real_time: bool = True) -> int:
        """Loop at the monitor rate until landing or `max_steps`.
        着陸するか `max_steps` に達するまで監視周期で回す。返り値は実行した周期数。"""
        period = 1.0 / self.cfg.monitor_hz
        steps = 0
        while max_steps is None or steps < max_steps:
            started = time.monotonic()
            self.step()
            steps += 1
            if self.executor.landing:
                break
            if real_time:
                # Sleep only the slack, so a slow step does not make the
                # loop drift slower than the monitor rate on top of it.
                # 余った時間だけ待つ。処理が重い周期があっても、その分さらに
                # 遅くならないようにするため。
                slack = period - (time.monotonic() - started)
                if slack > 0:
                    time.sleep(slack)
        return steps

    # -- judging, off the loop's thread / ループ外スレッドでの判断 ------

    def _maybe_ask(self, state: dict, current_signature: str, now: float) -> None:
        """Start a request when the situation changed, or 1Hz has elapsed.
        状況が変わったとき、または 1 秒経過したときにリクエストを開始する。"""
        with self._lock:
            has_outstanding = self._pending is not None and not self._pending.done
        if has_outstanding:
            return

        changed = current_signature != self._last_signature
        overdue = (now - self._last_asked) >= (1.0 / self.cfg.judge_periodic_hz)
        if not (changed or overdue):
            return

        pending = _Pending(
            signature=current_signature,
            question_ids=self.question_ids,
            started=now,
        )
        with self._lock:
            self._pending = pending
        self._last_asked = now
        # Daemon thread: an outstanding request must never keep the CLI
        # alive at exit. Its result is only ever read through `pending`.
        # daemon スレッドにする: 未完了のリクエストが CLI の終了を妨げては
        # ならない。結果は `pending` 経由でのみ読む。
        threading.Thread(
            target=self._ask_worker, args=(state, pending), daemon=True
        ).start()

    def _ask_worker(self, state: dict, pending: _Pending) -> None:
        """Ask the Judge; store whatever comes back, including failure.
        Judge に問い、返ったもの（失敗も含む）を保存する。"""
        try:
            judgement = self.judge.ask(state, pending.question_ids)
        except Exception as exc:                      # noqa: BLE001
            # A Judge that raises must not take the flight down with it.
            # The Arbiter turns this into hovering like any other failure.
            # 例外を投げる Judge に飛行を巻き込ませない。Arbiter は他の失敗と
            # 同様、これを待機に変える。
            judgement = Judgement(error=f"{type(exc).__name__}: {exc}")
        pending.judgement = judgement
        pending.done = True

    def _collect(self, now: float):
        """Take a finished answer with the signature it was asked under,
        or mark an overdue one as late. Returns (judgement|None, signature).
        完了した答えを、質問時の指紋と組で取り出す。期限を過ぎていれば遅延
        として扱う。(judgement|None, 指紋) を返す。"""
        with self._lock:
            pending = self._pending
        if pending is None:
            return None, self._last_signature

        if pending.done:
            with self._lock:
                self._pending = None
            return pending.judgement, pending.signature

        # Still outstanding past the deadline: report it as late now,
        # rather than waiting for a reply the Arbiter would discard
        # anyway. The worker keeps running and its result is dropped.
        # 期限を過ぎてなお未完了: どのみち Arbiter が破棄する応答を待たず、
        # ここで期限超過として扱う。スレッドは動き続け、結果は捨てられる。
        elapsed_ms = (now - pending.started) * 1e3
        is_late = elapsed_ms > self.cfg.judge.deadline_s * 1e3
        if is_late:
            with self._lock:
                self._pending = None
            return (
                Judgement(latency_ms=elapsed_ms, error="deadline exceeded"),
                pending.signature,
            )
        return None, pending.signature


def replay(link, judge, config=DEFAULT_CONFIG, trace=None, max_steps=None) -> "Pilot":
    """Play a recorded flight through the decision layers, as fast as
    the machine allows. Nothing is transmitted: the link records instead.
    記録済みの飛行を判断層に通す。実時間では待たない。何も送信せず、リンクが
    記録するだけである。"""
    pilot = Pilot(link, judge, config, trace=trace)
    steps = 0
    while max_steps is None or steps < max_steps:
        samples_left = _has_samples(link)
        pilot.step(now=steps / config.monitor_hz)
        steps += 1
        if not samples_left:
            break
        if pilot.executor.landing:
            break
    return pilot


def _has_samples(link) -> bool:
    """Whether the replay source still has data. / 再生元にまだデータがあるか。"""
    index = getattr(link, "_index", None)
    total = getattr(link, "sample_count", None)
    if index is None or total is None:
        return True
    return index < total
