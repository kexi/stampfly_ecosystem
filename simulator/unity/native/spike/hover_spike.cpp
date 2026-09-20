/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 1 spike).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file hover_spike.cpp
 * @brief Stage 1(a) speed check: the WHOLE unmodified firmware (app_main + all
 *        14 tasks) driven by run_until at the host's own pace, against the
 *        MuJoCo-free plant, measured for wall-clock cost per simulated second.
 *        段階1(a)の速度計測: 無改変のファーム全体（app_main ＋ 14 タスク）を
 *        run_until でホスト側の刻みに合わせて回し、MuJoCo を使わないプラントと
 *        閉ループにして、シミュレーション1秒あたりの実処理時間を測る。
 *
 * This is the shape the Unity build will have, minus Unity: the host owns the
 * loop and calls run_until(t) once per 2.5 ms physics tick, exactly as
 * SimLoop.Update() will call sfu_step through the .jslib. It deliberately does
 * NOT use emu_main.cpp — that entry point carries environment-variable
 * configuration, stdin plumbing and std::_Exit, none of which belongs in a
 * browser.
 *
 * これは Unity を除いた、Unity 版と同じ形: ホストがループを所有し、2.5ms の物理
 * 刻みごとに run_until(t) を1回呼ぶ。SimLoop.Update() が .jslib 経由で sfu_step を
 * 呼ぶのと同じ構造。emu_main.cpp は意図的に使わない — 同入口は環境変数による設定・
 * 標準入力の配管・std::_Exit を持ち、どれもブラウザには置けないため。
 *
 * The RC input is the take-off + ALTITUDE_HOLD sequence of
 * simulator/sils/scenarios/alt_flight.scn, injected through the real
 * sils::inject_rc seam (a genuine 14-byte ControlPacket through the ESP-NOW
 * hub), so the firmware reaches FLYING by the same path a scenario run does.
 *
 * 操縦入力は simulator/sils/scenarios/alt_flight.scn の離陸＋ALTITUDE_HOLD の列を、
 * 本物の sils::inject_rc シーム（ESP-NOW ハブを通る本物の 14 バイト ControlPacket）で
 * 注入する。よってファームはシナリオ実行と同じ経路で FLYING に達する。
 *
 * @design docs/plans/unity-simulator.md — 段階 1(a) 技術検証
 */

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

#include "scheduler.hpp"
#include "plant.hpp"
#include "virtual_board.hpp"
#include "scenario_inject.hpp"
#include "topics.hpp"
#include "data_types.hpp"
#include "flight_state.hpp"

// The firmware's own entry point (unmodified): BSP init + create all 14 tasks.
// ファーム自身の入口（無改変）: BSP 初期化＋14 タスク生成。
extern "C" void app_main(void);

using sils::rtos::Scheduler;

namespace {

// Host tick: one physics step. Matches the 2.5 ms (400 Hz) the Unity SimLoop
// will use, and the firmware's own control period.
// ホスト側の刻み: 物理1ステップ。Unity の SimLoop が使う 2.5ms（400Hz）と同じで、
// ファーム自身の制御周期とも一致する。
constexpr int64_t kTickUs = 2500;

constexpr float kGroundZ = 0.013f;   ///< body rest height [m] ENU (box half-height)
                                     ///< 静止時の機体高さ [m]（ENU、衝突箱の半分の厚み）

sils::Plant g_plant;
int64_t g_last_step_us = 0;

// Scheduler advance hook: step the physics by the elapsed virtual time. Same
// role as emu_main.cpp's on_advance, without the logging/pacing/flight-log side.
// スケジューラの advance フック: 経過した仮想時間ぶん物理を進める。emu_main.cpp の
// on_advance と同じ役目で、ログ・ペーシング・フライトログの側は持たない。
void on_advance(int64_t now_us)
{
    if (now_us > g_last_step_us) {
        const float dt = (float)(now_us - g_last_step_us) * 1e-6f;
        sils_board_step_plant(dt);
        g_last_step_us = now_us;
    }
}

// One entry of the scripted RC sequence: hold these stick values until t_end_us.
// 台本 RC 列の1項目: t_end_us までこのスティック値を保持する。
struct RcStep {
    int64_t  t_end_us;
    uint16_t throttle, roll, pitch, yaw;
    uint8_t  flags;
};

// Transcribed from simulator/sils/scenarios/alt_flight.scn (the take-off and
// ALTITUDE_HOLD portion). Raw 12-bit ADC, centre 2048.
// simulator/sils/scenarios/alt_flight.scn（離陸と ALTITUDE_HOLD の部分）からの
// 書き写し。raw 12bit ADC、中央 2048。
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
    // of ANY run length: the firmware's comm-loss failsafe lands the craft after
    // ~0.5 s without a packet, so the script must never simply stop sending.
    // D: 空中で ALTITUDE_HOLD へ、スロットル中央＝保持。実行長に関わらず最後まで
    // 保持する: ファームの通信途絶フェイルセーフは約 0.5 秒パケットが来ないと着陸を
    // 始めるため、台本が送信を止めてはならない。
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

// Flight-state number → name, for the progress lines below. The firmware
// publishes the state as a raw uint8_t, so the cast is the only place that
// knows it is an sf::FlightState.
// 飛行状態の番号 → 名前（下の経過表示用）。ファームは状態を生の uint8_t で
// publish するため、それが sf::FlightState であることを知っているのはこの
// キャスト1か所だけ。
const char* state_name(uint8_t s)
{
    switch (static_cast<sf::FlightState>(s)) {
        case sf::FlightState::INIT:          return "INIT";
        case sf::FlightState::IDLE_GROUND:   return "IDLE_GROUND";
        case sf::FlightState::ARMED_GROUND:  return "ARMED_GROUND";
        case sf::FlightState::TAKEOFF:       return "TAKEOFF";
        case sf::FlightState::FLYING:        return "FLYING";
        case sf::FlightState::LANDING:       return "LANDING";
        default:                             return "?";
    }
}

}  // namespace

