"""
sf pilot - Jev-assisted autopilot (judging layer)

サブコマンド:
  bench     Jev の往復時間と入力トークン数を実測する（TYPESAFE_API_KEY 必要）
  replay    記録済みの飛行ログを判断層に通し、判断の記録を出す（キー不要: --fake）

設計は docs/plans/jev-autopilot.md。`run`（実飛行）と `say`（自然言語の指示）は
P2 以降のため、まだ登録していない。
"""

import argparse
import json
import statistics
import sys
import time

from ..utils import console

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


def run(args: argparse.Namespace) -> int:
    """Fallback when no subcommand ran / サブコマンドが無い場合"""
    console.error("usage: sf pilot {bench|replay}")
    return 1
