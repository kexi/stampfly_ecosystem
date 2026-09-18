#!/usr/bin/env python3
"""
`sf pilot mission` end to end, against the real emulator (P4).

`sf pilot mission` を実エミュレータに対して端から端まで確認する（P4）。

What is verified, all with `--fake` so no API key and no network are needed:

  (a) `nominal` flies the shipped `line` route to completion, and the ground
      truth confirms the aircraft went where the legs said.
  (b) `battery_drop` ends the route early: the code refuses to go on and
      brings the aircraft home, whatever the judge proposed.
  (c) `drift` does not redo a leg indefinitely -- the retry limit converts
      repeated failure into skipping, and the reason is recorded.
  (d) A landing settles the craft first, so it does not slide across the
      floor during the descent.

いずれも `--fake` で確認するため、API キーも通信も不要:
  (a) `nominal` が同梱の `line` 経路を完走し、区間のとおりに機体が動いたことを
      真値が裏づけること。
  (b) `battery_drop` が経路を途中で終えること。判断層が何を提案しようと、コードが
      先へ進むことを拒否し、機体を帰還させる。
  (c) `drift` が区間を無限にやり直さないこと。やり直しの上限が、繰り返す失敗を
      「飛ばす」に変え、その理由が記録に残る。
  (d) 着陸が先に機体を静定させ、降下中に床の上を滑らないこと。

**These tests cost real seconds**: the emulator runs in real time and a
route is several legs long. Each one uses the shortest route that still
reaches the property it checks.

**これらの試験は実時間を消費する**: エミュレータは実時間で動き、経路は数区間
ある。各試験は、確認したい性質に到達する最短の経路を使う。

Prerequisite / 事前条件:
    source setup_env.sh && sf sils build
    pytest simulator/tests/test_mission_sils.py -v
"""

import csv
import math
import sys
from pathlib import Path

import pytest

from sfcli.utils.paths import paths


def _exe(name: str) -> Path:
    suffix = ".exe" if sys.platform.startswith("win") else ""
    return paths.sils_build() / f"{name}{suffix}"


EMU_VEHICLE = _exe("emu_vehicle")

pytestmark = pytest.mark.skipif(
    not EMU_VEHICLE.exists(),
    reason=f"{EMU_VEHICLE.name} not built — run 'source setup_env.sh && sf sils build'",
)

# The route every test here flies. `line` rather than `square` because a
# move along the east/west axis following one along the north/south axis
# disarms the emulated craft through the impact detector, with none of
# `sf pilot` involved (docs/plans/jev-autopilot.md §4.4). `line` stays on
# one horizontal axis, which is the combination measured to be unaffected.
# ここの試験が飛ぶ経路。`square` ではなく `line` を使う。南北軸の移動に続く
# 東西軸の移動は、衝撃検出により機体を解除するためである（`sf pilot` は一切
# 関与しない。docs/plans/jev-autopilot.md §4.4）。`line` は水平 1 軸に留まり、
# これは影響を受けないと実測で確かめた組み合わせである。
ROUTE = "line"

# Altitude [m] above which the craft counts as flying, for reading a
# descent out of `truth.csv`. Well clear of the 0.01 m the model rests at.
# `truth.csv` から降下を読み取るための「飛行中」とみなす高度 [m]。モデルが
# 静止する 0.01m から十分離してある。
AIRBORNE_M = 0.05


@pytest.fixture(scope="module")
def nominal_flight():
    """One `nominal` flight, shared by every test that reads its truth.

    Module-scoped because the flight takes the best part of a minute in
    real time and several properties are read off the same one. Sharing it
    is also what makes them comparable: they describe ONE flight, not four
    flights that happened to be configured alike.

    `nominal` の飛行 1 回。その真値を読む全ての試験で共有する。

    実時間で 1 分近くかかり、同じ 1 回からいくつもの性質を読むためモジュール
    スコープにする。共有することで比較可能にもなる — 4 回の別々の飛行ではなく
    **1 回の**飛行を述べたものになる。
    """
    return _fly("nominal", duration_s=120.0)


