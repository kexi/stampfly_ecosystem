/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the page's structured log).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.Collections.Generic;

namespace StampFly.Core
{
    /// <summary>
    /// Where a composed line goes. Separated from <see cref="StructuredLog"/>
    /// so that the same log serves a locally served page (batched to
    /// <c>POST /api/log</c>), the editor's play mode (a file under
    /// <c>simulator/unity/Logs/stampfly/</c>) and the public page (the ring
    /// buffer alone).
    /// 組み立てた行の行き先。同じログが、ローカル配信のページ（まとめて
    /// <c>POST /api/log</c>）・エディタの再生（<c>simulator/unity/Logs/stampfly/</c>
    /// の下のファイル）・公開ページ（輪状バッファだけ）に使えるよう、
    /// <see cref="StructuredLog"/> から分けてある。
    /// </summary>
    public interface ILogSink
    {
        /// <summary>
        /// Take one finished JSON line. Never throws: a sink that cannot
        /// deliver drops the line and says so through its own channel, because
        /// a failing log must not stop the simulation.
        /// 出来上がった JSON の 1 行を受け取る。例外を投げない。届けられない
        /// 出力先は行を捨て、自分の経路でそれを伝える。ログの失敗が飛行を
        /// 止めてはならないため。
        /// </summary>
        void Write(string line);
    }

    /// <summary>
    /// The page's log: it holds the run's identity, tags every line with the
    /// command being handled, supplies the simulated clock, keeps the most
    /// recent lines for <c>window.stampfly.logs()</c>, and hands each finished
    /// line to a sink.
    ///
    /// Not thread safe by design. Everything that logs -- the simulation loop,
    /// the world, the firmware pump, the command handlers -- runs on Unity's
    /// main thread, and WebGL has no other thread to log from. A lock per line
    /// would cost more than it protects.
    ///
    /// ページ側のログ。実行の素性を持ち、処理中の命令を全ての行に付け、仮想
    /// 時刻を供給し、<c>window.stampfly.logs()</c> のために直近の行を保持し、
    /// 出来上がった行を出力先へ渡す。
    ///
    /// スレッド安全にはしない。ログを出すもの（刻みの輪・空間・ファームのログの
    /// 汲み出し・命令の処理）は全て Unity の主スレッドで動き、WebGL には他に
    /// ログを出すスレッドが無い。1 行ごとの錠は守るものより高くつく。
    /// </summary>
    public sealed class StructuredLog : IStructuredLog
    {
        /// <summary>
        /// The event a log emits about itself when a sink lost lines. The count
        /// goes in <c>data.dropped</c>, so a gap in a run's log explains itself
        /// rather than looking like the simulator went quiet.
        /// 出力先が行を失ったときにログ自身が出す事象。数は
        /// <c>data.dropped</c> に入れる。ログの切れ目が自分で理由を語り、
        /// シミュレータが黙ったように見えないようにする。
        /// </summary>
        public const string DroppedEvent = "log.dropped";

        /// <summary>
        /// How many lines the ring keeps by default. Enough for one flight's
        /// worth of events at the rates AGENTS.md allows (no line per control
        /// step), and small enough to hand to JavaScript in one string.
        /// 輪状バッファが既定で保持する行数。AGENTS.md が許す頻度（制御の刻み
        /// ごとの行は出さない）での 1 回の飛行に足り、JavaScript へ 1 つの
        /// 文字列で渡せる程度に小さい。
        /// </summary>
        public const int DefaultRingCapacity = 2000;

        private readonly string[] ring;
        private int ringCount;
        private int ringStart;

        private readonly List<string> commandStack = new List<string>();

        private ILogSink sink;
        private Func<SimulationClock> clockSource;

