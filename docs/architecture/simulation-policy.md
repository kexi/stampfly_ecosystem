# StampFly シミュレーション方針（Simulation Policy）

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。

> 制定: 2026-07-22。全面改定: 2026-09-08。改定理由: 初版は「層1 設計用線形モデル／層2 実ログ駆動再生／層3 SILS」という 3 層の枠組みで書かれていたが、これは設計者の意図した整理ではなく、設計の道具（モデル・解析手法）と実行環境（SILS）という軸の違うものを一列に並べていた。本改定では、設計者が各シミュレータに与えた役割を正とし、3 層の枠組みを廃止する。モデル一致の合否判定・規律・改修バックログは事実として引き継ぐ。

## 1. 概要

### このドキュメントについて

本ドキュメントは、StampFly Ecosystem にあるシミュレータ（SILS・VPython 版・Genesis 版）それぞれの役割と実現方法、共通の物理パラメータの扱い、実機データの使い方、SILS のプラント（制御対象の物理モデル）が満たすべき合格基準、そして今後の強化学習に向けた物理エンジンの比較を定める。シミュレーションに関する方針の基準文書であり、他文書と食い違ったときは本書を先に直す。

### 対象読者

- SILS・シミュレータを開発・改修する開発者
- 制御パラメータの変更を SILS や解析で裏付けようとする制御設計者
- シミュレータを教材として使う教育者、強化学習への応用を検討する研究者

### なぜ本書が必要か

シミュレータが 3 つあると「なぜ複数あるのか」「どれを何に使うのか」「物理はどこから来ているのか」「実機と同じコードが動くのはどれか」という疑問が必ず出る。答えを一か所に置き、資料や実装がこれと食い違わないようにするのが本書の目的である。

## 2. 3 つのシミュレータとその役割

| | SILS | VPython 版 | Genesis 版 |
|---|---|---|---|
| **役割** | **ファームウェアの開発と制御系の実装を、机上である程度完了させる**ための仕組み。飛ばす前の検証と合否判定 | **練習用**。比較的簡単で可読性の高い Python コードで、力学エンジン・3D 可視化・センサモデルなど「シミュレータの作り方」を学んでもらう | **強化学習**を行う上で使いやすいと判断し、選択肢として残している |
| **動く制御コード** | 実機に書き込むのと同じ C++ ファームウェア（無改変） | Python に移植した制御則 | Python の制御則 |
| **物理モデル** | MuJoCo（外部の物理エンジン。**物理計算にのみ使用**）＋自作のモータ・センサ・風モデル | 自作の 6 自由度剛体モデル（Python）＋センサモデル | Genesis（外部の高精度物理エンジン、GPU 並列） |
| **可視化** | `sf sils gui`（ブラウザ。three.js の 3D と Plotly のグラフ）。レビュー動画は事後に MuJoCo の Python レンダラで生成（`--video`） | VPython（ブラウザ 3D） | Genesis の描画 |
| **入力** | シナリオ `.scn`（操縦・外乱・故障の時系列）、キーボード操縦 | USB HID ジョイスティック（AtomS3 + Atom JoyStick） | スクリプト |
| **合否判定** | `.expect` による自動判定、`sf sils regression`（CI） | なし | なし |
| **場所・入口** | `simulator/sils/`、`sf sils build/scenario/gui` | `simulator/vpython/`、`sf sim run vpython` | `simulator/genesis/`、`sf sim run genesis` |

### なぜ 1 つでは足りないか

「実機のファームをそのまま動かして検証する」「中身を読んで作り方を学ぶ」「強化学習を回す」は要求が違い、1 つの実装では両立しない。SILS は決定論と実ファームとの同一性を最優先し、VPython 版は読みやすさを最優先し、Genesis 版は GPU 並列と学習との相性を優先する。3 つは §3 の物理パラメータを共有し、`sf params check` で食い違いを検出する。

### SILS の実現方法

- **ファームウェアは無改変**: `firmware/vehicle`（および `workshop`）のソースをそのまま PC 向けにコンパイルする。推定・制御だけでなく状態機械やフェイルセーフも含めて実機と同一（Code Identity）。パラメータも同じ表から読む（Parameter Identity）。
- **OS の代わり**: ESP-IDF / FreeRTOS の代わりに、ホスト用の互換の代替実装（`compat/`）と、単一トークン＋仮想時計の離散事象スケジューラである決定論的な疑似 RTOS（`rtos/`）の上で動かす。同じ入力なら毎回同じ結果になる。
- **制御対象**: MuJoCo の 6 自由度剛体モデルに、自作のモータ（電気機械 ODE）・センサ・風のモデルを載せ（`physics/`, `plant/`）、400 Hz でファームと歩調を合わせる。**MuJoCo は物理計算にのみ使い、実行中の描画には使わない**。MuJoCo の対話ビューアはモデルファイルを目視確認するための任意ビルドオプション（`-DSILS_MUJOCO_VIEWER=ON`）で、シナリオ実行には関与しない。
- **試験の与え方**: シナリオ `.scn` に操縦入力・外乱・故障を時系列で書き、`.expect` の合格基準で PASS / FAIL を自動判定する。`sf sils regression` が CI で既存動作の破壊を検出する。
- **学習者コードも同じ土俵**: `workshop` ターゲットでは `user_code.cpp` が同じプラントでループを閉じる（`sf lesson sils`）。
- **できないこと**: 複数タスクの競合（並行処理の競合）と、実際の WiFi / ESP-NOW の物理層は、再現性のために処理を一本のループにまとめている構造上、原理的に再現できない（旧 `RESET_PLAN.md` §11、2026-09-13 削除・タグ `archive/2026-09-13`。経緯は §10 参照）。実機でしか確かめられない。

### 設計・解析の道具はシミュレータではない

次の 2 つは以前「層 1・層 2」と呼んでいたが、機体を動かして見せる実行環境ではなく、設計と解析の道具である。本書では区別して扱う。

| 道具 | 中身 | 用途 | 場所・入口 |
|---|---|---|---|
| 設計用の線形モデル | 実飛行ログから同定した低次の伝達関数 $G(s)$ | ゲイン設計、ループ整形、仕様ベースの自動チューニング | `tools/sysid/`、`sf sysid fit` / `rate-fit` / `rate-tune` |
| 実ログ駆動の再生 | 同定モデルと Python に移植した制御則を、実機ログから再構成した外乱・指令で駆動する閉ループ再生 | パラメータ変更の A/B 判定 | `analysis/scripts/` |

## 3. 共通の物理パラメータ

質量・慣性・推力係数 $C_T$・反トルク係数 $C_Q$ などの機体物理パラメータの基準ファイルは `control/models/stampfly_physical.yaml` である。ファームウェア（`generated_params` 系ヘッダ）・SILS プラント・VPython 版・Genesis 版・`docs/architecture/stampfly-parameters.md` はここから生成または転記し、`sf params check` が転記の食い違いを検出する。値の実測履歴と採用根拠は `stampfly-parameters.md` に置く。

注意: `sf params check` は転記の一致しか見ない。「ファームの静的モータ曲線と SILS プラントの ODE が別のモータを表していた」（2026-08-22 判明）のような**モデル構造の不一致**は検出できないため、§5 の合否判定で数値的に確認する。

## 4. 実機データの扱い（立ち上げ期 → Model Fidelity 期）

| 期間 | フェーズ | 実機データの扱い | 根拠文書 |
|---|---|---|---|
| 〜2026-06（初飛行前・SILS立ち上げ期） | 更地化・物理ベース SILS の再構築 | 実機データ不要。物理モデルの真値で機械的に検証（旧 M7/M8 の実機ログ再生・差分診断は廃止） | §10（立ち上げ期の経緯） |
| 2026-06〜（実機飛行後・Model Fidelity 期＝現在） | development_roadmap Phase 3〜5 | 実機ログで線形モデルの同定・実ログ再生による A/B・SILS プラントの較正を行う。実機ログの再生・突き合わせは方針違反ではなく Phase 5 の本作業そのもの | development_roadmap Phase 5 |

注: アルゴリズムの中身に依存せず、実装でなくインターフェースに依存するという原則（旧 RESET_PLAN 方針2、§10 参照）は期に依らず有効であり、本書はこれを変更しない。

## 5. SILS のモデル一致の合否判定

Code Identity のおかげで、実機同定に使ったのと同一の同定パイプライン（`sf sysid rate-fit` 等）を、SILS が生成したログにもそのまま適用できる。

**2026-09-11 追記（フライトログ形式の統一）:** SILS と実機は、同じ「StampFly フライトログ一式」形式（拡張子 `.sflog.zip`。1 回の飛行・実行のセンサ信号一式をまとめた zip 形式のログファイルで、仕様の基準ファイルは `protocol/spec/flight_log.yaml`）でログを書き出すようになった。これにより Code Identity（コード一致: 制御・推定のソースコードが実機と SILS で同一であること）は同定パイプラインだけでなく記録形式にも及び、`sf sysid fit`/`rate-fit` と `sf log viz`/`analyze` は実機ログ・SILS ログのどちらに対しても改修なしで動く。SILS 側だけが追加で持つストリームは、MuJoCo（物理計算のみに使う外部の物理エンジン）が計算した位置・姿勢・速度・角速度の真値を収める `truth.csv` と、シナリオが注入した外乱・故障などの事象を時刻付きで記録する `events.csv` である。ロール軸のレートステップ用シナリオ `sysid_roll_step.scn`（ACRO モードでの角速度ダブレット入力、13 秒）でこの経路を実測したところ、`sf sysid fit --axis roll --mixer vehicle` はロール軸のトルク→角加速度ゲイン $K=99453$ rad/s² per N·m（設計値 $1/I_{xx}=109170$ に対し 8.9% 低い）、モータの実効時定数 $\tau_m=14$ ms（SILS のモータ ODE 自体の実効時定数は約 16 ms、同定ツール側の既定基準値は 20 ms）、決定係数（当てはまりの良さを表す指標。1 に近いほど良い）$R^2=1.00$ を得た。

**手順:**

