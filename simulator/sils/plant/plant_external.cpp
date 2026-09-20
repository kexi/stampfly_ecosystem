/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (SILS host bench — MuJoCo-free plant).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file plant_external.cpp
 * @brief Plant implementation WITHOUT MuJoCo: the same motor/thrust/battery
 *        models, with the rigid body integrated in C++.
 *        MuJoCo を使わない Plant の実装: モータ/推力/電池のモデルは同じで、
 *        剛体の運動だけを C++ 内で積分する。
 *
 * Linked INSTEAD OF plant.cpp, with SILS_PLANT_EXTERNAL defined so plant.hpp
 * drops its four MuJoCo-typed declarations. Everything else in that header —
 * Config, the public API, the physical constants — is shared verbatim, so the
 * firmware and every device model (virtual_board.cpp, vl53_device.cpp, ...)
 * link against it unchanged.
 *
 * plant.cpp の「代わりに」リンクし、SILS_PLANT_EXTERNAL を定義して plant.hpp の
 * MuJoCo 型の宣言4か所を落とす。同ヘッダのそれ以外 — Config・公開 API・物理定数 —
 * はそのまま共有するので、ファームと各デバイスモデル（virtual_board.cpp、
 * vl53_device.cpp …）は無改変でリンクできる。
 *
 * STATUS — stage 1(a) verification build (docs/plans/unity-simulator.md). The
 * electromechanical half (omegaDot / RK4 / thrust / reaction torque / battery /
 * ground effect) is transcribed statement-by-statement from plant.cpp so the
 * numbers agree. The rigid-body half is a 6-DOF integrator, NOT MuJoCo: it
 * reproduces the same equations of motion for the free-flight case, but the
 * contact model is a simple floor at z=0 rather than MuJoCo's full contact
 * solver. Trajectories therefore agree in the air and diverge on impact. Stage
 * 2 replaces this with the reviewed implementation.
 *
 * 位置づけ — 段階1(a)の検証用ビルド（docs/plans/unity-simulator.md）。電気機械の側
 * （omegaDot／RK4／推力／反トルク／電池／地面効果）は plant.cpp から文単位で書き写し、
 * 数値が一致するようにしてある。剛体の側は MuJoCo ではなく 6 自由度積分で、自由飛行の
 * 運動方程式は同じだが、接触は MuJoCo の接触ソルバではなく z=0 の単純な床。よって
 * 空中では一致し、接地の瞬間から分かれる。段階 2 で査読済みの実装に置き換える。
 *
 * @design docs/plans/unity-simulator.md — 段階 1(a), 段階 2 ネイティブコア
 */

#define SILS_PLANT_EXTERNAL 1

#include "plant.hpp"

#include <cmath>
#include <cstdio>
#include <cstring>

