# Jev による StampFly 自動操縦（`sf pilot`）

状態: **実装中**。作成 2026-09-19、最終更新 2026-09-19（P2b: 前方 ToF の駆動を実装し、結果と**実機確認の手順書**を 4.7 節に追記。実機未確認）。

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。

## 1. 概要

### この文書について

TypeSafe の System One モデル **Jev**（自然言語と状況から、型のついた判断と確率を返すモデル）を判断役に据え、StampFly を自動操縦する計画を述べる。センサーは高速に監視し、判断は Jev に任せる。用途は 3 つ: ①状況監視と安全判断 ②自然言語の指示で飛ぶ ③ミッション中の次の一手。最初は SILS（Software In the Loop Simulation）で確認し、Jev の応答が遅い・不確かなときはその場で待機する。

### 対象読者

`sf pilot` の実装・改修を行う者、および本方式の安全設計を検討する者。

### 実装状況（2026-09-19 時点）

P0、P1、P2a、P2、P3、P4 を実装した。それ以外は未着手である。

| 段階 | 内容 | 状況 |
|------|------|------|
| P0 | 本計画文書、`sf pilot bench`（Jev 往復時間・トークン数の実測） | **実装済み** |
| P1 | `lib/sfpilot` の中核（Monitor / Summarizer / Judge / Arbiter / Executor）、FakeJudge、ReplayLink | **実装済み** |
| P2a | テレメトリ拡張（UDP:5005 を 140B の v2 に。電池電圧・下向き ToF・フロー・地磁気・気圧高度を追加。様式は `firmware/vehicle/docs/detailed_design.md` §10） | **実装済み**（実機未確認） |
| P2b | 前方 ToF の駆動（`sensor_tof_front` 新設、`SensorSnapshot` へのミラー、テレメトリ bit1 の供給）。**実機確認必須。バッテリー電源必須**（USB 給電では前方 ToF が立ち上がらない事例がある） | **実装済み**（**実機未確認** — 手順書は下記 4.7）。SILS は既定のまま基準どおり（28 PASS / 5 KNOWN-FAIL / 1 SKIP、決定論性 SHA256 も一致）|
| P2 | SILS 連携（stdin の `api` 行、SilsLink、場面 3 種）、`sf pilot run --sils`、実機 UDP:5005 の 50Hz 受信 | **実装済み**。Jev 実走で 3 場面すべて確認済み（下記 4.5）|
| P3 | `sf pilot say`（自然言語の指示） | **実装済み**。Jev 実走で 7/10 → 原因を特定して修正 → **10/10**（下記 4.5）|
| P4 | ミッション（経路巡回）と `next_move`、および着陸前手順 | **実装済み**。Jev 実走で応答計数の競合を発見して修正し、**全 6 区間が `as planned` で完走**（下記 4.5）|
| P4b | **前方 ToF による探索**（前方の空きを見て進路を選ぶ）。**前提は 2 つ**: ①前方 ToF が駆動していること（P2b、実装済み・実機未確認）②**SILS に前方距離の模擬があること**（MuJoCo のレイキャスト等。未着手）。②が無いと、判断の誤りを飛ばす前に見つけられない | 未着手（P2b の実機確認と、SILS の前方距離模擬を待つ）|
| P4c | **飛行を見る手段**（`--web` のライブ表示＋飛行後の動画・GUI 再生。4.6 節） | **実装済み** |
| P5 | 実機。事前に往復時間を実測し、送信機を手元に置く | 未着手 |

**前方 ToF の現状（重要）**: 2026-09-19 に駆動を開始した（P2b、下記 4.7）。`TofTask` が前方をリセット保持したまま底面を 0x30 で起動し、その後で前方を 0x31 へ振り直す。テレメトリ v2 の `tof_front` と有効ビット bit1 はここから供給される。**ただし実機未確認である。** また前方は Optional で**バッテリー電源が要る**（USB 給電のみでは起動せず、bit1 は 0 のまま）。**障害物回避・探索（P4b）にはもう 1 つ前提がある — SILS に前方距離の模擬が無い。** 模擬が無いままでは、判断の誤りを実機で初めて知ることになる。

## 2. 設計を決めた制約

調査で分かった以下の事実が設計を決めている。

| 事実 | 出典 | 設計への影響 |
|------|------|-------------|
| Jev は計算機ではない。数値比較・しきい値・時間計算は当てにできない。無関係な state は精度を下げる | docs.typesafe.ai `model-jaggedness/jev-1.13` | 数値判定はすべてコード。Jev には**言葉に要約した状況**だけ渡す |
| 応答時間の保証は無い。入力トークン課金。上限 1,200 req/分 | `cookbooks/parallel_questions`, `models` | Jev は 1〜4Hz の判断層。質問は 1 リクエストにまとめ、状況が変わったときだけ問う |
| PC→機体で連続送信できるのは `rc a b c d`（速度指令）だけ。他は応答待ちでブロックする。API は POS_HOLD 固定 | `firmware/vehicle/tasks/api_task.cpp:433`, `docs/plans/ros2-integration.md:46` | 姿勢安定・位置保持は機体に任せ、PC は速度指令と `stop`/`land` のみ出す |
| テレメトリ: UDP:5005（50Hz、姿勢・位置・速度・飛行状態。**v2 以降は電池電圧・下向き ToF・フロー・地磁気・気圧高度も**）＋ UDP:8890 状態文字列（10Hz、ToF・電池） | `firmware/vehicle/docs/detailed_design.md` §10、`sf_telemetry/include/tello_state.hpp:57` | 監視層は 5005 を主、8890 を補助に使う。400Hz Data Stream は 8890 を占有するので使わない。v2（140B）なら電池も ToF も 5005 だけで揃うため、8890 への依存を減らせる |
| PC からの指令が途絶えても機体は着陸しない（位置保持のみ）。COMM_LOST は送信機（ESP-NOW）だけを見る | `sf_failsafe/failsafe.cpp:108`, `tello-api-reference.md:146` | 実機では送信機を手元に置く（INV-2 のパイロット優先で即解除できる）。機体側の PC 途絶着陸は別計画とする |
| SILS にネットワークは無い。実時間モード＋stdin 入力と stdout の `STATE k=v` 行、API 行の注入口 `sf_api_inject_line()` はある | `simulator/sils/devices/rc_stdin.cpp`, `api_task.cpp:1117` | stdin に `api <行>` を 1 種類足せば、同じコマンド列を SILS に流せる（P2） |

## 3. 構成

```
機体 / SILS（400Hz 制御・推定、POS_HOLD）
   │ テレメトリ 50Hz + 10Hz            ▲ rc 20Hz / stop / land
   ▼                                   │
┌─ Link ──────────────────────────────────────────┐  RealLink(UDP) / ReplayLink(.sflog)
├─ Monitor（50Hz、コードのみ）                    │  しきい値・傾向・継続時間を数値で判定
│    └ 即時安全則: 電池危険・高度逸脱・推定発散 → Jev を待たず stop/land
├─ Summarizer                                     │  数値 → 言葉の状況（JSON、小さく保つ）
├─ Judge（Jev、状況が変わったとき＋1Hz の定期）   │  1 リクエストに全質問。FakeJudge と差し替え可
├─ Arbiter（コードのみ）                          │  期限・鮮度・確信度・行動包絡で採否を決める
└─ Executor                                       │  採用した行動を rc / stop / land に変換、20Hz で送り続ける
```

### 役割分担の原則

Jev が選べるのは**あらかじめ列挙した有限の行動**だけである。`emergency`（モータ即停止＝墜落）はどの質問の選択肢にも入れず、コードの即時安全則にも持たせない。人が送信機・`sf blocks` から出す操作のままとする。Jev の出力は提案であり、Arbiter が通したものだけを実行する。

### Summarizer が作る state

数値そのものではなく、コードが区分した結果を渡す。値は英語にする（モデルの精度が英語で最も高く、これらの語はコードが生成するため）。唯一の例外が `operator_instruction` で、操作者自身の文をそのまま渡す。

```json
{
  "flight": {"phase": "flying", "altitude": "on target", "altitude_trend": "steady",
             "horizontal_drift": "drifting slowly to the right", "attitude": "close to level",
             "position_estimate": "reliable", "ground_distance_sensor": "working normally"},
  "battery": {"level": "running low", "trend": "falling gradually"},
  "operator_instruction": "1m まで上がって前に少し進んで戻ってきて"
}
```

値が不明な項目は「不明」として送らず、項目ごと省く（無関係な state は精度を下げるため）。`lib/sfpilot/summarizer.py` の `assert_no_numbers()` が、数値の混入を送信前に機械的に拒否する。

### Jev への質問

| ID | 種別 | 選択肢 | 使う場面 |
|----|------|--------|---------|
| `safety_action` | Choice | `continue` / `hold` / `land` | 常時（用途①） |
| `abnormal` | Noul | 今の飛行は異常か | 常時。記録と待機判定の補助 |
| `next_move` | Choice | `next_step` / `hold` / `redo_step` / `skip_step` / `return_home` / `land` | ミッション中（用途③、P4） |

ミッションが動いていないときに `next_move` は問わない（存在しない区間について答えさせることになり、精度を下げるため）。

### Arbiter の規則

| 条件 | 扱い |
|------|------|
| 応答が期限（既定 500ms）を超えた | 破棄して**待機**（`rc 0 0 0 0`） |
| 応答到着時点で状況の区分が質問時と変わっていた | 破棄。次の周期で問い直す |
| Choice の確信度が閾値（既定 0.6）未満 | **待機** |
| 上位 2 択が `continue` と `land` で拮抗（差 0.15 以内） | **待機** |
| 行動が包絡の外（高度 0.3〜1.5m、離陸点から半径 2m、速度上限） | 却下して待機 |
| API エラー・通信断 | 再試行はせず待機（次周期で自然に問い直される） |
| 待機が連続 10 秒 | **着陸** |

待機は無償ではない（位置保持は電池を消費し、それ自体では何も解決しない）ため、最後の規則が終わらない待機を着陸に変える。

## 4. 実測

`sf pilot bench -n 20` による実測値（2026-09-19、macOS、本リポジトリの開発環境から、質問 2 問 `safety_action` + `abnormal` を 1 リクエストに同梱）。実行には環境変数 `TYPESAFE_API_KEY` を設定する。

| 項目 | 実測値 |
|------|--------|
| 初回（TLS 確立を含む） | 688.9 ms |
| 2 回目以降 p50 | 231.7 ms |
| 2 回目以降 p95 | 442.2 ms |
| 2 回目以降 最大 | 501.6 ms |
| 2 回目以降 最小 | 181.2 ms |
| 入力トークン数（1 判断あたり） | 558（20 回とも同一。state を固定したため） |
| 出力トークン数（1 判断あたり） | 60 |
| 失敗 | 0 / 20 |
| 費用 | **未計測**（課金明細を確認していない） |

**読み取り**: 接続を使い回した場合の p95（442ms）は期限 500ms に収まるが、最大値 501.6ms がわずかに超えた。初回は TLS 確立の分だけ大きく、操縦ループはこれを起動時に 1 回だけ払う。したがって **JevJudge は接続を 1 本使い回し、起動時に 1 回だけ暖機する**方針とする。P2 で SILS を通した実測を追加し、期限 500ms のままでよいかを再検討する。

**未計測の項目**（P2 以降で埋める）:

| 項目 | 状況 |
|------|------|
| 1 判断あたりの費用 | 未計測。課金明細を確認して記入する |
| SILS 経由での往復時間 | **未計測**（P2 の実装は完了したが、実行環境で 1Password の対話的な解錠ができず Jev 実走を行えていない。下記「P2 の結果」参照） |
| ミッション質問 3 問同梱時のトークン数 | 未計測（P4） |
| 実機の PC↔機体往復時間 | 未計測（P5） |

### 期限 500ms の再検討（P2）

**結論: 既定の 500ms を変えない。** 根拠は次のとおりである。

- SILS 経由の Jev 往復時間はまだ実測できていない（上表）。往復するのは PC と
  TypeSafe の間だけで、SILS は同じ PC の中のパイプであり経路に入らないため、
  P0 の実測（p50 232ms / p95 442ms / max 502ms）から変わる理由が無い。
- 期限を変えるべき根拠が無い状態で動かすと、P0 の実測との対応が切れる。
- 期限を超えた応答は破棄されて待機になるだけで、飛行は続く（待機が 10 秒続けば
  着陸する）。max 502ms がまれに期限をわずかに超えても、失われるのは 1 回の
  判断であって飛行ではない。
- 実測が必要になったときのために、`sf pilot run --deadline-ms` で 1 回の実行
  だけ期限を変えられるようにしてある（`config.py` の既定値は変えない）。

FakeJudge（応答 10ms 固定）での SILS 実走では、期限超過は 3 場面とも 0 回で
あった。これは Jev の速さではなく**経路の他の部分が期限を食わないこと**の確認で
あり、その意味では有効な結果である（50Hz のループ・stdin への書き込み・STATE 行
の解釈が、判断の予算を削っていない）。

## 4.1 P2 の結果

### 実装したもの

| 対象 | 内容 |
|------|------|
| `simulator/sils/devices/rc_stdin.cpp` | stdin に `api <行>`・`wind <fx> <fy> <fz>`・`vbatt <電圧>` を追加 |
| `simulator/sils/devices/scenario.{hpp,cpp}` | `sils_scenario_api_inject()` を公開。`.scn` の `api` 事象と stdin が同じ継ぎ目を通る |
| `simulator/sils/emu/emu_main.cpp` | STATE 行の**末尾に** `x y vx vy vz tof tof_valid batt` を追記 |
| `simulator/sils/devices/virtual_board.{hpp,cpp}` | INA3221 シムの電池電圧を上書きする継ぎ目（SILS 専用、ファーム無改変） |
| `lib/sfpilot/link.py` | `SilsLink`、`RealLink` の UDP:5005 50Hz 受信、電圧→残量の共有関数 |
| `lib/sfpilot/scenes.py` | 場面 3 種（`nominal` / `battery_drop` / `drift`） |
| `lib/sfcli/commands/sils.py` | 実時間起動を `realtime_emu_env()` / `launch_realtime_emu()` に切り出し、`sf sils fly` と共有 |
| `lib/sfcli/commands/pilot.py` | `sf pilot run --sils` |

### 確認できたこと

- **INV-2（パイロット優先）は成立している。** SILS で stdin の `rc` が 20ms ごとに
  中立値を送り続けても、API 誘導は解除されない。実時間モードで確認した
  （`simulator/tests/test_pilot_sils.py` の `test_api_stdin_takes_off_while_the_sticks_stay_parked`）。
  解除則が見るのはスティックの**動き**であり、置いたままの中立は動きではない。
- 既存の決定論性は壊れていない。`simulator/tests/test_realtime_fly.py` の
  SHA256 基準値が一致し、`sf sils regression` も基準どおり 28 PASS / 5 KNOWN-FAIL / 1 SKIP。
  STATE 行の追記も新しい stdin 語も、既定経路では完全な no-op である。
- 場面 3 種とも、判断すべき状況を実際に作れている（下表は FakeJudge での実走）。

| 場面 | 実測した挙動 |
|------|-------------|
| `nominal` | 60 秒飛んで着陸を選ばない。判断は全て `continue` |
| `battery_drop` | 電圧低下が危険域に届き、Monitor の即時安全則で着陸（実測: t=26.4s） |
| `drift` | 35 周期中 16 周期が流れとして区分された（「速く流されている」を含む） |

### `drift` 場面の訂正（2026-09-19）

**P2 のコミットメッセージと当初の文書は、`drift` 場面を「風とフローの読み違いの
組み合わせ」と説明していた。これは不正確だった。** 場面は**風だけ**である。

`--flow-scale`（`SILS_EMU_FLOW_SCALE`）は閉ループの飛行経路に届かない。
`Config::flow_vel_scale` を読むのは `Plant::flow()` だけで、それを呼ぶのは
`simulator/sils/smoke/plant_smoke.cpp`（smoke 試験）のみである。飛行中に
ファームへ渡るフローは `virtual_board.cpp` → `sils_pmw3901::set_motion_from_velocity()`
で合成されており、この乗数を参照しない。

風だけで十分であることを実測で確認した（0.060N・35 秒・実時間エミュレータ、
Monitor の区分を数えたもの）:

