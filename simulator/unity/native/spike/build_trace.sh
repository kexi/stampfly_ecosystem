#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Kouhei Ito
#
# Part of StampFly Ecosystem (Unity/WebAssembly native core — scheduler check).
# https://github.com/M5Fly-kanazawa/stampfly_ecosystem
#
# Builds the scheduler trace checks against BOTH schedulers and compares the
# outputs, which is the pass criterion for the tick-driven entry points:
#
#   trace_dump  — scheduler owns the loop (run)       : 3 builds must agree
#   step_trace  — host owns the loop (run_until)      : 6 builds must agree,
#                 at every tick size, with the trace_dump result
#
# 両方のスケジューラに対してトレース検証をビルドし、出力を突き合わせる。これが
# 刻み実行用の入口の合格基準になる:
#
#   trace_dump  — ループはスケジューラが所有（run）       : 3 ビルドが一致すること
#   step_trace  — ループはホストが所有（run_until）      : どの刻み幅でも 6 ビルドが、
#                 trace_dump の結果とともに一致すること
#
# Run it from inside the Nix development shell (`nix develop`), which supplies
# emcc and node. The native compiler is Xcode's clang++.
# Nix 開発シェル（`nix develop`）の中で実行する。emcc と node はそこから来る。
# ネイティブのコンパイラは Xcode の clang++。
#
# Usage / 使い方:
#   build_trace.sh                 # build everything and compare
#                                  # 全てビルドして突き合わせる

set -euo pipefail

# Repository root, derived from this script's location so the script works from
# any working directory.
# このスクリプトの位置からリポジトリ直下を求める。どの作業ディレクトリから
# 呼ばれても動くようにするため。
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NATIVE_DIR="$(dirname -- "$SCRIPT_DIR")"
REPO_ROOT="$(cd -- "$NATIVE_DIR/../../.." && pwd)"

SILS="$REPO_ROOT/simulator/sils"
VN="$REPO_ROOT/firmware/vehicle"
BUILD_DIR="$NATIVE_DIR/build-trace"

# Emscripten writes its sysroot cache next to the compiler unless redirected,
# which fails on a read-only Nix store path.
# Emscripten は向き先を変えないとコンパイラの隣に sysroot キャッシュを書き、
# 読み取り専用の Nix ストアでは失敗する。
export EM_CACHE="$NATIVE_DIR/.cache"

NATIVE_CXX=/usr/bin/clang++

# Tick sizes swept by step_trace, in microseconds. 2500 is the Unity physics
# tick; 1000 and 7000 bracket it on both sides, including a size that does not
# divide the 500000 us scenario evenly.
# step_trace で走査する刻み幅（マイクロ秒）。2500 が Unity の物理刻みで、1000 と
# 7000 はその両側。7000 はシナリオの 500000 us を割り切らない刻みでもある。
TICKS=(1000 2500 7000)

mkdir -p "$BUILD_DIR"

# ---------------------------------------------------------------------------
# Source list. Mirrors the rtos_smoke target of simulator/sils/CMakeLists.txt
# (the same three unmodified firmware tasks and the same algorithm cores).
# The scheduler itself is substituted per build.
# ソース一覧。simulator/sils/CMakeLists.txt の rtos_smoke と同じ構成（同じ無改変の
# 3 タスクと同じアルゴリズムコア）。スケジューラだけをビルドごとに差し替える。
# ---------------------------------------------------------------------------
COMMON_SRCS=(
    "$SILS/rtos/esp_timer_shim.cpp"
    "$SILS/sim_hal/sim_bmi270.cpp"
    "$SILS/sim_hal/sim_motor_driver.cpp"
    "$VN/tasks/imu_task.cpp"
    "$VN/tasks/control_task.cpp"
    "$VN/tasks/state_task.cpp"
    "$VN/components/sf_state/state_manager.cpp"
    "$VN/components/sf_actuator/actuator.cpp"
    "$VN/components/sf_estimator_eskf/eskf_estimator.cpp"
    "$VN/components/sf_estimator_eskf/eskf_core.cpp"
    "$VN/components/sf_estimator_complementary/complementary_estimator.cpp"
    "$VN/components/sf_controller_pid/pid_controller.cpp"
    "$VN/components/sf_app_hooks/app_default.cpp"
    "$VN/components/sf_app_hooks/stock_hooks.cpp"
    "$VN/components/sf_takeoff_landing/takeoff_landing.cpp"
    "$VN/components/sf_calibration/calibration.cpp"
    "$VN/components/sf_failsafe/failsafe.cpp"
    "$VN/components/sf_core/params.cpp"
    "$SILS/compat/nvs_shim.cpp"
    "$SILS/compat/clock_shim.cpp"
    "$SILS/esp_idf_host/win_stdio_shim.cpp"
)

