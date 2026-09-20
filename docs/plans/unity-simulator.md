# Unity 版シミュレータ（WebGL）追加計画

状態: **実装中**。作成 2026-09-20、最終更新 2026-09-21。

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。

## 1. 概要

### このドキュメントについて

ブラウザで動く Unity 版シミュレータを、既存の 3 つのシミュレータに続く **4 つ目**として新設するための計画である。実装は段階に分け、各段階の合格基準を満たしてから次へ進む。段階 1（技術検証）の結果でこの計画の成否と方式が決まるため、その数値は §9 に記録してから段階 2 以降へ入る。

### 対象読者

- このリポジトリでシミュレータの実装・改修を行う開発者
- 既存の SILS（Software In the Loop Simulation）と新設分の役割の違いを知りたい利用者・教育担当者

### 位置づけ（既存の 3 つは残して併設する）

既存の SILS・VPython 版・Genesis 版は**そのまま残す**。Unity 版はそれらを置き換えない。変更で既存の動作が壊れていないかを自動で確かめる試験（再確認試験、`sf sils regression` の 34 シナリオ）と、モデル一致の合否判定は、引き続き既存 SILS が担う。Unity 版で 34 シナリオの合否を再現することは目標にしない。

`simulator/sils/` はこのリポジトリ自作の C++ 製シミュレータで、実機と同じ無改変のファームウェアを MuJoCo 物理と閉ループで動かす。その可視化（`sf sils gui`）は「一括実行 → JSON → ブラウザで再生」の方式であり、次の 3 つを持たない。

| 既存 SILS に無いもの | Unity 版で満たすこと |
|---|---|
| リアルタイム操縦 | 実行中に操縦入力を与え、その場で反応を見る |
| カメラ画像・前向き ToF（Time of Flight、光の飛行時間による測距）の模擬 | 下向き・前向き ToF、オプティカルフロー、下向き・FPV（一人称視点）カメラ |
| 障害物のある 3D 環境 | 障害物を置いて衝突とセンサに反映する空間 |

シミュレーション全体の方針は [`../architecture/simulation-policy.md`](../architecture/simulation-policy.md) を基準とする。同文書 §2 の比較表を 4 列（SILS・VPython 版・Genesis 版・Unity 版）に広げるのは、Unity 版の中身が入る段階で行う。

## 2. 要件

| 項目 | 内容 |
|---|---|
| 位置づけ | 既存の 3 つを残して併設する。Unity 版で 34 シナリオの合否を再現することは目標にしない |
| 配布先 | まず WebGL だけ。**対応ブラウザは Chrome のみ**とし、他のブラウザでの動作確認はしない。macOS アプリの配布は後回し |
| ファームウェア | C++ のまま無改変で使う（C# に書き直さない。実機とのコード一致を保つ） |
| 物理 | 剛体の運動と接触は Unity（PhysX）。モータ・推力・電池・風・センサ合成は既存の自作 C++ を使う |
| 目的 | リアルタイム操縦／センサの模擬（下向き・前向き ToF、オプティカルフロー、カメラ）／見た目と環境 |
| 障害物 | 自由に置ける（置く・動かす・回す・寸法変更・消す・保存・読み込み）。衝突とセンサに反映する |
| プリセット | 障害物 10 種（自作の基本形状のみ、寸法変更可）と、配置済みの空間 6 個を同梱する。利用者の保存ファイルと同じ形式にする |
| 操作 | 公式 Unity CLI（`unity` コマンド）を使う。利用者向けは `sf` コマンドとして公開し、動いているシミュレータも端末から操作できるようにする |
| 開発環境 | `nix develop` ＋ flake ＋ direnv（リポジトリ直下に新設。`just`・`lefthook` も）。ESP-IDF と sf CLI は従来どおり `setup_env.sh` で有効化する |

## 3. 全体構成

```
ブラウザ（Chrome）
├─ Unity WebGL（C#）… PhysX 400Hz、描画、レイキャスト、障害物編集、UI
│     ↑↓ 物理 1 刻み（2.5ms）につき sfu_step を 1 回
└─ ファームウェア（無改変 C++）＋疑似 RTOS＋デバイスモデル＋モータ ODE 4000Hz
      = 静的ライブラリ sfu_firmware（新設の bridge/ が C ABI を提供）

端末:  sf unity build / serve / cmd … ──(ローカルサーバ経由)──> ブラウザのシミュレータ
       unity install / build / test / command … エディタの導入・ビルド・試験・操作
```

### 1 刻みの処理

`SimLoop.Update()` が自前で回す（Unity の `FixedUpdate` は使わない）。

| 順 | 担当 | 処理 |
|---|---|---|
| 1 | C# | `Rigidbody` の位置・回転・速度・角速度、前の刻みで求めた加速度計の測定値、レイキャストの ToF 距離を `SfuStepIn` に詰める |
| 2 | C++（`sfu_step`） | 状態を注入 → ファームウェアを 2.5ms 進める（その中でモータ ODE を 4000Hz で 10 回）→ 区間平均の合力・合トルクと状態を返す |
| 3 | C# | `AddRelativeForce` ／ `AddRelativeTorque` ／ 風の `AddForce` → `Physics.Simulate(0.0025)` |
| 4 | C# | 加速度計の測定値 = R⁻¹·((v後 − v前)/dt − g)。接触力が自動で入り、静止時は実機と同じ −9.81 m/s² になる |

処理が実時間に間に合わないときは、ファームウェアの刻みを飛ばさず、仮想時間を遅らせて画面に実時間比を表示する。一時停止・コマ送り・倍速（0.1〜4 倍）を持つ。

## 4. 設計上の決定

