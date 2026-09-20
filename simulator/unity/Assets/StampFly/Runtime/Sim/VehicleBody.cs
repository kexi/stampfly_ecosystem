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
    /// Mass, inertia and the collision box all come from
    /// <see cref="GeneratedParams"/>, which the repository generates from
    /// control/models/stampfly_physical.yaml — the same file the MuJoCo model and
    /// the SILS C++ plant are checked against, so the two simulators cannot drift
    /// apart. The axis mapping from the FLU body frame into Unity's left-handed
    /// frame is applied in that generated file, not here.
    ///
    /// Unity の <see cref="Rigidbody"/> を、MuJoCo モデル
    /// （simulator/sils/models/stampfly.xml）と同じ剛体になるように設定する。
    /// 質量・慣性・衝突箱はいずれも <see cref="GeneratedParams"/> から来る。同
    /// クラスは control/models/stampfly_physical.yaml から生成され、MuJoCo モデル
    /// と SILS の C++ プラントも同じファイルに対して照合されるため、2 つの
    /// シミュレータが離れていくことがない。FLU から Unity の左手系への軸の割り当ては
    /// ここではなく、その生成物の中で行う。
    /// </summary>
    [RequireComponent(typeof(Rigidbody))]
    [RequireComponent(typeof(BoxCollider))]
    public sealed class VehicleBody : MonoBehaviour
    {
        // No aerodynamic damping is applied by PhysX: drag belongs to the C++ plant.
        // PhysX 側では空気抵抗を掛けない。抵抗は C++ のプラントが担う。
        public const float NoDamping = 0.0f;

        // Never let PhysX clamp the angular velocity: a 37 g body reaches high rates.
        // PhysX に角速度を頭打ちにさせない。37 g の機体は高い角速度に達する。
        public const float AngularSpeedLimitRadPerSecond = 1.0e6f;

        // Sleeping would freeze the body mid-simulation, so it is disabled.
        // 途中で休止されると積分が止まるため、休止を無効にする。
        public const float NeverSleepThreshold = 0.0f;

        // The vehicle's own physical quantities are not written here: they are
        // named here only so a caller can keep asking the body for them, and
        // every one forwards to the generated class. Changing a value means
        // editing control/models/stampfly_physical.yaml and running
        // `sf params generate`, never editing a number in this file.
        // 機体そのものの物理量はここには書かない。呼び出し側が剛体に尋ね続け
        // られるよう名前だけを置き、いずれも生成されたクラスへ委ねる。値を変える
        // には control/models/stampfly_physical.yaml を編集して
        // `sf params generate` を実行する。このファイルの数値を書き換えるのではない。

        /// <summary>Mass of the real StampFly [kg]. / 実機 StampFly の質量 [kg]。</summary>
        public const float MassKilograms = GeneratedParams.MassKilograms;

        /// <summary>
        /// The inertia tensor in Unity axis order (x right, y up, z forward),
        /// built from the FLU principal moments.
        /// Unity の軸順（x 右・y 上・z 前）に並べ替えた慣性テンソル。
        /// </summary>
        public static Vector3 InertiaTensorUnityAxes => GeneratedParams.InertiaTensorUnityAxes;

        /// <summary>The collision box's full size in Unity axis order. / 衝突箱の全長。</summary>
        public static Vector3 BoxSizeUnityAxes => GeneratedParams.BoxSizeUnityAxes;

        /// <summary>
        /// Half the box thickness: the centre height of a body resting on the floor.
        /// 箱の厚みの半分。床に載った機体の中心の高さ。
        /// </summary>
        public const float RestingCentreHeight = GeneratedParams.RestingCentreHeightMeters;

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
