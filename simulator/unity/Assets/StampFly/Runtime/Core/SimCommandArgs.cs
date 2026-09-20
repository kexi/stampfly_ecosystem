/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — one command's arguments).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace StampFly.Core
{
    /// <summary>
    /// The arguments of one command, read from the flat JSON object the server
    /// forwards (<c>{"name": "gate_course", "paused": true}</c>).
    ///
    /// <c>JsonUtility</c> cannot read an object whose keys differ per command,
    /// so this reads the outermost object itself and keeps each value as the
    /// text it was written with. A handler then asks for the type it wants:
    /// <c>sf unity cmd --arg</c> sends <c>"0.5"</c> and <c>--json</c> sends
    /// <c>0.5</c>, and both reach <see cref="Double"/> the same way.
    ///
    /// 命令 1 つの引数。サーバが転送する平らな JSON のオブジェクト
    /// （<c>{"name": "gate_course", "paused": true}</c>）から読む。
    ///
    /// <c>JsonUtility</c> は命令ごとに鍵が変わるオブジェクトを読めないので、
    /// 一番外のオブジェクトを自分で読み、各値を書かれたままの文字列で持つ。
    /// 処理は欲しい型で尋ねる。<c>sf unity cmd --arg</c> は <c>"0.5"</c> を、
    /// <c>--json</c> は <c>0.5</c> を送るが、どちらも同じように
    /// <see cref="Double"/> へ届く。
    /// </summary>
    public sealed class SimCommandArgs
    {
        private static readonly Dictionary<string, string> NoValues =
            new Dictionary<string, string>(StringComparer.Ordinal);

        private readonly Dictionary<string, string> values;

        /// <summary>
        /// Build from already-separated values. / 分かれた値から作る。
        /// </summary>
        public SimCommandArgs(Dictionary<string, string> values = null)
        {
            this.values = values ?? NoValues;
        }

        /// <summary>No arguments at all. / 引数が無い場合。</summary>
        public static readonly SimCommandArgs Empty = new SimCommandArgs();

        /// <summary>The keys present. / ある鍵。</summary>
        public IEnumerable<string> Keys => values.Keys;

        /// <summary>How many arguments were given. / 渡された引数の数。</summary>
        public int Count => values.Count;

        /// <summary>Whether a key was given. / 鍵が渡されたか。</summary>
        public bool Has(string key)
        {
            return key != null && values.ContainsKey(key);
        }

        /// <summary>
        /// A string argument, or <paramref name="fallback"/> when absent.
        /// 文字列の引数。無ければ <paramref name="fallback"/>。
        /// </summary>
        public string String(string key, string fallback = "")
        {
            return values.TryGetValue(key ?? string.Empty, out string value) ? value : fallback;
        }

        /// <summary>
        /// A number, or <paramref name="fallback"/> when absent or unreadable.
        /// 数。無い・読めないときは <paramref name="fallback"/>。
        /// </summary>
        public double Double(string key, double fallback = 0.0)
        {
            bool parsed = double.TryParse(String(key, null), NumberStyles.Float,
                                          CultureInfo.InvariantCulture, out double value);
            return parsed ? value : fallback;
        }

        /// <summary>
        /// An integer, or <paramref name="fallback"/>. / 整数。無ければ既定。
        /// </summary>
        public int Int(string key, int fallback = 0)
        {
            bool parsed = int.TryParse(String(key, null), NumberStyles.Integer,
                                       CultureInfo.InvariantCulture, out int value);
            return parsed ? value : fallback;
        }

        /// <summary>
        /// A boolean. <c>true</c> and <c>1</c> are true; everything else falls
        /// back, so a typo does not silently read as false.
        /// 真偽。<c>true</c> と <c>1</c> が真。それ以外は既定に落ちる。打ち
        /// 間違いが黙って偽として読まれないようにする。
        /// </summary>
        public bool Bool(string key, bool fallback = false)
        {
            string text = String(key, null);
            if (text == null)
            {
                return fallback;
            }

            string lowered = text.ToLowerInvariant();
            if (lowered == "true" || lowered == "1")
            {
                return true;
            }

            return lowered == "false" || lowered == "0" ? false : fallback;
        }

        /// <summary>
        /// Read a flat JSON object into arguments. A value that is a string is
        /// unquoted and unescaped; a number, boolean or null is kept as its
        /// literal text; a nested object or array is kept as its raw JSON so a
        /// handler that wants it can read it with its own type.
        ///
        /// 平らな JSON のオブジェクトを引数として読む。文字列の値は引用符と
        /// エスケープを外し、数・真偽・null はその綴りのまま、入れ子の
        /// オブジェクトや配列は元の JSON のまま持つ。欲しい処理が自分の型で
        /// 読めるようにするため。
        /// </summary>
        public static SimCommandArgs Parse(string json)
        {
            Dictionary<string, string> parsed = new Dictionary<string, string>(StringComparer.Ordinal);
            if (string.IsNullOrEmpty(json))
            {
                return new SimCommandArgs(parsed);
            }

            JsonCursor cursor = new JsonCursor(json);
            cursor.SkipWhitespace();
            if (!cursor.Take('{'))
            {
                return new SimCommandArgs(parsed);
            }

            ReadMembers(ref cursor, parsed);
            return new SimCommandArgs(parsed);
        }

        /// <summary>
        /// Read <c>"key": value</c> pairs until the object closes.
        /// オブジェクトが閉じるまで <c>"key": value</c> を読む。
        /// </summary>
        private static void ReadMembers(ref JsonCursor cursor, Dictionary<string, string> into)
        {
            cursor.SkipWhitespace();
            if (cursor.Take('}'))
            {
                return;
            }

            while (!cursor.AtEnd)
            {
                cursor.SkipWhitespace();
                if (!cursor.TryReadString(out string key))
                {
                    return;
                }

                cursor.SkipWhitespace();
                if (!cursor.Take(':'))
                {
                    return;
                }

                cursor.SkipWhitespace();
                if (!cursor.TryReadValue(out string value))
                {
                    return;
                }

                into[key] = value;

                cursor.SkipWhitespace();
                if (cursor.Take(','))
                {
                    continue;
                }

                return;
            }
        }

        /// <summary>
        /// A position in a JSON text. Only as much of JSON as a command's
        /// arguments need: one object, whose values may be anything but are
        /// never inspected below the top level.
        /// JSON の文字列の中の位置。命令の引数に要るだけの JSON。オブジェクト
        /// 1 つで、値は何でもよいが、一番上より下は見ない。
        /// </summary>
        private struct JsonCursor
        {
            private readonly string text;
            private int index;

            internal JsonCursor(string text)
            {
                this.text = text;
                index = 0;
            }

            internal bool AtEnd => index >= text.Length;

            internal void SkipWhitespace()
            {
                while (index < text.Length && char.IsWhiteSpace(text[index]))
                {
                    index++;
                }
            }

            /// <summary>
            /// Consume <paramref name="expected"/> when it is next.
            /// 次が <paramref name="expected"/> なら読み進める。
            /// </summary>
            internal bool Take(char expected)
            {
                if (index < text.Length && text[index] == expected)
                {
                    index++;
                    return true;
                }

                return false;
            }

            /// <summary>
            /// Read a quoted string, resolving the escapes JSON defines.
            /// 引用符付きの文字列を読み、JSON のエスケープを解く。
            /// </summary>
            internal bool TryReadString(out string value)
            {
                value = null;
                if (!Take('"'))
                {
                    return false;
                }

                StringBuilder builder = new StringBuilder();
                while (index < text.Length)
                {
                    char character = text[index++];
                    if (character == '"')
                    {
                        value = builder.ToString();
                        return true;
                    }

                    if (character != '\\')
                    {
                        builder.Append(character);
                        continue;
                    }

                    if (!TryReadEscape(builder))
                    {
                        return false;
                    }
                }

                return false;
            }

            /// <summary>
            /// Resolve one escape sequence after its backslash.
            /// 逆斜線の後ろのエスケープを 1 つ解く。
            /// </summary>
            private bool TryReadEscape(StringBuilder builder)
            {
                if (index >= text.Length)
                {
                    return false;
                }

                char escaped = text[index++];
                switch (escaped)
                {
                    case '"': builder.Append('"'); return true;
                    case '\\': builder.Append('\\'); return true;
                    case '/': builder.Append('/'); return true;
                    case 'b': builder.Append('\b'); return true;
                    case 'f': builder.Append('\f'); return true;
                    case 'n': builder.Append('\n'); return true;
                    case 'r': builder.Append('\r'); return true;
                    case 't': builder.Append('\t'); return true;
                    case 'u': return TryReadUnicode(builder);
                    default: return false;
                }
            }

            private bool TryReadUnicode(StringBuilder builder)
            {
                const int hexDigits = 4;
                if (index + hexDigits > text.Length)
                {
                    return false;
                }

                string digits = text.Substring(index, hexDigits);
                if (!ushort.TryParse(digits, NumberStyles.HexNumber,
                                     CultureInfo.InvariantCulture, out ushort code))
                {
                    return false;
                }

                index += hexDigits;
                builder.Append((char)code);
                return true;
            }

            /// <summary>
            /// Read one value. A string comes back unquoted; anything else comes
            /// back as the raw text it spans, which is what a handler wanting a
            /// number, an array or a nested object needs.
            /// 値を 1 つ読む。文字列は引用符を外して返し、それ以外は元の文字列
            /// のまま返す。数・配列・入れ子のオブジェクトが欲しい処理にはそれが
            /// 要る。
            /// </summary>
            internal bool TryReadValue(out string value)
            {
                if (index < text.Length && text[index] == '"')
                {
                    return TryReadString(out value);
                }

                int start = index;
                SkipRawValue();
                value = text.Substring(start, index - start).Trim();
                return value.Length > 0;
            }

            /// <summary>
            /// Advance past a value that is not a string, keeping nesting and
            /// any quoted text inside it balanced.
            /// 文字列でない値を読み飛ばす。入れ子と、その中の引用された文字列の
            /// 釣り合いを保つ。
            /// </summary>
            private void SkipRawValue()
            {
                int depth = 0;
                while (index < text.Length)
                {
                    char character = text[index];
                    bool endsValue = depth == 0 && (character == ',' || character == '}');
                    if (endsValue)
                    {
                        return;
                    }

                    if (character == '{' || character == '[')
                    {
                        depth++;
                    }
                    else if (character == '}' || character == ']')
                    {
                        depth--;
                    }
                    else if (character == '"')
                    {
                        SkipQuoted();
                        continue;
                    }

                    index++;
                }
            }

            /// <summary>
            /// Advance past a quoted string inside a raw value, so a brace in
            /// the text does not read as nesting.
            /// 元のまま持つ値の中の引用された文字列を読み飛ばす。文の中の括弧を
            /// 入れ子と読み違えないようにする。
            /// </summary>
            private void SkipQuoted()
            {
                index++;
                while (index < text.Length)
                {
                    char character = text[index++];
                    if (character == '\\')
                    {
                        index++;
                        continue;
                    }

                    if (character == '"')
                    {
                        return;
                    }
                }
            }
        }
    }
}
