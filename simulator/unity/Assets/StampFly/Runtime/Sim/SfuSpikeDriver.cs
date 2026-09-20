/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — stage 1(b)(d) spike).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Text;
using UnityEngine;
using UnityEngine.InputSystem;

namespace StampFly.Sim
{
    /// <summary>
    /// The smallest scene that answers stage 1(b): load the firmware's own wasm
    /// module, call it synchronously several times per frame to catch up with
    /// real time, and show the vehicle's height on a cube.
    ///
    /// 段階 1(b) に答える最小の場面: ファーム自身の wasm モジュールを読み込み、
    /// 実時間に追いつくため 1 フレームに複数回、同期で呼び、機体の高さを立方体で示す。
    ///
    /// It also carries the power-cycle check: pressing R throws the module away
    /// and builds a new one, which must start the firmware from INIT again, and
    /// the panel reports the wasm memory so repeated cycles can be watched.
    ///
    /// 電源の入れ直しの検証も兼ねる: R を押すとモジュールを捨てて作り直す。ファームは
    /// INIT から起動し直さねばならない。繰り返したときの推移を見られるよう、
    /// 表示板に wasm のメモリの大きさを出す。
    ///
    /// There is deliberately no PhysX here. Stage 1(b) asks only whether the
    /// synchronous call works; the rigid body and the real SimLoop are stage 3.
    ///
    /// ここに PhysX は意図的に置かない。段階 1(b) が問うのは同期呼び出しが成り立つか
    /// だけであり、剛体と本物の SimLoop は段階 3 の作業。
    ///
    /// @design docs/plans/unity-simulator.md — 段階 1(b)(d) 技術検証
    /// </summary>
    public sealed class SfuSpikeDriver : MonoBehaviour
    {
        // One firmware tick, matching the firmware's own 400 Hz control period.
        // ファーム 1 刻み。ファーム自身の 400Hz の制御周期と同じ。
        private const double TickSeconds = 0.0025;

        // Never run more than this many ticks in one frame. Without a ceiling a
        // slow frame would ask for a longer catch-up, which would make the next
        // frame slower still, and the browser would stop drawing. 40 ticks is
        // 0.1 s of simulation, so a 60 fps frame (0.0167 s) has ample room and
        // a long stall is worked off over several frames instead of one.
        // 1 フレームで進める刻みの上限。上限が無いと、遅いフレームがより長い
        // 追いつきを要求し、次のフレームをさらに遅くして、ブラウザが描画を
        // やめてしまう。40 刻み＝シミュレーション 0.1 秒で、60fps の 1 フレーム
        // （0.0167 秒）に十分な余裕があり、長い停止も 1 フレームでなく数フレームに
        // 分けて取り戻す。
        private const int MaxTicksPerFrame = 40;

        // Flight-state numbers, as the firmware publishes them (sf::FlightState).
        // ファームが publish する飛行状態の番号（sf::FlightState）。
        private static readonly string[] StateNames =
        {
            "INIT", "IDLE_GROUND", "ARMED_GROUND", "TAKEOFF", "FLYING", "LANDING",
        };

        [Tooltip("Where the firmware module is served from, relative to the player. " +
                 "ファームのモジュールの配信元。プレイヤーからの相対で指定する。")]
        public string firmwareUrl = "StreamingAssets/sfu_firmware.js";

        [Tooltip("The object moved to the vehicle's height. 機体の高さに動かす対象。")]
        public Transform vehicleTransform;

        private bool isBooted;
        private double virtualSeconds;
        private double realSecondsSinceBoot;

        // Totals for the measurements the report needs.
        // 報告に要る計測のための積算値。
        private long totalTicks;
        private double totalStepSeconds;
        private double loadStartRealtime;
        private double loadFinishedSeconds;

        // Per-second sampling, so the panel shows a rate rather than a total.
        // 1 秒ごとの標本。表示板に積算ではなく速さを出すため。
        private double windowRealSeconds;
        private double windowVirtualSeconds;
        private long windowTicks;
        private double windowStepSeconds;
        private int windowFrames;

        private double realTimeRatio;
        private double microsecondsPerStep;
        private double framesPerSecond;

        private readonly double[] pose = new double[SfuSpikeFirmware.PoseValueCount];
        private readonly StringBuilder panelText = new StringBuilder();
        private GUIStyle panelStyle;

        private void Start()
        {
            StartLoading();
        }

        /// <summary>
        /// Begin (or begin again) loading the firmware module. Called on start
        /// and on every power cycle.
        /// ファームのモジュールの読み込みを開始する（やり直しも同じ）。起動時と
        /// 電源の入れ直しのたびに呼ぶ。
        /// </summary>
        private void StartLoading()
        {
            isBooted = false;
            virtualSeconds = 0.0;
            realSecondsSinceBoot = 0.0;
            totalTicks = 0;
            totalStepSeconds = 0.0;
            loadFinishedSeconds = 0.0;
            loadStartRealtime = Time.realtimeSinceStartupAsDouble;

            SfuSpikeFirmware.Load(firmwareUrl);
        }

