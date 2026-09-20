/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the page's side of sf unity).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Collections.Generic;
using StampFly.Core;
using UnityEngine;

namespace StampFly.Remote
{
    /// <summary>
    /// The page's end of <c>sf unity</c>: it owns the run's
    /// <see cref="StructuredLog"/>, the <see cref="SimCommandRegistry"/> every
    /// owner registers into, and the commands the URL asked for.
    ///
    /// Put one of these in the scene. It exists in development builds and in
    /// the editor only -- the assembly's <c>defineConstraints</c> keep the whole
    /// of <c>StampFly.Remote</c> out of a distributed build, which is the plan's
    /// "the relay endpoint is included only in development builds" (§4).
    ///
    /// The order matters and is fixed here: the log and the registry exist
    /// before <c>Start</c> returns, so an owner may register a command from its
    /// own <c>Awake</c> or <c>Start</c> without caring which ran first. The
    /// commands from the URL run only once the handshake has settled, because a
    /// locally served page must carry the server's <c>run_id</c> on the lines
    /// those commands produce.
    ///
    /// <c>sf unity</c> のページ側。この実行の <see cref="StructuredLog"/>、
    /// 持ち主が登録する <see cref="SimCommandRegistry"/>、URL が求めた命令を持つ。
    ///
    /// 場面に 1 つ置く。開発用ビルドとエディタにしか存在しない。アセンブリの
    /// <c>defineConstraints</c> が <c>StampFly.Remote</c> を配布用ビルドから
    /// 丸ごと外す。これが計画 §4 の「中継の受け口は開発用ビルドだけに入れる」
    /// である。
    ///
    /// 順序は重要なのでここで固定する。ログと登録簿は <c>Start</c> が戻る前に
    /// 存在するので、持ち主は自分の <c>Awake</c>・<c>Start</c> のどちらからでも、
    /// どちらが先かを気にせず登録できる。URL の命令は握手が落ち着いてから走る。
    /// ローカル配信のページは、その命令が出す行にサーバの <c>run_id</c> を載せ
    /// なければならないためである。
    /// </summary>
    [DefaultExecutionOrder(ExecutionOrder)]
    public sealed class RemoteBridge : MonoBehaviour
    {
        /// <summary>
        /// Runs before everything that logs. A negative order puts this
        /// component's <c>Awake</c> ahead of the default ones, so the log and
        /// the registry are there when an owner's <c>Awake</c> looks for them.
        /// ログを出す全てより先に動く。負の順序でこの部品の <c>Awake</c> を既定の
        /// ものより前に置き、持ち主の <c>Awake</c> が探したときにログと登録簿が
        /// あるようにする。
        /// </summary>
        public const int ExecutionOrder = -10000;

        /// <summary>
        /// The <c>event</c> names this bridge writes about itself.
        /// この橋が自分について書く <c>event</c> の名前。
        /// </summary>
        public const string ReadyEvent = "bridge.ready";

        /// <summary>The page took the server's run id. / サーバの run_id を受けた。</summary>
        public const string HelloEvent = "bridge.hello";

        /// <summary>A command arrived from the server. / サーバから命令が届いた。</summary>
        public const string CommandEvent = "bridge.command";

        [SerializeField]
        [Tooltip("The lowest log level kept. / 残す最も低い段。")]
        private LogLevel minimumLevel = LogLevel.Info;

        [SerializeField]
        [Tooltip("How many lines window.stampfly.logs() keeps. / 保持する行数。")]
        private int ringCapacity = StructuredLog.DefaultRingCapacity;

        private RemoteMode mode = RemoteMode.Unknown;
        private bool settled;
        private EditorFileLogSink editorFile;
        private string deferredCommandId;
        private readonly List<PendingCommand> urlCommands = new List<PendingCommand>();

        /// <summary>
        /// The one bridge in the scene, for an owner that has no reference to
        /// it. Null in a distributed build, where this assembly is absent --
        /// which is why every caller must check rather than assume.
        /// 場面にある 1 つの橋。参照を持たない持ち主のため。このアセンブリが無い
        /// 配布用ビルドでは null になる。呼び出し側が前提にせず確かめるべき理由が
        /// これである。
        /// </summary>
        public static RemoteBridge Instance { get; private set; }

