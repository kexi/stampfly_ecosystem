/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — chase camera zoom tests).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Ui;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// Guarantees what a person zooming the chase camera relies on: that one step
    /// is always the same proportion of the current distance, that neither end of
    /// the range can be crossed, that a stated distance is honoured or brought
    /// inside the range, that one key returns to the opening view, and that the
    /// angle the camera looks down at never changes with the zoom.
    ///
    /// 追跡カメラを寄せる人が頼るものを保証する。1 段が常にいまの距離に対する同じ
    /// 割合であること、範囲のどちらの端も越えられないこと、述べた距離が通るか範囲の
    /// 中へ収められること、キー 1 つで開いたときの絵へ戻ること、そして見下ろす角が
    /// ズームで変わらないことである。
    /// </summary>
    public sealed class ChaseZoomTest
    {
        /// <summary>
        /// How close two distances must be to count as equal [m]. Well under a
        /// millimetre, which is far finer than the picture can show.
        /// 2 つの距離が等しいと数えるための近さ [m]。1 ミリメートルよりずっと
        /// 細かく、絵に映る細かさを大きく下回る。
        /// </summary>
        private const float Tolerance = 1e-4f;

        /// <summary>
        /// A fresh zoom opens at the default, so the simulator's first frame
        /// shows the airframe at the size the plan states.
        /// 作った直後のズームは既定から始まる。シミュレータの最初のフレームが、
        /// 計画の述べる大きさで機体を映すためである。
        /// </summary>
        [Test]
        public void AFreshZoomOpensAtTheDefault()
        {
            var zoom = new ChaseZoom();

            Assert.That(zoom.DistanceMeters,
                        Is.EqualTo(ChaseZoom.DefaultDistanceMeters).Within(Tolerance));
            Assert.That(zoom.Multiplier, Is.EqualTo(1.0f).Within(Tolerance));
        }

        /// <summary>
        /// One step in multiplies the distance by the same RATIO wherever it
        /// starts. A step of a fixed number of metres would be most of the way
        /// to the vehicle when close and invisible when far.
        /// 1 段寄ると、どこから始めても距離に同じ**比**が掛かる。固定のメートル数の
        /// 刻みは、近いときは機体までのほとんどの距離になり、遠いときは見て分からない。
        /// </summary>
        [Test]
        public void OneStepIsAConstantRatioWhereverItStarts()
        {
            var zoom = new ChaseZoom();

            zoom.SetDistance(1.0f);
            zoom.Step(1);
            Assert.That(zoom.DistanceMeters,
                        Is.EqualTo(1.0f / ChaseZoom.StepRatio).Within(Tolerance));

            zoom.SetDistance(2.0f);
            zoom.Step(1);
            Assert.That(zoom.DistanceMeters,
                        Is.EqualTo(2.0f / ChaseZoom.StepRatio).Within(Tolerance));
        }

        /// <summary>
        /// A positive step goes towards the vehicle and a negative one away, so
        /// a wheel pushed forward zooms in as it does in a map or a photo viewer.
        /// 正の段は機体へ向かい、負の段は離れる。前へ回したホイールが、地図や写真の
        /// 閲覧と同じように拡大する。
        /// </summary>
        [Test]
        public void APositiveStepMovesTowardsTheVehicle()
        {
            var zoom = new ChaseZoom();
            float opened = zoom.DistanceMeters;

            zoom.Step(1);
            Assert.That(zoom.DistanceMeters, Is.LessThan(opened));

            zoom.Reset();
            zoom.Step(-1);
            Assert.That(zoom.DistanceMeters, Is.GreaterThan(opened));
        }

        /// <summary>
        /// Several steps in one call are the same as the same number applied one
        /// at a time. A wheel reports several notches in one frame, and the two
        /// must agree or a flick would land somewhere a person cannot predict.
        /// 1 回の呼び出しでの複数段は、同じ数を 1 段ずつ掛けたものと同じである。
        /// ホイールは 1 フレームに複数の刻みを報告し、両者が一致しなければ、はじいた
        /// 結果が予測できない場所へ着く。
        /// </summary>
        [Test]
        public void SeveralStepsAtOnceMatchTheSameNumberOneAtATime()
        {
            var manySteps = new ChaseZoom();
            manySteps.Step(3);

            var oneAtATime = new ChaseZoom();
            oneAtATime.Step(1);
            oneAtATime.Step(1);
            oneAtATime.Step(1);

            Assert.That(manySteps.DistanceMeters,
                        Is.EqualTo(oneAtATime.DistanceMeters).Within(Tolerance));
        }

        /// <summary>
        /// Stepping in past the limit stops at it and stays there, however many
        /// more steps arrive. Held-down keys and a long scroll both produce that.
        /// 限界を越えて寄ろうとすると、限界で止まり、さらに何段来てもそこに留まる。
        /// キーの押しっぱなしと長いスクロールが、どちらもそれを生む。
        /// </summary>
        [Test]
        public void SteppingInStopsAtTheClosestLimit()
        {
            var zoom = new ChaseZoom();

            zoom.Step(50);

            Assert.That(zoom.DistanceMeters,
                        Is.EqualTo(ChaseZoom.MinimumDistanceMeters).Within(Tolerance));
            Assert.That(zoom.IsAtClosest, Is.True);
        }

        /// <summary>
        /// And the same the other way: the furthest limit holds.
        /// 逆向きも同じ。最も遠い限界で留まる。
        /// </summary>
        [Test]
        public void SteppingOutStopsAtTheFurthestLimit()
        {
            var zoom = new ChaseZoom();

            zoom.Step(-50);

            Assert.That(zoom.DistanceMeters,
                        Is.EqualTo(ChaseZoom.MaximumDistanceMeters).Within(Tolerance));
            Assert.That(zoom.IsAtFurthest, Is.True);
        }

        /// <summary>
        /// A distance inside the range is taken exactly, which is what makes a
        /// screenshot repeatable: two pictures asked for at 0.2 m are the same
        /// size.
        /// 範囲の中の距離はそのまま取る。画面の写しを繰り返し取れるのはこれによる。
        /// 0.2 m で求めた 2 枚は同じ大きさになる。
        /// </summary>
        [Test]
        public void ADistanceInsideTheRangeIsTakenExactly()
        {
            var zoom = new ChaseZoom();

            float taken = zoom.SetDistance(0.2f);

            Assert.That(taken, Is.EqualTo(0.2f).Within(Tolerance));
            Assert.That(zoom.DistanceMeters, Is.EqualTo(0.2f).Within(Tolerance));
        }

        /// <summary>
        /// A distance outside the range is brought inside it and the value
        /// actually taken is returned, so a caller that asked for too much learns
        /// what it got without asking again.
        /// 範囲外の距離は範囲の中へ収め、実際に取った値を返す。行き過ぎを求めた
        /// 呼び出し側は、もう一度尋ねずに結果を知る。
        /// </summary>
        [Test]
        public void ADistanceOutsideTheRangeIsBroughtInsideIt()
        {
            var zoom = new ChaseZoom();

            Assert.That(zoom.SetDistance(0.01f),
                        Is.EqualTo(ChaseZoom.MinimumDistanceMeters).Within(Tolerance));
            Assert.That(zoom.SetDistance(99.0f),
                        Is.EqualTo(ChaseZoom.MaximumDistanceMeters).Within(Tolerance));
        }

        /// <summary>
        /// Reset returns to the opening distance from either end of the range, so
        /// one key ends the hunt for a vehicle zoomed out of sight.
        /// 既定へ戻すのは、範囲のどちらの端からでも開いたときの距離へ戻す。キー 1 つ
        /// で、引きすぎて見えなくなった機体を探すのが終わる。
        /// </summary>
        [Test]
        public void ResetReturnsToTheOpeningDistanceFromEitherEnd()
        {
            var zoom = new ChaseZoom();

            zoom.Step(50);
            Assert.That(zoom.Reset(),
                        Is.EqualTo(ChaseZoom.DefaultDistanceMeters).Within(Tolerance));

            zoom.Step(-50);
            Assert.That(zoom.Reset(),
                        Is.EqualTo(ChaseZoom.DefaultDistanceMeters).Within(Tolerance));
        }

        /// <summary>
        /// The height keeps the same proportion of the distance at every zoom, so
        /// the angle the camera looks down at does not slide from "behind and
        /// above" towards "directly overhead" as a person zooms in.
        /// 高さは、どの倍率でも距離に対する同じ割合を保つ。寄せていくにつれて見下ろす
        /// 角が「後ろ上方」から「真上」へずれていかないためである。
        /// </summary>
        [Test]
        public void TheHeightKeepsItsProportionOfTheDistance()
        {
            var zoom = new ChaseZoom();
            float openedRatio = zoom.HeightMeters / zoom.DistanceMeters;

            foreach (float distance in new[] { 0.15f, 0.3f, 1.0f, 3.0f })
            {
                zoom.SetDistance(distance);
                Assert.That(zoom.HeightMeters / zoom.DistanceMeters,
                            Is.EqualTo(openedRatio).Within(Tolerance),
                            $"the look-down angle changed at {distance} m");
            }
        }

        /// <summary>
        /// The default sits inside the range with room to move both ways, so
        /// neither key is dead the moment the simulator opens.
        /// 既定は範囲の中にあり、両方向へ動く余地がある。シミュレータを開いた時点で
        /// どちらのキーも効かない、ということが無いようにする。
        /// </summary>
        [Test]
        public void TheDefaultSitsInsideTheRangeWithRoomBothWays()
        {
            Assert.That(ChaseZoom.DefaultDistanceMeters,
                        Is.GreaterThan(ChaseZoom.MinimumDistanceMeters));
            Assert.That(ChaseZoom.DefaultDistanceMeters,
                        Is.LessThan(ChaseZoom.MaximumDistanceMeters));
            Assert.That(ChaseZoom.StepRatio, Is.GreaterThan(1.0f),
                        "a ratio of 1 or less would make a step move nothing");
        }

        /// <summary>
        /// A step of zero moves nothing. The wheel reading truncates to whole
        /// notches, so a scroll too small to count reaches here as zero on
        /// every frame a finger rests on a trackpad.
        /// 0 段は何も動かさない。ホイールの読みは丸めた刻みに切り捨てるので、数える
        /// に足りないスクロールは、トラックパッドに指を置いている全てのフレームで
        /// 0 としてここへ届く。
        /// </summary>
        [Test]
        public void AStepOfZeroMovesNothing()
        {
            var zoom = new ChaseZoom();
            zoom.SetDistance(0.7f);

            Assert.That(zoom.Step(0), Is.EqualTo(0.7f).Within(Tolerance));
        }

        /// <summary>
        /// A distance that is not a number leaves the camera where it was. A
        /// command's argument reaches this straight from JSON, and a page that
        /// sent nonsense must not lose sight of the vehicle over it.
        /// 数でない距離は、カメラをそのままにする。命令の引数は JSON からそのまま
        /// ここへ届き、意味を成さない値を送ったページが、それで機体を見失っては
        /// ならない。
        /// </summary>
        [Test]
        public void ADistanceThatIsNotANumberIsIgnored()
        {
            var zoom = new ChaseZoom();
            zoom.SetDistance(0.7f);

            Assert.That(zoom.SetDistance(float.NaN), Is.EqualTo(0.7f).Within(Tolerance));
        }
    }
}
