/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the on-screen readout).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using StampFly.Input;
using StampFly.Native;
using StampFly.Sim;
using UnityEngine;
using UnityEngine.UIElements;

namespace StampFly.Ui
{
    /// <summary>
    /// The on-screen readout: what the vehicle is doing, how well the host is
    /// keeping up, and which keys do what. Built with UI Toolkit in code, so a
    /// change needs no asset to be edited in the editor.
    ///
    /// 画面の表示。機体が何をしているか、ホストがどれだけ追いつけているか、どの
    /// キーが何をするか。UI Toolkit をコードから組み立てるので、変更のために
    /// エディタで資産を編集する必要が無い。
    ///
    /// The real-time ratio and the cost of one tick are here because stage 3's
    /// pass criterion is stated in them: 60 fps at a ratio of 1.0, with the
    /// per-tick cost measured.
    ///
    /// 実時間比と 1 刻みの所要時間をここに出すのは、段階 3 の合格基準がそれで
    /// 述べられているからである。60fps で比 1.0、かつ 1 刻みの所要時間を計測済みで
    /// あること。
    ///
    /// @design docs/plans/unity-simulator.md §5 段階 3
    /// </summary>
    [RequireComponent(typeof(UIDocument))]
    public sealed class SimHud : MonoBehaviour
    {
        /// <summary>How often the readout is refreshed [s]. / 表示を更新する間隔 [s]。</summary>
        public const float RefreshSeconds = 0.1f;

        [Tooltip("The loop this reports on. 内容の元になるループ。")]
        public SimLoop simLoop;

        private Label flightLabel;
        private Label hostLabel;
        private float secondsSinceRefresh;

        private void OnEnable()
        {
            VisualElement root = GetComponent<UIDocument>().rootVisualElement;
            root.Clear();

            // The root is made to fill the panel before anything absolute is put
            // in it. An element positioned by `bottom` is placed against its
            // parent's resolved height, and a root left to size itself to its
            // contents has none to place against -- which is why the key hints
            // were being laid out below the visible area and never appeared on
            // screen. The top-left readout was unaffected because it is placed by
            // `top`, so it was the hints alone that went missing.
            // 絶対位置のものを入れる前に、根の要素を画面いっぱいにする。`bottom` で
            // 置く要素は親の確定した高さに対して置かれ、中身に合わせて自分の大きさを
            // 決める根にはその高さが無い。キーの案内が見える範囲の下に配置され、
            // 画面に出てこなかったのはこれによる。左上の表示は `top` で置くので影響を
            // 受けず、消えていたのは案内だけだった。
            root.style.position = Position.Absolute;
            root.style.left = 0;
            root.style.top = 0;
            root.style.right = 0;
            root.style.bottom = 0;

            root.Add(BuildPanel());
            root.Add(BuildHelp());
        }

        private void Update()
        {
            secondsSinceRefresh += Time.unscaledDeltaTime;
            bool isTooSoon = secondsSinceRefresh < RefreshSeconds;
            if (isTooSoon)
            {
                return;
            }

            secondsSinceRefresh = 0.0f;
            Refresh();
        }

        /// <summary>The two blocks of text, in a panel at the top left. / 左上の枠に入れた 2 つの文の塊。</summary>
        private VisualElement BuildPanel()
        {
            var panel = new VisualElement();
            panel.style.position = Position.Absolute;
            panel.style.left = 12;
            panel.style.top = 12;
            panel.style.paddingLeft = 10;
            panel.style.paddingRight = 10;
            panel.style.paddingTop = 8;
            panel.style.paddingBottom = 8;
            panel.style.backgroundColor = new Color(0.05f, 0.06f, 0.08f, 0.72f);

            flightLabel = MonospaceLabel(new Color(0.92f, 0.94f, 0.96f));
            hostLabel = MonospaceLabel(new Color(0.62f, 0.78f, 0.90f));
            panel.Add(flightLabel);
            panel.Add(hostLabel);
            return panel;
        }

        /// <summary>The key bindings, along the bottom. / 画面の下に置くキーの案内。</summary>
        private static VisualElement BuildHelp()
        {
            Label help = MonospaceLabel(new Color(0.72f, 0.74f, 0.78f));
            help.text =
                "W/S pitch   A/D roll   ,/. yaw   Space/Z throttle   " +
                "R arm   H alt-hold   -/+ deflection\n" +
                "P pause   N step   [ / ] speed   B power cycle   " +
                "Backspace return to spawn\n" +
                "wheel or F/G zoom   C default view";
            help.style.position = Position.Absolute;
            help.style.left = 12;

            // Clear of the bottom edge by more than one line. Measured at 1280x773
            // the third line left about 10 px under it, so a fourth line -- or a
            // shorter window -- would have run the hints off the screen.
            // 画面の下端から 1 行ぶんより多く離す。1280x773 で測ると 3 行目の下に
            // 10 px ほどしか残っておらず、4 行目が増えるか窓が低くなれば、案内が
            // 画面の外へ出てしまうところだった。
            help.style.bottom = 24;

            help.style.backgroundColor = new Color(0.05f, 0.06f, 0.08f, 0.62f);
            help.style.paddingLeft = 8;
            help.style.paddingRight = 8;
            help.style.paddingTop = 5;
            help.style.paddingBottom = 5;
            return help;
        }

