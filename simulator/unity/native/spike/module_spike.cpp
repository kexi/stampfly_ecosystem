/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 1 spike).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file module_spike.cpp
 * @brief Stage 1(b)(d) check: the same unmodified firmware as hover_spike.cpp,
 *        but exposed as a handful of C entry points instead of a main(), so a
 *        separate wasm module can be created by createSfuFirmware() and driven
 *        one tick at a time from Unity WebGL through a .jslib.
 *        段階 1(b)(d) の検証: hover_spike.cpp と同じ無改変ファームウェアを、
 *        main ではなく数個の C 入口として出す。別の wasm モジュールを
 *        createSfuFirmware() で作り、Unity WebGL から .jslib 経由で 1 刻みずつ
 *        動かせるようにするため。
 *
 * The difference from hover_spike.cpp is only the shape, not the contents: the
 * host loop that lived in main() moves to the caller (C# / JavaScript), and the
 * RC script, the plant and the scheduler hook are unchanged transcriptions.
 *
 * hover_spike.cpp との違いは形だけで中身は同じ: main() にあったホスト側ループを
 * 呼び出し側（C# / JavaScript）へ移し、RC の台本・プラント・スケジューラのフックは
 * そのまま写している。
 *
 * The plan's real C ABI (sfu_step with a SfuStepIn/SfuStepOut pair) belongs to
 * stage 2's bridge/ directory. These sfu_spike_* names are deliberately separate
 * so the two never collide.
 *
 * 計画にある本物の C ABI（SfuStepIn/SfuStepOut を伴う sfu_step）は段階 2 の
 * bridge/ の担当である。ここの sfu_spike_* という名前は、両者がぶつからないよう
 * 意図的に分けてある。
 *
 * @design docs/plans/unity-simulator.md — 段階 1(b)(d) 技術検証
 */

#include <cstdint>
#include <cstdio>

#include "scheduler.hpp"
#include "plant.hpp"
#include "virtual_board.hpp"
#include "scenario_inject.hpp"
#include "topics.hpp"
#include "data_types.hpp"
#include "flight_state.hpp"

#if defined(__EMSCRIPTEN__)
#include <emscripten/emscripten.h>
#define SFU_SPIKE_EXPORT EMSCRIPTEN_KEEPALIVE
#else
#define SFU_SPIKE_EXPORT
#endif

// The firmware's own entry point (unmodified): BSP init + create all 14 tasks.
// ファーム自身の入口（無改変）: BSP 初期化＋14 タスク生成。
extern "C" void app_main(void);

using sils::rtos::Scheduler;

namespace {

// Host tick: one physics step. Matches the 2.5 ms (400 Hz) the Unity SimLoop
// will use, and the firmware's own control period.
// ホスト側の刻み: 物理 1 ステップ。Unity の SimLoop が使う 2.5ms（400Hz）と同じで、
// ファーム自身の制御周期とも一致する。
constexpr int64_t kTickUs = 2500;

// Body rest height [m] in ENU: the collision box's half-thickness.
// 静止時の機体高さ [m]（ENU）。衝突箱の半分の厚み。
constexpr float kGroundZ = 0.013f;

// Degrees per radian, for the attitude reported to the caller.
// 呼び出し側へ返す姿勢用の、ラジアン→度の換算。
constexpr float kRad2Deg = 57.2957795f;

sils::Plant g_plant;
int64_t g_last_step_us = 0;
int64_t g_now_us = 0;
bool g_booted = false;

// Scheduler advance hook: step the physics by the elapsed virtual time. Same
// role as emu_main.cpp's on_advance, without the logging or pacing side.
// スケジューラの advance フック: 経過した仮想時間ぶん物理を進める。emu_main.cpp の
// on_advance と同じ役目で、ログ・ペーシングの側は持たない。
void on_advance(int64_t now_us)
{
    if (now_us > g_last_step_us) {
        const float dt = (float)(now_us - g_last_step_us) * 1e-6f;
        sils_board_step_plant(dt);
        g_last_step_us = now_us;
    }
}

// One entry of the scripted RC sequence: hold these stick values until t_end_us.
// 台本 RC 列の 1 項目: t_end_us までこのスティック値を保持する。
struct RcStep {
    int64_t  t_end_us;
    uint16_t throttle, roll, pitch, yaw;
    uint8_t  flags;
};

// Transcribed from simulator/sils/scenarios/alt_flight.scn, the same sequence
// hover_spike.cpp uses. Raw 12-bit ADC, centre 2048.
// simulator/sils/scenarios/alt_flight.scn からの書き写しで、hover_spike.cpp と
// 同じ列。raw 12bit ADC、中央 2048。
constexpr RcStep kRcScript[] = {
    // A: disarmed 4 s — boot reaches IDLE_GROUND before the ARM edge.
    // A: 4 秒 disarmed — ARM エッジの前に起動が IDLE_GROUND に達する。
    { 4000000, 2048, 2048, 2048, 2048, 0 },
    // B: ARM rising edge (IDLE_GROUND → ARMED_GROUND), idle throttle 1 s.
    // B: ARM 立ち上がり（IDLE_GROUND → ARMED_GROUND）、アイドル 1 秒。
    { 5000000, 2048, 2048, 2048, 2048, sils::kFlagArm },
    // C: STABILIZE take-off — throttle up → TAKEOFF → FLYING, 1.3 s.
    // C: STABILIZE 離陸 — スロットルを上げ TAKEOFF → FLYING、1.3 秒。
    { 6300000, 3243, 2048, 2048, 2048, sils::kFlagArm },
    // D: ALTITUDE_HOLD while airborne, throttle centred = hold. Held to the end
    // of ANY run length, because the comm-loss failsafe lands the craft after
    // about 0.5 s without a packet.
    // D: 空中で ALTITUDE_HOLD へ、スロットル中央＝保持。実行長に関わらず最後まで
    // 保持する。通信途絶フェイルセーフが約 0.5 秒で着陸を始めるため。
    { INT64_MAX, 2048, 2048, 2048, 2048,
      (uint8_t)(sils::kFlagArm | sils::kFlagAltMode) },
};

// Feed the scripted RC at 50 Hz, the rate a real transmitter sends at.
// 台本 RC を 50Hz で送る。実際の送信機と同じ間隔。
constexpr int64_t kRcPeriodUs = 20000;
int64_t g_next_rc_us = 0;

void pump_rc(int64_t now_us)
{
    if (now_us < g_next_rc_us) return;
    g_next_rc_us += kRcPeriodUs;

    for (const RcStep& s : kRcScript) {
        if (now_us < s.t_end_us) {
            sils::inject_rc(s.throttle, s.roll, s.pitch, s.yaw, s.flags);
            return;
        }
    }
}

}  // namespace

extern "C" {

/**
 * Power on: initialise the plant and run the firmware's own app_main, which
 * performs BSP init and creates all 14 tasks. Returns 1 on success, 0 on
 * failure. Calling it twice in one module is refused, because the firmware's
 * tasks hold static state that cannot be reset without editing the firmware
 * (the plan's "one module equals one power-on" decision).
 *
 * 電源投入: プラントを初期化し、ファーム自身の app_main を走らせる。app_main は
 * BSP 初期化と 14 タスク生成を行う。成功 1、失敗 0。同一モジュールでの 2 回目は
 * 拒否する。ファームのタスクが無改変では初期状態に戻せない静的変数を持つため
 * （計画の「1 モジュール＝1 電源投入」の決定）。
 */
SFU_SPIKE_EXPORT int sfu_spike_boot(void)
{
    if (g_booted) {
        return 0;
    }

    if (!g_plant.init(nullptr, sils::Plant::Config{})) {
        std::fprintf(stderr, "[module_spike] plant init failed\n");
        return 0;
    }
    g_plant.setStartHeight(kGroundZ);
    sils_board_attach_plant(&g_plant);

    Scheduler::instance().set_on_advance(on_advance);

    // Boot the vehicle already paired to the injector's transmitter, so the
    // scripted RC is accepted without a pairing handshake (as emu_main does).
    // 機体をインジェクタの送信機とペア済みで起動させ、台本 RC がペアリングの
    // ハンドシェイク無しで受理されるようにする（emu_main と同じ）。
    sils::seed_pairing_nvs();

    app_main();

    g_booted = true;
    return 1;
}

/**
 * Advance one 2.5 ms tick: feed the scripted RC, then run the firmware until
 * the new virtual time. Returns the new virtual time in microseconds. This is
 * the call the .jslib makes synchronously, once per physics tick.
 *
 * 2.5ms を 1 刻み進める: 台本 RC を送り、新しい仮想時刻までファームを走らせる。
 * 戻り値は新しい仮想時刻 [マイクロ秒]。.jslib が物理 1 刻みにつき 1 回、
 * 同期で呼ぶのがこの関数。
 */
SFU_SPIKE_EXPORT double sfu_spike_step(void)
{
    if (!g_booted) {
        return 0.0;
    }

    g_now_us += kTickUs;
    pump_rc(g_now_us);
    Scheduler::instance().run_until(g_now_us);
    return (double)g_now_us;
}

/**
 * Advance to the given virtual time [microseconds], in whole ticks. The caller
 * passes a double because JavaScript numbers hold 2.5 ms ticks exactly far
 * beyond any run length this simulator sees. Returns the reached virtual time.
 *
 * 与えた仮想時刻 [マイクロ秒] まで、刻み単位で進める。JavaScript の数値は
 * このシミュレータが扱うどの実行長よりも遠くまで 2.5ms の刻みを誤差無く表せるため、
 * 引数は double で受ける。戻り値は到達した仮想時刻。
 */
SFU_SPIKE_EXPORT double sfu_spike_step_until(double target_us)
{
    if (!g_booted) {
        return 0.0;
    }

    while ((double)(g_now_us + kTickUs) <= target_us) {
        sfu_spike_step();
    }
    return (double)g_now_us;
}

/**
 * Altitude above the floor [m], from the plant's ground truth. NED z is down,
 * so the sign is flipped here.
 * 床からの高度 [m]。プラントの真値から取る。NED の z は下向きなのでここで
 * 符号を反転する。
 */
SFU_SPIKE_EXPORT double sfu_spike_altitude(void)
{
    return g_booted ? -(double)g_plant.truth().pos_ned.z : 0.0;
}

/**
 * The firmware's flight state as a number (sf::FlightState), for the caller to
 * show as text. Returns 0 (INIT) before boot.
 * ファームの飛行状態を番号（sf::FlightState）で返す。呼び出し側が文字に直して
 * 表示するため。起動前は 0（INIT）。
 */
SFU_SPIKE_EXPORT int sfu_spike_state(void)
{
    return g_booted ? (int)sf::system_mode.latest().state : 0;
}

/**
 * Fill six doubles the caller owns with the ground-truth pose, so one call
 * covers what a frame of rendering needs: east, north, up [m] and roll, pitch,
 * yaw [degrees]. The plant works in NED, so north/east swap and z flips sign.
 *
 * 呼び出し側が持つ double 6 個に真値の位置姿勢を書き込む。描画 1 フレームに
 * 要るものが 1 回の呼び出しで揃うようにするため: 東・北・上 [m] と
 * ロール・ピッチ・ヨー [度]。プラントは NED なので北と東が入れ替わり、z の符号が反転する。
 */
SFU_SPIKE_EXPORT void sfu_spike_pose(double* out_six)
{
    if (out_six == nullptr) {
        return;
    }
    if (!g_booted) {
        for (int index = 0; index < 6; index++) {
            out_six[index] = 0.0;
        }
        return;
    }

    const sils::Plant::Truth truth = g_plant.truth();
    const sf::math::Vec3 euler = truth.q_nb.to_euler();

    out_six[0] = truth.pos_ned.y;    // east  [m] / 東
    out_six[1] = truth.pos_ned.x;    // north [m] / 北
    out_six[2] = -truth.pos_ned.z;   // up    [m] / 上
    out_six[3] = euler.x * kRad2Deg; // roll  [deg] / ロール
    out_six[4] = euler.y * kRad2Deg; // pitch [deg] / ピッチ
    out_six[5] = euler.z * kRad2Deg; // yaw   [deg] / ヨー
}

/**
 * Battery voltage [V], so the caller can show that the plant's battery model
 * is running and not merely that the numbers move.
 * 電池電圧 [V]。数値が動いているだけでなくプラントの電池モデルが回っていることを
 * 呼び出し側が示せるようにするため。
 */
SFU_SPIKE_EXPORT double sfu_spike_battery_volts(void)
{
    return g_booted ? (double)g_plant.batteryVoltage() : 0.0;
}

}  // extern "C"
