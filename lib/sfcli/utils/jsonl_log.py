"""
JSON Lines logging for PC-side sf code (AGENTS.md "Logs" rules, 2026-09-20)

One event per line, UTF-8, no formatting or colour in the file, so that a
single run and a single command can be followed end to end with `grep` and
`jq` alone. This module is the shared implementation of those rules: every
new sf command, local server and PC-side tool writes through it instead of
inventing its own shape.

PC 側で動く sf の新しいコードのための JSON Lines ログ（AGENTS.md「Logs」の
決まり、2026-09-20 制定）。1 事象 1 行、UTF-8、整形や色付けをファイルに
入れない。目的は、1 回の実行と 1 つの命令を `grep` と `jq` だけで端から端
まで追えるようにすること。本モジュールがその決まりの共有実装で、新しい sf
コマンド・ローカルサーバ・PC 側の道具はここを通して書く。

Required keys (never renamed) / 必ず入れる鍵（名前を変えない）:
    ts     UTC, RFC 3339 with milliseconds / UTC・RFC 3339・ミリ秒
    level  debug / info / warn / error
    src    fw, bridge, sim, world, ui, cmd, server, cli, build, test
    event  dot-separated name, e.g. `server.start` / 点区切りの名前
    run_id one per run / 実行 1 回に 1 つ
    msg    one short human-readable line / 人が読む短い 1 行

Correlation keys, only when they apply / 相関の鍵（あるときだけ）:
    boot_id, cmd_id, sim_us, tick, frame
Per-event values go under `data` / 事象ごとの値は `data` の下に置く。

This module is stdlib-only, like the local servers that use it (the classroom
runs offline). 本モジュールは標準ライブラリだけで書く（教室はインターネット
無しで動く前提。ローカルサーバ群と同じ方針）。
"""

import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

# ---------------------------------------------------------------------------
# Vocabulary / 語彙
# ---------------------------------------------------------------------------

# The required keys, checked before a line is written or accepted.
# 必ず入れる鍵。行を書く前・受け入れる前に検査する。
REQUIRED_KEYS: Tuple[str, ...] = ("ts", "level", "src", "event", "run_id", "msg")

# The correlation keys. Present only when they apply; never invented.
# 相関の鍵。当てはまるときだけ入れ、無理に作らない。
CORRELATION_KEYS: Tuple[str, ...] = ("boot_id", "cmd_id", "sim_us", "tick", "frame")

LEVELS: Tuple[str, ...] = ("debug", "info", "warn", "error")

# Rank used by `--level warn` style filters: a threshold keeps its own level
# and everything above it. 絞り込み `--level warn` 用の順位: しきい値以上を残す。
LEVEL_RANK: Dict[str, int] = {name: index for index, name in enumerate(LEVELS)}

# Where a line came from. `fw` is the firmware's ESP_LOGx received before it
# becomes a string; the rest are PC-side or page-side producers.
# 行の出どころ。`fw` はファームウェアの ESP_LOGx を文字列にする前の形で
# 受けたもの。残りは PC 側・ページ側の出し手。
SOURCES: Tuple[str, ...] = (
    "fw", "bridge", "sim", "world", "ui", "cmd", "server", "cli", "build", "test",
)

# One line's maximum size in bytes once serialized. A page that batches its
# logs must split before this; the server rejects anything larger rather than
# letting one runaway line make the file unreadable.
# 直列化した 1 行の上限（バイト）。ページ側はまとめて送る前にこれで分ける。
# サーバはこれを超える行を拒否し、暴走した 1 行でファイルが読めなくなるのを防ぐ。
MAX_LINE_BYTES = 64 * 1024

# ---------------------------------------------------------------------------
# Identifiers / 識別子
# ---------------------------------------------------------------------------

# `20260920T044500Z-1a2b3c4d`: the UTC timestamp comes first so that sorting
# by name sorts by time (AGENTS.md's rule for run_id).
# `20260920T044500Z-1a2b3c4d`: 先頭を UTC の日時にして、名前順が時刻順に
# なるようにする（AGENTS.md の run_id の決まり）。
RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")