1. SILS 内で `rate-excite` 相当の励振を行う
2. 実機と同一の同定パイプラインを適用する
3. $(b, L, T)$ を抽出する
4. 実機同定値と比較する

**合格基準**（development_roadmap Phase 3 の許容差を流用）:

| 指標 | 許容差 |
|---|---|
| ステップ応答立ち上がり時定数 | ±20% |
| gyro RMS | ±50% |

この合否判定を SILS 再確認試験（変更で既存の動作が壊れていないかを自動で確かめる試験）に組み込み、以後のプラント改修の効果と劣化を毎回数値で判定する。

合否は軸ごとに独立して判定する。**roll/pitch は 2026-07-26 計測（下記）で合格域に達した（yaw は未達）**。ゲイン検証に SILS を使う信頼性は roll/pitch 軸については向上したが、**全軸が合格に達するまでは**、§5 の摂動族（トルク効き $\in[0.4,0.7]$・むだ時間 $L\in[8,15]$ ms・会場級外乱 0.2〜1 Hz）による検証を SILS 検証と併用し、「SILS 単独最適化禁止」の原則を維持する。SILS 乱流ベンチを直接最適化すると実機で位相余裕が負になるゲインに収束した教訓がある（`firmware/vehicle/docs/control_theory_overview.md` §5.4: SILS 上で Td=0.08 に最適化したゲインが実機では PM −375° に発散）。

> **初回計測（2026-07-22, `sf sils sysid-gate`、むだ時間0・静的モータ曲線の旧プラント）:** 全軸 FAIL — roll b +39.6% / L_total +39.0%、pitch b +109.7% / L_total +19.3%、yaw b −61.4% / L_total +95.7%。遅れの**構造**が実機と逆で、SILS は一次遅れ支配（T≈20ms=motor_tau、L≈1.5ms）、実機はむだ時間支配（L≈11〜16ms、T小）。`--motor-delay 10` で L は 1.5→10.6〜12.2ms と設計どおり動くが、L_total は 27ms 前後へ悪化する — 一致には遅延単独ではなく、バックログ#2（モータ ODE 化）・#3（係数再較正）との同時調整が必要。なお yaw の実機基準値は3パラフィット由来で最も弱い（`analysis/reports/rate_sysid_reference/README.md` の注意参照）。
>
> **第2回計測（2026-07-26 計測, ODE プラント, `sf sils sysid-gate`）:** roll は b +27.2% / L_total +12.7% で **PASS**、pitch は b +26.6% / −1.5% で **PASS**（コヒーレンス 0.97〜0.99、良好）。yaw は b −18.2% は許容域内だが L_total が −100%（識別が退化）で **FAIL** — 実装した反トルク零点（$\tau_z\approx45.7$ ms、制御帯域内で 3.5 Hz のリード）を、現行の3パラメータ $(b,L,T)$ フィットでは表現できない構造的な問題。実機側の基準値自体も3パラフィット・コヒーレンス0.44の弱い基準である点に注意（バックログ#11 で対処予定）。

## 6. 期に依らず変わらない規律

- **制御パラメータ変更は必ず実フライトログを使った数値シミュレーションで裏付ける。** 「Ti を短くすれば改善する」のような定性推測だけで提案しない。シミュレーションの結果、逆効果であれば提案しない（`control_theory_overview.md` §5.5 の鉄則）。
- **公称モデル1点への最適化をしない。** 実機はセッション間でドリフトする（同一ゲインで 5–8 Hz 帯の基準値が2.4〜2.7倍変動した実例がある）。トルク効き $\in[0.4, 0.7]$、むだ時間 $L \in [8, 15]$ ms、会場級外乱 0.2〜1 Hz を**摂動族**として持ち、ゲインの採否は族全体で悪化しないことを条件にする。
- **SILS の原理的限界は SILS では検証できない。** 並行処理の競合（複数タスクが同時に動くことによる競合）や実 WiFi/ESP-NOW の物理層は、SILS の再現性のために本来並行する処理を一本のループにまとめている構造上、原理的に再現できない（旧 RESET_PLAN §11、§10 参照）。実機並行性の検証は別途行う。

## 7. 強化学習に向けた物理エンジンの比較（MuJoCo と Genesis）

今後、強化学習で「MuJoCo か Genesis か」という選択が出てくる。本リポジトリでの位置づけと、判断の観点を整理しておく。数値や対応状況は 2026 年 9 月時点の把握であり、採用時に最新版で再確認すること。

| 観点 | MuJoCo | Genesis |
|---|---|---|
| 開発元・実装 | Google DeepMind。C 実装＋Python バインディング。Apache-2.0 | Genesis-Embodied-AI（大学・企業の共同）。Python / PyTorch 実装。Apache-2.0 |
| 実行形態 | CPU 逐次実行が基本。GPU 大規模並列は別実装の MJX（JAX 版） | GPU 並列が前提。数千環境を一括で進め、微分可能 |
| 物理の範囲 | 剛体・関節・接触が中心 | 剛体に加え流体・柔軟体・粒子などの複数物理（対応範囲は版により異なる、要確認） |
| 決定論・再現性 | 単一スレッドで決定論的（SILS が要求する性質） | GPU 並列では演算順序により結果が揺れ得る（要確認） |
| 強化学習の周辺整備 | 成熟。Gymnasium・dm_control・MuJoCo Playground など事例が多い | 新しい（2024 年 12 月公開）。学習例は同梱されるが周辺は発展途上 |
| 本リポジトリでの現在の使い方 | SILS の物理（C API で実ファームと歩調を合わせる）。描画には使わない | `simulator/genesis/`（Python の制御則、モータ ODE の出典） |
| 実ファームとの接続 | SILS が既に接続済み（C++ 同一ソース） | 接続する仕組みは無い。学習した方策をファームへ移す工程が別途要る |
| 向く場面 | 実ファームと同じ物理で方策を検証したい、CPU で確実に回したい | 何千機を同時に学習させたい、微分可能性を使いたい |
| 課題 | 大規模並列は MJX を別途用意する必要があり、C++ ファームとは接続できない | 決定論性と成熟度。SILS の合否判定に相当する物差しが無い |

方針: 学習は Genesis（または MJX）で回し、得られた方策の検証は SILS（MuJoCo 物理、実ファーム）で行う、という分担が現実的である。両者の物理パラメータは §3 の基準ファイルで揃える。

## 8. SILS プラント改修バックログ（優先順）

