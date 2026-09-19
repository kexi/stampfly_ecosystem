"""
sfpilot.mission_run - staging a mission flight, and reporting what it did.
sfpilot.mission_run - ミッション飛行の段取りと、その結果の報告。

`sf pilot mission` is argument parsing and printing; everything it does
between those two is here. The split follows the repository's rule that
`lib/sfcli/commands/` stays a command surface -- the CLI file had grown to
nearly a thousand lines by P3, and a fourth subcommand's flight logic
belongs with the other flight logic, not next to `argparse`.

`sf pilot mission` は引数の解釈と表示を担い、その間に行うことはすべてここに
置く。この分け方はリポジトリの方針に従う — `lib/sfcli/commands/` はコマンドの
入口に留める。P3 の時点で CLI のファイルは千行近くに育っており、4 つ目の
サブコマンドの飛行の処理は、`argparse` の隣ではなく他の飛行の処理と同じ場所に
属する。
"""

import time
from dataclasses import dataclass

from .config import DEFAULT_CONFIG
from .mission import (
    LEG_FLOWN, LEG_NOT_REACHED, LEG_REDONE, LEG_SKIPPED,
    MissionError, fly_mission, load_mission, resolve_mission_path,
)


class SilsUnavailable(RuntimeError):
    """The emulator could not be started. / エミュレータを起動できなかった。"""


@dataclass
class MissionRequest:
    """Everything a mission flight needs, gathered from the command line.

    Passed as one object so this module has no dependency on argparse: the
    same flight can then be staged from a test, which is how the argument
    handling is checked without launching an emulator.

    ミッション飛行に必要なもの一式。コマンドラインから集めたもの。

    1 つのオブジェクトで渡し、本モジュールが argparse に依存しないようにする。
    同じ飛行を試験からも組み立てられるようになり、エミュレータを起動せずに
    引数の処理を確認できる。
    """

    mission_path: str
    scene: str = "nominal"
    duration_s: float = 180.0
    fake: bool = False


def prepare(path: str, config=DEFAULT_CONFIG):
    """Load and check a mission before anything is launched.

    Separate from flying it so `--dry-run` and the confirmation prompt both
    see the checked route: the envelope refusal has to reach the operator
    while the aircraft is on the ground, which is the same rule
    `instruction.check_envelope` follows.

    何かを起動する前にミッションを読み込み、検査する。

    飛ばすことと分けてあるのは、`--dry-run` と実行前の確認の双方が、検査済みの
    経路を見られるようにするためである。包絡による拒否は、機体が地上にあるうちに
    操作者へ届く必要がある。`instruction.check_envelope` と同じ規則である。
    """
    return load_mission(resolve_mission_path(path), config)


def fly_in_sils(request: MissionRequest, mission, judge, config=DEFAULT_CONFIG,
                trace=None, on_event=None, recording=None, on_cycle=None,
                on_decisions=None):
    """Launch the emulator, settle it, fly the mission, shut it down.

    The launch is `sfcli.commands.sils`'s, shared with `sf sils fly` and
    `sf pilot run`. Assembling it separately here would let a decision
    tested in one of them fail to reproduce in another.

    `recording` says where the flight-log bundle lands; the caller passes
    one when it wants the bundle's path back (to print the video command,
    or to name it in the trace), and omitting it records to a fresh dated
    directory all the same -- a flight is never left unrecorded.

    エミュレータを起動・静定させ、ミッションを飛ばし、終了させる。

    起動処理は `sfcli.commands.sils` のもので、`sf sils fly`・`sf pilot run` と
    共有している。ここで別に組み立てると、一方で試した判断が他方で再現しなく
    なる。

    `recording` はフライトログ一式の置き場所を表す。束のパスを受け取りたい側
    （動画のコマンドを表示する、記録に書き残す）が渡す。省略しても新しい日時の
    ディレクトリに記録する — 飛行が記録されないままになることはない。
    """
    from sfcli.commands.sils import (
        RealtimeEmuUnavailable, launch_realtime_emu, realtime_emu_env,
    )

    from .link import SilsLink
    from .recording import FlightRecording
    from .scenes import get_scene

    scene = get_scene(request.scene, config)
    # Record the flight-log bundle: `truth.csv` is how the route's SHAPE is
    # checked afterwards, independently of what the firmware's own estimate
    # believed at the time.
    # フライトログ一式を記録する。経路の**形**を、ファーム自身の推定とは独立に
    # 後から確かめる手段が `truth.csv` だからである。
    recording = (recording or FlightRecording("mission")).prepare()
    env = realtime_emu_env(recording.bundle_dir,
                           flightlog_dir=recording.flightlog_dir,
                           extra_env=scene.env)
    total_s = (config.sils.boot_settle_s + request.duration_s
               + config.sils.land_grace_s + 20.0)
    try:
        proc = launch_realtime_emu(total_s, env, scenario_path=None)
    except RealtimeEmuUnavailable as exc:
        raise SilsUnavailable(str(exc)) from exc

    link = SilsLink(proc)
    outcome = None
    try:
        _settle_boot(link, config, on_event)
        # `command` puts the firmware in SDK mode; the route's own first leg
        # is the takeoff, so it is not sent here.
        # `command` はファームを SDK モードにする。離陸は経路自身の最初の区間
        # なので、ここでは送らない。
        link.send_command("command")
        outcome = fly_mission(link, judge, mission, config, trace=trace,
                              scene=scene, on_event=on_event, on_cycle=on_cycle,
                              on_decisions=on_decisions)
        return outcome
    finally:
        link.close()
        _shutdown(proc)
        # After the process is gone, never before: the emulator flushes and
        # closes its CSVs as it exits, so bundling any earlier would capture
        # a half-written flight.
        # プロセスの終了後に行う（それ以前では決して行わない）。エミュレータは
        # 終了時に CSV を書き出して閉じるので、それより早く束にすると書きかけの
        # 飛行を取り込んでしまう。
        recording.finalize(
            notes=f"sf pilot mission ({request.mission_path}, "
                  f"scene={request.scene})",
            decisions=outcome.decision_rows if outcome else (),
        )


