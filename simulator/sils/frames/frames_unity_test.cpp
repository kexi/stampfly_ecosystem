/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (SILS host bench).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file frames_unity_test.cpp
 * @brief Canonical unit tests for the Unity ↔ StampFly frame mapping.
 *        Unity ↔ StampFly 座標対応の正準単体試験。
 *
 * These tests ARE the verification of frames_unity.hpp
 * (coordinate_frames.md §7). If they pass, the single Unity coordinate
 * transform is correct. Depends only on frames_unity.hpp and sf_math — no MuJoCo,
 * so it builds standalone with /usr/bin/clang++.
 *
 * これらの試験が Unity ↔ StampFly 対応の検証（coordinate_frames.md §7）。
 * 通れば、Unity 側で唯一の座標変換が正しい。依存は frames_unity.hpp と sf_math
 * だけで MuJoCo を含まないため、/usr/bin/clang++ で単体でビルドできる。
 *
 * Build & run / ビルドと実行:
 *   /usr/bin/clang++ -std=c++17 -O2 \
 *     -I simulator/sils/frames \
 *     -I firmware/vehicle/components/sf_math/include \
 *     -o /tmp/frames_unity_test simulator/sils/frames/frames_unity_test.cpp
 *   /tmp/frames_unity_test
 */

#include <cmath>
#include <cstdio>

#include "frames_unity.hpp"

using sf::math::Quat;
using sf::math::Vec3;
namespace U = sils::frames::unity;
using U::UnityQuat;

