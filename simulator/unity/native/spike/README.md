# 段階 1(b)(d) — Unity WebGL から別の wasm モジュールを同期で呼ぶ

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。

## 1. 概要

### このディレクトリについて

`docs/plans/unity-simulator.md`（Unity 版シミュレータ計画）の **段階 1(b) と (d)** の
成果物を置く。確かめたのは次の 2 問である。

> (b) Unity WebGL が、ファームウェアを収めた**別の** wasm モジュールを `.jslib` 経由で
> **同期に**呼べるか。モジュールを作り直せばファームは初期状態から起動し直すか。
>
> (d) ブラウザで `prompt()` のダイアログが開かないか（ファームの CLI タスクが
> 標準入力を読むため）。

段階 1(a) の成果物（`hover_spike.cpp`・`build_spike.sh` ほか）は
[`../README.md`](../README.md) にある。本文書はその続きで、同じ無改変ファームウェアを
**main を持たない C 入口の集まり**として組み直したものを扱う。

### 対象読者

段階 2（ネイティブコアの本実装）と段階 3（最小の WebGL 版）に入る担当者。

**結果: (b) も (d) も成立した。** 加えて、段階 1(a) から**引き継がれていた不具合を 1 件
見つけた**（§4）。

## 2. 検証の結果

### 結論

| 問い | 結果 |
|------|------|
| (b) `.jslib` から同期で呼べるか | **成立。** 1 刻み（2.5ms）あたり Chrome で **28.23 マイクロ秒**。実時間に対し **88.5 倍**の余裕 |
| (b) モジュールの作り直しで再起動するか | **成立。** 16 回繰り返し、毎回 INIT から起動して同じ高度に達した。メモリは増え続けない |
| (d) `prompt()` が開かないか | **開かない。** `-sFILESYSTEM=0` により標準入力の装置そのものが無い。30 秒ぶんの実行でダイアログの呼び出し 0 回 |

### 速度（Chrome、Apple M2 Max）

`.jslib` と同じ呼び出し（`cwrap` で作った関数を 1 刻みにつき 1 回）を、
Chrome のページから 12,000 回続けて呼んだ計測:

| 項目 | 値 |
|------|-----|
| シミュレーションした長さ | 30.0 秒（12,000 刻み × 2.5ms） |
| 実処理時間 | 0.3388 秒 |
| シミュレーション 1 秒あたり | **0.01129 秒**（合格の目安 0.30 秒の約 27 分の 1） |
| 1 刻みあたり | **28.23 マイクロ秒**（刻みの長さ 2500 マイクロ秒に対し **88.5 倍**の余裕） |
| 最大高度・最終高度 | 0.813 m・0.577 m（段階 1(a) の Node.js の値と一致） |
| 飛行状態 | FLYING（番号 5） |

段階 1(a) の Chrome の測定値（1 秒あたり 0.0172 秒）より速い。1(a) の計測用ページが
約 200 行を 1 行ずつ `console.log` と DOM 追記で出していたのに対し、今回は出力を捨てて
いる。**ただし両者の差の要因は切り分けていない。**

### 読み込みにかかる時間

| 段階 | 時間 |
|------|------|
| `sfu_firmware.js` の `<script>` 取得 | 10 ミリ秒 |
| `createSfuFirmware()` の解決（wasm の実体化） | 15 ミリ秒 |
| 電源の入れ直し 2 回目以降の `createSfuFirmware()` | 5.5〜9.2 ミリ秒 |

いずれも 127.0.0.1 からの配信。`sfu_firmware.wasm` は 424,053 バイト、
`sfu_firmware.js` は 23,286 バイト。

### 電源の入れ直し（モジュールの作り直し）

`createSfuFirmware()` を呼び直して新しいモジュールを作り、古い参照を捨てる。16 回
繰り返した結果:

