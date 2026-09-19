# vehicle Detailed Design
# vehicle 詳細設計書

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。

## 1. 概要

本文書はvehicleの詳細設計を定義する。アーキテクチャ設計書（architecture.md）に基づき、実装レベルの仕様を記述する。

| 項目 | 内容 |
|------|------|
| Pub-Subフレームワーク | トピック定義、バッファ方式、API |
| 状態遷移テーブル | onExit/onEnterコールバック |
| 制御インターフェース定義 | IController |
| 状態推定インターフェース定義 | IEstimator |
| パラメータシステム | マクロ1行定義、バリデーション、コールバック |
| センサ観測スイッチ | パラメータ連動、推定内部で判断 |
| ディレクトリ構造 | ESP-IDFプロジェクト配置 |
| メモリ配置 | パーティション、Blackbox |

## 2. Pub-Subフレームワーク

### 設計方針

- StampFlyの制約に最適化した独自の軽量設計
- 同一MCU内のタスク間通信のみ
- シリアライズ不要（構造体メモリ直接共有）
- 全トピックはコンパイル時に確定
- 内部バッファ方式はデータフローの特性に応じて選択

### トピック定義

トピックの実装方針は **ヘッダで `extern` 宣言、対応する `.cpp` で実体定義** とする。`inline` グローバル変数は ESP-IDF のリンク方針と相性が悪く（複数翻訳単位を跨ぐ初期化順序に依存しやすい）、`extern` + 実体定義のほうが安全。

```cpp
// topics.hpp — 全トピックの宣言（1箇所）
// All topic declarations (single location)
// トピック追加はここに1行追加し、対応する .cpp に定義を1行追加する
// Adding a topic: add one extern line here + one definition line in .cpp

#include "topic.hpp"
#include "data_types.hpp"

namespace sf {

// Topic<DataType, BufferPolicy, BufferSize>
extern Topic<ImuData,         RingBuffer, 8>  sensor_imu;
extern Topic<TofData,         Queue, 2>       sensor_tof;
extern Topic<FlowData,        Queue, 2>       sensor_flow;
extern Topic<MagData,         Queue, 2>       sensor_mag;
extern Topic<BaroData,        Queue, 2>       sensor_baro;
extern Topic<PowerData,       Latest, 1>      sensor_power;
extern Topic<StateEstimate,   Latest, 1>      estimate_state;
extern Topic<CommandSetpoint, Latest, 1>      command_setpoint;
extern Topic<ControlOutput,   Latest, 1>      control_output;
extern Topic<MotorOutput,     Latest, 1>      actuator_motor;
extern Topic<SystemMode,      Latest, 1>      system_mode;
extern Topic<SystemAlert,     Queue, 4>       system_alert;

}  // namespace sf
```

```cpp
// topics.cpp — トピック実体定義
// Topic instance definitions

#include "topics.hpp"

namespace sf {

Topic<ImuData,         RingBuffer, 8>  sensor_imu;
Topic<TofData,         Queue, 2>       sensor_tof;
// ... 以下同様
// ... and so on

}  // namespace sf
```

### バッファ方式（3種）

| 方式 | 特性 | 用途 |
|------|------|------|
| RingBuffer | Lock-free SPSC、全サンプル保持、ISR安全 | IMU→推定、全データ→ログ |
| Queue | FreeRTOS Queue、バッファリング | 低レートセンサ（ToF/Flow/Mag/Baro） |
| Latest | 最新値上書き、共有メモリ | 推定値→制御、推定値→テレメトリ |

### API

```cpp
// Publish (producer side)
// 発行（生産者側）
ImuData data = readSensor();
sensor_imu.publish(data);

// Subscribe with callback (consumer side, at init)
// コールバックで購読（消費者側、初期化時に登録）
sensor_imu.subscribe([this](const ImuData& d) {
    this->onImuData(d);
});

// Poll latest value
// 最新値のポーリング
auto data = estimate_state.latest();
```

### トピック追加手順

1. `data_types.hpp` に構造体を追加（`timestamp` フィールド必須）
2. `topics.hpp` に `extern Topic<>` 宣言を1行追加
3. `topics.cpp` に対応する実体定義を1行追加
4. 発行側で `publish()`、購読側で `subscribe()` または `latest()` / `read()`
5. **`docs/topic_reference.md` §3 の表に 1 行追加**（R9 — Topic SSOT 文書）
6. **publisher / subscriber 側のタスクヘッダに `@publisher` / `@subscriber` アノテーション**を追加（R13）

### Publisher / Subscriber アノテーション規約（R13）

各タスク（`tasks/*.cpp`）の冒頭 Doxygen ブロックに、利用する Topic を明記する。これにより「どの Topic を誰が publish / subscribe するか」がコードを読むだけで分かり、`topic_reference.md` の表との一貫性も担保される。

```cpp
/**
 * @file imu_task.cpp
 * @brief IMU reading and state estimation task (400Hz)
 *
 * @publisher  sensor_imu, estimate_state
 * @subscriber sensor_tof, sensor_flow, sensor_mag, sensor_baro
 *
 * @design architecture.md §6 — ImuTask                                [OK]
 * @design detailed_design.md §8 — ImuTask: 400Hz, priority 24         [OK]
 */
```

将来は lint で「アノテーション ↔ 実コード」の整合検証も視野に入れる。

### 将来予約 Topic（v3 設計、未実装）

v3 設計で 4 つの Topic を予約定義した。実装は後続マイルストーンで行うが、置き場所と入出力契約は今のうちに確定する（R11）。

| Topic | データ型 | バッファ | Publisher | 用途 | 実装予定 |
|-------|---------|--------|-----------|------|---------|
| `sensor_imu_raw` | `ImuRawData` | RingBuffer 8 | ImuTask | キャリブ前生 IMU。L2 学習者向け教材 | M2 |
| `sensor_health` | `SensorHealth` | Latest | sf_board / 各 task | publisher 死活監視（presence / last_update_us / quality） | M2 |
| `command_target` | `GuidanceTarget` | Latest | Guidance / Navigator | 位置 + yaw target、ウェイポイント | M4+ |
| `nav_path` | `NavigationPath` | Queue 4 | Navigator | 経路シーケンス | Phase 6 |

詳細な使用方針・データ型定義・選定根拠は [`topic_reference.md`](topic_reference.md) §3.2 を参照。

## 3. 状態遷移テーブル

### onExit/onEnterコールバック

> **注: このテーブルはクラスA（遷移リセット）のみを定義する。** センサ1サンプル精度を要する推定器内部の reset（クラスB、例: 離陸時の鉛直ハンドオフ `resetPositionVelocity`/`holdPositionVelocity`）はこのテーブルの対象外で、そのセンサを観測するタスク（ImuTask）が所有する。クラスA / クラスB の分類基準・境界条件・越権防止制約は [`architecture.md`](architecture.md) §4「リセット処理の2層分類」を参照。

| 遷移 | onExit（旧状態を出る時） | onEnter（新状態に入る時） |
|------|------------------------|------------------------|
| INIT → IDLE_GROUND | — | キャリブレーション管理を起動 |
| IDLE_GROUND → IDLE_HELD | （リザーブ） | （リザーブ） |
| IDLE_HELD → IDLE_GROUND | （リザーブ） | （リザーブ） |
| IDLE_GROUND → ARMED_GROUND | （リザーブ） | 全PIDリセット、ESKF**姿勢共分散の膨張**（注1）、ブザー(arm音) |
| ARMED_GROUND → TAKEOFF | （リザーブ） | 離着陸MGR: 離陸シーケンス開始、高度目標セット（ALT/POS は `takeoff_target_alt_`=0.5m、注4） |
| TAKEOFF → FLYING | 離着陸MGR: シーケンス終了 | ESKF位置/速度リセット（注2: クラスB, ImuTask）、~~バイアスフリーズ解除~~（注3で見送り） |
| サブモード切替（地上/FLYING） | 旧サブモードのコントローラリセット | 新サブモードの初期化（高度/位置キャプチャ＋ALT/POS 進入時は**スロットル再センターロック**（中央に戻すまでスロットル入力を無視）、注4）。地上では制御器の鉛直フェーズ判定（Grounded=推力ゼロ）により ALT/POS 選択でもモータは回らない |

### 3.1 モード調停表（規範 / Normative）

**この表を基準とし、コード（state_task のモード調停・StateManager::requestModeChange）は本表を写す。**
表に無い (状態, 事象) の組合せを実装してはならず、表の全セルが実装されていなければならない
（抜けのない設計 — 実機 LED バグ①と TAKEOFF 窓の適用漏れ②は本表の作成によって発見・修正された）。

事象の定義:
- **エッジ**: モードスイッチの位置変化（want ≠ prev_want）。拒否された場合 prev_want を更新せず**持続**（受理される状態に達した時点で適用）
- **不一致**: スイッチ位置と実モードの不一致（want ≠ mode、エッジなし）。接地リセット等で発生
- **API設定**: ApiCmd::Takeoff によるモード設定（同一周期で ARM まで進む）
- **接地リセット**: 空中状態→IDLE_GROUND 突入時の STABILIZE 強制（再飛行安全則）

| 状態 \ 事象 | エッジ | 不一致（レベル） | API設定 | 接地リセット |
|------------|--------|----------------|---------|------------|
| INIT | 拒否（エッジ持続） | 無視 | 拒否 | — |
| IDLE_GROUND | **適用** | **適用（再適用・マーカーログ）** | **適用** | （突入時に作動し、直後に不一致則が再適用） |
| IDLE_HELD | 拒否（持続→設置時に適用） | 無視 | 拒否 | — |
| ARMED_GROUND | **適用** | 無視（エッジのみ） | **適用** | — |
| TAKEOFF | 拒否（持続→FLYING で適用） | 無視 | 拒否 | — |
| FLYING | **適用** | 無視（放置送信機が API を覆せない） | （誘導目標のみ） | — |
| LANDING | 拒否（持続→接地後に適用） | 無視 | 拒否 | — |

検証: SILS `acro_crash_relevel`（①の固定）、`api_flight`（FLYING 不一致の無視＝API保護）、
`modeswitch`/`alt_flight`（FLYING エッジ適用）、`alt_auto_takeoff`（IDLE_GROUND API設定）。
| ARMED_GROUND → TAKEOFF（ALT/POS 選択時） | — | **ARM 自体がトリガ**（スプールドウェル後, 注4）→ ControllerCmd::Takeoff 発行 → 制御器が自動離陸（高度カスケードで目標 0.5m へ速度制限上昇＝**鉛直のみ自動**。姿勢はパイロット操縦可、ALT_HOLD は roll/pitch=スティック、POS は発進点保持） |
| TAKEOFF → FLYING（ALT/POS） | — | **制御器が目標高度 0.5m を捕捉**（`isTakeoffComplete`→`controller_status.takeoff_reached`）→ StateTask が ControllerCmd::TakeoffComplete 発行 → 通常モード則を係合（**目標値 0.5m を保持**、行き過ぎ瞬時高度でなく。注4） |
| FLYING → LANDING | （リザーブ） | 制御器が `VerticalPhase::Landing` に切替（注6, INV-1）。鉛直チャネルが `landing_descent_rate_` で降下、姿勢はパイロット（リンク途絶時のみ水平, INV-2）。**ALT/POS でのパイロット DISARM**（注5）or 通信途絶の猶予経過で起動 |
| FLYING → ARMED_GROUND | 高度/位置コントローラリセット | ESKFホールド |
| FLYING → IDLE_GROUND | 高度/位置コントローラリセット | モーター停止、ESKFリセット、ブザー(disarm音)。**ACRO/STABILIZE のパイロット DISARM・衝突検知・緊急停止**（注5） |
| LANDING → IDLE_GROUND | 離着陸MGR: シーケンス終了 | モーター停止、ESKFリセット、~~バイアスフリーズ~~（注3で見送り）。接地検出＝本当の DISARM |
| ARMED_GROUND → IDLE_GROUND | （リザーブ） | モーター停止、ブザー(disarm音) |

