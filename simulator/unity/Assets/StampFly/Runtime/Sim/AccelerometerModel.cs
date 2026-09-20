/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — accelerometer synthesis).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine;

namespace StampFly.Sim
{
    /// <summary>
    /// Builds the accelerometer reading the firmware expects, from the velocity
    /// change one <c>Physics.Simulate</c> produced. Contact forces are already in
    /// that change, so no contact model is needed here: at rest the reading is
    /// +9.81 along the vehicle's up axis, as on the real vehicle.
    ///
    /// 1 回の <c>Physics.Simulate</c> による速度変化から、ファームウェアが待つ
    /// 加速度計の測定値を作る。接触力はその変化に既に入っているので、ここに
    /// 接触モデルは要らない。静止時は実機と同じく機体の上向きに +9.81 になる。
    /// </summary>
    public static class AccelerometerModel
    {
        // Standard gravity as the MuJoCo model uses it, in m/s^2.
        // MuJoCo モデルと同じ重力加速度（m/s^2）。
        public const float GravityMetersPerSecondSquared = 9.81f;

        /// <summary>
        /// Returns the reading in the body frame: R^-1 * ((v_after - v_before)/dt - g).
        /// 機体座標系の測定値 R^-1 * ((v後 - v前)/dt - g) を返す。
        /// </summary>
        /// <param name="velocityBefore">World velocity before the step. / 刻み前の世界速度。</param>
        /// <param name="velocityAfter">World velocity after the step. / 刻み後の世界速度。</param>
        /// <param name="rotationAfter">Body rotation after the step. / 刻み後の機体の姿勢。</param>
        /// <param name="stepSeconds">Length of the step, s. / 刻みの長さ（s）。</param>
        public static Vector3 Read(
            Vector3 velocityBefore,
            Vector3 velocityAfter,
            Quaternion rotationAfter,
            float stepSeconds)
        {
            Vector3 worldAcceleration = (velocityAfter - velocityBefore) / stepSeconds;
            Vector3 gravity = new Vector3(0.0f, -GravityMetersPerSecondSquared, 0.0f);
            return Quaternion.Inverse(rotationAfter) * (worldAcceleration - gravity);
        }
    }
}
