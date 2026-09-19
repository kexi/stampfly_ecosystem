"""
The rule of §4.13, checked mechanically: **this program does not impose a
limit stricter than the vehicle's own.**

The vehicle decides whether it may fly, and this package's job is the part
the vehicle does not do -- landing when the PC goes quiet, the wall ahead,
the trend over a window. Whenever a PC-side number shadows a vehicle-side
one, the PC-side number must be at least as permissive, or the program
refuses flights the aircraft was willing to make. That is not a theoretical
failure: a `battery_min_v` of 3.85 V did exactly that, turning away most of
a cell's usable range while the vehicle's own lamp reported it fit to fly.

§4.13 の原則を機械的に検査する: **この処理は、機体自身より厳しい制限を課さない。**

飛んでよいかを決めるのは機体であり、本パッケージの仕事は機体がやらないほう ——
PC が黙ったときの着陸、前方の壁、窓を通した傾向 —— である。PC 側の数値が機体側の
数値に重なるときは、PC 側が少なくとも同じだけ緩くなければならない。さもなければ、
機体が応じる気のある飛行を拒むことになる。これは机上の失敗ではない。3.85V の
`battery_min_v` がまさにそれを行い、機体自身のランプが飛行可能を示しているのに、
セルの使える範囲の大半を門前払いしていた。

**The firmware's values are read from its source, not copied into this
file.** A test that restates both sides agrees with itself for ever: it
would still pass on the day someone retunes `position.stick_vel` and the
PC-side ceiling silently becomes the stricter of the two. So the C++ is
parsed, and a value that can no longer be found fails the test rather than
being skipped -- a check that quietly stops checking is worse than no check.

**ファーム側の値は、この場所に転記せず、ファームの原文から読む。** 両側を書き
写した試験は永遠に自分自身と一致する。誰かが `position.stick_vel` を調整し直し、
PC 側の上限が黙って厳しいほうになった日にも、その試験は通ってしまう。そこで C++
を解析し、値が見つからなくなった場合は読み飛ばさず不合格にする —— 黙って検査を
やめる検査は、検査が無いことより悪い。
"""

import re
from pathlib import Path

import pytest

from sfpilot.config import DEFAULT_CONFIG, real_config
from sfpilot.link import BATTERY_EMPTY_V, BATTERY_FULL_V
from sfpilot.preflight import VEHICLE_ARM_REFUSED_V, VEHICLE_AUTO_LAND_V

# The firmware sources these limits are read from, relative to the repository
# root (four levels up from this file: tests -> sfpilot -> lib -> root).
# ここで読むファームの出典。リポジトリ根からの相対（本ファイルから 4 つ上:
# tests → sfpilot → lib → 根）。
_ROOT = Path(__file__).resolve().parents[3]
_PARAMS_CPP = _ROOT / "firmware/vehicle/components/sf_core/params.cpp"
_API_TASK_CPP = _ROOT / "firmware/vehicle/tasks/api_task.cpp"
_FAILSAFE_HPP = _ROOT / "firmware/vehicle/components/sf_failsafe/include/failsafe.hpp"


def _param_default(name: str) -> float:
    """One `params.cpp` table row's default value.

    The table's rows read `{"position.stick_vel", ParamType::FLOAT, &var,
    0.4f, 0.05f, 2.0f, ...}` -- name, type, address, DEFAULT, min, max. The
    fourth field is taken, because that is the value the vehicle boots with
    when NVS holds nothing.

    `params.cpp` の表の 1 行から既定値を取る。

    行は `{"position.stick_vel", ParamType::FLOAT, &var, 0.4f, 0.05f, 2.0f, ...}`
    ——名前・型・アドレス・**既定**・最小・最大——の形である。4 番目を取る。NVS に
    何も無いとき機体が起動する値がそれだからである。
    """
    pattern = (r'\{\s*"' + re.escape(name) + r'"\s*,\s*ParamType::\w+\s*,'
               r'\s*&\w+\s*,\s*(-?[\d.]+)f?\s*,')
    return _one_float(_PARAMS_CPP, pattern, f'params.cpp param "{name}"')


def _constant(path: Path, name: str) -> float:
    """One `constexpr float kName = 1.23f;` from a firmware source.
    ファームの原文から `constexpr float kName = 1.23f;` を 1 つ取る。"""
    pattern = r'constexpr\s+float\s+' + re.escape(name) + r'\s*=\s*(-?[\d.]+)f?\s*;'
    return _one_float(path, pattern, f"{path.name} {name}")


def _field_default(path: Path, name: str) -> float:
    """One `float name = 1.23f;` struct field initialiser.
    構造体の項目初期化子 `float name = 1.23f;` を 1 つ取る。"""
    pattern = r'float\s+' + re.escape(name) + r'\s*=\s*(-?[\d.]+)f?\s*;'
    return _one_float(path, pattern, f"{path.name} {name}")


