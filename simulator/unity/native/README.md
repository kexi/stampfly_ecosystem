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
| `sfu_bridge_smoke` | ネイティブ（スレッド版） | 0.0090 s | 0.684 m | 0.492 m | hover OK |
| `sfu_bridge_smoke` | wasm（fiber 版）／Node.js | 0.0030 s | 0.684 m | 0.492 m | hover OK |
| `sfu_external_smoke` | ネイティブ（スレッド版） | 0.0094 s | 0.680 m | 0.480 m | hover OK |
| `sfu_external_smoke` | wasm（fiber 版）／Node.js | 0.0031 s | 0.680 m | 0.480 m | hover OK |
| `sfu_module_check.mjs` | wasm モジュール／Node.js | 0.0033 s | 0.680 m | 0.484 m | hover OK |

いずれも合格の目安 0.30 s を大きく下回る。スレッド版がおよそ 3 倍遅いのは、段階 1 で
fiber 版を選んだ理由（計画 §9）と整合する。

### ネイティブと wasm が**バイト単位で一致する**こと

`sfu_bridge_smoke` と `sfu_external_smoke` は、ネイティブと wasm で**標準出力が完全に一致する**。
`just unity-native-test` が毎回 `diff` で確かめる。以前この節には「最終高度の幅はコンパイラごとの
浮動小数点の丸めから来る」と書いてあったが、これは事実ではなかった。実際には
`sfu_module_check.mjs` だけがホストの積分器の違い（C++ の `float` と JavaScript の double）で
数 mm ずれ、C++ の 2 つは一致する。

一致を保つために、`CMakeLists.txt` が**全ターゲット**に `-ffp-contract=off` を付けている。
既定ではコンパイラが `a*b + c` を積和融合へ縮約してよく、縮約すると丸めが 2 回でなく 1 回になる。
Clang と Emscripten がその選択を別々に行えば下位ビットが分かれうる。なお実測では、
この構成のもとで**このフラグは両者の出力を 1 ビットも変えなかった**（Emscripten は元から縮約
しておらず、Xcode の clang もこのコードでは縮約していなかった）。それでも付けてあるのは、
将来のコンパイラの版や最適化の設定で縮約が始まっても一致が壊れないようにするためである。

段階 1(a) の技術検証（`spike/`）には付けていない。付けた版でも
トレースのハッシュは `48852b7d2bddad63` のまま変わらないことを確かめたが、`spike/` は
技術検証当時の記録であり、記録を後から作り直す理由が無いのでそのままにしてある。

3 つの最小動作確認はいずれも、**同じ実行を 2 回して標準出力が完全に一致する**。速度の数値だけは
標準エラー出力へ出す。実時間の測定値は繰り返さないため、標準出力に混ぜると一致の判定が壊れる
からである。

### 飛行は起動時のヒープの配置に依存しない（かつては依存した。原因は fiber のスタックの整列）

**`sfu_boot` の前にヒープを確保してよい。飛行の結果は変わらない。**

かつては変わった。起動の前に 48 バイトを 1 回確保するだけで、ホバリングの成立が
「Impact detected: 8.0G」による緊急 DISARM に反転し、ヒープを 16 バイトずらすだけで反転した。
Unity の `.jslib` は起動の前に確保せざるを得ないので、Unity 経由では毎回失敗していた。

**原因はヒープの確保そのものではなく、fiber 版スケジューラのタスクスタックの整列であった。**
スケジューラは各タスクのスタックを `std::malloc` で確保していたが、`malloc` が保証するのは
`max_align_t` まで（wasm32 では 8 バイト）である。一方 C ABI は呼び出し境界で 16 バイト整列の
スタックポインタを要求し、コンパイラは 16 バイト整列のローカルを実行時の切り上げではなく
スタックポインタの**マスク**で配置する。よって 8 mod 16 で始まるスタックでは、そのスロットが
下方向へずれて別のローカルに重なり、2 つが静かに壊し合った。スタックが境界に乗るかは
その時のヒープのずれだけで決まるため、ディレクトリ名の長さや起動前の 1 回の確保で反転した。
**コミット `e4ed60cd` が `std::aligned_alloc(16, …)` に直した。**

そのため「起動までヒープに触れない」という以前の規律はもう要らない。代わりに、退行を規律では
なく試験で捕まえる。`just unity-native-test` が毎回、次の 4 つを確かめる。

