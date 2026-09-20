#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Kouhei Ito
#
# Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 1 spike).
# https://github.com/M5Fly-kanazawa/stampfly_ecosystem
#
# Rebuilds and runs the stage 1(a) feasibility check of
# docs/plans/unity-simulator.md: the unmodified firmware compiled to
# WebAssembly, driven by the thread-free fiber scheduler.
#
# docs/plans/unity-simulator.md の段階 1(a) 技術検証を作り直して実行する:
# 無改変のファームウェアを WebAssembly へコンパイルし、スレッド無しの
# fiber 型スケジューラで動かす。
#
# Run it from inside the Nix development shell (`nix develop`), which supplies
# emcc, cmake, ninja and node. `just unity-native-spike` does that for you.
# Nix 開発シェル（`nix develop`）の中で実行する。emcc・cmake・ninja・node は
# そこから来る。`just unity-native-spike` がその手順を代行する。
#
# Usage / 使い方:
#   build_spike.sh [seconds]     # simulated seconds, default 30
#                                # シミュレーションする秒数。既定 30

set -euo pipefail

SIM_SECONDS="${1:-30}"

# Repository root, derived from this script's location so the script works from
# any working directory (the agent guidance forbids relying on the caller's cwd).
# このスクリプトの位置からリポジトリ直下を求める。どの作業ディレクトリから
# 呼ばれても動くようにするため。
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NATIVE_DIR="$(dirname -- "$SCRIPT_DIR")"
REPO_ROOT="$(cd -- "$NATIVE_DIR/../../.." && pwd)"

SILS="$REPO_ROOT/simulator/sils"
VN="$REPO_ROOT/firmware/vehicle"
BUILD_DIR="$NATIVE_DIR/build-spike"

# Emscripten writes its sysroot cache next to the compiler unless redirected,
# which fails on a read-only Nix store path. Keep it inside the ignored
# build-time cache directory instead.
# Emscripten は向き先を変えないとコンパイラの隣に sysroot キャッシュを書き、
# 読み取り専用の Nix ストアでは失敗する。無視対象のキャッシュ置き場へ向ける。
export EM_CACHE="$NATIVE_DIR/.cache"

mkdir -p "$BUILD_DIR/obj-fiber-wasm_O2"

# ---------------------------------------------------------------------------
# Source list. Mirrors the emu_vehicle target of simulator/sils/CMakeLists.txt,
# with two substitutions: the fiber scheduler replaces rtos/scheduler.cpp, and
# plant_external.cpp (no MuJoCo) replaces plant/plant.cpp.
# ソース一覧。simulator/sils/CMakeLists.txt の emu_vehicle と同じ構成で、
# 2 点だけ差し替える: fiber 版スケジューラが rtos/scheduler.cpp の代わり、
# plant_external.cpp（MuJoCo 無し）が plant/plant.cpp の代わり。
# ---------------------------------------------------------------------------
mapfile -t FW_SRCS < <(
    find "$VN/main" "$VN/tasks" "$VN/components" \
         \( -name '*.cpp' -o -name '*.c' \) -type f \
      | grep -Ev '/(docs|examples|test|build)/|_test\.|/CubeIDE' \
      | sort
)

SILS_SRCS=(
    "$SILS/devices/vl53_platform_glue.c"
    "$SILS/devices/virtual_board.cpp"
    "$SILS/devices/vl53_device.cpp"
    "$SILS/devices/pmw3901_device.cpp"
    "$SILS/devices/espnow_hub.cpp"
    "$SILS/devices/emu_record.cpp"
    "$SILS/devices/emu_flightlog.cpp"
    "$SILS/devices/emu_flightlog_vehicle.cpp"
    "$SILS/devices/emu_vehicle_glue.cpp"
    "$SILS/devices/scenario.cpp"
    "$SILS/devices/scenario_inject.cpp"
    "$SILS/devices/console_feeder.cpp"
    "$SILS/devices/esp_console_shim.cpp"
    "$SILS/devices/emu_realtime.cpp"
    "$SILS/devices/rc_stdin.cpp"
    "$SILS/plant/plant_external.cpp"
    "$SILS/rtos/scheduler_fiber.cpp"
    "$SILS/rtos/esp_timer_shim.cpp"
    "$SILS/compat/clock_shim.cpp"
    "$SILS/compat/nvs_shim.cpp"
    "$SILS/esp_idf_host/win_stdio_shim.cpp"
    "$NATIVE_DIR/compat_wasm/wasm_stdio_shim.cpp"
    "$SCRIPT_DIR/hover_spike.cpp"
)