def _one_float(path: Path, pattern: str, what: str) -> float:
    """The single float `pattern` matches in `path`, or a failure saying so.

    A miss FAILS rather than skips. The whole value of this file is that it
    breaks when the firmware moves, and a miss is the loudest form of the
    firmware having moved.

    `path` の中で `pattern` が一致させる唯一の実数。見つからなければその旨で不合格。

    一致しなければ読み飛ばさず**不合格**にする。このファイルの価値はすべて、ファームが
    動いたときに壊れることにある。そして一致しないことは、ファームが動いたことの
    最もはっきりした形である。
    """
    if not path.exists():
        pytest.fail(f"firmware source missing: {path}（ファームの原文が無い）")
    found = re.findall(pattern, path.read_text(encoding="utf-8"))
    if len(found) != 1:
        pytest.fail(
            f"{what}: expected exactly one match, found {len(found)}. "
            f"ファームの書き方が変わった可能性がある。この試験の正規表現を "
            f"更新すること（値を転記してはならない）"
        )
    return float(found[0])


def _as_percent(voltage: float) -> float:
    """A pack voltage on the same 0-100 map the Monitor's bands use.
    Monitor の区分と同じ 0〜100 の写像で、パック電圧を表す。"""
    ratio = (voltage - BATTERY_EMPTY_V) / (BATTERY_FULL_V - BATTERY_EMPTY_V)
    return ratio * 100.0


# =============================================================================
# The quoted thresholds still say what the firmware says
# 引用したしきい値が、いまもファームの述べるとおりであること
# =============================================================================
def test_the_quoted_arm_threshold_matches_the_firmware():
    """`VEHICLE_ARM_REFUSED_V` is `safety.battery.usb_v`'s actual default.

    The preflight prints this number beside the voltage, telling the
    operator where the vehicle will refuse to arm. A stale copy would tell
    them the wrong thing about someone else's rule.

    `VEHICLE_ARM_REFUSED_V` が `safety.battery.usb_v` の実際の既定値であること。

    飛行前点検はこの数値を電圧の横に表示し、機体がどこで ARM を拒むかを操作者に
    伝える。写しが古ければ、他人の規則について誤ったことを伝えることになる。
    """
    assert VEHICLE_ARM_REFUSED_V == pytest.approx(
        _param_default("safety.battery.usb_v"), abs=1e-6)


def test_the_quoted_auto_land_threshold_matches_the_firmware():
    """`VEHICLE_AUTO_LAND_V` is `failsafe.hpp`'s `critical_battery_v`.
    `VEHICLE_AUTO_LAND_V` が `failsafe.hpp` の `critical_battery_v` であること。"""
    assert VEHICLE_AUTO_LAND_V == pytest.approx(
        _field_default(_FAILSAFE_HPP, "critical_battery_v"), abs=1e-6)


# =============================================================================
# No PC-side limit is stricter than the vehicle's
# PC 側のどの制限も、機体のものより厳しくないこと
# =============================================================================
def test_the_preflight_has_no_take_off_voltage_gate_at_all():
    """There is no `battery_min_v` to be stricter WITH.

    Pinned as an absence rather than as a value, because the fault this
    guards against was a value existing at all: any threshold here either
    duplicates one of the vehicle's three battery rules or overrides it, and
    the vehicle applies all three whatever this program believes.

    厳しくなる**ための** `battery_min_v` が存在しないこと。

    値ではなく**不在**として固定する。ここで防ぎたい過ちは、そもそも値が存在した
    ことだからである。ここに置くどんなしきい値も、機体が持つ 3 つの電池規則の
    いずれかの複製になるか、それを上書きするかであり、機体はこの処理が何を信じて
    いようと 3 つとも適用する。
    """
    assert not hasattr(DEFAULT_CONFIG.real, "battery_min_v")


def test_the_immediate_landing_band_is_not_above_the_vehicles_warning():
    """The band that lands without asking Jev sits at or below 3.4 V.

    Above the vehicle's own warning, this program would be ending flights
    the vehicle has not yet remarked on -- the aircraft reporting itself fit
    while the PC brings it down.

    Jev を待たずに着陸する区分が、3.4V 以下にあること。

    機体自身の警告より上にあれば、この処理は、機体がまだ何も言っていない飛行を
    終わらせていることになる。機体は自らを健全と報告しているのに、PC がそれを
    降ろしている状態である。
    """
    vehicle_warning_pct = _as_percent(_param_default("safety.battery.low_v"))

    assert DEFAULT_CONFIG.monitor.battery_danger_pct <= vehicle_warning_pct


