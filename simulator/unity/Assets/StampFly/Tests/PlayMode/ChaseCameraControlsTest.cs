/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — zooming by wheel and key).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Collections;
using NUnit.Framework;
using StampFly.Ui;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.TestTools;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// That the wheel and the three keys actually reach the zoom, driven through
    /// a virtual keyboard and mouse rather than through a browser.
    ///
    /// ホイールと 3 つのキーが本当にズームへ届くこと。ブラウザではなく、仮想の
    /// キーボードとマウスを通して動かして確かめる。
    ///
    /// This exists because the browser cannot answer the question. Unity's Input
    /// System reads the real device, so a key event synthesized into the page does
    /// not reach it (`simulator/unity/README.md`: keyboard piloting cannot be
    /// checked by automation), and an automated tab runs at a few frames a second,
    /// where a press and its release both land between two frames and
    /// `wasPressedThisFrame` never sees them. A virtual device queued into the
    /// Input System is seen exactly as a real one is.
    ///
    /// これがあるのは、ブラウザではこの問いに答えられないからである。Unity の
    /// Input System は実物の装置を読むので、ページへ合成したキーの事象は届かない
    /// （`simulator/unity/README.md`「キーボードでの操縦は自動操作では確かめられない」）。
    /// また自動操作のタブは毎秒数フレームで動き、押下と離しの両方が 2 つのフレームの
    /// 間に収まって `wasPressedThisFrame` に見えない。Input System へ積んだ仮想の
    /// 装置は、実物とまったく同じように見える。
    ///
    /// @design docs/plans/unity-simulator.md §5 段階 3
    /// </summary>
    public sealed class ChaseCameraControlsTest : InputTestFixture
    {
        /// <summary>
        /// How close two distances must be to count as equal [m].
        /// 2 つの距離が等しいと数えるための近さ [m]。
        /// </summary>
        private const float Tolerance = 1e-4f;

        private GameObject vehicleObject;
        private GameObject cameraObject;
        private FollowCamera follow;
        private Keyboard keyboard;
        private Mouse mouse;

        public override void Setup()
        {
            base.Setup();

            vehicleObject = new GameObject("Vehicle");

            cameraObject = new GameObject("ChaseCamera");
            follow = cameraObject.AddComponent<FollowCamera>();
            follow.target = vehicleObject.transform;
            follow.SnapToZoom();
            cameraObject.AddComponent<ChaseCameraControls>();

            keyboard = InputSystem.AddDevice<Keyboard>();
            mouse = InputSystem.AddDevice<Mouse>();
        }

        public override void TearDown()
        {
            Object.DestroyImmediate(cameraObject);
            Object.DestroyImmediate(vehicleObject);
            base.TearDown();
        }

        /// <summary>
        /// F moves one step in. The camera's own smoothing is not waited on: what
        /// is checked is that the press reached <see cref="ChaseZoom"/>, and where
        /// the camera then travels to is <c>CameraRemoteCommandsTest</c>'s.
        /// F で 1 段寄る。カメラ自身の滑らかな追従は待たない。ここで見るのは押下が
        /// <see cref="ChaseZoom"/> へ届いたことで、カメラがその後どこへ動くかは
        /// <c>CameraRemoteCommandsTest</c> のものである。
        /// </summary>
        [UnityTest]
        public IEnumerator PressingFMovesOneStepIn()
        {
            yield return null;
            float opened = follow.Zoom.DistanceMeters;

            yield return PressAndRelease(keyboard.fKey);

            Assert.That(follow.Zoom.DistanceMeters,
                        Is.EqualTo(opened / ChaseZoom.StepRatio).Within(Tolerance));
        }

        /// <summary>G moves one step out. / G で 1 段引く。</summary>
        [UnityTest]
        public IEnumerator PressingGMovesOneStepOut()
        {
            yield return null;
            float opened = follow.Zoom.DistanceMeters;

            yield return PressAndRelease(keyboard.gKey);

            Assert.That(follow.Zoom.DistanceMeters,
                        Is.EqualTo(opened * ChaseZoom.StepRatio).Within(Tolerance));
        }

        /// <summary>
        /// C returns to the opening distance from wherever the zoom has got to.
        /// C は、ズームがどこまで行っていても開いたときの距離へ戻す。
        /// </summary>
        [UnityTest]
        public IEnumerator PressingCReturnsToTheOpeningDistance()
        {
            yield return null;

            follow.Zoom.SetDistance(ChaseZoom.MaximumDistanceMeters);
            yield return PressAndRelease(keyboard.cKey);

            Assert.That(follow.Zoom.DistanceMeters,
                        Is.EqualTo(ChaseZoom.DefaultDistanceMeters).Within(Tolerance));
        }

        /// <summary>
        /// Holding F does not run the camera into the airframe: the press acts
        /// once, however many frames the key stays down.
        /// F を押し続けてもカメラが機体に突っ込まない。キーが何フレーム下がっていても
        /// 押下が働くのは 1 回である。
        /// </summary>
        [UnityTest]
        public IEnumerator HoldingFActsOnceRatherThanEveryFrame()
        {
            yield return null;
            float opened = follow.Zoom.DistanceMeters;

            Press(keyboard.fKey);
            for (int frame = 0; frame < 10; frame++)
            {
                yield return null;
            }
            Release(keyboard.fKey);
            yield return null;

            Assert.That(follow.Zoom.DistanceMeters,
                        Is.EqualTo(opened / ChaseZoom.StepRatio).Within(Tolerance),
                        "holding the key moved more than one step");
        }

        /// <summary>
        /// A wheel pushed forward moves in. The sign is the one measured in
        /// Chrome: Unity hands this side a POSITIVE scroll for a forward push,
        /// and a test with the sign the wrong way round is how an inverted wheel
        /// would reach a person.
        /// ホイールを前へ回すと寄る。符号は Chrome で実測したものである。前へ回すと
        /// Unity はこちら側へ**正**のスクロールを渡す。符号が逆の試験は、逆向きの
        /// ホイールが人の手元へ届く道筋そのものである。
        /// </summary>
        [UnityTest]
        public IEnumerator ScrollingForwardMovesIn()
        {
            yield return null;
            float opened = follow.Zoom.DistanceMeters;

            yield return Scroll(ChaseCameraControls.ScrollPerNotchInNotches);

            Assert.That(follow.Zoom.DistanceMeters,
                        Is.EqualTo(opened / ChaseZoom.StepRatio).Within(Tolerance));
        }

        /// <summary>And back moves out. / 後ろへ回すと引く。</summary>
        [UnityTest]
        public IEnumerator ScrollingBackMovesOut()
        {
            yield return null;
            float opened = follow.Zoom.DistanceMeters;

            yield return Scroll(-ChaseCameraControls.ScrollPerNotchInNotches);

            Assert.That(follow.Zoom.DistanceMeters,
                        Is.EqualTo(opened * ChaseZoom.StepRatio).Within(Tolerance));
        }

        /// <summary>
        /// A reading in the units a desktop player uses moves the same one step,
        /// so the same build behaves the same way in the editor and in the page.
        /// デスクトップのプレイヤーが使う単位の値も同じ 1 段を動かす。同じビルドが
        /// エディタとページで同じように振る舞うためである。
        /// </summary>
        [UnityTest]
        public IEnumerator AReadingInWindowsUnitsMovesOneStepToo()
        {
            yield return null;
            float opened = follow.Zoom.DistanceMeters;

            yield return Scroll(ChaseCameraControls.ScrollPerNotchInWindowsUnits);

            Assert.That(follow.Zoom.DistanceMeters,
                        Is.EqualTo(opened / ChaseZoom.StepRatio).Within(Tolerance));
        }

        /// <summary>
        /// One frame's scroll never moves more than the ceiling, however hard a
        /// trackpad is flicked.
        /// トラックパッドをどれだけ強くはじいても、1 フレームのスクロールが動かすのは
        /// 上限までである。
        /// </summary>
        [UnityTest]
        public IEnumerator OneFramesScrollNeverMovesMoreThanTheCeiling()
        {
            yield return null;
            float opened = follow.Zoom.DistanceMeters;

            yield return Scroll(9999.0f);

            float ceiling =
                opened / Mathf.Pow(ChaseZoom.StepRatio,
                                   ChaseCameraControls.MaximumStepsPerFrame);
            Assert.That(follow.Zoom.DistanceMeters,
                        Is.EqualTo(ChaseZoom.Clamp(ceiling)).Within(Tolerance));
        }

        /// <summary>
        /// Zooming works while the simulation is paused, which is when somebody
        /// wants a closer look. <see cref="Time.timeScale"/> at zero is what a
        /// pause looks like to everything reading the clock, and the controls run
        /// on <c>Update</c> with unscaled time, so they keep working.
        /// シミュレーションを一時停止している間も寄せられる。よく見たいのはそのときで
        /// ある。時計を読むものにとって一時停止は <see cref="Time.timeScale"/> が 0 に
        /// なることで、この操作は <c>Update</c> と実時間で動くので効き続ける。
        /// </summary>
        [UnityTest]
        public IEnumerator ZoomingWorksWhileTheSimulationIsPaused()
        {
            yield return null;
            float opened = follow.Zoom.DistanceMeters;
            float wasTimeScale = Time.timeScale;

            try
            {
                Time.timeScale = 0.0f;
                yield return PressAndRelease(keyboard.fKey);

                Assert.That(follow.Zoom.DistanceMeters,
                            Is.EqualTo(opened / ChaseZoom.StepRatio).Within(Tolerance));
            }
            finally
            {
                Time.timeScale = wasTimeScale;
            }
        }

        /// <summary>
        /// One press, held over a frame so <c>wasPressedThisFrame</c> sees it and
        /// released before the next check.
        /// 押下 1 回。<c>wasPressedThisFrame</c> に見えるよう 1 フレームまたいで保ち、
        /// 次の確認の前に離す。
        /// </summary>
        private IEnumerator PressAndRelease(UnityEngine.InputSystem.Controls.KeyControl key)
        {
            Press(key);
            yield return null;
            Release(key);
            yield return null;
        }

        /// <summary>
        /// One frame's worth of scroll. The value is set, a frame is allowed to
        /// read it, and the Input System returns the axis to zero of its own
        /// accord -- a scroll is a delta, not a held position.
        /// 1 フレームぶんのスクロール。値を置き、フレームに読ませる。軸が 0 へ戻るのは
        /// Input System 自身が行う。スクロールは保持される位置ではなく差分である。
        /// </summary>
        private IEnumerator Scroll(float amount)
        {
            Set(mouse.scroll, new Vector2(0.0f, amount));
            yield return null;
            yield return null;
        }
    }
}
