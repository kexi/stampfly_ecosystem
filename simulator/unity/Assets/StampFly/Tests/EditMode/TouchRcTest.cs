/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — touch input tests).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Input;
using StampFly.Native;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// Touch input preserves transmitter axes, buttons and interruption behaviour.
    /// タッチ入力の送信機軸・ボタン・中断時の動作を保証する。
    /// </summary>
    public sealed class TouchRcTest : InputTestFixture
    {
        /// <summary>Each mode 2 axis reaches its own signed deflection. / モード 2 の各軸が個別の符号付き振れ幅になる。</summary>
        [Test]
        public void ModeTwoAxesUseTheSelectedDeflectionWithoutAKeyboard()
        {
            Assert.That(Keyboard.current, Is.Null);
            var source = new KeyboardRc();
            source.Touch.Enabled = true;
            source.Touch.SetLeft(new Vector2(-0.25f, 0.5f));
            source.Touch.SetRight(new Vector2(0.75f, -1f));

            RcFrame frame = source.Read();

            Assert.That(frame.Yaw, Is.EqualTo(RcScale.FromDeflection(-15f)));
            Assert.That(frame.Throttle, Is.EqualTo(RcScale.FromDeflection(30f)));
            Assert.That(frame.Roll, Is.EqualTo(RcScale.FromDeflection(45f)));
            Assert.That(frame.Pitch, Is.EqualTo(RcScale.FromDeflection(-60f)));
        }

        /// <summary>Out-of-range diagonals clamp per axis, and release centres all axes. / 範囲外の斜め操作は軸別に制限され、解放で全軸が中央に戻る。</summary>
        [Test]
        public void StickTravelClampsEachAxisAndClearCentresBothSticks()
        {
            var source = new KeyboardRc();
            source.Touch.Enabled = true;
            source.Touch.SetLeft(new Vector2(-4f, 2f));
            source.Touch.SetRight(new Vector2(3f, -5f));

            Assert.That(source.Touch.Left, Is.EqualTo(new Vector2(-1f, 1f)));
            Assert.That(source.Touch.Right, Is.EqualTo(new Vector2(1f, -1f)));
            source.Touch.Clear();

            AssertCentred(source.Read());
            Assert.That(source.Touch.Enabled, Is.True);
        }

        /// <summary>A keyboard is not required for altitude mode or a timed ARM press. / 高度維持と時限 ARM 押下はキーボードなしでも送信できる。</summary>
        [Test]
        public void ButtonsSurviveAMissingKeyboardAndArmExpiresInVirtualTime()
        {
            Assert.That(Keyboard.current, Is.Null);
            var source = new KeyboardRc();
            source.Touch.Enabled = true;
            source.PressAltitudeHold();
            source.PressArm();
            RcFrame frame = source.Read();

            Assert.That(frame.Flags, Is.EqualTo(SfuAbi.FlagAltitudeMode));
            Assert.That(source.FlagsAt(frame, 500L),
                Is.EqualTo(SfuAbi.FlagAltitudeMode | SfuAbi.FlagArm));
            Assert.That(source.FlagsAt(frame, 500L + KeyboardRc.ArmPulseMicroseconds),
                Is.EqualTo(SfuAbi.FlagAltitudeMode));
            source.Touch.Enabled = false;
            Assert.That(source.Read().Flags, Is.EqualTo(SfuAbi.FlagAltitudeMode));
        }

        /// <summary>Interruption cancels pending and active pulses but preserves altitude mode. / 中断で未開始・進行中のパルスを取り消し、高度維持は保持する。</summary>
        [TestCase(false)]
        [TestCase(true)]
        public void CancelTouchClearsSticksAndArmButPreservesAltitude(bool startPulse)
        {
            var source = new KeyboardRc();
            source.Touch.Enabled = true;
            source.Touch.SetLeft(Vector2.one);
            source.Touch.SetRight(Vector2.one);
            source.PressAltitudeHold();
            source.PressArm();
            if (startPulse)
            {
                source.FlagsAt(source.Read(), 0L);
            }

            source.CancelTouch();

            AssertCentred(source.Read());
            Assert.That(source.FlagsAt(source.Read(), 1L), Is.EqualTo(SfuAbi.FlagAltitudeMode));
            Assert.That(source.Touch.Enabled, Is.True);
        }

        /// <summary>Power cycling removes old touch axes, mode and pending ARM. / 電源入れ直しで前のタッチ軸・モード・未開始 ARM を消す。</summary>
        [Test]
        public void ResetClearsTouchAndAllButtonState()
        {
            var source = new KeyboardRc();
            source.Touch.Enabled = true;
            source.Touch.SetLeft(Vector2.one);
            source.Touch.SetRight(Vector2.one);
            source.PressAltitudeHold();
            source.PressArm();

            source.Reset();

            AssertCentred(source.Read());
            Assert.That(source.FlagsAt(source.Read(), 0L), Is.Zero);
        }

        /// <summary>Touch overrides keyboard axes while retaining keyboard buttons and deflection. / タッチ使用中もキーのボタン・振れ幅が有効で、軸はタッチを使う。</summary>
        [Test]
        public void TouchOverridesKeyboardAxesButSharesButtonsAndDeflection()
        {
            Keyboard keyboard = InputSystem.AddDevice<Keyboard>();
            var source = new KeyboardRc();
            source.Touch.Enabled = true;
            source.Touch.SetRight(new Vector2(0.5f, 0f));
            InputSystem.QueueStateEvent(keyboard, new KeyboardState(Key.A, Key.R, Key.H, Key.Equals));
            InputSystem.Update();

            RcFrame frame = source.Read();

            Assert.That(source.Deflection, Is.EqualTo(70f));
            Assert.That(frame.Roll, Is.EqualTo(RcScale.FromDeflection(35f)));
            Assert.That(source.FlagsAt(frame, 0L),
                Is.EqualTo(SfuAbi.FlagAltitudeMode | SfuAbi.FlagArm));
        }

        /// <summary>Disabling touch restores keyboard axes and does not retain a stick displacement. / タッチを無効にするとキー軸に戻り、タッチの振れは残らない。</summary>
        [Test]
        public void DisablingTouchRestoresKeyboardAndClearsOldStickValues()
        {
            Keyboard keyboard = InputSystem.AddDevice<Keyboard>();
            var source = new KeyboardRc();
            source.Touch.Enabled = true;
            source.Touch.SetLeft(Vector2.one);
            source.Touch.SetRight(Vector2.one);
            source.Touch.Enabled = false;
            InputSystem.QueueStateEvent(keyboard, new KeyboardState(Key.A, Key.W, Key.Comma, Key.Z));
            InputSystem.Update();

            RcFrame frame = source.Read();

            Assert.That(frame.Roll, Is.EqualTo(RcScale.FromDeflection(-60f)));
            Assert.That(frame.Pitch, Is.EqualTo(RcScale.FromDeflection(60f)));
            Assert.That(frame.Yaw, Is.EqualTo(RcScale.FromDeflection(-60f)));
            Assert.That(frame.Throttle, Is.EqualTo(RcScale.FromDeflection(-60f)));
            source.Touch.Enabled = true;
            AssertCentred(source.Read());
        }

        /// <summary>Every released axis is the transmitter centre count. / 解放した全軸が送信機の中央値であること。</summary>
        private static void AssertCentred(RcFrame frame)
        {
            Assert.That(frame.Throttle, Is.EqualTo(RcScale.Centre));
            Assert.That(frame.Roll, Is.EqualTo(RcScale.Centre));
            Assert.That(frame.Pitch, Is.EqualTo(RcScale.Centre));
            Assert.That(frame.Yaw, Is.EqualTo(RcScale.Centre));
        }
    }
}
