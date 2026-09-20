/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — where log lines go).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEngine;

namespace StampFly.Core
{
    /// <summary>
    /// Prints each line to Unity's console. The fallback when no other sink is
    /// installed: in the editor it is what a developer sees, and in a browser
    /// it reaches the devtools console.
    /// 各行を Unity のコンソールへ出す。他の出力先が差し込まれていないときの
    /// 既定。エディタでは開発者が見るもので、ブラウザでは devtools のコンソール
    /// へ届く。
    /// </summary>
    public sealed class UnityConsoleLogSink : ILogSink
    {
        /// <inheritdoc />
        public void Write(string line)
        {
            Debug.Log(line);
        }
    }

    /// <summary>
    /// Keeps lines and does nothing else. Used by tests and by a page that has
    /// no server: the ring inside <see cref="StructuredLog"/> already holds the
    /// recent lines, so a public page needs no sink at all.
    /// 行を受け取って何もしない。試験と、サーバを持たないページが使う。
    /// 直近の行は <see cref="StructuredLog"/> の輪状バッファが持つので、公開
    /// ページには出力先が要らない。
    /// </summary>
    public sealed class NullLogSink : ILogSink
    {
        /// <summary>The one instance needed. / 必要なのは 1 つだけ。</summary>
        public static readonly NullLogSink Instance = new NullLogSink();

        /// <inheritdoc />
        public void Write(string line)
        {
        }
    }

    /// <summary>
    /// Sends every line to several sinks. Lets the editor write to a file and
    /// keep printing to the console at the same time.
    /// 各行を複数の出力先へ送る。エディタがファイルへ書きつつコンソールにも
    /// 出し続けられるようにする。
    /// </summary>
    public sealed class FanOutLogSink : ILogSink
    {
        private readonly ILogSink[] targets;

        /// <summary>Fan out to these. / これらへ配る。</summary>
        public FanOutLogSink(params ILogSink[] targets)
        {
            this.targets = targets ?? Array.Empty<ILogSink>();
        }

        /// <inheritdoc />
        public void Write(string line)
        {
            foreach (ILogSink target in targets)
            {
                target?.Write(line);
            }
        }
    }

    /// <summary>
    /// Issues the identifiers AGENTS.md's "Logs" defines, in the exact shapes
    /// <c>lib/sfcli/utils/jsonl_log.py</c> checks for: a run id is
    /// <c>20260920T044500Z-1a2b3c4d</c> and a command id the same with a
    /// leading <c>c</c> and a shorter tail. A page issues its own run id only
    /// when it is not served locally; a served page takes the server's.
    ///
    /// AGENTS.md「Logs」が定める識別子を、<c>lib/sfcli/utils/jsonl_log.py</c>
    /// が検査するとおりの形で発行する。run_id は
    /// <c>20260920T044500Z-1a2b3c4d</c>、cmd_id は先頭に <c>c</c> を付け後ろを
    /// 短くした同じ形。ページが自分の run_id を発行するのはローカル配信で
    /// ないときだけで、配信されているページはサーバのものを使う。
    /// </summary>
    public static class RunIdentifiers
    {
        private const string StampFormat = "yyyyMMdd'T'HHmmss'Z'";

        // A run's tail is 8 hex digits and a command's 4 fewer, matching
        // RUN_ID_PATTERN and CMD_ID_PATTERN in jsonl_log.py.
        // 実行の後ろは 16 進 8 桁、命令はそれより 4 桁短い。jsonl_log.py の
        // RUN_ID_PATTERN・CMD_ID_PATTERN に合わせる。
        private const int RunTailDigits = 8;
        private const int CommandTailDigits = 6;

        private static readonly System.Random Entropy = new System.Random();

        /// <summary>
        /// A new run id. / 新しい run_id。
        /// </summary>
        public static string NewRunId()
        {
            return Stamp() + "-" + RandomHex(RunTailDigits);
        }