| 確認 | 結果 |
|------|------|
| 起動前の飛行状態 | 毎回 0（INIT） |
| `sfu_spike_boot()` | 毎回 1（成功） |
| シミュレーション 10 秒後 | 毎回 5（FLYING）、高度 0.479 m で完全一致 |
| wasm のメモリ | 毎回 67,108,864 バイト（変化なし） |
| JavaScript ヒープ（1〜6 回目、参照を保持） | 61.6 → 66.3 MB（1 回あたり約 0.91 MB 増） |
| JavaScript ヒープ（7〜16 回目、参照を捨てた） | 58.0〜58.6 MB（**増加の傾向なし**） |

参照を保持したまま回した 1〜6 回目は増えるが、参照を捨てた 7〜16 回目は 10 回を通じて
横ばいで、漏れはない。同一モジュール内での 2 回目の `sfu_spike_boot()` は 0 を返して
拒否する（計画の「1 モジュール＝1 電源投入」の決定どおり）。

### (d) 標準入出力

| 確認 | 結果 |
|------|------|
| `window.prompt` の呼び出し回数 | **0 回**（シミュレーション 30 秒、CLI タスクを含む全 14 タスク稼働中） |
| `window.confirm` / `window.alert` | 同じく 0 回 |
| モジュールの `FS` | `undefined`（`-sFILESYSTEM=0` のため） |
| 標準出力の行数 | 0 行 |
| 標準誤差出力の行数 | 190 行（起動時のログ。既定では捨てる） |

`-sFILESYSTEM=0` を付けると Emscripten は標準入力の装置そのものを作らないため、
`window.prompt()` へ至る経路が消える。捨てた場合と出した場合で飛行の結果は変わらない
（どちらも FLYING・0.577 m）。`.jslib` は既定で両方の流れを捨て、
`window.sfuSpikeVerbose = true` のときだけコンソールへ出す。

### Unity WebGL のビルド

| 項目 | 値 |
|------|-----|
| ビルドの所要時間 | 2 分 2 秒〜2 分 27 秒（`Library/` が温まった状態） |
| 出力の合計 | 12 MB |
| `webgl.wasm.br` | 6,904,197 バイト |
| `webgl.data.br` | 3,219,550 バイト |
| `webgl.framework.js.br` | 66,966 バイト |
| `webgl.loader.js` | 27,914 バイト |
| `StreamingAssets/sfu_firmware.wasm` | 424,053 バイト（ファームは Unity の wasm に**入っていない**） |

ファームの wasm が Unity 本体の wasm と別のままであることは、この大きさの内訳が示して
いる。

## 3. 構成

### 新設したファイル

| パス | 役割 |
|------|------|
| `spike/module_spike.cpp` | `hover_spike.cpp` の `main` を C 入口の集まりに組み替えたもの。`sfu_spike_boot` / `step` / `step_until` / `altitude` / `state` / `pose` / `battery_volts`。RC の台本とプラントは `hover_spike.cpp` からの書き写し |
| `spike/build_module_spike.sh` | 上を `-sMODULARIZE -sEXPORT_NAME=createSfuFirmware -sASYNCIFY -sFILESYSTEM=0` で組み、`sfu_firmware.js` と `.wasm` を出す |
| `../../Assets/StampFly/Plugins/WebGL/SfuSpike.jslib` | Unity WebGL 側の橋。`<script>` でファームのローダを読み、`createSfuFirmware()` を呼び、`cwrap` した関数を C# へ中継する |
| `../../Assets/StampFly/Runtime/Sim/SfuSpikeFirmware.cs` | `[DllImport("__Internal")]` の薄い包み。WebGL 以外では張りぼてになり、場面がエディタでも開ける |
| `../../Assets/StampFly/Runtime/Sim/SfuSpikeDriver.cs` | 実時間に追いつく数だけ `Step()` を呼び、立方体を動かし、実時間比・1 刻みの所要時間・フレームレートを表示する |
| `../../Assets/StampFly/Runtime/Sim/SfuSpikeScene.cs` | 検証用の最小の場面（カメラ・床・立方体）をコードで組み立てる |

### 段階 1(a) との違い

`build_spike.sh` と `build_module_spike.sh` は、ソース一覧も include パスも同一で、
次の 2 点だけが違う。

