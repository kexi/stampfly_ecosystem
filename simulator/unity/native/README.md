# Unity 版シミュレータ — ネイティブコア（段階 1(a) 技術検証）

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。

## 1. 概要

### このディレクトリについて

`docs/plans/unity-simulator.md`（Unity 版シミュレータ計画）の **段階 1(a) 技術検証** の成果物を置く。
確かめたのは次の 1 問:

> 無改変のファームウェアが Emscripten でビルドでき、ブラウザで実時間より十分速く回るか。

計測時の Emscripten は 6.0.8-git（計画の執筆時に見込んでいた 3.1.38 ではない）。版は
`flake.nix` の開発シェルが与えるもので、`flake.lock` の更新で上がる（本文書の作成後に
6.0.9-git へ上がっている）。版が変われば速度の数値は変わりうるが、`just unity-native-spike`
が毎回その場で測り直す。

**結果: 通った。** シミュレーション 1 秒あたりの実処理時間は、Node.js の WebAssembly で
**0.0029〜0.0030 秒**（合格の目安 0.30 秒に対し約 100 倍の余裕）、**Chrome で 0.0172 秒**
（同じく約 17 倍の余裕）。無改変のファームウェアが ARM → 離陸 → ALTITUDE_HOLD と進み、
最大高度 0.813 m から 0.577 m へ落ち着いてホバリングを保つ。

### 対象読者

段階 2（ネイティブコアの本実装）以降に入る担当者。計測の数値と、その再現手順を示す。

## 2. 検証の結果

### 確認項目ごとの結果

計画の段階 1(a) を 3 つの確認項目に分けて進めた（番号は本文書内のもので、計画の段階番号とは別）。

| 確認項目 | 内容 | 結果 |
|------|------|------|
| 1 | 無改変ファーム＋シム＋`devices/`＋`rtos/` を Emscripten でコンパイル | 97 翻訳単位すべて成功（`firmware/` は 1 バイトも編集していない） |
| 2 | スレッド無しの fiber 型スケジューラでトレースが現行と一致するか | 425 事象・ハッシュ・最終制御出力まで**バイト単位で一致** |
| 3 | 全 14 タスク＋検証用プラントで離陸・ホバリングと速度計測 | 0.0029 s/s、ホバリング成立、wasm 412 KiB |

### 速度（Apple M2 Max）

| ビルド・実行環境 | シミュレーション 1 秒あたりの実処理時間 | wasm の大きさ |
|--------|--------------------------------|--------------|
| fiber 版 wasm `-O2`（Asyncify 全体計装）／Node.js v24 | 0.0029〜0.0030 s | 412.1 KiB |
| fiber 版 wasm `-O2`（同上）／**Chrome** | **0.0172 s** | 412.1 KiB |
| fiber 版 wasm `-O3`（Asyncify 全体計装）／Node.js v24 | 0.0031 s | 438.6 KiB |
| fiber 版 ネイティブ（ucontext、参考） | 0.0024 s | — |

いずれも合格の目安 0.30 s を大きく下回る。`ASYNCIFY_ONLY` 等による計装の絞り込みは**不要**だった。

Chrome の実測は同じ `.js`／`.wasm` を素の HTML ページから読み込み（`Module.arguments=['60']`）、
`python3 -m http.server` で 127.0.0.1 から配信して行った。シミュレーション 60 秒を実時間 1.031 秒。
最大高度 0.813 m・最終高度 0.576 m・`hover OK` は Node.js と同じ値で、無改変ファームウェアの
起動ログ（Phase 0〜5、全 16 タスク起動、BMI270 の設定ファイル 8192 バイトの転送など）も
Chrome のコンソールに出た。

Chrome が Node.js より約 6.6 倍遅い理由は**切り分けていない**。計測用ページは約 200 行の出力を
1 行ごとに `console.log` と DOM への追記で出しており、初回読み込みでは WebAssembly の最適化
コンパイルが終わる前から走る。これらが効いている可能性はあるが、推測であって確かめていない。

### トレース一致（確認項目 2 の判定）

`rtos_smoke` と同じシナリオ（無改変の ImuTask / ControlTask / StateTask、仮想時間 0.5 秒、
100 ms で FLYING 注入）で、全事象を出力して突き合わせた結果:

| ビルド | 事象数 | トレースのハッシュ | 出力全体の sha256（先頭 16 桁） |
|--------|--------|------------------|------------------------------|
| スレッド版 ネイティブ | 425 | `0x66abc4f49ec8e9b1` | `48852b7d2bddad63` |
| fiber 版 ネイティブ | 425 | `0x66abc4f49ec8e9b1` | `48852b7d2bddad63` |
| fiber 版 wasm | 425 | `0x66abc4f49ec8e9b1` | `48852b7d2bddad63` |

