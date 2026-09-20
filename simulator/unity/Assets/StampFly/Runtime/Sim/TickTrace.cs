/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the per-tick diagnostic ring).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Globalization;
using System.Text;
using StampFly.Native;
using UnityEngine;

namespace StampFly.Sim
{
    /// <summary>
    /// A ring of what crossed the ABI on each of the last few thousand ticks,
    /// so two runs of the same script can be compared tick by tick.
    ///
    /// 直近数千刻みについて、ABI を渡ったものを溜める輪。同じ台本の 2 つの実行を
    /// 刻みごとに突き合わせられるようにするためである。
    ///
    /// ## Why a ring and not a log / なぜログではなく輪なのか
    ///
    /// `AGENTS.md` forbids a line per control step, and rightly: 400 lines a
    /// simulated second would bury every other event and cost more than the
    /// simulation. A ring costs one struct copy per tick, holds the last twenty
    /// seconds, and is written out ONLY when somebody asks — so the decision to
    /// look costs nothing until it is taken, and a failing flight can be dumped
    /// after the fact.
    ///
    /// `AGENTS.md` は制御の刻みごとの行を禁じている。もっともなことで、
    /// シミュレーションの 1 秒に 400 行は他の全ての事象を埋め、シミュレーション
    /// そのものより高くつく。輪なら費用は刻みあたり構造体 1 つの複写で、直近
    /// 20 秒を保ち、書き出すのは**誰かが求めたときだけ**である。よって「見る」と
    /// 決めるまで費用は掛からず、失敗した飛行を事後に取り出せる。
    ///
    /// ## What it is for / 何のためにあるか
    ///
    /// Both halves of one tick are kept: what the host PUT IN (`SfuStepIn`) and
    /// what came BACK (`SfuStepOut`). When two runs part company, which half
    /// moved first says where the cause is. If the input differs first, the
    /// fault is in Unity, PhysX or C#; if the input matches and the output
    /// differs, the same bytes produced different answers and the fault is
    /// inside the firmware module.
    ///
    /// 1 刻みの両側を持つ。ホストが**入れた**もの（`SfuStepIn`）と、**返って
    /// きた**もの（`SfuStepOut`）である。2 つの実行が食い違ったとき、どちらの側が
    /// 先に動いたかが原因の在り処を告げる。入力が先に違えば原因は Unity・PhysX・
    /// C# の側にあり、入力が一致して出力が違えば、同じバイト列が違う答えを生んだ
    /// ことになり、原因はファームのモジュールの中にある。
    ///
    /// @design docs/plans/unity-simulator.md §3 1 刻みの処理
    /// </summary>
    public sealed class TickTrace
    {
        /// <summary>
        /// How many ticks the ring holds: 8192 at 400 Hz is about twenty
        /// simulated seconds, which covers a whole check flight.
        /// 輪が保持する刻みの数。400 Hz で 8192 はシミュレーションの約 20 秒で、
        /// 確認の飛行 1 回をまるごと覆う。
        /// </summary>
        public const int Capacity = 8192;

        /// <summary>The format version, so a reader can refuse an old dump. / 形式の版。読み手が古い取り出しを断れるように。</summary>
        public const int FormatVersion = 1;

        private readonly Sample[] samples = new Sample[Capacity];
        private int head;
        private int size;
        private long recorded;

        /// <summary>
        /// Whether ticks are being recorded. Off until `sim.trace_start`, so the
        /// cost is taken only by a run that means to be examined.
        /// 刻みを記録しているか。`sim.trace_start` まで止まっているので、費用を
        /// 払うのは調べるつもりのある実行だけである。
        /// </summary>
        public bool IsRecording { get; private set; }

        /// <summary>How many ticks the ring holds right now. / いま輪が保持している刻みの数。</summary>
        public int Count => size;

        /// <summary>How many ticks have been recorded since the last start. / 前回の開始からに記録した刻みの数。</summary>
        public long Recorded => recorded;

        /// <summary>Begin recording, discarding whatever was held. / 記録を始める。保持していたものは捨てる。</summary>
        public void Start()
        {
            head = 0;
            size = 0;
            recorded = 0;
            IsRecording = true;
        }

        /// <summary>Stop recording, keeping what is held. / 記録を止める。保持しているものは残す。</summary>
        public void Stop()
        {
            IsRecording = false;
        }

