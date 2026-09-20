/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — stage 1(c) PhysX checks).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Collections.Generic;
using NUnit.Framework;
using StampFly.Sim;
using UnityEngine;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// What these guarantee: PhysX can carry a 37 g body with inertia on the order
    /// of 1e-5 kg*m^2 at 400 Hz — the torque response matches tau/I, a landing
    /// settles at the box's half thickness, halving the step does not change the
    /// trajectory, the gyroscopic term is accounted for exactly once, the
    /// accelerometer reads +9.81 up at rest, and two identical runs agree.
    ///
    /// 保証すること: PhysX が質量 37 g・慣性 1e-5 桁の剛体を 400 Hz で扱えること。
    /// トルク応答が tau/I と一致し、着地が箱の厚みの半分で静止し、刻みを半分に
    /// しても軌跡が変わらず、ジャイロ項がちょうど 1 回だけ数えられ、静止時の
    /// 加速度計が上向き +9.81 になり、同じ入力の 2 回の実行が一致すること。
    /// </summary>
    public sealed class PhysXStabilityTest
    {
        // The plan's tolerance for the angular-acceleration check.
        // 角加速度の検証に対する計画の許容範囲。
        private const double TorqueTolerancePercent = 1.0;

        // A torque that spins the body fast enough to measure cleanly but stays far
        // from any solver limit: chosen so tau/I is about 100 rad/s^2 on each axis.
        // 測りやすく、かつソルバの限界から十分遠いトルク。各軸で tau/I が
        // 100 rad/s^2 ほどになるように選ぶ。
        private const float TargetAngularAccelerationRadPerSecondSquared = 100.0f;

        private const int TorqueStepCount = 40;
        private const float DropHeightMeters = 0.5f;
        private const float SettleWindowSeconds = 2.0f;

        private PhysXVehicleScene scene;

        [TearDown]
        public void TearDown()
        {
            scene?.Dispose();
            scene = null;
        }

        /// <summary>
        /// Check 1: a known torque about each axis produces tau/I within 1%.
        /// 検証 1: 各軸に既知のトルクを掛けると角加速度が tau/I の 1% 以内に入る。
        /// </summary>
        [Test]
        public void TorqueProducesExpectedAngularAcceleration(
            [Values(0, 1, 2)] int axisIndex)
        {
            scene = new PhysXVehicleScene();
            scene.Build(Vector3.zero, withFloor: false, useGravity: false);

            Vector3 inertia = VehicleBody.InertiaTensorUnityAxes;
            float inertiaOnAxis = inertia[axisIndex];
            float torqueMagnitude =
                inertiaOnAxis * TargetAngularAccelerationRadPerSecondSquared;

            Vector3 torque = Vector3.zero;
            torque[axisIndex] = torqueMagnitude;

            for (int step = 0; step < TorqueStepCount; step++)
            {
                scene.Body.AddRelativeTorque(torque, ForceMode.Force);
                scene.Step();
            }

            float elapsed = TorqueStepCount * PhysicsStepSettings.StepSeconds;
            float measuredRate = scene.Body.angularVelocity[axisIndex];
            double measured = measuredRate / elapsed;
            double expected = torqueMagnitude / inertiaOnAxis;
            double errorPercent = 100.0 * (measured - expected) / expected;

            Debug.Log(
                $"[check1] axis={axisIndex} I={inertiaOnAxis:E4} tau={torqueMagnitude:E4} " +
                $"expected={expected:F6} measured={measured:F6} error={errorPercent:F4}%");

            Assert.That(errorPercent, Is.EqualTo(0.0).Within(TorqueTolerancePercent));
        }

        /// <summary>
        /// Check 2: dropped from 0.5 m the body comes to rest near the box's half
        /// thickness and stops moving.
        /// 検証 2: 0.5 m から落とすと箱の厚みの半分の付近で静止し、動かなくなる。
        /// </summary>
        [Test]
        public void DropSettlesAtHalfBoxThickness()
        {
            scene = new PhysXVehicleScene();
            scene.Build(new Vector3(0.0f, DropHeightMeters, 0.0f),
                withFloor: true, useGravity: true);

            float freeFallSeconds = Mathf.Sqrt(
                2.0f * (DropHeightMeters - VehicleBody.RestingCentreHeight) /
                AccelerometerModel.GravityMetersPerSecondSquared);
            int fallSteps =
                Mathf.CeilToInt(freeFallSeconds / PhysicsStepSettings.StepSeconds);
            for (int step = 0; step < fallSteps; step++)
            {
                scene.Step();
            }

            int settleSteps =
                Mathf.CeilToInt(SettleWindowSeconds / PhysicsStepSettings.StepSeconds);
            float lowest = float.MaxValue;
            float highest = float.MinValue;
            for (int step = 0; step < settleSteps; step++)
            {
                scene.Step();
                float height = scene.Body.position.y;
                lowest = Mathf.Min(lowest, height);
                highest = Mathf.Max(highest, height);
            }

            float finalHeight = scene.Body.position.y;
            float spread = highest - lowest;

            Debug.Log(
                $"[check2] resting={VehicleBody.RestingCentreHeight:F6} " +
                $"final={finalHeight:F6} min={lowest:F6} max={highest:F6} " +
                $"spread={spread:E4} speed={scene.Body.linearVelocity.magnitude:E4}");

            Assert.That(finalHeight,
                Is.EqualTo(VehicleBody.RestingCentreHeight).Within(0.001f),
                "resting height differs from half the box thickness");
            Assert.That(spread, Is.LessThan(0.001f),
                "height oscillates after landing");
        }

        /// <summary>
        /// Check 3: 400 Hz and 1600 Hz produce the same trajectory to within the
        /// half-step lag a fixed-step integrator necessarily has. PhysX's
        /// semi-implicit Euler leaves a position error of about
        /// (1/2)*a*dt*t, so the gap between the two rates must be that size and
        /// must shrink in proportion to the step. Anything larger, or an error
        /// that does not shrink, would mean an instability rather than the
        /// integrator's own order.
        ///
        /// 検証 3: 400 Hz と 1600 Hz の軌跡が、固定刻みの積分に必ず伴う半刻みの
        /// 遅れのぶんだけしか違わないこと。PhysX の半陰的オイラーは位置に
        /// およそ (1/2)*a*dt*t の誤差を残すので、2 つの刻みの差はその大きさに
        /// なり、刻みに比例して縮む。これより大きい差や、縮まない誤差は、
        /// 積分の次数ではなく不安定を意味する。
        /// </summary>
        [Test]
        public void StepRateDifferenceIsOnlyTheHalfStepLag()
        {
            Vector3 coarse = RunConstantForce(PhysicsStepSettings.StepSeconds, 400);
            scene.Dispose();
            scene = null;
            Vector3 fine = RunConstantForce(PhysicsStepSettings.StepSeconds / 4.0f, 1600);
            scene.Dispose();
            scene = null;
            Vector3 finest = RunConstantForce(PhysicsStepSettings.StepSeconds / 8.0f, 3200);

            float coarseToFine = Vector3.Distance(coarse, fine);
            float fineToFinest = Vector3.Distance(fine, finest);

            // The upward acceleration in RunConstantForce is half of gravity, and
            // the run lasts one second, so the expected 400 Hz lag is
            // (1/2)*(g/2)*dt*1 s. The 400-to-1600 gap is 3/4 of that.
            // RunConstantForce の上向き加速度は重力の半分で、走行時間は 1 秒なので、
            // 400 Hz の遅れは (1/2)*(g/2)*dt*1 秒。400→1600 の差はその 3/4。
            float expectedLagAt400Hz = 0.5f *
                (0.5f * AccelerometerModel.GravityMetersPerSecondSquared) *
                PhysicsStepSettings.StepSeconds * 1.0f;
            float expectedGap = 0.75f * expectedLagAt400Hz;

            Debug.Log(
                $"[check3] at400Hz={coarse:F8} at1600Hz={fine:F8} at3200Hz={finest:F8} " +
                $"gap400to1600={coarseToFine:E4} m (predicted {expectedGap:E4} m) " +
                $"gap1600to3200={fineToFinest:E4} m " +
                $"ratio={coarseToFine / fineToFinest:F3} (first order predicts 6.0)");

            Assert.That(coarseToFine, Is.EqualTo(expectedGap).Within(0.3 * expectedGap),
                "the gap between the step rates is not the half-step lag");
            Assert.That(fineToFinest, Is.LessThan(coarseToFine),
                "refining the step does not reduce the difference");
        }

        /// <summary>
        /// Check 4: free rotation about two axes at once. PhysX holds the world
        /// angular velocity constant for a torque-free body, so for an asymmetric
        /// inertia the world angular momentum swings instead of staying put — the
        /// gyroscopic term is missing. Adding -omega x (I*omega) explicitly fixes
        /// the direction, which also shows it is not being applied twice: were
        /// PhysX applying it too, adding a second copy would make the direction
        /// worse, not better.
        ///
        /// 検証 4: 2 軸まわりの自由回転。PhysX はトルクの無い剛体の世界角速度を
        /// 一定に保つため、慣性が非対称だと世界角運動量が向きを振り、保存しない。
        /// ジャイロ項が欠けている。-omega x (I*omega) を明示的に足すと向きが
        /// 定まる。これは二重計上でないことの証でもある。PhysX も足していれば、
        /// 2 つ目を足すと向きは良くならず悪くなる。
        /// </summary>
        [Test]
        public void GyroscopicTermMustBeAddedExplicitly()
        {
            DriftMeasurement plain =
                MeasureAngularMomentumDrift(addGyroscopicTorque: false);
            scene.Dispose();
            scene = null;
            DriftMeasurement corrected =
                MeasureAngularMomentumDrift(addGyroscopicTorque: true);

            Debug.Log(
                $"[check4] relative angular-momentum drift over 1 s: " +
                $"without the explicit term={plain.Relative:E4} " +
                $"(direction={plain.DirectionDegrees:F3} deg, " +
                $"magnitude change={plain.MagnitudeRatio - 1.0f:E4}), " +
                $"with it={corrected.Relative:E4} " +
                $"(direction={corrected.DirectionDegrees:F3} deg, " +
                $"magnitude change={corrected.MagnitudeRatio - 1.0f:E4})");

            Assert.That(plain.DirectionDegrees, Is.GreaterThan(5.0f),
                "PhysX unexpectedly kept the angular-momentum direction on its own");
            Assert.That(corrected.DirectionDegrees, Is.LessThan(0.5f),
                "the explicit gyroscopic term did not hold the direction");
            Assert.That(corrected.DirectionDegrees, Is.LessThan(plain.DirectionDegrees),
                "adding the term made the direction worse, which would mean double counting");
            Assert.That(corrected.MagnitudeRatio, Is.EqualTo(1.0f).Within(0.001f),
                "the explicit term changed the angular-momentum magnitude");
            Assert.That(plain.MagnitudeRatio, Is.EqualTo(1.0f).Within(0.001f),
                "PhysX did not hold the angular-momentum magnitude");
        }

        /// <summary>
        /// Check 5: at rest on the floor the synthesized accelerometer reads +9.81
        /// along the body's up axis.
        /// 検証 5: 床の上で静止したとき、合成した加速度計が機体の上向きに +9.81 を
        /// 示す。
        /// </summary>
        [Test]
        public void AccelerometerReadsGravityAtRest()
        {
            scene = new PhysXVehicleScene();
            scene.Build(new Vector3(0.0f, VehicleBody.RestingCentreHeight, 0.0f),
                withFloor: true, useGravity: true);

            int settleSteps = Mathf.CeilToInt(0.5f / PhysicsStepSettings.StepSeconds);
            for (int step = 0; step < settleSteps; step++)
            {
                scene.Step();
            }

            var upReadings = new List<float>();
            int sampleSteps = Mathf.CeilToInt(0.5f / PhysicsStepSettings.StepSeconds);
            for (int step = 0; step < sampleSteps; step++)
            {
                Vector3 before = scene.Body.linearVelocity;
                scene.Step();
                Vector3 reading = AccelerometerModel.Read(
                    before, scene.Body.linearVelocity, scene.Body.rotation,
                    PhysicsStepSettings.StepSeconds);
                upReadings.Add(reading.y);
            }

            float mean = 0.0f;
            foreach (float value in upReadings)
            {
                mean += value;
            }

            mean /= upReadings.Count;

            float spread = 0.0f;
            foreach (float value in upReadings)
            {
                spread = Mathf.Max(spread, Mathf.Abs(value - mean));
            }

            Debug.Log(
                $"[check5] samples={upReadings.Count} mean={mean:F6} " +
                $"max deviation={spread:E4} m/s^2");

            Assert.That(mean,
                Is.EqualTo(AccelerometerModel.GravityMetersPerSecondSquared).Within(0.05f));
        }

        /// <summary>
        /// Check 6: two runs with identical input produce identical state.
        /// 検証 6: 同じ入力の 2 回の実行が同じ状態になる。
        /// </summary>
        [Test]
        public void RepeatedRunsAgreeExactly()
        {
            Vector3 first = RunConstantForce(PhysicsStepSettings.StepSeconds, 400);
            scene.Dispose();
            scene = null;
            Vector3 second = RunConstantForce(PhysicsStepSettings.StepSeconds, 400);

            float difference = Vector3.Distance(first, second);

            Debug.Log(
                $"[check6] run1={first:F9} run2={second:F9} distance={difference:E4} m " +
                $"(Physics.enhancedDeterminism is a project setting)");

            Assert.That(difference, Is.EqualTo(0.0f),
                "two identical runs diverged");
        }

        /// <summary>
        /// Applies a fixed body force and torque for one simulated second and
        /// returns the final position. Used by checks 3 and 6.
        /// 一定の力とトルクを仮想時間 1 秒ぶん掛け、最終位置を返す。検証 3・6 が使う。
        /// </summary>
        private Vector3 RunConstantForce(float stepSeconds, int stepsPerSecond)
        {
            // A force of about half the vehicle's weight plus a small torque, so
            // the trajectory bends rather than staying a straight line.
            // 機体の重さの半分ほどの力と小さなトルク。軌跡が直線にならないように。
            Vector3 force = new Vector3(0.0f, 0.5f * VehicleBody.MassKilograms *
                AccelerometerModel.GravityMetersPerSecondSquared, 0.02f);
            Vector3 torque = new Vector3(
                0.2f * VehicleBody.InertiaTensorUnityAxes.x, 0.0f, 0.0f);

            scene = new PhysXVehicleScene();
            scene.Build(new Vector3(0.0f, 1.0f, 0.0f),
                withFloor: false, useGravity: true);

            for (int step = 0; step < stepsPerSecond; step++)
            {
                scene.Body.AddRelativeForce(force, ForceMode.Force);
                scene.Body.AddRelativeTorque(torque, ForceMode.Force);
                scene.Step(stepSeconds);
            }

            return scene.Body.position;
        }

        /// <summary>
        /// How far the world-frame angular momentum moved: as a fraction of its
        /// magnitude, as an angle, and as a change in length.
        /// 世界座標系の角運動量がどれだけ動いたか。大きさに対する割合・角度・
        /// 長さの変化で表す。
        /// </summary>
        private readonly struct DriftMeasurement
        {
            public DriftMeasurement(Vector3 start, Vector3 end)
            {
                Relative = Vector3.Distance(start, end) / start.magnitude;
                DirectionDegrees = Vector3.Angle(start, end);
                MagnitudeRatio = end.magnitude / start.magnitude;
            }

            public float Relative { get; }
            public float DirectionDegrees { get; }
            public float MagnitudeRatio { get; }
        }

        /// <summary>
        /// Spins the body about two axes and returns how far the world-frame
        /// angular momentum moved over one simulated second.
        /// 2 軸まわりに回し、仮想時間 1 秒で世界座標系の角運動量がどれだけ
        /// 動いたかを返す。
        /// </summary>
        private DriftMeasurement MeasureAngularMomentumDrift(bool addGyroscopicTorque)
        {
            // Rates chosen so the two contributing axes give comparable momentum,
            // which makes the gyroscopic coupling large enough to see.
            // ジャイロの連成が見える大きさになるよう、2 軸の角運動量が同程度に
            // なる角速度を選ぶ。
            Vector3 initialRate = new Vector3(20.0f, 0.0f, 30.0f);

            scene = new PhysXVehicleScene();
            scene.Build(Vector3.zero, withFloor: false, useGravity: false);
            scene.Body.angularVelocity = initialRate;

            Vector3 momentumAtStart = GyroscopicTerm.WorldAngularMomentum(scene.Body);

            int stepCount = Mathf.CeilToInt(1.0f / PhysicsStepSettings.StepSeconds);
            for (int step = 0; step < stepCount; step++)
            {
                if (addGyroscopicTorque)
                {
                    Vector3 bodyRate =
                        Quaternion.Inverse(scene.Body.rotation) * scene.Body.angularVelocity;
                    scene.Body.AddRelativeTorque(
                        GyroscopicTerm.BodyTorqueAtMidpoint(
                            bodyRate, scene.Body.inertiaTensor,
                            PhysicsStepSettings.StepSeconds),
                        ForceMode.Force);
                }

                scene.Step();
            }

            Vector3 momentumAtEnd = GyroscopicTerm.WorldAngularMomentum(scene.Body);
            return new DriftMeasurement(momentumAtStart, momentumAtEnd);
        }
    }
}