        /// <summary>
        /// Create a log for one run. <paramref name="runId"/> is the value every
        /// line carries; a page served locally receives it from
        /// <c>GET /api/hello</c>, and a public page issues its own.
        /// 実行 1 回分のログを作る。<paramref name="runId"/> は全ての行が持つ値
        /// で、ローカル配信のページは <c>GET /api/hello</c> から受け取り、公開
        /// ページは自分で発行する。
        /// </summary>
        public StructuredLog(string runId, ILogSink sink = null,
                             int ringCapacity = DefaultRingCapacity)
        {
            RunId = runId ?? string.Empty;
            this.sink = sink;
            ring = new string[Math.Max(1, ringCapacity)];
        }

        /// <summary>The run every line belongs to. / 全ての行が属する実行。</summary>
        public string RunId { get; private set; }

        /// <summary>
        /// The firmware's power-up, put on every line while it is set. The
        /// firmware side sets it when a module is created and clears it when
        /// the module is thrown away (one module is one power-on).
        /// ファームの電源投入。設定されている間、全ての行に付く。ファーム側が
        /// モジュールを作ったときに設定し、捨てたときに消す（モジュール 1 つが
        /// 電源投入 1 回）。
        /// </summary>
        public string BootId { get; set; }

        /// <summary>
        /// The lowest level kept. A line below it is not composed at all, so a
        /// debug line costs nothing while debug is off.
        /// 残す最も低い段。これより下の行は組み立てもしない。debug を切って
        /// いる間、debug の行は何も費やさない。
        /// </summary>
        public LogLevel MinimumLevel { get; set; } = LogLevel.Info;

        /// <summary>
        /// The command whose handling is in progress, or null. Every line
        /// written inside a <see cref="BeginCommand"/> scope carries it.
        /// 処理中の命令。無ければ null。<see cref="BeginCommand"/> の範囲の中で
        /// 書かれた行は全てこれを持つ。
        /// </summary>
        public string CurrentCommandId =>
            commandStack.Count == 0 ? null : commandStack[commandStack.Count - 1];

        /// <summary>
        /// How many lines have been dropped and not yet reported.
        /// 捨てられ、まだ報告していない行の数。
        /// </summary>
        public int PendingDropped { get; private set; }

        /// <summary>
        /// Replace the sink. Lines already in the ring stay there; only where
        /// new lines go changes. The remote bridge calls this once
        /// <c>/api/hello</c> has answered.
        /// 出力先を差し替える。既に輪状バッファにある行はそのまま残り、以後の
        /// 行の行き先だけが変わる。中継の橋は <c>/api/hello</c> が応えてから
        /// これを呼ぶ。
        /// </summary>
        public void SetSink(ILogSink replacement)
        {
            sink = replacement;
        }

        /// <summary>
        /// Adopt the run id the server issued. A page starts with its own id
        /// (it may have to log before <c>/api/hello</c> answers) and switches
        /// to the server's when it arrives; the server rejects a line whose
        /// <c>run_id</c> is not this run's.
        /// サーバが発行した run_id を採用する。ページは自分の id で始まり
        /// （<c>/api/hello</c> が応える前に記録することがある）、届いたら
        /// サーバのものへ切り替える。サーバはこの実行の <c>run_id</c> でない
        /// 行を拒否する。
        /// </summary>
        public void AdoptRunId(string runId)
        {
            if (!string.IsNullOrEmpty(runId))
            {
                RunId = runId;
            }
        }

        /// <summary>
        /// Where <c>sim_us</c>, <c>tick</c> and <c>frame</c> come from. The
        /// simulation loop installs this; until it does, the three keys are
        /// simply absent, which AGENTS.md allows (a correlation key appears
        /// only when it applies).
        /// <c>sim_us</c>・<c>tick</c>・<c>frame</c> の供給元。刻みの輪が差し込む。
        /// 差し込まれるまでこの 3 つの鍵は付かない。AGENTS.md はそれを許す
        /// （相関の鍵は当てはまるときだけ入れる）。
        /// </summary>
        public void SetClockSource(Func<SimulationClock> source)
        {
            clockSource = source;
        }

