using NUnit.Framework;
using StampFly.World;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// What the writer guarantees: reading a world, writing it and reading the
    /// result again gives the same world. The editing UI (a later stage) saves
    /// through this path, so a person's work must survive it unchanged.
    /// 書き出し側が保証すること。空間を読み、書き、もう一度読むと同じ空間に
    /// なる。編集 UI（後の段階）はこの道筋で保存するので、人の作業がそのまま
    /// 残らなければならない。
    /// </summary>
    public sealed class WorldWriterTest
    {
        private const float Tolerance = 1e-4f;

        [Test]
        [TestCaseSource(typeof(ShippedWorlds), nameof(ShippedWorlds.AllNames))]
        public void ShippedWorldSurvivesAReadWriteReadRoundTrip(string worldName)
        {
            WorldReadResult first = ShippedWorlds.Read(worldName);
            Assert.That(first.Ok, Is.True, first.Reason);

            string written = WorldWriter.Write(first.World);
            WorldReadResult second = WorldFileReader.Read(written);

            Assert.That(second.Ok, Is.True,
                        $"the world this writer produced was refused on reading: {second.Reason}");
            AssertSameWorld(first.World, second.World);
        }

        [Test]
        public void WrittenTextIsReadableByTheCheckersRules()
        {
            // The shipped files never carry `thickness` or `segments` on a
            // solid kind, because the schema forbids it. The writer must keep
            // that true, or `sf unity world validate` would reject what a
            // person just saved.
            // 同梱のファイルは中身の詰まった種類に thickness・segments を書か
            // ない。スキーマが禁じているためである。書き出し側もそれを保たねば
            // ならない。でなければ、人が保存したものを
            // `sf unity world validate` が断ることになる。
            WorldReadResult result = ShippedWorlds.Read("stepped_floor");
            Assert.That(result.Ok, Is.True, result.Reason);

            string written = WorldWriter.Write(result.World);

            Assert.That(written, Does.Not.Contain("\"thickness\""),
                        "stepped_floor holds no hollow obstacle, so no thickness may be written");
            Assert.That(written, Does.Not.Contain("\"segments\""),
                        "stepped_floor holds no ring, so no segments may be written");
        }

        [Test]
        public void HollowObstaclesKeepTheirThicknessAndSegments()
        {
            WorldReadResult result = ShippedWorlds.Read("gate_course");
            Assert.That(result.Ok, Is.True, result.Reason);

            string written = WorldWriter.Write(result.World);

            Assert.That(written, Does.Contain("\"thickness\""));
            Assert.That(written, Does.Contain("\"segments\""));
        }

        [Test]
        public void WrittenNumbersStayReadable()
        {
            // A float written at full round-trip precision turns 0.1 into
            // 0.10000000149011612, which makes a saved world unreadable and
            // its diff useless.
            // float を往復精度で書くと 0.1 が 0.10000000149011612 になり、保存
            // した空間が読めず、差分も役に立たなくなる。
            WorldReadResult result = ShippedWorlds.Read("gate_course");
            Assert.That(result.Ok, Is.True, result.Reason);

            string written = WorldWriter.Write(result.World);

            Assert.That(written, Does.Not.Contain("0.0000000"),
                        "a number must not be written at full float precision");
            Assert.That(written, Does.Not.Contain("E-"), "a number must not use exponent notation");
        }

        [Test]
        public void EmptyObstacleListSurvives()
        {
            WorldReadResult first = ShippedWorlds.Read("empty_room");
            Assert.That(first.Ok, Is.True, first.Reason);
            Assert.That(first.World.obstacles.Length, Is.EqualTo(0));

            WorldReadResult second = WorldFileReader.Read(WorldWriter.Write(first.World));

            Assert.That(second.Ok, Is.True, second.Reason);
            Assert.That(second.World.obstacles.Length, Is.EqualTo(0));
        }

        /// <summary>
        /// Compare two worlds field by field. / 空間 2 つを項目ごとに比べる。
        /// </summary>
        private static void AssertSameWorld(WorldFile expected, WorldFile got)
        {
            Assert.That(got.name, Is.EqualTo(expected.name));
            Assert.That(got.frame, Is.EqualTo(expected.frame));
            Assert.That(got.light, Is.EqualTo(expected.light));
            Assert.That(got.room.walls, Is.EqualTo(expected.room.walls));
            Assert.That(got.room.ceiling, Is.EqualTo(expected.room.ceiling));
            AssertSameVector(expected.room.size, got.room.size, "room.size");

            Assert.That(got.floor.pattern, Is.EqualTo(expected.floor.pattern));
            Assert.That(got.floor.pitch, Is.EqualTo(expected.floor.pitch).Within(Tolerance));
            Assert.That(got.floor.flow_quality, Is.EqualTo(expected.floor.flow_quality).Within(Tolerance),
                        "the floor's flow_quality must survive, 0 included");
            Assert.That(got.floor.colors, Is.EqualTo(expected.floor.colors));

            AssertSameVector(expected.spawn.position, got.spawn.position, "spawn.position");
            Assert.That(got.spawn.yaw_deg, Is.EqualTo(expected.spawn.yaw_deg).Within(Tolerance));

            AssertSameObstacles(expected, got);
        }

        private static void AssertSameObstacles(WorldFile expected, WorldFile got)
        {
            Assert.That(got.obstacles.Length, Is.EqualTo(expected.obstacles.Length));

            for (int index = 0; index < expected.obstacles.Length; index++)
            {
                WorldObstacle want = expected.obstacles[index];
                WorldObstacle have = got.obstacles[index];
                string where = $"obstacle '{want.id}'";

                Assert.That(have.id, Is.EqualTo(want.id), where);
                Assert.That(have.type, Is.EqualTo(want.type), where);
                Assert.That(have.color, Is.EqualTo(want.color), where);
                Assert.That(have.flow_quality, Is.EqualTo(want.flow_quality).Within(Tolerance), where);
                Assert.That(have.thickness, Is.EqualTo(want.thickness).Within(Tolerance), where);
                Assert.That(have.segments, Is.EqualTo(want.segments), where);
                AssertSameVector(want.position, have.position, $"{where}.position");
                AssertSameVector(want.rotation_deg, have.rotation_deg, $"{where}.rotation_deg");
                AssertSameVector(want.size, have.size, $"{where}.size");
            }
        }

        private static void AssertSameVector(float[] expected, float[] got, string where)
        {
            Assert.That(got.Length, Is.EqualTo(expected.Length), where);
            for (int axis = 0; axis < expected.Length; axis++)
            {
                Assert.That(got[axis], Is.EqualTo(expected[axis]).Within(Tolerance), $"{where}[{axis}]");
            }
        }
    }
}
