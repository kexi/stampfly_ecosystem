/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — one press always reverses ARM).
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
    /// Against the real firmware: EVERY press of R reverses what the vehicle is
    /// doing — through a disarm the pilot asked for and through one the firmware
    /// made on its own.
    ///
    /// 実物のファームウェアを相手に、R の**どの押下も**機体の状態を反転させること。
    /// 利用者が求めた DISARM でも、ファームが自分で行った DISARM でも同じである。
    ///
    /// One flight walks the whole sequence:
    /// 1 回の飛行が全体を通る:
    ///
    /// | # | Press / 押下 | Expected / 期待 |
    /// |---|---|---|
    /// | 1 | R | disarmed -> ARMED |
    /// | 2 | R | ARMED -> disarmed（利用者の DISARM） |
    /// | 3 | R | disarmed -> ARMED |
    /// | — | 床へ叩きつける | ファームが自分で DISARM |
    /// | 4 | R | disarmed -> ARMED |
    ///
    /// Press 2 is the one the ORIGINAL code got wrong, and it needs no crash to
    /// show it: the flag was a latch, so pressing R while armed cleared it — a
    /// FALLING edge, which the firmware ignores — and the vehicle stayed armed.
    /// Press 4 is the one the crash reaches. Both are here because the reported
    /// symptom ("the props spin sometimes") has these two independent causes.
    ///
    /// 2 回目の押下が**元のコード**が誤っていたところで、これを示すのに墜落は要らない。
    /// フラグがラッチだったので、ARM 中に R を押すとそれを落とし ―
    /// ファームが無視する**立ち下がり**― 機体は ARM のままだった。4 回目の押下が墜落の
    /// 先に在るものである。報告された症状（「プロペラが回ったり回らなかったりする」）は
    /// この 2 つの独立した原因を持つので、両方をここに置く。
    ///
    /// @design firmware/vehicle/tasks/state_task.cpp:339-357
    /// </summary>
    public sealed class ArmPressReversesStateTest
    {
        // The firmware needs a few seconds of boot and calibration before it
        // accepts an ARM at all (FirmwareFlightTest uses the same 4 s).
        // ファームは ARM を受け付ける前に数秒の起動と校正を要る（FirmwareFlightTest も
        // 同じ 4 秒を使う）。
        private const double FirstPressAtSeconds = 4.0;

        // How long to wait for the firmware's state machine to settle after a
        // press. Its stick intake is 50 Hz and the transition takes a few control
        // periods, so half a second is generous without being slow.
        // 押下の後、ファームの状態機械が落ち着くのを待つ長さ。取り込みは 50 Hz で遷移は
        // 数制御周期かかるので、0.5 秒は遅くならない範囲で十分である。
        private const double SettleSeconds = 0.5;

        // How hard the vehicle is thrown at the floor [m/s]. Above the impact
        // detector's threshold in G for the consecutive samples it wants.
        // 機体を床へ投げつける速さ [m/s]。衝撃検出が求める連続標本ぶん、G の閾値を
        // 超える値である。
        private const float SlamSpeedMetersPerSecond = 6.0f;

        private const double GiveUpAtSeconds = 40.0;

        private FirmwareFlightScene scene;
        private KeyboardRc keyboard;

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

            scene = new FirmwareFlightScene();
            keyboard = new KeyboardRc();
        }

        [TearDown]
        public void TearDown()
        {
            scene?.Dispose();
            scene = null;
        }

        /// <summary>
        /// Four presses, each of which must reverse the firmware's ARM — with a
        /// crash-induced self-disarm in the middle.
        /// 4 回の押下がそれぞれファームの ARM を反転させること。途中に墜落による
        /// 自律 DISARM を挟む。
        /// </summary>
        [Test]
        public void EveryPressOfArmReversesTheFirmwaresState()
        {
            Assert.That(scene.Build(), Is.True,
                        $"sfu_boot failed: {scene.Firmware?.LastError}");

            // The pulse has to reach the firmware tick by tick, so the keyboard
            // supplies the flags rather than a fixed frame.
            // パルスは刻みごとにファームへ届かねばならないので、固定のフレームではなく
            // キーボードがフラグを供給する。
            scene.FlagSource = keyboard;

            var history = new List<string>();

            FlyTo(FirstPressAtSeconds);

            // --- Press 1: must ARM -------------------------------------------
            AssertPressReverses("press 1 (first arm)", history);

            // --- Press 2: must DISARM. This is the press the latch broke. -----
            // --- 2 回目: DISARM になること。ラッチが壊していたのはこの押下である。
            AssertPressReverses("press 2 (pilot disarm)", history);

            // --- Press 3: must ARM again --------------------------------------
            AssertPressReverses("press 3 (re-arm)", history);

            // --- The firmware disarms itself: fly up, then slam it down -------
            // --- ファームが自分で DISARM する: 浮かせてから叩きつける
            bool selfDisarmed = SlamIntoTheFloor();
            Assert.That(selfDisarmed, Is.True,
                        "the firmware never disarmed itself, so this flight did " +
                        "not reach the crash case — check the impact and " +
                        "gyro-anomaly thresholds in sf_failsafe. " +
                        Trace(history));

            // --- Press 4: must ARM, with no wasted press ---------------------
            // --- 4 回目: 押下を無駄にせず ARM されること
            AssertPressReverses("press 4 (after the self-disarm)", history);
        }

        /// <summary>
        /// Press R once and require that the firmware's ARM came out the other way
        /// than it went in.
        /// R を 1 回押し、ファームの ARM が入る前と逆になったことを求める。
        /// </summary>
        private void AssertPressReverses(string label, List<string> history)
        {
            bool before = IsArmed;
            keyboard.PressArm();
            history.Add($"{label}: armed {before} ->");

            bool reversed = FlyUntilArmedBecomes(!before);
            history[history.Count - 1] += $" {IsArmed} ({FlightState})";

            Assert.That(reversed, Is.True,
                        $"{label} did not reverse the firmware's ARM: it was " +
                        $"{before} before and is {IsArmed} after. " + Trace(history));
        }

        /// <summary>
        /// Fly until the firmware's ARM reaches <paramref name="wanted"/>, or until
        /// the settling time runs out.
        /// ファームの ARM が <paramref name="wanted"/> になるまで、または整定の時間が
        /// 尽きるまで飛ばす。
        /// </summary>
        private bool FlyUntilArmedBecomes(bool wanted)
        {
            double deadline = NowSeconds + SettleSeconds;
            bool reached = false;

            scene.FlyUntil(deadline, _ =>
            {
                reached |= IsArmed == wanted;
            });

            return reached || IsArmed == wanted;
        }

        /// <summary>
        /// Get the vehicle off the floor, then throw it down hard enough for the
        /// firmware's own impact detector to disarm it. The throttle is the only
        /// stick moved; what disarms the vehicle has to be the firmware's decision.
        /// 機体を床から離し、ファーム自身の衝撃検出が DISARM するだけの強さで落とす。
        /// 動かすスティックはスロットルだけで、DISARM させるものはファームの判断で
        /// なければならない。
        /// </summary>
        private bool SlamIntoTheFloor()
        {
            // Climb on raw throttle. The mode is STABILIZE here (no ALT_HOLD
            // switch), so the throttle IS the thrust command.
            // 生のスロットルで上昇する。ここでのモードは STABILIZE（ALT_HOLD の
            // スイッチを入れていない）なので、スロットルがそのまま推力の指令である。
            scene.Sticks = Frame(3243);
            scene.FlyUntil(NowSeconds + 2.0, _ => { });

            scene.Body.linearVelocity =
                new Vector3(0.0f, -SlamSpeedMetersPerSecond, 0.0f);

            bool wentDisarmed = false;
            double deadline = System.Math.Min(NowSeconds + 4.0, GiveUpAtSeconds);
            scene.FlyUntil(deadline, _ =>
            {
                wentDisarmed |= !IsArmed;
            });

            // Back to centre so the next press is judged on its own.
            // 次の押下がそれ自身で判定されるよう、中央へ戻す。
            scene.Sticks = Frame(RcScale.Centre);
            return wentDisarmed || !IsArmed;
        }

        /// <summary>Fly to a virtual time with the sticks as they are. / スティックをそのままに、仮想時刻まで飛ばす。</summary>
        private void FlyTo(double untilSeconds)
        {
            scene.FlyUntil(untilSeconds, _ => { });
        }

        /// <summary>One frame with everything but the throttle centred. / スロットル以外を中央にしたフレーム 1 つ。</summary>
        private static RcFrame Frame(ushort throttle)
        {
            return new RcFrame(
                throttle, RcScale.Centre, RcScale.Centre, RcScale.Centre, 0);
        }

        /// <summary>Whether the FIRMWARE says it is armed. / **ファーム**が ARM と述べているか。</summary>
        private bool IsArmed => scene.LastResult.Armed != 0;

        /// <summary>The firmware's flight state, by name. / ファームの飛行状態の名前。</summary>
        private string FlightState =>
            FlightStateNames.State(scene.LastResult.FlightState);

        /// <summary>The virtual time now [s]. / いまの仮想時刻 [s]。</summary>
        private double NowSeconds => scene.LastResult.NowMicroseconds * 1e-6;

        /// <summary>
        /// What happened so far, for a failure message. A press that did nothing
        /// is only diagnosable with the sequence that led to it.
        /// ここまでに起きたこと。失敗の文に添える。何も起きなかった押下は、そこへ至る
        /// 経過が無ければ診断できない。
        /// </summary>
        private static string Trace(List<string> history)
        {
            return "sequence: " + string.Join(" | ", history);
        }
    }
}
