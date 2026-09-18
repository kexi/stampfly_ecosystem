"""
sf pilot - Jev-assisted autopilot (judging layer)

サブコマンド:
  bench     Jev の往復時間と入力トークン数を実測する（TYPESAFE_API_KEY 必要）
  replay    記録済みの飛行ログを判断層に通し、判断の記録を出す（キー不要: --fake）
  run       SILS を実際に飛ばし、Jev の判断で監視する（--sils 必須）
  say       自然言語の指示を手順に変えて飛ぶ（--sils 必須。--dry-run で変換だけ）
  mission   経路（区間の列）を飛び、区間の境目ごとに次の一手を Jev に問う（--sils 必須）

設計は docs/plans/jev-autopilot.md。
"""

import argparse
import json
import statistics
import sys
import time

from ..utils import console

# `sf pilot run` defaults. The duration is the flight's own length, not a
# timeout: the loop lands when it ends.
# `sf pilot run` の既定値。duration は打ち切り時間ではなく飛行そのものの長さで、
# 終わればループは着陸する。
RUN_DEFAULT_DURATION_S = 60.0

COMMAND_NAME = "pilot"
COMMAND_HELP = "Jev-assisted autopilot — bench the judge, or replay a flight log"

BENCH_DEFAULT_N = 20

# A situation that exercises every question without being trivially safe
# or trivially dangerous: low battery plus a slow drift. Using one fixed
# state keeps repeated benches comparable.
# 全質問を働かせる、かつ明らかに安全でも明らかに危険でもない状況:
# 電池残量の低下と、ゆっくりした流れ。固定の state を使うことで、繰り返し
# 計測しても比較できる。
BENCH_STATE = {
    "flight": {
        "phase": "flying",
        "altitude": "on target",
        "altitude_trend": "steady",
        "horizontal_drift": "drifting slowly to the right",
        "attitude": "close to level",
        "position_estimate": "reliable",
        "ground_distance_sensor": "working normally",
    },
    "battery": {"level": "running low", "trend": "falling gradually"},
}


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register command with CLI / コマンドをCLIに登録"""
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help=COMMAND_HELP,
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="subcommand", required=True)

    bench_parser = subs.add_parser(
        "bench",
        help="Measure Jev round-trip time and input tokens (needs TYPESAFE_API_KEY)",
    )
    bench_parser.add_argument(
        "-n", "--count", type=int, default=BENCH_DEFAULT_N,
        help=f"Number of requests (default: {BENCH_DEFAULT_N})",
    )
    bench_parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Print the result as JSON instead of a table",
    )
    bench_parser.set_defaults(func=run_bench)

    replay_parser = subs.add_parser(
        "replay",
        help="Replay a flight log through the judging layers (no vehicle moves)",
    )
    replay_parser.add_argument("log", help="Flight log bundle (.sflog.zip or directory)")
    replay_parser.add_argument(
        "--fake", action="store_true",
        help="Use the deterministic FakeJudge (no API key, no network)",
    )
    replay_parser.add_argument(
        "--max-steps", type=int, default=None,
        help="Stop after this many monitor cycles",
    )
    replay_parser.set_defaults(func=run_replay)

    from sfpilot.scenes import scene_names

    run_parser = subs.add_parser(
        "run",
        help="Fly SILS under Jev's judgement (--sils required; real hardware is P5)",
    )
    run_parser.add_argument(
        "--sils", action="store_true",
        help="Fly the SILS emulator. Required: real hardware is not supported yet "
             "（実機は未対応）",
    )
    run_parser.add_argument(
        "--scene", default="nominal", choices=list(scene_names()),
        help="Which situation to rehearse the decision in (default: nominal)",
    )
    run_parser.add_argument(
        "--fake", action="store_true",
        help="Use the deterministic FakeJudge (no API key, no network)",
    )
    run_parser.add_argument(
        "--duration", type=float, default=RUN_DEFAULT_DURATION_S,
        help=f"Flight length in seconds (default: {RUN_DEFAULT_DURATION_S:g})",
    )
    run_parser.add_argument(
        "--deadline-ms", type=float, default=None,
        help="Override the Jev response deadline in milliseconds",
    )
    run_parser.set_defaults(func=run_run)

    say_parser = subs.add_parser(
        "say",
        help="Fly a natural-language instruction (--sils required; real hardware is P5)",
    )
    say_parser.add_argument(
        "instruction", nargs="?", default=None,
        help="What the aircraft should do, in plain words （例: 「1m 上がって前に "
             "50cm 進んで戻ってきて」）",
    )
    say_parser.add_argument(
        "--sils", action="store_true",
        help="Fly the SILS emulator. Required to fly: real hardware is not "
             "supported yet （実機は未対応）",
    )
    say_parser.add_argument(
        "--fake", action="store_true",
        help="Use the deterministic FakeJudge (no API key, no network)",
    )
    say_parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Translate and check the instruction; fly nothing",
    )
    say_parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Do not ask for confirmation before flying the steps",
    )
    say_parser.add_argument(
        "--no-auto-land", dest="auto_land", action="store_false",
        help="Do not append a landing when the instruction does not end in one",
    )
    say_parser.add_argument(
        "--eval", dest="eval_file", default=None, metavar="FILE",
        help="Translate every instruction in a YAML/JSON case file and compare "
             "the steps against the expected ones （期待表との照合）",
    )
    say_parser.add_argument(
        "--duration", type=float, default=RUN_DEFAULT_DURATION_S,
        help=f"Ceiling on the flight in seconds (default: {RUN_DEFAULT_DURATION_S:g})",
    )
    say_parser.set_defaults(func=run_say)

    mission_parser = subs.add_parser(
        "mission",
        help="Fly a route, asking Jev what to do at each leg boundary "
             "(--sils required; real hardware is P5)",
    )
    mission_parser.add_argument(
        "mission", help="Mission file, or the name of a shipped one "
                        "（例: square、または自分の .yaml へのパス）",
    )
    mission_parser.add_argument(
        "--sils", action="store_true",
        help="Fly the SILS emulator. Required to fly: real hardware is not "
             "supported yet （実機は未対応）",
    )
    mission_parser.add_argument(
        "--scene", default="nominal", choices=list(scene_names()),
        help="Which situation to fly the route in (default: nominal)",
    )
    mission_parser.add_argument(
        "--fake", action="store_true",
        help="Use the rule-based FakeJudge (no API key, no network)",
    )
    mission_parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Load and check the route; fly nothing",
    )
    mission_parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Do not ask for confirmation before flying the route",
    )
    mission_parser.add_argument(
        "--duration", type=float, default=None,
        help="Override the mission time limit in seconds",
    )
    mission_parser.set_defaults(func=run_mission)


# =============================================================================
# sf pilot bench
# =============================================================================
def run_bench(args: argparse.Namespace) -> int:
    """Measure what one judgement actually costs from this machine.
    この環境から 1 判断にかかる実際の時間とトークン数を計測する。"""
    from sfpilot.judge import JevJudge, MissingApiKey, SAFETY_ONLY

    try:
        judge = JevJudge()
    except MissingApiKey as exc:
        console.error(str(exc))
        console.info(
            "Set it before running, without writing it to a file: "
            "`TYPESAFE_API_KEY=... sf pilot bench`"
            "（ファイルに書かず、実行時に環境変数で渡してください）"
        )
        return 1

    console.info(f"Asking Jev {args.count} times ({', '.join(SAFETY_ONLY)}) ...")
    samples = _bench_requests(judge, args.count)
    judge.close()

    failures = [s for s in samples if s["error"]]
    latencies = [s["latency_ms"] for s in samples if not s["error"]]
    if not latencies:
        console.error(f"every request failed; first error: {failures[0]['error']}")
        return 1

    result = _bench_summary(samples, latencies, failures)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    _print_bench_table(result)
    return 0


def _bench_requests(judge, count: int) -> list:
    """Send `count` requests on one kept-alive connection.
    1 本の接続を使い回して `count` 回リクエストする。"""
    from sfpilot.judge import SAFETY_ONLY

    samples = []
    for index in range(count):
        started = time.monotonic()
        judgement = judge.ask(BENCH_STATE, SAFETY_ONLY)
        samples.append({
            "index": index,
            "latency_ms": (time.monotonic() - started) * 1e3,
            "input_tokens": judgement.input_tokens,
            "output_tokens": judgement.output_tokens,
            "error": judgement.error,
        })
    return samples


def _bench_summary(samples: list, latencies: list, failures: list) -> dict:
    """Percentiles, plus the first call separated from the rest.

    The first call pays for the TLS handshake, which the pilot loop pays
    only once at startup. Averaging it in would overstate the per-decision
    cost the deadline has to cover.
    百分位数と、初回を分けた集計。

    初回は TLS 確立の分を含む。操縦ループはこれを起動時に 1 回だけ払う。
    混ぜて平均すると、期限が賄うべき 1 判断あたりの時間を過大に見せる。
    """
    warm = latencies[1:] if len(latencies) > 1 else latencies
    tokens = [s["input_tokens"] for s in samples if not s["error"]]
    return {
        "requests": len(samples),
        "failures": len(failures),
        "first_call_ms": round(latencies[0], 1),
        "warm": {
            "n": len(warm),
            "p50_ms": round(_percentile(warm, 50), 1),
            "p95_ms": round(_percentile(warm, 95), 1),
            "max_ms": round(max(warm), 1),
            "min_ms": round(min(warm), 1),
        },
        "input_tokens": {
            "median": int(statistics.median(tokens)) if tokens else 0,
            "min": min(tokens) if tokens else 0,
            "max": max(tokens) if tokens else 0,
        },
        "output_tokens": {
            "median": int(statistics.median(
                [s["output_tokens"] for s in samples if not s["error"]]
            )) if tokens else 0,
        },
        "errors": [f["error"] for f in failures[:3]],
    }


def _percentile(values: list, percent: float) -> float:
    """Nearest-rank percentile. Avoids numpy so `sf pilot bench` works in
    the offline classroom install.
    最近傍順位の百分位数。教室のオフライン環境でも動くよう numpy を使わない。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, int(round(percent / 100.0 * len(ordered))))
    return ordered[min(rank, len(ordered)) - 1]


