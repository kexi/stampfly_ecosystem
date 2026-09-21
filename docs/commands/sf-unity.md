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
| `check fly` | 開いているページで ARM → 離陸 → ALT_HOLD → 着地を自動で飛ばし、合否を出す |
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

### 刻みの輪が登録する命令

`help` が出す一覧と同じもの。空間の組（`world.list` ／ `world.load`）は別の担当が登録する。

| 命令 | 引数 | 返るもの |
|------|------|---------|
| `sim.pause` | `paused`（既定 true） | `{"paused": bool}` |
| `sim.step` | `ticks`（既定 1、1〜4000） | `{"ticks": N}` |
| `sim.speed` | `speed`（0.1〜4） | `{"speed": N}` |
| `sim.reset` | — | `{}`（機体を出発点へ。ファームはそのまま） |
| `sim.power_cycle` | — | `{"power_cycles": N}`（時計を 0 に戻し INIT から） |
| `sim.state` | — | 時計・飛行状態・センサの要約 |
| `sim.wait` | `sim_us` または `seconds`、`timeout_s`（既定 60、上限 60） | `{"reached": bool, "requested_sim_us": N, "sim_us": N, "ticks": N}` |
| `vehicle.state` | — | 下表 |
| `rc.set` | `throttle`／`roll`／`pitch`／`yaw`（12 bit の生 ADC、中央 2048）、`alt_hold` | `{}`（`arm` は断る。下記参照） |
| `rc.arm` | `armed`（既定 true） | `{"armed_requested": bool, "pressed": bool, "was_armed": bool, "sim_us": N}` |
| `rc.release` | — | `{}`（スティックをキーボードへ戻す） |

`sim.wait` は指定した**仮想時刻**に達するまで待ってから答える。待っている間もページは刻み続ける
（主スレッドを塞がない）。時刻に達しないまま `timeout_s` が尽きると、時計がどこまで進んだかを
添えた失敗になる。端末からの台本が、実時間を当て推量で眠らずに手順を並べられるようにするための
命令である。

`rc.arm` は実機の送信機の ARM ボタンを 1 回押す動作にあたる。`armed` は「その状態にしたい」の
意味で、**ファームの現在の状態が違うときだけ押す**（同じなら押さない）。押したかどうかは答えの
`pressed` が述べる。スティックには触らないので、スロットルは直前の `rc.set` が置いた場所に留まる。

**ARM のフラグは値ではなくモーメンタリボタンである**（2026-09-22 に判明・修正）。
`firmware/vehicle/tasks/state_task.cpp:339-357` のとおり、電文のフラグは押している間だけ 1 で、
ファームは**立ち上がりごとに arm/disarm をトグル**し、押下の意味を自分の状態から決める。
離しても何も起きない。よって「armed にするために立てるビット」は存在せず、既に ARM された機体で
押せば DISARM になる。`rc.arm` がファームの状態を先に読むのはこのためで、これにより
`armed=true` を 2 回求めても ARM のままである（冪等）。

そのため **`rc.set` は `arm` を断る**。ビットを押し下げ続けることはファームには**1 回**の押下で
あり、`arm: true` を書いた台本は、機体がたまたま何をしていたかで効果の変わる押下 1 回を得ていた。
ARM は `rc.arm` で押す。`alt_hold` はスイッチの位置なので `rc.set` が従来どおり運ぶ。

#### `vehicle.state` が返すもの

| 鍵 | 内容 |
|----|------|
| `sim_us` ／ `ticks` ／ `paused` | 仮想時刻・刻みの数・一時停止しているか |
| `flight_state` ／ `flight_mode` ／ `armed` | 飛行状態・モードの名前と ARM |
| `battery_v` | 電池の端子電圧 [V] |
| `truth` | 真値。`position`・`altitude_m`・`euler_deg`・`tilt_deg`（水平からの角）・`velocity`・`angular_velocity` |
| `estimate` | ファームの推定。`position`・`rotation` |
| `range_down_m` ／ `range_down_valid` ／ `flow_quality` | 下向き ToF とフローの品質 |
| `real_time_ratio` ／ `us_per_tick` ／ `fps` ／ `behind` | 実時間比・1 刻みの所要時間・描画の速さ・遅れているか |
| `power_cycles` | 電源を入れ直した回数 |