def test_no_envelope_speed_is_stricter_than_the_vehicle_without_saying_so():
    """Every envelope ceiling is at or above the vehicle's, or documented.

    The horizontal ceiling must not sit below `position.stick_vel`, which
    is the speed an `rc` of full scale reaches: below it, this program
    would be holding the craft under a speed the craft itself allows.

    The vertical one is the single documented exception (`EnvelopeConfig`
    explains why the judging layer cannot keep up with a 0.5 m/s climb), so
    it is asserted as an exception rather than left unmentioned -- an
    undocumented exception and a bug look identical.

    飛行領域の各上限が、機体のもの以上であるか、さもなくば文書化されていること。

    水平の上限は `position.stick_vel` を下回ってはならない。それは `rc` の満舵が
    到達する速度であり、下回れば、この処理は機体自身が許す速度より下に機体を
    押さえていることになる。

    鉛直のほうは唯一の文書化された例外であり（0.5m/s の上昇に判断層が追随でき
    ない理由は `EnvelopeConfig` にある）、黙って放置せず例外として表明する ——
    文書化されていない例外と不具合は、見分けがつかないからである。
    """
    envelope = DEFAULT_CONFIG.envelope

    assert envelope.speed_max_mps >= _param_default("position.stick_vel")

    # The documented exception, pinned so that it stays deliberate: if the
    # firmware's climb rate is ever lowered to meet this, the assertion
    # flips and the exception should be removed rather than kept by habit.
    # 文書化された例外。意図したままであり続けるよう固定する。ファームの上昇速度が
    # これに合わせて下げられたら、この表明は反転する。そのときは惰性で残さず、
    # 例外そのものを外すべきである。
    firmware_climb = _param_default("altitude.climb_rate")
    assert envelope.climb_rate_max_mps < firmware_climb, (
        "altitude.climb_rate が下がったなら、EnvelopeConfig の例外は不要になる "
        "/ the documented climb exception is no longer needed"
    )


def test_the_instruction_move_range_is_the_vehicles_own():
    """`move_min_cm` / `move_max_cm` are `kMoveMinCm` / `kMoveMaxCm` exactly.

    These exist to catch a step the vehicle would refuse (`error out of
    range`) or silently clamp, on this side, where the operator can be told
    WHICH step was the problem. That only works while they are the same
    numbers: narrower and this program refuses moves the vehicle would fly;
    wider and the message it promises never appears.

    `move_min_cm` / `move_max_cm` が `kMoveMinCm` / `kMoveMaxCm` と厳密に一致する
    こと。

    これらは、機体が拒否する（`error out of range`）か黙ってクランプする手順を、
    どの手順が問題かを操作者に伝えられるこちら側で捕まえるために在る。それが
    成り立つのは同じ数値である間だけである。狭ければ、機体が飛ぶ移動をこの処理が
    拒むことになり、広ければ、約束したその案内がそもそも出ない。
    """
    instruction = DEFAULT_CONFIG.instruction

    assert instruction.move_min_cm == pytest.approx(
        _constant(_API_TASK_CPP, "kMoveMinCm"), abs=1e-6)
    assert instruction.move_max_cm == pytest.approx(
        _constant(_API_TASK_CPP, "kMoveMaxCm"), abs=1e-6)


def test_the_envelope_altitude_band_sits_inside_the_vehicles_api_clamp():
    """The judged altitude band is within the vehicle's 0.2-2.0 m clamp.

    Narrower is correct here and the opposite of the battery case: the
    vehicle CLAMPS silently at its own edges, so a band wider than the clamp
    would have the Monitor judging altitudes the vehicle will never reach.
    What must not happen is the band escaping the clamp, which would make
    the judging layer's target unreachable.

    判定に使う高度の帯域が、機体の 0.2〜2.0m のクランプの内側にあること。

    ここでは狭いことが正しく、電池の場合とは逆である。機体は自分の端で**黙って**
    クランプするので、クランプより広い帯域は、機体が決して到達しない高度を Monitor
    に判定させることになる。あってはならないのは帯域がクランプの外へ出ることで、
    そうなると判断層の目標が到達不能になる。
    """
    floor = _constant(_API_TASK_CPP, "kAltMinM")
    ceiling = _constant(_API_TASK_CPP, "kAltMaxM")

    for envelope in (DEFAULT_CONFIG.envelope, real_config().envelope):
        assert envelope.altitude_min_m >= floor
        assert envelope.altitude_max_m <= ceiling


def test_the_mission_arrival_tolerance_is_outside_the_vehicles_reach_radius():
    """Arrival is judged no tighter than the vehicle's own `kReachRadiusM`.

    Inside that radius the vehicle has already declared the move reached, so
    a tighter tolerance here would call a completed move "stopped short" --
    this program contradicting the aircraft about the aircraft's own move.

    到達の判定が、機体自身の `kReachRadiusM` より厳しくないこと。

    その半径の内側では、機体は既に「到達した」と宣言している。ここでより厳しい
    許容値を置けば、完了した移動を「手前で止まった」と呼ぶことになる —— 機体自身の
    移動について、この処理が機体と食い違うということである。
    """
    assert (DEFAULT_CONFIG.mission.arrival_tolerance_m
            >= _constant(_API_TASK_CPP, "kReachRadiusM"))