def _print_bench_table(result: dict) -> None:
    warm = result["warm"]
    tokens = result["input_tokens"]
    print()
    print("  Jev round-trip / Jev 往復時間")
    print("  " + "-" * 46)
    print(f"  requests            : {result['requests']}  (failures {result['failures']})")
    print(f"  first call (TLS)    : {result['first_call_ms']:8.1f} ms")
    print(f"  warm p50            : {warm['p50_ms']:8.1f} ms   (n={warm['n']})")
    print(f"  warm p95            : {warm['p95_ms']:8.1f} ms")
    print(f"  warm max / min      : {warm['max_ms']:8.1f} / {warm['min_ms']:.1f} ms")
    print(f"  input tokens        : {tokens['median']:8d}    "
          f"(min {tokens['min']}, max {tokens['max']})")
    print(f"  output tokens       : {result['output_tokens']['median']:8d}")
    print()
    for error in result["errors"]:
        console.warning(f"error: {error}")


# =============================================================================
# sf pilot replay
# =============================================================================
def run_replay(args: argparse.Namespace) -> int:
    """Run a recorded flight through the judging layers. Nothing moves.
    記録済みの飛行を判断層に通す。何も動かない。"""
    from sfpilot.config import DEFAULT_CONFIG
    from sfpilot.judge import FakeJudge, JevJudge, MissingApiKey
    from sfpilot.link import ReplayLink
    from sfpilot.pilot import replay
    from sfpilot.trace import Trace

    try:
        link = ReplayLink(args.log)
    except (OSError, ValueError) as exc:
        console.error(f"cannot replay {args.log}: {exc}")
        return 1
    if link.sample_count == 0:
        console.error(f"{args.log}: no samples to replay（再生できるサンプルがありません）")
        return 1

    if args.fake:
        judge = FakeJudge()
    else:
        try:
            judge = JevJudge()
        except MissingApiKey as exc:
            console.error(str(exc))
            console.info("Use --fake to replay without an API key（キー無しなら --fake）")
            return 1

    console.info(f"Replaying {link.sample_count} samples from {args.log} ...")
    trace = Trace()
    try:
        pilot = replay(link, judge, DEFAULT_CONFIG, trace=trace, max_steps=args.max_steps)
    finally:
        trace.close()
        judge.close()
        link.close()

    _print_replay_summary(pilot, link, trace)
    return 0


