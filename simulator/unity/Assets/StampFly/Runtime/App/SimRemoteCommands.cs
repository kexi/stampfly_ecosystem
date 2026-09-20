/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the loop's command surface).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

#if UNITY_EDITOR || DEVELOPMENT_BUILD || STAMPFLY_REMOTE

using StampFly.Core;
using StampFly.Input;
using StampFly.Native;
using StampFly.Remote;
using StampFly.Sim;
using UnityEngine;

namespace StampFly.App
{
    /// <summary>
    /// Puts the loop's own commands on the shared registry, and feeds the loop's
    /// virtual clock and the firmware's log into the page's structured log.
    ///
    /// 刻みの輪が持つ命令を共有の登録簿に載せ、輪の仮想時計とファームのログを
    /// ページの構造化ログへ流し込む。
    ///
    /// This lives apart from <see cref="SimLoop"/> and carries the same
    /// <c>defineConstraints</c> the relay does, so a distributed build has
    /// neither the relay nor anything that reaches for it, which is what
    /// <c>sf unity build --release</c> checks for (plan §4).
    ///
    /// <see cref="SimLoop"/> とは別にしてあり、中継と同じ <c>defineConstraints</c>
    /// を持つ。よって配布用ビルドには中継も、それに手を伸ばすものも入らない。
    /// <c>sf unity build --release</c> が確かめるのはそこである（計画 §4）。
    ///
    /// ## The commands / 命令
    ///
    /// | Command | What it does |
    /// |---|---|
    /// | `sim.pause` | pause or resume — `paused` (default true) |
    /// | `sim.step` | run N ticks while paused — `ticks` (default 1) |
    /// | `sim.speed` | set the speed, 0.1..4 — `speed` |
    /// | `sim.reset` | put the vehicle back at its spawn, firmware untouched |
    /// | `sim.power_cycle` | a new firmware from INIT |
    /// | `sim.state` | the clock, the firmware state and the sensors, as JSON |
    /// | `rc.set` | override the sticks — `throttle`/`roll`/`pitch`/`yaw`/`arm`/`alt_hold` |
    /// | `rc.release` | hand the sticks back to the keyboard |
    ///
    /// @design simulator/unity/README.md §8 命令の登録のしかた
    /// </summary>
    [RequireComponent(typeof(SimLoop))]
    public sealed class SimRemoteCommands : MonoBehaviour
    {
        private SimLoop simLoop;
        private ScriptedRc scripted;
        private IRcSource keyboardSource;

        private void Awake()
        {
            simLoop = GetComponent<SimLoop>();
            keyboardSource = simLoop.RcSource;
            scripted = new ScriptedRc();
        }

        private void Start()
        {
            RemoteBridge bridge = RemoteBridge.Instance;
            bool hasNoBridge = bridge == null;
            if (hasNoBridge)
            {
                return;
            }

            bridge.Log.SetClockSource(ReadClock);
            simLoop.DrainLog += DrainFirmwareLog;
            simLoop.Booted += bridge.FirmwareLog.ResetForNewBoot;
            RegisterPacingCommands(bridge);
            RegisterStateCommands(bridge);
        }

        /// <summary>
        /// Move every record the firmware has written since the last frame into
        /// the page's log. Draining the ring moves no clock and touches no plant
        /// state, so a page that logs and one that does not fly the same flight.
        /// 前のフレーム以降にファームが書いた記録を、すべてページのログへ移す。
        /// リングを空けても時計は動かずプラントの状態にも触れないので、ログを出す
        /// ページと出さないページは同じ飛行をする。
        /// </summary>
        private void DrainFirmwareLog(IFirmware firmware)
        {
            FirmwareLogPump pump = RemoteBridge.Instance?.FirmwareLog;
            bool cannotPump = pump == null || firmware == null;
            if (cannotPump)
            {
                return;
            }

            while (firmware.TryReadLogRecord(out FirmwareLogRecord record))
            {
                pump.Pump(record);
            }

            pump.NoteDropped(firmware.DroppedLogRecords());
        }

        private void OnDestroy()
        {
            simLoop.DrainLog -= DrainFirmwareLog;

            RemoteBridge bridge = RemoteBridge.Instance;
            bool hasNoBridge = bridge == null;
            if (hasNoBridge)
            {
                return;
            }

            simLoop.Booted -= bridge.FirmwareLog.ResetForNewBoot;

            foreach (string command in new[]
                     {
                         "sim.pause", "sim.step", "sim.speed", "sim.reset",
                         "sim.power_cycle", "sim.state", "rc.set", "rc.release",
                     })
            {
                bridge.Commands.Unregister(command);
            }
        }

        /// <summary>
        /// The clock every log line carries. Read on every line, so it only
        /// reads fields and computes nothing.
        /// ログの 1 行ごとに付く時計。毎行読まれるので、欄を読むだけで計算はしない。
        /// </summary>
        private SimulationClock ReadClock()
        {
            SimClock clock = simLoop.Clock;
            return new SimulationClock
            {
                SimulationMicroseconds = clock.VirtualMicroseconds,
                Tick = clock.TotalTicks,
                Frame = Time.frameCount,
            };
        }

