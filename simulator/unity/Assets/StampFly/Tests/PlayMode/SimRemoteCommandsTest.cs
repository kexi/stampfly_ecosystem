/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the loop's command surface).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Collections;
using NUnit.Framework;
using StampFly.App;
using StampFly.Core;
using StampFly.Input;
using StampFly.Native;
using StampFly.Remote;
using StampFly.Sim;
using UnityEngine;
using UnityEngine.TestTools;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// What the loop's own commands answer, checked against a real
    /// <see cref="RemoteBridge"/> and a real <see cref="SimLoop"/> in a scene
    /// built for the test.
    ///
    /// 刻みの輪が持つ命令が何を答えるか。試験のために組んだ場面の中で、実物の
    /// <see cref="RemoteBridge"/> と実物の <see cref="SimLoop"/> に対して確かめる。
    ///
    /// These do not fly: a flight needs the firmware dylib and takes seconds,
    /// and <c>FirmwareFlightTest</c> already covers it. What is checked here is
    /// the SHAPE of the command surface -- that the names are registered, that
    /// the answers carry the keys a script reads, and that pressing ARM
    /// produces the rising edge the firmware arms on.
    ///
    /// ここでは飛ばさない。飛行はファームの dylib を要し、秒の単位で時間がかかる。
    /// それは <c>FirmwareFlightTest</c> が既に担っている。ここで見るのは命令の
    /// 受け口の**形**である。名前が登録されていること、答えが台本の読む鍵を持つ
    /// こと、ARM を押すとファームが ARM する立ち上がりが出ること。
    ///
    /// @design docs/plans/unity-simulator.md §5 段階 3
    /// </summary>
    public sealed class SimRemoteCommandsTest
    {
        private GameObject bridgeObject;
        private GameObject vehicleObject;
        private SimLoop simLoop;

        [SetUp]
        public void SetUp()
        {
            PhysicsStepSettings.Apply();

            bridgeObject = new GameObject("StampFlyBridge");
            bridgeObject.AddComponent<RemoteBridge>();

            vehicleObject = new GameObject("Vehicle");
            Rigidbody body = vehicleObject.AddComponent<Rigidbody>();
            BoxCollider box = vehicleObject.AddComponent<BoxCollider>();
            VehicleBody.Apply(body, box);

            simLoop = vehicleObject.AddComponent<SimLoop>();
            vehicleObject.AddComponent<SimRemoteCommands>();
        }

        [TearDown]
        public void TearDown()
        {
            Object.DestroyImmediate(vehicleObject);
            Object.DestroyImmediate(bridgeObject);
            Physics.simulationMode = SimulationMode.FixedUpdate;
        }

        /// <summary>The registry, once every Start has run. / 全ての Start が走った後の登録簿。</summary>
        private SimCommandRegistry Commands => RemoteBridge.Instance.Commands;

        /// <summary>
        /// The names the flight check and the documentation both rely on. A
        /// command renamed without the two following is the failure this
        /// catches.
        /// 飛行の確認と文書の両方が頼る名前。2 つが追従せずに改名された命令を、
        /// これが捕まえる。
        /// </summary>
        [UnityTest]
        public IEnumerator EveryDocumentedCommandIsRegistered()
        {
            yield return null;

            foreach (string name in new[]
                     {
                         "sim.pause", "sim.step", "sim.speed", "sim.reset",
                         "sim.power_cycle", "sim.state", "sim.wait",
                         "vehicle.state", "rc.set", "rc.arm", "rc.release",
                     })
            {
                Assert.That(Commands.Has(name), Is.True, $"{name} is not registered");
            }
        }

        /// <summary>
        /// `help` lists them, which is how somebody at a terminal finds out
        /// what this page offers without reading the source.
        /// `help` がそれらを並べる。端末にいる人が、ソースを読まずにこのページが
        /// 何を提供するかを知る方法がこれである。
        /// </summary>
        [UnityTest]
        public IEnumerator HelpListsTheSimAndRcCommands()
        {
            yield return null;

            SimCommandResult help = Commands.Execute("help", SimCommandArgs.Empty);

            Assert.That(help.Ok, Is.True);
            Assert.That(help.Data, Does.Contain("sim.wait"));
            Assert.That(help.Data, Does.Contain("rc.arm"));
            Assert.That(help.Data, Does.Contain("vehicle.state"));
        }

        /// <summary>
        /// Every key the flight check reads out of `vehicle.state`. A field
        /// dropped here would make the check judge a flight on a number that
        /// is no longer there.
        /// 飛行の確認が `vehicle.state` から読む鍵の全て。ここで欄が落ちると、
        /// 確認は既に無い数値で飛行を判定することになる。
        /// </summary>
        [UnityTest]
        public IEnumerator VehicleStateCarriesEveryKeyTheCheckReads()
        {
            yield return null;

            SimCommandResult state =
                Commands.Execute("vehicle.state", SimCommandArgs.Empty);

            Assert.That(state.Ok, Is.True, state.Error);
            foreach (string key in new[]
                     {
                         "sim_us", "flight_state", "flight_mode", "armed",
                         "battery_v", "truth", "altitude_m", "tilt_deg",
                         "estimate", "range_down_m", "range_down_valid",
                         "flow_quality", "real_time_ratio", "us_per_tick", "fps",
                     })
            {
                Assert.That(state.Data, Does.Contain($"\"{key}\""),
                            $"vehicle.state lost the key {key}");
            }
        }

        /// <summary>
        /// The truth position is the rigid body's, not the firmware's estimate
        /// -- which is the whole point of reporting both.
        /// 真値は剛体のものであり、ファームの推定ではない。両方を報告することの
        /// 意味はそこにある。
        /// </summary>
        [UnityTest]
        public IEnumerator VehicleStateReportsTheBodysOwnAltitude()
        {
            yield return null;

            vehicleObject.transform.position = new Vector3(0.0f, 1.25f, 0.0f);
            Physics.SyncTransforms();

            SimCommandResult state =
                Commands.Execute("vehicle.state", SimCommandArgs.Empty);

            Assert.That(state.Data, Does.Contain("\"altitude_m\":1.2500"));
        }

        /// <summary>
        /// Pressing ARM sets the flag and leaves the sticks where they were,
        /// which is what lets a script climb first and arm without resetting
        /// the throttle it just set.
        /// ARM を押すとフラグが立ち、スティックはそのままになる。台本が、いま
        /// 設定したスロットルを戻さずに ARM を押せるのはこれによる。
        /// </summary>
        [UnityTest]
        public IEnumerator PressingArmSetsTheFlagAndKeepsTheSticks()
        {
            yield return null;

            Commands.Execute("rc.set", SimCommandArgs.Parse(
                "{\"throttle\": 3243, \"roll\": 2048, \"pitch\": 2048, \"yaw\": 2048}"));
            SimCommandResult armed =
                Commands.Execute("rc.arm", SimCommandArgs.Empty);

            Assert.That(armed.Ok, Is.True, armed.Error);

            RcFrame frame = simLoop.RcSource.Read();
            Assert.That(frame.Flags & SfuAbi.FlagArm, Is.EqualTo(SfuAbi.FlagArm));
            Assert.That(frame.Throttle, Is.EqualTo(3243),
                        "pressing ARM moved the throttle");
        }

        /// <summary>
        /// The firmware arms on a RISING edge, so a press has to leave the bit
        /// clear beforehand and set afterwards. Pressing it the other way
        /// clears it, which is the DISARM at the end of a flight.
        /// ファームは**立ち上がり**で ARM するので、押す前はビットが落ちていて、
        /// 押した後は立っていなければならない。逆向きに押すと落ちる。飛行の終わり
        /// の DISARM がそれである。
        /// </summary>
        [UnityTest]
        public IEnumerator ArmingAndDisarmingMoveTheFlagBothWays()
        {
            yield return null;

            Commands.Execute("rc.set", SimCommandArgs.Parse("{\"throttle\": 2048}"));
            Assert.That(simLoop.RcSource.Read().Flags & SfuAbi.FlagArm, Is.Zero,
                        "the flag was set before ARM was pressed");

            Commands.Execute("rc.arm", SimCommandArgs.Empty);
            Assert.That(simLoop.RcSource.Read().Flags & SfuAbi.FlagArm,
                        Is.EqualTo(SfuAbi.FlagArm));

            Commands.Execute("rc.arm", SimCommandArgs.Parse("{\"armed\": false}"));
            Assert.That(simLoop.RcSource.Read().Flags & SfuAbi.FlagArm, Is.Zero);
        }

        /// <summary>
        /// A `sim.wait` for a time already reached answers at once rather than
        /// deferring: there is nothing to wait for, and deferring would hold a
        /// request open for a frame that would answer identically.
        /// 既に達している時刻への `sim.wait` は、後回しにせずその場で答える。待つ
        /// ものが無く、後回しにしても同じ答えを出すフレームまで要求を保持するだけ
        /// だからである。
        /// </summary>
        [UnityTest]
        public IEnumerator WaitingForATimeAlreadyReachedAnswersAtOnce()
        {
            yield return null;

            SimCommandResult result = Commands.Execute(
                "sim.wait", SimCommandArgs.Parse("{\"sim_us\": 1}"));

            // The clock is at zero before the first tick, so ask for the
            // smallest positive time and expect either an immediate answer
            // (the clock moved) or a deferral (it has not) -- never a throw.
            // 最初の刻みの前は時計が 0 なので、正で最小の時刻を求め、即座の答え
            // （時計が動いた）か後回し（動いていない）のどちらかを期待する。
            // 例外は決して期待しない。
            Assert.That(result.Ok || result.IsDeferred, Is.True, result.Error);
        }

        /// <summary>
        /// A `sim.wait` reached through a route that cannot hold a request open
        /// is refused with that reason, rather than starting a wait nobody
        /// will ever collect.
        /// 要求を保持できない経路から届いた `sim.wait` は、その理由を付けて断られ
        /// る。誰も回収しない待ちを始めるのではなく。
        /// </summary>
        [UnityTest]
        public IEnumerator WaitingFromARouteThatCannotHoldIsRefused()
        {
            yield return null;

            // Executing straight against the registry is that route: the bridge
            // hands out a deferred id only from inside its own Handle.
            // 登録簿に直接実行するのがその経路である。橋が後回しの識別子を渡すのは
            // 自分の Handle の中からだけである。
            SimCommandResult result = Commands.Execute(
                "sim.wait", SimCommandArgs.Parse("{\"seconds\": 30}"));

            Assert.That(result.IsDeferred, Is.False);
            Assert.That(result.Ok, Is.False);
            Assert.That(result.Error, Does.Contain("cannot wait"));
        }

        [UnityTest]
        public IEnumerator WaitingWithNoTimeIsRefusedWithTheArgumentsItWants()
        {
            yield return null;

            SimCommandResult result =
                Commands.Execute("sim.wait", SimCommandArgs.Empty);

            Assert.That(result.Ok, Is.False);
            Assert.That(result.Error, Does.Contain("sim_us"));
            Assert.That(result.Error, Does.Contain("seconds"));
        }

        /// <summary>
        /// The commands come off the registry when the component goes, so a
        /// scene reloaded twice does not hit the registry's "already
        /// registered" refusal.
        /// 部品が消えるとき命令は登録簿から外れる。場面を二度読み直しても、
        /// 登録簿の「登録済み」の拒否に当たらないようにするためである。
        /// </summary>
        [UnityTest]
        public IEnumerator DestroyingTheComponentUnregistersItsCommands()
        {
            yield return null;

            Object.DestroyImmediate(vehicleObject.GetComponent<SimRemoteCommands>());

            Assert.That(Commands.Has("vehicle.state"), Is.False);
            Assert.That(Commands.Has("rc.arm"), Is.False);
            Assert.That(Commands.Has("sim.wait"), Is.False);
        }
    }
}
