/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — frame-batching invariance).
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
    /// The flight must not depend on how many ticks a rendered frame happens to
    /// run. A browser tab that is throttled to 10 fps runs the per-frame ceiling
    /// (twelve ticks) every frame, while the editor's own tests run one tick at
    /// a time; if the two disagree, the simulation is reading something that
    /// only a frame boundary refreshes, and a flight becomes a function of the
    /// host's frame rate rather than of the firmware and the physics.
    ///
    /// 飛行は、描画 1 フレームがたまたま何刻み回したかに左右されてはならない。
    /// 10 fps に絞られたブラウザのタブは毎フレーム上限の 12 刻みを回し、エディタの
    /// 試験は 1 刻みずつ回す。両者が食い違うなら、シミュレーションはフレームの
    /// 切れ目でしか更新されない何かを読んでいることになり、飛行はファームと物理で
    /// はなくホストのフレーム率の関数になってしまう。
    ///
    /// This is the check that would have caught the 2026-09-21 browser failure,
    /// where the vehicle wandered out of ALT_HOLD and hit the floor only in a
    /// backgrounded tab.
    /// 2026-09-21 のブラウザの不具合 ― 背面のタブでだけ機体が ALT_HOLD から
    /// さまよい出て床に当たった ― を捕まえられたはずの検査である。
    ///
    /// @design docs/plans/unity-simulator.md §3 1 刻みの処理
    /// </summary>
    public sealed class TickBatchingTest
    {
        // The same scripted moments the other flight checks use.
        // 他の飛行の検査と同じ台本の時点。
        private const double ArmAtSeconds = 4.0;
        private const double ClimbAtSeconds = 5.0;
        private const double HoldAtSeconds = 6.3;
        private const double EndAtSeconds = 14.0;
        private const ushort ClimbThrottle = 3243;

        // How often the altitude is compared between the two runs.
        // 2 つの実行で高度を比べる間隔。
        private const int SampleEveryTicks = 40;

        // The two runs integrate the same equations in the same order, so they
        // must agree to the last bit; a tolerance this small still absorbs
        // nothing but exact equality in practice, and stating one keeps the
        // failure message about metres rather than about floating point.
        // 2 つの実行は同じ式を同じ順序で積分するので、最後のビットまで一致する
        // はずである。これだけ小さい許容は実際には厳密な一致しか通さないが、
        // 書いておけば失敗の文面が浮動小数点ではなくメートルの話になる。
        private const float AgreementMeters = 1e-4f;

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
        }

        /// <summary>
        /// One tick per frame and twelve ticks per frame fly the same flight.
        /// 1 フレーム 1 刻みと 1 フレーム 12 刻みが、同じ飛行になること。
        /// </summary>
        [Test]
        public void TheFlightDoesNotDependOnTicksPerFrame()
        {
            List<float> one = Fly(TicksPerFrame.Fixed(1));
            List<float> twelve = Fly(TicksPerFrame.Fixed(SimClock.MaxTicksPerFrame));

            AssertTheyAgree(one, twelve, "1", SimClock.MaxTicksPerFrame.ToString());
        }

        /// <summary>
        /// A frame boundary that moves about — which is what a throttled browser
        /// tab produces — changes nothing either. The fixed comparison above
        /// cannot see a fault that needs the boundary to land in different
        /// places on different runs, and that is the shape the browser failure
        /// had: the same build, the same commands, a different outcome each time.
        /// フレームの切れ目が動き回っても ― 絞られたブラウザのタブが生むのがそれ
        /// である ― 何も変わらないこと。上の固定の比較では、切れ目が実行ごとに
        /// 違う場所に来ることを要する不具合は見えない。ブラウザの不具合はその形を
        /// していた。同じビルド、同じ命令で、毎回違う結果になったのである。
        /// </summary>
        [Test]
        public void TheFlightDoesNotDependOnWhereTheFrameBoundaryFalls()
        {
            List<float> steady = Fly(TicksPerFrame.Fixed(1));
            List<float> ragged = Fly(TicksPerFrame.Varying(seed: 12345));

            AssertTheyAgree(steady, ragged, "1", "1..12 varying");
        }

        /// <summary>Two runs must have flown the same flight. / 2 つの実行が同じ飛行をしたこと。</summary>
        private static void AssertTheyAgree(
            List<float> left, List<float> right, string leftName, string rightName)
        {
            Assert.That(right.Count, Is.EqualTo(left.Count),
                        "the two runs did not take the same number of ticks");

            for (int sample = 0; sample < left.Count; sample++)
            {
                double atSeconds = sample * SampleEveryTicks * SimClock.TickSeconds;
                Assert.That(right[sample], Is.EqualTo(left[sample]).Within(AgreementMeters),
                            $"the altitude parted company at {atSeconds:F2} s: " +
                            $"{leftName} tick(s) per frame gave {left[sample]:F4} m, " +
                            $"{rightName} gave {right[sample]:F4} m");
            }
        }

        /// <summary>
        /// How many ticks each frame runs. Either a fixed count or a varying one
        /// drawn from a seeded sequence, so a ragged run is still reproducible.
        /// 各フレームが回す刻みの数。固定の数か、種から作る列で変わる数。後者でも
        /// 実行は再現できる。
        /// </summary>
        private readonly struct TicksPerFrame
        {
            private readonly int fixedCount;
            private readonly System.Random varying;

            private TicksPerFrame(int fixedCount, System.Random varying)
            {
                this.fixedCount = fixedCount;
                this.varying = varying;
            }

            internal static TicksPerFrame Fixed(int count) =>
                new TicksPerFrame(count, null);

            internal static TicksPerFrame Varying(int seed) =>
                new TicksPerFrame(0, new System.Random(seed));

            /// <summary>The next frame's share of ticks. / 次のフレームのぶんの刻みの数。</summary>
            internal int Next()
            {
                bool isFixed = varying == null;
                if (isFixed)
                {
                    return fixedCount;
                }

                return varying.Next(1, SimClock.MaxTicksPerFrame + 1);
            }
        }

        /// <summary>
        /// Fly the script with the frame boundary falling every
        /// <paramref name="ticksPerFrame"/> ticks, sampling the altitude as it
        /// goes. The scene is the real one the browser flies: a raycast
        /// rangefinder against a real floor collider, not the analytic height
        /// <see cref="FirmwareFlightScene"/> hands the firmware.
        /// フレームの切れ目が <paramref name="ticksPerFrame"/> 刻みごとに来る形で
        /// 台本を飛ばし、その間の高度を記録する。場面はブラウザが飛ばす本物 ―
        /// <see cref="FirmwareFlightScene"/> が渡す解析的な高さではなく、本物の
        /// 床のコライダに対するレイキャストの測距器 ― である。
        /// </summary>
        private static List<float> Fly(TicksPerFrame ticksPerFrame)
        {
            var scene = new RaycastFlightScene();
            try
            {
                Assert.That(scene.Build(), Is.True,
                            $"sfu_boot failed: {scene.Firmware?.LastError}");

                var altitudes = new List<float>();
                int tick = 0;
                int ticksThisFrame = 0;
                int ticksWantedThisFrame = ticksPerFrame.Next();
                double nowSeconds = 0.0;

                while (nowSeconds < EndAtSeconds)
                {
                    bool frameHasEnded = ticksThisFrame >= ticksWantedThisFrame;
                    if (frameHasEnded)
                    {
                        scene.EndFrame();
                        ticksThisFrame = 0;
                        ticksWantedThisFrame = ticksPerFrame.Next();
                    }

                    scene.Sticks = SticksAt(nowSeconds);

                    bool isSample = tick % SampleEveryTicks == 0;
                    if (isSample)
                    {
                        altitudes.Add(scene.AltitudeMeters);
                    }

                    Assert.That(scene.RunOneTick(), Is.EqualTo(SfuAbi.Ok),
                                $"a tick failed at {nowSeconds:F2} s");

                    ticksThisFrame += 1;
                    tick += 1;
                    nowSeconds = scene.LastResult.NowMicroseconds * 1e-6;
                }

                return altitudes;
            }
            finally
            {
                scene.Dispose();
            }
        }

        /// <summary>The sticks the script holds at a moment. / ある時点で台本が持つスティックの値。</summary>
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

            return Frame(RcScale.Centre,
                         (byte)(SfuAbi.FlagArm | SfuAbi.FlagAltitudeMode));
        }

        private static RcFrame Frame(ushort throttle, byte flags)
        {
            return new RcFrame(
                throttle, RcScale.Centre, RcScale.Centre, RcScale.Centre, flags);
        }
    }
}
