"""
sfpilot.events - the one stream of things that happened, fanned out.

A flight produces two records of the same events: the trace file
(`logs/pilot/*.jsonl`, one decision per line) and, when `--web` is on, the
live browser view. They must say the same thing, so they are not built
twice: `EventBus` is the single point every event passes through, and the
trace and the web view are two subscribers to it.

sfpilot.events - 起きたことの流れを 1 つにまとめ、複数へ配る仕組み。

飛行は同じ出来事を 2 通りに記録する（記録ファイル `logs/pilot/*.jsonl` の
1 判断 1 行と、`--web` を付けたときのブラウザのライブ表示）。両者が同じことを
述べる必要があるので、二重には組み立てない。`EventBus` が全ての出来事が通る
唯一の地点であり、記録とライブ表示はその購読者 2 つである。

What never reaches a subscriber: the API key, any environment variable,
and the raw numerics beyond what the assessment already classified. The
trace file has that rule because a user attaches it to a bug report; the
web view needs it for the same reason and one more -- anything on the page
is one screenshot away from being shared.

購読者に決して渡らないもの: API キー、環境変数、評価が区分した以上の生の数値。
記録ファイルにこの規則があるのは、利用者がそれを不具合報告に添えるからである。
ライブ表示にも同じ理由に加えてもう 1 つある — 画面にあるものは、画面の写真
1 枚で共有されうる。
"""

import threading

# What kind of thing happened. Named here rather than spelled inline at each
# `publish` so the browser and the tests agree on the vocabulary.
# 起きたことの種別。各 `publish` に直接書かず、ここで名前にする。ブラウザと
# 試験が同じ語彙を使うようにするためである。
EVENT_DECISION = "decision"     # one judgement + verdict + command / 1 判断
EVENT_SAMPLE = "sample"         # one telemetry sample / テレメトリ 1 件
EVENT_PHASE = "phase"           # a change of flight phase, step or leg / 段階の変化
EVENT_STATUS = "status"         # the running totals shown at the top / 上部の現在値


class EventBus:
    """Fan one flight's events out to whoever is listening.

    Publishing must never block the 50Hz loop, so a subscriber that cannot
    keep up loses events rather than slowing the flight: the queue is
    bounded and the OLDEST event is dropped when it is full. A live view
    that skipped a frame is correct behaviour; a monitor loop that missed
    its deadline is not.

    1 回の飛行の出来事を、聞いている先すべてに配る。

    配信が 50Hz のループを止めてはならないので、追随できない購読者は飛行を
    遅らせるのではなく出来事を失う — 待ち行列には上限があり、いっぱいなら
    **最も古い**ものを捨てる。表示が 1 コマ飛ぶのは正しい動作だが、監視ループが
    周期を落とすのは正しくない。
    """

    def __init__(self):
        self._subscribers: list = []
        self._lock = threading.Lock()

    def subscribe(self, queue) -> None:
        """Add a sink. Anything with a thread-safe `put_nowait`/`full` works.
        配信先を足す。スレッド安全な `put_nowait`・`full` を持てばよい。"""
        with self._lock:
            self._subscribers.append(queue)

    def unsubscribe(self, queue) -> None:
        """Remove a sink. Safe to call for one that is already gone.
        配信先を外す。既に無いものに対して呼んでも安全。"""
        with self._lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, kind: str, payload: dict) -> None:
        """Hand one event to every sink, dropping rather than waiting.
        出来事を各配信先へ渡す。待たずに捨てる。"""
        event = {"kind": kind, **payload}
        with self._lock:
            sinks = list(self._subscribers)
        # Copied out of the lock before delivering: a sink's `put_nowait` is
        # another thread's code, and holding the lock across it would let a
        # slow sink block `subscribe` -- and so the HTTP thread -- from the
        # loop's thread.
        # 施錠を解いてから配る。配信先の `put_nowait` は別スレッドのコードであり、
        # 施錠したまま呼ぶと、遅い配信先がループのスレッドから `subscribe`
        #（ひいては HTTP スレッド）を止めうるためである。
        for sink in sinks:
            _offer(sink, event)


def _offer(queue, event: dict) -> None:
    """Put `event` on `queue`, making room by dropping the oldest.
    `event` を `queue` に載せる。満杯なら最も古いものを捨てて空ける。"""
    import queue as queue_module

    try:
        queue.put_nowait(event)
        return
    except queue_module.Full:
        pass
    try:
        queue.get_nowait()          # drop the oldest / 最も古いものを捨てる
        queue.put_nowait(event)
    except (queue_module.Empty, queue_module.Full):
        # Another consumer emptied or refilled it in between. The event is
        # lost, which is this class's contract; never raise into the loop.
        # その間に別の消費者が空にした・満たした。出来事は失われるが、それが
        # 本クラスの約束である。ループへ例外を投げてはならない。
        pass


def sample_payload(sample, phase: str = None) -> dict:
    """One telemetry Sample as the browser's 3D view and plots want it.

    Only the fields the view draws are copied, by name. A Sample is a dict
    whose keys differ by source (`link.Sample`), so copying it wholesale
    would put whatever the source happened to carry onto the page.

    テレメトリの Sample 1 件を、ブラウザの 3D 表示とグラフが要する形にする。

    表示が描く項目だけを名前で写す。Sample は入力源によってキーの異なる dict
    であり（`link.Sample`）、丸ごと写すと入力源がたまたま持っていたものが
    そのまま画面に載ってしまう。
    """
    if not sample:
        return {}
    payload = {"t": round(sample.get("t", 0.0), 3)}
    for key in ("altitude_m", "pos_n", "pos_e", "vel_n", "vel_e", "vel_d",
                "roll", "pitch", "yaw", "battery_pct", "battery_v", "tof_m"):
        value = sample.get(key)
        if value is not None:
            payload[key] = round(float(value), 4)
    state = sample.get("flight_state")
    if state:
        payload["flight_state"] = state
    if phase:
        payload["phase"] = phase
    return payload


def decision_payload(row: dict) -> dict:
    """One trace row as the browser's decision list wants it.

    Takes the row the Trace wrote, so the page and the file cannot
    disagree: there is one row, formatted twice.

    判断 1 件を、ブラウザの判断一覧が要する形にする。

    Trace が書いた行そのものを受け取る。ページとファイルが食い違いえない
    ためである — 行は 1 つで、体裁が 2 通りあるだけである。
    """
    return dict(row)
