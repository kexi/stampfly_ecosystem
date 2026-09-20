# Unity 版シミュレータ — ネイティブコア

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。

## 1. 概要

### このディレクトリについて

`docs/plans/unity-simulator.md`（Unity 版シミュレータ計画）の **段階 2 ネイティブコア** の実装を置く。
無改変のファームウェア・MuJoCo を使わないプラント・スケジューラを 1 つの静的ライブラリにまとめ、
Unity が呼べる C ABI（接頭辞 `sfu_`）を被せたものである。Unity はまだ無い。
この段階の成果物は、Unity 抜きで飛ぶ 3 種類の実行形態である。

段階 1(a) の技術検証（無改変ファームが Emscripten でビルドでき、ブラウザで実時間より十分速く
回るか）は合格済みで、その数値は §6 に残してある。`spike/` のものはその検証用で、段階 2 の
実装とは別に残している。

### 対象読者

- 段階 3（Unity プロジェクトの新設）に入る担当者。C ABI の一覧と、C# 側が守る取り決めを示す
- ネイティブコアを改修する担当者。ビルドと検査の手順、再現した数値を示す

### 何が動くか

| 実行形態 | 剛体を持つ側 | 確かめること |
|---|---|---|
| `sfu_bridge_smoke` | プラント（内蔵の簡易 6 自由度積分） | Unity 無しで ARM → 離陸 → ホバリングが成立する |
| `sfu_external_smoke` | C++ で書いた Unity の代役 | 外部供給の経路が成立し、ABI を渡る力の大きさ・符号・座標系が正しい |
| `sfu_module_check.mjs` | JavaScript で書いた Unity の代役 | 出荷する wasm モジュールが JS から使え、構造体の配置が一致する |

3 つとも、ネイティブ（スレッド版スケジューラ）と WebAssembly（fiber 版スケジューラ）の
両方で同じホバリングに至る。

## 2. 結果

### 最小動作確認（Apple M2 Max、シミュレーション 30 秒）

| 実行形態 | ビルド | 1 秒あたりの実処理時間 | 最大高度 | 最終高度 | 判定 |
|---|---|---|---|---|---|
| `sfu_bridge_smoke` | ネイティブ（スレッド版） | 0.0091 s | 0.684 m | 0.485 m | hover OK |
| `sfu_bridge_smoke` | wasm（fiber 版）／Node.js | 0.0028 s | 0.684 m | 0.492 m | hover OK |
| `sfu_external_smoke` | ネイティブ（スレッド版） | 0.0085〜0.0106 s | 0.681 m | 0.489 m | hover OK |
| `sfu_external_smoke` | wasm（fiber 版）／Node.js | 0.0029〜0.0030 s | 0.681 m | 0.476 m | hover OK |
| `sfu_module_check.mjs` | wasm モジュール／Node.js | 0.0027〜0.0028 s | 0.681 m | 0.487 m | hover OK |

いずれも合格の目安 0.30 s を大きく下回る。スレッド版がおよそ 3 倍遅いのは、段階 1 で
fiber 版を選んだ理由（計画 §9）と整合する。最終高度の 0.476〜0.492 m の幅は、コンパイラごとの
浮動小数点の丸めと、ホストの積分器の違い（C++ の float と JavaScript の double）から来る。

どちらの最小動作確認も、**同じ実行を 2 回して標準出力が完全に一致する**（`just unity-native-test`
が `diff` で確かめる）。速度の数値だけは標準エラー出力へ出す。実時間の測定値は繰り返さないため、
標準出力に混ぜると一致の判定が壊れるからである。

### 成果物の大きさ

| 成果物 | 大きさ | 用途 |
|---|---|---|
| `sfu_firmware.wasm` | 442,003 バイト | Unity WebGL が `.jslib` 越しに読み込む |
| `sfu_firmware.js` | 72,043 バイト | 同上（`createSfuFirmware` を出す読み込み側） |
| `libsfu_firmware.dylib` | 518,536 バイト | Unity エディタ（macOS arm64）の開発用プラグイン |

dylib が公開する記号は `sfu_*` の 14 個だけである（`nm -gU` で確認）。ファーム・プラント・
スケジューラの記号は `-fvisibility=hidden` の裏に隠れたままで、Unity のエディタのプロセスの
中から名前で届くものは無い。

### 既存のビルドが変わらないこと

