/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the page log's guarantees).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Collections.Generic;
using NUnit.Framework;
using StampFly.Core;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// What the page's log guarantees: every line carries the run, a line
    /// written inside a command's scope carries that command and a line outside
    /// one does not, the JSON is escaped so one message cannot break the file,
    /// and the ring keeps the newest lines when it overflows.
    ///
    /// ページ側のログが保証すること。全ての行が実行を持ち、命令の範囲の中で
    /// 書いた行はその命令を持ち、外の行は持たないこと。JSON がエスケープされ、
    /// 本文 1 つでファイルが壊れないこと。輪状バッファが溢れたとき新しい行が
    /// 残ること。
    /// </summary>
    public sealed class StructuredLogTest
    {
        private const string RunId = "20260920T044500Z-1a2b3c4d";
        private const string CommandId = "c20260920T044500Z-1a2b3c";

        /// <summary>
        /// Keeps the lines a log produced, so a test can read them back.
        /// ログが出した行を保持し、試験が読み返せるようにする。
        /// </summary>
        private sealed class CollectingSink : ILogSink
        {
            internal readonly List<string> Lines = new List<string>();

            public void Write(string line)
            {
                Lines.Add(line);
            }
        }

        private static StructuredLog NewLog(CollectingSink sink, int capacity = 16)
        {
            return new StructuredLog(RunId, sink, capacity) { MinimumLevel = LogLevel.Debug };
        }

        [Test]
        public void EveryLineCarriesTheRequiredKeys()
        {
            CollectingSink sink = new CollectingSink();
            NewLog(sink).Write(LogLevel.Info, LogSources.Simulation, "sim.started",
                               "the loop began", null);

            string line = sink.Lines[0];

            Assert.That(line, Does.Contain("\"ts\":\""));
            Assert.That(line, Does.Contain("\"level\":\"info\""));
            Assert.That(line, Does.Contain("\"src\":\"sim\""));
            Assert.That(line, Does.Contain("\"event\":\"sim.started\""));
            Assert.That(line, Does.Contain("\"run_id\":\"" + RunId + "\""));
            Assert.That(line, Does.Contain("\"msg\":\"the loop began\""));
        }

        [Test]
        public void ALineInsideACommandScopeCarriesItAndOneOutsideDoesNot()
        {
            CollectingSink sink = new CollectingSink();
            StructuredLog log = NewLog(sink);

            using (log.BeginCommand(CommandId))
            {
                log.Write(LogLevel.Info, LogSources.World, "world.loaded", "inside", null);
            }

            log.Write(LogLevel.Info, LogSources.World, "world.cleared", "outside", null);

            Assert.That(sink.Lines[0], Does.Contain("\"cmd_id\":\"" + CommandId + "\""));
            Assert.That(sink.Lines[1], Does.Not.Contain("cmd_id"));
        }

        [Test]
        public void NestedScopesUseTheInnermostAndRestoreTheOuterOne()
        {
            const string inner = "c20260920T044501Z-9f8e7d";
            CollectingSink sink = new CollectingSink();
            StructuredLog log = NewLog(sink);

            using (log.BeginCommand(CommandId))
            {
                using (log.BeginCommand(inner))
                {
                    log.Write(LogLevel.Info, LogSources.Command, "cmd.started", "inner", null);
                }

                log.Write(LogLevel.Info, LogSources.Command, "cmd.finished", "outer", null);
            }

            Assert.That(sink.Lines[0], Does.Contain(inner));
            Assert.That(sink.Lines[1], Does.Contain(CommandId));
            Assert.That(log.CurrentCommandId, Is.Null,
                        "the scope must not leak past its using block");
        }

        [Test]
        public void ADisposeOutOfOrderRemovesOnlyThatScope()
        {
            const string inner = "c20260920T044501Z-9f8e7d";
            CollectingSink sink = new CollectingSink();
            StructuredLog log = NewLog(sink);

            System.IDisposable outer = log.BeginCommand(CommandId);
            System.IDisposable nested = log.BeginCommand(inner);

            outer.Dispose();
            log.Write(LogLevel.Info, LogSources.Command, "cmd.started", "still inner", null);
            nested.Dispose();

            Assert.That(sink.Lines[0], Does.Contain(inner));
            Assert.That(log.CurrentCommandId, Is.Null);
        }

        [Test]
        public void QuotesBackslashesAndNewlinesAreEscaped()
        {
            CollectingSink sink = new CollectingSink();
            NewLog(sink).Write(LogLevel.Warn, LogSources.Ui, "ui.note",
                               "a \"quoted\" back\\slash\nand a newline", null);

            string line = sink.Lines[0];

            Assert.That(line, Does.Contain("\\\"quoted\\\""));
            Assert.That(line, Does.Contain("back\\\\slash"));
            Assert.That(line, Does.Contain("\\n"));
            Assert.That(line, Does.Not.Contain("\n"), "a line must stay one line");
        }

        [Test]
        public void ControlCharactersBecomeUnicodeEscapes()
        {
            CollectingSink sink = new CollectingSink();
            NewLog(sink).Write(LogLevel.Info, LogSources.Ui, "ui.note", "bell\u0007here", null);

            Assert.That(sink.Lines[0], Does.Contain("\\u0007"));
        }

        [Test]
        public void NumbersAndBooleansInDataAreUnquoted()
        {
            CollectingSink sink = new CollectingSink();
            NewLog(sink).Write(LogLevel.Info, LogSources.Simulation, "sim.step_overrun",
                               "over budget",
                               new Dictionary<string, object>
                               {
                                   { "budget_us", 2500 },
                                   { "armed", true },
                                   { "label", "tick" },
                               });

            string line = sink.Lines[0];

            Assert.That(line, Does.Contain("\"budget_us\":2500"));
            Assert.That(line, Does.Contain("\"armed\":true"));
            Assert.That(line, Does.Contain("\"label\":\"tick\""));
        }

        [Test]
        public void NotANumberIsQuotedSoTheLineStaysReadable()
        {
            CollectingSink sink = new CollectingSink();
            NewLog(sink).Write(LogLevel.Warn, LogSources.Simulation, "sim.sensor",
                               "bad reading",
                               new Dictionary<string, object> { { "altitude_m", double.NaN } });

            Assert.That(sink.Lines[0], Does.Contain("\"altitude_m\":\"NaN\""));
        }

        [Test]
        public void ALineBelowTheLevelFloorIsNotWritten()
        {
            CollectingSink sink = new CollectingSink();
            StructuredLog log = NewLog(sink);
            log.MinimumLevel = LogLevel.Warn;

            log.Write(LogLevel.Info, LogSources.Simulation, "sim.tick", "quiet", null);
            log.Write(LogLevel.Error, LogSources.Simulation, "sim.fault", "loud", null);

            Assert.That(sink.Lines.Count, Is.EqualTo(1));
            Assert.That(sink.Lines[0], Does.Contain("loud"));
        }

        [Test]
        public void TheRingKeepsTheNewestLinesWhenItOverflows()
        {
            const int capacity = 4;
            CollectingSink sink = new CollectingSink();
            StructuredLog log = NewLog(sink, capacity);

            for (int index = 0; index < capacity + 3; index++)
            {
                log.Write(LogLevel.Info, LogSources.Simulation, "sim.note",
                          "line " + index, null);
            }

            IReadOnlyList<string> kept = log.RecentLines();

            Assert.That(kept.Count, Is.EqualTo(capacity));
            Assert.That(kept[0], Does.Contain("line 3"));
            Assert.That(kept[capacity - 1], Does.Contain("line 6"));
        }

        [Test]
        public void TheClockSourceSuppliesSimulatedTimeWhenOneIsInstalled()
        {
            CollectingSink sink = new CollectingSink();
            StructuredLog log = NewLog(sink);

            log.Write(LogLevel.Info, LogSources.Simulation, "sim.before", "no clock", null);
            log.SetClockSource(() => new SimulationClock
            {
                SimulationMicroseconds = 1_250_000,
                Tick = 500,
                Frame = 30,
            });
            log.Write(LogLevel.Info, LogSources.Simulation, "sim.after", "with clock", null);

            Assert.That(sink.Lines[0], Does.Not.Contain("sim_us"));
            Assert.That(sink.Lines[1], Does.Contain("\"sim_us\":1250000"));
            Assert.That(sink.Lines[1], Does.Contain("\"tick\":500"));
            Assert.That(sink.Lines[1], Does.Contain("\"frame\":30"));
        }

        [Test]
        public void AdoptingTheServersRunIdChangesLaterLines()
        {
            const string serverRun = "20260920T050000Z-aabbccdd";
            CollectingSink sink = new CollectingSink();
            StructuredLog log = NewLog(sink);

            log.Write(LogLevel.Info, LogSources.Bridge, "bridge.ready", "before", null);
            log.AdoptRunId(serverRun);
            log.Write(LogLevel.Info, LogSources.Bridge, "bridge.hello", "after", null);

            Assert.That(sink.Lines[0], Does.Contain(RunId));
            Assert.That(sink.Lines[1], Does.Contain(serverRun));
        }

        [Test]
        public void DroppedLinesAreReportedOnceWithTheirCount()
        {
            CollectingSink sink = new CollectingSink();
            StructuredLog log = NewLog(sink);

            log.ReportDropped(7, "the page's send queue overflowed");
            log.ReportDropped(0, "nothing more");

            Assert.That(sink.Lines.Count, Is.EqualTo(1));
            Assert.That(sink.Lines[0], Does.Contain("\"event\":\"log.dropped\""));
            Assert.That(sink.Lines[0], Does.Contain("\"dropped\":7"));
        }

        [Test]
        public void AFirmwareRecordKeepsItsOwnTimeAndTag()
        {
            CollectingSink sink = new CollectingSink();
            StructuredLog log = NewLog(sink);
            log.SetClockSource(() => new SimulationClock { SimulationMicroseconds = 9_000_000 });

            FirmwareLogPump pump = new FirmwareLogPump(log);
            using (log.BeginCommand(CommandId))
            {
                pump.Pump(new FirmwareLogRecord
                {
                    Level = FirmwareLogLevels.Warn,
                    SimulationMicroseconds = 1_250_000,
                    Tag = "flight_ctrl",
                    Message = "mode changed to ALT_HOLD",
                });
            }

            string line = sink.Lines[0];

            Assert.That(line, Does.Contain("\"src\":\"fw\""));
            Assert.That(line, Does.Contain("\"event\":\"fw.log\""));
            Assert.That(line, Does.Contain("\"level\":\"warn\""));
            Assert.That(line, Does.Contain("\"tag\":\"flight_ctrl\""));
            Assert.That(line, Does.Contain("\"sim_us\":1250000"),
                        "the record's own virtual time wins over the running clock");
            Assert.That(line, Does.Contain(CommandId));
        }

        [Test]
        public void TheFirmwaresOwnLossesAreReportedAsIncrements()
        {
            CollectingSink sink = new CollectingSink();
            FirmwareLogPump pump = new FirmwareLogPump(NewLog(sink));

            pump.NoteDropped(3);
            pump.NoteDropped(3);
            pump.NoteDropped(5);

            Assert.That(sink.Lines.Count, Is.EqualTo(2));
            Assert.That(sink.Lines[0], Does.Contain("\"dropped\":3"));
            Assert.That(sink.Lines[1], Does.Contain("\"dropped\":2"));
        }

        [Test]
        [TestCase(FirmwareLogLevels.Error, LogLevel.Error)]
        [TestCase(FirmwareLogLevels.Warn, LogLevel.Warn)]
        [TestCase(FirmwareLogLevels.Info, LogLevel.Info)]
        [TestCase(FirmwareLogLevels.Debug, LogLevel.Debug)]
        [TestCase(FirmwareLogLevels.Verbose, LogLevel.Debug)]
        public void FirmwareLevelsMapOntoTheFourLevels(int firmwareLevel, LogLevel expected)
        {
            Assert.That(FirmwareLogLevels.ToLogLevel(firmwareLevel), Is.EqualTo(expected));
        }

        [Test]
        public void IssuedIdentifiersHaveTheShapeTheServerChecks()
        {
            string runId = RunIdentifiers.NewRunId();
            string commandId = RunIdentifiers.NewCommandId();

            Assert.That(RunIdentifiers.IsRunId(runId), Is.True, runId);
            Assert.That(RunIdentifiers.IsCommandId(commandId), Is.True, commandId);
            Assert.That(RunIdentifiers.IsRunId(commandId), Is.False,
                        "a command id must never read as a run id");
            Assert.That(RunIdentifiers.IsCommandId("nope"), Is.False);
        }
    }
}