        /// <summary>
        /// Add one tick. Called from the loop's hot path, so it copies fields
        /// into a pre-allocated slot and does nothing else — no allocation, no
        /// formatting, no branching on anything but the recording flag.
        /// 刻みを 1 つ足す。ループの主経路から呼ばれるので、あらかじめ確保した
        /// 枠へ欄を写すだけで他は何もしない。確保も整形もせず、記録の可否以外で
        /// 分岐もしない。
        /// </summary>
        /// <param name="velocityBefore">
        /// The world velocity `Physics.Simulate` started this tick from. With
        /// the velocity after (taken from <paramref name="body"/>) it is what
        /// the accelerometer reading was built from, so a reading that jumps
        /// can be told from a velocity that jumped: the first would be a fault
        /// in the arithmetic, the second in PhysX.
        /// この刻みで `Physics.Simulate` が出発した世界系の速度。刻み後の速度
        /// （<paramref name="body"/> から取る）と合わせて、加速度計の測定値が
        /// これらから作られる。よって測定値が飛んだのか速度が飛んだのかを
        /// 見分けられる。前者なら計算の、後者なら PhysX の誤りである。
        /// </param>
        public void Record(long simulationMicroseconds, int tickInFrame,
                           Rigidbody body, Vector3 velocityBefore,
                           in SfuStepIn input,
                           in SfuStepOut output, int status)
        {
            bool isNotRecording = !IsRecording;
            if (isNotRecording)
            {
                return;
            }

            int slot = (head + size) % Capacity;
            bool isFull = size == Capacity;
            if (isFull)
            {
                head = (head + 1) % Capacity;
            }
            else
            {
                size += 1;
            }

            samples[slot] = new Sample
            {
                SimulationMicroseconds = simulationMicroseconds,
                TickInFrame = tickInFrame,
                TruthAltitude = body.position.y,
                TruthVerticalSpeed = body.linearVelocity.y,
                TruthVelocityBeforeY = velocityBefore.y,
                InVelocityY = input.VelocityWorldY,
                InPositionX = input.PositionX,
                InPositionY = input.PositionY,
                InPositionZ = input.PositionZ,
                InRotationX = input.RotationX,
                InRotationY = input.RotationY,
                InRotationZ = input.RotationZ,
                InRotationW = input.RotationW,
                InRangeDown = input.RangeDownMeters,
                InRangeValid = input.RangeDownValid,
                InAccelX = input.AccelLocalX,
                InAccelY = input.AccelLocalY,
                InAccelZ = input.AccelLocalZ,
                InThrottle = input.RcThrottle,
                InFlags = input.RcFlags,
                OutForceX = output.ForceLocalX,
                OutForceY = output.ForceLocalY,
                OutForceZ = output.ForceLocalZ,
                OutTorqueX = output.TorqueLocalX,
                OutTorqueY = output.TorqueLocalY,
                OutTorqueZ = output.TorqueLocalZ,
                OutNowMicroseconds = output.NowMicroseconds,
                OutWrenchSeconds = output.WrenchSeconds,
                OutDuty0 = output.MotorDuty0,
                OutDuty1 = output.MotorDuty1,
                OutDuty2 = output.MotorDuty2,
                OutDuty3 = output.MotorDuty3,
                OutEstimatedAltitude = output.EstimatedPositionY,
                OutFlightState = output.FlightState,
                OutFlightMode = output.FlightMode,
                OutBatteryVoltage = output.BatteryVoltage,
                OutStatus = status,
            };

            recorded += 1;
        }

        /// <summary>
        /// The ring as JSON Lines, oldest first: one object per tick, with a
        /// header object first so a reader knows the version and the count.
        /// Built into the caller's builder rather than returned as a string,
        /// because twenty seconds is eight thousand lines and the caller
        /// usually wants to post them somewhere.
        /// 輪を JSON Lines にする。古い順に刻み 1 つずつ 1 行で、先頭に版と数を
        /// 告げる見出しの行を置く。文字列を返さず呼び出し側の組み立て器へ書くのは、
        /// 20 秒が 8000 行になり、呼び出し側はたいていそれをどこかへ送るためである。
        /// </summary>
        public void WriteJsonLines(StringBuilder into, string runId, string source)
        {
            var culture = CultureInfo.InvariantCulture;

            into.Append("{\"trace_version\":").Append(FormatVersion)
                .Append(",\"source\":").Append(Quote(source))
                .Append(",\"run_id\":").Append(Quote(runId))
                .Append(",\"ticks\":").Append(size)
                .Append(",\"recorded\":").Append(recorded)
                .Append("}\n");

            for (int index = 0; index < size; index++)
            {
                Sample sample = samples[(head + index) % Capacity];
                AppendSample(into, sample, culture);
            }
        }

