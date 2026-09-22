/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 */
using System;
using UnityEngine;
using UnityEngine.UIElements;

namespace StampFly.Ui
{
    /// <summary>
    /// Two thumbs and four flight controls, arranged for a phone in either orientation.
    /// 縦横どちらのスマホ画面でも使える左右スティックと4つの操作ボタン。
    /// @design docs/plans/unity-simulator.md §4 操縦入力 [OK]
    /// </summary>
    public sealed class TouchFlightControls : VisualElement
    {
        private const float PortraitWidth = 600;
        private readonly TouchStick leftStick;
        private readonly TouchStick rightStick;
        private readonly VisualElement actions;
        public Button Arm { get; }
        public Button AltitudeHold { get; }
        public Button Pause { get; }
        public Button Restart { get; }

        /// <summary>Wire UI actions to the same controls as the keyboard. / キーボードと同じ処理にUIをつなぐ。</summary>
        public TouchFlightControls(Action<Vector2> left, Action<Vector2> right,
                                   Action arm, Action altitude, Action pause, Action restart)
        {
            name = "touch-flight-controls";
            pickingMode = PickingMode.Ignore;
            style.position = Position.Absolute;
            style.left = style.right = 12;
            style.bottom = 12;
            style.height = 260;
            leftStick = new TouchStick("THROTTLE / YAW", left);
            rightStick = new TouchStick("PITCH / ROLL", right);
            leftStick.style.position = rightStick.style.position = Position.Absolute;
            leftStick.style.bottom = rightStick.style.bottom = 0;
            leftStick.style.left = rightStick.style.right = 0;
            Add(leftStick);
            Add(rightStick);
            actions = new VisualElement();
            actions.style.position = Position.Absolute;
            actions.style.flexDirection = FlexDirection.Row;
            actions.style.flexWrap = Wrap.Wrap;
            actions.style.justifyContent = Justify.Center;
            Arm = ActionButton("ARM", arm);
            AltitudeHold = ActionButton("ALT HOLD", altitude);
            Pause = ActionButton("PAUSE", pause);
            Restart = ActionButton("RESTART", restart);
            actions.Add(Arm);
            actions.Add(AltitudeHold);
            actions.Add(Pause);
            actions.Add(Restart);
            Add(actions);
            RegisterCallback<GeometryChangedEvent>(_ => Arrange());
        }

        /// <summary>Keep a 48-pixel target even on a small screen. / 小さな画面でも48ピクセルの操作領域を確保する。</summary>
        public static Button ActionButton(string text, Action action)
        {
            var button = new Button(action) { text = text, focusable = false };
            button.style.minHeight = 48;
            button.style.minWidth = 48;
            button.style.fontSize = 13;
            button.style.marginLeft = button.style.marginRight = 3;
            button.style.marginTop = button.style.marginBottom = 3;
            button.style.backgroundColor = new Color(0.08f, 0.13f, 0.19f, 0.94f);
            button.style.color = new Color(0.9f, 0.95f, 1);
            button.style.alignItems = Align.Center;
            button.style.justifyContent = Justify.Center;
            return button;
        }

        /// <summary>Place actions between sticks in landscape, above them in portrait. / 横持ちは中央、縦持ちはスティックの上にボタンを置く。</summary>
        private void Arrange()
        {
            bool isPortrait = contentRect.width < PortraitWidth;
            float size = Mathf.Min(160, (contentRect.width - 24) / 2);
            leftStick.style.width = leftStick.style.height = size;
            rightStick.style.width = rightStick.style.height = size;
            actions.style.left = actions.style.right = isPortrait ? 0 : size + 12;
            actions.style.bottom = isPortrait ? size + 12 : 24;
            foreach (VisualElement button in actions.Children())
                button.style.width = Length.Percent(isPortrait ? 22 : 44);
        }

        /// <summary>Also release pointer capture, not only the numeric input. / 数値だけでなく指の捕捉も解除する。</summary>
        public void Cancel()
        {
            leftStick.Cancel();
            rightStick.Cancel();
        }

        /// <summary>Paused or remotely controlled flights cannot retain local gestures. / 停止中・遠隔操作中は指の入力を保持しない。</summary>
        public void SetPilotingEnabled(bool enabled)
        {
            leftStick.SetEnabled(enabled);
            rightStick.SetEnabled(enabled);
        }
    }
}
