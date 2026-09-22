/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 */
using System;
using UnityEngine;
using UnityEngine.UIElements;

namespace StampFly.Ui
{
    /// <summary>
    /// A spring-centred stick owned by one pointer, so two thumbs remain independent.
    /// 1 本の指が操作する中央復帰式スティック。左右の親指を独立して扱う。
    /// @design docs/plans/unity-simulator.md §4 操縦入力 [OK]
    /// </summary>
    public sealed class TouchStick : VisualElement
    {
        private const int NoPointer = -1;
        private const float KnobSize = 48;
        private readonly VisualElement knob;
        private readonly Action<Vector2> changed;
        private int pointer = NoPointer;

        public Vector2 Value { get; private set; }

        /// <summary>Build the pad and capture each gesture. / 操作面を作り、指の操作を捕捉する。</summary>
        public TouchStick(string title, Action<Vector2> changed)
        {
            this.changed = changed;
            name = title;
            style.width = 160;
            style.height = 160;
            style.backgroundColor = new Color(0.04f, 0.08f, 0.12f, 0.76f);
            style.borderTopLeftRadius = style.borderTopRightRadius = 80;
            style.borderBottomLeftRadius = style.borderBottomRightRadius = 80;
            var label = new Label(title) { pickingMode = PickingMode.Ignore };
            label.style.color = new Color(0.8f, 0.9f, 1);
            label.style.fontSize = 12;
            label.style.unityTextAlign = TextAnchor.MiddleCenter;
            label.style.marginTop = 14;
            Add(label);
            knob = new VisualElement { pickingMode = PickingMode.Ignore };
            knob.style.position = Position.Absolute;
            knob.style.width = knob.style.height = KnobSize;
            knob.style.backgroundColor = new Color(0.35f, 0.77f, 0.94f, 0.9f);
            knob.style.borderTopLeftRadius = knob.style.borderTopRightRadius = KnobSize / 2;
            knob.style.borderBottomLeftRadius = knob.style.borderBottomRightRadius = KnobSize / 2;
            Add(knob);
            RegisterCallback<PointerDownEvent>(OnDown);
            RegisterCallback<PointerMoveEvent>(OnMove);
            RegisterCallback<PointerUpEvent>(evt => EndDrag(evt.pointerId));
            RegisterCallback<PointerCancelEvent>(evt => EndDrag(evt.pointerId));
            RegisterCallback<PointerCaptureOutEvent>(evt => EndDrag(evt.pointerId));
            RegisterCallback<DetachFromPanelEvent>(_ => Cancel());
            RegisterCallback<GeometryChangedEvent>(_ => Cancel());
        }

        /// <summary>Take only an unclaimed primary press. / 操作中でないときだけ押下を受け付ける。</summary>
        private void OnDown(PointerDownEvent evt)
        {
            bool isAvailable = pointer == NoPointer && evt.button == 0;
            if (!isAvailable) return;
            pointer = evt.pointerId;
            this.CapturePointer(pointer);
            MoveStick(evt.localPosition);
            evt.StopPropagation();
        }

        /// <summary>Keep following the owner outside the pad. / 操作面の外でも同じ指を追跡する。</summary>
        private void OnMove(PointerMoveEvent evt)
        {
            bool isOwner = evt.pointerId == pointer;
            if (!isOwner) return;
            MoveStick(evt.localPosition);
            evt.StopPropagation();
        }

        /// <summary>Map screen-down Y to forward/up input. / 画面下向きのYを前進・上昇へ反転する。</summary>
        private void MoveStick(Vector2 position)
        {
            Vector2 centre = contentRect.center;
            float radius = Mathf.Max(1, (Mathf.Min(contentRect.width, contentRect.height) - KnobSize) / 2);
            Vector2 delta = Vector2.ClampMagnitude((position - centre) / radius, 1);
            Value = new Vector2(delta.x, -delta.y);
            PlaceKnob(delta * radius);
            changed(Value);
        }

        /// <summary>Releasing one finger must not release the other. / 片方の指の終了で他方を解除しない。</summary>
        private void EndDrag(int pointerId)
        {
            bool isOwner = pointer == pointerId;
            if (!isOwner) return;
            Cancel();
        }

        /// <summary>Clear ownership before releasing capture to avoid recursion. / 再入を避け、捕捉解除の前に所有を消す。</summary>
        public void Cancel()
        {
            int previous = pointer;
            pointer = NoPointer;
            bool hadPointer = previous != NoPointer;
            if (hadPointer) this.ReleasePointer(previous);
            Value = Vector2.zero;
            PlaceKnob(Vector2.zero);
            changed(Value);
        }

        /// <summary>Position the knob without changing the pad geometry. / 操作面の寸法を変えず、つまみを配置する。</summary>
        private void PlaceKnob(Vector2 delta)
        {
            knob.style.left = contentRect.width / 2 - KnobSize / 2 + delta.x;
            knob.style.top = contentRect.height / 2 - KnobSize / 2 + delta.y;
        }
    }
}