# `c20260920T044500Z-1a2b3c`: the same shape with a `c` in front, so a command
# id is never mistaken for a run id at a glance or by a filter.
# `c20260920T044500Z-1a2b3c`: 先頭に `c` を付けた同じ形。命令の識別子が
# 実行の識別子と見分けられなくなることを防ぐ。
CMD_ID_PATTERN = re.compile(r"^c\d{8}T\d{6}Z-[0-9a-f]{6}$")


def utc_now() -> str:
    """Current UTC time as RFC 3339 with milliseconds, e.g.
    `2026-09-20T04:45:00.123Z`. datetime's `isoformat` gives microseconds, so
    the last three digits are cut and `+00:00` is replaced by `Z`.
    現在の UTC 時刻を RFC 3339・ミリ秒で返す。`isoformat` はマイクロ秒まで
    出すため末尾 3 桁を落とし、`+00:00` を `Z` に置き換える。"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _stamp() -> str:
    """Compact UTC stamp `YYYYMMDDTHHMMSSZ` used as an identifier's prefix.
    識別子の先頭に置く詰めた形の UTC 日時 `YYYYMMDDTHHMMSSZ`。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_run_id() -> str:
    """Issue a run id: one per run (one page load, one CLI or test launch).
    The random tail keeps two runs started in the same second apart.
    実行の識別子を発行する。実行 1 回（ページの読み込み 1 回、CLI・試験の
    起動 1 回）に 1 つ。後ろの乱数は同じ秒に始まった 2 つの実行を分ける。"""
    return f"{_stamp()}-{secrets.token_hex(4)}"


def new_cmd_id() -> str:
    """Issue a command id at the entry point that received the command. The
    same value is attached to every line the relaying server, the page, the
    processing and the result produce.
    命令を受けた入口で発行する識別子。中継するサーバ・ページ・処理・結果の
    全ての行に同じ値を付ける。"""
    return f"c{_stamp()}-{secrets.token_hex(3)}"


def is_run_id(value: Any) -> bool:
    """True when `value` has the run id shape. run_id の形かどうか。"""
    return isinstance(value, str) and RUN_ID_PATTERN.match(value) is not None


def is_cmd_id(value: Any) -> bool:
    """True when `value` has the command id shape. cmd_id の形かどうか。"""
    return isinstance(value, str) and CMD_ID_PATTERN.match(value) is not None


# ---------------------------------------------------------------------------
# Validation / 検査
# ---------------------------------------------------------------------------

def validate_record(record: Any, expect_run_id: Optional[str] = None) -> Optional[str]:
    """Check one decoded line against the rules. Returns None when it passes,
    or a short reason when it does not -- callers put that reason in the
    rejection's `data` so a `jq` pass over the file explains itself.

    Checks, in order: it is an object; every required key is present and a
    non-empty string (`ts`, `level`, `src`, `event`, `run_id`, `msg`);
    `level` and `src` are from the fixed vocabularies; `run_id` matches
    `expect_run_id` when one is given; `cmd_id`, when present, has the command
    id shape; `data`, when present, is an object.

    復号した 1 行を決まりに照らす。合格なら None、不合格なら短い理由を返す。
    呼び出し側はその理由を拒否の行の `data` に入れ、ファイルを `jq` で見れば
    理由が分かるようにする。

    検査の順: オブジェクトであること、必須の鍵が全てあり空でない文字列で
    あること、`level` と `src` が決めた語彙であること、`expect_run_id` を
    渡されたとき `run_id` が一致すること、`cmd_id` があればその形であること、
    `data` があればオブジェクトであること。
    """
    if not isinstance(record, dict):
        return "not an object"

    for key in REQUIRED_KEYS:
        value = record.get(key)
        if not isinstance(value, str) or not value:
            return f"missing or empty required key: {key}"

    if record["level"] not in LEVELS:
        return f"unknown level: {record['level']}"
    if record["src"] not in SOURCES:
        return f"unknown src: {record['src']}"

    is_foreign_run = expect_run_id is not None and record["run_id"] != expect_run_id
    if is_foreign_run:
        return "run_id mismatch"

    cmd_id = record.get("cmd_id")
    has_bad_cmd_id = cmd_id is not None and not is_cmd_id(cmd_id)
    if has_bad_cmd_id:
        return "malformed cmd_id"

    data = record.get("data")
    if data is not None and not isinstance(data, dict):
        return "data is not an object"

    return None


