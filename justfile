# Task runner for the Nix development shell (see docs/plans/unity-simulator.md).
# Nix 開発シェル用のタスクランナー（docs/plans/unity-simulator.md 参照）。
#
# Firmware builds stay with the sf CLI after `source setup_env.sh`; this file
# only holds tasks that run inside `nix develop`.
# ファームウェアのビルドは従来どおり `source setup_env.sh` 後の sf CLI が担う。
# このファイルには `nix develop` の中で動くタスクだけを置く。

# Show the available recipes
# 利用できるレシピの一覧を表示する
default:
    @just --list

# Format the files covered by treefmt (flake.nix and files added with it)
# treefmt の対象ファイル（flake.nix と一緒に新設したファイル）を整形する
fmt:
    nix fmt

# Run every flake check, including the formatting check
# flake の検査を全て実行する（整形の検査を含む）
check:
    nix flake check

# Verify that this justfile is formatted as `just --fmt` would write it
# この justfile が `just --fmt` の出力どおりの書式かを確かめる
lint-just:
    just --fmt --check --unstable

# Rebuild the stage 1(a) WebAssembly spike and fly it for N simulated seconds
# 段階 1(a) の WebAssembly 技術検証を作り直し、N 秒ぶん飛ばす
unity-native-spike seconds="30":
    bash simulator/unity/native/spike/build_spike.sh {{ seconds }}

# Build the Unity native core both ways: the macOS dylib and the WebGL module
# Unity 版のネイティブコアを両方ビルドする: macOS の dylib と WebGL 用モジュール
unity-native-build:
    cmake -S simulator/unity/native -B simulator/unity/native/build-native -G Ninja \
        -DCMAKE_C_COMPILER=/usr/bin/clang -DCMAKE_CXX_COMPILER=/usr/bin/clang++
    cmake --build simulator/unity/native/build-native
    EM_CACHE="$PWD/simulator/unity/native/.cache" emcmake cmake \
        -S simulator/unity/native -B simulator/unity/native/build-wasm -G Ninja
    EM_CACHE="$PWD/simulator/unity/native/.cache" cmake --build simulator/unity/native/build-wasm

