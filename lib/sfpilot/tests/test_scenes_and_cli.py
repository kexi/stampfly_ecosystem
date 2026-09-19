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


def test_the_drift_scene_is_wind_and_nothing_else():
    """Drift is the Plant's wind hook alone, with no environment override.

    An earlier version also set SILS_EMU_FLOW_SCALE, which does not reach
    the flying path at all (only `Plant::flow()` reads it, and only the
    plant smoke test calls that). Pinning the scene to an empty environment
    keeps a knob that does nothing from being reintroduced as though it did.

    流れが Plant の wind フックだけで構成され、環境変数の上書きを持たないこと。

    以前の版は SILS_EMU_FLOW_SCALE も設定していたが、これは飛行経路に一切
    届かない（読むのは `Plant::flow()` だけで、それを呼ぶのは plant の smoke
    試験だけ）。環境変数が空であることを固定して、効かないノブが効くものとして
    再び持ち込まれるのを防ぐ。
    """
    scene = get_scene(DRIFT)

    assert scene.env == {}
    assert scene.drive is not None, "the wind hook is what makes this scene"


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


# =============================================================================
# `sf pilot say` argument handling / `sf pilot say` の引数処理
# =============================================================================

def test_say_without_sils_is_refused_before_anything_is_launched():
    """Real hardware is P5: `say` without --sils fails, it does not fly.
    実機は P5。--sils 無しの `say` は飛ばずに失敗すること。"""
    args = _parse(["pilot", "say", "--fake", "前に進んで"])

    assert pilot_cmd.run_say(args) == 1


def test_say_with_no_instruction_asks_for_one():
    """An empty `say` is refused rather than flying a default.
    指示の無い `say` は、既定の飛行をせずに拒否されること。"""
    args = _parse(["pilot", "say", "--sils", "--fake"])

    assert pilot_cmd.run_say(args) == 1


def test_a_dry_run_translates_without_flying(capsys):
    """--dry-run prints the steps and launches no emulator.

    This is the safe way to see what an instruction became, and it must
    work without --yes: nothing moves, so there is nothing to confirm.

    --dry-run は手順を表示し、エミュレータを起動しないこと。

    指示が何になったかを安全に確かめる手段であり、--yes 無しで動く必要が
    ある。何も動かない以上、確認すべきものが無いからである。
    """
    args = _parse(["pilot", "say", "--sils", "--fake", "--dry-run",
                   "上がって前に進んで戻ってきて"])

    assert pilot_cmd.run_say(args) == 0
    printed = capsys.readouterr().out
    assert "forward" in printed and "land" in printed


def test_a_non_interactive_session_does_not_fly_without_yes(monkeypatch):
    """Silence is not consent: with no terminal to confirm at, nothing flies.

    pytest runs without a tty, which is exactly the situation this guards:
    a script or a CI job must not be able to launch a flight by omitting
    an answer.

    無言は同意ではない。確認する端末が無ければ何も飛ばさないこと。

    pytest は tty 無しで動くので、まさにこの状況である。スクリプトや CI が
    「答えないこと」で飛行を始められてはならない。
    """
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    args = _parse(["pilot", "say", "--sils", "--fake", "前に進んで"])

    assert pilot_cmd.run_say(args) == 1


def test_say_defaults_to_appending_a_landing():
    """Without --no-auto-land the sequence is closed with a landing.
    --no-auto-land が無ければ、列は着陸で閉じられること。"""
    assert _parse(["pilot", "say", "--sils", "x"]).auto_land is True


def test_no_auto_land_is_switchable_off():
    """--no-auto-land is how an operator keeps the aircraft hovering.
    --no-auto-land は、機体を浮かせたままにするための指定であること。"""
    assert _parse(["pilot", "say", "--sils", "--no-auto-land", "x"]).auto_land is False


def test_the_shipped_eval_cases_parse_and_carry_expectations():
    """The case file `--eval` documents is loadable and complete.

    A case file that had drifted out of shape would only be discovered
    when someone spent API credit running it, so its structure is checked
    here without a key.

    `--eval` が案内する事例ファイルが読み込め、期待を備えていること。

    形が崩れた事例ファイルは、誰かが API の費用をかけて実行したときにしか
    見つからない。そこで構造だけをキー無しでここで確かめる。
    """
    from pathlib import Path

    case_file = Path(__file__).with_name("say_eval_cases.yaml")
    cases, error = pilot_cmd._load_cases(str(case_file))

    assert cases is not None, error
    assert len(cases) >= 10, f"only {len(cases)} cases"
    for case in cases:
        assert case.get("instruction"), case
        has_expectation = case.get("expect_steps") or case.get("expect_refusal")
        assert has_expectation, f"no expectation for {case['instruction']}"


