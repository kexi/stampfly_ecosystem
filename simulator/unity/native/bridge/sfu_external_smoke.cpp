/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file sfu_external_smoke.cpp
 * @brief Unity's stand-in: a rigid body integrated in C++, in Unity's own
 *        conventions, driven by the forces sfu_step returns.
 *        Unity の代役。Unity 自身の規約で C++ の中に持った剛体を、sfu_step が
 *        返す力で動かす。
 *
 * This is the externally supplied path — the one the Unity build actually uses —
 * exercised without Unity. Everything here is in Unity's conventions (left-
 * handed, +Y up, quaternion x,y,z,w), never converted, because that is exactly
 * the contract the C# side will hold to. It is therefore also the worked example
 * `SimLoop.Update()` is written from: each tick packs the Rigidbody's state,
 * calls `sfu_step`, applies `force_local` / `torque_local` / `wind_force_world`,
 * integrates, and computes the accelerometer reading for the next tick.
 *
 * これは外部供給の経路 ― Unity 版が実際に使う方 ― を Unity 無しで動かすものである。
 * ここにあるものは全て Unity の規約（左手系・+Y 上・クォータニオンは x,y,z,w）の
 * ままで、1 度も変換しない。それが C# 側が守る取り決めそのものだからである。
 * よってこれは `SimLoop.Update()` を書き起こすときの手本でもある。1 刻みごとに
 * Rigidbody の状態を詰め、`sfu_step` を呼び、`force_local`・`torque_local`・
 * `wind_force_world` を加え、積分し、次の刻みのための加速度計の測定値を作る。
 *
 * The physics here is deliberately the simplest thing that works — semi-implicit
 * Euler with a spring-damper floor, standing in for PhysX. It is NOT a claim
 * about what PhysX will do; PhysX's contact solver and this floor will part ways
 * on impact. What it does establish is that the force and torque crossing the
 * ABI are the right magnitude, the right sign and in the right frame.
 *
 * ここの物理は「動く最も単純なもの」を意図的に選んである。準陰的オイラーと、
 * ばね・ダンパの床で、PhysX の代役である。PhysX がどう振る舞うかを主張するもの
 * ではない。PhysX の接触ソルバとこの床は、接地の瞬間から分かれる。確かめられる
 * のは、ABI を渡る力とトルクの大きさ・符号・座標系が正しいことである。
 *
 * ## Why a gust, and why the attitude is a verdict / なぜ突風を入れ、姿勢で判定するか
 *
 * Altitude alone does NOT establish the torque's sign. A vertical take-off into
 * an undisturbed hover starts level and stays level, so the attitude loop is
 * never asked to correct anything and the torque stays near zero: reversing
 * every torque component still produces the same climb. This check therefore
 * pushes the vehicle off level with a lateral gust partway through the flight,
 * and requires it to be **back near level and still FLYING at the end**. With
 * the torque reversed, the attitude loop's correction drives the tilt further
 * instead of back, and the vehicle tumbles — so the reversed build fails.
 *
 * 高度だけではトルクの符号を確かめられない。外乱の無いホバリングへ鉛直に離陸する
 * 飛行は、水平に始まり水平のままなので、姿勢ループは何も直すよう求められず、
 * トルクは 0 付近に留まる。トルクの全成分を反転させても同じ上昇が出てしまう。
 * よってこの確認は、飛行の途中で横向きの突風を当てて機体を水平から外し、
 * **終了時に水平付近へ戻っており、かつ FLYING のままであること**を求める。トルクが
 * 反転していれば、姿勢ループの修正は傾きを戻さず深める向きに働き、機体は転がる ―
 * 反転させた版は不合格になる。
 *
 * Usage / 使い方:
 *   sfu_external_smoke [simulated seconds, default 30] [--log-jsonl <path>]
 *
 * @design docs/plans/unity-simulator.md §3 1 刻みの処理, §5 段階 2
 *         AGENTS.md「新しく書くコードのログの決まり」
 */

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>

#include <string>

