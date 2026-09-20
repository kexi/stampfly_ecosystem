using System.Collections.Generic;
using StampFly.Core;
using UnityEngine;

// `IStructuredLog` and `LogLevel` used to be declared here. They moved to
// `StampFly.Core`, which references no other assembly, so that the page-side
// logger, the simulation loop and the firmware pump can all depend on the
// contract without depending on the world. `using StampFly.Core` above is what
// keeps every `LogLevel` in this package resolving, unchanged.
// `IStructuredLog` と `LogLevel` はここで宣言していた。他のどのアセンブリも
// 参照しない `StampFly.Core` へ移した。ページ側のログ担当・刻みの輪・ファームの
// ログの汲み出しが、空間に依存せず約束にだけ依存できるようにするためである。
// この一式の `LogLevel` がそのまま解決できるのは、上の `using StampFly.Core`
// による。

namespace StampFly.World
{
    /// <summary>
    /// The `src` and `event` names this package uses, kept in one place so they
    /// match docs/commands/sf-unity.md §7 and AGENTS.md's "Logs".
    /// この一式が使う `src` と `event` の名前。docs/commands/sf-unity.md §7 と
    /// AGENTS.md「Logs」に合わせるため 1 か所にまとめる。
    /// </summary>
    public static class WorldLogEvents
    {
        /// <summary>The `src` for everything in this package. / この一式の `src`。</summary>
        public const string Source = LogSources.World;

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
    /// package is not the entry point that issues one. The page-side
    /// <see cref="StampFly.Core.StructuredLog"/>, which carries `run_id` and
    /// forwards to the local server, replaces it through
    /// <see cref="WorldLoader.Log"/> once a page has one.
    ///
    /// 既定のログ。1 事象 1 行の JSON を Unity のコンソールへ出す。AGENTS.md が
    /// 求める鍵を書くが、`run_id` はこの一式が発行する立場に無いので空にする。
    /// `run_id` を持ってサーバへ中継するページ側の
    /// <see cref="StampFly.Core.StructuredLog"/> は、ページが持ったときに
    /// <see cref="WorldLoader.Log"/> 経由で差し替える。
    /// </summary>
    public sealed class UnityDebugStructuredLog : IStructuredLog
    {
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
            string line = LogJson.Compose(new LogRecord
            {
                Timestamp = LogJson.Now(),
                Level = level,
                Source = source,
                EventName = eventName,
                RunId = runId,
                Message = message,
                Data = data,
            });

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
    }
}