真値は `Rigidbody` から、推定はファームから取る。片方を二度読んで食い違いを隠さないためである。

### 追跡カメラが登録する命令

| 命令 | 引数 | 返るもの |
|------|------|---------|
| `camera.zoom` | `distance`（m、0 より大きい）／`step`（正で寄る、負で引く）／`reset`（true で既定へ） | 下表（`camera.state` と同じ） |
| `camera.state` | — | 下表 |

寄せるのはカメラを動かすことで行い、視野角（FOV）は変えない。視野角を狭めると遠近感が平らになり、
機体と壁の間の距離がその距離らしく見えなくなる。目で飛ばす人はそこから接近の速さを判断するので、
寄せるたびに変わるレンズはそれを奪う。距離と高さには同じ倍率が掛かるので、見下ろす角はどの倍率でも
変わらない。

範囲は 0.15 m〜3.0 m で、既定は 0.28 m（1280x720 の画面に機体が幅 200 px ほどで映る。幅は軸方向の 0.0816 m で数える。
対角の 0.12 m ではない）。
範囲外の距離は断らずに範囲の中へ収め、答えに実際に取った値を載せる。ズームを端から端まで振る台本が
欲しいのは両端の絵であって、2 つの失敗ではないからである。`distance`・`step`・`reset` のどれも渡され
なければ失敗する（渡されなかったときにいまの状態を答えると、カメラを動かしたように見えてしまう）。

```bash
sf unity cmd camera.zoom --json '{"distance": 0.2}'   # 0.2 m まで寄せる
sf unity cmd camera.zoom --json '{"step": 2}'          # 2 段寄せる（1 段 = 1.15 倍）
sf unity cmd camera.zoom --json '{"reset": true}'      # 既定の 0.28 m へ戻す
sf unity cmd camera.state                              # いまの距離・高さ・倍率・範囲
```

#### `camera.state` が返すもの

| 鍵 | 内容 |
|----|------|
| `distance_m` ／ `height_m` | カメラが座るよう**求められている**機体の後ろの距離と上の高さ [m] |
| `multiplier` | 既定と比べた倍率（既定で 1、寄れば 1 未満） |
| `current_distance_m` ／ `current_height_m` | カメラが**いまいる**距離と高さ [m] |
| `minimum_distance_m` ／ `maximum_distance_m` | 動ける範囲 [m]（0.15／3.0） |
| `step_ratio` | 1 段で距離に掛かる値（1.15） |
| `default_distance_m` | `reset` が戻る距離 [m]（0.28） |

求めた距離と実際の距離の両方を返すのは、カメラが 0.1 秒ほどかけてずれを詰めるためである。
`camera.zoom` の直後に読むと `current_distance_m` はまだ途中の値で、これを知らない検査は正常な
途中の値を誤りとして見てしまう。

### 画面の操作（キーとマウス）

ページを開いた Chrome の中で効く。表示板の下段にも同じ案内が出る。

| 入力 | 働き |
|------|------|
| マウスホイール 前 ／ 後 | 寄る ／ 引く（1 刻み = 1 段。1 フレームに 4 段まで） |
| F ／ G | 1 段 寄る ／ 引く |
| C | 既定の距離（0.28 m）へ戻す |

操縦とシミュレーションのキー（W A S D、Space、Z、`,` `.`、R、H、`-` `+`、P、N、`[` `]`、B、
Backspace）は `simulator/unity/README.md` の「操縦」の節にある。F・G・C を選んだのは、近くの他の
文字が既に使われているからである。

ホイールの値の単位は環境によって違う（Chrome では 1 刻みにつき 1、デスクトップのプレイヤーでは 120）。
値の大きさからどちらかを判定するので、どちらでも 1 刻み = 1 段になる。

一時停止中（P、または `sim.pause`）でも寄せられる。機体をよく見たいのはまさにそのときである。

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

## 6.5. sf unity check fly

動いている `sf unity serve` が配信し、Chrome で既に開いているページに対して、段階 3 の合格基準を
自動で飛ばす。**ページは先に開いておく**（自動操作のタブでは `?raf=worker` を付ける）。

