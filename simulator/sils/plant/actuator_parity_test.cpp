/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (SILS host bench).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file actuator_parity_test.cpp
 * @brief Checks that plant/actuator_model.hpp computes the same numbers as the
 *        MuJoCo Plant, bit for bit.
 *        plant/actuator_model.hpp が MuJoCo 版の Plant と同じ数値を 1 ビットの
 *        違いもなく出すことを確かめる。
 *
 * The statements in actuator_model.hpp were MOVED from plant.cpp, so the two
 * must agree exactly. This test drives both with the SAME sequence of per-motor
 * duties — take-off, hover, one motor's health dropped mid-flight, and a wind
 * step — and compares them every step.
 *
 * actuator_model.hpp の文は plant.cpp から移したものなので、両者は厳密に一致して
 * いなければならない。本試験は同じ各モータ duty の列 ― 離陸、ホバリング、途中で
 * 1 モータの健全度を落とす、風を入れる ― を両者に与え、毎ステップ突き合わせる。
 *
 * ## Why the battery voltage is the measure / なぜ電池電圧で測るのか
 *
 * The battery model is turned ON, so each step's terminal voltage is
 * OCV(SoC) − I·R_int, where the current I is summed from all four motors'
 * (V − Km·ω)/Rm. The voltage therefore depends on the whole history of all four
 * ω trajectories, and the next step's duty→voltage mapping depends on it in
 * turn. A single-bit difference anywhere in the ODE, the RK4, the clamps or the
 * Coulomb counting shows up here within a few steps and never washes out — which
 * is what makes it a good single indicator.
 *
 * 電池モデルを ON にするので、毎ステップの端子電圧は OCV(SoC) − I·R_int になる。
 * 電流 I は 4 モータの (V − Km·ω)/Rm の総和なので、電圧は 4 つの ω の軌跡すべての
 * 履歴に依存し、次のステップの duty→電圧の対応もそれに依存する。ODE・RK4・
 * クランプ・クーロンカウントのどこかに 1 ビットの違いがあれば、数ステップのうちに
 * ここに現れ、以後消えない。1 つの指標としてこれを選ぶ理由である。
 *
 * ## What is NOT compared here / ここで比べていないもの
 *
 * The rigid body. The MuJoCo Plant integrates one and ActuatorModel has none, so
 * the position, the attitude and the synthetic sensors are out of scope; the
 * ground effect is left at its default OFF for the same reason (its multiplier
 * would need the MuJoCo body's height, which the actuator model does not own).
 * 剛体。MuJoCo 版は剛体を積分し、ActuatorModel は剛体を持たないので、位置・姿勢・
 * 合成センサは対象外である。地面効果を既定の OFF のままにするのも同じ理由による
 * （倍率を出すには MuJoCo 側の機体の高さが要るが、アクチュエータのモデルはそれを
 * 持たない）。
 *
 * ## Running it / 実行のしかた
 *
 *   actuator_parity_test <path to simulator/sils/models/stampfly.xml>
 *
 * Exit status 0 on agreement, 1 on any mismatch.
 * 一致すれば終了状態 0、1 か所でも食い違えば 1。
 *
 * @design docs/plans/unity-simulator.md §7 検証方法 2, 段階 2 合格基準
 */

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "plant.hpp"
#include "actuator_model.hpp"

namespace {

int g_failures = 0;

void check(bool ok, const char* name)
{
    std::printf("  [%s] %s\n", ok ? "PASS" : "FAIL", name);
    if (!ok) ++g_failures;
}

/// Bit-for-bit equality of two floats. Not `==`: this also separates +0 from −0
/// and reports NaN as unequal to itself, both of which a transcription error
/// could produce. 2 つの float がビット単位で等しいか。`==` ではないのは、+0 と
/// −0 を区別し、NaN を自分自身と等しくないものとして報告するためで、どちらも
/// 写し間違いが生みうる値である。
bool bit_equal(float a, float b)
{
    uint32_t ua, ub;
    std::memcpy(&ua, &a, sizeof ua);
    std::memcpy(&ub, &b, sizeof ub);
    return ua == ub;
}

/// The control period the firmware commands motors at [s]. Both plants are
/// stepped by this, and each divides it into 250 µs substeps internally.
/// ファームがモータを指令する制御周期 [s]。両方のプラントをこの刻みで進め、
/// それぞれが内部で 250 µs の副刻みに割る。
constexpr float kControlPeriodS = 0.0025f;

/// The physics substep both plants integrate at [s] (stampfly.xml's timestep).
/// 両方のプラントが積分する物理の副刻み [s]（stampfly.xml の timestep）。
constexpr double kSubstepS = 0.00025;

/// How many control periods the scripted duty sequence lasts. 4 s at 400 Hz.
/// 台本の duty の列が続く制御周期の回数。400 Hz で 4 秒。
constexpr int kSteps = 1600;

/// Step index at which one motor's health is dropped, and at which the wind is
/// switched on. モータ 1 個の健全度を落とすステップ番号と、風を入れるステップ番号。
constexpr int kHealthDropStep = 600;   // 1.5 s
constexpr int kWindStep       = 1000;  // 2.5 s

/// The configuration both plants are given. The battery model is ON, which is
/// what makes the voltage a running check on the ODE; everything else is left at
/// its default so the comparison covers the path the scenarios actually use.
/// 両方のプラントに与える設定。電池モデルを ON にすることで、電圧が ODE の継続的な
/// 検査になる。それ以外は既定のままにして、シナリオが実際に通る経路を比べる。
sils::Plant::Config make_config()
{
    sils::Plant::Config cfg;
    cfg.batt_model_enable = true;
    return cfg;
}

/// The scripted per-motor duty at step `i`: spool up, hold near hover, then a
/// small asymmetric bias so the four motors do not stay identical (identical
/// motors would hide an index mix-up).
/// ステップ `i` での台本の各モータ duty。立ち上げ、ホバー付近で保持、そのあと
/// 4 モータが同じ値のままにならないよう小さく非対称に振る（同じ値のままだと
/// 添字の取り違えが隠れてしまう）。
sf::MotorOutput scripted_duty(int i, float hover_duty)
{
    const float ramp_end = 400.0f;                     // 1.0 s of spool-up
    const float ramp = (i < (int)ramp_end) ? (float)i / ramp_end : 1.0f;
    const float base = hover_duty * ramp;

    // A slow per-motor bias, each motor on its own phase, so roll, pitch and yaw
    // differentials are all exercised. モータごとに位相をずらした緩やかな偏差を
    // 与え、ロール・ピッチ・ヨーの差動をすべて動かす。
    constexpr float kBiasAmplitude = 0.02f;
    constexpr float kBiasRateHz    = 0.7f;
    const float t = (float)i * kControlPeriodS;
    const float w = 2.0f * 3.14159265f * kBiasRateHz * t;

    sf::MotorOutput cmd{};
    for (int m = 0; m < 4; ++m) {
        const float phase = (float)m * 1.5707963f;     // 90° apart
        float duty = base + kBiasAmplitude * std::sin(w + phase);
        if (duty < 0.0f) duty = 0.0f;
        if (duty > 1.0f) duty = 1.0f;
        cmd.duty[m] = duty;
    }
    return cmd;
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc < 2) {
        std::fprintf(stderr,
                     "usage: actuator_parity_test <stampfly.xml>\n"
                     "使い方: actuator_parity_test <stampfly.xml>\n");
        return 1;
    }

    std::printf("actuator_parity_test — actuator_model.hpp vs the MuJoCo Plant\n");

    const sils::Plant::Config cfg = make_config();

    sils::Plant plant;
    if (!plant.init(argv[1], cfg)) {
        std::fprintf(stderr, "[parity] plant init failed: %s\n", argv[1]);
        return 1;
    }
    // Start the body on the ground, level and at rest, so the MuJoCo side sees
    // the same situation a scenario starts from.
    // 機体を地上に水平・静止で置き、MuJoCo 側がシナリオと同じ状況から始まるようにする。
    constexpr float kGroundZ = 0.013f;      // body rest height [m] ENU
    plant.setStartHeight(kGroundZ);

    sils::ActuatorModel actuator;
    actuator.init(cfg, kSubstepS);

    // --- The closed-form queries, before anything has moved -------------------
    // --- 何も動かす前に、閉じた式で書ける問い合わせを比べる ---
    check(bit_equal(plant.hoverDuty(), actuator.hoverDuty()), "hoverDuty");

    bool duty_thrust_ok = true;
    for (int i = 0; i <= 20; ++i) {
        const float duty = (float)i * 0.05f;   // 0.00, 0.05, ... 1.00
        if (!bit_equal(plant.dutyToThrust(duty), actuator.dutyToThrust(duty))) {
            std::printf("    dutyToThrust(%.2f): plant %.9g vs actuator %.9g\n",
                        duty, plant.dutyToThrust(duty), actuator.dutyToThrust(duty));
            duty_thrust_ok = false;
        }
    }
    check(duty_thrust_ok, "dutyToThrust over duty 0.00..1.00");

    // --- The stepped comparison ----------------------------------------------
    // --- 刻みを進めながらの突き合わせ ---
    const float hover_duty = plant.hoverDuty();
    double actuator_accum = 0.0;   // the same accumulator discipline Plant::step uses
    int    voltage_mismatch_step = -1;
    float  voltage_plant = 0.0f, voltage_actuator = 0.0f;

    for (int i = 0; i < kSteps; ++i) {
        // Drop one motor's health mid-flight, then add a wind force — both are
        // commands the P7 disturbance scenarios issue.
        // 途中で 1 モータの健全度を落とし、続いて風を加える。どちらも P7 の外乱
        // シナリオが出す指令である。
        if (i == kHealthDropStep) {
            constexpr int   kDegradedMotor = 2;    // M3 RL
            constexpr float kDegradedGain  = 0.6f;
            plant.setHealth(kDegradedMotor, kDegradedGain);
            actuator.setHealth(kDegradedMotor, kDegradedGain);
        }
        if (i == kWindStep) {
            const sf::math::Vec3 wind_ned{0.02f, -0.01f, 0.005f};   // [N]
            plant.setWind(wind_ned);
            actuator.setWind(wind_ned);
        }

        const sf::MotorOutput cmd = scripted_duty(i, hover_duty);
        plant.setDuty(cmd);
        actuator.setDuty(cmd);

        plant.step(kControlPeriodS);

        // Run the actuator model over the same number of substeps, with the same
        // carried remainder. The ground-effect height is irrelevant while
        // ge_gain is 0 (the default), so the resting height is passed.
        // 同じ回数の副刻みを、同じ端数の繰り越しでアクチュエータのモデルにも回す。
        // ge_gain が 0（既定）のあいだ地面効果の高さは結果に効かないので、静止高さを渡す。
        actuator_accum += (double)kControlPeriodS;
        while (actuator_accum >= kSubstepS) {
            actuator.substep((float)kSubstepS, kGroundZ);
            actuator_accum -= kSubstepS;
        }

        if (voltage_mismatch_step < 0 &&
            !bit_equal(plant.batteryVoltage(), actuator.batteryVoltage())) {
            voltage_mismatch_step = i;
            voltage_plant    = plant.batteryVoltage();
            voltage_actuator = actuator.batteryVoltage();
        }
    }

    if (voltage_mismatch_step >= 0) {
        std::printf("    first mismatch at step %d (t=%.4f s): plant %.9g V vs actuator %.9g V\n",
                    voltage_mismatch_step,
                    (double)voltage_mismatch_step * kControlPeriodS,
                    voltage_plant, voltage_actuator);
    }
    check(voltage_mismatch_step < 0, "batteryVoltage bit-identical over every step");

    // The voltage must actually have moved, or the check above would pass on a
    // model that never ran. 電圧が実際に動いていなければ、何も動かないモデルでも
    // 上の検査は通ってしまう。
    check(plant.batteryVoltage() < cfg.batt_full_v,
          "the battery actually discharged (the run was not a no-op)");

    // dutyToThrust reads the CURRENT supply voltage, so re-checking it after the
    // run compares the two discharged batteries as well.
    // dutyToThrust は現在の電源電圧を読むので、実行後にもう一度比べると、放電した
    // 2 つの電池どうしの比較にもなる。
    check(bit_equal(plant.dutyToThrust(hover_duty), actuator.dutyToThrust(hover_duty)),
          "dutyToThrust after discharge");
    check(bit_equal(plant.hoverDuty(), actuator.hoverDuty()),
          "hoverDuty after discharge");

    std::printf("%s (%d failure(s))\n", g_failures == 0 ? "ALL PASS" : "FAILED", g_failures);
    return g_failures == 0 ? 0 : 1;
}
