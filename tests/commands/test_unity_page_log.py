"""
The page's log lines, checked against the server that receives them

The Unity page composes its JSON Lines in C# (`StampFly.Core.LogJson`), and
`sf unity serve` decides whether to keep or refuse each one
(`lib/sfcli/utils/jsonl_log.validate_record`). The two cannot meet in one
process -- Unity has no Python and pytest has no Unity -- so they meet in a
file: `tests/fixtures/unity_page_log_sample.jsonl` holds one line of every kind
the page produces, regenerated from the live C# by the EditMode test
`LogSampleTest`, and this module puts that file through the server's own
checker. The tests below therefore run in CI with no Unity installed, and fail
whenever the page's format drifts away from what the server accepts.

Unity のページは JSON Lines を C#（`StampFly.Core.LogJson`）で組み立て、
`sf unity serve` が 1 行ずつ残すか断るかを決める
（`lib/sfcli/utils/jsonl_log.validate_record`）。両者は同じ処理の中で出会え
ない ― Unity に Python は無く、pytest に Unity は無い ― ので、ファイルで出会
う。`tests/fixtures/unity_page_log_sample.jsonl` がページの出す各種の行を 1 行
ずつ持ち、EditMode の試験 `LogSampleTest` が今の C# から作り直す。本モジュール
はそのファイルをサーバ自身の検査へ通す。よってこの試験は Unity の無い CI でも
回り、ページの形式がサーバの受け入れる形から離れたときに落ちる。
"""

import json
import sys
from pathlib import Path

# The sf CLI lives in lib/; tests run against the checkout, not an install.
# sf CLI は lib/ にある。試験は導入物ではなくチェックアウトを対象にする。
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "lib"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sfcli.utils import jsonl_log  # noqa: E402

# The relay tests already start a real `sf unity serve` and speak to it. Reusing
# their fixtures and helpers keeps one description of how a server is started,
# rather than a second one that could drift from it. The import is by module
# name, with this directory on the path above, because `tests/` is not a package
# and pytest's own rootdir handling must not be what makes it resolve.
# 中継の試験は既に本物の `sf unity serve` を起動して話す。その前準備と補助を
# 使い回し、サーバの起こし方の記述を 1 つに保つ。2 つ目を書けば離れていく。
# `tests/` はパッケージではないので、上でこのディレクトリをパスへ入れ、
# モジュール名で取り込む。pytest の rootdir の扱いに依存させない。
from test_unity import (  # noqa: E402,F401
    _post,
    build_dir,
    logs_root,
    server,
)

# The run and command the sample belongs to. `LogSampleTest` fixes the same two
# values, so the file is stable across regenerations.
# 見本が属する実行と命令。`LogSampleTest` が同じ 2 つを固定するので、作り直して
# もファイルは変わらない。
SAMPLE_RUN_ID = "20260920T044500Z-1a2b3c4d"
SAMPLE_CMD_ID = "c20260920T044500Z-1a2b3c"

SAMPLE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "unity_page_log_sample.jsonl"


def _sample_records():
    """The sample's lines, decoded. A line that is not JSON fails here rather
    than further down, where the reason would be less clear.
    見本の行を復号したもの。JSON でない行はここで落とす。先へ進めると理由が
    分かりにくくなる。"""
    records, broken = jsonl_log.read_records(SAMPLE_PATH)
    assert broken == 0, f"{SAMPLE_PATH} has {broken} line(s) that are not JSON"
    return records


# ---------------------------------------------------------------------------
# The server's own check / サーバ自身の検査
# ---------------------------------------------------------------------------

def test_every_sample_line_passes_the_servers_validation():
    """Each line the page produces is one `sf unity serve` stores rather than
    refuses. 検査に通り、拒否されずに保存される行であること。"""
    for record in _sample_records():
        reason = jsonl_log.validate_record(record, expect_run_id=SAMPLE_RUN_ID)
        assert reason is None, f"{reason}: {json.dumps(record, ensure_ascii=False)}"


