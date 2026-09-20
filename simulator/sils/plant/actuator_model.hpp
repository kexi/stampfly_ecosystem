/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (SILS host bench).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file actuator_model.hpp
 * @brief The MuJoCo-free half of the plant: motor ODE, thrust, reaction torque,
 *        battery, wind and turbulence. Header-only, so both the MuJoCo plant
 *        and the Unity plant can hold one.
 *        プラントのうち MuJoCo に触れない側: モータ ODE・推力・反トルク・電池・
 *        風・乱流。ヘッダのみなので、MuJoCo 版のプラントと Unity 版のプラントの
 *        どちらからも持てる。
 *
 * Every statement here is MOVED from plant.cpp — `substep()`'s non-MuJoCo part
 * plus `omegaDot` / `steadyStateOmega` / `dutyToThrust` / `ocvFromCharge` /
 * `updateBattery` / `hoverDuty` — with the order of the statements and the shape
 * of each expression left exactly as they were. The point is that the floating
 * -point result does not change by a single bit, so nothing here may be
 * reordered, no common subexpression may be hoisted, and no float may become a
 * double. The long rationale comments stay in plant.cpp (and in plant.hpp's
 * Config); what is repeated here is only what is needed to read the code.
 *
 * ここにある文はすべて plant.cpp から移したもの ― `substep()` の MuJoCo に
 * 触れない部分と `omegaDot`／`steadyStateOmega`／`dutyToThrust`／`ocvFromCharge`／
 * `updateBattery`／`hoverDuty` ― であり、文の順序も式の形もそのままにしてある。
 * 目的は浮動小数の結果を 1 ビットも変えないことなので、並べ替え・共通部分式の
 * くくり出し・float から double への変更はしない。理由を述べた長いコメントは
 * plant.cpp（と plant.hpp の Config）に残っており、ここには読むのに要る分だけを
 * 置く。
 *
 * NOT DONE HERE (deliberately): plant.cpp is left untouched, so for now it keeps
 * its own copy of these statements and this header is used only by the Unity
 * plant. Pointing plant.cpp at this header is a separate piece of work, because
 * it would change an existing build. 計画どおり plant.cpp 側をこのヘッダに寄せる
 * 作業はしない（既存ビルドを変えないため、後日の別作業）。
 *
 * @design docs/plans/unity-simulator.md §4 プラント, §6 主なファイル
 */

#pragma once

#include <cmath>
#include <cstddef>
#include <vector>

#include "plant.hpp"
#include "sf_math.hpp"

namespace sils {

/// The four per-motor thrusts and the body-frame reactions one substep produces.
/// 副刻み 1 回が生む、モータごとの推力 4 個と機体系の反作用。
struct ActuatorOutput {
    /// Per-motor thrust along body +Z (FLU up) [N], in MotorOutput order
    /// M1 FR / M2 RR / M3 RL / M4 FL. This is what the MuJoCo build writes into
    /// `d_->ctrl[i]`, i.e. AFTER the roll/pitch differential redistribution.
    /// 機体 +Z（FLU 上）向きのモータごとの推力 [N]。並びは MotorOutput と同じ
    /// M1 FR / M2 RR / M3 RL / M4 FL。MuJoCo 版が `d_->ctrl[i]` に書く値、つまり
    /// ロール/ピッチ差動の再配分を通した後の値。
    float thrust[4] = {0.0f, 0.0f, 0.0f, 0.0f};

    /// Reaction torque about body +Z (FLU) [N·m]: aerodynamic drag Cq·ω² plus the
    /// rotor's own angular-momentum reaction Jmp·ω̇, summed over the four motors,
    /// with the turbulence body torque added on top.
    /// 機体 +Z（FLU）まわりの反トルク [N·m]: 空力抗力 Cq·ω² とローター自身の角運動量
    /// 反作用 Jmp·ω̇ を 4 モータぶん足したもの。乱流の機体トルクもここに乗る。
    sf::math::Vec3 torque_flu = {0.0f, 0.0f, 0.0f};

    /// Wind force in world ENU [N] (the NED wind plus the turbulence force).
    /// 世界 ENU での風の力 [N]（NED の風 ＋ 乱流の力）。
    sf::math::Vec3 wind_enu = {0.0f, 0.0f, 0.0f};

