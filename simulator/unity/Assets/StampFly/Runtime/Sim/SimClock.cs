/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — pacing virtual time).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine;

namespace StampFly.Sim
{
    /// <summary>
    /// Decides how many firmware ticks one rendered frame should run, and keeps
    /// the figures the panel reports. It holds no physics and no firmware, so
    /// its arithmetic can be tested on its own.
    ///
    /// 描画 1 フレームでファームを何刻み進めるかを決め、表示板が出す数値を保つ。
    /// 物理もファームも持たないので、この計算だけを単体で試験できる。
    ///
    /// ## What happens when a frame is late / フレームが遅れたときの扱い
    ///
    /// No firmware tick is ever skipped: virtual time falls behind instead, and
    /// the panel shows the ratio to real time so the person can see it. A frame
    /// runs at most <see cref="MaxTicksPerFrame"/> ticks; without that ceiling a
    /// slow frame would ask for a longer catch-up, which would make the next
    /// frame slower still, and the browser would stop drawing altogether.
    ///
    /// ファームの刻みを飛ばすことはしない。代わりに仮想時間を遅らせ、実時間比を
    /// 表示板に出して人に見えるようにする。1 フレームで進めるのは多くとも
    /// <see cref="MaxTicksPerFrame"/> 刻みである。上限が無いと、遅いフレームが
    /// より長い追いつきを要求し、次のフレームをさらに遅くして、ついにはブラウザが
    /// 描画をやめてしまう。
    ///
    /// A backgrounded tab produces a stall of any length — Chrome does not call
    /// `requestAnimationFrame` there at all — so on the way back the backlog is
    /// DISCARDED rather than worked off. Catching up on a minute of stall would
    /// freeze the first frames after the return and fast-forward the flight.
    ///
    /// 背面に回ったタブは任意の長さの停止を生む ― Chrome はそこで
    /// `requestAnimationFrame` を呼ばない ― ので、戻ってきたときの溜まりは
    /// 取り戻さずに**捨てる**。1 分の停止を追いかければ、戻った直後のフレームが
    /// 固まり、飛行が早送りになる。
    ///
    /// @design docs/plans/unity-simulator.md §3 1 刻みの処理
    /// </summary>
    public sealed class SimClock
    {
        /// <summary>One firmware tick [µs]: 400 Hz, the control period. / ファーム 1 刻み [µs]。400 Hz の制御周期。</summary>
        public const uint TickMicroseconds = 2500;

        /// <summary>The same tick in seconds. / 同じ刻みを秒で表したもの。</summary>
        public const double TickSeconds = TickMicroseconds * 1e-6;

        /// <summary>
        /// The most ticks one frame runs: 12 ticks = 30 ms of simulation, which
        /// a 60 fps frame (16.7 ms) has room for even at several times the
        /// measured cost per tick.
        /// 1 フレームで進める刻みの上限。12 刻み = シミュレーション 30 ms で、
        /// 60fps の 1 フレーム（16.7 ms）は、実測の 1 刻みあたりの費用が数倍に
        /// なっても収まる余裕がある。
        /// </summary>
        public const int MaxTicksPerFrame = 12;

        /// <summary>
        /// A frame longer than this counts as a stall — a backgrounded tab, a
        /// breakpoint, a long asset load — and its backlog is dropped instead of
        /// chased. Well above any frame a running simulation produces.
        /// これより長いフレームは停止とみなし ― 背面のタブ、停止点、長い読み込み ―
        /// 溜まりを追いかけずに捨てる。動いているシミュレーションが生むどのフレーム
        /// よりも十分に長くしてある。
        /// </summary>
        public const double StallSeconds = 0.5;

        /// <summary>The slowest the simulation may be asked to run. / シミュレーションに頼める最も遅い倍率。</summary>
        public const float MinSpeed = 0.1f;

        /// <summary>The fastest the simulation may be asked to run. / シミュレーションに頼める最も速い倍率。</summary>
        public const float MaxSpeed = 4.0f;

        private double virtualSeconds;
        private double owedSeconds;
        private int singleStepsPending;

        // Totals over the current one-second reporting window.
        // いまの 1 秒の報告の窓での積算値。
        private double windowRealSeconds;
        private int windowTicks;
        private double windowStepSeconds;
        private int windowFrames;

