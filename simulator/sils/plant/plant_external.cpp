/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (SILS host bench — MuJoCo-free plant).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file plant_external.cpp
 * @brief Plant implementation WITHOUT MuJoCo. The motor, battery, wind and
 *        sensor models are the same ones; the rigid body comes either from the
 *        host (Unity) or from a small built-in integrator.
 *        MuJoCo を使わない Plant の実装。モータ・電池・風・センサのモデルは同じで、
 *        剛体はホスト（Unity）から受け取るか、内蔵の簡易な積分器で解く。
 *
 * Linked INSTEAD OF plant.cpp, with SILS_PLANT_EXTERNAL defined so plant.hpp
 * drops its four MuJoCo-typed declarations and adds the external-state ones.
 * Everything else in that header — Config, the public API, the physical
 * constants — is shared verbatim, so the firmware and every device model
 * (virtual_board.cpp, vl53_device.cpp, ...) link against it unchanged.
 *
 * plant.cpp の「代わりに」リンクし、SILS_PLANT_EXTERNAL を定義して plant.hpp の
 * MuJoCo 型の宣言 4 か所を落とし、外部供給用の宣言を足す。同ヘッダのそれ以外 ―
 * Config・公開 API・物理定数 ― はそのまま共有するので、ファームと各デバイスモデル
 * （virtual_board.cpp、vl53_device.cpp …）は無改変でリンクできる。
 *
 * ## Two ways of moving the rigid body / 剛体を動かす 2 つのやり方
 *
 * (A) Externally supplied — what the Unity build uses. The host steps PhysX and
 *     injects the resulting pose, velocity and accelerometer reading through
 *     setExternalState() each tick; step() then only advances the motors and
 *     accumulates the force they produce, which the host takes with takeWrench()
 *     and applies to its own Rigidbody. Calling setExternalState() once switches
 *     this Plant into that mode permanently.
 * (B) Built-in — what a run without Unity uses (the minimum-operation check and
 *     `just unity-native-spike`). A 6-DOF integrator at the same 250 µs substep,
 *     with a spring-damper floor standing in for MuJoCo's contact solver. The
 *     free-flight equations of motion are the same; the trajectories agree in
 *     the air and diverge on impact.
 *
 * (A) 外部供給 ― Unity 版が使う動作。ホストが PhysX を 1 刻み進め、その姿勢・速度・
 *     加速度計の測定値を setExternalState() で毎刻み注入する。step() はモータを
 *     進めて、そのモータが出した力を累積するだけで、ホストは takeWrench() でそれを
 *     取り出して自分の Rigidbody に加える。setExternalState() を 1 度呼ぶと、この
 *     Plant はこの動作のままになる。
 * (B) 内蔵 ― Unity 無しで回すとき（最小動作確認と `just unity-native-spike`）の動作。
 *     同じ 250 µs の副刻みで 6 自由度を積分し、MuJoCo の接触ソルバの代わりに床を
 *     ばね・ダンパで置く。自由飛行の運動方程式は同じで、空中では一致し、接地の
 *     瞬間から分かれる。
 *
 * The motor ODE, thrust, reaction torque, battery, ground effect, wind and
 * turbulence all live in plant/actuator_model.hpp, which holds the statements
 * moved from plant.cpp; this file no longer keeps a second copy of them.
 *
 * モータ ODE・推力・反トルク・電池・地面効果・風・乱流は plant/actuator_model.hpp に
 * あり、そこに plant.cpp から移した文が置いてある。このファイルはもう写しを持たない。
 *
 * @design docs/plans/unity-simulator.md §3 1 刻みの処理, §4 設計上の決定, 段階 2
 */

#define SILS_PLANT_EXTERNAL 1

#include "plant.hpp"
#include "actuator_model.hpp"

#include <cmath>
#include <cstdio>