    /// Mean of the four post-transport-delay commanded duties, which the sensor
    /// -noise model uses as its throttle.
    /// 輸送遅れを通した後の 4 モータ指令 duty の平均。センサノイズモデルがスロットル
    /// として使う。
    float mean_duty = 0.0f;
};

/// Motor + propeller + battery + wind, with no rigid body and no MuJoCo.
/// モータ＋プロペラ＋電池＋風。剛体も MuJoCo も含まない。
///
/// Holds exactly the state the MuJoCo Plant keeps for these models
/// (`motor_omega_`, `motor_target_`, the transport-delay ring buffers,
/// `turb_t_`, `v_batt_`, `batt_charge_mah_`), so the two run in lock-step when
/// given the same duty sequence.
/// MuJoCo 版の Plant がこれらのモデルのために持つ状態（`motor_omega_`・
/// `motor_target_`・輸送遅れのリングバッファ・`turb_t_`・`v_batt_`・
/// `batt_charge_mah_`）をそのまま持つので、同じ duty の列を与えれば両者は歩調を
/// 合わせて進む。
class ActuatorModel {
public:
    using Config = Plant::Config;

    /// Take the configuration and set up the battery and the delay buffers, the
    /// same way `Plant::init` does. `substep_s` is the fixed substep length [s]
    /// the delay is measured in (the MuJoCo model timestep, 0.25 ms).
    /// Config を受け取り、電池と遅れ用バッファを `Plant::init` と同じ手順で用意する。
    /// `substep_s` は遅れを数える固定副刻みの長さ [s]（MuJoCo のモデル timestep、
    /// 0.25 ms）。
    void init(const Config& cfg, double substep_s)
    {
        cfg_ = cfg;

        // Motor transport delay → whole substeps, rounded to nearest, then one
        // zero-filled ring buffer per motor. 0 → empty, and substep() takes the
        // explicit bypass branch. (plant.cpp:104-109)
        // モータ輸送遅れ → 副刻みの整数個（最近傍丸め）。モータごとにゼロ埋めの
        // リングバッファを確保する。0 なら空で、substep() は明示バイパス分岐を通る。
        delay_n_ = (cfg_.motor_delay_ms > 0.0f)
                       ? (int)std::lround((double)cfg_.motor_delay_ms * 1e-3 / substep_s)
                       : 0;
        delay_head_ = 0;
        for (int i = 0; i < 4; ++i) delay_buf_[i].assign((size_t)delay_n_, 0.0f);

        // Battery supply voltage. Model OFF → the fixed nominal forever; ON →
        // start from the initial state of charge. (plant.cpp:115-122)
        // 電池の電源電圧。モデル OFF なら固定公称のまま、ON なら初期 SoC から始める。
        if (cfg_.batt_model_enable) {
            batt_charge_mah_ = cfg_.batt_initial_frac * cfg_.batt_capacity_mah;
            v_batt_ = ocvFromCharge(batt_charge_mah_) - cfg_.avionics_current_a * cfg_.batt_r_int;
        } else {
            v_batt_ = cfg_.v_batt;
        }
    }

    /// Set the commanded per-motor duty target. Index 1:1, no reshuffle.
    /// 指令の各モータ duty 目標を設定する。添字は 1:1、並べ替えなし。
    void setDuty(const sf::MotorOutput& cmd)
    {
        for (int i = 0; i < 4; ++i) motor_target_[i] = cmd.duty[i];
    }

    /// Snap the motors to the steady state of the current target (warm start).
    /// モータを現在の目標の定常状態へ即時一致させる（暖機起動）。
    void primeMotors()
    {
        for (int i = 0; i < 4; ++i) {
            motor_omega_[i] = steadyStateOmega(motor_target_[i] * v_batt_);
            for (size_t k = 0; k < delay_buf_[i].size(); ++k) delay_buf_[i][k] = motor_target_[i];
        }
    }

    void setWind(const sf::math::Vec3& force_ned) { cfg_.wind_force_ned = force_ned; }

    void setHealth(int motor, float gain)
    {
        if (motor < 0 || motor > 3) return;
        cfg_.health[motor] = gain < 0.0f ? 0.0f : (gain > 1.0f ? 1.0f : gain);
    }

    const Config& config() const { return cfg_; }
    Config&       config()       { return cfg_; }

    /// Battery terminal voltage [V] as the INA3221 would measure it.
    /// INA3221 が測る電池端子電圧 [V]。
    float batteryVoltage() const { return v_batt_; }

    /// Integrated propeller speed of one motor [rad/s].
    /// 積分されたモータ 1 個のプロペラ回転速度 [rad/s]。
    float motorOmega(int motor) const
    {
        if (motor < 0 || motor > 3) return 0.0f;
        return motor_omega_[motor];
    }

