/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file sfu_rc_script.hpp
 * @brief The scripted stick sequence both smoke checks fly: arm, take off,
 *        hold altitude. Stands in for the pilot Unity will have.
 *        2 つの最小動作確認が飛ばす台本のスティック列。ARM・離陸・高度保持を行う。
 *        Unity が持つことになる操縦者の代役である。
 *
 * Transcribed from the take-off and ALTITUDE_HOLD portion of
 * `simulator/sils/scenarios/alt_flight.scn`, so the firmware reaches FLYING by
 * the same path a scenario run does. Values are raw 12-bit ADC, centre 2048,
 * exactly what a real transmitter puts on the wire.
 *
 * `simulator/sils/scenarios/alt_flight.scn` の離陸と ALTITUDE_HOLD の部分からの
 * 書き写し。ファームはシナリオ実行と同じ経路で FLYING に達する。値は raw 12bit
 * ADC・中央 2048 で、実際の送信機が電文に載せるものそのままである。
 *
 * Header-only because both `sfu_bridge_smoke` and `sfu_external_smoke` need it
 * and neither should own it.
 * ヘッダだけにしてあるのは、`sfu_bridge_smoke` と `sfu_external_smoke` の両方が
 * 要り、どちらの持ち物でもないためである。
 *
 * @design docs/plans/unity-simulator.md §5 段階 2
 */

#ifndef SFU_RC_SCRIPT_HPP
#define SFU_RC_SCRIPT_HPP

#include <cstdint>

namespace sfu {

/// Raw ADC value of a centred stick. 中央にあるスティックの raw ADC 値。
constexpr uint16_t kAdcCentre = 2048;

/// The ARM bit and the ALTITUDE_HOLD bit of the ControlPacket's flag byte, as
/// `simulator/sils/devices/scenario_inject.hpp` defines them.
/// ControlPacket のフラグの ARM ビットと ALTITUDE_HOLD ビット。定義は
/// `simulator/sils/devices/scenario_inject.hpp` にある。
constexpr uint8_t kFlagArm     = 0x01;
constexpr uint8_t kFlagAltMode = 0x08;

/// One entry: hold these stick values until t_end_us of virtual time.
/// 1 項目: 仮想時間 t_end_us までこのスティックの値を保持する。
struct RcStep {
    int64_t  t_end_us;
    uint16_t throttle, roll, pitch, yaw;
    uint8_t  flags;
};

constexpr RcStep kRcScript[] = {
    // A: disarmed for 4 s — the boot sequence reaches IDLE_GROUND before the
    // ARM edge arrives.
    // A: 4 秒 disarmed — ARM のエッジが来る前に、起動が IDLE_GROUND に達する。
    { 4000000, kAdcCentre, kAdcCentre, kAdcCentre, kAdcCentre, 0 },
    // B: the ARM rising edge (IDLE_GROUND → ARMED_GROUND), then 1 s at idle.
    // B: ARM の立ち上がり（IDLE_GROUND → ARMED_GROUND）、続いてアイドルで 1 秒。
    { 5000000, kAdcCentre, kAdcCentre, kAdcCentre, kAdcCentre, kFlagArm },
    // C: STABILIZE take-off — throttle up for 1.3 s, TAKEOFF → FLYING.
    // C: STABILIZE で離陸 — 1.3 秒スロットルを上げ、TAKEOFF → FLYING。
    { 6300000, 3243, kAdcCentre, kAdcCentre, kAdcCentre, kFlagArm },
    // D: ALTITUDE_HOLD airborne, throttle centred = hold. Held to the end of a
    // run of ANY length: the firmware's comm-loss failsafe lands the craft after
    // about 0.5 s without a packet, so the script must never simply stop.
    // D: 空中で ALTITUDE_HOLD、スロットル中央＝保持。実行の長さに関わらず最後まで
    // 保持する。ファームの通信途絶フェイルセーフは約 0.5 秒パケットが来ないと着陸を
    // 始めるので、台本が止まってはならない。
    { INT64_MAX, kAdcCentre, kAdcCentre, kAdcCentre, kAdcCentre,
      (uint8_t)(kFlagArm | kFlagAltMode) },
};

/// The stick values the script calls for at virtual time `now_us`.
/// 仮想時刻 `now_us` で台本が指示するスティックの値。
inline void rc_script_at(int64_t now_us,
                         uint16_t& throttle, uint16_t& roll,
                         uint16_t& pitch, uint16_t& yaw, uint8_t& flags)
{
    for (const RcStep& entry : kRcScript) {
        if (now_us >= entry.t_end_us) continue;
        throttle = entry.throttle;
        roll     = entry.roll;
        pitch    = entry.pitch;
        yaw      = entry.yaw;
        flags    = entry.flags;
        return;
    }
}

}  // namespace sfu

#endif  // SFU_RC_SCRIPT_HPP
