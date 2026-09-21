/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the stick-read cadence).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Input;
using StampFly.Sim;
using UnityEngine;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// The sticks are read once per rendered frame, not once per firmware tick.
    ///
    /// スティックを読むのは、ファームの 1 刻みごとではなく描画の 1 フレームに
    /// 1 回であること。
    ///
    /// <see cref="IRcSource.Read"/> says so in its own documentation, and
    /// <see cref="KeyboardRc"/> relies on it: a toggle is implemented with
    /// `wasPressedThisFrame`, which answers true for every call made during
    /// that frame. Reading it once a tick therefore toggled ARM as many times
    /// as the frame had ticks — at the twelve-tick ceiling a press of R did
    /// nothing at all, and whether it worked depended on the frame rate.
    ///
    /// <see cref="IRcSource.Read"/> は自身の文書でそう定めており、
    /// <see cref="KeyboardRc"/> はそれに頼っている。トグルは
    /// `wasPressedThisFrame` で実装されており、これはそのフレームの間の全ての
    /// 呼び出しに真を返す。よって刻みごとに読むと、ARM はフレームの刻みの数だけ
    /// トグルされていた。12 刻みの上限では R を押しても何も起きず、効くかどうかは
    /// フレーム率次第だった。
    ///
    /// @design simulator/unity/Assets/StampFly/Runtime/Input/IRcSource.cs
    /// </summary>
    public sealed class StickReadCadenceTest
    {
        /// <summary>
        /// A source that counts how often it was asked, standing in for the
        /// keyboard: a real <see cref="KeyboardRc"/> cannot be driven from a
        /// test, because the Input System reads the browser's own queue rather
        /// than anything a test can synthesise.
        /// 尋ねられた回数を数える入力源。キーボードの代役である。本物の
        /// <see cref="KeyboardRc"/> は試験から動かせない。Input System が、試験の
        /// 合成できるものではなくブラウザ自身の待ち行列を読むためである。
        /// </summary>
        private sealed class CountingRc : IRcSource
        {
            internal int Reads;
            internal int FlagQueries;
            internal RcFrame Frame = RcFrame.Centred;

            public RcFrame Read()
            {
                Reads += 1;
                return Frame;
            }

            /// <summary>
            /// Asked once a TICK, unlike <see cref="Read"/>. Counted separately so
            /// the test can show the two cadences really are different.
            /// <see cref="Read"/> と違い**刻み**ごとに尋ねられる。2 つの周期が実際に
            /// 違うことを試験が示せるよう、別に数える。
            /// </summary>
            public byte FlagsAt(in RcFrame frame, long nowMicroseconds)
            {
                FlagQueries += 1;
                return frame.Flags;
            }
        }

        private GameObject host;

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

        [TearDown]
        public void TearDown()
        {
            if (host != null)
            {
                Object.DestroyImmediate(host);
                host = null;
            }

            Physics.simulationMode = SimulationMode.FixedUpdate;
        }

        /// <summary>
        /// A frame that runs twelve ticks reads the sticks once, and the one
        /// frame it read is what every one of those ticks flew with.
        /// 12 刻みを回すフレームはスティックを 1 回だけ読み、その 1 回で読んだ
        /// 値で 12 刻みすべてが飛ぶこと。
        /// </summary>
        [Test]
        public void AFrameOfTwelveTicksReadsTheSticksOnce()
        {
            var counting = new CountingRc();
            SimLoop loop = BuildLoop();
            loop.RcSource = counting;

            // The clock's own ceiling is what a throttled tab runs every frame,
            // so asking for exactly that is asking for the failing case.
            // 絞られたタブが毎フレーム回すのは時計自身の上限なので、ちょうどその
            // 数を求めることが、壊れていた場合を求めることになる。
            loop.Clock.StepOnce(SimClock.MaxTicksPerFrame);
            RunOneFrame(loop);

            Assert.That(counting.Reads, Is.EqualTo(1),
                        $"a frame of {SimClock.MaxTicksPerFrame} ticks read the " +
                        $"sticks {counting.Reads} times; a toggle keyed on " +
                        "wasPressedThisFrame would have fired that many times");

            // The FLAGS are a per-tick question, because a momentary button is a
            // pulse of a fixed length in simulated time and a frame spans many
            // ticks of it. The two cadences are deliberately different, and that
            // is the whole reason `FlagsAt` exists apart from `Read`.
            // **フラグ**は刻みごとの問いである。モーメンタリボタンはシミュレーションの
            // 時間で一定の長さを持つパルスで、1 フレームはその何刻みぶんにもなるから
            // である。2 つの周期は意図して違えてあり、`FlagsAt` が `Read` と別に在る
            // 理由はまさにそれである。
            Assert.That(counting.FlagQueries,
                        Is.EqualTo(SimClock.MaxTicksPerFrame),
                        "the flags were not asked for once per tick");
        }

        /// <summary>
        /// Across several frames the count follows the frames, not the ticks.
        /// 数フレームにわたって、回数は刻みではなくフレームに従うこと。
        /// </summary>
        [Test]
        public void TheReadCountFollowsFramesRatherThanTicks()
        {
            var counting = new CountingRc();
            SimLoop loop = BuildLoop();
            loop.RcSource = counting;

            const int Frames = 5;
            for (int frame = 0; frame < Frames; frame++)
            {
                loop.Clock.StepOnce(SimClock.MaxTicksPerFrame);
                RunOneFrame(loop);
            }

            Assert.That(counting.Reads, Is.EqualTo(Frames),
                        "the sticks were not read exactly once a frame");
            Assert.That(loop.Clock.TotalTicks,
                        Is.EqualTo((long)Frames * SimClock.MaxTicksPerFrame),
                        "the frames did not run the ticks they were asked for");
        }

        /// <summary>
        /// Build a loop with a floor under it and the firmware running, then
        /// leave it ready to be stepped a frame at a time.
        /// 床を敷き、ファームを動かしたループを組み立て、1 フレームずつ進められる
        /// 状態にして返す。
        /// </summary>
        private SimLoop BuildLoop()
        {
            PhysicsStepSettings.Apply();

            GameObject floor = GameObject.CreatePrimitive(PrimitiveType.Cube);
            floor.transform.position = new Vector3(0.0f, -0.25f, 0.0f);
            floor.transform.localScale = new Vector3(20.0f, 0.5f, 20.0f);

            host = new GameObject("Vehicle");
            floor.transform.SetParent(host.transform, true);
            host.transform.position = new Vector3(0.0f, VehicleBody.RestingCentreHeight, 0.0f);

            var body = host.AddComponent<Rigidbody>();
            var box = host.AddComponent<BoxCollider>();
            VehicleBody.Apply(body, box);
            Physics.SyncTransforms();

            SimLoop loop = host.AddComponent<SimLoop>();

            // `AddComponent` sends `Awake`, but `Start` does not arrive until
            // Unity's next frame, and it is `Start` that powers the firmware on.
            // This test drives `Update` itself rather than waiting for frames,
            // so it must send `Start` itself too; without it the first `Update`
            // dereferences a firmware that was never created.
            // `AddComponent` は `Awake` を送るが、`Start` は Unity の次のフレーム
            // まで来ない。そしてファームの電源を入れるのは `Start` である。この
            // 試験はフレームを待たず自分で `Update` を回すので、`Start` も自分で
            // 送らねばならない。送らないと最初の `Update` が、作られていない
            // ファームを参照する。
            Invoke(loop, "Start");
            RunUntilRunning(loop);
            return loop;
        }

        /// <summary>
        /// Drive the loop's own <c>Update</c> until the firmware is up. The
        /// editor's firmware loads synchronously, so this is a handful of
        /// invocations rather than a wait.
        /// ファームが起きるまでループ自身の <c>Update</c> を回す。エディタの
        /// ファームは同期で読み込まれるので、待ちではなく数回の呼び出しで済む。
        /// </summary>
        private static void RunUntilRunning(SimLoop loop)
        {
            const int GivenUpAfter = 8;
            for (int attempt = 0; attempt < GivenUpAfter; attempt++)
            {
                bool isRunning = loop.Firmware != null &&
                                 loop.Firmware.Status == Native.FirmwareStatus.Running;
                if (isRunning)
                {
                    return;
                }

                Invoke(loop, "Update");
            }

            Assert.Fail("the firmware never reached Running: " +
                        loop.Firmware?.LastError);
        }

        /// <summary>One turn of the loop's Update. / ループの Update を 1 回。</summary>
        private static void RunOneFrame(SimLoop loop)
        {
            Invoke(loop, "Update");
        }

        /// <summary>
        /// Call one of the loop's private MonoBehaviour messages. A PlayMode
        /// test cannot wait for Unity to send them here, because the clock is
        /// driven a frame at a time rather than in real time.
        /// ループの private な MonoBehaviour のメッセージを呼ぶ。この試験は時計を
        /// 実時間ではなく 1 フレームずつ進めるので、Unity が送ってくるのを待てない。
        /// </summary>
        private static void Invoke(SimLoop loop, string method)
        {
            typeof(SimLoop)
                .GetMethod(method, System.Reflection.BindingFlags.Instance |
                                   System.Reflection.BindingFlags.NonPublic)
                .Invoke(loop, null);
        }
    }
}
