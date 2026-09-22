/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — touch stick state).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine;

namespace StampFly.Input
{
    /// <summary>
    /// Normalised mode 2 sticks: left yaw/throttle, right roll/pitch; positive is right/up.
    /// 正規化したモード 2 スティック。左はヨー/スロットル、右はロール/ピッチ。右/上を正とする。
    /// </summary>
    public sealed class TouchRcState
    {
        private bool enabled;

        /// <summary>Disabling touch also releases both sticks. / タッチを無効にすると両スティックも中央に戻す。</summary>
        public bool Enabled
        {
            get => enabled;
            set
            {
                enabled = value;
                bool isDisabled = !enabled;
                if (isDisabled)
                {
                    Clear();
                }
            }
        }

        /// <summary>Left stick, yaw and throttle. / 左スティック。ヨーとスロットル。</summary>
        public Vector2 Left { get; private set; }

        /// <summary>Right stick, roll and pitch. / 右スティック。ロールとピッチ。</summary>
        public Vector2 Right { get; private set; }

        /// <summary>Limit each left axis independently to its travel. / 左の各軸を独立に可動範囲へ収める。</summary>
        public void SetLeft(Vector2 value)
        {
            Left = Clamp(value);
        }

        /// <summary>Limit each right axis independently to its travel. / 右の各軸を独立に可動範囲へ収める。</summary>
        public void SetRight(Vector2 value)
        {
            Right = Clamp(value);
        }

        /// <summary>Release both sticks without changing input selection. / 入力の選択を変えずに両スティックを中央へ戻す。</summary>
        public void Clear()
        {
            Left = Vector2.zero;
            Right = Vector2.zero;
        }

        /// <summary>Keep diagonal travel available on both axes. / 斜め操作でも両軸の可動範囲を保つ。</summary>
        private static Vector2 Clamp(Vector2 value)
        {
            return new Vector2(Mathf.Clamp(value.x, -1f, 1f), Mathf.Clamp(value.y, -1f, 1f));
        }
    }
}