    // -------------------------------------------------------------------------
    // substep — one fixed step of length h. Moved from plant.cpp::substep(),
    // minus the MuJoCo calls: transport delay → RK4 on the motor ODE → thrust →
    // reaction torque → differential redistribution → battery → turbulence and
    // wind. `ge_height_m` is the body height above the floor [m] the ground
    // -effect gain is evaluated at (the MuJoCo build reads `d_->qpos[2]`).
    //
    // substep — 長さ h の固定刻み 1 回。plant.cpp::substep() から MuJoCo の呼び出しを
    // 除いて移したもの: 輸送遅れ → モータ ODE の RK4 → 推力 → 反トルク → 差動の
    // 再配分 → 電池 → 乱流と風。`ge_height_m` は地面効果を評価する地表からの機体高さ
    // [m]（MuJoCo 版は `d_->qpos[2]` を読む）。
    // -------------------------------------------------------------------------
    ActuatorOutput substep(float h, float ge_height_m)
    {
        const float v_supply = v_batt_;          // this substep's supply voltage

        // Motor transport delay (duty path). delay_n_==0 → duty_cmd IS
        // motor_target_, with no buffer touched at all (exact bypass).
        // モータ輸送遅れ（duty 経路）。delay_n_==0 なら duty_cmd は motor_target_
        // そのもので、バッファには一切触れない（完全なバイパス）。
        float duty_cmd[4];
        if (delay_n_ == 0) {
            for (int i = 0; i < 4; ++i) duty_cmd[i] = motor_target_[i];
        } else {
            for (int i = 0; i < 4; ++i) {
                duty_cmd[i] = delay_buf_[i][delay_head_];       // read: delay_n_ substeps old
                delay_buf_[i][delay_head_] = motor_target_[i];  // write: this substep's target
            }
            delay_head_ = (delay_head_ + 1) % delay_n_;
        }

        // Ground-effect lift gain at the current body height, once per substep.
        // 現在の機体高さでの地面効果の揚力ゲイン。副刻みごとに 1 回。
        const float ge_mult = groundEffectMultiplier(ge_height_m);
        float thrust[4];
        float i_total = cfg_.avionics_current_a; // battery current [A] (avionics baseline)
        float tau_yaw_flu_z = 0.0f;              // accumulated per-motor below
        for (int i = 0; i < 4; ++i) {
            float v_motor = duty_cmd[i] * v_supply;
            if (v_motor < 0.0f) v_motor = 0.0f;
            if (v_motor > v_supply) v_motor = v_supply;  // V clamped to [0, v_supply]

            // Classical RK4, one substep of length h.
            // 古典 RK4、長さ h の副刻み 1 回。
            const float omega0 = motor_omega_[i];
            const float k1 = omegaDot(omega0, v_motor);
            const float k2 = omegaDot(omega0 + 0.5f * h * k1, v_motor);
            const float k3 = omegaDot(omega0 + 0.5f * h * k2, v_motor);
            const float k4 = omegaDot(omega0 + h * k3, v_motor);
            float omega1 = omega0 + (h / 6.0f) * (k1 + 2.0f * k2 + 2.0f * k3 + k4);
            if (omega1 < 0.0f) omega1 = 0.0f;             // ω clamped ≥ 0
            motor_omega_[i] = omega1;
            const float omega_dot = (omega1 - omega0) / h;  // this substep's realized ω̇

            thrust[i] = cfg_.thrust_efficiency * cfg_.Ct * omega1 * omega1 * cfg_.health[i] * ge_mult;

            // Motor electrical current (DC model: V = I·Rm + Km·ω). Clamp ≥ 0.
            // モータ電気電流（DC モデル: V = I·Rm + Km·ω）。0 以上にクランプ。
            const float i_motor = (v_motor - cfg_.motor_Km * omega1) / cfg_.motor_Rm;
            i_total += (i_motor > 0.0f) ? i_motor : 0.0f;

            // Per-motor reaction torque about body +Z (FLU): aerodynamic drag
            // Cq·ω² plus the rotor's own angular-momentum reaction Jmp·ω̇. CCW
            // props (M1 FR=0, M3 RL=2) react −Z_FLU; CW props react +Z_FLU.
            // 機体 +Z(FLU) まわりのモータごとの反トルク: 空力抗力 Cq·ω² と
            // ローター自身の角運動量反作用 Jmp·ω̇。CCW（M1=0, M3=2）は −Z_FLU、
            // CW は +Z_FLU。
            const float reaction = (cfg_.Cq * omega1 * omega1 + cfg_.Jmp * omega_dot)
                                    * cfg_.health[i] * ge_mult;
            tau_yaw_flu_z += (i == 0 || i == 2) ? -reaction : reaction;
        }

        // Roll/pitch DIFFERENTIAL torque authority. At the default (1.0) take an
        // explicit branch that writes thrust[i] straight through with NO
        // arithmetic: mean ± (thrust[i] − mean) is only MATHEMATICALLY equal to
        // thrust[i], and the last-bit difference compounds over a long run.
        // ロール/ピッチ差動トルクの効き。既定値（1.0）では算術を一切行わずに
        // thrust[i] をそのまま書く明示分岐を通る: mean ± (thrust[i] − mean) は
        // 「数学的に」thrust[i] と等しいだけで、最下位ビットの差が長時間の実行で
        // 効いてくるため。
        ActuatorOutput out;
        if (cfg_.torque_authority == 1.0f) {
            for (int i = 0; i < 4; ++i) out.thrust[i] = thrust[i];
        } else {
            const float mean_thrust = 0.25f * (thrust[0] + thrust[1] + thrust[2] + thrust[3]);
            for (int i = 0; i < 4; ++i) {
                const float ctrl_i = mean_thrust + cfg_.torque_authority * (thrust[i] - mean_thrust);
                out.thrust[i] = ctrl_i;
            }
        }

        // Update the battery supply (Coulomb count + IR sag) for the next substep.
        // 次の副刻みに向けて電池の電源を更新する（クーロンカウント＋IR による電圧降下）。
        updateBattery(i_total, h);

        // Turbulence body TORQUE (3–6 Hz), on top of the yaw reaction torque.
        // 乱流の機体トルク（3–6 Hz）。ヨー反トルクの上に乗る。
        sf::math::Vec3 tau_body = {0.0f, 0.0f, tau_yaw_flu_z};
        if (cfg_.turbulence_n > 0.0f) {
            const float t = turb_t_, k = 2.0f * 3.14159265f, Q = cfg_.turbulence_n * 0.03f;
            tau_body.x += Q * (std::sin(k * 3.7f * t) + 0.6f * std::sin(k * 5.3f * t + 1.2f));
            tau_body.y += Q * (0.8f * std::sin(k * 4.1f * t + 0.6f) + std::sin(k * 5.9f * t + 2.1f));
            tau_body.z += 0.5f * Q * std::sin(k * 2.9f * t + 0.4f);
        }
        out.torque_flu = tau_body;

        // Wind force NED → world ENU, plus the deterministic band-limited
        // turbulence (1–3 Hz horizontal sinusoid sum). 0 = off.
        // 風の力 NED → 世界 ENU ＋ 決定論的な帯域制限乱流（1–3 Hz の水平正弦和）。
        // 0 で無効。
        sf::math::Vec3 wind_ned = cfg_.wind_force_ned;
        if (cfg_.turbulence_n > 0.0f) {
            turb_t_ += h;
            const float t = turb_t_, k = 2.0f * 3.14159265f, A = cfg_.turbulence_n;
            wind_ned.x += A * (std::sin(k * 1.3f * t) + 0.7f * std::sin(k * 2.1f * t + 1.0f)
                                                       + 0.5f * std::sin(k * 3.3f * t + 2.0f));
            wind_ned.y += A * (0.8f * std::sin(k * 0.9f * t + 0.3f) + std::sin(k * 1.7f * t + 1.5f)
                                                                    + 0.6f * std::sin(k * 2.6f * t + 0.7f));
        }
        out.wind_enu = frames::ned_to_enu(wind_ned);

        // Mean of the four post-delay commanded duties, for the noise model's
        // throttle-dependent vibration.
        // 遅れを通した後の 4 モータ指令 duty の平均。ノイズモデルのスロットル依存
        // 振動のために渡す。
        out.mean_duty = 0.25f * (duty_cmd[0] + duty_cmd[1] + duty_cmd[2] + duty_cmd[3]);
        return out;
    }