スケジュールだけでなく、最終的な姿勢・推力・4 モータ duty（有効数字 9 桁）まで一致している。

## 3. 構成

### 新設したファイル

| パス | 役割 |
|------|------|
| `simulator/sils/rtos/scheduler_fiber.{hpp,cpp}` | スレッド無しの協調スケジューラ。`Scheduler` の公開 API と FreeRTOS シムはスレッド版と同一。`emscripten_fiber_t`（wasm）／`ucontext`（ネイティブ）で切り替える。`run_until(target_us)` を持つ |
| `simulator/sils/plant/plant_external.cpp` | MuJoCo を使わない `Plant`。モータ ODE・推力・反トルク・電池は `plant.cpp` からの書き写しで、剛体だけ 6 自由度積分（4000Hz、床は z=0） |
| `simulator/unity/native/rtos_fiber/scheduler.hpp` | `#include "scheduler.hpp"` を fiber 版へ向ける 1 行の転送ヘッダ |
| `simulator/unity/native/compat_wasm/cstdio` | musl の `FILE *const stdout` を再代入可能にするシム（`esp_idf_host/cstdio` の Windows 経路と同じ手口） |
| `simulator/unity/native/compat_wasm/wasm_stdio_shim.cpp` | 上のシャドウ変数の実体 |
| `simulator/unity/native/spike/trace_dump.cpp` | 確認項目 2 の検証: トレース全体を 1 行 1 事象で出力する |
| `simulator/unity/native/spike/hover_spike.cpp` | 確認項目 3 の検証: `app_main` ＋ 14 タスクを `run_until` で回し、速度と高度を測る |
| `simulator/unity/native/spike/build_spike.sh` | 確認項目 3 を作り直して実行する。`just unity-native-spike` から呼ばれる |

### 既存ファイルへの変更（1 か所だけ）

`simulator/sils/plant/plant.hpp` に `#ifndef SILS_PLANT_EXTERNAL` の囲みを 3 か所入れ、
MuJoCo 型の宣言 4 つ（`#include <mujoco/mujoco.h>`、`model()`、`data()`、`sensor()` と
`m_`／`d_`）を切り離せるようにした。これは計画で認められた変更。

マクロ未定義時のプリプロセス後のトークン列が変わらないことは確認済み（`clang++ -E -P` の
出力差分は空行 2 行のみで、トークンの差は 0）。

### 仕組み

fiber 版スケジューラは、スレッド版の**スケジューリング論理をそのまま**写している
（`pick_ready` の優先度・id 順、仮想時計の進め方、タイマの登録順での作動、`on_advance` の
呼び出し位置）。変えたのは「タスクの待機のしかた」だけで、`std::thread` ＋ 条件変数による
トークンの受け渡しを、専用スタック間の明示的な文脈切り替えに置き換えている。

```
  スケジューラ（main のスタック）
        │  grant(task)          … emscripten_fiber_swap(&sched, &task)
        ▼
  タスクの fiber（専用スタック 1 MiB）
        │  block_current(state) … emscripten_fiber_swap(&task, &sched)
        ▼
  スケジューラへ戻る
```

## 4. 再現手順

Emscripten・`cmake`・`ninja`・Node.js はリポジトリ直下の `flake.nix` の開発シェルから来る。
`nix develop` に入るか、`nix develop -c <コマンド>` で個別に実行する。Emscripten のキャッシュは
スクリプトが `simulator/unity/native/.cache/` へ向ける（Nix ストアは読み取り専用のため）。

```bash
# 速度計測とホバリング成立の確認（引数はシミュレーションする秒数、既定 30）
just unity-native-spike 30
```

このレシピは `simulator/unity/native/spike/build_spike.sh` を呼ぶ。同スクリプトは無改変の
ファームウェアとシミュレータを WebAssembly へコンパイルし（2 回目以降はソースが新しい翻訳単位
だけを作り直す）、Node.js で実行して、1 秒あたりの実処理時間・最大高度・最終高度を表示する。

ブラウザでの実測は、同じ `spike_fiber_wasm_O2.js` と `.wasm` を素の HTML ページから
`Module.arguments = ['60']` を与えて読み込み、`python3 -m http.server` で 127.0.0.1 から配信して
Chrome で開く。COOP/COEP 等の特別な HTTP ヘッダは要らない。この計測用ページは使い捨てのため
リポジトリには含めていない。

ビルドの生成物（`build-*/`、`.cache/`、`*.wasm`）は git に入れない（`.gitignore` 済み）。

## 5. 段階 2 への引き継ぎ