        /// <summary>
        /// This run's log. Every owner writes through it, so every line carries
        /// the same <c>run_id</c> and the <c>cmd_id</c> of whatever command is
        /// being handled.
        /// この実行のログ。持ち主は全てこれを通して書く。全ての行が同じ
        /// <c>run_id</c> と、処理中の命令の <c>cmd_id</c> を持つ。
        /// </summary>
        public StructuredLog Log { get; private set; }

        /// <summary>
        /// The commands this page can run. An owner registers its own into it
        /// from <c>Awake</c> or <c>Start</c>.
        /// このページが実行できる命令。持ち主が <c>Awake</c>・<c>Start</c> から
        /// 自分の分を登録する。
        /// </summary>
        public SimCommandRegistry Commands { get; private set; }

        /// <summary>
        /// The firmware's log records, turned into lines. The owner of
        /// <c>IFirmware</c> reads the records out of the module and calls
        /// <see cref="FirmwareLogPump.Pump"/> with each one.
        /// ファームのログの記録を行にするもの。<c>IFirmware</c> の持ち主が
        /// モジュールから記録を取り出し、1 件ずつ
        /// <see cref="FirmwareLogPump.Pump"/> へ渡す。
        /// </summary>
        public FirmwareLogPump FirmwareLog { get; private set; }

        /// <summary>
        /// How this page reached its run id. / このページが run_id を得た経路。
        /// </summary>
        public RemoteMode Mode => mode;

        /// <summary>
        /// Build the log and the registry before anyone can log.
        /// 誰かが記録する前に、ログと登録簿を作る。
        /// </summary>
        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                // Two bridges would mean two run ids and two registries; the
                // second one is a mistake in the scene, not a configuration.
                // 橋が 2 つあると run_id も登録簿も 2 つになる。2 つ目は設定では
                // なく場面の誤りである。
                Destroy(this);
                return;
            }

            Instance = this;

            // The page starts with an id of its own. A locally served page
            // replaces it the moment /api/hello answers; until then, lines are
            // still coherent rather than run-less.
            // ページは自前の id で始まる。ローカル配信のページは /api/hello が
            // 応えた時点で差し替える。それまでの行も、実行を持たない行ではなく
            // 一続きとして読める。
            Log = new StructuredLog(RunIdentifiers.NewRunId(), null, ringCapacity)
            {
                MinimumLevel = minimumLevel,
            };
            Log.SetSink(OpenSink(Log.RunId));
            Commands = new SimCommandRegistry(Log);
            FirmwareLog = new FirmwareLogPump(Log);