| 確かめるもの | 手立て |
|---|---|
| 2 つの C++ の最小動作確認（ネイティブ） | `--preallocate 48` を付けた実行と付けない実行の標準出力を `diff` |
| 同じ 2 つ（wasm） | 同じく `diff`。整列の不具合が実際に居たのはこちら |
| 段階 1(a) のモジュール | `spike/heap_layout_check.mjs` ― 起動前のずれ 32 通りが一致すること |
| 出荷する橋渡しのモジュール | `bridge/sfu_heap_layout_check.mjs` ― 起動前のずれ 24 通りが一致すること |

後ろの 2 つは、16 バイトの窓を 1 バイトずつ歩いてから、48・64・128・256・512・1024・2048・4096
を抜き取りで見る。「全て一致したが一度も飛んでいない」実行は自分自身と自明に一致して退行を
隠すので、最大高度と飛行状態も判定に入れて不合格にする。`aligned_alloc` を `malloc` へ戻すと
橋渡し版は 24 通り中 11 通りが分かれ、分かれた側は最終高度 0.013 m（接地）・状態 1 になる ―
この試験が不具合を実際に検出することは、そうして確かめてある。

`sfu_smoke_options.hpp` の `const char*`（`std::string` ではなく `argv` を指す）は残してある。
これはもはや正しさの要件ではなく、解析より長生きするコマンド行を読むには単に安いからである。

### 成果物の大きさ

| 成果物 | 大きさ | 用途 |
|---|---|---|
| `sfu_firmware.wasm` | 444,482 バイト | Unity WebGL が `.jslib` 越しに読み込む |
| `sfu_firmware.js` | 72,352 バイト | 同上（`createSfuFirmware` を出す読み込み側） |
| `libsfu_firmware.dylib` | 518,744 バイト | Unity エディタ（macOS arm64）の開発用プラグイン |

dylib が公開する記号は `sfu_*` の 17 個だけである（`nm -gU` で確認）。ファーム・プラント・
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

Unity が呼ぶ入口は 17 個である（ABI の版は **2**）。渡すものはすべて **Unity 自身の規約**
（左手系・Y 上・クォータニオンは x,y,z,w の順・単位は m と秒と rad/s）で、NED／FRD への変換は
C++ 側の `simulator/sils/frames/frames_unity.hpp` だけで行う。

| 入口 | 役目 |
|---|---|
| `sfu_abi_version()` | ABI の版。他の何より先に突き合わせる |
| `sfu_struct_size(which)` | 本モジュールが構造体をどの大きさにコンパイルしたか。C# 側が自分の `sizeof` と比べる |
| `sfu_boot(&config)` | 電源投入。プラント初期化 → ペアリングの記録 → `app_main`（BSP ＋ 14 タスク）。2 回目はエラー |
| `sfu_shutdown()` | タスクの巻き戻しとスタックの解放。**この後は全ての入口が `SFU_ERR_SHUT_DOWN` を返す** |
| `sfu_last_status()` | 直近の `sfu_step`／`sfu_shutdown` の結果。wasm で結果を読む手立て |
| `sfu_step(&in, &out)` | 主経路。状態を注入 → `run_until(now + dt_us)` → 区間平均の合力・合トルクと状態を返す |
| `sfu_param_count()` ／ `sfu_param_info(i, &out)` | ファームが公開する調整値（99 個）の数と素性 |
| `sfu_param_set(name, value)` ／ `sfu_param_get(name, &out)` | 名前で読み書き。型は SSOT のテーブルから引く |
| `sfu_set_wind(x, y, z)` | 一定の風力 [N]、Unity の世界系 |
| `sfu_set_motor_health(motor, gain)` | モータごとの推力の健全度（1 = 正常、0 = 停止） |
| `sfu_set_imu_bias(ax, ay, az, gx, gy, gz)` | 一定の生 IMU バイアス、機体ローカルの Unity 系 |
| `sfu_log_read_record(&out)` ／ `sfu_log_dropped()` | ファームのログを**構造化した記録**として 1 つずつ取り出す |
| `sfu_log_read(buffer, capacity)` | 同じものを改行区切りの文字列で取り出す（印字するだけの呼び出し側用） |
| `sfu_set_log_level(level)` | 残す最も低い段。既定は `SFU_LOG_INFO`（`ESP_LOGD`／`ESP_LOGV` を捨てる） |

構造体は 5 つあり、いずれも先頭に `uint32_t struct_size` を置き、呼び出し側が自分の宣言の
`sizeof` を書き込む。橋渡しがそれと比べ、食い違えば `SFU_ERR_STRUCT_SIZE` を返す。短い構造体の
末尾を越えて読む代わりにその場で拒否するためであり、後の版が構造体を末尾に伸ばしても古い
呼び出し側が壊れないためでもある。

