#!/usr/bin/env python3
"""
Compare two per-tick traces and say where, and in what, they first part company.
刻みごとのトレースを 2 つ比べ、どこで何が最初に食い違ったかを言う。

`sim.trace_dump` (the browser) and `RaycastFlightScene.WriteTrace` (the editor)
write the same JSON Lines format, so a browser run and an editor run of the
same script can be laid side by side tick by tick.

`sim.trace_dump`（ブラウザ）と `RaycastFlightScene.WriteTrace`（エディタ）は
同じ JSON Lines の形式で書く。よって同じ台本のブラウザの実行とエディタの実行を、
刻みごとに並べて比べられる。

## Why the first difference is the answer / なぜ最初の食い違いが答えなのか

Each tick has two halves: what the host PUT IN (`in_*`) and what came BACK
(`out_*`). Once two runs differ at all, everything after is downstream of that
one difference, so only the FIRST one is evidence. Which half moved first says
where the cause is:

  * an `in_*` field first  -> Unity, PhysX or C# produced different input
  * every `in_*` equal but an `out_*` different -> the same bytes went into the
    firmware module and different bytes came out, so the cause is inside it

各刻みには 2 つの側がある。ホストが**入れた**もの（`in_*`）と、**返ってきた**
もの（`out_*`）である。2 つの実行がひとたび食い違えば、以後の全てはその 1 つの
食い違いの下流なので、証拠になるのは**最初の**ものだけである。どちらの側が先に
動いたかが原因の在り処を告げる。

  * 先に `in_*` が違う → Unity・PhysX・C# が違う入力を作った
  * `in_*` が全て一致して `out_*` が違う → 同じバイト列がファームのモジュールへ
    入り、違うバイト列が出てきた。原因はその中にある

Usage / 使い方:
    python3 tools/unity_check/trace_diff.py A.trace.jsonl B.trace.jsonl
    python3 tools/unity_check/trace_diff.py --json A.jsonl B.jsonl C.jsonl

Standard library only, like the rest of `tools/unity_check`.
`tools/unity_check` の他と同じく、標準ライブラリだけで書く。

@design docs/plans/unity-simulator.md section 3
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# The fields, and what counts as a difference / 欄と、何を食い違いとみなすか
# ---------------------------------------------------------------------------

# The two halves, in the order they are reported. `in_*` first, because an
# input difference explains an output difference and never the other way round.
# 2 つの側を、報告する順に並べたもの。`in_*` が先である。入力の食い違いは出力の
# 食い違いを説明するが、その逆は無いためである。
INPUT_FIELDS: Tuple[str, ...] = (
    "in_pos", "in_rot", "in_vz", "in_range", "in_range_valid", "in_accel",
    "in_throttle", "in_flags",
)

OUTPUT_FIELDS: Tuple[str, ...] = (
    "out_force", "out_torque", "out_now_us", "out_wrench_dt", "out_duty",
    "out_est_alt", "out_state", "out_mode", "out_vbatt", "out_status",
)

# Truth is neither half: it is what PhysX produced after the wrench was applied,
# so it moves one tick AFTER the output that caused it. Reported, never used to
# decide which side moved first.
# 真値はどちらの側でもない。力を加えた後に PhysX が出したものなので、原因となった
# 出力より 1 刻み**後**に動く。報告はするが、どちらの側が先に動いたかの判断には
# 使わない。
TRUTH_FIELDS: Tuple[str, ...] = ("truth_alt", "truth_vz")

# Below this, two floats count as equal. The traces are written with "R", so a
# run that is truly identical is identical to the last bit and this tolerance
# absorbs nothing; it is here so that a comparison of two runs that were never
# expected to be bit-identical (different machines) still reports usefully.
# これ未満なら 2 つの浮動小数を等しいとみなす。トレースは "R" で書かれるので、
# 本当に同一の実行は最後のビットまで一致し、この許容は何も吸収しない。別の機械の
# ように、そもそも bit 一致を期待しない 2 つの実行の比較でも役立つ報告になるよう
# 置いてある。
DEFAULT_TOLERANCE = 0.0


class TraceError(Exception):
    """A trace could not be read at all. / トレースがそもそも読めなかった。"""


# ---------------------------------------------------------------------------
# Reading / 読み込み
# ---------------------------------------------------------------------------

def load(path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Read one trace: its header and its ticks, oldest first.
    トレースを 1 つ読む。見出しと、古い順の刻み。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
    except OSError as failure:
        raise TraceError(f"{path}: {failure}") from failure

    if not lines:
        raise TraceError(f"{path}: the file is empty")

    try:
        header = json.loads(lines[0])
    except ValueError as failure:
        raise TraceError(f"{path}: the first line is not JSON: {failure}") from failure

    if not isinstance(header, dict) or "trace_version" not in header:
        raise TraceError(
            f"{path}: the first line is not a trace header "
            "(no trace_version) — is this a log rather than a trace?")

    ticks: List[Dict[str, Any]] = []
    for number, line in enumerate(lines[1:], start=2):
        try:
            ticks.append(json.loads(line))
        except ValueError as failure:
            raise TraceError(f"{path}:{number}: {failure}") from failure

    # Ticks are aligned on `sim_us`, so a repeated one silently folds many
    # ticks onto one and makes every later comparison meaningless. A writer
    # that stamps a whole frame with the frame's time rather than the tick's
    # does exactly that, so it is refused here rather than quietly believed.
    # 刻みは `sim_us` で対応づけるので、時刻の重複は複数の刻みを黙って 1 つに
    # 畳み、以後の比較を無意味にする。フレームの時刻を刻みの時刻として打つ
    # 書き手はまさにそれをするので、黙って信じずここで断る。
    moments = [tick.get("sim_us") for tick in ticks]
    distinct = len(set(moments))
    if ticks and distinct != len(moments):
        raise TraceError(
            f"{path}: {len(moments)} ticks carry only {distinct} distinct "
            "sim_us values — the writer stamped a whole frame with one time, "
            "so these ticks cannot be lined up against another run")

    return header, ticks


# ---------------------------------------------------------------------------
# Comparing / 比較
# ---------------------------------------------------------------------------

def differs(left: Any, right: Any, tolerance: float) -> bool:
    """Whether two field values count as different.
    2 つの欄の値が食い違いとみなされるか。"""
    both_are_lists = isinstance(left, list) and isinstance(right, list)
    if both_are_lists:
        if len(left) != len(right):
            return True
        return any(differs(a, b, tolerance) for a, b in zip(left, right))

    both_are_numbers = (isinstance(left, (int, float))
                        and isinstance(right, (int, float))
                        and not isinstance(left, bool)
                        and not isinstance(right, bool))
    if both_are_numbers:
        return abs(float(left) - float(right)) > tolerance

    return left != right


def magnitude(left: Any, right: Any) -> Optional[float]:
    """How far apart two values are, when that is a meaningful question.
    2 つの値がどれだけ離れているか。それが意味を持つときだけ答える。"""
    both_are_lists = isinstance(left, list) and isinstance(right, list)
    if both_are_lists and len(left) == len(right):
        parts = [magnitude(a, b) for a, b in zip(left, right)]
        known = [p for p in parts if p is not None]
        return max(known) if known else None

    both_are_numbers = (isinstance(left, (int, float))
                        and isinstance(right, (int, float))
                        and not isinstance(left, bool)
                        and not isinstance(right, bool))
    if both_are_numbers:
        return abs(float(left) - float(right))

    return None


def compare(left: Sequence[Dict[str, Any]], right: Sequence[Dict[str, Any]],
            tolerance: float = DEFAULT_TOLERANCE) -> Dict[str, Any]:
    """Walk two traces together and report the first difference and the drift.

    The two are aligned on `sim_us`, not on position, because one run may have
    recorded more ticks than the other; only the span they share is compared.

    2 つのトレースを並べて歩き、最初の食い違いと、その後の広がりを報告する。

    位置ではなく `sim_us` で対応づける。一方が他方より多くの刻みを記録している
    ことがあるためで、比べるのは両者が共有する区間だけである。
    """
    by_time_right = {tick.get("sim_us"): tick for tick in right}

    first: Optional[Dict[str, Any]] = None
    compared = 0
    drift: List[Dict[str, Any]] = []

    for tick in left:
        moment = tick.get("sim_us")
        other = by_time_right.get(moment)
        if other is None:
            continue

        compared += 1
        found = _first_difference(tick, other, tolerance)

        if found is not None and first is None:
            first = {"sim_us": moment, **found}

        # Once they have parted, follow the altitude gap so the report shows
        # whether it healed or ran away.
        # ひとたび分かれたら、高度の差を追う。報告が、それが収まったのか広がった
        # のかを示せるようにするためである。
        if first is not None:
            gap = magnitude(tick.get("truth_alt"), other.get("truth_alt"))
            if gap is not None:
                drift.append({"sim_us": moment, "truth_alt_gap": gap})

    return {
        "compared_ticks": compared,
        "left_ticks": len(left),
        "right_ticks": len(right),
        "identical": first is None,
        "first_difference": first,
        "drift": _thin(drift),
        "diagnosis": _diagnose(left, by_time_right, first, tolerance),
    }


def _diagnose(left: Sequence[Dict[str, Any]],
              by_time_right: Dict[Any, Dict[str, Any]],
              first: Optional[Dict[str, Any]],
              tolerance: float) -> Optional[Dict[str, Any]]:
    """Name which layer produced the first difference.

    Three questions, asked at the tick BEFORE the one that first differed --
    the state that produced it:

      physx      the wrench going in matched but the state coming out did not,
                 so the same forces gave different motion
      accel      the velocities matched but the accelerometer reading built
                 from them did not, so the arithmetic differed
      jslib      the firmware's inputs matched but the wrench coming back did
                 not, which for an exonerated module means the copy between
                 the two heaps lost or stale-read a field

    最初に食い違った刻みの **1 つ前**、それを生んだ状態について 3 つを問い、
    どの層が食い違いを生んだかを名指しする。
    """
    if first is None:
        return None

    moment = first["sim_us"]
    previous = None
    for index, tick in enumerate(left):
        if tick.get("sim_us") == moment:
            previous = left[index - 1] if index > 0 else None
            break

    if previous is None:
        return {"layer": "unknown",
                "why": "the first difference is the first shared tick, so "
                       "there is no earlier state to attribute it to"}

    other = by_time_right.get(previous.get("sim_us"))
    if other is None:
        return None

    wrench_agreed = not any(
        differs(previous.get(field), other.get(field), tolerance)
        for field in ("out_force", "out_torque", "out_wrench_dt"))
    firmware_input_agreed = not any(
        differs(previous.get(field), other.get(field), tolerance)
        for field in INPUT_FIELDS)

    now = by_time_right.get(moment)
    side = first["side"]
    field = first["field"]

    if side == "output" and firmware_input_agreed:
        return {
            "layer": "jslib",
            "why": "every SfuStepIn field matched on the tick before, and the "
                   f"first difference is {field}: the same bytes went into the "
                   "firmware module and different bytes came back, so suspect "
                   "the heap-to-heap copy in SfuFirmware.jslib",
        }

    if side == "input" and field == "in_accel" and now is not None:
        velocities_agreed = not any(
            differs(now.get(f), by_time_right.get(moment, {}).get(f), tolerance)
            for f in ("truth_vz_before", "in_vz"))
        if velocities_agreed:
            return {
                "layer": "accelerometer",
                "why": "the velocities the reading is built from matched but "
                       "the reading did not: AccelerometerModel.Read produced "
                       "a different answer from the same inputs",
            }

    if side == "input" and wrench_agreed:
        return {
            "layer": "physx",
            "why": "the wrench applied on the tick before matched, but the "
                   f"state coming out of Physics.Simulate differs in {field}: "
                   "the same forces produced different motion",
        }

    return {
        "layer": "host",
        "why": f"the first difference is {side}.{field}, with the previous "
               "tick's wrench "
               + ("matching" if wrench_agreed else "already differing"),
    }


def _first_difference(left: Dict[str, Any], right: Dict[str, Any],
                      tolerance: float) -> Optional[Dict[str, Any]]:
    """The first field of one tick that differs, input side first.
    ある刻みで最初に食い違う欄。入力の側を先に見る。"""
    for side, fields in (("input", INPUT_FIELDS), ("output", OUTPUT_FIELDS),
                         ("truth", TRUTH_FIELDS)):
        for field in fields:
            if field not in left or field not in right:
                continue
            if differs(left[field], right[field], tolerance):
                return {
                    "side": side,
                    "field": field,
                    "left": left[field],
                    "right": right[field],
                    "magnitude": magnitude(left[field], right[field]),
                    "tick_in_frame": [left.get("tick_in_frame"),
                                      right.get("tick_in_frame")],
                }
    return None


def _thin(drift: List[Dict[str, Any]], keep: int = 40) -> List[Dict[str, Any]]:
    """Thin the drift to a readable number of evenly spaced points.
    広がりの記録を、等間隔の読める数まで間引く。"""
    if len(drift) <= keep:
        return drift
    stride = len(drift) / keep
    return [drift[int(index * stride)] for index in range(keep)]


# ---------------------------------------------------------------------------
# Reporting / 報告
# ---------------------------------------------------------------------------

def summarise(name_left: str, name_right: str,
              report: Dict[str, Any]) -> List[str]:
    """The comparison as lines a person reads.
    比較を人が読む行にする。"""
    lines = [f"{name_left}  vs  {name_right}"]
    lines.append(
        f"  ticks     {report['left_ticks']} and {report['right_ticks']}, "
        f"{report['compared_ticks']} at the same virtual time")

    if report["identical"]:
        lines.append("  verdict   IDENTICAL over every shared tick")
        return lines

    first = report["first_difference"]
    at_seconds = (first["sim_us"] or 0) / 1e6
    lines.append(f"  verdict   they part company at {at_seconds:.4f} s "
                 f"(sim_us {first['sim_us']})")
    lines.append(f"  first     {first['side']}.{first['field']}")
    lines.append(f"              left  {first['left']}")
    lines.append(f"              right {first['right']}")
    if first["magnitude"] is not None:
        lines.append(f"              apart by {first['magnitude']:.6g}")
    lines.append(f"              tick in frame {first['tick_in_frame'][0]} "
                 f"and {first['tick_in_frame'][1]}")

    lines.append(_verdict_line(first["side"]))

    diagnosis = report.get("diagnosis")
    if diagnosis:
        lines.append(f"  layer     {diagnosis['layer'].upper()}")
        lines.append(f"              {diagnosis['why']}")

    drift = report["drift"]
    if drift:
        last = drift[-1]
        lines.append(f"  drift     altitude gap reaches "
                     f"{last['truth_alt_gap']:.6g} m by "
                     f"{(last['sim_us'] or 0) / 1e6:.2f} s")
    return lines


def _verdict_line(side: str) -> str:
    """What the side of the first difference implies about the cause.
    最初の食い違いがどちらの側かが、原因について何を意味するか。"""
    if side == "input":
        return ("  means     the HOST differed first: Unity, PhysX or the C# "
                "side produced different input")
    if side == "output":
        return ("  means     the input matched and the OUTPUT differed: the "
                "same bytes entered the firmware module and different bytes "
                "left it")
    return ("  means     only the truth differed, which is downstream of an "
            "output that matched — look at the tolerance")


def main(argv: Optional[List[str]] = None) -> int:
    """Compare two or more traces, each against the first.
    2 つ以上のトレースを、それぞれ最初のものと比べる。"""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("traces", nargs="+",
                        help="two or more .trace.jsonl files")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                        help="below this two floats count as equal")
    parser.add_argument("--json", action="store_true",
                        help="print the machine-readable report")
    parsed = parser.parse_args(argv)

    if len(parsed.traces) < 2:
        parser.error("give at least two traces to compare")

    try:
        loaded = [(path, load(path)) for path in parsed.traces]
    except TraceError as failure:
        print(f"trace_diff: {failure}")
        return 2

    base_path, (base_header, base_ticks) = loaded[0]
    reports = []
    identical = True

    for path, (header, ticks) in loaded[1:]:
        report = compare(base_ticks, ticks, parsed.tolerance)
        report["left"] = {"path": base_path, **base_header}
        report["right"] = {"path": path, **header}
        reports.append(report)
        identical &= report["identical"]

        if not parsed.json:
            for line in summarise(base_path, path, report):
                print(line)
            print()

    if parsed.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))

    return 0 if identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
