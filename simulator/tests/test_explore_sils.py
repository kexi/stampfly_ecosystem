#!/usr/bin/env python3
"""
`sf pilot explore` against the real emulator, while the sweep is DISABLED.

掃引を**無効にしたまま**、`sf pilot explore` を実エミュレータに対して確認する。

What is verified here is deliberately narrow, and the narrowness is the
point. The sweep cannot be flown: in SILS the aircraft falls out of the
air when it yaws at all -- from a 0.5 m hover, `cw 90`, `cw 45` and
`cw 30` each reach the ground in about 1.3 s with a 4.7-5.3 G impact and
a disarm, and an `rc` yaw rate as low as 0.1 rad/s sinks just the same
(docs/plans/jev-autopilot.md §4.9.1, §4.10). So there is no flight of the
manoeuvre to test, and writing one would produce a test whose evidence is
a crash, or one that hides the crash by climbing first -- which §4.3
rules out.

What CAN be established without flying it is that the refusal is real:
that `sf pilot explore` with the feature off commands nothing, launches
nothing and lands nothing. That is what this file checks, and it is the
one property that has to hold on the day someone flips the switch and
finds out whether the release condition was really met.

ここで確認することは意図して狭く、その狭さこそが要点である。掃引は飛ばせない。
SILS では機体がヨー回転しただけで落下する —— 高度 0.5m のホバリングから
`cw 90`・`cw 45`・`cw 30` のいずれでも約 1.3 秒で接地し、4.7〜5.3G の衝撃と解除に
至る。`rc` のヨー速度を 0.1 rad/s まで落としても同じように沈下する
（docs/plans/jev-autopilot.md §4.9.1・§4.10）。したがって試験すべき「操作の飛行」
は存在せず、書けば、証拠が墜落である試験か、先に上昇して墜落を隠す試験のどちらか
になる。後者は §4.3 が禁じている。

飛ばさずに確かめられるのは「拒否が本物である」ことである。機能が無効のとき
`sf pilot explore` は何も指令せず、何も起動せず、何も着陸させない。本ファイルが
確認するのはそれであり、誰かが切り替えを有効にして解除条件が本当に満たされたかを
確かめるその日に、成り立っていなければならない唯一の性質である。

Prerequisite / 事前条件:
    source setup_env.sh && sf sils build
    SF_SILS_SKIP_FRESHNESS_CHECK=1 pytest simulator/tests/test_explore_sils.py -v
"""

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

# The scene the sweep is FOR: a wall 1.0 m ahead and one along the east
# side, with the west left open (`scenes.py`). Named here so that the day
# the feature is enabled, this file already points at the situation the
# release condition is checked in.
# 掃引が「何のためのものか」を示す場面。北 1.0m の壁と東側の壁があり、西だけが
# 開いている（`scenes.py`）。ここで名前にしておくのは、機能が有効になるその日に、
# 本ファイルが既に、解除条件を確かめるべき状況を指しているようにするためである。
SCENE = "dead_end"


def _args(**overrides):
    """The parsed arguments `sf pilot explore --sils --scene dead_end --fake`
    produces, as an object the command reads.

    Built through the real parser rather than hand-assembled, so a renamed
    or removed option fails here instead of being silently absent.

    `sf pilot explore --sils --scene dead_end --fake` が作る引数を、コマンドが
    読む形で返す。

    手で組み立てず実際のパーサを通す。選択肢の改名・削除が、黙って欠けるのでは
    なくここで失敗するようにするためである。
    """
    import argparse

    from sfcli.commands import pilot

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    pilot.register(subparsers)
    args = parser.parse_args(
        ["pilot", "explore", "--sils", "--scene", SCENE, "--fake", "--yes"]
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_explore_refuses_and_never_reaches_the_emulator(monkeypatch, capsys):
    """With the sweep off, `sf pilot explore` flies nothing and says why.

    The refusal has to happen BEFORE the emulator is launched, which is
    what makes it cost nothing: no process, no takeoff, and so nothing
    left in the air to land. The launch is replaced with a failure here,
    so that reaching it at all fails the test rather than quietly starting
    a flight the feature is not supposed to allow.

    掃引が無効のとき、`sf pilot explore` は何も飛ばさず、理由を述べること。

    拒否はエミュレータの起動**より前**に起こる必要がある。それがこの拒否の代償を
    無くしている（プロセスも離陸も無く、したがって空中に残るものも無い）。ここでは
    起動処理を失敗に差し替え、そこへ到達すること自体が試験の失敗になるようにする。
    機能が許していないはずの飛行を、黙って始めないためである。
    """
    from sfcli.commands import pilot as pilot_command
    from sfcli.commands import sils as sils_command
    from sfpilot.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG.explore.enabled is False, (
        "this test is about the DISABLED path; enabling the sweep needs the "
        "release condition of docs/plans/jev-autopilot.md §4.9.1 to be met"
    )

    def _must_not_launch(*args, **kwargs):
        raise AssertionError(
            "the emulator was launched for a sweep that is disabled — the "
            "refusal must happen before anything is started"
        )

    monkeypatch.setattr(sils_command, "launch_realtime_emu", _must_not_launch)

    exit_code = pilot_command.run_explore(_args())

    assert exit_code == 1, (
        "a refused sweep must not report success: the operator asked for a "
        "look around and did not get one"
    )
    output = capsys.readouterr().out
    assert "無効" in output, "the refusal must say the feature is off"
    assert "§4.11" in output or "jev-autopilot" in output, (
        "the refusal must point at the measurement it rests on"
    )


def test_the_refusal_names_the_release_condition(capsys):
    """The reason shown carries what would have to be true to lift it.

    A refusal that only said "disabled" would leave the next person to
    rediscover the measurement. This one names the condition -- a `cw 90`
    from a 0.5 m hover that does not fall -- and where it is recorded.

    表示される理由が、解除するには何が成り立てばよいかを携えていること。

    「無効です」とだけ言う拒否は、次の人に実測をやり直させる。ここでの拒否は
    条件を名指しする（高度 0.5m のホバリングからの `cw 90` が落下しないこと）。
    そして、それがどこに記録されているかも述べる。
    """
    from sfcli.commands import pilot as pilot_command

    pilot_command.run_explore(_args())

    output = capsys.readouterr().out
    assert "cw 90" in output
    assert "解除条件" in output
    assert "#12" in output, "the backlog item the fix depends on is named"


def test_the_pocket_route_still_loads_while_the_sweep_is_off():
    """The shipped route is valid even though its `explore` leg cannot fly.

    A route that failed to load would hide the fact that everything except
    the manoeuvre itself is finished and connected. It loads, its legs
    check against the envelope, and the sweep leg is there waiting.

    掃引が無効でも、同梱の経路が読み込めること。

    読み込めない経路は、「操作そのもの以外はすべて完成し繋がっている」という事実を
    隠してしまう。経路は読み込め、区間は飛行領域の検査を通り、掃引の区間はそこで
    待っている。
    """
    from sfpilot.mission import STEP_EXPLORE, load_mission, resolve_mission_path

    mission = load_mission(resolve_mission_path("explore_pocket"))

    assert any(leg.step.verb == STEP_EXPLORE for leg in mission.legs)
    assert mission.total > 1, "the route has ordinary legs besides the sweep"
