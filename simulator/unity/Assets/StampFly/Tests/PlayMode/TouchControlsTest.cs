/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 */
using System.Collections;
using NUnit.Framework;
using StampFly.Input;
using StampFly.Sim;
using StampFly.Ui;
using StampFly.World;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// Exercise actual UI event callbacks, including two independently captured fingers.
    /// 実際のUIイベント経由で、独立して捕捉された2本の指を含む操作を検証する。
    /// </summary>
    public sealed class TouchControlsTest : InputTestFixture
    {
        private GameObject host;
        private GameObject vehicle;
        private PanelSettings settings;
        private RenderTexture texture;
        private UIDocument document;
        private WorldCatalog roomCatalog;
        private TextAsset roomJson;

        /// <summary>Give events a real runtime panel with fixed dimensions. / 一定寸法の実行時パネルでイベントを送る。</summary>
        public override void Setup()
        {
            base.Setup();
            settings = ScriptableObject.CreateInstance<PanelSettings>();
            settings.themeStyleSheet = Resources.Load<ThemeStyleSheet>("UnityThemes/UnityDefaultRuntimeTheme");
            texture = new RenderTexture(900, 500, 0);
            settings.targetTexture = texture;
            settings.scaleMode = PanelScaleMode.ConstantPixelSize;
            host = new GameObject("Touch UI test");
            document = host.AddComponent<UIDocument>();
            document.panelSettings = settings;
        }

        /// <summary>Remove the panel and restore the Input System. / パネルを除去しInput Systemを復元する。</summary>
        public override void TearDown()
        {
            Object.DestroyImmediate(host);
            Object.DestroyImmediate(vehicle);
            Object.DestroyImmediate(settings);
            Object.DestroyImmediate(texture);
            Object.DestroyImmediate(roomCatalog);
            Object.DestroyImmediate(roomJson);
            base.TearDown();
        }

        /// <summary>One finger cannot move or release the other stick. / 一方の指は他方のスティックを動かしたり解除したりしない。</summary>
        [UnityTest]
        public IEnumerator TwoFingersMoveIndependentlyAndReleaseOnlyTheirOwnStick()
        {
            var source = new KeyboardRc();
            TouchFlightControls controls = AddControls(source);
            yield return null;
            yield return null;
            TouchStick left = controls.Q<TouchStick>("THROTTLE / YAW");
            TouchStick right = controls.Q<TouchStick>("PITCH / ROLL");

            Down(left, 0, new Vector2(24, -18));
            Down(right, 1, new Vector2(-18, 24));
            Assert.That(source.Touch.Left.x, Is.GreaterThan(0));
            Assert.That(source.Touch.Left.y, Is.GreaterThan(0));
            Assert.That(source.Touch.Right.x, Is.LessThan(0));
            Assert.That(source.Touch.Right.y, Is.LessThan(0));
            Vector2 rightBefore = source.Touch.Right;

            Move(left, 2, new Vector2(-40, 40));
            Assert.That(source.Touch.Left.x, Is.GreaterThan(0), "another finger moved the left stick");
            Up(left, 0);
            Assert.That(source.Touch.Left, Is.EqualTo(Vector2.zero));
            Assert.That(source.Touch.Right, Is.EqualTo(rightBefore));
            Up(right, 1);
            Assert.That(source.Touch.Right, Is.EqualTo(Vector2.zero));
        }

        /// <summary>Cancel, capture loss and detach each release an active gesture. / キャンセル・捕捉喪失・パネル離脱で操作中の入力を解除する。</summary>
        [UnityTest]
        public IEnumerator CancelCaptureLossAndDetachEachReturnTheStickToCentre()
        {
            var source = new KeyboardRc();
            TouchFlightControls controls = AddControls(source);
            yield return null;
            yield return null;
            TouchStick left = controls.Q<TouchStick>("THROTTLE / YAW");
            Down(left, 0, new Vector2(25, -25));
            using (var cancel = PointerCancelEvent.GetPooled(TouchAt(left, 0, Vector2.zero, UnityEngine.TouchPhase.Canceled), EventModifiers.None))
            {
                cancel.target = left;
                left.SendEvent(cancel);
            }
            Assert.That(source.Touch.Left, Is.EqualTo(Vector2.zero));

            bool receivedCapture = false;
            bool receivedCaptureLoss = false;
            left.RegisterCallback<PointerCaptureEvent>(_ => receivedCapture = true);
            left.RegisterCallback<PointerCaptureOutEvent>(_ => receivedCaptureLoss = true);
            int pointer = Down(left, 0, new Vector2(25, -25));
            Assert.That(left.HasPointerCapture(pointer), Is.True);
            Assert.That(receivedCapture, Is.True, "the initial capture event was not dispatched");
            var thief = new VisualElement();
            document.rootVisualElement.Add(thief);
            thief.CapturePointer(pointer);

            // Capture requests are committed during pointer-event post-dispatch,
            // so waiting a frame alone does not emit a capture-loss event.
            // 捕捉要求はポインタイベントの配信後に確定するため、フレーム待機だけでは捕捉喪失が通知されない。
            Move(thief, 0, Vector2.zero);
            Assert.That(thief.HasPointerCapture(pointer), Is.True);
            Assert.That(receivedCaptureLoss, Is.True, "capture loss was not dispatched");
            Assert.That(source.Touch.Left, Is.EqualTo(Vector2.zero), "capture loss retained input");
            thief.ReleasePointer(pointer);
            Move(thief, 0, Vector2.zero);

            Down(left, 0, new Vector2(25, -25));
            left.RemoveFromHierarchy();
            Assert.That(source.Touch.Left, Is.EqualTo(Vector2.zero), "detaching retained input");
        }

        /// <summary>Button submit events reach all four supplied actions. / ボタンの実行イベントが4種類すべての処理に届く。</summary>
        [UnityTest]
        public IEnumerator FlightButtonsDispatchTheirAssignedActions()
        {
            var counts = new int[4];
            var controls = new TouchFlightControls(_ => { }, _ => { },
                () => counts[0]++, () => counts[1]++, () => counts[2]++, () => counts[3]++);
            document.rootVisualElement.Add(controls);
            yield return null;
            Submit(controls.Arm);
            Submit(controls.AltitudeHold);
            Submit(controls.Pause);
            Submit(controls.Restart);
            Assert.That(counts, Is.EqualTo(new[] { 1, 1, 1, 1 }));
        }

        /// <summary>Toolbar links are icon-only and retain destination hints. / ツールバーのリンクはアイコンのみで行き先の案内を持つ。</summary>
        [UnityTest]
        public IEnumerator HudShowsThreeIconLinksWithDestinationHints()
        {
            host.AddComponent<SimHud>();
            yield return null;
            foreach (string name in new[] { "repository-link", "upstream-link", "license-link" })
            {
                Button button = document.rootVisualElement.Q<Button>(name);
                Assert.That(button, Is.Not.Null, name);
                Assert.That(button.text, Is.Empty, name);
                Assert.That(button.tooltip, Is.Not.Empty, name);
                Assert.That(button.Q<LinkIcon>(), Is.Not.Null, name);
            }
        }

        /// <summary>HUD events switch input and pause without retaining a held thumb or ARM pulse. / HUD操作で入力切替・一時停止した後に指の入力やARMパルスを残さない。</summary>
        [UnityTest]
        public IEnumerator HudTogglePauseAndDisableClearActiveTouchWithoutAKeyboard()
        {
            Assert.That(Keyboard.current, Is.Null);
            vehicle = new GameObject("Touch UI loop");
            vehicle.AddComponent<Rigidbody>().isKinematic = true;
            SimLoop loop = vehicle.AddComponent<SimLoop>();
            loop.enabled = false;
            var source = (KeyboardRc)loop.RcSource;
            SimHud hud = host.AddComponent<SimHud>();
            hud.simLoop = loop;
            yield return null;
            Button toggle = document.rootVisualElement.Query<Button>().ToList().Find(button => button.text == "Touch");
            Assert.That(toggle, Is.Not.Null);
            Submit(toggle);
            yield return null;
            yield return null;
            Assert.That(source.Touch.Enabled, Is.True);
            TouchFlightControls controls = document.rootVisualElement.Q<TouchFlightControls>();
            TouchStick left = controls.Q<TouchStick>("THROTTLE / YAW");
            Assert.That(left.enabledInHierarchy, Is.False, "unloaded firmware allowed piloting");
            source.Touch.SetLeft(Vector2.one);
            source.PressAltitudeHold();
            source.PressArm();

            Submit(controls.Pause);
            Assert.That(loop.Clock.IsPaused, Is.True);
            Assert.That(source.Touch.Left, Is.EqualTo(Vector2.zero));
            Assert.That(source.FlagsAt(source.Read(), 0L), Is.EqualTo(StampFly.Native.SfuAbi.FlagAltitudeMode));
            Submit(controls.Pause);
            Assert.That(loop.Clock.IsPaused, Is.False);
            source.Touch.SetLeft(Vector2.one);
            Submit(toggle);
            Assert.That(source.Touch.Enabled, Is.False);
            Assert.That(source.Touch.Left, Is.EqualTo(Vector2.zero));
            Submit(toggle);
            yield return null;
            source.Touch.SetLeft(Vector2.one);
            loop.Clock.TogglePause();
            yield return null;
            Assert.That(source.Touch.Left, Is.EqualTo(Vector2.zero), "external pause retained input");
            source.Touch.SetLeft(Vector2.one);
            hud.enabled = false;
            Assert.That(source.Touch.Left, Is.EqualTo(Vector2.zero));
        }

        /// <summary>Use the same stick-to-input wiring as the HUD. / HUDと同じスティック入力の接続を用意する。</summary>
        private TouchFlightControls AddControls(KeyboardRc source)
        {
            source.Touch.Enabled = true;
            var controls = new TouchFlightControls(source.Touch.SetLeft, source.Touch.SetRight,
                source.PressArm, source.PressAltitudeHold, () => { }, () => { });
            document.rootVisualElement.Add(controls);
            return controls;
        }

        /// <summary>
        /// Phone portrait and landscape keep controls inside the screen without overlaps.
        /// スマホの縦持ち・横持ちで操作領域が画面内に収まり、互いに重ならないこと。
        /// </summary>
        [UnityTest]
        public IEnumerator PhonePortraitAndLandscapeKeepControlsVisibleAndSeparate()
        {
            vehicle = new GameObject("Phone layout loop");
            vehicle.AddComponent<Rigidbody>().isKinematic = true;
            SimLoop loop = vehicle.AddComponent<SimLoop>();
            loop.enabled = false;
            SimHud hud = host.AddComponent<SimHud>();
            hud.simLoop = loop;
            var roomLoader = vehicle.AddComponent<WorldLoader>();
            roomCatalog = ScriptableObject.CreateInstance<WorldCatalog>();
            roomJson = new TextAsset(System.IO.File.ReadAllText("Assets/StampFly/Worlds/empty_room.world.json"));
            roomCatalog.SetEntries(new[] { new WorldCatalog.Entry { name = "empty_room", json = roomJson } });
            roomLoader.Catalog = roomCatalog;
            roomLoader.LoadByName("empty_room");
            hud.ConfigureRooms(roomLoader, () => { });
            yield return null;
            hud.SetTouchEnabled(true);

            foreach (Vector2Int size in new[] { new Vector2Int(390, 844), new Vector2Int(844, 390) })
            {
                var previous = texture;
                texture = new RenderTexture(size.x, size.y, 0);
                settings.targetTexture = texture;
                Object.DestroyImmediate(previous);
                yield return null;
                yield return null;
                yield return null;

                VisualElement root = document.rootVisualElement;
                Assert.That(root.worldBound.width, Is.EqualTo(size.x).Within(1), size.ToString());
                Assert.That(root.worldBound.height, Is.EqualTo(size.y).Within(1), size.ToString());
                var screen = new Rect(0, 0, size.x, size.y);
                TouchFlightControls controls = root.Q<TouchFlightControls>();
                var targets = root.Query<Button>().ToList().ConvertAll(button => (VisualElement)button);
                targets.Add(root.Q<RoomSelector>());
                targets.Add(controls.Q<TouchStick>("THROTTLE / YAW"));
                targets.Add(controls.Q<TouchStick>("PITCH / ROLL"));
                foreach (VisualElement target in targets)
                {
                    AssertInside(screen, target, size.ToString());
                    Assert.That(target.worldBound.width, Is.GreaterThanOrEqualTo(48), target.name);
                    Assert.That(target.worldBound.height, Is.GreaterThanOrEqualTo(48), target.name);
                }

                for (int first = 0; first < targets.Count; first++)
                {
                    for (int second = first + 1; second < targets.Count; second++)
                    {
                        Assert.That(targets[first].worldBound.Overlaps(targets[second].worldBound), Is.False,
                            $"{size}: controls overlap: {targets[first].name} / {targets[second].name}");
                    }
                }

                VisualElement readout = root.ElementAt(0);
                AssertInside(screen, readout, $"{size}: readout");
                foreach (VisualElement target in targets)
                {
                    // The furniture button belongs inside the status panel.
                    // 家具ボタンは状態表示の内部に配置する。
                    bool belongsToReadout = readout.Contains(target);
                    if (belongsToReadout)
                    {
                        AssertInside(readout.worldBound, target, $"{size}: status action");
                        foreach (Label label in readout.Query<Label>().ToList())
                        {
                            bool visible = label.resolvedStyle.display != DisplayStyle.None;
                            bool isTargetContent = target.Contains(label);
                            if (visible && !isTargetContent) Assert.That(label.worldBound.Overlaps(target.worldBound), Is.False);
                        }
                        continue;
                    }
                    Assert.That(readout.worldBound.Overlaps(target.worldBound), Is.False,
                        $"{size}: readout overlaps {target.name}");
                }
                foreach (Label label in readout.Query<Label>().ToList())
                {
                    bool isVisible = label.resolvedStyle.display != DisplayStyle.None;
                    if (isVisible) AssertInside(readout.worldBound, label, $"{size}: flight text");
                }
            }
        }

        /// <summary>Allow only subpixel layout rounding at a containing edge. / 境界では小数ピクセルの丸めだけを許す。</summary>
        private static void AssertInside(Rect outer, VisualElement element, string context)
        {
            Rect inner = element.worldBound;
            const float tolerance = 0.5f;
            Assert.That(inner.width, Is.GreaterThan(0), context);
            Assert.That(inner.height, Is.GreaterThan(0), context);
            Assert.That(inner.xMin, Is.GreaterThanOrEqualTo(outer.xMin - tolerance), context);
            Assert.That(inner.yMin, Is.GreaterThanOrEqualTo(outer.yMin - tolerance), context);
            Assert.That(inner.xMax, Is.LessThanOrEqualTo(outer.xMax + tolerance), context);
            Assert.That(inner.yMax, Is.LessThanOrEqualTo(outer.yMax + tolerance), context);
        }

        /// <summary>Create a finger at panel coordinates. / パネル座標で指の位置を指定する。</summary>
        private static UnityEngine.Touch TouchAt(VisualElement element, int finger, Vector2 offset, UnityEngine.TouchPhase phase)
        {
            return new UnityEngine.Touch { fingerId = finger, position = element.worldBound.center + offset, phase = phase };
        }

        /// <summary>Dispatch a real pointer-down event and return its assigned pointer ID. / 押下イベントを配信してポインタIDを返す。</summary>
        private static int Down(VisualElement element, int finger, Vector2 offset)
        {
            using (var evt = PointerDownEvent.GetPooled(TouchAt(element, finger, offset, UnityEngine.TouchPhase.Began), EventModifiers.None))
            {
                evt.target = element;
                element.SendEvent(evt);
                return evt.pointerId;
            }
        }

        /// <summary>Dispatch finger movement through the registered event callback. / 登録済みイベント処理へ指の移動を配信する。</summary>
        private static void Move(VisualElement element, int finger, Vector2 offset)
        {
            using (var evt = PointerMoveEvent.GetPooled(TouchAt(element, finger, offset, UnityEngine.TouchPhase.Moved), EventModifiers.None))
            {
                evt.target = element;
                element.SendEvent(evt);
            }
        }

        /// <summary>Dispatch release for one finger. / 1本の指の解放を配信する。</summary>
        private static void Up(VisualElement element, int finger)
        {
            using (var evt = PointerUpEvent.GetPooled(TouchAt(element, finger, Vector2.zero, UnityEngine.TouchPhase.Ended), EventModifiers.None))
            {
                evt.target = element;
                element.SendEvent(evt);
            }
        }

        /// <summary>Exercise the Button's submit event binding without opening external URLs. / 外部URLを開かずButtonの実行イベント接続を検証する。</summary>
        private static void Submit(Button button)
        {
            using (var evt = NavigationSubmitEvent.GetPooled())
            {
                evt.target = button;
                button.SendEvent(evt);
            }
        }
    }
}