`simulator/sils/CMakeLists.txt` のソース収集を `cmake/firmware_sources.cmake` へ括り出した。
括り出しの前後で `cmake` が生成したビルド系から全ターゲットのソース一覧を取り出して
突き合わせた結果、**47 ターゲット・485 ソースが完全に一致**した（新設した
`actuator_parity_test` の 2 ソースを除く）。手順は §5 にある。

併せて、括り出し後のビルドで `emu_vehicle`・`rtos_smoke`・`frames_test` を作り直し、
`rtos_smoke` のトレースハッシュが従来どおり `0x66abc4f49ec8e9b1` であること、`frames_test` が
ALL PASS であること、`emu_vehicle` が 1 秒ぶん走って `state=1 sub_mode=1 armed=0` で終わることを
確かめた。

## 3. 構成

### C ABI（`bridge/sfu_api.h`）

Unity が呼ぶ入口は 14 個である。渡すものはすべて **Unity 自身の規約**（左手系・Y 上・
クォータニオンは x,y,z,w の順・単位は m と秒と rad/s）で、NED／FRD への変換は C++ 側の
`simulator/sils/frames/frames_unity.hpp` だけで行う。

| 入口 | 役目 |
|---|---|
| `sfu_abi_version()` | ABI の版。他の何より先に突き合わせる |
| `sfu_struct_size(which)` | 本モジュールが構造体をどの大きさにコンパイルしたか。C# 側が自分の `sizeof` と比べる |
| `sfu_boot(&config)` | 電源投入。プラント初期化 → ペアリングの記録 → `app_main`（BSP ＋ 14 タスク）。2 回目はエラー |
| `sfu_shutdown()` | タスクの巻き戻し。省略可 |
| `sfu_step(&in, &out)` | 主経路。状態を注入 → `run_until(now + dt)` → 区間平均の合力・合トルクと状態を返す |
| `sfu_param_count()` ／ `sfu_param_info(i, &out)` | ファームが公開する調整値（99 個）の数と素性 |
| `sfu_param_set(name, value)` ／ `sfu_param_get(name, &out)` | 名前で読み書き。型は SSOT のテーブルから引く |
| `sfu_set_wind(x, y, z)` | 一定の風力 [N]、Unity の世界系 |
| `sfu_set_motor_health(motor, gain)` | モータごとの推力の健全度（1 = 正常、0 = 停止） |
| `sfu_set_imu_bias(ax, ay, az, gx, gy, gz)` | 一定の生 IMU バイアス、機体ローカルの Unity 系 |
| `sfu_log_read(buffer, capacity)` ／ `sfu_log_dropped()` | ファームのログをリングバッファから取り出す |

構造体は 4 つあり、いずれも先頭に `uint32_t struct_size` を置き、呼び出し側が自分の宣言の
`sizeof` を書き込む。橋渡しがそれと比べ、食い違えば `SFU_ERR_STRUCT_SIZE` を返す。短い構造体の
末尾を越えて読む代わりにその場で拒否するためであり、後の版が構造体を末尾に伸ばしても古い
呼び出し側が壊れないためでもある。

この ABI での大きさ（ネイティブ arm64・wasm32 のどちらも同じ）: `SfuConfig` 48 バイト、
`SfuStepIn` 96 バイト、`SfuStepOut` 160 バイト。

### 1 刻みの処理

`sfu_step` の中で起きることは次のとおりである。

1. ホストの状態（位置・回転・世界系の速度と角速度・機体系の加速度計の測定値）を
   `frames_unity.hpp` で NED／FRD へ直し、プラントへ注入する
2. レイキャストの結果（下向き ToF の距離と有効フラグ、地表からの高さ）を注入する
3. RC の最新値を保持する。**送信はここでしない**
4. `run_until(now + dt)` でファームを進める。その途中、スケジューラの `on_advance` が
   仮想時間の経過ぶんだけプラントを進め、**仮想時間の 50 Hz** で RC を ESP-NOW へ注入する
5. `takeWrench()` で区間平均の合力・合トルク・風の力を取り出し、Unity 系へ直して返す
6. duty・モータ角速度・電池電圧・仮想時刻・ファームの状態（armed・飛行状態・推定姿勢・
   推定位置・真の姿勢と位置）を返す

RC の注入を仮想時間で行うことが、刻みが RC 周期より長くても送信が詰まらない理由である。
100 ms の刻み 1 回でも、送るのは 5 個であって、刻みあたり 1 個でも 50 個でもない。