        /// <summary>
        /// One tick as one JSON object. Every float is written with "R" so the
        /// value round-trips exactly: a comparison that is looking for the first
        /// differing bit must not be defeated by the printing.
        /// 刻み 1 つを JSON の物体 1 つにする。浮動小数は全て "R" で書き、値が
        /// そのまま往復するようにする。最初に違うビットを探す比較が、印字の side で
        /// 潰されてはならない。
        /// </summary>
        private static void AppendSample(StringBuilder into, in Sample sample,
                                         CultureInfo culture)
        {
            into.Append("{\"sim_us\":").Append(sample.SimulationMicroseconds)
                .Append(",\"tick_in_frame\":").Append(sample.TickInFrame)
                .Append(",\"truth_alt\":").Append(Number(sample.TruthAltitude, culture))
                .Append(",\"truth_vz\":").Append(Number(sample.TruthVerticalSpeed, culture))
                .Append(",\"truth_vz_before\":")
                .Append(Number(sample.TruthVelocityBeforeY, culture))
                .Append(",\"in_vz\":").Append(Number(sample.InVelocityY, culture))
                .Append(",\"in_pos\":[").Append(Number(sample.InPositionX, culture))
                .Append(',').Append(Number(sample.InPositionY, culture))
                .Append(',').Append(Number(sample.InPositionZ, culture))
                .Append("],\"in_rot\":[").Append(Number(sample.InRotationX, culture))
                .Append(',').Append(Number(sample.InRotationY, culture))
                .Append(',').Append(Number(sample.InRotationZ, culture))
                .Append(',').Append(Number(sample.InRotationW, culture))
                .Append("],\"in_range\":").Append(Number(sample.InRangeDown, culture))
                .Append(",\"in_range_valid\":").Append(sample.InRangeValid)
                .Append(",\"in_accel\":[").Append(Number(sample.InAccelX, culture))
                .Append(',').Append(Number(sample.InAccelY, culture))
                .Append(',').Append(Number(sample.InAccelZ, culture))
                .Append("],\"in_throttle\":").Append(sample.InThrottle)
                .Append(",\"in_flags\":").Append(sample.InFlags)
                .Append(",\"out_force\":[").Append(Number(sample.OutForceX, culture))
                .Append(',').Append(Number(sample.OutForceY, culture))
                .Append(',').Append(Number(sample.OutForceZ, culture))
                .Append("],\"out_torque\":[").Append(Number(sample.OutTorqueX, culture))
                .Append(',').Append(Number(sample.OutTorqueY, culture))
                .Append(',').Append(Number(sample.OutTorqueZ, culture))
                .Append("],\"out_now_us\":").Append(sample.OutNowMicroseconds)
                .Append(",\"out_wrench_dt\":")
                .Append(Number(sample.OutWrenchSeconds, culture))
                .Append(",\"out_duty\":[").Append(Number(sample.OutDuty0, culture))
                .Append(',').Append(Number(sample.OutDuty1, culture))
                .Append(',').Append(Number(sample.OutDuty2, culture))
                .Append(',').Append(Number(sample.OutDuty3, culture))
                .Append("],\"out_est_alt\":").Append(Number(sample.OutEstimatedAltitude, culture))
                .Append(",\"out_state\":").Append(sample.OutFlightState)
                .Append(",\"out_mode\":").Append(sample.OutFlightMode)
                .Append(",\"out_vbatt\":").Append(Number(sample.OutBatteryVoltage, culture))
                .Append(",\"out_status\":").Append(sample.OutStatus)
                .Append("}\n");
        }

        /// <summary>
        /// A float as JSON. "R" round-trips the exact value; a non-finite one
        /// becomes a quoted string, because JSON has no NaN and a reader that
        /// crashes on the dump cannot tell anybody what went wrong.
        /// 浮動小数を JSON にする。"R" は値をそのまま往復させる。有限でない値は
        /// 引用した文字列にする。JSON に NaN は無く、取り出したもので落ちる読み手は
        /// 何が起きたかを誰にも伝えられないからである。
        /// </summary>
        private static string Number(float value, CultureInfo culture)
        {
            bool isFinite = !float.IsNaN(value) && !float.IsInfinity(value);
            if (isFinite)
            {
                return value.ToString("R", culture);
            }

            return "\"" + value.ToString(culture) + "\"";
        }

        /// <summary>A string as a JSON string. / 文字列を JSON の文字列にする。</summary>
        private static string Quote(string value)
        {
            bool isEmpty = string.IsNullOrEmpty(value);
            if (isEmpty)
            {
                return "\"\"";
            }

            return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
        }

        /// <summary>
        /// One tick's worth. A flat struct of value types, so the ring is one
        /// allocation and recording a tick copies rather than allocates.
        /// 刻み 1 つぶん。値型だけの平らな構造体にしてあるので、輪は 1 回の確保で
        /// 済み、刻みの記録は確保ではなく複写になる。
        /// </summary>
        private struct Sample
        {
            internal long SimulationMicroseconds;
            internal int TickInFrame;
            internal float TruthAltitude;
            internal float TruthVerticalSpeed;
            internal float TruthVelocityBeforeY;
            internal float InVelocityY;
            internal float InPositionX, InPositionY, InPositionZ;
            internal float InRotationX, InRotationY, InRotationZ, InRotationW;
            internal float InRangeDown;
            internal int InRangeValid;
            internal float InAccelX, InAccelY, InAccelZ;
            internal int InThrottle;
            internal int InFlags;
            internal float OutForceX, OutForceY, OutForceZ;
            internal float OutTorqueX, OutTorqueY, OutTorqueZ;
            internal long OutNowMicroseconds;
            internal float OutWrenchSeconds;
            internal float OutDuty0, OutDuty1, OutDuty2, OutDuty3;
            internal float OutEstimatedAltitude;
            internal int OutFlightState;
            internal int OutFlightMode;
            internal float OutBatteryVoltage;
            internal int OutStatus;
        }
    }
}