| 条件 | 「流されている」周期 | うち「速く」 | 最大速度 | 最大変位 |
|------|------------------|-----------|---------|---------|
| 風 0.060N のみ | 1388 中 **359** | 58 | 0.635 m/s | 1.038 m |
| 風 0.060N ＋ `--flow-scale 0.35` | 1389 中 361 | 58 | 0.638 m/s | 1.041 m |
| 対照（風なし） | 1385 中 **0** | 0 | 0.000 m/s | 0.000 m |

上 2 行の差は実時間エミュレータの実行ごとのばらつきであって、ノブの効果では
ない。風の値 0.060N は据え置いた（`config.py` にある選定根拠の実測 — 0.02N では
区分に届かず、0.06N では約 0.9m まで振れる — は、効かないノブの有無に影響されない）。

`--flow-scale` 自体は修理していない（本計画の範囲外）。
`docs/architecture/simulation-policy.md` の改修バックログ #14 に記載した。

### 見つけた問題（本計画の範囲外・要報告）

**ファームの位置推定の水平軸が、真値に対して入れ替わっている（`sf pilot` より前から存在）。**

`wind 0.02 0 0`（NED の北向き 0.02N）をかけたとき:

| 量 | 実測 |
|----|------|
| 真値 `truth.csv` の `pos_x`（北） | +0.161 m |
| 真値 `truth.csv` の `pos_y`（東） | -0.000 m |
| ファーム推定 `posvel.csv` の `pos_x` | **+0.000 m**（終始ゼロ） |
| ファーム推定 `posvel.csv` の `pos_y` | **-0.138 m** |

北へ押しているのに、推定は東に出る。`sf pilot` の追加とは無関係で、stdin を
介さない既存の `.scn` の `wind` 事象でも再現する。Plant の `setWind` と
`frames::ned_to_enu`（`{n.y, n.x, -n.z}`）はどちらも正しく、真値は正しく北へ動く。
食い違うのは推定側だけである。位置保持自体は自己整合的に閉じている（0.161m →
0.025m と引き戻している）ため、SILS の飛行は成立してしまい、これまで表面化
しなかったものと見られる。

`docs/architecture/simulation-policy.md` の改修バックログにも記載が無い。
本計画の範囲外のため修正していない。**位置に関わる実機との対応付け（POS_HOLD の
評価、フライトログの解析、ROS2 連携）に影響しうるため、別途の調査を推奨する。**

## 4.2 P3 の結果（`sf pilot say`）

### 実装したもの

| 対象 | 内容 |
|------|------|
| `lib/sfpilot/instruction.py` | 数値の抽出（半角・全角・漢数字、m→cm）、組み立て規則、包絡の事前検査、`return_home` の計算 |
| `lib/sfpilot/say.py` | 手順の実行（作業スレッド）と、その間も動き続ける用途①の監視層 |
| `lib/sfpilot/judge.py` | 手順の質問（`step_N_move` / `step_N_amount`）、`ask_questions()`（ID ではなく本体で問う） |
| `lib/sfpilot/config.py` | `InstructionConfig`（量の区分・手順数の上限・確信度の関門） |
| `lib/sfpilot/trace.py` | `write_plan()` — 変換結果を 1 行の JSON で記録 |
| `lib/sfcli/commands/pilot.py` | `sf pilot say`（`--dry-run` / `--yes` / `--no-auto-land` / `--eval`） |
| `lib/sfpilot/tests/say_eval_cases.yaml` | 指示 10 例と、想定する手順の表 |

### 役割分担（設計のとおり）

Jev が答えるのは「k 番目の動作は何か」（Choice、k=1..6）と「k 番目の量はどれだけか」
（Choice: small / medium / large / unspecified）だけで、12 問を 1 リクエストにまとめて
問う。質問は互いの答えを見られない前提（fan-out）なので、各質問に「指示の k 番目の
動作」と完結して書いてある。**数値はモデルに一切渡さず、扱わせない** — 指示中の
「70cm」「1m」「九十度」はコードが抜き出し、m→cm の換算もコードが行い、量の区分から
cm・度への対応は `config.py` の表が持つ。

### 実測（SILS、`--fake`）

「上がって前に進んで戻ってきて着陸して」に相当する手順を完走させ、真値
（`truth.csv`）で軌跡を確認した。

| 確認項目 | 実測 |
|---------|------|
| 手順 | `takeoff` → `up 50` → `forward 50` → `go -50 0 0 50` → `land` の 5 手順を完走 |
| 北へ進んだか | 最大 **N=+0.658m**（東西は |E|=0.000m。北向き開始が効いている） |
| 高度 | 最大 **1.094m**（離陸 0.5m ＋ `up 50`） |
| 離陸点へ戻ったか | `go` 完了時点で **N=+0.00m**（t=26s） |
| 接地位置 | N=−0.382m |
| 監視層 | 飛行中 69 判断。手順の中断は 0 回 |

接地位置が離陸点から 0.38m ずれるのは**降下中の横流れ**であり、`sf pilot say` の
層ではない。静止したホバリングからの `land` は横流れ 0.000m であることを別途確認して
いる（下記「見つけた問題」）。

### 実装中に見つけて直したもの（設計に無く、実測で判明した）

1. **待機の `rc` が移動を打ち消していた。** 安全層が毎周期送る `rc 0 0 0 0` は
   **速度**誘導目標を publish し（`api_task.cpp` の `cmdRc`、mode 2）、`forward` が
   設定した**位置**目標を置き換えてしまう。手順の実行中は待機の `rc` を送らない
   ようにした（`Executor.hold_commands_silently`）。`land`・`stop` は送り続ける —
   安全層が動いている理由そのものだからである。
2. **待機を一律に中断の理由にすると、指示が最初の手順で必ず打ち切られた。** 待機の
   多くは「まだ使える意見が無い」（未着・期限超過・鮮度切れ）で、どの離陸でも起きる。
   中断するのは `land`・`stop` と、**Jev が選んだ**待機だけにした。終わらない待機は
   Arbiter 自身の計時が着陸に変え、着陸はここで中断になる。
3. **`ok` は「到達」であって「停止」ではない。** 機体は許容球 0.15m
   （`kReachRadiusM`）に入った時点で応答するが、進入速度はまだ残っている。その瞬間に
   `land` を送ると降下中も進み続け、離陸点を 0.7〜0.9m 行き過ぎて接地した。手順の間に
   **実測した速度**が 0.05m/s を 1 秒間下回るまで待つようにした（固定の待ち時間では
   なく実測にした理由: 長い移動ほど速度を持ち、単一の固定値は両方には正しくない）。

### 見つけた問題（本計画の範囲外・要報告）

**降下中に横へ流れる。** 静止したホバリングからの `land` は横流れ 0.000m である一方、
移動の直後に着陸すると降下中に 0.3〜0.9m 流れる。位置保持が降下中に効いていない
（または効きが弱い）ように見える。`sf pilot say` の追加とは無関係で、`land` を直接
送るだけで再現する。実機の着陸精度に影響しうるため、別途の調査を推奨する。

### Jev での評価（キーが要る。**実施済み: 4.5 節を参照**）

指示 10 例（単純・複数手順・数値つき・曖昧・範囲外・飛行と無関係を含む）と、想定する
手順を `lib/sfpilot/tests/say_eval_cases.yaml` に置いた。実行:

```bash
TYPESAFE_API_KEY=... sf pilot say --eval lib/sfpilot/tests/say_eval_cases.yaml
```

組み立て規則・数値の抽出・包絡の検査はコードなので `pytest lib/sfpilot` が
キー無しで固定する。この表が測るのは**それ以外**、すなわち「Jev が日本語の指示から
正しい動作と量を選ぶか」だけである。不一致が出た場合、期待のほうが不当に狭い可能性
（「ちょっと移動して」に向きの指定は無い）も併記してある。

## 4.3 P4 の結果（`sf pilot mission`）

### 実装したもの

| 対象 | 内容 |
|------|------|
| `lib/sfpilot/mission.py` | ミッションの読み込み・包絡の事前検査・区間の到達の区分・飛行ループ・コード側の上限 |
| `lib/sfpilot/mission_run.py` | SILS の起動・段取りと、結果の集計表示 |
| `lib/sfpilot/missions/line.yaml` | **現状 SILS で飛べる**経路（1 軸の往復＋上昇＋帰還＋着陸） |
| `lib/sfpilot/missions/square.yaml` | 一辺 0.6m の四角形。**現状は飛べない**（下記「見つけた問題」） |
| `lib/sfpilot/landing.py` | 着陸前手順（実行中の移動を終える → `stop` → 静定待ち → `land`） |
| `lib/sfpilot/config.py` | `MissionConfig`（やり直し上限・時間上限・到達許容）、`LandingConfig`（静定の閾値・上限） |
| `lib/sfpilot/judge.py` | `MissionFakeJudge`（規則ベースの代役。キー不要） |
| `lib/sfcli/commands/pilot.py` | `sf pilot mission`（引数と表示のみ。処理本体は `sfpilot` 側）|

### 役割分担（設計のとおり）

Jev が答えるのは `next_move` の 1 問（`next_step` / `hold` / `redo_step` /
`skip_step` / `return_home` / `land`）だけで、用途①の `safety_action`・`abnormal`
と**同じ 1 リクエスト**に同梱する（`judge.WITH_MISSION`）。数値比較は一切させない —
区間の到達は「目標どおり / 手前で止まった / 行き過ぎた」をコードが距離から区分し、
やり直し回数は「まだ / 1 回 / 上限に達した」という語にし、経過時間は「まだ余裕がある /
半分ほど / 上限が近い」にする。`assert_no_numbers()` が送信前に数値の混入を拒否する。

**コード側の上限（Jev の答えに関わらず強制）**: 区間あたりのやり直し 2 回まで、
ミッション全体の時間上限 180 秒、電池が「残り少ない」になったら進む系の答え
（`next_step`・`redo_step`・`skip_step`）を `return_home` に置き換える、そして
読み込み時の包絡検査。いずれも `_MissionFlight._allow` に集め、却下の理由を
集計と記録に残す。

### 実測（SILS、`--fake`、`line` 経路）

| 場面 | 実測した結末 |
|------|------------|
| `nominal` | 6 区間すべて「目標どおり」で完走。真値で最大 N=+1.376m、\|E\|=0.000m、最大高度 0.808m、接地 N=+0.119m |
| `battery_drop` | 電池が区分をまたいだ時点で `return_home` を選び、帰還して着陸（実測 t=54.5s）|
| `drift` | 区間が「手前で止まった」と区分され、やり直し 2 回で上限に達して「飛ばす」に変わる。理由は記録に残る |

### 着陸前手順（ファームの設計への対応）

**ファームは `land` の受理と同時に水平の位置保持を切る。** これは設計どおりで
あり、欠陥ではない。`firmware/vehicle/components/sf_controller_pid/pid_controller.cpp`
が `computePositionHold()` から `VerticalPhase::Landing` を除外しており、降下の
操縦則を全ての飛行モードで同一にするためである（POS_HOLD の着陸も STABILIZE と
同じ操縦感になる）。

その帰結として、降下に**持ち込んだ**水平速度は、妨げられることなく降下中も
**持ち越される**。SILS での実測（`truth.csv`、`land` 送信から接地までの水平移動）:

| 条件 | 降下中の水平移動 |
|------|---------------|
| 移動の直後に `land`（手順なし） | 0.271 m |
| 着陸前手順を通した `land`（4 回の飛行） | 0.043 / 0.132 / 0.163 / 0.226 m |
| 静止したホバリングからの `land` | 0.000 m |

そこで `sfpilot` 側で次の対応をした（**ファームは無改変**）:

- **`land` を送る経路を Executor 1 か所にまとめた。** 自前で
  `link.send_command("land")` を呼ぶ 2 人目の呼び出し側は、静かに静定しない
  着陸になる。1 か所にすることが、その前の静定を省略不可能にする。
- **着陸前手順**: 実行中の移動があれば応答を待つ（上限 2 秒、超えたら `stop` で
  上書き）→ `stop`（その場の位置保持に戻す）→ 実測した水平速度が 0.05m/s を
  1 秒間下回るまで待つ（上限 6 秒）→ `land`。`ok` の前に `land` が割り込むと
  誘導目標だけが残って降下中も加速するため、移動は放置せず意図して終わらせる。
- **待てない理由のときは同じ手順を短い上限で走らせる**（電池の危険域・推定の発散
  など即時安全則による着陸は、静定の上限 0.5 秒）。省略はしない — 0.5 秒の `stop`
  でも進入速度の大部分は落ちる。`emergency`（モータ停止）は従来どおり本パッケージ
  からは到達できない。
- どの経路でどれだけ待ったか（`urgent` / `settled` / `timed_out` / `waited_s` /
  `ceiling_s`）を記録に残す。

**実機でも起きる見込み（未照合）**: 上記はすべて SILS での実測であり、実機とは
照合していない。ただし根拠はファーム自身の制御則であって SILS 固有の仕組みでは
ないため、実機でも同様に起きると見込まれる。**根治（降下中も位置保持を効かせるか
どうか）は機体側の設計判断であり、本計画の範囲外である。** 本計画は、ファームの
設計を変えずに接地精度を改善する範囲に留めた。

### 見つけた問題（本計画の範囲外・要報告）

**1. 水平軸をまたぐ移動が、衝撃検出により機体を解除する（最重要）。**

南北軸の移動に続けて東西軸へ移動すると、1 秒ほどで
`[WARN] failsafe: Impact detected: 5.5G (x2 @400Hz)` が出て
`Impact/anomaly → emergency DISARM` に至り、飛行が終わる。`sf pilot` は一切
関与しない — 素のエミュレータに `api` の行を打つだけで再現する。

| 送った列 | 結果 |
|---------|------|
| `forward 60` → `right 60` | **解除**（5.5G）|
| `right 60` → `forward 60` | **解除** |
| `left 60` → `forward 60` | **解除** |
| `forward 60` → `go 0 -60 0 50`（斜め）| **解除** |
| `cw 90`（離陸直後の旋回）| **解除**（6.2G）|
| `forward 60` → `forward 60` | 問題なし |
| `right 60` → `right 60` | 問題なし |
| `forward 60` → `up 30` | 問題なし |
| `right 60` 単独 | 問題なし |
| `up 50` → `forward 50` → `go -50 0 0 50`（P3 の列）| 問題なし |

同じ軸の移動の繰り返しと、水平移動の後の垂直移動は影響を受けない。間に 16 秒の
ホバリングを挟んでも再現するので、静定不足ではない。

**原因（2026-09-19 の調査で確定。当初の「SILS の IMU または座標系」という見立ては誤り）**:
5〜6G は空中の異常ではなく**接地の衝撃**である（ピーク時の真値高度 0.007〜0.008m）。
機体は 2 つ目の移動の最中に 0.47m から 1.3 秒で落ちていた。

| 順 | 起きたこと | 根拠 |
|----|-----------|------|
| 1 | ジャイロ z の量子化 1 LSB（0.001064 rad/s）の交番が種になる | `virtual_board.cpp` の 16bit 量子化（実機と同じ） |
| 2 | ヨーレートの D 項が毎ステップ符号反転で増幅（`rate.yaw.td=0.01` が必須。0 にすると起きない） | 実測 |
| 3 | ヨートルクが上限 `rate.yaw.max_torque=1.226e-3` に飽和 | 398 サンプル |
| 4 | ミキサーが 1/κ≈244 倍に拡大し、モータごとの推力がホバリング分担の 82% 振れて duty が 0.31/1.00 に張り付く | `actuator.cpp:271-274` |
| 5 | モータが 200Hz の矩形波に追従できず平均揚力が落ち、落下 | 姿勢は終始穏やか（roll < 1.1°） |

- 初期姿勢の修正（`1d99538c`）は**無関係**（当該差分だけを逆適用したビルドでも同じ 5.2G で再現）
- 既知の課題と同じ機構: `pos_flight.expect:19`・`pos_yaw.expect:18` の既知失敗の注記と
  `simulation-policy.md` 改修バックログ #12（ヨートルク権限の飽和、`rate.yaw.max_torque` の
  余裕の見直し）。`api_flight.scn` は同じ現象を「旋回の前に 0.7m まで上がる」ことで避けている
