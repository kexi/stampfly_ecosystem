"""
sfpilot.recording - where a `sf pilot` SILS flight leaves its flight-log bundle.

Every `sf pilot` flight that drives SILS (`run`, `say`, `mission`) records
the same way `sf sils fly` does: the emulator writes flight-log v1 CSVs into
a directory named by `SILS_EMU_FLIGHTLOG`, and once the process has exited
those are assembled into one `sils_*.sflog.zip`. Both halves are
`sfcli.commands.sils`'s -- this module only decides WHERE, and pairs the
bundle with the decision trace.

sfpilot.recording - `sf pilot` の SILS 飛行が、フライトログ一式を置く場所。

SILS を飛ばす `sf pilot` の飛行（`run`・`say`・`mission`）はすべて、
`sf sils fly` と同じやり方で記録する。エミュレータが `SILS_EMU_FLIGHTLOG` で
指定したディレクトリへフライトログ v1 の CSV を書き、プロセス終了後にそれを
1 個の `sils_*.sflog.zip` にまとめる。この両方は `sfcli.commands.sils` のもの
であり、本モジュールが決めるのは**置き場所**と、判断の記録との対応づけだけで
ある。

Why each flight gets its own dated directory: `_finalize_flightlog` keeps
exactly one bundle per directory and deletes the others, so flights sharing
a directory would overwrite each other -- and the flight worth watching
again is usually not the last one. A dated directory also pairs a bundle
with the trace written at the same moment.

飛行ごとに日時のディレクトリを与える理由: `_finalize_flightlog` は 1 つの
ディレクトリにつき束を 1 個だけ残し、他を消す。ディレクトリを共有すると飛行
どうしが上書きし合うが、もう一度見たい飛行はたいてい最後の 1 回ではない。
日時のディレクトリは、同じ時刻に書かれた判断の記録との対応づけも兼ねる。
"""

from datetime import datetime
from pathlib import Path

# Where `sf sils video -m <name>` looks: `simulator/sils/viz/out_<name>`,
# lower-cased by that command. The run directories below therefore use a
# lower-case stamp, so `-m pilot/<stamp>` survives the lower-casing and names
# one flight exactly.
# `sf sils video -m <名前>` が見る場所は `simulator/sils/viz/out_<名前>`（同
# コマンドが小文字化する）。そのため下の実行ディレクトリ名は小文字の日時に
# する。`-m pilot/<日時>` が小文字化を経ても 1 回の飛行を正しく指すためである。
VIZ_SUBDIR = ("simulator", "sils", "viz")
RUN_STAMP_FORMAT = "%Y%m%dt%H%M%S"

# The bundle's name must start with `sils_`: both the SILS GUI
# (simulator/sils/gui/server.py `_find_latest_bundle_zip`) and
# simulator/sils/viz/render_video.py glob for `sils_*.sflog.zip`. A bundle
# named otherwise is written but never found again. The kind follows, so a
# bundle names the subcommand that flew it even once it has been copied out
# of its directory.
# 束の名前は `sils_` で始まる必要がある。SILS GUI（server.py の
# `_find_latest_bundle_zip`）も render_video.py も `sils_*.sflog.zip` で探す
# ためである。違う名前の束は、書けても二度と見つからない。続けて種別を置く。
# ディレクトリの外へ複製した後でも、どのサブコマンドが飛ばしたものか分かる
# ようにするためである。
BUNDLE_PREFIX = "sils_"