1. `hover_spike.cpp`（ループを所有する `main`）を `module_spike.cpp`（`main` 無し、C 入口）に差し替える
2. リンク時に `-sMODULARIZE` ／ `-sEXPORT_NAME` ／ `-sEXIT_RUNTIME=0` ／ `-sINVOKE_RUN=0` ／ `-sFILESYSTEM=0` を足す

`firmware/` 配下は 1 バイトも編集していない。

## 4. 見つかった不具合（段階 1(a) から引き継がれている）

**ファームの飛行結果が、wasm の外側の事情（ヒープ／スタックの配置）で変わる。**

同じ `.js` と `.wasm` を、**置き場所のパスの長さだけ変えて**実行すると、結果が 2 通りに
分かれる。

| 実行したパス | 最大高度 | 最終高度 | 判定 |
|------|----------|----------|------|
| `.../d1/spike_fiber_wasm_O2.js` | 0.813 m | 0.577 m | `hover OK` |
| `.../d22/spike_fiber_wasm_O2.js` | 0.813 m | 0.577 m | `hover OK` |
| `.../d333/spike_fiber_wasm_O2.js` | **0.637 m** | **0.013 m** | **`hover FAILED`** |
| `.../d4444/spike_fiber_wasm_O2.js` | 0.637 m | 0.013 m | `hover FAILED` |
| `.../d55555/spike_fiber_wasm_O2.js` | 0.637 m | 0.013 m | `hover FAILED` |

**これは段階 1(b) で作ったモジュール版だけの症状ではない。上の表は段階 1(a) の
`build_spike.sh` が作る `hover_spike` そのものである**（ファイルの内容は同一で、
置いたディレクトリ名の長さだけが違う）。モジュール版でも同じで、内容が
バイト単位で同一の `.mjs` を 2 つの名前で実行すると、一方が 0.813 m、他方が
0.637 m になる。

失敗する側の兆候は明確で、離陸中（約 6 秒）にファームの衝撃検出フェイルセーフが働く。

```
ALT t=  6.00 alt=  0.340 ... state=TAKEOFF      armed=1
[WARN] failsafe: Impact detected: 8.0G (x2 @400Hz)
[WARN] StateManager: Alert received: type=2 severity=3
ALT t=  7.00 alt=  0.323 ... state=IDLE_GROUND  armed=0
```

同じパスで繰り返せば結果は毎回同じで、12 回の実行が完全に一致する。つまり実行ごとの
ゆらぎではなく、**起動時のメモリ配置で決まる分岐**である。原因は特定していない
（未初期化のメモリの読み出し、fiber のスタックの配置、Asyncify の巻き戻しのいずれか、
と推測しているが**確かめていない**）。

段階 1(a) の記録にある「Node.js で 2 回実行して出力が完全一致」は、**同じパスでの
2 回**であり、この分岐は見えていなかった。段階 2 で原因を特定するまで、
**ホバリングの成否を合格基準に使う計測は、パスを固定して行う必要がある**。

## 5. 未確認のまま残したこと

| 事項 | 状況 |
|------|------|
| Unity の画面での実時間比 1.0 | **測れていない。** 自動操作のブラウザはタブが `hidden` のままで、Chrome が `requestAnimationFrame` を毎秒 0〜1 回まで絞る。1 刻み 28.23 マイクロ秒・フレーム予算の 0.01〜0.66% という数値は取れており、余裕は十分だが、60fps で 1.0 を保つ確認は段階 3 で前面のタブで行う |
| 最後の Unity ビルドが起動しない | 最後に作り直した WebGL ビルドは、読み込み後 3 分を過ぎても `Start()` に達しなかった。その 1 つ前のビルドは同じ場面で動き、`ticks total 2014`・`us per step 54.12`・`state FLYING` を表示した。原因は追えていない |
| Decompression Fallback | 有効にしていない。`.br` の配信には `Content-Encoding` が要り、検証では自前の Python サーバで付けた。GitHub Pages では付けられないため、計画どおり有効化が要る |
| 配信物の置き場所 | `sfu_firmware.js` と `.wasm` は `Assets/StreamingAssets/` に置いて配信した。ビルドの生成物なので、リポジトリには残していない。段階 3 では `sf unity build` が複製する手順にするか、`.gitignore` に加えるかを決める必要がある |