- 量子化もミキサーもファーム側で実機と同じなので、**SILS 固有とは言えない**（実機ログでは未確認）
- 回帰で見つからなかった理由: 水平の軸をまたぐ移動を含むシナリオが無い

**試した変更（別の作業ツリーで、本線には入れていない）**: `rate.yaw.td` を 0 にすると軸またぎは
通るが回帰が壊れる（`stab_flight` が FAIL）。`rate.yaw.max_torque` を 6e-4 に下げると軸またぎも
`cw 90` も通る（回帰全体は未実測）。上限の引き下げは制御系パラメータの変更であり、実機の
ヨー外乱の記録（`analysis/scripts/yaw_nt_kanazawa/`）での再検証が必須なので、**本計画では
変更せず、ファームの判断事項として引き渡す**。回帰に足す案: `api_cross_axis.scn`
（`takeoff`→`forward 60`→`right 60`→`land`、`duty_max < 0.92`、移動中の `alt_min > 0.3`、
`Impact detected` が出ないこと）。

**影響**: 四角形をはじめ、水平 2 軸を使う経路と離陸直後の旋回は現状飛べない。P3 が
これに当たらなかったのは、その手順が北軸だけで完結していたためである。`square.yaml`
は「P4 が何のためのものか」を示す経路として残し、実際に飛ばす経路は `line.yaml`
（1 軸）とした。試験もそちらを使う。高度を上げて地面に届かなくする回避は、落下そのものを
隠すだけなので自動操縦側には入れない。

**2. 降下中の横流れ**（4.2 節で報告済み）。本節の着陸前手順で軽減したが、根治は
ファーム側の判断事項である。

### Jev での評価（キーが要る。**実施済み: 4.5 節を参照**）

ユーザーが後で実行するコマンド（3 場面）:

```bash
TYPESAFE_API_KEY=... sf pilot mission line --sils --yes                       # 正常
TYPESAFE_API_KEY=... sf pilot mission line --sils --yes --scene battery_drop  # 電池低下
TYPESAFE_API_KEY=... sf pilot mission line --sils --yes --scene drift         # 横流れ
```

キー不要の確認は `--fake` を付ける（規則ベースの代役に差し替わる）。経路の確認だけ
なら `sf pilot mission line --dry-run`。

読み取るべき点: Jev が区間の境目で `next_step` を選ぶか、到達しなかった区間に
`redo_step` を選ぶか、電池が「残り少ない」ときに何を選ぶか（コードは進む系を
`return_home` に置き換えるので、Jev の生の選択との差が集計に出る）。ミッション
質問 3 問同梱時のトークン数も、このとき初めて実測できる。

## 4.5 本物の Jev による初回実走の結果（2026-09-19）

### 要旨

**判断層（Jev と Arbiter）ではなく、state を作るコードに 5 件の不具合があった。**
Jev の判断は、与えられた state に対しては終始妥当だった。誤っていたのは state の
ほうである。最も重い症状は `run nominal` が 7.4 秒で着陸したことだが、その直前の
state は、完璧に保持されたホバリングを「目標より低い」と書き、ほぼ満充電のパックを
「この数秒で急に低下」と書いていた。それを見た Jev が `land`（確率 0.74）を選ぶのは
正しい読み方である。

**なぜ既存の試験が 1 件も捕まえられなかったか**: `FakeJudge` は state の**言葉を
読まない**。何を渡されても固定の答えを返すので、警告だらけの state も正しい state も、
まったく同じ「全件成功」になった。試験一式のどこにも、生成された**言葉そのもの**を
見るものが無かった。これが本件の最大の教訓であり、対策として実測サンプルを
Monitor → Summarizer に通して語を検査する試験を追加した
（`lib/sfpilot/tests/test_nominal_flight_words.py`、実測サンプルは
`lib/sfpilot/tests/fixtures/`）。

### 原因と対処

| # | 症状 | 原因（実測による） | 対処 |
|---|------|------------------|------|
| 1 | 健全なホバリング中ずっと `altitude: "below target"` | `Pilot` が `Monitor(config)` を目標高度なしで生成し、`Monitor` の既定値 0.8m が使われていた。ファームは自動離陸の上昇が**到達した高度をそのまま**保持する（`api_task.cpp` は FLYING 到達時の姿勢から誘導目標を作る。定数ではない）。実測のホバリングは 0.441〜0.483m で、誤差 0.35m は許容幅 0.15m の 2 倍以上 | 固定値をやめ、**機体が実際に保持している高度を採用する**（`Monitor._adopt_target_if_unset`）。採用は FLYING かつ上昇が水平になってからに限る（地上では高度が完璧に安定した 0 であり、それを採ると以後の全飛行が「目標より高い」になる） |
| 2 | 異常が無いのに `battery.trend` が「急に低下」へ | 傾向を**百分率**で判定していた。百分率は瞬時の**負荷がかかった**電圧の線形写像なので、消費と無関係に動く。実測: 離陸だけで 1 秒以内に 4.19V → 3.79V、すなわち 99% → 55% の 44 ポイント低下。さらに定常ホバリングだけでも 0.35 pct/s 減り、10 秒窓の最悪値は 8.9 ポイントで、いずれも閾値 5.0 を超える | 判定を**電圧**に変更（`battery_drop_fast_v = 0.15V` / 20 秒窓）。実測の分離: 通常ホバリング 0.066V 対 `battery_drop` 場面 0.233V。加えて離陸時の負荷段差を窓に入れない（`battery_settle_s`） |
| 3 | 11 判断中 7 回が「質問時から状況の区分が変わった」で破棄 | 区分が境目でばたついていた。実測: `battery_drop` 場面で電池の傾向が 60 秒に **510 回**入れ替わる。窓の最古のサンプルが毎周期出入りするため | 全区分に最小保持時間を導入（`classification_hold_s = 1.0s`、`Monitor._held`）。新しい区分が報告中のものを置き換えるのは、それが続いた後だけにした。実測で 510 回 → **2 回** |
| 4 | ミッションの区間 3・4 が `stopped short` になり、各 2 回やり直して飛ばされた | **応答計数の競合**。`StepRunner._await_step` は応答の**個数**の増加だけを待つ。実測: `takeoff` の応答が `forward 60` 送信の 0.7 秒**後**に届き、次の区間の待ちを即座に満たした。区間は始まった瞬間に「終わった」と宣言され、`classify_arrival` は（正しく）「向かってもいない終点の手前で止まった」と報告した。同じ経路を FakeJudge で飛ばすと 52.6 秒・全区間 `as planned`、Jev では 33.1 秒。差は往復時間が持ち込んだ**時間**だけである | リンクが「未応答の件数」を持つようにし（`SilsLink.replies_outstanding`）、次の移動を送る前に前の応答を待って捨てる（`StepRunner._drain_stale_replies`）。`landing.py` の重複した計数も同じ 1 か所へ寄せた |
| 5 | `say`: 「…戻ってきて着陸して」の末尾に `land` が 2 回 | `_read_verbs` は最初の `none` で打ち切るが、`land` では打ち切らない。質問は fan-out で互いの答えを見られないため、動作 4 つの指示の「5 番目」を問われたモデルは、`none` ではなく最ももっともらしい続きとして再び `land` と答えた。文の妥当な読み方であって、criteria で争うべき誤りではない | 最初の `land` でも打ち切る（着陸で飛行は終わり、その後の動作は存在しない） |

### 併せて直したもの

| 内容 | 理由 |
|------|------|
| `say`: 「戻ってきて」が `return_home` ではなく `back 50` になる | `back` と `return_home` の criteria に**対比**と**例**を書いた（日本語の「戻る」がどちらにも読めるため）。`back` は進む向きの話で終点は新しい場所、`return_home` は行き先の話でそれまでの飛行を帳消しにする、と明示 |
| 事例集の「ちょっと移動して」の期待を `expect_refusal: true` に変更 | 向きの指定が無く、どの水平移動も等しくありうるため、モデルはどれにも高い確信を持てない（実測 0.46）。**拒否が望ましい挙動**であり、期待のほうが誤っていた。`expect_refusal` は既存の形式でそのまま書ける |
| 電池の傾向の区分名から窓の長さを除去 | 区分が「この 10 秒で急に低下」という文字列で、summarizer の変換表がそれを鍵にしていた。窓を 20 秒に変えた瞬間に引きが外れ、**訳されない日本語が Jev へ渡る**ところだった。しかもどこも失敗しない。区分名を定数化し（`BATTERY_TREND_*`）、変換漏れを検出する試験を追加した |
| Arbiter: 上位 2 択が `continue` と `hold` の低確信は破棄せず `hold` として採用 | `drift` の待機 13 回連続と着陸の原因。詳細は下記「`drift` の低確信への対処」。閾値は下げていない |
| 電池の傾向の窓が判定境界でばたついていた | 履歴を窓ちょうどに刈り込んでいたため、最古のサンプルが毎周期出入りして「窓が満ちたか」の答えが反転していた（実測 60 秒で 510 回）。窓より長く保持し、測る範囲を明示的に選ぶようにした（`Monitor._trend_window`）|
| API キーの取得を 1 か所へ集約 | `lib/sfpilot/credentials.py` を新設。環境変数 → macOS キーチェーン（`security find-generic-password`）の順で解決する。6 節参照 |

### Jev による再確認（2026-09-19）

`sf pilot run --sils --scene nominal --duration 60`:

| 項目 | 修正前（`…112337`） | 修正後（`…120119`） |
|------|------------------|------------------|
| 結末 | **7.4 秒で誤着陸** | **60 秒飛び切る（着陸しない）** |
| 判断 | 11 件（continue 3 / hover 7 / land 1） | 61 件（**continue 60** / hover 1） |
| 鮮度切れによる破棄 | **11 件中 7 件** | **0 件** |
| `safety_action` の確信度 | 0.67〜0.78 | **0.97〜0.98** |
| state の高度 | 終始 `below target` | 終始 `on target` |
| state の電池の傾向 | `steady`→`falling gradually`→`fell sharply` | 終始 `steady` |
| 往復時間 | p50 237ms / max 461ms | p50 216ms / p95 326ms / max 521ms |

確信度が 0.67〜0.78 から 0.97〜0.98 へ上がった点が本質である。**state を直しただけで、
Jev の判断は迷いのないものに変わった。** 質問文（`safety_action` の instructions と
criteria）は一切変更していない。

### 残る 4 件の Jev 再確認（全て実施済み）

| 実行 | 結果 |
|------|------|
| `run --scene battery_drop --duration 70`（`…123257`） | **正しく着陸を選んだ。** 電池が健全な間は確信度 0.97〜0.98・abnormal 0.04 で `continue` を続け、傾向が `fell sharply` になった瞬間に `land`（確信度 0.84、abnormal 0.79）。24.3 秒で着陸 |
| `run --scene drift --duration 60`（`…123648`） | **60 秒飛び切る。** 62 判断のうち continue 51 / hover 11。下記の Arbiter 規則により、continue と hold で割れた 3 件が破棄されず待機として採用された |
| `mission line --sils --yes`（`…124014`） | **全 6 区間が `as planned` で完走。** 区間 1〜4 は `next_step`（確信度 0.89〜0.98）。修正前は区間 3・4 が `stopped short` で 2 回ずつやり直したうえ飛ばされていた |
| `say --eval`（10 例） | **10/10 一致**（修正前 7/10）。3 件の不一致はいずれも上表の対処で解消した |

### `drift` の低確信への対処 — Arbiter の規則を 1 つ追加

記録の `probabilities` を読むと、閾値未満の答えは **`continue` と `hold` で割れて**
いた（実測 0.45/0.40、0.53/0.36、0.45/0.41。`land` は 0.11〜0.15 と大きく離れている）。
これはモデルが安全と危険を区別できていないのではない。**どちらも「特筆すべきことの
ない状況」の慎重な読み方**であり、そもそも Arbiter が「使える答えが無い」ときに取る
行動は待機、すなわち `hold` そのものである。

そこで、**上位 2 択が `continue` と `hold` のときに限り、確信度が閾値未満でも
`hold` として採用する**規則を足した（`land` が絡む拮抗は従来どおり破棄する — 飛行の
継続と終了を区別できていないモデルの答えは行動の根拠にしない）。**閾値は下げていない。**

この待機は「Jev が選んだ待機」とは区別して記録する（`chosen_hold`）。手順の列を進める
側は前者では止まるが、後者では止まらない。この区別を入れる前は、むしろ**継続を選好
していた**答え（continue 0.63〜0.72 対 hold 0.23〜0.30）でミッションが最後の区間で
終わっていた。

### 往復時間について（本修正とは無関係の観測）

作業中、`api.typesafe.ai` への接続が一時的に失われ（`curl` で connect が成立せず
タイムアウト。同時刻に `docs.typesafe.ai` は 95ms で応答）、その前後で往復時間が
p50 216ms から 511ms まで悪化した。その間の実行は全判断が期限超過となり、場面の
確認にならなかった（`…120257`、`…120408`）。接続回復後の再実行が上表である。

外的要因ではあるが、**p50 が 216ms から 511ms へ動くだけで全判断が失われる**という
事実自体は、期限 500ms の余裕が薄いことを示している。4 節の「期限 500ms を変えない」
判断は、この観測を踏まえて再検討する価値がある（本作業では変更していない。根拠が
1 回の悪化しかないため）。

キー不要の確認も完了しており、修正の効果は SILS で再現する:

| 実行 | 結果 |
|------|------|
| `run --scene nominal --fake`（`…115901`） | 41 判断すべて `continue`。state は 37 件が `on target / steady`、残りは値が揃う前の 4 件のみ |
| `mission line --fake`（`…120011`） | **全 6 区間が `as planned` で完走**（修正前の Jev 実走では区間 3・4 が `stopped short` で飛ばされた） |
| `pytest simulator/tests lib/sfpilot lib/sfcli lib/sflog` | 元の 368 passed / 1 skipped に対し、**393 passed / 1 skipped（追加 25 件）** |

## 4.6 飛行を見る（P4c、2026-09-19）

「自動操縦のシミュレーションを自分の目で見たい」という要望に応える。見方は 2 通りで、**飛行中**に見るもの（`--web`）と、**飛行後**に見るもの（動画・GUI 再生）である。

### 飛行中に見る — `sf pilot run|say|mission --web`

```bash
sf pilot run --sils --scene drift --fake --web
```

`--web` を付けると 127.0.0.1 に HTTP サーバを立ててブラウザを開く（`--port` で変更、`--no-browser` で自動で開かない）。画面は左に機体、右に判断の流れを置く。

| 欄 | 内容 |
|----|------|
| 上部の帯 | 飛行フェーズ、実行中の手順・区間、判断の件数、待機の連続秒数、Jev の往復時間 p50/p95、期限超過の回数 |
| 3D 表示 | 機体の姿勢・位置・軌跡。`sf telemetry --web` と**同一**の表示（下記） |
| 上から見た図 | 水平位置の軌跡、離陸点、包絡の半径 2m、ミッションなら経路と現在の区間 |
| 判断の流れ | 1 判断 1 行、新しいものが上。時刻／Jev に渡した状況の語（変わった区分を強調）／`safety_action` 3 択の確率を棒で／`abnormal`／`next_move`／Arbiter の採否と理由／実際に送ったコマンド／往復時間。期限超過と破棄は色と文字で区別する |

**データの出どころは記録と同じ 1 か所である。** `lib/sfpilot/events.py` の `EventBus` を `Trace` が持ち、`Trace.write()` が行を書いた直後に同じ行を配信する。ページと `logs/pilot/*.jsonl` は「1 つの行を 2 通りの体裁で見せたもの」であり、別々に組み立ててはいない（別々ならいずれ食い違い、その食い違いは誰かが見ている飛行でしか表に出ない）。

**監視ループは表示を待たない。** サーバは別スレッドで動き、配信は `EventBus` の規則により決してブロックしない（閲覧者の待ち行列が満杯なら**最も古い**出来事を捨てる）。表示が 1 コマ飛ぶのは正しい動作で、50Hz のループが周期を落とすのは正しくない。閲覧者が 0 でも、`--web` を付けなければサーバもスレッドもソケットも作らない。

**秘密情報は画面にも流れない。** 記録ファイルと同じ規則に従う（API キー・環境変数・区分前の生の数値は配信しない）。ページは画面の写真 1 枚で共有されうるため、規則を緩める理由がない。

