/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the camera's command surface).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

#if UNITY_EDITOR || DEVELOPMENT_BUILD || STAMPFLY_REMOTE

using System.Globalization;
using StampFly.Core;
using StampFly.Remote;
using StampFly.Ui;
using UnityEngine;

namespace StampFly.App
{
    /// <summary>
    /// Puts the chase camera's two commands on the shared registry, so a person
    /// at a terminal can frame the vehicle without reaching for the wheel.
    ///
    /// 追跡カメラが持つ 2 つの命令を共有の登録簿に載せる。端末にいる人が、ホイール
    /// に手を伸ばさずに機体を画面へ収められるようにする。
    ///
    /// This is what makes a screenshot repeatable: `sf unity cmd camera.zoom
    /// --json '{"distance": 0.2}'` puts the camera at a stated distance, so two
    /// pictures taken a week apart show the airframe at the same size.
    ///
    /// 画面の写しを繰り返し取れるようになるのはこれによる。`sf unity cmd
    /// camera.zoom --json '{"distance": 0.2}'` はカメラを述べた距離に置くので、
    /// 1 週間空けて撮った 2 枚が同じ大きさの機体を映す。
    ///
    /// It sits beside <see cref="SimRemoteCommands"/> rather than inside
    /// <see cref="StampFly.Ui.FollowCamera"/> and carries the same
    /// <c>defineConstraints</c> the relay does, so a distributed build has
    /// neither the relay nor anything that reaches for it -- which is what
    /// <c>sf unity build --release</c> checks for (plan §4). Keeping it out of
    /// <c>StampFly.Ui</c> is also what stops that assembly from having to
    /// reference <c>StampFly.Remote</c>.
    ///
    /// <see cref="StampFly.Ui.FollowCamera"/> の中ではなく
    /// <see cref="SimRemoteCommands"/> の隣に置き、中継と同じ
    /// <c>defineConstraints</c> を持たせてある。よって配布用ビルドには中継も、
    /// それに手を伸ばすものも入らない。<c>sf unity build --release</c> が確かめるのは
    /// そこである（計画 §4）。<c>StampFly.Ui</c> の外に置いたのは、そのアセンブリが
    /// <c>StampFly.Remote</c> を参照せずに済むようにするためでもある。
    ///
    /// ## The commands / 命令
    ///
    /// | Command | What it does |
    /// |---|---|
    /// | `camera.zoom` | move the camera in or out — `distance` [m] or `step` |
    /// | `camera.state` | the distance, the height, the multiplier and the range |
    ///
    /// @design simulator/unity/README.md §8 命令の登録のしかた
    /// </summary>
    [RequireComponent(typeof(FollowCamera))]
    public sealed class CameraRemoteCommands : MonoBehaviour
    {
        /// <summary>Move the camera in or out. / カメラを寄せる・引く。</summary>
        public const string ZoomCommand = "camera.zoom";

        /// <summary>Report where the camera sits. / カメラの位置を報告する。</summary>
        public const string StateCommand = "camera.state";

        private FollowCamera follow;

        private void Awake()
        {
            follow = GetComponent<FollowCamera>();
        }

        private void Start()
        {
            RemoteBridge bridge = RemoteBridge.Instance;
            bool hasNoBridge = bridge == null;
            if (hasNoBridge)
            {
                return;
            }

            bridge.Commands.Register(ZoomCommand, Zoom,
                "Move the chase camera in or out: distance [m] or step");
            bridge.Commands.Register(StateCommand,
                _ => SimCommandResult.Success(StateJson()),
                "Where the chase camera sits, and how far it may go");
        }

        private void OnDestroy()
        {
            RemoteBridge bridge = RemoteBridge.Instance;
            bool hasNoBridge = bridge == null;
            if (hasNoBridge)
            {
                return;
            }

            bridge.Commands.Unregister(ZoomCommand);
            bridge.Commands.Unregister(StateCommand);
        }

