/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — answering a command later).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Core;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// What a deferred result promises: a handler may say "not yet" without
    /// looking like a success, and the registry must not write a finishing
    /// line for a command that has not finished.
    ///
    /// 後で答える結果が約束すること。処理は「まだ」と言え、それが成功に見えない
    /// こと。そして登録簿は、終わっていない命令に終わりの行を書かないこと。
    ///
    /// This is what <c>sim.wait</c> rests on: it runs on the main thread, so
    /// blocking there would stop the very loop whose clock it is waiting for.
    /// <c>sim.wait</c> が拠るのはこれである。主スレッドで走るので、そこで待つと、
    /// 時計を待っている当の輪を止めてしまう。
    /// </summary>
    public sealed class DeferredCommandTest
    {
        [Test]
        public void ADeferredResultIsNeitherASuccessNorAFailure()
        {
            SimCommandResult deferred = SimCommandResult.Deferred();

            Assert.That(deferred.IsDeferred, Is.True);
            Assert.That(deferred.Data, Is.Null,
                        "a deferred result must carry no answer: there is none yet");
            Assert.That(deferred.Error, Is.Null);
        }

        [Test]
        public void AnOrdinaryResultIsNotDeferred()
        {
            Assert.That(SimCommandResult.Success("{}").IsDeferred, Is.False);
            Assert.That(SimCommandResult.Failure("no").IsDeferred, Is.False);
        }

        [Test]
        public void TheRegistryPassesADeferredResultThrough()
        {
            SimCommandRegistry registry = new SimCommandRegistry();
            registry.Register("sim.wait", _ => SimCommandResult.Deferred());

            SimCommandResult result = registry.Execute("sim.wait", SimCommandArgs.Empty);

            Assert.That(result.IsDeferred, Is.True);
        }

        /// <summary>
        /// A command that has not finished must not produce a `cmd.finished`
        /// line, or a log reader would see the flight's longest step end the
        /// moment it began.
        /// 終わっていない命令が `cmd.finished` の行を出してはならない。出すと、
        /// ログを読む人には、飛行の最も長い手順が始まった瞬間に終わったように
        /// 見えてしまう。
        /// </summary>
        [Test]
        public void ADeferredCommandWritesNoFinishedLine()
        {
            var log = new StructuredLog("20260920T000000Z-deadbeef");
            var sink = new RecordingSink();
            log.SetSink(sink);
            log.MinimumLevel = LogLevel.Debug;

            SimCommandRegistry registry = new SimCommandRegistry(log);
            registry.Register("sim.wait", _ => SimCommandResult.Deferred());
            registry.Execute("sim.wait", SimCommandArgs.Empty);

            Assert.That(sink.Lines, Has.None.Contains(SimCommandRegistry.FinishedEvent));
            Assert.That(sink.Lines, Has.Some.Contains(SimCommandRegistry.StartedEvent));
        }

        [Test]
        public void AnOrdinaryCommandStillWritesItsFinishedLine()
        {
            var log = new StructuredLog("20260920T000000Z-deadbeef");
            var sink = new RecordingSink();
            log.SetSink(sink);
            log.MinimumLevel = LogLevel.Debug;

            SimCommandRegistry registry = new SimCommandRegistry(log);
            registry.Register("sim.ping2", _ => SimCommandResult.Success());
            registry.Execute("sim.ping2", SimCommandArgs.Empty);

            Assert.That(sink.Lines, Has.Some.Contains(SimCommandRegistry.FinishedEvent));
        }

        /// <summary>Keeps every finished line, for a test to read. / 出来上がった行を全て保持する。試験が読むため。</summary>
        private sealed class RecordingSink : ILogSink
        {
            internal System.Collections.Generic.List<string> Lines { get; } =
                new System.Collections.Generic.List<string>();

            public void Write(string line) => Lines.Add(line);
        }
    }
}