def _settle_boot(link, config, on_event) -> None:
    """Hold the sticks centred while boot calibration finishes.

    An ARM (and so an API `takeoff`) is refused until calibration is done,
    and the STATE lines that arrive meanwhile are drained rather than left
    queued -- keeping them would make the Monitor's very first cycle fold
    in several seconds of stale history at once.

    起動校正が終わるまでスティックを中立に保つ。

    校正が終わるまで ARM（したがって API の `takeoff`）は受理されない。その間に
    届く STATE 行は溜めずに捨てる — 残すと、Monitor の最初の 1 周期が数秒ぶんの
    古い履歴を一度に取り込んでしまう。
    """
    if on_event is not None:
        on_event(f"waiting {config.sils.boot_settle_s:g}s for boot calibration")
    deadline = time.monotonic() + config.sils.boot_settle_s
    while time.monotonic() < deadline:
        link.hold_sticks_neutral()
        link.read_samples()
        time.sleep(0.02)


def _shutdown(proc) -> None:
    """Let the emulator exit, escalating only if it will not.
    エミュレータの終了を待ち、終わらない場合だけ段階的に強める。"""
    import subprocess

    if proc.poll() is not None:
        return
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


# =============================================================================
# Reporting / 報告
# =============================================================================

def describe_mission(mission) -> list:
    """The route as lines the operator can check before saying yes.
    操作者が了承の前に確認できる、経路の各行。"""
    lines = [f"  mission     : {mission.name}"]
    if mission.purpose:
        lines.append(f"  purpose     : {mission.purpose}")
    lines.append(f"  legs        : {mission.total}")
    for position, leg in enumerate(mission.legs, start=1):
        lines.append(f"    {position}. {leg.describe():<28} -> {leg.preview_command()}")
    return lines


def summarize_outcome(outcome, trace_path=None) -> list:
    """The whole flight as lines: each leg, each choice, each refusal.

    A refusal is printed on its own line rather than folded into the
    outcome, because "Jev said carry on and the code sent it home" is the
    single most important thing a mission run can report -- it is the
    moment the limits in MissionConfig actually did something.

    飛行全体を各行にする: 各区間・各選択・各却下。

    却下は結果に畳み込まず、独立した行に出す。「Jev は進むと言い、コードは帰還
    させた」は、ミッションの実行が報告しうる最も重要な事柄だからである —
    MissionConfig の上限が実際に働いた瞬間である。
    """
    lines = ["", f"  mission     : {outcome.mission}"]
    lines.append(f"  ended       : {outcome.stop_reason}"
                 + (f" — {outcome.stop_detail}" if outcome.stop_detail else ""))
    lines.append(f"  legs        : {len(outcome.results)} attempt(s)")
    for result in outcome.results:
        lines.append(_leg_line(result))
    counts = _count_outcomes(outcome)
    lines.append("  tally / 集計: " + ", ".join(
        f"{name} {count}" for name, count in counts.items()
    ) if counts else "  tally / 集計: —")
    lines.append(f"  decisions   : {outcome.decisions}")
    lines.append(f"  landed      : {'yes' if outcome.landed else 'no'}")
    lines.append(f"  flown       : {outcome.flown_s:.1f} s")
    if trace_path is not None:
        lines.append(f"  trace       : {trace_path}")
    lines.append("")
    return lines


def _leg_line(result) -> str:
    """One attempt at one leg, on one line. / 区間 1 回の試行を 1 行で。"""
    choice = result.move_choice or "—"
    confidence = (f" ({result.move_confidence:.2f})"
                  if result.move_confidence else "")
    arrival = result.arrival or "—"
    line = (f"    {result.index}. {result.label:<28} {arrival:<14} "
            f"{result.outcome:<12} jev={choice}{confidence}")
    if result.refusal:
        line += f"\n         却下・上書き: {result.refusal}"
    return line


def _count_outcomes(outcome) -> dict:
    """How many legs ended each way. / 各結果で終わった区間の数。"""
    counts: dict = {}
    for name in (LEG_FLOWN, LEG_REDONE, LEG_SKIPPED, LEG_NOT_REACHED):
        count = sum(1 for r in outcome.results if r.outcome == name)
        if count:
            counts[name] = count
    return counts