**3D 表示は複製せず共有する。** `sf telemetry --web` のページに埋め込まれていた 3D シーン（StampFly のモデル・照明・OS 別ズーム付き OrbitControls・duty 駆動のプロペラ回転）を `lib/sfcli/assets/stampfly3d.js` へ切り出し、両ページがこれを読み込む。複製すれば食い違っていき、最初に食い違うのは機体ヨー 90 度のずれを直したクォータニオン変換である（その発見には SILS GUI との数値比較を要した）。three.js と STL は従来どおりローカル配信で、CDN は使わない。

### 飛行後に見る — 動画と GUI 再生

SILS 飛行は毎回フライトログ一式（`sils_pilot_<日時>.sflog.zip` 等）を残し、終了時に動画にする 1 行を表示する。

```
  flight log  : .../simulator/sils/viz/out_pilot/20260919t131801/sils_pilot_20260919t131801.sflog.zip
  watch it    : sf sils video -m pilot/20260919t131801
```

置き場所は `simulator/sils/viz/out_<種別>/<日時>/` とし、飛行ごとに分ける。`finalize_flightlog()` は 1 ディレクトリにつき一式を 1 つだけ残して他を消すので、共有するともう一度見たい飛行が次の飛行の時点で失われるためである。日時は小文字にしてある（`sf sils video -m` が名前を小文字化するため）。記録（`logs/pilot/<日時>.jsonl`）と一式は同じ日時で対応づく。

### なぜ今まで一式が出なかったか（原因は 3 つ重なっていた）

| # | 原因 | 場所 | 対処 |
|---|------|------|------|
| A | `sf pilot run --sils` が記録を**有効化していなかった**（`realtime_emu_env()` に `flightlog_dir` を渡していないため `SILS_EMU_FLIGHTLOG` が付かず、エミュレータ側は何も書かない） | `lib/sfcli/commands/pilot.py` | `sf sils fly` と同じく `<一式>/flightlog` を渡す |
| B | `say`・`mission` は CSV を書いていたが、**一式にまとめる処理を誰も呼んでいなかった**（`_finalize_flightlog()` の呼び出しは `sils.py` 内の 4 か所だけ）。加えて CSV の出力先が一式のディレクトリ自身で、まとめる処理がそれを消してしまう配置だった | `lib/sfcli/commands/pilot.py`、`lib/sfpilot/mission_run.py` | エミュレータの**終了後**に既存の `finalize_flightlog()` を呼ぶ。CSV は下位ディレクトリへ |
| C | **エミュレータが `quit` で終わるとき、フライトログを閉じていなかった。** `emu_main.cpp` の `quit` 経路は `std::_Exit(0)` を呼ぶが、`_Exit` は stdio の書き出しを行わない。通常終了の経路にある `sils_emu_flightlog_close()` がこちらには無く、各ストリームの未書き出し行が失われていた。とりわけ最も低速な `status.csv`（1Hz）は見出し行さえ失い、0 バイトのファイルが残って `pd.read_csv` が `No columns to parse from file` で拒否していた | `simulator/sils/emu/emu_main.cpp` | `_Exit` の前に `sils_emu_flightlog_close()` を呼ぶ（1 行） |

C が `sf sils scenario` で表に出なかったのは、そちらが時間を走り切って**通常経路**から抜けるためである。`sf pilot` は `quit` を送って終わるので、この経路だけが壊れていた。これが本件で SILS の C/C++ に手を入れた唯一の箇所である。

併せて `sf sils video -m <名前>` が、区切りを含む名前（`pilot/<日時>`）でも動画の出力先を誤らないようにした（末尾の区切りだけをファイル名に使う）。

## 4.7 P2b の結果（前方 ToF の駆動、2026-09-19）

### 要旨

前方 ToF を駆動し、専用トピック `sensor_tof_front` → `SensorSnapshot` → テレメトリ v2 の `tof_front` / bit1 という経路を通した。SILS で確認済み、**実機未確認**（手元に実機が無いため。手順書は下記）。

**設計の出発点は「前方を足したことで底面 ToF と高度保持が一切変わらないこと」である。** 底面 ToF は高度推定の唯一の鉛直観測（気圧は既定で非融合）で Critical、前方は Optional にすぎない。順序・読み出し・失敗処理はすべてこの一線から導いた。

### 実装したもの

| 層 | 変更 |
|----|------|
| 起動 | `TofTask` が 2 センサを時差起動する。①前方 XSHUT=LOW ②底面を**従来と同じ手順**で 0x30 へ、`startRanging()` まで ③以降は周期ループの余り時間で 1 周期 1 段ずつ（`FrontBringUp`）: `Wake` で前方 XSHUT を上げ、次周期の `Probe` で model id を確認し、本物のときだけ `init()`→0x31→`startRanging()`。**どの段も `last_wake` に触れない**ので底面の位相は動かない。前方の失敗・不在は XSHUT を LOW に戻して `sensor_present(FrontToF)=false` で続行（R4） |
| 在否確認 | `VL53L3CXWrapper::isPresentAt()` を新設（HAL 層）。model id レジスタ 1 本読みで、居ないときのコストを 500ms から数 ms へ |
| トピック | `sensor_tof_front`（`TofData`, Queue 2）を新設。底面と変数・トピック・タイムスタンプ・`SensorId` をすべて分離 |
| ミラー | `SensorSnapshot` に `tof_front_distance` / `tof_front_status` / `tof_front_valid` / `tof_front_timestamp`。`ImuTask` が**ミラーのみ**行い、推定器にも離着陸マネージャにも渡さない |
| テレメトリ | `tof_front` と bit1 を `sensor_snapshot` から供給。他のセンサと同じ 2 条件（publish された有効性 ＋ 鮮度 R16） |
| 無効化 | パラメータ `tof.front.enable`（既定 1）。`TofTask` が起動手順の前に 1 回読む。**反映には再起動が要る**（アドレス割り当ては実行中に返上できないため） |
| CLI | `sensor tof` を底面・前方の 2 行にし、各行に `t=<us>` を付けた（実機でレートを確かめるため） |
| PC 側 | `sf telemetry` と `--web` の「未対応」を撤去し、有効ビットに応じて値／`invalid` を出す。`lib/sfpilot` の Sample に `tof_front_m` を通す（**判断にはまだ使わない**） |

### 安全要件をどう満たしたか

| 要件 | 手段 |
|------|------|
| 1. 起動順序 | 上記。底面の失敗時の扱いは従来から変えていない |
| 2. 前方の異常が底面の周期・値を乱さない | 1 周期内で**底面を先に、無条件で**読む。前方はその後、周期に余裕があるときだけ（`cycle_still_on_schedule`）。バス占有は 2 センサ合計で約 6.6ms／33ms 周期＝**約 20%**（見積もりは `detailed_design.md` §10） |
| 3. 1 データソース = 1 変数 | トピック・スナップショットのフィールド・タイムスタンプ・`SensorId` をすべて分離。SILS 試験 `simulator/tests/test_tof_front_sils.py` が起動順序・Optional 継続・飛行中の底面供給を確認 |
| 4. 推定・状態機械・制御則に触れない | `ImuTask` はミラーのみ。INV-1〜INV-5 のいずれにも該当する変更は無い（観測の追加であって鉛直フェーズ・姿勢・検出／判断の分離・状態遷移表・ミキサーのいずれも変えていない） |
| 5. 無効化手段 | `tof.front.enable`（既定 1、既存の params の流儀どおり） |

### SILS で確認したこと

SILS には VL53L3CX が無いので前方の初期化は必ず失敗する。これは**実機で USB 給電のみのときと同じ経路**であり、そのまま「初期化失敗 → Optional で続行」の確認になる。

```
[INFO] TofTask: VL53L3CX bottom ready at 0x30 (continuous ranging at ~30Hz)
[WARN] TofTask: VL53L3CX front init failed: ESP_ERR — continuing without it (Optional; needs battery power)
```

### 起動時のタイミング中立性（実機の安全要件 2。SILS で検証）

最初の実装では、前方が「居ない」とき `VL53L3CXWrapper::init()` の中でドライバが起動完了を最大 500ms ポーリングしてから失敗していた（`VL53LX_BOOT_COMPLETION_POLLING_TIMEOUT_MS`）。**これは 33ms 周期の約 15 回分、底面がまったく読まれないことを意味する。** 底面は高度推定の唯一の鉛直観測なので、これは安全要件 2 の未達である。USB 給電のみ・前方非搭載・コネクタ不良のいずれでも起こる。

SILS でもこれは `stab_flight` の `att_rmse` と決定論性 SHA256 のずれとして現れていた。

**対処（2 段構え）**:

| 段 | 内容 |
|----|------|
| 安価な在否確認を先に | `VL53L3CXWrapper::isPresentAt()` を新設。`IDENTIFICATION__MODEL_ID`（0x010F）を**レジスタ 1 本だけ**読み、VL53L3CX の model id（0xEA。当初 0xEB/0xEC としたのは誤りで、それは VL53L4CX の値。実機確認で判明し修正）が返ったときだけ本物と判定する。NACK・不一致なら**ドライバの重い `init()` を呼ばずに**即 XSHUT=LOW へ戻して `sensor_present(FrontToF)=false`。I2C タイムアウト 10ms が最悪時間を抑える。素の ACK ではなく model id を読むのは、既定で ACK を返すバスと本物を区別するためである |
| 起動を周期に分散 | 起動を `FrontBringUp{Wake→Probe→Done}` の 3 状態にし、**1 周期に 1 段だけ**進める。`Wake` は XSHUT を上げるだけ（GPIO 書き込み 1 回）で、**起動待ちは「どのみち眠る予定だった周期のスリープ」に吸収させる** — 専用の `vTaskDelay` を持たない。`Probe` は次の周期に走るので、部品は 33ms（`BOOT_TIME_MS`=4ms を十分上回る）の起動時間を得ている。どの段も底面を読んだ後の余り時間で、`cycle_still_on_schedule` の下で走り、**`last_wake` に一切触れない** |

HAL 側（`isPresentAt()`）は待たず XSHUT にも触れない設計にした。待ちと XSHUT は呼び出し側の周期に属する関心事であり、HAL に持たせると周期を持たない呼び出し側から使えなくなるためである。

**結果（既定 `tof.front.enable=1` のまま、SILS に前方のシム無し）**:

| 確認 | 結果 |
|------|------|
| `stab_flight` の `att_rmse` | **0.0489（2.80°）= 基準値と同一**、PASS |
| `sf sils regression` | **28 PASS / 5 KNOWN-FAIL / 1 SKIP**（基準どおり）|
| `test_determinism_unchanged_without_env_vars` の SHA256 | `e3cd84e0…b609` = **基準値とバイト単位で一致** |
| pytest 全件 | **436 passed / 1 skipped / 0 failed**（基準 427 passed + 1 skipped に、本改修の追加試験 9 件）|
| `sf build vehicle` | エラー 0・警告 0。サイズ 1,173,744 バイト（基準比 +432）|

**閾値も SHA256 基準値も動かしていません。** 前方が居ない環境で底面のタイミングに完全中立になったため、動かす必要がなくなりました。

試験 `simulator/tests/test_tof_front_sils.py::test_an_absent_front_sensor_does_not_shift_the_bottom_sensor_timing` が、前方の有無（`tof.front.enable`）で STATE 列が 1 行も変わらないことを見張ります（上書きが効かず空振りになる場合を検出する保険つき）。

### 前方が「居る」ときの重い初期化（ブロック時間の見積もり）

在否確認が肯定のときだけ `init()`＋`startRanging()` を走らせます。**地上での起動時に 1 回だけ**です。

| 項目 | 値・根拠 |
|------|---------|
| `WaitDeviceBooted` のポーリング | `VL53LX_WaitValueMaskEx` は**最初の一致で即座に抜ける**（`found=1` で待ちに入らない。`vl53lx_platform.c`）。`Probe` の段に来た時点で部品は 33ms 起動済みなので、初回読みで一致する |
| ドライバ内のその他の待ち | `DataInit`/`SetDistanceMode`/`SetTimingBudget`/`StartMeasurement` の経路に `VL53LX_WaitMs` は**無い**（grep 済み、0 件）。残りは I2C 転送のみ |
| I2C 転送量 | 設定ブロックの書き込みと NVM 読み出しで数百バイト。400kHz なら合計 10ms 前後 |
| SILS での実測 | 底面の同一経路（`init`＋`startRanging`）が**1 周期（33ms）以内**に完了。前方の `Probe` は t=0.033s、すなわち設計どおり 1 周期後に到達 |

**底面が欠けるサンプル数**: 最悪でも 1 周期分（1 サンプル）。R16 の鮮度しきい値は 500ms（`kTelemSensorStaleUs`）＝底面 15 周期分なので、1 サンプル欠落では `sensor_health` も telemetry の bit0 も落ちません。接地/ARM 判定（`TakeoffLandingMgr`）と ESKF の鉛直ハンドオフも ToF サンプル列に対して十分な時定数を持ち、1 サンプルでは状態が変わりません。

**ARM 前に完了します**: 起動手順は `TofTask` の最初の 2 周期（66ms）で終わります。実機の起動校正は数秒あり、`prearm` は校正完了まで ARM を拒否します（`requirements.md` の状態モデル）。したがって前方の起動は ARM のはるか前に決着します。

### 今回やらなかったこと（意図的）

| 項目 | 理由 |
|------|------|
| `sensor_health` への前方 ToF の追加 | `SensorHealth` の present/healthy マスクは `sf::SensorId`（`Imu/Mag/Baro/Tof/Flow/Power`）を索引とする**電文**で、前方を足すと様式変更になる。前方の presence/鮮度は BSP 内部（`board::SensorId::FrontToF`）には記録済みで、`PowerTask` が両 enum を明示的に対応付けているため黙った食い違いは起きない |
| Data Stream（400Hz ログ）への追加 | 下記 |
| SILS の前方距離の模擬 | P4b の前提。今回の範囲外（`simulation-policy` #15 と併せて議論）|
| 前方 ToF を推定器へ入れること | **やってはならない**。前方は機体の状態ではなく障害物の観測であり、状態ベクトルがモデル化していない |

### Data Stream（400Hz ログ）は対象外

前方 ToF を 400Hz ログに載せることは今回行わない。テレメトリ（50Hz）で監視と `sf pilot` への供給は足りており、ワイヤ様式の変更は独立に検証すべき別の作業だからである。載せるときのワイヤ id は **0x47**（`lib/sflog` に `tof_front` ストリームとして定義済み）。**0x49 は使用禁止**（`udp_capture.py` が `PKT_ESKF_PDIAG` として予約済み。`data_stream_wire.hpp:68-79`）。

### 実機確認の手順書（未実施。ユーザーが実行する）

**前提**: **プロペラを外す。** 前方 ToF はバッテリー電源が要るので、バッテリー駆動と USB 給電のみの両方を試す。飛行を伴う項目は、それ以外の全項目が通ってから。

**USB 給電のみの項目（手順 2）についての注意**: **この機体は、少なくともドックを経由した USB では、バッテリーをつながないと USB 機器として認識されなかった**（本体ポートへの直挿し・バッテリー無しの場合は未確認）。したがって手順 2 は「機体が USB だけで起動する場合に限り」実施する。起動しないなら、USB 給電のみの経路は**確認不能**として記録し、飛ばしてよい — その場合に前方 ToF が居ない状況を作るには、手順 8（コネクタを抜く）か `tof.front.enable 0`（手順 10）を使う。

