/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the log contract).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Collections.Generic;

namespace StampFly.Core
{
    /// <summary>
    /// Severity of a log line, spelled as AGENTS.md "Logs" requires.
    /// ログ 1 行の重さ。AGENTS.md「Logs」が定める綴り。
    /// </summary>
    public enum LogLevel
    {
        Debug,
        Info,
        Warn,
        Error,
    }

    /// <summary>
    /// Where a component sends its log lines.
    ///
    /// It lives in <c>StampFly.Core</c>, which references no other assembly, so
    /// that every producer (the world, the simulation loop, the firmware pump,
    /// the UI) can depend on the contract without depending on the page-side
    /// implementation that batches lines to <c>POST /api/log</c>. A producer
    /// takes an <see cref="IStructuredLog"/> and never decides how a line
    /// reaches <c>logs/unity/&lt;run_id&gt;.jsonl</c>.
    ///
    /// どこへログ 1 行を出すか。
    ///
    /// 他のどのアセンブリも参照しない <c>StampFly.Core</c> に置く。出し手
    /// （空間・刻みの輪・ファームのログの汲み出し・画面）が、行をまとめて
    /// <c>POST /api/log</c> へ送るページ側の実装に依存せず、約束だけに依存
    /// できるようにするため。出し手は <see cref="IStructuredLog"/> を受け取る
    /// だけで、行が <c>logs/unity/&lt;run_id&gt;.jsonl</c> へ届くまでの道筋を
    /// 決めない。
    /// </summary>
    public interface IStructuredLog
    {
        /// <summary>
        /// Write one event. <paramref name="source"/> is AGENTS.md's <c>src</c>,
        /// <paramref name="eventName"/> its <c>event</c>, and
        /// <paramref name="data"/> the per-event values that go under
        /// <c>data</c>. The implementation adds <c>ts</c>, <c>run_id</c> and any
        /// correlation keys it holds (<c>cmd_id</c>, <c>sim_us</c>, <c>tick</c>,
        /// <c>frame</c>, <c>boot_id</c>).
        /// 事象を 1 件書く。source は AGENTS.md の <c>src</c>、eventName は
        /// <c>event</c>、data は <c>data</c> の下に置く事象ごとの値。<c>ts</c>・
        /// <c>run_id</c> と相関の鍵（<c>cmd_id</c>・<c>sim_us</c>・<c>tick</c>・
        /// <c>frame</c>・<c>boot_id</c>）は実装側が付ける。
        /// </summary>
        void Write(LogLevel level, string source, string eventName, string message,
                   IReadOnlyDictionary<string, object> data);
    }

    /// <summary>
    /// The <c>src</c> vocabulary of AGENTS.md "Logs", spelled once so no
    /// producer invents a source the server would reject. A line whose
    /// <c>src</c> is outside this list is refused by
    /// <c>lib/sfcli/utils/jsonl_log.py</c>.
    /// AGENTS.md「Logs」の <c>src</c> の語彙。1 か所に綴り、サーバが拒否する
    /// 出どころを出し手が作らないようにする。この一覧に無い <c>src</c> の行は
    /// <c>lib/sfcli/utils/jsonl_log.py</c> が断る。
    /// </summary>
    public static class LogSources
    {
        /// <summary>The firmware's own ESP_LOGx. / ファーム自身の ESP_LOGx。</summary>
        public const string Firmware = "fw";

        /// <summary>The page-to-server bridge. / ページとサーバの間の橋。</summary>
        public const string Bridge = "bridge";

        /// <summary>The simulation loop. / 刻みの輪。</summary>
        public const string Simulation = "sim";

        /// <summary>World loading and editing. / 空間の読み込みと編集。</summary>
        public const string World = "world";

        /// <summary>On-screen controls. / 画面の操作。</summary>
        public const string Ui = "ui";

        /// <summary>Command handling on the page. / ページ側の命令の処理。</summary>
        public const string Command = "cmd";
    }
}
