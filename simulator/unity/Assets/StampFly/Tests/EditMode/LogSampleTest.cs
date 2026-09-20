/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the sample the server checks).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Collections.Generic;
using System.IO;
using System.Text;
using NUnit.Framework;
using StampFly.Core;
using UnityEngine;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// Writes one line of every kind this page produces, so the server's own
    /// checker can be run against them.
    ///
    /// The C# side and the Python side cannot meet in one process: Unity has no
    /// Python, and pytest has no Unity. They meet in a file instead. This test
    /// regenerates <c>tests/fixtures/unity_page_log_sample.jsonl</c> from the
    /// live code and fails when it differs from what is committed;
    /// <c>tests/commands/test_unity_page_log.py</c> then runs that committed
    /// file through <c>jsonl_log.validate_record</c>, which is the check that
    /// matters and which runs in CI without Unity.
    ///
    /// このページが出す各種の行を 1 行ずつ書き出し、サーバ自身の検査を通せる
    /// ようにする。
    ///
    /// C# 側と Python 側は同じ処理の中では出会えない。Unity に Python は無く、
    /// pytest に Unity は無い。代わりにファイルで出会う。この試験が
    /// <c>tests/fixtures/unity_page_log_sample.jsonl</c> を今のコードから作り
    /// 直し、コミットされているものと違えば不合格にする。
    /// <c>tests/commands/test_unity_page_log.py</c> がその committed の
    /// ファイルを <c>jsonl_log.validate_record</c> へ通す。効くのはそちらで、
    /// Unity 無しに CI で回る。
    /// </summary>
    public sealed class LogSampleTest
    {
        /// <summary>
        /// The run the sample belongs to. Fixed rather than issued, so
        /// regenerating the file changes nothing unless the format changed.
        /// 見本が属する実行。発行せず固定する。形式が変わらない限り、作り直し
        /// ても中身が変わらないようにするため。
        /// </summary>
        public const string SampleRunId = "20260920T044500Z-1a2b3c4d";

        /// <summary>The command the sample's lines were raised under. / 見本の命令。</summary>
        public const string SampleCommandId = "c20260920T044500Z-1a2b3c";

        /// <summary>
        /// Where the sample lives, relative to the repository root.
        /// リポジトリ直下から見た見本の置き場。
        /// </summary>
        public const string SampleRelativePath = "tests/fixtures/unity_page_log_sample.jsonl";

        /// <summary>
        /// Keeps the lines, and lets the test write them out.
        /// 行を保持し、試験が書き出せるようにする。
        /// </summary>
        private sealed class CollectingSink : ILogSink
        {
            internal readonly List<string> Lines = new List<string>();

            public void Write(string line)
            {
                Lines.Add(line);
            }
        }

        [Test]
        public void TheCommittedSampleMatchesWhatThisCodeProduces()
        {
            string produced = string.Join("\n", Produce()) + "\n";
            string path = SamplePath();

            Assert.That(File.Exists(path), Is.True,
                        $"the sample is missing: {path}");

            string committed = File.ReadAllText(path).Replace("\r\n", "\n");
            bool differs = committed != produced;
            if (differs)
            {
                // Rewrite before failing, so the fix is `git add` rather than
                // transcribing lines by hand out of an assertion message.
                // 不合格にする前に書き直す。直し方が、判定の本文から行を書き
                // 写すことではなく `git add` になるようにする。
                File.WriteAllText(path, produced, new UTF8Encoding(false));
            }

            Assert.That(differs, Is.False,
                        $"the page's log format changed; {SampleRelativePath} was rewritten. " +
                        "Review the difference and commit it.");
        }

        /// <summary>
        /// One line of each kind: the bridge's, the world's, the simulation's,
        /// the firmware's, and a dropped-lines report. Between them they cover
        /// every required key, both optional-key groups, and the `data` object.
        /// 各種の行を 1 つずつ。橋・空間・シミュレーション・ファームと、失った
        /// 行の報告。合わせて、必須の鍵の全てと、任意の鍵の両方の組と、
        /// `data` を通る。
        /// </summary>
        private static List<string> Produce()
        {
            CollectingSink sink = new CollectingSink();
            StructuredLog log = new StructuredLog(SampleRunId, sink)
            {
                MinimumLevel = LogLevel.Debug,
            };

            log.Write(LogLevel.Info, LogSources.Bridge, "bridge.hello",
                      "served by sf unity serve",
                      LogData.Of("mode", "local", "run_id", SampleRunId));

            using (log.BeginCommand(SampleCommandId))
            {
                log.Write(LogLevel.Debug, LogSources.Command, "cmd.started",
                          "running world.load", LogData.Of("command", "world.load"));

                log.Write(LogLevel.Info, LogSources.World, "world.loaded",
                          "loaded world 'gate_course'",
                          new Dictionary<string, object>
                          {
                              { "world", "gate_course" },
                              { "obstacles", 12 },
                              { "elapsed_ms", 4.5 },
                          });

                log.SetClockSource(() => new SimulationClock
                {
                    SimulationMicroseconds = 1_252_500,
                    Tick = 501,
                    Frame = 30,
                });

                log.Write(LogLevel.Warn, LogSources.Simulation, "sim.step_overrun",
                          "step took longer than the budget",
                          LogData.Of("budget_us", 2500, "actual_us", 3100));

                new FirmwareLogPump(log).Pump(new FirmwareLogRecord
                {
                    Level = FirmwareLogLevels.Info,
                    SimulationMicroseconds = 1_250_000,
                    Tag = "flight_ctrl",
                    Message = "mode changed to ALT_HOLD",
                });
            }

            log.SetClockSource(null);
            log.ReportDropped(3, "the page's send queue overflowed");

            // The timestamps are the one thing that cannot be stable, so they
            // are replaced with a fixed instant: the format is what this sample
            // pins down, not when it was produced.
            // 安定させられない唯一のものが日時なので、決まった時刻に置き換える。
            // この見本が押さえるのは形式であって、作った時刻ではない。
            List<string> stamped = new List<string>();
            foreach (string line in sink.Lines)
            {
                stamped.Add(WithFixedTimestamp(line));
            }

            return stamped;
        }

        /// <summary>
        /// Replace the line's <c>ts</c> with a fixed one.
        /// 行の <c>ts</c> を決まった値に置き換える。
        /// </summary>
        private static string WithFixedTimestamp(string line)
        {
            const string opening = "{\"ts\":\"";
            int start = opening.Length;
            int end = line.IndexOf('"', start);
            return opening + "2026-09-20T04:45:00.123Z" + line.Substring(end);
        }

        /// <summary>
        /// The sample's absolute path, found by walking up from the Unity
        /// project (<c>simulator/unity</c>) to the repository root.
        /// 見本の絶対パス。Unity プロジェクト（<c>simulator/unity</c>）から
        /// リポジトリ直下まで遡って求める。
        /// </summary>
        private static string SamplePath()
        {
            string projectRoot = Directory.GetParent(Application.dataPath).FullName;
            string repositoryRoot = Directory.GetParent(projectRoot).Parent.FullName;
            return Path.Combine(repositoryRoot, SampleRelativePath);
        }
    }
}