COMMON_INCS=(
    -I"$SILS/rtos"
    -I"$SILS/sim_hal"
    -I"$SILS/compat"
    -I"$SILS/esp_idf_host"
    -I"$VN/main"
    -I"$VN/tasks"
    -I"$VN/components/sf_math/include"
    -I"$VN/components/sf_core/include"
    -I"$VN/components/sf_estimator/include"
    -I"$VN/components/sf_estimator_eskf/include"
    -I"$VN/components/sf_estimator_complementary/include"
    -I"$VN/components/sf_controller/include"
    -I"$VN/components/sf_controller_pid/include"
    -I"$VN/components/sf_app_hooks/include"
    -I"$VN/components/sf_state/include"
    -I"$VN/components/sf_takeoff_landing/include"
    -I"$VN/components/sf_calibration/include"
    -I"$VN/components/sf_failsafe/include"
    -I"$VN/components/sf_actuator/include"
    -I"$VN/components/sf_hal_motor/include"
    -I"$REPO_ROOT/firmware/common/protocol/include"
)

# SILS_SCHEDULER_STEP_TRACE makes the thread version's run_until record the
# trace; without it an interactive host would grow that vector without bound,
# so it is off by default and only these checks turn it on.
# SILS_SCHEDULER_STEP_TRACE はスレッド版の run_until にトレースを記録させる。
# 無いと対話的なホストでこの列が際限なく伸びるため既定では無効で、
# この検証だけが有効にする。
COMMON_FLAGS=(-std=c++17 -O2 -Wno-deprecated-declarations -DSILS_SCHEDULER_STEP_TRACE=1)

# Build one program. / プログラムを 1 つビルドする。
#   $1 output name / 出力名
#   $2 scheduler: thread | fiber / スケジューラ
#   $3 target: native | wasm / ビルド先
#   $4 main source / 入口のソース
build_one() {
    local name="$1" sched="$2" target="$3" main="$4"
    local srcs=("$main" "${COMMON_SRCS[@]}")
    local incs=("${COMMON_INCS[@]}")
    local flags=("${COMMON_FLAGS[@]}")

    if [[ "$sched" == fiber ]]; then
        # rtos_fiber first on the include path makes "scheduler.hpp" resolve to
        # the fiber declarations without editing a single consumer.
        # include パスの先頭に rtos_fiber を置くと、利用側を 1 つも編集せずに
        # "scheduler.hpp" が fiber 版の宣言に解決される。
        incs=(-I"$NATIVE_DIR/rtos_fiber" "${incs[@]}")
        srcs+=("$SILS/rtos/scheduler_fiber.cpp")
    else
        srcs+=("$SILS/rtos/scheduler.cpp" "$SILS/rtos/scheduler_step.cpp")
    fi

    if [[ "$target" == wasm ]]; then
        incs=(-I"$NATIVE_DIR/compat_wasm" "${incs[@]}")
        srcs+=("$NATIVE_DIR/compat_wasm/wasm_stdio_shim.cpp")
        em++ "${flags[@]}" "${incs[@]}" "${srcs[@]}" \
             -sASYNCIFY -sALLOW_MEMORY_GROWTH=1 -sEXIT_RUNTIME=1 \
             -sINITIAL_MEMORY=64MB -sSTACK_SIZE=1MB \
             -o "$BUILD_DIR/$name.js"
    else
        # macOS hides the ucontext routines behind _XOPEN_SOURCE (deprecated in
        # POSIX 2008), which the fiber scheduler's native path needs.
        # macOS は ucontext 群を _XOPEN_SOURCE の後ろに隠しており（POSIX 2008 で
        # 非推奨のため）、fiber 版のネイティブ経路にはこれが要る。
        [[ "$sched" == fiber ]] && flags+=(-D_XOPEN_SOURCE=700)
        "$NATIVE_CXX" "${flags[@]}" "${incs[@]}" "${srcs[@]}" -o "$BUILD_DIR/$name"
    fi
}

