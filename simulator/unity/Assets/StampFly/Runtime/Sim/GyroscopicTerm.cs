/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — Euler gyroscopic term).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine;

namespace StampFly.Sim
{
    /// <summary>
    /// Euler's rigid-body equation carries the term -omega x (I*omega). Whether
    /// PhysX integrates it is a per-engine question, so this computes the term
    /// and the caller decides whether to add it (see the PlayMode check
    /// <c>GyroscopicTermTest</c>, which measures it rather than assuming).
    ///
    /// 剛体のオイラーの運動方程式には -omega x (I*omega) の項がある。PhysX が
    /// これを積分するかはエンジン次第なので、ここでは項を計算するだけにして、
    /// 足すかどうかは呼び出し側が決める（PlayMode の <c>GyroscopicTermTest</c>
    /// が前提を置かずに実測する）。
    /// </summary>
    public static class GyroscopicTerm
    {
        /// <summary>
        /// Returns -omega x (I*omega) in the body frame, the torque that must be
        /// applied for free rotation to follow Euler's equation.
        /// 機体座標系での -omega x (I*omega) を返す。自由回転がオイラーの式に
        /// 従うために掛ける必要のあるトルク。
        /// </summary>
        /// <param name="bodyAngularVelocity">Angular velocity in the body frame, rad/s.
        /// 機体座標系の角速度（rad/s）。</param>
        /// <param name="inertiaTensor">Principal moments in the same axis order.
        /// 同じ軸順の主慣性モーメント。</param>
        public static Vector3 BodyTorque(Vector3 bodyAngularVelocity, Vector3 inertiaTensor)
        {
            Vector3 angularMomentum = new Vector3(
                inertiaTensor.x * bodyAngularVelocity.x,
                inertiaTensor.y * bodyAngularVelocity.y,
                inertiaTensor.z * bodyAngularVelocity.z);
            return -Vector3.Cross(bodyAngularVelocity, angularMomentum);
        }

        /// <summary>
        /// The same torque, but with the body rate advanced half a step first.
        /// Evaluating at the step start injects energy into an explicit integrator:
        /// measured at 400 Hz, |I*omega| grew 10.8% per second that way and 0.006%
        /// this way, for the cost of one more cross product. This is the form the
        /// simulator uses.
        ///
        /// 同じトルクを、機体の角速度を半刻みだけ進めてから評価したもの。刻みの
        /// 先頭で評価すると陽的な積分にエネルギーが入る。400 Hz の実測で
        /// |I*omega| は先頭評価で 1 秒あたり 10.8% 増え、この方法では 0.006% だった。
        /// 追加の費用は外積 1 つ。シミュレータはこちらを使う。
        /// </summary>
        public static Vector3 BodyTorqueAtMidpoint(
            Vector3 bodyAngularVelocity, Vector3 inertiaTensor, float stepSeconds)
        {
            Vector3 torqueAtStart = BodyTorque(bodyAngularVelocity, inertiaTensor);
            Vector3 halfStepRate = bodyAngularVelocity + 0.5f * stepSeconds * new Vector3(
                torqueAtStart.x / inertiaTensor.x,
                torqueAtStart.y / inertiaTensor.y,
                torqueAtStart.z / inertiaTensor.z);
            return BodyTorque(halfStepRate, inertiaTensor);
        }

        /// <summary>
        /// The world-frame angular momentum I*omega, which free rotation conserves.
        /// 世界座標系の角運動量 I*omega。自由回転ではこれが保存する。
        /// </summary>
        public static Vector3 WorldAngularMomentum(Rigidbody body)
        {
            Vector3 bodyRate = Quaternion.Inverse(body.rotation) * body.angularVelocity;
            Vector3 inertia = body.inertiaTensor;
            Vector3 bodyMomentum = new Vector3(
                inertia.x * bodyRate.x,
                inertia.y * bodyRate.y,
                inertia.z * bodyRate.z);
            return body.rotation * bodyMomentum;
        }
    }
}
