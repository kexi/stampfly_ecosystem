using NUnit.Framework;
using StampFly.World;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// What the reader guarantees: every shipped world loads, and a file that
    /// is not a usable world file is refused with a reason that names what is
    /// wrong.
    /// 読み込み側が保証すること。同梱の空間は全て読め、使えないファイルは、何が
    /// 悪いかを述べた理由とともに断られる。
    /// </summary>
    public sealed class WorldFileReaderTest
    {
        [Test]
        [TestCaseSource(typeof(ShippedWorlds), nameof(ShippedWorlds.AllNames))]
        public void ShippedWorldLoads(string worldName)
        {
            WorldReadResult result = ShippedWorlds.Read(worldName);

            Assert.That(result.Ok, Is.True, $"'{worldName}' was refused: {result.Reason}");
            Assert.That(result.World.name, Is.EqualTo(worldName),
                        "a shipped world's `name` must match its file stem");
            Assert.That(result.World.obstacles, Is.Not.Null);
            Assert.That(result.World.room.size.Length, Is.EqualTo(WorldFormat.VectorLength));
        }

        [Test]
        public void MalformedJsonIsRefused()
        {
            WorldReadResult result = WorldFileReader.Read("{ this is not json");

            Assert.That(result.Ok, Is.False);
            Assert.That(result.Reason, Is.Not.Null.And.Not.Empty);
        }

        [Test]
        public void EmptyTextIsRefused()
        {
            WorldReadResult result = WorldFileReader.Read("");

            Assert.That(result.Ok, Is.False);
            Assert.That(result.Reason, Does.Contain("empty"));
        }

        [Test]
        public void WrongFormatMarkerIsRefused()
        {
            string json = ShippedWorlds.Json("empty_room")
                .Replace("\"stampfly-world\"", "\"some-other-world\"");

            WorldReadResult result = WorldFileReader.Read(json);

            Assert.That(result.Ok, Is.False);
            Assert.That(result.Reason, Does.Contain("format"),
                        "the reason must name the format marker as the problem");
            Assert.That(result.Reason, Does.Contain("stampfly-world"));
        }

        [Test]
        public void WrongVersionIsRefused()
        {
            string json = ShippedWorlds.Json("empty_room").Replace("\"version\": 1", "\"version\": 2");

            WorldReadResult result = WorldFileReader.Read(json);

            Assert.That(result.Ok, Is.False);
            Assert.That(result.Reason, Does.Contain("version"));
        }

        [Test]
        public void WrongFrameIsRefused()
        {
            // A file in NED would place every obstacle somewhere else, so it
            // must never be read as if it were ENU.
            // NED のファイルは障害物を別の場所に置くので、ENU として読んでは
            // ならない。
            string json = ShippedWorlds.Json("empty_room").Replace("\"frame\": \"ENU\"", "\"frame\": \"NED\"");

            WorldReadResult result = WorldFileReader.Read(json);

            Assert.That(result.Ok, Is.False);
            Assert.That(result.Reason, Does.Contain("frame"));
            Assert.That(result.Reason, Does.Contain("ENU"));
        }

        [Test]
        public void UnknownObstacleTypeIsRefused()
        {
            string json = ShippedWorlds.Json("pillar_forest").Replace("\"pillar\"", "\"obelisk\"");

            WorldReadResult result = WorldFileReader.Read(json);

            Assert.That(result.Ok, Is.False);
            Assert.That(result.Reason, Does.Contain("obelisk"));
        }

        [Test]
        public void AbsentLightBecomesIndoor()
        {
            // Every shipped world states a `light`, so one is written without
            // it here: the reader must supply the format's default rather than
            // leave it empty.
            // 同梱の空間はどれも light を書いている。そこで light の無い空間を
            // 組み立てる。読み込み側が空のままにせず、形式の既定を入れること。
            string json = MinimalWorld(light: null);

            WorldReadResult result = WorldFileReader.Read(json);

            Assert.That(result.Ok, Is.True, result.Reason);
            Assert.That(result.World.light, Is.EqualTo(WorldFormat.DefaultLight));
        }

        [Test]
        public void AbsentRingSegmentsBecomeTwentyFour()
        {
            string json = ShippedWorlds.Json("gate_course").Replace(",\n      \"segments\": 24", "");
            Assert.That(json, Does.Not.Contain("segments"), "the test's own edit must have applied");

            WorldReadResult result = WorldFileReader.Read(json);

            Assert.That(result.Ok, Is.True, result.Reason);
            int rings = 0;
            foreach (WorldObstacle obstacle in result.World.obstacles)
            {
                if (obstacle.type == WorldObstacleTypes.Ring)
                {
                    rings++;
                    Assert.That(obstacle.segments, Is.EqualTo(WorldFormat.DefaultRingSegments));
                }
            }

            Assert.That(rings, Is.GreaterThan(0), "gate_course must still contain rings");
        }

        [Test]
        public void AbsentObstacleFlowQualityBecomesDefault()
        {
            // The shipped worlds all state flow_quality, so an obstacle without
            // one is written here to check the default is applied.
            // 同梱の空間はどれも flow_quality を書いている。既定が効くことを
            // 見るため、flow_quality の無い障害物を組み立てる。
            string json = MinimalWorld(light: WorldLightPresets.Indoor, withObstacle: true);

            WorldReadResult result = WorldFileReader.Read(json);

            Assert.That(result.Ok, Is.True, result.Reason);
            Assert.That(result.World.obstacles.Length, Is.EqualTo(1));
            Assert.That(result.World.obstacles[0].flow_quality,
                        Is.EqualTo(WorldFormat.DefaultObstacleFlowQuality).Within(1e-6f),
                        "an obstacle's flow_quality must take the default when absent");
        }

        /// <summary>
        /// The smallest valid world, optionally without a `light` and
        /// optionally with one obstacle that states no flow_quality. Written
        /// out here rather than patched from a shipped file, because these two
        /// cases are precisely the ones no shipped file exercises.
        /// いちばん小さい正しい空間。light を省くか、flow_quality を書かない
        /// 障害物を 1 個入れるかを選べる。同梱のファイルを書き換えるのではなく
        /// ここに書き出すのは、この 2 つがどの同梱ファイルにも無い場合だから。
        /// </summary>
        private static string MinimalWorld(string light, bool withObstacle = false)
        {
            string lightLine = light == null ? "" : $"\"light\": \"{light}\",";
            string obstacles = withObstacle
                ? "{\"id\": \"a_box\", \"type\": \"box\", \"position\": [0, 1, 0], "
                + "\"rotation_deg\": [0, 0, 0], \"size\": [0.2, 0.2, 0.2], \"color\": \"#ff0000\"}"
                : "";

            return "{"
                 + $"\"format\": \"{WorldFormat.Marker}\", \"version\": {WorldFormat.Version},"
                 + "\"units\": {\"length\": \"m\", \"angle\": \"deg\"},"
                 + $"\"frame\": \"{WorldFormat.Frame}\", \"name\": \"tiny\","
                 + "\"room\": {\"size\": [4, 4, 2.5], \"walls\": true, \"ceiling\": true},"
                 + "\"floor\": {\"pattern\": \"plain\", \"pitch\": 1.0, "
                 + "\"colors\": [\"#cccccc\", \"#888888\"], \"flow_quality\": 0.9},"
                 + lightLine
                 + "\"spawn\": {\"position\": [0, 0, 0.05], \"yaw_deg\": 90},"
                 + $"\"obstacles\": [{obstacles}]"
                 + "}";
        }

        [Test]
        public void FloorFlowQualityOfZeroPointZeroFiveSurvives()
        {
            // featureless_floor depends on a low floor value reaching the
            // builder unchanged; a default must not be applied to the floor.
            // featureless_floor は床の低い値がそのまま生成側へ届くことに頼る。
            // 床には既定を当ててはならない。
            WorldReadResult result = ShippedWorlds.Read("featureless_floor");

            Assert.That(result.Ok, Is.True, result.Reason);
            Assert.That(result.World.floor.flow_quality, Is.EqualTo(0.05f).Within(1e-6f));
        }

        [Test]
        public void HollowObstacleWithoutThicknessIsRefused()
        {
            string json = ShippedWorlds.Json("gate_course").Replace("\"thickness\": 0.06,", "");

            WorldReadResult result = WorldFileReader.Read(json);

            Assert.That(result.Ok, Is.False);
            Assert.That(result.Reason, Does.Contain("thickness"));
        }
    }
}
