#!/usr/bin/env python3
"""
The forward distance becomes words, and a close wall stops the craft (P4b).

前方距離が言葉になること、そして近い壁が機体を止めること（P4b）。

What is verified:

  (a) The words themselves, against MEASURED samples: a real approach to a
      wall 1.0 m away is described as a wall getting near, and a real flight
      in an empty room never produces that description.
  (b) An invalid forward reading is NOT clearance. This is the one that
      matters: the real part reports invalid both when nothing is ahead and
      when a surface is too close to resolve (jev-autopilot §4.8.6), so
      reading an absent value as "open" would fly the craft into the case it
      cannot see.
  (c) The immediate stop rule fires from the Monitor alone, without a
      judgement, and the Arbiter carries it through as a stop.

確認する内容:

  (a) 語そのものを「実測」サンプルに対して: 1.0m 先の壁への実際の接近が「壁が
      近づいている」と記述され、空の部屋での実際の飛行がその記述を一度も生まない
      こと。
  (b) 無効な前方の読み値が「空いている」では「ない」こと。ここが肝心である: 実機は
      前に何も無いときも、面が近すぎて復元できないときも無効を返す（§4.8.6）。
      無い値を「開けている」と読めば、見えていない方の場合へ機体を進めることになる。
  (c) 即時停止則が Monitor だけで、判断を待たずに発火し、Arbiter がそれを停止として
      通すこと。

WHY MEASURED FIXTURES: a FakeJudge does not read the state, so a wrong word
reaches no assertion of its own -- that is how five state bugs survived a
green suite until the first live-Jev flight (§4.5). The fixtures here are
captured from real SILS flights (see fixtures/README.md), so the words are
checked against distances the sensor actually produced.

実測の fixture を使う理由: FakeJudge は state を読まないので、誤った語はどの表明にも
届かない ―― 5 件の state の不具合が、実際に Jev を飛ばすまで全件成功の試験一式を
生き延びたのはそのためである（§4.5）。ここの fixture は実際の SILS 飛行から採取して
あり（fixtures/README.md 参照）、語はセンサが実際に出した距離に対して確認される。
"""

import json
from pathlib import Path

import pytest