| 事項 | 内容 |
|------|------|
| `scheduler.hpp` への `run_until` 追加 | 現行のスレッド版 `Scheduler` に `run_until` が無いため、スレッド版で同じ入口を使うビルドは通らない。計画どおり `scheduler.hpp` に宣言を足し、定義を `scheduler_step.cpp` に置く必要がある |
| `plant_external.cpp` の重複 | `omegaDot`／`steadyStateOmega`／`dutyToThrust`／`ocvFromCharge`／`updateBattery`／`hoverDuty` を `plant.cpp` から写している。計画にある `plant/actuator_model.hpp` へ括り出し、写しを 1 つにする |
| 接触モデル | 検証用プラントの床は z=0 のばね・ダンパで、MuJoCo の接触ソルバではない。空中の軌跡は一致するが、接地の瞬間から分かれる |
| 未移植 | `flow()`／`mag()` はゼロ値、`startHandling()` は何もしない。確認項目 3 の離陸・ホバリングでは使わないため |
| スタック | タスクごとに 1 MiB（14 タスクで 14 MiB）。ファームの `config::STACK_*` は 32bit 向けの値なので従っていない |

---

<a id="english"></a>

## 1. Overview

### About This Directory

Holds the deliverable of **stage 1(a), technical verification** from
`docs/plans/unity-simulator.md` (the Unity simulator plan). One question was asked:

> Can the unmodified firmware be built with Emscripten, and does it run comfortably
> faster than real time in a browser?

The measurements used Emscripten 6.0.8-git, not the 3.1.38 assumed when the plan was
written. The version comes from the `flake.nix` development shell and moves with
`flake.lock` (it has since advanced to 6.0.9-git). A different version may shift the
speed figures, but `just unity-native-spike` re-measures them on every run.

**Result: yes.** Wall-clock cost per simulated second is **0.0029–0.0030 s** under
Node.js WebAssembly — roughly 100× the margin the 0.30 s pass criterion asks for — and
**0.0172 s in Chrome**, roughly 17× that margin. The unmodified firmware arms, takes
off, enters ALTITUDE_HOLD, and settles from a 0.813 m peak to hold 0.577 m.

### Target Audience

Whoever picks up stage 2 (the real native core). This records the measured numbers and
how to reproduce them.

## 2. Results

### Per Check

Stage 1(a) of the plan was carried out as three checks. These numbers are local to
this document and are not the plan's stage numbers.

| Check | Work | Result |
|-------|------|--------|
| 1 | Compile unmodified firmware + shims + `devices/` + `rtos/` with Emscripten | All 97 translation units compile (not one byte of `firmware/` was edited) |
| 2 | Thread-free fiber scheduler reproduces the current trace | **Byte-identical**: 425 events, same hash, same final control outputs |
| 3 | All 14 tasks + verification plant: take off, hover, measure | 0.0029 s/s, hover holds, 412 KiB wasm |

### Speed (Apple M2 Max)

| Build and runtime | Wall-clock per simulated second | wasm size |
|-------|--------------------------------|-----------|
| fiber wasm `-O2` (whole-program Asyncify) / Node.js v24 | 0.0029–0.0030 s | 412.1 KiB |
| fiber wasm `-O2` (same) / **Chrome** | **0.0172 s** | 412.1 KiB |
| fiber wasm `-O3` (whole-program Asyncify) / Node.js v24 | 0.0031 s | 438.6 KiB |
| fiber native (ucontext, for reference) | 0.0024 s | — |

All are far under the 0.30 s pass criterion, and narrowing the instrumentation with
`ASYNCIFY_ONLY` and friends proved **unnecessary**.

The Chrome figure was measured by loading the same `.js` and `.wasm` from a plain HTML
page (`Module.arguments=['60']`) served from 127.0.0.1 by `python3 -m http.server`: 60
simulated seconds in 1.031 s. The peak altitude of 0.813 m, the final 0.576 m and the
`hover OK` verdict match Node.js, and the unmodified firmware's boot log (phases 0–5,
all 16 tasks started, the 8192-byte BMI270 configuration transfer) appeared in Chrome's
console.

Why Chrome is about 6.6× slower than Node.js **has not been isolated**. The measurement
page emits roughly 200 lines one at a time through `console.log` and DOM appends, and on
a first load it starts before WebAssembly's optimizing compilation finishes. Either may
contribute, but this is conjecture and was not tested.

### Trace Agreement (the check 2 criterion)

Same scenario as `rtos_smoke` (unmodified ImuTask / ControlTask / StateTask, 0.5 s of
virtual time, FLYING injected at 100 ms), with every event printed and compared:

| Build | Events | Trace hash | sha256 of the whole output (first 16) |
|-------|--------|-----------|---------------------------------------|
| thread, native | 425 | `0x66abc4f49ec8e9b1` | `48852b7d2bddad63` |
| fiber, native | 425 | `0x66abc4f49ec8e9b1` | `48852b7d2bddad63` |
| fiber, wasm | 425 | `0x66abc4f49ec8e9b1` | `48852b7d2bddad63` |