## 6. 再現手順

```bash
# ファームのモジュールを組む（出力は simulator/unity/native/build-module-spike/）
nix develop -c bash simulator/unity/native/spike/build_module_spike.sh

# Unity の StreamingAssets へ複製する（ビルドの生成物なのでコミットしない）
mkdir -p simulator/unity/Assets/StreamingAssets
cp simulator/unity/native/build-module-spike/sfu_firmware.{js,wasm} \
   simulator/unity/Assets/StreamingAssets/

# WebGL ビルド
cd simulator/unity
unity build . --target WebGL \
  --execute-method StampFly.Editor.Builders.WebGLBuilder.Build \
  -o <出力先> --non-interactive --no-tail
```

配信には `Content-Encoding: br` を付けられるサーバが要る（`python3 -m http.server`
だけでは Chrome が `Incorrect response MIME type` で拒む）。

---

<a id="english"></a>

# Stage 1(b)(d) — Calling a Separate wasm Module Synchronously from Unity WebGL

## 1. Overview

### About This Directory

Holds the deliverable of **stages 1(b) and 1(d)** of `docs/plans/unity-simulator.md`
(the Unity simulator plan). Two questions were asked:

> (b) Can Unity WebGL call a **separate** wasm module holding the firmware
> **synchronously** through `.jslib`, and does recreating the module restart the
> firmware from its initial state?
>
> (d) Does the `prompt()` dialog open in the browser (the firmware's CLI task
> reads standard input)?

Stage 1(a)'s deliverable (`hover_spike.cpp`, `build_spike.sh` and the rest) is in
[`../README.md`](../README.md). This document continues from it, covering the same
unmodified firmware rebuilt as **a set of C entry points with no main**.

### Target Audience

Whoever picks up stage 2 (the real native core) and stage 3 (the minimal WebGL
version).

**Result: both (b) and (d) hold.** One defect **inherited from stage 1(a)** was also
found (§4).

## 2. Results

### Conclusions

| Question | Result |
|----------|--------|
| (b) Synchronous call from `.jslib` | **Holds.** One 2.5 ms tick costs **28.23 microseconds** in Chrome — **88.5×** the real-time margin |
| (b) Restart by recreating the module | **Holds.** 16 repetitions, each starting from INIT and reaching the same altitude. Memory does not grow without end |
| (d) Does `prompt()` open | **It does not.** `-sFILESYSTEM=0` leaves no standard-input device at all. Zero dialog calls over 30 simulated seconds |

### Speed (Chrome, Apple M2 Max)

The same calls the `.jslib` makes (a `cwrap`ped function, once per tick), issued
12,000 times in a row from a Chrome page:

| Item | Value |
|------|-------|
| Simulated duration | 30.0 s (12,000 ticks × 2.5 ms) |
| Wall-clock time | 0.3388 s |
| Per simulated second | **0.01129 s**, about one twenty-seventh of the 0.30 s pass criterion |
| Per tick | **28.23 microseconds** against a 2500 microsecond tick — **88.5×** margin |
| Peak and final altitude | 0.813 m and 0.577 m, matching stage 1(a)'s Node.js figures |
| Flight state | FLYING (number 5) |

Faster than stage 1(a)'s Chrome figure of 0.0172 s per simulated second. That page
printed roughly 200 lines one at a time through `console.log` and DOM appends, while
this measurement drops the output. **The cause of the difference was not isolated.**

### Load Times

| Step | Time |
|------|------|
| Fetching `sfu_firmware.js` as a `<script>` | 10 ms |
| `createSfuFirmware()` resolving (wasm instantiation) | 15 ms |
| `createSfuFirmware()` on later power cycles | 5.5–9.2 ms |

All served from 127.0.0.1. `sfu_firmware.wasm` is 424,053 bytes and
`sfu_firmware.js` is 23,286 bytes.

### Power Cycling (Recreating the Module)

Calling `createSfuFirmware()` again for a new module and dropping the old reference,
16 times:

