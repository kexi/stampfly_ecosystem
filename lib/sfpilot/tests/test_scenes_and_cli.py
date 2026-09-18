"""
What the scene table and `sf pilot run`'s argument handling guarantee.

場面の表と `sf pilot run` の引数処理が保証すること。

These tests start no emulator: a scene is declarative (environment plus a
hook), and the argument checks must fail before anything is launched.
これらの試験はエミュレータを起動しない。場面は宣言的（環境変数とフック）で
あり、引数の検査は何かを起動するより前に落ちる必要があるためである。
"""

import argparse

import pytest

from sfcli.commands import pilot as pilot_cmd
from sfpilot.config import DEFAULT_CONFIG
from sfpilot.scenes import (
    BATTERY_DROP, DRIFT, NOMINAL, get_scene, scene_names, scene_table,
)


class _RecordingLink:
    """Records what a scene's hook does, without an emulator.
    場面のフックが何をするかを、エミュレータ無しで記録する。"""

    def __init__(self):
        self.voltages: list = []
        self.winds: list = []

    def set_battery_voltage(self, volts: float) -> None:
        self.voltages.append(volts)

    def set_wind(self, north_n: float, east_n: float, down_n: float) -> None:
        self.winds.append((north_n, east_n, down_n))


# =============================================================================
# The scene table / 場面の表
# =============================================================================

def test_every_offered_scene_exists_in_the_table():
    """The names the CLI offers and the table's keys are the same set.
    CLI が提示する名前と表のキーが一致すること。"""
    assert set(scene_names()) == set(scene_table())


def test_an_unknown_scene_names_the_valid_choices():
    """A typo is rejected with the list of what would have worked.
    綴り違いは、有効な選択肢を添えて拒否されること。"""
    with pytest.raises(KeyError) as excinfo:
        get_scene("battery-drop")   # hyphen, not underscore / ハイフン違い

    for name in scene_names():
        assert name in str(excinfo.value)


def test_the_nominal_scene_does_nothing_to_the_flight():
    """`nominal` injects no fault: no environment, no per-cycle hook.

    This is what makes it the control case -- anything the other scenes
    show has to come from what they add, not from the harness.
    `nominal` は故障を注入しない（環境変数もフックも無い）こと。

    これが対照条件になる根拠である。他の場面で見えたものは、仕掛けではなく
    その場面が足したものに由来する、と言えるようになる。
    """
    scene = get_scene(NOMINAL)

    assert scene.env == {}
    assert scene.drive is None


def test_the_battery_scene_walks_the_voltage_down_to_the_danger_band():
    """The voltage falls monotonically and ends below the danger threshold.

    Ending merely NEAR the threshold would make the scene rehearse
    approaching a decision instead of reaching one.
    電圧が単調に下がり、危険域のしきい値より下で終わること。

    しきい値の「近く」で終わると、判断に到達するのではなく近づくだけの
    予行になってしまう。
    """
    from sfpilot.link import battery_percent

    scene = get_scene(BATTERY_DROP)
    link = _RecordingLink()

    total_s = 30.0
    for step in range(0, 31):
        scene.drive(link, elapsed_s=float(step), total_s=total_s)

    assert link.voltages == sorted(link.voltages, reverse=True), "must fall, never rise"
    ends_in_danger = (battery_percent(link.voltages[-1])
                      <= DEFAULT_CONFIG.monitor.battery_danger_pct)
    assert ends_in_danger, (
        f"ends at {battery_percent(link.voltages[-1]):.1f}%, which is above the "
        f"{DEFAULT_CONFIG.monitor.battery_danger_pct}% danger threshold"
    )


def test_the_battery_scene_starts_from_a_healthy_pack():
    """The first voltage is above the low band, so the fall is visible.
    最初の電圧が「残り少ない」より上にあり、低下の過程が見えること。"""
    from sfpilot.link import battery_percent

    scene = get_scene(BATTERY_DROP)
    link = _RecordingLink()

    scene.drive(link, elapsed_s=0.0, total_s=30.0)

    assert battery_percent(link.voltages[0]) > DEFAULT_CONFIG.monitor.battery_low_pct


def test_the_drift_scene_applies_its_force_once_and_only_once():
    """The wind is a level, not a pulse: setting it repeatedly is waste.
    風は水準でありパルスではない。繰り返し設定するのは無駄であること。"""
    scene = get_scene(DRIFT)
    link = _RecordingLink()

    for step in range(20):
        scene.drive(link, elapsed_s=float(step), total_s=20.0)

    assert len(link.winds) == 1


def test_the_drift_scene_pushes_along_one_horizontal_axis():
    """The force is horizontal and on a single axis, so the drift has one
    unambiguous direction for the Monitor to name.
    力は水平で 1 軸のみ。Monitor が名前を付ける流れの向きが一意になること。"""
    scene = get_scene(DRIFT)
    link = _RecordingLink()

    scene.drive(link, elapsed_s=0.0, total_s=20.0)

    north_n, east_n, down_n = link.winds[0]
    assert down_n == 0.0, "a vertical push would be an altitude problem, not a drift"
    assert (north_n == 0.0) != (east_n == 0.0), "exactly one horizontal axis"


def test_the_drift_scene_reuses_the_existing_flow_scale_knob():
    """Drift is built from SILS mechanisms that already existed.
    流れは既存の SILS の機構から組み立てること。"""
    assert "SILS_EMU_FLOW_SCALE" in get_scene(DRIFT).env


# =============================================================================
# `sf pilot run` argument handling / `sf pilot run` の引数処理
# =============================================================================

def _parse(argv: list) -> argparse.Namespace:
    """Parse `sf pilot ...` arguments the way the CLI does.
    CLI と同じやり方で `sf pilot ...` の引数を解釈する。"""
    parser = argparse.ArgumentParser()
    pilot_cmd.register(parser.add_subparsers(dest="command"))
    return parser.parse_args(argv)


def test_run_without_sils_is_refused_before_anything_is_launched():
    """Real hardware is P5: `run` without --sils fails, it does not fly.
    実機は P5。--sils 無しの `run` は飛ばずに失敗すること。"""
    args = _parse(["pilot", "run", "--fake"])

    assert pilot_cmd.run_run(args) == 1


def test_run_defaults_to_the_nominal_scene():
    """Omitting --scene rehearses the no-fault case.
    --scene 省略時は故障なしの場合を予行すること。"""
    assert _parse(["pilot", "run", "--sils", "--fake"]).scene == NOMINAL


def test_run_rejects_a_scene_that_does_not_exist():
    """An unknown --scene is refused by the parser, not at flight time.
    存在しない --scene は飛行時ではなく引数解釈で拒否されること。"""
    with pytest.raises(SystemExit):
        _parse(["pilot", "run", "--sils", "--scene", "no_such_scene"])


def test_run_accepts_every_scene_the_table_offers():
    """Each name in the table is a value --scene will take.
    表にある各名前が --scene の値として通ること。"""
    for name in scene_names():
        assert _parse(["pilot", "run", "--sils", "--scene", name]).scene == name


def test_the_deadline_override_is_read_in_milliseconds():
    """--deadline-ms is taken as given; the conversion happens in the run.
    --deadline-ms はそのまま読み取り、変換は実行時に行うこと。"""
    assert _parse(["pilot", "run", "--sils", "--deadline-ms", "800"]).deadline_ms == 800.0


def test_the_deadline_defaults_to_the_configured_one():
    """With no override the run uses config.py's deadline, not a literal here.
    上書きが無ければ config.py の期限を使い、ここの直書き値は使わないこと。"""
    assert _parse(["pilot", "run", "--sils"]).deadline_ms is None