int main(int argc, char** argv)
{
    // Simulated duration [s]; default 30 s as the plan's speed check specifies.
    // シミュレーションする長さ[秒]。既定は計画の速度計測に合わせて 30 秒。
    const double sim_seconds = (argc > 1) ? std::atof(argv[1]) : 30.0;
    const int64_t sim_us = (int64_t)(sim_seconds * 1e6);

    if (!g_plant.init(nullptr, sils::Plant::Config{})) {
        std::fprintf(stderr, "[spike] plant init failed\n");
        return 1;
    }
    g_plant.setStartHeight(kGroundZ);
    sils_board_attach_plant(&g_plant);

    Scheduler& scheduler = Scheduler::instance();
    scheduler.set_on_advance(on_advance);

    // Boot the vehicle already paired to the injector's transmitter, so the
    // scripted RC is accepted without a pairing handshake (as emu_main does).
    // 機体をインジェクタの送信機とペア済みで起動させ、台本 RC がペアリングの
    // ハンドシェイク無しで受理されるようにする（emu_main と同じ）。
    sils::seed_pairing_nvs();

    // The real firmware startup, unmodified: BSP init + all 14 tasks.
    // 実ファームの起動そのまま: BSP 初期化＋14 タスク。
    app_main();

    // ---- The host loop. This is what SimLoop.Update() will do. ----
    // ---- ホスト側のループ。SimLoop.Update() が行うことと同じ。 ----
    const auto wall_start = std::chrono::steady_clock::now();
    int64_t t_us = 0;
    double max_alt = 0.0, last_alt = 0.0;
    int64_t next_report_us = 0;

    while (t_us < sim_us) {
        t_us += kTickUs;
        pump_rc(t_us);
        scheduler.run_until(t_us);

        // Sample the truth state for the hover check.
        // ホバリング判定のため真値を取る。
        const sils::Plant::Truth truth = g_plant.truth();
        last_alt = -truth.pos_ned.z;
        if (last_alt > max_alt) max_alt = last_alt;

        if (t_us >= next_report_us) {
            next_report_us += 1000000;   // one line per simulated second / 1 行 = シミュレーション 1 秒
            sf::SystemMode mode = sf::system_mode.latest();
            sf::math::Vec3 e = truth.q_nb.to_euler();
            constexpr float kRad2Deg = 57.2957795f;
            std::printf("ALT t=%6.2f alt=%7.3f roll=%7.2f pitch=%7.2f yaw=%8.2f "
                        "state=%-12s armed=%d vbatt=%.2f\n",
                        t_us * 1e-6, last_alt,
                        e.x * kRad2Deg, e.y * kRad2Deg, e.z * kRad2Deg,
                        state_name(mode.state), mode.armed ? 1 : 0,
                        g_plant.batteryVoltage());
        }
    }

    const auto wall_end = std::chrono::steady_clock::now();
    const double wall_s =
        std::chrono::duration<double>(wall_end - wall_start).count();

    std::printf("\n[spike] simulated %.1f s of flight in %.3f s of wall clock\n",
                sim_seconds, wall_s);
    std::printf("[spike] REAL TIME PER SIMULATED SECOND = %.4f s  (target <= 0.30)\n",
                wall_s / sim_seconds);
    std::printf("[spike] max altitude %.3f m, final altitude %.3f m\n",
                max_alt, last_alt);

    // Hover check: airborne at the end, and not diverging.
    // ホバリング判定: 最後に空中にいて、発散していないこと。
    const bool hovering = (last_alt > 0.15 && last_alt < 3.0);
    std::printf("[spike] hover %s\n", hovering ? "OK" : "FAILED");
    return hovering ? 0 : 2;
}
