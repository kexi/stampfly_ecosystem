/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the stage 3 flight checks).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Input;
using StampFly.Native;
using StampFly.Sim;
using UnityEngine;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// The stage 3 pass criterion, checked against the real firmware and real
    /// PhysX: ARM, take off, hold altitude for ten seconds, and land.
    ///
    /// 段階 3 の合格基準を、実物のファームウェアと実物の PhysX で確かめる。
    /// ARM し、離陸し、10 秒ぶん高度を保ち、着地することである。
    ///
    /// These need `simulator/unity/native/build-native/libsfu_firmware.dylib`,
    /// which `nix develop -c just unity-native-build` produces. Without it every
    /// check is ignored with that command in the message, rather than failing in
    /// a way that reads as a broken simulator.
    ///
    /// これらは `simulator/unity/native/build-native/libsfu_firmware.dylib` を要り、
    /// それは `nix develop -c just unity-native-build` が作る。無ければ全ての検査は
    /// そのコマンドを文に添えて見送られる。シミュレータが壊れたように読める失敗を
    /// 避けるためである。
    ///
    /// @design docs/plans/unity-simulator.md §5 段階 3
    /// </summary>
    public sealed class FirmwareFlightTest
    {
        // The scripted moments, in simulated seconds. The firmware needs a few
        // seconds of boot and calibration before it reaches IDLE_GROUND, and the
        // ARM edge has to arrive after that.
        // 台本の時点。シミュレーションの秒で表す。ファームは IDLE_GROUND に達する
        // まで数秒の起動と校正を要り、ARM のエッジはその後に来なければならない。
        private const double ArmAtSeconds = 4.0;
        private const double ClimbAtSeconds = 5.0;
        private const double HoldAtSeconds = 6.3;
        private const double LandAtSeconds = 16.5;
        private const double EndAtSeconds = 21.0;

        // The throttle that takes the vehicle off the ground, from the smoke
        // checks' own script (`sfu_rc_script.hpp`).
        // 機体を地面から離すスロットル。最小動作確認自身の台本
        // （`sfu_rc_script.hpp`）から取った値である。
        private const ushort ClimbThrottle = 3243;

        // The band the altitude must stay inside while holding. The smoke checks
        // settle around 0.58 m from a 0.81 m peak, so this is wide enough for
        // the settling and narrow enough to catch a vehicle that never left the
        // floor or one that flew away.
        // 保持している間、高度が収まっていなければならない帯。最小動作確認は
        // 0.81 m の頂点から 0.58 m ほどに落ち着くので、落ち着きを許すだけ広く、
        // 床を離れなかった機体や飛び去った機体を捕まえるだけ狭い。
        private const float HoldMinimumMeters = 0.15f;
        private const float HoldMaximumMeters = 3.0f;

        // How near the floor counts as landed. Above the vehicle's own resting
        // height, so a body settled on its collider passes.
        // どれだけ床に近ければ着地とみなすか。機体自身の静止高より上に取ってあり、
        // コライダの上で落ち着いた機体が通る。
        private const float LandedMeters = 0.08f;

        private FirmwareFlightScene scene;

        [SetUp]
        public void SetUp()
        {
            bool cannotFly = !FirmwareFlightScene.DylibExists;
            if (cannotFly)
            {
                Assert.Ignore(
                    "the firmware dylib is not built — run " +
                    "`nix develop -c just unity-native-build` first");
            }

            scene = new FirmwareFlightScene();
        }

        [TearDown]
        public void TearDown()
        {
            scene?.Dispose();
            scene = null;
        }

        /// <summary>
        /// The whole flight: ARM, climb, hold for ten seconds inside the band
        /// while FLYING, then land. This one test is the stage 3 criterion.
        /// 飛行の全体。ARM し、上昇し、FLYING のまま帯の中で 10 秒保ち、着地する。
        /// この 1 件が段階 3 の基準である。
        /// </summary>
        [Test]
        public void ArmsClimbsHoldsAltitudeAndLands()
        {
            Assert.That(scene.Build(), Is.True,
                        $"sfu_boot failed: {scene.Firmware?.LastError}");

            float lowestWhileHolding = float.MaxValue;
            float highestWhileHolding = 0.0f;
            bool stayedFlying = true;

            int status = scene.FlyUntil(EndAtSeconds, now =>
            {
                scene.Sticks = SticksAt(now);

                bool isHolding = now >= HoldAtSeconds + 1.0 && now < LandAtSeconds;
                if (isHolding)
                {
                    lowestWhileHolding = Mathf.Min(lowestWhileHolding, scene.AltitudeMeters);
                    highestWhileHolding = Mathf.Max(highestWhileHolding, scene.AltitudeMeters);
                    stayedFlying &= scene.LastResult.FlightState == FlightStateNames.Flying;
                }
            });

            Assert.That(status, Is.EqualTo(SfuAbi.Ok),
                        $"a tick failed: {SfuAbi.Describe(status)}");
            Assert.That(lowestWhileHolding, Is.GreaterThan(HoldMinimumMeters),
                        "the vehicle dropped out of the altitude band");
            Assert.That(highestWhileHolding, Is.LessThan(HoldMaximumMeters),
                        "the vehicle climbed out of the altitude band");
            Assert.That(stayedFlying, Is.True,
                        "the firmware left FLYING while it should have been holding");
            Assert.That(scene.AltitudeMeters, Is.LessThan(LandedMeters),
                        "the vehicle did not come back down");
        }

        /// <summary>
        /// The firmware reaches ALT_HOLD once the altitude-hold bit is set,
        /// which is what makes the hold above a hold rather than a steady hover
        /// on a fixed throttle.
        /// 高度保持のビットを立てるとファームが ALT_HOLD に達すること。上の保持を、
        /// 一定のスロットルでの安定したホバリングではなく「保持」にしているのが
        /// これである。
        /// </summary>
        [Test]
        public void ReachesAltitudeHoldMode()
        {
            Assert.That(scene.Build(), Is.True,
                        $"sfu_boot failed: {scene.Firmware?.LastError}");

            bool reachedAltitudeHold = false;
            scene.FlyUntil(HoldAtSeconds + 3.0, now =>
            {
                scene.Sticks = SticksAt(now);
                reachedAltitudeHold |=
                    scene.LastResult.FlightMode == FlightStateNames.AltitudeHold;
            });

            Assert.That(reachedAltitudeHold, Is.True,
                        "the firmware never entered ALT_HOLD");
        }

        /// <summary>
        /// N ticks land the virtual clock on exactly N times the tick length —
        /// the integer promise the ABI makes, checked through the C# side.
        /// N 刻みは仮想時計をちょうど N × 刻みの長さに置く。ABI が約束する整数の
        /// 計算を、C# の側から確かめる。
        /// </summary>
        [Test]
        public void TheVirtualClockIsExact()
        {
            Assert.That(scene.Build(), Is.True);

            const int Ticks = 400;
            for (int tick = 0; tick < Ticks; tick++)
            {
                Assert.That(scene.RunOneTick(), Is.EqualTo(SfuAbi.Ok));
            }

            Assert.That(scene.LastResult.NowMicroseconds,
                        Is.EqualTo((long)Ticks * SimClock.TickMicroseconds));
        }

        /// <summary>
        /// A vehicle at rest on the floor reports the accelerometer reading the
        /// real one does, so the firmware's attitude estimate starts level. This
        /// is the check that the loop's step-4 arithmetic is the right way round.
        /// 床に静止した機体が、実機と同じ加速度計の測定値を報告すること。これに
        /// よりファームの姿勢推定は水平から始まる。ループの手順 4 の計算の向きが
        /// 正しいことを確かめる検査である。
        /// </summary>
        [Test]
        public void SittingOnTheFloorStaysLevel()
        {
            Assert.That(scene.Build(), Is.True);

            for (int tick = 0; tick < 400; tick++)
            {
                Assert.That(scene.RunOneTick(), Is.EqualTo(SfuAbi.Ok));
            }

            Assert.That(scene.AltitudeMeters,
                        Is.EqualTo(VehicleBody.RestingCentreHeight).Within(0.002f));
            Assert.That(Vector3.Angle(scene.Body.rotation * Vector3.up, Vector3.up),
                        Is.LessThan(1.0f),
                        "a vehicle left alone on the floor tipped over");
        }

        /// <summary>
        /// The sticks the script holds at a given moment, the same sequence the
        /// native smoke checks fly: disarmed, ARM, climb, then altitude hold,
        /// and finally the throttle down that brings it back.
        /// ある時点で台本が持つスティックの値。ネイティブの最小動作確認が飛ばすのと
        /// 同じ流れである。disarmed、ARM、上昇、高度保持、最後に戻すためのスロットル
        /// 下げ。
        /// </summary>
        private static RcFrame SticksAt(double nowSeconds)
        {
            bool isBeforeArm = nowSeconds < ArmAtSeconds;
            if (isBeforeArm)
            {
                return RcFrame.Centred;
            }

            bool isArmingOnly = nowSeconds < ClimbAtSeconds;
            if (isArmingOnly)
            {
                return Frame(RcScale.Centre, SfuAbi.FlagArm);
            }

            bool isClimbing = nowSeconds < HoldAtSeconds;
            if (isClimbing)
            {
                return Frame(ClimbThrottle, SfuAbi.FlagArm);
            }

            byte holdingFlags = SfuAbi.FlagArm | SfuAbi.FlagAltitudeMode;
            bool isHolding = nowSeconds < LandAtSeconds;
            if (isHolding)
            {
                return Frame(RcScale.Centre, holdingFlags);
            }

            // Throttle at the bottom in ALT_HOLD commands a descent, and the
            // firmware lands from it.
            // ALT_HOLD でスロットルを一番下にすると下降の指令になり、ファームは
            // そこから着地する。
            return Frame(RcScale.Minimum, holdingFlags);
        }

        /// <summary>One frame with the roll, pitch and yaw sticks centred. / ロール・ピッチ・ヨーを中央にしたフレーム 1 つ。</summary>
        private static RcFrame Frame(ushort throttle, byte flags)
        {
            return new RcFrame(
                throttle, RcScale.Centre, RcScale.Centre, RcScale.Centre, flags);
        }
    }
}