| # | 作業 | 根拠・目標値 | 状態 |
|---|---|---|---|
| 0 | モデル一致の合否判定の実装（§4） | すべての改修の物差し。最優先 | **実装済み（2026-07-22）** — `sf sils sysid-gate` |
| 1 | むだ時間の追加 | 現状 SILS 実効遅れ ~5 ms vs 実機 8.4〜14.7 ms。合否判定ツールで SILS の現状 $L$ を実測し、差分を duty→推力経路の輸送遅れとして設定可能にする | **実装済み（2026-07-22, 既定OFF）** — `sf sils scenario --motor-delay`。**ODE化後は追加遅延不要と判明（重畳するとL_total悪化、2026-07-26計測）。既定OFF維持** |
| 2 | モータモデルの ODE 化 | `simulator/genesis/motor_model.py` の電気機械 ODE $\dot\omega = \bigl[-(D_m + K_m^2/R_m)\omega - C_Q\omega^2 - Q_f + K_m V/R_m\bigr]/J_{mp}$ を SILS へ移植。実測値 $J_{mp}=1.375\times10^{-8}$ kg·m²、$C_Q=4.10\times10^{-11}$ N·m·s²/rad²、$\omega_{hover}\approx3670$ rad/s、ホバ点実効時定数 $\tau_{eff}\approx17.5$ ms | **実装済み（2026-07-26）** — 実測ファミリ（$C_Q=4.10\times10^{-11}$, $J_{mp}=1.375\times10^{-8}$, $K_m=5.682\times10^{-4}$, $R_m=0.593$, $D_m\approx0$, $Q_f=9.507\times10^{-6}$）で RK4 積分。反トルクは $C_Q\omega^2+J_{mp}\dot\omega$（ヨー零点を物理的に再現）。（2026-08-22 追記: この実測ファミリ `measured_2026_07` は、ファームが使う静的曲線 `legacy_motor_curve` とは別モータの記述と判明。統一は #3 のベンチ計測待ち） |
| 3 | $C_T$/$C_Q$/thrust_efficiency の3点セット再較正（ファームCt切替） | 2026-07-15 thrust stand実測の $C_T$ は、新プロペラでの電圧・回転数・推力の有効な同時計測を欠くため撤回（2026-08-03）。撤回時点でファームウェア（`actuator.cpp` の `MOTOR_CT`）は暫定採用値 $C_T=1.00\times10^{-8}$ と数値一致していたため、当時は本タスクが解消したと判断されたが、この判断は誤りだった（詳細は状態欄） | **再オープン（2026-08-22）** — 2026-08-03 の「解消」判定は誤りだった。静的曲線 Am/Bm/Cm の廃止（2026-07-26）は SILS プラント側のみで、ファームの推力→デューティ経路（`thrustToDuty()`, `firmware/vehicle/components/sf_actuator/actuator.cpp`）は現在も静的曲線を使用しており、firmware/SILSプラント間の乖離は消滅していなかった。ファームの静的曲線は SSOT `legacy_motor_curve`（$R_m=0.34$, $K_m=6.125\times10^{-4}$, $C_Q=9.71\times10^{-11}$）の代数的言い換え（$A_m=R_mC_Q/K_m$, $C_m=R_mQ_f/K_m$ で厳密再現）である一方、SILS ODEプラントは `measured_2026_07`（$R_m=0.593$, $K_m=5.682\times10^{-4}$, $C_Q=4.10\times10^{-11}$）——両者は別モータを記述していた。ホバー点でプラントはファーム指令推力の1.252倍を出し、`hover.thrust_corr=1.12` が乗って機体重量の1.402倍の推力になっていたが、プラントの `thrust_efficiency` がこの不整合を偶然打ち消していた。db65e0e5（2026-08-03, Ct撤回）がその打ち消しを失わせ、2026-08-03〜2026-08-22 の間 `sf sils regression` シナリオ33本中20本が失敗していた（main未pushでCI未検知）。**2026-08-22 対応:** ファームの静的曲線に `hover.thrust_corr` の1.12を畳み込み（$A_m$×1.12・$B_m$×$\sqrt{1.12}$・$C_m$不変、全推力域でduty出力恒等の変換）、`hover.thrust_corr` 既定を1.00に復元。SSOTに `flight_anchored_motor_curve` を新設しファームはその写しに（`legacy_motor_curve` の実測記録は数値を変えず保存）。SILSプラントの `thrust_efficiency` を0.7133（=1/1.402、理想ODEと飛行実証済みファーム+実機の差を表す明示係数）に設定。SILSシナリオのSTABILIZEスロットルを×0.8386で再較正（ALT/POSは上昇率指令のため不変）。結果: `sf sils regression` は28 PASS + 5 KNOWN-FAIL（33本）に回復。**未解決:** `legacy_motor_curve`/`measured_2026_07` のどちらが新プロペラの実体かは未決着（ベンチ V-ω-T 3量同時計測待ち、後継タスクと同一）。`thrust_efficiency=0.7133` は計測完了までの暫定値であり、計測後は両ファミリを1モータモデルに統一し `thrust_efficiency=1.0` にできるはず |
| 4 | モータ不感帯・低 duty 非線形 | 実機 ~0.9 Hz リミットサイクルの再現に必要。`analysis/datasets/motor_sweep_20260714/` のベンチデータ（3個体・プロペラ有無2条件）で同定 | 未着手 |
| 5 | 空気抵抗の追加 | 現状 MuJoCo プラントは抗力ゼロ。`sf sysid drag` の実ログ同定値を使用 | 未着手 |
| 6 | フロー品質モデル（N3） | SQUAL（オプティカルフローの表面品質指標）固定100・無ノイズが POS_HOLD 初飛行発散の盲点だった（`firmware/vehicle/docs/poshold_journey.md`: 「Code Identity でも実機で動かない」盲点の実例）。Flow/Mag ノイズは N3 tier として後段に計画済み（旧 SILS 再構築計画の記録、§10 参照） | 未着手 |
| 7 | 電池電圧降下の $R_{int}$ 実測較正 | 電圧依存推力誤差が高度ウォブルの主因（corr(V, 高度std)=−0.78、`analysis/reports/poshold_3min_battery_wobble_20260627.md`）。電圧降下モデル自体は実装済みで閉ループ emu では既定 ON（2026-06-07, `b8fd27ea`）。残作業は内部抵抗 $R_{int}$（現状値 0.1 Ω は vpython 由来の仮値）の実測較正のみ | 未着手 |
| 8 | N1 振動係数を現行 vehicle ログで再同定 | 現在の軸別係数（`vib_accel_k`/`vib_gyro_k`）は旧機（legacy `firmware/vehicle`）の hover02 ログ由来のシード値 | 未着手 |
| 9 | 実機ログ入力リプレイ（`sf sils replay` 相当） | WireControl（テレメトリの制御入力構造体）50 Hz スティック入力を `.scn` シナリオへ変換し、実ログと同一プロットで比較する。実ログ再生（解析）の結果を SILS で再現するための要 | 未着手 |
| 10 | 関連文書の整合維持 | 本書と development_roadmap の食い違いに気づいたら、本書を先に更新する | 継続 |
| 11 | ヨー軸の合否判定の4パラメータ化 | `rate_sysid` のヨーフィットを反トルク零点込みの4パラメータモデル（`firmware/vehicle/docs/yaw_axis_model.md`）へ拡張し、実機基準値（`analysis/reports/rate_sysid_reference/README.md` の `reference.json`）も同一パイプラインで再生成して同条件比較にする | 未着手 |
| 12 | ファームヨートルク権限の再検討 | 新基準ホバー duty（≈0.7245）下での `rate.yaw.max_torque` 差動余裕を再検討する。SILS 再確認試験の pos_flight/pos_yaw/yaw_hold が known-fail（`sf sils regression` の xfail マーカー）として追跡中。実機 NT金沢問題（2026-07-17 治療）と同根の可能性がある | **調査済み・オーナーの判断待ち（2026-09-19）** — 3 案を実測し、いずれも現時点では採らない。真因は「ミキサーが上側で切り落とした分を再配分しないこと」と特定した。詳細は下記「#12 の調査結果」 |
| 13 | 姿勢減衰余裕の調査 | calib（注入バイアス×新プラントの離陸動特性で 0.6-0.7 Hz 自励振動）・commloss_land_level（LANDING 水平化判定中のロール収束不足）で顕在化。バックログ#4（モータ不感帯）・#5（空気抵抗ゼロ）との関連を確認する。（2026-08-22 追記: #3 で判明したプラント推力過大[ホバー点でファーム指令の1.252倍、corr込みで機体重量の1.402倍]が本現象の一因だった可能性があり、thrust_efficiency 補正後に再検証する） | 未着手 |
| 14 | `flow_vel_scale`（`SILS_EMU_FLOW_SCALE` / `sf sils scenario --flow-scale`）が飛行経路に効いていない | 2026-09-19 発見。`Config::flow_vel_scale` を読むのは `Plant::flow()` だけで、それを呼ぶのは `simulator/sils/smoke/plant_smoke.cpp` のみ。閉ループ飛行時にファームへ届くフローは `virtual_board.cpp` の `sils_board_spi_transfer()` → `sils_pmw3901::set_motion_from_velocity()` 経由で合成されており、この乗数を一切参照しない。実測（0.060N の横風・35 秒・実時間エミュレータ）: 指定なしで 1388 周期中 359 周期が「流されている」、`--flow-scale 0.35` で 1389 周期中 361 周期 — 差は実行ごとのばらつきの範囲。`--flow-scale` は hikoki64 §3.3 の注入実験（#6 のフロー品質モデルと同じ系統）のために用意されたノブであり、**閉ループの故障注入として使うと、効いていないのに効いたつもりの実験になる**。修正は「合成側（pmw3901_device）にも同じ乗数を掛ける」で足りるとみられるが、既定 1.0 のバイト一致（`sf sils regression` の基準）を壊さないことの確認が要る | 未着手 |

| 16 | 前方 ToF のデバイスモデルにノイズが無い（既定 OFF の模擬） | 2026-09-19 追加（jev-autopilot 4.9）。`simulator/sils/devices/vl53_front_device.cpp` と `Plant::tofFront()` により、SILS は前方距離を模擬できるようになった（`SILS_EMU_FRONT_TOF=1` のときだけ有効。既定は不在で、回帰の基準は不変）。**忠実度の位置づけ**: 上限レンジ 2.0m（飛行領域の半径 2m の内側で意味を持つ範囲に限定）、レンジ外・対象なしは実機の実測（4.8.6: status=255・値 0）に合わせた。**観測ノイズは付けていない** — 底面 ToF の N2 観測ノイズが既定 OFF である以上、前方だけノイズを持つと 2 センサの忠実度がちぐはぐになるため。ノイズを入れるなら #6（フロー品質モデル）と同じ N3 tier で両方まとめて扱うのが筋である。実測の偏りは真値 1.0m に対し +17mm（底面と共有する histogram のサブ bin 分解能に由来し、ノイズではない） | 未着手（N3 tier で #6 と併せて） |

| 15 | `stab_flight` の `att_rmse` の余裕が狭く、起動時のタイミングのずれで合否が変わる | 2026-09-19 発見。基準値 0.0489（2.80°）に対し閾値は 0.05236（3.0°）で、**余裕は 6.6% しかない**。前方 ToF とは無関係に、起動時に素の `vTaskDelay(500ms)` を 1 つ入れるだけで `att_rmse` は 0.0483 へ動く（前方のコードを 1 行も通さずに測定）。つまりこの指標は起動タイミングに敏感で、**無関係な変更でも境界を跨ぎうる**。同じ感度は `test_realtime_fly.py::test_determinism_unchanged_without_env_vars` の SHA256 基準値にもある。閾値の見直し（または指標を起動タイミングに鈍感にする）を検討する。なお 2026-09-19 の前方 ToF 駆動（jev-autopilot P2b）は、在否確認を安価にし起動を周期に分散する設計に直した結果、**既定（`tof.front.enable=1`）で `att_rmse` も SHA256 も基準どおり**であり、本項目の原因ではない | 未着手 |

### #12 の調査結果（2026-09-19、オーナーの判断待ち）

3 案を別々の作業ツリーで実装・実測し、審査と 3 観点の反証にかけた。**結論は「3 案とも現時点では採用しない」である。** 採否を分ける論点が制御則の設計判断であり、飛行実績と実機ログを持つファームのオーナーが決めるべきものだからである。

#### 真因（実測で特定した）

**ミキサー（`firmware/vehicle/components/sf_actuator/actuator.cpp`）は各モータの duty を独立に切り詰め、上側で切り落とした分を再配分しない。** そのため飽和時には平均揚力が構造的に失われる。数値で閉じている:

- ヨーのレバレッジは $0.25/\kappa = 61$ N/(N·m)（$\kappa = 4.10\times10^{-3}$）。
- 上限いっぱいのヨートルク $1.226\times10^{-3}$ N·m は、1 モータに $0.0748$ N ＝ ホバー分担（$0.1016$ N）の **73.6%** の上積みを要求する。必要な duty は **1.0522** で上限 1.0 を超える。つまり**現行の上限は構造的に到達不能**である。
- 超過分はモータ毎に 1.0 で切り落とされ、下側のモータへ戻されない。結果として 4 モータの平均推力が落ち、機体が沈下する。
- 参照用シナリオの実測（HEAD 8a956654）: `yaw_crossaxis` では「トルクが上限に張り付いている標本の割合」と「duty が上限に張り付いている標本の割合」が **どちらも 0.8156 と完全に一致**する。同じ標本であり、トルク飽和がそのまま duty 飽和に変換されている。

再現手段は `simulator/sils/scenarios/yaw_crossaxis.scn` と `yaw_cw90_low.scn`（**参照用・合否判定なし**、TEST_MATRIX.md 2 節「参照用シナリオ」）。

