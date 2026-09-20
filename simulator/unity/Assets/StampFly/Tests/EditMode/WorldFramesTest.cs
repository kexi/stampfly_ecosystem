using NUnit.Framework;
using StampFly.World;
using UnityEngine;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// What the frame conversion guarantees: it agrees with the numbers
    /// Schemas/README.md §6 states, and it is invertible, so a world can be
    /// read, edited and written back without drifting.
    /// 座標の変換が保証すること。README §6 が述べる数値と一致し、逆変換が
    /// できること。空間を読み、編集し、書き戻してもずれない。
    /// </summary>
    public sealed class WorldFramesTest
    {
        // Angles and lengths are compared to this, which is far tighter than
        // any difference a person could place an obstacle to.
        // 角と長さはこの値で比べる。人が障害物を置き分けられる差よりはるかに
        // 細かい。
        private const float Tolerance = 1e-4f;

        // -------------------------------------------------------------------
        // Position: the axis swap of README §6.1.
        // 位置。README §6.1 の軸の入れ替え。
        // -------------------------------------------------------------------

        [Test]
        public void EnuEastBecomesUnityRight()
        {
            Vector3 unity = WorldFrames.EnuToUnity(new[] { 1.0f, 0.0f, 0.0f });

            Assert.That(unity, Is.EqualTo(Vector3.right).Using(Vector3Comparer()));
        }

        [Test]
        public void EnuNorthBecomesUnityForward()
        {
            Vector3 unity = WorldFrames.EnuToUnity(new[] { 0.0f, 1.0f, 0.0f });

            Assert.That(unity, Is.EqualTo(Vector3.forward).Using(Vector3Comparer()));
        }

        [Test]
        public void EnuUpBecomesUnityUp()
        {
            Vector3 unity = WorldFrames.EnuToUnity(new[] { 0.0f, 0.0f, 1.0f });

            Assert.That(unity, Is.EqualTo(Vector3.up).Using(Vector3Comparer()));
        }

        [Test]
        public void PositionRoundTripsThroughUnity()
        {
            float[] enu = { 1.25f, -3.5f, 0.75f };

            float[] back = WorldFrames.UnityToEnu(WorldFrames.EnuToUnity(enu));

            Assert.That(back[0], Is.EqualTo(enu[0]).Within(Tolerance));
            Assert.That(back[1], Is.EqualTo(enu[1]).Within(Tolerance));
            Assert.That(back[2], Is.EqualTo(enu[2]).Within(Tolerance));
        }

        // -------------------------------------------------------------------
        // Obstacle rotation: README §6.2.
        // 障害物の回転。README §6.2。
        // -------------------------------------------------------------------

        [Test]
        [TestCase(0.0f, 1.0f, 0.0f, 0.0f)]
        [TestCase(90.0f, 0.0f, 1.0f, 0.0f)]
        [TestCase(180.0f, -1.0f, 0.0f, 0.0f)]
        [TestCase(-90.0f, 0.0f, -1.0f, 0.0f)]
        public void YawTurnsTheEnuEastAxisCounterClockwise(
            float yawDeg, float expectEast, float expectNorth, float expectUp)
        {
            // A yaw about ENU +z is counter-clockwise seen from above, so ENU
            // east swings toward north. The expectation is written in ENU.
            // ENU の +z まわりの yaw は上から見て反時計回りなので、ENU の東は
            // 北へ振れる。期待値は ENU で書く。
            Quaternion rotation = WorldFrames.EnuRotationToUnity(new[] { 0.0f, 0.0f, yawDeg });

            Vector3 turnedEast = rotation * WorldFrames.EnuToUnity(new[] { 1.0f, 0.0f, 0.0f });
            float[] enu = WorldFrames.UnityToEnu(turnedEast);

            Assert.That(enu[0], Is.EqualTo(expectEast).Within(Tolerance), "east component");
            Assert.That(enu[1], Is.EqualTo(expectNorth).Within(Tolerance), "north component");
            Assert.That(enu[2], Is.EqualTo(expectUp).Within(Tolerance), "up component");
        }

        [Test]
        public void PitchTipsTheEnuEastAxisDownward()
        {
            // A positive pitch about ENU +y is counter-clockwise seen from the
            // +y axis, which tips ENU east downward.
            // ENU の +y まわりの正の pitch は +y 軸から見て反時計回りで、ENU の
            // 東を下へ傾ける。
            const float pitchDeg = 90.0f;
            Quaternion rotation = WorldFrames.EnuRotationToUnity(new[] { 0.0f, pitchDeg, 0.0f });

            float[] enu = WorldFrames.UnityToEnu(rotation * WorldFrames.EnuToUnity(new[] { 1.0f, 0.0f, 0.0f }));

            Assert.That(enu[0], Is.EqualTo(0.0f).Within(Tolerance));
            Assert.That(enu[1], Is.EqualTo(0.0f).Within(Tolerance));
            Assert.That(enu[2], Is.EqualTo(-1.0f).Within(Tolerance), "east must tip down to -up");
        }

        [Test]
        public void RollTurnsTheEnuNorthAxisUpward()
        {
            // A positive roll about ENU +x is counter-clockwise seen from the
            // +x axis, which lifts ENU north toward up.
            // ENU の +x まわりの正の roll は +x 軸から見て反時計回りで、ENU の
            // 北を上へ持ち上げる。
            const float rollDeg = 90.0f;
            Quaternion rotation = WorldFrames.EnuRotationToUnity(new[] { rollDeg, 0.0f, 0.0f });

            float[] enu = WorldFrames.UnityToEnu(rotation * WorldFrames.EnuToUnity(new[] { 0.0f, 1.0f, 0.0f }));

            Assert.That(enu[0], Is.EqualTo(0.0f).Within(Tolerance));
            Assert.That(enu[1], Is.EqualTo(0.0f).Within(Tolerance));
            Assert.That(enu[2], Is.EqualTo(1.0f).Within(Tolerance), "north must lift to +up");
        }

        [Test]
        public void RotationIsAppliedYawThenPitchThenRoll()
        {
            // The format fixes R = Rz(yaw) Ry(pitch) Rx(roll) (README §3.2).
            // The reference matrix below is that product, evaluated in ENU, and
            // the conversion must land on the same place for a mixed angle.
            // 形式は R = Rz(yaw) Ry(pitch) Rx(roll) と定める（README §3.2）。
            // 下の参照行列はその積を ENU で評価したもので、3 つを混ぜた角でも
            // 変換が同じ場所へ着くこと。
            float[] rotationDeg = { 30.0f, -20.0f, 55.0f };
            Vector3 enuPoint = new Vector3(0.7f, -0.2f, 0.45f);

            Vector3 expected = MultiplyEnuRotation(rotationDeg, enuPoint);

            Quaternion rotation = WorldFrames.EnuRotationToUnity(rotationDeg);
            float[] got = WorldFrames.UnityToEnu(rotation * WorldFrames.EnuToUnity(enuPoint));

            Assert.That(got[0], Is.EqualTo(expected.x).Within(Tolerance));
            Assert.That(got[1], Is.EqualTo(expected.y).Within(Tolerance));
            Assert.That(got[2], Is.EqualTo(expected.z).Within(Tolerance));
        }

        [Test]
        [TestCase(0.0f, 0.0f, 0.0f)]
        [TestCase(0.0f, 0.0f, 45.0f)]
        [TestCase(0.0f, 0.0f, -45.0f)]
        [TestCase(0.0f, 0.0f, 90.0f)]
        [TestCase(0.0f, 0.0f, 180.0f)]
        [TestCase(30.0f, -20.0f, 55.0f)]
        [TestCase(-75.0f, 40.0f, -120.0f)]
        public void RotationRoundTripsBackToTheSameAngles(float roll, float pitch, float yaw)
        {
            // The inverse is exact while |pitch| stays away from 90 degrees,
            // where the decomposition is singular. No shipped world tilts an
            // obstacle that far.
            // |pitch| が 90 度から離れている限り逆変換は厳密である。90 度では
            // 分解が特異になるが、同梱のどの空間もそこまで傾けない。
            float[] rotationDeg = { roll, pitch, yaw };

            Quaternion unity = WorldFrames.EnuRotationToUnity(rotationDeg);
            float[] back = WorldFrames.UnityRotationToEnu(unity);

            Assert.That(DegreeDifference(back[0], roll), Is.EqualTo(0.0f).Within(Tolerance), "roll");
            Assert.That(DegreeDifference(back[1], pitch), Is.EqualTo(0.0f).Within(Tolerance), "pitch");
            Assert.That(DegreeDifference(back[2], yaw), Is.EqualTo(0.0f).Within(Tolerance), "yaw");
        }

        // -------------------------------------------------------------------
        // Spawn heading: README §6.2.1, including its -90 degree offset.
        // 出発点の方位。README §6.2.1、−90 度のずれを含む。
        // -------------------------------------------------------------------

        [Test]
        [TestCase(0.0f, 1.0f, 0.0f)]
        [TestCase(90.0f, 0.0f, 1.0f)]
        [TestCase(180.0f, -1.0f, 0.0f)]
        [TestCase(-90.0f, 0.0f, -1.0f)]
        public void SpawnYawPointsTheVehicleWhereTheFormatSays(
            float yawDeg, float expectEast, float expectNorth)
        {
            // README §6.2.1's own check table: yaw_deg 0 faces east, 90 north,
            // 180 west, -90 south. The vehicle model's forward is Unity +z.
            // README §6.2.1 の検算の表。yaw_deg は 0 が東、90 が北、180 が西、
            // −90 が南。機体モデルの前方は Unity の +z。
            Quaternion rotation = WorldFrames.SpawnYawToUnity(yawDeg);

            float[] forwardEnu = WorldFrames.UnityToEnu(rotation * Vector3.forward);

            Assert.That(forwardEnu[0], Is.EqualTo(expectEast).Within(Tolerance), "east component");
            Assert.That(forwardEnu[1], Is.EqualTo(expectNorth).Within(Tolerance), "north component");
            Assert.That(forwardEnu[2], Is.EqualTo(0.0f).Within(Tolerance), "a heading must stay level");
        }

        [Test]
        public void SpawnYawIsTheOffsetMinusTheHeading()
        {
            // The offset itself, because a vehicle model whose forward is not
            // +z would need this number changed and a test naming it makes
            // that visible.
            //
            // The angle is `offset - yaw`, not the `yaw - offset` that
            // Schemas/README.md §6.2.1 gives: with the spec's form, yaw_deg 0
            // faces WEST. The two forms agree at yaw 90 and -90, which is why
            // no shipped world reveals the error.
            //
            // ずれそのもの。機体モデルの前方が +z でなくなればこの数を直す必要が
            // あり、名指しの試験があればそれが見える。
            //
            // 角は README §6.2.1 の `yaw − offset` ではなく `offset − yaw` で
            // ある。仕様の形では yaw_deg 0 が**西**を向く。yaw が 90 と −90 では
            // 両者が一致するため、同梱のどの空間もこの誤りを表に出さない。
            Assert.That(WorldFrames.SpawnYawOffsetDeg, Is.EqualTo(90.0f));

            Quaternion rotation = WorldFrames.SpawnYawToUnity(0.0f);

            Assert.That(rotation.eulerAngles.y, Is.EqualTo(90.0f).Within(Tolerance));
        }

        [Test]
        [TestCase(0.0f)]
        [TestCase(90.0f)]
        [TestCase(180.0f)]
        [TestCase(-90.0f)]
        [TestCase(37.5f)]
        public void SpawnYawRoundTrips(float yawDeg)
        {
            Quaternion rotation = WorldFrames.SpawnYawToUnity(yawDeg);

            float back = WorldFrames.UnityToSpawnYaw(rotation);

            Assert.That(DegreeDifference(back, yawDeg), Is.EqualTo(0.0f).Within(Tolerance));
        }

        // -------------------------------------------------------------------
        // Helpers.
        // 補助。
        // -------------------------------------------------------------------

        /// <summary>
        /// Apply R = Rz(yaw) Ry(pitch) Rx(roll) in ENU directly, as the
        /// reference the conversion is checked against.
        /// ENU で R = Rz(yaw) Ry(pitch) Rx(roll) を直に適用する。変換を照らす
        /// 参照である。
        /// </summary>
        private static Vector3 MultiplyEnuRotation(float[] rotationDeg, Vector3 point)
        {
            float roll = rotationDeg[0] * Mathf.Deg2Rad;
            float pitch = rotationDeg[1] * Mathf.Deg2Rad;
            float yaw = rotationDeg[2] * Mathf.Deg2Rad;

            float cr = Mathf.Cos(roll), sr = Mathf.Sin(roll);
            float cp = Mathf.Cos(pitch), sp = Mathf.Sin(pitch);
            float cy = Mathf.Cos(yaw), sy = Mathf.Sin(yaw);

            return new Vector3(
                (cy * cp) * point.x + (cy * sp * sr - sy * cr) * point.y + (cy * sp * cr + sy * sr) * point.z,
                (sy * cp) * point.x + (sy * sp * sr + cy * cr) * point.y + (sy * sp * cr - cy * sr) * point.z,
                (-sp) * point.x + (cp * sr) * point.y + (cp * cr) * point.z);
        }

        /// <summary>
        /// The signed difference of two angles, wrapped to (-180, 180], so 359
        /// and -1 count as the same heading.
        /// 2 つの角の差を (−180, 180] に折り返して返す。359 度と −1 度が同じ
        /// 方位として扱われる。
        /// </summary>
        private static float DegreeDifference(float left, float right)
        {
            const float fullTurn = 360.0f;
            const float halfTurn = 180.0f;
            float difference = Mathf.Repeat(left - right + halfTurn, fullTurn) - halfTurn;
            return difference;
        }

        /// <summary>
        /// Compares vectors within the test's tolerance. / 許容の範囲で比べる。
        /// </summary>
        private static System.Collections.Generic.IEqualityComparer<Vector3> Vector3Comparer()
        {
            return new ApproximateVector3();
        }

        private sealed class ApproximateVector3 : System.Collections.Generic.IEqualityComparer<Vector3>
        {
            public bool Equals(Vector3 left, Vector3 right)
            {
                return (left - right).magnitude < Tolerance;
            }

            public int GetHashCode(Vector3 value) => value.GetHashCode();
        }
    }
}