| # | 手順 | 期待する結果 | 外れたときに見るもの |
|---|------|------------|-------------------|
| 1 | `sf flash vehicle -m`（**バッテリー接続**）。起動ログを見る | `TofTask: VL53L3CX bottom ready at 0x30` が先、`TofTask: VL53L3CX front ready at 0x31` が後。**この順序**であること | 順序が逆、または 0x30／0x31 以外のアドレスが出る → 起動手順の破綻。混線の疑いなので**そこで止める** |
| 2 | 同上（**USB 給電のみ、バッテリー外す**）。**機体が USB だけで起動する場合に限る**（上記の注意を参照。起動しないなら「確認不能」と記録して手順 8 で代替） | `bottom ready at 0x30` の後、`Front ToF not detected — continuing without it`。**`front init failed` は出ない**（出たら重い初期化に入っており、在否確認が効いていない）。その後も通常起動 | `[ERROR]` や abort、あるいは底面まで失敗 → Optional の扱いが効いていない |
| 3 | `sf telemetry`（バッテリー駆動、機体を手に持って静止） | `tof : down 0.xxx m   front x.xxx m` の形で**両方が数値**。「未対応」の文字が出ないこと | 前方が `invalid` のまま → 手順 1 で前方が起動したか確認。`--web` でも同じ表示になること |
| 4 | 手を**機体の下**にかざす／離す | **底面だけ**が変わり、前方は変わらない | 両方同時に動く → 混線（1 データソース = 1 変数の破れ）。**そこで止めて報告** |
| 5 | 手を**機体の前**にかざす／離す | **前方だけ**が変わり、底面は変わらない | 同上 |
| 6 | 底面の更新レートを測る。CLI の `sensor tof` は底面・前方を 2 行で出し、各行に `t=<マイクロ秒>` を付ける。これを数回打って**底面の `t` の増分**を見る（33000us 前後なら 30Hz）。可能なら `sf log wifi` で 400Hz ログを取り `tof_bottom.csv` の行間隔を見る | 底面の `t` の増分が約 33000us（30Hz）。**手順 2（前方なし）と手順 3（前方あり）で変わらないこと** | 前方ありで落ちる → バス占有の見積もりが外れている。`tof.front.enable 0` で切って再測し、差分を報告 |
| 7 | 前方センサを指で塞ぐ | 前方が `invalid` または近距離の値になる。**底面は乱れない**（手順 6 のレートも値も） | 底面が乱れる → 前方の異常が底面に及んでいる。**そこで止めて報告** |
| 8 | 可能なら前方のコネクタを抜いて再起動（コネクタ不良の模擬） | `Front ToF not detected`（`front init failed` ではない）、底面は正常。**手順 6 の底面レートがコネクタの有無で変わらないこと** | 起動しない・底面が失敗する・底面のレートが変わる → 失敗処理か在否確認の破綻 |
| 9 | **ここまで全て通ってから**、低高度（0.5m 程度）・短時間（10〜20 秒）でホバリング | 高度保持が従来どおり（±6〜7cm）。高度の振れ・落ち込みが従来と変わらないこと | 悪化 → **直ちに `tof.front.enable 0` で切り**（下記）、切った状態で再度ホバリングして従来に戻るか確認。戻るなら前方が原因、戻らないなら別の原因 |
| 10 | 無効化手順の確認 | `param set tof.front.enable 0` → `param save` → **再起動**。起動ログに `Front ToF disabled by tof.front.enable — left in reset` が出て、前方が起動しないこと。テレメトリの前方は `invalid` | 再起動しないと反映されない点に注意（アドレス割り当ては実行中に返上できない） |

**手順 4・5・7・9 で異常が出たら、`tof.front.enable 0` ＋ 再起動で前方を切れば、従来のファームと同じ挙動に戻る。**

### 実機でしか確かめられないこと

| 事項 | 理由 |
|------|------|
| 前方 ToF が実際に 0x31 で起動し測距すること | SILS に VL53L3CX が無い。SILS が通るのは「起動失敗 → Optional で続行」の経路だけである |
| 2 センサ同時運用時の実バス占有 | 見積もり（約 20%）は転送量からの計算であり、実測ではない |
| 前方ありで底面が 30Hz を保つこと | 上と同じ理由 |
| バッテリー駆動と USB 給電のみの差 | 電源に依存する現象で、SILS には電源が無い |
| ホバリング時の高度保持が従来どおりであること | SILS の回帰は変更前と一致したが、SILS のプラントは実機と忠実度が異なる |

## 5. 置き場所

| パス | 内容 | 状況 |
|------|------|------|
| `lib/sfpilot/config.py` | しきい値・期限・包絡（dataclass） | 実装済み |
| `lib/sfpilot/link.py` | `Link` インターフェース、`RealLink`（UDP:5005 の 50Hz 受信を含む）、`ReplayLink`、`SilsLink` | 実装済み |
| `lib/sfpilot/scenes.py` | 場面 3 種の宣言（環境変数＋周期ごとのフック） | 実装済み（P2） |
| `lib/sfpilot/monitor.py` | 区分の判定と即時安全則 | 実装済み |
| `lib/sfpilot/summarizer.py` | 区分 → JSON state、`signature()` | 実装済み |
| `lib/sfpilot/judge.py` | 質問定義、`JevJudge`、`FakeJudge` | 実装済み |
| `lib/sfpilot/arbiter.py` | 採否の規則表 | 実装済み |
| `lib/sfpilot/executor.py` | 行動 → `rc`/`stop`/`land` | 実装済み |
| `lib/sfpilot/trace.py` | 1 判断 1 行の JSON 記録 | 実装済み |
| `lib/sfpilot/pilot.py` | ループ本体 | 実装済み |
| `lib/sfcli/commands/pilot.py` | `sf pilot bench` / `replay` / `run` / `say` | 実装済み |
| `lib/sfpilot/instruction.py` | 指示 → 手順の列（数値の抽出・組み立て規則・包絡の事前検査・`return_home`） | 実装済み（P3） |
| `lib/sfpilot/say.py` | 手順の実行と、その間も動き続ける監視層 | 実装済み（P3） |
| `lib/sfpilot/tests/say_eval_cases.yaml` | `sf pilot say --eval` の指示 10 例と期待 | 実装済み（P3） |
| `lib/sfpilot/mission.py` | 経路の読み込み・到達の区分・飛行ループ・コード側の上限 | 実装済み（P4） |
| `lib/sfpilot/mission_run.py` | SILS の段取りと結果の集計表示（CLI から処理本体を移した先）| 実装済み（P4） |
| `lib/sfpilot/missions/*.yaml` | 同梱の経路（`line`＝現状飛べる、`square`＝P4 の対象だが現状飛べない）| 実装済み（P4） |
| `lib/sfpilot/landing.py` | 着陸前手順（移動を終える → `stop` → 静定待ち → `land`）| 実装済み（P4） |
| `lib/sfpilot/events.py` | 出来事の流れ（`EventBus`）。記録とライブ表示の分岐点 | 実装済み（P4c） |
| `lib/sfpilot/recording.py` | SILS 飛行のフライトログ一式の置き場所と命名 | 実装済み（P4c） |
| `lib/sfcli/commands/pilot_web.py` | `--web` の HTTP・SSE サーバ（127.0.0.1 のみ） | 実装済み（P4c） |
| `lib/sfcli/commands/web_assets.py` | ブラウザ表示の共有静的配信（STL・three.js・共有 3D） | 実装済み（P4c） |
| `lib/sfcli/assets/pilot_web.html` | 飛行と判断を並べて見せるページ | 実装済み（P4c） |
| `lib/sfcli/assets/stampfly3d.js` | 共有 3D シーン（`sf telemetry --web` から切り出し） | 実装済み（P4c） |
| `simulator/sils/devices/rc_stdin.cpp` | stdin に `api <行>`・`wind`・`vbatt` を追加 | 実装済み（P2） |
| `lib/sfpilot/scenes.py` | 電池低下・横流れの場面（`.scn` ファイルではなく宣言の表として持つ） | 実装済み（P2） |

場面を `simulator/sils/scenarios/` の `.scn` ファイルにしなかった理由: `.scn` は
起動前に全体を凍結する決定論的なタイムラインであり、実行中に判断結果へ反応できない。
場面は「飛行中に何をするか」を含む（電池を徐々に下げる等）ため、`sf pilot run` の
周期から呼ぶフックとして持つほうが素直である。故障注入の機構そのものは `.scn` と
共有している（同じ Plant のフック）。

`RealLink` は `lib/sfcli/commands/blocks.py` から `lib/sfpilot/link.py` へ移設した（2026-09-19）。`sf blocks` と `sf pilot` が 2 つの写しに分岐せず、1 つのクライアントで機体を操作するためである。`blocks.py` は移設先を import して使う。

### SilsLink の置き方（P2 で実装）

`SilsLink` は `RealLink` と同じ `Link` インターフェースを満たすので、その上の層
（Monitor・Arbiter・Executor）は SILS と実機の違いを知らない。ただし
`set_battery_voltage()` と `set_wind()` の 2 つだけはインターフェースに含めていない
— 実機に「自分の電池はこう読め」「風はこう吹け」とは言えないからである。この 2 つは
場面の駆動専用であり、`Link` として使う限り呼べない。

実時間エミュレータの起動処理は `lib/sfcli/commands/sils.py` の
`realtime_emu_env()` / `launch_realtime_emu()` に切り出し、`sf sils fly`（キーボード）と
`sf pilot run --sils`（Jev）で共有している。別々に組み立てると、一方で試した判断が
他方で再現しなくなるためである。

### 通信方式（typesafe-sdk を使わない理由）

HTTP API（`POST https://api.typesafe.ai/v1/systemone`、Bearer 認証）を httpx で直接呼ぶ。操縦ループは ①判断をまたいで接続を 1 本に保つこと ②リクエストごとの厳格な期限 ③再試行の無効化 の 3 点を必要とし、いずれも httpx の素の設定で足りる。SDK に依存すると `sf pilot` 全体がその導入を前提にしてしまう。任意依存は `pyproject.toml` の `pilot` に `httpx>=0.27` として置いた。

## 6. 記録と秘密情報

判断の記録は `logs/pilot/<日時>.jsonl` に 1 判断 1 行の JSON で書く（`trace_id`・`t_mono`・`state`・`questions`・`answers`（確率つき）・`latency_ms`・`arbiter_verdict`・`command`）。`grep` と `jq` で追える形にしてある。`logs/` は `.gitignore` 済み（`logs/*`）である。

API キーはリポジトリ・記録・state・コマンドの出力・例外メッセージのいずれにも書かない。記録は利用者が不具合報告に添付するものなので、伏せ字なしで共有して安全であることを試験で確認している。CI と pytest は `FakeJudge` だけを使い、キー無しで通る。

### キーの取得元（`lib/sfpilot/credentials.py`）

取得経路は 1 本にまとめてある。`sf pilot bench`・`run`・`say`・`mission` はすべて `resolve_api_key()` を通り、次の順で解決する。

| 順 | 取得元 | 備考 |
|----|--------|------|
| 1 | 環境変数 `TYPESAFE_API_KEY` | 1 回の実行だけ別のキーを試せるよう、常に最優先 |
| 2 | macOS のログインキーチェーン | `security find-generic-password -a "$USER" -s <サービス名> -w` を呼ぶ。サービス名の既定は `typesafe-api-key`（`JudgeConfig.keychain_service`）で、環境変数 `SF_TYPESAFE_KEYCHAIN_SERVICE` で変更できる。**macOS 以外では試さない**（`security` は macOS のプログラムであり、他環境で呼べば「キーが未設定」が分かりにくい異常終了に化ける） |
| — | どちらも無い場合 | 設定方法 2 通りを添えた `MissingApiKey` を投げる。メッセージにキーは含めない |

キーチェーンへの登録（値は対話入力されるのでシェルの履歴に残らない）:

```bash
security add-generic-password -U -a "$USER" -s typesafe-api-key -w
```

**`.env` ファイルを使わない理由**: `.env` はリポジトリの隣に置かれた平文であり、`git add .` 1 回、あるいは書庫の共有 1 回で公開されうる。追跡しない約束のファイルは、誰かのエディタが別の場所へ書き出すまでしか追跡されない。環境変数もキーチェーンも、秘密を作業ツリーの外に置く。

## 7. 試験

`lib/sfpilot/tests/` に 253 件（ライブ表示 14 件・記録 6 件を含む）。キー不要・通信不要で
通る。ブラウザ表示の共有部分は `lib/sfcli/commands/test_web_assets.py` に 10 件
（3D シーンの切り出しで `sf telemetry --web` が壊れていないことの確認を含む）。加えて
`simulator/tests/test_mission_sils.py` に SILS の実飛行 6 件（`--fake`。エミュレータの
ビルドが無ければ自動で飛ばす）。

### FakeJudge では捕まえられない誤り（2026-09-19 の教訓）

**`FakeJudge` は state の言葉を読まない。** 何を渡されても宣言された答えを返すので、
「state が健全な飛行を異常として**記述する**」種類の誤りに対しては、まったく無力である。
2026-09-19 の Jev 実走で見つかった 5 件のうち 3 件（高度の目標・電池の傾向・区分の
ばたつき）はこの種類であり、208 件の試験がすべて成功したまま、実走で初めて表に出た。

そこで、**実測サンプルを Monitor → Summarizer に通し、出てきた語そのものを検査する**
試験を追加した（`test_nominal_flight_words.py`）。確認するのは次の 5 点である。

| 確認内容 | 意図 |
|---------|------|
| 健全なホバリングの間、異常を示す語（`below target`・`fell sharply`・`drifting` 等）が 1 つも現れないこと | 7.4 秒の誤着陸を直接防ぐ |
| 目標高度が、機体が実際に保持している高度から採られていること | コード内の定数に戻ることを防ぐ |
| ホバリングが静定した後、指紋が一度も変わらないこと | 鮮度切れによる答えの取りこぼしを防ぐ |
| 離陸時の電圧降下が「急に低下」にならないこと | 負荷による降下を放電と取り違えることを防ぐ |
| `battery_drop` の電圧推移では「残り少ない」「危険」「急に低下」に確実に達すること | 上の 4 点の対策で傾向が鈍感になっていないことを担保する |

サンプルは手で書かず、実際の SILS 飛行から採取したものを使う
（`lib/sfpilot/tests/fixtures/`。理由は同ディレクトリの README を参照 —— 手で書いた
サンプルは、コードと同じ誤った前提を埋め込んでしまう）。

| 観点 | 確認内容 |
|------|---------|
| Arbiter | 期限超過・鮮度切れ・低確信・拮抗・包絡外・API エラーがすべて待機になること。待機 10 秒で着陸すること |
| Monitor | 数値が区分（言葉）になること。傾向に継続時間が要ること。電池危険で Judge を待たず着陸すること |
| Summarizer | state に数値が 1 つも無いこと。不明な項目が省かれること。指紋が区分の変化に追随すること |
| Judge | `emergency` がどの質問の選択肢にも無く、判断経路から出てこないこと |
| ループ | 例外を投げる Judge でも止まらないこと。記録が 1 行 1 JSON で出ること |
| ReplayLink | 実際の飛行ログを時刻順に再生し、判断の記録が出ること |
| 指示の変換（P3） | 数値の抽出（半角・全角・漢数字、m/cm/度）。組み立て規則の各項目（`none` 以降を捨てる・離陸と着陸の補い・既定の量・数値が区分に優先）。包絡の事前検査が超過を手順名指しで拒否すること。低確信で実行しないこと。旋回を含む `return_home` の計算 |
| 手順の実行（P3） | 手順の実行中に待機の `rc` を送らないこと（移動を打ち消すため）。着陸・停止は送ること。答え待ちの待機で中断しないこと。次の手順は機体の応答と静定を待つこと |
| CLI（P3） | 非対話で `--yes` が無ければ実行しないこと。`--dry-run` が何も飛ばさないこと |
| 経路の読み込み（P4） | 包絡外の経路を、操作者が書いた区間名で拒否すること。`verb` の欠落・不正・量の欠落・空の経路・存在しないファイルを拒否すること。裸の名前が同梱の経路に解決され、手元の同名ファイルがそれに優先すること |
| 到達の区分（P4） | 許容内が「目標どおり」。区間の進行方向への射影で「手前」と「行き過ぎ」を分けること。終点の横で終わった区間を「行き過ぎ」と呼ばないこと。許容が config 由来であること |
| ミッションの state（P4） | 数値が 1 つも無いこと。「3/10」が位置として渡ること。やり直し回数と経過時間が語になること。到達が未確定なら項目ごと省くこと |
| コード側の上限（P4） | やり直しが上限で「飛ばす」に変わること。待機がやり直しに解決された場合も計数に含まれること。電池が少ないとき進む系の答えが `return_home` に置き換わり、`land` は置き換わらないこと。時間上限が次の区間の前に効くこと |
| 着陸前手順（P4） | `land` より先に `stop` が届くこと。実測速度が高い間は `land` を送らないこと。低速が継続して初めて送ること。一瞬の低速では静定としないこと。上限に達したら送ること。緊急時は短い上限を使い、実行中の移動を待たないこと。着陸中は他の指令を出さないこと。記録に経路と待ち時間が残ること |
| SILS 実飛行（P4） | `nominal` が完走し真値が経路と一致すること。帰還が離陸点へ戻すこと。降下中の横移動が手順なしの基準を超えないこと。`battery_drop` が経路を途中で終えて着陸すること。`drift` が区間を無限にやり直さず、飛ばした理由を記録すること |

