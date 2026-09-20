/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (SILS host bench — MuJoCo-free plant).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file plant_external_state.hpp
 * @brief The types the host (Unity) uses to inject the rigid-body state into
 *        the MuJoCo-free plant and to take the resulting forces back out.
 *        ホスト（Unity）が MuJoCo を使わないプラントへ剛体の状態を注入し、
 *        その結果の力を取り出すための型。
 *
 * Everything here is in StampFly's own conventions — world NED (x north, y east,
 * z down) and body FRD (x forward, y right, z down) — exactly like `Plant::Truth`
 * and the synthetic sensors. Unity's left-handed, Y-up conventions are converted
 * in `frames/frames_unity.hpp` before reaching this layer, so that the SILS rule
 * "coordinate transforms live in one place" still holds.
 *
 * ここにある型はすべて StampFly 自身の規約 ― 世界 NED（x 北・y 東・z 下）と機体
 * FRD（x 前・y 右・z 下）― で書いてある。`Plant::Truth` と合成センサに合わせたもの。
 * Unity の左手系・Y 上の規約は、この層に届く前に `frames/frames_unity.hpp` が
 * 変換する。「座標変換は 1 か所」という SILS の方針をそのまま守るため。
 *
 * @design docs/plans/unity-simulator.md §3 1 刻みの処理, §4 座標変換
 */

#pragma once

#include "sf_math.hpp"

namespace sils {

/// The rigid-body state the host owns and injects once per tick.
/// ホストが所有し、1 刻みにつき 1 回注入する剛体の状態。
///
/// `accel_frd` is the accelerometer READING, not the kinematic acceleration:
/// the host computes R⁻¹·((v_after − v_before)/dt − g) after its own physics
/// step, so contact forces are already in it and a body at rest reads −9.81 on
/// FRD +Z, as on the real vehicle.
/// `accel_frd` は運動としての加速度ではなく加速度計の測定値である。ホストは自分の
/// 物理を 1 刻み進めた後に R⁻¹·((v 後 − v 前)/dt − g) を計算するので、接触力は
/// すでに入っており、静止した機体は実機と同じく FRD +Z に −9.81 を読む。
struct ExternalState {
    sf::math::Vec3 pos_ned   = {0.0f, 0.0f, 0.0f};  ///< position NED [m]
    sf::math::Vec3 vel_ned   = {0.0f, 0.0f, 0.0f};  ///< velocity NED [m/s]
    sf::math::Vec3 omega_frd = {0.0f, 0.0f, 0.0f};  ///< body angular rate FRD [rad/s]
    sf::math::Quat q_nb      = {1.0f, 0.0f, 0.0f, 0.0f};  ///< attitude body→NED
    sf::math::Vec3 accel_frd = {0.0f, 0.0f, -9.81f};      ///< accelerometer reading FRD [m/s²]
};

/// The downward range the host measured with a raycast, plus the height the
/// ground effect is evaluated at.
/// ホストがレイキャストで測った下向きの距離と、地面効果を評価する高さ。
///
/// The two are separate on purpose: the ToF reading is what the sensor sees —
/// it can hit a table, a wall or nothing at all — while the ground effect needs
/// the height above whatever surface is actually below the rotors. Over a table
/// they agree; with the beam missing the floor entirely they do not.
/// 2 つを分けてあるのは意図的である。ToF の値はセンサが見たものであり、机にも壁にも
/// 当たれば何にも当たらないこともある。一方、地面効果に要るのはロータの真下にある面
/// からの高さである。机の上では両者は一致し、ビームが床を外れた場合は一致しない。
struct RangeInput {
    float downward_m    = 0.0f;   ///< downward ToF distance [m] along the sensor beam
    bool  downward_valid = false; ///< the beam hit a surface within range
    float ground_height_m = 0.0f; ///< height above the surface below the rotors [m]
};

/// The force and torque the actuators produced since the last time they were
/// taken, averaged over that interval.
/// 前回の取り出しから今回までにアクチュエータが出した力とトルクを、その区間で
/// 平均したもの。
///
/// The averaging is over the IMPULSE: each substep's force is accumulated
/// multiplied by the substep length, and the sum is divided by the elapsed time
/// on the way out. A host tick that covers ten 250 µs substeps therefore gets
/// one force that carries all ten, rather than only the last one.
/// 平均は力積で取る。各副刻みの力に副刻みの長さを掛けて足し込み、取り出すときに
/// 経過時間で割る。250 µs の副刻みを 10 回ぶん含むホストの 1 刻みは、最後の 1 回
/// だけでなく 10 回ぶんを持つ 1 つの力を受け取る。
struct Wrench {
    sf::math::Vec3 force_frd  = {0.0f, 0.0f, 0.0f};  ///< actuator force, body FRD [N]
    sf::math::Vec3 torque_frd = {0.0f, 0.0f, 0.0f};  ///< actuator torque, body FRD [N·m]
    sf::math::Vec3 wind_ned   = {0.0f, 0.0f, 0.0f};  ///< wind + turbulence force, world NED [N]
    float          dt_s       = 0.0f;                ///< the interval averaged over [s]
};

}  // namespace sils