        /// <summary>Virtual time since boot [s]. / 起動からの仮想時間 [s]。</summary>
        public double VirtualSeconds => virtualSeconds;

        /// <summary>Virtual time since boot [µs], the firmware's own clock. / 起動からの仮想時間 [µs]。ファーム自身の時計。</summary>
        public long VirtualMicroseconds => (long)System.Math.Round(virtualSeconds * 1e6);

        /// <summary>Whether the loop is paused. / ループが一時停止しているか。</summary>
        public bool IsPaused { get; private set; }

        /// <summary>How fast the simulation is asked to run, 0.1..4. / シミュレーションに頼む倍率。0.1〜4。</summary>
        public float Speed { get; private set; } = 1.0f;

        /// <summary>Simulated seconds per real second, over the last window. / 直前の窓での、実時間 1 秒あたりのシミュレーション秒。</summary>
        public double RealTimeRatio { get; private set; }

        /// <summary>Real time one firmware tick cost [µs], over the last window. / 直前の窓での 1 刻みの所要時間 [µs]。</summary>
        public double MicrosecondsPerTick { get; private set; }

        /// <summary>Rendered frames per second, over the last window. / 直前の窓での 1 秒あたりの描画フレーム数。</summary>
        public double FramesPerSecond { get; private set; }

        /// <summary>Ticks run since boot. / 起動からの刻みの数。</summary>
        public long TotalTicks { get; private set; }

        /// <summary>Whether the last frame hit the per-frame ceiling. / 直前のフレームが 1 フレームの上限に当たったか。</summary>
        public bool IsBehind { get; private set; }

        /// <summary>Pause, holding virtual time where it is. / 一時停止する。仮想時間はその場に留まる。</summary>
        public void Pause() => IsPaused = true;

        /// <summary>Resume. The time spent paused is not owed. / 再開する。止まっていた間の時間は借りにしない。</summary>
        public void Resume()
        {
            IsPaused = false;
            owedSeconds = 0.0;
        }

        /// <summary>Pause if running, resume if paused. / 動いていれば止め、止まっていれば動かす。</summary>
        public void TogglePause()
        {
            if (IsPaused)
            {
                Resume();
                return;
            }
            Pause();
        }

        /// <summary>
        /// Run <paramref name="ticks"/> more ticks and then hold, whether or not
        /// the clock is paused — the single-step control.
        /// <paramref name="ticks"/> 刻みだけ進めてから止まる。一時停止していても
        /// いなくても同じ。コマ送りの操作である。
        /// </summary>
        public void StepOnce(int ticks = 1)
        {
            IsPaused = true;
            singleStepsPending += ticks;
        }

        /// <summary>Sets the speed, clamped to its range. / 倍率を設定する。範囲に収める。</summary>
        public void SetSpeed(float speed)
        {
            Speed = Mathf.Clamp(speed, MinSpeed, MaxSpeed);
        }

        /// <summary>
        /// Put virtual time back to zero, for a power cycle. The measured rates
        /// are kept: they describe the host, not the flight.
        /// 電源の入れ直しのため、仮想時間を 0 に戻す。実測の速さは残す。あれは
        /// 飛行ではなくホストの性能を表すものだからである。
        /// </summary>
        public void Reset()
        {
            virtualSeconds = 0.0;
            owedSeconds = 0.0;
            singleStepsPending = 0;
            TotalTicks = 0;
            IsBehind = false;
        }