```bash
sf unity check fly                                   # empty_room で 10 秒保持
sf unity check fly --world gate_course --hold-seconds 20
```

### 手順

`world.load` → `sim.power_cycle` → `sim.reset` → スティック中央 → `rc.arm` → スロットルを上げて離陸
→ ALT_HOLD で保持 → スロットルを下げて着地 → DISARM を、`sf unity cmd` と同じ経路
（`POST /api/cmd`）で順に送る。時点は実時間ではなく**仮想時刻**で刻む（`sim.wait`）ので、
速い機械でも遅い機械でも同じ飛行になる。スティックの値と時点は PlayMode 試験
`FirmwareFlightTest` と `simulator/unity/native/bridge/sfu_rc_script.hpp` に合わせてある。

`sim.power_cycle` を最初に入れるのは、しばらく開いていたページの仮想時計が既に進んでいるためで
ある。入れないと「4 秒を待つ」命令が全てその場で返り、しかも機体はとうに起動を終えている。

### オプション

| オプション | 説明 | 既定 |
|-----------|------|------|
| `--world NAME` | 飛ばす空間 | `empty_room` |
| `--hold-seconds N` | ALT_HOLD で保持する長さ（シミュレーションの秒） | 10 |

### 判定

| 検査 | 合格の条件 |
|------|-----------|
| `held_the_altitude_band` | 保持中の高度が 0.15〜3.0 m に収まる |
| `stayed_flying` | 保持中ずっと FLYING |
| `stayed_near_level` | 傾きが 25 度未満 |
| `reached_altitude_hold` | モードが ALT_HOLD に達する |
| `came_back_down` | 最後の高度が 0.08 m 未満 |
| `ended_idle_and_disarmed` | IDLE_GROUND かつ DISARM |
| `kept_up_with_real_time` | 実時間比が 0.05 以上 |
| `a_tick_cost_less_than_its_length` | 1 刻みの所要時間が 2500 マイクロ秒未満 |

実時間比の下限が緩いのは、自動操作のタブが背面扱いで `?raf=worker` の 16 ms のタイマーで動く
ためである。**前面のタブでの 60fps と実時間比 1.0 は人が確かめる**
（`simulator/unity/README.md` §9）。

### 出力

判定と数値を JSON で標準出力に出し、人が読む要約を続けて出す。不合格なら終了コード 1。
報告には各検査の測定値、高度の時系列、状態遷移とその仮想時刻、発行した全ての `cmd_id` が入る。

```bash
sf unity check fly > flight.json
jq -r '.checks[] | select(.pass|not) | "\(.name): \(.measured) (wanted \(.wanted))"' flight.json
jq -r '.transitions[] | "\(.sim_us/1000000)s \(.to)"' flight.json
```

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
| `fw.boot` | `sim` | ファームが起きた（電源投入ごとに 1 行） |
| `fw.log` | `fw` | ファームの `ESP_LOGx` 1 件（`tag`・`sim_us` つき） |
| `sim.flight_state` | `sim` | 飛行状態・モード・ARM が変わった（`sim_us` つき） |
| `sim.stats` | `sim` | 実時間 1 秒ごとの実時間比・1 刻みの所要時間・描画の速さ |
| `sim.step_overrun` | `sim` | フレームが 1 フレームの刻みの上限に当たった |
| `world.loaded` ／ `world.cleared` ／ `world.rejected` | `world` | 空間の読み込み・片付け・拒否 |

### 量

制御の刻みごとの行は出さない。高レートの信号はフライトログ一式（`.sflog.zip`）に書き、
その記録に `run_id` を入れて突き合わせられるようにする。飛行の速さは `sim.stats` として
**実時間 1 秒ごとに 1 行**だけ出し、状態の変化は `sim.flight_state` として**変わったときだけ**
出す。どちらも刻みごとの行にはしない。

### jq での絞り込み