    // -------------------------------------------------------------------------
    // The closed-form parts of the model, moved from plant.cpp:348-447.
    // モデルのうち閉じた式で書ける部分。plant.cpp:348-447 から移したもの。
    // -------------------------------------------------------------------------

    /// Motor + propeller ODE right-hand side dω/dt — the RK4 integrand.
    /// モータ＋プロペラの ODE の右辺 dω/dt — RK4 の被積分関数。
    float omegaDot(float omega, float v_motor) const
    {
        const float drag = (cfg_.Dm + cfg_.motor_Km * cfg_.motor_Km / cfg_.motor_Rm) * omega
                            + cfg_.Cq * omega * omega + cfg_.Qf;
        const float drive = cfg_.motor_Km * v_motor / cfg_.motor_Rm;
        return (drive - drag) / cfg_.Jmp;
    }

    /// Steady-state prop speed ω for a HELD voltage (positive root of dω/dt = 0).
    /// 保持電圧に対する定常回転 ω（dω/dt = 0 の正の根）。
    float steadyStateOmega(float v_motor) const
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

    /// Normalized duty [0,1] → STEADY-STATE thrust [N] via the ODE's equilibrium.
    /// 正規化 duty[0,1] → ODE の平衡での定常推力 [N]。
    float dutyToThrust(float duty) const
    {
        if (duty <= 0.0f) return 0.0f;
        const float omega = steadyStateOmega(duty * v_batt_);
        return cfg_.thrust_efficiency * cfg_.Ct * omega * omega;   // thrust T [N]
    }

