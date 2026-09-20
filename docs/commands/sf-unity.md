# sf unity

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。

## 1. 概要

### このドキュメントについて

Unity 版シミュレータ（WebGL、Chrome のみ対応）の PC 側の入口 `sf unity` の使い方を説明する。
設計は `docs/plans/unity-simulator.md` を参照する。

### 対象読者

ブラウザで動くシミュレータを端末から操作する利用者と、その入口に手を入れる担当者。

### 何をするコマンドか

| 役割 | 内容 |
|------|------|
| 配信 | WebGL のビルドを 127.0.0.1 だけで配信し、Chrome で開く |
| 中継 | 端末で打った命令を、動いているページへ届けて結果を受け取る |
| 記録 | 1 回の実行（サーバ・CLI・ページ）のログを 1 つの JSON Lines ファイルにまとめる |
| ビルドと試験 | Unity CLI（`~/.unity/bin/unity`）を呼ぶ |
| 空間ファイル | `*.world.json` の検査と一覧 |

### 前提

| 事項 | 内容 |
|------|------|
| ブラウザ | Chrome のみ。他のブラウザでの動作は確かめていない |
| Unity CLI | `~/.unity/bin/unity`（1.0.0-beta.8）。`serve`・`cmd`・`logs` には要らない |
| 依存パッケージ | 追加不要（Python 標準ライブラリだけで動く） |
| 通信 | サーバは 127.0.0.1 だけに bind する。LAN には出さない |

## 2. サブコマンド

| サブコマンド | 説明 |
|-------------|------|
| `serve` | WebGL のビルドを配信し、命令を中継する |
| `cmd` | 動いているページへ命令を 1 つ送る |
| `logs` | 1 回の実行のログを絞り込んで表示する |
| `build` | WebGL のビルド（Unity CLI） |
| `test` | EditMode ／ PlayMode 試験（Unity CLI） |
| `open` | Unity エディタでプロジェクトを開く |
| `setup` | `com.unity.pipeline` をプロジェクトに追加する |
| `world validate` ／ `world list` | 空間ファイルの検査と一覧 |

## 3. sf unity serve

WebGL のビルドを 127.0.0.1 で配信し、`sf unity cmd` の命令をページへ中継する。

```bash
sf unity serve
sf unity serve --dir /path/to/webgl --port 8770
sf unity serve --world gate_course --no-browser
```

### オプション

| オプション | 説明 | 既定 |
|-----------|------|------|
| `--dir BUILD_DIR` | 配信する WebGL のビルドのディレクトリ | `simulator/unity/Build/WebGL` |
| `--port N` | 127.0.0.1 の待ち受けポート | 8770 |
| `--no-browser` | Chrome を自動で開かない | 開く |
| `--world NAME` | 起動時に読み込む空間（`?world=<名前>` として渡す） | 指定なし |
| `--coi` | COOP/COEP ヘッダを付ける | 付けない |

`--coi` が要るのは退避先の案（`-pthread`・SharedArrayBuffer）だけで、決定した案は無しで動く。
既定で付けると外部の資源を読み込むページが動かなくなるため、選んだときだけ付ける。

### 配信するファイルの扱い

| 拡張子 | Content-Type | Content-Encoding |
|--------|--------------|------------------|
| `.wasm` | `application/wasm` | — |
| `.wasm.br` | `application/wasm` | `br` |
| `.data.gz` | `application/octet-stream` | `gzip` |
| `.js` | `text/javascript` | — |

圧縮の拡張子を先に外してから種別を決めるので、`Build.wasm.br` は `Content-Encoding: br` 付きの
`application/wasm` になる。ビルドのディレクトリの外へ出るパス（`..`）は 403 で拒否する。

### 起動時に作るもの

| 置き場 | 中身 |
|--------|------|
| `logs/unity/<run_id>.jsonl` | この実行のログ（サーバ・CLI・ページの全ての行） |
| `logs/unity/latest` | 直近の `run_id` を 1 行 |
| `logs/unity/<run_id>.server.json` | ポート・pid・ビルドのディレクトリ（終了時に消す） |

