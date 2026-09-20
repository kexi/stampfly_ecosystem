using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// Builds the room a world describes: the floor with its generated pattern,
    /// the four walls, the ceiling, and the lighting preset.
    ///
    /// The room is a box centred on the origin whose floor is the plane z=0
    /// (Schemas/README.md §2.1), which in Unity is y=0 after the frame swap.
    /// Walls and ceiling are boxes just outside the interior, so the interior
    /// extent the world file states is exactly what the vehicle can fly in.
    ///
    /// 空間が述べる部屋を作る。模様を生成した床、壁 4 枚、天井、照明。
    ///
    /// 部屋は原点を中心とする直方体で、床は z=0 の平面（README §2.1）。座標を
    /// 入れ替えた後の Unity では y=0 になる。壁と天井は内側のすぐ外に置く箱
    /// なので、空間ファイルが述べる内寸がそのまま機体の飛べる範囲になる。
    /// </summary>
    public static class RoomBuilder
    {
        // Walls and the ceiling are this thick. Thin enough not to eat into a
        // room a few metres across, thick enough that a fast vehicle cannot
        // tunnel through one between two physics steps.
        // 壁と天井の厚み。数 m 四方の部屋を削らない薄さで、かつ速い機体が刻みの
        // 間にすり抜けない厚み。
        private const float SurfaceThickness = 0.05f;

        // The floor slab is thicker: a downward ToF ray that misses the
        // vehicle's own footprint must still hit something solid.
        // 床の板はもう少し厚い。下向き ToF のレイが機体の真下を外しても、
        // 確かな物に当たる必要がある。
        private const float FloorThickness = 0.10f;

        // The four walls, as the signs of their inward normal along x and y.
        // 壁 4 枚を、内向きの法線の x・y の符号で表す。
        private static readonly Vector2Int[] WallDirections =
        {
            new Vector2Int(-1, 0), new Vector2Int(1, 0),
            new Vector2Int(0, -1), new Vector2Int(0, 1),
        };

        private static readonly string[] WallNames = { "wall_west", "wall_east", "wall_south", "wall_north" };

        /// <summary>
        /// Build the room under <paramref name="parent"/>.
        /// <paramref name="parent"/> の下に部屋を作る。
        /// </summary>
        public static void Build(WorldFile world, Transform parent, WorldMaterials materials)
        {
            Vector3 interior = InteriorExtent(world.room.size);
            BuildFloor(world, parent, materials, interior);

            if (world.room.walls)
            {
                BuildWalls(world, parent, materials, interior);
            }

            if (world.room.ceiling)
            {
                BuildCeiling(world, parent, materials, interior);
            }

            BuildLight(world.light, parent);
        }

        /// <summary>
        /// The floor: one slab under y=0, textured with the world's pattern and
        /// carrying the floor's own flow quality. The floor's flow_quality is
        /// taken as written, with no default: 0 is meaningful there, and
        /// featureless_floor relies on being able to say 0.05.
        /// 床。y=0 の下に置く板 1 枚。空間の模様を貼り、床自身のフロー品質を
        /// 持たせる。床の flow_quality は書かれたまま使い、既定を当てない。床では
        /// 0 が意味を持つ値で、featureless_floor がそれに頼る。
        /// </summary>
        private static void BuildFloor(WorldFile world, Transform parent, WorldMaterials materials,
                                       Vector3 interior)
        {
            Color first = WorldMaterials.ParseColor(world.floor.colors[0]);
            Color second = WorldMaterials.ParseColor(world.floor.colors[1]);
            Texture2D texture = FloorTexture.Create(world.floor.pattern, first, second);

            // One texture tile covers one `pitch` of floor, so the pattern's
            // repeat length on the floor is the length the file states.
            // テクスチャ 1 枚が床の pitch 1 つ分を覆う。床の上での模様の繰り返し
            // 長さが、ファイルの述べる長さになる。
            Vector2 tiling = new Vector2(interior.x / world.floor.pitch, interior.z / world.floor.pitch);
            Material material = materials.Textured(texture, tiling);

            GameObject floor = MakeSlab(parent, "floor", material,
                                        new Vector3(0.0f, -FloorThickness / 2.0f, 0.0f),
                                        new Vector3(interior.x, FloorThickness, interior.z));
            ObstacleInfo.Attach(floor, ObstacleInfo.FloorId, ObstacleInfo.FloorType,
                                world.floor.flow_quality);
        }

        /// <summary>
        /// The four side walls, standing just outside the interior.
        /// 側面 4 枚の壁。内側のすぐ外に立てる。
        /// </summary>
        private static void BuildWalls(WorldFile world, Transform parent, WorldMaterials materials,
                                       Vector3 interior)
        {
            Material material = materials.Solid(world.floor.colors[1]);
            float centreHeight = interior.y / 2.0f;

            for (int index = 0; index < WallDirections.Length; index++)
            {
                Vector2Int direction = WallDirections[index];
                bool alongX = direction.x != 0;

                // The wall's own plane: half the interior plus half the
                // thickness puts its inner face exactly on the boundary.
                // 壁の位置。内寸の半分に厚みの半分を足すと、内側の面がちょうど
                // 境界に来る。
                float offsetX = direction.x * (interior.x + SurfaceThickness) / 2.0f;
                float offsetZ = direction.y * (interior.z + SurfaceThickness) / 2.0f;

                Vector3 size = alongX
                    ? new Vector3(SurfaceThickness, interior.y, interior.z + 2.0f * SurfaceThickness)
                    : new Vector3(interior.x + 2.0f * SurfaceThickness, interior.y, SurfaceThickness);

                GameObject wall = MakeSlab(parent, WallNames[index], material,
                                           new Vector3(offsetX, centreHeight, offsetZ), size);
                // A wall is not a surface the downward flow sensor reads, but
                // every collider carries an info so a ray never comes back
                // without an answer.
                // 壁は下向きのフローのセンサが読む面ではないが、レイが答えを
                // 持たずに返らないよう、全てのコライダに素性を付ける。
                ObstacleInfo.Attach(wall, WallNames[index], ObstacleInfo.RoomSurfaceType,
                                    world.floor.flow_quality);
            }
        }

        /// <summary>The ceiling, just above the interior. / 内側のすぐ上の天井。</summary>
        private static void BuildCeiling(WorldFile world, Transform parent, WorldMaterials materials,
                                         Vector3 interior)
        {
            Material material = materials.Solid(world.floor.colors[0]);
            GameObject ceiling = MakeSlab(parent, "ceiling", material,
                                          new Vector3(0.0f, interior.y + SurfaceThickness / 2.0f, 0.0f),
                                          new Vector3(interior.x, SurfaceThickness, interior.z));
            ObstacleInfo.Attach(ceiling, "ceiling", ObstacleInfo.RoomSurfaceType,
                                world.floor.flow_quality);
        }

        /// <summary>
        /// The lighting preset. One directional light plus ambient, because the
        /// worlds are small rooms and a WebGL build cannot afford many
        /// real-time lights.
        /// 照明。指向性の光 1 つと環境光。空間は小さな部屋で、WebGL のビルドに
        /// 実時間の光を何本も置く余裕が無いため。
        /// </summary>
        private static void BuildLight(string preset, Transform parent)
        {
            LightSettings settings = LightSettings.For(preset);

            GameObject holder = new GameObject($"light_{preset}");
            holder.transform.SetParent(parent, false);
            holder.transform.localRotation = Quaternion.Euler(settings.PitchDeg, settings.YawDeg, 0.0f);

            Light light = holder.AddComponent<Light>();
            light.type = LightType.Directional;
            light.color = settings.Color;
            light.intensity = settings.Intensity;
            light.shadows = LightShadows.Soft;

            RenderSettings.ambientLight = settings.Ambient;
        }

        /// <summary>
        /// Add a box with a collider, used for every room surface.
        /// コライダ付きの箱を足す。部屋の面は全てこれで作る。
        /// </summary>
        private static GameObject MakeSlab(Transform parent, string name, Material material,
                                           Vector3 centre, Vector3 size)
        {
            GameObject slab = GameObject.CreatePrimitive(PrimitiveType.Cube);
            slab.name = name;
            slab.transform.SetParent(parent, false);
            slab.transform.localPosition = centre;
            slab.transform.localScale = size;
            slab.GetComponent<MeshRenderer>().sharedMaterial = material;
            return slab;
        }

        /// <summary>
        /// The interior extent in Unity axes: the ENU [x, y, z] with y and z
        /// swapped, so `interior.y` is the ceiling height.
        /// 内寸を Unity の軸で。ENU の [x, y, z] の y と z を入れ替えたもので、
        /// interior.y が天井の高さになる。
        /// </summary>
        public static Vector3 InteriorExtent(float[] roomSize)
        {
            return new Vector3(roomSize[0], roomSize[2], roomSize[1]);
        }

        /// <summary>
        /// What one lighting preset means. Kept as a table rather than as
        /// numbers scattered through BuildLight.
        /// 照明 1 種の中身。BuildLight に数を散らさず、表にまとめる。
        /// </summary>
        private readonly struct LightSettings
        {
            public readonly Color Color;
            public readonly float Intensity;
            public readonly Color Ambient;
            public readonly float PitchDeg;
            public readonly float YawDeg;

            private LightSettings(Color color, float intensity, Color ambient, float pitchDeg, float yawDeg)
            {
                Color = color;
                Intensity = intensity;
                Ambient = ambient;
                PitchDeg = pitchDeg;
                YawDeg = yawDeg;
            }

            /// <summary>
            /// The settings of a preset, defaulting to indoor for an unknown
            /// name, as the format's default is indoor.
            /// 指定した照明の設定。未知の名前は indoor にする。形式の既定が
            /// indoor だからである。
            /// </summary>
            public static LightSettings For(string preset)
            {
                switch (preset)
                {
                    case WorldLightPresets.Day:
                        // Bright, slightly warm sun high in the sky.
                        // 高い位置からの、明るくわずかに暖かい日ざし。
                        return new LightSettings(new Color(1.0f, 0.97f, 0.91f), 1.4f,
                                                 new Color(0.52f, 0.56f, 0.62f), 55.0f, 35.0f);
                    case WorldLightPresets.Dim:
                        // Low and cool, so a low-texture floor really is hard
                        // to see; featureless_floor uses this.
                        // 低く冷たい光。模様の少ない床が本当に見えにくくなる。
                        // featureless_floor がこれを使う。
                        return new LightSettings(new Color(0.78f, 0.80f, 0.88f), 0.45f,
                                                 new Color(0.16f, 0.17f, 0.20f), 35.0f, -20.0f);
                    default:
                        // Even indoor lighting, the format's default.
                        // 室内の均した明るさ。形式の既定。
                        return new LightSettings(new Color(1.0f, 0.98f, 0.95f), 0.95f,
                                                 new Color(0.36f, 0.37f, 0.40f), 50.0f, 15.0f);
                }
            }
        }
    }
}