```bash
LOG=logs/unity/$(cat logs/unity/latest).jsonl

# 飛行の筋書き: 状態遷移だけを仮想時刻つきで
jq -r 'select(.event=="sim.flight_state")
       | "\(.sim_us/1000000)s  \(.data.previous_state) -> \(.data.state)  \(.data.mode) armed=\(.data.armed)"' $LOG

# 追いつけていたか: 1 秒ごとの速さ
jq -r 'select(.event=="sim.stats")
       | "\(.sim_us/1000000)s rt=\(.data.real_time_ratio) \(.data.us_per_tick)us/tick \(.data.fps)fps"' $LOG

# 1 つの命令の流れ（CLI・サーバ・ページ・ファームが時刻順に並ぶ）
jq -c --arg id "c20260920T135444Z-28469a" 'select(.cmd_id == $id)' $LOG

# ファームが警告以上で言ったこと
jq -r 'select(.src=="fw" and (.level=="warn" or .level=="error"))
       | "\(.sim_us) [\(.tag)] \(.msg)"' $LOG

# サーバが拒否した行（0 であること）
jq -c 'select(.event=="log.rejected")' $LOG

# 電源の入れ直しの境目
jq -r 'select(.event=="fw.boot") | "\(.ts) power_cycles=\(.data.power_cycles)"' $LOG
```

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
| `check fly` | Fly ARM, take off, ALT_HOLD and land against the open page, and judge it |
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

### The Commands the Loop Registers

The same list `help` prints. The world group (`world.list`, `world.load`) is registered by its
own owner.

| Command | Arguments | Returns |
|---------|-----------|---------|
| `sim.pause` | `paused` (default true) | `{"paused": bool}` |
| `sim.step` | `ticks` (default 1, 1..4000) | `{"ticks": N}` |
| `sim.speed` | `speed` (0.1..4) | `{"speed": N}` |
| `sim.reset` | — | `{}` (the vehicle back at its spawn; the firmware keeps running) |
| `sim.power_cycle` | — | `{"power_cycles": N}` (clock back to zero, a new firmware from INIT) |
| `sim.state` | — | A summary of the clock, the flight state and the sensors |
| `sim.wait` | `sim_us` or `seconds`, `timeout_s` (default 60, cap 60) | `{"reached": bool, "requested_sim_us": N, "sim_us": N, "ticks": N}` |
| `vehicle.state` | — | See below |
| `rc.set` | `throttle`/`roll`/`pitch`/`yaw` (raw 12-bit ADC, centre 2048), `alt_hold` | `{}` (`arm` is refused — see below) |
| `rc.arm` | `armed` (default true) | `{"armed_requested": bool, "pressed": bool, "was_armed": bool, "sim_us": N}` |
| `rc.release` | — | `{}` (the sticks go back to the keyboard) |

`sim.wait` answers once the given **virtual** time arrives. The page keeps ticking while it waits
(the main thread is not blocked). If `timeout_s` runs out first, it fails and says how far the
clock got. It exists so a script at a terminal can order its steps without sleeping for a guessed
wall-clock duration.

`rc.arm` is one press of the real transmitter's ARM button. `armed` means "bring it to this
state", and the button is pressed **only when the firmware is not already in it**; the answer's
`pressed` says whether a press went out. The sticks are untouched, so the throttle stays wherever
the last `rc.set` put it.

**The ARM flag is a momentary button, not a value** (found and fixed 2026-09-22).
Per `firmware/vehicle/tasks/state_task.cpp:339-357` the wire flag is 1 only while the button is
held, and the firmware **toggles arm/disarm on each rising edge**, deciding what a press means
from its own state; the release does nothing. So there is no bit to set to "armed", and pressing
at an already-armed vehicle would disarm it. That is why `rc.arm` reads the firmware's state
first, which makes it idempotent: asking twice for `armed=true` leaves the vehicle armed.

For the same reason **`rc.set` refuses `arm`**. Holding the bit down is ONE press to the
firmware, so a script that wrote `arm: true` got a single press whose effect depended on what the
vehicle happened to be doing. Press ARM with `rc.arm`. `alt_hold` is a switch position and
`rc.set` still carries it.

#### What `vehicle.state` Returns

| Key | Contents |
|-----|----------|
| `sim_us` / `ticks` / `paused` | Virtual time, tick count, whether it is paused |
| `flight_state` / `flight_mode` / `armed` | The state's and mode's names, and ARM |
| `battery_v` | Terminal voltage [V] |
| `truth` | Ground truth: `position`, `altitude_m`, `euler_deg`, `tilt_deg` (angle from level), `velocity`, `angular_velocity` |
| `estimate` | The firmware's own: `position`, `rotation` |
| `range_down_m` / `range_down_valid` / `flow_quality` | Downward ToF and flow quality |
| `real_time_ratio` / `us_per_tick` / `fps` / `behind` | Real-time ratio, cost per tick, frame rate, whether it is behind |
| `power_cycles` | How many times the power has been cycled |

