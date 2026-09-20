/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — stage 1(c) diagnostics).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Sim;
using UnityEngine;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// Measurements that explain the two checks which did not pass on the first
    /// run: the step-rate comparison and the gyroscopic term. These report
    /// numbers rather than asserting a threshold, so the cause can be read off.
    ///
    /// 初回に合格しなかった 2 つの検証（刻みの比較とジャイロ項）の理由を測る。
    /// 閾値で判定せず数値を報告し、原因を読み取れるようにする。
    /// </summary>
    public sealed class PhysXDiagnosticsProbe
    {
        private const float MeasuredSeconds = 1.0f;

        private PhysXVehicleScene scene;

        [TearDown]
        public void TearDown()
        {
            scene?.Dispose();
            scene = null;
        }

        /// <summary>
        /// Free fall with no applied force: any step-rate difference here comes
        /// from PhysX's own gravity integration, not from how the test applies
        /// force.
        /// 力を掛けない自由落下。ここでの刻みによる差は PhysX 自身の重力の積分に
        /// よるもので、試験側の力の掛け方によるものではない。
        /// </summary>
        [Test]
        public void ReportFreeFallStepRateDifference()
        {
            float coarse = FallHeightAfterOneSecond(PhysicsStepSettings.StepSeconds);
            scene.Dispose();
            scene = null;
            float fine = FallHeightAfterOneSecond(PhysicsStepSettings.StepSeconds / 4.0f);
            float analytic = 1.0f -
                0.5f * AccelerometerModel.GravityMetersPerSecondSquared *
                MeasuredSeconds * MeasuredSeconds;

            Debug.Log(
                $"[probeA] free fall after 1 s: 400Hz={coarse:F8} 1600Hz={fine:F8} " +
                $"analytic={analytic:F8} " +
                $"error400={coarse - analytic:E4} error1600={fine - analytic:E4}");
        }

        /// <summary>
        /// Applies force through <c>ForceMode.Force</c> each step. With an explicit
        /// integrator the velocity gained in a step is a*dt regardless of rate, but
        /// the position lags by half a step, so halving the step halves the gap.
        /// 毎刻み <c>ForceMode.Force</c> で力を掛ける。陽的な積分では 1 刻みの
        /// 速度増分は刻みによらず a*dt だが、位置は半刻みぶん遅れるため、刻みを
        /// 半分にすると差も半分になる。
        /// </summary>
        [Test]
        public void ReportAppliedForceStepRateDifference()
        {
            float[] rates = { 1.0f, 0.5f, 0.25f, 0.125f };
            var heights = new float[rates.Length];
            for (int index = 0; index < rates.Length; index++)
            {
                heights[index] =
                    RiseHeightAfterOneSecond(PhysicsStepSettings.StepSeconds * rates[index]);
                scene.Dispose();
                scene = null;
            }

            Debug.Log(
                $"[probeB] applied force after 1 s: " +
                $"400Hz={heights[0]:F8} 800Hz={heights[1]:F8} " +
                $"1600Hz={heights[2]:F8} 3200Hz={heights[3]:F8} " +
                $"gap400to1600={heights[0] - heights[2]:E4} " +
                $"gap1600to3200={heights[2] - heights[3]:E4}");
        }

        /// <summary>
        /// Free rotation about two axes with no applied torque, at several step
        /// rates. Shows whether the angular-momentum drift is a missing gyroscopic
        /// term (drift independent of the step) or integration error (drift
        /// shrinking with the step).
        /// トルクを掛けない 2 軸自由回転を複数の刻みで回す。角運動量の変化が
        /// ジャイロ項の欠落（刻みによらない）か積分誤差（刻みとともに縮む）かが
        /// 分かる。
        /// </summary>
        [Test]
        public void ReportFreeRotationDriftVersusStepRate()
        {
            float[] rates = { 1.0f, 0.5f, 0.25f, 0.125f, 0.0625f };
            for (int index = 0; index < rates.Length; index++)
            {
                float stepSeconds = PhysicsStepSettings.StepSeconds * rates[index];
                var result = FreeRotationDrift(stepSeconds, addGyroscopicTorque: false);
                scene.Dispose();
                scene = null;
                var corrected = FreeRotationDrift(stepSeconds, addGyroscopicTorque: true);
                scene.Dispose();
                scene = null;

                Debug.Log(
                    $"[probeC] step={stepSeconds * 1000.0f:F4} ms " +
                    $"({1.0f / stepSeconds:F0} Hz): " +
                    $"plain drift={result.RelativeDrift:E4} " +
                    $"angle={result.DirectionDegrees:F4} deg " +
                    $"(|L| {result.StartMagnitude:E6} -> {result.EndMagnitude:E6}); " +
                    $"with explicit term drift={corrected.RelativeDrift:E4} " +
                    $"angle={corrected.DirectionDegrees:F4} deg " +
                    $"(|L| -> {corrected.EndMagnitude:E6})");
            }
        }

        /// <summary>
        /// Body rate about a single principal axis: with no coupling the
        /// gyroscopic term is zero, so this isolates pure integration drift.
        /// 主軸 1 本まわりの回転。連成が無くジャイロ項が 0 なので、積分だけの
        /// 誤差を切り出せる。
        /// </summary>
        [Test]
        public void ReportSingleAxisRotationDrift()
        {
            scene = new PhysXVehicleScene();
            scene.Build(Vector3.zero, withFloor: false, useGravity: false);
            scene.Body.angularVelocity = new Vector3(0.0f, 0.0f, 30.0f);

            Vector3 start = GyroscopicTerm.WorldAngularMomentum(scene.Body);
            int stepCount =
                Mathf.CeilToInt(MeasuredSeconds / PhysicsStepSettings.StepSeconds);
            for (int step = 0; step < stepCount; step++)
            {
                scene.Step();
            }

            Vector3 end = GyroscopicTerm.WorldAngularMomentum(scene.Body);

            Debug.Log(
                $"[probeD] single-axis 30 rad/s about z: " +
                $"|L| {start.magnitude:E6} -> {end.magnitude:E6}, " +
                $"relative drift={Vector3.Distance(start, end) / start.magnitude:E4}, " +
                $"final rate={scene.Body.angularVelocity:F6}");
        }

        /// <summary>
        /// The explicit term evaluated at the start of a step injects energy, so
        /// |L| grows. This compares that with evaluating it at the step's midpoint
        /// — one extra cross product, no extra PhysX work — to see whether the
        /// growth is worth correcting that way.
        /// 刻みの先頭で項を評価するとエネルギーが入り |L| が増える。外積 1 つぶんの
        /// 追加だけで済む中点評価と比べ、その増加をこの方法で直す価値があるかを
        /// 見る。
        /// </summary>
        [Test]
        public void ReportMidpointEvaluationOfGyroscopicTerm()
        {
            DriftResult atStart = GyroscopicDrift(useMidpointRate: false);
            scene.Dispose();
            scene = null;
            DriftResult atMidpoint = GyroscopicDrift(useMidpointRate: true);

            Debug.Log(
                $"[probeH] gyroscopic term at 400 Hz over 1 s: " +
                $"evaluated at step start -> angle={atStart.DirectionDegrees:F4} deg, " +
                $"|L| x{atStart.EndMagnitude / atStart.StartMagnitude:F6}; " +
                $"evaluated at midpoint -> angle={atMidpoint.DirectionDegrees:F4} deg, " +
                $"|L| x{atMidpoint.EndMagnitude / atMidpoint.StartMagnitude:F6}");
        }

        private DriftResult GyroscopicDrift(bool useMidpointRate)
        {
            Vector3 initialRate = new Vector3(20.0f, 0.0f, 30.0f);
            float stepSeconds = PhysicsStepSettings.StepSeconds;

            scene = new PhysXVehicleScene();
            scene.Build(Vector3.zero, withFloor: false, useGravity: false);
            scene.Body.angularVelocity = initialRate;

            Vector3 start = GyroscopicTerm.WorldAngularMomentum(scene.Body);
            int stepCount = Mathf.RoundToInt(MeasuredSeconds / stepSeconds);
            Vector3 inertia = scene.Body.inertiaTensor;

            for (int step = 0; step < stepCount; step++)
            {
                Vector3 rate =
                    Quaternion.Inverse(scene.Body.rotation) * scene.Body.angularVelocity;
                Vector3 torque = GyroscopicTerm.BodyTorque(rate, inertia);

                if (useMidpointRate)
                {
                    Vector3 halfStepRate = rate + 0.5f * stepSeconds * new Vector3(
                        torque.x / inertia.x, torque.y / inertia.y, torque.z / inertia.z);
                    torque = GyroscopicTerm.BodyTorque(halfStepRate, inertia);
                }

                scene.Body.AddRelativeTorque(torque, ForceMode.Force);
                scene.Step(stepSeconds);
            }

            return new DriftResult(start, GyroscopicTerm.WorldAngularMomentum(scene.Body));
        }

        private float FallHeightAfterOneSecond(float stepSeconds)
        {
            scene = new PhysXVehicleScene();
            scene.Build(new Vector3(0.0f, 1.0f, 0.0f),
                withFloor: false, useGravity: true);
            int stepCount = Mathf.RoundToInt(MeasuredSeconds / stepSeconds);
            for (int step = 0; step < stepCount; step++)
            {
                scene.Step(stepSeconds);
            }

            return scene.Body.position.y;
        }

        private float RiseHeightAfterOneSecond(float stepSeconds)
        {
            // Twice the weight, so the body accelerates upward at +g.
            // 重さの 2 倍。機体は上向きに +g で加速する。
            Vector3 force = new Vector3(0.0f, 2.0f * VehicleBody.MassKilograms *
                AccelerometerModel.GravityMetersPerSecondSquared, 0.0f);

            scene = new PhysXVehicleScene();
            scene.Build(new Vector3(0.0f, 1.0f, 0.0f),
                withFloor: false, useGravity: true);
            int stepCount = Mathf.RoundToInt(MeasuredSeconds / stepSeconds);
            for (int step = 0; step < stepCount; step++)
            {
                scene.Body.AddRelativeForce(force, ForceMode.Force);
                scene.Step(stepSeconds);
            }

            return scene.Body.position.y;
        }

        private readonly struct DriftResult
        {
            public DriftResult(Vector3 start, Vector3 end)
            {
                RelativeDrift = Vector3.Distance(start, end) / start.magnitude;
                DirectionDegrees = Vector3.Angle(start, end);
                StartMagnitude = start.magnitude;
                EndMagnitude = end.magnitude;
            }

            public float RelativeDrift { get; }
            public float DirectionDegrees { get; }
            public float StartMagnitude { get; }
            public float EndMagnitude { get; }
        }

        private DriftResult FreeRotationDrift(float stepSeconds, bool addGyroscopicTorque)
        {
            Vector3 initialRate = new Vector3(20.0f, 0.0f, 30.0f);

            scene = new PhysXVehicleScene();
            scene.Build(Vector3.zero, withFloor: false, useGravity: false);
            scene.Body.angularVelocity = initialRate;

            Vector3 start = GyroscopicTerm.WorldAngularMomentum(scene.Body);
            int stepCount = Mathf.RoundToInt(MeasuredSeconds / stepSeconds);
            for (int step = 0; step < stepCount; step++)
            {
                if (addGyroscopicTorque)
                {
                    Vector3 bodyRate =
                        Quaternion.Inverse(scene.Body.rotation) * scene.Body.angularVelocity;
                    scene.Body.AddRelativeTorque(
                        GyroscopicTerm.BodyTorque(bodyRate, scene.Body.inertiaTensor),
                        ForceMode.Force);
                }

                scene.Step(stepSeconds);
            }

            Vector3 end = GyroscopicTerm.WorldAngularMomentum(scene.Body);
            return new DriftResult(start, end);
        }
    }
}