### 剛体を持つ側の切り替え

`SfuConfig::host_owns_body` が 1 なら Unity 側が剛体を持ち、`sfu_step` は毎回ホストの状態を
注入してプラントは力を溜めるだけになる。0 ならプラント自身の簡易 6 自由度の積分器が動き、
`SfuStepIn` の状態の欄は読まれず、`SfuStepOut` の `truth_position` ／ `truth_rotation` が
プラントの置いた機体の位置を返す。`sfu_bridge_smoke` は後者で飛ぶ。

### 新設したファイル

| パス | 役割 |
|---|---|
| `bridge/sfu_api.h` | C ABI の宣言。C# 側はこれと同じ構造体を自分で宣言する |
| `bridge/sfu_bridge.cpp` | その実装。起動手順は `simulator/sils/emu/emu_main.cpp` に倣う |
| `bridge/sfu_log_ring.cpp` | ファームのログのリングバッファ（512 行 × 256 バイト） |
| `bridge/shim/esp_log.h` | `ESP_LOGx` の行き先を stderr からそのリングバッファへ変える |
| `bridge/sfu_rc_script.hpp` | 2 つの最小動作確認が共有する台本の操縦入力 |
| `bridge/sfu_bridge_smoke.cpp` | Unity 無しの最小動作確認（プラントが剛体を持つ） |
| `bridge/sfu_external_smoke.cpp` | 外部供給の最小動作確認（C++ が Unity の代役） |
| `bridge/sfu_module_check.mjs` | wasm モジュールを JS から飛ばす確認（JS が Unity の代役） |
| `bridge/sfu_module_main.cpp` | 出荷するモジュールがリンクする翻訳単位。論理は持たない |
| `bridge/sfu_exports.txt` | dylib が公開する記号の一覧 |
| `CMakeLists.txt` | 独立したプロジェクト。MuJoCo を取得しない |
| `simulator/sils/cmake/firmware_sources.cmake` | ファームのソース一覧。SILS と共有する |

### ファームを無改変で差し替える仕組み

include の順序だけで行う。先にあるものが勝つので、同名のヘッダを前に置けば、`firmware/` を
1 バイトも編集せずに行き先を変えられる。

| 前に置くヘッダ | 差し替えるもの | 理由 |
|---|---|---|
| `bridge/shim/esp_log.h` | `simulator/sils/compat/esp_log.h` | ブラウザにはプロセスの stderr を読み戻せるものが無い |
| `compat_wasm/cstdio` | 標準の `<cstdio>` | musl の `stdout` は const で、`cli_task.cpp` が再代入する |
| `rtos_fiber/scheduler.hpp` | `simulator/sils/rtos/scheduler.hpp` | wasm ではスレッドを使わない（fiber 版へ向ける） |

`esp_idf_host/cstdio` が Windows 向けに使っているのと同じ手口である。

## 4. ビルドと検査

Emscripten・`cmake`・`ninja`・Node.js はリポジトリ直下の `flake.nix` の開発シェルから来る。
ネイティブのコンパイラは Xcode の `/usr/bin/clang++` を使う。

```bash
# ネイティブ（macOS arm64 の dylib）と wasm（WebGL 用モジュール）の両方をビルドする
just unity-native-build

# 全ての検査を実行する（ビルドも含む）。引数はシミュレーションする秒数、既定 30
just unity-native-test 30
```

`unity-native-test` が回すもの:

1. `frames_unity_test` — Unity の座標変換の単体試験（28 項目）。ネイティブと wasm の両方
2. `sfu_bridge_smoke` を 2 回実行し、標準出力を `diff` で突き合わせる
3. `sfu_external_smoke` を 2 回実行し、同じく突き合わせる
4. 同じ 3 つを wasm（Node.js）でも実行する
5. `sfu_module_check.mjs` — 出荷する `sfu_firmware.js` を JS から読み込み、構造体の大きさを
   `sfu_struct_size` と突き合わせてから 30 秒飛ばす

`actuator_parity_test`（`actuator_model.hpp` と MuJoCo 版 `plant.cpp` の電池電圧がビット一致
することを確かめる）は MuJoCo を要するため、`simulator/sils` 側のターゲットにしてある。

```bash
cmake --build <sils の build> --target actuator_parity_test
<sils の build>/actuator_parity_test simulator/sils/models/stampfly.xml
```