def _print_replay_summary(pilot, link, trace) -> None:
    """Report what the judging layers decided, and where it was recorded.
    判断層が何を決めたか、どこに記録したかを報告する。"""
    actions: dict = {}
    for row in pilot.decisions:
        action = row["verdict"].action
        actions[action] = actions.get(action, 0) + 1
    print()
    print(f"  decisions   : {len(pilot.decisions)}")
    for action, count in sorted(actions.items(), key=lambda kv: -kv[1]):
        print(f"    {action:<10}: {count}")
    print(f"  commands    : {len(link.sent)} recorded (nothing transmitted)")
    print(f"  trace       : {trace.path}")
    print()


# =============================================================================
# sf pilot run --sils
# =============================================================================
def run_run(args: argparse.Namespace) -> int:
    """Fly the SILS emulator under the judging layers.
    SILS エミュレータを判断層の下で飛ばす。"""
    from dataclasses import replace

    from sfpilot.config import DEFAULT_CONFIG
    from sfpilot.judge import FakeJudge, JevJudge, MissingApiKey
    from sfpilot.scenes import get_scene
    from sfpilot.trace import Trace

    if not args.sils:
        console.error(
            "`sf pilot run` currently supports --sils only — flying real hardware "
            "is P5 in docs/plans/jev-autopilot.md and is deliberately not wired up "
            "（実機飛行は未対応です）"
        )
        return 1

    config = DEFAULT_CONFIG
    if args.deadline_ms is not None:
        # Rebuild the whole config so the Arbiter's copy of the deadline
        # moves with the Judge's -- they are read from one object for
        # exactly this reason (config.py).
        # 期限は Judge と Arbiter の両方が見るため、設定一式を作り直して
        # 両者を一緒に動かす（そのために 1 つのオブジェクトにまとめてある）。
        judge_config = replace(config.judge, deadline_s=args.deadline_ms / 1e3)
        config = replace(config, judge=judge_config)

    scene = get_scene(args.scene, config)

    if args.fake:
        judge = FakeJudge()
    else:
        try:
            judge = JevJudge(config)
        except MissingApiKey as exc:
            console.error(str(exc))
            console.info("Use --fake to fly without an API key（キー無しなら --fake）")
            return 1

    console.info(f"Scene: {scene.name} — {scene.description}")
    trace = Trace()
    try:
        outcome = _fly_sils(args, config, scene, judge, trace)
    except _SilsUnavailable as exc:
        console.error(str(exc))
        return 1
    finally:
        trace.close()
        judge.close()

    _print_run_summary(outcome, trace)
    return 0


