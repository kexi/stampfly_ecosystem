"""
test_monitor.py - numbers become words, trends need duration, and the
immediate safety rules act without asking anyone.
test_monitor.py - 数値が言葉になること、傾向には継続時間が要ること、
即時安全則が誰にも問わずに働くこと。
"""

from sfpilot.config import DEFAULT_CONFIG
from sfpilot.link import Sample
from sfpilot.monitor import Monitor, SAFETY_LAND, SAFETY_NONE, SAFETY_STOP


def _stream(count, dt=0.02, **fields):
    """A run of identical samples at 50Hz. / 50Hz の同一サンプル列。"""
    return [Sample(t=i * dt, **fields) for i in range(count)]


def test_altitude_is_reported_as_a_band_not_a_number():
    """Altitude is classified relative to the target, in words.
    高度は目標に対する区分（言葉）で報告されること。"""
    monitor = Monitor(target_altitude_m=0.8)
    assert monitor.update(_stream(3, altitude_m=0.81)).altitude == "目標どおり"
    assert monitor.update(_stream(3, altitude_m=1.30)).altitude == "目標より高い"
    assert monitor.update(_stream(3, altitude_m=0.30)).altitude == "目標より低い"


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
    包絡を大きく外れた高度は、待たずに停止すること。"""
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
