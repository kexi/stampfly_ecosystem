/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the simulation controls).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine;
using UnityEngine.InputSystem;

namespace StampFly.Sim
{
    /// <summary>
    /// The keys that drive the simulation rather than the vehicle: pause, single
    /// step, speed, power cycle, and putting the vehicle back at its spawn.
    ///
    /// 機体ではなくシミュレーションを操作するキー。一時停止・コマ送り・倍率・
    /// 電源の入れ直し・機体を出発点へ戻すこと。
    ///
    /// | Key / キー | Effect / 働き |
    /// |---|---|
    /// | P | pause and resume — 一時停止と再開 |
    /// | N | one tick while paused — 一時停止中に 1 刻み |
    /// | [ / ] | slower / faster, 0.1..4 — 遅く / 速く、0.1〜4 |
    /// | B | power cycle: a new firmware from INIT — 電源の入れ直し。INIT から新しいファーム |
    /// | Backspace | back to the spawn, firmware untouched — ファームはそのままで出発点へ |
    ///
    /// A power cycle and a reposition are deliberately different things: the
    /// firmware's tasks cannot be reset in place, so putting the vehicle back
    /// where it started does NOT restart it, and restarting it means building a
    /// whole new module.
    ///
    /// 電源の入れ直しと置き直しは、意図して別のものにしてある。ファームのタスクは
    /// その場では戻せないので、機体を最初の場所へ戻してもファームは再起動しない。
    /// 再起動はモジュールごと作り直すことを意味する。
    ///
    /// @design docs/plans/unity-simulator.md §3 1 刻みの処理
    /// </summary>
    [RequireComponent(typeof(SimLoop))]
    public sealed class SimControls : MonoBehaviour
    {
        /// <summary>What one press of [ or ] multiplies the speed by. / [ か ] を 1 回押したときに倍率に掛ける値。</summary>
        public const float SpeedStep = 1.5f;

        private SimLoop simLoop;

        private void Awake()
        {
            simLoop = GetComponent<SimLoop>();
        }

        private void Update()
        {
            Keyboard keyboard = Keyboard.current;
            bool hasKeyboard = keyboard != null;
            if (!hasKeyboard)
            {
                return;
            }

            ReadPacingKeys(keyboard);
            ReadLifetimeKeys(keyboard);
        }

        /// <summary>Pause, single step and speed. / 一時停止・コマ送り・倍率。</summary>
        private void ReadPacingKeys(Keyboard keyboard)
        {
            SimClock clock = simLoop.Clock;

            if (keyboard.pKey.wasPressedThisFrame)
            {
                clock.TogglePause();
            }

            if (keyboard.nKey.wasPressedThisFrame)
            {
                clock.StepOnce();
            }

            if (keyboard.leftBracketKey.wasPressedThisFrame)
            {
                clock.SetSpeed(clock.Speed / SpeedStep);
            }

            if (keyboard.rightBracketKey.wasPressedThisFrame)
            {
                clock.SetSpeed(clock.Speed * SpeedStep);
            }
        }

        /// <summary>Power cycle and reposition. / 電源の入れ直しと置き直し。</summary>
        private void ReadLifetimeKeys(Keyboard keyboard)
        {
            if (keyboard.bKey.wasPressedThisFrame)
            {
                simLoop.PowerOn();
            }

            if (keyboard.backspaceKey.wasPressedThisFrame)
            {
                simLoop.MoveToSpawn();
            }
        }
    }
}