The truth comes from the `Rigidbody` and the estimate from the firmware, so a disagreement between
the two shows up rather than being hidden by reading one of them twice.

### The Commands the Chase Camera Registers

| Command | Arguments | Returns |
|---------|-----------|---------|
| `camera.zoom` | `distance` (m, greater than zero) / `step` (positive closer, negative further out) / `reset` (true for the default) | The table below (the same as `camera.state`) |
| `camera.state` | — | The table below |

Zooming moves the camera; it never narrows the field of view. A narrower lens flattens perspective,
so the gap between the vehicle and a wall stops looking like the distance it is — and somebody
flying by eye judges closing speed from exactly that. The distance and the height take the same
multiplier, so the angle the camera looks down at is the same at every zoom.

The range is 0.15 m to 3.0 m and the default is 0.28 m (which shows the airframe about 200 px wide
on a 1280x720 picture; the width is counted across an axis, 0.0816 m, not on the 0.12 m diagonal). A distance outside the range is brought inside it rather than
refused, and the answer carries the value actually taken: a script sweeping the zoom from end to
end wants a picture at both ends, not two failures. Passing none of `distance`, `step` or `reset`
fails, because answering the current state would look as though the camera had moved.

```bash
sf unity cmd camera.zoom --json '{"distance": 0.2}'   # in to 0.2 m
sf unity cmd camera.zoom --json '{"step": 2}'          # two steps in (one step = 1.15x)
sf unity cmd camera.zoom --json '{"reset": true}'      # back to the default 0.28 m
sf unity cmd camera.state                              # distance, height, multiplier, range
```

#### What `camera.state` Returns

| Key | Contents |
|-----|----------|
| `distance_m` / `height_m` | Where the camera is **asked** to sit: behind and above the vehicle [m] |
| `multiplier` | Against the default (1 at the default, below 1 when closer) |
| `current_distance_m` / `current_height_m` | Where the camera **actually is** [m] |
| `minimum_distance_m` / `maximum_distance_m` | The range it may move in [m] (0.15 / 3.0) |
| `step_ratio` | What one step multiplies the distance by (1.15) |
| `default_distance_m` | Where `reset` goes [m] (0.28) |

Both the asked-for and the actual distance are returned because the camera closes the gap over
about a tenth of a second. Read straight after a `camera.zoom`, `current_distance_m` is still on
its way, and a check that does not know this would judge a healthy value to be wrong.

### On-Screen Controls (Keys and Mouse)

These work inside the Chrome tab showing the page. The same hints appear on the bottom line of the
readout.

| Input | Effect |
|-------|--------|
| Wheel forward / back | Closer / further out (one notch = one step, up to four steps a frame) |
| F / G | One step closer / further out |
| C | Back to the default distance (0.28 m) |

The piloting and simulation keys (W A S D, Space, Z, `,` `.`, R, H, `-` `+`, P, N, `[` `]`, B,
Backspace) are listed in `simulator/unity/README.md`. F, G and C were chosen because every other
letter near them is already taken.

The unit a wheel reading arrives in depends on the platform (1 per notch in Chrome, 120 in a desktop
player). Which one it is is decided from the reading's own size, so one notch is one step either way.

Zooming works while the simulation is paused (P, or `sim.pause`) — which is exactly when somebody
wants a closer look at the airframe.

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

## 6.5. sf unity check fly

Flies the stage 3 pass criterion against a page a running `sf unity serve` is serving and that is
already open in Chrome. **Open the page first** (with `?raf=worker` in an automated tab).

```bash
sf unity check fly                                   # empty_room, ten seconds of hold
sf unity check fly --world gate_course --hold-seconds 20
```

### The Script