# =============================================================================
# `sf pilot mission` argument handling / `sf pilot mission` の引数処理
# =============================================================================

def test_mission_without_sils_is_refused_before_anything_is_launched():
    """Real hardware is P5: `mission` without --sils fails, it does not fly.
    実機は P5。--sils 無しの `mission` は飛ばずに失敗すること。"""
    args = _parse(["pilot", "mission", "square", "--fake", "--yes"])

    assert pilot_cmd.run_mission(args) == 1


def test_a_mission_dry_run_checks_the_route_without_flying(capsys):
    """--dry-run loads and prints the route and launches no emulator.

    This is how an operator sees what a route file became -- including the
    envelope refusal -- before anything is in the air. It must work without
    --yes: nothing moves, so there is nothing to consent to.

    --dry-run は経路を読んで表示し、エミュレータを起動しないこと。

    経路ファイルが何になったか（飛行領域による拒否を含む）を、何も空中に無いうちに
    操作者が確かめる手段である。--yes 無しで動く必要がある。何も動かない以上、
    同意すべきものが無いからである。
    """
    args = _parse(["pilot", "mission", "square", "--sils", "--fake", "--dry-run"])

    assert pilot_cmd.run_mission(args) == 0
    printed = capsys.readouterr().out
    assert "forward 60" in printed
    assert "takeoff" in printed and "land" in printed


def test_a_mission_dry_run_does_not_need_sils_either():
    """Checking a route is not flying, so --sils is not required for it.
    経路の確認は飛行ではないので、--sils を要求しないこと。"""
    args = _parse(["pilot", "mission", "square", "--fake", "--dry-run"])

    assert pilot_cmd.run_mission(args) == 0


def test_a_route_that_cannot_be_flown_is_reported_and_nothing_starts(tmp_path):
    """A refused route exits non-zero without reaching the confirmation.
    拒否された経路は、確認に達する前に非ゼロで終わること。"""
    import json

    far = DEFAULT_CONFIG.envelope.radius_max_m * 100.0 + 100.0
    path = tmp_path / "too_far.json"
    path.write_text(json.dumps({"legs": [
        {"verb": "takeoff"}, {"verb": "forward", "amount": far},
    ]}), encoding="utf-8")
    args = _parse(["pilot", "mission", str(path), "--sils", "--fake", "--yes"])

    assert pilot_cmd.run_mission(args) == 1


def test_a_non_interactive_session_does_not_fly_a_mission_without_yes(monkeypatch):
    """Silence is not consent, for a route as much as for an instruction.

    pytest runs without a tty, which is exactly the situation this guards:
    a script or a CI job must not launch a flight by omitting an answer.

    無言は同意ではない。経路についても指示と同じであること。

    pytest は tty 無しで動くので、まさにこの状況である。スクリプトや CI が
    「答えないこと」で飛行を始められてはならない。
    """
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    args = _parse(["pilot", "mission", "square", "--sils", "--fake"])

    assert pilot_cmd.run_mission(args) == 1


def test_a_mission_defaults_to_the_nominal_scene():
    """Omitting --scene flies the route with no fault injected.
    --scene 省略時は、故障を注入せず経路を飛ぶこと。"""
    assert _parse(["pilot", "mission", "square", "--sils"]).scene == NOMINAL


def test_a_mission_accepts_every_scene_the_table_offers():
    """Each scene name is a value --scene will take for a mission too.
    各場面の名前が、ミッションの --scene の値としても通ること。"""
    for name in scene_names():
        args = _parse(["pilot", "mission", "square", "--sils", "--scene", name])
        assert args.scene == name


def test_the_mission_time_limit_defaults_to_the_configured_one():
    """With no override the route uses config.py's limit, not a literal here.
    上書きが無ければ config.py の上限を使い、ここの直書き値は使わないこと。"""
    assert _parse(["pilot", "mission", "square", "--sils"]).duration is None