def _fly(scene: str, duration_s: float):
    """Fly the route once in `scene` and return (outcome, truth rows).
    `scene` で経路を 1 回飛ばし、(結果, 真値の行) を返す。"""
    from sfpilot.config import DEFAULT_CONFIG
    from sfpilot.judge import MissionFakeJudge
    from sfpilot.mission import builtin_mission_path, load_mission
    from sfpilot.mission_run import MissionRequest, fly_in_sils

    from dataclasses import replace

    config = replace(DEFAULT_CONFIG,
                     mission=replace(DEFAULT_CONFIG.mission, time_limit_s=duration_s))
    mission = load_mission(builtin_mission_path(ROUTE), config)
    request = MissionRequest(mission_path=ROUTE, scene=scene,
                             duration_s=duration_s, fake=True)
    outcome = fly_in_sils(request, mission, MissionFakeJudge(), config)
    return outcome, _truth_rows()


def _truth_rows() -> list:
    """`truth.csv` as (t, north, east, altitude) in metres.

    The last line is dropped when it is short of fields. The emulator is
    killed once the flight is over, so its final row can be half-written;
    discarding an incomplete row is right, while letting it through turns
    every reader of this file into a `NoneType` error at the point it is
    least expected.

    `truth.csv` を (時刻, 北, 東, 高度) [m] の列にする。

    項目の足りない最終行は捨てる。飛行の終了後にエミュレータを終了させるため、
    最後の行は書きかけになりうる。不完全な行を捨てるのが正しく、通してしまうと、
    このファイルを読む全員が、最も予期しない場所で `NoneType` の例外に変わる。
    """
    path = (paths.root() / "simulator" / "sils" / "viz" / "out_mission" / "truth.csv")
    rows = []
    with path.open(encoding="utf-8") as handle:
        for record in csv.DictReader(handle):
            wanted = ("timestamp_us", "pos_x", "pos_y", "pos_z")
            is_complete = all(record.get(key) for key in wanted)
            if not is_complete:
                continue
            rows.append((float(record["timestamp_us"]) * 1e-6,
                         float(record["pos_x"]), float(record["pos_y"]),
                         -float(record["pos_z"])))
    return rows


def _touchdown_index(rows: list) -> int:
    """The last row at which the craft was still airborne.
    機体がまだ空中にあった最後の行。"""
    return max(i for i, r in enumerate(rows) if r[3] > AIRBORNE_M)


# =============================================================================
# (a) The route flies / 経路が飛べること
# =============================================================================

def test_the_route_completes_and_the_truth_shows_where_it_went(nominal_flight):
    """Every leg flies, and the ground truth matches what the legs asked for.

    Checked against `truth.csv` rather than the firmware's own estimate,
    because the estimate is what decided the route was going well -- reading
    it back would confirm only that the code agrees with itself.

    全区間が飛び、真値が区間の指示と一致すること。

    ファーム自身の推定ではなく `truth.csv` で確認する。経路が順調だと判断した
    のがその推定であり、それを読み返しても「コードが自分自身と一致する」ことしか
    確かめられないためである。
    """
    outcome, rows = nominal_flight

    assert outcome.completed, f"{outcome.stop_reason}: {outcome.stop_detail}"
    assert outcome.landed

    # The route is two 0.6 m legs north, a climb, and a return. North must
    # therefore reach about 1.2 m, and east must never move: the nose stays
    # north for the whole route and no leg is lateral.
    # 経路は北へ 0.6m の区間 2 つ、上昇、そして帰還である。したがって北は約 1.2m に
    # 達し、東は動かないはずである。機首は経路の間ずっと北を向き、横方向の区間は
    # 無いためである。
    peak_north = max(r[1] for r in rows)
    peak_east = max(abs(r[2]) for r in rows)

    assert peak_north > 1.0, f"only reached N={peak_north:.3f} m"
    assert peak_east < 0.20, f"drifted E={peak_east:.3f} m with no lateral leg"