| Check | Result |
|-------|--------|
| Flight state before boot | 0 (INIT) every time |
| `sfu_spike_boot()` | 1 (success) every time |
| After 10 simulated seconds | 5 (FLYING) every time, altitude 0.479 m identically |
| wasm memory | 67,108,864 bytes every time (unchanged) |
| JavaScript heap (cycles 1–6, references kept) | 61.6 → 66.3 MB (about 0.91 MB per cycle) |
| JavaScript heap (cycles 7–16, references dropped) | 58.0–58.6 MB (**no upward trend**) |

Cycles 1–6 held their references and grew; cycles 7–16 dropped them and stayed flat
across ten repetitions, so nothing leaks. A second `sfu_spike_boot()` within one
module returns 0 and is refused, as the plan's "one module equals one power-on"
decision requires.

### (d) Standard I/O

| Check | Result |
|-------|--------|
| Calls to `window.prompt` | **0**, over 30 simulated seconds with all 14 tasks running, CLI task included |
| Calls to `window.confirm` / `window.alert` | 0 as well |
| The module's `FS` | `undefined`, because of `-sFILESYSTEM=0` |
| Lines on standard output | 0 |
| Lines on standard error | 190 (the boot log; dropped by default) |

With `-sFILESYSTEM=0` Emscripten builds no standard-input device at all, so no path
reaches `window.prompt()`. Dropping the streams or printing them makes no difference
to the flight (FLYING at 0.577 m either way). The `.jslib` drops both by default and
prints to the console only when `window.sfuSpikeVerbose = true`.

### The Unity WebGL Build

| Item | Value |
|------|-------|
| Build time | 2 min 2 s to 2 min 27 s, with `Library/` warm |
| Total output | 12 MB |
| `webgl.wasm.br` | 6,904,197 bytes |
| `webgl.data.br` | 3,219,550 bytes |
| `webgl.framework.js.br` | 66,966 bytes |
| `webgl.loader.js` | 27,914 bytes |
| `StreamingAssets/sfu_firmware.wasm` | 424,053 bytes — the firmware is **not** inside Unity's wasm |

That breakdown is what shows the firmware's wasm stays separate from Unity's own.

## 3. Structure

### New Files

| Path | Role |
|------|------|
| `spike/module_spike.cpp` | `hover_spike.cpp`'s `main` recast as a set of C entry points: `sfu_spike_boot` / `step` / `step_until` / `altitude` / `state` / `pose` / `battery_volts`. The RC script and the plant are transcribed from `hover_spike.cpp` |
| `spike/build_module_spike.sh` | Builds it with `-sMODULARIZE -sEXPORT_NAME=createSfuFirmware -sASYNCIFY -sFILESYSTEM=0`, producing `sfu_firmware.js` and `.wasm` |
| `../../Assets/StampFly/Plugins/WebGL/SfuSpike.jslib` | The WebGL-side bridge: loads the firmware's loader as a `<script>`, calls `createSfuFirmware()`, and forwards the `cwrap`ped functions to C# |
| `../../Assets/StampFly/Runtime/Sim/SfuSpikeFirmware.cs` | A thin `[DllImport("__Internal")]` wrapper. Outside WebGL every call is a stub, so the scene still opens in the editor |
| `../../Assets/StampFly/Runtime/Sim/SfuSpikeDriver.cs` | Calls `Step()` as many times as real time asks for, moves a cube, and displays the real-time ratio, per-tick cost and frame rate |
| `../../Assets/StampFly/Runtime/Sim/SfuSpikeScene.cs` | Builds the check's minimal scene (camera, floor, cube) in code |

### How It Differs from Stage 1(a)

`build_spike.sh` and `build_module_spike.sh` share their source list and include
path exactly, and differ in only two ways:

1. `hover_spike.cpp` (a `main` that owns the loop) is replaced by `module_spike.cpp` (no `main`, C entry points)
2. The link adds `-sMODULARIZE`, `-sEXPORT_NAME`, `-sEXIT_RUNTIME=0`, `-sINVOKE_RUN=0` and `-sFILESYSTEM=0`

Not one byte under `firmware/` was edited.

## 4. A Defect Found (Inherited from Stage 1(a))

