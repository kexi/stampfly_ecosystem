"""
test_monitor.py - numbers become words, trends need duration, and the
immediate safety rules act without asking anyone.
test_monitor.py - 数値が言葉になること、傾向には継続時間が要ること、
即時安全則が誰にも問わずに働くこと。
"""

from sfpilot.config import DEFAULT_CONFIG
from sfpilot.link import Sample
from sfpilot.monitor import (
    BATTERY_TREND_SHARP, Monitor, SAFETY_LAND, SAFETY_NONE, SAFETY_STOP,
)


def _stream(count, dt=0.02, start=0.0, **fields):
    """A run of identical samples at 50Hz, beginning at `start`.

    `start` matters because the Monitor's debounce is measured on the
    sample clock: a second call that restarted at t=0 would look like time
    running backwards (monitor.MonitorConfig.classification_hold_s).

    50Hz の同一サンプル列。`start` から始める。

    `start` に意味があるのは、Monitor の保持時間がサンプルの時刻で測られる
    ためである。2 回目の呼び出しが t=0 から始まると、時間が巻き戻ったように
    見えてしまう。
    """
    return [Sample(t=start + i * dt, **fields) for i in range(count)]


def _hold_samples(dt=0.02) -> int:
    """Enough samples to outlast the classification hold time.
    区分の保持時間を超えるのに足りるサンプル数。"""
    return int(DEFAULT_CONFIG.monitor.classification_hold_s / dt) + 5


def _feed(monitor, count, dt=0.02, start=0.0, **fields) -> str:
    """Fold samples in ONE AT A TIME and return the reported altitude.

    One at a time because the debounce advances per `update()` call, not
    per sample: a whole list handed over at once classifies only its last
    member, which is right for a batch arriving in one cycle but is not
    how a flight feeds the Monitor (pilot.py calls `update` per cycle).

    サンプルを 1 件ずつ取り込み、報告された高度を返す。

    1 件ずつにするのは、保持の判定が `update()` の呼び出しごとに進むからで
    ある（サンプルごとではない）。リストを一度に渡すと最後の 1 件だけが区分
    される。1 周期に届いたまとまりとしてはそれで正しいが、飛行が Monitor に
    与える形ではない（pilot.py は 1 周期ごとに `update` を呼ぶ）。
    """
    reported = ""
    for sample in _stream(count, dt=dt, start=start, **fields):
        reported = monitor.update([sample]).altitude
    return reported


def test_altitude_is_reported_as_a_band_not_a_number():
    """Altitude is classified relative to the target, in words.

    Each band is fed for longer than `classification_hold_s`, because a new
    classification only replaces the reported one after it has held (see
    `test_a_classification_must_hold_before_it_replaces_the_reported_one`).

    高度は目標に対する区分（言葉）で報告されること。

    各区分は `classification_hold_s` より長く与える。新しい区分が報告中のものを
    置き換えるのは、それが続いた後だからである。
    """
    monitor = Monitor(target_altitude_m=0.8)
    assert _feed(monitor, 3, altitude_m=0.81, start=0.0) == "目標どおり"
    assert _feed(monitor, _hold_samples(), altitude_m=1.30, start=0.1) == "目標より高い"
    assert _feed(monitor, _hold_samples(), altitude_m=0.30, start=3.0) == "目標より低い"


