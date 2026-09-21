/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the ARM button's pulse).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Input;
using StampFly.Native;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// The ARM flag is a momentary BUTTON: R sends a pulse of a fixed length in
    /// SIMULATED time, and the simulator holds no ARM state of its own.
    ///
    /// ARM のフラグはモーメンタリ**ボタン**である。R は**シミュレーションの時間**で
    /// 一定の長さを持つパルスを送り、シミュレータ側は ARM の状態を一切持たない。
    ///
    /// This is what the firmware defines
    /// (`firmware/vehicle/tasks/state_task.cpp:339-357`): the wire flag is 1 only
    /// while the button is held, each RISING edge toggles arm/disarm, and the
    /// release does nothing. The bridge (`sfu_api.h` `rc_flags`) copies the bits
    /// into the ControlPacket without translating them, so the pulse this class
    /// sends is what the firmware's edge detector sees.
    ///
    /// これはファームが定めることである
    /// （`firmware/vehicle/tasks/state_task.cpp:339-357`）。電文のフラグは押している間
    /// だけ 1 で、**立ち上がり**ごとに arm/disarm がトグルし、離しても何も起きない。
    /// 橋渡し（`sfu_api.h` の `rc_flags`）はビットを変換せず ControlPacket に写すので、
    /// ここが送るパルスがそのままファームのエッジ検出に見える。
    ///
    /// ## What was wrong before / 以前どこが誤っていたか
    ///
    /// The flag used to be a LATCH holding "the state the pilot wants". Pressing R
    /// while armed cleared it, which is a FALLING edge — and a falling edge does
    /// nothing, so the vehicle stayed armed. R therefore worked only every other
    /// time even without ever touching the floor: arm, then a press that did
    /// nothing, then arm again. An earlier repair kept the latch and added a
    /// reconciliation that put the bit back up 20 frames later; the disarm then
    /// happened on THAT rising edge, about a third of a second late, which made
    /// the symptom go away for the wrong reason. This suite exists so the latch
    /// cannot come back.
    ///
    /// 以前このフラグは「利用者が望む状態」を保つ**ラッチ**だった。ARM 中に R を押すと
    /// それを落とすが、これは**立ち下がり**である。立ち下がりでは何も起きないので機体は
    /// ARM のままだった。よって床に当てずとも R は 2 回に 1 回しか効かなかった。ARM →
    /// 何も起きない押下 → 再び ARM である。先の修正はラッチを残したまま突き合わせを足し、
    /// 20 フレーム後にビットを立て直していた。DISARM は**その**立ち上がりで、約 3 分の
    /// 1 秒遅れて起きており、症状は誤った理由で消えていた。ラッチが戻らないように
    /// この一式が在る。
    /// </summary>
    public sealed class ArmButtonTest
    {
        /// <summary>A virtual time to start a pulse at, far from zero. / パルスを始める仮想時刻。0 から離してある。</summary>
        private const long StartMicroseconds = 4_000_000;

        /// <summary>
        /// A press raises the bit, and it falls again on its own. Both halves
        /// matter: the rise is the edge the firmware acts on, and the fall is what
        /// lets the NEXT press make another one.
        /// 押下でビットが上がり、自然に下がること。両方が重要である。上がりはファームが
        /// 反応するエッジであり、下がりは**次の**押下がもう 1 つのエッジを作れるように
        /// するものである。
        /// </summary>
        [Test]
        public void APressRaisesTheBitAndItFallsOnItsOwn()
        {
            var source = new KeyboardRc();
            source.PressArm();

            Assert.That(ArmBitAt(source, StartMicroseconds), Is.Not.Zero,
                        "the press did not raise the ARM bit");

            long justInside = StartMicroseconds + KeyboardRc.ArmPulseMicroseconds - 1;
            Assert.That(ArmBitAt(source, justInside), Is.Not.Zero,
                        "the pulse ended early");

            long justOutside = StartMicroseconds + KeyboardRc.ArmPulseMicroseconds;
            Assert.That(ArmBitAt(source, justOutside), Is.Zero,
                        "the bit never came back down, so the next press cannot " +
                        "produce a rising edge");
        }

        /// <summary>
        /// The pulse is long enough for the firmware's 50 Hz stick intake to
        /// sample it several times. One intake every 20 ms against a 100 ms pulse
        /// is about five, so losing one does not lose the press.
        /// パルスは、ファームの 50 Hz の取り込みが数回標本できる長さであること。
        /// 20 ms ごとの取り込みに対して 100 ms のパルスは約 5 回で、1 回失っても押下は
        /// 失われない。
        /// </summary>
        [Test]
        public void ThePulseCoversSeveralStickIntakes()
        {
            const long IntakePeriodMicroseconds = 20_000;   // 50 Hz

            var source = new KeyboardRc();
            source.PressArm();

            int intakesThatSawIt = 0;
            for (long at = StartMicroseconds;
                 at < StartMicroseconds + KeyboardRc.ArmPulseMicroseconds * 2;
                 at += IntakePeriodMicroseconds)
            {
                bool sawIt = ArmBitAt(source, at) != 0;
                if (sawIt) { intakesThatSawIt += 1; }
            }

            Assert.That(intakesThatSawIt, Is.GreaterThanOrEqualTo(3),
                        "a 50 Hz intake saw the pulse only " +
                        $"{intakesThatSawIt} time(s); one missed intake would " +
                        "then lose the press");
        }

        /// <summary>
        /// The pulse is measured in VIRTUAL time, so it does not depend on how
        /// many ticks a frame happened to run. A press asked about at widely
        /// spaced virtual times is over; asked about at close ones it is still on.
        /// パルスは**仮想時間**で計るので、1 フレームがたまたま何刻み回したかに
        /// 依存しないこと。離れた仮想時刻で尋ねれば終わっており、近い時刻で尋ねれば
        /// まだ出ている。
        /// </summary>
        [Test]
        public void ThePulseIsMeasuredInVirtualTimeNotInCalls()
        {
            var source = new KeyboardRc();
            source.PressArm();

            // Many queries, all inside the pulse: it stays on. A pulse counted in
            // calls or frames would have expired partway through this loop.
            // 多数回の問い合わせ。すべてパルスの内側なので、出たままである。呼び出し数や
            // フレーム数で数えるパルスなら、この繰り返しの途中で切れていた。
            for (int query = 0; query < 500; query++)
            {
                Assert.That(ArmBitAt(source, StartMicroseconds + 1000), Is.Not.Zero,
                            $"the pulse expired after {query} queries at the same " +
                            "virtual time, so it is not being measured in " +
                            "simulated time");
            }
        }

        /// <summary>
        /// Holding R down is ONE press. The real button springs back, and a pilot
        /// resting a finger on the key must not arm and disarm forty times a
        /// second.
        /// R を押し続けても**1 回**の押下であること。実機のボタンはばねで戻り、キーに
        /// 指を置いたままの利用者が毎秒 40 回 ARM と DISARM を繰り返してはならない。
        /// </summary>
        [Test]
        public void HoldingTheKeyIsOnePress()
        {
            var source = new KeyboardRc();
            source.PressArm();

            // The first query is what stamps the press with a virtual time, so
            // the pulse starts here.
            // 最初の問い合わせが押下に仮想時刻を刻むので、パルスはここから始まる。
            Assert.That(ArmBitAt(source, StartMicroseconds), Is.Not.Zero);

            // Run well past the pulse. Nothing re-presses it, because a press
            // comes from the key's transition and no new transition happened.
            // パルスを十分に越えて進める。押し直されないのは、押下がキーの遷移から
            // 来るものであり、新しい遷移が無かったからである。
            long afterPulse =
                StartMicroseconds + KeyboardRc.ArmPulseMicroseconds * 4;

            Assert.That(ArmBitAt(source, afterPulse), Is.Zero,
                        "the bit rose again without a new press, so holding R " +
                        "would toggle arm and disarm repeatedly");
        }

        /// <summary>
        /// A second press makes a second pulse, so two presses are two edges. This
        /// is what makes R reverse the vehicle every single time: the firmware
        /// decides what each edge means from its own state.
        /// 2 回目の押下が 2 つ目のパルスを作り、2 回の押下が 2 つのエッジになること。
        /// R が毎回必ず機体を反転させるのはこれによる。各エッジが何を意味するかは、
        /// ファームが自分の状態から決める。
        /// </summary>
        [Test]
        public void ASecondPressMakesASecondPulse()
        {
            var source = new KeyboardRc();

            source.PressArm();
            Assert.That(ArmBitAt(source, StartMicroseconds), Is.Not.Zero);

            long afterFirst =
                StartMicroseconds + KeyboardRc.ArmPulseMicroseconds + 1;
            Assert.That(ArmBitAt(source, afterFirst), Is.Zero,
                        "the first pulse had not finished");

            source.PressArm();
            Assert.That(ArmBitAt(source, afterFirst + 1), Is.Not.Zero,
                        "the second press produced no pulse, so the firmware " +
                        "never sees a second rising edge");
        }

        /// <summary>
        /// The ARM bit never comes from the frame's own flags. It is a button this
        /// class presses, so a caller cannot hold it down by handing in a frame
        /// that has it set — which is exactly how a latch would creep back.
        /// ARM のビットはフレーム自身のフラグからは決して来ないこと。これはこのクラスが
        /// 押すボタンなので、ビットの立ったフレームを渡して押し下げたままにすることは
        /// できない。ラッチが戻ってくる道はまさにそれである。
        /// </summary>
        [Test]
        public void AFramesOwnArmBitIsIgnored()
        {
            var source = new KeyboardRc();
            var frameWithArmSet = new RcFrame(
                RcScale.Centre, RcScale.Centre, RcScale.Centre, RcScale.Centre,
                SfuAbi.FlagArm);

            byte flags = source.FlagsAt(frameWithArmSet, StartMicroseconds);

            Assert.That(flags & SfuAbi.FlagArm, Is.Zero,
                        "a frame's own ARM bit was passed through, so a caller " +
                        "can hold ARM down and the firmware sees one endless press");
        }

        /// <summary>
        /// ALT_HOLD is a SWITCH, not a button: the firmware reads its position as
        /// the requested mode (`state_task.cpp:376-379`), so the latch is right
        /// here and the bit is held for as long as the switch is on.
        /// ALT_HOLD はボタンではなく**スイッチ**である。ファームはその位置を要求モードと
        /// して読む（`state_task.cpp:376-379`）ので、ここではラッチが正しく、スイッチが
        /// 入っている間ビットは立ち続ける。
        /// </summary>
        [Test]
        public void AltitudeHoldIsHeldBecauseItIsASwitch()
        {
            var source = new KeyboardRc();
            source.PressAltitudeHold();

            RcFrame frame = new RcFrame(
                RcScale.Centre, RcScale.Centre, RcScale.Centre, RcScale.Centre,
                SfuAbi.FlagAltitudeMode);

            long wellPastAnyPulse =
                StartMicroseconds + KeyboardRc.ArmPulseMicroseconds * 10;
            byte flags = source.FlagsAt(frame, wellPastAnyPulse);

            Assert.That(flags & SfuAbi.FlagAltitudeMode,
                        Is.EqualTo(SfuAbi.FlagAltitudeMode),
                        "the ALT_HOLD switch's position was not held, so the " +
                        "firmware would read the mode as STABILIZE");
            Assert.That(source.IsAltitudeHold, Is.True);
        }

        /// <summary>
        /// Pressing ARM leaves the ALT_HOLD switch where it was. The two flags are
        /// different kinds of control and must not move together.
        /// ARM を押しても ALT_HOLD のスイッチは動かないこと。2 つは種類の違う操作器で
        /// あり、連動してはならない。
        /// </summary>
        [Test]
        public void PressingArmLeavesTheAltitudeHoldSwitchAlone()
        {
            var source = new KeyboardRc();
            source.PressAltitudeHold();

            source.PressArm();

            Assert.That(source.IsAltitudeHold, Is.True);
        }

        /// <summary>The ARM bit this source sends at one virtual time. / この入力がある仮想時刻に送る ARM のビット。</summary>
        private static int ArmBitAt(KeyboardRc source, long nowMicroseconds)
        {
            return source.FlagsAt(RcFrame.Centred, nowMicroseconds) & SfuAbi.FlagArm;
        }
    }
}
