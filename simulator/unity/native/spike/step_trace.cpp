/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 check).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file step_trace.cpp
 * @brief Same scenario and output as trace_dump.cpp, but driven by repeated
 *        run_until() calls instead of one run() — so the thread scheduler's
 *        new scheduler_step.cpp and the fiber scheduler's run_until() can be
 *        compared event by event.
 *        シナリオも出力も trace_dump.cpp と同じだが、run() 1 回ではなく
 *        run_until() の繰り返しで回す。スレッド版に新設した scheduler_step.cpp と
 *        fiber 版の run_until() を事象単位で突き合わせるため。
 *
 * This is the shape the Unity build has: the host owns the loop and asks the
 * firmware to advance by one physics tick at a time. trace_dump.cpp answers
 * "do the two schedulers agree when the scheduler owns the loop?"; this file
 * answers "do they still agree when the HOST owns it, at any tick size?".
 *
 * これは Unity 版と同じ形 — ホストがループを所有し、ファームウェアに物理 1 刻みずつ
 * 進むよう求める。trace_dump.cpp が「スケジューラがループを所有するとき両者は
 * 一致するか」に答えるのに対し、本ファイルは「ホストが所有するとき、どの刻み幅でも
 * 一致するか」に答える。
 *
 * The tick size comes from argv[1] in microseconds (default 2500 = 2.5 ms, the
 * Unity physics tick). The trace must not depend on it: the virtual clock is
 * driven by the tasks' own deadlines, and a tick only decides where the host is
 * allowed to interrupt. Running with 1000 / 2500 / 7000 and diffing the outputs
 * is what shows that.
 *
 * 刻み幅は argv[1]（マイクロ秒、既定 2500 = 2.5 ms、Unity の物理刻み）で与える。
 * トレースはこれに依存してはならない: 仮想時計を進めるのはタスク自身の期限であり、
 * 刻みは「ホストが割り込んでよい場所」を決めるだけだから。1000 / 2500 / 7000 で
 * 実行して出力を diff することがその確認になる。
 *
 * Built against BOTH schedulers, exactly as trace_dump.cpp is. The thread build
 * additionally needs -DSILS_SCHEDULER_STEP_TRACE=1, because scheduler_step.cpp
 * leaves the trace unrecorded by default (an interactive host would grow it
 * without bound).
 *
 * trace_dump.cpp と同じく、両方のスケジューラに対してビルドする。スレッド版の
 * ビルドには -DSILS_SCHEDULER_STEP_TRACE=1 も要る。scheduler_step.cpp は既定では
 * トレースを記録しないため（対話的なホストでは際限なく伸びてしまう）。
 *
 * @design docs/plans/unity-simulator.md — 段階 2 ネイティブコア
 */

#include <cstdint>
#include <cstdio>
#include <cstdlib>

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

// Scenario constants, identical to trace_dump.cpp so the two programs' traces
// are directly comparable.
// シナリオ定数は trace_dump.cpp と同一。両プログラムのトレースをそのまま
// 比較できるようにするため。
constexpr int64_t kSimDurationUs = 500000;   // 0.5 s of virtual time / 仮想時間 0.5 秒
constexpr int64_t kArmTimeUs     = 100000;   // inject FLYING at 100 ms / 100ms で FLYING を注入
constexpr int64_t kDefaultTickUs = 2500;     // Unity physics tick / Unity の物理刻み

// FNV-1a hash of the schedule trace — same expression as trace_dump.cpp's.
// スケジュールトレースの FNV-1a ハッシュ — 式は trace_dump.cpp と同じ。
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
// trace_dump.cpp).
// 台本の飛行シナリオ: kArmTimeUs で ARM ＋ ホバーセットポイント
// （trace_dump.cpp のまま）。
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

// Print the trace and the closed-loop outputs in trace_dump.cpp's exact format,
// so a diff against that program's output is meaningful.
// トレースと閉ループの出力を trace_dump.cpp と全く同じ書式で出す。そうすれば
// 同プログラムの出力との diff に意味が出る。
void report(Scheduler& scheduler)
{
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

    sf::StateEstimate state = sf::estimate_state.latest();
    sf::ControlOutput control = sf::control_output.latest();
    sf::MotorOutput motors = sf::actuator_motor.latest();
    printf("# quat_w=%.9f pos_z=%.9f thrust=%.9f motors=%.9f %.9f %.9f %.9f\n",
           state.attitude[0], state.position[2], control.thrust,
           motors.duty[0], motors.duty[1], motors.duty[2], motors.duty[3]);
}

}  // namespace

int main(int argc, char** argv)
{
    const int64_t tick_us = (argc > 1) ? std::atoll(argv[1]) : kDefaultTickUs;
    if (tick_us <= 0) {
        fprintf(stderr, "step_trace: tick must be positive / 刻みは正の値にする\n");
        return 1;
    }

    sf::params::init();
    sf::topics_init();

    Scheduler& scheduler = Scheduler::instance();
    scheduler.set_on_advance(scenario);

    // Creation order fixes the task ids, so it must match trace_dump.cpp.
    // 生成順がタスク id を決めるため、trace_dump.cpp と同じ順にする。
    TaskHandle_t h_state = nullptr, h_control = nullptr, h_imu = nullptr;
    xTaskCreatePinnedToCore(StateTask,   "StateTask",   config::STACK_STATE,
                            nullptr, config::PRIORITY_STATE,   &h_state,   1);
    xTaskCreatePinnedToCore(ControlTask, "ControlTask", config::STACK_CONTROL,
                            nullptr, config::PRIORITY_CONTROL, &h_control, 1);
    xTaskCreatePinnedToCore(ImuTask,     "ImuTask",     config::STACK_IMU,
                            nullptr, config::PRIORITY_IMU,     &h_imu,     1);

    // The host's loop: advance one tick at a time to the end of the scenario.
    // The last target is clamped to kSimDurationUs so a tick size that does not
    // divide the duration still stops on the same virtual time as run() would.
    // ホスト側のループ: シナリオの終わりまで 1 刻みずつ進める。最後の目標時刻は
    // kSimDurationUs で切り詰める。継続時間を割り切らない刻み幅でも、run() と
    // 同じ仮想時刻で止まるようにするため。
    for (int64_t target = tick_us; ; target += tick_us) {
        const int64_t clamped = (target < kSimDurationUs) ? target : kSimDurationUs;
        if (!scheduler.run_until(clamped)) {
            fprintf(stderr, "step_trace: a task failed to yield / タスクがトークンを返さなかった\n");
            return 1;
        }
        if (clamped >= kSimDurationUs) break;
    }

    report(scheduler);
    scheduler.shutdown();
    return 0;
}