class _SilsUnavailable(RuntimeError):
    """The emulator could not be started. / エミュレータを起動できなかった。"""


def _fly_sils(args, config, scene, judge, trace) -> dict:
    """Launch, take off, monitor, land, shut down. Returns the outcome.
    起動・離陸・監視・着陸・終了。結果を返す。"""
    from sfcli.commands.sils import (
        RealtimeEmuUnavailable, launch_realtime_emu, realtime_emu_env,
    )
    from sfpilot.link import SilsLink

    from sfcli.utils.paths import paths

    bundle = paths.root() / "simulator" / "sils" / "viz" / "out_pilot"
    bundle.mkdir(parents=True, exist_ok=True)
    # The emulator is given headroom beyond the flight so it is still alive
    # to accept `land` and `quit` at the end rather than exiting underneath
    # the loop.
    # 飛行より長めの時間をエミュレータに与える。最後の `land`・`quit` を受け
    # 取れるよう、ループの下で先に終了してしまわないようにするためである。
    total_s = (config.sils.boot_settle_s + config.sils.takeoff_settle_s
               + args.duration + config.sils.land_grace_s)
    env = realtime_emu_env(bundle, extra_env=scene.env)
    try:
        proc = launch_realtime_emu(total_s + 10.0, env, scenario_path=None)
    except RealtimeEmuUnavailable as exc:
        raise _SilsUnavailable(str(exc)) from exc

    link = SilsLink(proc)
    try:
        return _run_flight(args, config, scene, judge, trace, link, proc)
    finally:
        link.close()
        _shutdown(proc)


def _run_flight(args, config, scene, judge, trace, link, proc) -> dict:
    """The flight itself: settle, take off, judge, land.
    飛行そのもの: 静定・離陸・判断・着陸。"""
    from sfpilot.pilot import Pilot

    console.info(f"Waiting {config.sils.boot_settle_s:g}s for boot calibration ...")
    _hold_neutral(link, config.sils.boot_settle_s)

    console.info("Taking off (api command -> api takeoff) ...")
    link.takeoff()
    _hold_neutral(link, config.sils.takeoff_settle_s)

    console.info(f"Monitoring for {args.duration:g}s "
                 f"({'FakeJudge' if args.fake else 'Jev'}) ...")
    pilot = Pilot(link, judge, config, trace=trace)
    period = 1.0 / config.monitor_hz
    started = time.monotonic()
    landed_early = False
    # An emulator that died is a THIRD outcome, neither "landed" nor "flew
    # its full length". Without it, the code below would send a `land` to a
    # dead process and then wait out the landing grace period, reporting a
    # normal flight that was several seconds longer than it really was.
    # エミュレータの異常終了は「着陸した」でも「飛び切った」でもない第 3 の
    # 結末である。区別しないと、死んだプロセスへ `land` を送ったうえで着陸の
    # 猶予時間を待ち切り、実際より数秒長い正常な飛行として報告してしまう。
    emulator_died = False
    while True:
        cycle_start = time.monotonic()
        elapsed = cycle_start - started
        if elapsed >= args.duration:
            break
        if proc.poll() is not None:
            console.error("emu_vehicle exited during the flight")
            for line in link.log_tail[-15:]:
                console.print(f"  {line}")
            emulator_died = True
            break
        if scene.drive is not None:
            scene.drive(link, elapsed, args.duration)
        pilot.step()
        link.hold_sticks_neutral()
        if pilot.executor.landing:
            landed_early = True
            console.info(f"Landing decided at t={elapsed:.1f}s")
            break
        slack = period - (time.monotonic() - cycle_start)
        if slack > 0:
            time.sleep(slack)

    flown_s = time.monotonic() - started
    should_land = not landed_early and not emulator_died
    if should_land:
        console.info("Flight time elapsed — landing")
        link.send_command("land")
    if not emulator_died:
        time.sleep(config.sils.land_grace_s)
    return {
        "pilot": pilot,
        "landed_early": landed_early,
        "emulator_died": emulator_died,
        # Measured before the landing grace period, so the reported flight
        # length is time actually flown, not time spent waiting to shut down.
        # 着陸の猶予時間より前に測る。報告する飛行時間を、終了待ちではなく
        # 実際に飛んだ時間にするためである。
        "flown_s": flown_s,
        "scene": scene.name,
    }