| 論点 | 決定 | 理由 |
|---|---|---|
| 既存コードへの変更 | 3 か所だけにする。(1) `rtos/scheduler.hpp` に `run_until` ／ `shutdown` の宣言を追加（定義は新規 `rtos/scheduler_step.cpp`）、(2) `plant/plant.hpp` に `SILS_PLANT_EXTERNAL` の囲みを追加、(3) `CMakeLists.txt` のソース一覧を `cmake/firmware_sources.cmake` に切り出す | マクロ未定義の既存ビルドは構文が同一で、計算結果が 1 ビットも変わらない |
| プラント | 新規 `plant/plant_external.cpp` を `plant.cpp` の代わりにリンクする。モータ ODE・電池・乱流は新規 `plant/actuator_model.hpp` に文単位で同じ式を置き、MuJoCo 版と数値一致の試験で照合する | `virtual_board.cpp` とデバイスモデルを無編集で使える |
| 座標変換 | C ABI は Unity の生の規約（左手系・Y 上・クォータニオンは xyzw）で受け渡し、変換は C++ の新規 `frames/frames_unity.hpp` に集中させる。C# 側は空間ファイル（ENU: x=東・y=北・z=上）と Unity の変換だけ持つ | 「座標変換は 1 か所」という SILS の方針に合わせる |
| 力の渡し方 | 合力・合トルク（機体座標系、10 副刻みの力積平均）を返す | 往復が 1 回で済み、モーメントアームの長さを C# に写さなくてよい |
| 再初期化 | 1 モジュール＝1 電源投入とする。同一モジュール内での再 `init` はしない。機体の置き直しはファームウェアを再起動せず、電源の入れ直しはモジュールを作り直す | ファームウェアのタスク群に静的変数が多く、無改変では初期状態に戻せない |
| スレッド | **案 2 に決定**（2026-09-20、段階 1(a) の実測で決着。§9 参照）: ファームウェアを別の wasm モジュール（`sfu_firmware.wasm`、`-sMODULARIZE -sASYNCIFY`）にし、スレッド無しの fiber 型スケジューラ（新規 `rtos/scheduler_fiber.cpp`、`emscripten_fiber_t`。既存 `scheduler.cpp` とはリンクで切り替え）で動かし、`.jslib` 経由で同期呼び出しする。**案 1**（スレッドのまま `.a` を Unity に静的リンク、`-pthread`、実験的設定、COOP/COEP ヘッダが必須）は退避先として残すが、案 2 が速度の目安を Node.js で約 100 倍・Chrome で約 17 倍上回ったため、切り替える理由は無い | 案 2 は特別な HTTP ヘッダ無しで GitHub Pages にそのまま載り、Emscripten の版が Unity に縛られず、電源の入れ直しがモジュールの作り直しで済む。「Unity に静的リンクした `.a` の中で fiber」は、Asyncify の計装が Unity エンジン全体に掛かるので採れない。実測ではその全体計装のままで目安を大きく下回り、`ASYNCIFY_ONLY` による絞り込みは不要だった |
| エディタでの開発 | 配布はしないが、開発用に macOS arm64 のネイティブプラグイン（現行のスレッド型スケジューラのまま）を作る。C# 側は `IFirmware` で WebGL 用とエディタ用を切り替える | 毎回 WebGL ビルドを待たずに再生ボタンで試せ、`unity test` の Play Mode 試験も回せる |
| 端末からシミュレータを操作する経路 | 3 層にする。土台はページ側の JavaScript API（`window.stampfly.command(json)` → `ISimCommands`）。その上に (1) URL の引数（`?world=gate_course&scn=...`。公開サイトでも使える）と (2) `sf unity serve` のローカルサーバ経由（Python 標準ライブラリ、127.0.0.1 のみ、Origin 検査つき。ページが `/api/cmd/next` を待ち受けて `/api/cmd/result` に返し、`sf unity cmd <命令>` が POST する）を載せる。`sf unity cmd` はエディタが動いていれば `unity command` へ、無ければローカルサーバへ送る | `com.unity.pipeline` はローカル HTTP で待ち受ける方式で、WebGL には届かない。サーバの前例は `simulator/sils/gui/server.py`、Origin 検査の前例は `lib/sfcli/commands/blocks.py` |
| 操縦入力 | 最初はキーボード（割り当ては `sf sils fly` に合わせ、離すと中央へ戻る）。ゲームパッドは次の段階で、`navigator.getGamepads()` を直接読む `GamepadBridge.jslib` と軸の割り当て画面を作り、実機コントローラ用の既定の割り当てを同梱する | 実機コントローラの HID（Human Interface Device）は標準のゲームパッド配置に載らず、軸の順序が実装依存である。最初の合格基準をこれに依存させない |
| 公開 | WebGL ビルドは手元で `sf unity build --release` して GitHub Release に添付し、`deploy-pages.yml` が `flash/` と同じやり方で `_site/sim/` に展開する。圧縮は Decompression Fallback を有効にする | 配信の処理に Unity もライセンス認証も要らない。GitHub Pages は `Content-Encoding` を付けられない |
| 操作の実装の共通化 | 全ての操作を C# の `ISimCommands` に載せる。画面操作・`sf unity cmd`・エディタの `[CliCommand]` が同じ実装を呼ぶ。中継の受け口は開発用ビルドだけに入れ、配布用ビルドに入っていないことを `sf unity build --release` が検査する | 経路が違っても結果が一致する |
| 空間ファイル | `*.world.json`、ENU（x=東・y=北・z=上）、単位は m と度。構造は `Schemas/world.schema.json`、検査は `sf unity world validate`（Python だけで動く） | 左手系を Unity の外へ出さない。MuJoCo 版・Genesis 版も将来そのまま読める |
| 機体の見た目 | **既存の STL 13 個へ移植済み（2026-09-21）。** エディタのメニュー `StampFly/Meshes/Rebuild Vehicle Meshes` が `simulator/shared/assets/meshes/parts/` の STL を 1 回変換して `Assets/StampFly/Runtime/Resources/StampFly/Meshes/<部品名>.asset` に書き、その `.asset` をコミットする（STL の複製は Unity 側に置かない）。変換は x の符号反転＋三角形の巻き反転＋0.001 倍で、**内容を検証して正しいことを確かめた**（符号付き体積は 13 個とも正、保存法線は信用せず巻きから計算し直す。`m5stamps3.stl` は 108 面すべてが巻きと逆の法線を保存している）。部品と色の一覧は `Assets/StampFly/Runtime/Vehicle/VehicleParts.cs`、色は `stampfly_fixed.urdf` の `rgba`。自作の基本形状（板・モータ缶 4 つ・手続き生成の 3 枚羽根）の見た目は切り替え用に残す | 当初は「出所とライセンスを確認してから移植する」としていたが、**2026-09-21 にユーザー判断でこの条件を変更し、未確認のまま移植して出所が未確認である旨を `simulator/shared/assets/meshes/README.md` に明記する方式にした。** `stampfly_v1.stl` は外部リポジトリから移されたもので、元の CAD の作者と利用条件の記載がどこにも無い。確認できたら同 README を更新する |
| Unity の CI | 最初は載せない。Unity なしで回せる検査（`sf params generate --check` に C# 生成物を含める、`sf unity world validate`、ネイティブの最小動作確認）だけ既存 CI に足す | ライセンス認証の手間と実行時間に対して見返りが小さい |

## 5. 段階と合格基準

「段階 0」「段階 1」…は本計画の作業の区切りであり、教材の学習段階（L0〜L3）とは別のものである。

| 段階 | 作業 | 合格基準 |
|---|---|---|
| 0 準備 | 本文書を置き、`PROJECT_PLAN.md`（§12 に開発環境のファイル、§14 に lefthook の検査、§15 に本文書へのリンク）と `simulation-policy.md` §9 に追記する。`flake.nix`・`.envrc`・`justfile`・`lefthook.yml` を新設する | `nix develop` で cmake と ninja が使える。既存 CI と `setup_env.sh` に影響が無い |
| 1 技術検証 | **(a) 最優先・Unity を使わず素の HTML と Node.js で**: 無改変ファームウェア＋シミュレータ＋`rtos` を Emscripten でコンパイルし、fiber 版スケジューラの最小版で `rtos_smoke` のトレースが現行と一致することを確かめる。続いて `app_main` ＋ 14 タスク ＋ `plant_external` ＋ C++ 内の簡易剛体積分を **Chrome で回して速度を測る**。同じものを案 1（`-pthread`、COOP/COEP ヘッダ付きのローカルサーバ）でも測る。**(b)** Unity WebGL から `.jslib` 経由で別モジュールの `sfu_step` を同期で呼べるか、モジュールの作り直しで再起動できるか。**(c)** PhysX で質量 37g・慣性 1e-5 桁の剛体が 400Hz で安定するか（トルク応答 ±1%、着地で振動しない、ジャイロ項 ω×Iω の扱い）。**(d)** WebGL での標準入出力（`prompt()` が開かないこと）。**(e)** Unity CLI の `install -m webgl` ／ `build` ／ `test` が手元で通るか、`unity command` がエディタの再生中に届くか。**(f)** 実機コントローラが **Chrome の Gamepad API** でどう見えるか | シミュレーション 1 秒を実時間 0.3 秒以下で処理でき、離陸してホバリングが成立する。スレッドの案を数値付きで決定する。満たさなければ Asyncify の対象を絞って再計測し、それでもだめなら案 1 に切り替える。**どちらも満たさなければ計画を見直す判断点** |
| 2 ネイティブコア | `scheduler_step.cpp`、`plant.hpp` の囲み、`plant_external.cpp`、`actuator_model.hpp`、`frames_unity.hpp`、`simulator/unity/native/bridge/`（`sfu_api.h`、ログのリングバッファ）、`sfu_bridge_smoke`（Unity なしで C++ 内の簡易剛体積分により ARM → 離陸 → ホバリング） | `sf sils regression` が 34 件とも変更前と同じ結果で、ログの sha256 が一致する。`actuator_parity_test`・`frames_unity_test` が通る。最小動作確認が 2 回走って出力が一致する |
| 3 最小の WebGL 版 | Unity プロジェクト（Unity 6 LTS、URP、UI Toolkit、Input System）、`SimLoop`、`VehicleBody`（PhysX 設定）、`IFirmware` の 2 実装（WebGL は `.jslib`、エディタは開発用 dylib ＋ ローダ）、キーボード操縦、何もない部屋（ビルドに埋め込み）、自作形状の機体（2026-09-21 に既存の STL 13 個の見た目へ移植。自作形状は切り替え用に残す。§4「機体の見た目」）、下向き ToF（レイ 1 本）、`GeneratedParams.cs`、`sf unity setup` ／ `build` ／ `serve` | **Chrome で ARM → 離陸 → ALT_HOLD → 着地ができ、60fps で実時間比 1.0 を保つ**（1 刻みの所要時間を計測済みであること）。エディタの再生ボタンでも同じく飛ぶ。スレッド版と fiber 版で同じ入力に対する出力が一致する。`sf params check` が通る。**2026-09-21: ARM → 離陸 → ALT_HOLD → 着地までを Chrome で自動で飛ばし、構造化ログで端から端まで追えることを確かめた（§9 (g)）。60fps と実時間比 1.0 は前面のタブでの人の確認が未実施** |
| 4 障害物と空間 | ゲームパッド（`GamepadBridge.jslib`、割り当て画面）、`WorldFile`、`ObstacleFactory`（box・pillar・wall・gate・ring・tunnel・table・step・ramp・pad）、編集 UI（グリッド吸着、元に戻す）、同梱 6 空間（何もない部屋／柱の林／通過枠のコース／狭い廊下・トンネル／段差のある床／模様の少ない床）、ブラウザでの保存と読み込み（`FileIO.jslib`）、`sf unity world validate` | 置く・動かす・回す・寸法変更・消すができ、保存 → 読み込みで一致する。壁に当たる。机の上で ToF の値が変わる |
| 5 センサとカメラ | ToF の円錐化（中心 1 ＋ 外周 8 本）、前向き ToF、オプティカルフローの SQUAL（面ごとの `flow_quality` × 距離の減衰）、下向き・FPV カメラの小窓、視点切り替え | 「模様の少ない床」で SQUAL が下がり POS_HOLD の挙動が変わる。段差で ToF の値が急変する |
| 6 端末からの操作と再生 | `ISimCommands`、`sf unity cmd`（sim・world・obstacle・vehicle・param・plant・rc・scenario・log・view・sensor の各群）、エディタ用 `[CliCommand]`、URL の引数、`.scn` の再生（合否は見ない）、`.sflog.zip` の書き出し、`sf sim list` への登録 | 端末から空間の読み込み・リセット・一時停止・障害物の追加・パラメータ変更・状態取得ができる。書き出したログを `sf log viz` で開ける |
| 7 試験と文書 | EditMode ／ PlayMode 試験、`sf unity test`、`docs/commands/sf-unity.md`・`coordinate-systems.md`・`simulator/README.md`（全て 2 言語）、配布用ビルドの検査、公開サイトへの掲載（任意） | 手元で全て合格する。配布物に操作用の受け口が入っていない。完了後に本文書を削除する（`PROJECT_PLAN.md` §15 規則 7） |