ビルドの生成物（`build-*/`、`.cache/`）は git に入れない（`.gitignore` 済み）。

## 5. ソース一覧が変わっていないことの確かめ方

`simulator/sils/CMakeLists.txt` の 299-322 行付近にあったソース収集を
`cmake/firmware_sources.cmake` へ括り出した。既存のターゲットのソース一覧が 1 つも変わって
いないことは、次の手順で確かめた。

1. 括り出し**前**の `CMakeLists.txt` で `cmake -G Ninja` を実行し、ビルド系を作る
2. `ninja -t targets all` で `*.dir/*.o` の形のオブジェクトを全て挙げ、それぞれに
   `ninja -t query` を掛けて入力のソースを引く。結果を「ターゲット名 ＋ ソースのパス」の
   行にして並べ替える
3. 括り出し**後**の `CMakeLists.txt` で同じことを行う
4. 2 つを `diff` する

`ninja -t query` を使うのは、`compile_commands.json` が `emu_vehicle` を含まなかったためである
（同ファイルはこの構成では一部のターゲットしか載せない）。ビルド系から引けば、CMake が実際に
何をコンパイルするかを漏れなく取れる。

結果は **47 ターゲット・485 ソースが完全に一致**した。差は 2 つだけで、どちらも意図したもので
ある。新設した `actuator_parity_test` の 2 ソースと、ビルドディレクトリの下に取得される
`miniz` の 4 ソースのパス（ビルドディレクトリ名が違うため）。

## 6. 段階 1(a) 技術検証の記録

段階 2 の実装より前に行った技術検証の結果である。問いは 1 つだった。

> 無改変のファームウェアが Emscripten でビルドでき、ブラウザで実時間より十分速く回るか。

**結果: 通った。** シミュレーション 1 秒あたりの実処理時間は、Node.js の WebAssembly で
**0.0029〜0.0030 秒**（合格の目安 0.30 秒に対し約 100 倍の余裕）、**Chrome で 0.0172 秒**
（同じく約 17 倍の余裕）。無改変のファームウェアが ARM → 離陸 → ALTITUDE_HOLD と進み、
最大高度 0.813 m から 0.577 m へ落ち着いてホバリングを保つ。

計測時の Emscripten は 6.0.8-git（計画の執筆時に見込んでいた 3.1.38 ではない）。版は
`flake.nix` の開発シェルが与えるもので、`flake.lock` の更新で上がる。

| 確認項目 | 内容 | 結果 |
|---|---|---|
| 1 | 無改変ファーム＋シム＋`devices/`＋`rtos/` を Emscripten でコンパイル | 97 翻訳単位すべて成功（`firmware/` は 1 バイトも編集していない） |
| 2 | スレッド無しの fiber 型スケジューラでトレースが現行と一致するか | 425 事象・ハッシュ・最終制御出力まで**バイト単位で一致**（`0x66abc4f49ec8e9b1`、出力全体の sha256 先頭 `48852b7d2bddad63`） |
| 3 | 全 14 タスク＋検証用プラントで離陸・ホバリングと速度計測 | 0.0029 s/s、ホバリング成立、wasm 412 KiB |

この検証の成果物は `spike/` に残してある。`just unity-native-spike 30` で作り直して実行できる。

```bash
# 段階 1(a) の技術検証を作り直し、N 秒ぶん飛ばす（既定 30）
just unity-native-spike 30
```

Chrome の実測は同じ `.js`／`.wasm` を素の HTML ページから読み込み、`python3 -m http.server` で
127.0.0.1 から配信して行った。COOP/COEP 等の特別な HTTP ヘッダは要らない。この計測用ページは
使い捨てのためリポジトリには含めていない。Chrome が Node.js より約 6.6 倍遅い理由は
**切り分けていない**。計画 §9 の「Node.js と Chrome の差について」に経緯がある。

## 7. 段階 3 への引き継ぎ