def test_a_classification_must_hold_before_it_replaces_the_reported_one():
    """A band edge crossed briefly does not become a new situation.

    Every crossing is a new situation signature, and the Arbiter discards
    an answer whose signature changed while it was in flight. A quantity
    resting on an edge would otherwise throw away every answer asked about
    it — measured on 2026-09-19, the battery trend alternated 510 times in
    60 s and `run nominal` lost 7 of its 11 answers to staleness.

    一瞬またいだ境目が、新しい状況にならないこと。

    またぐたびに状況の指紋が変わり、Arbiter は往復中に指紋が変わった答えを
    破棄する。境目の上に留まる量は、それについて問うた答えをすべて捨てさせる。
    2026-09-19 の実測では電池の傾向が 60 秒で 510 回入れ替わり、`run nominal` は
    11 件中 7 件の答えを鮮度切れで失った。
    """
    monitor = Monitor(target_altitude_m=0.8)
    assert _feed(monitor, 3, altitude_m=0.81, start=0.0) == "目標どおり"

    # A brief excursion into another band, far shorter than the hold time.
    # 別の区分への短い逸脱。保持時間よりはるかに短い。
    assert _feed(monitor, 5, altitude_m=1.30, start=0.1) == "目標どおり"

    # Back where it was: the excursion is never reported at all.
    # 元の区分に戻る。逸脱は結局一度も報告されない。
    assert _feed(monitor, 3, altitude_m=0.81, start=0.3) == "目標どおり"


def test_a_trend_needs_to_hold_before_it_is_reported():
    """Two adjacent samples are not a trend; a sustained change is.
    隣り合う 2 サンプルは傾向ではなく、続いた変化が傾向であること。"""
    monitor = Monitor()
    brief = [Sample(t=0.00, altitude_m=0.80), Sample(t=0.02, altitude_m=0.78)]
    assert monitor.update(brief).altitude_trend == "不明"

    sinking = [Sample(t=i * 0.02, altitude_m=0.80 - i * 0.02 * 0.4) for i in range(60)]
    assert Monitor().update(sinking).altitude_trend == "下降中"


def test_drift_direction_and_speed_become_words():
    """Horizontal velocity becomes a direction and a pace, never a figure.
    水平速度は方向と速さの語になり、数値にはならないこと。"""
    monitor = Monitor()
    still = monitor.update(_stream(3, vel_n=0.01, vel_e=0.01))
    assert still.horizontal_drift == "ほぼ静止"

    fast_right = Monitor().update(_stream(3, vel_n=0.0, vel_e=0.5))
    assert fast_right.horizontal_drift == "右へ速く流されている"


def test_dangerous_battery_lands_without_a_judge():
    """A battery in the danger band commands a landing on its own.
    電池が危険域なら、それだけで着陸を指示すること。"""
    danger = DEFAULT_CONFIG.monitor.battery_danger_pct - 1.0
    assessment = Monitor().update(_stream(3, altitude_m=0.8, battery_pct=danger))
    assert assessment.battery_level == "危険"
    assert assessment.safety_action == SAFETY_LAND


def test_low_battery_alone_does_not_land():
    """"Running low" is reported but left to the Judge to weigh.
    「残り少ない」は報告するが、判断は Judge に委ねること。"""
    low = DEFAULT_CONFIG.monitor.battery_danger_pct + 5.0
    assessment = Monitor().update(_stream(3, altitude_m=0.8, battery_pct=low))
    assert assessment.battery_level == "残り少ない"
    assert assessment.safety_action == SAFETY_NONE


def test_altitude_far_outside_the_envelope_stops_immediately():
    """An altitude well beyond the envelope stops without waiting.
    飛行領域を大きく外れた高度は、待たずに停止すること。"""
    way_high = (DEFAULT_CONFIG.envelope.altitude_max_m
                + DEFAULT_CONFIG.monitor.altitude_deviation_m + 0.5)
    assessment = Monitor().update(_stream(3, altitude_m=way_high))
    assert assessment.safety_action == SAFETY_STOP


def test_diverged_estimate_lands_immediately():
    """An implausible velocity means the estimate failed; land.
    あり得ない速度は推定の破綻を意味するので着陸すること。"""
    assessment = Monitor().update(_stream(3, altitude_m=0.8, vel_n=99.0, vel_e=0.0, vel_d=0.0))
    assert assessment.position_estimate == "発散している"
    assert assessment.safety_action == SAFETY_LAND


