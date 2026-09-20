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
    /// </summary>
    public sealed class FollowCamera : MonoBehaviour
    {
        [Tooltip("What to keep in frame. 画面に収める対象。")]
        public Transform target;

        [Tooltip("How far behind the target the camera sits [m]. " +
                 "カメラが対象の後ろに下がる距離 [m]。")]
        public float distanceMeters = 1.2f;

        [Tooltip("How far above the target the camera sits [m]. " +
                 "カメラが対象の上に立つ高さ [m]。")]
        public float heightMeters = 0.55f;

        [Tooltip("How far above the target the camera looks [m]. " +
                 "カメラが対象より上を見る高さ [m]。")]
        public float lookAheadMeters = 0.08f;

        [Tooltip("Seconds for the camera to close most of a gap. " +
                 "カメラがずれの大半を詰めるのに掛ける秒数。")]
        public float smoothingSeconds = 0.25f;

        private Vector3 followVelocity;
        private float heading;

        private void LateUpdate()
        {
            bool hasTarget = target != null;
            if (!hasTarget)
            {
                return;
            }

            heading = SmoothedHeading();
            Vector3 behind = Quaternion.Euler(0.0f, heading, 0.0f) * Vector3.back;
            Vector3 wanted = target.position
                             + behind * distanceMeters
                             + Vector3.up * heightMeters;

            transform.position = Vector3.SmoothDamp(
                transform.position, wanted, ref followVelocity, smoothingSeconds,
                Mathf.Infinity, Time.unscaledDeltaTime);
            transform.LookAt(target.position + Vector3.up * lookAheadMeters);
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
