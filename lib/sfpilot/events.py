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
EVENT_SWEEP = "sweep"           # one look around, as eight sectors / 見回し 1 回


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
    # `tof_front_m` is absent from most samples, and its absence is meaningful
    # (nothing measured ahead), so the loop's "skip a missing value" rule
    # carries it: the page then draws the forward ray only when there is a
    # reading to draw, rather than drawing a ray of length zero.
    # `tof_front_m` は多くのサンプルに無く、その不在には意味がある（前方に測れた
    # ものが無い）。この繰り返しの「無い値は写さない」規則がそれをそのまま運ぶので、
    # ページは長さ 0 の線ではなく「描くべき読み値があるときだけ」前方の線を描く。
    for key in ("altitude_m", "pos_n", "pos_e", "vel_n", "vel_e", "vel_d",
                "roll", "pitch", "yaw", "battery_pct", "battery_v", "tof_m",
                "tof_front_m"):
        value = sample.get(key)
        if value is not None:
            payload[key] = round(float(value), 4)
    state = sample.get("flight_state")
    if state:
        payload["flight_state"] = state
    if phase:
        payload["phase"] = phase
    return payload


def sweep_payload(sweep_result, sample=None) -> dict:
    """One sweep as the top view's fan of sectors wants it.

    Each bearing becomes an angle, a classification and a distance, plus
    where the craft was when it swept -- the fan is drawn around that
    point, not around wherever the craft has got to since, or a sweep
    would appear to follow the aircraft down the corridor it measured.

    The distance is the NEAREST valid reading and may be absent, which is
    what an unmeasured bearing is. The page draws such a sector at the
    sensor's own range rather than at length zero, so "we looked and could
    not tell" is visible instead of missing.

    掃引 1 回分を、上から見た図の扇形が要する形にする。

    各方位は角度・区分・距離になり、加えて「掃引した時点で機体がどこにいたか」を
    持つ。扇形はその点のまわりに描くのであって、その後に機体が着いた場所のまわり
    ではない。そうしないと、掃引が、それが測った通路を機体について回るように
    見えてしまう。

    距離は**最も近い**有効な読み値で、欠けることがある。それが「測れていない
    方位」である。ページはその扇形を長さ 0 ではなくセンサ自身の射程で描くので、
    「見たが判別できなかった」が、欠落ではなく目に見える形で出る。
    """
    if sweep_result is None or not sweep_result.readings:
        return {}
    origin = sample or {}
    payload = {
        "n": round(float(origin.get("pos_n") or 0.0), 4),
        "e": round(float(origin.get("pos_e") or 0.0), 4),
        "yaw": round(float(origin.get("yaw") or 0.0), 4),
        "outcome": sweep_result.outcome,
        "bearings": [],
    }
    for reading in sweep_result.readings:
        bearing = {
            "name": reading.name,
            "deg": round(float(reading.relative_deg), 2),
            "clearance": reading.clearance,
        }
        if reading.nearest_m is not None:
            bearing["metres"] = round(float(reading.nearest_m), 4)
        payload["bearings"].append(bearing)
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