#include "sfu_api.h"
#include "sfu_log_jsonl.hpp"
#include "sfu_rc_script.hpp"
#include "sfu_smoke_options.hpp"

namespace {

// -----------------------------------------------------------------------------
// The airframe's numbers, from simulator/sils/models/stampfly.xml. The inertia
// is about the body axes in Unity's order: X = right, Y = up, Z = forward, so
// the firmware's roll/pitch/yaw inertias map to Z/X/Y.
// 機体の数値。出典は simulator/sils/models/stampfly.xml。慣性は Unity の順に
// 並べた機体軸まわりのもので、X = 右・Y = 上・Z = 前。よってファームの
// ロール/ピッチ/ヨーの慣性は Z/X/Y に対応する。
// -----------------------------------------------------------------------------
constexpr float kMassKg = 0.037f;

constexpr float kInertiaRollKgM2  = 9.16e-6f;   ///< about the forward axis (Unity Z)
constexpr float kInertiaPitchKgM2 = 13.3e-6f;   ///< about the right axis  (Unity X)
constexpr float kInertiaYawKgM2   = 20.4e-6f;   ///< about the up axis     (Unity Y)

constexpr float kGravityMs2 = 9.81f;

/// Floor contact, standing in for PhysX: a stiff spring with damping, acting
/// only while the body is below its resting height.
/// 床の接触。PhysX の代役として、硬いばねと減衰を、機体が静止高さより下にある
/// 間だけ効かせる。
constexpr float kFloorStiffnessNPerM = 800.0f;
constexpr float kFloorDampingNsPerM  = 3.0f;

/// Host tick: 2.5 ms, the 400 Hz the Unity SimLoop will run its physics at.
/// ホストの刻み: 2.5 ms。Unity の SimLoop が物理を回す 400 Hz である。
constexpr uint32_t kTickUs = 2500;
constexpr float kTickSeconds = (float)kTickUs * 1e-6f;

constexpr float kRestHeightM = 0.013f;

constexpr double kHoverMinM = 0.15;
constexpr double kHoverMaxM = 3.0;

/// The gust: a lateral force in the Unity world frame, switched on well after
/// the vehicle is airborne and switched off again, so the attitude loop has to
/// both absorb it and recover from it before the run ends.
/// 突風。Unity の世界系での横向きの力で、機体が十分に浮いてから入れ、また切る。
/// 姿勢ループは、実行が終わるまでにそれを受け止め、かつそこから戻らなければ
/// ならない。
constexpr int64_t kGustStartUs = 10000000;   // 10 s
constexpr int64_t kGustEndUs   = 12000000;   // 12 s
/// Sized against the weight (0.037 kg · 9.81 = 0.363 N): about a fifth of it,
/// enough to tilt the vehicle several degrees and make the attitude loop work,
/// far too little to throw it out of the hover band on its own.
/// 重量（0.037 kg · 9.81 = 0.363 N）に対する大きさ。その 5 分の 1 ほどで、機体を
/// 数度傾けて姿勢ループを働かせるには十分、単独でホバリングの帯から放り出すには
/// まったく足りない。
constexpr float kGustForceN = 0.07f;

/// The verdict on the attitude: the vehicle must end within this much of level.
/// A build with the torque's sign reversed does not come back at all — it
/// tumbles — so the threshold only has to separate "recovered" from "tumbling",
/// not to be a tight performance figure.
/// 姿勢についての判定。機体は水平からこの範囲内で終わらなければならない。トルクの
/// 符号を反転させた版はそもそも戻らず転がるので、閾値は「戻った」と「転がった」を
/// 分けられればよく、厳しい性能の数値である必要は無い。
constexpr double kLevelMaxDeg = 20.0;

/// `sf::FlightState::FLYING` as the ABI reports it. Written out as a number
/// rather than included from the firmware's header, because this check touches
/// `sfu_api.h` and nothing else — exactly the position the C# side is in.
/// ABI が返す `sf::FlightState::FLYING` の値。ファームのヘッダから取り込まずに
/// 数値で書くのは、この確認が触れるのが `sfu_api.h` だけだからである ― C# 側が
/// 置かれるのとまさに同じ立場である。
constexpr int32_t kFlightStateFlying = 5;

/// A three-component vector in Unity's frame. Deliberately its own tiny type
/// rather than sf::math::Vec3, so nothing here can reach a StampFly-frame helper
/// by accident — the C# side will not have those helpers either.
/// Unity 系の 3 成分ベクトル。sf::math::Vec3 ではなく意図的に専用の小さな型に
/// してある。ここから StampFly 系の補助関数へうっかり届かないようにするため
/// である。C# 側にもそれらの補助関数は無い。
struct UVec3 {
    float x = 0.0f, y = 0.0f, z = 0.0f;
};

UVec3 operator+(const UVec3& a, const UVec3& b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
UVec3 operator*(const UVec3& v, float s) { return {v.x * s, v.y * s, v.z * s}; }

/// A rotation in Unity's x,y,z,w order.
/// Unity の x,y,z,w の順での回転。
struct UQuat {
    float x = 0.0f, y = 0.0f, z = 0.0f, w = 1.0f;
};

/// Rotate a vector from the body frame into the world frame: v' = q·v·q⁻¹.
/// Unity is left-handed, but the algebra of a unit quaternion acting on a vector
/// is the same in either handedness once the components are in the same order.
/// ベクトルを機体系から世界系へ回す: v' = q·v·q⁻¹。Unity は左手系だが、単位
/// クォータニオンがベクトルに作用する代数は、成分の順が揃っていればどちらの
/// 利き手でも同じである。
UVec3 rotate_to_world(const UQuat& q, const UVec3& v)
{
    // t = 2·(q_vec × v);  v' = v + q_w·t + q_vec × t.
    const UVec3 t{2.0f * (q.y * v.z - q.z * v.y),
                  2.0f * (q.z * v.x - q.x * v.z),
                  2.0f * (q.x * v.y - q.y * v.x)};
    return {v.x + q.w * t.x + (q.y * t.z - q.z * t.y),
            v.y + q.w * t.y + (q.z * t.x - q.x * t.z),
            v.z + q.w * t.z + (q.x * t.y - q.y * t.x)};
}

/// Rotate a vector from the world frame into the body frame — the inverse of the
/// above, which for a unit quaternion is the same rotation with the vector part
/// negated.
/// ベクトルを世界系から機体系へ回す。上の逆で、単位クォータニオンではベクトル部の
/// 符号を反転した回転にあたる。
UVec3 rotate_to_body(const UQuat& q, const UVec3& v)
{
    const UQuat inverse{-q.x, -q.y, -q.z, q.w};
    return rotate_to_world(inverse, v);
}

/// Advance the rotation by the world-frame angular velocity over dt, keeping it
/// a unit quaternion. q̇ = ½·ω⊗q.
/// 世界系の角速度で回転を dt ぶん進め、単位クォータニオンに保つ。q̇ = ½·ω⊗q。
UQuat integrate_rotation(const UQuat& q, const UVec3& omega_world, float dt)
{
    const float half = 0.5f * dt;
    UQuat next{q.x + half * (omega_world.x * q.w + omega_world.y * q.z - omega_world.z * q.y),
               q.y + half * (omega_world.y * q.w + omega_world.z * q.x - omega_world.x * q.z),
               q.z + half * (omega_world.z * q.w + omega_world.x * q.y - omega_world.y * q.x),
               q.w - half * (omega_world.x * q.x + omega_world.y * q.y + omega_world.z * q.z)};
    const float norm = std::sqrt(next.x * next.x + next.y * next.y +
                                 next.z * next.z + next.w * next.w);
    constexpr float kMinNorm = 1e-12f;
    if (norm > kMinNorm) {
        next.x /= norm;
        next.y /= norm;
        next.z /= norm;
        next.w /= norm;
    }
    return next;
}

/// The Rigidbody, exactly as Unity would hold it.
/// Unity が持つのと同じ形での Rigidbody。
struct Body {
    UVec3 position{0.0f, kRestHeightM, 0.0f};
    UQuat rotation;
    UVec3 velocity_world;
    UVec3 angular_velocity_world;
    /// The accelerometer reading computed at the end of the previous tick, in
    /// the body frame. Fed to the firmware on the NEXT tick, as the plan's
    /// one-tick table says.
    /// 前の刻みの終わりに作った加速度計の測定値（機体系）。計画の 1 刻みの表の
    /// とおり、**次の**刻みでファームへ渡す。
    UVec3 accel_local{0.0f, kGravityMs2, 0.0f};
};

/// The force the floor pushes back with while the body is below its rest height.
/// 機体が静止高さより下にある間、床が押し返す力。
UVec3 floor_force(const Body& body)
{
    const float penetration_m = kRestHeightM - body.position.y;
    const bool is_in_contact = (penetration_m > 0.0f);
    if (!is_in_contact) return UVec3{};

    float upward_n = kFloorStiffnessNPerM * penetration_m
                   - kFloorDampingNsPerM * body.velocity_world.y;
    // A spring must never pull the body down into the floor it is pushing it
    // out of, which the damping term alone could do on a fast rebound.
    // ばねが、押し出している床の中へ機体を引き込んではならない。減衰の項だけでは
    // 跳ね返りの速いときにそれが起こり得る。
    if (upward_n < 0.0f) upward_n = 0.0f;
    return UVec3{0.0f, upward_n, 0.0f};
}

/// One physics step: apply the wrench the bridge returned plus gravity and the
/// floor, then integrate. Returns the accelerometer reading for the next tick.
/// 物理を 1 歩進める。橋渡しが返した力に重力と床を足して加え、積分する。次の刻み
/// 用の加速度計の測定値を返す。
UVec3 advance_body(Body& body, const SfuStepOut& out, float dt)
{
    const UVec3 thrust_world = rotate_to_world(
        body.rotation, UVec3{out.force_local[0], out.force_local[1], out.force_local[2]});
    const UVec3 wind_world{out.wind_force_world[0], out.wind_force_world[1],
                           out.wind_force_world[2]};
    const UVec3 contact_world = floor_force(body);

    const UVec3 applied_world = thrust_world + wind_world + contact_world;
    const UVec3 gravity_world{0.0f, -kGravityMs2 * kMassKg, 0.0f};

    const UVec3 velocity_before = body.velocity_world;
    const UVec3 acceleration_world = (applied_world + gravity_world) * (1.0f / kMassKg);
    body.velocity_world = body.velocity_world + acceleration_world * dt;
    body.position = body.position + body.velocity_world * dt;

    // Rotation: the torque arrives in the body frame, so the angular
    // acceleration is computed there and then rotated into the world frame,
    // which is where Unity reports the angular velocity.
    // 回転: トルクは機体系で来るので、角加速度を機体系で作ってから世界系へ回す。
    // Unity が角速度を報告するのは世界系だからである。
    const UVec3 omega_body = rotate_to_body(body.rotation, body.angular_velocity_world);
    const UVec3 torque_body{out.torque_local[0], out.torque_local[1], out.torque_local[2]};
    // Euler's equation, ω̇ = I⁻¹·(τ − ω × I·ω). The gyroscopic term matters
    // here because the three inertias differ by a factor of two.
    // オイラーの式 ω̇ = I⁻¹·(τ − ω × I·ω)。3 つの慣性が 2 倍ほど違うので、
    // ジャイロ項がここでは効く。
    const UVec3 angular_momentum{omega_body.x * kInertiaPitchKgM2,
                                 omega_body.y * kInertiaYawKgM2,
                                 omega_body.z * kInertiaRollKgM2};
    const UVec3 gyroscopic{omega_body.y * angular_momentum.z - omega_body.z * angular_momentum.y,
                           omega_body.z * angular_momentum.x - omega_body.x * angular_momentum.z,
                           omega_body.x * angular_momentum.y - omega_body.y * angular_momentum.x};
    const UVec3 angular_accel_body{(torque_body.x - gyroscopic.x) / kInertiaPitchKgM2,
                                   (torque_body.y - gyroscopic.y) / kInertiaYawKgM2,
                                   (torque_body.z - gyroscopic.z) / kInertiaRollKgM2};
    const UVec3 omega_body_next = omega_body + angular_accel_body * dt;
    body.angular_velocity_world = rotate_to_world(body.rotation, omega_body_next);
    body.rotation = integrate_rotation(body.rotation, body.angular_velocity_world, dt);

    // The accelerometer reading, as the plan's step 4 defines it:
    // R⁻¹·((v_after − v_before)/dt − g). Contact enters through v, so a body at
    // rest on the floor reads +9.81 on Unity's +Y.
    // 加速度計の測定値。計画の手順 4 の定義どおり
    // R⁻¹·((v 後 − v 前)/dt − g)。接触は v を通して入るので、床に静止した機体は
    // Unity の +Y に +9.81 を読む。
    const UVec3 kinematic_world{(body.velocity_world.x - velocity_before.x) / dt,
                                (body.velocity_world.y - velocity_before.y) / dt + kGravityMs2,
                                (body.velocity_world.z - velocity_before.z) / dt};
    return rotate_to_body(body.rotation, kinematic_world);
}

/// Copy the body's state into the struct the bridge reads.
/// 機体の状態を、橋渡しが読む構造体へ写す。
void pack_state(const Body& body, SfuStepIn& in)
{
    in.position[0] = body.position.x;
    in.position[1] = body.position.y;
    in.position[2] = body.position.z;
    in.rotation[0] = body.rotation.x;
    in.rotation[1] = body.rotation.y;
    in.rotation[2] = body.rotation.z;
    in.rotation[3] = body.rotation.w;
    in.velocity_world[0] = body.velocity_world.x;
    in.velocity_world[1] = body.velocity_world.y;
    in.velocity_world[2] = body.velocity_world.z;
    in.angular_velocity_world[0] = body.angular_velocity_world.x;
    in.angular_velocity_world[1] = body.angular_velocity_world.y;
    in.angular_velocity_world[2] = body.angular_velocity_world.z;
    in.accel_local[0] = body.accel_local.x;
    in.accel_local[1] = body.accel_local.y;
    in.accel_local[2] = body.accel_local.z;

    // The downward raycast. With nothing but a flat floor at y = 0, the beam
    // measures the body's own height; Unity will raycast against the world.
    // 下向きのレイキャスト。y = 0 の平らな床しか無いので、ビームは機体自身の高さを
    // 測る。Unity では空間に対してレイキャストする。
    in.range_down_m = body.position.y;
    in.range_down_valid = 1;
    in.ground_height_m = body.position.y;
}

/// The angle between the vehicle's own up axis and the world's, in degrees.
/// Zero when level, 180 when inverted — one number that says how far from level
/// the vehicle is, whichever way it tipped.
/// 機体自身の上方向と世界の上方向のなす角 [度]。水平で 0、反転で 180 ― どちらへ
/// 倒れたかによらず、水平からどれだけ離れているかを 1 つの数で表す。
double tilt_from_level_deg(const UQuat& rotation)
{
    const UVec3 body_up = rotate_to_world(rotation, UVec3{0.0f, 1.0f, 0.0f});
    double cosine = (double)body_up.y;
    if (cosine > 1.0) cosine = 1.0;
    if (cosine < -1.0) cosine = -1.0;
    constexpr double kDegPerRad = 57.29577951308232;
    return std::acos(cosine) * kDegPerRad;
}

/// What the run is judged on, gathered as it goes.
/// 実行の判定に使うもの。進行しながら集める。
struct Verdict {
    double max_altitude = 0.0;
    double last_altitude = 0.0;
    double last_tilt_deg = 0.0;
    double max_tilt_deg = 0.0;
    int32_t last_flight_state = 0;
};

/// Switch the gust on at its start and off at its end. Stated as "the force the
/// gust has at this moment", so the same call works however long a tick is.
/// 突風を、始まりで入れ、終わりで切る。「この時点で突風が持つ力」として書いてある
/// ので、刻みの長さに関わらず同じ呼び出しで済む。
void apply_gust(int64_t now_us, bool& gust_is_on)
{
    const bool should_be_on = (now_us >= kGustStartUs && now_us < kGustEndUs);
    if (should_be_on == gust_is_on) return;
    gust_is_on = should_be_on;
    // Unity's +X is right: a sideways push, which rolls the vehicle.
    // Unity の +X は右。横向きに押すので、機体はロールする。
    sfu_set_wind(should_be_on ? kGustForceN : 0.0f, 0.0f, 0.0f);
}

/// One line per simulated second, so a failing run leaves a readable trace.
/// シミュレーション 1 秒に 1 行。失敗した実行が読める記録を残すようにするため。
void report_second(const SfuStepOut& out, const Verdict& verdict)
{
    std::printf("EXT t=%6.2f alt=%7.3f tilt=%5.1f state=%d armed=%d vbatt=%.2f "
                "force=%7.4f,%7.4f,%7.4f dt=%.6f\n",
                (double)out.now_us * 1e-6, verdict.last_altitude, verdict.last_tilt_deg,
                (int)out.flight_state, (int)out.armed, out.battery_voltage,
                out.force_local[0], out.force_local[1], out.force_local[2],
                out.wrench_dt_s);
}

/// The flight itself. Returns SFU_OK, or the code the bridge refused with.
/// 飛行そのもの。SFU_OK か、橋渡しが拒んだ値を返す。
int32_t fly(double sim_seconds, sfu::JsonlLog& log, Verdict& verdict)
{
    Body body;
    SfuStepIn  in{};
    SfuStepOut out{};
    in.struct_size  = sizeof(SfuStepIn);
    out.struct_size = sizeof(SfuStepOut);
    in.dt_us = kTickUs;

    int64_t next_report_us = 0;
    bool gust_is_on = false;

    const int64_t total_us = (int64_t)(sim_seconds * 1e6);
    while (out.now_us < total_us) {
        // --- What SimLoop.Update() does, in order ---
        // --- SimLoop.Update() が行うことを、その順序で ---
        pack_state(body, in);
        sfu::rc_script_at(out.now_us + (int64_t)kTickUs,
                          in.rc_throttle, in.rc_roll, in.rc_pitch, in.rc_yaw, in.rc_flags);
        apply_gust(out.now_us, gust_is_on);

        const int32_t stepped = sfu_step(&in, &out);
        if (stepped != SFU_OK) return stepped;

        body.accel_local = advance_body(body, out, kTickSeconds);

        verdict.last_altitude = body.position.y;
        if (verdict.last_altitude > verdict.max_altitude) {
            verdict.max_altitude = verdict.last_altitude;
        }
        verdict.last_tilt_deg = tilt_from_level_deg(body.rotation);
        if (verdict.last_tilt_deg > verdict.max_tilt_deg) {
            verdict.max_tilt_deg = verdict.last_tilt_deg;
        }
        verdict.last_flight_state = out.flight_state;

        if (out.now_us >= next_report_us) {
            constexpr int64_t kReportPeriodUs = 1000000;
            next_report_us += kReportPeriodUs;
            report_second(out, verdict);
            // Drain the firmware's log on the same cadence, which is also what
            // a host does: read once per frame, not once at the end.
            // ファームのログも同じ周期で取り出す。ホストが行うのもこれである ―
            // 最後に 1 回ではなく、1 コマに 1 回読む。
            log.drain_firmware();
        }
    }
    return SFU_OK;
}

/// Print the verdict and say whether the run passed.
/// 判定を印字し、実行が合格したかを返す。
bool announce(const Verdict& verdict)
{
    std::printf("[external_smoke] max altitude %.3f m, final altitude %.3f m\n",
                verdict.max_altitude, verdict.last_altitude);
    std::printf("[external_smoke] max tilt %.1f deg, final tilt %.1f deg, state %d\n",
                verdict.max_tilt_deg, verdict.last_tilt_deg,
                (int)verdict.last_flight_state);

    // Three conditions, all of them necessary. Altitude alone passes with the
    // torque reversed; the tilt and the flight state are what do not.
    // 3 つの条件で、どれも欠かせない。高度だけではトルクを反転させても通ってしまう。
    // 通らなくするのが傾きと飛行状態である。
    const bool hovering = (verdict.last_altitude > kHoverMinM &&
                           verdict.last_altitude < kHoverMaxM);
    const bool level = (verdict.last_tilt_deg < kLevelMaxDeg);
    const bool flying = (verdict.last_flight_state == kFlightStateFlying);

    std::printf("[external_smoke] hover %s (altitude %s, level %s, FLYING %s)\n",
                (hovering && level && flying) ? "OK" : "FAILED",
                hovering ? "OK" : "FAILED", level ? "OK" : "FAILED",
                flying ? "OK" : "FAILED");
    return hovering && level && flying;
}

}  // namespace

int main(int argc, char** argv)
{
    const sfu::SmokeOptions options = sfu::parse_smoke_options(argc, argv);
    if (!options.ok) {
        std::fprintf(stderr,
                     "usage: sfu_external_smoke [seconds] [--log-jsonl <path>] "
                     "[--run-id <id>]\n");
        return 1;
    }

    SfuConfig config{};
    config.struct_size      = sizeof(SfuConfig);
    config.battery_model    = 1;
    config.boot_calibration = 1;
    // 1 = the host owns the rigid body. This program is that host.
    // 1 = ホストが剛体を持つ。このプログラムがそのホストである。
    config.host_owns_body   = 1;
    config.start_height_m   = kRestHeightM;

    const int32_t booted = sfu_boot(&config);
    if (booted != SFU_OK) {
        std::fprintf(stderr, "[external_smoke] sfu_boot failed: %d\n", (int)booted);
        return 1;
    }

    // Only NOW may anything allocate. One run, one run_id, issued here and
    // carried by every line the run writes — the firmware's and the bridge's
    // alike. See sfu_smoke_options.hpp on why this waits for the boot.
    // 確保を行ってよいのはここからである。1 回の実行に 1 つの run_id を発行し、
    // この実行が書く全ての行が持つ ― ファームのものも橋渡しのものも同じように。
    // なぜ起動を待つのかは sfu_smoke_options.hpp を参照。
    const std::string run_id = (options.run_id != nullptr)
        ? std::string(options.run_id) : sfu::make_run_id();
    sfu::JsonlLog log(
        (options.log_jsonl_path != nullptr) ? std::string(options.log_jsonl_path)
                                            : std::string(), run_id);
    log.write_bridge("info", "bridge.boot", -1, "sfu_boot");

    Verdict verdict;
    const auto wall_start = std::chrono::steady_clock::now();
    const int32_t flown = fly(options.sim_seconds, log, verdict);
    const double wall_seconds =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - wall_start).count();