| 事項 | 内容 |
|---|---|
| C# 側で座標変換を書かないこと | `Rigidbody` の `position`・`rotation`・`linearVelocity`・`angularVelocity` をそのまま渡す（符号反転も軸の入替もしない）。`angularVelocity` は世界系のまま渡す。返る力とトルクは Unity の機体系なので `AddRelativeForce`・`AddRelativeTorque` にそのまま渡せる。詳しくは `frames_unity.hpp` の冒頭と `simulator/sils/docs/coordinate_frames.md` §7.5 |
| 構造体の宣言 | C# 側は `sfu_api.h` と同じ 4 つの構造体を自分で宣言する。最初の呼び出しの前に `sfu_struct_size` で自分の `sizeof` と突き合わせること。`sfu_module_check.mjs` が JavaScript で同じことをしているので手本になる |
| 1 刻みの手順の手本 | `bridge/sfu_external_smoke.cpp` が `SimLoop.Update()` と同じ順序で書いてある。状態を詰める → `sfu_step` → 力を加える → 積分する → 次の刻み用の加速度計の測定値を作る |
| `takeWrench` の `dt_s` | 2.5 ms 固定ではない。プラントの副刻みの端数が繰り越されるため、最初の刻みは 2.25 ms になりうる。返ってきた `wrench_dt_s` を使うこと |
| エディタ用 dylib の読み込み直し | 再生のたびに読み込み直す仕組みは Unity プロジェクトと一緒に作る。段階 2 では dylib までとした |
| 接触モデル | `sfu_external_smoke.cpp` の床は z=0 のばね・ダンパで、PhysX の接触ソルバではない。空中の軌跡は近いが、接地の瞬間から分かれる |
| スタックの大きさ | タスクごとに 1 MiB（14 タスクで 14 MiB）。ファームの `config::STACK_*` は 32bit 向けの値なので従っていない |
| `plant.cpp` との重複 | `actuator_model.hpp` は `plant.cpp` からの写しであり、現時点では同じ式が 2 組ある。食い違わないことを守るのが `actuator_parity_test` の役目で、CI に入れることが望ましい |

---

<a id="english"></a>

# Unity Simulator — Native Core

## 1. Overview

### About This Directory

Holds the **stage 2 native core** from `docs/plans/unity-simulator.md` (the Unity simulator
plan): the unmodified firmware, the MuJoCo-free plant and the scheduler gathered into one
static library, behind a C ABI (prefix `sfu_`) that Unity can call. There is no Unity yet.
What this stage delivers is three ways to fly without it.

Stage 1(a)'s feasibility measurement — can the unmodified firmware build with Emscripten and
run comfortably faster than real time in a browser — passed, and its numbers are kept in §6.
The contents of `spike/` belong to that measurement and are kept separate from the stage 2
implementation.

### Target Audience

- Whoever starts stage 3 (the Unity project): the list of C ABI entry points and the contract
  the C# side holds to
- Whoever changes the native core: how to build it, how to check it, and the numbers reproduced

### What Flies

| Executable | Who owns the rigid body | What it establishes |
|---|---|---|
| `sfu_bridge_smoke` | The plant (its own simple 6-DOF integrator) | ARM → take-off → hover without Unity |
| `sfu_external_smoke` | A C++ stand-in for Unity | The externally supplied path works, and the force crossing the ABI has the right magnitude, sign and frame |
| `sfu_module_check.mjs` | A JavaScript stand-in for Unity | The shipped wasm module is usable from JS and its struct layout agrees |

All three reach the same hover under both the native build (thread scheduler) and the
WebAssembly build (fiber scheduler).

## 2. Results

### Minimum-Operation Checks (Apple M2 Max, 30 simulated seconds)

| Executable | Build | Wall-clock per simulated second | Peak altitude | Final altitude | Verdict |
|---|---|---|---|---|---|
| `sfu_bridge_smoke` | native (thread) | 0.0091 s | 0.684 m | 0.485 m | hover OK |
| `sfu_bridge_smoke` | wasm (fiber) / Node.js | 0.0028 s | 0.684 m | 0.492 m | hover OK |
| `sfu_external_smoke` | native (thread) | 0.0085–0.0106 s | 0.681 m | 0.489 m | hover OK |
| `sfu_external_smoke` | wasm (fiber) / Node.js | 0.0029–0.0030 s | 0.681 m | 0.476 m | hover OK |
| `sfu_module_check.mjs` | wasm module / Node.js | 0.0027–0.0028 s | 0.681 m | 0.487 m | hover OK |