`run_id` は先頭が UTC の日時で、後ろに短い乱数が付く（例: `20260920T044500Z-1a2b3c4d`）。
名前順が時刻順になるようにしてある。

## 4. sf unity cmd

動いているページへ命令を 1 つ送り、結果を標準出力に JSON で出す。失敗すると非 0 で終わる。

```bash
sf unity cmd sim.pause
sf unity cmd world.load --arg name=gate_course
sf unity cmd obstacle.add --json '{"type": "box", "size": [0.5, 0.2, 0.5]}'
sf unity cmd vehicle.state --timeout 3
```

### オプション

| オプション | 説明 | 既定 |
|-----------|------|------|
| `--arg KEY=VALUE` | 引数を 1 つ。繰り返せる。値は文字列のまま | なし |
| `--json JSON` | 引数を JSON のオブジェクトで（`--arg` と合わせる） | なし |
| `--timeout N` | ページの結果を待つ秒数 | 10 |

`--arg` の値を文字列のままにするのは、型をページ側の命令が知っているためである。ここで推測すると
`2026` という名前の空間が数値になってしまう。数値・真偽値を渡したいときは `--json` を使う。

### 失敗のしかた

| 状況 | 終了コード | 表示 |
|------|-----------|------|
| ページが返した結果が失敗 | 1 | ページの `error` |
| ページが繋がっていない | 1 | `no page connected: open the simulator in Chrome first` |
| `sf unity serve` が動いていない | 1 | 起動の案内（`sf unity serve`） |
| ページが時間内に返さない | 1 | `timeout after Ns` |
| `--arg` ／ `--json` の書式が違う | 2 | 書式の説明 |

## 5. sf unity logs

1 回の実行のログを絞り込んで表示する。既定は人が読む 1 行表示。

```bash
sf unity logs                                     # 直近の実行の全ての行
sf unity logs --cmd c20260920T044500Z-1a2b3c      # 1 つの命令の流れ
sf unity logs --src fw,sim --level warn           # ページ側の警告以上
sf unity logs --event cmd. --json                 # 命令の事象を元の行のまま
sf unity logs --since-sim-us 5000000 --follow     # 仮想時刻 5 秒以降を出し続ける
```

### オプション

| オプション | 説明 | 既定 |
|-----------|------|------|
| `--run latest\|RUN_ID` | 読む実行 | `latest` |
| `--cmd CMD_ID` | この `cmd_id` を持つ行だけ | 全て |
| `--src fw,sim` | 出どころ（カンマ区切り） | 全て |
| `--level warn` | この水準以上（`warn` は warn と error） | 全て |
| `--event PREFIX` | 事象名の先頭に一致する行だけ | 全て |
| `--since-sim-us N` | この仮想時刻（マイクロ秒）以降の行だけ | 全て |
| `--follow` | 追記される行を出し続ける | 一度だけ |
| `--json` | 元の行をそのまま出す | 人が読む 1 行 |

条件は全て満たす必要がある（AND で組み合わせる）。壊れた行は数えて最後に報告する。

`jq` で直接見ることもできる。

```bash
jq -c 'select(.cmd_id == "c20260920T044500Z-1a2b3c")' logs/unity/$(cat logs/unity/latest).jsonl
jq -r 'select(.level == "error") | "\(.ts) \(.src) \(.event) \(.msg)"' logs/unity/*.jsonl
```

## 6. sf unity build ／ test ／ open ／ setup

Unity CLI（`~/.unity/bin/unity`）を呼ぶ薄い包み。正確なコマンドの形は
`simulator/unity/README.md` §2 に合わせてある。

```bash
sf unity setup                   # com.unity.pipeline を追加（1 回だけ）
sf unity build                   # WebGL ビルド
sf unity build --release -o dist # 配布用（中継の受け口を入れない）
sf unity test --mode play        # PlayMode 試験
sf unity test --mode edit        # EditMode 試験
sf unity open                    # エディタで開く
```

### 組み立てるコマンド

