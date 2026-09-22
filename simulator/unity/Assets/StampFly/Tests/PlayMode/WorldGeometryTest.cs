using System.Collections.Generic;
using System.IO;
using NUnit.Framework;
using StampFly.World;
using UnityEngine;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// What these guarantee: a built world matches the geometry the checker
    /// (tools/unity_world/validate.py) assumes. The checker decides whether a
    /// world file is usable — obstacles inside the room, a clear takeoff
    /// column, openings wide enough — from per-type bounds it computes from
    /// `size`, `thickness` and the type's origin. If what Unity builds does not
    /// fill the same bounds, a world that passes the checker can still be
    /// unflyable, so these tests hold the two interpretations together.
    ///
    /// They also check what a sensor asks of the world: a ray through a gate's
    /// opening passes and a ray at its frame stops, and a ray down onto a pad
    /// or the floor comes back with that surface's flow quality.
    ///
    /// 保証すること: 生成した空間が、検査（tools/unity_world/validate.py）の
    /// 前提と同じ幾何になること。検査は size・thickness・種類ごとの原点から
    /// 外形を計算し、それで空間ファイルが使えるかを決める（部屋の中か、離陸の
    /// 余地があるか、開口が広いか）。Unity が作る物が同じ外形を満たさなければ、
    /// 検査を通った空間でも飛べないことがある。この試験が両者の解釈をつなぐ。
    ///
    /// センサが空間に求めることも見る。ゲートの開口を通るレイは抜け、枠に
    /// 当たるレイは止まること。pad や床へ下ろしたレイが、その面のフロー品質を
    /// 返すこと。
    /// </summary>
    public sealed class WorldGeometryTest
    {
        // Bounds are compared to this. A collider's Bounds is the renderer's
        // axis-aligned box, exact for the axis-aligned placements the shipped
        // worlds use; the slack absorbs float arithmetic only.
        // 外形はこの許容で比べる。コライダの Bounds は軸平行な箱で、同梱の空間
        // が使う軸に沿った配置では厳密である。この余裕は浮動小数の誤差ぶんだけ。
        private const float BoundsTolerance = 1e-3f;

        // A ray offset by less than half the vehicle's width stays inside an
        // opening the checker considers passable. The width is the collision
        // box's, taken from the generated parameters rather than restated here.
        // 機体の幅の半分未満だけずらしたレイは、検査が通れると見なす開口の中に
        // 留まる。幅は衝突箱のもので、ここに書き直さず生成されたパラメータから取る。
        private const float VehicleHalfWidthM = 0.5f * Sim.GeneratedParams.BoxSizeRight;

        private const string WorldsFolder = "Assets/StampFly/Worlds";

        private GameObject host;
        private WorldLoader loader;

        private static readonly string[] ShippedWorldNames =
        {
            "bedroom", "corridor_tunnel", "empty_room", "featureless_floor",
            "gate_course", "living_room", "pillar_forest", "stepped_floor", "study",
        };

        [SetUp]
        public void SetUp()
        {
            host = new GameObject("WorldLoaderHost");
            loader = host.AddComponent<WorldLoader>();
        }

        [TearDown]
        public void TearDown()
        {
            if (host != null)
            {
                Object.DestroyImmediate(host);
            }
        }

        // -------------------------------------------------------------------
        // Obstacle counts and per-type bounds.
        // 障害物の数と、種類ごとの外形。
        // -------------------------------------------------------------------

        [Test]
        [TestCaseSource(nameof(ShippedWorldNames))]
        public void ObstacleCountMatchesTheFile(string worldName)
        {
            WorldFile world = Load(worldName);

            int roots = CountObstacleRoots();

            Assert.That(roots, Is.EqualTo(world.obstacles.Length),
                        $"'{worldName}' must build exactly the obstacles its file lists");
        }

        [Test]
        [TestCaseSource(nameof(ShippedWorldNames))]
        public void EveryObstacleFillsTheBoundsTheCheckerAssumes(string worldName)
        {
            WorldFile world = Load(worldName);

            foreach (WorldObstacle obstacle in world.obstacles)
            {
                GameObject built = loader.Find(obstacle.id);
                Assert.That(built, Is.Not.Null, $"obstacle '{obstacle.id}' was not built");

                Bounds expected = ExpectedWorldBounds(obstacle);
                Bounds actual = ColliderBounds(built);

                AssertSameBounds(expected, actual, $"{worldName}/{obstacle.id} ({obstacle.type})");
            }
        }

        [Test]
        public void ShippedWorldsAndFurnitureCatalogCoverEveryObstacleKind()
        {
            // The bounds check above is only worth as much as its coverage, so
            // the kinds the shipped worlds exercise are counted here.
            // 上の外形の検査は、どれだけの種類を通るかで価値が決まる。ここで
            // 同梱の空間が使う種類を数える。
            HashSet<string> seen = new HashSet<string>();
            foreach (string worldName in ShippedWorldNames)
            {
                foreach (WorldObstacle obstacle in Load(worldName).obstacles)
                {
                    seen.Add(obstacle.type);
                }

                loader.Clear();
            }

            // Catalog defaults are also buildable geometry, not just type names.
            // カタログの既定値も、名前だけでなく生成した形状を検査する。
            foreach (string type in FurnitureCatalog.Types)
            {
                WorldFile world = Load("empty_room");
                WorldObstacle obstacle = FurnitureCatalog.CreateDefault(type, "catalog_item");
                world.obstacles = new[] { obstacle };
                loader.Build(world);
                AssertSameBounds(ExpectedWorldBounds(obstacle), ColliderBounds(loader.Find(obstacle.id)), type);
                seen.Add(type);
                loader.Clear();
            }

            Assert.That(seen, Is.EquivalentTo(WorldObstacleTypes.All),
                        "shipped worlds and catalog defaults must exercise every kind; "
                      + $"missing: {string.Join(", ", Missing(seen))}");
        }

        // -------------------------------------------------------------------
        // Hollow kinds: the opening must genuinely be open.
        // 中空の種類。開口が本当に空いていること。
        // -------------------------------------------------------------------

        [Test]
        public void AGatesOpeningIsClearAndItsFrameIsSolid()
        {
            Load("gate_course");
            WorldObstacle gate = FindObstacle("gate_course", "gate_1");

            // gate_1 is unrotated at ENU (0, -2.4, 0) with a 0.9 x 1.1 opening,
            // so a ray flown north through the opening's middle must pass, and
            // one aimed at an upright must stop.
            // gate_1 は ENU の (0, −2.4, 0) に回転無しで置かれ、開口は
            // 0.9 x 1.1。開口の中ほどを北へ抜けるレイは通り、柱を狙うレイは
            // 止まること。
            float openingHalfWidth = gate.size[0] / 2.0f;
            float openingMidHeight = gate.size[2] / 2.0f;
            float frameDepth = gate.size[1];
            Vector3 gateCentre = WorldFrames.EnuToUnity(gate.position);
            Vector3 through = gateCentre + Vector3.up * openingMidHeight;

            Assert.That(PassesThrough(through, Vector3.forward, frameDepth), Is.True,
                        "the gate's opening must be clear");

            // A ray at an upright must start outside the frame's depth, or
            // Unity reports no hit for a ray that begins inside a collider.
            // 柱を狙うレイは枠の奥行きの外から出す。コライダの内側から始まった
            // レイは Unity が当たりとして返さないためである。
            Vector3 atPost = through + Vector3.right * (openingHalfWidth + gate.thickness / 2.0f);
            Assert.That(PassesThrough(atPost, Vector3.forward, frameDepth), Is.False,
                        "the gate's upright must stop a ray");

            Vector3 atLintel = gateCentre + Vector3.up * (gate.size[2] + gate.thickness / 2.0f);
            Assert.That(PassesThrough(atLintel, Vector3.forward, frameDepth), Is.False,
                        "the gate's lintel must stop a ray");
        }

        [Test]
        public void ARingsHoleIsClearAndItsBarsAreSolid()
        {
            Load("gate_course");
            WorldObstacle ring = FindObstacle("gate_course", "gate_4");

            // gate_4 is an unrotated ring, so it is flown through heading north
            // and its origin is the centre of the opening (README §3.1).
            // gate_4 は回転の無い輪なので北向きにくぐる。原点は開口の中心
            // （README §3.1）。
            Vector3 centre = WorldFrames.EnuToUnity(ring.position);
            Quaternion rotation = WorldFrames.EnuRotationToUnity(ring.rotation_deg);
            Vector3 throughHole = rotation * Vector3.forward;
            float ringDepth = ring.thickness;

            Assert.That(PassesThrough(centre, throughHole, ringDepth), Is.True,
                        "the ring's hole must be clear");

            // The bars are inscribed in the circle of radius innerDiameter/2 +
            // thickness, so their own centreline sits a little inside that.
            // 棒は半径 innerDiameter/2 + thickness の円に内接するので、中心線は
            // そこより少し内側にある。
            float segmentAngle = 2.0f * Mathf.PI / ring.segments;
            float outerRadius = ring.size[0] / 2.0f + ring.thickness;
            float barRadius = outerRadius * Mathf.Cos(segmentAngle / 2.0f) - ring.thickness / 2.0f;

            Assert.That(PassesThrough(centre + rotation * Vector3.up * barRadius, throughHole, ringDepth),
                        Is.False, "the ring's top bar must stop a ray");
            Assert.That(PassesThrough(centre + rotation * Vector3.right * barRadius, throughHole, ringDepth),
                        Is.False, "the ring's side bar must stop a ray");
        }

        [Test]
        public void ATunnelsBoreIsClearAndItsWallsAreSolid()
        {
            Load("corridor_tunnel");
            WorldObstacle tunnel = FindObstacle("corridor_tunnel", TunnelId("corridor_tunnel"));

            // The origin is the centre of the entry opening's bottom edge, so
            // the bore runs from the origin toward ENU +y at mid-height.
            // 原点は入口の開口の下辺の中心。管は原点から ENU の +y 方向へ、
            // 開口の中ほどの高さで伸びる。
            Vector3 entry = WorldFrames.EnuToUnity(tunnel.position);
            Quaternion rotation = WorldFrames.EnuRotationToUnity(tunnel.rotation_deg);
            Vector3 alongBore = rotation * Vector3.forward;
            Vector3 up = rotation * Vector3.up;
            Vector3 across = rotation * Vector3.right;

            Vector3 insideBore = entry + up * (tunnel.size[2] / 2.0f);
            Assert.That(PassesThrough(insideBore, alongBore, tunnel.size[1]), Is.True,
                        "the tunnel's bore must be clear end to end");

            Vector3 atWall = insideBore + across * (tunnel.size[0] / 2.0f + tunnel.thickness / 2.0f);
            Assert.That(PassesThrough(atWall, alongBore, tunnel.size[1]), Is.False,
                        "the tunnel's wall must stop a ray");
        }

        // -------------------------------------------------------------------
        // Flow quality, read the way a sensor reads it.
        // フロー品質を、センサが読むのと同じ方法で読む。
        // -------------------------------------------------------------------

        [Test]
        public void ADownwardRayOntoAPadReadsThePadsFlowQuality()
        {
            WorldFile world = Load("featureless_floor");
            WorldObstacle pad = FindObstacle("featureless_floor", "pad_center");

            // featureless_floor exists to put a textured pad on an untextured
            // floor, so the two values must be distinguishable through a ray.
            // featureless_floor は模様の無い床に模様のある pad を置くための空間
            // なので、2 つの値がレイで見分けられなければならない。
            float above = pad.position[2] + pad.size[2] + 0.5f;
            Vector3 fromAbovePad = new Vector3(pad.position[0], above, pad.position[1]);

            float onPad = FlowQualityBelow(fromAbovePad);

            Assert.That(onPad, Is.EqualTo(pad.flow_quality).Within(1e-4f));
            Assert.That(onPad, Is.Not.EqualTo(world.floor.flow_quality).Within(1e-4f),
                        "the pad must read differently from the floor it sits on");
        }

        [Test]
        public void ADownwardRayOntoTheFloorReadsTheFloorsFlowQuality()
        {
            WorldFile world = Load("featureless_floor");

            // A corner of the room, well away from any pad.
            // どの pad からも離れた部屋の隅。
            float insetFromWall = 0.5f;
            float eastOfCentre = world.room.size[0] / 2.0f - insetFromWall;
            float northOfCentre = world.room.size[1] / 2.0f - insetFromWall;
            Vector3 fromAboveFloor = new Vector3(eastOfCentre, 0.5f, northOfCentre);

            float onFloor = FlowQualityBelow(fromAboveFloor);

            Assert.That(onFloor, Is.EqualTo(world.floor.flow_quality).Within(1e-4f),
                        "the floor's own flow_quality must reach the sensor, 0.05 included");
        }

        [Test]
        public void EveryColliderCarriesAnObstacleInfo()
        {
            // A ray that hits the world must always have an answer, so no
            // collider may be left without one.
            // 空間に当たったレイが必ず答えを持つよう、素性の無いコライダを
            // 残さない。
            Load("stepped_floor");

            foreach (Collider collider in host.GetComponentsInChildren<Collider>(true))
            {
                Assert.That(collider.GetComponent<ObstacleInfo>(), Is.Not.Null,
                            $"'{collider.name}' has a collider but no ObstacleInfo");
            }
        }

        // -------------------------------------------------------------------
        // Clearing.
        // 片付け。
        // -------------------------------------------------------------------

        [Test]
        public void LoadingAnotherWorldLeavesNothingOfThePreviousOne()
        {
            Load("pillar_forest");
            int pillars = CountObstacleRoots();
            Assert.That(pillars, Is.GreaterThan(0));

            WorldFile second = Load("gate_course");

            Assert.That(CountObstacleRoots(), Is.EqualTo(second.obstacles.Length),
                        "the previous world's obstacles must be gone, not merely hidden");
            Assert.That(loader.Find("pillar_01"), Is.Null,
                        "an obstacle of the previous world must no longer be findable");
            Assert.That(loader.Current.name, Is.EqualTo("gate_course"));
        }

        [Test]
        public void ClearingRemovesTheWorld()
        {
            Load("empty_room");

            loader.Clear();

            Assert.That(loader.HasWorld, Is.False);
            Assert.That(host.GetComponentsInChildren<ObstacleInfo>(true).Length, Is.EqualTo(0),
                        "clearing must leave no surface behind, floor and walls included");
        }

        [Test]
        public void SpawnPoseComesBackInUnityCoordinates()
        {
            WorldFile world = Load("gate_course");

            Vector3 spawn = loader.SpawnPosition;

            // gate_course spawns at ENU (0, -3.2, 0.05) facing north.
            // gate_course の出発点は ENU の (0, −3.2, 0.05) で北向き。
            Assert.That(spawn.x, Is.EqualTo(world.spawn.position[0]).Within(1e-4f), "east");
            Assert.That(spawn.y, Is.EqualTo(world.spawn.position[2]).Within(1e-4f), "up");
            Assert.That(spawn.z, Is.EqualTo(world.spawn.position[1]).Within(1e-4f), "north");

            float[] forwardEnu = WorldFrames.UnityToEnu(loader.SpawnRotation * Vector3.forward);
            Assert.That(forwardEnu[1], Is.EqualTo(1.0f).Within(1e-4f),
                        "yaw_deg 90 must face ENU north");
        }

        [Test]
        public void AnOffAxisSpawnHeadingSurvivesTheWholeLoad()
        {
            // empty_room spawns at yaw 45 on purpose: a heading of only +/-90
            // gives the same answer under both the correct expression and the
            // mirrored one the specification used to give, so it would hide a
            // sign error in the frame conversion. This checks the off-axis
            // heading end to end, through the file, the reader and the loader.
            // empty_room の出発点が yaw 45 なのは意図的である。方位が ±90 だけ
            // だと、正しい式と、仕様が以前示していた鏡像の式とで同じ答えになり、
            // 座標変換の符号の誤りを隠してしまう。ここでは軸から外れた方位が、
            // ファイル・読み込み・生成を通して端から端まで保たれることを見る。
            WorldFile world = Load("empty_room");
            Assert.That(world.spawn.yaw_deg, Is.EqualTo(45.0f).Within(1e-4f),
                        "empty_room must keep an off-axis spawn heading");

            float[] forwardEnu = WorldFrames.UnityToEnu(loader.SpawnRotation * Vector3.forward);

            const float diagonal = 0.70710678f;
            Assert.That(forwardEnu[0], Is.EqualTo(diagonal).Within(1e-4f), "east component");
            Assert.That(forwardEnu[1], Is.EqualTo(diagonal).Within(1e-4f), "north component");
            Assert.That(forwardEnu[2], Is.EqualTo(0.0f).Within(1e-4f), "a heading must stay level");
        }

        // -------------------------------------------------------------------
        // The per-type bounds, as tools/unity_world/validate.py computes them.
        // 種類ごとの外形。tools/unity_world/validate.py の計算と同じ。
        // -------------------------------------------------------------------

        /// <summary>
        /// The obstacle's world-frame axis-aligned bounds, in Unity axes,
        /// derived exactly as `_local_bounds` and `_world_aabb` in
        /// tools/unity_world/validate.py derive them.
        /// 障害物の世界系での軸平行な外形を Unity の軸で。
        /// tools/unity_world/validate.py の _local_bounds と _world_aabb と
        /// まったく同じ導き方をする。
        /// </summary>
        private static Bounds ExpectedWorldBounds(WorldObstacle obstacle)
        {
            LocalBox local = LocalBoundsOf(obstacle);

            // Rotate the eight corners and bound them, as the checker does.
            // 検査と同じく、8 隅を回してその範囲を取る。
            Quaternion rotation = WorldFrames.EnuRotationToUnity(obstacle.rotation_deg);
            Vector3 origin = WorldFrames.EnuToUnity(obstacle.position);

            Vector3 low = Vector3.positiveInfinity;
            Vector3 high = Vector3.negativeInfinity;
            foreach (Vector3 corner in local.Corners())
            {
                Vector3 placed = rotation * WorldFrames.EnuToUnity(corner) + origin;
                low = Vector3.Min(low, placed);
                high = Vector3.Max(high, placed);
            }

            Bounds bounds = new Bounds();
            bounds.SetMinMax(low, high);
            return bounds;
        }

        /// <summary>
        /// The obstacle's bounds in its own ENU frame, before rotation.
        /// 回転の前の、障害物自身の ENU の座標系での外形。
        /// </summary>
        private static LocalBox LocalBoundsOf(WorldObstacle obstacle)
        {
            float sizeX = obstacle.size[0];
            float sizeY = obstacle.size[1];
            float sizeZ = obstacle.size[2];
            float thickness = obstacle.thickness;

            switch (obstacle.type)
            {
                case WorldObstacleTypes.Gate:
                    return new LocalBox(
                        new Vector3(-sizeX / 2.0f - thickness, -sizeY / 2.0f, 0.0f),
                        new Vector3(sizeX / 2.0f + thickness, sizeY / 2.0f, sizeZ + thickness));
                case WorldObstacleTypes.Tunnel:
                    return new LocalBox(
                        new Vector3(-sizeX / 2.0f - thickness, 0.0f, 0.0f),
                        new Vector3(sizeX / 2.0f + thickness, sizeY, sizeZ + thickness));
                case WorldObstacleTypes.Ring:
                    float outer = sizeX / 2.0f + thickness;
                    return new LocalBox(
                        new Vector3(-outer, -thickness / 2.0f, -outer),
                        new Vector3(outer, thickness / 2.0f, outer));
                case WorldObstacleTypes.Ramp:
                    return new LocalBox(
                        new Vector3(-sizeX / 2.0f, 0.0f, 0.0f),
                        new Vector3(sizeX / 2.0f, sizeY, sizeZ));
                default:
                    // box, pillar, wall, step, pad and table all sit on their
                    // bottom face, centred horizontally.
                    // box・pillar・wall・step・pad・table は底面の上に立ち、
                    // 水平方向は中心に揃う。
                    return new LocalBox(
                        new Vector3(-sizeX / 2.0f, -sizeY / 2.0f, 0.0f),
                        new Vector3(sizeX / 2.0f, sizeY / 2.0f, sizeZ));
            }
        }

        /// <summary>
        /// An axis-aligned box in ENU, with its eight corners.
        /// ENU での軸平行な箱と、その 8 隅。
        /// </summary>
        private readonly struct LocalBox
        {
            private readonly Vector3 low;
            private readonly Vector3 high;

            public LocalBox(Vector3 low, Vector3 high)
            {
                this.low = low;
                this.high = high;
            }

            public IEnumerable<Vector3> Corners()
            {
                foreach (float x in new[] { low.x, high.x })
                {
                    foreach (float y in new[] { low.y, high.y })
                    {
                        yield return new Vector3(x, y, low.z);
                        yield return new Vector3(x, y, high.z);
                    }
                }
            }
        }

        // -------------------------------------------------------------------
        // Helpers.
        // 補助。
        // -------------------------------------------------------------------

        private WorldFile Load(string worldName)
        {
            string json = File.ReadAllText(Path.Combine(WorldsFolder, worldName + WorldFormat.FileSuffix));
            bool ok = loader.Load(json, worldName);
            Assert.That(ok, Is.True, $"'{worldName}' failed to load");
            return loader.Current;
        }

        private static WorldObstacle FindObstacle(string worldName, string obstacleId)
        {
            string json = File.ReadAllText(Path.Combine(WorldsFolder, worldName + WorldFormat.FileSuffix));
            WorldReadResult result = WorldFileReader.Read(json);
            Assert.That(result.Ok, Is.True, result.Reason);

            foreach (WorldObstacle obstacle in result.World.obstacles)
            {
                if (obstacle.id == obstacleId)
                {
                    return obstacle;
                }
            }

            Assert.Fail($"'{worldName}' holds no obstacle '{obstacleId}'");
            return null;
        }

        /// <summary>
        /// The id of the one tunnel in a world. / 空間にある唯一の tunnel の id。
        /// </summary>
        private static string TunnelId(string worldName)
        {
            string json = File.ReadAllText(Path.Combine(WorldsFolder, worldName + WorldFormat.FileSuffix));
            WorldReadResult result = WorldFileReader.Read(json);
            foreach (WorldObstacle obstacle in result.World.obstacles)
            {
                if (obstacle.type == WorldObstacleTypes.Tunnel)
                {
                    return obstacle.id;
                }
            }

            Assert.Fail($"'{worldName}' holds no tunnel");
            return null;
        }

        /// <summary>
        /// How many obstacle roots the loader built. A root is a direct child
        /// of the world root that carries an ObstacleInfo.
        /// 生成した障害物の根の数。根とは、空間の根の直下にあって ObstacleInfo を
        /// 持つものである。
        /// </summary>
        private int CountObstacleRoots()
        {
            int count = 0;
            foreach (Transform worldRoot in host.transform)
            {
                foreach (Transform child in worldRoot)
                {
                    ObstacleInfo info = child.GetComponent<ObstacleInfo>();
                    bool isObstacle = info != null
                                      && info.ObstacleType != ObstacleInfo.FloorType
                                      && info.ObstacleType != ObstacleInfo.RoomSurfaceType;
                    if (isObstacle)
                    {
                        count++;
                    }
                }
            }

            return count;
        }

        /// <summary>
        /// The combined bounds of every collider under an obstacle.
        /// 障害物の下にある全コライダを合わせた外形。
        /// </summary>
        private static Bounds ColliderBounds(GameObject obstacleRoot)
        {
            Collider[] colliders = obstacleRoot.GetComponentsInChildren<Collider>(true);
            Assert.That(colliders.Length, Is.GreaterThan(0),
                        $"'{obstacleRoot.name}' must have at least one collider");

            Bounds combined = colliders[0].bounds;
            for (int index = 1; index < colliders.Length; index++)
            {
                combined.Encapsulate(colliders[index].bounds);
            }

            return combined;
        }

        private static void AssertSameBounds(Bounds expected, Bounds actual, string where)
        {
            Assert.That(actual.min.x, Is.EqualTo(expected.min.x).Within(BoundsTolerance), $"{where} min.x");
            Assert.That(actual.min.y, Is.EqualTo(expected.min.y).Within(BoundsTolerance), $"{where} min.y");
            Assert.That(actual.min.z, Is.EqualTo(expected.min.z).Within(BoundsTolerance), $"{where} min.z");
            Assert.That(actual.max.x, Is.EqualTo(expected.max.x).Within(BoundsTolerance), $"{where} max.x");
            Assert.That(actual.max.y, Is.EqualTo(expected.max.y).Within(BoundsTolerance), $"{where} max.y");
            Assert.That(actual.max.z, Is.EqualTo(expected.max.z).Within(BoundsTolerance), $"{where} max.z");
        }

        /// <summary>
        /// Whether a ray started short of an obstacle reaches the far side.
        /// The start is backed off by the vehicle's half width so the ray
        /// begins outside whatever it is passing through.
        /// 障害物の手前から出したレイが向こう側へ抜けるか。機体の半分の幅だけ
        /// 手前から出し、通り抜ける物の外から始まるようにする。
        /// </summary>
        private static bool PassesThrough(Vector3 midpoint)
        {
            return PassesThrough(midpoint, Vector3.forward, 0.0f);
        }

        private static bool PassesThrough(Vector3 midpoint, Vector3 direction, float depth)
        {
            float backOff = depth / 2.0f + VehicleHalfWidthM;
            Vector3 start = midpoint - direction.normalized * backOff;
            float distance = 2.0f * backOff;
            return !Physics.Raycast(start, direction.normalized, distance);
        }

        /// <summary>
        /// The flow quality of whatever lies below a point, read the way the
        /// flow sensor will read it (plan stage 5).
        /// ある点の下にある面のフロー品質を、オプティカルフローのセンサが読む
        /// のと同じ方法で読む（計画の段階 5）。
        /// </summary>
        private static float FlowQualityBelow(Vector3 from)
        {
            const float reach = 5.0f;
            bool hitSomething = Physics.Raycast(from, Vector3.down, out RaycastHit hit, reach);
            Assert.That(hitSomething, Is.True, $"nothing lies below {from}");

            ObstacleInfo info = hit.collider.GetComponent<ObstacleInfo>();
            Assert.That(info, Is.Not.Null, $"'{hit.collider.name}' carries no ObstacleInfo");
            return info.FlowQuality;
        }

        private static IEnumerable<string> Missing(HashSet<string> seen)
        {
            foreach (string type in WorldObstacleTypes.All)
            {
                if (!seen.Contains(type))
                {
                    yield return type;
                }
            }
        }
    }
}