# ---------------------------------------------------------------------------
# Writer / 書き出し
# ---------------------------------------------------------------------------

class JsonlLogger:
    """Appends JSON Lines to one file, safely from several threads.

    Opened in append mode and flushed after every line, so a reader
    (`sf unity logs --follow`, `tail -f`, `jq`) sees each event as it
    happens and a process that ends abruptly still leaves complete lines.
    Only one process writes a given file: other processes hand their lines
    to that writer (for `sf unity`, the CLI posts to the running server)
    rather than opening the same path themselves, because interleaved
    appends from several processes can tear a line.

    1 つのファイルへ JSON Lines を追記する。スレッドから安全。

    追記で開き、1 行ごとに flush する。読む側（`sf unity logs --follow`・
    `tail -f`・`jq`）が起きた順に見られ、途中で終了した処理も完全な行だけを
    残す。1 つのファイルを書くのは 1 つのプロセスだけとし、他のプロセスは
    自分で同じパスを開かずその書き手に渡す（`sf unity` では CLI が動いている
    サーバへ POST する）。複数プロセスの追記が混ざると 1 行が壊れるため。
    """

    def __init__(self, path: Path, run_id: str, src: str = "cli") -> None:
        """Open `path` for appending (its parent is created if missing) and
        remember the run id and the default `src` for lines written here.
        `path` を追記で開き（親ディレクトリが無ければ作る）、ここで書く行の
        run_id と既定の `src` を覚える。"""
        self.path = Path(path)
        self.run_id = run_id
        self.default_src = src
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._handle = self.path.open("a", encoding="utf-8")

    # -- writing ----------------------------------------------------------
    def log(
        self,
        event: str,
        msg: str,
        level: str = "info",
        src: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        **correlation: Any,
    ) -> Dict[str, Any]:
        """Write one event and return the record as written.

        `correlation` takes the correlation keys (`cmd_id`, `boot_id`,
        `sim_us`, `tick`, `frame`); a None value is dropped rather than
        written, so callers can pass an optional cmd_id unconditionally.
        Unknown keyword names raise, which catches a typo at the call site
        instead of silently producing a key no filter looks at.

        1 事象を書き、書いた行をそのまま返す。

        `correlation` は相関の鍵（`cmd_id`・`boot_id`・`sim_us`・`tick`・
        `frame`）を受ける。値が None の鍵は書かずに落とすので、呼び出し側は
        あるとは限らない cmd_id をそのまま渡せる。知らない名前は例外にして、
        どの絞り込みも見ない鍵が黙って増えるのを防ぐ。
        """
        unknown = set(correlation) - set(CORRELATION_KEYS)
        if unknown:
            raise TypeError(f"unknown correlation keys: {sorted(unknown)}")

        record: Dict[str, Any] = {
            "ts": utc_now(),
            "level": level,
            "src": src or self.default_src,
            "event": event,
            "run_id": self.run_id,
            "msg": msg,
        }
        for key in CORRELATION_KEYS:
            value = correlation.get(key)
            if value is not None:
                record[key] = value
        if data:
            record["data"] = data

        self.write_record(record)
        return record

    def write_record(self, record: Dict[str, Any]) -> None:
        """Append an already-built record (used for lines a page produced,
        which must be stored as they arrived rather than rebuilt here).
        組み立て済みの行を追記する（ページが出した行に使う。受け取ったままを
        残す必要があり、ここで作り直してはならない）。"""
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()

    def close(self) -> None:
        """Close the file. Further writes raise, which is what we want at the
        end of a run. ファイルを閉じる。以後の書き込みは例外になる。"""
        with self._lock:
            if not self._handle.closed:
                self._handle.close()

    # -- context manager --------------------------------------------------
    def __enter__(self) -> "JsonlLogger":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Reading / 読み出し
# ---------------------------------------------------------------------------