#### 3 案の実測と、採らない理由

| 案 | 内容 | 実測 | 採らない理由 |
|---|---|---|---|
| **A** 上限の余裕則 | `rate.yaw.max_torque` を $1.226\times10^{-3}$ → $8.331\times10^{-4}$（ホバー分担の 50% をヨーに充てる規則） | 落下は解消（`yaw_crossaxis` の `alt_min` 0.006 → 0.294m）。ただし `stab_flight` が PASS → FAIL（`att_rmse` 0.0489 → 0.0594、閾値 0.05236）。狙った既知失敗（pos_flight / pos_yaw）は**直っていない**（`duty_max` 1.0000 のまま）。トルク上限の張り付き率は 0.8156 → **0.9719 に悪化** | **制御リミットの変更**であり、6 章の規律（実フライトログを使った数値シミュレーションでの裏付け）の対象。必要な実機再生ログ 3 本（NT 金沢、`stampfly_udp_20260627T020050` / `T164611` / `T165713`）が**リポジトリに無い**（`logs/*` は `.gitignore` 対象、`git ls-files logs/` は `.gitkeep` のみ）ため裏付けが取れない。加えて保持データ `analysis/scripts/yaw_nt_kanazawa/kappa_fix_sim_results.json` の実測外乱ピークは最悪 1.54 mNm に対し、新上限が出せる真のトルクは **0.74 mNm（0.48 倍）** — 事故の治療と逆行する |
| **B** D 項フィルタ | ヨーレート D 項の高域利得を下げる | **前提が誤りであることを実測で示し、実装者自身が却下した。** 1 LSB 交番への D 項の寄与は $3.4\times10^{-6}$ N·m（上限の 0.28%）で、飽和の原因ではない。不完全微分の高域利得 $K_p/\eta = 8.0$ は $T_d$ に依存しない。`alpha=1.0` は `detailed_design.md:295` に明記された**意図された設計** | 直すべき対象が存在しない。試した代替（後退差分）は軸ごとに減衰率が変わり、ロールが設計の 16.7% まで鈍って `stab_flight` の `att_rmse` を 0.0885（閾値の 69% 超過）にした |
| **C** ミキサーの優先度つき縮小 | 配分を推力[N]空間で行い、上下限に収まるよう差動群だけを縮小する（ヨーを先に譲る）。`actuator.cpp` の 1 ファイルのみ | 落下は解消（`yaw_crossaxis` の `alt_min` 0.006 → 0.211m、`yaw_cw90_low` 0.007 → 0.194m、`api_flight` 0.359 → 0.946m）。再確認試験の判定は 34 本すべて不変（28 PASS + 5 KNOWN-FAIL + 1 SKIP） | **反証で阻止級の指摘が 4 件**（下記） |

#### 案 C に対する反証（4 件、いずれも阻止級）

1. **離陸できなくなる組み合わせがある。** 加速度 X 軸バイアス 0.12 と ヨージャイロバイアス 0.02 が**同時に**乗ると離陸できず緊急解除する（`calib` の `Takeoff complete` が PASS → FAIL、`alt_max` 0.4694 → 0.0189m）。単独のバイアス掃引（ヨー 6 条件・加速度 7 条件）では再現せず、**組み合わせ特有の新規欠陥**である。実機の慣性計測装置のバイアスは両軸に同時に乗るため、これは実機ホバリング試験の直接のリスクになる。なお `calib` は元から KNOWN-FAIL なので、**再確認試験の合否数字は一切動かず、この欠陥は検出されない**。
2. **縮め方がコメントの説明と違う。** 「ヨーをゼロにしても収まらない場合に限りロール/ピッチを縮める」とコメントにあるが、コードは無条件に縮めている。ヨー指令が 0 のときでもロール/ピッチを 0.30〜0.54 倍に削る（20 万点の掃引で 42%）。さらに**総推力が 1.2×ホバーを超えると姿勢トルクが完全に 0 になる領域**がある（30 万点の掃引で 15.14%、該当は総推力 1.21〜1.80×ホバー）。旧ミキサーは同じ条件で姿勢トルクを出していた。`architecture.md` の不変条件 INV-2（パイロットの姿勢権限）に違反する。
3. **NT 金沢の治療を実効的に無効化する。** 治療の成立条件は「duty の飽和する範囲（1.39〜1.85 mNm）までヨーを出し切れること」だが、新ミキサーはヨーを先に譲るため、出せるヨートルクが構造的に頭打ちになる: 3.7V で 1.00 mNm（旧 1.33）、3.3V で 0.56 mNm（旧 1.11）。実機のホバー電圧帯 3.65〜3.86V で**全 4 事象の必要値に届かない**。案 A が「上限を下げるのは治療と逆行する」として却下されたのと同じ逆行を、`params.cpp` を触らずに引き起こしている。「上限値を下げていないので対象外」という論法は、実際に送り出せるヨートルクが下がっている以上成立しない。
4. **実ログ裏付けの規律の対象である。** `firmware/vehicle/docs/architecture.md:202` が、ミキサーの 2 段分離（まさに案 C がやっていること）の着手条件として「SILS 再確認試験・既存実習ゲインへの影響をシミュレーションで検証すること — CLAUDE.md『制御系パラメータ変更』原則」を**名指しで要求**している。「`params.cpp` を触らないので対象外」は誤り。

加えて、案 C は**送信機による手動 POS_HOLD の軸またぎ落下を直さない**（同じ緊急解除に至る）うえ、接地速度が 32% 増える（0.76 → 1.00 m/s）。直っているのは API 経由の自律飛行だけである。

#### 相反 — ここがオーナーの判断を要する点

**「揚力を守ること」と「ヨー権限を確保すること（外乱の治療）」は相反する。** 飽和時に出せる合計は有限で、ヨーを譲れば揚力は守れるが外乱に抗するヨートルクが減り、ヨーを押し通せばヨー権限は保てるが揚力が失われて沈下する。どちらを優先するかは制御則の設計判断であり、**飛行実績（実飛行 87 回の系譜）と実機ログを持つファームのオーナーが決めるべきものである。**

#### オーナーへの判断依頼（選択肢）

| 選択肢 | 内容 | 併せて必要になること |
|---|---|---|
| **(a)** 揚力優先のデサチュレーション | 案 C を修正して採用する。修正必須は反証 2 の 2 点（ヨーがゼロならロール/ピッチを削らない、総推力超過でも姿勢トルクを残す）と反証 1 の離陸阻害 | ヨー外乱への対処の**再設計**（案 C を直してもヨーを先に譲る限り外乱権限は落ちるため、NT 金沢型の外乱に対する手当てが別途要る）。実機ログによる裏付けと、低高度ホバーからの段階的な実機確認 |
| **(b)** 現状維持 | ミキサーを変えず、「旋回前に高度を取る」運用で回避する（`api_flight.scn` の `up 70` が現にそうしている） | 自動操縦側でこの制約を明示すること。`docs/plans/jev-autopilot.md` 4.9 節のヨー回転探索は保留のままになる |
| **(c)** 別案 | 例えば、上側で切り落とした分を下側のモータへ戻す（総推力を保ったまま再配分する）方向。今回の 3 案はいずれもこれを試していない | 同上の実機裏付け |

#### 再現手段

- 参照用シナリオ: `simulator/sils/scenarios/yaw_crossaxis.scn`（`--duration 40000000`、窓 26-34 秒）、`yaw_cw90_low.scn`（`--duration 36000000`、窓 18-28 秒）。**いずれも `.expect` を持たない**ので `sf sils regression` の対象外である。
- 上記の落下を特徴づける指標（duty の上限張り付き率・トルク上限張り付き率・ヨー角速度の二乗平均平方根誤差）は、今回は使い捨ての試験プログラムで算出した。`tools/` にスクリプトを増やさない方針（PROJECT_PLAN §8）に従い、**恒久化するなら `sf sils scenario` の既存の指標（`_traj_metric`）に `duty_sat_frac` 等として足すのが筋である**（提案のみ。未実装）。
- 実機再生ログ 3 本が入手できたときは、`analysis/scripts/yaw_nt_kanazawa/torque_budget.py` が使える。ただし同スクリプトは別マシンの絶対パスを直書きしており（`LOG_DIR`）、引数解析も無いため、入力パスを引数で受けるよう直す必要がある。

#### 併せて見つかった問題（本件とは別に処理が要る）

**`actuator.cpp:23` の `@design detailed_design.md §5 — X-quad mixer` が実在しない節を指している。** `detailed_design.md` の §5 は「状態推定インターフェース定義」であり、ミキサーの節ではない。判定ステータスは `[OK]` と書かれているが、参照が解決しないので実際には `[OK]` ではない。**設計文書にミキサーの節を新設するかどうかは文書構成の変更であり、オーナーの判断事項**のため、本更新では記録にとどめる。

## 9. 関連文書マップ

| 文書 | 何の正か |
|---|---|
| 本書 | シミュレーション方針（3 つのシミュレータの役割・SILS の実現方法・物理パラメータの基準ファイル・モデル一致の合否判定・バックログ） |
| `simulator/README.md` | VPython 版・Genesis 版の使い方 |
| `simulator/sils/README.md` | SILS ベンチの使い方（ターゲット・シナリオ・GUI） |
| `simulator/sils/RESET_PLAN.md`（2026-09-13 削除、タグ `archive/2026-09-13`） | 削除済み。SILS ベンチの構造・立ち上げ経緯は本書 §10 に要約 |
| `firmware/vehicle/docs/development_roadmap.md` | 開発工程全体（Phase 0〜6） |
| `firmware/vehicle/docs/control_theory_overview.md` | 制御設計の規律・同定の教訓 |
| `firmware/vehicle/docs/noise_and_vibration_model.md` | センサノイズモデル（N0〜N2、N3/N4 計画） |
| `firmware/vehicle/docs/yaw_axis_model.md` | ヨー軸モデル |
| `docs/architecture/stampfly-parameters.md` | 物理パラメータの値と実測履歴 |
| `analysis/scripts/alt_dob_design/README.md` ほか `analysis/reports/` | 実ログ駆動の再生の実施記録 |

