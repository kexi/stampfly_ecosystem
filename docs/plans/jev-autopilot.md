# Jev による StampFly 自動操縦（`sf pilot`）

状態: **実装中**。作成 2026-09-19、最終更新 2026-09-19。

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。

## 1. 概要

### この文書について

TypeSafe の System One モデル **Jev**（自然言語と状況から、型のついた判断と確率を返すモデル）を判断役に据え、StampFly を自動操縦する計画を述べる。センサーは高速に監視し、判断は Jev に任せる。用途は 3 つ: ①状況監視と安全判断 ②自然言語の指示で飛ぶ ③ミッション中の次の一手。最初は SILS（Software In the Loop Simulation）で確認し、Jev の応答が遅い・不確かなときはその場で待機する。

### 対象読者

`sf pilot` の実装・改修を行う者、および本方式の安全設計を検討する者。

### 実装状況（2026-09-19 時点）

P0、P1、P2a、P2 を実装した。それ以外は未着手である。

| 段階 | 内容 | 状況 |
|------|------|------|
| P0 | 本計画文書、`sf pilot bench`（Jev 往復時間・トークン数の実測） | **実装済み** |
| P1 | `lib/sfpilot` の中核（Monitor / Summarizer / Judge / Arbiter / Executor）、FakeJudge、ReplayLink | **実装済み** |
| P2a | テレメトリ拡張（UDP:5005 を 140B の v2 に。電池電圧・下向き ToF・フロー・地磁気・気圧高度を追加。様式は `firmware/vehicle/docs/detailed_design.md` §10） | **実装済み**（実機未確認） |
| P2b | 前方 ToF の駆動（`sensor_tof_front` 新設、`SensorSnapshot` へのミラー、テレメトリ bit1 の供給）。**実機確認必須。バッテリー電源必須**（USB 給電では前方 ToF が立ち上がらない事例がある） | 未着手 |
| P2 | SILS 連携（stdin の `api` 行、SilsLink、場面 3 種）、`sf pilot run --sils`、実機 UDP:5005 の 50Hz 受信 | **実装済み**（Jev 実走は未実施） |
| P3 | `sf pilot say`（自然言語の指示） | 未着手 |
| P4 | ミッション（経路巡回）と `next_move`。**前方 ToF による探索**（P2b 完了が前提。前方の空きを見て進路を選ぶ） | 未着手 |
| P5 | 実機。事前に往復時間を実測し、送信機を手元に置く | 未着手 |

**前方 ToF の現状（重要）**: 前方 ToF はハードウェアとしては実装されているが、**現行ファームでは駆動していない**。`TofTask` が XSHUT を low に固定してリセット保持している（VL53L3CX 2 個が同じ I2C アドレス 0x29 で起動し、底面のアドレス変更が両方に届いて測距データが混線するため）。テレメトリ v2 には枠（`tof_front`、有効ビット bit1）を確保してあるが、現行ファームでは bit1 は常に 0、値は常に -1.0 である。**障害物回避・探索を Jev に判断させる計画は、P2b の完了まで前提が成立しない。**

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
| `lib/sfcli/commands/pilot.py` | `sf pilot bench` / `sf pilot replay` / `sf pilot run` | 実装済み |
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

API キーは環境変数 `TYPESAFE_API_KEY` のみで渡す。リポジトリ・記録・state・コマンドの出力には書かない。記録は利用者が不具合報告に添付するものなので、伏せ字なしで共有して安全であることを試験で確認している。CI と pytest は `FakeJudge` だけを使い、キー無しで通る。

## 7. 試験

`lib/sfpilot/tests/` に 50 件。キー不要・通信不要で通る。

| 観点 | 確認内容 |
|------|---------|
| Arbiter | 期限超過・鮮度切れ・低確信・拮抗・包絡外・API エラーがすべて待機になること。待機 10 秒で着陸すること |
| Monitor | 数値が区分（言葉）になること。傾向に継続時間が要ること。電池危険で Judge を待たず着陸すること |
| Summarizer | state に数値が 1 つも無いこと。不明な項目が省かれること。指紋が区分の変化に追随すること |
| Judge | `emergency` がどの質問の選択肢にも無く、判断経路から出てこないこと |
| ループ | 例外を投げる Judge でも止まらないこと。記録が 1 行 1 JSON で出ること |
| ReplayLink | 実際の飛行ログを時刻順に再生し、判断の記録が出ること |

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

P0, P1, P2a and P2 are implemented. The rest is not started.

