/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — how close the chase camera sits).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;

namespace StampFly.Ui
{
    /// <summary>
    /// How close the chase camera sits, as one multiplier applied to both the
    /// distance behind the vehicle and the height above it.
    ///
    /// 追跡カメラがどれだけ寄っているか。機体の後ろの距離と、その上の高さの両方に
    /// 掛ける倍率 1 つで表す。
    ///
    /// The multiplier scales BOTH, so the angle the camera looks down at stays
    /// the same at every zoom: the picture gets bigger without the viewpoint
    /// sliding from "behind and above" towards "directly overhead", which would
    /// change what a person reads out of it while flying.
    ///
    /// 倍率は**両方**に掛かるので、見下ろす角はどの倍率でも変わらない。絵が大きく
    /// なるだけで、視点が「後ろ上方」から「真上」へずれていかない。ずれると、飛ばし
    /// ながら絵から読み取るものが変わってしまう。
    ///
    /// Why not zoom with the field of view: a narrower field of view flattens
    /// perspective, so the distance between the vehicle and a wall stops looking
    /// like the distance it is. Somebody flying by eye judges closing speed from
    /// exactly that, and a lens that changes as they zoom takes it away. Moving
    /// the camera keeps the perspective of the real viewpoint and only changes
    /// where that viewpoint is.
    ///
    /// なぜ視野角で寄せないか。視野角を狭めると遠近感が平らになり、機体と壁の間の
    /// 距離が、その距離らしく見えなくなる。目で飛ばす人はまさにそこから接近の速さを
    /// 判断しており、寄せるたびに変わるレンズはそれを奪う。カメラを動かすやり方なら、
    /// 実際の視点の遠近感はそのままで、その視点の位置だけが変わる。
    ///
    /// This holds no Unity type and no frame: it is arithmetic on one number, so
    /// the step sizes and the limits can be checked in an EditMode test without a
    /// scene, a camera or a vehicle.
    ///
    /// Unity の型もフレームも持たない。数 1 つの算術なので、刻みの大きさと限界を、
    /// 場面もカメラも機体も無しに EditMode の試験で確かめられる。
    ///
    /// @design docs/plans/unity-simulator.md §5 段階 3
    /// </summary>
    public sealed class ChaseZoom
    {
        /// <summary>
        /// The distance behind the vehicle at a multiplier of 1 [m]. At this
        /// distance a 1280x720 picture shows the airframe about 200 px wide,
        /// which is where the white frame, the four legs and the orange
        /// M5StampS3 all become separately legible.
        ///
        /// The figure is set from the airframe's ACROSS-AN-AXIS width, 0.0816 m
        /// (the collision box's footprint, which is also what the rotors at
        /// ±0.023 m with 0.015 m propellers reach). The 0.12 m often quoted for
        /// a StampFly is the diagonal between opposite rotors, and sizing on it
        /// put the vehicle at about 130 px here -- too small to make out the
        /// legs, which is what a measurement on a real 1280-wide page showed.
        ///
        /// 倍率 1 のときの機体の後ろの距離 [m]。この距離で 1280x720 の絵に機体が
        /// 幅 200 px ほどで映る。白いフレーム・4 本の脚・オレンジの M5StampS3 が
        /// それぞれ見分けられるようになるのがこの大きさである。
        ///
        /// この値は機体の**軸方向の**幅 0.0816 m から決めた（衝突箱の footprint で
        /// あり、±0.023 m のロータに半径 0.015 m のプロペラが届く幅でもある）。
        /// StampFly についてよく挙がる 0.12 m は対角のロータ間の距離で、それを基に
        /// 大きさを決めると、ここでは機体が幅 130 px ほどにしかならない。脚が
        /// 見分けられない大きさであり、実際に 1280 幅のページで測って分かった。
        /// </summary>
        public const float DefaultDistanceMeters = 0.28f;