namespace sils {

using sf::math::Vec3;
using sf::math::Quat;

namespace {

// Rigid-body constants, read from simulator/sils/models/stampfly.xml so the two
// plants describe the SAME vehicle. Stage 2 will read them from the physical
// -parameter SSOT (control/models/stampfly_physical.yaml) instead of here.
// 剛体の定数。2つのプラントが同じ機体を表すよう simulator/sils/models/stampfly.xml
// から取った値。段階 2 ではここではなく物理パラメータの SSOT
// （control/models/stampfly_physical.yaml）から読む。
constexpr float kMass      = 0.037f;      ///< body mass [kg]
constexpr float kIxx       = 9.16e-6f;    ///< diaginertia, body FLU [kg·m²]
constexpr float kIyy       = 13.3e-6f;
constexpr float kIzz       = 20.4e-6f;
constexpr float kGravity   = 9.81f;       ///< <option gravity="0 0 -9.81">
constexpr float kTimestep  = 0.00025f;    ///< <option timestep="0.00025"> = 4000 Hz

// Rotor site positions in body FLU [m] (<site name="rotorN">), in MotorOutput
// order M1 FR / M2 RR / M3 RL / M4 FL — the same 1:1 order plant.cpp uses.
// 機体 FLU でのロータ site 位置 [m]（<site name="rotorN">）。並びは MotorOutput と
// 同じ M1 FR / M2 RR / M3 RL / M4 FL で、plant.cpp と 1:1 で一致する。
constexpr float kRotorX[4] = { 0.023f, -0.023f, -0.023f,  0.023f };
constexpr float kRotorY[4] = {-0.023f, -0.023f,  0.023f,  0.023f };
constexpr float kRotorZ    = 0.005f;

// Floor contact. The body rests on a box half-height above z=0; a stiff
// spring-damper against penetration stands in for MuJoCo's contact solver,
// which is enough to hold the craft still on the ground and to catch a landing.
// 床の接触。機体は z=0 から箱の半分の高さだけ上で静止する。MuJoCo の接触ソルバの
// 代わりに、めり込みに対する硬いばね・ダンパを置く。地上で機体を静止させ、着地を
// 受け止めるにはこれで足りる。
constexpr float kRestZ        = 0.013f;   ///< body rest height [m] ENU
constexpr float kContactK     = 800.0f;   ///< contact stiffness [N/m]
constexpr float kContactC     = 1.2f;     ///< contact damping [N·s/m]
constexpr float kContactMuC   = 6.0f;     ///< tangential/angular drag on the ground

// Normalize a quaternion in place (the integrator's error otherwise accumulates).
// クォータニオンを正規化する（しないと積分誤差が溜まる）。
Quat normalized(const Quat& q)
{
    float n = std::sqrt(q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z);
    if (n < 1e-9f) return Quat{1.0f, 0.0f, 0.0f, 0.0f};
    return Quat{q.w / n, q.x / n, q.y / n, q.z / n};
}

}  // namespace

// State that the MuJoCo build keeps inside mjData. Held in a file-scope struct
// rather than as Plant members so plant.hpp needs no new fields — the header
// must stay byte-compatible with the MuJoCo build (see its SILS_PLANT_EXTERNAL
// comment). One Plant per process, which is exactly the "1 module = 1 power-on"
// rule the Unity plan sets out.
//
// MuJoCo 版が mjData の中に持っている状態。plant.hpp に新しいフィールドを足さずに
// 済むよう、Plant のメンバではなくファイルスコープの構造体に置く（ヘッダは MuJoCo 版
// とバイト互換のままにする必要がある。同ヘッダの SILS_PLANT_EXTERNAL のコメント参照）。
// プロセスあたり Plant は1つで、これは Unity 計画の「1 モジュール＝1 電源投入」の
// 規則そのもの。
namespace {
struct BodyState {
    Vec3  pos_enu{0.0f, 0.0f, kRestZ};   ///< world ENU position [m]
    Vec3  vel_enu{0.0f, 0.0f, 0.0f};     ///< world ENU linear velocity [m/s]
    Vec3  omega_flu{0.0f, 0.0f, 0.0f};   ///< body FLU angular rate [rad/s]
    Quat  q_flu_enu{1.0f, 0.0f, 0.0f, 0.0f};  ///< body FLU → world ENU
    Vec3  accel_flu{0.0f, 0.0f, kGravity};    ///< specific force, body FLU [m/s²]
    double time = 0.0;                   ///< physics clock [s] (mjData::time)
};
BodyState g_body;
}  // namespace

// -----------------------------------------------------------------------------
// init — no model file to load; just take the Config and reset the body.
// init — 読み込むモデルファイルは無い。Config を受け取り機体を初期化するだけ。
// -----------------------------------------------------------------------------
bool Plant::init(const char* /*model_path*/, const Config& cfg)
{
    cfg_ = cfg;
    g_body = BodyState{};

    v_batt_ = cfg_.v_batt;
    batt_charge_mah_ = cfg_.batt_capacity_mah;
    noise_.init(cfg_.noise);

    // Motor transport delay, in whole substeps — same rounding as plant.cpp.
    // モータ輸送遅れを substep 数に換算（plant.cpp と同じ丸め）。
    delay_n_ = (int)(cfg_.motor_delay_ms * 0.001f / kTimestep + 0.5f);
    if (delay_n_ > 0) {
        for (int i = 0; i < 4; ++i) delay_buf_[i].assign((size_t)delay_n_, 0.0f);
    }
    delay_head_ = 0;
    return true;
}

Plant::~Plant() = default;

// =============================================================================
// MuJoCo-free members of Plant, transcribed VERBATIM from plant.cpp so the two
// plants compute the same numbers. These live in plant.cpp's lines 133-178 and
// 348-445; the statements below are copies, not reimplementations. Stage 2
// moves this block into the shared plant/actuator_model.hpp the plan calls for,
// so there is one copy instead of two.
//
// Plant の MuJoCo に依らないメンバ。2つのプラントが同じ数値を出すよう plant.cpp から
// そのまま書き写す（plant.cpp の 133-178 行と 348-445 行）。以下は再実装ではなく写し。
// 段階 2 で、計画にある共有の plant/actuator_model.hpp へこのまとまりを移し、写しが2つ
// ある状態を解消する。
// =============================================================================

void Plant::setDuty(const sf::MotorOutput& cmd)
{
    // Index 1:1: duty[0..3] = M1FR/M2RR/M3RL/M4FL. No reshuffle.
    // 添字 1:1: duty[0..3] = M1FR/M2RR/M3RL/M4FL。並べ替えなし。
    for (int i = 0; i < 4; ++i) motor_target_[i] = cmd.duty[i];
}

void Plant::primeMotors()
{
    for (int i = 0; i < 4; ++i) {
        motor_omega_[i] = steadyStateOmega(motor_target_[i] * v_batt_);
        for (size_t k = 0; k < delay_buf_[i].size(); ++k) delay_buf_[i][k] = motor_target_[i];
    }
}

void Plant::setWind(const sf::math::Vec3& force_ned)
{
    cfg_.wind_force_ned = force_ned;
}

void Plant::setHealth(int motor, float gain)
{
    if (motor < 0 || motor > 3) return;
    cfg_.health[motor] = gain < 0.0f ? 0.0f : (gain > 1.0f ? 1.0f : gain);
}

void Plant::setImuBias(const sf::math::Vec3& accel_bias, const sf::math::Vec3& gyro_bias)
{
    cfg_.imu_bias_accel = accel_bias;
    cfg_.imu_bias_gyro  = gyro_bias;
}

// The motor+propeller electromechanical ODE's right-hand side — the RK4
// integrator in substep() calls this every substep.
// モータ+プロペラの電気機械 ODE の右辺 — substep() の RK4 積分器が毎回呼ぶ。
float Plant::omegaDot(float omega, float v_motor) const
{
    const float drag = (cfg_.Dm + cfg_.motor_Km * cfg_.motor_Km / cfg_.motor_Rm) * omega
                        + cfg_.Cq * omega * omega + cfg_.Qf;
    const float drive = cfg_.motor_Km * v_motor / cfg_.motor_Rm;
    return (drive - drag) / cfg_.Jmp;
}

float Plant::steadyStateOmega(float v_motor) const
{
    if (v_motor <= 0.0f) return 0.0f;
    const float a = cfg_.Cq;
    const float b = cfg_.Dm + cfg_.motor_Km * cfg_.motor_Km / cfg_.motor_Rm;
    const float c = cfg_.Qf - cfg_.motor_Km * v_motor / cfg_.motor_Rm;
    const float disc = b * b - 4.0f * a * c;
    if (disc <= 0.0f) return 0.0f;
    const float omega = (-b + std::sqrt(disc)) / (2.0f * a);
    return omega > 0.0f ? omega : 0.0f;
}

float Plant::dutyToThrust(float duty) const
{
    if (duty <= 0.0f) return 0.0f;
    const float omega = steadyStateOmega(duty * v_batt_);
    return cfg_.thrust_efficiency * cfg_.Ct * omega * omega;   // thrust T [N]
}

// Remaining charge [mAh] → open-circuit voltage [V], simplified 1S LiPo curve.
// 残容量 [mAh] → 開回路電圧 [V]（簡略 1S LiPo 放電曲線）。
float Plant::ocvFromCharge(float charge_mah) const
{
    float pct = charge_mah / cfg_.batt_capacity_mah;   // state of charge [0..1]
    if (pct < 0.0f) pct = 0.0f;
    if (pct > 1.0f) pct = 1.0f;

    float adj;
    if      (pct > 0.95f) adj = 0.9f + (pct - 0.9f) * 2.0f;  // steep near full
    else if (pct < 0.05f) adj = pct * 2.0f;                  // steep near empty
    else                  adj = pct;

    float v = cfg_.batt_empty_v + adj * (cfg_.batt_full_v - cfg_.batt_empty_v);
    if (v < cfg_.batt_empty_v) v = cfg_.batt_empty_v;
    if (v > cfg_.batt_full_v)  v = cfg_.batt_full_v;
    return v;
}

void Plant::updateBattery(float i_total_a, float h)
{
    if (!cfg_.batt_model_enable) return;            // constant v_batt_ (= cfg_.v_batt)

    // Coulomb counting: charge[mAh] -= I[A]·h[s] / 3.6  (A·s → mAh).
    // クーロンカウント: charge[mAh] -= I[A]·h[s]/3.6（A·s→mAh）。
    batt_charge_mah_ -= i_total_a * h / 3.6f;
    if (batt_charge_mah_ < 0.0f) batt_charge_mah_ = 0.0f;

    v_batt_ = ocvFromCharge(batt_charge_mah_) - i_total_a * cfg_.batt_r_int;
}

float Plant::hoverDuty() const
{
    const float thrust = cfg_.mass * cfg_.g / 4.0f;       // per-motor hover thrust [N]
    const float omega = std::sqrt(thrust / (cfg_.Ct * cfg_.thrust_efficiency));
    const float V = cfg_.motor_Km * omega
                     + (cfg_.motor_Rm / cfg_.motor_Km)
                           * (cfg_.Cq * omega * omega + cfg_.Dm * omega + cfg_.Qf);
    return V / v_batt_;                                    // duty = V / v_batt
}

// -----------------------------------------------------------------------------
// setStartHeight — place the body at rest, level, at ENU height z.
// setStartHeight — 機体を ENU 高さ z で水平・静止に置く。
// -----------------------------------------------------------------------------
void Plant::setStartHeight(float z)
{
    ground_rest_z_enu_ = z;
    g_body.pos_enu = Vec3{0.0f, 0.0f, z};
    g_body.vel_enu = Vec3{0.0f, 0.0f, 0.0f};
    g_body.omega_flu = Vec3{0.0f, 0.0f, 0.0f};
    g_body.q_flu_enu = Quat{1.0f, 0.0f, 0.0f, 0.0f};
    g_body.accel_flu = Vec3{0.0f, 0.0f, kGravity};   // at rest the IMU reads +g up in FLU
}

// -----------------------------------------------------------------------------
// step — advance by dt of virtual time in fixed substeps, remainder carried.
//        Same accumulator discipline as plant.cpp, so the physics clock tracks
//        the caller's virtual clock 1:1.
// step — 仮想時間 dt ぶんを固定 substep で進め、端数は繰り越す。plant.cpp と同じ
//        累積のしかたなので、物理時計は呼び出し側の仮想時計と 1:1 で一致する。
// -----------------------------------------------------------------------------
void Plant::step(float dt)
{
    last_dt_ = dt;
    const double h = kTimestep;
    step_accum_ += (double)dt;
    while (step_accum_ >= h) {
        substep((float)h);
        step_accum_ -= h;
    }
}

// -----------------------------------------------------------------------------
// substep — one fixed step of length h: motor ODE (RK4) → thrust → forces and
//           torques → 6-DOF integration → floor contact → noise.
//
// The electromechanical part is transcribed from plant.cpp::substep. What
// MuJoCo did implicitly — turning the four off-center rotor forces into a net
// force and a roll/pitch moment — is done explicitly here.
//
// substep — 長さ h の固定刻み1回: モータ ODE（RK4）→推力→力とトルク→6自由度積分
//           →床の接触→ノイズ。電気機械の部分は plant.cpp::substep からの書き写し。
//           MuJoCo が暗黙にやっていたこと（偏心した4つのロータ力から合力とロール/
//           ピッチのモーメントを作ること）を、ここでは明示的に計算する。
// -----------------------------------------------------------------------------
void Plant::substep(float h)
{
    const float v_supply = v_batt_;

    // Motor transport delay (duty path) — exact bypass when OFF, as in plant.cpp.
    // モータ輸送遅れ（duty 経路）— OFF のときは plant.cpp と同じく完全バイパス。
    float duty_cmd[4];
    if (delay_n_ == 0) {
        for (int i = 0; i < 4; ++i) duty_cmd[i] = motor_target_[i];
    } else {
        for (int i = 0; i < 4; ++i) {
            duty_cmd[i] = delay_buf_[i][delay_head_];
            delay_buf_[i][delay_head_] = motor_target_[i];
        }
        delay_head_ = (delay_head_ + 1) % delay_n_;
    }

    const float ge_mult = groundEffectMultiplier(g_body.pos_enu.z);

    float thrust[4];
    float i_total = cfg_.avionics_current_a;
    float tau_yaw_flu_z = 0.0f;
    for (int i = 0; i < 4; ++i) {
        float v_motor = duty_cmd[i] * v_supply;
        if (v_motor < 0.0f) v_motor = 0.0f;
        if (v_motor > v_supply) v_motor = v_supply;

        // Classical RK4, one substep — identical to plant.cpp.
        // 古典 RK4 を1 substep — plant.cpp と同一。
        const float omega0 = motor_omega_[i];
        const float k1 = omegaDot(omega0, v_motor);
        const float k2 = omegaDot(omega0 + 0.5f * h * k1, v_motor);
        const float k3 = omegaDot(omega0 + 0.5f * h * k2, v_motor);
        const float k4 = omegaDot(omega0 + h * k3, v_motor);
        float omega1 = omega0 + (h / 6.0f) * (k1 + 2.0f * k2 + 2.0f * k3 + k4);
        if (omega1 < 0.0f) omega1 = 0.0f;
        motor_omega_[i] = omega1;
        const float omega_dot = (omega1 - omega0) / h;

        thrust[i] = cfg_.thrust_efficiency * cfg_.Ct * omega1 * omega1
                    * cfg_.health[i] * ge_mult;

        const float i_motor = (v_motor - cfg_.motor_Km * omega1) / cfg_.motor_Rm;
        i_total += (i_motor > 0.0f) ? i_motor : 0.0f;

        // Reaction torque about body +Z(FLU): aerodynamic drag plus the rotor's
        // own angular-momentum reaction. CCW props (M1=0, M3=2) react −Z_FLU.
        // 機体 +Z(FLU) まわりの反トルク: 空力抗力＋ローター自身の角運動量反作用。
        // CCW（M1=0, M3=2）は −Z_FLU 向き。
        const float reaction = (cfg_.Cq * omega1 * omega1 + cfg_.Jmp * omega_dot)
                                * cfg_.health[i] * ge_mult;
        tau_yaw_flu_z += (i == 0 || i == 2) ? -reaction : reaction;
    }

    // Roll/pitch differential authority — same exact-bypass branch as plant.cpp
    // (the arithmetic is skipped at the default so no extra rounding creeps in).
    // ロール/ピッチ差動の効き — plant.cpp と同じ完全バイパス分岐（既定値では算術を
    // 行わず、余計な丸めが入らないようにする）。
    float ctrl[4];
    if (cfg_.torque_authority == 1.0f) {
        for (int i = 0; i < 4; ++i) ctrl[i] = thrust[i];
    } else {
        const float mean_thrust = 0.25f * (thrust[0] + thrust[1] + thrust[2] + thrust[3]);
        for (int i = 0; i < 4; ++i) {
            ctrl[i] = mean_thrust + cfg_.torque_authority * (thrust[i] - mean_thrust);
        }
    }

    updateBattery(i_total, h);

    // --- What MuJoCo derived from the <motor site=... gear="0 0 1 0 0 0"> set:
    // each rotor pushes along body +Z (FLU) at its own off-center site, giving a
    // net vertical force and a roll/pitch moment r × F.
    // --- MuJoCo が <motor site=... gear="0 0 1 0 0 0"> から導いていたもの:
    // 各ロータが自分の偏心した site で機体 +Z(FLU) 方向に押し、合力の鉛直成分と
    // ロール/ピッチのモーメント r × F を生む。
    Vec3 force_flu{0.0f, 0.0f, 0.0f};
    Vec3 torque_flu{0.0f, 0.0f, tau_yaw_flu_z};
    for (int i = 0; i < 4; ++i) {
        force_flu.z += ctrl[i];
        // r × F with F = (0,0,ctrl[i]) → (ry·Fz, −rx·Fz, 0).
        // r × F（F = (0,0,ctrl[i])）→ (ry·Fz, −rx·Fz, 0)。
        torque_flu.x +=  kRotorY[i] * ctrl[i];
        torque_flu.y += -kRotorX[i] * ctrl[i];
    }
    // The rotor height contributes no moment for a purely +Z force, so kRotorZ
    // is deliberately unused here rather than removed — it stays as the record
    // of the rotor geometry that plant.cpp and stampfly.xml share.
    // 純粋な +Z 方向の力に対してロータ高さはモーメントを生まないため、kRotorZ は
    // 削除せず意図的に未使用にしてある。plant.cpp・stampfly.xml と共有する
    // ロータ配置の記録として残す。
    (void)kRotorZ;

    // Turbulence body torque (3–6 Hz), as in plant.cpp.
    // 乱流ボディトルク（3–6Hz）。plant.cpp と同じ。
    if (cfg_.turbulence_n > 0.0f) {
        const float t = turb_t_, k = 2.0f * 3.14159265f, Q = cfg_.turbulence_n * 0.03f;
        torque_flu.x += Q * (std::sin(k * 3.7f * t) + 0.6f * std::sin(k * 5.3f * t + 1.2f));
        torque_flu.y += Q * (0.8f * std::sin(k * 4.1f * t + 0.6f) + std::sin(k * 5.9f * t + 2.1f));
        torque_flu.z += 0.5f * Q * std::sin(k * 2.9f * t + 0.4f);
    }

    // Wind force NED → world ENU, plus the same deterministic turbulence.
    // 風力 NED→世界 ENU ＋ 同じ決定論的乱流。
    Vec3 wind_ned = cfg_.wind_force_ned;
    if (cfg_.turbulence_n > 0.0f) {
        turb_t_ += h;
        const float t = turb_t_, k = 2.0f * 3.14159265f, A = cfg_.turbulence_n;
        wind_ned.x += A * (std::sin(k * 1.3f * t) + 0.7f * std::sin(k * 2.1f * t + 1.0f)
                                                   + 0.5f * std::sin(k * 3.3f * t + 2.0f));
        wind_ned.y += A * (0.8f * std::sin(k * 0.9f * t + 0.3f) + std::sin(k * 1.7f * t + 1.5f)
                                                                + 0.6f * std::sin(k * 2.6f * t + 0.7f));
    }
    const Vec3 wind_enu = frames::ned_to_enu(wind_ned);

    // --- Translation: world ENU. f = R·F_body + wind + gravity + contact.
    // --- 並進: 世界 ENU。f = R·F_body ＋ 風 ＋ 重力 ＋ 接触。
    const Quat q = g_body.q_flu_enu;
    Vec3 force_enu = q.rotate(force_flu);
    force_enu.x += wind_enu.x;
    force_enu.y += wind_enu.y;
    force_enu.z += wind_enu.z;

    // Floor: a stiff spring-damper while the body is below its rest height, plus
    // tangential drag so it does not slide or spin freely on the ground.
    // 床: 静止高さより下にある間は硬いばね・ダンパ。加えて接線方向の抗力を入れ、
    // 地上で滑ったり自由に回ったりしないようにする。
    const float penetration = kRestZ - g_body.pos_enu.z;
    bool on_ground = false;
    if (penetration > 0.0f) {
        on_ground = true;
        float fz = kContactK * penetration - kContactC * g_body.vel_enu.z;
        if (fz < 0.0f) fz = 0.0f;              // the floor only pushes
        force_enu.z += fz;
        force_enu.x -= kContactMuC * g_body.vel_enu.x;
        force_enu.y -= kContactMuC * g_body.vel_enu.y;
    }

    const Vec3 accel_enu{force_enu.x / kMass,
                         force_enu.y / kMass,
                         force_enu.z / kMass - kGravity};

    // Semi-implicit Euler: velocity first, then position with the NEW velocity.
    // Stable for a stiff contact at this substep length, unlike explicit Euler.
    // 準陰的オイラー: 先に速度、次に「更新後の」速度で位置。この substep 長では
    // 陽的オイラーと違い、硬い接触でも安定する。
    g_body.vel_enu.x += accel_enu.x * h;
    g_body.vel_enu.y += accel_enu.y * h;
    g_body.vel_enu.z += accel_enu.z * h;
    g_body.pos_enu.x += g_body.vel_enu.x * h;
    g_body.pos_enu.y += g_body.vel_enu.y * h;
    g_body.pos_enu.z += g_body.vel_enu.z * h;

    // --- Rotation: body FLU. I·ω̇ = τ − ω × (I·ω) (the gyroscopic term the
    // asymmetric inertia makes non-negligible).
    // --- 回転: 機体 FLU。I·ω̇ = τ − ω × (I·ω)（非対称な慣性のため無視できない
    // ジャイロ項）。
    const Vec3 w = g_body.omega_flu;
    const Vec3 Iw{kIxx * w.x, kIyy * w.y, kIzz * w.z};
    Vec3 tau = torque_flu;
    tau.x -= (w.y * Iw.z - w.z * Iw.y);
    tau.y -= (w.z * Iw.x - w.x * Iw.z);
    tau.z -= (w.x * Iw.y - w.y * Iw.x);
    if (on_ground) {
        // Angular drag on the ground, matching the tangential linear drag above.
        // 地上での角度方向の抗力。上の接線方向の抗力と対になるもの。
        tau.x -= kContactMuC * 1e-5f * w.x;
        tau.y -= kContactMuC * 1e-5f * w.y;
        tau.z -= kContactMuC * 1e-5f * w.z;
    }
    g_body.omega_flu.x += (tau.x / kIxx) * h;
    g_body.omega_flu.y += (tau.y / kIyy) * h;
    g_body.omega_flu.z += (tau.z / kIzz) * h;

    // Quaternion kinematics: q̇ = ½·q⊗(0,ω_body).
    // クォータニオンの運動学: q̇ = ½·q⊗(0,ω_body)。
    const Vec3 wb = g_body.omega_flu;
    const Quat qd{
        0.5f * (-q.x * wb.x - q.y * wb.y - q.z * wb.z),
        0.5f * ( q.w * wb.x + q.y * wb.z - q.z * wb.y),
        0.5f * ( q.w * wb.y - q.x * wb.z + q.z * wb.x),
        0.5f * ( q.w * wb.z + q.x * wb.y - q.y * wb.x)};
    g_body.q_flu_enu = normalized(Quat{q.w + qd.w * h, q.x + qd.x * h,
                                       q.y + qd.y * h, q.z + qd.z * h});

    // Specific force the accelerometer measures, body FLU: a − g, rotated into
    // the body. At rest this is +9.81 along body +Z (up), which frames::flu_to_frd
    // then turns into the −9.81 on FRD +Z the driver convention expects.
    // 加速度計が測る加速度計測定値（機体 FLU）: a − g を機体系へ回したもの。静止時は
    // 機体 +Z（上）向きに +9.81 で、frames::flu_to_frd がこれをドライバ規約の
    // FRD +Z 上の −9.81 に変換する。
    const Vec3 sf_enu{accel_enu.x, accel_enu.y, accel_enu.z + kGravity};
    const Quat qc = g_body.q_flu_enu;
    const Quat qinv{qc.w, -qc.x, -qc.y, -qc.z};
    g_body.accel_flu = qinv.rotate(sf_enu);

    g_body.time += h;

    const float mean_duty =
        0.25f * (duty_cmd[0] + duty_cmd[1] + duty_cmd[2] + duty_cmd[3]);
    noise_.setThrottle(mean_duty);
    noise_.advance(h);
}

// -----------------------------------------------------------------------------
// truth — body state → StampFly NED/FRD, through frames only (same as plant.cpp).
// truth — 機体の状態 → StampFly NED/FRD。frames だけを通す（plant.cpp と同じ）。
// -----------------------------------------------------------------------------
Plant::Truth Plant::truth() const
{
    Truth t;
    t.pos_ned   = frames::enu_to_ned(g_body.pos_enu);
    t.vel_ned   = frames::enu_to_ned(g_body.vel_enu);
    t.q_nb      = frames::mujoco_quat_to_qnb(g_body.q_flu_enu);
    t.omega_frd = frames::gyro_body_frd(g_body.omega_flu);
    return t;
}

// -----------------------------------------------------------------------------
// imu — synthetic IMU in body FRD, driver-normalized (−9.8 at rest).
// imu — 機体 FRD の合成 IMU。ドライバ正規化（静止で −9.8）。
// -----------------------------------------------------------------------------
sf::ImuData Plant::imu() const
{
    const Vec3 accel_frd = frames::flu_to_frd(g_body.accel_flu);
    const Vec3 gyro_frd  = frames::gyro_body_frd(g_body.omega_flu);

    sf::ImuData out{};
    out.accel[0] = accel_frd.x; out.accel[1] = accel_frd.y; out.accel[2] = accel_frd.z;
    out.gyro[0]  = gyro_frd.x;  out.gyro[1]  = gyro_frd.y;  out.gyro[2]  = gyro_frd.z;
    noise_.applyAccel(out.accel);
    noise_.applyGyro(out.gyro);
    out.accel[0] += cfg_.imu_bias_accel.x;
    out.accel[1] += cfg_.imu_bias_accel.y;
    out.accel[2] += cfg_.imu_bias_accel.z;
    out.gyro[0]  += cfg_.imu_bias_gyro.x;
    out.gyro[1]  += cfg_.imu_bias_gyro.y;
    out.gyro[2]  += cfg_.imu_bias_gyro.z;
    out.temperature = 25.0f;
    out.timestamp = (uint32_t)(g_body.time * 1e6);
    return out;
}

// -----------------------------------------------------------------------------
// tof — downward range, transcribed from plant.cpp (same range gate and noise
//       floor, so the firmware's ToF handling sees the same shape of signal).
// tof — 下向き距離。plant.cpp からの書き写し（範囲判定とノイズ下限も同じにして、
//       ファーム側の ToF の扱いが同じ形の信号を見るようにする）。
// -----------------------------------------------------------------------------
sf::TofData Plant::tof() const
{
    Truth t = truth();
    Vec3 e = t.q_nb.to_euler();
    float denom = std::cos(e.x) * std::cos(e.y);
    float dist = (std::fabs(denom) > 1e-3f) ? (-t.pos_ned.z / denom) : -t.pos_ned.z;

    sf::TofData out{};
    out.distance = dist;
    out.valid = (dist > 0.0f && dist < 4.0f);

    constexpr float kTofMinRange = 0.05f;
    if (dist > kTofMinRange) {
        noise_.applyTof(out.distance);
        if (out.distance < kTofMinRange) out.distance = kTofMinRange;
    }
    out.status = 0;
    out.timestamp = (uint32_t)(g_body.time * 1e6);
    return out;
}

// -----------------------------------------------------------------------------
// baro — pressure-altitude = −pos_z, with the ISA pressure for that altitude.
// baro — 気圧高度 = −pos_z と、その高度の ISA 気圧。
// -----------------------------------------------------------------------------
sf::BaroData Plant::baro() const
{
    Truth t = truth();
    const float altitude = -t.pos_ned.z;

    sf::BaroData out{};
    out.altitude = altitude;
    // ISA troposphere: p = p0·(1 − 2.25577e-5·h)^5.25588, p0 = 101325 Pa.
    // ISA 対流圏: p = p0·(1 − 2.25577e-5·h)^5.25588、p0 = 101325 Pa。
    out.pressure = 101325.0f * std::pow(1.0f - 2.25577e-5f * altitude, 5.25588f);
    out.temperature = 25.0f;
    out.timestamp = (uint32_t)(g_body.time * 1e6);
    return out;
}

// -----------------------------------------------------------------------------
// flow / mag — not exercised by the stage 1(a) takeoff-and-hover check, so they
// return a well-formed zero reading rather than a half-ported model. Stage 2
// ports plant.cpp:878-897 (flow) properly.
// flow / mag — 段階1(a) の離陸・ホバリング確認では使わないため、中途半端に移植した
// モデルではなく、形の整ったゼロ値を返す。flow は段階 2 で plant.cpp:878-897 から
// きちんと移植する。
// -----------------------------------------------------------------------------
sf::FlowData Plant::flow() const
{
    sf::FlowData out{};
    out.timestamp = (uint32_t)(g_body.time * 1e6);
    return out;
}

sf::MagData Plant::mag() const
{
    sf::MagData out{};
    out.timestamp = (uint32_t)(g_body.time * 1e6);
    return out;
}

// -----------------------------------------------------------------------------
// Handling maneuver — out of scope for stage 1(a) (it is the "pick the drone up
// and put it back" workflow, not takeoff/hover). Declared so the device models
// link; calling it is a no-op that leaves the body where it is.
// ハンドリング動作 — 段階1(a) の対象外（「機体を拾って置き直す」ワークフローであり
// 離陸・ホバリングではない）。デバイスモデルがリンクできるよう定義だけ置く。呼んでも
// 何もせず、機体はその場に留まる。
// -----------------------------------------------------------------------------
void Plant::startHandling(float, float, float, float, float, float)
{
    std::fprintf(stderr,
                 "[plant_external] startHandling is not implemented in the "
                 "MuJoCo-free plant (stage 1a) — ignored\n");
}

}  // namespace sils