| Stage | Content | Status |
|-------|---------|--------|
| P0 | This plan document, `sf pilot bench` (measure Jev round-trip time and tokens) | **Done** |
| P1 | `lib/sfpilot` core (Monitor / Summarizer / Judge / Arbiter / Executor), FakeJudge, ReplayLink | **Done** |
| P2a | Telemetry extension (UDP:5005 becomes the 140B v2 packet, adding battery voltage, downward ToF, optical flow, magnetometer and pressure altitude; format in `firmware/vehicle/docs/detailed_design.md` §10) | **Done** (not verified on hardware) |
| P2b | Drive the forward ToF (add `sensor_tof_front`, mirror into `SensorSnapshot`, supply telemetry bit1). **Requires hardware verification and battery power** (the forward ToF has been seen not to come up on USB power) | Not started |
| P2 | SILS integration (`api` stdin verb, SilsLink, three scenes), `sf pilot run --sils`, RealLink's 50Hz UDP:5005 reader | **Done** (not yet exercised against the live Jev API) |
| P3 | `sf pilot say` (natural-language instruction) | Not started |
| P4 | Mission (route patrol) and `next_move`. **Forward-ToF exploration** (depends on P2b: choose a heading from the clear space ahead) | Not started |
| P5 | Real hardware, after measuring round-trip time, transmitter in hand | Not started |

**Forward ToF status (important):** the forward ToF exists in hardware but is **not driven by the current firmware**. `TofTask` holds its XSHUT low, keeping it in reset (both VL53L3CX parts boot at I2C address 0x29, so re-addressing the bottom sensor would reach both and interleave their ranging data). Telemetry v2 reserves the slot (`tof_front`, validity bit1), but on current firmware bit1 is always clear and the value is always -1.0. **Any plan to have Jev judge obstacle avoidance or exploration rests on a premise that does not hold until P2b is done.**

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

**Problem found (out of scope here, reported rather than worked around):** the firmware's horizontal position estimate has its axes swapped relative to ground truth. Pushing north with `wind 0.02 0 0` moves `truth.csv`'s `pos_x` to +0.161 m while the firmware's `posvel.csv` keeps `pos_x` at exactly 0 and moves `pos_y` to -0.138 m. This predates `sf pilot` -- it reproduces through the existing `.scn` `wind` event with no stdin involved -- and the Plant's `setWind` and `frames::ned_to_enu` are both correct, as is the truth. Position hold is self-consistent (it pulls 0.161 m back to 0.025 m), which is probably why this has not surfaced. It is not in `docs/architecture/simulation-policy.md`'s backlog. It could affect anything that maps position between SILS and the real vehicle, so a separate investigation is recommended.

## 5. Placement

`RealLink` moved from `lib/sfcli/commands/blocks.py` to `lib/sfpilot/link.py` on 2026-09-19, so that `sf blocks` and `sf pilot` drive the vehicle through one client instead of two copies that could drift apart. `blocks.py` imports it.

`SilsLink` satisfies the same `Link` interface as `RealLink`, so the layers above it do not know whether they are flying SILS or hardware. Its `set_battery_voltage()` and `set_wind()` are deliberately NOT part of that interface: a real vehicle cannot be told what its own battery reads or which way the wind blows. Those two drive the scenes only.

The scenes live in `lib/sfpilot/scenes.py` rather than as `.scn` files because a `.scn` timeline is frozen before the run starts and cannot react to a decision, whereas a scene includes what to do DURING the flight (walking the battery down). The fault-injection mechanisms themselves are shared with `.scn` -- the same Plant hooks.

The HTTP API (`POST https://api.typesafe.ai/v1/systemone`, Bearer auth) is called directly with httpx rather than through `typesafe-sdk`. The pilot loop needs one kept-alive connection, a hard per-request deadline and retries disabled; all three are plain httpx settings. The optional dependency is declared as `pilot = ["httpx>=0.27"]`.

## 6. Traces and Secrets

Decisions are written to `logs/pilot/<datetime>.jsonl`, one JSON object per line, readable with `grep` and `jq`. `logs/` is already covered by `.gitignore`.

The API key is passed only through the environment variable `TYPESAFE_API_KEY`, and never written to the repository, a trace, a state or any command output -- a test asserts this, since users attach trace files to bug reports. CI and pytest use `FakeJudge` only and pass without a key.

## 7. Tests

50 tests in `lib/sfpilot/tests/`, all passing without a key or a network: that every uncertain case becomes holding and that a 10-second hold becomes a landing; that numbers become words and trends require duration; that the state carries no numbers; that `emergency` cannot emerge from the judging path; that a Judge which raises does not stop the loop; and that a real flight log replays into decision records.

## 8. Proposed as a Separate Plan (out of scope here)

A vehicle-side safety feature: land when PC API commands have been absent for N seconds. It touches the state machine, so it needs the six vehicle design documents read and the architecture invariants (INV) checked, in its own plan document.

### Accumulating Knowledge

This repository has no `knowledge/` directory. Measured round-trip times and examples of mistaken judgements are worth keeping, so introducing an OKF (Open Knowledge Format) `knowledge/` directory is proposed -- not created without permission. Until then, measurements go in section 4 above.