# Run every Unity native-core check: the frames test, both smoke flights, and the wasm module
# Unity 版ネイティブコアの検査を全て実行する: 座標変換の試験・2 つの最小動作確認・wasm モジュール
unity-native-test seconds="30": unity-native-build
    ./simulator/unity/native/build-native/frames_unity_test
    # Two runs of each smoke check must print exactly the same lines — the
    # stage 2 pass criterion for determinism.
    # 各最小動作確認は、2 回の実行がまったく同じ行を出さなければならない。決定論に
    # ついての段階 2 の合格基準である。
    ./simulator/unity/native/build-native/sfu_bridge_smoke {{ seconds }} 2>/dev/null > simulator/unity/native/build-native/bridge_smoke.run1.txt
    ./simulator/unity/native/build-native/sfu_bridge_smoke {{ seconds }} 2>/dev/null > simulator/unity/native/build-native/bridge_smoke.run2.txt
    diff simulator/unity/native/build-native/bridge_smoke.run1.txt simulator/unity/native/build-native/bridge_smoke.run2.txt
    ./simulator/unity/native/build-native/sfu_external_smoke {{ seconds }} 2>/dev/null > simulator/unity/native/build-native/external_smoke.run1.txt
    ./simulator/unity/native/build-native/sfu_external_smoke {{ seconds }} 2>/dev/null > simulator/unity/native/build-native/external_smoke.run2.txt
    diff simulator/unity/native/build-native/external_smoke.run1.txt simulator/unity/native/build-native/external_smoke.run2.txt
    @echo "[unity-native-test] native: frames_unity_test OK; both smoke checks hover and repeat identically"
    # `sfu_shutdown` must always return and always give its threads back. It once
    # did not: booting and shutting down without a single `sfu_step` in between
    # left all fourteen task threads parked forever, which is how the Unity
    # editor hung (`SimLoop.BootFirmware` boots and returns without stepping that
    # frame). Ten repetitions over eight tick counts — 0 first, the one that hung
    # — from a thread other than the one that stepped, with a second module
    # resident and booted throughout, as the editor has after a second play
    # session. Each repetition must come back to the same live thread count, so a
    # teardown that returns but leaks is caught too.
    # `sfu_shutdown` は必ず戻り、必ずスレッドを返さなければならない。かつてそうで
    # なかった。間に `sfu_step` を 1 回も挟まずに起動して終了すると、14 本のタスクの
    # スレッドが永久に待機したままになり、それが Unity のエディタが固まった経路で
    # ある（`SimLoop.BootFirmware` は起動してその frame では刻まずに戻る）。刻みの
    # 回数 8 通り ― 固まった 0 を先頭に ― を 10 回繰り返し、刻んだのとは別のスレッド
    # から、もう 1 つのモジュールを起動したまま常駐させて行う。2 回目の再生の後の
    # エディタがその状態だからである。繰り返しごとに同じスレッド数へ戻ることも
    # 求めるので、戻りはするが漏らす後始末も捕まえられる。
    ./simulator/unity/native/build-native/sfu_shutdown_check \
        simulator/unity/native/build-native/libsfu_firmware.dylib 10
    # The same two flights with the heap shifted before `sfu_boot`. The flight
    # must not depend on where the heap sits: it once did, because the fiber
    # scheduler took its task stacks from `malloc` (8-byte aligned under wasm32)
    # while the compiler places 16-byte-aligned locals by masking the stack
    # pointer — fixed in e4ed60cd by `aligned_alloc(16, ...)`. 48 bytes is what
    # Unity's `.jslib` allocates before the boot, which is how this reached the
    # Unity build.
    # 同じ 2 つの飛行を、`sfu_boot` の前にヒープをずらして行う。飛行はヒープの位置に
    # 依存してはならない。かつては依存した。fiber 版スケジューラがタスクスタックを
    # `malloc`（wasm32 では 8 バイト整列）で取る一方、コンパイラは 16 バイト整列の
    # ローカルをスタックポインタのマスクで配置していたためで、e4ed60cd の
    # `aligned_alloc(16, ...)` で直っている。48 バイトは Unity の `.jslib` が起動前に
    # 確保する量で、これが Unity 版まで届いた経路である。
    ./simulator/unity/native/build-native/sfu_bridge_smoke {{ seconds }} --preallocate 48 2>/dev/null > simulator/unity/native/build-native/bridge_smoke.prealloc.txt
    diff simulator/unity/native/build-native/bridge_smoke.run1.txt simulator/unity/native/build-native/bridge_smoke.prealloc.txt
    ./simulator/unity/native/build-native/sfu_external_smoke {{ seconds }} --preallocate 48 2>/dev/null > simulator/unity/native/build-native/external_smoke.prealloc.txt
    diff simulator/unity/native/build-native/external_smoke.run1.txt simulator/unity/native/build-native/external_smoke.prealloc.txt
    @echo "[unity-native-test] native: allocating before sfu_boot does not change the flight"
    node simulator/unity/native/build-wasm/frames_unity_test.js
    # The same two flights under wasm, kept so they can be compared with the
    # native ones BYTE FOR BYTE. Both toolchains compile with -ffp-contract=off,
    # so the only thing left that could differ is a real mistake.
    # 同じ 2 つの飛行を wasm でも行い、ネイティブのものと**バイト単位**で比べられる
    # ように保存する。両方のツールチェーンが -ffp-contract=off でコンパイルするので、
    # 残る違いは本物の誤りだけである。
    node simulator/unity/native/build-wasm/sfu_bridge_smoke.js {{ seconds }} 2>/dev/null > simulator/unity/native/build-wasm/bridge_smoke.txt
    diff simulator/unity/native/build-native/bridge_smoke.run1.txt simulator/unity/native/build-wasm/bridge_smoke.txt
    node simulator/unity/native/build-wasm/sfu_external_smoke.js {{ seconds }} 2>/dev/null > simulator/unity/native/build-wasm/external_smoke.txt
    diff simulator/unity/native/build-native/external_smoke.run1.txt simulator/unity/native/build-wasm/external_smoke.txt
    @echo "[unity-native-test] native and wasm produce byte-identical output"
    # And under wasm, where the alignment fault actually lived: the shifted heap
    # must still produce the native output, byte for byte.
    # そして、整列の不具合が実際に居た wasm でも。ずらしたヒープで、なおネイティブの
    # 出力とバイト単位で一致しなければならない。
    node simulator/unity/native/build-wasm/sfu_bridge_smoke.js {{ seconds }} --preallocate 48 2>/dev/null > simulator/unity/native/build-wasm/bridge_smoke.prealloc.txt
    diff simulator/unity/native/build-native/bridge_smoke.run1.txt simulator/unity/native/build-wasm/bridge_smoke.prealloc.txt
    node simulator/unity/native/build-wasm/sfu_external_smoke.js {{ seconds }} --preallocate 48 2>/dev/null > simulator/unity/native/build-wasm/external_smoke.prealloc.txt
    diff simulator/unity/native/build-native/external_smoke.run1.txt simulator/unity/native/build-wasm/external_smoke.prealloc.txt
    @echo "[unity-native-test] wasm: allocating before sfu_boot does not change the flight"
    node simulator/unity/native/bridge/sfu_module_check.mjs \
        simulator/unity/native/build-wasm/sfu_firmware.js {{ seconds }} \
        --log-jsonl simulator/unity/native/build-wasm/logs/module_check.jsonl
    # The firmware's log as JSON Lines, and that `jq` can read every line of it.
    # ファームのログを JSON Lines で書き、その全行を `jq` が読めることを確かめる。
    ./simulator/unity/native/build-native/sfu_bridge_smoke {{ seconds }} \
        --log-jsonl simulator/unity/native/build-native/logs/bridge_smoke.jsonl > /dev/null
    jq -e -s 'length > 0' simulator/unity/native/build-native/logs/bridge_smoke.jsonl > /dev/null
    @echo "[unity-native-test] JSON Lines log written and readable by jq"
    # The MuJoCo-side parity test, only when a built SILS has one: this project
    # does not fetch MuJoCo, so the target exists only after simulator/sils has
    # been configured and built.
    # MuJoCo 側の対照試験は、ビルド済みの SILS に在るときだけ実行する。本プロジェクト
    # は MuJoCo を取得しないので、このターゲットは simulator/sils を構成してビルドした
    # 後にしか存在しない。
    @parity=$(ls simulator/sils/build*/actuator_parity_test 2>/dev/null | head -1); \
    if [ -n "$parity" ]; then \
        "$parity" simulator/sils/models/stampfly.xml; \
    else \
        echo "[unity-native-test] actuator_parity_test skipped (no built SILS in simulator/sils/build*/)"; \
    fi
    # The two heap-layout scans, last because each builds and flies a module
    # dozens of times. Both walk a 16-byte window one byte at a time and then
    # sample larger shifts, requiring every flight to agree: the stage 1(a) spike
    # module, where the alignment fault was found, and the shipped bridge module
    # Unity loads. Fourteen simulated seconds is past the gust and long enough
    # for the failsafe the fault tripped to show.
    # 2 つのヒープ配置の走査。モジュールを何十回も作って飛ばすので最後に置く。
    # どちらも 16 バイトの窓を 1 バイトずつ歩いてからより大きなずれを抜き取りで見て、
    # 全ての飛行が一致することを求める。対象は、整列の不具合が見つかった段階 1(a) の
    # 技術検証のモジュールと、Unity が読み込む出荷用の橋渡しのモジュールである。
    # シミュレーション 14 秒は突風を過ぎており、不具合が誤作動させたフェイルセーフが
    # 現れるのに十分な長さである。
    bash simulator/unity/native/spike/build_module_spike.sh
    node simulator/unity/native/spike/heap_layout_check.mjs \
        simulator/unity/native/build-module-spike/sfu_firmware.js 14
    node simulator/unity/native/bridge/sfu_heap_layout_check.mjs \
        simulator/unity/native/build-wasm/sfu_firmware.js 14
    # And the same question asked of what the host does BETWEEN ticks, which is
    # where the two heap scans stop: they shift the heap once before `sfu_boot`
    # and then leave it alone, while the browser allocates, frees, drains the log
    # and reads parameters on frame boundaries that land on different ticks every
    # run. Sixteen seeds over 25 simulated seconds, each holding ALT_HOLD well
    # past the moment a browser run came apart; the full 64-seed sweep is
    # `--seeds 64`, and `--noise <metres>` measures the hold's sensitivity
    # instead of its determinism.
    # 同じ問いを、刻みと刻みの**あいだ**にホストが行うことへ向ける。2 つのヒープの
    # 走査が届かないのがそこである。走査は `sfu_boot` の前に 1 回ずらして以後放って
    # おくが、ブラウザは実行ごとに違う刻みへ落ちるフレームの切れ目で、確保・解放・
    # ログの取り出し・パラメータの読み出しを行う。種 16 通り、各 25 秒で、ブラウザの
    # 実行が崩れた時刻を十分に越えて ALT_HOLD を保つ。64 通りの全数は `--seeds 64`、
    # `--noise <メートル>` は決定性ではなく保持の敏感さを測る。
    node simulator/unity/native/bridge/sfu_inflight_alloc_check.mjs \
        simulator/unity/native/build-wasm/sfu_firmware.js 25 --seeds 16
