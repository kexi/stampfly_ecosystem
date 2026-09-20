/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — lines on their way to /api/log).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using StampFly.Core;

namespace StampFly.Remote
{
    /// <summary>
    /// Hands each finished line to <c>RemoteBridge.jslib</c>, which keeps it for
    /// <c>window.stampfly.logs()</c> and batches it to <c>POST /api/log</c>.
    ///
    /// The batching itself is JavaScript's, not C#'s: only JavaScript can hold
    /// a timer that keeps running while the player is between frames, and only
    /// it can reach <c>navigator.sendBeacon</c> when the tab closes. C# does the
    /// one thing it must -- compose the line while it still knows the command
    /// and the clock -- and lets go of it.
    ///
    /// 出来上がった行を <c>RemoteBridge.jslib</c> へ渡す。あちらが
    /// <c>window.stampfly.logs()</c> のために保持し、まとめて
    /// <c>POST /api/log</c> へ送る。
    ///
    /// まとめる仕事は C# ではなく JavaScript のものである。プレイヤーのコマの
    /// 合間にも動く時計を持てるのも、タブが閉じるときに
    /// <c>navigator.sendBeacon</c> へ届くのも JavaScript だけである。C# は自分に
    /// しかできないこと ― まだ命令と時計が分かっているうちに行を組み立てること
    /// ― をして、手放す。
    /// </summary>
    public sealed class RemoteLogSink : ILogSink
    {
        /// <inheritdoc />
        public void Write(string line)
        {
            RemoteNative.Log(line);
        }
    }
}