この ABI での大きさ（ネイティブ arm64・wasm32 のどちらも同じ）: `SfuConfig` 48 バイト、
`SfuStepIn` 96 バイト、`SfuStepOut` 168 バイト、`SfuParamInfo` 84 バイト、
`SfuLogRecord` 272 バイト。

### wasm では `sfu_step` の戻り値を読まない

**wasm では `sfu_step` の戻り値は意味を持たない。`out->status`（または `sfu_last_status()`）を
読むこと。** fiber 版スケジューラは呼び出しの途中でスタックを切り替え、Asyncify はそれを wasm
スタック全体の巻き戻しと巻き直しで実現する。JavaScript が受け取るのは巻き直しの際に生じた値
（0）であって、C のコードが返した値ではない。最小の例で再現して確かめてある。
`out->status` は `sfu_step` が**最後に**記憶域へ書くので残る。ネイティブの呼び出し側は
どちらを読んでもよいが、両方で動く必要のある呼び出し側は `status` を読む。

### 刻み幅は整数のマイクロ秒

`SfuStepIn::dt_us` は `uint32_t` で、秒ではなく**マイクロ秒**である。浮動小数を境界に出すと
丸めが橋渡しの中で起き、呼び出し側がその結果を予測できない。整数であれば、`dt_us` の N 刻みは
必ずちょうど `N × dt_us` に時計を置く（`just unity-native-test` が 12,000 刻みで確かめる）。

- 0、または上限（`SFU_DT_US_MAX` = 100,000 µs = 100 ms）を超える値は `SFU_ERR_BAD_ARGUMENT`
- 上限**ちょうど**は受理する
- `SFU_PLANT_SUBSTEP_US`（250 µs）の倍数でない値も受理し、時計はちょうどその値だけ進む。
  プラントは副刻みの端数を繰り越すので、端数は失われるのではなく**次の**刻みの
  `wrench_dt_s` に現れる

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
| `bridge/sfu_log_ring.cpp` | ファームのログのリングバッファ（記録 512 個。1 記録 = 仮想時刻・段・タグ・本文） |
| `bridge/shim/esp_log.h` | `ESP_LOGx` の行き先を stderr からそのリングバッファへ変える |
| `bridge/sfu_log_jsonl.hpp` | 記録を JSON Lines で書き出す（`AGENTS.md` のログの決まりの形） |
| `bridge/sfu_smoke_options.hpp` | 2 つの最小動作確認が共有するコマンド行の解析 |
| `bridge/sfu_rc_script.hpp` | 2 つの最小動作確認が共有する台本の操縦入力 |
| `bridge/sfu_bridge_smoke.cpp` | Unity 無しの最小動作確認（プラントが剛体を持つ） |
| `bridge/sfu_external_smoke.cpp` | 外部供給の最小動作確認（C++ が Unity の代役） |
| `bridge/sfu_flight.mjs` | 2 つの JS の確認が共有する構造体の配置と飛行（台本・物理・判定） |
| `bridge/sfu_module_check.mjs` | wasm モジュールを JS から飛ばす確認（JS が Unity の代役） |
| `bridge/sfu_heap_layout_check.mjs` | 同じモジュールを、起動前のヒープのずれ 24 通りで飛ばして一致を求める |
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
2. `sfu_bridge_smoke` を 2 回実行し、標準出力を `diff` で突き合わせる（決定論）
3. `sfu_external_smoke` を 2 回実行し、同じく突き合わせる
4. 同じ 2 つを wasm でも実行し、**ネイティブの出力と `diff` で突き合わせる**（バイト単位の一致）
5. `sfu_module_check.mjs` — 出荷する `sfu_firmware.js` を JS から読み込み、構造体の大きさを
   `sfu_struct_size` と突き合わせてから 30 秒飛ばす
6. JSON Lines のログを書き、`jq` が全行を読めることを確かめる
7. `actuator_parity_test` — ビルド済みの SILS に在るときだけ
8. 2 つの C++ の確認を `--preallocate 48` でも実行し、付けない実行と `diff` で突き合わせる
   （ネイティブと wasm の両方）
9. `spike/heap_layout_check.mjs` と `bridge/sfu_heap_layout_check.mjs` — 起動前のヒープのずれを
   走査して飛行が一致することを求める。段階 1(a) のモジュールは `build_module_spike.sh` が作る

