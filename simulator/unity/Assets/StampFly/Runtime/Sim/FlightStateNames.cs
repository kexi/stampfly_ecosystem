/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — flight state and mode names).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

namespace StampFly.Sim
{
    /// <summary>
    /// The names of the firmware's flight states and modes, in the firmware's
    /// own order. The ABI passes them as plain integers, so this is where a
    /// number becomes a word a person can read.
    ///
    /// ファームの飛行状態とモードの名前を、ファーム自身の順序で持つ。ABI はこれを
    /// ただの整数で渡すので、数が人の読める語になるのはここである。
    ///
    /// The source is `firmware/vehicle/components/sf_state/include/flight_state.hpp`.
    /// A state added there without being added here shows as its own number
    /// rather than as a wrong name.
    /// 出典は `firmware/vehicle/components/sf_state/include/flight_state.hpp`。
    /// 向こうに足してここに足さなかった状態は、誤った名前ではなく数そのものとして
    /// 表示される。
    /// </summary>
    public static class FlightStateNames
    {
        private static readonly string[] States =
        {
            "INIT",          // 0
            "IDLE_GROUND",   // 1
            "IDLE_HELD",     // 2
            "ARMED_GROUND",  // 3
            "TAKEOFF",       // 4
            "FLYING",        // 5
            "LANDING",       // 6
        };

        private static readonly string[] Modes =
        {
            "ACRO",       // 0
            "STABILIZE",  // 1
            "ALT_HOLD",   // 2
            "POS_HOLD",   // 3
        };

        /// <summary>The number the FLYING state has. / FLYING の番号。</summary>
        public const int Flying = 5;

        /// <summary>The number the ALT_HOLD mode has. / ALT_HOLD の番号。</summary>
        public const int AltitudeHold = 2;

        /// <summary>A flight state's name, or the number when it is unknown. / 飛行状態の名前。知らない値なら数そのもの。</summary>
        public static string State(int state)
        {
            bool isKnown = state >= 0 && state < States.Length;
            return isKnown ? States[state] : state.ToString();
        }

        /// <summary>A flight mode's name, or the number when it is unknown. / 飛行モードの名前。知らない値なら数そのもの。</summary>
        public static string Mode(int mode)
        {
            bool isKnown = mode >= 0 && mode < Modes.Length;
            return isKnown ? Modes[mode] : mode.ToString();
        }
    }
}