## 8. 別計画として提案（本計画の範囲外）

機体側で「PC からの API 指令が N 秒途絶えたら着陸する」安全機能。状態機械に関わるため、vehicle の設計 6 文書を読み、アーキテクチャ不変条件（INV）に照合したうえで別の計画文書にする。

### 知見の蓄積

このリポジトリに `knowledge/` は無い。Jev の往復時間の実測や判断の誤り例は記録する価値があるため、OKF（Open Knowledge Format）形式の `knowledge/` 導入を提案する（無断では作らない）。それまでは本書の 4 章「実測」に書く。

---

<a id="english"></a>

## 1. Overview

### About This Document

This plan describes flying the StampFly under the judgement of **Jev**, TypeSafe's System One model, which turns natural language and application state into typed judgements and probabilities. Sensors are monitored fast; judgement is delegated to Jev. Three uses are in scope: (1) situation monitoring and safety judgement, (2) flying from a natural-language instruction, (3) choosing the next move during a mission. Verification starts in SILS (Software In the Loop Simulation); when Jev is slow or unsure, the aircraft holds position.

### Target Audience

Anyone implementing or changing `sf pilot`, and anyone reviewing the safety design of this approach.

### Implementation Status (as of 2026-09-19)

P0, P1, P2a, P2, P3 and P4 are implemented. The rest is not started.