`sfu_bridge_smoke` は飛行のほかに、ABI の約束も確かめる: 2 回目の `sfu_boot` が拒まれること、
N 刻み後の `now_us` がちょうど N×2500 µs であること、範囲外の `dt_us` が丸められず拒まれること、
`sfu_shutdown` の後は全ての入口が `SFU_ERR_SHUT_DOWN` を返すこと。`sfu_external_smoke` と
`sfu_module_check.mjs` は飛行の途中で突風を当て、**終了時に水平付近へ戻っており FLYING のまま**
であることを求める。高度だけではトルクの符号を確かめられない（符号を全反転させても同じ上昇が
出る）ためで、反転させた版が実際に不合格になることを確かめてある。

`actuator_parity_test`（`actuator_model.hpp` と MuJoCo 版 `plant.cpp` の電池電圧がビット一致
することを確かめる）は MuJoCo を要するため、`simulator/sils` 側のターゲットにしてある。
`unity-native-test` は、ビルド済みの SILS に在れば実行し、無ければ「省略した」と表示する。

```bash
cmake --build <sils の build> --target actuator_parity_test
<sils の build>/actuator_parity_test simulator/sils/models/stampfly.xml
```

同試験は設定 2 つ（既定と、`motor_delay_ms = 8.0`・`torque_authority = 0.75`）で比べる。
乱流と反トルクは MuJoCo 版に取り出し口が無いため比べられない ― 既知の穴で、詳しくは
同ファイルの冒頭にある。

ビルドの生成物（`build-*/`、`.cache/`）は git に入れない（`.gitignore` 済み）。

## ファームのログを構造化した記録として取り出す

ファームは無改変のまま `ESP_LOGx(tag, fmt, ...)` を呼ぶ。`bridge/shim/esp_log.h` の差し替えが
それを受け、**文字列 1 本にする前の形**でリングバッファへ置く。1 記録が持つのはマクロが実際に
持っていた 4 つである。

| 欄 | 内容 |
|---|---|
| `sim_us` | その時点の仮想時刻 [µs]（壁時計ではない。2 回の実行で同じ値になる） |
| `level` | `SFU_LOG_ERROR`／`_WARN`／`_INFO`／`_DEBUG`／`_VERBOSE`（`esp_log_level_t` と同じ数値） |
| `tag` | `ESP_LOGx` のタグ |
| `message` | 書式を適用した本文。**レベルやタグの接頭辞は付けない** |

`ESP_LOGD`／`ESP_LOGV` はコンパイル時に消さず、リングの入口で閾値と比べて捨てる。閾値は
`sfu_set_log_level` でいつでも動かせるので、動いているシミュレータをビルドし直さずに
デバッグの段を出せる。既定は `SFU_LOG_INFO` で、SILS のホスト版と同じ振る舞いになる。

3 つの最小動作確認は `--log-jsonl <path>` でこれを JSON Lines として書き出す。鍵は
`AGENTS.md`「新しく書くコードのログの決まり」のとおり。`run_id` は実行ごとに入口で 1 つ発行し
（`--run-id` で外から渡せる）、形は `lib/sfcli/utils/jsonl_log.py` の `new_run_id()` と同じ
`YYYYMMDDTHHMMSSZ-` ＋ 16 進 8 桁である。`boot_id` は `<run_id>-b1`。

```bash
# ログを書きながら飛ばす（既定の出力先はビルドディレクトリの中で、git 管理外）
./simulator/unity/native/build-native/sfu_bridge_smoke 30 \
    --log-jsonl simulator/unity/native/build-native/logs/bridge_smoke.jsonl

# 警告だけを読む
jq -c 'select(.level=="warn")' simulator/unity/native/build-native/logs/bridge_smoke.jsonl

# あるタグの行を仮想時刻つきで追う
jq -r 'select(.tag=="StateManager") | "\(.sim_us) \(.msg)"' <path>
```

1 行はこの形になる。

```json
{"ts":"2026-09-20T11:35:54.459Z","level":"warn","src":"fw","event":"fw.log",
 "run_id":"20260920T113554Z-5bee2788","boot_id":"20260920T113554Z-5bee2788-b1",
 "sim_us":3000,"tag":"MagTask","msg":"BMM150 init failed: ESP_ERR — Mag disabled (Optional)"}
```

橋渡し自身の行は `src: "bridge"` で、`bridge.boot`／`bridge.shutdown`／`bridge.error` を出す。