def test_missing_fields_are_unknown_not_zero():
    """An absent measurement is reported unknown, never as a value of zero.
    測っていない項目は「不明」として報告され、0 とはみなされないこと。"""
    assessment = Monitor().update([Sample(t=0.0, altitude_m=0.8)])
    assert assessment.battery_level == "不明"
    assert assessment.horizontal_drift == "不明"
    assert assessment.ground_distance_sensor == "不明"


def test_implausible_tof_is_flagged_rather_than_used():
    """A ToF reading outside the sensor's plausible range is called out.
    ToF が妥当な範囲を外れたら、その旨を報告すること。"""
    beyond = DEFAULT_CONFIG.monitor.tof_max_m + 1.0
    assessment = Monitor().update(_stream(3, altitude_m=0.8, tof_m=beyond))
    assert assessment.ground_distance_sensor == "当てにならない値"


def test_the_battery_trend_works_from_a_percentage_only_source():
    """A source that reports no voltage still gets a battery trend.

    The trend is judged on volts, but not every link carries them: the
    140-byte v2 telemetry packet and the SILS STATE line do, while the
    10Hz Tello state string reports only a percentage. Since that
    percentage is a linear map of the voltage, it is inverted rather than
    left out -- otherwise the trend would go silently blind on exactly the
    link a fallback flight uses.

    電圧を報告しない入力源でも、電池の傾向が働くこと。

    傾向は電圧で判定するが、すべてのリンクが電圧を運ぶわけではない。140 バイトの
    v2 テレメトリと SILS の STATE 行は運ぶが、10Hz の Tello 状態文字列は百分率
    しか報告しない。その百分率は電圧の線形写像なので、捨てずに逆算する。
    そうしなければ、予備の飛行が使うまさにそのリンクで、傾向が静かに効かなく
    なってしまう。
    """
    window_s = DEFAULT_CONFIG.monitor.battery_trend_window_s
    settle_s = DEFAULT_CONFIG.monitor.battery_settle_s
    hold_s = DEFAULT_CONFIG.monitor.classification_hold_s
    monitor = Monitor(target_altitude_m=0.5)

    # A pack falling far faster than the sharp band needs, reported ONLY as
    # a percentage. The rate is set from the threshold rather than picked:
    # three times the drop the band asks for, so the classification is
    # unambiguous throughout and the debounce has a steady candidate to
    # hold. (A rate close to the threshold makes the raw classification
    # itself alternate, which the hold then correctly suppresses -- right
    # behaviour, but not what this test is about.)
    # 「急に低下」の区分が要求するよりはるかに速く落ちるパック。報告は百分率のみ。
    # 速度は閾値から決める（任意に選ばない）: 区分が求める低下の 3 倍にして、
    # 区分が終始一意になり、保持機構が安定した候補を持てるようにする。
    #（閾値ぎりぎりの速度では生の区分自体が交互になり、保持機構がそれを正しく
    # 抑える。正しい挙動だが、本試験の対象ではない。）
    volts_per_percent = 0.009           # 0.9 V spans 0-100% / 0.9V が 0〜100%
    drop_v = DEFAULT_CONFIG.monitor.battery_drop_fast_v * 3.0
    drop_pct_per_window = drop_v / volts_per_percent

    reported = ""
    total_s = settle_s + window_s + hold_s + 2.0
    fall_pct = drop_pct_per_window * (total_s / window_s)
    for step in range(int(total_s * 50)):
        elapsed = step / 50.0
        percent = 95.0 - fall_pct * (elapsed / total_s)
        reported = monitor.update([Sample(
            t=elapsed, altitude_m=0.5, flight_state="FLYING",
            battery_pct=percent, vel_n=0.0, vel_e=0.0, vel_d=0.0,
            roll=0.0, pitch=0.0,
        )]).battery_trend

    assert reported == BATTERY_TREND_SHARP, (
        f"a pack falling 30 points read as {reported!r} from a percentage-only source"
    )
