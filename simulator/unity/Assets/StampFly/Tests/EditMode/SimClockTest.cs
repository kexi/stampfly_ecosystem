/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — pacing tests).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Sim;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// Guarantees the pacing rules the plan states: no tick is ever skipped, a
    /// frame runs a bounded number of them, a pause holds virtual time still,
    /// and a stall's backlog is dropped rather than chased.
    ///
    /// 計画が述べるペース配分の決まりを保証する。刻みを飛ばさないこと、1 フレームで
    /// 進める数に上限が在ること、一時停止が仮想時間を止めること、そして停止の溜まりを
    /// 追いかけずに捨てることである。
    /// </summary>
    public sealed class SimClockTest
    {
        /// <summary>One frame at 60 fps, in seconds. / 60fps の 1 フレーム [秒]。</summary>
        private const double FrameAt60Fps = 1.0 / 60.0;

        /// <summary>
        /// A 60 fps frame asks for the ticks that fit in it: 16.67 ms holds six
        /// whole 2.5 ms ticks, and the 1.67 ms left over is carried to the next
        /// frame rather than lost.
        /// 60fps の 1 フレームは、そこに収まる刻みを求める。16.67 ms には 2.5 ms の
        /// 刻みが 6 つ入り、余る 1.67 ms は失われずに次のフレームへ繰り越される。
        /// </summary>
        [Test]
        public void OneFrameAtSixtyFpsRunsSixTicks()
        {
            var clock = new SimClock();

            Assert.That(clock.TicksForFrame(FrameAt60Fps), Is.EqualTo(6));
        }

        /// <summary>
        /// The remainder is carried, so over many frames virtual time keeps up
        /// with real time rather than falling steadily behind.
        /// 端数は繰り越すので、多くのフレームにわたって仮想時間は実時間に追いつき
        /// 続ける。じりじり遅れていくことはない。
        /// </summary>
        [Test]
        public void TheRemainderIsCarriedBetweenFrames()
        {
            var clock = new SimClock();

            int total = 0;
            for (int frame = 0; frame < 60; frame++)
            {
                total += clock.TicksForFrame(FrameAt60Fps);
            }

            // One second at 400 Hz is 400 ticks; the carry keeps it within one.
            // 1 秒は 400 Hz で 400 刻み。繰り越しにより誤差は 1 以内に収まる。
            Assert.That(total, Is.EqualTo(400).Within(1));
            Assert.That(clock.VirtualSeconds, Is.EqualTo(1.0).Within(0.01));
        }

        /// <summary>
        /// A frame never runs more than the ceiling, however long it was, so a
        /// slow frame cannot ask the next one to be slower still.
        /// フレームが進めるのは、どれだけ長くても上限までである。遅いフレームが
        /// 次のフレームをさらに遅くさせることはない。
        /// </summary>
        [Test]
        public void AFrameNeverRunsMoreThanTheCeiling()
        {
            var clock = new SimClock();

            Assert.That(clock.TicksForFrame(0.2),
                        Is.EqualTo(SimClock.MaxTicksPerFrame));
        }

        /// <summary>
        /// A backlog past the ceiling is reported, so the readout can say the
        /// host is behind rather than letting it pass unseen.
        /// 上限を越えた溜まりは報告される。見過ごさせず、ホストが遅れていると
        /// 表示できるようにするためである。
        /// </summary>
        [Test]
        public void BeingBehindIsReported()
        {
            var clock = new SimClock();

            clock.TicksForFrame(0.2);

            Assert.That(clock.IsBehind, Is.True);
        }

        /// <summary>
        /// Virtual time does not move while paused — the check the plan's
        /// stage 3 asks for by name.
        /// 一時停止している間、仮想時間は動かない。計画の段階 3 が名指しで求める
        /// 検査である。
        /// </summary>
        [Test]
        public void PausedTimeDoesNotMove()
        {
            var clock = new SimClock();
            clock.TicksForFrame(FrameAt60Fps);
            double beforePause = clock.VirtualSeconds;

            clock.Pause();
            for (int frame = 0; frame < 10; frame++)
            {
                Assert.That(clock.TicksForFrame(FrameAt60Fps), Is.EqualTo(0));
            }

            Assert.That(clock.VirtualSeconds, Is.EqualTo(beforePause));
        }

        /// <summary>
        /// A single step runs exactly one tick and then holds, whether or not
        /// the clock was already paused.
        /// コマ送りはちょうど 1 刻み進めてから止まる。すでに止まっていたかどうかに
        /// よらない。
        /// </summary>
        [Test]
        public void ASingleStepRunsExactlyOneTick()
        {
            var clock = new SimClock();

            clock.StepOnce();

            Assert.That(clock.TicksForFrame(FrameAt60Fps), Is.EqualTo(1));
            Assert.That(clock.TicksForFrame(FrameAt60Fps), Is.EqualTo(0));
            Assert.That(clock.IsPaused, Is.True);
        }

        /// <summary>
        /// Resuming does not owe the time spent paused, so the simulation
        /// carries on from where it stopped instead of fast-forwarding.
        /// 再開しても、止まっていた間の時間は借りにならない。早送りではなく、
        /// 止まった所から続く。
        /// </summary>
        [Test]
        public void ResumingOwesNothingForThePause()
        {
            var clock = new SimClock();
            clock.Pause();
            clock.TicksForFrame(2.0);

            clock.Resume();

            Assert.That(clock.TicksForFrame(FrameAt60Fps), Is.EqualTo(6));
        }

        /// <summary>
        /// A stall's backlog is dropped: coming back from a backgrounded tab
        /// must not fast-forward the flight through a minute of simulation.
        /// 停止の溜まりは捨てる。背面のタブから戻ったとき、1 分ぶんのシミュレー
        /// ションを早送りしてはならない。
        /// </summary>
        [Test]
        public void AStallsBacklogIsDropped()
        {
            var clock = new SimClock();

            clock.TicksForFrame(60.0);

            // At most the ceiling comes out of a 60-second gap, and virtual time
            // has moved by that and no more.
            // 60 秒の空白から出てくるのは上限までで、仮想時間もそのぶんしか動かない。
            Assert.That(clock.VirtualSeconds,
                        Is.LessThanOrEqualTo(
                            SimClock.MaxTicksPerFrame * SimClock.TickSeconds));
        }

        /// <summary>
        /// Twice the speed asks for twice the ticks from the same frame.
        /// 倍の速さは、同じフレームから倍の刻みを求める。
        /// </summary>
        [Test]
        public void DoubleSpeedRunsTwiceAsManyTicks()
        {
            var clock = new SimClock();
            clock.SetSpeed(2.0f);

            Assert.That(clock.TicksForFrame(FrameAt60Fps), Is.EqualTo(12));
        }

        /// <summary>The speed stays inside the range the plan gives. / 倍率は計画が定める範囲に留まる。</summary>
        [Test]
        public void SpeedIsClampedToItsRange()
        {
            var clock = new SimClock();

            clock.SetSpeed(100.0f);
            Assert.That(clock.Speed, Is.EqualTo(SimClock.MaxSpeed));

            clock.SetSpeed(0.0f);
            Assert.That(clock.Speed, Is.EqualTo(SimClock.MinSpeed));
        }

        /// <summary>
        /// A reset puts virtual time back to zero, which a power cycle needs so
        /// a fresh firmware starts from a clock of zero.
        /// 戻す操作は仮想時間を 0 に返す。新しいファームが 0 の時計から始まるよう、
        /// 電源の入れ直しがこれを要る。
        /// </summary>
        [Test]
        public void ResetReturnsVirtualTimeToZero()
        {
            var clock = new SimClock();
            clock.TicksForFrame(FrameAt60Fps);

            clock.Reset();

            Assert.That(clock.VirtualSeconds, Is.EqualTo(0.0));
            Assert.That(clock.TotalTicks, Is.EqualTo(0));
        }

        /// <summary>
        /// The virtual clock in microseconds is exactly the tick count times the
        /// tick length — the same integer arithmetic the ABI promises.
        /// マイクロ秒での仮想時計は、刻みの数と刻みの長さの積とちょうど等しい。
        /// ABI が約束するのと同じ整数の計算である。
        /// </summary>
        [Test]
        public void TheClockInMicrosecondsIsExact()
        {
            var clock = new SimClock();

            clock.StepOnce(7);
            clock.TicksForFrame(FrameAt60Fps);

            Assert.That(clock.VirtualMicroseconds,
                        Is.EqualTo(7L * SimClock.TickMicroseconds));
        }

        /// <summary>
        /// The measured rates come out once a second's worth of frames has been
        /// recorded, which is what the readout shows.
        /// 実測の速さは、1 秒ぶんのフレームを記録した時点で出る。表示が見せるのは
        /// それである。
        /// </summary>
        [Test]
        public void RatesAppearAfterOneSecondOfFrames()
        {
            var clock = new SimClock();

            for (int frame = 0; frame < 60; frame++)
            {
                int ticks = clock.TicksForFrame(FrameAt60Fps);
                clock.RecordFrame(FrameAt60Fps, ticks, ticks * 30e-6);
            }

            Assert.That(clock.RealTimeRatio, Is.EqualTo(1.0).Within(0.02));
            Assert.That(clock.FramesPerSecond, Is.EqualTo(60.0).Within(1.0));
            Assert.That(clock.MicrosecondsPerTick, Is.EqualTo(30.0).Within(1.0));
        }

        /// <summary>
        /// At speed 1.0 the simulation never runs FASTER than real time, whatever
        /// happened before. The plan says a loop that cannot keep up falls
        /// behind; it does not say it may then sprint to catch up, because a
        /// flight that fast-forwards is not the flight the pilot flew and a
        /// real-time ratio above 1.0 makes the readout unreadable.
        ///
        /// 倍速 1.0 では、それ以前に何があろうと、シミュレーションが実時間より
        /// **速く**進むことはない。計画は「追いつけない輪は遅れる」と定めており、
        /// その後で追いつくために駆けてよいとは定めていない。早送りされた飛行は
        /// 操縦者が飛ばした飛行ではなく、1.0 を超える実時間比は表示板を読めなく
        /// するからである。
        ///
        /// This is the check for a measured 1.875: a page left open in a
        /// throttled tab accumulated a debt no single stall ever dropped, and
        /// then ran the twelve-tick ceiling (30 ms of simulation) in every
        /// 16 ms frame for as long as the debt lasted.
        /// 実測の 1.875 に対する検査である。絞られたタブに開いたままのページが、
        /// どの停止でも捨てられない借りを溜め、その借りが続く限り、16 ms のどの
        /// フレームでも 12 刻みの上限（シミュレーション 30 ms）を走った。
        /// </summary>
        [Test]
        public void AtNormalSpeedTheSimulationNeverOutrunsRealTime()
        {
            var clock = new SimClock();

            // Fall behind the way a throttled tab does: frames long enough to
            // build a debt, but each below the half-second a stall needs.
            // 絞られたタブと同じ形で遅れる。借りが溜まるだけ長く、しかし 1 つ 1 つ
            // は停止とみなす半秒より短いフレームである。
            const double ThrottledFrame = 0.4;
            for (int frame = 0; frame < 25; frame++)
            {
                int ticks = clock.TicksForFrame(ThrottledFrame);
                clock.RecordFrame(ThrottledFrame, ticks, ticks * 30e-6);
            }

            // Then run a second of healthy 60 fps frames and read the ratio.
            // 続いて健全な 60fps のフレームを 1 秒ぶん回し、比を読む。
            for (int frame = 0; frame < 60; frame++)
            {
                int ticks = clock.TicksForFrame(FrameAt60Fps);
                clock.RecordFrame(FrameAt60Fps, ticks, ticks * 30e-6);
            }

            Assert.That(clock.RealTimeRatio, Is.LessThanOrEqualTo(1.02),
                        "the simulation outran real time while catching up");
        }

        /// <summary>
        /// The debt one slow frame leaves is at most what the next frame can
        /// pay off. Without that cap it grows for as long as the page is slow,
        /// which is what let the ratio above exceed 1.
        /// 遅いフレーム 1 つが残す借りは、多くとも次のフレームが返せる量である。
        /// この上限が無いと、ページが遅い間ずっと借りは膨らむ。上の比が 1 を
        /// 超えたのはそれによる。
        /// </summary>
        [Test]
        public void TheBacklogNeverGrowsBeyondOneFramesWorth()
        {
            var clock = new SimClock();

            // Twenty throttled frames owe 8 s of simulation between them; the
            // clock must not have promised to run all of it.
            // 絞られたフレーム 20 個は合わせて 8 秒ぶんの借りになる。時計がその
            // 全てを走ると約束してはならない。
            for (int frame = 0; frame < 20; frame++)
            {
                clock.TicksForFrame(0.4);
            }

            double before = clock.VirtualSeconds;
            int caughtUp = 0;
            for (int frame = 0; frame < 10; frame++)
            {
                caughtUp += clock.TicksForFrame(FrameAt60Fps);
            }

            // Ten 60 fps frames are 167 ms of real time. A clock that had kept
            // the whole backlog would run 12 ticks each time (120 ticks, 300 ms
            // of simulation); one that capped it runs about what the frames ask
            // for, plus one frame's worth of catching up.
            // 60fps のフレーム 10 個は実時間 167 ms である。借りを丸ごと抱えた
            // 時計は毎回 12 刻み（120 刻み、シミュレーション 300 ms）を走る。
            // 上限を置いた時計は、フレームが求める分に 1 フレーム分の追いつきを
            // 足した程度を走る。
            Assert.That(caughtUp, Is.LessThan(12 * 10));
            Assert.That(clock.VirtualSeconds - before,
                        Is.LessThan(10 * FrameAt60Fps + MaxTicksWorthOfSeconds + 1e-9));
        }

        /// <summary>One frame's ceiling, in seconds. / 1 フレームの上限を秒で表したもの。</summary>
        private static double MaxTicksWorthOfSeconds =>
            SimClock.MaxTicksPerFrame * SimClock.TickSeconds;
    }
}