namespace sils {

using sf::math::Vec3;
using sf::math::Quat;

namespace {

// Rigid-body constants, read from simulator/sils/models/stampfly.xml so the two
// plants describe the SAME vehicle. Only the built-in integrator (B) uses them;
// in externally supplied mode (A) the host owns the mass and the inertia.
// 剛体の定数。2 つのプラントが同じ機体を表すよう simulator/sils/models/stampfly.xml
// から取った値。使うのは内蔵の積分器（B）だけで、外部供給（A）では質量も慣性も
// ホストが持つ。
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

// Floor contact for the built-in integrator (B). The body rests on a box
// half-height above z=0; a stiff spring-damper against penetration stands in
// for MuJoCo's contact solver, which is enough to hold the craft still on the
// ground and to catch a landing.
// 内蔵の積分器（B）用の床の接触。機体は z=0 から箱の半分の高さだけ上で静止する。
// MuJoCo の接触ソルバの代わりに、めり込みに対する硬いばね・ダンパを置く。地上で
// 機体を静止させ、着地を受け止めるにはこれで足りる。
constexpr float kRestZ        = 0.013f;   ///< default body rest height [m] ENU
constexpr float kContactK     = 800.0f;   ///< contact stiffness [N/m]
constexpr float kContactC     = 1.2f;     ///< contact damping [N·s/m]
constexpr float kContactMuC   = 6.0f;     ///< tangential/angular drag on the ground
constexpr float kContactMuAng = 1e-5f;    ///< angular-drag scale [kg·m²] against kContactMuC

// Normalize a quaternion (the integrator's error otherwise accumulates).
// クォータニオンを正規化する（しないと積分誤差が溜まる）。
Quat normalized(const Quat& q)
{
    float n = std::sqrt(q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z);
    if (n < 1e-9f) return Quat{1.0f, 0.0f, 0.0f, 0.0f};
    return Quat{q.w / n, q.x / n, q.y / n, q.z / n};
}

}  // namespace

// State that the MuJoCo build keeps inside mjData, plus the actuator model and
// the externally supplied inputs. Held in a file-scope struct rather than as
// Plant members so plant.hpp keeps the same fields as the MuJoCo build. One
// Plant per process, which is exactly the "one module = one power-on" rule the
// Unity plan sets out.
//
// MuJoCo 版が mjData の中に持っている状態と、アクチュエータのモデルと、外から
// 与えられた入力。plant.hpp のフィールドを MuJoCo 版と同じに保つため、Plant の
// メンバではなくファイルスコープの構造体に置く。プロセスあたり Plant は 1 つで、
// これは Unity 計画の「1 モジュール＝1 電源投入」の規則そのもの。
namespace {

struct BodyState {
    Vec3  pos_enu{0.0f, 0.0f, kRestZ};   ///< world ENU position [m]
    Vec3  vel_enu{0.0f, 0.0f, 0.0f};     ///< world ENU linear velocity [m/s]
    Vec3  omega_flu{0.0f, 0.0f, 0.0f};   ///< body FLU angular rate [rad/s]
    Quat  q_flu_enu{1.0f, 0.0f, 0.0f, 0.0f};  ///< body FLU → world ENU
    Vec3  accel_flu{0.0f, 0.0f, kGravity};    ///< accelerometer reading, body FLU [m/s²]
    double time = 0.0;                   ///< physics clock [s] (mjData::time)
};

/// Everything this translation unit owns beyond what plant.hpp declares.
/// plant.hpp が宣言している以外に、この翻訳単位が持つもの全て。
struct ExternalPlant {
    ActuatorModel actuator;      ///< motors, battery, wind (the moved plant.cpp statements)
    BodyState     body;          ///< the rigid body of the built-in integrator (B)

    // --- Externally supplied mode (A) ---
    bool          external = false;  ///< set for good by the first setExternalState()
    ExternalState state{};           ///< the host's latest injection
    RangeInput    range{};           ///< the host's latest raycast