| sf のコマンド | Unity CLI のコマンド |
|--------------|---------------------|
| `sf unity build` | `unity build <project> --target WebGL --execute-method StampFly.Editor.Builders.WebGLBuilder.Build -o <out> --non-interactive --no-tail --format ndjson` |
| `sf unity build --release` | 上に `-stampflyRelease` を末尾で足す |
| `sf unity test --mode play` | `unity test <project> --mode PlayMode --output <report>.xml --non-interactive --format ndjson` |
| `sf unity setup` | `unity pipeline install --project-path <project>` |
| `sf unity open` | `unity open <project>` |

WebGL には内蔵のコマンドライン用ビルドが無いため `--execute-method` は必須である
（`--target WebGL` だけでは終了コード 2 になる）。

`--release` がダッシュ 1 つの `-stampflyRelease` になり末尾に置かれるのは、`unity build`
自身にその引数が無く、エディタ自身のコマンド行へ届ける必要があるためである。
`WebGLBuilder` が `-buildOutput` を読むのと同じ仕組みで読む。配布用ビルドに命令の中継の
受け口を入れないという判断（計画 §4）は、CLI ではなくビルドの入口が行う。

### 終了コードと出力

| 事項 | 内容 |
|------|------|
| ビルド | Unity CLI の終了コードをそのまま返す |
| 試験 | 合格 0、**1 件でも不合格なら 8**（そのまま返す）。報告書は `logs/unity/test-<run_id>.xml` |
| 出力の保存 | `logs/unity/build-<run_id>.jsonl` ／ `logs/unity/test-<run_id>.jsonl`（`--format ndjson`） |
| Unity CLI が無いとき | 想定の置き場（`~/.unity/bin/unity`）と版を示して 1 で終わる |

## 7. ログの形

PC 側で動く新しいコードのログの決まり（`AGENTS.md`「Logs」）の実装である。形式は JSON Lines
（UTF-8、1 事象 1 行）で、整形や色付けはファイルに入れない。

### 必ず入れる鍵

| 鍵 | 内容 |
|----|------|
| `ts` | UTC、RFC 3339、ミリ秒（例 `2026-09-20T04:45:00.123Z`） |
| `level` | `debug` ／ `info` ／ `warn` ／ `error` |
| `src` | `fw`・`bridge`・`sim`・`world`・`ui`・`cmd`・`server`・`cli`・`build`・`test` |
| `event` | 点区切りの名前（下表） |
| `run_id` | 実行 1 回に 1 つ |
| `msg` | 人が読む短い 1 行 |

### 相関の鍵（あるときだけ）

| 鍵 | 内容 |
|----|------|
| `cmd_id` | 1 つの命令ごと。CLI が発行し、サーバ・ページ・結果の全ての行に付く |
| `boot_id` | ファームウェアの電源投入ごと |
| `sim_us` | 仮想時刻（マイクロ秒） |
| `tick` ／ `frame` | 制御の刻み ／ 描画のコマ |

事象ごとの値は `data` の下に置く。

### 事象の名前

| `event` | 出す側 | いつ |
|---------|--------|------|
| `server.start` | `server` | ポートを確保してログを開いた |
| `server.stop` | `server` | 終了した |
| `server.hello` | `server` | ページが `/api/hello` を呼び `run_id` を受け取った |
| `server.page_connected` | `server` | ページが待ち受けを始めた |
| `server.page_disconnected` | `server` | ページの待ち受けが窓を超えて途絶えた |
| `server.rejected` | `server` | リクエストを拒否した（Origin・Host・パス・メソッド） |
| `cmd.issued` | `cli` | `sf unity cmd` が `cmd_id` を発行して送った |
| `cmd.received` | `server` | CLI からの命令を受け付けた |
| `cmd.forwarded` | `server` | 待っているページへ渡した |
| `cmd.completed` | `server` | ページが結果を返した |
| `cmd.timeout` | `server` | 時間内に結果が来なかった |
| `cmd.no_page` | `server` | ページが繋がっておらず届けられなかった |
| `cmd.result` | `cli` | `sf unity cmd` が受け取った結果を出した |
| `log.accepted` | `server` | ページの行のまとまりを追記した |
| `log.rejected` | `server` | ページの行が検査に通らなかった |
| `build.start` ／ `build.finished` | `build` | Unity CLI のビルドの開始 ／ 終了 |
| `test.start` ／ `test.finished` | `test` | Unity CLI の試験の開始 ／ 終了 |

