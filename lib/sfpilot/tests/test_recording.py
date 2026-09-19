"""
What a `sf pilot` SILS flight's recording guarantees: a bundle the existing
replay tools can find, one per flight, never overwritten.

`sf pilot` の SILS 飛行の記録が保証すること: 既存の再生手段が見つけられる
一式を、飛行ごとに 1 つ、上書きせずに残すこと。

No emulator: these check the placement and naming rules, which are what
`sf sils video` and the SILS GUI actually depend on.
エミュレータは使わない。ここで確かめるのは置き場所と命名の規則であり、
`sf sils video` と SILS GUI が実際に依存しているのはそこだからである。
"""

import re

from sfpilot.recording import BUNDLE_PREFIX, FlightRecording


def test_each_flight_records_into_its_own_directory(tmp_path):
    """Two flights do not share a directory, so neither overwrites the other.

    `finalize_flightlog` keeps exactly one bundle per directory and deletes
    the rest, so sharing one would mean the flight worth watching again is
    gone as soon as another is flown.

    2 回の飛行がディレクトリを共有せず、互いを上書きしないこと。

    `finalize_flightlog` は 1 ディレクトリにつき一式を 1 つだけ残して他を消す。
    共有すれば、もう一度見たい飛行は次の飛行の時点で失われる。
    """
    first = FlightRecording("pilot", root=tmp_path, stamp="20260919t120000")
    second = FlightRecording("pilot", root=tmp_path, stamp="20260919t120500")

    assert first.bundle_dir != second.bundle_dir
    assert first.bundle_dir.parent == second.bundle_dir.parent


def test_the_csv_directory_is_below_the_bundle_directory(tmp_path):
    """The emulator writes into a subdirectory, not the bundle directory.

    `finalize_flightlog` removes the CSV directory once it has zipped it,
    so pointing the emulator at the bundle directory would delete the
    bundle's own neighbours.

    エミュレータの書き込み先が、一式のディレクトリ自身ではなくその下である
    こと。

    `finalize_flightlog` は zip 化後に CSV のディレクトリを消すので、一式の
    ディレクトリを指すと、その隣に置いたものまで消えてしまう。
    """
    recording = FlightRecording("pilot", root=tmp_path).prepare()

    assert recording.flightlog_dir.parent == recording.bundle_dir
    assert recording.flightlog_dir.is_dir()


def test_the_bundle_name_is_one_the_replay_tools_glob_for(tmp_path):
    """The name starts with `sils_` and ends with `.sflog.zip`.

    Both the SILS GUI (`_find_latest_bundle_zip`) and render_video.py glob
    for `sils_*.sflog.zip`; a bundle named otherwise is written but never
    found again.

    名前が `sils_` で始まり `.sflog.zip` で終わること。

    SILS GUI（`_find_latest_bundle_zip`）も render_video.py も
    `sils_*.sflog.zip` で探す。違う名前の一式は、書けても二度と見つからない。
    """
    recording = FlightRecording("say", root=tmp_path, stamp="20260919t120000")

    assert recording.bundle_name.startswith(BUNDLE_PREFIX)
    assert recording.bundle_name.startswith("sils_")
    assert recording.bundle_name.endswith(".sflog.zip")
    # The kind is in the name, so a copied bundle still says what flew it.
    # 種別が名前に入るので、複製した束でも何が飛ばしたものか分かる。
    assert "say" in recording.bundle_name


def test_the_stamp_survives_the_video_command_lower_casing(tmp_path):
    """`sf sils video -m <milestone>` lower-cases; the stamp must not change.

    That command resolves `-m NAME` to `out_<name.lower()>`, so a stamp
    containing an upper-case letter would name a directory that does not
    exist.

    `sf sils video -m <名前>` は小文字化する。日時がそれで変わらないこと。

    同コマンドは `-m NAME` を `out_<小文字>` に解決するので、大文字を含む
    日時は存在しないディレクトリを指すことになる。
    """
    recording = FlightRecording("pilot", root=tmp_path)

    assert recording.stamp == recording.stamp.lower()
    assert re.fullmatch(r"\d{8}t\d{6}", recording.stamp)

    command = recording.video_command()
    assert command == f"sf sils video -m pilot/{recording.stamp}"
    assert command == command.lower()


def test_the_decisions_csv_puts_the_timeline_beside_the_bundle(tmp_path):
    """Each decision becomes one row, timed, so it can be overlaid on a chart.

    It sits BESIDE the bundle rather than inside it: the contents of a
    flight-log v1 bundle are defined by `protocol/spec/flight_log.yaml`,
    and adding a stream there is a protocol change, not a visualisation
    convenience.

    各判断が時刻つきの 1 行になり、グラフに重ねられること。

    束の中ではなく**隣**に置く。フライトログ v1 の中身は
    `protocol/spec/flight_log.yaml` が定めており、そこへストリームを足すのは
    可視化の都合ではなくプロトコルの変更だからである。
    """
    import csv

    class _Verdict:
        action, source, reason = "hover", "arbiter", "確信度が低い"

    recording = FlightRecording("pilot", root=tmp_path).prepare()

    path = recording.write_decisions_csv([
        {"t_s": 1.25, "verdict": _Verdict(), "judgement": None,
         "command": "rc 0 0 0 0"},
    ])

    assert path.parent == recording.bundle_dir
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert rows[0]["t_s"] == "1.25"
    assert rows[0]["action"] == "hover"
    assert rows[0]["reason"] == "確信度が低い"
    assert rows[0]["command"] == "rc 0 0 0 0"


def test_a_run_that_wrote_no_csv_reports_no_bundle(tmp_path):
    """An emulator that died early leaves no bundle, and says so.

    The flight is not failed over it: its decisions are in the trace
    either way, and the operator is told the replay is unavailable.

    早々に終了したエミュレータは一式を残さず、その旨が分かること。

    それで飛行を失敗にはしない。判断はどのみち記録に残っており、操作者には
    再生できないことが伝わる。
    """
    recording = FlightRecording("pilot", root=tmp_path).prepare()

    assert recording.finalize(notes="nothing was written") is None
    assert recording.bundle_path is None
