/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the loop's command surface).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

#if UNITY_EDITOR || DEVELOPMENT_BUILD || STAMPFLY_REMOTE

using System.Collections.Generic;
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
    /// | `sim.wait` | hold until a virtual time arrives, then answer (`sim_us`/`seconds`, `timeout_s`) |
    /// | `vehicle.state` | truth, estimate, flight state, battery, ToF and the pacing figures |
    /// | `rc.set` | override the sticks — `throttle`/`roll`/`pitch`/`yaw`/`arm`/`alt_hold` |
    /// | `rc.arm` | one press of the transmitter's ARM button (a rising edge) |
    /// | `rc.release` | hand the sticks back to the keyboard |
    ///
    /// ## The lines it writes / 書く行
    ///
    /// | `event` | When |
    /// |---|---|
    /// | `fw.boot` | the firmware came up, once per power cycle |
    /// | `sim.flight_state` | the firmware's flight state or mode changed |
    /// | `sim.stats` | once a second: real-time ratio, µs per tick, frames per second |
    /// | `sim.step_overrun` | a frame hit the per-frame tick ceiling |
    ///
    /// No line is written per tick (`AGENTS.md` "Logs": no per-control-step
    /// line). A transition is an event, and the pacing figures are a once-a-
    /// second summary.
    /// 刻みごとの行は出さない（`AGENTS.md`「Logs」の「制御の刻みごとの行は出さ
    /// ない」）。遷移は事象、速さの数値は 1 秒ごとの要約である。
    ///
    /// @design simulator/unity/README.md §8 命令の登録のしかた
    /// </summary>
    [RequireComponent(typeof(SimLoop))]
    public sealed class SimRemoteCommands : MonoBehaviour
    {
        /// <summary>The firmware came up. / ファームが起きた。</summary>
        public const string BootEvent = "fw.boot";

        /// <summary>The flight state or mode changed. / 飛行状態かモードが変わった。</summary>
        public const string FlightStateEvent = "sim.flight_state";

        /// <summary>The once-a-second pacing summary. / 1 秒ごとの速さの要約。</summary>
        public const string StatsEvent = "sim.stats";

        /// <summary>A frame hit the per-frame tick ceiling. / フレームが刻みの上限に当たった。</summary>
        public const string OverrunEvent = "sim.step_overrun";

        /// <summary>
        /// How long one press of the ARM button lasts, in simulated seconds.
        /// The firmware arms on the RISING edge of the flag, so the bit has to
        /// go up and come back down; the real transmitter's button does the same
        /// and the flag stays set afterwards only because the pilot's stick
        /// frame carries it. Long enough for the firmware's 400 Hz loop to see
        /// several frames with it set.
        /// ARM ボタンを 1 回押している長さ。シミュレーションの秒で表す。ファームは
        /// フラグの**立ち上がり**で ARM するので、ビットは上がって下がる必要がある。
        /// 実機の送信機のボタンも同じで、以後フラグが立ったままなのは操縦者の
        /// スティックのフレームがそれを運ぶからにすぎない。ファームの 400 Hz の
        /// 輪が、立った状態のフレームを数回見られるだけの長さにしてある。
        /// </summary>
        public const double ArmPressSeconds = 0.20;

        /// <summary>
        /// The longest a <c>sim.wait</c> may hold, in real seconds. Below the
        /// server's own 70 s connection window, so a page that is waiting is
        /// still polling and still counts as connected.
        /// <c>sim.wait</c> が待てる最長。実時間の秒で表す。サーバ自身の 70 秒の
        /// 接続の窓より短くしてあり、待っているページは待ち受けを続けて接続中の
        /// ままである。
        /// </summary>
        public const double MaxWaitSeconds = 60.0;

        /// <summary>
        /// How long a stick command's consequences are still counted as its
        /// own, in simulated seconds. The firmware receives sticks on its own
        /// 50 Hz cadence and its state machine settles over a few control
        /// periods, so a quarter of a second covers the lines an ARM or a
        /// throttle change produces without reaching into the next command's.
        /// スティックの命令の結果を、まだその命令のものと数える長さ。シミュレー
        /// ションの秒で表す。ファームはスティックを自分の 50 Hz の周期で受け取り、
        /// その状態機械は数制御周期をかけて落ち着く。0.25 秒あれば、ARM やスロットル
        /// の変更が生む行は覆え、次の命令のものまで手を伸ばすことはない。
        /// </summary>
        public const double StickEffectSeconds = 0.25;

        private SimLoop simLoop;
        private ScriptedRc scripted;
        private IRcSource keyboardSource;

        // What the last frame saw, so a change can be told from a repeat.
        // 直前のフレームが見たもの。変化と同じ値を見分けるため。
        private int lastFlightState = -1;
        private int lastFlightMode = -1;
        private bool lastArmed;
        private bool wasBehind;
        private double nextStatsAtRealSeconds;

        // The one sim.wait in flight, if any.
        // 途中にある sim.wait が 1 つあれば、それ。
        private PendingWait pendingWait;

        // The command whose consequences the firmware is still producing, and
        // the virtual time that claim runs out at.
        // ファームがまだ結果を出し続けている命令と、その主張が切れる仮想時刻。
        private string attributedCommandId;
        private long attributedUntilMicroseconds;

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
            simLoop.Booted += NoteBoot;
            RegisterPacingCommands(bridge);
            RegisterStateCommands(bridge);
        }

        /// <summary>
        /// Watch for the things worth a line, and finish a waiting
        /// <c>sim.wait</c>. Runs after <see cref="SimLoop"/>'s own
        /// <c>Update</c> in the default order only by luck, so nothing here
        /// depends on the order: every check reads a value and compares it to
        /// what it saw last.
        /// 行に値することが起きていないか見て、待っている <c>sim.wait</c> を
        /// 終わらせる。既定の順で <see cref="SimLoop"/> の <c>Update</c> の後に
        /// なるのは偶然なので、ここでは順に頼らない。どの検査も値を読んで前回
        /// 見たものと比べるだけである。
        /// </summary>
        private void Update()
        {
            RemoteBridge bridge = RemoteBridge.Instance;
            bool hasNoBridge = bridge == null;
            if (hasNoBridge)
            {
                return;
            }

            ReportFlightStateChange(bridge);
            ReportPacing(bridge);
            FinishWait(bridge);
        }

        /// <summary>
        /// One line per power-up, so a log reader can tell one boot's lines from
        /// the next one's without counting.
        /// 電源投入ごとに 1 行。ログを読む人が、数えずに起動の境目を見分けられる
        /// ようにする。
        /// </summary>
        private void NoteBoot()
        {
            RemoteBridge.Instance?.Log.Write(
                LogLevel.Info, LogSources.Simulation, BootEvent,
                "the firmware booted",
                LogData.Of("power_cycles", simLoop.PowerCycles,
                           "battery_model", simLoop.batteryModel));
        }

        /// <summary>
        /// A line whenever the firmware's flight state, mode or ARM changes.
        /// This is what puts INIT → IDLE_GROUND → ARMED_GROUND → FLYING into the
        /// log as events with their virtual times, rather than leaving a reader
        /// to infer the flight from the sticks that were sent.
        /// ファームの飛行状態・モード・ARM が変わるたびに 1 行。
        /// INIT → IDLE_GROUND → ARMED_GROUND → FLYING が、仮想時刻を持つ事象と
        /// してログに載るのはこれによる。読む人が、送られたスティックから飛行を
        /// 推し量らずに済む。
        /// </summary>
        private void ReportFlightStateChange(RemoteBridge bridge)
        {
            SfuStepOut state = simLoop.LastResult;
            bool isArmed = state.Armed != 0;
            bool isUnchanged = state.FlightState == lastFlightState &&
                               state.FlightMode == lastFlightMode &&
                               isArmed == lastArmed;
            if (isUnchanged)
            {
                return;
            }

            string from = FlightStateNames.State(lastFlightState);
            lastFlightState = state.FlightState;
            lastFlightMode = state.FlightMode;
            lastArmed = isArmed;

            // Under the same claim the firmware's lines are, so ARM's transition
            // and the firmware's own account of arming come back together from
            // one `sf unity logs --cmd <id>`.
            // ファームの行と同じ主張の下に置く。ARM の遷移と、ARM についてファーム
            // 自身が述べたものが、1 回の `sf unity logs --cmd <id>` で揃って返る
            // ようにするためである。
            using (bridge.Log.BeginCommand(AttributedCommandId(bridge)))
            {
                bridge.Log.Write(
                    LogLevel.Info, LogSources.Simulation, FlightStateEvent,
                    $"{from} -> {FlightStateNames.State(state.FlightState)} " +
                    $"({FlightStateNames.Mode(state.FlightMode)}" +
                    (isArmed ? ", armed)" : ")"),
                    new Dictionary<string, object>
                    {
                        { "state", FlightStateNames.State(state.FlightState) },
                        { "previous_state", from },
                        { "mode", FlightStateNames.Mode(state.FlightMode) },
                        { "armed", isArmed },
                        { "altitude_m", simLoop.transform.position.y },
                    });
            }
        }

        /// <summary>
        /// The pacing figures, once a real second, plus a warning the first time
        /// a frame hits the per-frame ceiling. A per-tick line is forbidden
        /// (`AGENTS.md` "Logs"), and a once-a-second summary is what makes
        /// "did it keep up?" answerable from the log alone.
        /// 速さの数値を実時間 1 秒ごとに、加えてフレームが上限に当たった最初の
        /// 1 回に警告を出す。刻みごとの行は禁じられており（`AGENTS.md`「Logs」）、
        /// 「追いつけていたか」をログだけで答えられるようにするのが 1 秒ごとの
        /// 要約である。
        /// </summary>
        private void ReportPacing(RemoteBridge bridge)
        {
            SimClock clock = simLoop.Clock;

            bool startedFallingBehind = clock.IsBehind && !wasBehind;
            wasBehind = clock.IsBehind;
            if (startedFallingBehind)
            {
                bridge.Log.Write(
                    LogLevel.Warn, LogSources.Simulation, OverrunEvent,
                    "a frame asked for more ticks than the per-frame ceiling",
                    LogData.Of("max_ticks_per_frame", SimClock.MaxTicksPerFrame,
                               "us_per_tick", clock.MicrosecondsPerTick));
            }

            double now = Time.realtimeSinceStartupAsDouble;
            bool isNotDueYet = now < nextStatsAtRealSeconds;
            if (isNotDueYet)
            {
                return;
            }

            nextStatsAtRealSeconds = now + 1.0;
            bridge.Log.Write(
                LogLevel.Info, LogSources.Simulation, StatsEvent,
                $"real-time {clock.RealTimeRatio:F3}, " +
                $"{clock.MicrosecondsPerTick:F1} us/tick, {clock.FramesPerSecond:F1} fps",
                new Dictionary<string, object>
                {
                    { "real_time_ratio", clock.RealTimeRatio },
                    { "us_per_tick", clock.MicrosecondsPerTick },
                    { "fps", clock.FramesPerSecond },
                    { "behind", clock.IsBehind },
                    { "paused", clock.IsPaused },
                });
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
            RemoteBridge bridge = RemoteBridge.Instance;
            FirmwareLogPump pump = bridge?.FirmwareLog;
            bool cannotPump = pump == null || firmware == null;
            if (cannotPump)
            {
                return;
            }

            // Inside the scope of the command still taking effect, if there is
            // one. A command that moves the sticks -- `rc.arm`, `rc.set` -- has
            // returned long before the firmware reacts: the flag reaches the
            // firmware on a later tick, and the lines it writes about arming
            // are drained at the end of a later frame still. Attributing those
            // lines to the command that caused them is the whole point of
            // `sf unity logs --cmd <id>`, so the scope is held open across the
            // ticks the effect needs rather than closed when the answer went
            // out.
            // 効果がまだ及んでいる命令があれば、その範囲の中で行う。スティックを
            // 動かす命令 ― `rc.arm`・`rc.set` ― は、ファームが反応するずっと前に
            // 返っている。フラグがファームへ届くのは後の刻みで、ARM についてファーム
            // が書く行が汲み出されるのは、さらに後のフレームの終わりである。その行を
            // 原因となった命令に結び付けることこそ `sf unity logs --cmd <id>` の
            // 目的なので、範囲は答えを返した時点で閉じず、効果に要る刻みの間ずっと
            // 開けておく。
            using (bridge.Log.BeginCommand(AttributedCommandId(bridge)))
            {
                while (firmware.TryReadLogRecord(out FirmwareLogRecord record))
                {
                    pump.Pump(record);
                }

                pump.NoteDropped(firmware.DroppedLogRecords());
            }
        }

        /// <summary>
        /// The command this frame's firmware lines belong to, or null when none
        /// does. It expires on virtual time, not on frames: what decides
        /// whether a line is still that command's consequence is how much
        /// simulated time has passed since the sticks moved, and a slow host
        /// renders fewer frames over the same simulated span.
        /// このフレームのファームの行が属する命令。無ければ null。期限は仮想時間で
        /// 切る。フレーム数ではない。ある行がまだその命令の結果かを決めるのは、
        /// スティックが動いてから何秒ぶんシミュレーションが進んだかであり、遅い
        /// ホストは同じ区間でより少ないフレームしか描かないからである。
        /// </summary>
        private string AttributedCommandId(RemoteBridge bridge)
        {
            bool hasNone = string.IsNullOrEmpty(attributedCommandId);
            if (hasNone)
            {
                return null;
            }

            bool hasExpired = simLoop.Clock.VirtualMicroseconds > attributedUntilMicroseconds;
            if (hasExpired)
            {
                attributedCommandId = null;
                return null;
            }

            return attributedCommandId;
        }

        /// <summary>
        /// Claim the firmware's next few ticks of log for the command now being
        /// handled. Called by the commands that change what the firmware is
        /// asked to do; a command that only reads state claims nothing, so its
        /// id never appears on a line it did not cause.
        /// いま処理している命令のために、ファームのこの先数刻みぶんのログを取る。
        /// ファームへの指示を変える命令が呼ぶ。状態を読むだけの命令は何も取らない
        /// ので、その識別子が、原因でない行に載ることはない。
        /// </summary>
        private void ClaimFirmwareLog(RemoteBridge bridge)
        {
            string commandId = bridge.Log.CurrentCommandId;
            bool isNotInsideACommand = string.IsNullOrEmpty(commandId);
            if (isNotInsideACommand)
            {
                return;
            }

            attributedCommandId = commandId;
            attributedUntilMicroseconds = simLoop.Clock.VirtualMicroseconds +
                                          (long)(StickEffectSeconds * 1e6);
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
            simLoop.Booted -= NoteBoot;

            foreach (string command in new[]
                     {
                         "sim.pause", "sim.step", "sim.speed", "sim.reset",
                         "sim.power_cycle", "sim.state", "sim.wait",
                         "sim.trace_start", "sim.trace_stop", "sim.trace_dump",
                         "plant.torque_probe", "plant.wind",
                         "vehicle.state", "rc.set", "rc.arm", "rc.release",
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
                // Deliberately NOT a restart. The body goes back to its spawn
                // and the firmware keeps running, so the clock keeps counting
                // and a vehicle that was armed is still armed -- which is the
                // point when somebody wants to retry a manoeuvre mid-flight.
                // For a clean slate use `sim.power_cycle`. The answer says
                // which is which, because "reset" reads like the other one.
                // これは意図して**再起動ではない**。機体は出発点へ戻り、ファームは
                // 動き続けるので、時計は数え続け、ARM されていた機体は ARM された
                // ままである。飛行の途中で操作をやり直したい人が欲しいのはこちら
                // である。まっさらにしたいなら `sim.power_cycle` を使う。「reset」は
                // もう一方に読めるので、答えにどちらがどちらかを書く。
                simLoop.MoveToSpawn();
                return SimCommandResult.Success(
                    "{" +
                    $"\"sim_us\":{simLoop.Clock.VirtualMicroseconds}," +
                    $"\"armed\":{(simLoop.LastResult.Armed != 0 ? "true" : "false")}," +
                    "\"note\":\"the body moved; the clock and the firmware kept " +
                    "running — use sim.power_cycle for a fresh start\"" +
                    "}");
            }, "Move the vehicle to its spawn; the firmware and clock keep running");

            bridge.Commands.Register("sim.power_cycle", _ =>
            {
                // Centre the scripted sticks too. A power cycle is "a new
                // vehicle on the bench", and a new vehicle does not meet the
                // last flight's throttle and ALT_HOLD bit still held down: a
                // page left with `throttle 0, alt_hold true` cannot be armed
                // at all, which is what a person trying this by hand hits.
                // `rc.release` still hands the sticks back to the keyboard;
                // this only clears what a script was holding.
                // 台本のスティックも中央へ戻す。電源の入れ直しは「台の上の新しい
                // 機体」であり、新しい機体が前の飛行のスロットルと ALT_HOLD の
                // ビットを握ったままということは無い。`throttle 0, alt_hold true`
                // のまま残ったページは ARM すらできず、手で試した人がぶつかるのが
                // これである。`rc.release` は従来どおりスティックをキーボードへ
                // 返す。ここで消すのは台本が握っていた値だけである。
                scripted.Frame = RcFrame.Centred;
                simLoop.PowerOn();
                return SimCommandResult.Success(
                    "{" +
                    $"\"power_cycles\":{simLoop.PowerCycles}," +
                    "\"sticks\":\"centred\"" +
                    "}");
            }, "Discard the firmware, start a new one from INIT, centre the sticks");

            bridge.Commands.Register("sim.wait", args => BeginWait(bridge, args),
                "Hold until a virtual time arrives, then answer");

            RegisterTraceCommands(bridge);
        }

        /// <summary>
        /// The per-tick trace: start it, stop it, and post what it holds.
        ///
        /// The dump does NOT come back as the command's result. Twenty seconds
        /// is eight thousand lines, which would make one `POST /api/cmd` answer
        /// several megabytes and put a per-tick record into the command log the
        /// `AGENTS.md` rule keeps clear. It goes to `POST /api/trace` instead,
        /// which writes `logs/unity/<run_id>.trace.jsonl`, and the command
        /// answers only with how many ticks were sent.
        ///
        /// 刻みごとのトレース。始め、止め、保持しているものを送る。
        ///
        /// 取り出したものは命令の結果としては**返らない**。20 秒は 8000 行で、
        /// 1 回の `POST /api/cmd` の答えが数メガバイトになり、`AGENTS.md` の
        /// 決まりが空けておく命令のログへ刻みごとの記録を入れてしまう。代わりに
        /// `POST /api/trace` へ送り、そちらが `logs/unity/<run_id>.trace.jsonl`
        /// を書く。命令が答えるのは、何刻みぶん送ったかだけである。
        /// </summary>
        private void RegisterTraceCommands(RemoteBridge bridge)
        {
            bridge.Commands.Register("sim.trace_start", _ =>
            {
                simLoop.Trace.Start();
                return SimCommandResult.Success(
                    $"{{\"recording\":true,\"capacity\":{TickTrace.Capacity}}}");
            }, "Begin recording one line per tick into the ring");

            bridge.Commands.Register("sim.trace_stop", _ =>
            {
                simLoop.Trace.Stop();
                return SimCommandResult.Success(
                    $"{{\"recording\":false,\"ticks\":{simLoop.Trace.Count}}}");
            }, "Stop recording, keeping what the ring holds");

            bridge.Commands.Register("sim.trace_dump",
                _ => DumpTrace(bridge),
                "Post the ring to /api/trace and answer with the count");

            bridge.Commands.Register("plant.torque_probe", args =>
            {
                int steps = args.Int("steps", TorqueProbe.DefaultSteps);
                bool isOutOfRange = steps < 1 || steps > 4000;
                if (isOutOfRange)
                {
                    return SimCommandResult.Failure("steps must be between 1 and 4000");
                }

                // Pause first: the probe moves the body and drives PhysX
                // itself, so a tick running underneath it would fight it.
                // 先に止める。この測定は機体を動かし PhysX を自分で回すので、下で
                // 刻みが走っていると取り合いになる。
                bool wasPaused = simLoop.Clock.IsPaused;
                simLoop.Clock.Pause();

                Rigidbody body = simLoop.GetComponent<Rigidbody>();
                TorqueProbe.AxisResult[] axes = TorqueProbe.Run(body, steps);

                if (!wasPaused) { simLoop.Clock.Resume(); }
                return SimCommandResult.Success(TorqueProbe.ToJson(body, axes));
            }, "Apply a known torque about each body axis and report tau/I");

            bridge.Commands.Register("plant.wind", args =>
            {
                // A steady world-frame force on the airframe, which is how a
                // check pushes a flight off symmetry. The firmware owns the
                // wind model, so this only tells it what is blowing.
                // 機体にかかる世界系の一定の力。検査が飛行を対称から押し出す
                // 手立てである。風のモデルはファームが持つので、ここは何が吹いて
                // いるかを伝えるだけである。
                float x = (float)args.Double("x", 0.0);
                float y = (float)args.Double("y", 0.0);
                float z = (float)args.Double("z", 0.0);

                IFirmware firmware = simLoop.Firmware;
                bool hasNoFirmware = firmware == null;
                if (hasNoFirmware)
                {
                    return SimCommandResult.Failure("the firmware is not running");
                }

                int status = firmware.SetWind(x, y, z);
                bool refused = status != SfuAbi.Ok;
                if (refused)
                {
                    return SimCommandResult.Failure(
                        $"sfu_set_wind: {SfuAbi.Describe(status)}");
                }

                return SimCommandResult.Success(
                    $"{{\"wind\":[{x:R},{y:R},{z:R}]}}");
            }, "Set a steady world-frame wind force on the airframe [N]");
        }

        /// <summary>
        /// Hand the ring to the relay, which posts it to `/api/trace`.
        /// 輪を中継へ渡す。中継が `/api/trace` へ送る。
        /// </summary>
        private SimCommandResult DumpTrace(RemoteBridge bridge)
        {
            TickTrace tickTrace = simLoop.Trace;
            bool hasNothing = tickTrace.Count == 0;
            if (hasNothing)
            {
                return SimCommandResult.Failure(
                    "the trace ring is empty — run sim.trace_start first");
            }

            var builder = new System.Text.StringBuilder(tickTrace.Count * 320);
            tickTrace.WriteJsonLines(builder, bridge.Log.RunId, "page");

            bool posted = bridge.PostTrace(builder.ToString());
            bool couldNotPost = !posted;
            if (couldNotPost)
            {
                return SimCommandResult.Failure(
                    "this route cannot post a trace: sim.trace_dump needs a " +
                    "page served by `sf unity serve`");
            }

            return SimCommandResult.Success(
                "{" +
                $"\"ticks\":{tickTrace.Count}," +
                $"\"recorded\":{tickTrace.Recorded}," +
                $"\"bytes\":{builder.Length}" +
                "}");
        }

        /// <summary>
        /// Start a <c>sim.wait</c>: work out the virtual time to wait for, and
        /// hand the command back as deferred so the loop keeps running while it
        /// waits. A script at a terminal can then say "run to 6.3 s, then read
        /// the state" in two commands instead of sleeping for a guessed
        /// wall-clock duration and hoping the simulation matched it.
        /// <c>sim.wait</c> を始める。待つ仮想時刻を決め、命令を「後で答える」と
        /// して返す。待っている間も輪は回り続ける。端末の台本は「6.3 秒まで進めて
        /// から状態を読む」を 2 つの命令で書けるようになり、当て推量の実時間だけ
        /// 眠ってシミュレーションが合っていることを祈らずに済む。
        /// </summary>
        private SimCommandResult BeginWait(RemoteBridge bridge, SimCommandArgs args)
        {
            bool alreadyWaiting = pendingWait.IsActive;
            if (alreadyWaiting)
            {
                return SimCommandResult.Failure(
                    "another sim.wait is already in flight");
            }

            // Either spelling: microseconds for a script that reads `sim_us`
            // out of a previous answer, seconds for one a person writes.
            // どちらの綴りでも受ける。前の答えの `sim_us` をそのまま渡す台本には
            // マイクロ秒、人が書く台本には秒。
            // Read as a double rather than an int: a long run's `sim_us` passes
            // 2^31 after about 36 minutes of virtual time, and a script that
            // feeds a previous answer's value straight back must not wrap.
            // int ではなく double で読む。長い実行の `sim_us` は仮想時間 36 分ほどで
            // 2^31 を越え、前の答えの値をそのまま渡す台本が回り込んではならない。
            long target = (long)System.Math.Round(args.Double("sim_us", 0.0));
            bool usesSeconds = target <= 0;
            if (usesSeconds)
            {
                target = (long)System.Math.Round(args.Double("seconds", 0.0) * 1e6);
            }

            bool hasNoTarget = target <= 0;
            if (hasNoTarget)
            {
                return SimCommandResult.Failure(
                    "sim.wait needs sim_us or seconds, greater than zero");
            }

            double timeout = args.Double("timeout_s", MaxWaitSeconds);
            bool isOutOfRange = timeout <= 0.0 || timeout > MaxWaitSeconds;
            if (isOutOfRange)
            {
                return SimCommandResult.Failure(
                    $"timeout_s must be between 0 and {MaxWaitSeconds}");
            }

            // Already there: answer at once rather than waiting for a frame
            // that would answer identically.
            // 既に達している。同じ答えを出すだけのフレームを待たず、その場で返す。
            bool isAlreadyThere = simLoop.Clock.VirtualMicroseconds >= target;
            if (isAlreadyThere)
            {
                return SimCommandResult.Success(WaitAnswer(target, true));
            }

            // Only a route that can hold a request open across frames may wait.
            // The bridge hands the command's id out while the handler runs; a
            // route that cannot (the editor's `unity command`) hands out none,
            // and this refuses rather than starting a wait nobody will collect.
            // フレームをまたいで要求を保持できる経路だけが待てる。橋は処理が
            // 走っている間その命令の識別子を渡す。できない経路（エディタの
            // `unity command`）は何も渡さないので、誰も回収しない待ちを始めずに
            // ここで断る。
            string commandId = bridge.DeferredCommandId;
            bool cannotBeAnsweredLater = string.IsNullOrEmpty(commandId);
            if (cannotBeAnsweredLater)
            {
                return SimCommandResult.Failure(
                    "this route cannot wait: sim.wait needs a page served by " +
                    "`sf unity serve`");
            }

            pendingWait = new PendingWait(
                commandId, target, Time.realtimeSinceStartupAsDouble + timeout);
            return SimCommandResult.Deferred();
        }

        /// <summary>
        /// Answer a waiting <c>sim.wait</c> once its virtual time has arrived,
        /// or once it has waited long enough. A timeout is a FAILURE carrying
        /// how far the clock got, because a script that asked to reach 6.3 s and
        /// did not must stop rather than read a state from the wrong moment.
        /// 待っている <c>sim.wait</c> に、仮想時刻が来たとき、または待ちすぎた
        /// ときに答える。待ちすぎは**失敗**とし、時計がどこまで進んだかを添える。
        /// 6.3 秒に達することを求めて達しなかった台本は、違う時点の状態を読むので
        /// はなく止まるべきだからである。
        /// </summary>
        private void FinishWait(RemoteBridge bridge)
        {
            bool isNotWaiting = !pendingWait.IsActive;
            if (isNotWaiting)
            {
                return;
            }

            bool hasArrived = simLoop.Clock.VirtualMicroseconds >= pendingWait.TargetMicroseconds;
            if (hasArrived)
            {
                string commandId = pendingWait.CommandId;
                long target = pendingWait.TargetMicroseconds;
                pendingWait = default;
                bridge.AnswerLater(commandId,
                                   SimCommandResult.Success(WaitAnswer(target, true)));
                return;
            }

            bool hasWaitedLongEnough =
                Time.realtimeSinceStartupAsDouble >= pendingWait.DeadlineRealSeconds;
            if (!hasWaitedLongEnough)
            {
                return;
            }

            string timedOut = pendingWait.CommandId;
            long wanted = pendingWait.TargetMicroseconds;
            pendingWait = default;
            bridge.AnswerLater(timedOut, SimCommandResult.Failure(
                $"sim.wait timed out: the clock reached " +
                $"{simLoop.Clock.VirtualMicroseconds} us of {wanted} us " +
                (simLoop.Clock.IsPaused ? "(the simulation is paused)" : "")));
        }

        /// <summary>What a finished <c>sim.wait</c> answers. / 終わった <c>sim.wait</c> の答え。</summary>
        private string WaitAnswer(long target, bool reached)
        {
            return "{" +
                $"\"reached\":{(reached ? "true" : "false")}," +
                $"\"requested_sim_us\":{target}," +
                $"\"sim_us\":{simLoop.Clock.VirtualMicroseconds}," +
                $"\"ticks\":{simLoop.Clock.TotalTicks}" +
                "}";
        }

        /// <summary>
        /// One <c>sim.wait</c> in flight: which command to answer, the virtual
        /// time to answer at, and when to give up.
        /// 途中にある <c>sim.wait</c> 1 つ。どの命令に答えるか、どの仮想時刻で
        /// 答えるか、いつ諦めるか。
        /// </summary>
        private readonly struct PendingWait
        {
            internal PendingWait(string commandId, long targetMicroseconds,
                                 double deadlineRealSeconds)
            {
                CommandId = commandId;
                TargetMicroseconds = targetMicroseconds;
                DeadlineRealSeconds = deadlineRealSeconds;
            }

            internal string CommandId { get; }

            internal long TargetMicroseconds { get; }

            internal double DeadlineRealSeconds { get; }

            /// <summary>Whether a wait is in flight. / 待ちが途中にあるか。</summary>
            internal bool IsActive => TargetMicroseconds > 0;
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
                ClaimFirmwareLog(bridge);
                return SimCommandResult.Success();
            }, "Override the sticks until rc.release");

            bridge.Commands.Register("vehicle.state",
                _ => SimCommandResult.Success(VehicleStateJson()),
                "Truth, estimate, flight state, battery, ToF and the pacing figures");

            bridge.Commands.Register("rc.arm", args => Arm(bridge, args),
                "Press the transmitter's ARM button once (a rising edge)");

            bridge.Commands.Register("rc.release", _ =>
            {
                simLoop.RcSource = keyboardSource;
                return SimCommandResult.Success();
            }, "Hand the sticks back to the keyboard");
        }

        /// <summary>
        /// One press of the ARM button, the way the real transmitter's is: the
        /// flag goes up on a rising edge and stays up, because that is what the
        /// pilot's stick frames carry from then on. <c>armed=false</c> presses
        /// it the other way, which disarms.
        ///
        /// A script could set the bit with <c>rc.set --json '{"arm": true}'</c>,
        /// but then it owns the sticks from that moment and must remember to
        /// carry the bit in every later frame; forgetting it disarms mid-flight.
        /// This command exists so a script says "press ARM" and the throttle
        /// stays wherever the last <c>rc.set</c> left it.
        ///
        /// ARM ボタンを 1 回押す。実機の送信機と同じで、立ち上がりでフラグが立ち、
        /// 以後立ったままになる。操縦者のスティックのフレームがそれを運び続ける
        /// ためである。<c>armed=false</c> は逆向きに押すことで、DISARM になる。
        ///
        /// 台本は <c>rc.set --json '{"arm": true}'</c> でもビットを立てられるが、
        /// その瞬間からスティックの持ち主になり、以後の全てのフレームでビットを
        /// 運び続けねばならない。忘れると飛行中に DISARM する。この命令があるのは、
        /// 台本が「ARM を押す」とだけ言えて、スロットルは直前の <c>rc.set</c> が
        /// 置いた場所に留まるようにするためである。
        /// </summary>
        private SimCommandResult Arm(RemoteBridge bridge, SimCommandArgs args)
        {
            bool wantsArmed = args.Bool("armed", true);
            byte armBit = SfuAbi.FlagArm;

            RcFrame held = scripted.Frame;
            byte flags = wantsArmed
                ? (byte)(held.Flags | armBit)
                : (byte)(held.Flags & ~armBit);

            scripted.Frame = new RcFrame(
                held.Throttle, held.Roll, held.Pitch, held.Yaw, flags);
            simLoop.RcSource = scripted;
            ClaimFirmwareLog(bridge);

            return SimCommandResult.Success(
                "{" +
                $"\"armed_requested\":{(wantsArmed ? "true" : "false")}," +
                $"\"flags\":{flags}," +
                $"\"sim_us\":{simLoop.Clock.VirtualMicroseconds}" +
                "}");
        }

        /// <summary>
        /// Everything a check needs to judge a flight: where the vehicle really
        /// is, where the firmware thinks it is, what state it is in, and how
        /// well the host is keeping up.
        ///
        /// The truth comes from the <see cref="Rigidbody"/> the loop drives, not
        /// from the firmware's own copy, so a disagreement between the two shows
        /// up rather than being hidden by reading one of them twice.
        ///
        /// 飛行の合否を判定するために要るものすべて。機体が実際にどこに居るか、
        /// ファームがどこだと思っているか、どの状態か、ホストがどれだけ追いつけて
        /// いるか。
        ///
        /// 真値は輪が動かす <see cref="Rigidbody"/> から取る。ファーム自身の写し
        /// からではない。片方を二度読んで食い違いを隠すのではなく、表に出すため
        /// である。
        /// </summary>
        private string VehicleStateJson()
        {
            SfuStepOut state = simLoop.LastResult;
            SimClock clock = simLoop.Clock;
            Transform place = simLoop.transform;
            Rigidbody body = simLoop.GetComponent<Rigidbody>();
            Vector3 euler = place.rotation.eulerAngles;

            // The angle between the vehicle's own up and the world's. The check
            // wants one number for "is it roughly level?", which neither a
            // quaternion nor three Euler angles answer without arithmetic at
            // the far end.
            // 機体自身の上方向と世界の上方向の間の角。検証が欲しいのは「おおむね
            // 水平か」の 1 つの数であり、四元数も 3 つのオイラー角も、受け取る側で
            // 計算しなければそれを答えない。
            float tilt = Vector3.Angle(place.rotation * Vector3.up, Vector3.up);

            return "{" +
                $"\"sim_us\":{clock.VirtualMicroseconds}," +
                $"\"ticks\":{clock.TotalTicks}," +
                $"\"paused\":{(clock.IsPaused ? "true" : "false")}," +
                $"\"flight_state\":{LogJson.Quote(FlightStateNames.State(state.FlightState))}," +
                $"\"flight_mode\":{LogJson.Quote(FlightStateNames.Mode(state.FlightMode))}," +
                $"\"armed\":{(state.Armed != 0 ? "true" : "false")}," +
                $"\"battery_v\":{state.BatteryVoltage:F3}," +
                $"\"truth\":{{" +
                    $"\"position\":[{place.position.x:F4},{place.position.y:F4}," +
                    $"{place.position.z:F4}]," +
                    $"\"altitude_m\":{place.position.y:F4}," +
                    $"\"euler_deg\":[{euler.x:F2},{euler.y:F2},{euler.z:F2}]," +
                    $"\"tilt_deg\":{tilt:F2}," +
                    $"\"velocity\":[{body.linearVelocity.x:F4},{body.linearVelocity.y:F4}," +
                    $"{body.linearVelocity.z:F4}]," +
                    $"\"angular_velocity\":[{body.angularVelocity.x:F4}," +
                    $"{body.angularVelocity.y:F4},{body.angularVelocity.z:F4}]" +
                "}," +
                $"\"estimate\":{{" +
                    $"\"position\":[{state.EstimatedPositionX:F4}," +
                    $"{state.EstimatedPositionY:F4},{state.EstimatedPositionZ:F4}]," +
                    $"\"rotation\":[{state.EstimatedRotationX:F4}," +
                    $"{state.EstimatedRotationY:F4},{state.EstimatedRotationZ:F4}," +
                    $"{state.EstimatedRotationW:F4}]" +
                "}," +
                $"\"range_down_m\":{simLoop.LastRange.Distance:F4}," +
                $"\"range_down_valid\":{(simLoop.LastRange.IsValid ? "true" : "false")}," +
                $"\"flow_quality\":{simLoop.LastRange.FlowQuality:F3}," +
                $"\"real_time_ratio\":{clock.RealTimeRatio:F4}," +
                $"\"us_per_tick\":{clock.MicrosecondsPerTick:F2}," +
                $"\"fps\":{clock.FramesPerSecond:F1}," +
                $"\"behind\":{(clock.IsBehind ? "true" : "false")}," +
                $"\"power_cycles\":{simLoop.PowerCycles}" +
                "}";
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