        private void Update()
        {
            HandlePowerCycleKey();

            bool isReady = SfuSpikeFirmware.Status == SfuSpikeStatus.Ready;
            if (!isReady)
            {
                return;
            }

            if (!isBooted)
            {
                isBooted = SfuSpikeFirmware.Boot();
                loadFinishedSeconds = Time.realtimeSinceStartupAsDouble - loadStartRealtime;
                return;
            }

            AdvanceToRealTime();
            ApplyPoseToVehicle();
            UpdateRates();
        }

        /// <summary>
        /// R throws the module away and loads a fresh one — the plan's power
        /// cycle, since the firmware's statics cannot be reset in place.
        ///
        /// R でモジュールを捨てて新しく読み込む。ファームの静的変数はその場では
        /// 戻せないため、これが計画でいう電源の入れ直し。
        ///
        /// The key is read through the Input System, because this project has
        /// Player Settings set to that package and the old UnityEngine.Input
        /// throws there.
        /// キーは Input System で読む。このプロジェクトの Player Settings が同
        /// パッケージに切り替えてあり、旧来の UnityEngine.Input は例外を出すため。
        /// </summary>
        private void HandlePowerCycleKey()
        {
            Keyboard keyboard = Keyboard.current;
            if (keyboard == null)
            {
                return;
            }

            bool wantsPowerCycle = keyboard.rKey.wasPressedThisFrame;
            if (wantsPowerCycle)
            {
                PowerCycle();
            }
        }

        /// <summary>
        /// Throw the module away and load a new one. Also reachable from
        /// JavaScript, so a browser check can cycle the power without keystrokes.
        /// モジュールを捨てて新しく読み込む。JavaScript からも到達でき、ブラウザでの
        /// 検証がキー操作なしで電源を入れ直せるようにする。
        /// </summary>
        public void PowerCycle()
        {
            SfuSpikeFirmware.Release();
            StartLoading();
        }

        /// <summary>
        /// Run as many whole ticks as this frame's elapsed real time asks for,
        /// each one a synchronous call into the firmware module.
        /// このフレームの実経過時間が求めるだけの刻みを進める。1 刻みごとに
        /// ファームのモジュールを同期で呼ぶ。
        /// </summary>
        private void AdvanceToRealTime()
        {
            realSecondsSinceBoot += Time.unscaledDeltaTime;

            // Give up on a stall longer than this rather than chase it: the
            // page load and a backgrounded tab both produce one, and chasing it
            // would freeze the first frames after they end.
            // これより長い停止は追いかけずに諦める。ページの読み込みも、背面に
            // 回ったタブも停止を生み、追いかけるとその直後のフレームが固まる。
            double maxCatchUpSeconds = MaxTicksPerFrame * TickSeconds;
            bool hasStalled = realSecondsSinceBoot - virtualSeconds > maxCatchUpSeconds;
            if (hasStalled)
            {
                realSecondsSinceBoot = virtualSeconds + maxCatchUpSeconds;
            }

            double behindSeconds = realSecondsSinceBoot - virtualSeconds;
            int wantedTicks = (int)(behindSeconds / TickSeconds);
            bool isTooFarBehind = wantedTicks > MaxTicksPerFrame;
            if (isTooFarBehind)
            {
                wantedTicks = MaxTicksPerFrame;
            }
            if (wantedTicks <= 0)
            {
                return;
            }

            double startRealtime = Time.realtimeSinceStartupAsDouble;
            for (int tick = 0; tick < wantedTicks; tick++)
            {
                SfuSpikeFirmware.Step();
            }
            double spentSeconds = Time.realtimeSinceStartupAsDouble - startRealtime;

            virtualSeconds += wantedTicks * TickSeconds;
            totalTicks += wantedTicks;
            totalStepSeconds += spentSeconds;
            windowTicks += wantedTicks;
            windowStepSeconds += spentSeconds;
        }

        /// <summary>
        /// Put the vehicle object where the firmware thinks the craft is. ENU
        /// (east, north, up) becomes Unity's left-handed (x, y, z) = (east, up, north).
        /// ファームが考える機体の位置へ対象を置く。ENU（東・北・上）を Unity の
        /// 左手系 (x, y, z) = (東, 上, 北) に直す。
        /// </summary>
        private void ApplyPoseToVehicle()
        {
            bool hasPose = SfuSpikeFirmware.Pose(pose);
            bool canMove = hasPose && vehicleTransform != null;
            if (!canMove)
            {
                return;
            }

            vehicleTransform.position = new Vector3(
                (float)pose[0], (float)pose[2], (float)pose[1]);
        }