## 10. 立ち上げ期の経緯（記録）

- **旧 SILS の全面刷新（2026-05-31 完全削除→再構築）**: 実機ログ再生・差分診断ベースだった旧 SILS（`quad_model`／`sils_main.cpp` 等）を削除し、物理ベース・MuJoCo・アルゴリズム非依存の新 SILS を E0（更地化）〜E8 の段階で構築、続く P1（骨格）〜P10（検証カバレッジ計測）で CLI・ダッシュボード・共有用レビュー動画・センサノイズ N0〜N2・外乱・全飛行モード網羅・衝突耐性まで積み上げた。
- **プラント時間基準バグ（2026-06-03, `cea0d8cf`）**: 物理更新が実時間の3倍速で進み、空中ホバーが成立しなかった。4000Hz 固定タイムステップの累積器方式に修正して解消（詳細は削除対象外の `simulator/sils/docs/plant_timebase_bug.md`）。
- **χ² 判定（カイ二乗判定）過剰棄却（2026-06-08, `90093c1`）**: 加速度-姿勢 χ² 検定のしきい値 `accel_att_noise` が 0.06 と厳しすぎ、観測を71%の頻度で棄却していた。0.8 へ緩和して解消。
- **決定論的起動の確立**: 同一入力なら毎回同じ結果になる性質を確立し、以後の `sf sils regression`（CI）の土台になった。
- **VPython 版シミュレータへの移行**: `simulator/vpython/` への Phase 1〜5 移植が 2026-01 に完了している。

原文は `git show archive/2026-09-13:simulator/sils/RESET_PLAN.md`／`:docs/plans/simulator-migration.md` で参照できる。

---

<a id="english"></a>

> Established: 2026-07-22. Fully revised: 2026-09-08. Reason for revision: The first edition was written around a three-tier framework — "Tier 1: design-oriented linear model / Tier 2: log-driven replay / Tier 3: SILS" — but this was not the organisation the designer intended; it lined up things with different axes — design tools (models, analysis methods) and an execution environment (SILS) — in a single row. This revision treats the roles the designer assigned to each simulator as authoritative and abolishes the three-tier framework. The SILS model-match pass/fail check, the discipline, and the improvement backlog are carried forward as-is.

## 1. Overview

### About This Document

This document defines the role and implementation method of each simulator in the StampFly Ecosystem (SILS, the VPython version, and the Genesis version), how the shared physical parameters are handled, how real-flight data is used, the pass/fail criteria the SILS plant (the physical model of the controlled object) must satisfy, and a comparison of physics engines for future reinforcement learning work. It is the single source of truth for the simulation policy; when it conflicts with another document, this document is corrected first.

### Target Audience

- Developers who build and modify SILS and the simulators
- Control designers who want to back up control-parameter changes with SILS or analysis
- Educators who use the simulators as teaching material, and researchers considering applications to reinforcement learning

### Why This Document Is Needed

With three simulators, the questions "why are there several?", "which one is used for what?", "where does the physics come from?", and "which one runs the same code as the real vehicle?" inevitably come up. The purpose of this document is to put the answers in one place and keep documents and implementations from diverging from it.

## 2. The Three Simulators and Their Roles

| | SILS | VPython version | Genesis version |
|---|---|---|---|
| **Role** | A mechanism for **bringing firmware development and control-system implementation to a reasonable degree of completion on the desk (without flying)**. Verification and pass/fail checking before flight | **For practice**. Relatively simple, readable Python code for learning "how to build a simulator" — physics engine, 3D visualization, sensor models, and so on | Kept as an option because it is judged to be easy to use for **reinforcement learning** |
| **Running control code** | The same C++ firmware that is flashed onto the real vehicle (unmodified) | Control law ported to Python | Control law in Python |
| **Physical model** | MuJoCo (an external physics engine; **used only for physics computation**) plus in-house motor, sensor, and wind models | In-house 6-DOF rigid-body model (Python) plus a sensor model | Genesis (an external high-precision physics engine, GPU-parallel) |
| **Visualization** | `sf sils gui` (browser-based; 3D via three.js and graphs via Plotly). Review videos are generated afterward with MuJoCo's Python renderer (`--video`) | VPython (browser 3D) | Genesis's rendering |
| **Input** | Scenario `.scn` files (time series of stick input, disturbances, and faults), keyboard piloting | USB HID joystick (AtomS3 + Atom JoyStick) | Scripts |
| **Pass/fail check** | Automatic judgment via `.expect` files, `sf sils regression` (CI) | None | None |
| **Location / entry point** | `simulator/sils/`, `sf sils build/scenario/gui` | `simulator/vpython/`, `sf sim run vpython` | `simulator/genesis/`, `sf sim run genesis` |

### Why One Is Not Enough

"Run the real vehicle's firmware as-is to verify it," "read the internals to learn how it's built," and "run reinforcement learning" are different requirements that a single implementation cannot satisfy at once. SILS gives top priority to determinism and identity with the real firmware, the VPython version gives top priority to readability, and the Genesis version prioritizes GPU parallelism and compatibility with learning. All three share the physical parameters in §3, and `sf params check` detects discrepancies.

### How SILS Is Realised

- **Firmware is unmodified**: The source of `firmware/vehicle` (and `workshop`) is compiled for the PC as-is. Not only estimation and control but also the state machine and failsafe logic are identical to the real vehicle (Code Identity). Parameters are also read from the same table (Parameter Identity).
- **In place of the OS**: Instead of ESP-IDF / FreeRTOS, the firmware runs on host-side compatibility stubs (`compat/`) and a deterministic pseudo-RTOS (`rtos/`) — a discrete-event scheduler with a single token and a virtual clock. The same input always produces the same result.
- **Controlled object (plant)**: On top of MuJoCo's 6-DOF rigid-body model, in-house motor (electromechanical ODE), sensor, and wind models are layered (`physics/`, `plant/`), running in step with the firmware at 400 Hz. **MuJoCo is used only for physics computation, not for rendering during execution.** MuJoCo's interactive viewer is an optional build flag (`-DSILS_MUJOCO_VIEWER=ON`) for visually inspecting the model file, and plays no part in scenario execution.
- **How tests are given**: Stick input, disturbances, and faults are written as a time series in a scenario `.scn` file, and PASS/FAIL is judged automatically against the pass criteria in an `.expect` file. `sf sils regression` detects regressions in CI.
- **Learner code runs on the same ground**: In the `workshop` target, `user_code.cpp` closes the loop against the same plant (`sf lesson sils`).
- **What it cannot do**: Contention between multiple tasks (concurrency contention) and the physical layer of actual WiFi / ESP-NOW cannot be reproduced, in principle, because of the structure that folds processing into a single loop for the sake of reproducibility (the archived `RESET_PLAN.md` §11, deleted 2026-09-13, tag `archive/2026-09-13`; see §10). These can only be checked on the real vehicle.

### Design and Analysis Tools Are Not Simulators

The following two used to be called "Tier 1" and "Tier 2," but they are design and analysis tools, not execution environments that show the vehicle moving. This document treats them separately.

| Tool | Content | Purpose | Location / entry point |
|---|---|---|---|
| Design-oriented linear model | A low-order transfer function $G(s)$ identified from real flight logs | Gain design, loop shaping, specification-based automatic tuning | `tools/sysid/`, `sf sysid fit` / `rate-fit` / `rate-tune` |
| Log-driven replay | Closed-loop replay that drives the identified model and a control law ported to Python with disturbances and commands reconstructed from real-vehicle logs | A/B comparison of parameter changes | `analysis/scripts/` |

## 3. Shared Physical Parameters

The single source of truth for the vehicle's physical parameters — mass, inertia, thrust coefficient $C_T$, counter-torque coefficient $C_Q$, and so on — is `control/models/stampfly_physical.yaml`. The firmware (the `generated_params` family of headers), the SILS plant, the VPython version, the Genesis version, and `docs/architecture/stampfly-parameters.md` are generated from it or hand-copied from it, and `sf params check` detects discrepancies in the hand-copied values. The measurement history of the values and the rationale for adopting them are kept in `stampfly-parameters.md`.

Note: `sf params check` only checks that hand-copied values match; it cannot detect **a mismatch in model structure**, such as "the firmware's static motor curve and the SILS plant's ODE represented different motors" (discovered 2026-08-22). Such mismatches are confirmed numerically by the pass/fail check in §5.

## 4. Handling Real-Flight Data (Startup Phase → Model Fidelity Phase)

| Period | Phase | Handling of real-flight data | Basis document |
|---|---|---|---|
| Through 2026-06 (before first flight; SILS startup phase) | Clean-slate rebuild of a physics-based SILS | Real-flight data not required. Mechanical verification against the physical model's true values (the old M7/M8 real-log replay and differential diagnosis is discontinued) | §10 (Startup-Phase History) |
| From 2026-06 (after real flight; Model Fidelity phase = present) | development_roadmap Phase 3–5 | Identify the linear model from real-flight logs, perform A/B comparison via log-driven replay, and calibrate the SILS plant. Replaying and cross-checking against real-flight logs is not a policy violation — it is precisely the work of Phase 5 itself | development_roadmap Phase 5 |

Note: the principle of not depending on the internals of an algorithm, depending on the interface rather than the implementation (the former RESET_PLAN Policy 2; see §10) remains valid regardless of phase; this document does not change it.

## 5. SILS Model-Match Pass/Fail

Thanks to Code Identity, the same identification pipeline used for real-vehicle identification (`sf sysid rate-fit`, etc.) can be applied as-is to logs generated by SILS.

