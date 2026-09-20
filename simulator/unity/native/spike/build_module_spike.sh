#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Kouhei Ito
#
# Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 1 spike).
# https://github.com/M5Fly-kanazawa/stampfly_ecosystem
#
# Builds the stage 1(b)(d) feasibility check of docs/plans/unity-simulator.md:
# the same unmodified firmware build as build_spike.sh, but linked as a
# MODULARIZE'd wasm module (createSfuFirmware) with C entry points, so Unity
# WebGL can create it from a .jslib and call it synchronously once per tick.
#
# docs/plans/unity-simulator.md の段階 1(b)(d) 技術検証をビルドする:
# build_spike.sh と同じ無改変ファームウェアのビルドを、C 入口を持つ
# MODULARIZE 済みの wasm モジュール（createSfuFirmware）として作る。Unity WebGL が
# .jslib から生成し、1 刻みにつき 1 回、同期で呼べるようにするため。
#
# Run it from inside the Nix development shell (`nix develop`), which supplies
# emcc, cmake, ninja and node.
# Nix 開発シェル（`nix develop`）の中で実行する。emcc・cmake・ninja・node は
# そこから来る。
#
# Usage / 使い方:
#   build_module_spike.sh [output_dir]   # default: ../build-module-spike
#                                        # 既定: ../build-module-spike

set -euo pipefail

# Repository root, derived from this script's location so the script works from
# any working directory (the agent guidance forbids relying on the caller's cwd).
# このスクリプトの位置からリポジトリ直下を求める。どの作業ディレクトリから
# 呼ばれても動くようにするため。
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NATIVE_DIR="$(dirname -- "$SCRIPT_DIR")"
REPO_ROOT="$(cd -- "$NATIVE_DIR/../../.." && pwd)"

SILS="$REPO_ROOT/simulator/sils"
VN="$REPO_ROOT/firmware/vehicle"
BUILD_DIR="$NATIVE_DIR/build-module-spike"
OUT_DIR="${1:-$BUILD_DIR}"

# Emscripten writes its sysroot cache next to the compiler unless redirected,
# which fails on a read-only Nix store path. Keep it inside the ignored
# build-time cache directory instead.
# Emscripten は向き先を変えないとコンパイラの隣に sysroot キャッシュを書き、
# 読み取り専用の Nix ストアでは失敗する。無視対象のキャッシュ置き場へ向ける。
export EM_CACHE="$NATIVE_DIR/.cache"

OBJ_DIR="$BUILD_DIR/obj-module-wasm_O2"
mkdir -p "$OBJ_DIR" "$OUT_DIR"

# ---------------------------------------------------------------------------
# Source list. Identical to build_spike.sh except that module_spike.cpp (C
# entry points, no main) replaces hover_spike.cpp (a main that owns the loop).
# ソース一覧。build_spike.sh と同じで、hover_spike.cpp（ループを所有する main）の
# 代わりに module_spike.cpp（C 入口、main 無し）を使う点だけが違う。
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
    "$SCRIPT_DIR/module_spike.cpp"
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

echo "[module-spike] compiling ${#FW_SRCS[@]} firmware + ${#SILS_SRCS[@]} simulator translation units"
echo "[module-spike] ファームウェア ${#FW_SRCS[@]} ＋ シミュレータ ${#SILS_SRCS[@]} 翻訳単位をコンパイルする"

compile-one() {
    local src="$1" obj="$2"
    if [[ "$src" == *.c ]]; then
        emcc -std=c11 "${COMMON_FLAGS[@]}" "${INCS[@]}" -c "$src" -o "$obj"
    else
        em++ -std=c++17 "${COMMON_FLAGS[@]}" "${INCS[@]}" -c "$src" -o "$obj"
    fi
}

OBJS=()
for src in "${FW_SRCS[@]}" "${SILS_SRCS[@]}"; do
    rel="${src#"$REPO_ROOT"/}"
    obj="$OBJ_DIR/${rel//\//_}.o"
    OBJS+=("$obj")
    # Recompile only when the source is newer than the object, so a repeated
    # run costs seconds rather than minutes.
    # ソースがオブジェクトより新しいときだけ作り直す。2 回目以降を分でなく
    # 秒で終わらせるため。
    if [[ ! -f "$obj" || "$src" -nt "$obj" ]]; then
        compile-one "$src" "$obj"
    fi
done

# Link options, one reason each:
#   MODULARIZE + EXPORT_NAME  the page gets a factory, so a power cycle is a new
#                             module rather than a re-init of the firmware's statics
#   ASYNCIFY                  lets the fiber scheduler switch stacks inside wasm
#   EXPORTED_FUNCTIONS        the C entry points module_spike.cpp defines
#   EXPORTED_RUNTIME_METHODS  cwrap for typed calls, HEAPF64 for the pose buffer,
#                             HEAPU8 so the caller can read the module's memory size,
#                             _malloc/_free so the caller can own that buffer
#   EXIT_RUNTIME=0            there is no main; the module stays alive between ticks
#   INVOKE_RUN=0              do not look for a main at load time
#   FILESYSTEM=0              stage 1(d): with no filesystem there is no stdin
#                             device, so nothing can reach window.prompt()
#   ALLOW_MEMORY_GROWTH       covers the 14 MiB of task stacks (1 MiB each)
#
# リンク時の設定と、それぞれの理由:
#   MODULARIZE + EXPORT_NAME  ページが工場関数を得る。電源の入れ直しが、ファームの
#                             静的変数の再初期化ではなく新しいモジュールで済む
#   ASYNCIFY                  fiber 型スケジューラが wasm 内でスタックを切り替えられる
#   EXPORTED_FUNCTIONS        module_spike.cpp が定義する C 入口
#   EXPORTED_RUNTIME_METHODS  型付きで呼ぶための cwrap、位置姿勢の受け皿の HEAPF64、
#                             モジュールのメモリの大きさを呼び出し側が読むための HEAPU8、
#                             その受け皿を呼び出し側が持つための _malloc/_free
#   EXIT_RUNTIME=0            main が無い。刻みとの間もモジュールは生き続ける
#   INVOKE_RUN=0              読み込み時に main を探さない
#   FILESYSTEM=0              段階 1(d): ファイルシステムが無ければ stdin の装置も
#                             無く、window.prompt() に届く経路が消える
#   ALLOW_MEMORY_GROWTH       タスクスタック 14 MiB（1 タスク 1 MiB）ぶんを賄う
echo "[module-spike] linking sfu_firmware.js / sfu_firmware.wasm"
em++ -O2 -sASYNCIFY \
     -sMODULARIZE=1 -sEXPORT_NAME=createSfuFirmware \
     -sEXPORTED_FUNCTIONS='["_sfu_spike_boot","_sfu_spike_step","_sfu_spike_step_until","_sfu_spike_altitude","_sfu_spike_state","_sfu_spike_pose","_sfu_spike_battery_volts","_malloc","_free"]' \
     -sEXPORTED_RUNTIME_METHODS='["cwrap","HEAPF64","HEAPU8"]' \
     -sEXIT_RUNTIME=0 -sINVOKE_RUN=0 -sFILESYSTEM=0 \
     -sALLOW_MEMORY_GROWTH=1 -sINITIAL_MEMORY=64MB -sSTACK_SIZE=1MB \
     "${OBJS[@]}" -o "$OUT_DIR/sfu_firmware.js"

echo "[module-spike] wrote $OUT_DIR/sfu_firmware.js and $OUT_DIR/sfu_firmware.wasm"
ls -l "$OUT_DIR/sfu_firmware.js" "$OUT_DIR/sfu_firmware.wasm"
