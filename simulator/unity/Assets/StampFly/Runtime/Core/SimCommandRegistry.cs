/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the command registry).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.Collections.Generic;
using System.Text;

namespace StampFly.Core
{
    /// <summary>
    /// What one command answers: either a value or a reason it could not run.
    /// The three routes into the simulator (<c>sf unity cmd</c>, the page's
    /// <c>window.stampfly.command</c>, the editor's <c>[CliCommand]</c>) all
    /// receive this same shape, which is how the plan's "different routes
    /// produce identical results" is kept true.
    /// 命令 1 つが返すもの。値か、実行できなかった理由のどちらか。シミュレータ
    /// への 3 つの経路（<c>sf unity cmd</c>・ページの
    /// <c>window.stampfly.command</c>・エディタの <c>[CliCommand]</c>）は同じ
    /// 形を受け取る。計画の「経路が違っても結果は同じ」はこれで保つ。
    /// </summary>
    public readonly struct SimCommandResult
    {
        private SimCommandResult(bool ok, string data, string error, bool isDeferred)
        {
            Ok = ok;
            Data = data;
            Error = error;
            IsDeferred = isDeferred;
        }

        /// <summary>Whether the command ran. / 命令が通ったか。</summary>
        public bool Ok { get; }

        /// <summary>
        /// The handler has not answered yet and will answer from a later frame.
        /// Only a command that must watch the simulation run -- <c>sim.wait</c>,
        /// which waits for a virtual time to arrive -- returns this, because a
        /// handler runs on the main thread and blocking there would stop the
        /// very loop it is waiting for.
        /// 処理はまだ答えておらず、後のフレームから答える。これを返すのは、
        /// シミュレーションが進むのを見なければならない命令 ― 仮想時刻の到来を
        /// 待つ <c>sim.wait</c> ― だけである。処理は主スレッドで走るので、そこで
        /// 待つと、待っている当の輪を止めてしまうためである。
        /// </summary>
        public bool IsDeferred { get; }

        /// <summary>
        /// The answer as a JSON value (an object unless the command says
        /// otherwise), or null on failure.
        /// 答えを JSON の値で（命令が別に定めない限りオブジェクト）。失敗の
        /// ときは null。
        /// </summary>
        public string Data { get; }

        /// <summary>The reason it failed, or null. / 失敗の理由。無ければ null。</summary>
        public string Error { get; }

        /// <summary>
        /// A success carrying a JSON value. Passing nothing answers <c>{}</c>,
        /// so a caller always receives an object it can read.
        /// JSON の値を持つ成功。何も渡さなければ <c>{}</c> を返し、呼び出し側は
        /// 常に読めるオブジェクトを受け取る。
        /// </summary>
        public static SimCommandResult Success(string json = "{}")
        {
            return new SimCommandResult(
                true, string.IsNullOrEmpty(json) ? "{}" : json, null, false);
        }

        /// <summary>A failure carrying a reason. / 理由を持つ失敗。</summary>
        public static SimCommandResult Failure(string reason)
        {
            return new SimCommandResult(
                false, null, reason ?? "command failed", false);
        }

        /// <summary>
        /// The handler will answer from a later frame. Whoever routed the
        /// command must not answer now and must keep the <c>cmd_id</c> alive
        /// until the handler does; see <see cref="IsDeferred"/>.
        /// 処理が後のフレームから答える。命令を運んだ側はいま答えてはならず、
        /// 処理が答えるまで <c>cmd_id</c> を保たなければならない。
        /// <see cref="IsDeferred"/> を見よ。
        /// </summary>
        public static SimCommandResult Deferred()
        {
            return new SimCommandResult(true, null, null, true);
        }
    }

    /// <summary>
    /// One command's implementation: it receives the arguments the caller sent
    /// and returns a result. The arguments arrive as strings because
    /// <c>sf unity cmd --arg key=value</c> keeps them as strings on purpose
    /// (the handler knows its own types; guessing in the CLI would turn a world
    /// named <c>2026</c> into a number). <c>--json</c> sends real JSON, so a
    /// handler that wants a number can also read the raw text.
    /// 命令 1 つの実体。呼び出し側が送った引数を受け取り、結果を返す。引数が
    /// 文字列で届くのは、<c>sf unity cmd --arg key=value</c> が意図して文字列の
    /// ままにするため（型は処理側が知っている。CLI で推測すると <c>2026</c> と
    /// いう名前の空間が数値になる）。<c>--json</c> は本物の JSON を送るので、数
    /// が欲しい処理は元の文字列も読める。
    /// </summary>
    public delegate SimCommandResult SimCommandHandler(SimCommandArgs args);