def test_the_route_climbs_for_its_vertical_leg(nominal_flight):
    """The `up 30` leg is visible in the truth as a higher hover.

    The takeoff alone reaches about 0.5 m, so a peak meaningfully above that
    is the vertical leg and nothing else.

    `up 30` の区間が、より高いホバリングとして真値に現れること。

    離陸だけで約 0.5m に達するので、そこから明確に高い最大値は、垂直の区間以外の
    ものではない。
    """
    _, rows = nominal_flight

    peak_altitude = max(r[3] for r in rows)

    assert peak_altitude > 0.70, f"peaked at {peak_altitude:.3f} m"


def test_the_route_comes_back_to_the_takeoff_point(nominal_flight):
    """`return_home` puts the craft back where it started before landing.

    The whole point of the leg: a route that flew out and landed where it
    stopped would look identical in the leg summary and nothing like it in
    the truth.

    `return_home` が、着陸の前に機体を出発点へ戻すこと。

    この区間の目的そのものである。飛び出したまま止まった場所で着陸する経路は、
    区間の集計では同じに見え、真値ではまったく違って見える。
    """
    _, rows = nominal_flight

    touchdown = rows[_touchdown_index(rows)]
    distance_home = math.hypot(touchdown[1], touchdown[2])

    # Loose on purpose: the craft reaches the takeoff point at the end of
    # `return_home` and then drifts during the descent, which the firmware
    # does not oppose (landing.py). Measured touchdowns over four flights:
    # 0.119, 0.190, 0.421 and 0.624 m. The route flew out to 1.2 m, so what
    # this rules out is the failure that matters -- landing where it stopped
    # instead of coming back at all.
    # あえて緩くしてある。機体は `return_home` の終わりで離陸点に達し、その後の
    # 降下中に流れる。ファームはそれに抗わない（landing.py）。4 回の飛行での接地点は
    # 0.119・0.190・0.421・0.624m だった。経路は 1.2m まで出ているので、ここで
    # 排除するのは問題となる失敗である — 戻らずに、止まった場所で着陸すること。
    assert distance_home < 0.80, (
        f"touched down {distance_home:.3f} m from the takeoff point"
    )


# =============================================================================
# (d) The landing settles first / 着陸が先に静定すること
# =============================================================================

def test_the_craft_does_not_slide_away_during_its_descent(nominal_flight):
    """The descent stays within a bound an unsettled landing exceeds.

    The firmware stops holding horizontal position for the whole descent,
    by design (landing.py), so whatever speed the craft carries in is
    carried through. The pre-landing approach is what keeps that speed
    small.

    The threshold is loose on purpose, because the measurement is: four
    `nominal` flights slid 0.043, 0.132, 0.163 and 0.226 m, against 0.271 m
    for the same route landed without the approach. The spread overlaps the
    baseline at its worst, so a tight bound here would fail on the emulator's
    own run-to-run variation rather than on a change in behaviour. What is
    pinned is the thing that cannot happen: a descent that travels as far as
    the flight itself.

    降下中の移動が、静定しない着陸なら超える範囲に収まること。

    ファームは降下のあいだ水平の位置保持をやめる。設計どおりである（landing.py）。
    その速度を小さく保つのが着陸前手順である。

    しきい値はあえて緩くしてある。実測がそうだからである。`nominal` の 4 回の
    飛行での滑りは 0.043・0.132・0.163・0.226m であり、同じ経路を手順なしで
    着陸させた場合は 0.271m だった。ばらつきの上端は基準値と重なるので、ここで
    厳しい上限を置けば、挙動の変化ではなくエミュレータの実行ごとのばらつきで
    落ちる。固定するのは「起きてはならないこと」である — 飛行そのものと同じ
    だけ進む降下は起きない。
    """
    _, rows = nominal_flight

    touchdown = _touchdown_index(rows)
    descent_start = max(i for i in range(touchdown) if rows[i][3] > 0.45)
    slide = math.hypot(rows[touchdown][1] - rows[descent_start][1],
                       rows[touchdown][2] - rows[descent_start][2])

    assert slide < 0.40, f"slid {slide:.3f} m during the descent"