**Added 2026-09-11 (flight-log format unification):** SILS and the real vehicle now write logs in the same "StampFly flight-log bundle" format (extension `.sflog.zip` — a zip-format log file bundling one flight's or one run's sensor signals; the authoritative spec is `protocol/spec/flight_log.yaml`). This extends Code Identity (the property that the control/estimation source code is identical between the real vehicle and SILS) beyond the identification pipeline to the log format itself: `sf sysid fit`/`rate-fit` and `sf log viz`/`analyze` now run unchanged on either a real-vehicle log or a SILS log. The only streams unique to SILS are `truth.csv` (the ground-truth position/attitude/velocity/angular-rate computed by MuJoCo, the external physics engine used only for physics computation) and `events.csv` (a time-stamped record of the disturbances/faults the scenario injected). A cross-check using the roll-axis rate-step scenario `sysid_roll_step.scn` (ACRO-mode angular-rate doublets, 13 s) exercised this path and found: `sf sysid fit --axis roll --mixer vehicle` identified a roll torque-to-angular-acceleration gain $K=99453$ rad/s² per N·m (8.9% below the design value $1/I_{xx}=109170$), a motor effective time constant $\tau_m=14$ ms (the SILS motor ODE's own effective time constant is about 16 ms; the identification tool's default reference is 20 ms), and a coefficient of determination (a goodness-of-fit measure, closer to 1 is better) $R^2=1.00$.

**Procedure:**

1. Perform excitation equivalent to `rate-excite` inside SILS
2. Apply the same identification pipeline used for the real vehicle
3. Extract $(b, L, T)$
4. Compare against the real-vehicle identified values

**Pass criteria** (reusing the tolerances from development_roadmap Phase 3):

| Metric | Tolerance |
|---|---|
| Step-response rise time constant | ±20% |
| gyro RMS | ±50% |

This gate is built into the SILS regression test (an automated test for detecting regressions), and the effect and degradation of every subsequent plant improvement is judged numerically each time.

Gate pass/fail is judged independently per axis. **Roll/pitch reached the passing range in the 2026-07-26 measurement (below) (yaw has not yet reached it).** Confidence in using SILS for gain verification has improved for the roll/pitch axes, but **until all axes pass**, verification using the perturbation family from §5 (torque authority $\in[0.4,0.7]$, dead time $L\in[8,15]$ ms, venue-scale disturbance 0.2〜1 Hz) is used together with SILS verification, maintaining the principle of "no SILS-only optimization." There is a lesson learned that directly optimizing against the SILS turbulence bench once converged on a gain whose phase margin went negative on the real vehicle (`firmware/vehicle/docs/control_theory_overview.md` §5.4: a gain optimized to Td=0.08 on SILS diverged to a phase margin of −375° on the real vehicle).