    if (flown != SFU_OK) {
        log.write_bridge("error", "bridge.error", -1, "sfu_step failed");
        std::fprintf(stderr, "[external_smoke] sfu_step failed: %d\n", (int)flown);
        return 1;
    }

    // The speed goes to stderr, not stdout: two runs must print identical
    // stdout, and a wall-clock measurement never repeats exactly.
    // 速度は stdout ではなく stderr へ出す。2 回の実行の stdout は一致しなければ
    // ならず、実時間の測定値がそのまま繰り返されることはないからである。
    std::fprintf(stderr,
                 "[external_smoke] simulated %.1f s in %.3f s of wall clock — "
                 "REAL TIME PER SIMULATED SECOND = %.4f s (target <= 0.30)\n",
                 options.sim_seconds, wall_seconds, wall_seconds / options.sim_seconds);

    const bool passed = announce(verdict);

    log.drain_firmware();
    log.write_bridge("info", "bridge.shutdown", -1, passed ? "hover OK" : "hover FAILED");
    if (log.is_open()) {
        std::fprintf(stderr, "[external_smoke] log %s (run_id %s)\n",
                     (options.log_jsonl_path != nullptr ? options.log_jsonl_path : ""), run_id.c_str());
    }

    sfu_shutdown();
    return passed ? 0 : 2;
}