**ログを取り出してもシミュレーションの結果は変わらない。** 記録の取り出しは時計を動かさず、
タスクを走らせず、プラントの状態にも触れない。ログを書く実行と書かない実行の標準出力が完全に
一致することを確かめてある。2 回の実行で違うのは `ts` と `run_id` の末尾の乱数だけなので、
一致を比べるときはその 2 つを除き、`sim_us`・`level`・`tag`・`msg` の列を比べる。

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
| `wrench_dt_s` は診断用 | 返る `force_local`・`torque_local`・`wind_force_world` は**区間平均の力**である。力を自分の物理刻みで積分するのはホストなので、`AddRelativeForce` 等へはそのまま渡す ― `wrench_dt_s` を掛けたり割ったりしない。`wrench_dt_s` はその平均が覆う区間の長さで、2.5 ms 固定ではない（プラントが副刻みの端数を繰り越すため、最初の刻みは 2.25 ms になりうる）。ホストの刻みと大きく食い違っていれば何かがおかしい、と分かるための診断の値である |
| エディタ用 dylib の読み込み直し | 再生のたびに読み込み直す仕組みは Unity プロジェクトと一緒に作る。段階 2 では dylib までとした |
| 接触モデル | `sfu_external_smoke.cpp` の床は z=0 のばね・ダンパで、PhysX の接触ソルバではない。空中の軌跡は近いが、接地の瞬間から分かれる |
| スタックの大きさと整列 | タスクごとに 1 MiB（14 タスクで 14 MiB）。ファームの `config::STACK_*` は 32bit 向けの値なので従っていない。**スタックは 16 バイト境界に整列させなければならない。** C ABI が呼び出し境界で 16 バイト整列のスタックポインタを要求し、コンパイラは 16 バイト整列のローカルをスタックポインタのマスクで配置するためである。`malloc` が保証するのは wasm32 では 8 バイトまでなので、`scheduler_fiber.cpp` は `std::aligned_alloc(16, …)` で確保する（`e4ed60cd`。整列を外すと飛行がヒープの位置で 2 通りに分かれる） |
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
| `sfu_bridge_smoke` | native (thread) | 0.0090 s | 0.684 m | 0.492 m | hover OK |
| `sfu_bridge_smoke` | wasm (fiber) / Node.js | 0.0030 s | 0.684 m | 0.492 m | hover OK |
| `sfu_external_smoke` | native (thread) | 0.0094 s | 0.680 m | 0.480 m | hover OK |
| `sfu_external_smoke` | wasm (fiber) / Node.js | 0.0031 s | 0.680 m | 0.480 m | hover OK |
| `sfu_module_check.mjs` | wasm module / Node.js | 0.0033 s | 0.680 m | 0.484 m | hover OK |

All are far under the 0.30 s pass criterion. The thread build being about three times slower is
consistent with why stage 1 chose the fiber scheduler (plan §9).

### Native and wasm Agree BYTE FOR BYTE

