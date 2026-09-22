using System.Collections.Generic;
using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// Builds the fifteen obstacle kinds from Unity primitives and generated
    /// meshes, with no imported assets, as the plan's §2 requires.
    ///
    /// Two rules shape the whole file:
    ///
    /// * Each kind's GameObject sits at the origin Schemas/README.md §3.1
    ///   defines for that kind, which is rarely a primitive's own centre. The
    ///   parts are therefore positioned in the obstacle's local frame, and only
    ///   the parent carries the world position and rotation.
    /// * Collision is only boxes, cylinders and convex meshes. A concave mesh
    ///   collider would be the obvious way to hollow out a ring or a tunnel,
    ///   but PhysX cannot use one on a moving body and it is slow to raycast;
    ///   the hollow kinds are therefore assembled from solid parts around the
    ///   opening, which also leaves the opening genuinely empty for a ToF ray.
    ///
    /// 障害物 15 種を、Unity の基本形状と手続き生成のメッシュだけで作る（計画
    /// §2）。外部の素材は使わない。
    ///
    /// 全体を貫く 2 つの決まり:
    ///
    /// * 各種類の GameObject は、README §3.1 がその種類に定めた原点に置く。
    ///   多くの場合それは基本形状自身の中心ではない。そこで部品は障害物の局所
    ///   座標系で配置し、世界での位置と回転は親だけが持つ。
    /// * 衝突は箱・円柱・凸メッシュだけにする。輪やトンネルの中空は非凸の
    ///   メッシュコライダが素直だが、PhysX では動く物体に使えず、レイも遅い。
    ///   そこで中空の種類は開口の周りの中身の詰まった部品を組んで作る。開口が
    ///   本当に空になるので ToF のレイにとっても都合がよい。
    /// </summary>
    public static class ObstacleFactory
    {
        // A table's top is this fraction of the top-surface height, so a low
        // table still gets a top thin enough to look like one.
        // 天板の厚みは天板上面の高さのこの割合。低い机でも天板らしい薄さになる。
        private const float TableTopThicknessFraction = 0.12f;

        // ... but never thinner than this, so it stays a solid collider.
        // ただしこれより薄くはしない。確かなコライダであり続けるため。
        private const float TableTopMinimumThickness = 0.01f;

        // A table leg is this fraction of the smaller top dimension.
        // 脚の太さは天板の短いほうの辺のこの割合。
        private const float TableLegFraction = 0.08f;

        private const float TableLegMinimumWidth = 0.01f;

        // The legs stand this far in from the top's edge, so they read as legs
        // rather than as walls at the corners.
        // 脚は天板の縁からこの割合だけ内側に立てる。隅の壁ではなく脚に見える。
        private const float TableLegInsetFraction = 0.12f;

        private const int TableLegCount = 4;

        // A ring is drawn as this many straight segments unless the file says
        // otherwise; the file's own default is the same number.
        // 輪はファイルの指定が無ければこの数の直線で描く。ファイル側の既定も同じ。
        private const int RingFallbackSegments = WorldFormat.DefaultRingSegments;

        /// <summary>
        /// Build one obstacle under <paramref name="parent"/> and return its
        /// root. The root carries an <see cref="ObstacleInfo"/>, as does every
        /// part with a collider.
        /// 障害物を 1 つ <paramref name="parent"/> の下に作り、その根を返す。
        /// 根にも、コライダを持つ各部品にも <see cref="ObstacleInfo"/> が付く。
        /// </summary>
        public static GameObject Create(WorldObstacle obstacle, Transform parent, WorldMaterials materials)
        {
            GameObject root = new GameObject(ObstacleName(obstacle));
            root.transform.SetParent(parent, false);
            root.transform.localPosition = WorldFrames.EnuToUnity(obstacle.position);
            root.transform.localRotation = WorldFrames.EnuRotationToUnity(obstacle.rotation_deg);

            ObstacleInfo.Attach(root, obstacle.id, obstacle.type, obstacle.flow_quality);
            Material material = materials.Solid(obstacle.color);
            BuildParts(obstacle, root.transform, material, materials);
            return root;
        }

        /// <summary>
        /// Dispatch to the builder of the obstacle's kind. / 種類ごとの生成へ振り分ける。
        /// </summary>
        private static void BuildParts(WorldObstacle obstacle, Transform root, Material material,
                                       WorldMaterials materials)
        {
            switch (obstacle.type)
            {
                case WorldObstacleTypes.Box:
                case WorldObstacleTypes.Step:
                case WorldObstacleTypes.Pad:
                case WorldObstacleTypes.Wall:
                    BuildBottomCentredBox(obstacle, root, material);
                    return;
                case WorldObstacleTypes.Pillar:
                    BuildPillar(obstacle, root, material);
                    return;
                case WorldObstacleTypes.Gate:
                    BuildGate(obstacle, root, material);
                    return;
                case WorldObstacleTypes.Ring:
                    BuildRing(obstacle, root, material);
                    return;
                case WorldObstacleTypes.Tunnel:
                    BuildTunnel(obstacle, root, material);
                    return;
                case WorldObstacleTypes.Table:
                    BuildTable(obstacle, root, material);
                    return;
                case WorldObstacleTypes.Chair:
                case WorldObstacleTypes.Sofa:
                case WorldObstacleTypes.Shelf:
                case WorldObstacleTypes.Bed:
                    FurnitureFactory.Build(obstacle, root, material);
                    return;
                case WorldObstacleTypes.BowlingPin:
                    BowlingPinFactory.Build(obstacle, root, material, materials);
                    return;
                case WorldObstacleTypes.Ramp:
                    BuildRamp(obstacle, root, material, materials);
                    return;
                default:
                    BuildBottomCentredBox(obstacle, root, material);
                    return;
            }
        }

        // -------------------------------------------------------------------
        // Solid kinds whose origin is the centre of the bottom face.
        // 原点が底面の中心である、中身の詰まった種類。
        // -------------------------------------------------------------------

        /// <summary>
        /// box, step, pad and wall: one cube of size[x, y, z] sitting on the
        /// origin's plane. For a wall, size is [length, thickness, height].
        /// box・step・pad・wall。size[x, y, z] の立方体 1 つを原点の面の上に
        /// 立てる。wall の size は [長さ, 厚み, 高さ]。
        /// </summary>
        private static void BuildBottomCentredBox(WorldObstacle obstacle, Transform root, Material material)
        {
            Vector3 extent = EnuExtentToUnity(obstacle.size);
            AddBox(root, "body", material, obstacle, BottomCentredCentre(extent), extent);
        }

        /// <summary>
        /// A pillar: the same box, drawn as a cylinder when it is square, so a
        /// forest of pillars reads as round posts rather than as square ones.
        /// The collider follows the drawing.
        /// pillar。同じ箱だが、断面が正方形なら円柱で描く。柱の林が角材ではなく
        /// 丸い柱に見える。コライダも描いたとおりにする。
        /// </summary>
        private static void BuildPillar(WorldObstacle obstacle, Transform root, Material material)
        {
            Vector3 extent = EnuExtentToUnity(obstacle.size);
            bool isSquare = Mathf.Approximately(obstacle.size[0], obstacle.size[1]);
            if (!isSquare)
            {
                AddBox(root, "body", material, obstacle, BottomCentredCentre(extent), extent);
                return;
            }

            AddCylinder(root, "body", material, obstacle, BottomCentredCentre(extent), extent);
        }

        // -------------------------------------------------------------------
        // Hollow kinds. size describes the OPENING; the outer extent is the
        // opening plus thickness (Schemas/README.md §3.1).
        // 中空の種類。size は**開口**で、外形は開口に thickness を足した大きさ
        // になる（README §3.1）。
        // -------------------------------------------------------------------

        /// <summary>
        /// A gate: two uprights and a lintel around the opening. The origin is
        /// the centre of the opening's bottom edge at floor level, so the
        /// opening runs from z=0 to z=size[2] and the frame surrounds it.
        /// validate.py bounds it as x +/- (size[0]/2 + thickness), y +/-
        /// size[1]/2, z 0..size[2] + thickness, which is what these parts fill.
        ///
        /// gate。開口の周りに柱 2 本と梁 1 本を置く。原点は開口の下辺の中心
        /// （床の高さ）なので、開口は z=0 から z=size[2] までで、枠がそれを囲む。
        /// validate.py の外形と一致する。
        /// </summary>
        private static void BuildGate(WorldObstacle obstacle, Transform root, Material material)
        {
            float openingWidth = obstacle.size[0];
            float depth = obstacle.size[1];
            float openingHeight = obstacle.size[2];
            float thickness = obstacle.thickness;

            // Uprights: one on each side of the opening, floor to the lintel.
            // 柱: 開口の両側に 1 本ずつ、床から梁まで。
            float postCentreX = (openingWidth + thickness) / 2.0f;
            Vector3 postSize = new Vector3(thickness, openingHeight, depth);
            AddBox(root, "post_west", material, obstacle,
                   new Vector3(-postCentreX, openingHeight / 2.0f, 0.0f), postSize);
            AddBox(root, "post_east", material, obstacle,
                   new Vector3(postCentreX, openingHeight / 2.0f, 0.0f), postSize);

            // Lintel: spans both uprights, sitting on top of the opening.
            // 梁: 開口の上に載り、両方の柱を渡す。
            Vector3 lintelSize = new Vector3(openingWidth + 2.0f * thickness, thickness, depth);
            AddBox(root, "lintel", material, obstacle,
                   new Vector3(0.0f, openingHeight + thickness / 2.0f, 0.0f), lintelSize);
        }

        /// <summary>
        /// A tunnel: a tube running toward ENU +y, open at both ends. The
        /// origin is the centre of the entry opening's bottom edge, so the tube
        /// runs from y=0 to y=size[1] and the opening from z=0 to z=size[2].
        /// Only walls and a roof are built -- no floor -- because the tunnel in
        /// corridor_tunnel rests on the room floor and a second floor inside it
        /// would be a step the vehicle has to climb.
        ///
        /// tunnel。ENU の +y 方向へ伸び、両端が開いた管。原点は入口の開口の下辺
        /// の中心なので、管は y=0 から y=size[1]、開口は z=0 から z=size[2]。
        /// 側壁と天井だけを作り、床は作らない。corridor_tunnel の管は部屋の床の
        /// 上に置かれるので、中に床をもう 1 枚作ると機体が越える段差になる。
        /// </summary>
        private static void BuildTunnel(WorldObstacle obstacle, Transform root, Material material)
        {
            float openingWidth = obstacle.size[0];
            float length = obstacle.size[1];
            float openingHeight = obstacle.size[2];
            float thickness = obstacle.thickness;

            float wallCentreX = (openingWidth + thickness) / 2.0f;
            float lengthCentreZ = length / 2.0f;   // ENU +y is Unity +z.
            Vector3 wallSize = new Vector3(thickness, openingHeight, length);
            AddBox(root, "wall_west", material, obstacle,
                   new Vector3(-wallCentreX, openingHeight / 2.0f, lengthCentreZ), wallSize);
            AddBox(root, "wall_east", material, obstacle,
                   new Vector3(wallCentreX, openingHeight / 2.0f, lengthCentreZ), wallSize);

            Vector3 roofSize = new Vector3(openingWidth + 2.0f * thickness, thickness, length);
            AddBox(root, "roof", material, obstacle,
                   new Vector3(0.0f, openingHeight + thickness / 2.0f, lengthCentreZ), roofSize);
        }

        /// <summary>
        /// A ring: a polygon of straight bars in the ENU x-z plane, thickness
        /// deep along ENU y, centred on the opening's centre. Flown through
        /// heading north when unrotated (Schemas/README.md §3.1).
        /// Each bar is its own box collider, so the hole stays genuinely empty
        /// without a concave mesh collider.
        ///
        /// ring。ENU の x-z 平面に並ぶ直線の棒の多角形で、ENU の y 方向の厚みが
        /// thickness。原点は開口の中心。回転が無ければ北向きにくぐる（§3.1）。
        /// 棒 1 本ごとに箱のコライダを持たせるので、非凸のメッシュコライダ無しで
        /// 穴が本当に空く。
        /// </summary>
        private static void BuildRing(WorldObstacle obstacle, Transform root, Material material)
        {
            float innerDiameter = obstacle.size[0];
            float thickness = obstacle.thickness;
            int segments = obstacle.segments > 0 ? obstacle.segments : RingFallbackSegments;

            // The checker (tools/unity_world/validate.py) bounds a ring by the
            // circle of radius innerDiameter/2 + thickness, so the polygon is
            // INSCRIBED in that circle: each bar's outer corners sit on it.
            // Cutting the bars to the chord of the outer circle does exactly
            // that, and the axis-aligned bounds then match the checker's for
            // any segment count. Placing the bars any further out would make
            // the corners stick past the bound the checker uses to decide
            // whether a ring fits in the room.
            // 検査（tools/unity_world/validate.py）は輪を半径
            // innerDiameter/2 + thickness の円で囲む。そこで多角形をその円に
            // **内接**させ、各棒の外側の角がその円の上に来るようにする。棒を外
            // 円の弦の長さに切ればそうなり、軸平行な外形が分割数によらず検査と
            // 一致する。これより外へ置くと、輪が部屋に収まるかを検査が判断する
            // 境界から角がはみ出す。
            float outerRadius = innerDiameter / 2.0f + thickness;
            float segmentAngle = 2.0f * Mathf.PI / segments;
            float barLength = 2.0f * outerRadius * Mathf.Sin(segmentAngle / 2.0f);

            // The bar's outer face is the chord's own distance from the centre,
            // and its centreline sits half a thickness inside that.
            // 棒の外側の面は弦そのものの中心からの距離にあり、中心線はそこから
            // 太さの半分だけ内側になる。
            float outerFaceRadius = outerRadius * Mathf.Cos(segmentAngle / 2.0f);
            float centrelineRadius = outerFaceRadius - thickness / 2.0f;

            Vector3 barSize = new Vector3(barLength, thickness, thickness);

            for (int index = 0; index < segments; index++)
            {
                AddRingBar(root, material, obstacle, index, segmentAngle, centrelineRadius, barSize);
            }
        }

        /// <summary>
        /// Place one bar of the ring's polygon. / 輪の多角形の棒を 1 本置く。
        /// </summary>
        private static void AddRingBar(Transform root, Material material, WorldObstacle obstacle,
                                       int index, float segmentAngle, float radius, Vector3 barSize)
        {
            // The angle of the bar's midpoint around the ring, measured in the
            // ENU x-z plane, which is Unity's x-y plane.
            // 棒の中点の角。ENU の x-z 平面で測り、これは Unity の x-y 平面。
            float angle = (index + 0.5f) * segmentAngle;
            Vector3 centre = new Vector3(radius * Mathf.Cos(angle), radius * Mathf.Sin(angle), 0.0f);

            // Turn the bar so its length lies along the polygon's edge: the
            // tangent at that angle, which is the bar's own angle plus 90.
            // 棒の長さが多角形の辺に沿うよう回す。その角での接線の向きで、棒の
            // 角に 90 度を足したものである。
            const float quarterTurnDeg = 90.0f;
            Quaternion rotation = Quaternion.Euler(0.0f, 0.0f, angle * Mathf.Rad2Deg + quarterTurnDeg);

            GameObject bar = AddBox(root, $"bar_{index:D2}", material, obstacle, centre, barSize);
            bar.transform.localRotation = rotation;
        }

        // -------------------------------------------------------------------
        // Kinds with a shape of their own.
        // 自前の形を持つ種類。
        // -------------------------------------------------------------------

        /// <summary>
        /// A table: a top whose upper face is at z=size[2], with four legs down
        /// to the floor. The origin is the centre of the footprint on the
        /// floor, and size[2] is the height of the TOP SURFACE, not the top's
        /// thickness (Schemas/README.md §3.1).
        /// table。上面が z=size[2] にある天板と、床まで伸びる脚 4 本。原点は
        /// 接地面の中心で、size[2] は**天板上面**の高さであり天板の厚みではない
        /// （§3.1）。
        /// </summary>
        private static void BuildTable(WorldObstacle obstacle, Transform root, Material material)
        {
            float topWidth = obstacle.size[0];
            float topDepth = obstacle.size[1];
            float topSurfaceHeight = obstacle.size[2];

            float topThickness = Mathf.Max(TableTopMinimumThickness,
                                           topSurfaceHeight * TableTopThicknessFraction);
            topThickness = Mathf.Min(topThickness, topSurfaceHeight);

            Vector3 topSize = new Vector3(topWidth, topThickness, topDepth);
            AddBox(root, "top", material, obstacle,
                   new Vector3(0.0f, topSurfaceHeight - topThickness / 2.0f, 0.0f), topSize);

            AddTableLegs(root, material, obstacle, topWidth, topDepth, topSurfaceHeight - topThickness);
        }

        /// <summary>
        /// Four legs under the table's top, inset from its edges.
        /// 天板の下に、縁から内側へ入れた脚を 4 本。
        /// </summary>
        private static void AddTableLegs(Transform root, Material material, WorldObstacle obstacle,
                                         float topWidth, float topDepth, float legHeight)
        {
            if (legHeight <= 0.0f)
            {
                // A top as thick as the table is tall leaves no room for legs.
                // 天板が机の高さと同じ厚みなら、脚を入れる余地が無い。
                return;
            }

            float smallerDimension = Mathf.Min(topWidth, topDepth);
            float legWidth = Mathf.Min(smallerDimension,
                Mathf.Max(TableLegMinimumWidth, smallerDimension * TableLegFraction));
            float insetX = topWidth * TableLegInsetFraction + legWidth / 2.0f;
            float insetZ = topDepth * TableLegInsetFraction + legWidth / 2.0f;
            float legCentreX = Mathf.Max(0.0f, topWidth / 2.0f - insetX);
            float legCentreZ = Mathf.Max(0.0f, topDepth / 2.0f - insetZ);

            Vector3 legSize = new Vector3(legWidth, legHeight, legWidth);
            for (int index = 0; index < TableLegCount; index++)
            {
                // The four corners, as the two low bits of the leg's number.
                // 4 隅を、脚の番号の下位 2 ビットで表す。
                float signX = (index & 1) == 0 ? -1.0f : 1.0f;
                float signZ = (index & 2) == 0 ? -1.0f : 1.0f;
                AddBox(root, $"leg_{index}", material, obstacle,
                       new Vector3(signX * legCentreX, legHeight / 2.0f, signZ * legCentreZ), legSize);
            }
        }

        /// <summary>
        /// A ramp: a wedge rising toward ENU +y. The origin is the centre of
        /// the low edge at the bottom, so it occupies y=0..size[1] and
        /// z=0..size[2], with the slope from (y=0, z=0) up to (y=size[1],
        /// z=size[2]). The mesh is generated because no Unity primitive is a
        /// wedge; the collider is the same mesh marked convex, which a wedge is.
        /// ramp。ENU の +y 方向へ上がるくさび。原点は低い辺の中心（底）なので、
        /// y=0..size[1]・z=0..size[2] を占め、斜面は (y=0, z=0) から
        /// (y=size[1], z=size[2]) まで上がる。くさびの基本形状は Unity に無い
        /// のでメッシュを生成する。コライダは同じメッシュを凸として使う
        /// （くさびは凸である）。
        /// </summary>
        private static void BuildRamp(WorldObstacle obstacle, Transform root, Material material,
                                      WorldMaterials materials)
        {
            float width = obstacle.size[0];
            float run = obstacle.size[1];
            float rise = obstacle.size[2];

            Mesh mesh = WedgeMesh.Create(width, run, rise);
            materials.Own(mesh);

            GameObject part = new GameObject("wedge");
            part.transform.SetParent(root, false);
            part.AddComponent<MeshFilter>().sharedMesh = mesh;
            part.AddComponent<MeshRenderer>().sharedMaterial = material;

            MeshCollider collider = part.AddComponent<MeshCollider>();
            collider.sharedMesh = mesh;
            // A wedge is convex, so this costs nothing and keeps the collider
            // usable everywhere PhysX allows one.
            // くさびは凸なので、この指定に無理が無く、PhysX が許す全ての場面で
            // コライダとして使える。
            collider.convex = true;

            ObstacleInfo.Attach(part, obstacle.id, obstacle.type, obstacle.flow_quality);
        }

        // -------------------------------------------------------------------
        // Part construction shared by every kind.
        // 全ての種類が使う部品の生成。
        // -------------------------------------------------------------------

        /// <summary>
        /// Add a box part at a local centre, with a box collider of the same
        /// size. / 局所座標の中心に箱の部品を足す。同じ大きさの箱コライダ付き。
        /// </summary>
        internal static GameObject AddBox(Transform root, string name, Material material,
                                         WorldObstacle obstacle, Vector3 localCentre, Vector3 size)
        {
            GameObject part = GameObject.CreatePrimitive(PrimitiveType.Cube);
            return FinishPart(part, root, name, material, obstacle, localCentre, size);
        }

        /// <summary>
        /// Add a cylinder part. Unity's cylinder primitive is 2 units tall, so
        /// its y scale is half the height asked for.
        /// 円柱の部品を足す。Unity の円柱は高さ 2 単位なので、y の倍率は求める
        /// 高さの半分になる。
        /// </summary>
        private static GameObject AddCylinder(Transform root, string name, Material material,
                                              WorldObstacle obstacle, Vector3 localCentre, Vector3 size)
        {
            const float primitiveCylinderHeight = 2.0f;
            GameObject part = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            Vector3 scale = new Vector3(size.x, size.y / primitiveCylinderHeight, size.z);
            return FinishPart(part, root, name, material, obstacle, localCentre, scale);
        }

        /// <summary>
        /// Parent, name, place, scale and describe a freshly created part.
        /// 作ったばかりの部品を、親に付け・名前を付け・置き・倍率を与え・素性を
        /// 付ける。
        /// </summary>
        private static GameObject FinishPart(GameObject part, Transform root, string name,
                                             Material material, WorldObstacle obstacle,
                                             Vector3 localCentre, Vector3 scale)
        {
            part.name = name;
            part.transform.SetParent(root, false);
            part.transform.localPosition = localCentre;
            part.transform.localScale = scale;
            part.GetComponent<MeshRenderer>().sharedMaterial = material;
            ObstacleInfo.Attach(part, obstacle.id, obstacle.type, obstacle.flow_quality);
            return part;
        }

        /// <summary>
        /// Centre of a box whose origin is the centre of its bottom face.
        /// 原点が底面の中心である箱の、中心の位置。
        /// </summary>
        private static Vector3 BottomCentredCentre(Vector3 extent)
        {
            return new Vector3(0.0f, extent.y / 2.0f, 0.0f);
        }

        /// <summary>
        /// An ENU size [x, y, z] as a Unity size, which is the same y-z swap
        /// positions use. Sizes are lengths, so no sign can appear.
        /// ENU の大きさ [x, y, z] を Unity の大きさへ。位置と同じ y↔z の入れ
        /// 替えでよい。大きさは長さなので符号は出ない。
        /// </summary>
        private static Vector3 EnuExtentToUnity(float[] size)
        {
            return new Vector3(size[0], size[2], size[1]);
        }

        /// <summary>
        /// A readable name for the hierarchy. / 階層で読みやすい名前。
        /// </summary>
        private static string ObstacleName(WorldObstacle obstacle)
        {
            return $"{obstacle.type}:{obstacle.id}";
        }

        /// <summary>
        /// Every ObstacleInfo under a built obstacle, the root included.
        /// 生成した障害物の下にある ObstacleInfo を、根も含めて全て返す。
        /// </summary>
        public static IReadOnlyList<ObstacleInfo> SurfacesOf(GameObject obstacleRoot)
        {
            return obstacleRoot.GetComponentsInChildren<ObstacleInfo>(true);
        }
    }
}