# Run one program and print its output. Under node the development shell prints
# a greeting on stdout first, so the wasm runs drop the leading banner lines.
# プログラムを 1 つ実行して出力を出す。node では開発シェルの案内が先に標準出力へ
# 出るため、wasm の実行は先頭の案内行を落とす。
run_one() {
    local name="$1" target="$2"
    shift 2
    if [[ "$target" == wasm ]]; then
        node "$BUILD_DIR/$name.js" "$@" 2>/dev/null | grep -E '^[0-9]+ [0-9]+$|^# '
    else
        "$BUILD_DIR/$name" "$@" 2>/dev/null
    fi
}

echo "[trace] building 6 programs / 6 本のプログラムをビルドする"
build_one trace_thread_native thread native "$SCRIPT_DIR/trace_dump.cpp"
build_one trace_fiber_native  fiber  native "$SCRIPT_DIR/trace_dump.cpp"
build_one trace_fiber_wasm    fiber  wasm   "$SCRIPT_DIR/trace_dump.cpp"
build_one step_thread_native  thread native "$SCRIPT_DIR/step_trace.cpp"
build_one step_fiber_native   fiber  native "$SCRIPT_DIR/step_trace.cpp"
build_one step_fiber_wasm     fiber  wasm   "$SCRIPT_DIR/step_trace.cpp"

REFERENCE=""
FAILURES=0

# Record one run's sha256 and compare it with the first one seen.
# 実行 1 回の sha256 を記録し、最初のものと突き合わせる。
check() {
    local label="$1" digest="$2"
    if [[ -z "$REFERENCE" ]]; then
        REFERENCE="$digest"
    elif [[ "$digest" != "$REFERENCE" ]]; then
        FAILURES=$((FAILURES + 1))
        printf '  %-34s %s  MISMATCH / 不一致\n' "$label" "${digest:0:16}"
        return
    fi
    printf '  %-34s %s\n' "$label" "${digest:0:16}"
}

echo "[trace] run() — scheduler owns the loop / ループはスケジューラが所有"
for build in trace_thread_native:native trace_fiber_native:native trace_fiber_wasm:wasm; do
    name="${build%%:*}"
    target="${build##*:}"
    check "$name" "$(run_one "$name" "$target" | shasum -a 256 | cut -d' ' -f1)"
done

echo "[trace] run_until() — host owns the loop / ループはホストが所有"
for tick in "${TICKS[@]}"; do
    for build in step_thread_native:native step_fiber_native:native step_fiber_wasm:wasm; do
        name="${build%%:*}"
        target="${build##*:}"
        check "$name tick=${tick}us" \
              "$(run_one "$name" "$target" "$tick" | shasum -a 256 | cut -d' ' -f1)"
    done
done

if [[ "$FAILURES" -ne 0 ]]; then
    echo "[trace] FAILED: $FAILURES run(s) disagree / 不一致 $FAILURES 件"
    exit 1
fi

echo "[trace] OK — all 12 runs agree, sha256 ${REFERENCE:0:16}"
echo "[trace] 合格 — 12 回の実行が全て一致、sha256 ${REFERENCE:0:16}"