class FlightRecording:
    """One flight's recording: where the CSVs go, and where the bundle lands.

    Built before the emulator starts (the environment needs the CSV
    directory) and finished after it exits (the CSVs are only complete once
    the process has closed them).

    飛行 1 回分の記録。CSV の出力先と、束の置き場所を持つ。

    エミュレータの起動前に作り（環境変数が CSV のディレクトリを要する）、
    終了後に仕上げる（CSV が揃うのはプロセスがそれを閉じた後だからである）。
    """

    def __init__(self, kind: str, root: Path = None, stamp: str = None):
        self.kind = kind
        self.stamp = stamp or datetime.now().strftime(RUN_STAMP_FORMAT)
        base = Path(root) if root is not None else _repository_root().joinpath(*VIZ_SUBDIR)
        self.milestone = f"{kind}/{self.stamp}"
        self.bundle_dir = base / f"out_{kind}" / self.stamp
        # A subdirectory, not the bundle directory itself: `finalize_flightlog`
        # removes this whole tree once it has zipped it, so pointing the
        # emulator at the bundle directory would delete the bundle's own
        # neighbours (the decisions CSV written beside it).
        # 束のディレクトリ自身ではなく下位のディレクトリにする。
        # `finalize_flightlog` は zip 化した後この木ごと消すため、エミュレータを
        # 束のディレクトリに向けると、束の隣に置いたもの（判断の CSV）まで
        # 消えてしまう。
        self.flightlog_dir = self.bundle_dir / "flightlog"
        self.bundle_path = None

    def prepare(self) -> "FlightRecording":
        """Make the directories the emulator is about to write into.
        エミュレータがこれから書き込むディレクトリを作る。"""
        self.flightlog_dir.mkdir(parents=True, exist_ok=True)
        return self

    def finalize(self, notes: str, decisions=()):
        """Assemble the bundle. Call only after the emulator has exited.

        Returns the bundle path, or None when the run wrote no CSV at all
        (the emulator died early, or this target is not wired for
        SILS_EMU_FLIGHTLOG) -- the caller reports that rather than failing
        the flight, whose decisions are in the trace either way.

        `decisions` (the Pilot's rows) is written beside the bundle as a
        plain CSV, so a chart of the flight can be overlaid with what was
        decided when. It is written BEFORE the bundling, because that step
        removes the CSV directory but not its parent.

        束を組み立てる。エミュレータの終了後にのみ呼ぶこと。

        束のパスを返す。CSV が 1 つも書かれていなければ None を返す
        （エミュレータが早々に終了した、あるいはこの対象が
        SILS_EMU_FLIGHTLOG に未対応）。呼び出し側はそれを報告するだけで飛行を
        失敗にはしない。判断そのものはどちらにせよ記録に残っている。

        `decisions`（Pilot の判断の行）は束の隣に素の CSV として書く。飛行の
        グラフに「いつ何を決めたか」を重ねられるようにするためである。束を作る
        処理は CSV のディレクトリを消すがその親は消さないので、**先に**書く。
        """
        from sfcli.commands.sils import finalize_flightlog

        if decisions:
            self.write_decisions_csv(decisions)
        self.bundle_path = finalize_flightlog(
            self.flightlog_dir, self.bundle_dir / self.bundle_name, notes=notes,
        )
        return self.bundle_path

    def write_decisions_csv(self, decisions) -> Path:
        """One row per decision, beside the bundle, for overlaying on a chart.

        Deliberately NOT a stream inside the bundle: `protocol/spec/
        flight_log.yaml` defines what a flight-log v1 bundle contains, and
        adding a stream to it is a protocol change, not a visualisation
        convenience. A sibling CSV needs no such change and every plotting
        tool can read it.

        1 判断 1 行の CSV を束の隣に置く。グラフに重ねるためのものである。

        束の**中**のストリームにはしない。フライトログ v1 の中身は
        `protocol/spec/flight_log.yaml` が定めており、そこへストリームを足すのは
        可視化の都合ではなくプロトコルの変更だからである。隣に置く CSV なら
        その変更が要らず、どの作図ツールでも読める。
        """
        import csv

        path = self.bundle_dir / f"decisions_{self.stamp}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["t_s", "action", "source", "reason", "command",
                             "choice", "confidence", "abnormal", "latency_ms"])
            for row in decisions:
                writer.writerow(_decision_row(row))
        return path

    @property
    def bundle_name(self) -> str:
        """`sils_<kind>_<datetime>.sflog.zip`. / 束のファイル名。"""
        return f"{BUNDLE_PREFIX}{self.kind}_{self.stamp}.sflog.zip"

    def video_command(self) -> str:
        """The one line that turns this flight into a video.
        この飛行を動画にする 1 行。"""
        return f"sf sils video -m {self.milestone}"


def _decision_row(row: dict) -> list:
    """One Pilot decision as the CSV's columns.

    Reads the SAME objects the trace writes (`verdict`, `judgement`), so a
    row here and a line in `logs/pilot/*.jsonl` describe one decision.

    Pilot の判断 1 件を CSV の各列にする。

    記録が書くのと同じオブジェクト（`verdict`・`judgement`）を読むので、ここの
    1 行と `logs/pilot/*.jsonl` の 1 行は同じ 1 つの判断を述べる。
    """
    verdict = row.get("verdict")
    judgement = row.get("judgement")
    choice, confidence, abnormal, latency = "", "", "", ""
    if judgement is not None:
        latency = round(judgement.latency_ms, 1)
        answers = getattr(judgement, "answers", None) or {}
        safety = answers.get("safety_action")
        if safety is not None:
            choice = safety.choice
            confidence = round(safety.confidence, 4)
        unusual = answers.get("abnormal")
        if unusual is not None and unusual.noul is not None:
            abnormal = round(unusual.noul, 4)
    return [
        round(row.get("t_s", 0.0), 3),
        getattr(verdict, "action", ""),
        getattr(verdict, "source", ""),
        getattr(verdict, "reason", ""),
        row.get("command", ""),
        choice, confidence, abnormal, latency,
    ]


def _repository_root() -> Path:
    """Walk up to the directory holding PROJECT_PLAN.md, else use cwd.
    PROJECT_PLAN.md を持つディレクトリまで遡る。見つからなければ現在地。"""
    import os

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "PROJECT_PLAN.md").exists():
            return parent
    return Path(os.getcwd())