### 置き場を作る時期

`simulator/unity/` は中身と同じコミットで作り、そのコミットで `PROJECT_PLAN.md` §10 の `simulator/` の一覧に追記する（§15 規則 5「空の置き場は作らない」・規則 1「同じコミットで更新する」）。段階 0 では作らない。同様に `simulation-policy.md` §2 の比較表を 4 列に広げるのも、Unity 版の中身が入る段階で行う。

## 6. 主なファイル

### 既存を編集（最小限）

`simulator/sils/rtos/scheduler.hpp`、`simulator/sils/plant/plant.hpp`、`simulator/sils/CMakeLists.txt`、`lib/sfcli/commands/sim.py`（`BACKENDS` に `kind` を追加）、`lib/sfcli/commands/__init__.py`、`tools/params_audit/generate.py`（`render_csharp`）と `params_manifest.py`、`PROJECT_PLAN.md`、`docs/architecture/simulation-policy.md`、`docs/architecture/coordinate-systems.md`

### 新設

| 場所 | 内容 |
|---|---|
| `simulator/sils/rtos/scheduler_step.cpp` | 刻み実行用のスケジューラ（案 2 なら `scheduler_fiber.cpp` も） |
| `simulator/sils/plant/` | `plant_external.cpp`、`actuator_model.hpp`、`plant_external_state.hpp` |
| `simulator/sils/frames/frames_unity.hpp` | Unity 規約との座標変換 |
| `simulator/sils/cmake/firmware_sources.cmake` | ファームウェアのソース一覧 |
| `simulator/unity/native/` | `CMakeLists.txt`、`bridge/`、`toolchains/` |
| `simulator/unity/` | Unity プロジェクト（`Assets/StampFly/{Runtime,Editor,Plugins,Vehicle,Worlds,Scenes,Tests}`、`Schemas/world.schema.json`） |
| `lib/sfcli/commands/unity.py` | `sf unity` の実装 |
| `docs/commands/sf-unity.md` | `sf unity` の使い方（2 言語） |
| `docs/plans/unity-simulator.md` | 本文書 |
| `flake.nix`・`.envrc`・`justfile`・`lefthook.yml` | 開発環境（段階 0 で新設済み） |

### 再利用する既存の実装

| 用途 | 参照先 |
|---|---|
| 起動手順とパラメータ設定の手本 | `simulator/sils/emu/emu_main.cpp`（ライブラリには入れない） |
| ファームウェアとの境界 | `simulator/sils/devices/virtual_board.hpp`、RC 注入 `devices/scenario_inject.hpp`、チップへの入力 `vl53_device.hpp`・`pmw3901_device.hpp` |
| 物理の移植元 | `simulator/sils/plant/plant.cpp`（副刻みと ODE、ToF、オプティカルフロー） |
| 機体の数値 | `simulator/sils/models/stampfly.xml`（質量・慣性・衝突箱・ロータ位置）。基準は `control/models/stampfly_physical.yaml` |
| 機体の描画と 3 枚羽根の形 | `simulator/sils/gui/static/app.js` |
| 操縦入力 | `firmware/controller/components/usb_hid/src/usb_hid.cpp`、`simulator/vpython/interfaces/joystick.py`、キー割り当ては `lib/sfcli/commands/sils.py` |
| ログ | `simulator/sils/devices/emu_flightlog.cpp`、`lib/sfcli/commands/sils.py`（`_finalize_flightlog`） |
| ローカルサーバの前例 | `simulator/sils/gui/server.py` |

## 7. 検証方法

1. **既存を壊していないこと**: 変更前後で `sf sils build` → `sf sils regression` を実行し、34 件の結果とログの sha256 が一致することを確かめる
2. **ネイティブ単体**: `just unity-native-test` で `actuator_parity_test`（MuJoCo 版と電池電圧がビット一致）、`frames_unity_test`（往復変換、右ヨー・右ロール・機首上げの符号、静止時の加速度計の測定値）、`sfu_bridge_smoke`（ホバリング成立、2 回実行で出力一致）を回す。このレシピは段階 2 で対象の実装と一緒に `justfile` へ足す
3. **ブラウザ**: `sf unity build webgl` → `sf unity serve` で Chrome を開き、キーボードで ARM → 離陸 → ALT_HOLD → 着地。画面の実時間比が 1.0、ブラウザのコンソールにエラーが無いことを確かめる。各段階の合格基準を同じ手順で確かめる
4. **障害物とセンサ**: 同梱 6 空間を順に読み込み、壁への衝突、机の上での ToF の変化、「模様の少ない床」での SQUAL 低下を確かめる。空間を保存して読み込み直し、`sf unity world validate` を通す
5. **端末からの操作**: `sf unity cmd world load --name gate_course`、`sim pause`、`obstacle add --type box ...`、`vehicle state` を端末から実行し、ブラウザの画面に反映されることを確かめる
6. **自動試験**: `sf unity test`（EditMode と PlayMode）、`sf params check`、`sf params generate --check`

## 8. 未確認事項

段階 1 で確かめる。決着したものは結果を添えて残す（§9 に数値がある）。

