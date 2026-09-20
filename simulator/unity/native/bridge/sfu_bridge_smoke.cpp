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
 *   sfu_bridge_smoke [simulated seconds, default 30] [--log-jsonl <path>]
 *                    [--run-id <id>] [--preallocate <bytes>]
 *
 * @design docs/plans/unity-simulator.md §5 段階 2, §7 検証方法 2
 */

#include <chrono>
#include <cstdio>
#include <cstdlib>

#include <string>

#include "sfu_api.h"
#include "sfu_log_jsonl.hpp"
#include "sfu_rc_script.hpp"
#include "sfu_smoke_options.hpp"

namespace {

/// Host tick: 2.5 ms, the 400 Hz the Unity SimLoop will run its physics at and
/// the firmware's own control period.
/// ホストの刻み: 2.5 ms。Unity の SimLoop が物理を回す 400 Hz であり、ファーム自身の
/// 制御周期でもある。
constexpr uint32_t kTickUs = 2500;

/// Resting height of the body above the floor [m] — half the collision box's
/// thickness, as `simulator/sils/models/stampfly.xml` has it.
/// 機体が床に静止するときの高さ [m]。`simulator/sils/models/stampfly.xml` の
/// 衝突箱の半分の厚みである。
constexpr float kRestHeightM = 0.013f;

/// Hover verdict band [m]: off the ground at the end, and not diverging.
/// ホバリングの判定に使う帯 [m]: 最後に接地しておらず、発散していないこと。
constexpr double kHoverMinM = 0.15;
constexpr double kHoverMaxM = 3.0;

/// What the run is judged on, gathered as it goes.
/// 実行の判定に使うもの。進行しながら集める。
struct Verdict {
    double  max_altitude = 0.0;
    double  last_altitude = 0.0;
    int64_t ticks = 0;
    bool    clock_exact = true;   ///< every now_us was exactly ticks × kTickUs
};

/// One module is one power-on: a second boot must be refused. Checked before
/// the flight because it touches nothing — unlike a step, which moves the clock.
/// 1 モジュール＝1 回の電源投入であり、2 回目の起動は拒まれなければならない。
/// 飛行の前に確かめるのは、これが何にも触れないからである。刻みは時計を動かすので
/// そうはいかない。
bool check_second_boot_refused(const SfuConfig& config)
{
    if (sfu_boot(&config) == SFU_ERR_ALREADY_BOOTED) return true;
    std::fprintf(stderr, "[bridge_smoke] a second sfu_boot was not refused\n");
    return false;
}

/// The flight. Returns SFU_OK, or the code the bridge refused with.
/// 飛行。SFU_OK か、橋渡しが拒んだ値を返す。
int32_t fly(double sim_seconds, sfu::JsonlLog& log, Verdict& verdict, int64_t start_us)
{
    SfuStepIn  in{};
    SfuStepOut out{};
    in.struct_size  = sizeof(SfuStepIn);
    out.struct_size = sizeof(SfuStepOut);
    in.dt_us = kTickUs;
    out.now_us = start_us;

    int64_t next_report_us = start_us;
    const int64_t total_us = start_us + (int64_t)(sim_seconds * 1e6);

    while (out.now_us < total_us) {
        sfu::rc_script_at(out.now_us + (int64_t)kTickUs,
                          in.rc_throttle, in.rc_roll, in.rc_pitch, in.rc_yaw, in.rc_flags);

        const int32_t stepped = sfu_step(&in, &out);
        if (stepped != SFU_OK) return stepped;

        // N ticks of dt_us must land the clock on exactly N × dt_us. This is
        // the whole point of the integer dt_us: with a float the rounding would
        // have to happen inside the bridge and no caller could predict it.
        // dt_us の N 刻みは、時計をちょうど N × dt_us に置かなければならない。
        // 整数の dt_us にした理由そのものである。浮動小数なら丸めは橋渡しの中で
        // 起きるほかなく、どの呼び出し側もそれを予測できない。
        ++verdict.ticks;
        const int64_t expected_us = start_us + verdict.ticks * (int64_t)kTickUs;
        if (out.now_us != expected_us) verdict.clock_exact = false;

        // Unity's +Y is up, so the altitude is simply the Y component.
        // Unity は +Y が上なので、高度は Y 成分そのものである。
        verdict.last_altitude = out.truth_position[1];
        if (verdict.last_altitude > verdict.max_altitude) {
            verdict.max_altitude = verdict.last_altitude;
        }

        if (out.now_us >= next_report_us) {
            constexpr int64_t kReportPeriodUs = 1000000;   // one line per simulated second
            next_report_us += kReportPeriodUs;
            std::printf("STEP t=%6.2f alt=%7.3f state=%d mode=%d armed=%d "
                        "vbatt=%.2f duty=%.3f,%.3f,%.3f,%.3f\n",
                        (double)out.now_us * 1e-6, verdict.last_altitude,
                        (int)out.flight_state, (int)out.flight_mode, (int)out.armed,
                        out.battery_voltage,
                        out.motor_duty[0], out.motor_duty[1],
                        out.motor_duty[2], out.motor_duty[3]);
            log.drain_firmware();
        }
    }
    return SFU_OK;
}

/// What dt_us accepts and refuses. Run AFTER the flight, all of it: a refused
/// step touches nothing, but an accepted one moves the virtual clock, and
/// putting any of these before the flight would shift the scripted moments the
/// flight is judged on. Keeping the whole group in one place after the flight
/// is what makes the flight identical to one with no checks at all.
/// dt_us が何を受理し何を拒むか。飛行の**後**に、まとめて行う。拒まれる刻みは何にも
/// 触れないが、受理される刻みは仮想時計を動かす。どれかを飛行の前に置けば、飛行の
/// 判定に使う台本の時点がずれる。一式を飛行の後の 1 か所に置くことが、確認の無い
/// 飛行とまったく同じ飛行にする手立てである。
bool check_dt_us()
{
    SfuStepIn  in{};
    SfuStepOut out{};
    in.struct_size  = sizeof(SfuStepIn);
    out.struct_size = sizeof(SfuStepOut);

    struct DtCase { uint32_t dt_us; int32_t expected; const char* name; };
    const DtCase cases[] = {
        { 0,                  SFU_ERR_BAD_ARGUMENT, "dt_us = 0" },
        { SFU_DT_US_MAX + 1u, SFU_ERR_BAD_ARGUMENT, "dt_us above the cap" },
        { SFU_DT_US_MAX,      SFU_OK,               "dt_us at the cap" },
    };
    for (const DtCase& one : cases) {
        in.dt_us = one.dt_us;
        out.status = 0;
        const int32_t got = sfu_step(&in, &out);
        const bool as_promised = (got == one.expected && out.status == one.expected);
        if (!as_promised) {
            std::fprintf(stderr,
                         "[bridge_smoke] %s: expected %d, got return %d / status %d\n",
                         one.name, (int)one.expected, (int)got, (int)out.status);
            return false;
        }
    }
    return true;
}

/// After the flight: every entry point must refuse, and refuse the same way
/// twice, without reaching the fiber stacks sfu_shutdown has freed.
/// 飛行の後。どの入口も拒まなければならず、2 回目も同じように拒まなければ
/// ならない。sfu_shutdown が解放した fiber のスタックへ届かせずに、である。
bool check_after_shutdown()
{
    if (sfu_shutdown() != SFU_OK) {
        std::fprintf(stderr, "[bridge_smoke] the first sfu_shutdown failed\n");
        return false;
    }

    SfuStepIn  in{};
    SfuStepOut out{};
    in.struct_size  = sizeof(SfuStepIn);
    out.struct_size = sizeof(SfuStepOut);
    in.dt_us = kTickUs;
    out.status = 0;

    // Called one at a time, each checked before the next: a braced list of
    // calls would leave their order unspecified, and the order matters here
    // (the second sfu_shutdown must come after the others).
    // 1 つずつ呼び、次へ進む前に確かめる。呼び出しを波括弧の列に並べると順序が
    // 未規定になるが、ここでは順序が問題になる（2 回目の sfu_shutdown は他の後で
    // なければならない）。
    const auto expect_shut_down = [](int32_t got, const char* name) {
        if (got == SFU_ERR_SHUT_DOWN) return true;
        std::fprintf(stderr, "[bridge_smoke] %s after shutdown: expected %d, got %d\n",
                     name, (int)SFU_ERR_SHUT_DOWN, (int)got);
        return false;
    };
    if (!expect_shut_down(sfu_step(&in, &out), "sfu_step")) return false;
    if (!expect_shut_down(sfu_set_wind(0.0f, 0.0f, 0.0f), "sfu_set_wind")) return false;
    if (!expect_shut_down(sfu_set_motor_health(0, 1.0f), "sfu_set_motor_health")) return false;
    if (!expect_shut_down(sfu_set_imu_bias(0, 0, 0, 0, 0, 0), "sfu_set_imu_bias")) return false;
    if (!expect_shut_down(sfu_shutdown(), "a second sfu_shutdown")) return false;
    if (out.status != SFU_ERR_SHUT_DOWN) {
        std::fprintf(stderr, "[bridge_smoke] sfu_step's status after shutdown: %d\n",
                     (int)out.status);
        return false;
    }
    return true;
}

}  // namespace