def test_the_sample_covers_every_page_source():
    """The sample exercises each `src` a page may use, so a change to any one
    of them is checked here. ページが使う `src` を全て通ること。1 つが変わっても
    ここで見られるようにする。"""
    sources = {record["src"] for record in _sample_records()}

    assert {"fw", "sim", "world", "bridge", "cmd"} <= sources


def test_one_command_ties_the_page_lines_together():
    """The lines raised while handling one command carry its `cmd_id`, which is
    what `sf unity logs --cmd <id>` selects on.
    1 つの命令の処理中に出た行がその `cmd_id` を持つこと。
    `sf unity logs --cmd <id>` が選ぶのはこれである。"""
    selected = jsonl_log.filter_records(_sample_records(), cmd_id=SAMPLE_CMD_ID)

    assert {record["src"] for record in selected} >= {"cmd", "world", "sim", "fw"}


def test_a_firmware_line_carries_its_tag_and_virtual_time():
    """A firmware line keeps the structure the record had before it became
    text: `src: "fw"`, a `tag`, and the virtual time the firmware logged at
    (`docs/commands/sf-unity.md` §8).
    ファームの行が、文字列になる前の記録の構造を保つこと。`src: "fw"`・`tag`・
    ファームが記録した仮想時刻（`docs/commands/sf-unity.md` §8）。"""
    firmware = [r for r in _sample_records() if r["src"] == "fw"]

    assert firmware, "the sample has no firmware line"
    for record in firmware:
        assert record["event"] == "fw.log"
        assert record["tag"], "a firmware line must name its ESP_LOGx tag"
        assert isinstance(record["sim_us"], int)


def test_no_sample_line_exceeds_the_per_line_cap():
    """A line larger than the cap is refused whole, so the page must stay under
    it. 上限を超える行はまるごと拒否される。ページはその下に収まる必要がある。"""
    for record in _sample_records():
        size = len(json.dumps(record, ensure_ascii=False).encode("utf-8"))
        assert size <= jsonl_log.MAX_LINE_BYTES


def test_the_identifiers_have_the_shapes_the_server_checks():
    """The page issues its own ids when no server answered, so their shape is
    the page's responsibility too.
    サーバが応えなければページが自分で識別子を発行するので、形はページの責任でも
    ある。"""
    assert jsonl_log.is_run_id(SAMPLE_RUN_ID)
    assert jsonl_log.is_cmd_id(SAMPLE_CMD_ID)


# ---------------------------------------------------------------------------
# Through the server / サーバを通して
# ---------------------------------------------------------------------------

def test_the_server_accepts_the_whole_sample_as_one_batch(server):
    """The real endpoint, not just the checker: posting the sample the way the
    page batches it stores every line and rejects none.
    検査だけでなく本物の受け口で確かめる。ページがまとめて送るとおりに投げると、
    全ての行が保存され、1 つも拒否されないこと。"""
    lines = []
    for record in _sample_records():
        # A page may only post lines belonging to the run it was given; the
        # sample carries its own run id, so it is retagged before sending.
        # ページは与えられた実行の行しか送れない。見本は自分の run_id を持つ
        # ので、送る前に付け替える。
        record["run_id"] = server.run_id
        lines.append(record)

    status, answer = _post(server.base_url + "/api/log", {"lines": lines})

    assert status == 200
    assert answer["accepted"] == len(lines)
    assert answer["rejected"] == 0


def test_a_line_from_another_run_is_refused(server):
    """The sample's own run id is not this server's, so an unretagged line is
    refused -- which is what keeps two pages' logs from mixing.
    見本の run_id はこのサーバのものではないので、付け替えない行は拒否される。
    2 つのページのログが混ざらないのはこれによる。"""
    _, answer = _post(server.base_url + "/api/log",
                      {"lines": [_sample_records()[0]]})

    assert answer["accepted"] == 0
    assert answer["rejected"] == 1