    /// Remaining charge [mAh] → open-circuit voltage [V], 1S LiPo discharge curve.
    /// 残容量 [mAh] → 開回路電圧 [V]。1S LiPo の放電曲線。
    float ocvFromCharge(float charge_mah) const
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

    /// Coulomb-count the charge and recompute v_batt_ = OCV(SoC) − I·R_int.
    /// クーロンカウントで容量を減らし v_batt_ = OCV(SoC) − I·R_int を再計算する。
    void updateBattery(float i_total_a, float h)
    {
        if (!cfg_.batt_model_enable) return;            // constant v_batt_ (= cfg_.v_batt)

        // Coulomb counting: charge[mAh] -= I[A]·h[s] / 3.6  (A·s → mAh).
        // クーロンカウント: charge[mAh] -= I[A]·h[s]/3.6（A·s→mAh）。
        batt_charge_mah_ -= i_total_a * h / 3.6f;
        if (batt_charge_mah_ < 0.0f) batt_charge_mah_ = 0.0f;

        // Terminal voltage = open-circuit voltage minus the internal-resistance drop.
        // 端子電圧 = 開回路電圧 − 内部抵抗による電圧降下。
        v_batt_ = ocvFromCharge(batt_charge_mah_) - i_total_a * cfg_.batt_r_int;
    }

    /// Per-motor duty [0,1] that produces hover thrust (mg/4) at ODE steady state.
    /// ODE の定常状態でホバー推力（mg/4）を出す各モータ duty[0,1]。
    float hoverDuty() const
    {
        const float thrust = cfg_.mass * cfg_.g / 4.0f;       // per-motor hover thrust [N]
        const float omega = std::sqrt(thrust / (cfg_.Ct * cfg_.thrust_efficiency));  // ω = √(T/(Ct·η))
        const float V = cfg_.motor_Km * omega
                         + (cfg_.motor_Rm / cfg_.motor_Km)
                               * (cfg_.Cq * omega * omega + cfg_.Dm * omega + cfg_.Qf);
        return V / v_batt_;                                    // duty = V / v_batt (current supply)
    }

    /// Ground-effect lift multiplier at body height z [m] (ENU).
    /// 機体高さ z[m]（ENU）での地面効果の揚力倍率。
    float groundEffectMultiplier(float z_body_m) const
    {
        if (cfg_.ge_gain <= 0.0f) return 1.0f;
        return 1.0f + cfg_.ge_gain * std::exp(-z_body_m / cfg_.ge_height);
    }

private:
    Config cfg_;

    float motor_omega_[4]  = {0.0f, 0.0f, 0.0f, 0.0f}; ///< ODE angular velocity state [rad/s]
    float motor_target_[4] = {0.0f, 0.0f, 0.0f, 0.0f}; ///< commanded target duty

    std::vector<float> delay_buf_[4]; ///< past commanded duty targets, per motor
    int delay_head_ = 0;              ///< next write/read index into delay_buf_
    int delay_n_    = 0;              ///< delay length in substeps (0 = OFF/bypass)

    float turb_t_  = 0.0f;         ///< accumulated time for the turbulence force [s]
    float v_batt_ = 3.7f;          ///< current supply (terminal) voltage [V]
    float batt_charge_mah_ = 0.0f; ///< remaining battery charge [mAh] (model on)
};

}  // namespace sils
