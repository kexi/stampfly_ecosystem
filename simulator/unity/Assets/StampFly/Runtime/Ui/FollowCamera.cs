/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the chase camera).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine;

namespace StampFly.Ui
{
    /// <summary>
    /// Keeps the vehicle in frame from behind and above, moving smoothly so a
    /// 37 g body's quick attitude changes do not throw the picture about.
    ///
    /// 機体を後ろ上方から画面に収め続ける。滑らかに動かして、37 g の機体の素早い
    /// 姿勢変化で絵が振り回されないようにする。
    ///
    /// The camera follows the vehicle's POSITION but not its roll and pitch: a
    /// camera that rolled with the vehicle would make a level horizon impossible
    /// to read, which is exactly what a pilot needs to see. Only the heading is
    /// followed, and that lazily.
    ///
    /// カメラが追うのは機体の**位置**で、ロールとピッチは追わない。機体と一緒に
    /// 傾くカメラでは水平線が読めなくなるが、操縦する人が見たいのはまさにそれで
    /// ある。追うのは向きだけで、それもゆっくり追う。
    ///
    /// How close it sits is <see cref="ChaseZoom"/>'s, not this class's: the
    /// arithmetic of the steps and the limits is worth checking without a scene,
    /// and this class only moves towards whatever distance that one names.
    ///
    /// どれだけ寄っているかは <see cref="ChaseZoom"/> のもので、このクラスのもの
    /// ではない。刻みと限界の算術は場面無しで確かめる価値があり、このクラスは
    /// そちらが述べる距離へ寄っていくだけである。
    ///
    /// @design docs/plans/unity-simulator.md §5 段階 3
    /// </summary>
    public sealed class FollowCamera : MonoBehaviour
    {
        /// <summary>
        /// How far above the target the camera looks, as a fraction of the
        /// distance behind it. A fixed number of metres would put the aim point
        /// well above the vehicle once zoomed in -- at 0.15 m the old 0.08 m
        /// would have aimed half a picture high -- so the aim rises and falls
        /// with the distance and the vehicle stays in the same place on screen
        /// at every zoom.
        /// カメラが対象より上を見る高さを、後ろの距離に対する割合で表す。固定の
        /// メートル数にすると、寄せたときに狙う点が機体のかなり上へ行く。0.15 m
        /// では従来の 0.08 m が画面の半分ぶん上を狙うことになる。距離とともに
        /// 狙う高さも上下させれば、どの倍率でも機体は画面の同じ場所に留まる。
        /// </summary>
        public const float LookAheadFraction = 0.11f;

        /// <summary>
        /// Seconds for the zoom to close most of a change. Shorter than the
        /// position smoothing: a wheel flick should answer at once, while the
        /// position has to stay calm through the vehicle's own motion.
        /// ズームが変化の大半を詰めるのに掛ける秒数。位置の滑らかさより短くして
        /// ある。ホイールをはじいたらすぐ応えるべきだが、位置は機体自身の動きの中で
        /// 落ち着いていなければならない。
        /// </summary>
        public const float ZoomSmoothingSeconds = 0.12f;

        [Tooltip("What to keep in frame. 画面に収める対象。")]
        public Transform target;

        [Tooltip("Seconds for the camera to close most of a gap. " +
                 "カメラがずれの大半を詰めるのに掛ける秒数。")]
        public float smoothingSeconds = 0.25f;

        private readonly ChaseZoom zoom = new ChaseZoom();

        private Vector3 followVelocity;
        private float heading;

        // What the camera is actually at, chasing what the zoom asks for. A jump
        // straight to a new distance reads as a cut rather than a zoom.
        // カメラが実際にいる位置。ズームが求める値を追う。新しい距離へ飛ぶと、
        // 寄ったのではなく切り替わったように見える。
        private float currentDistanceMeters = ChaseZoom.DefaultDistanceMeters;
        private float currentHeightMeters = ChaseZoom.DefaultHeightMeters;

