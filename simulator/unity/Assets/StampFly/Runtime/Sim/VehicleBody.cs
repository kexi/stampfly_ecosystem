/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — vehicle rigid body).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine;

namespace StampFly.Sim
{
    /// <summary>
    /// Configures the vehicle's <see cref="Rigidbody"/> so PhysX integrates the same
    /// rigid body the MuJoCo model describes (simulator/sils/models/stampfly.xml).
    /// Mass, inertia and the collision box come from that model; the axis mapping
    /// turns its FLU body frame into Unity's left-handed frame.
    ///
    /// Unity の <see cref="Rigidbody"/> を、MuJoCo モデル
    /// （simulator/sils/models/stampfly.xml）と同じ剛体になるように設定する。
    /// 質量・慣性・衝突箱は同モデルの値で、軸の割り当てで FLU から Unity の
    /// 左手系へ移す。
    /// </summary>
    [RequireComponent(typeof(Rigidbody))]
    [RequireComponent(typeof(BoxCollider))]
    public sealed class VehicleBody : MonoBehaviour
    {
        // Mass of the real StampFly, in kg. / 実機 StampFly の質量（kg）。
        public const float MassKilograms = 0.037f;

        // Principal moments of inertia in the body FLU frame, in kg*m^2.
        // 機体 FLU 座標系での主慣性モーメント（kg*m^2）。
        public const float InertiaForwardAxis = 9.16e-6f;   // Ixx (FLU x = forward)
        public const float InertiaLeftAxis = 13.3e-6f;      // Iyy (FLU y = left)
        public const float InertiaUpAxis = 20.4e-6f;        // Izz (FLU z = up)

        // Full extents of the collision box, in m. The MuJoCo geom stores half
        // extents 0.0408 / 0.0408 / 0.0103 in FLU order.
        // 衝突箱の全長（m）。MuJoCo は FLU 順の半長 0.0408 / 0.0408 / 0.0103 を持つ。
        public const float BoxSizeRight = 0.0816f;  // Unity x, from FLU y (left)
        public const float BoxSizeUp = 0.0206f;     // Unity y, from FLU z (up)
        public const float BoxSizeForward = 0.0816f; // Unity z, from FLU x (forward)

        // Half the box thickness: the centre height of a body resting on the floor.
        // 箱の厚みの半分。床に載った機体の中心の高さ。
        public const float RestingCentreHeight = 0.5f * BoxSizeUp;

        // No aerodynamic damping is applied by PhysX: drag belongs to the C++ plant.
        // PhysX 側では空気抵抗を掛けない。抵抗は C++ のプラントが担う。
        public const float NoDamping = 0.0f;

        // Never let PhysX clamp the angular velocity: a 37 g body reaches high rates.
        // PhysX に角速度を頭打ちにさせない。37 g の機体は高い角速度に達する。
        public const float AngularSpeedLimitRadPerSecond = 1.0e6f;

        // Sleeping would freeze the body mid-simulation, so it is disabled.
        // 途中で休止されると積分が止まるため、休止を無効にする。
        public const float NeverSleepThreshold = 0.0f;

        /// <summary>
        /// The inertia tensor in Unity axis order (x right, y up, z forward),
        /// built from the FLU principal moments.
        /// Unity の軸順（x 右・y 上・z 前）に並べ替えた慣性テンソル。
        /// </summary>
        public static Vector3 InertiaTensorUnityAxes =>
            new Vector3(InertiaLeftAxis, InertiaUpAxis, InertiaForwardAxis);

        /// <summary>The collision box's full size in Unity axis order. / 衝突箱の全長。</summary>
        public static Vector3 BoxSizeUnityAxes =>
            new Vector3(BoxSizeRight, BoxSizeUp, BoxSizeForward);

        private void Awake()
        {
            Apply(GetComponent<Rigidbody>(), GetComponent<BoxCollider>());
        }

        /// <summary>
        /// Writes every physical property onto the body and its collider.
        /// Callers that build the vehicle from code (tests) call this directly.
        /// 剛体とコライダに物理量を全て書き込む。コードから機体を組み立てる側
        /// （試験）はこれを直接呼ぶ。
        /// </summary>
        public static void Apply(Rigidbody body, BoxCollider box)
        {
            body.mass = MassKilograms;
            body.automaticCenterOfMass = false;
            body.centerOfMass = Vector3.zero;
            body.automaticInertiaTensor = false;
            body.inertiaTensorRotation = Quaternion.identity;
            body.inertiaTensor = InertiaTensorUnityAxes;
            body.linearDamping = NoDamping;
            body.angularDamping = NoDamping;
            body.maxAngularVelocity = AngularSpeedLimitRadPerSecond;
            body.sleepThreshold = NeverSleepThreshold;
            body.useGravity = true;
            body.interpolation = RigidbodyInterpolation.None;
            body.collisionDetectionMode = CollisionDetectionMode.ContinuousSpeculative;

            box.center = Vector3.zero;
            box.size = BoxSizeUnityAxes;
        }
    }
}