**The firmware's flight outcome changes with circumstances outside the wasm — the
heap and stack layout.**

Running the same `.js` and `.wasm` with **only the length of their directory path
changed** splits the result two ways:

| Path run from | Peak altitude | Final altitude | Verdict |
|------|----------|----------|------|
| `.../d1/spike_fiber_wasm_O2.js` | 0.813 m | 0.577 m | `hover OK` |
| `.../d22/spike_fiber_wasm_O2.js` | 0.813 m | 0.577 m | `hover OK` |
| `.../d333/spike_fiber_wasm_O2.js` | **0.637 m** | **0.013 m** | **`hover FAILED`** |
| `.../d4444/spike_fiber_wasm_O2.js` | 0.637 m | 0.013 m | `hover FAILED` |
| `.../d55555/spike_fiber_wasm_O2.js` | 0.637 m | 0.013 m | `hover FAILED` |

**This is not confined to the module build made for stage 1(b). The table above is
stage 1(a)'s own `hover_spike`, as produced by `build_spike.sh`** — identical file
contents, differing only in the length of the directory holding them. The module
build behaves the same way: two byte-identical `.mjs` files under different names
give 0.813 m and 0.637 m respectively.

The failing side has a clear signature — the firmware's impact-detection failsafe
fires during take-off, at about 6 seconds:

```
ALT t=  6.00 alt=  0.340 ... state=TAKEOFF      armed=1
[WARN] failsafe: Impact detected: 8.0G (x2 @400Hz)
[WARN] StateManager: Alert received: type=2 severity=3
ALT t=  7.00 alt=  0.323 ... state=IDLE_GROUND  armed=0
```

Repeating at one path always gives the same answer; twelve runs agreed exactly. So
this is not run-to-run jitter but **a branch decided by the memory layout at
start-up**. The cause has not been identified (reading uninitialized memory, the
fiber stacks' placement, or Asyncify's rewind are the suspects, but **none of this
was confirmed**).

Stage 1(a)'s record that "two Node.js runs produced identical output" compared **two
runs at the same path**, where this branch is invisible. Until stage 2 identifies the
cause, **any measurement whose pass criterion is whether the hover succeeds must fix
the path.**

## 5. Left Unconfirmed

| Item | Status |
|------|--------|
| A real-time ratio of 1.0 on Unity's screen | **Not measured.** The automated browser keeps the tab `hidden`, and Chrome then throttles `requestAnimationFrame` to between zero and one call per second. The per-tick cost of 28.23 microseconds and the 0.01–0.66% of frame budget were obtained and leave ample room, but confirming 1.0 at 60fps belongs to stage 3, in a foreground tab |
| The last Unity build does not start | The final rebuilt WebGL player had not reached `Start()` three minutes after loading. The build before it ran the same scene and displayed `ticks total 2014`, `us per step 54.12` and `state FLYING`. The cause was not chased down |
| Decompression Fallback | Not enabled. Serving `.br` needs `Content-Encoding`, which this check supplied from its own Python server. GitHub Pages cannot set it, so enabling the fallback is still required, as the plan says |
| Where the served files live | `sfu_firmware.js` and `.wasm` were served from `Assets/StreamingAssets/`. They are build outputs and are not left in the repository. Stage 3 must decide whether `sf unity build` copies them or `.gitignore` covers them |

## 6. Reproducing

```bash
# Build the firmware module (output in simulator/unity/native/build-module-spike/)
nix develop -c bash simulator/unity/native/spike/build_module_spike.sh

# Copy it into Unity's StreamingAssets (a build output; not committed)
mkdir -p simulator/unity/Assets/StreamingAssets
cp simulator/unity/native/build-module-spike/sfu_firmware.{js,wasm} \
   simulator/unity/Assets/StreamingAssets/

# Build for WebGL
cd simulator/unity
unity build . --target WebGL \
  --execute-method StampFly.Editor.Builders.WebGLBuilder.Build \
  -o <output> --non-interactive --no-tail
```

Serving needs a server that can set `Content-Encoding: br`; `python3 -m http.server`
alone makes Chrome refuse the build with `Incorrect response MIME type`.
