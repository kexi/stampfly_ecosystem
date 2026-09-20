/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — JSON for one log line).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace StampFly.Core
{
    /// <summary>
    /// Turns one log line into JSON.
    ///
    /// Written by hand rather than with <c>JsonUtility</c>, which serializes
    /// only fields of a declared type and cannot express the <c>data</c> object
    /// whose keys differ per event. The keys and their order follow AGENTS.md
    /// "Logs": the required six first, then the correlation keys, then
    /// <c>data</c>. The server checks the same list in
    /// <c>lib/sfcli/utils/jsonl_log.py</c>, so anything this class emits must
    /// pass <c>validate_record</c>.
    ///
    /// ログ 1 行を JSON にする。
    ///
    /// <c>JsonUtility</c> は宣言した型のフィールドしか直列化できず、事象ごとに
    /// 鍵が変わる <c>data</c> を表せないため、手で書く。鍵と並びは AGENTS.md
    /// 「Logs」に従う。必須の 6 つが先、次に相関の鍵、最後に <c>data</c>。
    /// サーバは <c>lib/sfcli/utils/jsonl_log.py</c> で同じ一覧を検査するので、
    /// このクラスが出したものは <c>validate_record</c> を通らなければならない。
    /// </summary>
    public static class LogJson
    {
        /// <summary>
        /// The timestamp format AGENTS.md requires: UTC, RFC 3339, milliseconds.
        /// AGENTS.md が求める日時の形。UTC・RFC 3339・ミリ秒。
        /// </summary>
        public const string TimestampFormat = "yyyy-MM-dd'T'HH:mm:ss.fff'Z'";

        /// <summary>
        /// This instant as the <c>ts</c> value. / いまの時刻を <c>ts</c> の値で。
        /// </summary>
        public static string Now()
        {
            return DateTime.UtcNow.ToString(TimestampFormat, CultureInfo.InvariantCulture);
        }

        /// <summary>
        /// The <c>level</c> spelling of a severity (<c>debug</c>, <c>info</c>,
        /// <c>warn</c>, <c>error</c>).
        /// 重さの <c>level</c> の綴り。
        /// </summary>
        public static string LevelName(LogLevel level)
        {
            switch (level)
            {
                case LogLevel.Debug: return "debug";
                case LogLevel.Warn: return "warn";
                case LogLevel.Error: return "error";
                default: return "info";
            }
        }

        /// <summary>
        /// Compose one JSON line from a record whose values are already the
        /// right types. A null or empty correlation value is left out rather
        /// than written, because AGENTS.md says a correlation key appears only
        /// when it applies.
        /// 値の型が揃った 1 件から JSON の 1 行を組み立てる。相関の鍵は値が
        /// null・空なら書かずに落とす。AGENTS.md は当てはまるときだけ入れる
        /// と定めているため。
        /// </summary>
        public static string Compose(in LogRecord record)
        {
            StringBuilder builder = new StringBuilder(256);
            builder.Append('{');
            AppendString(builder, "ts", record.Timestamp);
            builder.Append(',');
            AppendString(builder, "level", LevelName(record.Level));
            builder.Append(',');
            AppendString(builder, "src", record.Source);
            builder.Append(',');
            AppendString(builder, "event", record.EventName);
            builder.Append(',');
            AppendString(builder, "run_id", record.RunId);
            builder.Append(',');
            AppendString(builder, "msg", record.Message);

            AppendOptionalString(builder, "cmd_id", record.CommandId);
            AppendOptionalString(builder, "boot_id", record.BootId);
            AppendOptionalLong(builder, "sim_us", record.SimulationMicroseconds);
            AppendOptionalLong(builder, "tick", record.Tick);
            AppendOptionalLong(builder, "frame", record.Frame);
            AppendOptionalString(builder, "tag", record.Tag);

            AppendData(builder, record.Data);
            builder.Append('}');
            return builder.ToString();
        }

        /// <summary>
        /// Append <c>"key":"value"</c> only when the value carries something.
        /// 値があるときだけ <c>"key":"value"</c> を足す。
        /// </summary>
        private static void AppendOptionalString(StringBuilder builder, string key, string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return;
            }

            builder.Append(',');
            AppendString(builder, key, value);
        }

        /// <summary>
        /// Append a numeric correlation key only when it was supplied.
        /// 渡された数の相関の鍵だけを足す。
        /// </summary>
        private static void AppendOptionalLong(StringBuilder builder, string key, long? value)
        {
            if (!value.HasValue)
            {
                return;
            }

            builder.Append(',').Append(Quote(key)).Append(':')
                   .Append(value.Value.ToString(CultureInfo.InvariantCulture));
        }

        /// <summary>
        /// Append the <c>data</c> object when the event carries values.
        /// 事象が値を持つときだけ <c>data</c> を足す。
        /// </summary>
        private static void AppendData(StringBuilder builder, IReadOnlyDictionary<string, object> data)
        {
            if (data == null || data.Count == 0)
            {
                return;
            }

            builder.Append(",\"data\":{");
            bool first = true;
            foreach (KeyValuePair<string, object> entry in data)
            {
                if (!first)
                {
                    builder.Append(',');
                }

                first = false;
                AppendValue(builder, entry.Key, entry.Value);
            }

            builder.Append('}');
        }

        /// <summary>
        /// Append one key and its value, keeping numbers and booleans unquoted
        /// so <c>jq</c> can compare them without converting. Anything else is
        /// written as its string form, because a log line is read by people and
        /// by <c>jq</c>, not deserialized back into objects.
        /// 鍵と値を 1 つ足す。数と真偽は引用符を付けず、<c>jq</c> が変換せずに
        /// 比べられるようにする。それ以外は文字列にする。ログの行は人と
        /// <c>jq</c> が読むもので、オブジェクトへ戻すものではないため。
        /// </summary>
        private static void AppendValue(StringBuilder builder, string key, object value)
        {
            switch (value)
            {
                case null:
                    builder.Append(Quote(key)).Append(":null");
                    return;
                case bool flag:
                    builder.Append(Quote(key)).Append(':').Append(flag ? "true" : "false");
                    return;
                case int number:
                    AppendNumber(builder, key, number.ToString(CultureInfo.InvariantCulture));
                    return;
                case long number:
                    AppendNumber(builder, key, number.ToString(CultureInfo.InvariantCulture));
                    return;
                case float number:
                    AppendFinite(builder, key, number, number.ToString("R", CultureInfo.InvariantCulture));
                    return;
                case double number:
                    AppendFinite(builder, key, number, number.ToString("R", CultureInfo.InvariantCulture));
                    return;
                default:
                    AppendString(builder, key, value.ToString());
                    return;
            }
        }

        /// <summary>
        /// Append a number that is already in its JSON spelling.
        /// JSON の綴りになっている数を足す。
        /// </summary>
        private static void AppendNumber(StringBuilder builder, string key, string literal)
        {
            builder.Append(Quote(key)).Append(':').Append(literal);
        }

        /// <summary>
        /// Append a floating-point value, quoting NaN and the infinities. JSON
        /// has no spelling for them, and an unquoted `NaN` would make the whole
        /// line unreadable to `jq` -- one bad sensor reading must not cost the
        /// rest of the run's log.
        /// 浮動小数を足す。NaN と無限大は引用符で囲む。JSON にはこれらの綴りが
        /// 無く、裸の `NaN` は行ごと `jq` で読めなくする。センサの値 1 つの
        /// 異常で、その実行のログ全体を失ってはならない。
        /// </summary>
        private static void AppendFinite(StringBuilder builder, string key, double value, string literal)
        {
            bool isWritable = !double.IsNaN(value) && !double.IsInfinity(value);
            if (isWritable)
            {
                AppendNumber(builder, key, literal);
                return;
            }

            AppendString(builder, key, literal);
        }

        private static void AppendString(StringBuilder builder, string key, string value)
        {
            builder.Append(Quote(key)).Append(':').Append(Quote(value));
        }

        /// <summary>
        /// Quote and escape a JSON string. / JSON の文字列を引用しエスケープする。
        /// </summary>
        public static string Quote(string value)
        {
            StringBuilder quoted = new StringBuilder(value == null ? 2 : value.Length + 2);
            quoted.Append('"');
            foreach (char character in value ?? string.Empty)
            {
                AppendEscaped(quoted, character);
            }

            quoted.Append('"');
            return quoted.ToString();
        }

        private static void AppendEscaped(StringBuilder builder, char character)
        {
            switch (character)
            {
                case '"':
                    builder.Append("\\\"");
                    return;
                case '\\':
                    builder.Append("\\\\");
                    return;
                case '\n':
                    builder.Append("\\n");
                    return;
                case '\r':
                    builder.Append("\\r");
                    return;
                case '\t':
                    builder.Append("\\t");
                    return;
                default:
                    AppendPlainOrUnicode(builder, character);
                    return;
            }
        }

        private static void AppendPlainOrUnicode(StringBuilder builder, char character)
        {
            // Control characters have no literal spelling in JSON.
            // 制御文字は JSON にそのままでは書けない。
            const char lowestPrintable = ' ';
            if (character < lowestPrintable)
            {
                builder.Append("\\u").Append(((int)character).ToString("x4", CultureInfo.InvariantCulture));
                return;
            }

            builder.Append(character);
        }
    }

    /// <summary>
    /// One log line's values, before they become JSON. A struct so composing a
    /// line allocates nothing beyond the string itself.
    /// JSON になる前のログ 1 行の値。行の文字列以外に割り当てが生じないよう
    /// 構造体にする。
    /// </summary>
    public struct LogRecord
    {
        /// <summary>UTC, RFC 3339, milliseconds. / UTC・RFC 3339・ミリ秒。</summary>
        public string Timestamp;

        /// <summary>How severe. / 重さ。</summary>
        public LogLevel Level;

        /// <summary>AGENTS.md's <c>src</c>. / AGENTS.md の <c>src</c>。</summary>
        public string Source;

        /// <summary>The dotted event name. / 点区切りの事象名。</summary>
        public string EventName;

        /// <summary>One per run. / 実行 1 回に 1 つ。</summary>
        public string RunId;

        /// <summary>One short human-readable line. / 人が読む短い 1 行。</summary>
        public string Message;

        /// <summary>The command being handled, when one is. / 処理中の命令。</summary>
        public string CommandId;

        /// <summary>The firmware's power-up, when known. / ファームの電源投入。</summary>
        public string BootId;

        /// <summary>The virtual clock [µs], when known. / 仮想時刻 [µs]。</summary>
        public long? SimulationMicroseconds;

        /// <summary>The control step, when known. / 制御の刻み。</summary>
        public long? Tick;

        /// <summary>The rendered frame, when known. / 描画のコマ。</summary>
        public long? Frame;

        /// <summary>
        /// The firmware's ESP_LOGx tag. Only <c>src: "fw"</c> lines carry it;
        /// it is not one of AGENTS.md's correlation keys but the format's
        /// documented extra key for firmware lines (sf-unity.md §8).
        /// ファームの ESP_LOGx のタグ。<c>src: "fw"</c> の行だけが持つ。
        /// AGENTS.md の相関の鍵ではなく、ファームの行のために形式が定める
        /// 追加の鍵（sf-unity.md §8）。
        /// </summary>
        public string Tag;

        /// <summary>Per-event values. / 事象ごとの値。</summary>
        public IReadOnlyDictionary<string, object> Data;
    }
}