> **First measurement (2026-07-22, `sf sils sysid-gate`, old plant with zero dead time and a static motor curve):** All axes FAIL — roll b +39.6% / L_total +39.0%, pitch b +109.7% / L_total +19.3%, yaw b −61.4% / L_total +95.7%. The **structure** of the delay is opposite to the real vehicle: SILS is dominated by a first-order lag (T≈20ms=motor_tau, L≈1.5ms), while the real vehicle is dominated by dead time (L≈11〜16ms, small T). With `--motor-delay 10`, L moves from 1.5→10.6〜12.2ms as designed, but L_total worsens to around 27ms — matching requires simultaneous adjustment with backlog #2 (moving to a motor ODE) and #3 (coefficient recalibration), not the delay alone. Note that the real-vehicle reference value for yaw, derived from a 3-parameter fit, is the weakest (see the note in `analysis/reports/rate_sysid_reference/README.md`).
>
> **Second measurement (measured 2026-07-26, ODE plant, `sf sils sysid-gate`):** roll: b +27.2% / L_total +12.7%, **PASS**; pitch: b +26.6% / −1.5%, **PASS** (coherence 0.97〜0.99, good). yaw: b −18.2% is within tolerance, but L_total is −100% (identification degenerate), **FAIL** — a structural problem in which the implemented counter-torque zero ($\tau_z\approx45.7$ ms, a 3.5 Hz lead within the control bandwidth) cannot be represented by the current 3-parameter $(b,L,T)$ fit. Note that the real-vehicle reference value itself is also a weak reference, being a 3-parameter fit with coherence 0.44 (to be addressed in backlog #11).

## 6. Discipline That Does Not Change With Phase

- **Control parameter changes must always be backed by numerical simulation using real flight logs.** Do not propose a change based only on a qualitative guess such as "shortening Ti should improve it." If the simulation shows the change is counterproductive, do not propose it (the iron rule in `control_theory_overview.md` §5.5).
- **Do not optimize for a single nominal-model point.** The real vehicle drifts between sessions (there is an actual case where, with the same gain, the reference value in the 5–8 Hz band varied by a factor of 2.4〜2.7). Maintain torque authority $\in[0.4, 0.7]$, dead time $L \in [8, 15]$ ms, and venue-scale disturbance 0.2〜1 Hz as a **perturbation family**, and require that adopting a gain not degrade performance across the whole family.
- **SILS's fundamental limitations cannot be verified with SILS.** Concurrency contention (contention arising from multiple tasks running at the same time) and the physical layer of actual WiFi/ESP-NOW cannot be reproduced, in principle, because of the structure that folds processing that is inherently concurrent into a single loop for SILS's reproducibility (the former RESET_PLAN §11; see §10). Verification of real-vehicle concurrency is carried out separately.

## 7. Physics Engines for Reinforcement Learning: MuJoCo vs Genesis

Going forward, the choice of "MuJoCo or Genesis" will come up for reinforcement learning. This section organizes their positioning in this repository and the points to consider when deciding. The figures and support status reflect the understanding as of September 2026; re-check against the latest version at the time of adoption.

| Aspect | MuJoCo | Genesis |
|---|---|---|
| Developer / implementation | Google DeepMind. C implementation with Python bindings. Apache-2.0 | Genesis-Embodied-AI (a university/industry collaboration). Python/PyTorch implementation. Apache-2.0 |
| Execution form | Basically sequential CPU execution. Large-scale GPU parallelism is provided by a separate implementation, MJX (the JAX version) | Assumes GPU parallelism. Advances thousands of environments in a batch, and is differentiable |
| Scope of physics | Centered on rigid bodies, joints, and contact | Multiple physics domains in addition to rigid bodies — fluids, soft bodies, particles, etc. (coverage varies by version; needs checking) |
| Determinism / reproducibility | Deterministic on a single thread (the property SILS requires) | With GPU parallelism, results can vary with computation order (needs checking) |
| Reinforcement-learning ecosystem | Mature. Many examples such as Gymnasium, dm_control, MuJoCo Playground | New (released December 2024). Training examples are bundled, but the surrounding ecosystem is still developing |
| Current usage in this repository | SILS's physics (kept in step with the real firmware via the C API). Not used for rendering | `simulator/genesis/` (Python control law; source of the motor ODE) |
| Connection to the real firmware | Already connected via SILS (identical C++ source) | There is no mechanism to connect it. A separate process is needed to transfer a learned policy to the firmware |
| Where it fits | When you want to verify a policy under the same physics as the real firmware, or want to run reliably on CPU | When you want to train thousands of instances at once, or want to use differentiability |
| Challenges | Large-scale parallelism requires setting up MJX separately, and it cannot be connected to the C++ firmware | Determinism and maturity. There is no yardstick equivalent to SILS's pass/fail check |

Policy: a realistic division of labor is to run training with Genesis (or MJX) and verify the resulting policy with SILS (MuJoCo physics, real firmware). The physical parameters of both are kept aligned via the single source of truth in §3.

## 8. SILS Plant Improvement Backlog (Priority Order)

| # | Task | Basis / target value | Status |
|---|---|---|---|
| 0 | Implementing the model-match gate (§4) | The yardstick for every improvement. Highest priority | **Implemented (2026-07-22)** — `sf sils sysid-gate` |
| 1 | Adding dead time | Current SILS effective lag ~5 ms vs. real vehicle 8.4〜14.7 ms. Measure SILS's current $L$ with the gate, and make it possible to configure the difference as a transport delay in the duty→thrust path | **Implemented (2026-07-22, default OFF)** — `sf sils scenario --motor-delay`. **Found that after moving to an ODE, no additional delay is needed (stacking it worsens L_total, measured 2026-07-26). Default OFF is maintained** |
| 2 | Moving the motor model to an ODE | Port the electromechanical ODE from `simulator/genesis/motor_model.py`, $\dot\omega = \bigl[-(D_m + K_m^2/R_m)\omega - C_Q\omega^2 - Q_f + K_m V/R_m\bigr]/J_{mp}$, to SILS. Measured values: $J_{mp}=1.375\times10^{-8}$ kg·m², $C_Q=4.10\times10^{-11}$ N·m·s²/rad², $\omega_{hover}\approx3670$ rad/s, hover-point effective time constant $\tau_{eff}\approx17.5$ ms | **Implemented (2026-07-26)** — RK4 integration with the measured family ($C_Q=4.10\times10^{-11}$, $J_{mp}=1.375\times10^{-8}$, $K_m=5.682\times10^{-4}$, $R_m=0.593$, $D_m\approx0$, $Q_f=9.507\times10^{-6}$). Counter-torque is $C_Q\omega^2+J_{mp}\dot\omega$ (physically reproducing the yaw zero). (Added 2026-08-22: this measured family, `measured_2026_07`, turned out to describe a different motor from `legacy_motor_curve`, the static curve the firmware uses. Unification awaits the bench measurement in #3) |
| 3 | Recalibrating the $C_T$/$C_Q$/thrust_efficiency triplet (firmware Ct switchover) | The $C_T$ measured on the 2026-07-15 thrust stand was withdrawn (2026-08-03) because it lacks a valid simultaneous measurement of voltage, rotation speed, and thrust for the new propeller. At the time of withdrawal, the firmware (`MOTOR_CT` in `actuator.cpp`) numerically matched the provisionally adopted value $C_T=1.00\times10^{-8}$, so this task was judged resolved at the time — but that judgment was wrong (see the status column for details) | **Reopened (2026-08-22)** — The "resolved" judgment of 2026-08-03 was wrong. The retirement of the static curve Am/Bm/Cm (2026-07-26) applied only to the SILS-plant side; the firmware's thrust→duty path (`thrustToDuty()`, `firmware/vehicle/components/sf_actuator/actuator.cpp`) still uses the static curve, and the divergence between the firmware and the SILS plant had not disappeared. The firmware's static curve is an algebraic restatement of the SSOT `legacy_motor_curve` ($R_m=0.34$, $K_m=6.125\times10^{-4}$, $C_Q=9.71\times10^{-11}$) — exactly reproduced via $A_m=R_mC_Q/K_m$, $C_m=R_mQ_f/K_m$ — while the SILS ODE plant is `measured_2026_07` ($R_m=0.593$, $K_m=5.682\times10^{-4}$, $C_Q=4.10\times10^{-11}$): the two describe different motors. At the hover point, the plant produced 1.252 times the firmware's commanded thrust, and with `hover.thrust_corr=1.12` applied on top, this became 1.402 times the vehicle weight in thrust — but the plant's `thrust_efficiency` happened to cancel this inconsistency. db65e0e5 (2026-08-03, withdrawal of Ct) removed that cancellation, and between 2026-08-03 and 2026-08-22, 20 of the 33 `sf sils regression` scenarios were failing (undetected by CI because main had not been pushed). **2026-08-22 fix:** folded the `hover.thrust_corr` factor of 1.12 into the firmware's static curve ($A_m$×1.12, $B_m$×$\sqrt{1.12}$, $C_m$ unchanged — a transformation that leaves the duty output identical across the whole thrust range), and restored the `hover.thrust_corr` default to 1.00. Added `flight_anchored_motor_curve` to the SSOT and made the firmware a copy of it (the measurement record of `legacy_motor_curve` is kept unchanged in value). Set the SILS plant's `thrust_efficiency` to 0.7133 (=1/1.402, an explicit coefficient representing the difference between the ideal ODE and the flight-proven firmware + real vehicle). Recalibrated the STABILIZE throttle in SILS scenarios by ×0.8386 (ALT/POS are unaffected since they command climb rate). Result: `sf sils regression` recovered to 28 PASS + 5 KNOWN-FAIL (33 total). **Unresolved:** it remains undecided which of `legacy_motor_curve` / `measured_2026_07` reflects the actual new propeller (awaiting a bench measurement of V-ω-T simultaneously across all three quantities — the same as the follow-on task). `thrust_efficiency=0.7133` is a provisional value until that measurement is complete; afterward, the two families should be unified into a single motor model and `thrust_efficiency` should become 1.0 |
| 4 | Motor dead zone / low-duty nonlinearity | Needed to reproduce the real vehicle's ~0.9 Hz limit cycle. Identify from bench data in `analysis/datasets/motor_sweep_20260714/` (3 units, 2 conditions with/without propeller) | Not started |
| 5 | Adding aerodynamic drag | Currently the MuJoCo plant has zero drag. Use the value identified from real logs by `sf sysid drag` | Not started |
| 6 | Flow-quality model (N3) | A fixed SQUAL (the optical-flow surface-quality indicator) of 100 with no noise was a blind spot behind the POS_HOLD divergence on the first flight (`firmware/vehicle/docs/poshold_journey.md`: a concrete example of the blind spot "even with Code Identity, it doesn't work on the real vehicle"). Flow/Mag noise is already planned as the N3 tier for a later stage (recorded in the former SILS rebuild plan; see §10) | Not started |
| 7 | Measurement-based calibration of the battery-sag $R_{int}$ | Voltage-dependent thrust error is the main cause of altitude wobble (corr(V, altitude std)=−0.78, `analysis/reports/poshold_3min_battery_wobble_20260627.md`). The sag model itself is already implemented and is default ON in the closed-loop emulator (2026-06-07, `b8fd27ea`). The only remaining work is measurement-based calibration of the internal resistance $R_{int}$ (the current value of 0.1 Ω is a provisional value taken from the VPython version) | Not started |
| 8 | Re-identifying the N1 vibration coefficients from current vehicle logs | The current per-axis coefficients (`vib_accel_k`/`vib_gyro_k`) are seed values derived from the hover02 log of the old airframe (legacy `firmware/vehicle`) | Not started |
| 9 | Real-vehicle log input replay (equivalent to `sf sils replay`) | Convert 50 Hz stick input from WireControl (the telemetry control-input struct) into a `.scn` scenario and compare it against the real log on the same plot. The key piece for reproducing, in SILS, the results of real-log replay (analysis) | Not started |
| 10 | Maintaining consistency of related documents | When a discrepancy is noticed between this document and development_roadmap, update this document first | Ongoing |
| 11 | Extending the yaw-axis gate to a 4-parameter model | Extend the `rate_sysid` yaw fit to a 4-parameter model that includes the counter-torque zero (`firmware/vehicle/docs/yaw_axis_model.md`), and regenerate the real-vehicle reference value (`reference.json` in `analysis/reports/rate_sysid_reference/README.md`) with the same pipeline so the comparison is made under matching conditions | Not started |
| 12 | Reconsidering firmware yaw torque authority | Reconsider the `rate.yaw.max_torque` differential margin under the new reference hover duty (≈0.7245). SILS regression's pos_flight/pos_yaw/yaw_hold are being tracked as known-fail (an xfail marker in `sf sils regression`). May share the same root cause as the real-vehicle NT Kanazawa issue (remedied 2026-07-17) | **Investigated; awaiting the owner's decision (2026-09-19)** — three candidate fixes were measured and none is adopted for now. The root cause was identified as "the mixer clips each motor's duty independently and never redistributes what it cut off at the upper rail." See "Investigation result for #12" below |
| 13 | Investigating attitude damping margin | Manifested in calib (0.6〜0.7 Hz self-excited oscillation from the interaction of injected bias with the new plant's takeoff dynamics) and commloss_land_level (insufficient roll convergence during the LANDING leveling gate). Check the relationship with backlog #4 (motor dead zone) and #5 (zero aerodynamic drag). (Added 2026-08-22: the excessive plant thrust found in #3 [1.252× the firmware's commanded thrust at the hover point, 1.402× the vehicle weight once `hover.thrust_corr` is included] may have been a contributing factor in this phenomenon; re-verify after the thrust_efficiency correction) | Not started |
| 16 | The forward-ToF device model carries no noise (a default-off simulation) | Added 2026-09-19 (jev-autopilot §4.9). `simulator/sils/devices/vl53_front_device.cpp` plus `Plant::tofFront()` let SILS simulate a forward distance, active only with `SILS_EMU_FRONT_TOF=1` (absent by default, so the regression baselines are unchanged). **Fidelity position**: maximum range 2.0 m (limited to what means anything inside the 2 m envelope radius); out of range / no target matches the hardware measurement (§4.8.6: status=255, value 0). **No observation noise is added** — with the downward ToF's N2 noise off by default, giving only the forward part noise would leave the two sensors at mismatched fidelity; the right place for it is the N3 tier together with #6 (flow-quality model). The measured bias is +17 mm against a true 1.0 m, from the sub-bin histogram resolution shared with the downward model, not from noise | Not started (with #6, in the N3 tier) |
| 14 | `flow_vel_scale` (`SILS_EMU_FLOW_SCALE` / `sf sils scenario --flow-scale`) does not reach the flying path | Found 2026-09-19. `Config::flow_vel_scale` is read only by `Plant::flow()`, and the only caller of that is `simulator/sils/smoke/plant_smoke.cpp`. In closed-loop flight the flow the firmware receives is synthesized through `virtual_board.cpp`'s `sils_board_spi_transfer()` → `sils_pmw3901::set_motion_from_velocity()`, which never consults the multiplier. Measured (0.060 N sideways wind, 35 s, real-time emulator): 359 of 1388 cycles classified as drifting with the knob unset, 361 of 1389 with `--flow-scale 0.35` — within run-to-run jitter. The knob was built for the hikoki64 §3.3 injection study (the same line of work as #6's flow-quality model); **used as closed-loop fault injection it produces an experiment that believes it injected a fault and did not.** The fix is likely just applying the same multiplier on the synthesis side (pmw3901_device), but it must be shown not to break the byte-identical default-1.0 path that `sf sils regression` baselines on | Not started |

### Investigation Result for #12 (2026-09-19, awaiting the owner's decision)

Three candidate fixes were implemented and measured in separate worktrees, then put through review and three lines of adversarial checking. **The conclusion is that none of the three is adopted for now**, because what separates them is a control-law design decision that belongs to the firmware's owner — the person who holds the flight record and the real-vehicle logs.

#### Root cause (identified by measurement)

**The mixer (`firmware/vehicle/components/sf_actuator/actuator.cpp`) clips each motor's duty independently and never redistributes what it cut off at the upper rail.** Mean lift is therefore lost structurally whenever it saturates. The numbers close the chain:

- Yaw leverage is $0.25/\kappa = 61$ N per N·m ($\kappa = 4.10\times10^{-3}$).
- A yaw torque at the cap, $1.226\times10^{-3}$ N·m, asks one motor for $0.0748$ N on top of hover — **73.6%** of its hover share ($0.1016$ N). The required duty is **1.0522**, above the rail of 1.0, so **the present cap is structurally unreachable**.
- The excess is clipped per motor at 1.0 and never handed back to the motors below the rail, so the four-motor mean thrust drops and the craft sags.
- Measured on the reference scenarios (HEAD 8a956654): in `yaw_crossaxis` the fraction of samples pinned at the torque cap and the fraction pinned at the duty rail are **both exactly 0.8156** — the same samples. Torque saturation is converted straight into duty saturation.

Reproduce with `simulator/sils/scenarios/yaw_crossaxis.scn` and `yaw_cw90_low.scn` (**reference only, no pass/fail criteria**; see "Reference scenarios" in TEST_MATRIX.md §2).

#### The three candidates and why none is adopted