        /// <summary>
        /// <c>camera.zoom</c>: a distance in metres, or a number of steps.
        ///
        /// A distance out of range is brought inside it rather than refused, and
        /// the answer carries what was actually taken: a script that sweeps the
        /// zoom from 0.1 m to 5 m wants pictures at both ends, not two failures.
        /// Asking for neither is refused, because it would otherwise answer the
        /// current state and look as if it had moved the camera.
        ///
        /// <c>camera.zoom</c>: メートルでの距離、または段の数。
        ///
        /// 範囲外の距離は断らずに範囲の中へ収め、答えには実際に取った値を載せる。
        /// ズームを 0.1 m から 5 m まで振る台本が欲しいのは両端の絵であって、
        /// 2 つの失敗ではない。どちらも渡されなければ断る。そうしないと、いまの状態を
        /// 答えて、カメラを動かしたように見えてしまうからである。
        /// </summary>
        private SimCommandResult Zoom(SimCommandArgs args)
        {
            ChaseZoom zoom = follow.Zoom;

            double distance = args.Double("distance", 0.0);
            bool wantsADistance = distance > 0.0;
            if (wantsADistance)
            {
                zoom.SetDistance((float)distance);
                return SimCommandResult.Success(StateJson());
            }

            int steps = args.Int("step", 0);
            bool wantsSteps = steps != 0;
            if (wantsSteps)
            {
                zoom.Step(steps);
                return SimCommandResult.Success(StateJson());
            }

            bool wantsDefault = args.Bool("reset", false);
            if (wantsDefault)
            {
                zoom.Reset();
                return SimCommandResult.Success(StateJson());
            }

            return SimCommandResult.Failure(
                "camera.zoom needs distance (m, greater than zero), step " +
                "(positive closer, negative further out) or reset");
        }

        /// <summary>
        /// Where the camera is asked to sit, where it actually is, and how far it
        /// may go. Both the asked-for and the actual distance are reported
        /// because the camera closes the gap over about a tenth of a second: a
        /// check that reads this immediately after a <c>camera.zoom</c> would
        /// otherwise see a distance that looks wrong and is merely on its way.
        /// カメラが座るよう求められている位置、実際にいる位置、そして動ける範囲。
        /// 求めた距離と実際の距離の両方を報告するのは、カメラが 0.1 秒ほどかけて
        /// ずれを詰めるためである。<c>camera.zoom</c> の直後にこれを読む検査は、
        /// さもなければ、途中にあるだけの距離を誤りとして見てしまう。
        /// </summary>
        private string StateJson()
        {
            ChaseZoom zoom = follow.Zoom;

            return "{" +
                $"\"distance_m\":{Number(zoom.DistanceMeters)}," +
                $"\"height_m\":{Number(zoom.HeightMeters)}," +
                $"\"multiplier\":{Number(zoom.Multiplier)}," +
                $"\"current_distance_m\":{Number(follow.CurrentDistanceMeters)}," +
                $"\"current_height_m\":{Number(follow.CurrentHeightMeters)}," +
                $"\"minimum_distance_m\":{Number(ChaseZoom.MinimumDistanceMeters)}," +
                $"\"maximum_distance_m\":{Number(ChaseZoom.MaximumDistanceMeters)}," +
                $"\"step_ratio\":{Number(ChaseZoom.StepRatio)}," +
                $"\"default_distance_m\":{Number(ChaseZoom.DefaultDistanceMeters)}" +
                "}";
        }

        /// <summary>
        /// One number, always with a dot for its decimal point. A culture that
        /// writes a comma there would produce JSON no reader can parse.
        /// 数値 1 つ。小数点は常に点にする。そこに読点を書く文化では、どの読み手も
        /// 解釈できない JSON になってしまう。
        /// </summary>
        private static string Number(float value)
        {
            return value.ToString("F4", CultureInfo.InvariantCulture);
        }
    }
}

#endif