    // Accumulated actuator IMPULSE since the last takeWrench(), and the time it
    // covers. Divided by that time on the way out, so a host tick spanning ten
    // substeps gets one force carrying all ten.
    // 前回の takeWrench() からのアクチュエータの力積と、それが覆う時間。取り出す
    // ときにその時間で割るので、副刻み 10 回ぶんのホストの 1 刻みは 10 回ぶんを
    // 持つ 1 つの力を受け取る。
    Vec3   impulse_frd{0.0f, 0.0f, 0.0f};
    Vec3   ang_impulse_frd{0.0f, 0.0f, 0.0f};
    Vec3   wind_impulse_ned{0.0f, 0.0f, 0.0f};
    double wrench_dt_s = 0.0;
};

ExternalPlant g_ext;

}  // namespace

// -----------------------------------------------------------------------------
// init — no model file to load; take the Config, reset the body and set up the
// actuator model (battery, transport delay) the way plant.cpp's init does.
// init — 読み込むモデルファイルは無い。Config を受け取り、機体を初期化し、
// アクチュエータのモデル（電池・輸送遅れ）を plant.cpp の init と同じ手順で用意する。
// -----------------------------------------------------------------------------
bool Plant::init(const char* /*model_path*/, const Config& cfg)
{
    cfg_ = cfg;
    g_ext = ExternalPlant{};
    g_ext.actuator.init(cfg_, kTimestep);

    v_batt_ = g_ext.actuator.batteryVoltage();
    noise_.init(cfg_.noise);
    return true;
}

Plant::~Plant() = default;

// =============================================================================
// Commands. These only forward to the actuator model, which holds the motor
// state; cfg_ is kept in step with it so the Config a caller set stays readable.
// 指令。モータの状態を持つアクチュエータのモデルへ渡すだけ。呼び出し側が設定した
// Config がそのまま読めるよう、cfg_ も一緒に更新する。
// =============================================================================

void Plant::setDuty(const sf::MotorOutput& cmd)
{
    g_ext.actuator.setDuty(cmd);
}

void Plant::primeMotors()
{
    g_ext.actuator.primeMotors();
}

void Plant::setWind(const sf::math::Vec3& force_ned)
{
    cfg_.wind_force_ned = force_ned;
    g_ext.actuator.setWind(force_ned);
}

void Plant::setHealth(int motor, float gain)
{
    if (motor < 0 || motor > 3) return;
    cfg_.health[motor] = gain < 0.0f ? 0.0f : (gain > 1.0f ? 1.0f : gain);
    g_ext.actuator.setHealth(motor, gain);
}

void Plant::setImuBias(const sf::math::Vec3& accel_bias, const sf::math::Vec3& gyro_bias)
{
    cfg_.imu_bias_accel = accel_bias;
    cfg_.imu_bias_gyro  = gyro_bias;
}

// =============================================================================
// The closed-form model queries, forwarded to the actuator model so there is
// one copy of each formula.
// 閉じた式で書けるモデルの問い合わせ。式の写しが 1 つで済むよう、アクチュエータの
// モデルへ渡す。
// =============================================================================

float Plant::omegaDot(float omega, float v_motor) const
{
    return g_ext.actuator.omegaDot(omega, v_motor);
}

float Plant::steadyStateOmega(float v_motor) const
{
    return g_ext.actuator.steadyStateOmega(v_motor);
}

float Plant::dutyToThrust(float duty) const
{
    return g_ext.actuator.dutyToThrust(duty);
}

float Plant::ocvFromCharge(float charge_mah) const
{
    return g_ext.actuator.ocvFromCharge(charge_mah);
}

void Plant::updateBattery(float i_total_a, float h)
{
    g_ext.actuator.updateBattery(i_total_a, h);
    v_batt_ = g_ext.actuator.batteryVoltage();
}

float Plant::hoverDuty() const
{
    return g_ext.actuator.hoverDuty();
}

// =============================================================================
// Externally supplied rigid body (mode A). The host injects, steps, and takes.
// 外から与える剛体（動作 A）。ホストが注入し、進め、取り出す。
// =============================================================================

void Plant::setExternalState(const ExternalState& state)
{
    g_ext.external = true;
    g_ext.state = state;
}

void Plant::setRange(const RangeInput& range)
{
    g_ext.range = range;
}

Wrench Plant::takeWrench()
{
    Wrench out;
    // Divide the accumulated impulse by the interval it covers. A take with no
    // step in between has nothing to average, so it reports zero.
    // 累積した力積を、それが覆う時間で割る。間に 1 度も step が無ければ平均する
    // ものが無いので、0 を返す。
    if (g_ext.wrench_dt_s > 0.0) {
        const float inv_dt = (float)(1.0 / g_ext.wrench_dt_s);
        out.force_frd  = g_ext.impulse_frd * inv_dt;
        out.torque_frd = g_ext.ang_impulse_frd * inv_dt;
        out.wind_ned   = g_ext.wind_impulse_ned * inv_dt;
        out.dt_s       = (float)g_ext.wrench_dt_s;
    }
    g_ext.impulse_frd     = Vec3{0.0f, 0.0f, 0.0f};
    g_ext.ang_impulse_frd = Vec3{0.0f, 0.0f, 0.0f};
    g_ext.wind_impulse_ned = Vec3{0.0f, 0.0f, 0.0f};
    g_ext.wrench_dt_s = 0.0;
    return out;
}

float Plant::motorOmega(int motor) const
{
    return g_ext.actuator.motorOmega(motor);
}

bool Plant::externalStateActive() const
{
    return g_ext.external;
}

// -----------------------------------------------------------------------------
// setStartHeight — place the body at rest, level, at ENU height z. In externally
// supplied mode the host owns the pose, so this only records the rest height
// (which the ground effect and the floor of the built-in integrator use).
// setStartHeight — 機体を ENU 高さ z で水平・静止に置く。外部供給の動作では姿勢は
// ホストが持つので、ここでは静止高さを覚えるだけにする（地面効果と、内蔵の積分器の
// 床が使う）。
// -----------------------------------------------------------------------------
void Plant::setStartHeight(float z)
{
    ground_rest_z_enu_ = z;
    if (g_ext.external) return;

    g_ext.body.pos_enu = Vec3{0.0f, 0.0f, z};
    g_ext.body.vel_enu = Vec3{0.0f, 0.0f, 0.0f};
    g_ext.body.omega_flu = Vec3{0.0f, 0.0f, 0.0f};
    g_ext.body.q_flu_enu = Quat{1.0f, 0.0f, 0.0f, 0.0f};
    g_ext.body.accel_flu = Vec3{0.0f, 0.0f, kGravity};   // at rest the IMU reads +g up in FLU
}

// -----------------------------------------------------------------------------
// step — advance by dt of virtual time in fixed substeps, remainder carried.
//        Same accumulator discipline as plant.cpp:469-487, so the physics clock
//        tracks the caller's virtual clock 1:1.
// step — 仮想時間 dt ぶんを固定の副刻みで進め、端数は繰り越す。plant.cpp:469-487 と
//        同じ累積のしかたなので、物理時計は呼び出し側の仮想時計と 1:1 で一致する。
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
// substep — one fixed step of length h: the actuator model, then either the
//           built-in 6-DOF integration (B) or the wrench accumulation (A), then
//           the noise clock.
//
// What MuJoCo did implicitly — turning the four off-center rotor forces into a
// net force and a roll/pitch moment — is done explicitly here, in both modes.
//
// substep — 長さ h の固定刻み 1 回: アクチュエータのモデル、続いて内蔵の 6 自由度
//           積分（B）か力の累積（A）、最後にノイズの時計。MuJoCo が暗黙にやっていた
//           こと（偏心した 4 つのロータ力から合力とロール/ピッチのモーメントを作る
//           こと）は、どちらの動作でもここで明示的に計算する。
// -----------------------------------------------------------------------------
void Plant::substep(float h)
{
    // Height the ground effect is evaluated at: the host's measured height above
    // the surface below the rotors in mode A, the integrator's own ENU z in B.
    // 地面効果を評価する高さ: 動作 A ではホストが測ったロータの真下の面からの高さ、
    // B では積分器自身の ENU z。
    const float ge_height_m =
        g_ext.external ? g_ext.range.ground_height_m : g_ext.body.pos_enu.z;

    const ActuatorOutput act = g_ext.actuator.substep(h, ge_height_m);
    v_batt_ = g_ext.actuator.batteryVoltage();

    // --- What MuJoCo derived from the <motor site=... gear="0 0 1 0 0 0"> set:
    // each rotor pushes along body +Z (FLU) at its own off-center site, giving a
    // net vertical force and a roll/pitch moment r × F. The rotor height adds no
    // moment for a purely +Z force, so only the X and Y offsets appear.
    // --- MuJoCo が <motor site=... gear="0 0 1 0 0 0"> から導いていたもの:
    // 各ロータが自分の偏心した site で機体 +Z(FLU) 方向に押し、合力の鉛直成分と
    // ロール/ピッチのモーメント r × F を生む。純粋な +Z 方向の力に対してロータの
    // 高さはモーメントを生まないので、X と Y のずれだけが現れる。
    Vec3 force_flu{0.0f, 0.0f, 0.0f};
    Vec3 torque_flu = act.torque_flu;
    for (int i = 0; i < 4; ++i) {
        force_flu.z += act.thrust[i];
        // r × F with F = (0,0,thrust[i]) → (ry·Fz, −rx·Fz, 0).
        // r × F（F = (0,0,thrust[i])）→ (ry·Fz, −rx·Fz, 0)。
        torque_flu.x +=  kRotorY[i] * act.thrust[i];
        torque_flu.y += -kRotorX[i] * act.thrust[i];
    }

    if (g_ext.external) accumulateWrench(force_flu, torque_flu, act.wind_enu, h);
    else                integrateBody(force_flu, torque_flu, act.wind_enu, h);

    // Feed this substep's throttle to the noise model and advance it one substep
    // (bias random walk + fresh white sample). No-op when noise is disabled.
    // 本副刻みのスロットルをノイズモデルへ渡し、1 副刻みぶん進める（バイアスの
    // ランダムウォークと新しい白色サンプル）。ノイズ無効時は何もしない。
    noise_.setThrottle(act.mean_duty);
    noise_.advance(h);
}

// -----------------------------------------------------------------------------
// accumulateWrench (mode A) — add this substep's impulse to the accumulator the
// host drains with takeWrench(). The body itself is not integrated here: the
// host's PhysX owns it. The force and torque are converted FLU → FRD and the
// wind ENU → NED, so the host sees StampFly conventions throughout.
// accumulateWrench（動作 A）— 本副刻みの力積を、ホストが takeWrench() で取り出す
// 累積器へ足す。剛体そのものはここでは積分しない（ホストの PhysX が持つ）。力と
// トルクは FLU → FRD へ、風は ENU → NED へ変換し、ホストが最後まで StampFly の
// 規約で見られるようにする。
// -----------------------------------------------------------------------------
void Plant::accumulateWrench(const sf::math::Vec3& force_flu,
                             const sf::math::Vec3& torque_flu,
                             const sf::math::Vec3& wind_enu,
                             float h)
{
    const Vec3 force_frd  = frames::flu_to_frd(force_flu);
    const Vec3 torque_frd = frames::flu_to_frd(torque_flu);
    const Vec3 wind_ned   = frames::enu_to_ned(wind_enu);

    g_ext.impulse_frd     = g_ext.impulse_frd + force_frd * h;
    g_ext.ang_impulse_frd = g_ext.ang_impulse_frd + torque_frd * h;
    g_ext.wind_impulse_ned = g_ext.wind_impulse_ned + wind_ned * h;
    g_ext.wrench_dt_s += (double)h;
    g_ext.body.time += h;
}

// -----------------------------------------------------------------------------
// integrateBody (mode B) — the built-in 6-DOF integration: translation in world
// ENU, rotation in body FLU, with a spring-damper floor. Semi-implicit Euler at
// the 250 µs substep, which is stable against that stiff contact where explicit
// Euler is not.
// integrateBody（動作 B）— 内蔵の 6 自由度積分: 並進は世界 ENU、回転は機体 FLU で、
// 床はばね・ダンパ。250 µs の副刻みでの準陰的オイラーで、この硬い接触に対して
// 陽的オイラーと違って安定する。
// -----------------------------------------------------------------------------
void Plant::integrateBody(const sf::math::Vec3& force_flu,
                          const sf::math::Vec3& torque_flu,
                          const sf::math::Vec3& wind_enu,
                          float h)
{
    BodyState& body = g_ext.body;

    // --- Translation: world ENU. f = R·F_body + wind + gravity + contact.
    // --- 並進: 世界 ENU。f = R·F_body ＋ 風 ＋ 重力 ＋ 接触。
    const Quat q = body.q_flu_enu;
    Vec3 force_enu = q.rotate(force_flu);
    force_enu.x += wind_enu.x;
    force_enu.y += wind_enu.y;
    force_enu.z += wind_enu.z;

    // Floor: a stiff spring-damper while the body is below its rest height, plus
    // tangential drag so it does not slide or spin freely on the ground. The
    // rest height is the one setStartHeight() recorded, so placing the craft
    // higher raises the floor with it.
    // 床: 静止高さより下にある間は硬いばね・ダンパ。加えて接線方向の抗力を入れ、
    // 地上で滑ったり自由に回ったりしないようにする。静止高さは setStartHeight() が
    // 覚えた値なので、機体を高い位置に置けば床も一緒に上がる。
    const float penetration = ground_rest_z_enu_ - body.pos_enu.z;
    bool on_ground = false;
    if (penetration > 0.0f) {
        on_ground = true;
        float fz = kContactK * penetration - kContactC * body.vel_enu.z;
        if (fz < 0.0f) fz = 0.0f;              // the floor only pushes
        force_enu.z += fz;
        force_enu.x -= kContactMuC * body.vel_enu.x;
        force_enu.y -= kContactMuC * body.vel_enu.y;
    }

    const Vec3 accel_enu{force_enu.x / kMass,
                         force_enu.y / kMass,
                         force_enu.z / kMass - kGravity};

    // Semi-implicit Euler: velocity first, then position with the NEW velocity.
    // 準陰的オイラー: 先に速度、次に「更新後の」速度で位置。
    body.vel_enu.x += accel_enu.x * h;
    body.vel_enu.y += accel_enu.y * h;
    body.vel_enu.z += accel_enu.z * h;
    body.pos_enu.x += body.vel_enu.x * h;
    body.pos_enu.y += body.vel_enu.y * h;
    body.pos_enu.z += body.vel_enu.z * h;

    // --- Rotation: body FLU. I·ω̇ = τ − ω × (I·ω) (the gyroscopic term the
    // asymmetric inertia makes non-negligible).
    // --- 回転: 機体 FLU。I·ω̇ = τ − ω × (I·ω)（非対称な慣性のため無視できない
    // ジャイロ項）。
    const Vec3 w = body.omega_flu;
    const Vec3 Iw{kIxx * w.x, kIyy * w.y, kIzz * w.z};
    Vec3 tau = torque_flu;
    tau.x -= (w.y * Iw.z - w.z * Iw.y);
    tau.y -= (w.z * Iw.x - w.x * Iw.z);
    tau.z -= (w.x * Iw.y - w.y * Iw.x);
    if (on_ground) {
        // Angular drag on the ground, matching the tangential linear drag above.
        // 地上での角度方向の抗力。上の接線方向の抗力と対になるもの。
        tau.x -= kContactMuC * kContactMuAng * w.x;
        tau.y -= kContactMuC * kContactMuAng * w.y;
        tau.z -= kContactMuC * kContactMuAng * w.z;
    }
    body.omega_flu.x += (tau.x / kIxx) * h;
    body.omega_flu.y += (tau.y / kIyy) * h;
    body.omega_flu.z += (tau.z / kIzz) * h;

    // Quaternion kinematics: q̇ = ½·q⊗(0,ω_body).
    // クォータニオンの運動学: q̇ = ½·q⊗(0,ω_body)。
    const Vec3 wb = body.omega_flu;
    const Quat qd{
        0.5f * (-q.x * wb.x - q.y * wb.y - q.z * wb.z),
        0.5f * ( q.w * wb.x + q.y * wb.z - q.z * wb.y),
        0.5f * ( q.w * wb.y - q.x * wb.z + q.z * wb.x),
        0.5f * ( q.w * wb.z + q.x * wb.y - q.y * wb.x)};
    body.q_flu_enu = normalized(Quat{q.w + qd.w * h, q.x + qd.x * h,
                                     q.y + qd.y * h, q.z + qd.z * h});

    // The accelerometer reading, body FLU: a − g rotated into the body. At rest
    // this is +9.81 along body +Z (up), which frames::flu_to_frd then turns into
    // the −9.81 on FRD +Z the driver convention expects.
    // 加速度計の測定値（機体 FLU）: a − g を機体系へ回したもの。静止時は機体 +Z
    // （上）向きに +9.81 で、frames::flu_to_frd がこれをドライバ規約の FRD +Z 上の
    // −9.81 に変換する。
    const Vec3 sf_enu{accel_enu.x, accel_enu.y, accel_enu.z + kGravity};
    const Quat qc = body.q_flu_enu;
    const Quat qinv{qc.w, -qc.x, -qc.y, -qc.z};
    body.accel_flu = qinv.rotate(sf_enu);

    body.time += h;
}

// =============================================================================
// Truth and the synthetic sensors. Both modes answer from the SAME expressions —
// only where the state comes from differs: the host's injection (A) or the
// built-in integrator (B). The formulas are the ones in plant.cpp:733-915.
// 真値と合成センサ。どちらの動作でも同じ式で答える。違うのは状態の出どころだけで、
// ホストの注入（A）か内蔵の積分器（B）か。式は plant.cpp:733-915 のもの。
// =============================================================================

Plant::Truth Plant::truth() const
{
    if (g_ext.external) {
        Truth t;
        t.pos_ned   = g_ext.state.pos_ned;
        t.vel_ned   = g_ext.state.vel_ned;
        t.q_nb      = g_ext.state.q_nb;
        t.omega_frd = g_ext.state.omega_frd;
        return t;
    }

    Truth t;
    t.pos_ned   = frames::enu_to_ned(g_ext.body.pos_enu);
    t.vel_ned   = frames::enu_to_ned(g_ext.body.vel_enu);
    t.q_nb      = frames::mujoco_quat_to_qnb(g_ext.body.q_flu_enu);
    t.omega_frd = frames::gyro_body_frd(g_ext.body.omega_flu);
    return t;
}

// -----------------------------------------------------------------------------
// imu — synthetic IMU in body FRD, driver-normalized (−9.8 at rest).
// imu — 機体 FRD の合成 IMU。ドライバ正規化（静止で −9.8）。
// -----------------------------------------------------------------------------
sf::ImuData Plant::imu() const
{
    // In mode A the host already computed the accelerometer reading in FRD from
    // its own velocity difference, so contact forces are in it; in mode B the
    // integrator's FLU value is mapped to FRD.
    // 動作 A では、ホストが自分の速度差から FRD の加速度計測定値を計算済みなので
    // 接触力が入っている。動作 B では積分器の FLU の値を FRD へ写す。
    const Vec3 accel_frd = g_ext.external ? g_ext.state.accel_frd
                                          : frames::flu_to_frd(g_ext.body.accel_flu);
    const Vec3 gyro_frd  = g_ext.external ? g_ext.state.omega_frd
                                          : frames::gyro_body_frd(g_ext.body.omega_flu);

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
    out.timestamp = (uint32_t)(g_ext.body.time * 1e6);
    return out;
}

// -----------------------------------------------------------------------------
// tof — downward range. In mode A the host's raycast already measured it, which
// is the whole point of the Unity build (a table or a wall changes the reading);
// in mode B it is the flat-floor geometry plant.cpp uses: the ESKF's model is
// height = distance·cosR·cosP, so distance = −pos_z / (cosR·cosP).
// tof — 下向きの距離。動作 A ではホストのレイキャストが測った値をそのまま使う
// （机や壁で値が変わるのが Unity 版の眼目である）。動作 B では plant.cpp と同じ
// 平らな床の幾何で、ESKF の観測モデル height = distance·cosR·cosP を逆に解いて
// distance = −pos_z/(cosR·cosP) とする。
// -----------------------------------------------------------------------------
sf::TofData Plant::tof() const
{
    float dist;
    bool  valid;
    if (g_ext.external) {
        dist  = g_ext.range.downward_m;
        valid = g_ext.range.downward_valid && dist > 0.0f && dist < 4.0f;
    } else {
        Truth t = truth();
        Vec3 e = t.q_nb.to_euler();
        float denom = std::cos(e.x) * std::cos(e.y);
        dist = (std::fabs(denom) > 1e-3f) ? (-t.pos_ned.z / denom) : -t.pos_ned.z;
        valid = (dist > 0.0f && dist < 4.0f);   // VL53L3CX range gate (true distance)
    }

    sf::TofData out{};
    out.distance = dist;
    // Validity reflects whether a real target is in range (from the TRUE
    // distance), NOT the noisy reading.
    // 妥当性は真の距離（実標的が範囲内か）で決める。ノイズで揺れた値ではない。
    out.valid = valid;

    // N2 observation noise, applied ONLY above the VL53 reliable close-range
    // minimum. The body rests at ~13 mm, below that range; adding σ-class noise
    // there drives the reading negative and makes the boot ToF gate chatter.
    // N2 観測ノイズは VL53 の信頼近接下限より上でのみ付与する。静止高さ約 13 mm は
    // 信頼範囲より近く、そこへ σ 級のノイズを載せると負になり、起動時の ToF 判定が
    // チャタリングする。
    constexpr float kTofMinRange = 0.05f;       // VL53 reliable close-range minimum [m]
    if (dist > kTofMinRange) {
        noise_.applyTof(out.distance);
        if (out.distance < kTofMinRange) out.distance = kTofMinRange;  // stay in valid range
    }
    out.status = 0;
    out.timestamp = (uint32_t)(g_ext.body.time * 1e6);
    return out;
}

// -----------------------------------------------------------------------------
// baro — pressure-altitude = −pos_z; ISA pressure from that altitude.
// baro — 気圧高度 = −pos_z と、その高度の ISA 気圧。
// -----------------------------------------------------------------------------
sf::BaroData Plant::baro() const
{
    Truth t = truth();
    float alt = -t.pos_ned.z;
    // N2 observation noise on the altitude [m], added BEFORE the ISA pressure
    // mapping so it propagates through the BMP280 encode/decode to the firmware.
    // N2 観測ノイズを高度[m]に付与する。ISA 気圧変換の前に入れることで、BMP280 の
    // 気圧のエンコード・デコードを経てファームへ伝わる。
    noise_.applyBaro(alt);

    sf::BaroData out{};
    out.altitude = alt;
    out.pressure = 101325.0f * std::pow(1.0f - 2.25577e-5f * alt, 5.25588f);
    out.temperature = 25.0f;
    out.timestamp = (uint32_t)(g_ext.body.time * 1e6);
    return out;
}

// -----------------------------------------------------------------------------
// flow — PMW3901 optical flow, raw counts, NO body remap (dx=fwd, dy=right).
//
// The plant must be the exact inverse of the ESKF's rotation removal
// (eskf_core.cpp:535-536): the ESKF computes trans_x = flow_x − gyro_y and
// trans_y = flow_y + gyro_x, so synthesis ADDS the opposite-signed gyro term:
//   dx ∝ (vx_body/height + omega_frd.y) ,  dy ∝ (vy_body/height − omega_frd.x).
//
// flow — PMW3901 オプティカルフロー、生カウント、機体の remap なし（dx=前、dy=右）。
// ESKF の回転除去（eskf_core.cpp:535-536）の厳密な逆。ESKF は
// trans_x = flow_x − gyro_y、trans_y = flow_y + gyro_x を計算するので、合成は
// 逆符号のジャイロ項を足す: dx ∝ (vx/h + ωy)、dy ∝ (vy/h − ωx)。
// -----------------------------------------------------------------------------
sf::FlowData Plant::flow() const
{
    Truth t = truth();
    Vec3 v_body = t.q_nb.inv_rotate(t.vel_ned);   // NED velocity → body FRD
    float height = -t.pos_ned.z;
    if (height < 1e-3f) height = 1e-3f;

    float rpp = cfg_.flow_rad_per_pixel;
    float fdx = (v_body.x / height + t.omega_frd.y) * last_dt_ / rpp;
    float fdy = (v_body.y / height - t.omega_frd.x) * last_dt_ / rpp;
    // Opt-in under-read model (default 1.0 = no effect) — see Config::flow_vel_scale.
    // オプトインの過小読みモデル（既定 1.0 = 無効）— Config::flow_vel_scale 参照。
    fdx *= cfg_.flow_vel_scale;
    fdy *= cfg_.flow_vel_scale;

    sf::FlowData out{};
    out.dx = (int16_t)std::lround(fdx);
    out.dy = (int16_t)std::lround(fdy);
    out.squal = 100;  // strong surface / 良好な表面
    out.timestamp = (uint32_t)(g_ext.body.time * 1e6);
    return out;
}

// -----------------------------------------------------------------------------
// mag — body magnetic field from a fixed NED reference (default OFF in the ESKF).
// mag — 固定の NED 基準磁場から機体の磁場（ESKF では既定 OFF）。
// -----------------------------------------------------------------------------
sf::MagData Plant::mag() const
{
    Truth t = truth();
    Vec3 ref_ned{20.0f, 0.0f, 40.0f};        // reference field NED [µT]
    Vec3 b = t.q_nb.inv_rotate(ref_ned);

    sf::MagData out{};
    out.mag[0] = b.x; out.mag[1] = b.y; out.mag[2] = b.z;
    out.timestamp = (uint32_t)(g_ext.body.time * 1e6);
    return out;
}

// -----------------------------------------------------------------------------
// Handling maneuver — out of scope for this plant. It is the "pick the drone up
// and put it back" workflow, which belongs to the MuJoCo bench's scenarios; in
// the Unity build the host moves the craft directly. Defined so the device
// models link; calling it leaves the body where it is.
// ハンドリング動作 — このプラントでは扱わない。「機体を拾って置き直す」ワークフローは
// MuJoCo 版のシナリオのもので、Unity 版では機体をホストが直接動かす。デバイスモデルが
// リンクできるよう定義だけ置く。呼んでも機体はその場に留まる。
// -----------------------------------------------------------------------------
void Plant::startHandling(float, float, float, float, float, float)
{
    std::fprintf(stderr,
                 "[plant_external] startHandling is not implemented in the "
                 "MuJoCo-free plant — ignored\n");
}

// handlingSubstep is never reached (handling_active_ stays false), but the
// declaration in plant.hpp needs a definition for the link to succeed.
// handlingSubstep には到達しない（handling_active_ は false のまま）が、plant.hpp の
// 宣言にはリンクのための定義が要る。
void Plant::handlingSubstep(float)
{
}

}  // namespace sils
