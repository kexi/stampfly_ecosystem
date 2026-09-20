/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — torque authority and asymmetry).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Collections.Generic;
using NUnit.Framework;
using StampFly.Input;
using StampFly.Native;
using StampFly.Sim;
using UnityEngine;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// A torque turns the body, and a flight that is pushed off symmetry comes
    /// back.
    ///
    /// トルクが機体を回すこと、そして対称から押し出された飛行が戻ってくること。
    ///
    /// Every browser flight flown so far has been perfectly symmetric: four
    /// equal duties, roll and pitch at exactly 0.0 degrees, yaw at exactly
    /// 45.000. A suite that only flies that case cannot see a fault in the
    /// rotational path at all, which is how a yaw-axis failure reached the
    /// browser unnoticed. These checks break the symmetry on purpose.
    ///
    /// これまでブラウザで飛ばした飛行は完全に対称だった。4 つの duty は等しく、
    /// ロールとピッチはちょうど 0.0 度、ヨーはちょうど 45.000 度である。その場合
    /// だけを飛ばす試験一式では、回転の経路の誤りは**一切**見えない。ヨー軸の
    /// 不具合が気付かれずブラウザまで届いたのはそのためである。以下の検査は、
    /// 意図して対称を崩す。
    ///
    /// @design simulator/unity/README.md §3 段階 1(c) の検証
    /// </summary>
    public sealed class TorqueAuthorityTest
    {
        // tau/I must come out this close. Stage 1(c) measured 0.0000% in the
        // editor; a few percent leaves room for the discrete integration
        // without admitting a body that barely turns.
        // tau/I はこれだけの精度で出なければならない。段階 1(c) のエディタでの
        // 実測は 0.0000% である。数 % は離散積分の余地を残しつつ、ほとんど回らない
        // 機体を通さない。
        private const float TorqueTolerance = 0.05f;

        private GameObject vehicleObject;
        private GameObject floorObject;

        [TearDown]
        public void TearDown()
        {
            if (vehicleObject != null) { Object.DestroyImmediate(vehicleObject); }
            if (floorObject != null) { Object.DestroyImmediate(floorObject); }
            vehicleObject = null;
            floorObject = null;
            Physics.simulationMode = SimulationMode.FixedUpdate;
        }

        /// <summary>
        /// A known torque about each body axis produces tau/I. This is the
        /// editor's half of the answer; `plant.torque_probe` asks the same
        /// question of a browser, and the two numbers are compared by hand.
        /// 既知のトルクが各機体軸で tau/I を生むこと。これは答えのエディタ側で
        /// ある。`plant.torque_probe` が同じ問いをブラウザに尋ね、2 つの数値を
        /// 突き合わせる。
        /// </summary>
        [Test]
        public void AKnownTorqueTurnsTheBodyAtTauOverI()
        {
            Rigidbody body = BuildBody();
            TorqueProbe.AxisResult[] axes = TorqueProbe.Run(body);

            Debug.Log("[TorqueProbe] " + TorqueProbe.ToJson(body, axes));

            foreach (TorqueProbe.AxisResult axis in axes)
            {
                Assert.That(axis.RelativeError, Is.LessThan(TorqueTolerance),
                            $"{axis.Axis}: measured {axis.Measured:F3} rad/s^2, " +
                            $"expected tau/I = {axis.Expected:F3} " +
                            $"(tau {axis.Torque:E3} N.m, I {axis.Inertia:E3} kg.m^2)");
            }
        }

        /// <summary>
        /// The rigid body carries the settings the probe's answer depends on.
        /// A silently automatic inertia tensor, a frozen rotation or a clamped
        /// angular velocity would each make a torque look ineffective for a
        /// reason that has nothing to do with the engine's arithmetic.
        /// 剛体が、測定の答えが依存する設定を持っていること。黙って自動になった
        /// 慣性テンソル、凍結された回転、頭打ちにされた角速度は、どれもトルクを
        /// 効かないように見せるが、その理由はエンジンの計算とは無関係である。
        /// </summary>
        [Test]
        public void TheBodyIsFreeToRotateWithTheInertiaTheModelGives()
        {
            Rigidbody body = BuildBody();

            Assert.That(body.automaticInertiaTensor, Is.False,
                        "the inertia tensor is being computed by PhysX, not taken "
                        + "from the model");
            Assert.That(body.constraints, Is.EqualTo(RigidbodyConstraints.None),
                        "the body's rotation is constrained");
            Assert.That(body.angularDamping, Is.EqualTo(0.0f),
                        "PhysX is damping the rotation the C++ plant owns");
            Assert.That(body.isKinematic, Is.False, "the body is kinematic");
            Assert.That(body.maxAngularVelocity,
                        Is.EqualTo(VehicleBody.AngularSpeedLimitRadPerSecond),
                        "the angular velocity is clamped below what this vehicle reaches");
            Assert.That(body.inertiaTensor,
                        Is.EqualTo(VehicleBody.InertiaTensorUnityAxes),
                        "the inertia tensor is not the model's");
        }

        /// <summary>
        /// A flight nudged off level with a lateral gust comes back to level
        /// and stays flying — the attitude loop working, which a symmetric
        /// hover never asks for.
        /// 横風で水平から押し出された飛行が、水平へ戻り、飛び続けること。姿勢
        /// ループが働くということで、対称なホバリングはそれを一度も求めない。
        /// </summary>
        [Test]
        public void AGustIsAbsorbedAndTheVehicleReturnsToLevel()
        {
            var scene = new RaycastFlightScene();
            try
            {
                Assert.That(scene.Build(), Is.True,
                            $"sfu_boot failed: {scene.Firmware?.LastError}");

                float worstTilt = 0.0f;
                float tiltAtEnd = 0.0f;

                Fly(scene, 18.0, now =>
                {
                    // A lateral push once the vehicle is well airborne, then off
                    // again, so the loop must both absorb it and recover.
                    // 十分に浮いてから横へ押し、また切る。ループはそれを受け止め、
                    // かつ戻さねばならない。
                    bool isGusting = now >= 9.0 && now < 11.0;
                    scene.Firmware.SetWind(isGusting ? 0.07f : 0.0f, 0.0f, 0.0f);

                    bool isAfterSettling = now > 8.0;
                    if (isAfterSettling)
                    {
                        float tilt = Vector3.Angle(
                            scene.Body.rotation * Vector3.up, Vector3.up);
                        worstTilt = Mathf.Max(worstTilt, tilt);
                        tiltAtEnd = tilt;
                    }
                });

                Assert.That(worstTilt, Is.GreaterThan(0.2f),
                            "the gust never tilted the vehicle at all, so this "
                            + "check proved nothing about the attitude loop");
                Assert.That(worstTilt, Is.LessThan(25.0f),
                            $"the gust tipped the vehicle {worstTilt:F1} degrees");
                Assert.That(tiltAtEnd, Is.LessThan(5.0f),
                            $"the vehicle ended {tiltAtEnd:F1} degrees off level");
                Assert.That(scene.LastResult.FlightState,
                            Is.EqualTo(FlightStateNames.Flying),
                            "the vehicle stopped flying after the gust");
            }
            finally
            {
                scene.Dispose();
            }
        }

        /// <summary>
        /// The yaw duties do not run away while holding.
        ///
        /// This is the check the browser failure needed. With the vehicle
        /// perfectly symmetric the yaw loop is excited by nothing but the 8-bit
        /// duty quantisation, and if it then oscillates, the two motor pairs
        /// walk apart until one saturates at 1.0 — which costs total thrust and
        /// drops the vehicle out of the sky without the yaw angle ever moving.
        /// So the assertion is on the SPREAD between the pairs, not on the
        /// heading.
        ///
        /// 保持の間、ヨーの duty が暴走しないこと。
        ///
        /// ブラウザの不具合に要ったのがこの検査である。完全に対称な機体では、
        /// ヨーのループを励起するものは 8 bit の duty の量子化しか無い。それで
        /// 発振すると、モータの 2 対は片方が 1.0 で飽和するまで開いていく。これは
        /// 全体の推力を奪い、ヨー角が一度も動かないまま機体を落とす。よって判定は
        /// 機首の向きではなく、**対の間の開き**に置く。
        /// </summary>
        [Test]
        public void TheYawDutiesDoNotWalkApartWhileHolding()
        {
            var scene = new RaycastFlightScene();
            try
            {
                Assert.That(scene.Build(), Is.True,
                            $"sfu_boot failed: {scene.Firmware?.LastError}");

                float worstSpread = 0.0f;
                double worstAt = 0.0;
                var saturated = new List<double>();

                Fly(scene, 18.0, now =>
                {
                    bool isHolding = now > 8.0;
                    if (!isHolding)
                    {
                        return;
                    }

                    SfuStepOut state = scene.LastResult;
                    // The diagonal pairs a yaw command drives apart.
                    // ヨーの指令が開かせる対角の 2 対。
                    float first = 0.5f * (state.MotorDuty0 + state.MotorDuty2);
                    float second = 0.5f * (state.MotorDuty1 + state.MotorDuty3);
                    float spread = Mathf.Abs(first - second);

                    if (spread > worstSpread)
                    {
                        worstSpread = spread;
                        worstAt = now;
                    }

                    bool isSaturated = state.MotorDuty0 >= 0.999f ||
                                       state.MotorDuty1 >= 0.999f ||
                                       state.MotorDuty2 >= 0.999f ||
                                       state.MotorDuty3 >= 0.999f;
                    if (isSaturated)
                    {
                        saturated.Add(now);
                    }
                });

                Assert.That(saturated, Is.Empty,
                            $"a motor saturated at full duty while holding, first at "
                            + $"{(saturated.Count > 0 ? saturated[0] : 0.0):F2} s: "
                            + "the yaw loop is spending the thrust budget");
                Assert.That(worstSpread, Is.LessThan(0.25f),
                            $"the diagonal motor pairs walked {worstSpread:F3} apart "
                            + $"at {worstAt:F2} s while the sticks were centred");
            }
            finally
            {
                scene.Dispose();
            }
        }

        /// <summary>
        /// Fly the standard script to a time, calling
        /// <paramref name="each"/> before every tick.
        /// 標準の台本をある時刻まで飛ばし、刻みごとの前に
        /// <paramref name="each"/> を呼ぶ。
        /// </summary>
        private static void Fly(RaycastFlightScene scene, double untilSeconds,
                                System.Action<double> each)
        {
            int ticksThisFrame = 0;
            double now = 0.0;

            while (now < untilSeconds)
            {
                bool frameHasEnded = ticksThisFrame >= SimClock.MaxTicksPerFrame;
                if (frameHasEnded)
                {
                    scene.EndFrame();
                    ticksThisFrame = 0;
                }

                scene.Sticks = SticksAt(now);
                each(now);

                Assert.That(scene.RunOneTick(), Is.EqualTo(SfuAbi.Ok),
                            $"a tick failed at {now:F2} s");

                ticksThisFrame += 1;
                now = scene.LastResult.NowMicroseconds * 1e-6;
            }
        }

        /// <summary>The check flight's sticks. / 確認の飛行のスティックの値。</summary>
        private static RcFrame SticksAt(double now)
        {
            if (now < 4.0) { return Frame(RcScale.Centre, 0); }
            if (now < 5.0) { return Frame(RcScale.Centre, SfuAbi.FlagArm); }
            if (now < 6.3) { return Frame(3243, SfuAbi.FlagArm); }
            return Frame(RcScale.Centre,
                         (byte)(SfuAbi.FlagArm | SfuAbi.FlagAltitudeMode));
        }

        private static RcFrame Frame(ushort throttle, byte flags)
        {
            return new RcFrame(
                throttle, RcScale.Centre, RcScale.Centre, RcScale.Centre, flags);
        }

        /// <summary>A vehicle over a floor, configured as the simulator does. / シミュレータと同じ設定の、床の上の機体。</summary>
        private Rigidbody BuildBody()
        {
            PhysicsStepSettings.Apply();

            floorObject = GameObject.CreatePrimitive(PrimitiveType.Cube);
            floorObject.transform.position = new Vector3(0.0f, -0.25f, 0.0f);
            floorObject.transform.localScale = new Vector3(20.0f, 0.5f, 20.0f);

            vehicleObject = new GameObject("Vehicle");
            vehicleObject.transform.position =
                new Vector3(0.0f, VehicleBody.RestingCentreHeight, 0.0f);

            var box = vehicleObject.AddComponent<BoxCollider>();
            var body = vehicleObject.AddComponent<Rigidbody>();
            VehicleBody.Apply(body, box);
            Physics.SyncTransforms();
            return body;
        }
    }
}
