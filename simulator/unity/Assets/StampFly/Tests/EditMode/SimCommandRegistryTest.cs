/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the registry's guarantees).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using NUnit.Framework;
using StampFly.Core;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// What the registry guarantees: an unregistered name is answered rather
    /// than thrown, a name cannot be claimed twice, a handler that throws
    /// becomes a readable failure instead of taking the page down, and the
    /// arguments reach the handler with the types it asks for.
    ///
    /// 登録簿が保証すること。登録の無い名前は例外ではなく答えで返ること。同じ
    /// 名前を二度名乗れないこと。例外を投げた処理が、ページを落とさず読める
    /// 失敗になること。引数が、処理の求める型で届くこと。
    /// </summary>
    public sealed class SimCommandRegistryTest
    {
        [Test]
        public void AnUnknownCommandIsRefusedWithItsName()
        {
            SimCommandResult result =
                new SimCommandRegistry().Execute("sim.nope", SimCommandArgs.Empty);

            Assert.That(result.Ok, Is.False);
            Assert.That(result.Error, Does.Contain("sim.nope"));
        }

        [Test]
        public void RegisteringTheSameNameTwiceThrows()
        {
            SimCommandRegistry registry = new SimCommandRegistry();
            registry.Register("sim.pause", _ => SimCommandResult.Success());

            Assert.Throws<InvalidOperationException>(
                () => registry.Register("sim.pause", _ => SimCommandResult.Success()));
        }

        [Test]
        public void AHandlerThatThrowsBecomesAFailureNotAnException()
        {
            SimCommandRegistry registry = new SimCommandRegistry();
            registry.Register("sim.explode",
                              _ => throw new InvalidOperationException("the plant is not built"));

            SimCommandResult result = registry.Execute("sim.explode", SimCommandArgs.Empty);

            Assert.That(result.Ok, Is.False);
            Assert.That(result.Error, Does.Contain("the plant is not built"));
        }

        [Test]
        public void UnregisteringLetsTheNameBeClaimedAgain()
        {
            SimCommandRegistry registry = new SimCommandRegistry();
            registry.Register("world.load", _ => SimCommandResult.Success());

            Assert.That(registry.Unregister("world.load"), Is.True);
            Assert.That(registry.Has("world.load"), Is.False);
            Assert.DoesNotThrow(() => registry.Register("world.load",
                                                        _ => SimCommandResult.Success()));
        }

        [Test]
        public void HelpListsEveryRegisteredCommand()
        {
            SimCommandRegistry registry = new SimCommandRegistry();
            registry.RegisterBuiltIns(new StructuredLog("20260920T044500Z-1a2b3c4d"),
                                      () => "{}");
            registry.Register("world.load", _ => SimCommandResult.Success(), "Load a world");

            SimCommandResult result = registry.Execute("help", SimCommandArgs.Empty);

            Assert.That(result.Ok, Is.True);
            Assert.That(result.Data, Does.Contain("\"name\":\"world.load\""));
            Assert.That(result.Data, Does.Contain("\"name\":\"sim.ping\""));
        }

        [Test]
        public void LogLevelReadsAndSetsTheFloor()
        {
            StructuredLog log = new StructuredLog("20260920T044500Z-1a2b3c4d");
            SimCommandRegistry registry = new SimCommandRegistry();
            registry.RegisterBuiltIns(log, () => "{}");

            SimCommandResult read = registry.Execute("log.level", SimCommandArgs.Empty);
            SimCommandResult set = registry.Execute(
                "log.level", SimCommandArgs.Parse("{\"level\": \"debug\"}"));
            SimCommandResult bad = registry.Execute(
                "log.level", SimCommandArgs.Parse("{\"level\": \"chatty\"}"));

            Assert.That(read.Data, Does.Contain("\"info\""));
            Assert.That(set.Ok, Is.True);
            Assert.That(log.MinimumLevel, Is.EqualTo(LogLevel.Debug));
            Assert.That(bad.Ok, Is.False);
            Assert.That(bad.Error, Does.Contain("chatty"));
        }

        [Test]
        public void ArgumentsAreReadWhetherTheyArriveAsStringsOrAsJson()
        {
            // `sf unity cmd --arg` keeps values as strings, `--json` sends real
            // JSON; both must reach the same handler the same way.
            // `sf unity cmd --arg` は値を文字列のまま送り、`--json` は本物の
            // JSON を送る。どちらも同じ処理へ同じように届かねばならない。
            SimCommandArgs asStrings =
                SimCommandArgs.Parse("{\"name\": \"gate_course\", \"height\": \"1.5\", " +
                                     "\"paused\": \"true\"}");
            SimCommandArgs asJson =
                SimCommandArgs.Parse("{\"name\": \"gate_course\", \"height\": 1.5, " +
                                     "\"paused\": true}");

            foreach (SimCommandArgs args in new[] { asStrings, asJson })
            {
                Assert.That(args.String("name"), Is.EqualTo("gate_course"));
                Assert.That(args.Double("height"), Is.EqualTo(1.5).Within(1e-9));
                Assert.That(args.Bool("paused"), Is.True);
            }
        }

        [Test]
        public void AnAbsentArgumentFallsBackRatherThanThrowing()
        {
            SimCommandArgs args = SimCommandArgs.Parse("{}");

            Assert.That(args.Has("name"), Is.False);
            Assert.That(args.String("name", "empty_room"), Is.EqualTo("empty_room"));
            Assert.That(args.Int("count", 7), Is.EqualTo(7));
            Assert.That(args.Bool("armed", true), Is.True);
        }

        [Test]
        public void EscapesInsideAnArgumentAreResolved()
        {
            SimCommandArgs args =
                SimCommandArgs.Parse("{\"note\": \"a \\\"quoted\\\" name\\nand a line\"}");

            Assert.That(args.String("note"), Is.EqualTo("a \"quoted\" name\nand a line"));
        }

        [Test]
        public void ANestedObjectIsKeptAsItsRawJson()
        {
            SimCommandArgs args =
                SimCommandArgs.Parse("{\"size\": [0.5, 0.2, 0.5], \"type\": \"box\"}");

            Assert.That(args.String("size"), Is.EqualTo("[0.5, 0.2, 0.5]"));
            Assert.That(args.String("type"), Is.EqualTo("box"));
        }

        [Test]
        public void AnUnreadablePayloadYieldsNoArgumentsRatherThanThrowing()
        {
            Assert.That(SimCommandArgs.Parse("not json at all").Count, Is.EqualTo(0));
            Assert.That(SimCommandArgs.Parse(null).Count, Is.EqualTo(0));
        }
    }
}