def _hold_neutral(link, seconds: float) -> None:
    """Keep the transmitter centred for `seconds` without judging.
    判断せずに `seconds` の間、送信機を中立に保つ。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        link.hold_sticks_neutral()
        # Drain the STATE lines that arrive meanwhile: the reader thread
        # queues them, and leaving them would make the Monitor's very first
        # cycle fold in seconds of stale history at once.
        # その間に届く STATE 行を捨てる。読み取りスレッドが溜めるので、残すと
        # Monitor の最初の 1 周期が数秒ぶんの古い履歴を一度に取り込んでしまう。
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


def _outcome_note(outcome: dict) -> str:
    """How the flight ended, in a few words, or nothing if it simply ran out.
    飛行の終わり方を短く表す。単に時間が来た場合は何も返さない。"""
    if outcome.get("emulator_died"):
        return "  (emu_vehicle exited — the run is incomplete)"
    if outcome["landed_early"]:
        return "  (landed early)"
    return ""


def _print_run_summary(outcome: dict, trace) -> None:
    """Report what was decided, how long Jev took, and where the trace is.
    何が決まったか、Jev に何秒かかったか、記録はどこかを報告する。"""
    pilot = outcome["pilot"]
    actions: dict = {}
    hover_reasons: dict = {}
    latencies: list = []
    overdue = 0
    for row in pilot.decisions:
        verdict = row["verdict"]
        actions[verdict.action] = actions.get(verdict.action, 0) + 1
        if verdict.action == "hover":
            hover_reasons[verdict.reason] = hover_reasons.get(verdict.reason, 0) + 1
        judgement = row["judgement"]
        if judgement is None:
            continue
        latencies.append(judgement.latency_ms)
        if judgement.error == "deadline exceeded":
            overdue += 1

    print()
    print(f"  scene       : {outcome['scene']}")
    print(f"  flown       : {outcome['flown_s']:.1f} s{_outcome_note(outcome)}")
    print(f"  decisions   : {len(pilot.decisions)}")
    for action, count in sorted(actions.items(), key=lambda kv: -kv[1]):
        print(f"    {action:<10}: {count}")
    if hover_reasons:
        print("  hold reasons / 待機の理由:")
        for reason, count in sorted(hover_reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {count:>4}x  {reason}")
    if latencies:
        print(f"  Jev round-trip / 往復時間 (n={len(latencies)}):")
        print(f"    p50 {_percentile(latencies, 50):.0f} ms   "
              f"p95 {_percentile(latencies, 95):.0f} ms   "
              f"max {max(latencies):.0f} ms")
    print(f"  over deadline / 期限超過: {overdue}")
    print(f"  trace       : {trace.path}")
    print()


# =============================================================================
# sf pilot say
# =============================================================================
def run_say(args: argparse.Namespace) -> int:
    """Translate an instruction into steps, then fly them if asked.
    指示を手順に変換し、求められれば飛ばす。"""
    if args.eval_file:
        return _run_eval(args)
    if not args.instruction:
        console.error("say what? （指示の文を引数に与えてください）")
        console.info('例: sf pilot say --sils --dry-run "1m 上がって前に進んで戻ってきて"')
        return 1

    judge, error = _open_judge(args)
    if judge is None:
        return error

    from sfpilot.config import DEFAULT_CONFIG
    from sfpilot.instruction import translate
    from sfpilot.judge import step_questions
    from sfpilot.trace import Trace

    config = DEFAULT_CONFIG
    trace = Trace()
    try:
        console.info(f"Translating: {args.instruction}")
        plan = translate(args.instruction, judge, on_ground=True,
                         auto_land=args.auto_land, config=config)
        trace.write_plan(plan, step_questions(config.instruction.max_steps))
        _print_plan(plan)
        if not plan.ok:
            return 1
        if args.dry_run:
            console.info(f"Dry run — nothing was flown. trace: {trace.path}")
            return 0
        return _fly_said_plan(args, config, plan, judge, trace)
    finally:
        trace.close()
        judge.close()


def _open_judge(args):
    """The Judge this run uses, or (None, exit code) with the reason printed.
    この実行が使う Judge。開けなければ (None, 終了コード) を返し理由を表示する。"""
    from sfpilot.judge import FakeJudge, JevJudge, MissingApiKey

    if args.fake:
        return FakeJudge(answers=_fake_say_answers()), 0
    try:
        return JevJudge(), 0
    except MissingApiKey as exc:
        console.error(str(exc))
        console.info("Use --fake to translate without an API key（キー無しなら --fake）")
        return None, 1


def _fake_say_answers() -> dict:
    """A fixed translation for `--fake`, plus the safety answers.

    `--fake` exists to exercise the path from a plan to a flight without a
    key, so it answers one representative instruction (up, forward, return,
    land) rather than trying to understand the words it was given. The
    instruction's own numbers still apply, because those are read by code.

    The safety answers have to be here too: the SAME judge serves the
    instruction translation and then the 50Hz safety layer that watches the
    flight. A judge that knew only about steps would leave every safety
    question unanswered, the Arbiter would hover on "no answer", and the
    hover-to-land timer would land the aircraft mid-instruction.

    `--fake` 用の固定の変換に、安全判断の答えを加えたもの。

    `--fake` はキー無しで「計画から飛行まで」の経路を動かすためのものなので、
    与えられた語を理解しようとはせず、代表的な 1 つの指示（上昇・前進・帰還・
    着陸）に答える。指示中の数値はそれでも効く — 読むのはコードだからである。

    安全判断の答えもここに要る。指示の変換と、その後に飛行を見張る 50Hz の
    安全層は**同じ** judge を使うためである。手順のことしか知らない judge では
    安全の質問が全て未回答になり、Arbiter は「答え無し」で待機し、待機継続の
    計時が指示の途中で機体を着陸させてしまう。
    """
    from sfpilot.judge import (
        AMOUNT_MEDIUM, Answer, Q_STEP_AMOUNT, Q_STEP_MOVE,
        STEP_FORWARD, STEP_LAND, STEP_NONE, STEP_RETURN_HOME, STEP_UP,
        default_fake_answers,
    )

    script = [STEP_UP, STEP_FORWARD, STEP_RETURN_HOME, STEP_LAND, STEP_NONE, STEP_NONE]
    answers = default_fake_answers()
    for index, verb in enumerate(script, start=1):
        answers[Q_STEP_MOVE.format(index)] = Answer(
            kind="choice", choice=verb, confidence=0.95,
            probabilities={verb: 0.95},
        )
        answers[Q_STEP_AMOUNT.format(index)] = Answer(
            kind="choice", choice=AMOUNT_MEDIUM, confidence=0.9,
            probabilities={AMOUNT_MEDIUM: 0.9},
        )
    return answers


def _print_plan(plan) -> None:
    """Show the steps, or the reason there are none.
    手順を表示する。手順が無ければその理由を表示する。"""
    print()
    if plan.spoken_numbers:
        said = ", ".join(f"{n.text.strip()} → {n.value:g}{n.unit}"
                         for n in plan.spoken_numbers)
        print(f"  numbers read from the instruction / 指示から読んだ数値: {said}")
    if not plan.ok:
        console.error(f"refused / 実行しない: {plan.refusal}")
        print()
        return
    print("  steps / 手順:")
    for position, step in enumerate(plan.steps, start=1):
        source = f"  [{step.amount_source}]" if step.amount_source else ""
        print(f"    {position}. {step.describe():<24} -> {step.command()}{source}")
    print()


def _fly_said_plan(args, config, plan, judge, trace) -> int:
    """Confirm, then fly the plan against SILS. / 確認してから SILS で飛ばす。"""
    if not args.sils:
        console.error(
            "`sf pilot say` currently supports --sils only — flying real hardware "
            "is P5 in docs/plans/jev-autopilot.md and is deliberately not wired up "
            "（実機は未対応です）。変換だけなら --dry-run"
        )
        return 1
    if not _confirmed(args):
        return 1

    try:
        outcome = _fly_instruction(args, config, plan, judge, trace)
    except _SilsUnavailable as exc:
        console.error(str(exc))
        return 1
    _print_say_summary(outcome, trace)
    return 0 if outcome.finished else 1


def _confirmed(args) -> bool:
    """Whether the operator agreed to fly these steps.

    A non-interactive session cannot agree to anything, so it is refused
    rather than treated as agreement -- the steps are about to move a real
    aircraft, and silence is not consent.

    操作者がこの手順を飛ばすことに同意したか。

    非対話のセッションは何にも同意できないので、同意とみなさず拒否する。
    手順はこれから実機を動かすものであり、無言は同意ではない。
    """
    if args.yes:
        return True
    if not sys.stdin.isatty():
        console.error(
            "not an interactive terminal, so there is nobody to confirm these "
            "steps — re-run with --yes to fly them, or --dry-run to only "
            "translate （非対話では確認が取れないため実行しません）"
        )
        return False
    answer = input("  Fly these steps? / この手順で飛ばしますか [y/N]: ").strip().lower()
    if answer in ("y", "yes"):
        return True
    console.info("cancelled / 中止しました")
    return False


def _fly_instruction(args, config, plan, judge, trace):
    """Launch SILS, settle, then fly the plan under the safety layer.
    SILS を起動・静定させ、安全層の下で計画を飛ばす。"""
    from sfcli.commands.sils import (
        RealtimeEmuUnavailable, launch_realtime_emu, realtime_emu_env,
    )
    from sfcli.utils.paths import paths
    from sfpilot.link import SilsLink
    from sfpilot.say import fly_plan

    bundle = paths.root() / "simulator" / "sils" / "viz" / "out_say"
    bundle.mkdir(parents=True, exist_ok=True)
    total_s = (config.sils.boot_settle_s + args.duration
               + config.sils.land_grace_s + 10.0)
    # Record the flight-log bundle: `truth.csv` is how an operator checks
    # that the aircraft actually went where the steps said, independently
    # of what the firmware's own estimate believed.
    # フライトログ一式を記録する。手順どおりに機体が実際に動いたかを、ファーム
    # 自身の推定とは独立に操作者が確かめる手段が `truth.csv` だからである。
    env = realtime_emu_env(bundle, flightlog_dir=bundle, extra_env={})
    try:
        proc = launch_realtime_emu(total_s + 10.0, env, scenario_path=None)
    except RealtimeEmuUnavailable as exc:
        raise _SilsUnavailable(str(exc)) from exc

    link = SilsLink(proc)
    try:
        console.info(f"Waiting {config.sils.boot_settle_s:g}s for boot calibration ...")
        _hold_neutral(link, config.sils.boot_settle_s)
        # `command` puts the firmware in SDK mode; the plan's own first step
        # is the takeoff, so it is not sent here.
        # `command` はファームを SDK モードにする。離陸は計画自身の最初の手順
        # なので、ここでは送らない。
        link.send_command("command")
        console.info("Flying the steps （手順を実行します）...")
        return fly_plan(link, judge, plan, config, trace=trace,
                        on_step=_announce_step)
    finally:
        link.close()
        _shutdown(proc)


def _announce_step(position: int, step) -> None:
    console.info(f"  step {position}: {step.describe()}  -> {step.command()}")


def _print_say_summary(outcome, trace) -> None:
    """Report which steps flew and why the rest did not.
    どの手順が飛び、残りがなぜ飛ばなかったかを報告する。"""
    print()
    print(f"  steps flown : {len(outcome.completed)}")
    for position, step in enumerate(outcome.completed, start=1):
        print(f"    {position}. {step.describe()}")
    if outcome.interrupt_reason:
        console.warning(
            f"interrupted after step {outcome.interrupted_at} "
            f"（手順 {outcome.interrupted_at} の後で中断）: {outcome.interrupt_reason}"
        )
    print(f"  decisions   : {outcome.decisions}")
    print(f"  landed      : {'yes' if outcome.landed else 'no'}")
    print(f"  flown       : {outcome.flown_s:.1f} s")
    print(f"  trace       : {trace.path}")
    print()


# -- sf pilot say --eval / 期待表との照合 ---------------------------------

def _run_eval(args: argparse.Namespace) -> int:
    """Translate every case in a file and compare against its expectation.

    This is how the instruction layer is judged against the LIVE model:
    the unit tests pin the assembly rules with a FakeJudge, but whether
    Jev reads "戻ってきて" as `return_home` can only be answered by asking
    it. Keeping it a subcommand rather than a script is the repository's
    tooling rule (CLAUDE.md): what users run is an `sf` command.

    ファイル中の全事例を変換し、期待と照合する。

    指示層を**実際のモデル**に対して評価する手段である。組み立て規則は
    FakeJudge を使った単体試験で固定できるが、Jev が「戻ってきて」を
    `return_home` と読むかどうかは、問うてみるほかない。スクリプトではなく
    サブコマンドにするのは、このリポジトリの方針である（CLAUDE.md）—
    利用者が実行するものは `sf` コマンドとする。
    """
    cases, error = _load_cases(args.eval_file)
    if cases is None:
        console.error(error)
        return 1

    judge, code = _open_judge(args)
    if judge is None:
        return code

    from sfpilot.config import DEFAULT_CONFIG
    from sfpilot.instruction import translate

    console.info(f"Translating {len(cases)} instructions from {args.eval_file} ...")
    results = []
    try:
        for case in cases:
            plan = translate(case["instruction"], judge, on_ground=True,
                             auto_land=case.get("auto_land", True),
                             config=DEFAULT_CONFIG)
            results.append(_compare_case(case, plan))
    finally:
        judge.close()

    return _print_eval_report(results)


def _load_cases(path: str):
    """Read the case file, as YAML if available and JSON otherwise.
    事例ファイルを読む。YAML が使えれば YAML、無ければ JSON として読む。"""
    from pathlib import Path

    file_path = Path(path)
    if not file_path.exists():
        return None, f"{path}: no such file（ファイルがありません）"
    text = file_path.read_text(encoding="utf-8")
    try:
        import yaml
        data = yaml.safe_load(text)
    except ImportError:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return None, (f"{path}: PyYAML is not installed and this is not JSON "
                          f"（PyYAML が無く、JSON としても読めません）: {exc}")
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list) or not cases:
        return None, f"{path}: no cases found（事例がありません）"
    return cases, ""


def _compare_case(case: dict, plan) -> dict:
    """One case's expected steps against what came back.

    Comparing the command LINES rather than the step objects is deliberate:
    the line is what reaches the vehicle, so an expectation written in the
    case file is checkable by anyone reading `tello-api-reference.md`
    without knowing this module's internals.

    1 事例の期待手順と実際の結果を比べる。

    手順オブジェクトではなく**コマンド行**を比べるのは意図的である。行は機体に
    届くものそのものなので、事例ファイルに書いた期待は、本モジュールの内部を
    知らなくても `tello-api-reference.md` を読める人なら検証できる。
    """
    expected = case.get("expect_steps")
    expects_refusal = case.get("expect_refusal", False)
    actual = plan.command_lines()
    if expects_refusal:
        passed = not plan.ok
        return {"instruction": case["instruction"], "passed": passed,
                "expected": "(refusal)", "actual": actual or plan.refusal,
                "note": case.get("note", "")}
    passed = plan.ok and actual == expected
    return {"instruction": case["instruction"], "passed": passed,
            "expected": expected, "actual": actual if plan.ok else plan.refusal,
            "note": case.get("note", "")}


def _print_eval_report(results: list) -> int:
    """Print each case's outcome and return non-zero if any failed.
    各事例の結果を表示し、1 件でも不一致なら非ゼロを返す。"""
    passed = [r for r in results if r["passed"]]
    print()
    for result in results:
        mark = "PASS" if result["passed"] else "FAIL"
        print(f"  [{mark}] {result['instruction']}")
        if result["passed"]:
            continue
        print(f"         expected: {result['expected']}")
        print(f"         actual  : {result['actual']}")
        if result["note"]:
            print(f"         note    : {result['note']}")
    print()
    print(f"  {len(passed)} / {len(results)} matched the expectation")
    print()
    return 0 if len(passed) == len(results) else 1


# =============================================================================
# sf pilot mission
# =============================================================================
def run_mission(args: argparse.Namespace) -> int:
    """Load a route, show it, and fly it against SILS if asked.

    The flight itself is `sfpilot.mission_run`'s; this function reads the
    arguments and prints. That division is the repository's rule for
    `lib/sfcli/commands/` and it is why the same route can be flown from a
    test without going through argparse.

    経路を読み、表示し、求められれば SILS で飛ばす。

    飛行そのものは `sfpilot.mission_run` のもので、この関数は引数を読んで表示
    する。この分け方は `lib/sfcli/commands/` に対するリポジトリの方針であり、
    同じ経路を argparse を通さず試験から飛ばせる理由でもある。
    """
    from dataclasses import replace

    from sfpilot.config import DEFAULT_CONFIG
    from sfpilot.mission import MissionError
    from sfpilot.mission_run import MissionRequest, describe_mission, prepare

    config = DEFAULT_CONFIG
    if args.duration is not None:
        config = replace(config, mission=replace(config.mission,
                                                 time_limit_s=args.duration))

    try:
        mission = prepare(args.mission, config)
    except MissionError as exc:
        console.error(str(exc))
        return 1

    print()
    for line in describe_mission(mission):
        print(line)
    print()

    if args.dry_run:
        console.info("Dry run — nothing was flown（予行のみ。何も飛ばしていません）")
        return 0
    if not args.sils:
        console.error(
            "`sf pilot mission` currently supports --sils only — flying real "
            "hardware is P5 in docs/plans/jev-autopilot.md and is deliberately "
            "not wired up （実機は未対応です）。確認だけなら --dry-run"
        )
        return 1
    if not _confirmed(args):
        return 1

    request = MissionRequest(mission_path=args.mission, scene=args.scene,
                             duration_s=config.mission.time_limit_s,
                             fake=args.fake)
    return _fly_mission(request, mission, args, config)


def _fly_mission(request, mission, args, config) -> int:
    """Open the judge, fly, and report. / Judge を開き、飛ばし、報告する。"""
    from sfpilot.judge import JevJudge, MissingApiKey, MissionFakeJudge
    from sfpilot.mission_run import SilsUnavailable, fly_in_sils, summarize_outcome
    from sfpilot.trace import Trace

    if args.fake:
        judge = MissionFakeJudge()
    else:
        try:
            judge = JevJudge(config)
        except MissingApiKey as exc:
            console.error(str(exc))
            console.info("Use --fake to fly without an API key（キー無しなら --fake）")
            return 1

    console.info(f"Scene: {request.scene}")
    trace = Trace()
    try:
        outcome = fly_in_sils(request, mission, judge, config, trace=trace,
                              on_event=console.info)
    except SilsUnavailable as exc:
        console.error(str(exc))
        return 1
    finally:
        trace.close()
        judge.close()

    for line in summarize_outcome(outcome, trace.path):
        print(line)
    return 0 if outcome.completed else 1


def run(args: argparse.Namespace) -> int:
    """Fallback when no subcommand ran / サブコマンドが無い場合"""
    console.error("usage: sf pilot {bench|replay|run|say|mission}")
    return 1