| Stage | Content | Status |
|-------|---------|--------|
| P0 | This plan document, `sf pilot bench` (measure Jev round-trip time and tokens) | **Done** |
| P1 | `lib/sfpilot` core (Monitor / Summarizer / Judge / Arbiter / Executor), FakeJudge, ReplayLink | **Done** |
| P2a | Telemetry extension (UDP:5005 becomes the 140B v2 packet, adding battery voltage, downward ToF, optical flow, magnetometer and pressure altitude; format in `firmware/vehicle/docs/detailed_design.md` §10) | **Done** (not verified on hardware) |
| P2b | Drive the forward ToF (add `sensor_tof_front`, mirror into `SensorSnapshot`, supply telemetry bit1). **Requires hardware verification and battery power** (the forward ToF has been seen not to come up on USB power) | **Implemented** (verified in SILS; **not yet verified on hardware** — the procedure is in §4.7 of the Japanese section) |
| P2 | SILS integration (`api` stdin verb, SilsLink, three scenes), `sf pilot run --sils`, RealLink's 50Hz UDP:5005 reader | **Done**. All three scenes confirmed against the live Jev (§4.5) |
| P3 | `sf pilot say` (natural-language instruction) | **Done**. 7/10 against the live Jev, all three misses diagnosed and fixed, now **10/10** (§4.5) |
| P4 | Mission (route patrol), `next_move`, and the pre-landing approach | **Done**. A reply-count race found and fixed during the live-Jev flights; **all six legs now complete `as planned`** (§4.5) |
| P4b | **Forward-ToF exploration** (choose a heading from the clear space ahead). **Two premises**: (1) the forward ToF is driven (P2b — implemented, not yet verified on hardware) and (2) **SILS simulates a forward distance** (a MuJoCo raycast or similar; not started). Without (2) a wrong heading judgment is first met in the air | Not started (waiting on P2b's hardware verification and on a simulated forward distance in SILS) |
| P4c | **Ways to watch a flight** (`--web` live view, plus video / GUI replay afterwards; §4.6) | **Done** |
| P5 | Real hardware, after measuring round-trip time, transmitter in hand | Not started |

**Forward ToF status (important):** the forward ToF has been driven since 2026-09-19 (P2b; see §4.7 of the Japanese section). `TofTask` holds the forward part in reset while it brings the bottom sensor up at 0x30, and only then wakes the forward one and moves it to 0x31. Telemetry v2's `tof_front` and validity bit1 are supplied from there. **It is not yet verified on hardware.** The forward sensor is Optional and **needs battery power**: on USB alone it does not start and bit1 stays clear. **Obstacle avoidance and exploration (P4b) rest on one more premise that does not yet hold — SILS does not simulate a forward distance.** Until it does, a wrong judgment would first be met in the air.

**Why the forward ToF is not fed to the estimator:** it observes obstacles ahead, not the vehicle's own state, so it is mirrored for monitoring and telemetry only. The bottom ToF remains the single vertical observation.

## 2. Constraints That Shaped the Design

| Fact | Source | Effect on the design |
|------|--------|----------------------|
| Jev is not a calculator. Numeric comparison, thresholds and time arithmetic are unreliable. Irrelevant state lowers accuracy | docs.typesafe.ai `model-jaggedness/jev-1.13` | All numeric judgement is code. Jev receives only a situation summarised in words |
| No latency guarantee. Charged per input token. Limit 1,200 req/min | `cookbooks/parallel_questions`, `models` | Jev is a 1-4Hz judging layer. All questions go in one request, asked only when the situation changes |
| The only command that can be sent continuously is `rc a b c d`. Others block waiting for a reply. The API is POS_HOLD only | `firmware/vehicle/tasks/api_task.cpp:433` | Attitude and position hold stay on the vehicle; the PC sends velocity commands and `stop`/`land` only |
| Telemetry: UDP:5005 (50Hz, attitude/position/velocity/flight state; **from v2 also battery voltage, downward ToF, optical flow, magnetometer and pressure altitude**) plus UDP:8890 state string (10Hz, ToF and battery) | `firmware/vehicle/docs/detailed_design.md` §10, `sf_telemetry/include/tello_state.hpp:57` | The monitor uses 5005 primarily, 8890 as a supplement. The 400Hz Data Stream occupies 8890 and is not used. With v2 (140B) both battery and ToF arrive on 5005 alone, reducing the dependence on 8890 |
| The vehicle does not land when PC commands stop arriving; COMM_LOST watches only the transmitter | `sf_failsafe/failsafe.cpp:108` | Keep the transmitter in hand on real hardware. A vehicle-side landing on PC timeout is a separate plan |
| SILS has no network, but does have a real-time mode, stdin input, `STATE k=v` output and `sf_api_inject_line()` | `simulator/sils/devices/rc_stdin.cpp` | Adding one `api <line>` stdin verb lets the same command stream reach SILS (P2) |

## 3. Structure

The division of labour: Jev may only choose from a fixed, enumerated set of actions. `emergency` (cutting the motors, i.e. a crash) appears in no question's options and in no automatic rule; it remains a human action through the transmitter or `sf blocks`. Jev's output is a proposal, and only what the Arbiter passes is carried out.

### The State Sent to Jev

Code sends classifications, not figures. Values are English words (the model is most accurate in English and these words are machine-generated); the one exception is `operator_instruction`, the operator's own sentence, passed through unchanged. Fields whose value is unknown are omitted entirely rather than sent as "unknown". `assert_no_numbers()` in `lib/sfpilot/summarizer.py` mechanically refuses a state carrying a number.

### Questions

| ID | Type | Options | When |
|----|------|---------|------|
| `safety_action` | Choice | `continue` / `hold` / `land` | Always (use 1) |
| `abnormal` | Noul | Is the flight abnormal? | Always; recorded, and supports the hold decision |
| `next_move` | Choice | `next_step` / `hold` / `redo_step` / `skip_step` / `return_home` / `land` | During a mission (use 3, P4) |

### Arbiter Rules

| Condition | Outcome |
|-----------|---------|
| Answer slower than the deadline (500ms default) | Discard, **hold** (`rc 0 0 0 0`) |
| The situation's classification changed since the question was asked | Discard; re-ask next cycle |
| Choice confidence below the threshold (0.6 default) | **Hold** |
| Top two options are `continue` and `land`, within 0.15 of each other | **Hold** |
| The action would leave the envelope (0.3-1.5m altitude, 2m radius, speed limit) | Refuse, hold |
| API error or lost connection | No retry; hold (the next cycle re-asks) |
| Holding for 10 consecutive seconds | **Land** |

Holding is not free -- it burns battery and resolves nothing by itself -- so the last rule converts a hold that will not end into a landing.

## 4. Measurements

Measured with `sf pilot bench -n 20` (2026-09-19, macOS, from this repository's development environment, two questions in one request). Set the environment variable `TYPESAFE_API_KEY` to run it.

| Item | Measured |
|------|----------|
| First call (includes TLS handshake) | 688.9 ms |
| Warm p50 | 231.7 ms |
| Warm p95 | 442.2 ms |
| Warm max | 501.6 ms |
| Warm min | 181.2 ms |
| Input tokens per judgement | 558 (identical across all 20; the state was fixed) |
| Output tokens per judgement | 60 |
| Failures | 0 / 20 |
| Cost | **Not measured** (the billing statement has not been checked) |

**Reading**: warm p95 (442ms) fits inside the 500ms deadline, but the maximum (501.6ms) slightly exceeded it. The first call pays for the TLS handshake, which the pilot loop pays once at startup. JevJudge therefore keeps **one connection alive** and warms it once at startup. P2 will add measurements taken through SILS and revisit whether 500ms remains the right deadline.

**Not yet measured**: cost per judgement, round-trip time through SILS (P2's implementation is complete, but the live Jev runs could not be performed -- the 1Password unlock needs an interactive prompt that was unavailable), token count with the third mission question included (P4), and the PC-to-vehicle round-trip on real hardware (P5).

### Revisiting the 500 ms deadline (P2)

**Conclusion: the 500 ms default stays.** The round trip is between the PC and TypeSafe; SILS is a pipe inside the same PC and is not on that path, so there is no reason for P0's measurement (p50 232 ms / p95 442 ms / max 502 ms) to change. Changing the deadline without evidence would break the correspondence with that measurement. An answer past the deadline is discarded into a hold, not a failure -- the flight continues, and a hold that will not end becomes a landing. `sf pilot run --deadline-ms` overrides it for one run when a measurement needs it, without touching the default in `config.py`.

Under FakeJudge (a fixed 10 ms answer), the SILS flights recorded zero deadline overruns in all three scenes. That measures the rest of the path rather than Jev: the 50 Hz loop, the stdin writes and the STATE parsing are not eating into the judgement's budget.

## 4.1 P2 Results

Implemented: the `api` / `wind` / `vbatt` stdin verbs (`rc_stdin.cpp`), a published `sils_scenario_api_inject()` so a `.scn` `api` event and a typed line share one seam, the STATE line's appended `x y vx vy vz tof tof_valid batt` fields, a SILS-only battery-voltage override on the INA3221 shim (the firmware is untouched), `SilsLink`, RealLink's 50 Hz UDP:5005 reader, the three scenes, and `sf pilot run --sils`. The real-time launch was extracted into `realtime_emu_env()` / `launch_realtime_emu()` and is now shared with `sf sils fly`.

**INV-2 (pilot authority) holds.** The 20 ms neutral-stick stream that SILS injects does not cancel API guidance, confirmed in real-time mode: the cancel rule watches stick MOVEMENT, and a parked centred stick is not movement. Existing determinism is intact -- the SHA256 baseline in `test_realtime_fly.py` still matches and `sf sils regression` is unchanged at 28 PASS / 5 KNOWN-FAIL / 1 SKIP.

Under FakeJudge: `nominal` flies 60 s without choosing to land; `battery_drop` lands at t=26.4 s through the Monitor's immediate safety rule; `drift` is classified as drifting in 16 of 35 cycles.

### Correction to the `drift` scene (2026-09-19)

**P2's commit message and the original text described `drift` as a combination of wind and a misread optical flow. That was inaccurate.** The scene is **wind alone.**

`--flow-scale` (`SILS_EMU_FLOW_SCALE`) does not reach the closed-loop flying path. `Config::flow_vel_scale` is read only by `Plant::flow()`, whose only caller is the smoke test `simulator/sils/smoke/plant_smoke.cpp`. In flight, the flow the firmware receives is synthesized through `virtual_board.cpp` → `sils_pmw3901::set_motion_from_velocity()`, which never consults the multiplier.

Wind alone was measured to be sufficient (0.060 N, 35 s, real-time emulator, counting the Monitor's classifications):

| Condition | Cycles classified as drifting | of which "fast" | Peak speed | Peak excursion |
|-----------|------------------------------|-----------------|------------|----------------|
| Wind 0.060 N only | **359** of 1388 | 58 | 0.635 m/s | 1.038 m |
| Wind 0.060 N + `--flow-scale 0.35` | 361 of 1389 | 58 | 0.638 m/s | 1.041 m |
| Control (no wind) | **0** of 1385 | 0 | 0.000 m/s | 0.000 m |

The difference between the first two rows is the run-to-run jitter of a real-time emulator, not an effect of the knob. The wind value of 0.060 N is unchanged: the measurement in `config.py` that chose it — 0.02 N never reaches the classification band, 0.06 N swings out to about 0.9 m — is unaffected by the presence of a knob that does nothing.

`--flow-scale` itself is not repaired here (out of scope); it is recorded as entry #14 in `docs/architecture/simulation-policy.md`'s improvement backlog.

**Problem found (out of scope here, reported rather than worked around):** the firmware's horizontal position estimate has its axes swapped relative to ground truth. Pushing north with `wind 0.02 0 0` moves `truth.csv`'s `pos_x` to +0.161 m while the firmware's `posvel.csv` keeps `pos_x` at exactly 0 and moves `pos_y` to -0.138 m. This predates `sf pilot` -- it reproduces through the existing `.scn` `wind` event with no stdin involved -- and the Plant's `setWind` and `frames::ned_to_enu` are both correct, as is the truth. Position hold is self-consistent (it pulls 0.161 m back to 0.025 m), which is probably why this has not surfaced. It is not in `docs/architecture/simulation-policy.md`'s backlog. It could affect anything that maps position between SILS and the real vehicle, so a separate investigation is recommended.

## 4.2 P3 Results (`sf pilot say`)

Implemented: `lib/sfpilot/instruction.py` (figure extraction across half-width, full-width and kanji numerals, m→cm conversion, the assembly rules, the envelope pre-check and the `return_home` computation), `lib/sfpilot/say.py` (walking the steps on a worker thread while the use-(1) safety layer keeps running), the step questions and `ask_questions()` in `judge.py`, `InstructionConfig` in `config.py`, `Trace.write_plan()`, the `sf pilot say` subcommand (`--dry-run` / `--yes` / `--no-auto-land` / `--eval`), and the 10-case expectation table in `lib/sfpilot/tests/say_eval_cases.yaml`.

**The division of labour is the design's.** Jev answers only "what is action number k?" (Choice, k=1..6) and "how big is action number k?" (Choice: small / medium / large / unspecified), all 12 questions in one request. Because the questions cannot see each other's answers (the fan-out pattern), each one states in full which position of the instruction it asks about. **No figure ever reaches the model**: "70cm", "1m" and "九十度" are extracted by code, m→cm is converted by code, and the map from a named band to centimetres or degrees lives in `config.py`.

**Measured in SILS under FakeJudge.** The sequence equivalent to "climb, go forward, come back, land" ran to completion and the ground truth (`truth.csv`) confirms it: the five steps `takeoff` → `up 50` → `forward 50` → `go -50 0 0 50` → `land` all flew; the craft travelled **north** to a peak of **N=+0.658 m** with |E|=0.000 m (the north-facing start is working); altitude peaked at **1.094 m** (0.5 m takeoff plus `up 50`); and at the end of the `go` it was back at **N=+0.00 m**, i.e. on the takeoff point. It touched down at N=−0.382 m, and that residual is drift DURING the descent, not the instruction layer — a `land` from a genuinely stationary hover drifts 0.000 m (see "Problem found" below). The safety layer made 69 decisions during the flight and interrupted the sequence zero times.

**Three things the design did not anticipate, found by measurement and fixed:**

1. **The safety layer's hovering `rc` was cancelling each move.** `rc` publishes a VELOCITY guidance target (`api_task.cpp` `cmdRc`, mode 2) which REPLACES the POSITION target a `forward` just set. The routine hover is now withheld while a plan drives the vehicle (`Executor.hold_commands_silently`); `land` and `stop` still go out, since stopping the flight is the whole reason the safety layer runs.
2. **Treating every hover as an interrupt cut every instruction off at its first step.** Most hovers mean "no usable opinion yet" (unanswered, late, stale) and several occur during any takeoff. Only `land`, `stop` and a hold Jev deliberately chose now interrupt; a hold that will not end still becomes a landing through the Arbiter's own timer, and that landing interrupts.
3. **`ok` means "reached", not "stopped".** The vehicle answers when the estimate enters the 0.15 m tolerance sphere (`kReachRadiusM`) while still carrying its approach speed; a `land` sent at that moment kept travelling through the descent and touched down 0.7–0.9 m past the takeoff point. Steps now wait until the MEASURED speed stays below 0.05 m/s for a second — measured rather than a fixed pause, because a longer move carries more speed and no single figure is right for both.

**Problem found (out of scope here, reported rather than worked around): the craft drifts sideways during a descent.** A `land` from a stationary hover drifts 0.000 m, but a `land` issued shortly after a move drifts 0.3–0.9 m while descending, as though position hold is not holding (or holds weakly) during the descent. This predates `sf pilot say` and reproduces by sending `land` directly. It could affect real-world landing accuracy, so a separate investigation is recommended.

**Evaluation against the live Jev (needs a key; now run — see §4.5):** ten instructions — simple, multi-step, with figures, vague, out of range, and not about flying at all — with their expected steps are in `lib/sfpilot/tests/say_eval_cases.yaml`, run with `TYPESAFE_API_KEY=... sf pilot say --eval lib/sfpilot/tests/say_eval_cases.yaml`. The assembly rules, the figure extraction and the envelope check are code and are pinned without a key by `pytest lib/sfpilot`; what this table measures is only the remainder — whether Jev picks the right move and size from a Japanese sentence. Where an expectation may itself be too narrow (a vague instruction names no direction), the case says so.

## 4.3 P4 Results (`sf pilot mission`)

Implemented: `lib/sfpilot/mission.py` (loading a route, the envelope pre-check, classifying a leg's arrival, the flight loop and the code's own limits), `lib/sfpilot/mission_run.py` (staging the SILS flight and printing the summary), the shipped routes in `lib/sfpilot/missions/`, `lib/sfpilot/landing.py` (the pre-landing approach), `MissionConfig` and `LandingConfig` in `config.py`, the keyless rule-based `MissionFakeJudge`, and the `sf pilot mission` subcommand (argument handling and printing only; the flight logic lives in `sfpilot`).

**The division of labour is the design's.** Jev answers one question at each leg boundary -- `next_move`, choosing between `next_step` / `hold` / `redo_step` / `skip_step` / `return_home` / `land` -- carried in the SAME request as use (1)'s `safety_action` and `abnormal` (`judge.WITH_MISSION`). No numeric comparison is asked of it: whether a leg arrived is classified by code from the distance to its intended end point ("as planned" / "stopped short" / "overshot"), the retry count becomes a word ("not yet" / "once already" / "already at the limit"), and elapsed time becomes "plenty of time left" / "about halfway" / "close to the time limit". `assert_no_numbers()` refuses a state carrying a figure before it is sent.

**Four limits belong to the code and no answer overrides them**: two retries per leg, a 180 s ceiling on the whole mission, a refusal to go ON once the battery reads "running low" (any of `next_step` / `redo_step` / `skip_step` is replaced with `return_home`), and the envelope, checked when the route is loaded. All four live in `_MissionFlight._allow`, and every refusal is recorded in the summary and the trace.

**Measured in SILS under FakeJudge, on the `line` route.** `nominal` flew all six legs "as planned" and completed; the ground truth reached N=+1.376 m with |E|=0.000 m, peaked at 0.808 m altitude and touched down at N=+0.119 m. `battery_drop` chose `return_home` as the battery crossed the band, flew home and landed (t=54.5 s). `drift` had legs classified as "stopped short", retried each twice, and converted the third attempt into skipping with the reason recorded.

### The pre-landing approach (working with the firmware's design)

**The firmware stops holding horizontal position the moment a landing is accepted.** This is by design, not a defect: `sf_controller_pid/pid_controller.cpp` excludes `VerticalPhase::Landing` from `computePositionHold()` so that a descent steers identically in every flight mode (a POS_HOLD landing handles like a STABILIZE one). The consequence is that whatever horizontal velocity the craft carries INTO the descent is carried THROUGH it, unopposed. Measured in SILS (`truth.csv`, horizontal travel from the `land` to touchdown): **0.271 m** for a `land` sent straight after a move, **0.043 / 0.132 / 0.163 / 0.226 m** over four flights through the approach, and **0.000 m** from a genuinely stationary hover.

So `sfpilot` does the following, **leaving the firmware untouched**:

- **Every `land` in the package goes out from the Executor.** A second caller with its own `link.send_command("land")` would be a landing that silently does not settle; having one place send it is what makes the settling unskippable.
- **The approach**: wait for an outstanding blocking move to answer (2 s ceiling, then override it with `stop`), command `stop` to re-capture the present position, wait until the MEASURED horizontal speed stays below 0.05 m/s for a second (6 s ceiling), and only then send `land`. A `land` that interrupts a move leaves the guidance target standing and the craft accelerates towards it once the descent begins, so the move is ended deliberately rather than left hanging.
- **A landing that cannot wait runs the same approach on a much shorter ceiling** (0.5 s for the Monitor's immediate safety rules -- a battery in the danger band, a diverged estimate). It is not skipped even then, because half a second of `stop` already removes most of the approach speed. `emergency` remains unreachable from this package.
- The trace records which path was taken and how long it waited (`urgent` / `settled` / `timed_out` / `waited_s` / `ceiling_s`).

**Expected on real hardware, not yet confirmed there.** All of the above is measured in SILS. The cause is the firmware's own control law rather than anything specific to SILS, so the same behaviour is expected on the vehicle. **Whether to fix it at the root -- by keeping position hold active during a descent -- is a vehicle-side design judgement and out of scope here.** This plan stays within what can be improved without changing the firmware's design.

### Problem found (out of scope here, reported rather than worked around)

**A move that crosses horizontal axes disarms the craft through the impact detector.** Following a north/south move with an east/west one produces `[WARN] failsafe: Impact detected: 5.5G (x2 @400Hz)` about a second in, then `Impact/anomaly → emergency DISARM`, and the flight ends. None of `sf pilot` is involved: it reproduces by typing `api` lines into a bare emulator.

| Sequence sent | Result |
|---------------|--------|
| `forward 60` → `right 60` | **Disarms** (5.5 G) |
| `right 60` → `forward 60` | **Disarms** |
| `left 60` → `forward 60` | **Disarms** |
| `forward 60` → `go 0 -60 0 50` (diagonal) | **Disarms** |
| `cw 90` (a turn just after takeoff) | **Disarms** (6.2 G) |
| `forward 60` → `forward 60` | Fine |
| `right 60` → `right 60` | Fine |
| `forward 60` → `up 30` | Fine |
| `right 60` alone | Fine |
| `up 50` → `forward 50` → `go -50 0 0 50` (P3's sequence) | Fine |

Repeating a move on the same axis is unaffected, as is a vertical move after a horizontal one. It reproduces with 16 s of hovering in between, so it is not a settling problem.

**Cause (established on 2026-09-19; the first reading, "the SILS IMU or frame handling", was wrong).** The 5-6 G is not an in-flight anomaly but the **impact of touching the ground**: the true altitude at the peak is 0.007-0.008 m. The craft fell from 0.47 m in 1.3 s during the second move.

| Step | What happens | Evidence |
|------|--------------|----------|
| 1 | A 1-LSB alternation of gyro z (0.001064 rad/s) is the seed | 16-bit quantisation in `virtual_board.cpp`, same as the real part |
| 2 | The yaw-rate D term amplifies it with a sign flip every step (`rate.yaw.td=0.01` is required; with 0 it does not happen) | measured |
| 3 | Yaw torque saturates at `rate.yaw.max_torque=1.226e-3` | 398 samples |
| 4 | The mixer scales it by 1/kappa (about 244), per-motor thrust swings by 82% of the hover share, and duty sticks at 0.31/1.00 | `actuator.cpp:271-274` |
| 5 | The motors cannot follow a 200 Hz square wave, mean lift drops, the craft falls | attitude stays calm throughout (roll < 1.1 deg) |

- The start-attitude fix (`1d99538c`) is **unrelated**: a build with only that diff reverted reproduces the same 5.2 G.
- It is the mechanism already known from the known-fail notes in `pos_flight.expect:19` and `pos_yaw.expect:18` and from backlog #12 in `simulation-policy.md` (yaw torque authority saturation, headroom of `rate.yaw.max_torque`). `api_flight.scn` avoids the same thing by climbing to 0.7 m before it turns.
- Quantisation and the mixer are firmware-side and identical on the real vehicle, so this **cannot be called SILS-only** (not checked against real flight logs).
- The regression suite missed it because no scenario crosses horizontal axes.

**Changes tried (in a separate worktree, not merged).** `rate.yaw.td` = 0 lets the cross-axis move pass but breaks the regression (`stab_flight` fails). `rate.yaw.max_torque` = 6e-4 lets the cross-axis move and `cw 90` pass (full regression not measured). Lowering the cap is a control-parameter change and must be re-verified against the recorded real-hardware yaw disturbance (`analysis/scripts/yaw_nt_kanazawa/`), so **this plan does not change it and hands it to the firmware owner**. Proposed regression scenario: `api_cross_axis.scn` (`takeoff` -> `forward 60` -> `right 60` -> `land`; `duty_max < 0.92`, `alt_min > 0.3` during the moves, no `Impact detected`).

**Effect**: every route that uses both horizontal axes -- the square among them -- and a turn right after take-off cannot be flown today. P3 escaped it because its sequence stayed on the north axis. `square.yaml` is kept as the route P4 is FOR, and the route actually flown, including by the tests, is the single-axis `line.yaml`. Climbing higher so the fall does not reach the ground would only hide the fall, so the pilot does not do that.

The sideways drift during a descent reported in §4.2 is reduced by the approach above, but fixing it at the root remains a vehicle-side judgement.

### Evaluation against the live Jev (needs a key; now run — see §4.5)

```bash
TYPESAFE_API_KEY=... sf pilot mission line --sils --yes                       # nominal
TYPESAFE_API_KEY=... sf pilot mission line --sils --yes --scene battery_drop  # falling battery
TYPESAFE_API_KEY=... sf pilot mission line --sils --yes --scene drift         # pushed sideways
```

Add `--fake` for a keyless run (the rule-based stand-in takes over), or `--dry-run` to check a route without flying. What to read: whether Jev picks `next_step` at a boundary, `redo_step` after a leg that did not arrive, and what it picks once the battery reads "running low" -- the code replaces any going-on answer with `return_home` there, so the summary shows the difference between its choice and what flew. The token count with the third mission question included is measurable for the first time here.

## 4.5 Results of the First Flights Against the Live Jev (2026-09-19)

### Summary

**Five faults, all of them in the code that BUILDS the state, none in the judging layer.** Jev's judgements were sound throughout, given what they were given; it was the state that was wrong. The worst symptom was `run nominal` landing after 7.4 seconds, but the state it landed on described a perfectly held hover as "below target" and a nearly full pack as having "fell sharply in the last few seconds". Choosing `land` (probability 0.74) on that reading is correct.

**Why no existing test caught any of it:** `FakeJudge` does not READ the words in a state. It returns a fixed answer whatever it is handed, so a state full of alarming words produced exactly the same green test run as a correct one. Nothing in the suite ever looked at the words that were generated. That is the main lesson here, and the remedy is a test that runs measured samples through Monitor → Summarizer and inspects the words themselves (`lib/sfpilot/tests/test_nominal_flight_words.py`, with the samples in `lib/sfpilot/tests/fixtures/`).

### Causes and fixes

| # | Symptom | Cause (measured) | Fix |
|---|---------|------------------|-----|
| 1 | `altitude: "below target"` throughout a healthy hover | `Pilot` built `Monitor(config)` with no target, so the `Monitor` default of 0.8 m was used. The firmware holds WHATEVER the auto-takeoff climb reached (`api_task.cpp` seeds the guidance target from the pose at FLYING, not from a constant). The measured hover was 0.441-0.483 m, so the 0.35 m error was more than twice the 0.15 m tolerance | Drop the constant and **adopt the altitude the aircraft is actually holding** (`Monitor._adopt_target_if_unset`), only once FLYING and once the climb has levelled off (on the ground the altitude is a perfectly steady zero, and adopting it would make the whole flight read "above target") |
| 2 | `battery.trend` reaching "fell sharply" with nothing wrong | The trend was judged on the PERCENTAGE, which is a linear map of the instantaneous, LOADED voltage and so moves for reasons unrelated to energy spent. Measured: takeoff alone drops the reading 4.19 V → 3.79 V within a second, i.e. 99% → 55%, a 44-point fall; and a steady hover alone drains 0.35 pct/s, reaching 8.9 points in the worst 10 s window. Both cross the 5.0 threshold | Judge on VOLTAGE (`battery_drop_fast_v = 0.15 V` over a 20 s window). Measured separation: 0.066 V for an ordinary hover against 0.233 V for the `battery_drop` scene. The takeoff load step is also kept out of the window (`battery_settle_s`) |
| 3 | 7 of 11 judgements discarded as "the situation changed since the question" | The classifications flapped on their band edges. Measured: the battery trend alternated **510 times in 60 s** on the `battery_drop` scene, because the window's oldest sample fell in and out of it each cycle | A minimum hold time on every classification (`classification_hold_s = 1.0 s`, `Monitor._held`): a new value replaces the reported one only after it has held. Measured: 510 → **2** |
| 4 | Mission legs 3 and 4 reported `stopped short`, retried twice each, then skipped | A **reply-count race**. `StepRunner._await_step` waits only for a COUNT to move. Measured: `takeoff`'s reply arrived 0.7 s AFTER `forward 60` had been sent and satisfied that leg's wait instantly. The leg was declared finished the moment it began, and `classify_arrival` then correctly reported it as short of a target it had not begun flying towards. The same route under FakeJudge took 52.6 s with every leg `as planned`; under Jev, 33.1 s. The difference was purely the timing the round trip introduced | The link now tracks how many replies are still owed (`SilsLink.replies_outstanding`), and a move waits out the previous reply before being sent (`StepRunner._drain_stale_replies`). `landing.py`'s duplicate reckoning of the same thing now reads the one source |
| 5 | `say`: two trailing `land`s for "...戻ってきて着陸して" | `_read_verbs` truncates at the first `none` but not at the first `land`. The questions are a fan-out and cannot see each other's answers, so asked for the 5th action of a 4-action instruction the model answered `land` again as the most plausible continuation rather than `none` -- a fair reading of the sentence, not something to argue with in the criteria | Truncate at the first `land` too: landing ends the flight, so there is no action after it |

### Fixed alongside

| Change | Why |
|--------|-----|
| `say`: "戻ってきて" read as `back 50` rather than `return_home` | The criteria for `back` and `return_home` now carry a CONTRAST and examples, because the Japanese "戻る" reads as either. `back` is about a direction of travel and ends somewhere new; `return_home` is about a destination and cancels the flight so far |
| The eval case "ちょっと移動して" now expects `expect_refusal: true` | No direction is given, so every horizontal move is equally plausible and the model cannot be confident in any (measured 0.46). **The refusal is the wanted behaviour** and the expectation was wrong. `expect_refusal` already existed, so no new format was needed |
| The window length removed from the battery-trend classification name | The classification was the string "この 10 秒で急に低下" and the summarizer's table keyed on it. Widening the window to 20 s would have missed the lookup and **sent untranslated Japanese to Jev**, with nothing failing. The names are now constants (`BATTERY_TREND_*`) and a test catches a missing translation |
| Arbiter: an unconfident answer whose top two are `continue` and `hold` is accepted as `hold` rather than discarded | The cause of `drift`'s 13 consecutive holds and its landing. See "What the `drift` low confidence turned out to be" below. The threshold was not lowered |
| The battery trend's window was flapping on its own threshold | The history was trimmed to exactly the window, so the oldest sample fell in and out each cycle and the "is the window full?" answer flipped with it (measured: 510 times in 60 s). The history is now kept longer than the window and the span to measure is chosen explicitly (`Monitor._trend_window`) |
| API key resolution gathered into one place | New `lib/sfpilot/credentials.py`: the environment variable first, then the macOS keychain. See §6 |

### Re-checked against Jev (2026-09-19)

`sf pilot run --sils --scene nominal --duration 60`:

| Item | Before (`…112337`) | After (`…120119`) |
|------|--------------------|-------------------|
| Outcome | **landed by mistake at 7.4 s** | **flew the full 60 s, never landed** |
| Judgements | 11 (continue 3 / hover 7 / land 1) | 61 (**continue 60** / hover 1) |
| Discarded as stale | **7 of 11** | **0** |
| `safety_action` confidence | 0.67-0.78 | **0.97-0.98** |
| State altitude | `below target` throughout | `on target` throughout |
| State battery trend | `steady`→`falling gradually`→`fell sharply` | `steady` throughout |
| Round trip | p50 237 ms / max 461 ms | p50 216 ms / p95 326 ms / max 521 ms |

The confidence moving from 0.67-0.78 to 0.97-0.98 is the substantive result: **correcting the state alone turned a hesitant judgement into an unambiguous one.** The question text (the `safety_action` instructions and criteria) was not changed at all.

### The other four Jev re-checks (all run)

| Run | Result |
|-----|--------|
| `run --scene battery_drop --duration 70` (`…123257`) | **Chose to land, correctly.** While the pack was healthy it kept answering `continue` at 0.97-0.98 with abnormal 0.04, and the moment the trend read `fell sharply` it chose `land` (confidence 0.84, abnormal 0.79). Landed at 24.3 s |
| `run --scene drift --duration 60` (`…123648`) | **Flew the full 60 s.** 62 judgements, continue 51 / hover 11. The three answers split between continue and hold were accepted as holds rather than discarded, under the Arbiter rule below |
| `mission line --sils --yes` (`…124014`) | **All six legs `as planned`, route completed.** Legs 1-4 chose `next_step` at 0.89-0.98. Before the fix, legs 3 and 4 were `stopped short`, retried twice each and skipped |
| `say --eval` (10 cases) | **10/10 matched** (7/10 before). All three misses were resolved by the fixes in the table above |

### What the `drift` low confidence turned out to be — one new Arbiter rule

Reading the `probabilities` in the trace, the answers below the threshold were split between **`continue` and `hold`** (measured 0.45/0.40, 0.53/0.36, 0.45/0.41, with `land` a distant 0.11-0.15). That is not the model failing to tell safe from unsafe: **both halves are cautious readings of an unremarkable situation**, and the Arbiter's own response to "no usable answer" is to hover, which IS `hold`.

So a rule was added: **when the top two are `continue` and `hold`, an answer below the confidence threshold is accepted as `hold`** rather than discarded. Anything involving `land` keeps the old treatment — a model that cannot separate carrying on from ending the flight is not one to act on. **The threshold itself was not lowered.**

Such a hold is recorded apart from one Jev actually chose (`chosen_hold`). Callers that walk a sequence stop on the latter but not the former; before that distinction existed, a mission ended at its last leg on answers that actually **preferred carrying on** (continue 0.63-0.72 against hold 0.23-0.30).

### On the round trip (an observation unrelated to these fixes)

Partway through the work the connection to `api.typesafe.ai` was lost for a while (`curl` never completed the connect, while `docs.typesafe.ai` answered in 95 ms at the same moment), and around that the round trip degraded from a p50 of 216 ms to 511 ms. Runs during that window lost every judgement to the deadline and exercised nothing (`…120257`, `…120408`); the table above is from the re-runs after it recovered.

External though it is, the fact that **a p50 moving from 216 ms to 511 ms loses every judgement** does show how little margin the 500 ms deadline has. The "do not change the 500 ms deadline" decision in §4 is worth revisiting in light of it; it was not changed here, because one episode of degradation is not enough evidence.

The keyless checks were completed too, and the fixes reproduce in SILS:

| Run | Result |
|-----|--------|
| `run --scene nominal --fake` (`…115901`) | All 41 judgements `continue`; the state reads `on target / steady` for 37 of them, the rest being the cycles before every field had a value |
| `mission line --fake` (`…120011`) | **All six legs `as planned`, route completed** (before the fix, the live-Jev run skipped legs 3 and 4 as `stopped short`) |
| `pytest simulator/tests lib/sfpilot lib/sfcli lib/sflog` | Against the original 368 passed / 1 skipped: **393 passed / 1 skipped, including 25 new tests** |

## 4.6 Watching the Flight (P4c, 2026-09-19)

Answers the request to *see* the autopilot's simulation. There are two ways: watching **during** the flight (`--web`), and watching **afterwards** (video / GUI replay).

### During the flight — `sf pilot run|say|mission --web`

```bash
sf pilot run --sils --scene drift --fake --web
```

`--web` serves a page on 127.0.0.1 and opens a browser (`--port` to change it, `--no-browser` to skip opening). The aircraft is on the left, the reasoning on the right.

| Area | Content |
|------|---------|
| Top strip | Flight phase, the step or leg running, decisions so far, how long it has been holding, Jev's round-trip p50/p95, deadline overruns |
| 3D view | Attitude, position and trail — the **same** view `sf telemetry --web` shows (below) |
| Top view | Horizontal track, the takeoff point, the 2 m envelope, and for a mission the route and the current leg |
| Decision list | One decision per row, newest on top: time, the WORDS sent to Jev (with whatever changed emphasised), the three `safety_action` probabilities as bars, `abnormal`, `next_move`, the Arbiter's ruling and reason, the command actually sent, and the round-trip time. Overruns and discards are marked |

**The page and the trace come from one place.** `Trace` holds an `EventBus` (`lib/sfpilot/events.py`) and publishes the row it has just written. The page and `logs/pilot/*.jsonl` are one row shown two ways, not two assemblies that could disagree — and such a disagreement would surface only while someone was watching.

**The loop never waits for the page.** The server runs on its own thread and publishing never blocks: when a viewer's queue is full the OLDEST event is dropped. A live view that skipped a frame is correct; a 50Hz loop that missed its deadline is not. Without `--web` nothing is started at all — no bus, no thread, no socket.

**No secrets reach the page**, following the trace file's rule: the API key, environment variables and raw pre-classification figures are never published. A page is one screenshot away from being shared.

**The 3D view is shared, not copied.** The scene embedded in `sf telemetry --web` (StampFly model, lighting, per-OS trackpad zoom, duty-driven prop spin) was extracted into `lib/sfcli/assets/stampfly3d.js`, which both pages import. A copy would drift, and the first thing to drift would be the quaternion conversion whose 90-degree body-yaw bug took a numeric comparison against the SILS GUI to find. three.js and the STL parts stay locally served; no CDN.

### Afterwards — video and GUI replay

Every SILS flight now records a flight-log bundle and prints the one line that turns it into a video:

```
  flight log  : .../simulator/sils/viz/out_pilot/20260919t131801/sils_pilot_20260919t131801.sflog.zip
  watch it    : sf sils video -m pilot/20260919t131801
```

Each flight gets `simulator/sils/viz/out_<kind>/<datetime>/`. `finalize_flightlog()` keeps exactly one bundle per directory and deletes the rest, so sharing one would lose the flight worth watching as soon as another was flown. The stamp is lower-case because `sf sils video -m` lower-cases its argument. The bundle and the trace share that datetime.

### Why no bundle was produced before (three causes, stacked)

| # | Cause | Where | Fix |
|---|-------|-------|-----|
| A | `sf pilot run --sils` never **enabled** recording: it passed no `flightlog_dir` to `realtime_emu_env()`, so `SILS_EMU_FLIGHTLOG` was absent and the emulator wrote nothing | `lib/sfcli/commands/pilot.py` | Pass `<bundle>/flightlog`, as `sf sils fly` does |
| B | `say` and `mission` wrote the CSVs but **nobody assembled them** (`_finalize_flightlog()` was called from four places, all inside `sils.py`). Their CSV directory was also the bundle directory itself, which the assembling step deletes | `lib/sfcli/commands/pilot.py`, `lib/sfpilot/mission_run.py` | Call the existing `finalize_flightlog()` **after** the emulator exits; write CSVs to a subdirectory |
| C | **The emulator did not close its flight log when it exited via `quit`.** That path calls `std::_Exit(0)`, which skips stdio flushing, and unlike the normal end-of-run path it never called `sils_emu_flightlog_close()`. Every stream lost its buffered rows; the slowest one (`status.csv`, 1Hz) lost even its header, leaving a zero-byte file that `pd.read_csv` rejects with "No columns to parse from file" | `simulator/sils/emu/emu_main.cpp` | Call `sils_emu_flightlog_close()` before `_Exit` (one line) |

C stayed hidden under `sf sils scenario` because that command runs its duration out and leaves through the normal path. `sf pilot` ends by sending `quit`, so only that path was broken. This is the only change made to the SILS C/C++ for this work.

`sf sils video -m <name>` was also fixed to name its output from the last path segment, so a name containing a separator (`pilot/<datetime>`) no longer points the mp4 at a directory that does not exist.

## 5. Placement

`RealLink` moved from `lib/sfcli/commands/blocks.py` to `lib/sfpilot/link.py` on 2026-09-19, so that `sf blocks` and `sf pilot` drive the vehicle through one client instead of two copies that could drift apart. `blocks.py` imports it.

`SilsLink` satisfies the same `Link` interface as `RealLink`, so the layers above it do not know whether they are flying SILS or hardware. Its `set_battery_voltage()` and `set_wind()` are deliberately NOT part of that interface: a real vehicle cannot be told what its own battery reads or which way the wind blows. Those two drive the scenes only.

The scenes live in `lib/sfpilot/scenes.py` rather than as `.scn` files because a `.scn` timeline is frozen before the run starts and cannot react to a decision, whereas a scene includes what to do DURING the flight (walking the battery down). The fault-injection mechanisms themselves are shared with `.scn` -- the same Plant hooks.

The HTTP API (`POST https://api.typesafe.ai/v1/systemone`, Bearer auth) is called directly with httpx rather than through `typesafe-sdk`. The pilot loop needs one kept-alive connection, a hard per-request deadline and retries disabled; all three are plain httpx settings. The optional dependency is declared as `pilot = ["httpx>=0.27"]`.

## 6. Traces and Secrets

Decisions are written to `logs/pilot/<datetime>.jsonl`, one JSON object per line, readable with `grep` and `jq`. `logs/` is already covered by `.gitignore`.

The API key is never written to the repository, a trace, a state, any command output or an exception message -- a test asserts this, since users attach trace files to bug reports. CI and pytest use `FakeJudge` only and pass without a key.

### Where the key comes from (`lib/sfpilot/credentials.py`)

There is one resolution path. `sf pilot bench`, `run`, `say` and `mission` all go through `resolve_api_key()`, which tries these in order:

| Order | Source | Notes |
|-------|--------|-------|
| 1 | the environment variable `TYPESAFE_API_KEY` | always first, so a different key can be tried for one command without disturbing the stored one |
| 2 | the macOS login keychain | calls `security find-generic-password -a "$USER" -s <service> -w`. The service name defaults to `typesafe-api-key` (`JudgeConfig.keychain_service`) and can be changed with `SF_TYPESAFE_KEYCHAIN_SERVICE`. **Not attempted off macOS** — `security` is a macOS program, and calling it elsewhere would turn "no key configured" into an obscure crash |
| — | neither | raises `MissingApiKey` naming both ways to set one. The message never contains a key |

To store it in the keychain (the value is read from a prompt, so it stays out of the shell history):

```bash
security add-generic-password -U -a "$USER" -s typesafe-api-key -w
```

**Why not a `.env` file:** a `.env` is plain text living next to the repository, so the key is one `git add .` or one shared archive away from being published, and a file that is meant to stay untracked is only untracked until somebody's editor writes it somewhere else. The environment variable and the keychain both keep the secret out of the working tree entirely.

## 7. Tests

### The class of fault FakeJudge cannot catch (the lesson of 2026-09-19)

**`FakeJudge` does not read the words in a state.** It returns its declared answer whatever it is handed, so it is entirely blind to faults where the state DESCRIBES a healthy flight as an unhealthy one. Three of the five faults found on 2026-09-19 (the altitude target, the battery trend, the flapping classifications) are of exactly that kind, and they surfaced only in a live flight while all 208 tests stayed green.

The remedy is a test that runs **measured samples through Monitor → Summarizer and inspects the words that come out** (`test_nominal_flight_words.py`). It checks five things:

| Check | Intent |
|-------|--------|
| No alarming word (`below target`, `fell sharply`, `drifting`, ...) appears anywhere during a healthy hover | directly prevents the 7.4 s landing |
| The hold altitude is taken from the altitude the aircraft is actually holding | prevents a return to a constant in the code |
| The signature never changes once the hover has settled | prevents answers being lost to staleness |
| The takeoff voltage drop is not reported as a battery falling sharply | prevents a load step being mistaken for a discharge |
| The `battery_drop` voltage walk still reaches "running low", "dangerously low" and "fell sharply" | ensures the four fixes above did not make the trend blind |

The samples are recorded from a real SILS flight rather than written by hand (`lib/sfpilot/tests/fixtures/`; the README there gives the reason — a hand-written sample would encode the same wrong assumptions the code did).

253 tests in `lib/sfpilot/tests/` (including 14 for the live view and 6 for the recording), all passing without a key or a network, plus 10 in `lib/sfcli/commands/test_web_assets.py` for the shared browser assets (including the check that extracting the 3D scene left `sf telemetry --web` behaving as before), plus six real SILS flights in `simulator/tests/test_mission_sils.py` (under `--fake`, skipped automatically when the emulator is not built): that every uncertain case becomes holding and that a 10-second hold becomes a landing; that numbers become words and trends require duration; that the state carries no numbers; that `emergency` cannot emerge from the judging path; that a Judge which raises does not stop the loop; and that a real flight log replays into decision records.

P3 adds: figure extraction across half-width, full-width and kanji numerals in m, cm and degrees; every assembly rule (everything after the first `none` is dropped, a takeoff and a landing are supplied, an unspecified amount takes the default band, a spoken figure outranks the band); that the envelope pre-check refuses an over-reaching plan and names the offending step; that a low-confidence step is not flown; the `return_home` computation including a turn along the way; that the hovering `rc` is withheld while a plan drives the vehicle but `land` is not; that waiting for an answer does not interrupt the sequence while a hold Jev chose does; that a step waits for the vehicle's reply and for the craft to settle; and that a non-interactive session will not fly without `--yes`.

P4 adds: that a route leaving the envelope is refused by the leg name the operator wrote, and that a missing verb, an unknown verb, a missing amount, an empty route and a missing file are each refused; that a bare name resolves to a shipped route while a local file of the same spelling always wins; that arrival is classified by projecting the error onto the leg's own direction, so a leg that ended sideways of its target is not called an overshoot, and that the tolerance comes from the config; that the mission state carries no numbers, that "3/10" reaches the model as a position while the retry count and the elapsed time reach it as words, and that an unknown arrival is omitted entirely; that the retry limit converts a redo into a skip, that a hold resolved into a retry still counts against that limit, that a low battery replaces every going-on answer with `return_home` while never replacing a `land`, and that the time limit is checked before a leg rather than after; that a landing sends `stop` before `land`, waits on the measured speed, does not accept a single slow reading as rest, lands anyway at its ceiling, uses the shorter ceiling when the reason cannot wait, and commands nothing else while it is under way; and, in SILS, that `nominal` completes with the ground truth matching the route, that `return_home` brings the craft back before it lands, that the descent does not slide as far as an unsettled one, that `battery_drop` ends the route early and lands, and that `drift` never exceeds the retry ceiling and records why anything was skipped.

## 8. Proposed as a Separate Plan (out of scope here)

A vehicle-side safety feature: land when PC API commands have been absent for N seconds. It touches the state machine, so it needs the six vehicle design documents read and the architecture invariants (INV) checked, in its own plan document.

### Accumulating Knowledge

This repository has no `knowledge/` directory. Measured round-trip times and examples of mistaken judgements are worth keeping, so introducing an OKF (Open Knowledge Format) `knowledge/` directory is proposed -- not created without permission. Until then, measurements go in section 4 above.
