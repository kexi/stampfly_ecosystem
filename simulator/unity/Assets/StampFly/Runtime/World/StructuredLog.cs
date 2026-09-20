using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEngine;

namespace StampFly.World
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
    /// Where this package sends its log lines. It is an interface so the world
    /// code does not decide how a line reaches logs/unity/&lt;run_id&gt;.jsonl:
    /// the page-side logger that carries `run_id` and forwards to the server is
    /// somebody else's work (plan stage 3/6), and it is injected here when it
    /// exists. Until then <see cref="UnityDebugStructuredLog"/> prints the same
    /// JSON line to the editor console.
    ///
    /// この一式がログ 1 行をどこへ出すか。行が logs/unity/&lt;run_id&gt;.jsonl へ
    /// 届くまでの道筋を空間側が決めないよう、インターフェースにしてある。
    /// `run_id` を持ってサーバへ中継するページ側のログ担当は別の仕事（計画の
    /// 段階 3・6）で、できたらここへ注入する。それまでは
    /// <see cref="UnityDebugStructuredLog"/> が同じ JSON の 1 行をエディタの
    /// コンソールへ出す。
    /// </summary>
    public interface IStructuredLog
    {
        /// <summary>
        /// Write one event. `source` is AGENTS.md's `src` (this package always
        /// passes "world"), `eventName` its `event`, and `data` the per-event
        /// values that go under `data`. The implementation adds `ts`, `run_id`
        /// and any correlation keys it holds.
        /// 事象を 1 件書く。source は AGENTS.md の `src`（この一式は常に
        /// "world"）、eventName は `event`、data は `data` の下に置く事象ごとの
        /// 値。`ts`・`run_id` と相関の鍵は実装側が付ける。
        /// </summary>
        void Write(LogLevel level, string source, string eventName, string message,
                   IReadOnlyDictionary<string, object> data);
    }

    /// <summary>
    /// The `src` and `event` names this package uses, kept in one place so they
    /// match docs/commands/sf-unity.md §7 and AGENTS.md's "Logs".
    /// この一式が使う `src` と `event` の名前。docs/commands/sf-unity.md §7 と
    /// AGENTS.md「Logs」に合わせるため 1 か所にまとめる。
    /// </summary>
    public static class WorldLogEvents
    {
        /// <summary>The `src` for everything in this package. / この一式の `src`。</summary>
        public const string Source = "world";

        /// <summary>A world was built into the scene. / 空間を場面に生成した。</summary>
        public const string Loaded = "world.loaded";

        /// <summary>A world file was refused, with a reason. / 空間ファイルを断った。</summary>
        public const string Rejected = "world.rejected";

        /// <summary>A world was removed from the scene. / 空間を場面から片付けた。</summary>
        public const string Cleared = "world.cleared";
    }

    /// <summary>
    /// The default log: one JSON line per event through Unity's console. It
    /// writes the keys AGENTS.md requires, leaving `run_id` empty because this
    /// package is not the entry point that issues one.
    /// 既定のログ。1 事象 1 行の JSON を Unity のコンソールへ出す。AGENTS.md が
    /// 求める鍵を書くが、`run_id` はこの一式が発行する立場に無いので空にする。
    /// </summary>
    public sealed class UnityDebugStructuredLog : IStructuredLog
    {
        private const string TimestampFormat = "yyyy-MM-dd'T'HH:mm:ss.fff'Z'";

        private readonly string runId;

        /// <summary>
        /// Create a log, optionally carrying the run id of the surrounding run.
        /// ログを作る。実行 1 回の run_id が分かっていれば渡す。
        /// </summary>
        public UnityDebugStructuredLog(string runId = "")
        {
            this.runId = runId ?? string.Empty;
        }

        /// <inheritdoc />
        public void Write(LogLevel level, string source, string eventName, string message,
                          IReadOnlyDictionary<string, object> data)
        {
            string line = Compose(level, source, eventName, message, data);
            if (level == LogLevel.Error)
            {
                Debug.LogError(line);
                return;
            }

            if (level == LogLevel.Warn)
            {
                Debug.LogWarning(line);
                return;
            }

            Debug.Log(line);
        }

        /// <summary>
        /// Build the JSON line. / JSON の 1 行を組み立てる。
        /// </summary>
        private string Compose(LogLevel level, string source, string eventName, string message,
                               IReadOnlyDictionary<string, object> data)
        {
            StringBuilder builder = new StringBuilder();
            builder.Append('{');
            AppendString(builder, "ts", DateTime.UtcNow.ToString(TimestampFormat, CultureInfo.InvariantCulture));
            builder.Append(',');
            AppendString(builder, "level", level.ToString().ToLowerInvariant());
            builder.Append(',');
            AppendString(builder, "src", source);
            builder.Append(',');
            AppendString(builder, "event", eventName);
            builder.Append(',');
            AppendString(builder, "run_id", runId);
            builder.Append(',');
            AppendString(builder, "msg", message);
            AppendData(builder, data);
            builder.Append('}');
            return builder.ToString();
        }

        /// <summary>
        /// Append the `data` object when the event carries values.
        /// 事象が値を持つときだけ `data` を足す。
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
        /// so `jq` can compare them without converting.
        /// 鍵と値を 1 つ足す。数と真偽は引用符を付けず、`jq` が変換せずに比べ
        /// られるようにする。
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
                    builder.Append(Quote(key)).Append(':').Append(number.ToString(CultureInfo.InvariantCulture));
                    return;
                case long number:
                    builder.Append(Quote(key)).Append(':').Append(number.ToString(CultureInfo.InvariantCulture));
                    return;
                case float number:
                    builder.Append(Quote(key)).Append(':').Append(number.ToString("R", CultureInfo.InvariantCulture));
                    return;
                case double number:
                    builder.Append(Quote(key)).Append(':').Append(number.ToString("R", CultureInfo.InvariantCulture));
                    return;
                default:
                    AppendString(builder, key, value.ToString());
                    return;
            }
        }

        private static void AppendString(StringBuilder builder, string key, string value)
        {
            builder.Append(Quote(key)).Append(':').Append(Quote(value));
        }

        /// <summary>
        /// Quote and escape a JSON string. / JSON の文字列を引用しエスケープする。
        /// </summary>
        private static string Quote(string value)
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
}