「リザーブ」は実装・テスト時に必要に応じて追加する。

**注1（ARM時の ESKF 処理 — SILS 掃引で確定）:** 当初は「ARM時 ESKF 全リセット」（地上の共分散収束は飛行を代表しない、という根拠）だったが、SILS のリセットタイミング掃引（8方策×飛行スイート）で、全リセット — および位置/速度/バイアスの共分散の膨張 — は離陸過渡を不安定化すると判明した。再膨張した共分散がスラスト汚染された加速度計を過信し、POS_HOLD 姿勢が発散する（pos_roll/pitch/flight 墜落）。飛行スイート全PASS は2方策のみ＝「何もしない」と「**姿勢の共分散だけ膨張**」。後者を採用：設計意図（ARM で姿勢の自信をリセット）を満たしつつ、姿勢は離陸前に地上で重力から再収束するため安定。実装は `EstimatorCmd::InflateCov(CovScope::Attitude)`（推定値 x は保持し姿勢共分散のみ初期値へ）。

**注2（位置/速度リセット = クラスB）:** 「ESKF 位置/速度リセット」はタイミング命の ToF 同期鉛直ハンドオフ（クラスB, `ImuTask::applyVerticalGroundHandoff`）。状態機械の onEnter 経由（~20ms 遅れ）に移すと α-βトラッカ初期化が遅れ POS_HOLD 姿勢が劣化する（[`architecture.md`](architecture.md) §4 クラスA/B 分類）。地上ゼロは飛行を代表しないが、その手当ては空中エッジで ImuTask が正確に行う。

**注3（バイアスフリーズ/解除 = 見送り）:** ESKF の凍結機構（`active_mask`＋`enforceCovarianceConstraints`）は「センサ恒久不在」用の隔離（凍結状態の共分散を毎周期 init 値へ戻す）で、地上↔飛行でトグルする用途とは非互換。トグルすると解除時に巨大な共分散が復活し、注1と同じ離陸発散を起こす（SILS で実証）。よってバイアス推定は飛行中も常時アクティブとし、フリーズ/解除は配線しない。地上振動からの校正バイアス保護が必要になった場合は、共分散を init に戻さない「ソフト凍結」を別途設計する（将来課題）。

**注4（ALT/POS 離陸・モード進入の再設計 — 2026-06-14, ユーザー確定）:** 旧仕様「スロットル>0.5 で離陸・ToF 空中検知 0.15m で高度保持」を、パイロットがスティックを戻すタイミングを計れない・0.15m は地面効果で物理的に不安定、という理由で再設計した。確定事項:
- **① ARM 起動離陸:** ALT_HOLD/POS_HOLD では **ARM 自体が離陸トリガ**（スロットル入力不要）。StateTask が ARMED_GROUND 突入後、短いスプール/整定ドウェル（`config::ARMED_GROUND_SPOOL_US`=0.3s, モータはゼロのまま=暴発防止＋ARM 時の姿勢共分散膨張が重力から再収束）を待ってから `notifyTakeoff()`。**ACRO/STABILIZE は従来どおり手動スロットル離陸**（生スロットル＝推力、自動離陸なし。`config::TAKEOFF_THROTTLE_THRESH`）。
- **② 目標高度 0.5m・0.15m の役割分離:** TakeoffClimb は高度カスケードで目標 `takeoff_target_alt_`=0.5m（PidController 定数、地面効果回避）へ速度制限（`takeoff_climb_rate_`=0.3m/s）つき上昇する。Airborne 進入後も `alt_setpoint_`=0.5m を保持するので**目標値で捕捉**される（運動量による行き過ぎ瞬時高度でなく、決定②）。TAKEOFF→FLYING は制御器の `isTakeoffComplete()`→`controller_status.takeoff_reached` で成立するが、その判定は**ロバストな「片側到達」＋タイムアウト・バックストップ**: 高度が目標近傍まで上昇（`altitude >= target - kTakeoffCaptureBandM`、上昇途中で成立）を `kTakeoffSettleCycles` 持続 OR `kTakeoffMaxCycles`(3s) 経過で完了。**旧来の両側バンド+低速整定は実機の定常偏差/速度ノイズで成立せず、機体が TakeoffClimb に留まりロール/ピッチが0固定される実機バグ（2026-06-14）を起こした**ため是正（SILS `alt_arm_rollpitch` でガード）。**ToF 空中検知 0.15m は ESKF 鉛直ハンドオフ専用**（注2, ImuTask）に役割分離し、ALT/POS の離陸完了判定には用いない（ACRO/STABILIZE 手動離陸の TAKEOFF→FLYING のみ ToF airborne で判定）。
- **④ API takeoff 統一:** ApiTask の takeoff も同じ ARM 起動自動離陸経路を通り（StateTask が requestArm 後、上記 ② で離陸）、離陸後の高度をそのまま誘導で保持＝目標 0.5m を継承（旧 0.8m ハードコードを廃止）。再センターロックは手動 RC 限定（API はコマンド操作）。
- **ALT_HOLD/POS_HOLD スロットルは対称（バネ復帰式, ユーザー確定）:** スロットルはバネ中央復帰で、ALT/POS では `CommandSetpoint.throttle_axis = (raw-2048)/2048 ∈ [-1,+1]` を使う。**中央 raw 2048 = 0 = 現在高度ホールド**、上(>2048)=上昇（`altitude.climb_rate`）、下(<2048)=降下（`altitude.descent_rate`、**上昇/降下は別パラメータ**）。スティック上下で目標高度を増減する。これは旧 vehicle `altitude_controller` の `stickToClimbRate`（中央2048=hold）と同型＝飛行実績あり。**STABILIZE/ACRO は別** — `throttle [0,1]`（中央2048=推力0=OFF・上半分のみ・離すと降下）。※ vehicle は当初 ALT_HOLD で STABILIZE 用の `[0,1]` を `(throttle-0.5)` で流用し中立を raw 3072 に誤配置していた（バネ静止 2048 で降下＝実機で操縦不能になる潜在バグ）。本対称化で是正。詳細は [`alt_hold_takeoff_findings.md`](alt_hold_takeoff_findings.md)。
- **スロットル再センターロック（`throttle_recentered_`）:** 離陸後（Case A）・飛行中の ALT/POS 進入（Case B, onModeChange）でロックを掛け、スロットルが一度**中央（バネ静止 raw 2048, ±`stick_deadzone_`）に戻って初めて解除し**高度操作を有効化する（暴発・高度ジャンプ防止）。バネ式は離せば中央に戻り解除（旧 vehicle stick-lock と同型）。タイムアウトなし。誘導/API は高度を歩く設定点で動かしスロットル経路を使わないためロック対象外。
- **⑥ ARM 後モードロックは見送り:** 飛行中モード変更は従来どおり可能（§3.1 モード調停表は現状維持）。コントローラ↔機体の双方向モード同期の改修が前提のため将来再検討。

**注5（DISARM のモード依存ルーティング = 自動着陸 — 2026-06-14, ユーザー確定）:** パイロットの DISARM 操作は機体状態・モードで意味が変わる。
- **ALT_HOLD/POS_HOLD で FLYING 中の DISARM → 自動着陸（FLYING → LANDING）:** 高度制御モードは自力で着陸できるため、空中でモータを切らず**緩降下**（`computeLanding`, `landing_descent_rate_`=0.3m/s）で接地し、**接地検出で初めて本当の DISARM**（LANDING → IDLE_GROUND）。空中での即カットは機体を落とすだけ、というユーザー要望。`StateManager::requestDisarm()` が `state_==FLYING && mode_>=ALT_HOLD` を判定して LANDING へルーティング。
- **ACRO/STABILIZE・地上での DISARM → 即カット（→ IDLE_GROUND）:** 手動推力モードは自動着陸を持たない。地上（ARMED_GROUND）も常に即カット。
- **緊急停止（`requestEmergencyStop()`）:** 任意の armed 状態から無条件で即カット。**API `emergency` verb** と、**自動着陸中の再 DISARM（中断）**に用いる。`requestDisarm()` を LANDING 中に再度押すと `state_!=FLYING` ゆえ即カット経路に落ちる＝2回押しが中断になる。
- **検証:** SILS `alt_disarm_land`（ALT_HOLD で ARM→自動離陸→**DISARM→自動着陸の緩降下→接地→DISARM** の全鎖。降下中 duty>0.5＝モータ稼働、DISARM 0.4s 後も alt>0.2＝自由落下でない、終端 alt<0.05＝着地を assert）。既存の飛行スイート（`alt_flight`/`pos_flight` 等）は DISARM 直前にモードスイッチを落として STABILIZE へ復帰するため即カット経路を通り、本変更の影響を受けない。

