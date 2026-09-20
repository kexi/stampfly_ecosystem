/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — chase camera wheel tests).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Ui;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// Pins how a scroll reading becomes steps, because the units and the sign
    /// were MEASURED rather than derived and nothing else records them.
    ///
    /// In Chrome, Unity's WebGL mouse reports 1 per notch and inverts the sign a
    /// browser uses: a wheel pushed forward gives <c>deltaY</c> -120 and arrives
    /// here as +1. A desktop player reports the 120 Windows uses. The first
    /// version of this code assumed 120 everywhere and the wheel did nothing at
    /// all in the browser -- a whole notch came in as 1, which divided by 120 and
    /// truncated to zero.
    ///
    /// スクロールの値が何段になるかを固定する。単位と符号は導出ではなく**実測**した
    /// もので、他にそれを記録するものが無いからである。
    ///
    /// Chrome では Unity の WebGL のマウスが 1 刻みにつき 1 を報告し、ブラウザの符号を
    /// 反転する。ホイールを前へ回すと <c>deltaY</c> は −120 で、ここへは +1 として
    /// 届く。デスクトップのプレイヤーは Windows の 120 を報告する。この実装の最初の版は
    /// どこでも 120 だと決めつけ、ブラウザではホイールが何もしなかった。1 刻みまるごとが
    /// 1 として届き、120 で割って切り捨てて 0 になっていたのである。
    /// </summary>
    public sealed class ChaseWheelTest
    {
        /// <summary>
        /// One notch as Chrome reports it is one step, and its sign already means
        /// "closer". This is the reading that used to be thrown away.
        /// Chrome が報告する 1 刻みが 1 段になり、その符号は既に「寄る」を意味する。
        /// かつて捨てられていたのがこの値である。
        /// </summary>
        [Test]
        public void OneNotchInTheBrowsersUnitIsOneStep()
        {
            Assert.That(ChaseCameraControls.NotchesOf(1.0f), Is.EqualTo(1));
            Assert.That(ChaseCameraControls.NotchesOf(-1.0f), Is.EqualTo(-1));
        }

        /// <summary>
        /// One notch in the units a desktop player uses is also one step, so the
        /// same build behaves the same way in the editor.
        /// デスクトップのプレイヤーが使う単位の 1 刻みも 1 段になる。同じビルドが
        /// エディタでも同じように振る舞うためである。
        /// </summary>
        [Test]
        public void OneNotchInWindowsUnitsIsAlsoOneStep()
        {
            Assert.That(
                ChaseCameraControls.NotchesOf(
                    ChaseCameraControls.ScrollPerNotchInWindowsUnits),
                Is.EqualTo(1));
            Assert.That(
                ChaseCameraControls.NotchesOf(
                    -ChaseCameraControls.ScrollPerNotchInWindowsUnits),
                Is.EqualTo(-1));
        }

        /// <summary>
        /// Several notches in one frame's reading move that many steps, which is
        /// what a wheel flicked hard produces.
        /// 1 フレームの値に複数の刻みが入っていれば、その数だけ段が動く。ホイールを
        /// 強くはじいたときに出るのがこれである。
        /// </summary>
        [Test]
        public void SeveralNotchesInOneReadingMoveThatManySteps()
        {
            Assert.That(ChaseCameraControls.NotchesOf(3.0f), Is.EqualTo(3));
            Assert.That(
                ChaseCameraControls.NotchesOf(
                    3.0f * ChaseCameraControls.ScrollPerNotchInWindowsUnits),
                Is.EqualTo(3));
        }

        /// <summary>
        /// A reading too small to make one notch moves nothing, rather than a
        /// fraction of a step on every frame a finger rests on a trackpad.
        /// 1 刻みに足りない値は何も動かさない。トラックパッドに指を置いている間、
        /// 毎フレーム 1 段の何分の 1 かを動かすことにはならない。
        /// </summary>
        [Test]
        public void AReadingTooSmallForOneNotchMovesNothing()
        {
            Assert.That(ChaseCameraControls.NotchesOf(0.0f), Is.EqualTo(0));
            Assert.That(ChaseCameraControls.NotchesOf(0.4f), Is.EqualTo(0));
            Assert.That(ChaseCameraControls.NotchesOf(-0.9f), Is.EqualTo(0));
        }

        /// <summary>
        /// However large a reading is, one frame moves at most the ceiling. A
        /// trackpad flick reports a large amount at once, and crossing the whole
        /// range in one frame leaves a person hunting for the vehicle.
        /// 値がどれだけ大きくても、1 フレームで動くのは上限までである。トラックパッドの
        /// はじきは一度に大きな量を報告し、1 フレームで範囲を渡りきると、人は機体を
        /// 探すことになる。
        /// </summary>
        [Test]
        public void OneFrameNeverMovesMoreThanTheCeiling()
        {
            int ceiling = ChaseCameraControls.MaximumStepsPerFrame;

            Assert.That(ChaseCameraControls.NotchesOf(9999.0f), Is.EqualTo(ceiling));
            Assert.That(ChaseCameraControls.NotchesOf(-9999.0f), Is.EqualTo(-ceiling));
        }

        /// <summary>
        /// The threshold that tells the two units apart sits clear of both: above
        /// the few notches a hand makes in one frame, below one Windows notch.
        /// A threshold inside either range would read one convention as the other.
        /// 2 つの単位を見分ける境目は、どちらからも離れている。手が 1 フレームで作る
        /// 数刻みより上で、Windows の 1 刻みより下である。どちらかの範囲の中に境目が
        /// あると、一方の流儀を他方として読んでしまう。
        /// </summary>
        [Test]
        public void TheThresholdSitsClearOfBothConventions()
        {
            Assert.That(ChaseCameraControls.WindowsUnitsThreshold,
                        Is.GreaterThan(ChaseCameraControls.MaximumStepsPerFrame),
                        "a hand's worth of notches would be read as Windows units");
            Assert.That(ChaseCameraControls.WindowsUnitsThreshold,
                        Is.LessThan(ChaseCameraControls.ScrollPerNotchInWindowsUnits),
                        "one Windows notch would be read as a count of notches");
        }
    }
}
