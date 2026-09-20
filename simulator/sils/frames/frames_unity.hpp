/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (SILS host bench).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file frames_unity.hpp
 * @brief The ONLY coordinate transform between Unity and StampFly.
 *        Unity と StampFly の間で唯一の座標変換。
 *
 * Companion to frames.hpp (MuJoCo ↔ StampFly). Spec & rationale:
 * simulator/sils/docs/coordinate_frames.md §7 (Unity). Kept header-only and free
 * of MuJoCo so the Unity native core and its unit test can build with sf_math alone.
 *
 * frames.hpp（MuJoCo ↔ StampFly）の対。仕様と理由:
 * simulator/sils/docs/coordinate_frames.md §7（Unity）。Unity のネイティブコアと
 * その単体試験が sf_math だけでビルドできるよう、ヘッダのみ・MuJoCo 非依存とする。
 *
 * ---------------------------------------------------------------------------
 * CONTRACT WITH THE C# SIDE / C# 側との取り決め
 * ---------------------------------------------------------------------------
 * The C ABI passes Unity's RAW conventions in both directions; ALL conversion
 * happens in this file. The C# side must NOT reimplement any of it.
 * C ABI は Unity の生の規約のまま双方向に受け渡し、変換は全てこのファイルで行う。
 * C# 側はこれを再実装しないこと。
 *
 * What the C# side must honor / C# 側が守ること:
 *   - Pass `Rigidbody` values verbatim: `position`, `rotation` (x,y,z,w),
 *     `linearVelocity`, `angularVelocity`. No sign flips, no axis swaps.
 *     `Rigidbody` の値をそのまま渡す。符号反転も軸の入替もしない。
 *   - `Rigidbody.angularVelocity` is in Unity's WORLD frame. Pass it as the world
 *     frame; do not pre-rotate it into the body frame.
 *     `Rigidbody.angularVelocity` は Unity の世界系。世界系のまま渡し、
 *     あらかじめ機体系へ直さない。
 *   - Force and torque come back in Unity's BODY frame, ready for
 *     `AddRelativeForce` / `AddRelativeTorque`.
 *     力とトルクは Unity の機体系で返るので、`AddRelativeForce` ／
 *     `AddRelativeTorque` にそのまま渡せる。
 *   - The world file stays in ENU (x east, y north, z up). Converting between the
 *     world file and Unity is the only frame work left on the C# side.
 *     空間ファイルは ENU（x=東・y=北・z=上）のまま。空間ファイルと Unity の
 *     変換だけが C# 側に残る座標の仕事。
 *
 * ---------------------------------------------------------------------------
 * CONVENTIONS / 規約
 * ---------------------------------------------------------------------------
 *   Unity:     LEFT-handed, Y up, metres. Quaternion component order is x,y,z,w.
 *              A rotation of angle t about unit axis a is (a·sin(t/2), cos(t/2))
 *              and turns CLOCKWISE seen from the origin looking along +a.
 *              左手系・Y 上・単位 m。クォータニオンの成分順は x,y,z,w。
 *   StampFly:  World = NED (X north, Y east, Z down); Body = FRD (X fwd, Y right,
 *              Z down); attitude q_nb = body→NED, component order w,x,y,z.
 *              世界 = NED、機体 = FRD、姿勢 q_nb は body→NED で成分順 w,x,y,z。
 *
 * Axis correspondence (world AND body use the same one):
 * 軸の対応（世界系も機体系も同じ）:
 *
 *   north / forward  →  Unity +Z
 *   east  / right    →  Unity +X
 *   down             →  Unity −Y
 *
 *           StampFly NED/FRD                 Unity
 *              X (north/fwd)                  Y (up)
 *               ↑                              ↑
 *               │                              │
 *               ●──→ Y (east/right)            ●──→ X (east/right)
 *              ╱                              ╱
 *             ▼                              ╱
 *            Z (down)                       Z (north/fwd)
 *
 * ---------------------------------------------------------------------------
 * WHY POLAR AND AXIAL VECTORS GET DIFFERENT SIGNS / 極性と軸性で符号が違う理由
 * ---------------------------------------------------------------------------
 * Write the axis correspondence as a matrix M taking NED/FRD components (n,e,d)
 * to Unity components:  u = M·v,  M = [[0,1,0],[0,0,-1],[1,0,0]].
 * 軸の対応を、NED/FRD の成分 (n,e,d) を Unity の成分へ送る行列 M で書く。
 *
 * M is orthogonal (M·Mᵀ = I) but det(M) = −1: it is a HANDEDNESS FLIP, not a
 * rotation. Verified by hand and in frames_unity_test.cpp.
 * M は直交（M·Mᵀ = I）だが det(M) = −1 — 回転ではなく利き手の反転である。
 *
 *   POLAR vectors (position, velocity, force, acceleration) are plain component
 *   tuples, so they transform straight through:  u = M·v,  v = Mᵀ·u.
 *   極性ベクトル（位置・速度・力・加速度）は素の成分の組なので、そのまま写る。
 *
 *   AXIAL vectors (angular velocity, torque) are built from a cross product.
 *   A cross product picks its direction from the handedness of the frame, so
 *   under a det = −1 map it gains one extra sign:  u = det(M)·M·v = −M·v.
 *   Equivalently: the same physical spin that is counter-clockwise under the
 *   right-hand rule is clockwise under Unity's left-hand rule, so its axis must
 *   be flipped to describe the same motion.
 *   軸性ベクトル（角速度・トルク）は外積で作られる。外積の向きは系の利き手で
 *   決まるため、det = −1 の写像では符号が 1 つ余分に付く: u = det(M)·M·v = −M·v。
 *   言い換えると、右手則で反時計回りの同じ回転が Unity の左手則では時計回りに
 *   なるので、同じ運動を表すには軸を反転させる必要がある。
 *
 * The quaternion mapping is fixed by requiring the two rotation matrices to agree:
 * R_unity = M·R(q_nb)·Mᵀ. Because Unity's left-hand rule and the det = −1 flip
 * cancel each other, the resulting Unity quaternion is read with the ordinary
 * Hamilton formula — no extra conjugation. Solving that identity gives
 * unity(x,y,z,w) = (−q.y, q.z, −q.x, q.w), checked numerically in the test.
 * クォータニオンの対応は、2 つの回転行列が一致する条件 R_unity = M·R(q_nb)·Mᵀ
 * で決まる。Unity の左手則と det = −1 の反転が打ち消し合うため、得られた Unity の
 * クォータニオンは通常の Hamilton の式でそのまま読める（余分な共役は不要）。
 * この等式を解くと unity(x,y,z,w) = (−q.y, q.z, −q.x, q.w) になる。
 */

