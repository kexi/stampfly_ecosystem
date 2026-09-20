/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — measuring PhysX's torque response).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Globalization;
using UnityEngine;

namespace StampFly.Sim
{
    /// <summary>
    /// Applies a known torque to the body for a known number of steps and
    /// reports the angular acceleration it produced, with no firmware in the
    /// loop at all.
    ///
    /// 既知のトルクを既知の刻み数だけ機体に加え、生じた角加速度を返す。ループに
    /// ファームウェアは一切入らない。
    ///
    /// ## Why this exists / なぜこれがあるか
    ///
    /// Stage 1(c) measured PhysX's torque response at tau/I within 0.0000% —
    /// but only in the macOS editor. The WebGL player runs a different build of
    /// PhysX under IL2CPP, and every flight flown in the browser so far has
    /// been perfectly symmetric, so the roll and pitch angles never left 0.0
    /// degrees. Whether a torque turns the body in a browser had therefore
    /// never actually been checked.
    ///
    /// 段階 1(c) は PhysX のトルク応答を tau/I に対し誤差 0.0000% と測ったが、
    /// 測ったのは macOS のエディタだけである。WebGL のプレイヤーは IL2CPP の下で
    /// 別ビルドの PhysX を動かす。そしてこれまでブラウザで飛ばした飛行は完全に
    /// 対称で、ロール・ピッチの角は 0.0 度から動いたことがない。よって
    /// **ブラウザの中でトルクが機体を回すのかは、一度も確かめられていなかった。**
    ///
    /// ## What it measures / 何を測るか
    ///
    /// For each body axis: zero the velocities, apply a constant torque for N
    /// steps of <see cref="PhysicsStepSettings.StepSeconds"/>, and divide the
    /// body-frame angular velocity gained by the time. A correct engine answers
    /// tau/I. Gravity is not switched off: it produces no torque about the
    /// centre of mass, and the body is placed clear of the floor so no contact
    /// can.
    ///
    /// 機体の軸ごとに、速度を 0 にし、一定のトルクを
    /// <see cref="PhysicsStepSettings.StepSeconds"/> の N 刻みだけ加え、得られた
    /// 機体系の角速度を時間で割る。正しいエンジンは tau/I を答える。重力は切らない。
    /// 重心まわりにトルクを生まないためである。機体は床から離して置くので、接触も
    /// トルクを生まない。
    ///
    /// @design simulator/unity/README.md §3 段階 1(c) の検証
    /// </summary>
    public static class TorqueProbe
    {
        /// <summary>
        /// The torque applied, in N·m. Small enough to stay in the linear
        /// regime and large enough that the angular velocity gained is far
        /// above any floating-point noise.
        /// 加えるトルク [N·m]。線形の範囲に留まるだけ小さく、得られる角速度が
        /// 浮動小数の雑音よりはるかに大きいだけ大きい。
        /// </summary>
        public const float TorqueNewtonMetres = 1.0e-4f;

        /// <summary>How many steps the torque is applied for. / トルクを加える刻みの数。</summary>
        public const int DefaultSteps = 40;

        /// <summary>
        /// How far above the floor the body is lifted while probing, so no
        /// contact can add a torque of its own.
        /// 測定中に機体を床から持ち上げる高さ。接触が自前のトルクを足さないように
        /// するためである。
        /// </summary>
        public const float ProbeHeightMetres = 2.0f;

        /// <summary>
        /// What one axis answered: the torque asked for, the angular
        /// acceleration measured, and what the inertia tensor says it should be.
        /// ある軸の答え。求めたトルク、測った角加速度、慣性テンソルが言う値。
        /// </summary>
        public readonly struct AxisResult
        {
            public AxisResult(string axis, float torque, float inertia,
                              float measured)
            {
                Axis = axis;
                Torque = torque;
                Inertia = inertia;
                Measured = measured;
            }

            /// <summary>Which body axis. / どの機体軸か。</summary>
            public string Axis { get; }

            /// <summary>The torque applied [N·m]. / 加えたトルク [N·m]。</summary>
            public float Torque { get; }

            /// <summary>The body's inertia about it [kg·m²]. / その軸まわりの慣性 [kg·m²]。</summary>
            public float Inertia { get; }

            /// <summary>The angular acceleration measured [rad/s²]. / 測った角加速度 [rad/s²]。</summary>
            public float Measured { get; }

            /// <summary>What tau/I says it should be [rad/s²]. / tau/I が言う値 [rad/s²]。</summary>
            public float Expected => Torque / Inertia;

            /// <summary>
            /// How far the measurement is from tau/I, as a fraction. Zero is a
            /// perfect answer; 1.0 means the body did not turn at all.
            /// 測定値が tau/I からどれだけ離れているか（割合）。0 が完全な答えで、
            /// 1.0 は機体が全く回らなかったことを意味する。
            /// </summary>
            public float RelativeError
            {
                get
                {
                    bool hasNoExpectation = Mathf.Approximately(Expected, 0.0f);
                    if (hasNoExpectation)
                    {
                        return 0.0f;
                    }
                    return Mathf.Abs(Measured - Expected) / Mathf.Abs(Expected);
                }
            }
        }

