/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — angular-momentum diagnostics).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Sim;
using UnityEngine;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// Traces the world-frame angular momentum through a free rotation, to tell
    /// apart three possibilities: PhysX integrates the gyroscopic term itself, it
    /// does not, or the vector is being read wrongly.
    ///
    /// 自由回転の間の世界座標系の角運動量を追い、3 つの可能性を切り分ける:
    /// PhysX がジャイロ項を自分で積分している、していない、あるいは読み取り方が
    /// 誤っている。
    /// </summary>
    public sealed class PhysXAngularMomentumProbe
    {
        private const float TraceSeconds = 0.2f;
        private const int SampleCount = 8;

        private PhysXVehicleScene scene;

        [TearDown]
        public void TearDown()
        {
            scene?.Dispose();
            scene = null;
        }

        /// <summary>
        /// Prints the world-frame L and the body rate at several instants of a free
        /// rotation. Under Euler's equation the body rate must wander while the
        /// world L stays put.
        /// 自由回転の何点かで、世界座標系の L と機体座標系の角速度を出力する。
        /// オイラーの式では機体の角速度は動き、世界の L は止まっているはず。
        /// </summary>
        [Test]
        public void TraceWorldAngularMomentum()
        {
            scene = new PhysXVehicleScene();
            scene.Build(Vector3.zero, withFloor: false, useGravity: false);
            scene.Body.angularVelocity = new Vector3(20.0f, 0.0f, 30.0f);

            int stepsPerSample = Mathf.RoundToInt(
                TraceSeconds / SampleCount / PhysicsStepSettings.StepSeconds);

            for (int sample = 0; sample <= SampleCount; sample++)
            {
                Vector3 worldMomentum = GyroscopicTerm.WorldAngularMomentum(scene.Body);
                Vector3 bodyRate =
                    Quaternion.Inverse(scene.Body.rotation) * scene.Body.angularVelocity;
                Debug.Log(
                    $"[probeE] t={sample * stepsPerSample * PhysicsStepSettings.StepSeconds:F4} " +
                    $"L_world=({worldMomentum.x:E5},{worldMomentum.y:E5},{worldMomentum.z:E5}) " +
                    $"|L|={worldMomentum.magnitude:E6} " +
                    $"bodyRate=({bodyRate.x:F4},{bodyRate.y:F4},{bodyRate.z:F4}) " +
                    $"worldRate={scene.Body.angularVelocity:F4}");

                if (sample == SampleCount)
                {
                    break;
                }

                for (int step = 0; step < stepsPerSample; step++)
                {
                    scene.Step();
                }
            }
        }

        /// <summary>
        /// The same trace with the explicit -omega x (I*omega) torque added, so the
        /// two can be compared side by side.
        /// 同じ追跡を、-omega x (I*omega) を明示的に足した場合について行い、
        /// 並べて比べられるようにする。
        /// </summary>
        [Test]
        public void TraceWorldAngularMomentumWithExplicitTerm()
        {
            scene = new PhysXVehicleScene();
            scene.Build(Vector3.zero, withFloor: false, useGravity: false);
            scene.Body.angularVelocity = new Vector3(20.0f, 0.0f, 30.0f);

            int stepsPerSample = Mathf.RoundToInt(
                TraceSeconds / SampleCount / PhysicsStepSettings.StepSeconds);

            for (int sample = 0; sample <= SampleCount; sample++)
            {
                Vector3 worldMomentum = GyroscopicTerm.WorldAngularMomentum(scene.Body);
                Vector3 bodyRate =
                    Quaternion.Inverse(scene.Body.rotation) * scene.Body.angularVelocity;
                Debug.Log(
                    $"[probeF] t={sample * stepsPerSample * PhysicsStepSettings.StepSeconds:F4} " +
                    $"L_world=({worldMomentum.x:E5},{worldMomentum.y:E5},{worldMomentum.z:E5}) " +
                    $"|L|={worldMomentum.magnitude:E6} " +
                    $"bodyRate=({bodyRate.x:F4},{bodyRate.y:F4},{bodyRate.z:F4})");

                if (sample == SampleCount)
                {
                    break;
                }

                for (int step = 0; step < stepsPerSample; step++)
                {
                    Vector3 rate =
                        Quaternion.Inverse(scene.Body.rotation) * scene.Body.angularVelocity;
                    scene.Body.AddRelativeTorque(
                        GyroscopicTerm.BodyTorque(rate, scene.Body.inertiaTensor),
                        ForceMode.Force);
                    scene.Step();
                }
            }
        }

        /// <summary>
        /// Reports whether <c>Rigidbody.angularVelocity</c> is expressed in the world
        /// frame, by rotating the body 90 degrees and reading it back unchanged.
        /// 機体を 90 度回して読み直し、<c>Rigidbody.angularVelocity</c> が世界座標系
        /// かどうかを確かめる。
        /// </summary>
        [Test]
        public void ReportAngularVelocityFrame()
        {
            scene = new PhysXVehicleScene();
            scene.Build(Vector3.zero, withFloor: false, useGravity: false);
            scene.Body.angularVelocity = new Vector3(0.0f, 0.0f, 10.0f);
            Vector3 before = scene.Body.angularVelocity;

            scene.Body.rotation = Quaternion.Euler(0.0f, 90.0f, 0.0f);
            Vector3 after = scene.Body.angularVelocity;

            Debug.Log(
                $"[probeG] angularVelocity before rotation={before:F4}, " +
                $"after a 90 deg yaw={after:F4} " +
                $"(unchanged means world frame)");
        }
    }
}