All are far under the 0.30 s pass criterion. The thread build being about three times slower is
consistent with why stage 1 chose the fiber scheduler (plan §9). The 0.476–0.492 m spread in the
final altitude comes from per-compiler floating-point rounding and from the host integrators
differing (C++ `float` against JavaScript's doubles).

Both smoke checks **print identical stdout across two runs** (`just unity-native-test` confirms
it with `diff`). Only the speed figure goes to stderr: a wall-clock measurement never repeats,
and mixing it into stdout would break that check.

### Artefact Sizes

| Artefact | Size | Purpose |
|---|---|---|
| `sfu_firmware.wasm` | 442,003 bytes | What Unity WebGL loads through `.jslib` |
| `sfu_firmware.js` | 72,043 bytes | The loader beside it, exporting `createSfuFirmware` |
| `libsfu_firmware.dylib` | 518,536 bytes | The development plugin for the Unity editor (macOS arm64) |

The dylib exposes exactly the 14 `sfu_*` symbols (confirmed with `nm -gU`). The firmware, plant
and scheduler symbols stay behind `-fvisibility=hidden`, so nothing inside the Unity editor's
process can reach one by name.

### That Existing Builds Are Unchanged

The source collection in `simulator/sils/CMakeLists.txt` moved out to
`cmake/firmware_sources.cmake`. Extracting every target's source list from the generated build
system before and after the move and comparing them showed **47 targets and 485 sources
identical**, apart from the two sources of the new `actuator_parity_test`. The method is in §5.

The post-move build was also used to rebuild `emu_vehicle`, `rtos_smoke` and `frames_test`:
`rtos_smoke`'s trace hash is `0x66abc4f49ec8e9b1` as before, `frames_test` is ALL PASS, and
`emu_vehicle` runs a simulated second and ends at `state=1 sub_mode=1 armed=0`.

## 3. Structure

### The C ABI (`bridge/sfu_api.h`)

Fourteen entry points. Everything crossing the boundary is in **Unity's own conventions**
(left-handed, Y up, quaternion in x,y,z,w order, metres, seconds, radians per second); the
conversion to NED/FRD happens only in `simulator/sils/frames/frames_unity.hpp`.

| Entry point | Role |
|---|---|
| `sfu_abi_version()` | The ABI revision; checked before anything else |
| `sfu_struct_size(which)` | The size this module compiled a struct to, for the C# side to check its own `sizeof` against |
| `sfu_boot(&config)` | Power on: plant init → seed the pairing store → `app_main` (BSP plus 14 tasks). A second call is an error |
| `sfu_shutdown()` | Unwind the tasks. Optional |
| `sfu_step(&in, &out)` | The main path: inject the state → `run_until(now + dt)` → return the interval-averaged force and torque plus the state |
| `sfu_param_count()` / `sfu_param_info(i, &out)` | How many tuning parameters the firmware exposes (99) and their identity |
| `sfu_param_set(name, value)` / `sfu_param_get(name, &out)` | Read and write by name; the type comes from the SSOT table |
| `sfu_set_wind(x, y, z)` | Constant wind force [N] in Unity's world frame |
| `sfu_set_motor_health(motor, gain)` | Per-motor thrust health (1 healthy, 0 dead) |
| `sfu_set_imu_bias(ax, ay, az, gx, gy, gz)` | Constant raw IMU bias, body-local Unity frame |
| `sfu_log_read(buffer, capacity)` / `sfu_log_dropped()` | Take the firmware's log out of the ring buffer |

Each of the four structs starts with `uint32_t struct_size`, which the caller fills with the
`sizeof` of its OWN declaration. The bridge compares and returns `SFU_ERR_STRUCT_SIZE` on a
mismatch, rather than reading past the end of a shorter struct — and so that a later version can
grow a struct at the end without breaking an older caller.

Sizes under this ABI (the same for native arm64 and for wasm32): `SfuConfig` 48 bytes,
`SfuStepIn` 96 bytes, `SfuStepOut` 160 bytes.

### What Happens in One Tick

Inside `sfu_step`:

1. The host's state (position, rotation, world-frame velocity and angular velocity, body-frame
   accelerometer reading) is converted to NED/FRD by `frames_unity.hpp` and injected
2. The raycast results (downward ToF distance and validity, height above the surface below) are
   injected
3. The latest stick values are stored. **Nothing is transmitted here**
4. `run_until(now + dt)` advances the firmware. Along the way the scheduler's `on_advance` steps
   the plant by the elapsed virtual time and injects the sticks into ESP-NOW at **50 Hz of
   virtual time**
5. `takeWrench()` yields the interval-averaged force, torque and wind force, converted back to
   Unity's frame
