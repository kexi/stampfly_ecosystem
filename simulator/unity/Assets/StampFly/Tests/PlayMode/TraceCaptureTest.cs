/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — capturing an editor trace).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.IO;
using NUnit.Framework;
using StampFly.Input;
using StampFly.Native;
using StampFly.Sim;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// Fly `sf unity check fly`'s script in the editor and write the per-tick
    /// trace, so `trace_diff.py` can lay it beside a browser run.
    ///
    /// エディタで `sf unity check fly` と同じ台本を飛ばし、刻みごとのトレースを
    /// 書き出す。`trace_diff.py` がブラウザの実行と並べられるようにするためである。
    ///
    /// The moments and the stick values are the ones in
    /// `tools/unity_check/fly_check.py`, so the two runs are the same flight
    /// asked for in two different ways: the browser through `rc.set` over HTTP,
    /// the editor by setting the same frame directly. Anything the two traces
    /// disagree about is therefore a difference in the simulator, not in the
    /// script.
    ///
    /// 時点とスティックの値は `tools/unity_check/fly_check.py` のものである。
    /// よって 2 つの実行は、同じ飛行を違う頼み方で求めたものになる。ブラウザは
    /// HTTP 越しの `rc.set` で、エディタは同じフレームを直接置くことで。2 つの
    /// トレースが食い違うなら、それは台本ではなくシミュレータの違いである。
    ///
    /// Not a judgement: it records and writes. The verdict belongs to
    /// `trace_diff.py`, which can see both runs and this can see only one.
    /// 判定はしない。記録して書くだけである。合否は `trace_diff.py` のもので、
    /// あちらは両方の実行を見られるが、これは片方しか見られない。
    ///
    /// @design tools/unity_check/fly_check.py
    /// </summary>
    public sealed class TraceCaptureTest
    {
        // The moments `fly_check.py` uses, in simulated seconds.
        // `fly_check.py` が使う時点。シミュレーションの秒で表す。
        private const double ArmAtSeconds = 4.0;
        private const double ClimbAtSeconds = 5.0;
        private const double HoldAtSeconds = 6.3;
        private const double LandAtSeconds = 16.3;   // HOLD_AT_S + 10 s hold
        private const double EndAtSeconds = 22.3;    // + DESCEND_SECONDS
        private const ushort ClimbThrottle = 3243;   // CLIMB_THROTTLE

        // The browser runs at the per-frame ceiling on a throttled tab, so the
        // editor is asked for the same shape rather than one tick a frame.
        // 絞られたタブのブラウザは 1 フレームで上限まで回す。よってエディタにも
        // 1 フレーム 1 刻みではなく同じ形を求める。
        private const int TicksPerFrame = SimClock.MaxTicksPerFrame;

        [SetUp]
        public void SetUp()
        {
            bool cannotFly = !RaycastFlightScene.DylibExists;
            if (cannotFly)
            {
                Assert.Ignore(
                    "the firmware dylib is not built — run " +
                    "`nix develop -c just unity-native-build` first");
            }
        }

        /// <summary>
        /// Fly the check's script and write `logs/unity/editor.trace.jsonl`.
        /// 確認の台本を飛ばし、`logs/unity/editor.trace.jsonl` を書く。
        /// </summary>
        [Test]
        public void CaptureTheCheckFlightAsATrace()
        {
            var scene = new RaycastFlightScene();
            try
            {
                Assert.That(scene.Build(), Is.True,
                            $"sfu_boot failed: {scene.Firmware?.LastError}");
                scene.Trace.Start();

                int ticksThisFrame = 0;
                double nowSeconds = 0.0;

                while (nowSeconds < EndAtSeconds)
                {
                    bool frameHasEnded = ticksThisFrame >= TicksPerFrame;
                    if (frameHasEnded)
                    {
                        scene.EndFrame();
                        ticksThisFrame = 0;
                    }

                    scene.Sticks = SticksAt(nowSeconds);
                    Assert.That(scene.RunOneTick(), Is.EqualTo(SfuAbi.Ok),
                                $"a tick failed at {nowSeconds:F2} s");

                    ticksThisFrame += 1;
                    nowSeconds = scene.LastResult.NowMicroseconds * 1e-6;
                }

                string path = Path.Combine(
                    RepositoryRoot(), "logs", "unity", "editor.trace.jsonl");
                scene.WriteTrace(path, "editor");

                Assert.That(File.Exists(path), Is.True, $"no trace at {path}");
                UnityEngine.Debug.Log(
                    $"[TraceCapture] wrote {scene.Trace.Count} ticks to {path}");
            }
            finally
            {
                scene.Dispose();
            }
        }

        /// <summary>
        /// The sticks `fly_check.py` holds at a moment. Its `rc.set` carries
        /// the ARM bit in every frame after the ARM press, which is what a real
        /// transmitter's frames do.
        /// `fly_check.py` がある時点で持つスティックの値。その `rc.set` は、ARM を
        /// 押した後の全てのフレームで ARM のビットを運ぶ。実機の送信機のフレームが
        /// するのと同じである。
        /// </summary>
        private static RcFrame SticksAt(double nowSeconds)
        {
            bool isBeforeArm = nowSeconds < ArmAtSeconds;
            if (isBeforeArm)
            {
                return Frame(RcScale.Centre, 0);
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

            byte holding = (byte)(SfuAbi.FlagArm | SfuAbi.FlagAltitudeMode);
            bool isHolding = nowSeconds < LandAtSeconds;
            if (isHolding)
            {
                return Frame(RcScale.Centre, holding);
            }

            return Frame(RcScale.Minimum, holding);
        }

        private static RcFrame Frame(ushort throttle, byte flags)
        {
            return new RcFrame(
                throttle, RcScale.Centre, RcScale.Centre, RcScale.Centre, flags);
        }

        /// <summary>
        /// The repository root, from the project folder Unity was opened on.
        /// `Application.dataPath` ends in `simulator/unity/Assets`.
        /// リポジトリの根。Unity が開いたプロジェクトのフォルダから求める。
        /// `Application.dataPath` は `simulator/unity/Assets` で終わる。
        /// </summary>
        private static string RepositoryRoot()
        {
            var assets = new DirectoryInfo(UnityEngine.Application.dataPath);
            DirectoryInfo root = assets.Parent?.Parent?.Parent;
            bool isMissing = root == null;
            if (isMissing)
            {
                throw new InvalidOperationException(
                    $"could not find the repository root above {assets.FullName}");
            }
            return root.FullName;
        }
    }
}