### 量

制御の刻みごとの行は出さない。高レートの信号はフライトログ一式（`.sflog.zip`）に書き、
その記録に `run_id` を入れて突き合わせられるようにする。

## 8. ページ側が守る約束

Unity の `.jslib` が実装する側の取り決め。全て同一オリジン（`sf unity serve` が配信するページ）
からの呼び出しである。

### 受け口

| メソッドとパス | 送る形 | 返る形 |
|---------------|--------|--------|
| `GET /api/hello` | — | `{"ok": true, "run_id": "...", "server_version": "..."}` |
| `GET /api/cmd/next?wait=25` | — | `{"ok": true, "cmd_id": "...", "command": "...", "args": {...}}` または `{"ok": true, "command": null}` |
| `POST /api/cmd/result` | `{"cmd_id": "...", "ok": true, "data": {...}}` または `{"cmd_id": "...", "ok": false, "error": "..."}` | `{"ok": true}` |
| `POST /api/log` | `{"lines": [ {…}, {…} ]}` | `{"ok": true, "accepted": N, "rejected": M}` |

### 決まり

| 事項 | 内容 |
|------|------|
| `run_id` | `GET /api/hello` で受け取り、送る全ての行に同じ値を入れる。違う値の行は拒否される |
| 応答が無いとき | `/api/hello` が届かないページはローカル配信ではない。URL の引数の経路に落ちる |
| `cmd_id` | サーバから受け取った値をそのまま返し、その命令の処理中に出したログの行にも付ける |
| 待ち受け | `/api/cmd/next` は `wait` 秒（既定 25、上限 60）まで待って `command: null` を返す。ページはすぐ待ち直す |
| 接続の判定 | サーバは直近の `/api/cmd/next` から 70 秒以内をページ接続中とみなす。待ち直しを止めると命令が拒否される |
| ログのまとめ | 数十行または数百ミリ秒ごとに `lines` にまとめて送る。1 行ずつ送らない |
| 1 行の上限 | 直列化して 64 KiB。超える行はまるごと拒否される |
| 行の形 | §7 の必須の鍵を全て入れる。`src` はページ側では `fw`・`sim`・`world`・`ui`・`bridge` を使う |
| ファームウェアのログ | `ESP_LOGx` を受ける側が、レベル・タグ・本文・仮想時刻を文字列にする前の形で受け取り、`src: "fw"` と `tag` を付けた行にする。文字列を解析して構造に戻さない |

## 9. 関連コマンド

| コマンド | 関係 |
|---------|------|
| `sf sim list` | Unity を含むシミュレータの一覧 |
| `sf sim run unity` | `sf unity serve` へ委譲する |
| `sf log viz` | シミュレータが書き出したフライトログ一式を開く |
| `sf params check` | 物理パラメータの整合検査 |

---

<a id="english"></a>

# sf unity

## 1. Overview

### About This Document

How to use `sf unity`, the PC-side entry point for the Unity simulator (WebGL, Chrome only).
The design is in `docs/plans/unity-simulator.md`.

### Target Audience

Anyone driving the browser-based simulator from a terminal, and anyone working on that entry
point.

### What the Command Does

| Role | Detail |
|------|--------|
| Serving | Serves a WebGL build on 127.0.0.1 only and opens it in Chrome |
| Relaying | Delivers a command typed in the terminal to the running page and returns its result |
| Recording | Collects one run's log -- server, CLI and page alike -- into one JSON Lines file |
| Building and testing | Calls the Unity CLI (`~/.unity/bin/unity`) |
| Space files | Validates and lists `*.world.json` |

### Prerequisites