Not only the schedule agrees — so do the final attitude, thrust and four motor duties
to nine significant figures.

## 3. Structure

### New Files

| Path | Role |
|------|------|
| `simulator/sils/rtos/scheduler_fiber.{hpp,cpp}` | Thread-free cooperative scheduler. Public `Scheduler` API and FreeRTOS shims identical to the thread version; switches via `emscripten_fiber_t` (wasm) or `ucontext` (native). Carries `run_until(target_us)` |
| `simulator/sils/plant/plant_external.cpp` | MuJoCo-free `Plant`. Motor ODE, thrust, reaction torque and battery transcribed from `plant.cpp`; only the rigid body is a 6-DOF integrator (4000 Hz, floor at z=0) |
| `simulator/unity/native/rtos_fiber/scheduler.hpp` | One-line forwarder pointing `#include "scheduler.hpp"` at the fiber version |
| `simulator/unity/native/compat_wasm/cstdio` | Makes musl's `FILE *const stdout` assignable (same trick as `esp_idf_host/cstdio`'s Windows path) |
| `simulator/unity/native/compat_wasm/wasm_stdio_shim.cpp` | Definitions backing that shadow |
| `simulator/unity/native/spike/trace_dump.cpp` | Check 2: prints the whole trace, one event per line |
| `simulator/unity/native/spike/hover_spike.cpp` | Check 3: drives `app_main` + 14 tasks through `run_until`, measures speed and altitude |
| `simulator/unity/native/spike/build_spike.sh` | Rebuilds and runs check 3; invoked by `just unity-native-spike` |

### The One Existing-File Change

`simulator/sils/plant/plant.hpp` gained three `#ifndef SILS_PLANT_EXTERNAL` guards so the
four MuJoCo-typed declarations (`#include <mujoco/mujoco.h>`, `model()`, `data()`,
`sensor()` with `m_`/`d_`) can be dropped. This is the change the plan authorises.

The preprocessed token sequence is unchanged when the macro is undefined (verified with
`clang++ -E -P`: the diff is two blank lines and zero token differences).

### How It Works

The fiber scheduler is a transcription of the thread version's **scheduling logic**
(`pick_ready` by priority then id, the virtual-clock advance, timers fired in
registration order, where `on_advance` lands). Only the parking mechanism changed: the
`std::thread` + condition-variable hand-off became an explicit context switch between
dedicated stacks.

## 4. Reproducing

Emscripten, `cmake`, `ninja` and Node.js all come from the development shell defined by
`flake.nix` at the repository root. Enter it with `nix develop`, or run single commands
through `nix develop -c <command>`. The script points Emscripten's cache at
`simulator/unity/native/.cache/`, since the Nix store is read-only.

```bash
# Measure the speed and confirm the hover (argument: simulated seconds, default 30)
just unity-native-spike 30
```

The recipe calls `simulator/unity/native/spike/build_spike.sh`, which compiles the
unmodified firmware and the simulator to WebAssembly (rebuilding only the translation
units whose sources are newer on a repeat run), runs it under Node.js, and prints the
wall-clock cost per simulated second along with the peak and final altitudes.

For the browser figure, load the same `spike_fiber_wasm_O2.js` and `.wasm` from a plain
HTML page with `Module.arguments = ['60']`, serve it from 127.0.0.1 with
`python3 -m http.server`, and open it in Chrome. No special HTTP headers such as
COOP/COEP are needed. That measurement page was throwaway and is not in the repository.

Build outputs (`build-*/`, `.cache/`, `*.wasm`) are not committed (see `.gitignore`).

## 5. Hand-off to Stage 2

| Item | Detail |
|------|--------|
| `run_until` on `scheduler.hpp` | The current thread `Scheduler` has no `run_until`, so a thread build of the same entry point does not compile. As planned, the declaration must go on `scheduler.hpp` with the definition in `scheduler_step.cpp` |
| Duplication in `plant_external.cpp` | `omegaDot` / `steadyStateOmega` / `dutyToThrust` / `ocvFromCharge` / `updateBattery` / `hoverDuty` are copied from `plant.cpp`. Factor them into the planned `plant/actuator_model.hpp` so there is one copy |
| Contact model | The verification plant's floor is a spring-damper at z=0, not MuJoCo's contact solver. Trajectories agree in the air and diverge on impact |
| Not ported | `flow()` / `mag()` return zeros, `startHandling()` is a no-op — none are exercised by the take-off-and-hover check |
| Stacks | 1 MiB per task (14 MiB for 14 tasks). The firmware's `config::STACK_*` values are 32-bit figures and are deliberately not honoured |