    /// <summary>
    /// The commands the page can run, by name.
    ///
    /// The registry holds no implementation of its own beyond the few commands
    /// that depend on nobody (<c>sim.ping</c>, <c>log.level</c>,
    /// <c>remote.status</c>, <c>help</c>): the owner of each area registers its
    /// own handlers -- the simulation loop registers <c>sim.*</c>,
    /// <c>vehicle.*</c>, <c>rc.*</c>, <c>param.*</c> and <c>plant.*</c>, and the
    /// world side registers <c>world.*</c> and <c>obstacle.*</c>. That is what
    /// keeps this assembly free of references to any other.
    ///
    /// ページが実行できる命令を名前で引く。
    ///
    /// 誰にも依存しない数個（<c>sim.ping</c>・<c>log.level</c>・
    /// <c>remote.status</c>・<c>help</c>）を除き、登録簿は実体を持たない。処理は
    /// それぞれの持ち主が登録する。刻みの輪が <c>sim.*</c>・<c>vehicle.*</c>・
    /// <c>rc.*</c>・<c>param.*</c>・<c>plant.*</c> を、空間の担当が
    /// <c>world.*</c>・<c>obstacle.*</c> を登録する。このアセンブリが他のどれも
    /// 参照しないでいられるのはそのためである。
    /// </summary>
    public sealed class SimCommandRegistry
    {
        /// <summary>
        /// The <c>event</c> names this registry writes. They sit under
        /// <c>cmd.</c> so <c>sf unity logs --event cmd.</c> shows a command's
        /// whole flow, the server's lines and the page's together.
        /// この登録簿が書く <c>event</c> の名前。<c>cmd.</c> の下に置き、
        /// <c>sf unity logs --event cmd.</c> でサーバの行とページの行を合わせた
        /// 1 つの命令の流れが見えるようにする。
        /// </summary>
        public const string StartedEvent = "cmd.started";

        /// <summary>The command finished. / 命令が終わった。</summary>
        public const string FinishedEvent = "cmd.finished";

        /// <summary>The command threw. / 命令が例外を投げた。</summary>
        public const string FailedEvent = "cmd.failed";

        private readonly Dictionary<string, Entry> handlers =
            new Dictionary<string, Entry>(StringComparer.Ordinal);

        private readonly IStructuredLog log;

        /// <summary>
        /// Create a registry that writes its own lines to <paramref name="log"/>.
        /// 自分の行を <paramref name="log"/> へ書く登録簿を作る。
        /// </summary>
        public SimCommandRegistry(IStructuredLog log = null)
        {
            this.log = log;
        }

        /// <summary>
        /// One registered command. / 登録された命令 1 つ。
        /// </summary>
        private readonly struct Entry
        {
            internal Entry(SimCommandHandler handler, string description)
            {
                Handler = handler;
                Description = description;
            }

            internal SimCommandHandler Handler { get; }

            internal string Description { get; }
        }

        /// <summary>
        /// Register one command. Registering a name twice throws rather than
        /// replacing: two owners claiming <c>sim.reset</c> is a mistake that
        /// must surface at startup, not become whichever one ran last.
        /// 命令を 1 つ登録する。同じ名前を二度登録すると差し替えではなく例外に
        /// する。2 人の持ち主が <c>sim.reset</c> を名乗るのは誤りであり、後から
        /// 登録した方が黙って勝つのではなく、起動時に表に出るべきである。
        /// </summary>
        public void Register(string command, SimCommandHandler handler,
                             string description = "")
        {
            if (string.IsNullOrEmpty(command))
            {
                throw new ArgumentException("a command needs a name", nameof(command));
            }

            if (handler == null)
            {
                throw new ArgumentNullException(nameof(handler));
            }

            if (handlers.ContainsKey(command))
            {
                throw new InvalidOperationException(
                    $"the command '{command}' is already registered");
            }

            handlers[command] = new Entry(handler, description ?? string.Empty);
        }