        /// <summary>
        /// A new command id, for a command the page raised itself (through
        /// <c>window.stampfly.command</c> or a URL argument). A command the
        /// server relayed already has one, which the page must return unchanged.
        /// ページ自身が起こした命令（<c>window.stampfly.command</c> や URL の
        /// 引数）のための新しい cmd_id。サーバが中継した命令は既に持っており、
        /// ページはそれをそのまま返す。
        /// </summary>
        public static string NewCommandId()
        {
            return "c" + Stamp() + "-" + RandomHex(CommandTailDigits);
        }

        /// <summary>
        /// True when the text has the run id shape. / run_id の形かどうか。
        /// </summary>
        public static bool IsRunId(string value)
        {
            return HasShape(value, prefix: string.Empty, tailDigits: RunTailDigits);
        }

        /// <summary>
        /// True when the text has the command id shape. / cmd_id の形かどうか。
        /// </summary>
        public static bool IsCommandId(string value)
        {
            return HasShape(value, prefix: "c", tailDigits: CommandTailDigits);
        }

        /// <summary>
        /// The compact UTC stamp both identifiers start with, so sorting by
        /// name sorts by time.
        /// 両方の識別子が先頭に持つ詰めた形の UTC 日時。名前順が時刻順になる。
        /// </summary>
        private static string Stamp()
        {
            return DateTime.UtcNow.ToString(StampFormat, CultureInfo.InvariantCulture);
        }

        private static string RandomHex(int digits)
        {
            StringBuilder builder = new StringBuilder(digits);
            for (int index = 0; index < digits; index++)
            {
                builder.Append("0123456789abcdef"[Entropy.Next(16)]);
            }

            return builder.ToString();
        }

        /// <summary>
        /// Check one identifier against <c>&lt;prefix&gt;YYYYMMDDTHHMMSSZ-&lt;hex&gt;</c>.
        /// 識別子が <c>&lt;接頭辞&gt;YYYYMMDDTHHMMSSZ-&lt;16 進&gt;</c> かを見る。
        /// </summary>
        private static bool HasShape(string value, string prefix, int tailDigits)
        {
            const int stampLength = 16;  // YYYYMMDDTHHMMSSZ
            int expected = prefix.Length + stampLength + 1 + tailDigits;
            bool wrongLength = string.IsNullOrEmpty(value) || value.Length != expected;
            if (wrongLength || !value.StartsWith(prefix, StringComparison.Ordinal))
            {
                return false;
            }

            string stamp = value.Substring(prefix.Length, stampLength);
            string tail = value.Substring(prefix.Length + stampLength + 1);
            bool separated = value[prefix.Length + stampLength] == '-';
            return separated && IsStamp(stamp) && IsHex(tail);
        }

        private static bool IsStamp(string stamp)
        {
            return DateTime.TryParseExact(stamp, StampFormat, CultureInfo.InvariantCulture,
                                          DateTimeStyles.AdjustToUniversal, out _);
        }

        private static bool IsHex(string text)
        {
            foreach (char character in text)
            {
                bool isHexDigit = (character >= '0' && character <= '9')
                                  || (character >= 'a' && character <= 'f');
                if (!isHexDigit)
                {
                    return false;
                }
            }

            return true;
        }
    }

    /// <summary>
    /// Small helpers for building a <c>data</c> object at a call site without
    /// writing the dictionary's type out each time.
    /// 呼び出し側で <c>data</c> を組み立てる小さな補助。辞書の型を毎回書かずに
    /// 済ませる。
    /// </summary>
    public static class LogData
    {
        /// <summary>One key. / 鍵 1 つ。</summary>
        public static IReadOnlyDictionary<string, object> Of(string key, object value)
        {
            return new Dictionary<string, object> { { key, value } };
        }

        /// <summary>Two keys. / 鍵 2 つ。</summary>
        public static IReadOnlyDictionary<string, object> Of(
            string firstKey, object firstValue, string secondKey, object secondValue)
        {
            return new Dictionary<string, object>
            {
                { firstKey, firstValue },
                { secondKey, secondValue },
            };
        }
    }
}