def read_records(path: Path) -> Tuple[list, int]:
    """Read a JSON Lines file into (records, broken_line_count).

    A line that is not valid JSON, or that decodes to something other than an
    object, is counted instead of raising: a log is still worth reading when
    one line was cut short by a process that died mid-write. Callers report
    the count at the end.

    JSON Lines のファイルを (行の一覧, 壊れた行の数) として読む。

    JSON として読めない行、オブジェクト以外に復号される行は、例外にせず
    数える。書いている途中で終了した処理が 1 行を切っていても、ログ全体は
    読む価値があるため。呼び出し側はその数を最後に報告する。
    """
    records = []
    broken = 0
    with Path(path).open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                broken += 1
                continue
            if not isinstance(record, dict):
                broken += 1
                continue
            records.append(record)
    return records, broken


def format_line(record: Dict[str, Any]) -> str:
    """One record as a single human-readable line, for the default display of
    `sf unity logs`. The file itself keeps the JSON Lines form; this shape is
    only produced on the way to the terminal.
    1 行を人が読む形にする。`sf unity logs` の既定の表示に使う。ファイル自体は
    JSON Lines のままで、この形は端末へ出すときにだけ作る。"""
    parts = [
        record.get("ts", "-"),
        f"{record.get('level', '-'):<5}",
        f"{record.get('src', '-'):<6}",
        str(record.get("event", "-")),
    ]
    for key in ("cmd_id", "boot_id"):
        value = record.get(key)
        if value:
            parts.append(f"{key}={value}")
    sim_us = record.get("sim_us")
    if sim_us is not None:
        parts.append(f"sim_us={sim_us}")
    parts.append(str(record.get("msg", "")))

    data = record.get("data")
    if isinstance(data, dict) and data:
        parts.append(json.dumps(data, ensure_ascii=False, sort_keys=True))
    return " ".join(parts)


def filter_records(
    records: Iterable[Dict[str, Any]],
    cmd_id: Optional[str] = None,
    sources: Optional[Iterable[str]] = None,
    level: Optional[str] = None,
    event_prefix: Optional[str] = None,
    since_sim_us: Optional[int] = None,
) -> list:
    """Narrow a sequence of records. Every given condition must hold (they
    combine with AND). `level` keeps that level and everything above it;
    `event_prefix` matches the start of the dotted event name, so `cmd`
    selects `cmd.received`, `cmd.forwarded` and the rest.
    行の並びを絞り込む。渡した条件は全て満たす必要がある（AND で組み合わせ
    る）。`level` はその水準以上を残し、`event_prefix` は点区切りの事象名の
    先頭に一致する（`cmd` で `cmd.received`・`cmd.forwarded` 等が残る）。"""
    source_set = set(sources) if sources else None
    threshold = LEVEL_RANK.get(level, 0) if level else None

    selected = []
    for record in records:
        if cmd_id is not None and record.get("cmd_id") != cmd_id:
            continue
        if source_set is not None and record.get("src") not in source_set:
            continue
        if threshold is not None:
            rank = LEVEL_RANK.get(record.get("level", ""), -1)
            if rank < threshold:
                continue
        if event_prefix and not str(record.get("event", "")).startswith(event_prefix):
            continue
        if since_sim_us is not None:
            sim_us = record.get("sim_us")
            if not isinstance(sim_us, int) or sim_us < since_sim_us:
                continue
        selected.append(record)
    return selected


def sort_by_time(records: Iterable[Dict[str, Any]]) -> list:
    """Order records by `ts`, keeping the file's order for equal stamps
    (Python's sort is stable). One command's flow crosses the CLI, the server
    and the page, and all three write through the same file, so time order is
    what reconstructs it.
    `ts` の順に並べる。同じ時刻は元の順を保つ（Python の並べ替えは安定）。
    1 つの命令の流れは CLI・サーバ・ページにまたがり、3 つとも同じファイルへ
    書くため、時刻順が流れを組み立て直す手がかりになる。"""
    return sorted(records, key=lambda record: str(record.get("ts", "")))


def hostname() -> str:
    """This machine's name, recorded once in `server.start`'s `data` so a log
    copied elsewhere still says where it was produced.
    このマシンの名前。`server.start` の `data` に 1 回だけ記録し、ログを他へ
    写してもどこで出たものか分かるようにする。"""
    try:
        return os.uname().nodename  # type: ignore[attr-defined]
    except AttributeError:  # Windows / Windows 系
        return os.environ.get("COMPUTERNAME", "unknown")