#pragma once

#include "sf_math.hpp"

namespace sils {
namespace frames {
namespace unity {

using sf::math::Quat;
using sf::math::Vec3;

// =============================================================================
// Polar vectors — position, velocity, force, accelerometer reading.
//   u = M·v  and  v = Mᵀ·u, with M = [[0,1,0],[0,0,-1],[1,0,0]].
// 極性ベクトル — 位置・速度・力・加速度計の測定値。
// =============================================================================

/// StampFly NED (world) or FRD (body) → Unity. Unity holds (east, −down, north).
/// StampFly の NED（世界）／FRD（機体）→ Unity。Unity は (東, −下, 北) を持つ。
inline Vec3 polar_to_unity(const Vec3& v) { return {v.y, -v.z, v.x}; }

/// Unity → StampFly NED (world) or FRD (body). Reads back (north, east, −up).
/// Unity → StampFly の NED（世界）／FRD（機体）。(北, 東, −上) を読み戻す。
inline Vec3 polar_from_unity(const Vec3& u) { return {u.z, u.x, -u.y}; }

// =============================================================================
// Axial vectors — angular velocity, torque. One extra sign vs. the polar case
// because det(M) = −1 (see the file header):  u = −M·v,  v = −Mᵀ·u.
// 軸性ベクトル — 角速度・トルク。det(M) = −1 のため極性より符号が 1 つ余分に付く。
// =============================================================================

/// StampFly NED (world) or FRD (body) → Unity, with the handedness sign applied.
/// StampFly の NED／FRD → Unity。利き手の符号を適用する。
inline Vec3 axial_to_unity(const Vec3& w) { return {-w.y, w.z, -w.x}; }

/// Unity → StampFly NED (world) or FRD (body), with the handedness sign applied.
/// Unity → StampFly の NED／FRD。利き手の符号を適用する。
inline Vec3 axial_from_unity(const Vec3& u) { return {-u.z, -u.x, u.y}; }

// =============================================================================
// Named wrappers. Same two maps as above, spelled out per physical quantity so a
// call site reads as the quantity it moves and cannot pick the wrong polarity.
// 別名。写像は上の 2 つと同じだが、物理量ごとに書き下ろすことで、呼び出し側が
// 運ぶ量のまま読め、極性を取り違えられないようにする。
// =============================================================================

inline Vec3 position_to_unity(const Vec3& p_ned) { return polar_to_unity(p_ned); }
inline Vec3 position_from_unity(const Vec3& p_unity) { return polar_from_unity(p_unity); }

inline Vec3 velocity_to_unity(const Vec3& v_ned) { return polar_to_unity(v_ned); }
inline Vec3 velocity_from_unity(const Vec3& v_unity) { return polar_from_unity(v_unity); }

inline Vec3 force_to_unity(const Vec3& f_frd) { return polar_to_unity(f_frd); }
inline Vec3 force_from_unity(const Vec3& f_unity) { return polar_from_unity(f_unity); }

/// Accelerometer reading. Polar, so at rest on the ground the firmware's
/// FRD [0,0,−9.81] shows up in Unity as [0,+9.81,0] — pointing up.
/// 加速度計の測定値。極性なので、静止して接地しているとき、ファームの
/// FRD [0,0,−9.81] は Unity で [0,+9.81,0]（上向き）になる。
inline Vec3 accel_to_unity(const Vec3& a_frd) { return polar_to_unity(a_frd); }
inline Vec3 accel_from_unity(const Vec3& a_unity) { return polar_from_unity(a_unity); }

inline Vec3 angular_velocity_to_unity(const Vec3& w_frd) { return axial_to_unity(w_frd); }
inline Vec3 angular_velocity_from_unity(const Vec3& w_unity) { return axial_from_unity(w_unity); }

inline Vec3 torque_to_unity(const Vec3& t_frd) { return axial_to_unity(t_frd); }
inline Vec3 torque_from_unity(const Vec3& t_unity) { return axial_from_unity(t_unity); }

// =============================================================================
// Attitude quaternion. Unity stores (x,y,z,w); sf::math::Quat stores (w,x,y,z).
//   R_unity = M·R(q_nb)·Mᵀ   ⟺   unity(x,y,z,w) = (−q.y, q.z, −q.x, q.w)
// 姿勢クォータニオン。Unity は (x,y,z,w)、sf::math::Quat は (w,x,y,z) の順。
// =============================================================================

/// Unity rotation, components in Unity's own x,y,z,w order.
/// Unity の回転。成分は Unity 自身の x,y,z,w の順。
struct UnityQuat {
    float x = 0, y = 0, z = 0, w = 1;
};

/// Unity `Rigidbody.rotation` → firmware attitude q_nb (body FRD → NED).
/// Unity の `Rigidbody.rotation` → ファームの姿勢 q_nb（機体 FRD → NED）。
inline Quat qnb_from_unity(const UnityQuat& u) {
    Quat q{u.w, -u.z, -u.x, u.y};
    q.normalize();
    return q;
}

/// Firmware attitude q_nb → Unity `Rigidbody.rotation`.
/// ファームの姿勢 q_nb → Unity の `Rigidbody.rotation`。
inline UnityQuat qnb_to_unity(const Quat& q_nb) {
    UnityQuat u{-q_nb.y, q_nb.z, -q_nb.x, q_nb.w};
    const float norm = sqrtf(u.x * u.x + u.y * u.y + u.z * u.z + u.w * u.w);
    const float kMinNorm = 1e-10f;  // below this the input is not a rotation / これ未満は回転でない
    if (norm > kMinNorm) {
        u.x /= norm;
        u.y /= norm;
        u.z /= norm;
        u.w /= norm;
    }
    return u;
}

// =============================================================================
// Composite helper — the route the step function actually walks.
// 合成の補助 — 1 刻みの処理が実際に通る経路。
// =============================================================================

/// Unity world-frame `Rigidbody.angularVelocity` → firmware body rates (FRD).
/// Two steps: undo the handedness into NED world rates, then rotate into the
/// body with the inverse of q_nb (q_nb maps body→NED, so its inverse is NED→body).
/// Unity の世界系 `Rigidbody.angularVelocity` → ファームの機体レート（FRD）。
/// 2 段階: 利き手を戻して NED の世界レートにし、q_nb の逆回転で機体へ直す
/// （q_nb は body→NED なので、その逆が NED→body）。
inline Vec3 body_rates_from_unity_world(const Vec3& w_world_unity, const Quat& q_nb) {
    const Vec3 w_world_ned = axial_from_unity(w_world_unity);
    return q_nb.inv_rotate(w_world_ned);
}

/// Firmware body rates (FRD) → Unity world-frame angular velocity.
/// Inverse of body_rates_from_unity_world.
/// ファームの機体レート（FRD）→ Unity の世界系の角速度。
/// body_rates_from_unity_world の逆。
inline Vec3 body_rates_to_unity_world(const Vec3& w_body_frd, const Quat& q_nb) {
    const Vec3 w_world_ned = q_nb.rotate(w_body_frd);
    return axial_to_unity(w_world_ned);
}

}  // namespace unity
}  // namespace frames
}  // namespace sils