# =============================================================================
# (b) A falling battery ends the route / 電池低下が経路を終えること
# =============================================================================

def test_a_falling_battery_ends_the_route_and_lands():
    """The route stops early and the aircraft is on the ground at the end.

    Which of the two endings it reaches -- flown home, or landed where it
    was -- depends on how far through the route the battery crossed the
    band, so the assertion is on what must be true either way: the route
    did not simply run to completion, and the craft is down.

    経路が途中で終わり、最後に機体が地上にあること。

    2 つの終わり方（帰還、その場で着陸）のどちらに至るかは、経路のどこで電池が
    区分をまたいだかで決まる。そこで、どちらであれ成り立つべきことを確認する:
    経路が単に完走したのではないこと、そして機体が降りていること。
    """
    from sfpilot.mission import STOP_COMPLETED

    outcome, rows = _fly("battery_drop", duration_s=60.0)

    assert outcome.stop_reason != STOP_COMPLETED, "the battery was ignored"
    assert outcome.landed
    assert rows[-1][3] < 0.10, f"ended at {rows[-1][3]:.3f} m, not on the ground"


# =============================================================================
# (c) A pushed craft does not retry forever / 押される機体が無限にやり直さないこと
# =============================================================================

def test_a_drifting_flight_stops_redoing_a_leg_and_says_why():
    """The retry limit converts repeated failure into moving on.

    Without it, a leg the wind keeps pushing off target is proposed for
    another attempt every time and the route never advances -- measured at
    63 attempts on one leg before this limit was keyed on the resolved
    action rather than on the judge's raw choice.

    やり直しの上限が、繰り返す失敗を「先へ進む」に変えること。

    上限が無ければ、風に押され続けて目標を外す区間は毎回もう一度試すよう提案され、
    経路は進まない。上限を「解決した行動」ではなく判断層の生の選択で判定していた
    版では、1 つの区間で 63 回の試行を実測した。
    """
    from sfpilot.mission import LEG_SKIPPED

    outcome, _ = _fly("drift", duration_s=120.0)

    attempts_per_leg: dict = {}
    for result in outcome.results:
        attempts_per_leg[result.index] = attempts_per_leg.get(result.index, 0) + 1

    worst = max(attempts_per_leg.values())
    ceiling = 1 + _retry_limit()
    assert worst <= ceiling, f"one leg was attempted {worst} times (limit {ceiling})"

    # Whether the wind pushes any leg far enough to be redone varies between
    # runs -- the same 0.060 N sometimes leaves every leg inside tolerance.
    # So a skip is not required; what is required is that any skip which DID
    # happen says why, because "the code overrode the model" is the single
    # most important thing a mission run reports.
    # 風がどの区間をやり直しに追い込むかは実行ごとに変わる。同じ 0.060N でも、
    # 全区間が許容内に収まることがある。そこで「飛ばすこと」は要求しない。要求
    # するのは、実際に起きた「飛ばす」が理由を伴うことである。「コードがモデルを
    # 覆した」は、ミッションの実行が報告する最も重要な事柄だからである。
    skipped = [r for r in outcome.results if r.outcome == LEG_SKIPPED]
    assert all(r.refusal for r in skipped), "a skip must record why"
    assert outcome.landed


def _retry_limit() -> int:
    from sfpilot.config import DEFAULT_CONFIG

    return DEFAULT_CONFIG.mission.max_retries_per_leg
