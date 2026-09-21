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
    /// ## ARM is a momentary button, ALT_HOLD is a switch
    /// ## ARM はモーメンタリボタン、ALT_HOLD はスイッチ
    ///
    /// The two flags are not the same kind of thing, and the firmware treats them
    /// differently (`firmware/vehicle/tasks/state_task.cpp:339-379`):
    ///
    /// | Flag | On the wire | What the firmware does |
    /// |---|---|---|
    /// | ARM | 1 only WHILE THE BUTTON IS HELD | each rising edge TOGGLES arm/disarm; the release does nothing |
    /// | ALT_HOLD / ACRO / POS_HOLD | the switch's POSITION, held | the position is the requested mode, applied on its edge |
    ///
    /// So ARM is sent as a PULSE: R presses a button that springs back. The bit
    /// is not "the state the pilot wants" — the firmware decides what a press
    /// means by looking at its own state, exactly as it does with the real
    /// transmitter's stick-press button. ALT_HOLD, being a switch position, is
    /// held as a latch here and that is correct.
    ///
    /// 2 つのフラグは同じ種類のものではなく、ファームは別々に扱う
    /// （`firmware/vehicle/tasks/state_task.cpp:339-379`）:
    ///
    /// | フラグ | 電文上の意味 | ファームの動作 |
    /// |---|---|---|
    /// | ARM | **押している間だけ** 1 | 立ち上がりごとに arm/disarm を**トグル**。離しても何も起きない |
    /// | ALT_HOLD／ACRO／POS_HOLD | スイッチの**位置**（保持） | 位置が要求モード。そのエッジで適用 |
    ///
    /// よって ARM は**パルス**として送る。R はばねで戻るボタンを押す操作である。
    /// ビットは「利用者が望む状態」ではない。押下が何を意味するかは、実機の送信機の
    /// スティック押し込みボタンと全く同じく、ファームが自分の状態を見て決める。
    /// ALT_HOLD はスイッチの位置なので、ここではラッチとして保持し、それが正しい。
    ///
    /// ## Why the pulse is measured in VIRTUAL time / なぜパルスを仮想時間で計るのか
    ///
    /// The bit has to stay up long enough for the firmware's 50 Hz stick intake
    /// to sample it at least once, and come back down before the next press. That
    /// is a duration in the SIMULATION's time, not in frames: one rendered frame
    /// runs between zero and twelve ticks depending on how well the host keeps
    /// up, so a pulse counted in frames would be a different length of simulated
    /// time on a fast host than on a slow one — and a different length again in a
    /// test that drives ticks directly. <see cref="ArmPulseMicroseconds"/> is
    /// therefore compared against the tick's own virtual clock, which is why
    /// <see cref="FlagsAt"/> takes one.
    ///
    /// ビットは、ファームの 50 Hz の取り込みが少なくとも 1 回標本できる長さ上がって
    /// いて、次の押下までに下がらなければならない。これはフレーム数ではなく
    /// **シミュレーションの時間**での長さである。描画 1 フレームは、ホストの追いつき
    /// 具合によって 0〜12 刻みを回すので、フレーム数で数えたパルスは、速いホストと
    /// 遅いホストで仮想時間の長さが変わり、刻みを直接回す試験ではさらに変わる。
    /// よって <see cref="ArmPulseMicroseconds"/> は刻み自身の仮想時計と比べる。
    /// <see cref="FlagsAt"/> がそれを引数に取るのはこのためである。
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

        /// <summary>
        /// How long one press of R holds the ARM bit up, in SIMULATED
        /// microseconds. 100 ms is what the real transmitter's stick-press button
        /// produces (`state_task.cpp:348` measures the same figure on hardware).
        /// The firmware takes the sticks in at 50 Hz — every 20 ms — so a pulse
        /// this long is sampled about five times: enough that no single missed
        /// intake loses the press, and short enough to be back down long before a
        /// person can press R again.
        ///
        /// R を 1 回押したときに ARM のビットを上げておく長さ。**シミュレーションの**
        /// マイクロ秒で表す。100 ms は実機の送信機のスティック押し込みボタンが出す
        /// 長さである（`state_task.cpp:348` が実機で同じ値を挙げている）。ファームは
        /// スティックを 50 Hz ― 20 ms ごと ― に取り込むので、この長さのパルスは
        /// 約 5 回標本される。取り込みを 1 回逃しても押下が失われない程度に長く、
        /// 人が次に R を押すよりずっと前に戻る程度に短い。
        /// </summary>
        public const long ArmPulseMicroseconds = 100_000;

        /// <summary>No press has been made. / 押下が無い。</summary>
        private const long NoPress = long.MinValue;

        /// <summary>
        /// A press made but not yet stamped with a virtual time. The key is read
        /// once a frame and the clock arrives per tick, so the two meet on the
        /// first <see cref="FlagsAt"/> after the press.
        /// 押下はあったが、まだ仮想時刻を刻まれていない。キーは 1 フレームに 1 回
        /// 読まれ、時計は刻みごとに来るので、2 つは押下後の最初の
        /// <see cref="FlagsAt"/> で出会う。
        /// </summary>
        private const long PressAwaitingClock = long.MinValue + 1;

        private float deflection = DefaultDeflection;
        private bool isAltitudeHold;

        // When R was last pressed, on the simulation's clock, or NoPress. The
        // pulse is this plus ArmPulseMicroseconds; nothing else is remembered
        // about ARM, because the firmware owns that state.
        // R が最後に押された時刻。シミュレーションの時計で表す。押下が無ければ
        // NoPress。パルスはこれに ArmPulseMicroseconds を足した区間である。ARM に
        // ついて他に覚えていることは無い。その状態はファームのものである。
        private long armPressedAtMicroseconds = NoPress;

        // Whether R was already down on the previous read, so holding it counts
        // as one press rather than a new one every frame.
        // 直前の読みで R がすでに押されていたか。押し続けたときに、毎フレーム新しい
        // 押下になるのではなく 1 回の押下として数えるためである。
        private bool wasArmKeyDown;

        /// <summary>The current stick deflection, 10..100. / いまのスティックの振れ幅。10〜100。</summary>
        public float Deflection => deflection;

        /// <summary>Whether the ALT_HOLD bit is being sent. / ALT_HOLD のビットを送っているか。</summary>
        public bool IsAltitudeHold => isAltitudeHold;

        /// <summary>
        /// Whether a press's pulse is still on at <paramref name="nowMicroseconds"/>.
        /// The readout uses this to say a press has gone out and is waiting for
        /// the firmware to act on it.
        /// <paramref name="nowMicroseconds"/> の時点で押下のパルスがまだ出ているか。
        /// 表示板が、押下が出てファームの反応を待っている状態を示すのに使う。
        /// </summary>
        public bool IsArmPulseOn(long nowMicroseconds)
        {
            bool hasNoTimedPress = armPressedAtMicroseconds == NoPress ||
                                   armPressedAtMicroseconds == PressAwaitingClock;
            if (hasNoTimedPress)
            {
                return false;
            }

            long sincePress = nowMicroseconds - armPressedAtMicroseconds;
            return sincePress >= 0 && sincePress < ArmPulseMicroseconds;
        }

        /// <summary>
        /// Press the ARM button, as R does. The pulse starts at the first tick
        /// that asks for flags, so a caller need not know the virtual clock.
        /// R と同じく ARM のボタンを押す。パルスは、フラグを求める最初の刻みから
        /// 始まるので、呼ぶ側が仮想時計を知る必要は無い。
        /// </summary>
        public void PressArm()
        {
            armPressedAtMicroseconds = PressAwaitingClock;
        }

        /// <summary>Flip the ALT_HOLD switch, as H does. / H と同じく ALT_HOLD のスイッチを倒す。</summary>
        public void PressAltitudeHold()
        {
            isAltitudeHold = !isAltitudeHold;
        }

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

        /// <inheritdoc/>
        public byte FlagsAt(in RcFrame frame, long nowMicroseconds)
        {
            // A press that has not been timed yet is given this tick's clock.
            // `Read` cannot do it: the key is read once a frame and has no
            // virtual time of its own, while the pulse has to be measured on the
            // simulation's clock for the reason in this class's notes.
            // まだ時刻を与えられていない押下に、この刻みの時計を与える。`Read` では
            // できない。キーは 1 フレームに 1 回読まれ、自身の仮想時刻を持たないが、
            // パルスはこのクラスの注記の理由でシミュレーションの時計で計らねばならない。
            bool isAwaitingAClock = armPressedAtMicroseconds == PressAwaitingClock;
            if (isAwaitingAClock)
            {
                armPressedAtMicroseconds = nowMicroseconds;
            }

            byte flags = (byte)(frame.Flags & ~Native.SfuAbi.FlagArm);
            bool isPulseOn = IsArmPulseOn(nowMicroseconds);
            if (isPulseOn)
            {
                flags |= Native.SfuAbi.FlagArm;
            }

            return flags;
        }

        /// <summary>
        /// The keys that are not axes: the ARM button, the ALT_HOLD switch and
        /// the deflection. Each acts on the press rather than while held, so
        /// holding a key down is one press.
        /// 軸でないキー。ARM のボタン・ALT_HOLD のスイッチ・振れ幅である。どれも
        /// 押している間ではなく押した瞬間に働くので、押し続けても 1 回の押下になる。
        /// </summary>
        private void ReadLatchedKeys(Keyboard keyboard)
        {
            // R is a momentary BUTTON: a press starts a pulse, and holding the
            // key does not start another. `wasPressedThisFrame` would almost do,
            // but a frame in which the input is not read at all -- the loop skips
            // `Read` when no firmware is running -- would swallow the press, so
            // the transition is tracked from the key's own held state.
            // R はモーメンタリ**ボタン**である。押下がパルスを始め、押し続けても次は
            // 始まらない。`wasPressedThisFrame` でもほぼ足りるが、入力がまったく
            // 読まれないフレーム ― ファームが動いていない間ループは `Read` を呼ばない ―
            // が押下を飲み込むので、キー自身の押されている状態から遷移を見る。
            bool isArmKeyDown = keyboard.rKey.isPressed;
            bool hasJustBeenPressed = isArmKeyDown && !wasArmKeyDown;
            wasArmKeyDown = isArmKeyDown;
            if (hasJustBeenPressed)
            {
                armPressedAtMicroseconds = PressAwaitingClock;
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

        /// <summary>
        /// The flags the SWITCHES produce. The ARM button is not among them:
        /// being a pulse measured on the simulation's clock, it is added by
        /// <see cref="FlagsAt"/>, which has that clock.
        /// **スイッチ**が作るフラグ。ARM のボタンはここに含まれない。シミュレーションの
        /// 時計で計るパルスなので、その時計を持つ <see cref="FlagsAt"/> が足す。
        /// </summary>
        private byte CurrentFlags()
        {
            byte flags = 0;
            if (isAltitudeHold)
            {
                flags |= Native.SfuAbi.FlagAltitudeMode;
            }
            return flags;
        }

        /// <summary>
        /// Drop the ALT_HOLD switch and forget any press in flight. The loop
        /// calls this on a power cycle, so a fresh firmware does not meet a
        /// half-finished ARM pulse or the last flight's mode switch.
        /// ALT_HOLD のスイッチを切り、途中の押下を忘れる。電源の入れ直しでループが
        /// これを呼ぶので、新しいファームが、途中の ARM のパルスや前の飛行のモードの
        /// スイッチに出会うことはない。
        /// </summary>
        public void Reset()
        {
            isAltitudeHold = false;
            armPressedAtMicroseconds = NoPress;
            wasArmKeyDown = false;
        }
    }
}
