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
    node simulator/unity/native/build-wasm/frames_unity_test.js
    node simulator/unity/native/build-wasm/sfu_bridge_smoke.js {{ seconds }} > /dev/null
    node simulator/unity/native/build-wasm/sfu_external_smoke.js {{ seconds }} > /dev/null
    node simulator/unity/native/bridge/sfu_module_check.mjs \
        simulator/unity/native/build-wasm/sfu_firmware.js {{ seconds }}
