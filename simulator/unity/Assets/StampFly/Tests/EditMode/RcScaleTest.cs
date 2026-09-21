/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — stick scale tests).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Input;
using StampFly.Native;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// Guarantees that a stick deflection becomes the raw ADC count the
    /// firmware expects, and that a released stick returns to centre.
    ///
    /// スティックの振れ幅が、ファームが待つ生の ADC の値になること、および離した
    /// スティックが中央へ戻ることを保証する。
    ///
    /// The scale matters because the same numbers appear in `*.scn` files and in
    /// `scenario_inject.hpp`: a value typed here has to mean the same deflection
    /// a scenario would encode, or a flight replayed from a scenario and one
    /// flown by hand would not be comparable.
    ///
    /// 尺度が問題になるのは、同じ数値が `*.scn` と `scenario_inject.hpp` にも
    /// 現れるからである。ここで打った値は台本が書く振れ幅と同じ意味でなければ
    /// ならない。さもなくば、台本から再生した飛行と手で飛ばした飛行を比べられない。
    /// </summary>
    public sealed class RcScaleTest
    {
        [Test]
        public void NoDeflectionIsTheCentreCount()
        {
            Assert.That(RcScale.FromDeflection(0.0f), Is.EqualTo(RcScale.Centre));
        }

        /// <summary>
        /// Full deflection reaches the top of the 12-bit range, as `sf sils fly`
        /// does with the same 20.47 counts per unit.
        /// 振り切りで 12 bit の上限に届く。`sf sils fly` が同じ 1 単位 20.47 で
        /// そうするのと同じである。
        /// </summary>
        [Test]
        public void FullDeflectionReachesTheTopOfTheRange()
        {
            Assert.That(RcScale.FromDeflection(100.0f), Is.EqualTo(RcScale.Maximum));
        }

        /// <summary>
        /// Full negative deflection reaches the bottom of the range, to within
        /// the one count the scale's own arithmetic leaves: 2048 - 100x20.47 is
        /// 1, not 0, and `sf sils fly`'s `_fly_adc` produces the same 1. The
        /// asymmetry is the ADC's, not a rounding mistake — a 12-bit count has
        /// 2048 values below centre and 2047 above it.
        /// 振り切りで範囲の下端に届く。尺度自身の計算が残す 1 単位の範囲でのこと
        /// である。2048 - 100x20.47 は 0 ではなく 1 で、`sf sils fly` の
        /// `_fly_adc` も同じ 1 を出す。この非対称は ADC のものであって丸めの誤り
        /// ではない。12 bit の値は中央の下に 2048、上に 2047 ある。
        /// </summary>
        [Test]
        public void FullNegativeDeflectionReachesTheBottomOfTheRange()
        {
            Assert.That(RcScale.FromDeflection(-100.0f),
                        Is.EqualTo(RcScale.Minimum).Within(1));
        }

        /// <summary>
        /// A deflection past the end is clamped rather than wrapped, so a bad
        /// value cannot come out as the opposite stick position.
        /// 端を越えた振れ幅は回り込まずに頭打ちになる。誤った値が反対側のスティック
        /// 位置として出てこないようにするためである。
        /// </summary>
        [Test]
        public void DeflectionPastTheEndIsClamped()
        {
            Assert.That(RcScale.FromDeflection(400.0f), Is.EqualTo(RcScale.Maximum));
            Assert.That(RcScale.FromDeflection(-400.0f), Is.EqualTo(RcScale.Minimum));
        }

        /// <summary>
        /// Half deflection is half way up, to within the rounding one count
        /// allows.
        /// 半分の振れ幅は中ほどに来る。1 単位の丸めの範囲でのことである。
        /// </summary>
        [Test]
        public void HalfDeflectionIsHalfWayUp()
        {
            ushort counts = RcScale.FromDeflection(50.0f);
            Assert.That(counts, Is.EqualTo(RcScale.Centre + 1024).Within(2));
        }

        /// <summary>
        /// The frame a source produces before anything is touched has every
        /// stick centred and no flag set — so nothing is armed by default.
        /// 何にも触れていない段階で作られるフレームは、全てのスティックが中央で
        /// フラグも無い。よって既定で何かが ARM されることはない。
        /// </summary>
        [Test]
        public void ACentredFrameArmsNothing()
        {
            RcFrame frame = RcFrame.Centred;

            Assert.That(frame.Throttle, Is.EqualTo(RcScale.Centre));
            Assert.That(frame.Roll, Is.EqualTo(RcScale.Centre));
            Assert.That(frame.Pitch, Is.EqualTo(RcScale.Centre));
            Assert.That(frame.Yaw, Is.EqualTo(RcScale.Centre));
            Assert.That(frame.Flags, Is.EqualTo(0));
        }

        /// <summary>
        /// A source with no keyboard attached — as in a batch-mode test run or
        /// before the browser has reported one — produces the centred frame
        /// rather than throwing.
        /// キーボードが繋がっていない場合 ― バッチでの試験の実行中や、ブラウザが
        /// まだ報告していない間 ― は、例外ではなく中央のフレームを返す。
        /// </summary>
        [Test]
        public void AKeyboardSourceWithNoKeyboardIsCentred()
        {
            var source = new KeyboardRc();

            RcFrame frame = source.Read();

            Assert.That(frame.Throttle, Is.EqualTo(RcScale.Centre));
            Assert.That(frame.Flags, Is.EqualTo(0));
        }

        /// <summary>
        /// A fresh source sends no flag and has no press in flight, and Reset
        /// returns it there — which is what a power cycle relies on so a new
        /// firmware does not meet the last flight's mode switch or a half-finished
        /// ARM pulse.
        /// 作った直後の入力はフラグを送らず、途中の押下も持たない。Reset はそこへ
        /// 戻す。電源の入れ直しがこれに頼っており、新しいファームが前の飛行のモードの
        /// スイッチや途中の ARM のパルスに出会わないようにしている。
        /// </summary>
        [Test]
        public void ResetClearsTheSwitchesAndAnyPressInFlight()
        {
            var source = new KeyboardRc();

            source.PressAltitudeHold();
            source.PressArm();
            source.Reset();

            Assert.That(source.IsAltitudeHold, Is.False);
            Assert.That(source.Read().Flags, Is.EqualTo(0));
            Assert.That(source.IsArmPulseOn(0L), Is.False,
                        "a press survived the power cycle");
        }

        /// <summary>
        /// The flag bits are the ControlPacket's own, so what this project sends
        /// and what the firmware reads cannot drift apart.
        /// フラグのビットは ControlPacket 自身のものである。この企画が送るものと
        /// ファームが読むものが食い違いようがない。
        /// </summary>
        [Test]
        public void FlagBitsMatchTheControlPacket()
        {
            Assert.That(SfuAbi.FlagArm, Is.EqualTo(0x01));
            Assert.That(SfuAbi.FlagAltitudeMode, Is.EqualTo(0x08));
        }
    }
}