namespace {

int g_failures = 0;

void check(bool ok, const char* name)
{
    printf("  [%s] %s\n", ok ? "PASS" : "FAIL", name);
    if (!ok) ++g_failures;
}

bool close(float a, float b, float tol = 1e-4f) { return std::fabs(a - b) < tol; }

bool vclose(const Vec3& a, const Vec3& b, float tol = 1e-4f)
{
    return close(a.x, b.x, tol) && close(a.y, b.y, tol) && close(a.z, b.z, tol);
}

// Quaternions q and −q are the same rotation; compare up to sign.
// q と −q は同じ回転。符号を無視して比較。
bool qclose_rot(const Quat& a, const Quat& b, float tol = 1e-4f)
{
    bool same = close(a.w, b.w, tol) && close(a.x, b.x, tol) &&
                close(a.y, b.y, tol) && close(a.z, b.z, tol);
    bool neg  = close(a.w, -b.w, tol) && close(a.x, -b.x, tol) &&
                close(a.y, -b.y, tol) && close(a.z, -b.z, tol);
    return same || neg;
}

bool uqclose_rot(const UnityQuat& a, const UnityQuat& b, float tol = 1e-4f)
{
    bool same = close(a.x, b.x, tol) && close(a.y, b.y, tol) &&
                close(a.z, b.z, tol) && close(a.w, b.w, tol);
    bool neg  = close(a.x, -b.x, tol) && close(a.y, -b.y, tol) &&
                close(a.z, -b.z, tol) && close(a.w, -b.w, tol);
    return same || neg;
}

// The handedness-flip matrix M of the file header, applied to a component tuple.
// It is written out here rather than reused from the header, so the test checks
// the header's closed-form expressions against the matrix they claim to encode.
// ヘッダ冒頭の利き手反転行列 M を成分の組に適用する。ヘッダから流用せず書き下ろし、
// ヘッダの閉じた式が、根拠としている行列と一致するかを試験する。
Vec3 apply_m(const Vec3& v) { return {v.y, -v.z, v.x}; }

// Rotation matrix of a quaternion (same formula as sf::math::Quat::to_dcm).
// クォータニオンの回転行列（sf::math::Quat::to_dcm と同じ式）。
struct Mat3 {
    float m[3][3] = {};
};

Mat3 dcm_of(const Quat& q)
{
    Mat3 r;
    q.to_dcm(r.m);
    return r;
}

// R_unity = M · R · Mᵀ, with M orthogonal so Mᵀ = M⁻¹.
// Applying M to each column, then to each row, is the same product.
// R_unity = M · R · Mᵀ。M は直交なので Mᵀ = M⁻¹。
// 各列に M を掛け、続けて各行に掛けるのが、この積と同じ。
Mat3 conjugate_by_m(const Mat3& r)
{
    Mat3 columns_mapped;
    for (int col = 0; col < 3; ++col) {
        const Vec3 mapped = apply_m(Vec3{r.m[0][col], r.m[1][col], r.m[2][col]});
        columns_mapped.m[0][col] = mapped.x;
        columns_mapped.m[1][col] = mapped.y;
        columns_mapped.m[2][col] = mapped.z;
    }
    // Right-multiplying by Mᵀ maps each ROW through M in the same way.
    // 右から Mᵀ を掛けるのは、各行を同じように M で写すことに等しい。
    Mat3 out;
    for (int row = 0; row < 3; ++row) {
        const Vec3 mapped = apply_m(Vec3{columns_mapped.m[row][0],
                                         columns_mapped.m[row][1],
                                         columns_mapped.m[row][2]});
        out.m[row][0] = mapped.x;
        out.m[row][1] = mapped.y;
        out.m[row][2] = mapped.z;
    }
    return out;
}

bool mclose(const Mat3& a, const Mat3& b, float tol = 1e-4f)
{
    for (int row = 0; row < 3; ++row) {
        for (int col = 0; col < 3; ++col) {
            if (!close(a.m[row][col], b.m[row][col], tol)) return false;
        }
    }
    return true;
}

// Read a Unity quaternion with the ordinary Hamilton to_dcm formula. Valid here
// because Unity's left-hand rule and the det = −1 flip cancel (file header).
// Unity のクォータニオンを通常の Hamilton の to_dcm の式で読む。Unity の左手則と
// det = −1 の反転が打ち消し合うため、ここではこれで正しい（ヘッダ冒頭）。
Mat3 dcm_of_unity(const UnityQuat& u) { return dcm_of(Quat{u.w, u.x, u.y, u.z}); }

// Standard gravity magnitude used by the resting-accelerometer cases [m/s²].
// 静止時の加速度計の事例で使う標準重力の大きさ [m/s²]。
constexpr float kGravityMagnitude = 9.81f;

// A fixed set of attitudes (NOT random, so a failure is always reproducible).
// Rotation vectors in rad: level, pure roll/pitch/yaw, and three mixed ones.
// 固定の姿勢の組（乱数でないので、失敗は必ず再現する）。
// 単位は rad の回転ベクトル: 水平、純ロール／ピッチ／ヨー、混合 3 つ。
const Vec3 kFixedRotationVectors[] = {
    {0.0f, 0.0f, 0.0f},
    {0.35f, 0.0f, 0.0f},
    {0.0f, -0.62f, 0.0f},
    {0.0f, 0.0f, 2.10f},
    {0.21f, 0.48f, -0.33f},
    {-1.15f, 0.27f, 0.90f},
    {0.80f, -1.40f, 2.60f},
};

}  // namespace