| 事項 | 確かめること |
|---|---|
| ~~Emscripten でのビルド~~ **決着（2026-09-20）** | 無改変ファームウェアが Emscripten でビルドできるか、Chrome で実時間に回るか → **両方とも成立**。Emscripten 6.0.8-git（計画時に見込んでいた 3.1.38 ではない）で 97 翻訳単位すべてが通り、Chrome で 1 秒あたり 0.0172 秒（目安 0.30 秒の約 17 分の 1）。§9 (a-0)・(a-2') |
| PhysX のジャイロ項 | PhysX が ω×Iω を積分するか。慣性が 9.16／13.3／20.4（単位 1e-6 kg·m²）と非対称なので、しない場合は明示的に足す。二重にならないことを確認する |
| PhysX の既定値 | `Physics.defaultContactOffset` の既定 0.01m が機体の半分の厚み 0.0103m と干渉しないか。`angularDamping` の既定 0.05 を 0 にする |
| Unity CLI の宣言規則 | `[CliCommand]` の書き方（`com.unity.pipeline` は 0.7 の実験版で、公開文書からは取得できなかった） |
| 画像からのフロー | WebGL2 での `AsyncGPUReadback` の可否（任意の後段なので、これで作業は止めない） |
| 機体メッシュの出所 | `kouhei1970/stampfly_sim` から移されたもの。元の CAD の作者と利用条件。**未確認のまま（2026-09-21 時点）。** これを待たずに移植する判断をしたため（§4「機体の見た目」）、未確認である事実は `simulator/shared/assets/meshes/README.md` に記してある。確認できたら同 README とこの行を更新する |

## 9. 技術検証の記録（段階 1）

段階 1 の結果を数値付きでここに記録し、その上で段階 2 以降へ入る。**(a)〜(e) を 2026-09-20 に実施した**。(a)(c)(d)(e) は合格。(b) も合格（途中で、飛行結果がメモリの配置で分かれる不具合が見つかり、同日に原因を特定して直した）。(f) は実機コントローラを要するため未実施。成果物と詳しい数値・再現手順は [`../../simulator/unity/native/README.md`](../../simulator/unity/native/README.md) にある。

| 項目 | 測定・確認の内容 | 結果 | 判定 |
|---|---|---|---|
| (a-0) Emscripten でのビルド | 無改変ファームウェア＋シミュレータ＋`devices/`＋`rtos/` がコンパイルできるか | Emscripten 6.0.8-git で 97 翻訳単位すべて成功。`firmware/` 配下は無編集。回避が要ったのは 2 件のみ（`tasks/cli_task.cpp` の `stdout` 再代入 — musl では `stdout` が const のため、`simulator/unity/native/compat_wasm/cstdio` を include の優先順位で前に置いて回避。既存の `esp_idf_host/cstdio` が Windows 向けに使うのと同じ手法。もう 1 件は `plant.hpp` の MuJoCo ヘッダで、`SILS_PLANT_EXTERNAL` の囲みで回避） | **合格** |
| (a-1) fiber 版スケジューラ | `rtos_smoke` のトレースが現行と一致するか | `trace_dump` の出力全体の sha256 先頭が、スレッド版ネイティブ・fiber 版ネイティブ（ucontext）・fiber 版 wasm（Asyncify）の 3 つとも `48852b7d2bddad63` で一致（事象数 425） | **合格** |
| (a-2) 案 2 の速度（Node.js） | シミュレーション 1 秒あたりの実時間（目安 0.3 秒以下） | 全 14 タスク＋検証用プラント（MuJoCo 無し、C++ 内の簡易 6 自由度積分）を Node.js v24 の WebAssembly で実行し、シミュレーション 60 秒を実時間 0.155 秒 = **1 秒あたり 0.0026 秒**（目安に対し約 100 倍の余裕）。`-O2`、Asyncify は全体計装のまま、wasm 412 KiB。Apple M2 Max | **合格** |
| (a-2') 案 2 の速度（Chrome） | 同上（ブラウザでの実測） | 同じ `.js`／`.wasm` を素の HTML ページから読み込み（`Module.arguments=['60']`）、`python3 -m http.server` で 127.0.0.1 から配信して Chrome で実行。シミュレーション 60 秒を実時間 1.031 秒 = **1 秒あたり 0.0172 秒**（目安に対し約 17 倍の余裕）。COOP/COEP 等の特別な HTTP ヘッダは付けていない | **合格** |
| (a-3) 案 1 の速度 | 同じ入口を `-pthread` でビルドし、Node.js で計測（段階 2 で `scheduler_step.cpp` ができたため可能になった。Apple M2 Max、シミュレーション 30 秒） | シミュレーション 1 秒あたり 0.0142〜0.0163 秒、wasm 328,664 バイト。案 2 は同条件で 0.0029 秒・422,471 バイトで、**案 2 が約 4.9 倍速い**。どちらもホバリング成立（最大 0.813 m → 最終 0.577 m）。Chrome（COOP/COEP ヘッダ付き）では測っていない | 合格（案 2 の決定を裏づける。2026-09-20） |
| (a-4) ホバリングの成立 | 離陸してホバリングが成立するか | ファームウェアの状態が INIT → IDLE\_GROUND → ARMED\_GROUND → FLYING と進み、最大高度 0.813 m から減衰して 0.576〜0.577 m を保持。Node.js で 2 回実行して出力が完全一致。Chrome でも同じ値 | **合格** |
| (a-5) `run_until` の同期性 | Asyncify を使っても外から見て同期関数として返るか | 1 回の `main` の中で 12,000 回以上呼んで正常終了。`.jslib` から同期で呼ぶ案（(b)）の前提が成り立つ | **合格** |
| (a-6) 既存ファイルへの影響 | 既存ビルドの計算結果が変わらないこと | 変更は `simulator/sils/plant/plant.hpp` だけで追加 18 行・削除 0 行（`#ifndef SILS_PLANT_EXTERNAL` の囲み 3 か所と説明コメント）。マクロ未定義時のプリプロセス後のトークン列は変わらない（`clang++ -E -P` で確認） | **合格** |
| (b) `.jslib` からの同期呼び出し | 別モジュールの `sfu_step` を同期で呼べるか、作り直しで再起動できるか | 同期呼び出しは成立。Chrome で 12,000 刻みを連続で呼び、1 刻み 21〜29 マイクロ秒（刻みの長さ 2500 マイクロ秒に対し約 90 倍の余裕）、シミュレーション 1 秒あたり 0.0083〜0.0116 秒。電源の入れ直し（`createSfuFirmware()` の呼び直し）を 16 回行い、毎回 INIT から起動、wasm メモリは毎回 67,108,864 バイト、JavaScript のヒープは横ばい。**ただし既知の不具合あり**: 飛行結果がメモリの配置で 2 通りに分かれ、起動前に `_malloc(48)` を 1 回呼ぶだけで、ホバリング成功（最大 0.813 m → 0.577 m）が「Impact detected: 8.0G」による失敗に反転する。`.jslib` は起動前に確保するため、Unity 経由では毎回失敗する。同じ配置なら結果は完全に再現する。(a-4) の「2 回の出力が一致」は同じパスでの 2 回で、この分岐は見えていなかった。**原因は fiber 型スケジューラがタスクのスタックを `malloc`（wasm32 では 8 バイト整列）で確保していたこと**で、16 バイト境界を前提にした局所変数の位置が重なって壊し合っていた。`aligned_alloc(16, …)` に直し、ヒープを 32 通りずらしても結果が全て一致することを確かめた（`simulator/unity/native/spike/heap_layout_check.mjs`。`.jslib` の 48 バイトを含む） | 合格（2026-09-20、不具合は修正済み）。Unity の画面から実際に飛ばす確認は段階 3 |
| (c) PhysX の安定性 | 質量 37g・慣性 1e-5 桁で 400Hz、トルク応答 ±1%、着地で振動しないか | PlayMode 試験 16 件が合格（macOS のエディタ上の PhysX。WebGL のプレイヤー内の PhysX では未確認）。トルク応答の誤差は 3 軸とも 0.0000%。0.5 m から落として静止高 0.010300 m（箱の厚みの半分と一致）、着地後 2 秒の変動幅 5.5e-5 m。**PhysX はジャイロ項 −ω×(Iω) を積分しない**: 世界座標系の角運動量が ω のまわりに振れ、1 秒時点で 15.1619 度（解析値 15.16189 度と一致、刻みを 400→6400 Hz に変えても不変）。明示的に足す必要があり、刻みの先頭で評価すると角運動量の大きさが 1 秒に 10.77% 増えるが、**中点で評価すると 0.006%** に収まる（`GyroscopicTerm.BodyTorqueAtMidpoint` を既定として実装済み） | 合格（2026-09-20） |
| (d) WebGL の標準入出力 | `prompt()` が開かないこと | `-sFILESYSTEM=0` により標準入力の装置そのものが無く、シミュレーション 30 秒（CLI タスクを含む全タスク稼働）で `prompt`／`confirm`／`alert` の呼び出しは 0 回。標準エラーは起動時に 190 行出るので、既定では捨て、必要なときだけ出す | 合格（2026-09-20） |
| (e) Unity CLI | `install -m webgl` ／ `build` ／ `test` が手元で通るか、`unity command` が再生中に届くか | Unity CLI 1.0.0-beta.8、エディタ 6000.6.2f1（Web モジュール入り）。`unity build`（WebGL）は終了コード 0、約 2 分、出力 12 MB。`unity test` で PlayMode 試験が回る。正確なコマンドの形は `simulator/unity/README.md`。`install` は導入済みのため未実行。**背面のタブでは `requestAnimationFrame` が来ないため Unity が起動しない**（自動の Chrome 検証では Worker のタイマーに差し替える）。前面のタブでの 60fps と実時間比 1.0 は未確認 | 合格（2026-09-20）。前面のタブでの描画速度は段階 3 で確認 |
| (f) ゲームパッド | 実機コントローラが Chrome の Gamepad API でどう見えるか（軸・ボタンの並び） | 未実施（実機コントローラでの確認が要る） | 未実施 |
| (g) ブラウザでの自動飛行（段階 3） | Chrome で実ファームを ARM → 離陸 → ALT_HOLD → 着地まで自動で飛ばし、構造化ログで端から端まで追えるか | `sf unity check fly` で 8 件の検査すべて合格（2026-09-21、`?raf=worker` の自動操作のタブ、`empty_room`）。状態遷移は **INIT → IDLE_GROUND(4.00s) → ARMED_GROUND(5.02s) → FLYING(6.30s) → IDLE_GROUND(22.32s)**、保持中の高度 **0.380〜0.520 m**（頂点 0.467 m、着地 0.0103 m）、傾き 0.0 度、1 刻みの所要時間 **42〜150 マイクロ秒**（刻みの長さ 2500 に対し 17〜60 倍の余裕）。ログは 1 つの `cmd_id` で cli → server → ページ → server → cli が時刻順に並び、状態遷移が `sim.flight_state`（`sim_us` つき）、速さが 1 秒ごとの `sim.stats` として出て、刻みごとの行は無く、**サーバに拒否された行は 0**。`fw.boot`・`sim.step_overrun` も出る | **安定しては通らない（2026-09-21）**。実装担当の実行では 8 件とも合格したが、同じ担当の別の実行と、取りまとめ役が同じビルドで行った再実行は不合格: ALT_HOLD で約 0.40 m を 5 秒ほど保持した後、仮想時刻 11.8 秒ごろから約 0.3 m/s で下がり始め、13.1 秒に床へ当たってファームウェアが `Impact detected: 7.9G` で緊急 DISARM した（担当の失敗した実行では、逆に上へずれてから落下）。衝撃検出そのものは床への衝突に対する正しい反応で、問題は保持中に高度が崩れること。原因は未特定。ログ（`logs/unity/<run_id>.jsonl`）には `fw` の `failsafe` の行が `sim_us` 付きで残り、時刻の特定はすぐにできた。前面のタブでの 60fps と実時間比 1.0 は**未確認**（自動操作のタブでは測れない。人が確かめる手順は `simulator/unity/README.md` §9） |
| 実時間比の不具合（段階 3 で発見・修正） | 実時間比が 1.0 を超えないか | **超えていた。** 背面・絞られたタブで溜まった借りを、以後のどのフレームも上限の 12 刻み（シミュレーション 30 ms）で返し続け、16 ms のフレームに対し **1.88** になっていた（実測）。停止とみなすのは 1 フレームが 0.5 秒を越えたときだけなので、0.4 秒で安定して絞られたタブはそれに当たらず借りを溜め続ける。`SimClock` が借り自体に「1 フレームで返せる量」の上限を置くよう直し、`SimClockTest` の `AtNormalSpeedTheSimulationNeverOutrunsRealTime` と `TheBacklogNeverGrowsBeyondOneFramesWorth` が保つ | 修正済み（2026-09-21） |
| 結論 | スレッドの案（案 1 ／ 案 2）の決定 | **案 2（ファームウェアを別の wasm モジュールにし、fiber 型スケジューラで動かす）に決定。** 速度の目安を Node.js で約 100 倍、Chrome で約 17 倍上回り、トレースがビット単位で一致し、特別な HTTP ヘッダを要しないため | **決定済み** |

### Node.js と Chrome の差について

Chrome（1 秒あたり 0.0172 秒）は Node.js（同 0.0026 秒）より約 6.6 倍遅い。**理由は切り分けていない。** 実測に使ったページは約 200 行の出力を 1 行ごとに `console.log` と DOM への追記で出しており、また初回読み込みでは WebAssembly の最適化コンパイルが終わる前から走る。これらが効いている可能性はあるが、推測であって確かめていない。どちらも目安を大きく下回るため、段階 1 では切り分けを行わなかった。段階 3 で実時間比 1.0 を測るときに、出力を抑えた状態で測り直す。

---

<a id="english"></a>

# Plan: Adding a Unity-Based Simulator (WebGL)

Status: **In progress**. Created 2026-09-20, last updated 2026-09-21.

## 1. Overview

### About This Document

This is the plan for adding a browser-based Unity simulator as a **fourth** simulator alongside the three that already exist. The work is split into stages, and each stage must meet its pass criteria before the next one starts. Stage 1 (feasibility measurement) decides both whether this plan proceeds and which threading approach is used, so its numbers are recorded in §9 before stage 2 begins.

### Target Audience

- Developers implementing or modifying simulators in this repository
- Users and instructors who want to know how the new simulator differs in role from the existing SILS (Software In the Loop Simulation)

### Position: The Existing Three Are Kept

SILS, the VPython version, and the Genesis version are **all kept**. The Unity version replaces none of them. The re-verification test suite (`sf sils regression`, 34 scenarios) and the model-match pass/fail check remain the job of the existing SILS. Reproducing the 34 scenarios' verdicts in the Unity version is not a goal.

`simulator/sils/` is this repository's own C++ simulator, closing the loop between unmodified real-vehicle firmware and MuJoCo physics. Its visualization (`sf sils gui`) works by "run the whole scenario, write JSON, replay in the browser," and so lacks the following three things.

| Missing from the existing SILS | What the Unity version provides |
|---|---|
| Real-time piloting | Stick input during execution, with the response visible as it happens |
| Camera images and forward-facing ToF (Time of Flight ranging) | Downward and forward ToF, optical flow, downward and FPV (first-person view) cameras |
| A 3D environment with obstacles | A world where obstacles are placed and affect both collisions and sensors |

The overall simulation policy is set by [`../architecture/simulation-policy.md`](../architecture/simulation-policy.md). Widening its §2 comparison table to four columns (SILS, VPython, Genesis, Unity) happens in the stage that brings in the Unity version's contents.

## 2. Requirements

| Item | Content |
|---|---|
| Position | Added alongside the existing three, which are kept. Reproducing the 34 scenarios' verdicts is not a goal |
| Distribution target | WebGL only at first. **Chrome is the only supported browser**; no other browser is verified. A macOS application build is deferred |
| Firmware | Used unmodified, in C++ (not rewritten in C#, so it stays identical to the real vehicle's code) |
| Physics | Rigid-body motion and contact come from Unity (PhysX). Motor, thrust, battery, wind, and sensor synthesis come from the existing in-house C++ |
| Purpose | Real-time piloting; sensor emulation (downward and forward ToF, optical flow, cameras); appearance and environment |
| Obstacles | Freely placed (add, move, rotate, resize, delete, save, load), affecting collisions and sensors |
| Presets | 10 obstacle types (in-house primitive shapes only, resizable) and 6 pre-arranged worlds, shipped in the same file format users save |
| Control surface | The official Unity CLI (`unity`). User-facing operations are exposed as `sf` commands, and a running simulator can also be driven from the terminal |
| Development environment | `nix develop` + flake + direnv (newly added at the repository root, along with `just` and `lefthook`). ESP-IDF and the sf CLI are still activated by `setup_env.sh` |

## 3. Overall Structure

```
Browser (Chrome)
├─ Unity WebGL (C#) … PhysX 400Hz, rendering, raycasts, obstacle editing, UI
│     ↑↓ one sfu_step call per 2.5ms physics tick
└─ Firmware (unmodified C++) + pseudo-RTOS + device models + motor ODE at 4000Hz
      = static library sfu_firmware (a new bridge/ provides the C ABI)

Terminal:  sf unity build / serve / cmd … ──(via a local server)──> the simulator in the browser
           unity install / build / test / command … editor install, build, test, control
```

### What Happens in One Tick

`SimLoop.Update()` drives this itself (Unity's `FixedUpdate` is not used).

| Step | Side | Processing |
|---|---|---|
| 1 | C# | Pack the `Rigidbody` position, rotation, velocity, and angular velocity, the accelerometer reading computed on the previous tick, and the raycast ToF distances into `SfuStepIn` |
| 2 | C++ (`sfu_step`) | Inject the state → advance the firmware by 2.5ms (running the motor ODE 10 times at 4000Hz) → return the interval-averaged total force and torque plus state |
| 3 | C# | `AddRelativeForce` / `AddRelativeTorque` / wind `AddForce` → `Physics.Simulate(0.0025)` |
| 4 | C# | Accelerometer reading = R⁻¹·((v_after − v_before)/dt − g). Contact forces enter automatically, so at rest the value is −9.81 m/s², as on the real vehicle |

When processing cannot keep up with real time, no firmware tick is skipped: virtual time falls behind instead, and the ratio to real time is displayed. Pause, single-step, and speed scaling (0.1×–4×) are provided.

## 4. Design Decisions

| Question | Decision | Reason |
|---|---|---|
| Changes to existing code | Exactly three places: (1) declare `run_until` / `shutdown` in `rtos/scheduler.hpp` (defined in a new `rtos/scheduler_step.cpp`), (2) add a `SILS_PLANT_EXTERNAL` guard in `plant/plant.hpp`, (3) extract the source list in `CMakeLists.txt` into `cmake/firmware_sources.cmake` | With the macro undefined, the existing build is syntactically identical and not a single bit of its results changes |
| Plant | Link a new `plant/plant_external.cpp` instead of `plant.cpp`. The motor ODE, battery, and turbulence go into a new `plant/actuator_model.hpp` with statement-for-statement identical formulas, cross-checked against the MuJoCo version by a numerical-equality test | `virtual_board.cpp` and the device models are used without edits |
| Frame conversion | The C ABI passes Unity's raw conventions (left-handed, Y up, quaternion as xyzw); all conversion is concentrated in a new C++ `frames/frames_unity.hpp`. The C# side only converts between the world file (ENU: x east, y north, z up) and Unity | Matches the SILS rule that frame conversion lives in one place |
| How forces are passed | Total force and total torque (body frame, impulse-averaged over 10 sub-ticks) are returned | One round trip suffices, and no moment-arm lengths need to be duplicated in C# |
| Re-initialization | One module equals one power-on. There is no second `init` within a module. Repositioning the vehicle does not restart the firmware; a power cycle recreates the module | The firmware's tasks hold many static variables and cannot be returned to their initial state without modification |
| Threading | **Approach 2, decided** (2026-09-20, settled by the stage 1(a) measurements; see §9): build the firmware as a separate wasm module (`sfu_firmware.wasm`, `-sMODULARIZE -sASYNCIFY`), run it on a thread-free fiber scheduler (a new `rtos/scheduler_fiber.cpp` using `emscripten_fiber_t`, selected against the existing `scheduler.cpp` at link time), and call it synchronously through `.jslib`. **Approach 1** (keep threads, statically link the `.a` into Unity, `-pthread`, experimental settings, COOP/COEP headers required) remains the fallback, but with approach 2 beating the speed target by roughly 100× under Node.js and 17× in Chrome there is no reason to switch | Approach 2 loads on GitHub Pages with no special HTTP headers, keeps the Emscripten version independent of Unity's, and makes a power cycle a matter of recreating the module. "Fibers inside an `.a` statically linked into Unity" is not viable, because Asyncify instrumentation would then apply to the whole Unity engine. In the measurement that whole-program instrumentation was kept and still came in far under the target, so narrowing it with `ASYNCIFY_ONLY` was unnecessary |
| Editor-side development | Not distributed, but a macOS arm64 native plugin (keeping the current thread-based scheduler) is built for development. On the C# side, `IFirmware` switches between the WebGL and editor implementations | The play button can be used without waiting for a WebGL build each time, and `unity test` Play Mode tests can run |
| Driving the browser simulator from a terminal | Three layers. The base is a page-side JavaScript API (`window.stampfly.command(json)` → `ISimCommands`). On top of it: (1) URL arguments (`?world=gate_course&scn=...`, usable on the public site) and (2) the local server behind `sf unity serve` (Python standard library, 127.0.0.1 only, with an Origin check; the page long-polls `/api/cmd/next` and answers to `/api/cmd/result`, while `sf unity cmd <command>` POSTs). `sf unity cmd` routes to `unity command` when the editor is running, and to the local server otherwise | `com.unity.pipeline` listens over local HTTP and does not reach WebGL. Precedents: the server in `simulator/sils/gui/server.py`, the Origin check in `lib/sfcli/commands/blocks.py` |
| Stick input | Keyboard first (bindings matching `sf sils fly`, self-centering on release). Gamepads come in the next stage, via a `GamepadBridge.jslib` that reads `navigator.getGamepads()` directly, an axis-assignment screen, and a shipped default mapping for the real controller | The real controller's HID (Human Interface Device) descriptor does not map onto the standard gamepad layout, and axis ordering is implementation-dependent. The first pass criteria must not depend on it |
| Publishing | WebGL builds are produced locally with `sf unity build --release`, attached to a GitHub Release, and unpacked into `_site/sim/` by `deploy-pages.yml` the same way `flash/` is. Decompression Fallback is enabled | The publishing job needs neither Unity nor license activation. GitHub Pages cannot set `Content-Encoding` |
| Sharing the command implementation | Every operation lives on the C# `ISimCommands`. On-screen controls, `sf unity cmd`, and the editor's `[CliCommand]` all call the same implementation. The relay endpoint is included only in development builds, and `sf unity build --release` verifies it is absent from distribution builds | Different routes produce identical results |
| World files | `*.world.json`, ENU (x east, y north, z up), metres and degrees. Structure in `Schemas/world.schema.json`, checked by `sf unity world validate` (pure Python) | The left-handed frame never leaves Unity. MuJoCo and Genesis can read the same files later |
| Vehicle appearance | **Ported to the existing 13 STL files (2026-09-21).** The editor menu item `StampFly/Meshes/Rebuild Vehicle Meshes` converts the STL files under `simulator/shared/assets/meshes/parts/` once and writes `Assets/StampFly/Runtime/Resources/StampFly/Meshes/<part>.asset`, which is committed (no copy of the STL files is kept on the Unity side). The conversion is an x sign flip, a triangle winding flip, and a 0.001 scale, and **its content was verified to be correct** (all thirteen enclose a positive signed volume; the stored normals are not trusted but recalculated from the winding, since all 108 facets of `m5stamps3.stl` store a normal opposite to their winding). The parts-and-colours list is `Assets/StampFly/Runtime/Vehicle/VehicleParts.cs`, with colours taken from the `rgba` values in `stampfly_fixed.urdf`. The in-house primitive appearance (a plate, four motor cans, procedurally generated three-blade propellers) is kept so it can be switched back to | The original condition was to port only after provenance and license were confirmed. **On 2026-09-21 the user changed that condition: the port proceeds with them unconfirmed, and `simulator/shared/assets/meshes/README.md` states plainly that the provenance is unestablished.** `stampfly_v1.stl` was moved in from an external repository, and the original CAD's author and terms are recorded nowhere. That README is updated once they are established |
| Unity in CI | Not added at first. Only checks that run without Unity are added to the existing CI (including the C# artifacts in `sf params generate --check`, `sf unity world validate`, and the native minimum-operation check) | The license-activation effort and runtime are not worth the return |

## 5. Stages and Pass Criteria

"Stage 0," "stage 1," and so on are divisions of this plan's work, distinct from the teaching tiers (L0–L3).

| Stage | Work | Pass criteria |
|---|---|---|
| 0 Preparation | Add this document; update `PROJECT_PLAN.md` (§12 for the development-environment files, §14 for the lefthook checks, §15 for the link to this document) and `simulation-policy.md` §9. Add `flake.nix`, `.envrc`, `justfile`, and `lefthook.yml` | cmake and ninja are usable under `nix develop`. The existing CI and `setup_env.sh` are unaffected |
| 1 Feasibility measurement | **(a) Highest priority, with plain HTML and Node.js rather than Unity**: compile the unmodified firmware, the simulator, and `rtos` with Emscripten; confirm that a minimal fiber scheduler reproduces the current `rtos_smoke` trace; then run `app_main` + 14 tasks + `plant_external` + a simple rigid-body integrator inside C++ and **measure the speed in Chrome**. Measure the same thing under approach 1 (`-pthread`, local server with COOP/COEP headers). **(b)** Whether Unity WebGL can call a separate module's `sfu_step` synchronously through `.jslib`, and whether recreating the module restarts it. **(c)** Whether PhysX is stable at 400Hz for a 37 g body with inertia on the order of 1e-5 (torque response within ±1%, no oscillation on landing, treatment of the ω×Iω gyroscopic term). **(d)** Standard I/O under WebGL (that `prompt()` does not open). **(e)** Whether the Unity CLI's `install -m webgl` / `build` / `test` work locally, and whether `unity command` reaches the editor during play. **(f)** How the real controller appears through **Chrome's Gamepad API** | One second of simulation is processed in 0.3 s of real time or less, and takeoff followed by a hover succeeds. The threading approach is decided with numbers. If the target is missed, Asyncify's scope is narrowed and the measurement repeated; if it is still missed, approach 1 is adopted. **If neither meets the target, this is the decision point to revise the plan** |
| 2 Native core | `scheduler_step.cpp`, the `plant.hpp` guard, `plant_external.cpp`, `actuator_model.hpp`, `frames_unity.hpp`, `simulator/unity/native/bridge/` (`sfu_api.h`, a log ring buffer), and `sfu_bridge_smoke` (ARM → takeoff → hover using the simple in-C++ rigid-body integrator, without Unity) | `sf sils regression` gives the same result for all 34 cases as before the change, with matching log sha256 values. `actuator_parity_test` and `frames_unity_test` pass. The minimum-operation check produces identical output across two runs |
| 3 Minimal WebGL version | Unity project (Unity 6 LTS, URP, UI Toolkit, Input System), `SimLoop`, `VehicleBody` (PhysX settings), two `IFirmware` implementations (WebGL via `.jslib`, editor via the development dylib and a loader), keyboard piloting, an empty room (embedded in the build), the primitive-shape vehicle (ported on 2026-09-21 to the appearance of the existing 13 STL files, with the primitives kept so they can be switched back to; §4, "Vehicle appearance"), downward ToF (a single ray), `GeneratedParams.cs`, `sf unity setup` / `build` / `serve` | **In Chrome, ARM → takeoff → ALT_HOLD → landing works, holding a real-time ratio of 1.0 at 60fps** (with per-tick timings measured). The same flight works from the editor's play button. The thread-based and fiber-based builds produce identical output for identical input. `sf params check` passes. **2026-09-21: ARM → takeoff → ALT_HOLD → landing was flown automatically in Chrome and followed end to end in the structured log (§9 (g)). 60 fps and a real-time ratio of 1.0 in a foreground tab remain unverified by a person** |
| 4 Obstacles and worlds | Gamepad support (`GamepadBridge.jslib`, assignment screen), `WorldFile`, `ObstacleFactory` (box, pillar, wall, gate, ring, tunnel, table, step, ramp, pad), the editing UI (grid snapping, undo), the 6 shipped worlds (empty room, pillar forest, gate course, narrow corridor and tunnel, stepped floor, low-texture floor), browser save and load (`FileIO.jslib`), `sf unity world validate` | Adding, moving, rotating, resizing, and deleting all work, and a save-then-load round trip matches. The vehicle collides with walls. ToF readings change above a table |
| 5 Sensors and cameras | ToF cone modelling (1 central plus 8 peripheral rays), forward ToF, optical-flow SQUAL (per-surface `flow_quality` with distance falloff), downward and FPV camera insets, view switching | SQUAL drops on the "low-texture floor" and POS_HOLD behaves differently. ToF readings jump at a step |
| 6 Terminal control and replay | `ISimCommands`, `sf unity cmd` (the sim, world, obstacle, vehicle, param, plant, rc, scenario, log, view, and sensor groups), the editor's `[CliCommand]`, URL arguments, `.scn` replay (without pass/fail judgment), `.sflog.zip` export, registration in `sf sim list` | Loading a world, resetting, pausing, adding an obstacle, changing a parameter, and querying state all work from a terminal. The exported log opens in `sf log viz` |
| 7 Tests and documentation | EditMode and PlayMode tests, `sf unity test`, `docs/commands/sf-unity.md`, `coordinate-systems.md`, `simulator/README.md` (all bilingual), the distribution-build check, optional publication on the public site | Everything passes locally. The distributed build contains no control endpoint. This document is deleted once the work is complete (`PROJECT_PLAN.md` §15, rule 7) |

### When the Directory Is Created

`simulator/unity/` is created in the same commit as its contents, and that commit adds it to the `simulator/` listing in `PROJECT_PLAN.md` §10 (§15, rule 5 "do not create empty placeholders" and rule 1 "update in the same commit"). It is not created in stage 0. Likewise, widening the §2 comparison table in `simulation-policy.md` to four columns happens in the stage that brings in the Unity version's contents.

## 6. Principal Files

### Existing Files to Edit (Minimally)

`simulator/sils/rtos/scheduler.hpp`, `simulator/sils/plant/plant.hpp`, `simulator/sils/CMakeLists.txt`, `lib/sfcli/commands/sim.py` (adding `kind` to `BACKENDS`), `lib/sfcli/commands/__init__.py`, `tools/params_audit/generate.py` (`render_csharp`) and `params_manifest.py`, `PROJECT_PLAN.md`, `docs/architecture/simulation-policy.md`, `docs/architecture/coordinate-systems.md`

### New Files

| Location | Content |
|---|---|
| `simulator/sils/rtos/scheduler_step.cpp` | Tick-driven scheduler (plus `scheduler_fiber.cpp` under approach 2) |
| `simulator/sils/plant/` | `plant_external.cpp`, `actuator_model.hpp`, `plant_external_state.hpp` |
| `simulator/sils/frames/frames_unity.hpp` | Conversion to and from Unity's conventions |
| `simulator/sils/cmake/firmware_sources.cmake` | The firmware source list |
| `simulator/unity/native/` | `CMakeLists.txt`, `bridge/`, `toolchains/` |
| `simulator/unity/` | The Unity project (`Assets/StampFly/{Runtime,Editor,Plugins,Vehicle,Worlds,Scenes,Tests}`, `Schemas/world.schema.json`) |
| `lib/sfcli/commands/unity.py` | The `sf unity` implementation |
| `docs/commands/sf-unity.md` | How to use `sf unity` (bilingual) |
| `docs/plans/unity-simulator.md` | This document |
| `flake.nix`, `.envrc`, `justfile`, `lefthook.yml` | The development environment (added in stage 0) |

### Existing Implementations to Reuse

| Purpose | Reference |
|---|---|
| Model for the startup sequence and parameter setup | `simulator/sils/emu/emu_main.cpp` (not included in the library) |
| Boundary with the firmware | `simulator/sils/devices/virtual_board.hpp`, RC injection in `devices/scenario_inject.hpp`, chip inputs in `vl53_device.hpp` and `pmw3901_device.hpp` |
| Source of the physics | `simulator/sils/plant/plant.cpp` (sub-ticks and ODE, ToF, optical flow) |
| Vehicle numbers | `simulator/sils/models/stampfly.xml` (mass, inertia, collision boxes, rotor positions); the reference values are in `control/models/stampfly_physical.yaml` |
| Vehicle rendering and three-blade propeller shape | `simulator/sils/gui/static/app.js` |
| Stick input | `firmware/controller/components/usb_hid/src/usb_hid.cpp`, `simulator/vpython/interfaces/joystick.py`, key bindings in `lib/sfcli/commands/sils.py` |
| Logging | `simulator/sils/devices/emu_flightlog.cpp`, `lib/sfcli/commands/sils.py` (`_finalize_flightlog`) |
| Local server precedent | `simulator/sils/gui/server.py` |

## 7. Verification Method

1. **Nothing existing is broken**: run `sf sils build` → `sf sils regression` before and after the change, and confirm that all 34 results and the log sha256 values match
2. **Native unit checks**: `just unity-native-test` runs `actuator_parity_test` (battery voltage bit-identical to the MuJoCo version), `frames_unity_test` (round-trip conversion; signs for right yaw, right roll, and nose-up; the accelerometer reading at rest), and `sfu_bridge_smoke` (hover achieved, identical output across two runs). This recipe is added to the `justfile` in stage 2, together with the implementation it covers
3. **Browser**: `sf unity build webgl` → `sf unity serve`, then in Chrome fly ARM → takeoff → ALT_HOLD → landing with the keyboard. Confirm the on-screen real-time ratio is 1.0 and the browser console is free of errors. Each stage's pass criteria are checked the same way
4. **Obstacles and sensors**: load each of the 6 shipped worlds in turn and confirm wall collisions, the ToF change above a table, and the SQUAL drop on the "low-texture floor." Save a world, load it back, and run `sf unity world validate`
5. **Terminal control**: run `sf unity cmd world load --name gate_course`, `sim pause`, `obstacle add --type box ...`, and `vehicle state` from a terminal, and confirm each is reflected on screen
6. **Automated tests**: `sf unity test` (EditMode and PlayMode), `sf params check`, `sf params generate --check`

## 8. Open Questions

To be settled in stage 1. Settled items are kept with their result (the numbers are in §9).

| Item | What to confirm |
|---|---|
| ~~Emscripten build~~ **Settled (2026-09-20)** | Whether the unmodified firmware builds with Emscripten and runs at real-time speed in Chrome → **both hold**. All 97 translation units compile under Emscripten 6.0.8-git (not the 3.1.38 assumed when this plan was written), and Chrome runs at 0.0172 s per simulated second, about one seventeenth of the 0.30 s target. §9 (a-0) and (a-2') |
| PhysX gyroscopic term | Whether PhysX integrates ω×Iω. Inertia is asymmetric (9.16 / 13.3 / 20.4 in units of 1e-6 kg·m²), so if it does not, the term is added explicitly — while confirming it is not applied twice |
| PhysX defaults | Whether the default `Physics.defaultContactOffset` of 0.01 m conflicts with the vehicle's half-thickness of 0.0103 m. The default `angularDamping` of 0.05 is set to 0 |
| Unity CLI declarations | How `[CliCommand]` is declared (`com.unity.pipeline` is at experimental version 0.7 and this could not be obtained from public documentation) |
| Flow from images | Whether `AsyncGPUReadback` is available under WebGL2 (an optional later step, so it does not block work) |
| Vehicle mesh provenance | Moved in from `kouhei1970/stampfly_sim`; the original CAD author and terms of use. **Still unconfirmed as of 2026-09-21.** Since the port went ahead without waiting for this (§4, "Vehicle appearance"), the fact that it is unconfirmed is recorded in `simulator/shared/assets/meshes/README.md`. That README and this row are updated once it is established |

## 9. Feasibility Record (Stage 1)

Stage 1's results are recorded here with numbers before stage 2 begins. **(a) through (e) were performed on 2026-09-20.** (a), (c), (d) and (e) pass. (b) passes too: a defect found on the way — the flight result depended on memory layout — was root-caused and fixed the same day. (f) needs the real controller and is not yet performed. The deliverable, the full numbers, and the reproduction steps are in [`../../simulator/unity/native/README.md`](../../simulator/unity/native/README.md).

| Item | What is measured or confirmed | Result | Verdict |
|---|---|---|---|
| (a-0) Emscripten build | Whether the unmodified firmware, the simulator, `devices/`, and `rtos/` compile | All 97 translation units compile under Emscripten 6.0.8-git, with nothing under `firmware/` edited. Only two workarounds were needed: the `stdout` reassignment in `tasks/cli_task.cpp` (musl makes `stdout` const, so `simulator/unity/native/compat_wasm/cstdio` is placed earlier on the include path — the same technique the existing `esp_idf_host/cstdio` uses for Windows), and the MuJoCo header in `plant.hpp` (guarded by `SILS_PLANT_EXTERNAL`) | **Pass** |
| (a-1) Fiber scheduler | Whether the `rtos_smoke` trace matches the current one | The sha256 prefix of the entire `trace_dump` output is `48852b7d2bddad63` for all three builds — thread/native, fiber/native (ucontext), and fiber/wasm (Asyncify) — over 425 events | **Pass** |
| (a-2) Approach 2 speed (Node.js) | Real time per second of simulation (target: 0.3 s or less) | All 14 tasks plus the verification plant (no MuJoCo; a simple 6-DOF integrator in C++) ran under Node.js v24 WebAssembly: 60 simulated seconds in 0.155 s of real time = **0.0026 s per simulated second**, roughly 100× the target's margin. `-O2`, whole-program Asyncify left in place, 412 KiB of wasm. Apple M2 Max | **Pass** |
| (a-2') Approach 2 speed (Chrome) | The same, measured in the browser | The same `.js` and `.wasm` were loaded from a plain HTML page (`Module.arguments=['60']`), served from 127.0.0.1 by `python3 -m http.server`, and run in Chrome: 60 simulated seconds in 1.031 s of real time = **0.0172 s per simulated second**, roughly 17× the target's margin. No special HTTP headers such as COOP/COEP were set | **Pass** |
| (a-3) Approach 1 speed | The same entry point built with `-pthread` and measured under Node.js (possible since stage 2 added `scheduler_step.cpp`; Apple M2 Max, 30 simulated seconds) | 0.0142–0.0163 s per simulated second, wasm 328,664 bytes. Approach 2 under the same conditions: 0.0029 s and 422,471 bytes, so **approach 2 is about 4.9× faster**. Both hover (peak 0.813 m settling to 0.577 m). Not measured in Chrome with COOP/COEP headers | Pass (supports the choice of approach 2; 2026-09-20) |
| (a-4) Hover achieved | Whether the vehicle takes off and hovers | The firmware progressed INIT → IDLE\_GROUND → ARMED\_GROUND → FLYING, peaked at 0.813 m, and settled to hold 0.576–0.577 m. Two Node.js runs produced identical output; Chrome produced the same values | **Pass** |
| (a-5) Synchronicity of `run_until` | Whether it returns as a synchronous function despite Asyncify | Called over 12,000 times within a single `main`, terminating normally. The premise of calling it synchronously from `.jslib` — item (b) — therefore holds | **Pass** |
| (a-6) Effect on existing files | That existing builds produce unchanged results | The only change is `simulator/sils/plant/plant.hpp`: 18 lines added, none removed (three `#ifndef SILS_PLANT_EXTERNAL` guards plus an explanatory comment). With the macro undefined the preprocessed token sequence is unchanged (verified with `clang++ -E -P`) | **Pass** |
| (b) Synchronous call from `.jslib` | Whether a separate module's `sfu_step` can be called synchronously, and whether recreating it restarts the firmware | The synchronous call works. 12,000 consecutive ticks in Chrome take 21–29 µs each (about 90× margin against the 2500 µs tick), 0.0083–0.0116 s per simulated second. 16 power cycles (calling `createSfuFirmware()` again) each start from INIT; wasm memory is 67,108,864 bytes every time and the JavaScript heap stays flat. **Known defect**: the flight result depends on memory layout. One `_malloc(48)` before boot turns a successful hover (peak 0.813 m, then 0.577 m) into a failure by "Impact detected: 8.0G". The `.jslib` allocates before boot, so through Unity the flight fails every time. For a fixed layout the result is fully reproducible. The "two identical runs" of (a-4) were two runs from the same path, so this split was not visible. **The cause was the fiber scheduler allocating task stacks with `malloc`** (8-byte alignment on wasm32): locals placed on the assumption of a 16-byte aligned stack overlapped and corrupted each other. Replaced with `aligned_alloc(16, ...)`; results now agree across 32 heap offsets, including the `.jslib`'s 48 bytes (`simulator/unity/native/spike/heap_layout_check.mjs`) | Pass (2026-09-20, defect fixed). Flying from the Unity screen is confirmed in stage 3 |
| (c) PhysX stability | 400Hz at 37 g with inertia on the order of 1e-5; torque response within ±1%; no oscillation on landing | 16 PlayMode tests pass (PhysX in the macOS editor; not checked inside a WebGL player). Torque response error 0.0000% on all three axes. A 0.5 m drop settles at 0.010300 m (half the box thickness) with 5.5e-5 m of motion over the next 2 s. **PhysX does not integrate the gyroscopic term −ω×(Iω)**: the world angular momentum swings about ω, 15.1619° at t = 1 s, equal to the analytic 15.16189° and unchanged from 400 to 6400 Hz. It must be added explicitly. Evaluated at the start of the step the magnitude grows 10.77% per second; **evaluated at the midpoint, 0.006%** (`GyroscopicTerm.BodyTorqueAtMidpoint` is the implemented default) | Pass (2026-09-20) |
| (d) WebGL standard I/O | That `prompt()` does not open | `-sFILESYSTEM=0` removes the stdin device altogether; over 30 simulated seconds with every task running, including the CLI task, `prompt` / `confirm` / `alert` were called 0 times. stderr prints 190 lines at boot, discarded by default and shown only on request | Pass (2026-09-20) |
| (e) Unity CLI | Whether `install -m webgl` / `build` / `test` work locally, and whether `unity command` arrives during play | Unity CLI 1.0.0-beta.8, editor 6000.6.2f1 with the Web module. `unity build` (WebGL) exits 0 in about 2 minutes, 12 MB output. `unity test` runs the PlayMode tests. The exact command forms are in `simulator/unity/README.md`. `install` was not run because the editor was already installed. **A background tab receives no `requestAnimationFrame`, so Unity does not start there** (automated Chrome checks replace it with a Worker timer). 60 fps and a real-time ratio of 1.0 in a foreground tab are not confirmed | Pass (2026-09-20). Rendering speed in a foreground tab is checked in stage 3 |
| (f) Gamepad | How the real controller appears through Chrome's Gamepad API (axis and button ordering) | Not yet performed (needs the real controller) | Not yet performed |
| (g) Automated flight in the browser (stage 3) | Whether the real firmware can be flown ARM → takeoff → ALT_HOLD → landing automatically in Chrome, and followed end to end in the structured log | `sf unity check fly` passed all eight checks (2026-09-21, an automated tab with `?raf=worker`, `empty_room`). The transitions were **INIT → IDLE_GROUND(4.00s) → ARMED_GROUND(5.02s) → FLYING(6.30s) → IDLE_GROUND(22.32s)**, the altitude while holding **0.380..0.520 m** (peak 0.467 m, landed 0.0103 m), tilt 0.0 degrees, and one tick cost **42..150 microseconds** (17x to 60x of headroom against the 2500 us tick). In the log one `cmd_id` puts cli → server → page → server → cli in time order; transitions appear as `sim.flight_state` with `sim_us`, pacing as a once-a-second `sim.stats`, no per-tick line is written, and **no line was rejected by the server**. `fw.boot` and `sim.step_overrun` appear too | **Does not pass reliably (2026-09-21)**. The implementing agent's run passed all eight checks, but another run by the same agent and the coordinator's re-run of the same build failed: after holding about 0.40 m in ALT_HOLD for some five seconds the vehicle started descending at about 0.3 m/s from virtual time 11.8 s, hit the floor at 13.1 s, and the firmware raised `Impact detected: 7.9G` and an emergency DISARM (in the agent's failing run it drifted up first, then fell). The impact detection is the correct reaction to hitting the floor; the defect is the altitude hold coming apart. The cause is not identified. The log (`logs/unity/<run_id>.jsonl`) kept the firmware's `failsafe` line with its `sim_us`, which located the moment at once. 60 fps and a real-time ratio of 1.0 in a foreground tab are **unverified** — an automated tab cannot measure them; the steps for a person are in `simulator/unity/README.md` section 9 |
| The real-time ratio defect (found and fixed in stage 3) | Whether the real-time ratio ever exceeds 1.0 | **It did.** A debt accumulated in a backgrounded or throttled tab was then paid off at the twelve-tick ceiling (30 ms of simulation) in every later frame, which against a 16 ms frame measured **1.88**. A stall is only recognised when one frame exceeds half a second, so a tab throttled to a steady 0.4 s never trips it and accumulates indefinitely. `SimClock` now caps the debt itself at what one frame can pay off, and `SimClockTest`'s `AtNormalSpeedTheSimulationNeverOutrunsRealTime` and `TheBacklogNeverGrowsBeyondOneFramesWorth` hold it | Fixed (2026-09-21) |
| Conclusion | The chosen threading approach (1 or 2) | **Approach 2 is chosen**: the firmware becomes a separate wasm module driven by the fiber scheduler. It beats the speed target by about 100× under Node.js and 17× in Chrome, reproduces the trace bit-for-bit, and needs no special HTTP headers | **Decided** |

### On the Node.js / Chrome Difference

Chrome (0.0172 s per simulated second) is about 6.6× slower than Node.js (0.0026 s). **The cause has not been isolated.** The page used for the measurement emits roughly 200 lines of output one line at a time, via `console.log` and by appending to the DOM, and on a first load it starts running before WebAssembly's optimizing compilation has finished. Either may contribute, but this is conjecture and was not tested. Both figures are far under the target, so stage 1 did not pursue it; the measurement will be repeated with output suppressed in stage 3, when the real-time ratio of 1.0 is measured.