        /// <summary>
        /// The height above the vehicle at a multiplier of 1 [m]. The ratio to
        /// the distance (0.55 / 1.2, the angle the camera looked down at before
        /// zoom existed) is what every multiplier preserves.
        /// 倍率 1 のときの機体の上の高さ [m]。距離との比（0.55 / 1.2。ズームが
        /// 無かった頃の見下ろす角）が、どの倍率でも保たれるものである。
        /// </summary>
        public const float DefaultHeightMeters =
            DefaultDistanceMeters * (0.55f / 1.2f);

        /// <summary>
        /// The closest the camera may sit [m]. Here the airframe fills about
        /// 376 px of a 1280-wide picture -- close enough to read a single leg,
        /// with the picture's own edges still well clear of the propellers and
        /// nothing reaching the 0.02 m near clip plane (the nearest propeller tip
        /// is about 0.1 m away). It was measured on a real page rather than
        /// derived, because whether the body is clipped depends on the near clip
        /// plane that `SimulatorBootstrap` sets, not on this class.
        /// カメラが寄れる限界 [m]。ここで機体は 1280 幅の絵の 376 px ほどを占める。
        /// 脚 1 本を読み取れる近さで、なお絵の端はプロペラから十分離れ、0.02 m の
        /// ニアクリップ面に届くものも無い（最も近いプロペラの先端まで 0.1 m ほど
        /// ある）。導出ではなく実際のページで測った。機体が切られるかを決めるのは
        /// `SimulatorBootstrap` が設定するニアクリップ面であって、このクラスでは
        /// ないからである。
        /// </summary>
        public const float MinimumDistanceMeters = 0.15f;

        /// <summary>
        /// The furthest the camera may sit [m]. Beyond this the airframe is
        /// under 20 px wide on a 720-line picture, which shows the room rather
        /// than the vehicle.
        /// カメラが引ける限界 [m]。これを越えると 720 行の絵で機体が幅 20 px を
        /// 下回り、機体ではなく部屋を見ていることになる。
        /// </summary>
        public const float MaximumDistanceMeters = 3.0f;

        /// <summary>
        /// What one step in or out multiplies the distance by. A constant RATIO
        /// rather than a constant number of metres: at 0.15 m a step of 0.1 m
        /// would be most of the way to the vehicle, and at 3.0 m it would be
        /// barely visible.
        ///
        /// 1.15 puts the closest limit about 4 steps in from the default and the
        /// furthest about 17 steps out, 21 across the whole range. A coarser
        /// ratio was tried first and reached the closest limit in under three
        /// notches of a wheel, which reads as a jump rather than a zoom.
        ///
        /// 1 段寄る・引くときに距離に掛ける値。一定のメートル数ではなく一定の**比**
        /// にする。0.15 m で 0.1 m の刻みは機体までのほとんどの距離になり、3.0 m
        /// では見て分からない。
        ///
        /// 1.15 なら、既定から最も近い限界まで約 4 段、最も遠い限界まで約 17 段、
        /// 範囲の全体で 21 段になる。先に粗い比を試したところ、ホイール 3 刻み未満で
        /// 最も近い限界に達し、寄ったのではなく飛んだように読めた。
        /// </summary>
        public const float StepRatio = 1.15f;

        private float distanceMeters = DefaultDistanceMeters;

        /// <summary>
        /// The distance behind the vehicle the camera is asked to sit at [m].
        /// Always within the limits.
        /// カメラが座るよう求められている、機体の後ろの距離 [m]。常に限界の中にある。
        /// </summary>
        public float DistanceMeters => distanceMeters;

        /// <summary>
        /// The height above the vehicle that goes with the current distance [m].
        /// いまの距離に対応する、機体の上の高さ [m]。
        /// </summary>
        public float HeightMeters => DefaultHeightMeters * Multiplier;