6. Duty, propeller speed, battery voltage, the virtual clock and the firmware's state (armed,
   flight state, estimated attitude and position, true attitude and position) come back

Injecting on virtual time is why a tick longer than the transmitter's period never queues up a
burst: one 100 ms tick sends five packets, not one per tick and not fifty.

### Switching Who Owns the Rigid Body

With `SfuConfig::host_owns_body` set to 1, Unity owns the body: every `sfu_step` injects the
host's state and the plant only accumulates forces. With 0, the plant's own simple 6-DOF
integrator runs, the state fields of `SfuStepIn` are ignored, and `SfuStepOut`'s
`truth_position` / `truth_rotation` report where the plant put the vehicle. `sfu_bridge_smoke`
flies with the latter.

### New Files

| Path | Role |
|---|---|
| `bridge/sfu_api.h` | The C ABI declarations; the C# side declares the same structs itself |
| `bridge/sfu_bridge.cpp` | Its implementation; the startup follows `simulator/sils/emu/emu_main.cpp` |
| `bridge/sfu_log_ring.cpp` | The firmware's log ring buffer (512 lines of 256 bytes) |
| `bridge/shim/esp_log.h` | Redirects `ESP_LOGx` from stderr into that ring buffer |
| `bridge/sfu_rc_script.hpp` | The scripted stick input both smoke checks share |
| `bridge/sfu_bridge_smoke.cpp` | The minimum-operation check without Unity (the plant owns the body) |
| `bridge/sfu_external_smoke.cpp` | The externally supplied check (C++ stands in for Unity) |
| `bridge/sfu_module_check.mjs` | Flies the wasm module from JS (JS stands in for Unity) |
| `bridge/sfu_module_main.cpp` | The translation unit the shipped module links; holds no logic |
| `bridge/sfu_exports.txt` | The symbols the dylib exposes |
| `CMakeLists.txt` | A project of its own; does not fetch MuJoCo |
| `simulator/sils/cmake/firmware_sources.cmake` | The firmware source list, shared with the SILS |

### How the Firmware Is Redirected Without Being Edited

Entirely through include order. Earlier wins, so a header of the same name placed earlier
changes where an include lands, with not one byte of `firmware/` edited.

| Header placed earlier | What it replaces | Why |
|---|---|---|
| `bridge/shim/esp_log.h` | `simulator/sils/compat/esp_log.h` | A browser has nothing that can read a process's stderr back |
| `compat_wasm/cstdio` | The standard `<cstdio>` | musl's `stdout` is const, and `cli_task.cpp` reassigns it |
| `rtos_fiber/scheduler.hpp` | `simulator/sils/rtos/scheduler.hpp` | The wasm build uses no threads (it points at the fiber version) |

This is the same technique `esp_idf_host/cstdio` uses for Windows.

## 4. Building and Checking

Emscripten, `cmake`, `ninja` and Node.js come from the development shell defined by `flake.nix`
at the repository root. The native compiler is Xcode's `/usr/bin/clang++`.

```bash
# Build both: the macOS arm64 dylib and the WebGL module
just unity-native-build

# Run every check (building first). The argument is the simulated seconds, default 30
just unity-native-test 30
```

What `unity-native-test` runs:

1. `frames_unity_test` — the Unity coordinate-transform unit test (28 checks), native and wasm
2. `sfu_bridge_smoke` twice, with the two stdout streams compared by `diff`
3. `sfu_external_smoke` twice, compared the same way
4. The same three under wasm (Node.js)
5. `sfu_module_check.mjs` — loads the shipped `sfu_firmware.js` from JS, checks the struct sizes
   against `sfu_struct_size`, and flies 30 seconds

`actuator_parity_test` (battery voltage bit-identical between `actuator_model.hpp` and the
MuJoCo `plant.cpp`) needs MuJoCo, so it is a target on the `simulator/sils` side.

```bash
cmake --build <the sils build> --target actuator_parity_test
<the sils build>/actuator_parity_test simulator/sils/models/stampfly.xml
```

Build outputs (`build-*/`, `.cache/`) are not committed (see `.gitignore`).

## 5. How the Source Lists Were Checked

The source collection that sat around lines 299–322 of `simulator/sils/CMakeLists.txt` moved to
`cmake/firmware_sources.cmake`. That no existing target's source list changed was established
like this:

1. Configure the **pre-move** `CMakeLists.txt` with `cmake -G Ninja`
2. List every `*.dir/*.o` object with `ninja -t targets all`, run `ninja -t query` on each to get
   its input source, and emit one sorted "target name + source path" line per object