| Item | Detail |
|------|--------|
| Browser | Chrome only; no other browser has been checked |
| Unity CLI | `~/.unity/bin/unity` (1.0.0-beta.8). Not needed by `serve`, `cmd` or `logs` |
| Dependencies | None to add (the Python standard library is enough) |
| Networking | The server binds 127.0.0.1 only; it is never a LAN service |

## 2. Subcommands

| Subcommand | Description |
|------------|-------------|
| `serve` | Serve the WebGL build and relay commands |
| `cmd` | Send one command to the running page |
| `logs` | Filter and show a run's log |
| `build` | WebGL build (Unity CLI) |
| `test` | EditMode / PlayMode tests (Unity CLI) |
| `open` | Open the project in the Unity editor |
| `setup` | Install `com.unity.pipeline` into the project |
| `world validate` / `world list` | Validate and list space files |

## 3. sf unity serve

Serves the WebGL build on 127.0.0.1 and relays `sf unity cmd`'s commands to the page.

```bash
sf unity serve
sf unity serve --dir /path/to/webgl --port 8770
sf unity serve --world gate_course --no-browser
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--dir BUILD_DIR` | The WebGL build directory to serve | `simulator/unity/Build/WebGL` |
| `--port N` | Port on 127.0.0.1 | 8770 |
| `--no-browser` | Do not open Chrome automatically | Opens it |
| `--world NAME` | World to load on startup (passed as `?world=<name>`) | None |
| `--coi` | Send COOP/COEP headers | Off |

`--coi` is needed only by the retired plan (`-pthread`, SharedArrayBuffer); the decided one
runs without it. Sending the headers by default would break any page that loads a resource
from elsewhere, so they go out only when asked for.

### Served Files

| Extension | Content-Type | Content-Encoding |
|-----------|--------------|------------------|
| `.wasm` | `application/wasm` | — |
| `.wasm.br` | `application/wasm` | `br` |
| `.data.gz` | `application/octet-stream` | `gzip` |
| `.js` | `text/javascript` | — |

A compression suffix is stripped before the type is decided, so `Build.wasm.br` is
`application/wasm` with `Content-Encoding: br`. A path leaving the build directory (`..`) is
refused with 403.

### What Startup Creates

| Path | Contents |
|------|----------|
| `logs/unity/<run_id>.jsonl` | This run's log (every line from the server, the CLI and the page) |
| `logs/unity/latest` | The most recent `run_id`, one line |
| `logs/unity/<run_id>.server.json` | Port, pid and build directory (removed on stop) |

A `run_id` starts with its UTC stamp and ends in a short random tail (for example
`20260920T044500Z-1a2b3c4d`), so sorting by name sorts by time.

## 4. sf unity cmd

Sends one command to the running page and prints the result as JSON on stdout. A failure
exits non-zero.

```bash
sf unity cmd sim.pause
sf unity cmd world.load --arg name=gate_course
sf unity cmd obstacle.add --json '{"type": "box", "size": [0.5, 0.2, 0.5]}'
sf unity cmd vehicle.state --timeout 3
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--arg KEY=VALUE` | One argument; repeatable. Values stay strings | None |
| `--json JSON` | Arguments as a JSON object (merged with `--arg`) | None |
| `--timeout N` | Seconds to wait for the page's result | 10 |

`--arg` values stay strings because the page's command handlers know their own types; guessing
here would turn a world named `2026` into a number. Use `--json` for numbers and booleans.

### How It Fails

| Situation | Exit code | Message |
|-----------|-----------|---------|
| The page returned a failure | 1 | The page's `error` |
| No page is connected | 1 | `no page connected: open the simulator in Chrome first` |
| No `sf unity serve` is running | 1 | How to start one (`sf unity serve`) |
| The page did not answer in time | 1 | `timeout after Ns` |
| Malformed `--arg` / `--json` | 2 | The expected form |

## 5. sf unity logs

Filters and shows one run's log. The default is a readable one-line form.