        /// <summary>
        /// Open a scope in which every line carries <paramref name="commandId"/>.
        /// Dispose closes it. Scopes nest: an inner scope's id wins while it is
        /// open, and the outer one resumes afterwards. Disposing out of order
        /// removes that scope's entry and leaves the others, so a handler that
        /// forgets to dispose cannot leave a stale id on the whole run.
        ///
        /// <paramref name="commandId"/> を全ての行に付ける範囲を開く。Dispose が
        /// 閉じる。入れ子にできる。内側の範囲が開いている間は内側の id が勝ち、
        /// 閉じると外側に戻る。順序を違えて閉じてもその範囲の分だけが消え、
        /// 他は残る。片付け忘れた処理が実行全体に古い id を残せないようにする。
        /// </summary>
        public IDisposable BeginCommand(string commandId)
        {
            if (string.IsNullOrEmpty(commandId))
            {
                return NullScope.Instance;
            }

            commandStack.Add(commandId);
            return new CommandScope(this, commandId);
        }

        /// <summary>
        /// Close one command scope. Removing the last occurrence rather than
        /// the top keeps a mis-ordered dispose from unwinding somebody else's
        /// scope.
        /// 命令の範囲を 1 つ閉じる。先頭ではなく最後に現れる 1 件を消すことで、
        /// 順序を違えた片付けが他人の範囲を巻き戻さないようにする。
        /// </summary>
        private void EndCommand(string commandId)
        {
            int index = commandStack.LastIndexOf(commandId);
            if (index >= 0)
            {
                commandStack.RemoveAt(index);
            }
        }

        /// <inheritdoc />
        public void Write(LogLevel level, string source, string eventName, string message,
                          IReadOnlyDictionary<string, object> data)
        {
            if (level < MinimumLevel)
            {
                return;
            }

            SimulationClock clock = clockSource == null ? default : clockSource();
            LogRecord record = new LogRecord
            {
                Timestamp = LogJson.Now(),
                Level = level,
                Source = source,
                EventName = eventName,
                RunId = RunId,
                Message = message,
                CommandId = CurrentCommandId,
                BootId = BootId,
                SimulationMicroseconds = clock.SimulationMicroseconds,
                Tick = clock.Tick,
                Frame = clock.Frame,
                Data = data,
            };
            Emit(record);
        }

        /// <summary>
        /// Write a line whose correlation keys are already decided. The
        /// firmware pump uses it: a firmware record carries its own
        /// <c>sim_us</c> and <c>tag</c>, which the running clock must not
        /// overwrite.
        /// 相関の鍵が既に決まっている行を書く。ファームのログの汲み出しが使う。
        /// ファームの記録は自分の <c>sim_us</c> と <c>tag</c> を持ち、いまの
        /// 時計がそれを上書きしてはならない。
        /// </summary>
        public void WriteRecord(LogRecord record)
        {
            if (record.Level < MinimumLevel)
            {
                return;
            }

            record.Timestamp = string.IsNullOrEmpty(record.Timestamp)
                ? LogJson.Now() : record.Timestamp;
            record.RunId = RunId;
            record.CommandId = record.CommandId ?? CurrentCommandId;
            record.BootId = record.BootId ?? BootId;
            Emit(record);
        }

        /// <summary>
        /// Compose the line, keep it in the ring and hand it to the sink.
        /// 行を組み立て、輪状バッファに残し、出力先へ渡す。
        /// </summary>
        private void Emit(in LogRecord record)
        {
            string line = LogJson.Compose(record);
            Remember(line);
            sink?.Write(line);
        }

        /// <summary>
        /// Report lines a sink could not deliver, as one line rather than one
        /// per loss. The count is cleared once reported, so the same loss is
        /// never counted twice.
        /// 出力先が届けられなかった行を、1 件ずつではなく 1 行にまとめて報告
        /// する。報告したら数を戻し、同じ損失を二度数えないようにする。
        /// </summary>
        public void ReportDropped(int dropped, string reason)
        {
            PendingDropped += Math.Max(0, dropped);
            if (PendingDropped == 0)
            {
                return;
            }

            int reported = PendingDropped;
            PendingDropped = 0;
            Write(LogLevel.Warn, LogSources.Bridge, DroppedEvent,
                  $"dropped {reported} log line(s): {reason}",
                  new Dictionary<string, object>
                  {
                      { "dropped", reported },
                      { "reason", reason ?? string.Empty },
                  });
        }