int main(int argc, char** argv)
{
    const sfu::SmokeOptions options = sfu::parse_smoke_options(argc, argv);
    if (!options.ok) {
        std::fprintf(stderr,
                     "usage: sfu_bridge_smoke [seconds] [--log-jsonl <path>] "
                     "[--run-id <id>] [--preallocate <bytes>]\n");
        return 1;
    }

    if (sfu_abi_version() != SFU_ABI_VERSION) {
        std::fprintf(stderr, "[bridge_smoke] ABI mismatch: module %d, header %d\n",
                     sfu_abi_version(), SFU_ABI_VERSION);
        return 1;
    }

    // `--preallocate <bytes>` shifts the heap before the boot. The flight must
    // come out identical with it and without it; `just unity-native-test` runs
    // both and diffs them. See sfu_smoke_options.hpp for what this once broke.
    // `--preallocate <bytes>` は起動の前にヒープをずらす。付けても付けなくても
    // 飛行は同一でなければならない。`just unity-native-test` が両方を実行して
    // 突き合わせる。これが何を壊していたかは sfu_smoke_options.hpp を参照。
    sfu::shift_heap_before_boot(options.preallocate_bytes);

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

    // Only NOW may anything allocate. One run, one run_id, issued here and
    // carried by every line the run writes — the firmware's and the bridge's
    // alike. See sfu_smoke_options.hpp on why this waits for the boot.
    // 確保を行ってよいのはここからである。1 回の実行に 1 つの run_id を発行し、
    // この実行が書く全ての行が持つ ― ファームのものも橋渡しのものも同じように。
    // なぜ起動を待つのかは sfu_smoke_options.hpp を参照。
    const std::string run_id = (options.run_id != nullptr)
        ? std::string(options.run_id) : sfu::make_run_id();
    sfu::JsonlLog log(
        (options.log_jsonl_path != nullptr) ? std::string(options.log_jsonl_path)
                                            : std::string(), run_id);
    log.write_bridge("info", "bridge.boot", -1, "sfu_boot");

    if (!check_second_boot_refused(config)) {
        log.write_bridge("error", "bridge.error", -1, "a second sfu_boot was not refused");
        return 1;
    }
    // The refusals moved no clock, so the flight starts at zero — the same
    // flight, tick for tick, as before these checks existed.
    // 拒否の確認は時計を動かしていないので、飛行は 0 から始まる。この確認が無かった
    // ときと刻みごとに同じ飛行である。
    const int64_t start_us = 0;

    Verdict verdict;
    const auto wall_start = std::chrono::steady_clock::now();
    const int32_t flown = fly(options.sim_seconds, log, verdict, start_us);
    const double wall_seconds =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - wall_start).count();

    if (flown != SFU_OK) {
        log.write_bridge("error", "bridge.error", -1, "sfu_step failed");
        std::fprintf(stderr, "[bridge_smoke] sfu_step failed: %d\n", (int)flown);
        return 1;
    }

    // The speed goes to stderr, not stdout: two runs must print identical
    // stdout, and a wall-clock measurement never repeats exactly.
    // 速度は stdout ではなく stderr へ出す。2 回の実行の stdout は一致しなければ
    // ならず、実時間の測定値がそのまま繰り返されることはないからである。
    std::fprintf(stderr,
                 "[bridge_smoke] simulated %.1f s in %.3f s of wall clock — "
                 "REAL TIME PER SIMULATED SECOND = %.4f s (target <= 0.30)\n",
                 options.sim_seconds, wall_seconds, wall_seconds / options.sim_seconds);

    log.drain_firmware();

    // Drain whatever is left as text too, so a failure has its own log to read
    // even when no JSON Lines file was asked for.
    // 残りを文字列としても取り出して出す。JSON Lines のファイルを求められなかった
    // ときでも、失敗したときに読むログが残るようにするため。
    char text[4096];
    while (sfu_log_read(text, (int32_t)sizeof(text)) > 0) {
        std::fputs(text, stderr);
    }
    const int32_t dropped = sfu_log_dropped();
    if (dropped > 0) {
        std::fprintf(stderr, "[bridge_smoke] %d log record(s) dropped\n", (int)dropped);
    }

    std::printf("[bridge_smoke] max altitude %.3f m, final altitude %.3f m\n",
                verdict.max_altitude, verdict.last_altitude);
    std::printf("[bridge_smoke] clock exact over %lld tick(s): %s\n",
                (long long)verdict.ticks, verdict.clock_exact ? "OK" : "FAILED");
    const bool hovering = (verdict.last_altitude > kHoverMinM &&
                           verdict.last_altitude < kHoverMaxM);
    const bool passed = hovering && verdict.clock_exact;
    std::printf("[bridge_smoke] hover %s\n", passed ? "OK" : "FAILED");

    const bool dt_us_ok = check_dt_us();
    std::printf("[bridge_smoke] dt_us range checks %s\n", dt_us_ok ? "OK" : "FAILED");

    const bool refused_after_shutdown = check_after_shutdown();
    std::printf("[bridge_smoke] refusals after shutdown %s\n",
                refused_after_shutdown ? "OK" : "FAILED");

    log.write_bridge("info", "bridge.shutdown", -1, passed ? "hover OK" : "hover FAILED");
    if (log.is_open()) {
        std::fprintf(stderr, "[bridge_smoke] log %s (run_id %s)\n",
                     (options.log_jsonl_path != nullptr ? options.log_jsonl_path : ""), run_id.c_str());
    }

    return (passed && dt_us_ok && refused_after_shutdown) ? 0 : 2;
}