```bash
sf unity logs                                     # every line of the latest run
sf unity logs --cmd c20260920T044500Z-1a2b3c      # one command's flow
sf unity logs --src fw,sim --level warn           # the page's warnings and errors
sf unity logs --event cmd. --json                 # command events, as raw lines
sf unity logs --since-sim-us 5000000 --follow     # from 5 s of virtual time onward
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--run latest\|RUN_ID` | Which run to read | `latest` |
| `--cmd CMD_ID` | Only lines carrying this `cmd_id` | All |
| `--src fw,sim` | Sources, comma-separated | All |
| `--level warn` | That level and above (`warn` keeps warn and error) | All |
| `--event PREFIX` | Only events whose name starts with this | All |
| `--since-sim-us N` | Only lines at or after this virtual time (microseconds) | All |
| `--follow` | Keep printing new lines | Once |
| `--json` | Print the raw lines | Readable one-line form |

Every condition given must hold (they combine with AND). Malformed lines are counted and
reported at the end.

`jq` reads the same file directly.

```bash
jq -c 'select(.cmd_id == "c20260920T044500Z-1a2b3c")' logs/unity/$(cat logs/unity/latest).jsonl
jq -r 'select(.level == "error") | "\(.ts) \(.src) \(.event) \(.msg)"' logs/unity/*.jsonl
```

## 6. sf unity build / test / open / setup

Thin wrappers over the Unity CLI (`~/.unity/bin/unity`). The exact command lines follow
`simulator/unity/README.md` section 2.

```bash
sf unity setup                   # add com.unity.pipeline (once)
sf unity build                   # WebGL build
sf unity build --release -o dist # for distribution (no command relay endpoint)
sf unity test --mode play        # PlayMode tests
sf unity test --mode edit        # EditMode tests
sf unity open                    # open in the editor
```

### The Assembled Commands

| sf command | Unity CLI command |
|------------|-------------------|
| `sf unity build` | `unity build <project> --target WebGL --execute-method StampFly.Editor.Builders.WebGLBuilder.Build -o <out> --non-interactive --no-tail --format ndjson` |
| `sf unity build --release` | The same, with `-stampflyRelease` appended |
| `sf unity test --mode play` | `unity test <project> --mode PlayMode --output <report>.xml --non-interactive --format ndjson` |
| `sf unity setup` | `unity pipeline install --project-path <project>` |
| `sf unity open` | `unity open <project>` |

WebGL has no built-in command-line build, so `--execute-method` is mandatory (`--target WebGL`
alone exits 2).

`--release` becomes the single-dash `-stampflyRelease` placed last because `unity build` has
no such option of its own and the flag has to reach the editor's own command line, which is
where `WebGLBuilder` reads it from -- the mechanism it already uses for `-buildOutput`. The
decision to leave the command relay endpoint out of a distributed build (plan section 4)
belongs to the build method, not the CLI.

### Exit Codes and Output

| Item | Detail |
|------|--------|
| Build | The Unity CLI's exit code, unchanged |
| Tests | 0 on pass, **8 when any test fails** (passed through). Report at `logs/unity/test-<run_id>.xml` |
| Saved output | `logs/unity/build-<run_id>.jsonl` / `logs/unity/test-<run_id>.jsonl` (`--format ndjson`) |
| Unity CLI missing | Names the expected location (`~/.unity/bin/unity`) and version, exits 1 |

## 7. The Log Format

This is the implementation of the logging rules for new PC-side code (`AGENTS.md`, "Logs").
The format is JSON Lines (UTF-8, one event per line); no formatting or colour goes into the
file.

### Required Keys

| Key | Contents |
|-----|----------|
| `ts` | UTC, RFC 3339, milliseconds (e.g. `2026-09-20T04:45:00.123Z`) |
| `level` | `debug` / `info` / `warn` / `error` |
| `src` | `fw`, `bridge`, `sim`, `world`, `ui`, `cmd`, `server`, `cli`, `build`, `test` |
| `event` | A dot-separated name (see below) |
| `run_id` | One per run |
| `msg` | One short human-readable line |

### Correlation Keys, Only When They Apply