3. Do the same with the **post-move** `CMakeLists.txt`
4. `diff` the two

`ninja -t query` is used because `compile_commands.json` did not contain `emu_vehicle` (in this
configuration it covers only some targets). Going through the build system captures what CMake
actually compiles, with nothing missed.

The result was **47 targets and 485 sources identical**. There were exactly two differences,
both intended: the two sources of the new `actuator_parity_test`, and the paths of the four
`miniz` sources fetched beneath the build directory (whose names differ).

## 6. The Stage 1(a) Feasibility Record

The measurement that preceded the stage 2 implementation. One question was asked:

> Can the unmodified firmware be built with Emscripten, and does it run comfortably faster than
> real time in a browser?

**Result: yes.** Wall-clock cost per simulated second is **0.0029–0.0030 s** under Node.js
WebAssembly — roughly 100× the margin the 0.30 s pass criterion asks for — and **0.0172 s in
Chrome**, roughly 17× that margin. The unmodified firmware arms, takes off, enters
ALTITUDE_HOLD, and settles from a 0.813 m peak to hold 0.577 m.

The measurements used Emscripten 6.0.8-git, not the 3.1.38 assumed when the plan was written.
The version comes from the `flake.nix` development shell and moves with `flake.lock`.

| Check | Work | Result |
|---|---|---|
| 1 | Compile unmodified firmware + shims + `devices/` + `rtos/` with Emscripten | All 97 translation units compile (not one byte of `firmware/` was edited) |
| 2 | Thread-free fiber scheduler reproduces the current trace | **Byte-identical**: 425 events, hash `0x66abc4f49ec8e9b1`, sha256 of the whole output starting `48852b7d2bddad63` |
| 3 | All 14 tasks + verification plant: take off, hover, measure | 0.0029 s/s, hover holds, 412 KiB of wasm |

That measurement's artefacts are kept in `spike/` and can be rebuilt and run:

```bash
# Rebuild the stage 1(a) feasibility check and fly it for N seconds (default 30)
just unity-native-spike 30
```

The Chrome figure came from loading the same `.js` and `.wasm` from a plain HTML page served
from 127.0.0.1 by `python3 -m http.server`. No special HTTP headers such as COOP/COEP are
needed. That measurement page was throwaway and is not in the repository. Why Chrome is about
6.6× slower than Node.js **has not been isolated**; plan §9's "On the Node.js / Chrome
Difference" records what is known.

## 7. Hand-off to Stage 3

| Item | Detail |
|---|---|
| Do not write coordinate transforms in C# | Pass `Rigidbody.position`, `.rotation`, `.linearVelocity` and `.angularVelocity` straight through — no sign flips, no axis swaps. `angularVelocity` stays in the world frame. The returned force and torque are in Unity's body frame, so they go straight into `AddRelativeForce` and `AddRelativeTorque`. See the head of `frames_unity.hpp` and `simulator/sils/docs/coordinate_frames.md` §7.5 |
| Declaring the structs | The C# side declares the same four structs as `sfu_api.h`, and checks its own `sizeof` against `sfu_struct_size` before the first call. `sfu_module_check.mjs` does exactly this in JavaScript and serves as the worked example |
| The worked example for one tick | `bridge/sfu_external_smoke.cpp` is written in the order `SimLoop.Update()` will use: pack the state → `sfu_step` → apply the forces → integrate → compute the accelerometer reading for the next tick |
| `takeWrench`'s `dt_s` | Not a fixed 2.5 ms. The plant carries its sub-tick remainder over, so the first tick can be 2.25 ms. Use the returned `wrench_dt_s` |
| Reloading the editor dylib | Reloading it between play sessions comes with the Unity project; stage 2 stops at the dylib |
| Contact model | The floor in `sfu_external_smoke.cpp` is a spring-damper at z=0, not PhysX's contact solver. Trajectories are close in the air and part ways on impact |
| Stack sizes | 1 MiB per task (14 MiB for 14 tasks). The firmware's `config::STACK_*` values are 32-bit figures and are deliberately not honoured |
| Duplication with `plant.cpp` | `actuator_model.hpp` was copied out of `plant.cpp`, so the same formulas exist in two places for now. Keeping them from drifting apart is `actuator_parity_test`'s job, and it belongs in CI |