        /// <summary>A label in a fixed-width face, so columns line up. / 桁が揃うよう等幅で置く文。</summary>
        private static Label MonospaceLabel(Color colour)
        {
            var label = new Label();
            label.style.color = colour;
            label.style.fontSize = 13;
            label.style.whiteSpace = WhiteSpace.Normal;
            label.style.unityFontStyleAndWeight = FontStyle.Normal;
            return label;
        }

        /// <summary>Rewrite both blocks from the loop's current state. / ループのいまの状態から 2 つの塊を書き直す。</summary>
        private void Refresh()
        {
            bool hasLoop = simLoop != null && flightLabel != null;
            if (!hasLoop)
            {
                return;
            }

            IFirmware firmware = simLoop.Firmware;
            bool isRunning = firmware != null && firmware.Status == FirmwareStatus.Running;
            if (!isRunning)
            {
                flightLabel.text = FirmwareStatusText(firmware);
                hostLabel.text = string.Empty;
                return;
            }

            flightLabel.text = FlightText();
            hostLabel.text = HostText();
        }

        /// <summary>What to show while the firmware is not yet flying. / ファームがまだ飛べない間に出す文。</summary>
        private static string FirmwareStatusText(IFirmware firmware)
        {
            bool hasFirmware = firmware != null;
            if (!hasFirmware)
            {
                return "firmware   (not created)";
            }

            bool hasFailed = firmware.Status == FirmwareStatus.Failed;
            if (hasFailed)
            {
                return $"firmware   FAILED\n{firmware.LastError}";
            }

            return $"firmware   {firmware.Status}...";
        }

        /// <summary>What the vehicle is doing. / 機体が何をしているか。</summary>
        private string FlightText()
        {
            SfuStepOut state = simLoop.LastResult;
            RangeReading range = simLoop.LastRange;
            var keyboard = simLoop.RcSource as KeyboardRc;

            // The FIRMWARE's own ARM, never anything the input holds. The input
            // holds no ARM state at all now — the flag is a button it presses —
            // so this is the only place the answer exists.
            // **ファーム自身の** ARM を出す。入力が保持しているものは決して出さない。
            // いまや入力は ARM の状態をまったく持たない（フラグは押すボタンである）ので、
            // 答えが在るのはここだけである。
            string armed = state.Armed != 0 ? "ARMED" : "disarmed";

            // While a press's pulse is still going out, say so. An arm the
            // firmware REFUSES — tilted, still calibrating, on USB power,
            // mid-pairing — is otherwise invisible: the vehicle simply does not
            // start and the pilot cannot tell a refusal from a lost keystroke.
            // This clears itself when the pulse ends, so a refusal shows as the
            // hint appearing and then going away with `disarmed` unchanged.
            // 押下のパルスがまだ出ている間はそれを示す。ファームが**拒否した** ARM ―
            // 傾き・校正中・USB 給電・ペアリング中 ― は、さもなくば見えない。機体はただ
            // 動き出さず、利用者は拒否と打鍵の取りこぼしを区別できない。パルスが終われば
            // 自然に消えるので、拒否は「案内が出て、`disarmed` のまま消える」ように見える。
            bool isPressing = keyboard != null &&
                              keyboard.IsArmPulseOn(simLoop.Clock.VirtualMicroseconds);
            if (isPressing)
            {
                armed += " (arming...)";
            }

            string stickHint = keyboard != null
                ? $"   stick {keyboard.Deflection:F0}"
                : string.Empty;
            string rangeText = range.IsValid
                ? $"{range.Distance:F3} m"
                : "(invalid)";

            return
                $"state      {FlightStateNames.State(state.FlightState)}  " +
                $"{FlightStateNames.Mode(state.FlightMode)}  {armed}{stickHint}\n" +
                $"altitude   {state.TruthPositionY:F3} m   " +
                $"tof {rangeText}   flow {range.FlowQuality:F2}\n" +
                $"battery    {state.BatteryVoltage:F2} V   " +
                $"sim {simLoop.Clock.VirtualSeconds:F1} s";
        }

        /// <summary>How well the host is keeping up. / ホストがどれだけ追いつけているか。</summary>
        private string HostText()
        {
            SimClock clock = simLoop.Clock;
            string pacing = clock.IsPaused ? "PAUSED" : $"x{clock.Speed:F2}";
            string behind = clock.IsBehind ? "  BEHIND" : string.Empty;

            return
                $"real-time  {clock.RealTimeRatio:F3}   " +
                $"{clock.MicrosecondsPerTick:F1} us/tick   " +
                $"{clock.FramesPerSecond:F0} fps   {pacing}{behind}";
        }
    }
}