# ---------------------------------------------------------------------------
# Include path. compat_wasm and rtos_fiber come FIRST so their <cstdio> shim and
# the scheduler.hpp forwarder win over the ones next to the originals; that is
# how the firmware and the SILS consumers are redirected without editing them.
# include パス。compat_wasm と rtos_fiber を最優先に置き、<cstdio> シムと
# scheduler.hpp の転送ヘッダを本来のものより先に解決させる。これがファームと
# SILS 利用側を無編集で差し替える仕組み。
# ---------------------------------------------------------------------------
INCS=(
    -I"$NATIVE_DIR/compat_wasm"
    -I"$NATIVE_DIR/rtos_fiber"
    -I"$SILS/esp_idf_host"
    -I"$SILS/compat"
    -I"$SILS/rtos"
    -I"$SILS/plant"
    -I"$SILS/frames"
    -I"$SILS/devices"
    -I"$VN/main"
    -I"$VN/tasks"
    -I"$VN/components/sf_hal_vl53l3cx/include/vl53lx"
    -I"$VN/components/sf_hal_vl53l3cx/src"
    -I"$VN/components/sf_hal_vl53l3cx/src/vl53lx"
    -I"$VN/components/sf_hal_bmi270/src"
    -I"$VN/components/sf_hal_pmw3901/src"
    -I"$REPO_ROOT/firmware/common/protocol/include"
)
while IFS= read -r dir; do
    INCS+=(-I"$dir")
done < <(find "$VN/components" -maxdepth 2 -type d -name include | sort)

# SILS_PLANT_EXTERNAL drops plant.hpp's four MuJoCo-typed declarations; every
# other declaration in that header is shared verbatim with the MuJoCo build.
# SILS_PLANT_EXTERNAL は plant.hpp の MuJoCo 型の宣言 4 つを落とす。同ヘッダの
# それ以外の宣言は MuJoCo 版とそのまま共有される。
COMMON_FLAGS=(-O2 -DSILS_PLANT_EXTERNAL=1 -Wno-deprecated-declarations)

echo "[spike] compiling ${#FW_SRCS[@]} firmware + ${#SILS_SRCS[@]} simulator translation units"
echo "[spike] ファームウェア ${#FW_SRCS[@]} ＋ シミュレータ ${#SILS_SRCS[@]} 翻訳単位をコンパイルする"

OBJS=()
compile_one() {
    local src="$1" obj="$2"
    if [[ "$src" == *.c ]]; then
        emcc -std=c11 "${COMMON_FLAGS[@]}" "${INCS[@]}" -c "$src" -o "$obj"
    else
        em++ -std=c++17 "${COMMON_FLAGS[@]}" "${INCS[@]}" -c "$src" -o "$obj"
    fi
}

for src in "${FW_SRCS[@]}" "${SILS_SRCS[@]}"; do
    rel="${src#"$REPO_ROOT"/}"
    obj="$BUILD_DIR/obj-fiber-wasm_O2/${rel//\//_}.o"
    OBJS+=("$obj")
    # Recompile only when the source is newer than the object, so a repeated
    # run costs seconds rather than minutes.
    # ソースがオブジェクトより新しいときだけ作り直す。2 回目以降を分でなく
    # 秒で終わらせるため。
    if [[ ! -f "$obj" || "$src" -nt "$obj" ]]; then
        compile_one "$src" "$obj"
    fi
done

# ASYNCIFY lets the fiber scheduler switch stacks inside WebAssembly. Whole-
# program instrumentation is kept: the measurement showed narrowing it with
# ASYNCIFY_ONLY is unnecessary. ALLOW_MEMORY_GROWTH covers the 14 MiB of task
# stacks (1 MiB each).
# ASYNCIFY により fiber 型スケジューラが WebAssembly 内でスタックを切り替えられる。
# 計装は全体のまま: ASYNCIFY_ONLY による絞り込みが不要であることは計測で確かめた。
# ALLOW_MEMORY_GROWTH はタスクスタック 14 MiB（1 タスク 1 MiB）ぶんを賄う。
echo "[spike] linking spike_fiber_wasm_O2.js"
em++ -O2 -sASYNCIFY -sALLOW_MEMORY_GROWTH=1 -sEXIT_RUNTIME=1 \
     -sINITIAL_MEMORY=64MB -sSTACK_SIZE=1MB \
     "${OBJS[@]}" -o "$BUILD_DIR/spike_fiber_wasm_O2.js"

echo "[spike] running ${SIM_SECONDS} simulated seconds under node"
echo "[spike] node で ${SIM_SECONDS} 秒ぶんのシミュレーションを実行する"
node "$BUILD_DIR/spike_fiber_wasm_O2.js" "$SIM_SECONDS"
