namespace StampFly.World
{
    /// <summary>
    /// Constants the world file format fixes. They match the same names in
    /// tools/unity_world/validate.py, which is the checking implementation.
    /// 空間ファイルの形式が定める定数。検査の実装である
    /// tools/unity_world/validate.py の同名の定数と一致する。
    /// </summary>
    public static class WorldFormat
    {
        /// <summary>Value the `format` key must carry. / format 鍵が持つべき値。</summary>
        public const string Marker = "stampfly-world";

        /// <summary>The only format version that exists. / 存在する唯一の版。</summary>
        public const int Version = 1;

        /// <summary>The only frame a world file may use. / 空間ファイルの唯一の座標系。</summary>
        public const string Frame = "ENU";

        /// <summary>Length unit every number in the file uses. / 全数値の長さの単位。</summary>
        public const string LengthUnit = "m";

        /// <summary>Angle unit every angle in the file uses. / 全角度の単位。</summary>
        public const string AngleUnit = "deg";

        /// <summary>Lighting preset used when `light` is absent. / light 省略時の照明。</summary>
        public const string DefaultLight = "indoor";

        /// <summary>Segment count used when a ring omits `segments`. / segments 省略時の分割数。</summary>
        public const int DefaultRingSegments = 24;

        /// <summary>Flow quality used when an obstacle omits it. / 障害物が省略したときの値。</summary>
        public const float DefaultObstacleFlowQuality = 0.8f;

        /// <summary>Number of components in every position, rotation and size. / 3 成分。</summary>
        public const int VectorLength = 3;

        /// <summary>Number of colours a floor pattern carries. / 床の模様が持つ色数。</summary>
        public const int FloorColorCount = 2;

        /// <summary>File-name suffix of a world file. / 空間ファイルの拡張子。</summary>
        public const string FileSuffix = ".world.json";
    }

    /// <summary>
    /// The fourteen obstacle kinds, spelled exactly as the format writes them.
    /// 障害物の 14 種。形式が書くとおりの綴り。
    /// </summary>
    public static class WorldObstacleTypes
    {
        public const string Box = "box";
        public const string Pillar = "pillar";
        public const string Wall = "wall";
        public const string Gate = "gate";
        public const string Ring = "ring";
        public const string Tunnel = "tunnel";
        public const string Table = "table";
        public const string Chair = "chair";
        public const string Sofa = "sofa";
        public const string Shelf = "shelf";
        public const string Bed = "bed";
        public const string Step = "step";
        public const string Ramp = "ramp";
        public const string Pad = "pad";

        /// <summary>Every kind, in the order the schema lists them. / スキーマの並び順。</summary>
        public static readonly string[] All =
        {
            Box, Pillar, Wall, Gate, Ring, Tunnel, Table, Chair, Sofa, Shelf, Bed, Step, Ramp, Pad,
        };

        /// <summary>Kinds that are hollow and carry a thickness. / 中空で肉厚を持つ種類。</summary>
        public static readonly string[] Hollow = { Gate, Ring, Tunnel };

        /// <summary>
        /// Whether a kind is one of the fourteen. / その綴りが 14 種のいずれかか。
        /// </summary>
        public static bool IsKnown(string type)
        {
            // A linear scan over fourteen short strings is cheaper than a hash set
            // and keeps the order of `All` as the single listing.
            // 14 個の短い文字列の走査は集合より安く、並びを All 1 か所に保てる。
            foreach (string known in All)
            {
                if (known == type)
                {
                    return true;
                }
            }

            return false;
        }

        /// <summary>
        /// Whether a kind is hollow, i.e. requires a thickness.
        /// その種類が中空か（肉厚が必須か）。
        /// </summary>
        public static bool IsHollow(string type)
        {
            foreach (string hollow in Hollow)
            {
                if (hollow == type)
                {
                    return true;
                }
            }

            return false;
        }
    }

    /// <summary>
    /// The five floor patterns the format allows. / 形式が許す床の模様 5 種。
    /// </summary>
    public static class WorldFloorPatterns
    {
        public const string Checker = "checker";
        public const string Grid = "grid";
        public const string Plain = "plain";
        public const string Noise = "noise";
        public const string Stripes = "stripes";

        public static readonly string[] All = { Checker, Grid, Plain, Noise, Stripes };
    }

    /// <summary>
    /// The three lighting presets the format allows. / 形式が許す照明 3 種。
    /// </summary>
    public static class WorldLightPresets
    {
        public const string Day = "day";
        public const string Indoor = "indoor";
        public const string Dim = "dim";

        public static readonly string[] All = { Day, Indoor, Dim };
    }
}