| Key | Contents |
|-----|----------|
| `cmd_id` | One per command. Issued by the CLI and carried by the server's, the page's and the result's lines |
| `boot_id` | One per firmware power-up |
| `sim_us` | Virtual time, microseconds |
| `tick` / `frame` | Control step / rendered frame |

Per-event values go under `data`.

### Event Names

| `event` | Producer | When |
|---------|----------|------|
| `server.start` | `server` | The port was bound and the log opened |
| `server.stop` | `server` | The server stopped |
| `server.hello` | `server` | A page called `/api/hello` and received the `run_id` |
| `server.page_connected` | `server` | A page started polling |
| `server.page_disconnected` | `server` | A page stopped polling for longer than the window |
| `server.rejected` | `server` | A request was refused (Origin, Host, path, method) |
| `cmd.issued` | `cli` | `sf unity cmd` issued a `cmd_id` and posted the command |
| `cmd.received` | `server` | A command from the CLI was accepted |
| `cmd.forwarded` | `server` | The command was handed to the waiting page |
| `cmd.completed` | `server` | The page returned a result |
| `cmd.timeout` | `server` | No result arrived in time |
| `cmd.no_page` | `server` | No page was connected, so it could not be delivered |
| `cmd.result` | `cli` | `sf unity cmd` printed the result it received |
| `log.accepted` | `server` | A batch of page lines was appended |
| `log.rejected` | `server` | A page line failed validation |
| `build.start` / `build.finished` | `build` | A Unity CLI build started / ended |
| `test.start` / `test.finished` | `test` | A Unity CLI test run started / ended |

### Volume

No line is written per control step. High-rate signals go into the flight-log bundle
(`.sflog.zip`), which carries the `run_id` so the two can be matched up.

## 8. What the Page Must Honour

The contract the Unity `.jslib` implements. Every call is same-origin, from the page
`sf unity serve` serves.

### Endpoints

| Method and path | Sent | Returned |
|-----------------|------|----------|
| `GET /api/hello` | — | `{"ok": true, "run_id": "...", "server_version": "..."}` |
| `GET /api/cmd/next?wait=25` | — | `{"ok": true, "cmd_id": "...", "command": "...", "args": {...}}` or `{"ok": true, "command": null}` |
| `POST /api/cmd/result` | `{"cmd_id": "...", "ok": true, "data": {...}}` or `{"cmd_id": "...", "ok": false, "error": "..."}` | `{"ok": true}` |
| `POST /api/log` | `{"lines": [ {…}, {…} ]}` | `{"ok": true, "accepted": N, "rejected": M}` |

### Rules

| Item | Detail |
|------|--------|
| `run_id` | Received from `GET /api/hello` and put on every line the page sends. A line with a different value is rejected |
| No answer | A page whose `/api/hello` goes unanswered is not served locally; it falls back to the URL-argument path |
| `cmd_id` | Returned exactly as received, and put on every log line the page produces while handling that command |
| Long-poll | `/api/cmd/next` waits up to `wait` seconds (default 25, cap 60) and then returns `command: null`; the page polls again immediately |
| Connection | The server counts a page as connected for 70 s after its last `/api/cmd/next`. Stop polling and commands are refused |
| Log batching | Send tens of lines, or every few hundred milliseconds, in one `lines` array -- never one line per request |
| Line size cap | 64 KiB serialized; a larger line is rejected whole |
| Line shape | Every required key from section 7. From the page, `src` is one of `fw`, `sim`, `world`, `ui`, `bridge` |
| Firmware logs | Whatever receives `ESP_LOGx` takes the level, tag, body and virtual time before they become a string, and emits a line tagged `src: "fw"` with `tag`. Never parse a string back into structure |

## 9. Related Commands

| Command | Relation |
|---------|----------|
| `sf sim list` | Lists the simulators, Unity included |
| `sf sim run unity` | Delegates to `sf unity serve` |
| `sf log viz` | Opens a flight-log bundle the simulator wrote |
| `sf params check` | Physical-parameter consistency check |