        /// <summary>
        /// Probe all three body axes and return what each answered. The body is
        /// left where it was found, at rest.
        /// 3 つの機体軸すべてを測り、それぞれの答えを返す。機体は元の場所へ、静止
        /// させて戻す。
        /// </summary>
        public static AxisResult[] Run(Rigidbody body, int steps = DefaultSteps)
        {
            Vector3 position = body.position;
            Quaternion rotation = body.rotation;
            bool hadGravity = body.useGravity;

            // Lift clear of the floor and switch gravity off: a body that falls
            // during the probe can touch something, and a contact would add a
            // torque this measurement would then blame on PhysX.
            // 床から離し、重力を切る。測定中に落ちた機体は何かに触れうる。接触は
            // トルクを足し、この測定はそれを PhysX のせいにしてしまう。
            body.useGravity = false;
            body.position = position + Vector3.up * ProbeHeightMetres;
            Physics.SyncTransforms();

            Vector3 inertia = body.inertiaTensor;
            var results = new[]
            {
                Probe(body, "x_pitch", Vector3.right, inertia.x, steps),
                Probe(body, "y_yaw", Vector3.up, inertia.y, steps),
                Probe(body, "z_roll", Vector3.forward, inertia.z, steps),
            };

            body.linearVelocity = Vector3.zero;
            body.angularVelocity = Vector3.zero;
            body.position = position;
            body.rotation = rotation;
            body.useGravity = hadGravity;
            Physics.SyncTransforms();

            return results;
        }

        /// <summary>
        /// One axis: from rest, apply the torque for <paramref name="steps"/>
        /// steps and divide the body-frame rate gained by the time.
        /// 軸 1 つ。静止から <paramref name="steps"/> 刻みだけトルクを加え、得た
        /// 機体系の角速度を時間で割る。
        /// </summary>
        private static AxisResult Probe(Rigidbody body, string name,
                                        Vector3 axis, float inertia, int steps)
        {
            body.linearVelocity = Vector3.zero;
            body.angularVelocity = Vector3.zero;
            body.rotation = Quaternion.identity;
            Physics.SyncTransforms();

            for (int step = 0; step < steps; step++)
            {
                body.AddRelativeTorque(axis * TorqueNewtonMetres, ForceMode.Force);
                Physics.Simulate(PhysicsStepSettings.StepSeconds);
            }

            // The rate in the BODY frame, which is where the torque was applied.
            // トルクを加えたのは機体系なので、角速度も機体系で読む。
            Vector3 bodyRate = Quaternion.Inverse(body.rotation) * body.angularVelocity;
            float seconds = steps * PhysicsStepSettings.StepSeconds;
            float measured = Vector3.Dot(bodyRate, axis) / seconds;

            return new AxisResult(name, TorqueNewtonMetres, inertia, measured);
        }

        /// <summary>
        /// The results as JSON, plus the rigid-body settings a reader needs to
        /// interpret them: a body whose inertia was silently replaced, or whose
        /// rotation is constrained, answers wrongly for a reason that is not
        /// PhysX's arithmetic.
        /// 結果を JSON にする。読み手が解釈するのに要る剛体の設定も添える。慣性を
        /// 黙って置き換えられた機体や、回転を拘束された機体は誤った答えを返すが、
        /// その理由は PhysX の計算ではないからである。
        /// </summary>
        public static string ToJson(Rigidbody body, AxisResult[] results)
        {
            var culture = CultureInfo.InvariantCulture;
            var text = new System.Text.StringBuilder();

            text.Append("{\"body\":{")
                .Append("\"mass\":").Append(body.mass.ToString("R", culture))
                .Append(",\"inertia_tensor\":[")
                .Append(body.inertiaTensor.x.ToString("R", culture)).Append(',')
                .Append(body.inertiaTensor.y.ToString("R", culture)).Append(',')
                .Append(body.inertiaTensor.z.ToString("R", culture))
                .Append("],\"automatic_inertia\":")
                .Append(body.automaticInertiaTensor ? "true" : "false")
                .Append(",\"constraints\":").Append((int)body.constraints)
                .Append(",\"angular_damping\":")
                .Append(body.angularDamping.ToString("R", culture))
                .Append(",\"max_angular_velocity\":")
                .Append(body.maxAngularVelocity.ToString("R", culture))
                .Append(",\"sleep_threshold\":")
                .Append(body.sleepThreshold.ToString("R", culture))
                .Append(",\"is_kinematic\":").Append(body.isKinematic ? "true" : "false")
                .Append("},\"axes\":[");

            for (int index = 0; index < results.Length; index++)
            {
                AxisResult result = results[index];
                if (index > 0) { text.Append(','); }
                text.Append("{\"axis\":\"").Append(result.Axis)
                    .Append("\",\"torque\":").Append(result.Torque.ToString("R", culture))
                    .Append(",\"inertia\":").Append(result.Inertia.ToString("R", culture))
                    .Append(",\"expected\":").Append(result.Expected.ToString("R", culture))
                    .Append(",\"measured\":").Append(result.Measured.ToString("R", culture))
                    .Append(",\"relative_error\":")
                    .Append(result.RelativeError.ToString("R", culture))
                    .Append('}');
            }

            return text.Append("]}").ToString();
        }
    }
}
