/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 1 spike).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file trace_dump.cpp
 * @brief Stage 1(a) check: dump the FULL scheduler trace so the thread and
 *        fiber schedulers can be compared event by event.
 *        段階1(a)の検証: スケジューラのトレース全体を出力し、スレッド版と
 *        fiber 版を事象単位で突き合わせられるようにする。
 *
 * Same scenario as simulator/sils/smoke/rtos_smoke.cpp (the same three
 * unmodified firmware tasks, the same 0.5 s of virtual time, the same FLYING
 * injection at 100 ms) — but it prints every (t_us, task_id) pair instead of
 * only the first twelve, because "the traces agree" is the stage-1 gate and a
 * hash alone cannot say WHERE two runs diverge.
 *
 * シナリオは simulator/sils/smoke/rtos_smoke.cpp と同じ（同じ無改変の3タスク、
 * 同じ仮想時間 0.5 秒、同じ 100 ms での FLYING 注入）。ただし先頭12件ではなく
 * 全ての (t_us, task_id) を出力する。段階1の判定は「トレースが一致すること」であり、
 * ハッシュだけでは2つの実行がどこで食い違ったかを示せないため。
 *
 * Built twice — once against rtos/scheduler.cpp (threads, native) and once
 * against rtos/scheduler_fiber.cpp (fibers, wasm) — and the two outputs are
 * diffed. This file is scheduler-agnostic: it only uses the public API that
 * both headers declare identically.
 *
 * rtos/scheduler.cpp（スレッド・ネイティブ）と rtos/scheduler_fiber.cpp
 * （fiber・wasm）のそれぞれに対して1回ずつビルドし、2つの出力を diff する。
 * 本ファイルはスケジューラ非依存で、両ヘッダが同一に宣言する公開 API しか使わない。
 */

#include <cstdint>
#include <cstdio>

#include "scheduler.hpp"
#include "topics.hpp"
#include "params.hpp"
#include "data_types.hpp"
#include "flight_state.hpp"
#include "config.hpp"

// Firmware task functions (unmodified).
// 本体タスク関数（無改変）。
void ImuTask(void*);
void ControlTask(void*);
void StateTask(void*);

using sils::rtos::Scheduler;

namespace {

// Scenario constants, matching rtos_smoke so the traces are comparable.
// rtos_smoke と同じシナリオ定数（トレースを比較できるようにするため）。
constexpr int64_t kSimDurationUs = 500000;   // 0.5 s of virtual time / 仮想時間 0.5 秒
constexpr int64_t kArmTimeUs     = 100000;   // inject FLYING at 100 ms / 100ms で FLYING を注入

// FNV-1a hash of the schedule trace — same as rtos_smoke's, so the two
// programs' hashes are directly comparable.
// スケジュールトレースの FNV-1a ハッシュ — rtos_smoke と同じ式なので、
// 両プログラムのハッシュをそのまま比較できる。
uint64_t trace_hash(const std::vector<sils::rtos::TraceEvent>& trace)
{
    uint64_t hash = 1469598103934665603ULL;
    for (const auto& event : trace) {
        uint64_t mixed = static_cast<uint64_t>(event.time_us) * 31u + event.task_id;
        for (int byte = 0; byte < 8; ++byte) {
            hash ^= (mixed >> (byte * 8)) & 0xff;
            hash *= 1099511628211ULL;
        }
    }
    return hash;
}

// Scripted flight scenario: arm + hover setpoint at kArmTimeUs (verbatim from
// rtos_smoke.cpp, minus its progress print so the two outputs line up).
// 台本の飛行シナリオ: kArmTimeUs で ARM ＋ ホバーセットポイント
// （rtos_smoke.cpp のまま。出力を揃えるため進捗表示だけ省く）。
void scenario(int64_t now_us)
{
    static bool injected = false;
    if (injected || now_us < kArmTimeUs) return;
    injected = true;

    sf::SystemMode mode = {};
    mode.state = static_cast<uint8_t>(sf::FlightState::FLYING);
    mode.armed = true;
    mode.timestamp = static_cast<uint32_t>(now_us);
    sf::system_mode.publish(mode);

    sf::CommandSetpoint setpoint = {};
    setpoint.throttle = 0.5f;
    setpoint.timestamp = static_cast<uint32_t>(now_us);
    sf::command_setpoint.publish(setpoint);
}

}  // namespace

int main()
{
    sf::params::init();
    sf::topics_init();

    Scheduler& scheduler = Scheduler::instance();
    scheduler.set_on_advance(scenario);

    // Creation order fixes the task ids, so it must match rtos_smoke exactly.
    // 生成順がタスク id を決めるため、rtos_smoke と厳密に同じ順にする。
    TaskHandle_t h_state = nullptr, h_control = nullptr, h_imu = nullptr;
    xTaskCreatePinnedToCore(StateTask,   "StateTask",   config::STACK_STATE,
                            nullptr, config::PRIORITY_STATE,   &h_state,   1);
    xTaskCreatePinnedToCore(ControlTask, "ControlTask", config::STACK_CONTROL,
                            nullptr, config::PRIORITY_CONTROL, &h_control, 1);
    xTaskCreatePinnedToCore(ImuTask,     "ImuTask",     config::STACK_IMU,
                            nullptr, config::PRIORITY_IMU,     &h_imu,     1);

    scheduler.run(kSimDurationUs);

    // ---- Full trace, one event per line ----
    // ---- トレース全体を1行1事象で ----
    const auto& trace = scheduler.trace();
    for (const auto& event : trace) {
        printf("%lld %u\n", static_cast<long long>(event.time_us), event.task_id);
    }

    uint32_t count[3] = {0, 0, 0};
    for (const auto& event : trace) {
        if (event.task_id < 3) count[event.task_id]++;
    }
    printf("# events=%zu state=%u control=%u imu=%u now_us=%lld hash=0x%016llx\n",
           trace.size(), count[0], count[1], count[2],
           static_cast<long long>(scheduler.now_us()),
           static_cast<unsigned long long>(trace_hash(trace)));

    // Final pipeline outputs — prove the closed path produced the same values,
    // not merely the same schedule.
    // パイプライン最終出力 — 同じスケジュールというだけでなく、閉じた経路が
    // 同じ値を生んだことを示す。
    sf::StateEstimate state = sf::estimate_state.latest();
    sf::ControlOutput control = sf::control_output.latest();
    sf::MotorOutput motors = sf::actuator_motor.latest();
    printf("# quat_w=%.9f pos_z=%.9f thrust=%.9f motors=%.9f %.9f %.9f %.9f\n",
           state.attitude[0], state.position[2], control.thrust,
           motors.duty[0], motors.duty[1], motors.duty[2], motors.duty[3]);
    return 0;
}