        /// <summary>
        /// How many ticks this frame should run, given how long the frame was.
        /// Advances virtual time by what it returns, so the caller runs exactly
        /// that many and nothing else has to agree on the number.
        ///
        /// このフレームで進めるべき刻みの数を、フレームの長さから決める。返した
        /// ぶんだけ仮想時間も進めるので、呼び出し側はその数だけ刻めばよく、数を
        /// 他の誰かと合わせる必要はない。
        /// </summary>
        public int TicksForFrame(double frameSeconds)
        {
            bool hasSingleSteps = singleStepsPending > 0;
            if (hasSingleSteps)
            {
                int ticks = Mathf.Min(singleStepsPending, MaxTicksPerFrame);
                singleStepsPending -= ticks;
                virtualSeconds += ticks * TickSeconds;
                IsBehind = false;
                return ticks;
            }

            if (IsPaused)
            {
                IsBehind = false;
                return 0;
            }

            // A stall's backlog is dropped, for the reason in the class note.
            // 停止の溜まりは捨てる。理由はこのクラスの注記のとおり。
            bool hasStalled = frameSeconds > StallSeconds;
            double usableSeconds = hasStalled ? StallSeconds : frameSeconds;

            owedSeconds += usableSeconds * Speed;

            // Cap the debt itself, not just the ticks one frame runs.
            //
            // Clamping only the ticks lets the debt grow without bound while
            // the page is slow, and once it has, EVERY later frame runs the
            // ceiling -- twelve ticks (30 ms of simulation) in a 16 ms frame is
            // 1.875x real time, which is what a log of a long-backgrounded tab
            // shows. The flight then runs FASTER than real time until the debt
            // is worked off, which is the opposite of the plan's "fall behind
            // rather than skip a tick" and makes the readout's real-time ratio
            // meaningless. A stall is only recognised when ONE frame exceeds
            // half a second, so a tab throttled to a steady 0.4 s never trips
            // it and accumulates debt for as long as it is left open.
            //
            // The cap is what one frame can pay off, so a page that has fallen
            // behind catches up at the ceiling for exactly one frame and then
            // tracks real time again.
            //
            // 刻みの数だけでなく、借り自体に上限を置く。
            //
            // 刻みの数だけを抑えると、ページが遅い間に借りは際限なく膨らむ。そして
            // 膨らんだ後は、**以後のどのフレームも**上限まで走る。16 ms のフレームで
            // 12 刻み（シミュレーション 30 ms）は実時間の 1.875 倍で、長く背面に
            // あったタブのログが示すのがこの値である。借りを返し終えるまで飛行は
            // 実時間より**速く**進む。これは計画の「刻みを飛ばさず、代わりに遅れる」
            // の逆であり、表示板の実時間比を無意味にする。停止とみなすのは 1 つの
            // フレームが 0.5 秒を越えたときだけなので、0.4 秒で安定して絞られた
            // タブはそれに当たらず、開かれている限り借りを溜め続ける。
            //
            // 上限は 1 フレームで返せる量にしてある。遅れたページはちょうど 1
            // フレームだけ上限で追いつき、その後は再び実時間に追随する。
            double maximumOwedSeconds = MaxTicksPerFrame * TickSeconds;
            bool owesMoreThanOneFrameCanPay = owedSeconds > maximumOwedSeconds;
            if (owesMoreThanOneFrameCanPay)
            {
                owedSeconds = maximumOwedSeconds;
            }

            int wanted = (int)(owedSeconds / TickSeconds);
            IsBehind = owesMoreThanOneFrameCanPay;
            if (wanted > MaxTicksPerFrame)
            {
                wanted = MaxTicksPerFrame;
            }

            bool hasNoWholeTick = wanted <= 0;
            if (hasNoWholeTick)
            {
                return 0;
            }

            owedSeconds -= wanted * TickSeconds;
            virtualSeconds += wanted * TickSeconds;
            return wanted;
        }

        /// <summary>
        /// Record what a frame actually did, so the once-a-second figures can be
        /// worked out. <paramref name="stepSeconds"/> is the real time the
        /// firmware calls themselves took, apart from rendering.
        /// フレームが実際に行ったことを記録し、1 秒ごとの数値を出せるようにする。
        /// <paramref name="stepSeconds"/> は、描画とは別に、ファームの呼び出しその
        /// ものに掛かった実時間である。
        /// </summary>
        public void RecordFrame(double frameSeconds, int ticks, double stepSeconds)
        {
            TotalTicks += ticks;
            windowRealSeconds += frameSeconds;
            windowTicks += ticks;
            windowStepSeconds += stepSeconds;
            windowFrames += 1;

            bool windowIsFull = windowRealSeconds >= 1.0;
            if (!windowIsFull)
            {
                return;
            }

            RealTimeRatio = (windowTicks * TickSeconds) / windowRealSeconds;
            bool hasTicks = windowTicks > 0;
            MicrosecondsPerTick = hasTicks
                ? (windowStepSeconds * 1e6) / windowTicks
                : 0.0;
            FramesPerSecond = windowFrames / windowRealSeconds;

            windowRealSeconds = 0.0;
            windowTicks = 0;
            windowStepSeconds = 0.0;
            windowFrames = 0;
        }
    }
}