int main()
{
    printf("[frames_unity_test] canonical Unity <-> StampFly cases\n");

    // -------------------------------------------------------------------------
    // 1. Polar vectors: the axis correspondence, both directions, and round trip.
    //    north→+Z, east→+X, down→−Y.
    // 1. 極性ベクトル: 軸の対応の両方向と往復。北→+Z、東→+X、下→−Y。
    // -------------------------------------------------------------------------
    {
        // 1 m north, 2 m east, 3 m down → Unity (east, −down, north) = (2, −3, 1).
        // 北 1 m・東 2 m・下 3 m → Unity は (東, −下, 北) = (2, −3, 1)。
        const Vec3 p_ned{1.0f, 2.0f, 3.0f};
        check(vclose(U::position_to_unity(p_ned), Vec3{2.0f, -3.0f, 1.0f}),
              "position NED(1,2,3) -> Unity(2,-3,1)");
        check(vclose(U::position_from_unity(U::position_to_unity(p_ned)), p_ned),
              "position Unity -> NED round-trip");

        // Unit axes, one at a time, so a single wrong sign cannot hide.
        // 単位軸を 1 本ずつ。1 つの符号違いが隠れないようにする。
        check(vclose(U::polar_to_unity(Vec3{1, 0, 0}), Vec3{0, 0, 1}), "north -> Unity +Z");
        check(vclose(U::polar_to_unity(Vec3{0, 1, 0}), Vec3{1, 0, 0}), "east  -> Unity +X");
        check(vclose(U::polar_to_unity(Vec3{0, 0, 1}), Vec3{0, -1, 0}), "down  -> Unity -Y");

        const Vec3 v_unity{-4.5f, 0.25f, 7.0f};
        check(vclose(U::polar_to_unity(U::polar_from_unity(v_unity)), v_unity),
              "polar Unity -> NED -> Unity round-trip");

        // Same map for body FRD: forward→+Z, right→+X, down→−Y.
        // 機体 FRD も同じ写像: 前→+Z、右→+X、下→−Y。
        check(vclose(U::force_to_unity(Vec3{1, 2, 3}), Vec3{2, -3, 1}),
              "force FRD(1,2,3) -> Unity(2,-3,1)");
        check(vclose(U::force_from_unity(U::force_to_unity(Vec3{1, 2, 3})), Vec3{1, 2, 3}),
              "force round-trip");
    }

    // -------------------------------------------------------------------------
    // 2. Axial vectors: one extra sign vs. polar, both directions, round trip.
    // 2. 軸性ベクトル: 極性より符号が 1 つ余分。両方向と往復。
    // -------------------------------------------------------------------------
    {
        const Vec3 w_frd{1.0f, 2.0f, 3.0f};
        // Polar would be (2,−3,1); axial negates every component of that.
        // 極性なら (2,−3,1)。軸性はその全成分を反転する。
        check(vclose(U::axial_to_unity(w_frd), Vec3{-2.0f, 3.0f, -1.0f}),
              "axial FRD(1,2,3) -> Unity(-2,3,-1) (polar negated)");
        check(vclose(U::axial_from_unity(U::axial_to_unity(w_frd)), w_frd),
              "axial FRD -> Unity -> FRD round-trip");

        const Vec3 w_unity{0.5f, -1.25f, 3.5f};
        check(vclose(U::axial_to_unity(U::axial_from_unity(w_unity)), w_unity),
              "axial Unity -> FRD -> Unity round-trip");

        check(vclose(U::torque_to_unity(w_frd), U::axial_to_unity(w_frd)),
              "torque uses the axial map");
        check(vclose(U::angular_velocity_to_unity(w_frd), U::axial_to_unity(w_frd)),
              "angular velocity uses the axial map");

        // The axial map differs from the polar map by exactly a global sign.
        // 軸性の写像は、極性の写像と全体の符号 1 つだけ違う。
        const Vec3 polar = U::polar_to_unity(w_frd);
        check(vclose(U::axial_to_unity(w_frd), polar * -1.0f),
              "axial = -(polar) for the same components");
    }

    // -------------------------------------------------------------------------
    // 3. Quaternion vs. rotation matrix: M·R(q_nb)·Mᵀ must equal the matrix read
    //    off the mapped Unity quaternion. Fixed attitudes, not random.
    // 3. クォータニオンと回転行列の一致: M·R(q_nb)·Mᵀ が、写した Unity の
    //    クォータニオンから読む行列と一致すること。固定の姿勢で確かめる。
    // -------------------------------------------------------------------------
    {
        bool all_ok = true;
        for (const Vec3& rotation_vector : kFixedRotationVectors) {
            Quat q_nb = Quat::from_rotvec(rotation_vector);
            q_nb.normalize();
            const Mat3 expected = conjugate_by_m(dcm_of(q_nb));
            const Mat3 actual   = dcm_of_unity(U::qnb_to_unity(q_nb));
            if (!mclose(expected, actual)) all_ok = false;
        }
        check(all_ok, "M*R(q_nb)*M^T == R(unity quat) for 7 fixed attitudes");
    }

    // -------------------------------------------------------------------------
    // 4. Quaternion round trip, both starting sides.
    // 4. クォータニオンの往復（両方の起点から）。
    // -------------------------------------------------------------------------
    {
        bool all_ok = true;
        for (const Vec3& rotation_vector : kFixedRotationVectors) {
            Quat q_nb = Quat::from_rotvec(rotation_vector);
            q_nb.normalize();
            const Quat back = U::qnb_from_unity(U::qnb_to_unity(q_nb));
            if (!qclose_rot(q_nb, back)) all_ok = false;
        }
        check(all_ok, "q_nb -> Unity -> q_nb round-trip (7 fixed attitudes)");

        // Level and north-facing: identity on both sides.
        // 水平・北向き: 双方で単位元。
        const Quat identity{1, 0, 0, 0};
        check(uqclose_rot(U::qnb_to_unity(identity), UnityQuat{0, 0, 0, 1}),
              "level/north q_nb = identity -> Unity identity");

        const UnityQuat u_in{0.3f, -0.5f, 0.1f, 0.8f};
        const UnityQuat u_back = U::qnb_to_unity(U::qnb_from_unity(u_in));
        // Compare after normalizing the input, since qnb_from_unity normalizes.
        // qnb_from_unity は正規化するので、入力を正規化してから比べる。
        const float u_norm = std::sqrt(u_in.x * u_in.x + u_in.y * u_in.y +
                                       u_in.z * u_in.z + u_in.w * u_in.w);
        const UnityQuat u_expected{u_in.x / u_norm, u_in.y / u_norm,
                                   u_in.z / u_norm, u_in.w / u_norm};
        check(uqclose_rot(u_back, u_expected), "Unity -> q_nb -> Unity round-trip");
    }

    // -------------------------------------------------------------------------
    // 5. Concrete sign cases from the spec.
    // 5. 仕様に挙げた符号の具体例。
    // -------------------------------------------------------------------------
    {
        // Right yaw: q_nb = (cos, 0, 0, sin) is a rotation about NED +Z (down),
        // i.e. nose to the right. In Unity that is a rotation about +Y (up), and
        // Unity's left-hand rule makes a +Y rotation turn the nose right too.
        // 右ヨー: q_nb = (cos, 0, 0, sin) は NED の +Z（下）まわり、つまり機首が右。
        // Unity では +Y（上）まわりで、左手則により +Y の回転も機首を右へ向ける。
        const float yaw_angle = 0.6f;  // [rad]
        const float half_cos  = std::cos(yaw_angle * 0.5f);
        const float half_sin  = std::sin(yaw_angle * 0.5f);
        const Quat q_yaw_right{half_cos, 0.0f, 0.0f, half_sin};
        const UnityQuat u_yaw = U::qnb_to_unity(q_yaw_right);
        check(uqclose_rot(u_yaw, UnityQuat{0.0f, half_sin, 0.0f, half_cos}),
              "right yaw q_nb=(cos,0,0,sin) -> Unity rotation about +Y");

        // Right roll: a positive body rate about FRD +X (forward) appears in Unity
        // as a rate about −Z (Unity +Z is forward; the axial sign flips it).
        // 右ロール: FRD の +X（前）まわりの正のレートは、Unity では −Z まわりに
        // 見える（Unity の +Z が前で、軸性の符号で反転する）。
        check(vclose(U::angular_velocity_to_unity(Vec3{1, 0, 0}), Vec3{0, 0, -1}),
              "right roll +wx(FRD) -> Unity -Z");

        // Nose up: a positive body rate about FRD +Y (right) appears as −X.
        // 機首上げ: FRD の +Y（右）まわりの正のレートは −X に見える。
        check(vclose(U::angular_velocity_to_unity(Vec3{0, 1, 0}), Vec3{-1, 0, 0}),
              "nose up +wy(FRD) -> Unity -X");

        // Right yaw rate: a positive body rate about FRD +Z (down) appears as +Y.
        // 右ヨーのレート: FRD の +Z（下）まわりの正のレートは +Y に見える。
        check(vclose(U::angular_velocity_to_unity(Vec3{0, 0, 1}), Vec3{0, 1, 0}),
              "right yaw +wz(FRD) -> Unity +Y");
    }

    // -------------------------------------------------------------------------
    // 6. Accelerometer at rest on the ground. The firmware's driver-normalized
    //    reading is FRD [0,0,−9.81]; in Unity it must point up, [0,+9.81,0].
    // 6. 静止して接地しているときの加速度計。ファームのドライバ正規化後の測定値は
    //    FRD [0,0,−9.81]。Unity では上向きの [0,+9.81,0] でなければならない。
    // -------------------------------------------------------------------------
    {
        const Vec3 accel_frd{0.0f, 0.0f, -kGravityMagnitude};
        const Vec3 accel_unity = U::accel_to_unity(accel_frd);
        check(vclose(accel_unity, Vec3{0.0f, kGravityMagnitude, 0.0f}),
              "resting accel FRD(0,0,-9.81) -> Unity(0,+9.81,0)");
        check(vclose(U::accel_from_unity(accel_unity), accel_frd),
              "resting accel Unity(0,+9.81,0) -> FRD(0,0,-9.81)");
        check(close(accel_unity.norm(), kGravityMagnitude),
              "accel magnitude preserved (orthogonal map)");
    }

    // -------------------------------------------------------------------------
    // 7. World angular velocity route — what the step function walks:
    //    Unity `Rigidbody.angularVelocity` (world) → body rates (FRD) via q_nb.
    // 7. 世界系の角速度の経路 — 1 刻みの処理が実際に通る道:
    //    Unity の `Rigidbody.angularVelocity`（世界系）→ q_nb で機体レート（FRD）。
    // -------------------------------------------------------------------------
    {
        // Level attitude: world and body coincide, so the route is the axial map.
        // 水平姿勢: 世界系と機体系が一致するので、経路は軸性の写像そのもの。
        const Quat identity{1, 0, 0, 0};
        check(vclose(U::body_rates_from_unity_world(Vec3{0, 1, 0}, identity), Vec3{0, 0, 1}),
              "level: Unity world +Y rate -> body FRD +wz (right yaw)");

        // Rotated attitudes: the two directions must undo each other exactly.
        // 傾いた姿勢: 2 つの向きが厳密に打ち消し合うこと。
        bool all_ok = true;
        const Vec3 body_rates{0.70f, -0.20f, 1.10f};
        for (const Vec3& rotation_vector : kFixedRotationVectors) {
            Quat q_nb = Quat::from_rotvec(rotation_vector);
            q_nb.normalize();
            const Vec3 w_unity_world = U::body_rates_to_unity_world(body_rates, q_nb);
            const Vec3 back = U::body_rates_from_unity_world(w_unity_world, q_nb);
            if (!vclose(back, body_rates, 1e-3f)) all_ok = false;
        }
        check(all_ok, "body rates -> Unity world -> body rates (7 fixed attitudes)");

        // Spin about the world down axis with the vehicle banked: the magnitude
        // must survive, since every map here is orthogonal.
        // 機体を傾けたまま世界系の下軸まわりに回す: 写像が全て直交なので
        // 大きさが保たれること。
        Quat q_banked = Quat::from_rotvec(Vec3{0.45f, 0.30f, -0.80f});
        q_banked.normalize();
        const Vec3 w_body = U::body_rates_from_unity_world(Vec3{0.0f, 2.0f, 0.0f}, q_banked);
        check(close(w_body.norm(), 2.0f, 1e-3f), "|body rates| preserved from world rates");
    }

    printf("[frames_unity_test] %s (%d failure%s)\n",
           g_failures == 0 ? "ALL PASS" : "FAILED",
           g_failures, g_failures == 1 ? "" : "s");
    return g_failures == 0 ? 0 : 1;
}