from sfpilot.arbiter import Arbiter, VERDICT_STOP, Verdict
from sfpilot.config import DEFAULT_CONFIG
from sfpilot.link import Sample
from sfpilot.monitor import (
    FORWARD_OPEN,
    FORWARD_SOMEWHAT_NEAR,
    FORWARD_UNKNOWN,
    FORWARD_WALL_NEAR,
    SAFETY_NONE,
    SAFETY_STOP,
    Monitor,
)
from sfpilot.summarizer import assert_no_numbers, summarize

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> list:
    """Measured samples, one per line, oldest first.
    実測サンプル。1 行 1 件、古い順。"""
    path = FIXTURES / name
    return [Sample(**json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines() if line]


def _words(samples: list) -> list:
    """Feed samples one per cycle and collect the forward word each time.

    One sample per `update` call because a flight does exactly that, and the
    hysteresis is a function of the sequence rather than of any one reading.
    1 周期 1 サンプルで流し、毎回の前方の語を集める。飛行がまさにそうするから
    であり、ヒステリシスは個々の読み値ではなく列の関数だからである。
    """
    monitor = Monitor()
    return [monitor.update([sample]).forward_clearance for sample in samples]


# =============================================================================
# (a) The words, against measured flights / 実測飛行に対する語
# =============================================================================

def test_a_real_approach_to_a_wall_ends_in_wall_near():
    """A measured approach is described as somewhat near, then as a wall.

    The fixture is a real approach: the reading starts at 1.017 m and closes
    to 0.11 m. Note it never passes through "open" -- the scene's wall stands
    1.0 m away, which is already inside the "somewhat near" band
    (`somewhat_near_m` = 1.5 m). That is the scene being deliberately small
    enough to fly in, not a misclassification: a wall the craft can reach
    before the envelope's 2 m radius has to start inside that band. The
    assertion therefore checks the transition the approach actually contains.

    実測の接近が「やや近い」→「壁が近い」と記述されること。

    fixture は実際の接近である（読み値は 1.017m から 0.11m へ詰まる）。「開けている」を
    一度も通らない点に注意 ―― 場面の壁は 1.0m 先に立っており、それは既に「やや近い」の
    帯域（`somewhat_near_m` = 1.5m）の内側である。これは誤区分ではなく、場面が飛べる
    大きさに意図して収めてある結果である: 飛行領域の半径 2m の内側で到達できる壁は、その
    帯域の中から始まるほかない。したがって表明は、この接近が実際に含む遷移を確認する。
    """
    samples = _load("wall_approach_states.jsonl")
    words = _words(samples)

    # The first rows are the boot seconds, before the forward part has been
    # brought up: they legitimately carry no reading. The approach is judged
    # from the first cycle that actually measured something.
    # 最初の数行は起動中で、前方の部品が立ち上がる前である。読み値を持たないのが
    # 正しい。接近の判定は、実際に何かを測った最初の周期から行う。
    first_measured = next(i for i, s in enumerate(samples) if "tof_front_m" in s)
    assert words[first_measured] == FORWARD_SOMEWHAT_NEAR, (
        f"the approach began at {samples[first_measured]['tof_front_m']:.3f} m "
        f"and was described as {words[first_measured]!r}")
    assert words[-1] == FORWARD_WALL_NEAR, (
        f"the approach ended at 0.11 m and was described as {words[-1]!r}")
    # Once the wall is near it stays near: the hysteresis must not let a
    # reading sitting on the threshold flip the description back to open.
    # 一度「壁が近い」になったら、そのままであること: しきい値上の読み値で記述が
    # 「開けている」へ戻ってはならない。
    first_near = words.index(FORWARD_WALL_NEAR)
    assert FORWARD_OPEN not in words[first_near:], (
        "the description returned to 'open' after the wall was already near")


def test_an_empty_room_is_never_described_as_a_wall():
    """A real flight with no obstacle never reports a wall, at any moment.

    Measured in the `nominal` scene, where the forward part is absent
    entirely, so every forward reading is invalid. The point is what that
    absence must NOT become: a wall that is not there would stop a healthy
    flight for the rest of its duration.

    障害物の無い実際の飛行が、どの瞬間にも壁を報告しないこと。

    前方の部品が丸ごと不在の `nominal` 場面での実測なので、前方の読み値は全て無効で
    ある。要点は、その不在が「何になってはならないか」である: 在りもしない壁は、
    健全な飛行を以後ずっと止めてしまう。
    """
    words = _words(_load("open_room_states.jsonl"))

    assert FORWARD_WALL_NEAR not in words, (
        "an empty room was described as having a wall near")
    assert FORWARD_SOMEWHAT_NEAR not in words, (
        "an empty room was described as having something somewhat near")


# =============================================================================
# (b) An invalid reading is not clearance / 無効は「空いている」ではない
# =============================================================================

def test_a_forward_reading_that_never_arrives_is_unknown_not_open():
    """With no forward sensor at all, the word is 'cannot measure'.

    This is the distinction the whole feature rests on. `open` would mean
    the code had decided there is clear space ahead on the strength of
    having measured nothing.
    前方センサが一切無いとき、語は「測定不能」であること。

    この機能全体が乗っている区別である。`open` であれば、「何も測らなかった」ことを
    根拠に「前は空いている」とコードが判断したことになる。
    """
    monitor = Monitor()
    assessment = monitor.update([Sample(t=0.0, altitude_m=0.5)])

    assert assessment.forward_clearance == FORWARD_UNKNOWN
    assert assessment.forward_clearance != FORWARD_OPEN


def test_a_dropped_reading_in_front_of_a_wall_keeps_the_wall():
    """A brief invalid reading holds the last valid word, not 'open'.

    A sample dropped in front of a wall is still a wall. Within the grace
    period the description must not improve just because the sensor blinked.
    壁の前での一時的な無効は、最後の有効な語を保つこと（「開けている」ではなく）。

    壁の前で落ちた 1 サンプルは、依然として壁である。猶予の間は、センサが瞬きした
    というだけで記述が好転してはならない。
    """
    monitor = Monitor()
    monitor.update([Sample(t=0.0, tof_front_m=0.4)])       # a wall, close
    grace = DEFAULT_CONFIG.forward.invalid_grace_s

    still_near = monitor.update([Sample(t=grace * 0.5)])   # reading drops out
    assert still_near.forward_clearance == FORWARD_WALL_NEAR, (
        "a dropped sample in front of a wall was read as the wall going away")


def test_a_reading_that_stays_missing_becomes_unknown():
    """Past the grace period the stale value is abandoned for 'unknown'.

    The last valid reading is evidence with an expiry: holding a wall
    forever on a memory would freeze the craft, and claiming clear space
    would be worse. Neither -- it becomes unknown.
    猶予を過ぎれば、古い値は捨てられ「測定不能」になること。

    最後の有効な読み値には期限がある。記憶だけで壁を保ち続ければ機体は固まり、
    空いていると言えばもっと悪い。どちらでもなく「測定不能」になる。
    """
    monitor = Monitor()
    monitor.update([Sample(t=0.0, tof_front_m=0.4)])
    grace = DEFAULT_CONFIG.forward.invalid_grace_s

    stale = monitor.update([Sample(t=grace + 0.5)])
    assert stale.forward_clearance == FORWARD_UNKNOWN


def test_unknown_does_not_by_itself_stop_the_craft():
    """'Cannot measure' is a reason for caution, not an automatic stop.

    Stopping on every unmeasurable reading would stop the craft on every
    flight without a forward sensor -- which is every flight the regression
    suite runs. Caution is Jev's to exercise on the word; the code stops
    only on a measured wall.
    「測定不能」は警戒の理由であって、自動的な停止ではないこと。

    測れない読み値のたびに止めるなら、前方センサの無い全ての飛行で止まることに
    なる ―― 回帰一式が走らせる飛行は全てそれである。語に対する警戒は Jev の仕事で
    あり、コードが止めるのは実測された壁に対してだけである。
    """
    monitor = Monitor()
    assessment = monitor.update([Sample(t=0.0, altitude_m=0.5)])

    assert assessment.forward_clearance == FORWARD_UNKNOWN
    assert assessment.safety_action == SAFETY_NONE


# =============================================================================
# (c) The immediate stop rule / 即時停止則
# =============================================================================

def test_a_wall_within_the_stop_distance_stops_without_asking_jev():
    """The Monitor alone returns a stop; no judgement is involved.

    The round trip has no guaranteed upper bound, so the decision that must
    happen before an answer could arrive is made by code.
    Monitor だけで停止を返すこと。判断は関与しない。

    往復時間に上限の保証は無いので、答えが届くより前に下さねばならない判断は
    コードが下す。
    """
    # At a standstill the stopping distance is its floor, the safety margin.
    # 静止していれば停止距離はその下限、すなわち安全余裕である。
    stop_at = DEFAULT_CONFIG.forward.safety_margin_m
    monitor = Monitor()

    assessment = monitor.update([Sample(t=0.0, tof_front_m=stop_at - 0.05)])

    assert assessment.safety_action == SAFETY_STOP
    assert assessment.forward_clearance == FORWARD_WALL_NEAR
    assert assessment.safety_reason, "a stop must say why it stopped"


def test_open_space_ahead_does_not_stop_the_craft():
    """A wall comfortably far away is not a reason to stop.
    十分に遠い壁は停止の理由にならないこと。"""
    monitor = Monitor()
    far = DEFAULT_CONFIG.forward.somewhat_near_m + 1.0

    assessment = monitor.update([Sample(t=0.0, tof_front_m=far)])

    assert assessment.forward_clearance == FORWARD_OPEN
    assert assessment.safety_action == SAFETY_NONE


def test_the_stop_does_not_chatter_at_the_threshold():
    """Once stopped, a reading just past the threshold stays stopped.

    Without hysteresis a reading sitting on the boundary alternates between
    stop and continue, which is a craft braking and accelerating at a wall.
    一度止まったら、しきい値をわずかに超えた読み値では止まったままであること。

    ヒステリシスが無いと、境界上の読み値で停止と継続が交互になる。それは壁の前で
    制動と加速を繰り返す機体である。
    """
    cfg = DEFAULT_CONFIG.forward
    monitor = Monitor()
    # Stationary throughout, so the stopping distance stays at its floor and
    # this test is about the hysteresis alone rather than about the speed.
    # 全体を通して静止させる。停止距離を下限に固定し、この試験が速度ではなく
    # ヒステリシスだけを対象とするようにするためである。
    stop_at = cfg.safety_margin_m
    release_at = stop_at + cfg.release_margin_m
    monitor.update([Sample(t=0.0, tof_front_m=stop_at - 0.05)])

    # Just above the stop distance, but not yet past the release distance.
    # 停止距離のすぐ上。ただし解除距離にはまだ達していない。
    between = (stop_at + release_at) / 2.0
    held = monitor.update([Sample(t=0.1, tof_front_m=between)])
    assert held.safety_action == SAFETY_STOP, (
        "the stop was released while the wall was still within the release "
        "distance — the classification will chatter")

    # Clear of the release distance: the craft may move again.
    # 解除距離を超えた: 再び動いてよい。
    cleared = monitor.update([Sample(t=0.2, tof_front_m=release_at + 0.3)])
    assert cleared.safety_action == SAFETY_NONE


def test_the_arbiter_carries_an_obstacle_stop_through_as_a_stop():
    """The Monitor's stop outranks any judgement, as the safety rules do.

    An obstacle stop is a monitor-sourced action, so the Arbiter must pass
    it through without consulting an answer -- including when no judgement
    has arrived at all.
    Monitor の停止が、他の安全則と同じくあらゆる判断に優先すること。

    障害物による停止は monitor 由来の動作なので、Arbiter は答えを参照せずに
    通さねばならない ―― 判断が一切届いていないときも含めて。
    """
    monitor = Monitor()
    assessment = monitor.update(
        [Sample(t=0.0, tof_front_m=DEFAULT_CONFIG.forward.safety_margin_m - 0.1)])

    verdict = Arbiter().decide(
        assessment, judgement=None, asked_signature="x",
        current_signature="x", now=0.0)

    assert verdict.action == VERDICT_STOP
    assert verdict.source == "monitor"


# =============================================================================
# (d) The stopping distance grows with the closing speed
#     停止距離が接近速度とともに伸びること
#
# The defect this replaced: a fixed 0.5 m threshold let the craft through the
# wall in 3 of 8 measured approaches, because `stop` restores position hold
# and does not brake -- so the room a stop needs depends on the speed it is
# issued at (jev-autopilot §4.9.7).
#
# 置き換えた欠陥: 固定の 0.5m というしきい値は、実測 8 回の接近のうち 3 回で機体を
# 壁の向こうへ通した。`stop` が戻すのは位置保持であって制動ではなく、したがって停止に
# 要る余地は、それが送られた時点の速度に依存するからである（4.9.7 節）。
# =============================================================================

def test_the_stopping_distance_grows_with_the_closing_speed():
    """A faster approach must be stopped from further out.

    This is the whole correction. Under the old fixed threshold these two
    speeds were stopped at the same distance, and the fast one went through
    the wall.
    速い接近ほど、遠くから止め始めねばならないこと。

    これが訂正の全体である。旧来の固定しきい値では、この 2 つの速度は同じ距離で
    止められており、速いほうは壁を通り越した。
    """
    monitor = Monitor()

    slow = monitor.stop_distance({"vel_n": 0.1, "vel_e": 0.0, "yaw": 0.0})
    fast = monitor.stop_distance({"vel_n": 0.4, "vel_e": 0.0, "yaw": 0.0})

    assert fast > slow, (
        f"a 0.4 m/s approach was given no more room ({fast:.2f} m) than a "
        f"0.1 m/s one ({slow:.2f} m) — this is the fixed-threshold defect")


def test_a_standstill_still_keeps_the_safety_margin():
    """With no closing speed the distance is the margin, never zero.
    接近速度が無ければ距離は安全余裕であって、0 にはならないこと。"""
    monitor = Monitor()

    at_rest = monitor.stop_distance({"vel_n": 0.0, "vel_e": 0.0, "yaw": 0.0})

    assert at_rest == pytest.approx(DEFAULT_CONFIG.forward.safety_margin_m)


def test_the_stopping_distance_is_capped_at_what_the_sensor_can_see():
    """However fast the craft closes, the distance stays reportable.

    A threshold beyond the forward part's range is a threshold that never
    fires, because no reading can ever be below it.
    どれだけ速く詰めても、距離は報告可能な範囲に留まること。

    前方の部品の射程を超えたしきい値とは、決して発火しないしきい値のことである。
    どの読み値もそれを下回れないからである。
    """
    monitor = Monitor()

    absurd = monitor.stop_distance({"vel_n": 50.0, "vel_e": 0.0, "yaw": 0.0})

    assert absurd == pytest.approx(DEFAULT_CONFIG.forward.stop_distance_max_m)


def test_travelling_sideways_is_not_closing_on_the_wall_ahead():
    """Only the component along the nose closes the forward distance.

    A craft sliding sideways past a wall is not approaching it, and braking
    for it would stop a flight that was never in danger. The forward sensor
    looks along the nose, so that is the only direction that consumes the
    distance it reports.
    機首方向の成分だけが前方距離を詰めること。

    壁の脇を横滑りしている機体は、その壁へ近づいてはいない。それに対して制動すれば、
    危険でなかった飛行を止めることになる。前方センサは機首方向を見ているので、その
    報告する距離を費やすのはその向きだけである。
    """
    monitor = Monitor()

    # Facing north, travelling due east at a speed that would otherwise
    # demand a large stopping distance.
    # 北を向き、真東へ進む。大きさだけ見れば大きな停止距離を要求する速度である。
    sideways = monitor.stop_distance({"vel_n": 0.0, "vel_e": 0.5, "yaw": 0.0})

    assert sideways == pytest.approx(DEFAULT_CONFIG.forward.safety_margin_m), (
        "a craft travelling parallel to the wall was treated as closing on it")


def test_retreating_from_a_wall_does_not_shrink_the_stopping_distance():
    """A negative closing speed is not a licence to get closer.

    Reversing away from a wall must not reduce the room the rule keeps
    below the safety margin, which is what an unclamped projection would do.
    負の接近速度が「もっと近づいてよい」にならないこと。

    壁から後退することで、規則が保つ余地が安全余裕より小さくなってはならない。
    射影を下側でクランプしなければ、まさにそうなる。
    """
    monitor = Monitor()

    reversing = monitor.stop_distance({"vel_n": -0.4, "vel_e": 0.0, "yaw": 0.0})

    assert reversing == pytest.approx(DEFAULT_CONFIG.forward.safety_margin_m)


def test_the_measured_approach_is_stopped_before_it_reaches_the_wall():
    """Against the recorded flight, the new rule fires with room to spare.

    The fixture is the measured approach of §4.9.7: it crosses the OLD fixed
    0.5 m threshold at 0.214 m/s and then coasts 0.417 m, which left only
    0.083 m of the 0.5 m. The rule must now fire while the wall is still far
    enough away that the same coast does not reach it.
    記録した飛行に対し、新しい規則が余裕を残して発火すること。

    fixture は 4.9.7 節の実測の接近である。旧来の固定しきい値 0.5m を 0.214m/s で
    通過し、その後 0.417m 惰走した ―― 0.5m のうち残りは 0.083m しかなかった。規則は
    今や、同じ惰走が届かない程度に壁が遠いうちに発火せねばならない。
    """
    samples = _load("wall_approach_states.jsonl")
    monitor = Monitor()

    fired_at = None
    for sample in samples:
        assessment = monitor.update([sample])
        if assessment.safety_action == SAFETY_STOP and fired_at is None:
            fired_at = sample.get("tof_front_m")

    assert fired_at is not None, "the approach never triggered a stop"
    # The coast measured on this very flight, from the moment the old
    # threshold was crossed. The new rule must leave at least this much.
    # まさにこの飛行で実測した惰走。旧しきい値の通過時点から測ったものである。
    # 新しい規則は、少なくともこれだけを残さねばならない。
    measured_coast_m = 0.417
    assert fired_at > measured_coast_m, (
        f"the stop fired at {fired_at:.3f} m, inside the {measured_coast_m} m "
        f"this same flight was measured to coast — the wall would be reached")


# =============================================================================
# (e) Backing away when the stop did not work / 停止が効かなかったときの後退
# =============================================================================

def _closing_samples(start_m: float, step_m: float, count: int,
                     speed: float = 0.2) -> list:
    """A craft closing steadily on a wall, one sample per 0.1 s.
    壁へ一定の割合で詰めていく機体。0.1 秒ごとに 1 サンプル。"""
    return [Sample(t=index * 0.1, tof_front_m=start_m - index * step_m,
                   vel_n=speed, vel_e=0.0, yaw=0.0)
            for index in range(count)]


def test_a_stop_that_opens_no_distance_becomes_a_retreat():
    """Still closing after the grace period escalates the stop to a retreat.

    `stop` is not braking, so a craft carrying speed keeps closing after it.
    When position hold has had its time and the gap is still shrinking, the
    only move left that increases the distance is backwards.
    猶予の後もなお詰まっていれば、停止が後退へ格上げされること。

    `stop` は制動ではないので、速度を持った機体は停止後も詰め続ける。位置保持に
    時間を与えてもなお隙間が縮んでいるなら、距離を増やす手段として残っているのは
    後退だけである。
    """
    from sfpilot.monitor import SAFETY_BACK

    grace = DEFAULT_CONFIG.forward.backoff_grace_s
    monitor = Monitor()
    # Start inside the stop band and keep closing right through the grace.
    # 停止帯域の内側から始め、猶予を通り越してなお詰め続ける。
    count = int(grace / 0.1) + 5
    actions = [monitor.update([s]).safety_action
               for s in _closing_samples(0.45, 0.01, count)]

    assert SAFETY_BACK in actions, (
        "a craft that kept closing after its stop was never told to back off")
    # The stop must come FIRST: a retreat is the escalation of a stop that
    # did not work, never the opening move.
    # 停止が**先**であること。後退は効かなかった停止の格上げであり、最初の一手では
    # ない。
    assert actions.index(SAFETY_STOP) < actions.index(SAFETY_BACK)


def test_a_stop_that_holds_the_distance_never_becomes_a_retreat():
    """A craft that stopped where it was told is left alone.

    Retreating from an obstacle that is no longer being approached would
    trade a known wall ahead for whatever is behind, which the craft cannot
    see at all.
    指示どおり止まった機体は、そのままにされること。

    もはや近づいていない障害物から後退することは、既知の前方の壁を、まったく
    見えていない後方の何かと取り替えることである。
    """
    from sfpilot.monitor import SAFETY_BACK

    grace = DEFAULT_CONFIG.forward.backoff_grace_s
    monitor = Monitor()
    # Inside the stop band, but holding its distance: it stopped.
    # 停止帯域の内側だが、距離を保っている。止まったということである。
    held = [Sample(t=index * 0.1, tof_front_m=0.45, vel_n=0.0, vel_e=0.0, yaw=0.0)
            for index in range(int(grace / 0.1) + 5)]
    actions = [monitor.update([s]).safety_action for s in held]

    assert SAFETY_BACK not in actions, (
        "a craft that had already stopped was told to back away anyway")
    assert SAFETY_STOP in actions


def test_sensor_noise_alone_does_not_trigger_a_retreat():
    """A change under the noise floor is not "still closing".

    A stationary craft's reading jitters, and retreating on that would have
    the craft reverse away from a wall it is not approaching.
    雑音の下限に満たない変化は「まだ詰まっている」ではないこと。

    静止した機体の読み値は揺らぐ。それで後退すれば、近づいてもいない壁から機体が
    後退することになる。
    """
    from sfpilot.monitor import SAFETY_BACK

    cfg = DEFAULT_CONFIG.forward
    monitor = Monitor()
    # Closing by less, in total, than the noise threshold allows.
    # 合計でも、雑音のしきい値が許す量より小さくしか詰まらない。
    count = int(cfg.backoff_grace_s / 0.1) + 5
    creep = (cfg.backoff_closing_m * 0.5) / count
    actions = [monitor.update([s]).safety_action
               for s in _closing_samples(0.45, creep, count, speed=0.0)]

    assert SAFETY_BACK not in actions


def test_the_retreat_is_sent_once_and_not_stacked():
    """One `back` goes out per retreat, however many cycles it spans.

    `back` is a blocking move the vehicle answers on arrival, so re-sending
    it every 50Hz cycle would queue dozens of retreats and fly the craft
    backwards into whatever is behind it.
    1 回の後退につき `back` は 1 度だけ送られること。何周期にまたがっても同じである。

    `back` は機体が到達時に応答するブロックする移動なので、50Hz の周期ごとに送り
    直せば後退が何十個も積まれ、機体は後方の何かへ向かって飛ぶことになる。
    """
    from sfpilot.arbiter import VERDICT_BACK
    from sfpilot.executor import Executor

    class _Link:
        def __init__(self):
            self.commands = []

        def send_command(self, line):
            self.commands.append(line)

        def send_rc(self, *rc):
            self.commands.append("rc")

        def priority(self, line):
            self.commands.append(line)

    link = _Link()
    executor = Executor(link, DEFAULT_CONFIG)
    retreat = Verdict(action=VERDICT_BACK, reason="still closing", source="monitor")

    for _ in range(10):
        executor.apply(retreat)
        executor.tick()

    backs = [c for c in link.commands if c.startswith("back")]
    assert len(backs) == 1, f"the retreat was sent {len(backs)} times: {backs}"
    # A `stop` precedes it: the interrupted move's position target is still
    # pulling the craft on, and a new target set alongside it would not undo
    # that.
    # その前に `stop` が来ること。割り込まれた移動の位置目標がなお機体を引いており、
    # それを残したまま新しい目標を置いても、引きは解けないからである。
    assert "stop" in link.commands
    assert link.commands.index("stop") < link.commands.index(backs[0])


# =============================================================================
# The state Jev reads / Jev が読む state
# =============================================================================

def test_the_forward_word_reaches_jev_in_english_and_without_numbers():
    """The state carries a translated forward_clearance and no figures.

    An untranslated classification would send Japanese to a model that is
    most accurate in English, and would do so silently -- the failure mode
    that put an untranslated battery trend one config change away from
    reaching Jev (§4.5).
    state が訳された forward_clearance を持ち、数値を持たないこと。

    訳し漏らした区分は、英語で最も精度の高いモデルへ日本語を送ることになり、しかも
    黙ってそうなる ―― 訳されない電池の傾向が、設定変更 1 つで Jev へ届く寸前だった
    のと同じ失敗の形である（§4.5）。
    """
    monitor = Monitor()
    assessment = monitor.update([Sample(t=0.0, tof_front_m=0.4, altitude_m=0.5)])

    state = summarize(assessment)

    assert state["flight"]["forward_clearance"] == "wall near"
    assert_no_numbers(state)   # raises if any figure slipped in


@pytest.mark.parametrize("metres,expected", [
    (0.30, "wall near"),
    (0.70, "wall near"),          # above the stop band, still near
    (1.20, "somewhat near"),
    (2.50, "open"),
])
def test_each_band_translates_to_its_own_english_word(metres, expected):
    """Every band has a distinct English word, so none is silently merged.
    各帯域が固有の英語の語を持ち、黙って統合される帯域が無いこと。"""
    monitor = Monitor()
    assessment = monitor.update([Sample(t=0.0, tof_front_m=metres)])

    assert summarize(assessment)["flight"]["forward_clearance"] == expected