| Candidate | Content | Measured | Why not adopted |
|---|---|---|---|
| **A** Cap with a headroom rule | `rate.yaw.max_torque` $1.226\times10^{-3}$ → $8.331\times10^{-4}$ (allot 50% of a motor's hover share to yaw) | The fall is gone (`yaw_crossaxis` `alt_min` 0.006 → 0.294 m), but `stab_flight` goes PASS → FAIL (`att_rmse` 0.0489 → 0.0594 against a 0.05236 threshold), the targeted known-fails (pos_flight / pos_yaw) are **not fixed** (`duty_max` still 1.0000), and the torque-cap pinning fraction gets **worse**, 0.8156 → 0.9719 | It changes a control limit, so §6's discipline (back it with numerical simulation on real flight logs) applies. The three replay logs it needs (NT Kanazawa: `stampfly_udp_20260627T020050` / `T164611` / `T165713`) are **not in the repository** (`logs/*` is gitignored; `git ls-files logs/` holds only `.gitkeep`), so the backing cannot be obtained. Worse, against the retained measurement in `analysis/scripts/yaw_nt_kanazawa/kappa_fix_sim_results.json` — a worst-case disturbance peak of 1.54 mNm — the new cap can deliver only **0.74 mNm of true torque (0.48×)**, working against the remedy for that accident |
| **B** D-term filter | Reduce the high-frequency gain of the yaw-rate D term | **The premise was shown false by measurement and the implementer rejected the candidate.** The D term's contribution to a 1-LSB alternation is $3.4\times10^{-6}$ N·m (0.28% of the cap) and is not what saturates. The incomplete derivative's high-frequency gain $K_p/\eta = 8.0$ is independent of $T_d$, and `alpha=1.0` is the **intended design**, stated at `detailed_design.md:295` | There is nothing to fix. The alternative tried (backward difference) detunes each axis differently — roll falls to 16.7% of design — pushing `stab_flight`'s `att_rmse` to 0.0885, 69% over the threshold |
| **C** Priority-ordered mixer desaturation | Allocate in thrust [N] space and shrink only the differential groups to fit the rails, yielding yaw first. `actuator.cpp` alone | The fall is gone (`yaw_crossaxis` `alt_min` 0.006 → 0.211 m, `yaw_cw90_low` 0.007 → 0.194 m, `api_flight` 0.359 → 0.946 m) and all 34 regression verdicts are unchanged (28 PASS + 5 KNOWN-FAIL + 1 SKIP) | **Four blocker-level findings** (below) |

#### Adversarial findings against candidate C (four, all blockers)

1. **A bias combination makes it unable to take off.** With an accelerometer X bias of 0.12 and a yaw-gyro bias of 0.02 applied **together**, the vehicle fails to take off and emergency-disarms (`calib`'s `Takeoff complete` PASS → FAIL, `alt_max` 0.4694 → 0.0189 m). Single-axis sweeps (6 yaw conditions, 7 accelerometer conditions) do not reproduce it, so this is a **new defect specific to the combination**. Real inertial-measurement-unit biases always appear on both axes at once, which makes this a direct risk for real-vehicle hover testing. Because `calib` was already KNOWN-FAIL, **the regression numbers do not move at all and this defect goes undetected**.
2. **The shrinking does not do what its comment says.** The comment states that roll/pitch are shrunk only when zeroing yaw is still not enough, but the code shrinks them unconditionally: with a yaw command of 0 it still cuts roll/pitch to 0.30〜0.54× (42% of a 200,000-point sweep). There is also a region where **attitude torque becomes exactly zero once total thrust exceeds 1.2× hover** (15.14% of a 300,000-point sweep, at 1.21〜1.80× hover), where the old mixer did produce attitude torque. This violates invariant INV-2 (the pilot's attitude authority) in `architecture.md`.
3. **It effectively nullifies the NT Kanazawa remedy.** That remedy depends on being able to deliver yaw all the way to the duty-saturation range (1.39〜1.85 mNm), but yielding yaw first puts a structural ceiling on deliverable yaw torque: 1.00 mNm at 3.7 V (was 1.33) and 0.56 mNm at 3.3 V (was 1.11). Across the real vehicle's hover voltage band, 3.65〜3.86 V, it **falls short of what all four recorded events required**. This is the same regression that got candidate A rejected ("lowering the cap works against the remedy"), produced without touching `params.cpp`. The argument "the cap value was not lowered, so the rule does not apply" does not hold, because the yaw torque actually deliverable *is* lowered.
4. **The real-log discipline does apply.** `firmware/vehicle/docs/architecture.md:202` explicitly requires, as a precondition for the two-stage mixer separation that candidate C implements, "verify by simulation against the SILS regression test and the effect on existing lesson gains — the CLAUDE.md 'control parameter change' principle." "It does not touch `params.cpp`, so it is out of scope" is incorrect.

Candidate C also **does not fix the cross-axis fall under manual POS_HOLD from the transmitter** (it reaches the same emergency disarm) and raises the touchdown speed by 32% (0.76 → 1.00 m/s). Only API-driven autonomous flight is fixed.

#### The conflict — this is what needs the owner's decision

**"Preserve lift" and "preserve yaw authority (the disturbance remedy)" are in direct conflict.** What can be delivered at saturation is finite: yield yaw and lift is preserved but the yaw torque available against disturbance shrinks; push yaw through and yaw authority is kept but lift is lost and the craft sags. Which one takes priority is a control-law design decision, and it belongs to the firmware's owner, who holds the flight record (87 real flights in this lineage) and the real-vehicle logs.

#### Decision requested from the owner

| Option | Content | What it also requires |
|---|---|---|
| **(a)** Lift-first desaturation | Fix candidate C and adopt it. The mandatory fixes are the two in finding 2 (do not cut roll/pitch when yaw is zero; keep attitude torque when total thrust is exceeded) and the takeoff blockage in finding 1 | A **redesign** of how yaw disturbance is handled (even fixed, yielding yaw first lowers disturbance authority, so NT Kanazawa-type disturbance needs separate provision). Plus real-log backing and staged real-vehicle checks starting from low-altitude hover |
| **(b)** Keep the present behaviour | Leave the mixer alone and avoid the problem operationally by gaining altitude before turning (which `api_flight.scn`'s `up 70` already does) | Make that constraint explicit on the autopilot side. The yaw-rotation search in `docs/plans/jev-autopilot.md` §4.9 stays on hold |
| **(c)** Another approach | For example, returning what was clipped at the top to the motors below it (redistributing while holding total thrust). None of the three candidates tried this | The same real-log backing |

#### How to reproduce

- Reference scenarios: `simulator/sils/scenarios/yaw_crossaxis.scn` (`--duration 40000000`, window 26-34 s) and `yaw_cw90_low.scn` (`--duration 36000000`, window 18-28 s). **Neither has an `.expect`**, so both are outside `sf sils regression`.
- The metrics that characterise the fall (duty-rail pinning fraction, torque-cap pinning fraction, yaw-rate root-mean-square error) were computed this time with a throw-away test program. Per the policy of not adding standalone scripts under `tools/` (PROJECT_PLAN §8), **if they are to be kept, the right place is the existing metric set of `sf sils scenario` (`_traj_metric`), as e.g. `duty_sat_frac`** (a proposal only; not implemented).
- If the three replay logs become available, `analysis/scripts/yaw_nt_kanazawa/torque_budget.py` can be used — but it hardcodes another machine's absolute path (`LOG_DIR`) and has no argument parsing, so it must first be changed to take the input path as an argument.

#### A separate problem found along the way

**`actuator.cpp:23`'s `@design detailed_design.md §5 — X-quad mixer` points at a section that does not exist.** §5 of `detailed_design.md` is "Estimation Interface Definition," not a mixer section. The tag's verdict status reads `[OK]`, but since the reference does not resolve it is not in fact `[OK]`. **Whether to add a mixer section to the design document is a change to the document's structure and therefore the owner's decision**, so this update only records the discrepancy.

## 9. Related Document Map

| Document | Authoritative for |
|---|---|
| This document | Simulation policy (the role of the three simulators, how SILS is realised, the single source of truth for physical parameters, the SILS model-match pass/fail check, the backlog) |
| `simulator/README.md` | How to use the VPython and Genesis versions |
| `simulator/sils/README.md` | How to use the SILS bench (targets, scenarios, GUI) |
| `simulator/sils/RESET_PLAN.md` (deleted 2026-09-13, tag `archive/2026-09-13`) | Deleted. The SILS bench's structure and startup history are summarized in §10 of this document |
| `firmware/vehicle/docs/development_roadmap.md` | The overall development process (Phase 0–6) |
| `firmware/vehicle/docs/control_theory_overview.md` | Control-design discipline and lessons from identification |
| `firmware/vehicle/docs/noise_and_vibration_model.md` | Sensor noise model (N0–N2, N3/N4 planned) |
| `firmware/vehicle/docs/yaw_axis_model.md` | The yaw-axis model |
| `docs/architecture/stampfly-parameters.md` | Physical parameter values and measurement history |
| `analysis/scripts/alt_dob_design/README.md` and others in `analysis/reports/` | Records of log-driven replay work |

## 10. Startup-Phase History (Record)

- **Full rebuild of the old SILS (deleted 2026-05-31, then rebuilt)**: The old SILS (`quad_model`, `sils_main.cpp`, etc.), based on real-log replay and differential diagnosis, was removed and rebuilt as a physics-based, MuJoCo, algorithm-independent SILS through stages E0 (clean slate) through E8, followed by P1 (skeleton) through P10 (verification-coverage measurement), which added the CLI, dashboard, shareable review videos, sensor noise N0–N2, disturbances, full flight-mode coverage, and collision resilience.
- **Plant timebase bug (2026-06-03, `cea0d8cf`)**: Physics updates were advancing at 3x real time, so hover could not be sustained. Fixed by switching to a fixed 4000 Hz timestep accumulator (details in `simulator/sils/docs/plant_timebase_bug.md`, which is not being deleted).
- **χ² gate over-rejection (2026-06-08, `90093c1`)**: The accel-attitude χ² test threshold `accel_att_noise` was too strict at 0.06, rejecting observations 71% of the time. Relaxed to 0.8, resolving it.
- **Deterministic boot established**: The same input now always produces the same result, becoming the foundation for `sf sils regression` (CI).
- **Migration to the VPython-based simulator**: Phase 1–5 porting to `simulator/vpython/` completed in 2026-01.

The original text can be retrieved via `git show archive/2026-09-13:simulator/sils/RESET_PLAN.md` / `:docs/plans/simulator-migration.md`.