        /// <summary>
        /// How far out the camera is compared with the default: 1 at the
        /// default, below 1 when closer, above 1 when further away.
        /// 既定と比べてどれだけ引いているか。既定で 1、寄れば 1 未満、引けば
        /// 1 を超える。
        /// </summary>
        public float Multiplier => distanceMeters / DefaultDistanceMeters;

        /// <summary>Whether the camera is as close as it may sit. / これ以上寄れない位置にいるか。</summary>
        public bool IsAtClosest => distanceMeters <= MinimumDistanceMeters;

        /// <summary>Whether the camera is as far out as it may sit. / これ以上引けない位置にいるか。</summary>
        public bool IsAtFurthest => distanceMeters >= MaximumDistanceMeters;

        /// <summary>
        /// Move <paramref name="steps"/> steps in (positive) or out (negative),
        /// each step a factor of <see cref="StepRatio"/>. Several steps in one
        /// call because a wheel reports several notches in one frame, and
        /// applying them one at a time from the caller would spread one flick
        /// over several frames of smoothing.
        /// <paramref name="steps"/> 段ぶん寄る（正）か引く（負）。1 段は
        /// <see cref="StepRatio"/> 倍である。1 回の呼び出しで複数段にしてあるのは、
        /// ホイールが 1 フレームに複数の刻みを報告するためで、呼び出し側から 1 段ずつ
        /// 掛けると 1 回のはじきが数フレームの滑らかな追従に散ってしまう。
        /// </summary>
        public float Step(int steps)
        {
            bool isNoMove = steps == 0;
            if (isNoMove)
            {
                return distanceMeters;
            }

            // A step IN divides, so `steps` counts how much closer the camera
            // gets -- the direction a wheel pushed forward means.
            // 寄る段は割り算である。`steps` はどれだけ近づくかを数える。ホイールを
            // 前へ回したときの向きである。
            double factor = Math.Pow(StepRatio, -steps);
            return SetDistance((float)(distanceMeters * factor));
        }

        /// <summary>
        /// Sit at a given distance [m], brought inside the limits. Returns the
        /// distance actually taken, so a caller that asked for something out of
        /// range learns what it got rather than having to ask again.
        /// 与えられた距離 [m] に座る。限界の中へ収めた上で。実際に取った距離を返す
        /// ので、範囲外を求めた呼び出し側は、もう一度尋ねずに結果を知る。
        /// </summary>
        public float SetDistance(float wanted)
        {
            bool isNotANumber = float.IsNaN(wanted);
            if (isNotANumber)
            {
                return distanceMeters;
            }

            distanceMeters = Clamp(wanted);
            return distanceMeters;
        }

        /// <summary>
        /// Back to the distance the simulator opens at. A person who has zoomed
        /// too far to tell where the vehicle is needs one key that ends the
        /// hunt, not a count of how many steps they took.
        /// シミュレータが開いたときの距離へ戻す。寄せすぎて機体がどこか分からなく
        /// なった人に要るのは、探すのを終わらせるキー 1 つであって、何段動かしたかの
        /// 数え上げではない。
        /// </summary>
        public float Reset()
        {
            distanceMeters = DefaultDistanceMeters;
            return distanceMeters;
        }

        /// <summary>
        /// A distance brought inside the limits. Named rather than inline
        /// because the range belongs to this class and is reported by
        /// <c>camera.state</c> from the same two constants.
        /// 限界の中へ収めた距離。範囲はこのクラスのものであり、<c>camera.state</c>
        /// も同じ 2 つの定数から報告するので、その場に書かず名前を付ける。
        /// </summary>
        public static float Clamp(float wanted)
        {
            bool isTooClose = wanted < MinimumDistanceMeters;
            if (isTooClose)
            {
                return MinimumDistanceMeters;
            }

            bool isTooFar = wanted > MaximumDistanceMeters;
            if (isTooFar)
            {
                return MaximumDistanceMeters;
            }

            return wanted;
        }
    }
}