`world.load` -> `sim.power_cycle` -> `sim.reset` -> sticks centred -> `rc.arm` -> throttle up to
take off -> hold in ALT_HOLD -> throttle down to land -> DISARM, all through the same route
`sf unity cmd` uses (`POST /api/cmd`). The moments are paced by **virtual** time rather than the
wall clock (`sim.wait`), so the same flight happens on a fast machine and a slow one. The stick
values and the moments match the PlayMode test `FirmwareFlightTest` and
`simulator/unity/native/bridge/sfu_rc_script.hpp`.

`sim.power_cycle` comes first because a page that has been open a while already has a virtual
clock well past zero; without it every "wait for 4 s" would return at once, on a vehicle that
finished booting long ago.

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--world NAME` | The world to fly in | `empty_room` |
| `--hold-seconds N` | How long to hold altitude, in simulated seconds | 10 |

### The Verdict

| Check | Passes when |
|-------|-------------|
| `held_the_altitude_band` | The altitude while holding stays within 0.15..3.0 m |
| `stayed_flying` | FLYING throughout the hold |
| `stayed_near_level` | The tilt stays under 25 degrees |
| `reached_altitude_hold` | The mode reaches ALT_HOLD |
| `came_back_down` | The final altitude is under 0.08 m |
| `ended_idle_and_disarmed` | IDLE_GROUND and disarmed |
| `kept_up_with_real_time` | The real-time ratio is at least 0.05 |
| `a_tick_cost_less_than_its_length` | One tick costs less than 2500 microseconds |

The real-time bound is loose because an automated tab is treated as backgrounded and runs off
`?raf=worker`'s 16 ms timer. **60 fps and a real-time ratio of 1.0 are checked by a person in a
foreground tab** (`simulator/unity/README.md` section 9).

### Output

The verdict and its numbers go to stdout as JSON, followed by a readable summary. A failing flight
exits 1. The report carries each check's measurement, the altitude samples, the state transitions
with their virtual times, and every `cmd_id` the flight issued.

```bash
sf unity check fly > flight.json
jq -r '.checks[] | select(.pass|not) | "\(.name): \(.measured) (wanted \(.wanted))"' flight.json
jq -r '.transitions[] | "\(.sim_us/1000000)s \(.to)"' flight.json
```

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
| `fw.boot` | `sim` | The firmware came up (one line per power cycle) |
| `fw.log` | `fw` | One firmware `ESP_LOGx` record (with `tag` and `sim_us`) |
| `sim.flight_state` | `sim` | The flight state, mode or ARM changed (with `sim_us`) |
| `sim.stats` | `sim` | Once a real second: real-time ratio, cost per tick, frame rate |
| `sim.step_overrun` | `sim` | A frame hit the per-frame tick ceiling |
| `world.loaded` / `world.cleared` / `world.rejected` | `world` | A world was loaded, cleared or refused |

### Volume

No line is written per control step. High-rate signals go into the flight-log bundle
(`.sflog.zip`), which carries the `run_id` so the two can be matched up. The pacing figures go out
as `sim.stats`, **one line per real second**, and a state change as `sim.flight_state`, **only
when it changes**. Neither becomes a per-tick line.

### Narrowing With jq

```bash
LOG=logs/unity/$(cat logs/unity/latest).jsonl

# The flight's story: the state transitions, with their virtual times
jq -r 'select(.event=="sim.flight_state")
       | "\(.sim_us/1000000)s  \(.data.previous_state) -> \(.data.state)  \(.data.mode) armed=\(.data.armed)"' $LOG

# Did it keep up? The once-a-second pacing
jq -r 'select(.event=="sim.stats")
       | "\(.sim_us/1000000)s rt=\(.data.real_time_ratio) \(.data.us_per_tick)us/tick \(.data.fps)fps"' $LOG

# One command's whole flow (CLI, server, page and firmware in time order)
jq -c --arg id "c20260920T135444Z-28469a" 'select(.cmd_id == $id)' $LOG

# What the firmware said at warning level or above
jq -r 'select(.src=="fw" and (.level=="warn" or .level=="error"))
       | "\(.sim_us) [\(.tag)] \(.msg)"' $LOG

# Lines the server refused (should be none)
jq -c 'select(.event=="log.rejected")' $LOG

# Where one boot ends and the next begins
jq -r 'select(.event=="fw.boot") | "\(.ts) power_cycles=\(.data.power_cycles)"' $LOG
```

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
