/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file sfu_bridge_smoke.cpp
 * @brief Flies the C ABI without Unity: boot, arm, take off and hold, with the
 *        plant's own rigid-body integrator carrying the vehicle.
 *        Unity 無しで C ABI を飛ばす。起動・ARM・離陸・保持を、プラント自身の
 *        剛体の積分器に機体を運ばせて行う。
 *
 * This is the stage 2 pass criterion "ARM → take-off → hover without Unity". It
 * touches nothing but `sfu_api.h`, so it exercises exactly what the C# side will
 * call, and a second run must print the same lines as the first — the firmware,
 * the plant and the scheduler are all deterministic, and any accidental
 * dependence on wall-clock time or uninitialised memory would show up as a
 * difference between the two.
 *
 * これは段階 2 の合格基準「Unity 無しで ARM → 離陸 → ホバリング」にあたる。触れる
 * のは `sfu_api.h` だけなので、C# 側が呼ぶものをそのまま動かすことになる。2 回目の
 * 実行は 1 回目と同じ行を出さなければならない。ファームもプラントもスケジューラも
 * 決定論的であり、実時間や未初期化の記憶域への意図しない依存があれば、2 つの実行の
 * 違いとして現れる。
 *
 * Usage / 使い方:
 *   sfu_bridge_smoke [simulated seconds, default 30]
 *
 * @design docs/plans/unity-simulator.md §5 段階 2, §7 検証方法 2
 */

#include <chrono>
#include <cstdio>
#include <cstdlib>

#include "sfu_api.h"
#include "sfu_rc_script.hpp"

namespace {

/// Host tick: 2.5 ms, the 400 Hz the Unity SimLoop will run its physics at and
/// the firmware's own control period.
/// ホストの刻み: 2.5 ms。Unity の SimLoop が物理を回す 400 Hz であり、ファーム自身の
/// 制御周期でもある。
constexpr float kTickSeconds = 0.0025f;

/// Resting height of the body above the floor [m] — half the collision box's
/// thickness, as `simulator/sils/models/stampfly.xml` has it.
/// 機体が床に静止するときの高さ [m]。`simulator/sils/models/stampfly.xml` の
/// 衝突箱の半分の厚みである。
constexpr float kRestHeightM = 0.013f;

/// Hover verdict band [m]: off the ground at the end, and not diverging.
/// ホバリングの判定に使う帯 [m]: 最後に接地しておらず、発散していないこと。
constexpr double kHoverMinM = 0.15;
constexpr double kHoverMaxM = 3.0;

}  // namespace

int main(int argc, char** argv)
{
    const double sim_seconds = (argc > 1) ? std::atof(argv[1]) : 30.0;

    if (sfu_abi_version() != SFU_ABI_VERSION) {
        std::fprintf(stderr, "[bridge_smoke] ABI mismatch: module %d, header %d\n",
                     sfu_abi_version(), SFU_ABI_VERSION);
        return 1;
    }

    SfuConfig config{};
    config.struct_size      = sizeof(SfuConfig);
    config.battery_model    = 1;
    config.boot_calibration = 1;
    // 0 = the plant integrates its own rigid body. No Unity here to do it.
    // 0 = プラントが内蔵の剛体を積分する。ここにはそれを行う Unity が無い。
    config.host_owns_body   = 0;
    config.start_height_m   = kRestHeightM;

    const int32_t booted = sfu_boot(&config);
    if (booted != SFU_OK) {
        std::fprintf(stderr, "[bridge_smoke] sfu_boot failed: %d\n", (int)booted);
        return 1;
    }

    // A second boot in the same module must be refused: one module is one
    // power-on. Checking it here keeps the rule honest.
    // 同じモジュールでの 2 回目の起動は拒否されなければならない。1 モジュール＝
    // 1 回の電源投入だからである。ここで確かめて、その決めごとを守らせる。
    if (sfu_boot(&config) != SFU_ERR_ALREADY_BOOTED) {
        std::fprintf(stderr, "[bridge_smoke] a second sfu_boot was not refused\n");
        return 1;
    }

    SfuStepIn  in{};
    SfuStepOut out{};
    in.struct_size  = sizeof(SfuStepIn);
    out.struct_size = sizeof(SfuStepOut);
    in.dt_s = kTickSeconds;

    double max_altitude = 0.0;
    double last_altitude = 0.0;
    int64_t next_report_us = 0;

    const int64_t total_us = (int64_t)(sim_seconds * 1e6);
    const auto wall_start = std::chrono::steady_clock::now();
    while (out.now_us < total_us) {
        sfu::rc_script_at(out.now_us + (int64_t)(kTickSeconds * 1e6),
                          in.rc_throttle, in.rc_roll, in.rc_pitch, in.rc_yaw, in.rc_flags);

        const int32_t stepped = sfu_step(&in, &out);
        if (stepped != SFU_OK) {
            std::fprintf(stderr, "[bridge_smoke] sfu_step failed: %d\n", (int)stepped);
            return 1;
        }

        // Unity's +Y is up, so the altitude is simply the Y component.
        // Unity は +Y が上なので、高度は Y 成分そのものである。
        last_altitude = out.truth_position[1];
        if (last_altitude > max_altitude) max_altitude = last_altitude;

        if (out.now_us >= next_report_us) {
            constexpr int64_t kReportPeriodUs = 1000000;   // one line per simulated second
            next_report_us += kReportPeriodUs;
            std::printf("STEP t=%6.2f alt=%7.3f state=%d mode=%d armed=%d "
                        "vbatt=%.2f duty=%.3f,%.3f,%.3f,%.3f\n",
                        (double)out.now_us * 1e-6, last_altitude,
                        (int)out.flight_state, (int)out.flight_mode, (int)out.armed,
                        out.battery_voltage,
                        out.motor_duty[0], out.motor_duty[1],
                        out.motor_duty[2], out.motor_duty[3]);
        }
    }

    // The speed goes to stderr, not stdout: two runs must print identical
    // stdout, and a wall-clock measurement never repeats exactly.
    // 速度は stdout ではなく stderr へ出す。2 回の実行の stdout は一致しなければ
    // ならず、実時間の測定値がそのまま繰り返されることはないからである。
    const double wall_seconds =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - wall_start).count();
    std::fprintf(stderr,
                 "[bridge_smoke] simulated %.1f s in %.3f s of wall clock — "
                 "REAL TIME PER SIMULATED SECOND = %.4f s (target <= 0.30)\n",
                 sim_seconds, wall_seconds, wall_seconds / sim_seconds);

    // Drain whatever the firmware logged, so a failure has its own log to read.
    // ファームが書いたログを取り出して出力する。失敗したときに読むログを残すため。
    char log[4096];
    while (sfu_log_read(log, (int32_t)sizeof(log)) > 0) {
        std::fputs(log, stderr);
    }
    const int32_t dropped = sfu_log_dropped();
    if (dropped > 0) std::fprintf(stderr, "[bridge_smoke] %d log line(s) dropped\n", (int)dropped);

    std::printf("[bridge_smoke] max altitude %.3f m, final altitude %.3f m\n",
                max_altitude, last_altitude);
    const bool hovering = (last_altitude > kHoverMinM && last_altitude < kHoverMaxM);
    std::printf("[bridge_smoke] hover %s\n", hovering ? "OK" : "FAILED");

    sfu_shutdown();
    return hovering ? 0 : 2;
}