        // -- ring buffer / 輪状バッファ --------------------------------------

        /// <summary>
        /// Keep the line, overwriting the oldest once the ring is full. The ring
        /// exists for the public page, which has no server to post to: the
        /// person still needs <c>window.stampfly.logs()</c> and a download.
        /// 行を保持し、いっぱいになったら最も古いものを上書きする。輪状バッファ
        /// は公開ページのためにある。送る先のサーバが無くても、
        /// <c>window.stampfly.logs()</c> とダウンロードで取り出せる必要がある。
        /// </summary>
        private void Remember(string line)
        {
            if (ringCount < ring.Length)
            {
                ring[(ringStart + ringCount) % ring.Length] = line;
                ringCount++;
                return;
            }

            ring[ringStart] = line;
            ringStart = (ringStart + 1) % ring.Length;
        }

        /// <summary>How many lines the ring holds. / 輪状バッファが持つ行数。</summary>
        public int RingCount => ringCount;

        /// <summary>How many lines the ring can hold. / 保持できる行数。</summary>
        public int RingCapacity => ring.Length;

        /// <summary>
        /// The kept lines, oldest first. / 保持している行。古い順。
        /// </summary>
        public IReadOnlyList<string> RecentLines()
        {
            string[] lines = new string[ringCount];
            for (int index = 0; index < ringCount; index++)
            {
                lines[index] = ring[(ringStart + index) % ring.Length];
            }

            return lines;
        }

        /// <summary>
        /// The kept lines as one JSON Lines document, which is the form
        /// <c>window.stampfly.logs()</c> returns and a download saves.
        /// 保持している行を 1 つの JSON Lines にしたもの。
        /// <c>window.stampfly.logs()</c> が返し、ダウンロードが保存する形。
        /// </summary>
        public string RecentAsJsonLines()
        {
            return string.Join("\n", RecentLines());
        }

        // -- scopes / 範囲 ----------------------------------------------------

        /// <summary>
        /// The handle <see cref="BeginCommand"/> returns.
        /// <see cref="BeginCommand"/> が返す取っ手。
        /// </summary>
        private sealed class CommandScope : IDisposable
        {
            private readonly StructuredLog log;
            private readonly string commandId;
            private bool closed;

            internal CommandScope(StructuredLog log, string commandId)
            {
                this.log = log;
                this.commandId = commandId;
            }

            public void Dispose()
            {
                if (closed)
                {
                    return;
                }

                closed = true;
                log.EndCommand(commandId);
            }
        }

        /// <summary>
        /// What an empty command id opens: a scope that changes nothing.
        /// 空の命令の識別子が開くもの。何も変えない範囲。
        /// </summary>
        private sealed class NullScope : IDisposable
        {
            internal static readonly NullScope Instance = new NullScope();

            public void Dispose()
            {
            }
        }
    }

    /// <summary>
    /// The simulated clock a line is stamped with. The simulation loop supplies
    /// it through <see cref="StructuredLog.SetClockSource"/>; a null field means
    /// that key does not apply yet.
    /// 行に押す仮想の時計。刻みの輪が
    /// <see cref="StructuredLog.SetClockSource"/> で供給する。null の項目は
    /// その鍵がまだ当てはまらないことを表す。
    /// </summary>
    public struct SimulationClock
    {
        /// <summary>Virtual time [µs]. / 仮想時刻 [µs]。</summary>
        public long? SimulationMicroseconds;

        /// <summary>The control step. / 制御の刻み。</summary>
        public long? Tick;

        /// <summary>The rendered frame. / 描画のコマ。</summary>
        public long? Frame;
    }
}