        /// <summary>
        /// Remove a command, so an owner that is being torn down (a scene
        /// unloading, a test finishing) does not leave a handler pointing at a
        /// destroyed object.
        /// 命令を外す。片付けられる持ち主（場面の破棄、試験の終了）が、壊れた
        /// 対象を指す処理を残さないようにする。
        /// </summary>
        public bool Unregister(string command)
        {
            return command != null && handlers.Remove(command);
        }

        /// <summary>Whether a name is registered. / 名前が登録されているか。</summary>
        public bool Has(string command)
        {
            return command != null && handlers.ContainsKey(command);
        }

        /// <summary>How many commands are registered. / 登録された命令の数。</summary>
        public int Count => handlers.Count;

        /// <summary>
        /// Every registered name, sorted, so <c>help</c> reads the same way
        /// twice running.
        /// 登録された名前を並べ替えて返す。<c>help</c> が実行ごとに変わらない
        /// ようにする。
        /// </summary>
        public IReadOnlyList<string> Names()
        {
            List<string> names = new List<string>(handlers.Keys);
            names.Sort(StringComparer.Ordinal);
            return names;
        }

        /// <summary>
        /// Run one command and return its result.
        ///
        /// An unregistered name is a failure naming what was asked for, not an
        /// exception: a typo at a terminal must produce a readable answer. A
        /// handler that throws is also turned into a failure, because one bad
        /// command must not take the page's command loop down with it.
        ///
        /// 命令を 1 つ実行し、結果を返す。
        ///
        /// 登録の無い名前は例外ではなく、求められた名前を述べる失敗にする。
        /// 端末での打ち間違いは読める答えを返すべきである。例外を投げた処理も
        /// 失敗にする。1 つの命令の誤りがページの命令の輪ごと落としては
        /// ならない。
        /// </summary>
        public SimCommandResult Execute(string command, SimCommandArgs args)
        {
            if (!handlers.TryGetValue(command ?? string.Empty, out Entry entry))
            {
                string reason = $"unknown command: {command}";
                Log(LogLevel.Warn, FailedEvent, reason, command);
                return SimCommandResult.Failure(reason);
            }

            Log(LogLevel.Debug, StartedEvent, $"running {command}", command);
            try
            {
                SimCommandResult result = entry.Handler(args);
                LogOutcome(command, result);
                return result;
            }
            catch (Exception failure)
            {
                string reason = $"{command} threw {failure.GetType().Name}: {failure.Message}";
                Log(LogLevel.Error, FailedEvent, reason, command);
                return SimCommandResult.Failure(reason);
            }
        }

        /// <summary>
        /// Record how a command ended. A refusal the handler chose is a warning,
        /// not an error: it is the simulator answering, not breaking.
        /// 命令の終わり方を記録する。処理が自ら断ったものは誤りではなく警告に
        /// する。壊れたのではなく、シミュレータが答えたのである。
        /// </summary>
        private void LogOutcome(string command, in SimCommandResult result)
        {
            if (result.IsDeferred)
            {
                // Nothing has finished yet: the handler answers from a later
                // frame and writes its own line then.
                // まだ何も終わっていない。処理は後のフレームから答え、そのとき
                // 自分の行を書く。
                Log(LogLevel.Debug, StartedEvent, $"{command} is waiting", command);
                return;
            }

            if (result.Ok)
            {
                Log(LogLevel.Debug, FinishedEvent, $"{command} succeeded", command);
                return;
            }

            Log(LogLevel.Warn, FinishedEvent, $"{command} failed: {result.Error}", command);
        }

        private void Log(LogLevel level, string eventName, string message, string command)
        {
            log?.Write(level, LogSources.Command, eventName, message,
                       LogData.Of("command", command ?? string.Empty));
        }

        // -- the commands that depend on nobody / 誰にも依存しない命令 --------