**注6（着陸則の統一＝Landing を VerticalPhase 化・操縦可能化 — 2026-06-14, リファクタA, INV-1/INV-2）:** 当初 §注5 で「将来課題」とした「自動着陸の降下中も姿勢をパイロットに」を、設計不変条件（[`architecture.md`](architecture.md) INV-1/INV-2）に基づき本実装で達成した。**背景＝場当たりパッチの是正:** §② の「鉛直のみ自動・姿勢は常にパイロット」原則を離陸（TakeoffClimb）には適用したのに、着陸 `computeLanding()` は古い前提「フェイルセーフ専用・スティック無視」のまま**並列の制御経路**として残り、後付けのパイロット起動着陸でこの前提が崩れ「着陸中ロール/ピッチが効かない」実機バグを生んだ。
- **修正:** `computeLanding()` を**削除**し、Landing を `VerticalPhase`（Grounded/TakeoffClimb/Airborne/**Landing**）の一フェーズとして `compute()` の**単一姿勢＋レートパイプラインに統合**（INV-1）。フェーズが変えるのは鉛直チャネル（`landing_descent_rate_` で降下, モード非依存＝ACRO/STABILIZE からの通信途絶着陸にも対応）と接地条件のみ。姿勢は他フェーズと同じ1本道。
- **操縦/水平の作り分け（INV-2, 単一判定）:** Landing 中、**リンク生存中はパイロットが roll/pitch/yaw を保つ**（パイロット指令の着陸は操縦可）。**リンク途絶（通信途絶フェイルセーフ＝パイロット不在）なら単一の水平判定で roll/pitch/yaw を 0 に強制**。生存判定は設定点の新鮮さ `(state.timestamp − setpoint.timestamp) < kLandingLinkStaleUs(500ms, R16)` で行い、**StateManager からフラグを配線しない**（新鮮さだけでパイロット在否が分かり、降下中にリンクが切れても自動で水平化する）。
- **検証:** 新 SILS 2 本＝`alt_disarm_land_steer`（リンク生存のパイロット着陸: 降下中にロール保持→`tilt_max=11°`＝操縦可。旧 computeLanding なら≈0 で FAIL）/`commloss_land_level`（フェイルセーフ着陸: 猶予中 FLYING は古いロールで `tilt 11.6°` だが LANDING 突入後は水平判定で `tilt 3.6°` に水平化＝INV-2 の敵対ガード）。再確認試験（変更で既存の動作が壊れていないかを自動で確かめる試験）SILS 29/29・host 25/25・実機ビルド OK。

**注7（地面効果フロート対策＝降下停滞による接地検出 — 2026-06-14, リファクタB, INV-3）:** 実機で「ある程度の高度で手動 DISARM しないと着地しない／地面付近は予想より小さい Duty で浮く」現象（パイロット報告）。真因＝**地面効果**（接地近傍でローター揚力が増し、機体が低推力で高度を保つ）で一定速度降下が 5cm 地上閾値の上で停滞し、**ToF のみ（<5cm 必須）の旧着陸検出器が作動しない**。
- **修正（検出層 `sf_takeoff_landing`, INV-3）:** 接地検出に第2経路を追加。**(1) 確実な接地**＝`on_ground_`(ToF<5cm)＋静止を `landing_hold_ms`(1s)。**(2) 降下停滞**＝**着陸降下が指令中**(`in_landing_descent`=FlightState::LANDING, `armed` と同じ状態由来の入力)＋ToF が `near_ground_tof_m`(12cm)以内＋鉛直速度停滞(<`landing_vel_mps`)を `stall_hold_ms`(0.6s)。「降下しようとしているのに地面に止められている＝地面が支えている」をキネマティクスで判定し、**脆い推力閾値を使わない**（低ホバーは降下指令でないので判定条件を満たさず誤検出しない）。検出は検出層、遷移判断は StateTask（検出と判断の分離）。
- **SILS 地面効果モデル（プラント, Model fidelity）:** `lift ×= 1 + ge_gain·exp(−z/ge_height)`。**既定 OFF**（クリーン経路バイト一致, 既存 29 シナリオ不変）で、`sf sils scenario --ground-effect [gain]`（env `SILS_EMU_GROUND_EFFECT`）でオプトイン（ノイズノブと同流儀）。GE ON で実機同様の「低 Duty フロート」（duty 0.75→0.69, alt~0.05m）を再現。**ただし SILS は真値速度ゆえ速度ループが <5cm まで押し切り cond1 で着地する**（実機の「5cm 上で無限フロート」は近地面の速度推定劣化が原因で、真値 SILS では再現されない）。
- **検証:** **host ユニットテスト 4 本**（`land_firm_ground`/`land_stalled_descent_ground_effect`＝8cm フロートを cond2 が捕捉/`land_low_hover_not_landing`＝低ホバー誤検出なし/`land_disarm_clears`）で cond2 ロジックを直接検証（モッククロック）。SILS では GE ON で着地が `Impact/anomaly` でなく `Landing detected` 経由のクリーン接地になることを確認。再確認試験 SILS 29/29（GE off）・host 29/29・実機ビルド OK。検出器はファーム側で常時有効ゆえ**実機（GE 常在）では常に効く**。

**注8（近地面の着地アシスト＝粘り解消の推力ランプダウン — 2026-06-14, A+B 実機フィードバック反映, INV-1）:** 実機で離着陸が成功したが**「最後の地面効果あたりでなかなか着地せず時間がかかる」**との報告。原因＝注7 の通り定速降下(0.3m/s)が地面効果と釣り合い、検出器(注7)は鉛直速度停滞(≈静止)を待つため、機体が地面効果で這うように降りている間は判定されず粘る。
- **A. 近地面で推力上限ランプダウン（制御器 Landing ブランチ, INV-1=鉛直チャネルのみ整形・姿勢不変）:** 高度推定が `kLandingSettleAltM`(0.15m)未満で、推力の**上限**を hover から `kLandingSettleThrustFrac·hover`(0.70)へ `kLandingSettleRampS`(1.0s)かけて絞る（`landing_settle_t_` で経過時間積算、onLanding/reset でクリア）。速度ループは上限内で動くので**通常降下は上限が効く前に穏やかに接地**、まだ浮いていれば**下がる上限が地面効果揚力を下回り確実に沈む**（有界の粘り・推力閾値の当て推量なし・床は hover 割合で自由落下でない）。
- **B. 検出閾値の緩和（検出層）:** `stall_hold_ms` 600→**400**, `landing_hold_ms` 1000→**700**, `near_ground_tof_m` 0.12→**0.15m**。A で機体が速く静止に達するので接地宣言を早める。
- **検証（SILS, GE ON）:** 着地が `Disarm ~16.0s→~15.0s`（約1秒短縮）、以前の「0.064m へ浮き上がる bounce」が消え一直線に沈む。接地鉛直速度 `vz=-0.42m/s`＝穏やか（落下でない）、duty も接地まで保持。再確認試験 SILS 30/30（GE off）・host 29/29・実機ビルド OK。**実機での粘り改善は GE が常在する実機で本領（SILS は真値速度ゆえ元々あまり浮かない）**。
- **`landing_descent_rate_` 等のparam化**は今後（現状は config 定数）。
- **検証:** SILS `alt_auto_takeoff`/`pos_auto_takeoff`（ARM 起動・スプール中 duty=0・0.5m 捕捉）、`alt_recenter_gate`（離陸後 Case A: 上げスティック無視→中央通過で有効）、`alt_inflight_switch`（飛行中 Case B: STABILIZE→ALT_HOLD 切替でジャンプなし）、`api_flight`（0.5m 統一）。**実機未検証。**
- **実装中の落とし穴2件（実測図つき解説）:** スロットルの中央は raw 3072（norm 0.5）で 2048 でない／TakeoffClimb の速度クランプは対称（±0.3m/s）でないと地上ブラインド窓の行き過ぎを捕捉できない。詳細・実 SILS トラジェクトリ図は [`alt_hold_takeoff_findings.md`](alt_hold_takeoff_findings.md) を参照。

### ペアリング状態遷移（PairingState — FlightState と並行）

FlightState とは別の独立状態機械。StateManager が所有する（[`architecture.md`](architecture.md) §4
「ペアリング状態の位置づけ」参照）。上の FlightState onExit/onEnter テーブルとは別物。

| 遷移 | トリガー | アクション |
|------|---------|-----------|
| 起動 → Paired / NotPaired | NVS load | 相手側の MAC あり→Paired・相手側をユニキャスト peer 登録 ／ なし→NotPaired |
| NotPaired → Pairing | 自動（未ペア起動） | comm: PairingPacket を 500ms 周期で broadcast 開始、notify: LED青速点滅+ブザー |
| 任意 → Pairing（再ペア） | `button_event`=LongPress3s（IDLE_GROUND / IDLE_HELD） | 既存ペア破棄（NVS clear）→ 上記 Pairing 開始 |
| Pairing → Paired | comm 発行の `pairing_complete`（src MAC 学習） | comm: NVS保存・broadcast peer 削除・相手側をユニキャスト peer 登録、notify: 点滅解除 |

**ガード:** Pairing 中は `requestArm` を拒否（StateManager）。突入は IDLE_GROUND / IDLE_HELD
（地上または手持ち）で可能（2026-09-12、手持ちでのボタン操作を許容するため拡張。ARM 自体は
従来どおり IDLE_GROUND 限定なので安全性に影響しない）。

**トピック:**
- `pairing_state`（Latest, StateManager → comm / notify）: 現在の PairingState を周知。
- `pairing_complete`（Latest, comm → StateManager）: comm の現在のバインド状態（bound + 学習/復元した
  送信機 MAC）。起動時の NVS 復元（restored=true）と Pairing 成立（restored=false）の両方を運ぶ。

## 4. 制御インターフェース定義

PID/MPC/LQRを差し替え可能にするための統一インターフェース。

```cpp
// Control interface definition
// 制御インターフェース定義

class IController {
public:
    virtual ~IController() = default;

    // Called every control cycle (400Hz)
    // 制御周期ごとに呼ばれる（400Hz）
    virtual ControlOutput compute(
        const StateEstimate& state,      // Current estimated state
        const CommandSetpoint& setpoint, // Target setpoint
        float dt                         // Time step [s]
    ) = 0;

    // Reset internal state (integrators, filters, etc.)
    // 内部状態リセット（積分器、フィルタ等）
    virtual void reset() = 0;

    // Mode change notification
    // モード変更通知
    virtual void onModeChange(uint8_t new_mode) = 0;
};
```

### PID 実装の離散化方式

`sf_controller_pid` の PID 実装は **双線形変換（Tustin 法）** を採用する。後退差分による離散化は α = Td/(η·Td+dt) > 1 となり微分フィルタが発散する（旧 SILS のプロトタイピング時に判明、コミット `06b4cd6`／git 履歴）。これを避けるため bilinear に統一した（コミット `07005fb`、vehicle/ 旧実装と一致）。

| 項 | 離散化 |
|----|--------|
| 積分（trapezoidal）| `integral += (kp/ti) · (error + prev_error) · dt/2` |
| 微分（bilinear）| α = 2·η·Td/dt、a = (α−1)/(α+1)、b = 2·Td/(dt·(α+1))<br>`d[n] = a·d[n−1] + b·(e[n] − e[n−1])`<br>Kp は filter 外で適用: `d_term = kp · d[n]` |

η = 0.125、Td = 0.01、dt = 0.0025 で α = 1.0、a = 0、b = 4.0 の安定動作。定量的な検証は、新しい物理真値の判定 **G1〜G4**（`../../../simulator/sils/RESET_PLAN.md` §4）で再取得する（旧 SILS での L1〜L4 数値は削除済み・git 履歴に保存）。

### ヘディングホールド（STABILIZE 以上）

レートループはヨー「角速度」しか帰還しないため、定在ヨー外乱トルク下では方位が操縦修正の合間にランダムウォークする（2026-06-11 実機で確認: 推力比例 CW モーメント下、手放し方位ずれ平均 12.3°）。これを推定ヨー角（バイアス補正済みジャイロ積分 — 地磁気不要）への外側 P ループで抑える。

| 項目 | 内容 |
|------|------|
| 係合 | ヨースティック中立（\|yaw\| < 0.03）かつ空中（STABILIZE: throttle > 0.25 / ALT_HOLD 以上: VerticalPhase == Airborne）|
| 目標捕捉 | 係合エッジで現在の推定ヨー角を捕捉。スティック入力で即解除（パイロット優先）、次の中立で再捕捉 |
| 制御則 | `rate_ref_yaw = clamp(kp · wrap(ψ_target − ψ), ±rate_max)` — 誘導ヨー則と同形 |
| 除外 | ACRO（純レート制御）、誘導アクティブ中（誘導がヨー所有）、自動離陸中、墜落リセット・モード切替で解除 |
| パラメータ | `attitude.yawhold.kp`（既定 3.0 [1/s]、0 で無効）、`attitude.yawhold.rate_max`（既定 2.0 [rad/s]）|
| 検証 | フライトログ再生で方位ずれ 12.3°→5.7°（平均）。SILS 判定 `yaw_hold.scn`: M1 80% 故障下 8 秒手放しで yaw_band < 1.5°（無効化対照 3.1° 単調流出）|

## 5. 状態推定インターフェース定義

ESKF/EKF/Complementary Filterを差し替え可能にするための統一インターフェース。

```cpp
// Estimation interface definition
// 状態推定インターフェース定義

class IEstimator {
public:
    virtual ~IEstimator() = default;

    // IMU prediction step (400Hz)
    // IMU予測ステップ（400Hz）
    virtual void predict(const ImuData& imu, float dt) = 0;

    // Sensor observation updates (async, called when data arrives)
    // センサ観測更新（非同期、データ到着時に呼ばれる）
    virtual void updateTof(const TofData& tof) = 0;
    virtual void updateFlow(const FlowData& flow) = 0;
    virtual void updateMag(const MagData& mag) = 0;
    virtual void updateBaro(const BaroData& baro) = 0;

    // Get current state estimate
    // 現在の推定値取得
    virtual StateEstimate getState() const = 0;

    // Reset full state
    // 全状態リセット
    virtual void reset() = 0;

    // Reset position and velocity only
    // 位置・速度のみリセット
    virtual void resetPositionVelocity() = 0;
};
```

### センサ観測スイッチ

センサ観測のON/OFFはパラメータシステムと連携し、推定コンポーネント内部で判断する。

```
sensor.tof → 推定コンポーネントに常に届く
           → 推定内部で eskf.use_tof パラメータを確認
           → false なら観測更新をスキップ
           → ログには常に記録される
```

パラメータ変更時のコールバックで推定コンポーネントの内部マスクを即時更新する。これにより段階的にセンサを有効化しながらデバッグが可能。

### ESKF 実装の特性

`sf_estimator_eskf` には次の機能が実装されている（χ² 判定（カイ二乗判定）は実機 vehicle/ の知見、Adaptive R・線形化バイアスは旧 SILS のプロトタイピング由来＝git 履歴。新 SILS での定量検証は G1〜G4 で行う）。

#### χ² 判定（外れ値棄却）

各観測更新前にイノベーション e と共分散 S = H·P·Hᵀ + R から χ² = eᵀ·S⁻¹·e を計算し、自由度に応じた閾値を超える観測を棄却する。実機 vehicle/ で「姿勢の揺れが PID ゲイン変更なしでほぼ解消」した実績あり（加速度計の外れ値が `updateAccelAttitude` で姿勢を乱していたのを抑制）。コミット `d9172c4`。

#### Adaptive R（加速度姿勢補正の thrust 汚染対策）

加速度計が観測する加速度は厳密には `[0, 0, −T/m]`（body frame）であり、ホバー以外では `R^T·g` から乖離する。15° ロールでは |a| ≈ g/cos(15°) で、ノルム判定は通過するが方向は誤りで、姿勢推定が破綻する。

対策として観測ノイズを **R_actual = R_base² × (1 + k_adaptive · |a − g|²)** で動的に膨らませ、g から外れた観測の重みを下げる。`k_adaptive = 50` を default とする。旧 SILS のプロトタイピング（0.3Hz サイン応答、git 履歴）での参考値:

| 指標 | Before | After | 改善 |
|------|--------|-------|------|
| ゲイン | 2.34× | 2.00× | — |
| トラッキング誤差 | 16.5° | 9.1° | 45% |
| ホバー姿勢 RMS | 3.94° | 3.87° | 維持 |

コミット `98839a6`。**根本解（thrust 補償観測モデル）は Phase 5 へ繰越**。

#### 線形化バイアス（既知の構造的限界）

加速度ベース姿勢補正の観測モデル `h(x) = R^T·g` はホバー近傍でのみ妥当な線形化。10° ステップ保持を 200s 実行すると、ESKF 推定値と真値の間に **−2.3° の定常バイアス** が残り、収束しない。これは推力寄与（`a_body = [0, 0, −T/m]`）が観測モデルから抜けているための構造的限界で、ESKF + PID ループのフィードバック構造により ESKF は指令値を追跡し、真値はバイアス分ずれる。

復帰時間は 1〜3s。ホバー定点用には十分だが、姿勢追跡用途には限界あり。コミット `8a2e6ca`。教材として「線形化が破綻する条件」の定量データに位置付ける。

## 6. パラメータシステム

### 設計方針

全パラメータの SSOT は `params.cpp`。型付き変数（`namespace param_vars`）と、名前→変数→
既定/最小/最大/コールバックを結ぶ明示的 `table[]` で、定義・バリデーション・コールバック・
NVS 永続化を完結させる。明示テーブルは意図的（模範コードとして読みやすく、値・範囲・根拠が
見える）。

> 注（Phase 5b, 2026-06-07）: 当初は `params.def` の X-macro コード生成を想定していたが、
> 実体は `params.cpp` の手書きテーブルに収束していた。`params.def` は非機能で値もずれて
> いたため撤去し、`params.cpp` を正式 SSOT とした（[[reference_params_ssot]]）。

### パラメータ定義（`params.cpp`）

```cpp
// 1) namespace param_vars に型付き変数を追加（既定値もここ）
namespace param_vars {
    float rate_roll_kp = 1.83e-4f;   // Ixx/τ_resp（物理ゲイン）
    // ...
}

// 2) table[] に行を追加（名前 → 変数 → 既定 / min / max / コールバック）
static const ParamEntry table[] = {
    //   名前              型               変数ポインタ      既定       min    max     callback
    {"rate.roll.kp", ParamType::FLOAT, &rate_roll_kp, 1.83e-4f, 0.0f, 0.01f, nullptr},
    // ...
};
// パラメータ追加＝この2箇所（同一ファイル）に書く
```

### アクセスAPI

```cpp
// Read parameter value (by name, into an out-param; returns false if not found)
// パラメータ値の読み取り（名前で out 引数へ。未発見なら false）
float kp = 0.0f;
params::get_float("rate.roll.kp", kp);

// Write parameter value (from WiFi/CLI)
// パラメータ値の書き込み（WiFi/CLIから）
// → バリデーション → 値更新 → コールバック実行
params::set_float("rate.roll.kp", 2.0e-3f);

// List all parameters (for CLI)
// 全パラメータ一覧（CLI用）
params::list();

// Init (load from NVS, else table defaults) / Save all to NVS / Load from NVS
// 初期化（NVS から、無ければ table 既定）/ NVS 一括保存・読み込み
params::init();
params::save();
params::load();

// Reset to defaults
// デフォルト値にリセット
params::resetAll();
```

### 命名規則

3階層: `機能.対象.パラメータ`

```
rate.roll.kp              # Rate control, roll axis, proportional gain
rate.pitch.ti             # Rate control, pitch axis, integral time
attitude.roll.kp          # Attitude control, roll axis, proportional gain
altitude.alt.kp           # Altitude control, altitude loop, Kp
altitude.vel.kp           # Altitude control, velocity loop, Kp
position.pos.kp           # Position control, position loop, Kp
eskf.process.gyro_noise   # ESKF, process noise, gyro
eskf.obs.tof_noise        # ESKF, observation noise, ToF
eskf.gate.tof_innov       # ESKF, innovation gate, ToF
eskf.use_tof              # ESKF, sensor enable, ToF
safety.impact.accel_threshold  # Safety, impact detection, accel threshold
```

## 7. ディレクトリ構造

HALコンポーネントはvehicle内にコピー（完全独立）。

```
firmware/vehicle/
├── CMakeLists.txt                 # ESP-IDF project root
├── partitions.csv
├── sdkconfig.defaults
├── docs/
│   ├── requirements.md            # 要件定義書
│   ├── requirements.tex/pdf       # 要件定義書（PDF版）
│   ├── architecture.md            # アーキテクチャ設計書
│   └── detailed_design.md         # 詳細設計書（本文書）
├── main/
│   ├── CMakeLists.txt
│   ├── main.cpp                   # app_main(), INIT逐次実行
│   └── config.hpp                 # 固定パラメータ（constexpr）
├── components/
│   ├── sf_core/                   # 基盤: Pub-Sub, パラメータ, データ型
│   │   ├── include/
│   │   │   ├── topic.hpp          # Topic<T> テンプレート
│   │   │   ├── topics.hpp         # 全トピック定義
│   │   │   ├── data_types.hpp     # 全構造体定義
│   │   │   └── params.hpp         # パラメータ公開API
│   │   └── params.cpp             # パラメータ SSOT（param_vars + table[]）
│   ├── sf_state/                  # 状態管理
│   │   └── include/
│   │       ├── state_manager.hpp  # 状態遷移、onExit/onEnter
│   │       └── flight_state.hpp   # enum定義
│   ├── sf_estimator/              # 状態推定インターフェース
│   │   └── include/
│   │       └── estimator.hpp      # IEstimator定義
│   ├── sf_estimator_eskf/         # ESKF実装
│   ├── sf_controller/             # 制御インターフェース
│   │   └── include/
│   │       └── controller.hpp     # IController定義
│   ├── sf_controller_pid/         # PID実装
│   ├── sf_actuator/               # ミキサー＋モーター出力
│   ├── sf_command/                # コマンド処理
│   ├── sf_failsafe/               # フェイルセーフ
│   ├── sf_takeoff_landing/        # 離着陸マネージャー
│   ├── sf_logger/                 # データロガー＋Blackbox
│   ├── sf_telemetry/              # テレメトリ
│   ├── sf_notify/                 # LED/ブザー通知
│   ├── sf_calibration/            # キャリブレーション管理
│   ├── sf_comm/                   # 通信（ESP-NOW/UDP/TCP）
│   ├── sf_hal_bmi270/             # IMUドライバ（コピー）
│   ├── sf_hal_bmm150/             # 地磁気ドライバ（コピー）
│   ├── sf_hal_bmp280/             # 気圧ドライバ（コピー）
│   ├── sf_hal_vl53l3cx/           # ToFドライバ（コピー）
│   ├── sf_hal_pmw3901/            # OptFlowドライバ（コピー）
│   ├── sf_hal_motor/              # モータードライバ（コピー）
│   ├── sf_hal_led/                # LEDドライバ（コピー）
│   ├── sf_hal_buzzer/             # ブザードライバ（コピー）
│   ├── sf_hal_button/             # ボタンドライバ（コピー）
│   └── sf_hal_power/              # 電源モニタドライバ（コピー）
├── tasks/
│   ├── tasks.hpp                  # タスク関数宣言
│   ├── imu_task.cpp               # IMU + 状態推定
│   ├── control_task.cpp           # 制御 + アクチュエーション
│   ├── state_task.cpp             # 状態管理（イベント駆動）
│   ├── flow_task.cpp              # OptFlow
│   ├── mag_task.cpp               # 地磁気
│   ├── baro_task.cpp              # 気圧
│   ├── comm_task.cpp              # 通信 + コマンド処理
│   ├── tof_task.cpp               # ToF + 離着陸マネージャー
│   ├── telemetry_task.cpp         # テレメトリ
│   ├── power_task.cpp             # 電源 + フェイルセーフ
│   ├── button_task.cpp            # ボタン入力
│   ├── notify_task.cpp            # LED/ブザー
│   ├── cli_task.cpp               # CLI + パラメータ
│   └── log_task.cpp               # データロガー + Blackbox
└── examples/                      # サンプル集（チュートリアル的コメント）
    ├── blink_led/
    ├── read_imu/
    ├── pid_motor/
    ├── eskf_sim/
    ├── tof_altitude/
    └── espnow_pair/
```

> **注（`examples/espnow_pair/` と本体ペアリング機能の区別）:** `examples/espnow_pair/` は ESP-NOW
> ペアリングの**最小教育例**（単独ビルド可能・PairingPacket の送受信を学ぶ短いコード）。本体の
> ペアリング機能（混信対策・状態統合・NVS 永続化）は `sf_comm` ＋ `sf_state` が担い、本書 §3
> 「ペアリング状態遷移」が正典。両者は別物として扱う。

## 8. メモリ配置

### パーティションテーブル

```
# Name,    Type, SubType, Offset,   Size,     Flags
nvs,       data, nvs,     0x9000,   0x6000,           # 24KB — パラメータ永続化
phy_init,  data, phy,     0xF000,   0x1000,           # 4KB  — WiFi/BT校正
factory,   app,  factory, 0x10000,  0x300000,          # 3MB  — ファームウェア
storage,   data, spiffs,  0x310000, 0x200000,          # 2MB  — Blackbox
# 残り 2MB (0x510000〜0x800000) は未割当（将来用）
```

### Blackbox容量

| 記録レート | 容量 | 記録時間 |
|-----------|------|---------|
| 400Hz / 128B | 50KB/s | 約40秒 |
| 50Hz / 128B | 6.25KB/s | 約5分 |

### Blackboxアクセス

- CLI経由: `sf log download` でUSBシリアル経由転送
- 将来: USBマスストレージも検討可能だが初期実装はCLIで統一

### RAMメモリ（ESP32-S3 512KB）

タスクスタックの合計: 約102KB

| タスク | スタック |
|--------|---------|
| ImuTask | 16KB |
| ControlTask | 8KB |
| StateTask | 4KB |
| FlowTask | 8KB |
| MagTask | 8KB |
| BaroTask | 8KB |
| CommTask | 4KB |
| TofTask | 8KB |
| TelemetryTask | 4KB |
| PowerTask | 4KB |
| ButtonTask | 4KB |
| NotifyTask | 4KB |
| ApiTask | 6KB |
| TelloStateTask | 4KB |
| CLITask | 8KB |
| LogTask | 4KB |
| **合計** | **102KB** |

残り約410KBでPub-Subバッファ、ESKF行列、ヒープ等を賄う。

## 9. Guidance / Navigation 層の予約設計（v3）

### 設計の動機

vehicle は「学習者がレイヤーを段階的に登れる」ことを目標にしている（[`architecture.md`](architecture.md) §2.5「学習者の入口」）。学習段階の最終形は **ガイダンス（目的地・経路指定）→ ナビゲーション（経路計画）** 層であり、要件 (`requirements.md` §4) でも「ナビゲーター（将来追加）」として位置づけられている。

実装は後続フェーズ（M4 以降、Phase 6）で行うが、**Topic 上の置き場所と入出力契約を v3 で確定**する（R11）。これにより：
- Guidance / Navigation を学びたい学習者が「自分のコードはどこに書くのか」を最初から把握できる
- 既存 Controller / Estimator のインターフェースを変更せずに追加できる構造を保証

### 階層構造

```
┌─────────────────────────────────────────────┐
│  L4 Application: free-style learner code     │
│   "ウェイポイントを設定して飛ばす"            │
├─────────────────────────────────────────────┤
│  Navigator    (`sf_navigator`、将来実装)       │
│   経路計画 → nav_path Topic                    │
├─────────────────────────────────────────────┤
│  Guidance     (`sf_guidance`、将来実装)        │
│   目的地 + nav_path → command_target Topic     │
├─────────────────────────────────────────────┤
│  Controller   (既存 sf_controller_pid)         │
│   command_target / command_setpoint           │
│           → control_output                    │
├─────────────────────────────────────────────┤
│  Estimator    (既存 sf_estimator_eskf)        │
└─────────────────────────────────────────────┘
```

### Topic 入出力契約

| Topic | データ型（予定） | Publisher | Subscriber | Rate |
|-------|-------------|-----------|-----------|------|
| `nav_path` | `NavigationPath` (waypoint sequence) | Navigator | Guidance | 1Hz |
| `command_target` | `GuidanceTarget` (position [m] + yaw [rad]) | Guidance / Navigator | ControlTask, NotifyTask | 10Hz |

`NavigationPath` データ型案:
```cpp
struct Waypoint {
    float x_m, y_m, z_m;
    float yaw_rad;
    float velocity_mps;        // 通過時の希望速度
    float acceptance_radius_m; // 到着判定半径
};

struct NavigationPath {
    uint32_t timestamp;
    uint8_t  num_waypoints;
    Waypoint waypoints[8];     // MVP は最大 8 点
    uint8_t  current_index;    // 現在追跡中のインデックス
};
```

`GuidanceTarget` データ型案:
```cpp
struct GuidanceTarget {
    uint32_t timestamp;
    float    position_target_m[3];  // x, y, z (NED)
    float    yaw_target_rad;
    float    velocity_target_mps[3]; // 速度 feed-forward (optional)
    bool     is_active;             // false なら直接 setpoint を使う
};
```

### Controller 側の優先順位

ControlTask は `command_target`（Guidance 出力）と `command_setpoint`（パイロット直接指令）の両方を subscribe するが、**system.mode により優先順位を決める**：

| system.mode | 入力 | 用途 |
|------------|-----|------|
| `MANUAL` (ACRO / STABILIZE / ALT / POS) | `command_setpoint` | パイロット直接操縦 |
| `AUTO_MISSION` (将来) | `command_target` | ウェイポイント追従 |
| `AUTO_RTH` (将来) | `command_target`（home 座標で固定） | Return To Home |

これにより既存 `command_setpoint` のセマンティクスを壊さずに自律モードを追加できる。

### 学習者から見える形

L1 Topic API ユーザーが「自分の Navigator」「自分の Guidance」を書くときのコード例：

```cpp
// MyGuidance.cpp — 学習者のコード
void MyGuidanceTask(void* pvParameters) {
    while (true) {
        // 経路を読む
        sf::NavigationPath path;
        if (sf::nav_path.read(path)) {
            // 現在の位置を読む
            auto state = sf::estimate_state.latest();

            // 自分のロジックで次の target を計算
            sf::GuidanceTarget target = my_pure_pursuit(state, path);
            sf::command_target.publish(target);   // ← Topic に流すだけ
        }
        vTaskDelay(pdMS_TO_TICKS(100));  // 10Hz
    }
}
```

Controller / Estimator / HW のことは何も触らずに、自分のロジックだけ書けば済む。これが 4 階層アクセスの「L1 だけで完結」の意味。

### 実装フェーズ

| Phase | 作業 |
|-------|------|
| M2 | 必要な Topic データ型 (`GuidanceTarget`, `NavigationPath`) を data_types.hpp に予約定義（実体生成は M4 以降） |
| M4 | `sf_guidance` コンポーネント新設、Pure Pursuit / Carrot Following など基本アルゴリズムの実装 |
| Phase 6 | `sf_navigator` コンポーネント新設、経路計画（A* / RRT などは選択肢として残す） |

実装が進んだ時点で本節を更新し、`@design` ステータスを `[OK]` に更新する。

## 10. UDP テレメトリ電文様式（UDP:5005）

### この節の位置づけ

`sf_telemetry` が 50Hz でブロードキャストするモニタ用テレメトリの電文様式を定義する。**本節を電文様式の基準とする。** 実装（`components/sf_telemetry/include/telemetry.hpp`）と PC 側デコーダ（`lib/sfcli/commands/telemetry.py`）は本節に従う。

2026-09-19 制定。それ以前は様式を定義した文書が無く、`telemetry.hpp` の `@design` タグが実在しない節（旧「detailed_design.md §8 — UDP telemetry packet format」、実際の §8 はメモリ配置）を参照していた。その誤りを正すために本節を新設した。

400Hz の解析用 Data Stream（`sf log wifi`）は別の電文で、`components/sf_telemetry/include/data_stream_wire.hpp` が定義する。混同しないこと。

### 基本

| 項目 | 値 |
|------|-----|
| 送信先 | 255.255.255.255:5005（UDP ブロードキャスト） |
| 周期 | 50Hz（20ms） |
| バイト順 | リトルエンディアン（ESP32 ネイティブ） |
| 詰め方 | `#pragma pack(1)`（詰め物なし） |
| magic | `0xCAFE` |
| packet_type | `0x01`（`TELEM_TYPE_PHASE2A_BASIC`） |

### 版の判別規則

**受信側は「全長」で版を判別する。**

| 全長 | 版 | 内容 |
|------|-----|------|
| 104 バイト | v1 | 姿勢・角速度・加速度・位置・速度・推力/トルク・モータ duty・飛行状態 |
| 140 バイト | v2 | v1 の全項目＋末尾にセンサ部（電池電圧・ToF・フロー・地磁気・気圧高度） |

v2 は v1 への**末尾追記**であり、先頭 104 バイトは両版でビット単位に同一である。よって旧受信機は 140 バイトのうち先頭 104 バイトをそのまま復号できる。`version` フィールド（v1 は 1、v2 は 2）は**検証にのみ**使い、版の選択には使わない。140 バイトなのに `version` が 2 でない電文は異常として破棄する。

`packet_type` は v2 でも `0x01` に据え置く。中身の種別（基本テレメトリ）が変わっていないためで、`packet_type` も変えると受信側が `(種別, 版)` の2次元分岐になり、末尾追記による前方互換の利点を失う。Data Stream のステータス電文が 17/53/57 バイトの版を全長だけで判別しているのと同じ規則である。

パケット長の増加は送信コストに影響しない。ESP32 の lwIP `sendto()` はペイロードによらず1「回」あたり約 2.5ms を要し（117 バイトと 840 バイトで同じ実測）、共存する ESP-NOW の品質を決めるのは呼び出し回数だからである（`docs/architecture/udp-telemetry-design.md` §3）。

### v1 部（オフセット 0〜103、凍結）

| オフセット | 名前 | 型 | 単位・意味 |
|---|---|---|---|
| 0 | magic | u16 | `0xCAFE` |
| 2 | version | u8 | 1 または 2 |
| 3 | packet_type | u8 | `0x01` |
| 4 | timestamp_us | u32 | 機体起動からの経過時間 [us] |
| 8 | roll, pitch, yaw | f32×3 | 姿勢 [rad] |
| 20 | gyro_x, y, z | f32×3 | 機体角速度 [rad/s] |
| 32 | accel_x, y, z | f32×3 | 機体加速度 [m/s²] |
| 44 | pos_x, y, z | f32×3 | 位置 [m]（NED） |
| 56 | vel_x, y, z | f32×3 | 速度 [m/s]（NED） |
| 68 | thrust | f32 | 推力 [N] |
| 72 | roll/pitch/yaw_torque | f32×3 | 機体トルク [Nm] |
| 84 | motor_duty[4] | f32×4 | モータ duty M1〜M4 [0,1] |
| 100 | system_mode | u8 | FlightState の値 |
| 101 | reserved[3] | u8×3 | 0（4バイト境界合わせ） |

**この領域は凍結する。** 項目の並べ替え・型変更・削除を行わない。追加は必ず末尾に行う。

### v2 センサ部（オフセット 104〜139）

| オフセット | 名前 | 型 | 単位・意味 |
|---|---|---|---|
| 104 | voltage | f32 | 電池電圧 [V]（0 = 不明） |
| 108 | tof_bottom | f32 | 下向き ToF 距離 [m] |
| 112 | tof_front | f32 | 前方 ToF 距離 [m]。測定値が無いときは `kTelemTofFrontUnavailable`（-1.0）。有効性は bit1 が正 |
| 116 | flow_dx_total16 | u16 | フロー累計 dx [counts] の下位 16 ビット（折り返す）。**差分ではない** |
| 118 | flow_dy_total16 | u16 | フロー累計 dy [counts] の下位 16 ビット（同上） |
| 120 | flow_squal | u8 | 最新の表面品質 |
| 121 | valid_flags | u8 | 有効ビット（下表） |
| 122 | reserved2[2] | u8×2 | 0（地磁気を4バイト境界に置くため） |
| 124 | mag_x, y, z | f32×3 | 地磁気 [uT]（補正済み） |
| 136 | baro_altitude | f32 | 気圧高度 [m] |

各オフセットは `telemetry.hpp` の `static_assert(offsetof(...))` で固定し、`lib/sfcli/commands/test_telemetry.py` が同じ表を持って Python の `struct` 書式と突き合わせる。片側だけ並べ替えると試験が落ちる。

### 有効ビット（valid_flags）

| ビット | 定数 | 対象 |
|---|---|---|
| bit0 | `TELEM_VALID_TOF_BOTTOM` | 下向き ToF |
| bit1 | `TELEM_VALID_TOF_FRONT` | 前方 ToF（Optional。非搭載・無効化・USB 給電のみでの起動失敗時は 0） |
| bit2 | `TELEM_VALID_FLOW` | オプティカルフロー |
| bit3 | `TELEM_VALID_MAG` | 地磁気 |
| bit4 | `TELEM_VALID_BARO` | 気圧高度 |
| bit5 | `TELEM_VALID_POWER` | 電池電圧 |

**有効性の基準はビットであり、値ではない。** 受信側はビットだけを見ること。`tof_front` の -1.0 のような値との比較で有効性を判定してはならない。実センサは異常時に負値を返しうるためである。

ビットは 2 つの条件の積で立つ。(1) センサ自身が publish した有効性（下向き ToF は `tof_valid`、電池電圧は 0 より大きいこと）と、(2) 鮮度である。鮮度は各センサのタイムスタンプの経過時間が `kTelemSensorStaleUs`（500ms）未満であることで判定する（R16）。`sensor_snapshot` と `sensor_power` はどちらも Latest トピックで最終値を周期を跨いで保持するため、経過時間を見ないと停止したセンサが動作中だと誤って報告され続ける。500ms は対象で最も遅い気圧計（50Hz）の 25 周期にあたり、正常なセンサが誤って無効になることはない。

### フローの 2 項目は「累計」である（2026-09-19 に差分から改めた）

`flow_dx_total16` / `flow_dy_total16` は、**起動からの累計の下位 16 ビット**である。移動量ではない。受信側が「今回の累計 − 前回受信の累計」を **16 ビットの折り返しつきで** 引き算して、その間の移動量を得る。

#### なぜ機体が差分を送らないのか

フローセンサの `dx`/`dy` は「前回読み出しからの変位」という差分量で、絶対量ではない。センサは約 100Hz、テレメトリは 50Hz なので、差分量をそのまま 50Hz で覗くと各周期の最後の 1 件しか見えず残りが失われる。そのため `SensorSnapshot` に**起動からの累計** `flow_dx_total` / `flow_dy_total`（`uint32`）を持たせ、`ImuTask` が 400Hz のループで**全サンプル**をこれに加算している（`ImuTask` は `sensor_flow` キューの唯一の consumer であり全サンプルを通す）。書き手は `ImuTask` の 1 か所だけである（R5、`docs/architecture/udp-telemetry-design.md` §2「1 データソース = 1 変数」）。

v2 は当初、この累計の差（＝前回**送信**からの変位）を `int16` の枠に入れて送っていた。**2026-09-19 の実機実測でこれが成り立たないことが分かった。** UDP:5005 は 255.255.255.255 へのブロードキャストで、WiFi はブロードキャストを再送しない。24.6 秒に約 1,230 個送って Mac が受け取ったのは 714 個（58%）で、連続 4〜10 個の欠損が何度も起きた（同じ時刻にユニキャストの Data Stream、`sf log wifi` は欠損 0%）。**空中で失われたパケットが運んでいた差分は、受信側では永久に失われる。** 「送信が飛んだ分を次のパケットに持ち越す」という当初の設計は、機体が送らなかった場合しか救わない。

累計ならこの欠損を越えて生き残る。欠損の両側で受け取った 2 つの累計の差は、間で何個パケットが消えていても、その間の移動量の全量のままだからである。

#### 折り返しと、唯一の限界

累計は `uint32` だが電文の枠は 16 ビットなので、`static_cast<uint16_t>(snap.flow_dx_total)` で下位 16 ビットに切り詰めて送る。ここでの切り捨ては望むところである。差分と違い累計は常に 2^16 を法として読まれるので、捨てた上位ビットに受信側が必要とする情報は無い。

受信側は `delta = int16(今回 − 前回)`、すなわち差を 2^16 で法として取り符号つきとして読む。**限界はただ一つ、「受信が途絶えている間の移動量が ±32767 カウントを超えた場合」である。** それを超えると、折り返しは逆向きの短い移動と区別がつかなくなる。実測では手で機体を振っても毎秒約 900 カウントだったので、これはおよそ **36 秒の連続欠損**にあたる。36 秒の途絶はどのみち復元できない。この値は `telemetry.hpp` の `kTelemFlowWrapSpan` に定数として置いてある。

#### 受信側（PC）の作法

差の取り方は `lib/sfcli/commands/telemetry.py` の `flow_delta(prev_total16, cur_total16)` **1 か所**にまとめてある。端末表示・CSV・ブラウザ表示・`lib/sfpilot/link.py` はいずれもこれを通す。状態（前回の累計と前回の `t_us`）を持つのは同ファイルの `FlowTracker` である。

- `decode_packet()` は**生の累計**を返す。移動量に変えるのは呼び出し側の仕事で、復号器は 1 パケットの純粋な復号にとどめる
- **機体の再起動を検出する。** タイムスタンプ `timestamp_us` は「その機体の起動」からのマイクロ秒なので、再起動すれば時刻もフロー累計も 0 から始まる。`FlowTracker` は `t_us` が**巻き戻ったら**基準を捨て、その 1 件だけ移動量を「不明」とする。これが無いと再起動後の最初のパケットが、古い累計との差を 1 回の巨大な移動として報告してしまう
- CSV には**生の累計と、前回行からの移動量の両方**を出す（`flow_dx_total` / `flow_dx_delta` 等）。移動量はそのまま図に描ける量で、累計は記録側が飛ばした行をまたいだ移動量を解析側が取り直すためにある
- 104B（v1）のパケットにはフロー項目が無いので、CSV は従来どおり空欄にする。**0 を書かない**（0 は「面が動かなかった」と読まれる）

#### バージョン番号を 2 のまま据え置いた理由

意味を変えたのに `TELEM_VERSION` を 3 へ上げなかったのは、**v2 自体が 2026-09-19 に `feat/jev-autopilot` ブランチで生まれ、同じ日・同じブランチで直したものであり、旧い意味（差分）を送った公開ファームが存在しない**ためである。3 へ上げれば、どこにも無いパケットのための復号経路を全受信側が抱えることになる。着手前に、リポジトリ内に v2 の差分を前提とした利用者が他に無いことを確認した（`flow_dx_sum` の読み手は `sf telemetry`・`telemetry_web`・その試験だけで、いずれも本変更に追随させた）。

### 前方 ToF の供給経路（R11 の契約を実装）

前方 ToF は 2026-09-19 に駆動を開始した（jev-autopilot P2b）。同日、実機で確認済みである（`docs/plans/jev-autopilot.md` §4.8）。かつて R11 に従って先に決めた 4 段階の契約を、そのまま実装したものである。

| 段階 | 内容 | 実装 |
|------|------|------|
| 1 | `TofTask` が XSHUT を時差解除する 2 センサ起動手順を実装する | `tasks/tof_task.cpp::startTofSensors()` |
| 2 | 専用トピック `sensor_tof_front` を新設する。**底面と変数・トピックを共有しない。** かつて底面と前方が同じタイムスタンプ変数を更新し、電池駆動時に前方が初期化に成功して 2 倍のレートに見えた不具合がある（`docs/architecture/udp-telemetry-design.md` §2「1 データソース = 1 変数」） | `sf_core/include/topics.hpp` |
| 3 | `SensorSnapshot` に `tof_front_distance` / `tof_front_valid` / `tof_front_timestamp` を追加し、`ImuTask` がミラーする | `sf_core/include/data_types.hpp`、`tasks/imu_task.cpp`（`tof_front_status` も併せて追加） |
| 4 | テレメトリは `sensor_snapshot` からそれを読み、bit1 と `tof_front` を埋める | `sf_telemetry/telemetry.cpp` |

#### 起動順序（この順序でなければならない）

VL53L3CX は 2 個とも I2C 0x29 で起動する。前方を生かしたまま底面のアドレスを変更すると、変更が**両方に届いて**混線する。したがって順序は 1 通りしかない。

| 段 | 動作 | なぜその順か |
|----|------|------------|
| 1 | 前方 XSHUT = LOW（リセット保持） | 0x29 に応答する部品を 1 個だけにする |
| 2 | 底面を初期化し 0x30 へ、`startRanging()` まで完了 | **従来と全く同じ手順**。底面は高度推定の唯一の鉛直観測であり、その挙動を変えない |
| 3 | 以降は周期ループの余り時間で 1 周期 1 段ずつ（`FrontBringUp`）: `Wake` で前方 XSHUT=HIGH → 次周期の `Probe` で `isPresentAt()`（model id 1 本読み）→ 本物なら `init()`→0x31→`startRanging()` | 底面が自分のアドレスへ移った後なので、0x29 に応答するのは前方だけである。**設定時に一括で行わない**: 前方が非搭載だとドライバが起動完了を最大 500ms ポーリングしてから失敗し（`VL53LX_BOOT_COMPLETION_POLLING_TIMEOUT_MS`）、底面が約 15 周期読まれなくなる。在否確認を先に置いて「居ない」を数 ms で決め、かつ起動待ちを「どのみち眠る周期のスリープ」に吸収させることで、**`last_wake` に触れず底面の 30Hz の位相を一切動かさない**（安全要件 2）|

段 3 が失敗した場合は前方 XSHUT を LOW に戻し、`sensor_present(FrontToF) = false` として続行する（R4 の Optional）。**底面の失敗時の扱いは従来から変えていない。**

#### 底面を乱さないための設計

底面 ToF は高度推定の唯一の鉛直観測（気圧は既定で非融合）であり、Critical である（`hardware_init.md` §5）。前方は Optional にすぎない。したがって次を設計で保証する。

| 要件 | 手段 |
|------|------|
| 前方の失敗が底面に及ばない | Topic・`SensorSnapshot` のフィールド・タイムスタンプ・`SensorId` をすべて別にする。前方の失敗経路は XSHUT を LOW に戻して終わり、底面の状態に一切触れない |
| 底面の 30Hz が前方で遅れない | 1 周期内で**底面を先に、無条件で**読む。前方はその後、周期に余裕があるときだけ読む（`cycle_still_on_schedule`）。余裕が無ければ前方のサンプルを 1 つ捨てる |
| 前方が推定器に入らない | `ImuTask` は `sensor_tof_front` を**ミラーするだけ**で `g_estimator` にも `g_takeoff_landing` にも渡さない。前方は機体の状態ではなく障害物の観測であり、状態ベクトルがモデル化していない |
| 実機で問題が出たら即座に切れる | パラメータ `tof.front.enable`（既定 1）。`TofTask` が起動手順の前に 1 回読む。`param set tof.front.enable 0; param save` の後**再起動**で反映（アドレス割り当ては実行中に返上できないため、ライブ再読込は持たない） |

#### I2C バス占有の見積もり

底面の周期 33ms に対し、2 センサ分の読み出しがどれだけ占めるかを見積もる。バスは 400kHz、1 バイト ≈ 9 クロック（8 ビット + ACK）＝ 22.5µs。

| 処理 | 転送量 | 所要 |
|------|--------|------|
| `isDataReady`（`GetMeasurementDataReady`） | 約 2 バイトの読み（レジスタアドレス 2 + データ 1、start/stop 込みで概算 6 バイト相当） | 約 0.14ms |
| `getDistance`（`GetMultiRangingData`, `DEVICERESULTSLEVEL_FULL`） | 結果ブロックを 1 トランザクションで一括読み。レジスタ 0x0088〜0x010E の **134 バイト**（system 44 + core 33 + debug 56 を含む範囲）＋アドレス 2 | 約 3.1ms |
| `clearInterruptAndStartMeasurement` | 数バイトの書き込み | 約 0.1ms |
| **1 センサあたり合計** | | **約 3.3ms** |
| **2 センサ合計** | | **約 6.6ms（33ms 周期の約 20%）** |

残り約 27ms が同じバスを使う他タスク（BMP280 50Hz、BMM150 25Hz、INA3221 10Hz — いずれも 1 回あたり数十バイト）と余裕に充てられる。**前方を足してもバス占有は周期の 2 割程度**であり、底面の 30Hz を脅かさない。なお `getDistance` は新サンプルがある周期にしか走らないため、上の値は最悪値である。

それでも `cycle_still_on_schedule` の間引きを置くのは、クロックストレッチや再試行中の部品でバスが想定外に遅くなったときに、**底面の次の読み出しを遅らせるのではなく前方のサンプルを捨てる**ためである。

#### Data Stream（400Hz ログ）は今回対象外

前方 ToF を Data Stream に載せることは**今回行わない**。テレメトリ（50Hz）で目的（監視・`sf pilot` への供給）は足りており、ワイヤ様式の変更は独立に検証すべき別の作業だからである。`lib/sflog` 側には既に `tof_front` ストリーム（ワイヤ id **0x47**）が定義済みで、載せるときはそれを使う。**0x49 は使用禁止**（`data_stream_wire.hpp:68-79`）。

### 関連ファイル

| ファイル | 役割 |
|---|---|
| `components/sf_telemetry/include/telemetry.hpp` | 構造体・定数・オフセット検査 |
| `components/sf_telemetry/telemetry.cpp` | 構築と送信 |
| `tasks/telemetry_task.cpp` | 50Hz 駆動、`@subscriber` 注記 |
| `lib/sfcli/commands/telemetry.py` | PC 側デコーダ・端末表示・CSV |
| `lib/sfcli/commands/telemetry_web.py` | ブラウザ表示（デコーダ共用） |
| `lib/sfcli/commands/test_telemetry.py` | 復号試験・オフセット突き合わせ |

なお本電文は `protocol/spec/messages.yaml` にまだ載っていない（同ファイルの `TelemetryPacket` は 22 バイトの ESP-NOW 版）。この乖離は `docs/plans/repository-cleanup-candidates.md` の課題 C2 として未解決である。

---

<a id="english"></a>

## 1. Overview

This document defines the detailed design of vehicle, based on the architecture design (architecture.md).

## 2. Pub-Sub Framework

### Design Policy

- Lightweight custom design optimized for StampFly constraints
- Intra-MCU task communication only, no serialization
- All topics determined at compile time
- Internal buffer policy selected per data flow characteristics

### Buffer Policies

| Policy | Characteristics | Use Case |
|--------|----------------|----------|
| RingBuffer | Lock-free SPSC, full retention, ISR-safe | IMU→Estimation, All→Logger |
| Queue | FreeRTOS Queue, buffered | Low-rate sensors |
| Latest | Latest-value overwrite, shared memory | Estimate→Control |

### API

```cpp
topic.publish(data);                    // Producer
topic.subscribe(callback);             // Consumer (callback)
auto data = topic.latest();            // Consumer (polling)
```

## 3. State Transition Table

See Japanese section for complete onExit/onEnter callback table. Entries marked (reserved) will be filled during implementation and testing.

## 4. Control Interface Definition

```cpp
class IController {
public:
    virtual ~IController() = default;
    virtual ControlOutput compute(const StateEstimate& state,
                                   const CommandSetpoint& setpoint,
                                   float dt) = 0;
    virtual void reset() = 0;
    virtual void onModeChange(uint8_t new_mode) = 0;
};
```

> **レート内ループの帰還（angular_rate）:** `compute()` は最内ループ（角速度制御）の帰還量を `StateEstimate.angular_rate[3]`（機体 FRD・推定器でバイアス補正済み）から読む。推定器（ESKF 実装では `predict` 内の `gyro_raw − gyro_bias`）がこのフィールドを毎サイクル埋める。アルゴリズム非依存：相補/Madgwick 等に差し替えても、角速度を出す推定器ならこのフィールド経由で同じレート制御が閉じる。
>
> *Rate inner-loop feedback: `compute()` reads the innermost (angular-rate) feedback from `StateEstimate.angular_rate[3]` (body-FRD, bias-corrected by the estimator). Any estimator that produces a body rate fills this field, so the rate loop closes regardless of the estimation algorithm.*

## 5. Estimation Interface Definition

```cpp
class IEstimator {
public:
    virtual ~IEstimator() = default;
    virtual void predict(const ImuData& imu, float dt) = 0;
    virtual void updateTof(const TofData& tof) = 0;
    virtual void updateFlow(const FlowData& flow) = 0;
    virtual void updateMag(const MagData& mag) = 0;
    virtual void updateBaro(const BaroData& baro) = 0;
    virtual StateEstimate getState() const = 0;
    virtual void reset() = 0;
    virtual void resetPositionVelocity() = 0;
};
```

### Sensor Observation Switch

Sensor observation ON/OFF is controlled via parameters and decided internally by the estimation component. Raw data always reaches the estimator and logger; the estimator skips observation updates when the parameter is false.

## 6. Parameter System

Single-macro definition: one line per parameter covers declaration, validation, callback, and NVS persistence.

```cpp
PARAM_FLOAT("rate.roll.kp", 1.365e-3f, 0.0f, 1.0f, on_pid_changed)
```

## 7. Directory Structure

HAL components are copied into vehicle (fully independent). See Japanese section for complete tree.

## 8. Memory Layout

### Partition Table

```
nvs,       data, nvs,     0x9000,   0x6000,    # 24KB
phy_init,  data, phy,     0xF000,   0x1000,    # 4KB
factory,   app,  factory, 0x10000,  0x300000,   # 3MB
storage,   data, spiffs,  0x310000, 0x200000,   # 2MB Blackbox
# Remaining 2MB unallocated (future use)
```

### Task Stack Summary

Total task stacks: ~102KB out of 512KB RAM. Remaining ~410KB for Pub-Sub buffers, ESKF matrices, heap, etc.

## 10. UDP Telemetry Wire Format (UDP:5005)

### Status of this section

This section defines the wire format of the monitoring telemetry that `sf_telemetry` broadcasts at 50Hz. **It is the authority for that format.** The firmware (`components/sf_telemetry/include/telemetry.hpp`) and the PC decoder (`lib/sfcli/commands/telemetry.py`) follow it.

Added 2026-09-19. Before that no document defined the format, and `telemetry.hpp` carried a `@design` tag pointing at a section that did not exist (formerly "detailed_design.md §8 — UDP telemetry packet format"; §8 is actually Memory Layout). This section was created to correct that.

The 400Hz analysis Data Stream (`sf log wifi`) is a different wire format, defined by `components/sf_telemetry/include/data_stream_wire.hpp`. Do not confuse the two.

### Basics

| Item | Value |
|------|-------|
| Destination | 255.255.255.255:5005 (UDP broadcast) |
| Rate | 50Hz (20ms) |
| Byte order | Little-endian (ESP32 native) |
| Packing | `#pragma pack(1)` (no padding) |
| magic | `0xCAFE` |
| packet_type | `0x01` (`TELEM_TYPE_PHASE2A_BASIC`) |

### Version dispatch rule

**Receivers select the layout by TOTAL LENGTH.**

| Total length | Version | Contents |
|---|---|---|
| 104 bytes | v1 | Attitude, angular rate, acceleration, position, velocity, thrust/torque, motor duty, flight state |
| 140 bytes | v2 | All of v1 plus an appended sensor block (battery voltage, ToF, optical flow, magnetometer, pressure altitude) |

v2 is a pure **append** to v1: the first 104 bytes are bit-identical in both, so an older receiver can decode the leading 104 bytes of a 140-byte packet unchanged. The `version` field (1 for v1, 2 for v2) is used **only for validation**, never to select the layout; a 140-byte packet whose `version` is not 2 is rejected as malformed.

`packet_type` stays `0x01` in v2 because the kind of content (basic telemetry) has not changed. Bumping it as well would force receivers into a two-dimensional (type, version) branch and throw away the forward compatibility the append buys. This is the same rule the Data Stream's status packet uses to tell its 17/53/57-byte revisions apart by total length alone.

Growing the packet does not cost anything to send: lwIP's `sendto()` on ESP32 takes about 2.5ms per **call** regardless of payload (117 bytes and 840 bytes measure the same), and what determines coexistence quality with ESP-NOW is the call rate (`docs/architecture/udp-telemetry-design.md` §3).

### v1 region (offsets 0–103, frozen)

| Offset | Name | Type | Unit / meaning |
|---|---|---|---|
| 0 | magic | u16 | `0xCAFE` |
| 2 | version | u8 | 1 or 2 |
| 3 | packet_type | u8 | `0x01` |
| 4 | timestamp_us | u32 | Time since boot [us] |
| 8 | roll, pitch, yaw | f32×3 | Attitude [rad] |
| 20 | gyro_x, y, z | f32×3 | Body angular rate [rad/s] |
| 32 | accel_x, y, z | f32×3 | Body acceleration [m/s²] |
| 44 | pos_x, y, z | f32×3 | Position [m] (NED) |
| 56 | vel_x, y, z | f32×3 | Velocity [m/s] (NED) |
| 68 | thrust | f32 | Thrust [N] |
| 72 | roll/pitch/yaw_torque | f32×3 | Body torque [Nm] |
| 84 | motor_duty[4] | f32×4 | Motor duty M1–M4 [0,1] |
| 100 | system_mode | u8 | FlightState value |
| 101 | reserved[3] | u8×3 | 0 (4-byte alignment) |

**This region is frozen.** Do not reorder, retype, or remove fields. Additions always go at the end.

### v2 sensor block (offsets 104–139)

| Offset | Name | Type | Unit / meaning |
|---|---|---|---|
| 104 | voltage | f32 | Battery voltage [V] (0 = unknown) |
| 108 | tof_bottom | f32 | Downward ToF distance [m] |
| 112 | tof_front | f32 | Forward ToF distance [m]. `kTelemTofFrontUnavailable` (-1.0) when there is no reading; bit1 is the authority on validity |
| 116 | flow_dx_total16 | u16 | Low 16 bits of the running flow total dx [counts], wrapping. **Not a difference** |
| 118 | flow_dy_total16 | u16 | Low 16 bits of the running flow total dy [counts], same |
| 120 | flow_squal | u8 | Latest surface quality |
| 121 | valid_flags | u8 | Validity bits (table below) |
| 122 | reserved2[2] | u8×2 | 0 (keeps the magnetometer 4-byte aligned) |
| 124 | mag_x, y, z | f32×3 | Magnetometer [uT] (calibrated) |
| 136 | baro_altitude | f32 | Pressure altitude [m] |

Each offset is pinned by `static_assert(offsetof(...))` in `telemetry.hpp`, and `lib/sfcli/commands/test_telemetry.py` holds the same table and checks it against Python's `struct` formats. Reordering one side alone fails that test.

### Validity bits (valid_flags)

| Bit | Constant | Sensor |
|---|---|---|
| bit0 | `TELEM_VALID_TOF_BOTTOM` | Downward ToF |
| bit1 | `TELEM_VALID_TOF_FRONT` | Forward ToF (Optional: 0 when not fitted, disabled, or not started on USB-only power) |
| bit2 | `TELEM_VALID_FLOW` | Optical flow |
| bit3 | `TELEM_VALID_MAG` | Magnetometer |
| bit4 | `TELEM_VALID_BARO` | Pressure altitude |
| bit5 | `TELEM_VALID_POWER` | Battery voltage |

**The bit is the authority, not the value.** Receivers must test the bit and must never infer validity by comparing against a value such as `tof_front == -1.0`, because a live sensor can legitimately report a negative reading on error.

A bit is set when two conditions hold: (1) the validity the sensor itself published (`tof_valid` for the downward ToF; greater than zero for the battery voltage), and (2) freshness. Freshness means the age of that sensor's own timestamp is below `kTelemSensorStaleUs` (500ms) (R16). Both `sensor_snapshot` and `sensor_power` are Latest topics that keep their last value across cycles, so without an age check a stopped sensor would be reported as working indefinitely. 500ms is 25 periods of the slowest source (the 50Hz barometer), so a healthy sensor never trips it.

### The two flow fields are RUNNING TOTALS (changed from differences on 2026-09-19)

`flow_dx_total16` / `flow_dy_total16` carry the **low 16 bits of a running total since boot**. They are not a movement. The receiver takes `current − previous` **as a wrapping 16-bit subtraction** to get the movement in between.

#### Why the vehicle does not send a difference

The sensor's `dx`/`dy` is an incremental quantity — displacement since the previous read, not an absolute value. The sensor runs at about 100Hz while telemetry sends at 50Hz, so reading the incremental value directly at 50Hz would show only the last sample of each interval and drop the rest. `SensorSnapshot` therefore carries **running totals since boot**, `flow_dx_total` / `flow_dy_total` (`uint32`), to which `ImuTask` adds **every sample** in its 400Hz loop (`ImuTask` is the sole consumer of the `sensor_flow` queue and sees all of them). `ImuTask` is the only writer (R5; `docs/architecture/udp-telemetry-design.md` §2, "one data source = one variable").

v2 originally sent the difference between two of those totals (the displacement since the previous **send**) in an `int16` slot. **Measurement on hardware on 2026-09-19 showed that this does not hold.** UDP:5005 is a broadcast to 255.255.255.255, and WiFi does not retransmit broadcasts: of about 1,230 packets sent over 24.6 s the Mac received 714 (58%), with repeated runs of 4 to 10 consecutive losses (the unicast Data Stream, `sf log wifi`, lost nothing in the same session). **A difference carried by a packet lost in the air is gone for good on the receiving side.** The original design — "a suppressed send carries over to the next packet" — only covers the case where the vehicle did not send.

A total survives that loss, because the difference between the two totals received either side of a gap is still the whole movement across it, however many packets vanished in between.

#### The wrap, and the one limit

The totals are `uint32` but the wire slot is 16 bits, so the firmware truncates with `static_cast<uint16_t>(snap.flow_dx_total)`. Truncation is exactly what is wanted: unlike a difference, a total is only ever read modulo 2^16, so the discarded high bits carry no information the receiver needs.

The receiver computes `delta = int16(current − previous)`, that is, the difference modulo 2^16 read as signed. **There is exactly one limit: a movement of more than ±32767 counts while reception is interrupted.** Beyond that the wrap is indistinguishable from a shorter move the other way. Hand-waving the vehicle measured at most about 900 counts per second, so this is roughly **36 seconds of unbroken loss**, and nothing recovers a 36-second outage anyway. The bound is `kTelemFlowWrapSpan` in `telemetry.hpp`.

#### What the PC side must do

The subtraction lives in **one place**, `flow_delta(prev_total16, cur_total16)` in `lib/sfcli/commands/telemetry.py`. The terminal display, the CSV writer, the browser view and `lib/sfpilot/link.py` all go through it. The state (previous totals and previous `t_us`) belongs to `FlowTracker` in the same file.

- `decode_packet()` returns the **raw totals**. Turning them into a movement is the caller's job; the decoder stays a pure decode of one packet.
- **Detect a vehicle restart.** `timestamp_us` counts microseconds since *that vehicle's* boot, so a reboot restarts both it and the flow totals at zero. `FlowTracker` drops its reference when `t_us` goes **backwards** and reports the movement as unknown for that one packet. Without this, the first packet after a reboot would report the difference against the pre-reboot total as one enormous movement.
- The CSV carries **both the raw total and the movement since the previous row** (`flow_dx_total` / `flow_dx_delta`, …). The movement is what an analysis plots directly; the total lets it re-derive a movement across rows the recorder itself skipped.
- A 104-byte (v1) packet has no flow field, so the CSV leaves those cells empty as before. **Never a zero** — a zero would read as "the surface did not move".

#### Why the version number stayed at 2

`TELEM_VERSION` was not raised to 3 despite the change of meaning, because **v2 itself was born on the `feat/jev-autopilot` branch on 2026-09-19 and was corrected on the same day on the same branch: no released firmware ever sent the old meaning.** Raising it would make every receiver carry a decode path for a packet that exists nowhere. Before making the change, the repository was checked for other consumers assuming the v2 difference; the only readers of `flow_dx_sum` were `sf telemetry`, `telemetry_web` and their tests, all of which were updated with it.

### How the forward ToF is supplied (the R11 contract, now implemented)

The forward ToF started being driven on 2026-09-19 (jev-autopilot P2b), and was confirmed on hardware the same day (`docs/plans/jev-autopilot.md` §4.8). It implements, unchanged, the four-step contract that had been fixed in advance under R11.

| Step | Content | Implementation |
|------|---------|----------------|
| 1 | `TofTask` performs an XSHUT-staggered two-sensor bring-up | `tasks/tof_task.cpp::startTofSensors()` |
| 2 | A dedicated topic `sensor_tof_front`. **It shares no variable and no topic with the bottom sensor.** Bottom and front once updated the same timestamp variable, and on battery power the front sensor initialised successfully and made the rate appear doubled (`docs/architecture/udp-telemetry-design.md` §2, "one data source = one variable") | `sf_core/include/topics.hpp` |
| 3 | `tof_front_distance` / `tof_front_valid` / `tof_front_timestamp` added to `SensorSnapshot`, mirrored by `ImuTask` | `sf_core/include/data_types.hpp`, `tasks/imu_task.cpp` (`tof_front_status` added alongside) |
| 4 | Telemetry reads them from `sensor_snapshot` and fills bit1 and `tof_front` | `sf_telemetry/telemetry.cpp` |

#### Bring-up order (there is only one possible order)

Both VL53L3CX parts boot at I2C address 0x29. Re-addressing the bottom sensor while the front one is awake would reach **both** and interleave their ranging data. Hence the order is forced.

| Stage | Action | Why in that order |
|-------|--------|-------------------|
| 1 | Front XSHUT = LOW (held in reset) | Leaves exactly one part answering at 0x29 |
| 2 | Initialise the bottom sensor, move it to 0x30, through to `startRanging()` | **Exactly the previous procedure.** The bottom sensor is the only vertical observation the altitude estimate has, and its behaviour is not changed |
| 3 | From then on, one stage per cycle in whatever time the cycle has left (`FrontBringUp`): `Wake` raises the front XSHUT → the next cycle's `Probe` calls `isPresentAt()` (a single model-id read) → only if the part is real, `init()` → 0x31 → `startRanging()` | The bottom sensor has already moved to its own address, so only the front part answers at 0x29. **This is deliberately not done all at once during setup**: with no front part fitted, the driver polls for boot completion for up to 500 ms before failing (`VL53LX_BOOT_COMPLETION_POLLING_TIMEOUT_MS`), which would leave the bottom sensor unread for about 15 cycles. Probing for presence first settles "not there" in a few ms, and the boot wait is absorbed into a sleep the cycle was going to take anyway, so **`last_wake` is never touched and the bottom sensor's 30 Hz phase does not move at all** (safety requirement 2) |

If stage 3 fails, the front XSHUT goes back LOW and the task continues with `sensor_present(FrontToF) = false` (Optional, per R4). **Handling of a bottom-sensor failure is unchanged from before.**

#### Design measures that keep the bottom sensor undisturbed

The bottom ToF is the only vertical observation the altitude estimate has (the barometer is not fused by default) and is Critical (`hardware_init.md` §5). The front sensor is merely Optional. The design therefore guarantees the following.

| Requirement | Means |
|-------------|-------|
| A front failure does not reach the bottom sensor | Topic, `SensorSnapshot` field, timestamp and `SensorId` are all separate. The front failure path returns XSHUT to LOW and ends there, touching no bottom-sensor state |
| The bottom 30 Hz is not delayed by the front sensor | Within a cycle the bottom sensor is read **first and unconditionally**. The front one is read afterwards, only when the cycle has time to spare (`cycle_still_on_schedule`). With no time to spare, one front sample is dropped |
| The front reading never enters the estimator | `ImuTask` **only mirrors** `sensor_tof_front`; it passes it to neither `g_estimator` nor `g_takeoff_landing`. The front reading observes an obstacle, not the vehicle's state, and the state vector does not model it |
| It can be switched off at once if hardware misbehaves | Parameter `tof.front.enable` (default 1), read once by `TofTask` before the bring-up. `param set tof.front.enable 0; param save` takes effect **after a restart** (an address assignment cannot be surrendered at run time, so there is no live reload) |

#### Estimated I2C bus occupancy

How much of the bottom sensor's 33 ms period two sensors' reads take. The bus runs at 400 kHz and one byte is about 9 clocks (8 bits + ACK) = 22.5 µs.

| Operation | Transfer | Time |
|-----------|----------|------|
| `isDataReady` (`GetMeasurementDataReady`) | About a 2-byte read (register address 2 + data 1; roughly 6 bytes including start/stop) | about 0.14 ms |
| `getDistance` (`GetMultiRangingData`, `DEVICERESULTSLEVEL_FULL`) | The result block in one transaction: registers 0x0088–0x010E, **134 bytes** (covering system 44 + core 33 + debug 56), plus 2 for the address | about 3.1 ms |
| `clearInterruptAndStartMeasurement` | A few bytes written | about 0.1 ms |
| **Per sensor** | | **about 3.3 ms** |
| **Both sensors** | | **about 6.6 ms (about 20% of the 33 ms period)** |

The remaining 27 ms covers the other tasks on the same bus (BMP280 at 50 Hz, BMM150 at 25 Hz, INA3221 at 10 Hz — each a few tens of bytes per read) and the margin. **Adding the front sensor leaves bus occupancy at about a fifth of the period**, which does not threaten the bottom sensor's 30 Hz. Note that `getDistance` only runs on a cycle that has a new sample, so the figures above are worst cases.

The `cycle_still_on_schedule` skip is kept even so, to **drop a front sample rather than delay the bottom sensor's next read** if clock stretching or a retrying part makes the bus unexpectedly slow.

#### The Data Stream (400 Hz log) is out of scope here

Carrying the forward ToF in the Data Stream is **not done here**. Telemetry at 50 Hz is enough for the purpose (monitoring, and supplying `sf pilot`), and changing the wire format is separate work that should be verified on its own. `lib/sflog` already defines a `tof_front` stream at wire id **0x47**, which is what to use when the time comes. **0x49 must not be used** (`data_stream_wire.hpp:68-79`).

### Related files

| File | Role |
|---|---|
| `components/sf_telemetry/include/telemetry.hpp` | Struct, constants, offset assertions |
| `components/sf_telemetry/telemetry.cpp` | Build and send |
| `tasks/telemetry_task.cpp` | 50Hz driver, `@subscriber` annotation |
| `lib/sfcli/commands/telemetry.py` | PC decoder, terminal view, CSV |
| `lib/sfcli/commands/telemetry_web.py` | Browser view (shared decoder) |
| `lib/sfcli/commands/test_telemetry.py` | Decode tests, offset cross-check |

This packet is not yet described in `protocol/spec/messages.yaml` (whose `TelemetryPacket` is the 22-byte ESP-NOW version). That divergence is tracked as open issue C2 in `docs/plans/repository-cleanup-candidates.md`.