`sfu_bridge_smoke` and `sfu_external_smoke` print **identical stdout under native and under
wasm**, and `just unity-native-test` confirms it with `diff` on every run. This section used to
say the spread in the final altitude came from per-compiler floating-point rounding; that was
not true. Only `sfu_module_check.mjs` differs, by a few millimetres, and that is its host
integrator (C++ `float` against JavaScript's doubles), not the compiler.

To keep the agreement, `CMakeLists.txt` adds `-ffp-contract=off` to **every target**. By default
a compiler may contract `a*b + c` into one fused multiply-add, rounding once instead of twice,
and Clang and Emscripten make that choice independently. Measured, **the flag changed neither
side's output by a single bit** in this configuration — Emscripten was not contracting, and
Xcode's clang was not contracting this code either. It is there so that a future compiler
version or optimisation setting cannot quietly break the agreement.

It is deliberately NOT added to the stage 1(a) spike in `spike/`. Adding it there was measured
to leave the trace hash at `48852b7d2bddad63`, unchanged; `spike/` is the record of that
measurement as it was taken, and there is no reason to rebuild a record after the fact.

All three checks also **print identical stdout across two runs**. Only the speed figure goes to
stderr: a wall-clock measurement never repeats, and mixing it into stdout would break that check.

### The Flight Does Not Depend on the Heap Layout at Boot (it once did — fiber stack alignment)

**Allocating before `sfu_boot` is fine; the flight comes out the same.**

It once did not. A single 48-byte allocation ahead of the boot turned the hover into an emergency
DISARM on "Impact detected: 8.0G", and a 16-byte shift was enough to flip it. Unity's `.jslib` has
no choice but to allocate before the boot, so through Unity the flight failed every time.

**The cause was not the allocation but the alignment of the fiber scheduler's task stacks.** The
scheduler took each task's stack from `std::malloc`, which guarantees only `max_align_t` — 8 bytes
under wasm32 — while the C ABI requires a 16-byte-aligned stack pointer at a call boundary and the
compiler places 16-byte-aligned locals by MASKING the stack pointer rather than rounding it up. On
a stack starting 8-mod-16 that slot moved downwards onto another local, and the two silently
corrupted each other. Whether a stack landed on the boundary depended only on the heap offset at
that moment, hence the sensitivity to a directory name's length or to one allocation before the
boot. **Commit `e4ed60cd` fixed it with `std::aligned_alloc(16, ...)`.**

The old "leave the heap alone until the boot" discipline is therefore gone. A regression is caught
by a check instead: `just unity-native-test` confirms all four of these on every run.

| What is checked | How |
|---|---|
| The two C++ checks, native | `diff` of stdout with `--preallocate 48` against stdout without it |
| The same two, wasm | The same `diff`. This is where the alignment fault actually lived |
| The stage 1(a) module | `spike/heap_layout_check.mjs` — 32 pre-boot shifts must agree |
| The shipped bridge module | `bridge/sfu_heap_layout_check.mjs` — 24 pre-boot shifts must agree |

The last two walk a 16-byte window one byte at a time, then sample 48, 64, 128, 256, 512, 1024,
2048 and 4096. A run that never left the ground would agree with itself trivially and hide a
regression, so the peak altitude and the flight state are part of the verdict too. Reverting
`aligned_alloc` to `malloc` makes the bridge check report 11 of 24 differing, the differing side
ending at 0.013 m (on the ground) in state 1 — that is how the check was confirmed to detect the
fault it guards against.

`sfu_smoke_options.hpp` still holds `const char*` into `argv` rather than `std::string`. That is no
longer a correctness requirement, just the cheaper way to read a command line that outlives the
parse.

### Artefact Sizes

| Artefact | Size | Purpose |
|---|---|---|
| `sfu_firmware.wasm` | 444,482 bytes | What Unity WebGL loads through `.jslib` |
| `sfu_firmware.js` | 72,352 bytes | The loader beside it, exporting `createSfuFirmware` |
| `libsfu_firmware.dylib` | 518,744 bytes | The development plugin for the Unity editor (macOS arm64) |

The dylib exposes exactly the 17 `sfu_*` symbols (confirmed with `nm -gU`). The firmware, plant
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

Seventeen entry points (the ABI revision is **2**). Everything crossing the boundary is in
**Unity's own conventions**
(left-handed, Y up, quaternion in x,y,z,w order, metres, seconds, radians per second); the
conversion to NED/FRD happens only in `simulator/sils/frames/frames_unity.hpp`.

| Entry point | Role |
|---|---|
| `sfu_abi_version()` | The ABI revision; checked before anything else |
| `sfu_struct_size(which)` | The size this module compiled a struct to, for the C# side to check its own `sizeof` against |
| `sfu_boot(&config)` | Power on: plant init → seed the pairing store → `app_main` (BSP plus 14 tasks). A second call is an error |
| `sfu_shutdown()` | Unwind the tasks and free their stacks. **After it every entry point returns `SFU_ERR_SHUT_DOWN`** |
| `sfu_last_status()` | The result of the most recent `sfu_step` / `sfu_shutdown`; how to read a result under wasm |
| `sfu_step(&in, &out)` | The main path: inject the state → `run_until(now + dt_us)` → return the interval-averaged force and torque plus the state |
| `sfu_param_count()` / `sfu_param_info(i, &out)` | How many tuning parameters the firmware exposes (99) and their identity |
| `sfu_param_set(name, value)` / `sfu_param_get(name, &out)` | Read and write by name; the type comes from the SSOT table |
| `sfu_set_wind(x, y, z)` | Constant wind force [N] in Unity's world frame |
| `sfu_set_motor_health(motor, gain)` | Per-motor thrust health (1 healthy, 0 dead) |
| `sfu_set_imu_bias(ax, ay, az, gx, gy, gz)` | Constant raw IMU bias, body-local Unity frame |
| `sfu_log_read_record(&out)` / `sfu_log_dropped()` | Take the firmware's log out as **structured records**, one at a time |
| `sfu_log_read(buffer, capacity)` | The same thing as newline-separated text, for a caller that only prints |
| `sfu_set_log_level(level)` | The lowest level kept; the default `SFU_LOG_INFO` discards `ESP_LOGD` / `ESP_LOGV` |

Each of the five structs starts with `uint32_t struct_size`, which the caller fills with the
`sizeof` of its OWN declaration. The bridge compares and returns `SFU_ERR_STRUCT_SIZE` on a
mismatch, rather than reading past the end of a shorter struct — and so that a later version can
grow a struct at the end without breaking an older caller.

Sizes under this ABI (the same for native arm64 and for wasm32): `SfuConfig` 48 bytes,
`SfuStepIn` 96 bytes, `SfuStepOut` 168 bytes, `SfuParamInfo` 84 bytes, `SfuLogRecord` 272 bytes.

### Do Not Read `sfu_step`'s Return Value Under wasm

**Under wasm the return value of `sfu_step` is meaningless — read `out->status` (or
`sfu_last_status()`).** The fiber scheduler switches stacks inside the call, and Asyncify
implements that by unwinding and rewinding the whole wasm stack, so what JavaScript receives is
the rewind stub's value (0), not the one the C code returned. This was reproduced in a minimal
example. `out->status` is the LAST thing `sfu_step` writes to memory, so it survives. A native
caller may read either; a caller that must work in both places reads `status`.

### The Step Is Whole Microseconds

`SfuStepIn::dt_us` is a `uint32_t` of MICROSECONDS, not seconds. A float crossing the boundary
would have to be rounded inside the bridge, where no caller could predict the result; with an
integer, N ticks of `dt_us` land the clock on exactly `N × dt_us` (`just unity-native-test`
confirms it over 12,000 ticks).

- Zero, or anything above the cap (`SFU_DT_US_MAX` = 100,000 µs = 100 ms), is `SFU_ERR_BAD_ARGUMENT`
- The cap **itself** is accepted
- A value that is not a multiple of `SFU_PLANT_SUBSTEP_US` (250 µs) is accepted too, and the
  clock advances by exactly that much. The plant carries its sub-step remainder over, so the
  remainder appears in the NEXT tick's `wrench_dt_s` rather than being lost

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
| `bridge/sfu_log_ring.cpp` | The firmware's log ring buffer (512 records of virtual time, level, tag and body) |
| `bridge/shim/esp_log.h` | Redirects `ESP_LOGx` from stderr into that ring buffer |
| `bridge/sfu_log_jsonl.hpp` | Writes those records as JSON Lines, in `AGENTS.md`'s shape |
| `bridge/sfu_smoke_options.hpp` | The command line both C++ checks share |
| `bridge/sfu_rc_script.hpp` | The scripted stick input both smoke checks share |
| `bridge/sfu_bridge_smoke.cpp` | The minimum-operation check without Unity (the plant owns the body) |
| `bridge/sfu_external_smoke.cpp` | The externally supplied check (C++ stands in for Unity) |
| `bridge/sfu_flight.mjs` | The struct layout and the flight (script, physics, verdict) both JS checks share |
| `bridge/sfu_module_check.mjs` | Flies the wasm module from JS (JS stands in for Unity) |
| `bridge/sfu_heap_layout_check.mjs` | Flies the same module behind 24 pre-boot heap shifts and requires them to agree |
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
2. `sfu_bridge_smoke` twice, with the two stdout streams compared by `diff` (determinism)
3. `sfu_external_smoke` twice, compared the same way
4. The same two under wasm, **compared against the native output with `diff`** (byte-identity)
5. `sfu_module_check.mjs` — loads the shipped `sfu_firmware.js` from JS, checks the struct sizes
   against `sfu_struct_size`, and flies 30 seconds
6. Writes a JSON Lines log and confirms `jq` can read every line of it
7. `actuator_parity_test`, only when a built SILS has one
8. Both C++ checks again with `--preallocate 48`, `diff`ed against the runs without it, under
   native and under wasm
9. `spike/heap_layout_check.mjs` and `bridge/sfu_heap_layout_check.mjs` — scanning pre-boot heap
   shifts and requiring the flights to agree. `build_module_spike.sh` builds the stage 1(a) module

`sfu_bridge_smoke` also checks the ABI's promises: a second `sfu_boot` is refused, `now_us` after
N ticks is exactly N×2500 µs, an out-of-range `dt_us` is refused rather than clamped, and after
`sfu_shutdown` every entry point returns `SFU_ERR_SHUT_DOWN`. `sfu_external_smoke` and
`sfu_module_check.mjs` hit the vehicle with a gust partway through and require it to be **back
near level and still FLYING at the end**: altitude alone cannot establish the torque's sign
(reversing every component still produces the same climb), and the reversed build was confirmed
to fail.

`actuator_parity_test` (battery voltage bit-identical between `actuator_model.hpp` and the
MuJoCo `plant.cpp`) needs MuJoCo, so it is a target on the `simulator/sils` side.
`unity-native-test` runs it when a built SILS has one and prints "skipped" when it does not.

```bash
cmake --build <the sils build> --target actuator_parity_test
<the sils build>/actuator_parity_test simulator/sils/models/stampfly.xml
```

It compares two configurations: the default, and `motor_delay_ms = 8.0` with
`torque_authority = 0.75`. Turbulence and the reaction torque cannot be compared at all, because
the MuJoCo Plant has no accessor for either — a known gap, described at the head of that file.

Build outputs (`build-*/`, `.cache/`) are not committed (see `.gitignore`).

## Taking the Firmware's Log Out as Structured Records

The firmware calls `ESP_LOGx(tag, fmt, ...)` unmodified. The `bridge/shim/esp_log.h` placed
earlier on the include path receives it and stores it in the ring **before it is made into one
string** — the four things the macro actually had.

| Field | Content |
|---|---|
| `sim_us` | The virtual clock at that moment [µs]; not the wall clock, so two runs agree |
| `level` | `SFU_LOG_ERROR` / `_WARN` / `_INFO` / `_DEBUG` / `_VERBOSE` (the `esp_log_level_t` numbers) |
| `tag` | The `ESP_LOGx` tag |
| `message` | The body with the format applied. **No level or tag prefix is pasted on** |

`ESP_LOGD` / `ESP_LOGV` are not compiled out; they reach the ring and are discarded there
against a threshold the host moves with `sfu_set_log_level`, so a developer can turn the debug
levels on in a running simulator without a rebuild. The default is `SFU_LOG_INFO`, which
behaves as the SILS host build does.

All three checks write this out as JSON Lines with `--log-jsonl <path>`, using the keys from
`AGENTS.md`. `run_id` is issued once per run at the entrance (`--run-id` passes one in) in the
same shape `lib/sfcli/utils/jsonl_log.py`'s `new_run_id()` produces: `YYYYMMDDTHHMMSSZ-` plus
eight hex digits. `boot_id` is `<run_id>-b1`.

```bash
# Fly while writing the log (the default destination is inside the build directory, untracked)
./simulator/unity/native/build-native/sfu_bridge_smoke 30 \
    --log-jsonl simulator/unity/native/build-native/logs/bridge_smoke.jsonl

# Read just the warnings
jq -c 'select(.level=="warn")' simulator/unity/native/build-native/logs/bridge_smoke.jsonl
```

**Reading the log does not change the simulation.** Draining the ring moves no clock, runs no
task and touches no plant state; a run that writes a log and one that does not were confirmed to
print identical stdout. The only fields that differ between two runs are `ts` and the random
tail of `run_id`, so a comparison leaves those out and compares `sim_us`, `level`, `tag` and
`msg`.

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
| `wrench_dt_s` is for diagnosis | `force_local`, `torque_local` and `wind_force_world` are interval-AVERAGED forces. The host integrates them over its own physics step, so they go straight into `AddRelativeForce` and friends — do not multiply or divide by `wrench_dt_s`. That field is the length of the interval the average covers, which is not a fixed 2.5 ms (the plant carries its sub-step remainder over, so the first tick can be 2.25 ms). It is there so a host can notice when it has drifted far from its own tick |
| Reloading the editor dylib | Reloading it between play sessions comes with the Unity project; stage 2 stops at the dylib |
| Contact model | The floor in `sfu_external_smoke.cpp` is a spring-damper at z=0, not PhysX's contact solver. Trajectories are close in the air and part ways on impact |
| Stack sizes and alignment | 1 MiB per task (14 MiB for 14 tasks). The firmware's `config::STACK_*` values are 32-bit figures and are deliberately not honoured. **A task stack must be 16-byte aligned:** the C ABI requires a 16-byte-aligned stack pointer at a call boundary, and the compiler places 16-byte-aligned locals by masking the stack pointer. `malloc` guarantees only 8 bytes under wasm32, so `scheduler_fiber.cpp` allocates with `std::aligned_alloc(16, ...)` (`e4ed60cd`; without the alignment the flight splits in two according to where the heap sits) |
| Duplication with `plant.cpp` | `actuator_model.hpp` was copied out of `plant.cpp`, so the same formulas exist in two places for now. Keeping them from drifting apart is `actuator_parity_test`'s job, and it belongs in CI |