        /// <summary>
        /// Register the commands that need no other part of the simulator.
        /// They are useful before anything else is wired up: <c>sim.ping</c>
        /// proves the whole path from a terminal to the page, <c>help</c> says
        /// what else is available, <c>log.level</c> turns debug lines on, and
        /// <c>remote.status</c> reports how the page is connected.
        /// シミュレータの他の部分を要しない命令を登録する。他が繋がる前から
        /// 役に立つ。<c>sim.ping</c> は端末からページまでの経路全体を示し、
        /// <c>help</c> は他に何があるかを述べ、<c>log.level</c> は debug の行を
        /// 出させ、<c>remote.status</c> はページの繋がり方を報告する。
        /// </summary>
        public void RegisterBuiltIns(StructuredLog pageLog, Func<string> statusJson)
        {
            Register("sim.ping", _ => SimCommandResult.Success(PingJson()),
                     "Answer with the page's identity and clock");
            Register("help", _ => SimCommandResult.Success(HelpJson()),
                     "List the registered commands");
            Register("log.level", args => SetLogLevel(pageLog, args),
                     "Read or set the lowest log level kept (debug/info/warn/error)");
            Register("remote.status",
                     _ => SimCommandResult.Success(statusJson == null ? "{}" : statusJson()),
                     "Report how this page is connected to the local server");
        }

        /// <summary>
        /// <c>sim.ping</c>'s answer: enough to tell one page from another and to
        /// see that the round trip works.
        /// <c>sim.ping</c> の答え。ページを見分け、往復が成り立っていることが
        /// 分かる程度。
        /// </summary>
        private string PingJson()
        {
            StringBuilder builder = new StringBuilder();
            builder.Append("{\"pong\":true,\"commands\":").Append(handlers.Count)
                   .Append(",\"unity_time_s\":")
                   .Append(UnityEngine.Time.realtimeSinceStartup.ToString(
                       "F3", System.Globalization.CultureInfo.InvariantCulture))
                   .Append('}');
            return builder.ToString();
        }

        /// <summary>
        /// <c>help</c>'s answer: every name with its one-line description.
        /// <c>help</c> の答え。名前と 1 行の説明。
        /// </summary>
        private string HelpJson()
        {
            StringBuilder builder = new StringBuilder();
            builder.Append("{\"commands\":[");
            bool first = true;
            foreach (string name in Names())
            {
                if (!first)
                {
                    builder.Append(',');
                }

                first = false;
                builder.Append('{')
                       .Append(LogJson.Quote("name")).Append(':').Append(LogJson.Quote(name))
                       .Append(',')
                       .Append(LogJson.Quote("description")).Append(':')
                       .Append(LogJson.Quote(handlers[name].Description))
                       .Append('}');
            }

            builder.Append("]}");
            return builder.ToString();
        }

        /// <summary>
        /// <c>log.level</c>: report the level, or set it when <c>level</c> is
        /// given. Turning debug on from a terminal is how a page that is already
        /// running starts explaining itself.
        /// <c>log.level</c>: 段を報告し、<c>level</c> が渡されたら設定する。
        /// 動いているページに事情を語らせ始める手段が、端末から debug を入れる
        /// ことである。
        /// </summary>
        private static SimCommandResult SetLogLevel(StructuredLog pageLog, SimCommandArgs args)
        {
            if (pageLog == null)
            {
                return SimCommandResult.Failure("this page has no structured log");
            }

            string requested = args.String("level", null);
            if (requested == null)
            {
                return SimCommandResult.Success(
                    "{\"level\":" + LogJson.Quote(LogJson.LevelName(pageLog.MinimumLevel)) + "}");
            }

            if (!TryParseLevel(requested, out LogLevel level))
            {
                return SimCommandResult.Failure(
                    $"unknown level '{requested}': use debug, info, warn or error");
            }

            pageLog.MinimumLevel = level;
            return SimCommandResult.Success(
                "{\"level\":" + LogJson.Quote(LogJson.LevelName(level)) + "}");
        }

        /// <summary>
        /// Read a level's name. / 段の名前を読む。
        /// </summary>
        public static bool TryParseLevel(string name, out LogLevel level)
        {
            switch ((name ?? string.Empty).ToLowerInvariant())
            {
                case "debug": level = LogLevel.Debug; return true;
                case "info": level = LogLevel.Info; return true;
                case "warn": level = LogLevel.Warn; return true;
                case "error": level = LogLevel.Error; return true;
                default: level = LogLevel.Info; return false;
            }
        }
    }
}