            Commands.RegisterBuiltIns(Log, StatusJson);
        }

        /// <summary>
        /// Open where this run's lines go.
        ///
        /// In a browser that is the <c>.jslib</c>, which batches them to
        /// <c>POST /api/log</c> and keeps them for
        /// <c>window.stampfly.logs()</c>. In the editor there is no
        /// <c>/api/hello</c> to answer and no page to keep them, so the lines go
        /// to a file under <c>simulator/unity/Logs/stampfly/</c> and to the
        /// console, which is where somebody pressing play will look.
        ///
        /// この実行の行の行き先を開く。
        ///
        /// ブラウザでは <c>.jslib</c> で、まとめて <c>POST /api/log</c> へ送り
        /// <c>window.stampfly.logs()</c> のために保持する。エディタには応える
        /// <c>/api/hello</c> も、保持するページも無いので、
        /// <c>simulator/unity/Logs/stampfly/</c> の下のファイルと、再生ボタンを
        /// 押した人が見るコンソールへ出す。
        /// </summary>
        private ILogSink OpenSink(string runId)
        {
            // Chosen at compile time rather than with `if (RemoteNative.Available)`,
            // because that field is a constant and the pruned branch would warn
            // about unreachable code in whichever build it is not taken.
            // `if (RemoteNative.Available)` ではなく翻訳時に選ぶ。あちらは定数で、
            // 通らない側の枝が、そのビルドで到達できないコードとして警告になる
            // ためである。
#if UNITY_WEBGL && !UNITY_EDITOR
            return new RemoteLogSink();
#else
            editorFile = new EditorFileLogSink(runId);
            return new FanOutLogSink(editorFile, new UnityConsoleLogSink());
#endif
        }

        /// <summary>
        /// Start the JavaScript side. The object's name is what
        /// <c>SendMessage</c> addresses, so it is passed rather than assumed.
        /// JavaScript 側を始める。<c>SendMessage</c> が宛先にするのはこの対象の
        /// 名前なので、決め打ちせず渡す。
        /// </summary>
        private void Start()
        {
            RemoteNative.Start(gameObject.name, nameof(ReceiveCommand));
            Log.Write(LogLevel.Info, LogSources.Bridge, ReadyEvent,
                      "page bridge started",
                      LogData.Of("jslib", RemoteNative.Available));
        }

        /// <summary>
        /// Watch for the handshake to settle, and report lines the page's queue
        /// had to drop. Both are cheap enough to check every frame and neither
        /// can be event-driven: <c>/api/hello</c> answers on JavaScript's own
        /// schedule, and a drop happens inside the queue without telling C#.
        /// 握手が落ち着くのを待ち、ページの待ち行列が捨てた行を報告する。どちらも
        /// 毎フレーム見ても安く、どちらも事象では受け取れない。<c>/api/hello</c>
        /// は JavaScript の都合で応え、行の破棄は C# に知らせず待ち行列の中で
        /// 起きる。
        /// </summary>
        private void Update()
        {
            if (!settled)
            {
                CheckHandshake();
            }

            int dropped = RemoteNative.TakeDropped();
            if (dropped > 0)
            {
                Log.ReportDropped(dropped, "the page's send queue overflowed");
            }
        }

        /// <summary>
        /// Adopt the server's run id once <c>/api/hello</c> has answered, then
        /// run whatever the URL asked for.
        /// <c>/api/hello</c> が応えたらサーバの run_id を採用し、URL が求めた
        /// ものを実行する。
        /// </summary>
        private void CheckHandshake()
        {
            mode = RemoteNative.Mode();
            if (mode == RemoteMode.Unknown)
            {
                return;
            }

            settled = true;
            if (mode == RemoteMode.Local)
            {
                Log.AdoptRunId(RemoteNative.RunId());
            }

            Log.Write(LogLevel.Info, LogSources.Bridge, HelloEvent,
                      mode == RemoteMode.Local
                          ? "served by sf unity serve"
                          : "no local server; this page issued its own run id",
                      LogData.Of("mode", mode.ToString().ToLowerInvariant(),
                                 "run_id", Log.RunId));

            RunUrlCommands();
        }

        /// <summary>
        /// Run the commands the URL stands for (<c>?world=gate_course</c>).
        /// They go through the registry like any other command, so the same
        /// handler answers whichever route was taken.
        /// URL が表す命令（<c>?world=gate_course</c>）を実行する。他の命令と同じ
        /// 登録簿を通るので、どの経路でも同じ処理が答える。
        /// </summary>
        private void RunUrlCommands()
        {
            urlCommands.Clear();
            PendingCommand.ParseList(RemoteNative.UrlCommands(), urlCommands);

            foreach (PendingCommand request in urlCommands)
            {
                Handle(request.WithNewId(), answerJavaScript: false);
            }
        }

        /// <summary>
        /// Receive one command from <c>RemoteBridge.jslib</c>. <c>SendMessage</c>
        /// carries a single string, so the whole request is one JSON object.
        /// <c>RemoteBridge.jslib</c> から命令を 1 つ受け取る。<c>SendMessage</c> は
        /// 文字列を 1 つしか運ばないので、要求全体を 1 つの JSON にする。
        /// </summary>
        public void ReceiveCommand(string payload)
        {
            Handle(PendingCommand.Parse(payload), answerJavaScript: true);
        }

        /// <summary>
        /// Run one command inside its own <c>cmd_id</c> scope, so every line the
        /// handler produces -- the world's, the simulation's, the firmware's --
        /// carries the same id as the CLI's and the server's lines. That is what
        /// makes <c>sf unity logs --cmd &lt;id&gt;</c> show one command's whole
        /// flow in time order.
        /// 命令 1 つを自分の <c>cmd_id</c> の範囲の中で実行する。処理が出す全ての
        /// 行 ― 空間・シミュレーション・ファーム ― が CLI とサーバの行と同じ
        /// 識別子を持つ。<c>sf unity logs --cmd &lt;id&gt;</c> が 1 つの命令の
        /// 流れを時刻順に見せられるのはこのためである。
        /// </summary>
        private void Handle(PendingCommand request, bool answerJavaScript)
        {
            using (Log.BeginCommand(request.CommandId))
            {
                Log.Write(LogLevel.Debug, LogSources.Bridge, CommandEvent,
                          $"received {request.Command}",
                          LogData.Of("command", request.Command));

                // Offered BEFORE the handler runs, because a handler that means
                // to answer later reads it from inside its own call -- that is
                // the only moment it can know which command it is handling.
                // Offered only on a route that can hold the request open; the
                // URL's own commands answer nobody, so they get none and a
                // handler refuses rather than waiting forever.
                // 処理が走る**前**に渡す。後で答えるつもりの処理は、自分の呼び出し
                // の中でこれを読むためで、自分がどの命令を処理しているかを知れる
                // のはその瞬間だけである。渡すのは要求を保持できる経路に対して
                // だけ。URL 自身の命令は誰にも答えないので何も渡らず、処理は永遠に
                // 待つのではなく断る。
                deferredCommandId = answerJavaScript ? request.CommandId : null;
                SimCommandResult result = Commands.Execute(request.Command, request.Args);
                deferredCommandId = null;

                // A handler that asked to answer later kept the cmd_id: the
                // answer goes out from AnswerLater, in whichever frame the
                // handler finishes in.
                // 後で答えると言った処理は cmd_id を預かっている。答えは、処理が
                // 終わるフレームで AnswerLater から出る。
                if (result.IsDeferred)
                {
                    return;
                }

                if (answerJavaScript)
                {
                    RemoteNative.Answer(request.CommandId, result.Ok, result.Data, result.Error);
                }
            }
        }

        /// <summary>
        /// The id of the command the running handler may answer later, or null.
        /// A handler reads it from inside its own call and holds it; nothing
        /// else may.
        /// 走っている処理が後で答えてよい命令の識別子。無ければ null。処理は
        /// 自分の呼び出しの中でこれを読んで保持する。他の誰も読んではならない。
        /// </summary>
        public string DeferredCommandId => deferredCommandId;

        /// <summary>
        /// Answer a command whose handler returned
        /// <see cref="SimCommandResult.Deferred"/>. The line the answer produces
        /// carries that command's <c>cmd_id</c>, so a deferred command reads in
        /// the log exactly like an immediate one.
        /// <see cref="SimCommandResult.Deferred"/> を返した処理の命令に答える。
        /// 答えが出す行はその命令の <c>cmd_id</c> を持つので、後で答えた命令も
        /// ログの上では即座に答えた命令と同じに読める。
        /// </summary>
        public void AnswerLater(string commandId, SimCommandResult result)
        {
            bool hasNothingToAnswer = string.IsNullOrEmpty(commandId);
            if (hasNothingToAnswer)
            {
                return;
            }

            using (Log.BeginCommand(commandId))
            {
                Log.Write(result.Ok ? LogLevel.Debug : LogLevel.Warn,
                          LogSources.Command, SimCommandRegistry.FinishedEvent,
                          result.Ok ? "the waiting command finished"
                                    : $"the waiting command failed: {result.Error}",
                          LogData.Of("deferred", true));
                RemoteNative.Answer(commandId, result.Ok, result.Data, result.Error);
            }
        }

        /// <summary>
        /// <c>remote.status</c>'s answer: how this page is connected and how its
        /// log is doing.
        /// <c>remote.status</c> の答え。このページの繋がり方と、ログの様子。
        /// </summary>
        private string StatusJson()
        {
            return "{" +
                   "\"mode\":" + LogJson.Quote(mode.ToString().ToLowerInvariant()) + "," +
                   "\"run_id\":" + LogJson.Quote(Log.RunId) + "," +
                   "\"jslib\":" + (RemoteNative.Available ? "true" : "false") + "," +
                   "\"commands\":" + Commands.Count + "," +
                   "\"kept_lines\":" + Log.RingCount +
                   "}";
        }

        private void OnDestroy()
        {
            editorFile?.Dispose();
            editorFile = null;

            if (Instance == this)
            {
                Instance = null;
            }
        }
    }
}
