/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kei Nakayama (kexi)
 */
using NUnit.Framework;
using StampFly.App;
using StampFly.Sim;
using StampFly.Ui;
using StampFly.World;
using UnityEngine;
using UnityEngine.UIElements;

namespace StampFly.Tests.EditMode
{
    /// <summary>Furniture editing isolates flight controls and retains saved geometry. / 家具編集が操縦を分離し保存した形状を維持することを保証する。</summary>
    public sealed class FurnitureEditorTest
    {
        private GameObject host;
        private WorldLoader world;
        private SimLoop loop;
        private SimControls controls;
        private SimHud hud;
        private Camera camera;
        private FurnitureEditor editor;
        private PanelSettings settings;
        private string previousSave;
        private bool hadSave;

        /// <summary>Create a world and UI without starting native firmware. / ネイティブのファームを開始せず空間とUIを作る。</summary>
        [SetUp]
        public void SetUp()
        {
            hadSave = PlayerPrefs.HasKey(FurnitureEditor.SaveKey);
            previousSave = PlayerPrefs.GetString(FurnitureEditor.SaveKey);
            host = new GameObject("Furniture test");
            world = Child("World").AddComponent<WorldLoader>();
            Assert.That(world.Load(ShippedWorlds.Json("empty_room")), Is.True);
            var vehicle = Child("Vehicle");
            vehicle.AddComponent<Rigidbody>();
            loop = vehicle.AddComponent<SimLoop>();
            loop.enabled = false;
            controls = vehicle.AddComponent<SimControls>();
            var cameraObject = Child("Camera");
            camera = cameraObject.AddComponent<Camera>();
            var follow = cameraObject.AddComponent<FollowCamera>();
            cameraObject.AddComponent<ChaseCameraControls>();
            settings = PanelSettingsFactory.Create();
            Assert.That(settings.themeStyleSheet, Is.Not.Null,
                "The runtime theme must be embedded for readable dropdowns in WebGL.");
            var hudObject = Child("Hud");
            hudObject.AddComponent<UIDocument>().panelSettings = settings;
            hud = hudObject.AddComponent<SimHud>();
            hud.simLoop = loop;
            var editorObject = Child("Editor");
            editorObject.AddComponent<UIDocument>().panelSettings = settings;
            editor = editorObject.AddComponent<FurnitureEditor>();
            editor.Initialize(world, loop, hud, follow);
        }

        /// <summary>Restore the user's save slot even after a failed assertion. / 判定が失敗しても利用者の保存枠を復元する。</summary>
        [TearDown]
        public void TearDown()
        {
            Object.DestroyImmediate(editor.gameObject);
            Object.DestroyImmediate(host);
            Object.DestroyImmediate(settings);
            if (hadSave) PlayerPrefs.SetString(FurnitureEditor.SaveKey, previousSave);
            else PlayerPrefs.DeleteKey(FurnitureEditor.SaveKey);
            PlayerPrefs.Save();
        }

        /// <summary>Editing hides resume controls and disables keyboard / chase controls. / 編集中は再開ボタンを隠しキーボードと追尾カメラの操作を止める。</summary>
        [Test]
        public void OpeningPausesAndHidesEveryFlightAction()
        {
            loop.Clock.Resume();
            editor.Open();
            Assert.That(editor.IsEditing, Is.True);
            Assert.That(loop.Clock.IsPaused, Is.True);
            Assert.That(controls.enabled, Is.False);
            Assert.That(camera.enabled, Is.False);
            Assert.That(camera.GetComponent<ChaseCameraControls>().enabled, Is.False);
            Assert.That(hud.GetComponent<UIDocument>().rootVisualElement.style.display.value,
                        Is.EqualTo(DisplayStyle.None));
        }

        /// <summary>Disabling an open editor restores both initially running and paused states. / 編集画面の無効化で元の再生・停止状態を復元する。</summary>
        [TestCase(true)]
        [TestCase(false)]
        public void DisablingRestoresThePreviousClockAndControlStates(bool initiallyPaused)
        {
            if (initiallyPaused) loop.Clock.Pause();
            else loop.Clock.Resume();
            controls.enabled = false;
            editor.Open();
            editor.enabled = false;
            InvokeEditor("OnDisable");
            Assert.That(editor.IsEditing, Is.False);
            Assert.That(loop.Clock.IsPaused, Is.EqualTo(initiallyPaused));
            Assert.That(controls.enabled, Is.False, "A pre-disabled control must remain disabled.");
            Assert.That(camera.enabled, Is.True);
            Assert.That(hud.GetComponent<UIDocument>().rootVisualElement.style.display.value,
                        Is.EqualTo(DisplayStyle.Flex));
        }

        /// <summary>Save then load restores the furniture rather than the latest unsaved edit. / 保存後の未保存編集ではなく保存した家具を読み戻す。</summary>
        [Test]
        public void LoadingRestoresTheExplicitlySavedLayout()
        {
            using (var edits = new WorldEditSession(world))
            {
                Assert.That(edits.TryAdd("chair", new Vector3(2, 2, 0), out string id), Is.True, edits.LastError);
                editor.Save();
                Assert.That(edits.TryRemove(id), Is.True);
                Assert.That(world.Current.obstacles.Length, Is.EqualTo(0));
                editor.Load();
                Assert.That(world.Current.obstacles.Length, Is.EqualTo(1));
                Assert.That(world.Current.obstacles[0].id, Is.EqualTo(id));
                Assert.That(world.Current.obstacles[0].type, Is.EqualTo("chair"));
            }
        }

        /// <summary>Room surfaces never intercept furniture selection. / 部屋の面が家具の選択を遮らないことを保証する。</summary>
        [Test]
        public void SelectionPassesThroughCeilingAndIgnoresEmptyFloor()
        {
            using (var edits = new WorldEditSession(world))
            {
                Assert.That(edits.TryAdd("chair", new Vector3(2, 2, 0), out string id), Is.True);
                editor.Open();
                InvokeEditor("SelectAt", new Ray(new Vector3(2, 5, 2), Vector3.down));
                Assert.That(editor.SelectedId, Is.EqualTo(id));
                InvokeEditor("SelectAt", new Ray(new Vector3(-2, 5, -2), Vector3.down));
                Assert.That(editor.SelectedId, Is.Null);
            }
        }

        /// <summary>EditMode does not dispatch runtime lifecycle or pointer events. / EditModeでは実行時の状態通知やポインタ通知が配送されないため直接呼ぶ。</summary>
        private void InvokeEditor(string method, params object[] arguments)
        {
            typeof(FurnitureEditor).GetMethod(method,
                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
                .Invoke(editor, arguments);
        }

        /// <summary>Parent the test objects for deterministic cleanup. / 確実に後始末できるよう試験物体に親を付ける。</summary>
        private GameObject Child(string name)
        {
            var child = new GameObject(name);
            child.transform.SetParent(host.transform, false);
            return child;
        }
    }
}
