/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — keyboard piloting).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine.InputSystem;

namespace StampFly.Input
{
    /// <summary>
    /// Keyboard piloting, with the bindings of `sf sils fly` so that what a
    /// person learned at the terminal works here unchanged
    /// (`lib/sfcli/commands/sils.py`).
    ///
    /// キーボードでの操縦。割り当ては `sf sils fly` に合わせてあり、端末で覚えた
    /// 操作がそのまま通るようにしてある（`lib/sfcli/commands/sils.py`）。
    ///
    /// | Key / キー | Axis / 軸 |
    /// |---|---|
    /// | W / S | pitch forward / back — 前進 / 後退 |
    /// | A / D | roll left / right — 左 / 右ロール |
    /// | , / . | yaw left / right — 左 / 右ヨー |
    /// | Space / Z | throttle up / down — スロットル 上げ / 下げ |
    /// | R | ARM / DISARM, one tap — 1 回のタップで ARM/DISARM |
    /// | H | ALT_HOLD on / off — ALT_HOLD の入切 |
    /// | + / - | deflection 10..100 — 振れ幅 10〜100 |
    ///
    /// A released key returns its axis to centre, as a spring-loaded stick does.
    /// Unlike the terminal version this needs no release timer: the Input System
    /// reports a key's state directly, so "not held" is known rather than
    /// inferred from how long ago a keystroke arrived.
    ///
    /// 離したキーの軸は、ばねの入ったスティックと同じく中央へ戻る。端末版と違って
    /// リリースの計時は要らない。Input System はキーの状態をそのまま報告するので、
    /// 「押していない」は、打鍵からの経過時間から推測するのではなく直接分かる。
    ///
    /// The throttle stick means different things by flight mode, and this class
    /// does not translate: in ACRO and STABILIZE it commands thrust and must be
    /// raised to take off, while in ALT_HOLD centre means "hold this height" and
    /// away from centre means climb or descend. The firmware decides which,
    /// exactly as it does with a real transmitter.
    ///
    /// スロットルの意味は飛行モードで変わるが、この実装は読み替えない。ACRO と
    /// STABILIZE では推力の指令で、離陸には上げる必要がある。ALT_HOLD では中央が
    /// 「この高さを保つ」で、中央から離すと上昇・下降になる。どちらかを決めるのは
    /// ファームであり、実機の送信機のときと同じである。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（操縦入力）
    /// </summary>
    public sealed class KeyboardRc : IRcSource
    {
        /// <summary>The deflection a fresh source starts at. / 作った直後の振れ幅。</summary>
        public const float DefaultDeflection = 60.0f;

        /// <summary>The smallest deflection - and + can reach. / - と + が届く最小の振れ幅。</summary>
        public const float MinimumDeflection = 10.0f;

        /// <summary>The largest deflection - and + can reach. / - と + が届く最大の振れ幅。</summary>
        public const float MaximumDeflection = 100.0f;

        /// <summary>How much one press of - or + moves the deflection. / - か + を 1 回押したときの変化量。</summary>
        public const float DeflectionStep = 10.0f;

        private float deflection = DefaultDeflection;
        private bool isArmed;
        private bool isAltitudeHold;

        /// <summary>The current stick deflection, 10..100. / いまのスティックの振れ幅。10〜100。</summary>
        public float Deflection => deflection;

        /// <summary>Whether the ARM bit is being sent. / ARM のビットを送っているか。</summary>
        public bool IsArmed => isArmed;

        /// <summary>Whether the ALT_HOLD bit is being sent. / ALT_HOLD のビットを送っているか。</summary>
        public bool IsAltitudeHold => isAltitudeHold;

        /// <inheritdoc/>
        public RcFrame Read()
        {
            Keyboard keyboard = Keyboard.current;
            bool hasKeyboard = keyboard != null;
            if (!hasKeyboard)
            {
                return RcFrame.Centred;
            }

            ReadLatchedKeys(keyboard);

            float roll = Axis(keyboard.dKey, keyboard.aKey);
            float pitch = Axis(keyboard.wKey, keyboard.sKey);
            float yaw = Axis(keyboard.periodKey, keyboard.commaKey);
            float throttle = Axis(keyboard.spaceKey, keyboard.zKey);

            return new RcFrame(
                RcScale.FromDeflection(throttle),
                RcScale.FromDeflection(roll),
                RcScale.FromDeflection(pitch),
                RcScale.FromDeflection(yaw),
                CurrentFlags());
        }

        /// <summary>
        /// The keys that toggle rather than deflect. Each acts on the press, not
        /// while held, so holding R does not arm and disarm every frame.
        /// 押している間ではなく、押した瞬間に働くキー。R を押し続けても毎フレーム
        /// ARM と DISARM を繰り返さないようにするためである。
        /// </summary>
        private void ReadLatchedKeys(Keyboard keyboard)
        {
            bool wantsArmToggle = keyboard.rKey.wasPressedThisFrame;
            if (wantsArmToggle)
            {
                isArmed = !isArmed;
            }

            bool wantsAltitudeHoldToggle = keyboard.hKey.wasPressedThisFrame;
            if (wantsAltitudeHoldToggle)
            {
                isAltitudeHold = !isAltitudeHold;
            }

            bool wantsMoreDeflection =
                keyboard.equalsKey.wasPressedThisFrame ||
                keyboard.numpadPlusKey.wasPressedThisFrame;
            if (wantsMoreDeflection)
            {
                deflection = UnityEngine.Mathf.Min(
                    MaximumDeflection, deflection + DeflectionStep);
            }

            bool wantsLessDeflection =
                keyboard.minusKey.wasPressedThisFrame ||
                keyboard.numpadMinusKey.wasPressedThisFrame;
            if (wantsLessDeflection)
            {
                deflection = UnityEngine.Mathf.Max(
                    MinimumDeflection, deflection - DeflectionStep);
            }
        }

        /// <summary>
        /// One axis from a pair of keys: the deflection when one is held, its
        /// negative when the other is, and zero when both or neither are.
        /// キー 2 つから軸 1 つを作る。片方を押していれば振れ幅、もう片方なら
        /// その符号を変えたもの、両方または何も押していなければ 0 になる。
        /// </summary>
        private float Axis(UnityEngine.InputSystem.Controls.KeyControl positive,
                           UnityEngine.InputSystem.Controls.KeyControl negative)
        {
            bool wantsPositive = positive.isPressed;
            bool wantsNegative = negative.isPressed;
            bool isOpposed = wantsPositive == wantsNegative;
            if (isOpposed)
            {
                return 0.0f;
            }

            return wantsPositive ? deflection : -deflection;
        }

        /// <summary>The flag byte the two latched keys produce. / 2 つのラッチするキーが作るフラグのバイト。</summary>
        private byte CurrentFlags()
        {
            byte flags = 0;
            if (isArmed)
            {
                flags |= Native.SfuAbi.FlagArm;
            }
            if (isAltitudeHold)
            {
                flags |= Native.SfuAbi.FlagAltitudeMode;
            }
            return flags;
        }

        /// <summary>
        /// Drop ARM and ALT_HOLD back to off. The loop calls this on a power
        /// cycle so a fresh firmware does not meet a latched ARM bit.
        /// ARM と ALT_HOLD を切った状態に戻す。電源を入れ直したとき、新しいファーム
        /// が押しっぱなしの ARM に出会わないよう、ループがこれを呼ぶ。
        /// </summary>
        public void Reset()
        {
            isArmed = false;
            isAltitudeHold = false;
        }
    }
}