        /// <summary>
        /// How close the camera is asked to sit. The keys, the wheel and the
        /// <c>camera.zoom</c> command all move this one object.
        /// カメラがどれだけ寄るよう求められているか。キー・ホイール・
        /// <c>camera.zoom</c> の命令は、いずれもこの 1 つの対象を動かす。
        /// </summary>
        public ChaseZoom Zoom => zoom;

        /// <summary>The distance the camera is at right now [m]. / カメラがいまいる距離 [m]。</summary>
        public float CurrentDistanceMeters => currentDistanceMeters;

        /// <summary>The height the camera is at right now [m]. / カメラがいまいる高さ [m]。</summary>
        public float CurrentHeightMeters => currentHeightMeters;

        private void LateUpdate()
        {
            bool hasTarget = target != null;
            if (!hasTarget)
            {
                return;
            }

            FollowZoom();

            heading = SmoothedHeading();
            Vector3 behind = Quaternion.Euler(0.0f, heading, 0.0f) * Vector3.back;
            Vector3 wanted = target.position
                             + behind * currentDistanceMeters
                             + Vector3.up * currentHeightMeters;

            transform.position = Vector3.SmoothDamp(
                transform.position, wanted, ref followVelocity, smoothingSeconds,
                Mathf.Infinity, Time.unscaledDeltaTime);
            transform.LookAt(
                target.position + Vector3.up * currentDistanceMeters * LookAheadFraction);
        }

        /// <summary>
        /// Close most of the gap towards the distance the zoom asks for. On
        /// <see cref="Time.unscaledDeltaTime"/>, so zooming works while the
        /// simulation is paused -- which is exactly when somebody wants a closer
        /// look at the airframe.
        /// ズームが求める距離へ、ずれの大半を詰める。
        /// <see cref="Time.unscaledDeltaTime"/> で動かすので、シミュレーションを
        /// 一時停止している間も寄せられる。機体をよく見たいのはまさにそのときである。
        /// </summary>
        private void FollowZoom()
        {
            float rate = Time.unscaledDeltaTime /
                         Mathf.Max(ZoomSmoothingSeconds, 1e-3f);
            float closed = Mathf.Clamp01(rate);

            currentDistanceMeters =
                Mathf.Lerp(currentDistanceMeters, zoom.DistanceMeters, closed);
            currentHeightMeters =
                Mathf.Lerp(currentHeightMeters, zoom.HeightMeters, closed);
        }

        /// <summary>
        /// Put the camera at the zoom's distance at once, without the smoothing.
        /// Used when a scene has just been built, so the first frame is not a
        /// rush inwards from wherever the previous distance was.
        /// 滑らかな追従を挟まず、その場でズームの距離へ置く。場面を組み立てた直後に
        /// 使う。最初のフレームが、前の距離からの寄りになってしまわないようにする。
        /// </summary>
        public void SnapToZoom()
        {
            currentDistanceMeters = zoom.DistanceMeters;
            currentHeightMeters = zoom.HeightMeters;
        }

        /// <summary>
        /// The heading the camera should sit behind: the vehicle's own, closed
        /// on gradually. Taken from where the vehicle's nose points once
        /// flattened onto the ground plane, so an inverted vehicle does not spin
        /// the camera.
        /// カメラが後ろに付くべき向き。機体自身の向きへ徐々に寄せる。機体の機首の
        /// 向きを水平面へ落としたものを使うので、反転した機体でカメラが回らない。
        /// </summary>
        private float SmoothedHeading()
        {
            Vector3 nose = target.forward;
            nose.y = 0.0f;

            bool isTooVertical = nose.sqrMagnitude < 1e-6f;
            if (isTooVertical)
            {
                return heading;
            }

            float wanted = Quaternion.LookRotation(nose.normalized).eulerAngles.y;
            float rate = Time.unscaledDeltaTime / Mathf.Max(smoothingSeconds, 1e-3f);
            return Mathf.LerpAngle(heading, wanted, Mathf.Clamp01(rate));
        }
    }
}