        /// <summary>
        /// Refresh the once-a-second figures the panel shows.
        /// 表示板に出す、1 秒ごとの数値を更新する。
        /// </summary>
        private void UpdateRates()
        {
            windowRealSeconds += Time.unscaledDeltaTime;
            windowVirtualSeconds = virtualSeconds;
            windowFrames += 1;

            bool windowIsFull = windowRealSeconds >= 1.0;
            if (!windowIsFull)
            {
                return;
            }

            realTimeRatio = (windowTicks * TickSeconds) / windowRealSeconds;
            bool hasTicks = windowTicks > 0;
            microsecondsPerStep = hasTicks
                ? (windowStepSeconds * 1e6) / windowTicks
                : 0.0;
            framesPerSecond = windowFrames / windowRealSeconds;

            windowRealSeconds = 0.0;
            windowTicks = 0;
            windowStepSeconds = 0.0;
            windowFrames = 0;

            // One line per second into the browser console, tagged so it can be
            // picked out of Unity's own output by a pattern.
            // 1 秒に 1 行をブラウザのコンソールへ出す。Unity 自身の出力から
            // 目印で拾えるようにする。
            Debug.Log($"SFUSPIKE {Summary()}");
        }

        private void OnGUI()
        {
            if (panelStyle == null)
            {
                panelStyle = new GUIStyle(GUI.skin.label) { fontSize = 16 };
            }

            panelText.Clear();
            panelText.AppendLine($"status        {SfuSpikeFirmware.Status}");
            panelText.AppendLine($"generation    {SfuSpikeFirmware.Generation()}");
            panelText.AppendLine($"load seconds  {loadFinishedSeconds:F3}");
            panelText.AppendLine($"real-time     {realTimeRatio:F3} x");
            panelText.AppendLine($"us per step   {microsecondsPerStep:F2}");
            panelText.AppendLine($"frames/s      {framesPerSecond:F1}");
            panelText.AppendLine($"sim seconds   {windowVirtualSeconds:F2}");
            panelText.AppendLine($"ticks total   {totalTicks}");
            panelText.AppendLine($"altitude m    {SfuSpikeFirmware.Altitude():F3}");
            panelText.AppendLine($"state         {StateName(SfuSpikeFirmware.State())}");
            panelText.AppendLine($"battery V     {SfuSpikeFirmware.BatteryVolts():F2}");
            panelText.AppendLine($"wasm bytes    {SfuSpikeFirmware.HeapBytes():F0}");
            panelText.AppendLine($"js heap bytes {SfuSpikeFirmware.JsHeapBytes():F0}");
            panelText.AppendLine("press R for a power cycle / R で電源の入れ直し");

            GUI.Label(new Rect(12, 12, 560, 380), panelText.ToString(), panelStyle);
        }

        /// <summary>Flight-state number → name. / 飛行状態の番号 → 名前。</summary>
        private static string StateName(int state)
        {
            bool isKnown = state >= 0 && state < StateNames.Length;
            return isKnown ? StateNames[state] : "?";
        }

        /// <summary>
        /// The numbers the stage 1(b) report needs, as one line. Read from the
        /// browser console with <c>window.stampflySpikeSummary()</c>, which the
        /// scene's bootstrap installs.
        /// 段階 1(b) の報告に要る数値を 1 行で返す。場面の組み立て側が用意する
        /// <c>window.stampflySpikeSummary()</c> でブラウザのコンソールから読む。
        /// </summary>
        public string Summary()
        {
            double averageMicroseconds = totalTicks > 0
                ? (totalStepSeconds * 1e6) / totalTicks
                : 0.0;

            return $"status={SfuSpikeFirmware.Status} " +
                   $"generation={SfuSpikeFirmware.Generation()} " +
                   $"loadSeconds={loadFinishedSeconds:F3} " +
                   $"realTimeRatio={realTimeRatio:F3} " +
                   $"usPerStep={microsecondsPerStep:F2} " +
                   $"usPerStepAverage={averageMicroseconds:F2} " +
                   $"fps={framesPerSecond:F1} " +
                   $"simSeconds={virtualSeconds:F2} " +
                   $"ticks={totalTicks} " +
                   $"altitude={SfuSpikeFirmware.Altitude():F3} " +
                   $"state={StateName(SfuSpikeFirmware.State())} " +
                   $"battery={SfuSpikeFirmware.BatteryVolts():F2} " +
                   $"wasmBytes={SfuSpikeFirmware.HeapBytes():F0} " +
                   $"jsHeapBytes={SfuSpikeFirmware.JsHeapBytes():F0}";
        }
    }
}