        /// <summary>Pause, single step, speed, reset and power cycle. / 一時停止・コマ送り・倍率・戻す・電源の入れ直し。</summary>
        private void RegisterPacingCommands(RemoteBridge bridge)
        {
            bridge.Commands.Register("sim.pause", args =>
            {
                bool wantsPause = args.Bool("paused", true);
                if (wantsPause) { simLoop.Clock.Pause(); } else { simLoop.Clock.Resume(); }
                return SimCommandResult.Success(
                    $"{{\"paused\":{(simLoop.Clock.IsPaused ? "true" : "false")}}}");
            }, "Pause or resume the simulation");

            bridge.Commands.Register("sim.step", args =>
            {
                int ticks = args.Int("ticks", 1);
                bool isOutOfRange = ticks < 1 || ticks > 4000;
                if (isOutOfRange)
                {
                    return SimCommandResult.Failure("ticks must be between 1 and 4000");
                }
                simLoop.Clock.StepOnce(ticks);
                return SimCommandResult.Success($"{{\"ticks\":{ticks}}}");
            }, "Run N firmware ticks, then hold");

            bridge.Commands.Register("sim.speed", args =>
            {
                simLoop.Clock.SetSpeed((float)args.Double("speed", 1.0));
                return SimCommandResult.Success(
                    $"{{\"speed\":{simLoop.Clock.Speed:F3}}}");
            }, "Set the simulation speed, 0.1 to 4");

            bridge.Commands.Register("sim.reset", _ =>
            {
                simLoop.MoveToSpawn();
                return SimCommandResult.Success();
            }, "Put the vehicle back at its spawn, leaving the firmware running");

            bridge.Commands.Register("sim.power_cycle", _ =>
            {
                simLoop.PowerOn();
                return SimCommandResult.Success(
                    $"{{\"power_cycles\":{simLoop.PowerCycles}}}");
            }, "Discard the firmware and start a new one from INIT");
        }

        /// <summary>Reading the state, and overriding the sticks. / 状態の取得と、スティックの上書き。</summary>
        private void RegisterStateCommands(RemoteBridge bridge)
        {
            bridge.Commands.Register("sim.state",
                _ => SimCommandResult.Success(StateJson()),
                "The clock, the firmware's state and the sensors");

            bridge.Commands.Register("rc.set", args =>
            {
                scripted.Frame = new RcFrame(
                    (ushort)args.Int("throttle", RcScale.Centre),
                    (ushort)args.Int("roll", RcScale.Centre),
                    (ushort)args.Int("pitch", RcScale.Centre),
                    (ushort)args.Int("yaw", RcScale.Centre),
                    Flags(args));
                simLoop.RcSource = scripted;
                return SimCommandResult.Success();
            }, "Override the sticks until rc.release");

            bridge.Commands.Register("rc.release", _ =>
            {
                simLoop.RcSource = keyboardSource;
                return SimCommandResult.Success();
            }, "Hand the sticks back to the keyboard");
        }

        /// <summary>The flag byte an rc.set carries. / rc.set が持つフラグのバイト。</summary>
        private static byte Flags(SimCommandArgs args)
        {
            byte flags = 0;
            if (args.Bool("arm", false)) { flags |= SfuAbi.FlagArm; }
            if (args.Bool("alt_hold", false)) { flags |= SfuAbi.FlagAltitudeMode; }
            return flags;
        }

        /// <summary>
        /// The whole state as one JSON object, built by hand because
        /// <c>JsonUtility</c> cannot write the shapes this needs.
        /// 状態の全体を JSON の物体 1 つにする。手で組み立てるのは、ここで要る形を
        /// <c>JsonUtility</c> が書けないためである。
        /// </summary>
        private string StateJson()
        {
            SfuStepOut state = simLoop.LastResult;
            SimClock clock = simLoop.Clock;
            Vector3 place = simLoop.transform.position;
            IFirmware firmware = simLoop.Firmware;

            return "{" +
                $"\"firmware\":{LogJson.Quote(firmware == null ? "none" : firmware.Status.ToString())}," +
                $"\"sim_us\":{clock.VirtualMicroseconds}," +
                $"\"ticks\":{clock.TotalTicks}," +
                $"\"paused\":{(clock.IsPaused ? "true" : "false")}," +
                $"\"speed\":{clock.Speed:F3}," +
                $"\"real_time_ratio\":{clock.RealTimeRatio:F4}," +
                $"\"us_per_tick\":{clock.MicrosecondsPerTick:F2}," +
                $"\"fps\":{clock.FramesPerSecond:F1}," +
                $"\"flight_state\":{LogJson.Quote(FlightStateNames.State(state.FlightState))}," +
                $"\"flight_mode\":{LogJson.Quote(FlightStateNames.Mode(state.FlightMode))}," +
                $"\"armed\":{(state.Armed != 0 ? "true" : "false")}," +
                $"\"battery_v\":{state.BatteryVoltage:F3}," +
                $"\"position\":[{place.x:F4},{place.y:F4},{place.z:F4}]," +
                $"\"range_down_m\":{simLoop.LastRange.Distance:F4}," +
                $"\"range_down_valid\":{(simLoop.LastRange.IsValid ? "true" : "false")}," +
                $"\"flow_quality\":{simLoop.LastRange.FlowQuality:F3}," +
                $"\"power_cycles\":{simLoop.PowerCycles}" +
                "}";
        }

        /// <summary>
        /// Sticks a command set, held until the next command changes them.
        /// This is what lets a browser check fly without synthesising key events.
        /// 命令が決めたスティックの値を、次の命令が変えるまで保つ。ブラウザでの
        /// 検証が、キーの事象を作らずに飛ばせるのはこれのおかげである。
        /// </summary>
        private sealed class ScriptedRc : IRcSource
        {
            public RcFrame Frame = RcFrame.Centred;

            public RcFrame Read() => Frame;
        }
    }
}

#endif
