/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the camera's command surface).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Collections;
using NUnit.Framework;
using StampFly.App;
using StampFly.Core;
using StampFly.Remote;
using StampFly.Ui;
using UnityEngine;
using UnityEngine.TestTools;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// What `camera.zoom` and `camera.state` answer, and that the camera really
    /// moves to the distance asked for -- checked against a real
    /// <see cref="RemoteBridge"/> and a real <see cref="FollowCamera"/> in a
    /// scene built for the test.
    ///
    /// `camera.zoom` と `camera.state` が何を答えるか、そしてカメラが本当に求めた
    /// 距離へ動くか。試験のために組んだ場面の中で、実物の
    /// <see cref="RemoteBridge"/> と実物の <see cref="FollowCamera"/> に対して
    /// 確かめる。
    ///
    /// The arithmetic of the zoom is <c>ChaseZoomTest</c>'s, which needs no
    /// scene. What is checked here is what only a running scene can show: that
    /// the command surface is registered, that the answers carry the keys a
    /// script reads, and that a frame later the camera is actually where it was
    /// told to be.
    ///
    /// ズームの算術は <c>ChaseZoomTest</c> のもので、そちらは場面を要しない。ここで
    /// 見るのは、動いている場面でしか分からないことである。命令の受け口が登録されて
    /// いること、答えが台本の読む鍵を持つこと、そしてフレームが進んだ後にカメラが
    /// 実際に言われた場所にいることである。
    ///
    /// @design docs/plans/unity-simulator.md §5 段階 3
    /// </summary>
    public sealed class CameraRemoteCommandsTest
    {
        /// <summary>
        /// Real seconds allowed for the camera to close the gap to a new
        /// distance. The zoom closes most of a change in
        /// <see cref="FollowCamera.ZoomSmoothingSeconds"/>, so ten times that is
        /// far more than enough while still ending a test that has stopped
        /// moving.
        ///
        /// Waited out in SECONDS rather than in frames. The test runner renders
        /// frames as fast as it can -- a hundredth of the wall-clock time of a
        /// real one -- and the zoom closes on
        /// <see cref="Time.unscaledDeltaTime"/>, so a frame budget that looks
        /// generous (300 frames) covers only a few hundredths of a second here
        /// and the camera is legitimately still on its way.
        ///
        /// カメラが新しい距離までのずれを詰めるのに許す実時間の秒数。ズームは変化の
        /// 大半を <see cref="FollowCamera.ZoomSmoothingSeconds"/> で詰めるので、
        /// その 10 倍あれば十分に余り、なお動きが止まった試験は終わる。
        ///
        /// フレーム数ではなく**秒**で待つ。試験の実行側はできる限り速くフレームを
        /// 描き、実際のフレームの実時間の 100 分の 1 になる。ズームは
        /// <see cref="Time.unscaledDeltaTime"/> で詰めるので、余裕があるように見える
        /// フレーム数（300 フレーム）はここでは数百分の 1 秒しか覆わず、カメラは
        /// 正しくまだ途中にいる。
        /// </summary>
        private const float MaxSecondsToClose =
            FollowCamera.ZoomSmoothingSeconds * 10.0f;

        /// <summary>
        /// How close the camera must get to the distance asked for [m]. A
        /// millimetre: the smoothing is exponential and never arrives exactly.
        /// カメラが求めた距離にどれだけ近づかねばならないか [m]。1 ミリメートル。
        /// 滑らかな追従は指数的で、ぴったりには到達しない。
        /// </summary>
        private const float ArrivalTolerance = 1e-3f;

        private GameObject bridgeObject;
        private GameObject vehicleObject;
        private GameObject cameraObject;
        private FollowCamera follow;

        [SetUp]
        public void SetUp()
        {
            bridgeObject = new GameObject("StampFlyBridge");
            bridgeObject.AddComponent<RemoteBridge>();

            // A plain transform is enough to be followed: the camera reads the
            // target's position and forward, and nothing here needs a rigid body
            // or the firmware.
            // 追う対象は素の transform で足りる。カメラが読むのは対象の位置と前方
            // だけで、ここでは剛体もファームも要らない。
            vehicleObject = new GameObject("Vehicle");

            cameraObject = new GameObject("ChaseCamera");
            follow = cameraObject.AddComponent<FollowCamera>();
            follow.target = vehicleObject.transform;
            follow.SnapToZoom();
            cameraObject.AddComponent<CameraRemoteCommands>();
        }

        [TearDown]
        public void TearDown()
        {
            Object.DestroyImmediate(cameraObject);
            Object.DestroyImmediate(vehicleObject);
            Object.DestroyImmediate(bridgeObject);
        }

        /// <summary>The registry, once every Start has run. / 全ての Start が走った後の登録簿。</summary>
        private SimCommandRegistry Commands => RemoteBridge.Instance.Commands;

        /// <summary>
        /// The two names the documentation states. A command renamed without the
        /// documentation following is the failure this catches.
        /// 文書が述べる 2 つの名前。文書が追従せずに改名された命令を、これが捕まえる。
        /// </summary>
        [UnityTest]
        public IEnumerator BothCameraCommandsAreRegistered()
        {
            yield return null;

            Assert.That(Commands.Has(CameraRemoteCommands.ZoomCommand), Is.True);
            Assert.That(Commands.Has(CameraRemoteCommands.StateCommand), Is.True);
        }

        /// <summary>
        /// `help` lists them, which is how somebody at a terminal finds out the
        /// page can be zoomed without reading the source.
        /// `help` がそれらを並べる。端末にいる人が、ソースを読まずにページを寄せられる
        /// ことを知る方法がこれである。
        /// </summary>
        [UnityTest]
        public IEnumerator HelpListsTheCameraCommands()
        {
            yield return null;

            SimCommandResult help = Commands.Execute("help", SimCommandArgs.Empty);

            Assert.That(help.Ok, Is.True);
            Assert.That(help.Data, Does.Contain(CameraRemoteCommands.ZoomCommand));
            Assert.That(help.Data, Does.Contain(CameraRemoteCommands.StateCommand));
        }

        /// <summary>
        /// Every key a script reads out of `camera.state`. A field dropped here
        /// would leave a check judging the framing on a number that is no longer
        /// there.
        /// 台本が `camera.state` から読む鍵の全て。ここで欄が落ちると、検査は既に無い
        /// 数値で画面への収まりを判定することになる。
        /// </summary>
        [UnityTest]
        public IEnumerator CameraStateCarriesEveryKeyAScriptReads()
        {
            yield return null;

            SimCommandResult state = Commands.Execute(
                CameraRemoteCommands.StateCommand, SimCommandArgs.Empty);

            Assert.That(state.Ok, Is.True, state.Error);
            foreach (string key in new[]
                     {
                         "distance_m", "height_m", "multiplier",
                         "current_distance_m", "current_height_m",
                         "minimum_distance_m", "maximum_distance_m",
                         "step_ratio", "default_distance_m",
                     })
            {
                Assert.That(state.Data, Does.Contain($"\"{key}\""),
                            $"camera.state lost the key {key}");
            }
        }

        /// <summary>
        /// A stated distance is answered with the value actually taken, and a
        /// few frames later the camera is there. This is the whole point of the
        /// command: a picture can be asked for at a stated size.
        /// 述べた距離には、実際に取った値が返り、数フレーム後にカメラはそこにいる。
        /// これが命令の目的である。述べた大きさで絵を求められること。
        /// </summary>
        [UnityTest]
        public IEnumerator ZoomingToADistanceMovesTheCameraThere()
        {
            yield return null;

            SimCommandResult zoomed = Commands.Execute(
                CameraRemoteCommands.ZoomCommand,
                SimCommandArgs.Parse("{\"distance\": 0.2}"));

            Assert.That(zoomed.Ok, Is.True, zoomed.Error);
            Assert.That(zoomed.Data, Does.Contain("\"distance_m\":0.2000"));
            Assert.That(follow.Zoom.DistanceMeters, Is.EqualTo(0.2f).Within(1e-4f));

            yield return WaitForTheCameraToArriveAt(0.2f);

            Assert.That(follow.CurrentDistanceMeters,
                        Is.EqualTo(0.2f).Within(ArrivalTolerance));
        }

        /// <summary>
        /// A distance beyond the closest limit is brought inside it rather than
        /// refused, and the answer says where the camera actually went: a script
        /// sweeping the zoom wants a picture at the end of the range, not a
        /// failure.
        /// 最も近い限界を越えた距離は、断られずに範囲の中へ収められ、答えはカメラが
        /// 実際にどこへ行ったかを述べる。ズームを振る台本が欲しいのは範囲の端の絵で
        /// あって、失敗ではない。
        /// </summary>
        [UnityTest]
        public IEnumerator ADistanceBeyondTheLimitIsBroughtInsideItAndReported()
        {
            yield return null;

            SimCommandResult zoomed = Commands.Execute(
                CameraRemoteCommands.ZoomCommand,
                SimCommandArgs.Parse("{\"distance\": 0.01}"));

            Assert.That(zoomed.Ok, Is.True, zoomed.Error);
            Assert.That(follow.Zoom.DistanceMeters,
                        Is.EqualTo(ChaseZoom.MinimumDistanceMeters).Within(1e-4f));

            yield return WaitForTheCameraToArriveAt(ChaseZoom.MinimumDistanceMeters);

            Assert.That(follow.CurrentDistanceMeters,
                        Is.EqualTo(ChaseZoom.MinimumDistanceMeters)
                          .Within(ArrivalTolerance));
        }

        /// <summary>
        /// A step count moves the camera by the step ratio, which is what
        /// `sf unity cmd camera.zoom --json '{"step": 2}'` does for somebody who
        /// wants the same notch a wheel gives.
        /// 段の数はステップの比の分だけカメラを動かす。ホイールと同じ刻みが欲しい人の
        /// ための `sf unity cmd camera.zoom --json '{"step": 2}'` がこれである。
        /// </summary>
        [UnityTest]
        public IEnumerator SteppingMovesTheCameraByTheStepRatio()
        {
            yield return null;

            float opened = follow.Zoom.DistanceMeters;

            SimCommandResult stepped = Commands.Execute(
                CameraRemoteCommands.ZoomCommand,
                SimCommandArgs.Parse("{\"step\": 1}"));

            Assert.That(stepped.Ok, Is.True, stepped.Error);
            Assert.That(follow.Zoom.DistanceMeters,
                        Is.EqualTo(opened / ChaseZoom.StepRatio).Within(1e-4f));
        }

        /// <summary>
        /// `reset` returns to the opening distance, so a script that zoomed in
        /// for one picture can put the view back before the next one.
        /// `reset` は開いたときの距離へ戻す。1 枚のために寄せた台本が、次の 1 枚の
        /// 前に絵を戻せるようにする。
        /// </summary>
        [UnityTest]
        public IEnumerator ResettingReturnsToTheOpeningDistance()
        {
            yield return null;

            Commands.Execute(CameraRemoteCommands.ZoomCommand,
                             SimCommandArgs.Parse("{\"distance\": 0.2}"));
            SimCommandResult reset = Commands.Execute(
                CameraRemoteCommands.ZoomCommand,
                SimCommandArgs.Parse("{\"reset\": true}"));

            Assert.That(reset.Ok, Is.True, reset.Error);
            Assert.That(follow.Zoom.DistanceMeters,
                        Is.EqualTo(ChaseZoom.DefaultDistanceMeters).Within(1e-4f));
        }

        /// <summary>
        /// Asking for neither a distance nor a step is refused with the
        /// arguments it wants, rather than answering the current state and
        /// looking as though it had moved the camera.
        /// 距離も段も渡されなければ、欲しい引数を述べて断る。いまの状態を答えて、
        /// カメラを動かしたように見せるのではなく。
        /// </summary>
        [UnityTest]
        public IEnumerator ZoomingWithNoArgumentIsRefusedWithWhatItWants()
        {
            yield return null;

            SimCommandResult refused = Commands.Execute(
                CameraRemoteCommands.ZoomCommand, SimCommandArgs.Empty);

            Assert.That(refused.Ok, Is.False);
            Assert.That(refused.Error, Does.Contain("distance"));
            Assert.That(refused.Error, Does.Contain("step"));
        }

        /// <summary>
        /// The commands come off the registry when the component goes, so a
        /// scene reloaded twice does not hit the registry's "already registered"
        /// refusal.
        /// 部品が消えるとき命令は登録簿から外れる。場面を二度読み直しても、登録簿の
        /// 「登録済み」の拒否に当たらないようにするためである。
        /// </summary>
        [UnityTest]
        public IEnumerator DestroyingTheComponentUnregistersItsCommands()
        {
            yield return null;

            Object.DestroyImmediate(
                cameraObject.GetComponent<CameraRemoteCommands>());

            Assert.That(Commands.Has(CameraRemoteCommands.ZoomCommand), Is.False);
            Assert.That(Commands.Has(CameraRemoteCommands.StateCommand), Is.False);
        }

        /// <summary>
        /// Hold until the camera has closed the gap to <paramref name="wanted"/>,
        /// or fail saying how far it got. Waiting on the value rather than on a
        /// fixed number of frames is what keeps this from passing or failing by
        /// luck on a slow editor.
        /// カメラが <paramref name="wanted"/> までのずれを詰めるまで待つ。詰めなけ
        /// れば、どこまで来たかを述べて失敗する。決まったフレーム数ではなく値を待つ
        /// ことで、遅いエディタで合否が運で決まらないようにする。
        /// </summary>
        private IEnumerator WaitForTheCameraToArriveAt(float wanted)
        {
            float startedAt = Time.realtimeSinceStartup;
            float waited = 0.0f;
            while (Mathf.Abs(follow.CurrentDistanceMeters - wanted) > ArrivalTolerance
                   && waited < MaxSecondsToClose)
            {
                yield return null;
                waited = Time.realtimeSinceStartup - startedAt;
            }

            Assert.That(follow.CurrentDistanceMeters, Is.EqualTo(wanted).Within(ArrivalTolerance),
                        $"the camera stopped at {follow.CurrentDistanceMeters} m " +
                        $"after {waited:F2} s");
        }
    }
}
