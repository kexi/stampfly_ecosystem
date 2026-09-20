using System;

namespace StampFly.World
{
    // Data classes that mirror the world file format one key at a time.
    // Specification: simulator/unity/Schemas/README.md and world.schema.json.
    // 空間ファイルの形式を鍵ごとにそのまま写したデータクラス群。
    // 仕様は simulator/unity/Schemas/README.md と world.schema.json。
    //
    // Naming choice (Schemas/README.md §9.2 asks for this to be recorded):
    // option 1 was taken -- the C# field names copy the JSON keys verbatim,
    // underscores included (flow_quality, rotation_deg, yaw_deg). JsonUtility
    // matches key names literally, so this keeps the load a single call with no
    // rewriting layer in front of it; the cost is that a few field names are not
    // idiomatic C#. WorldFileReader and WorldWriter are the only places that
    // touch these names, and everything downstream reads them through
    // WorldFrames or ObstacleFactory.
    // 命名の選択（§9.2 が実装に書き残すよう求めている）:
    // 案 1 を採った。C# のフィールド名を JSON の鍵にそのまま合わせ、下線も残す
    // （flow_quality・rotation_deg・yaw_deg）。JsonUtility は鍵名をそのまま見る
    // ので、鍵名を書き換える層を挟まずに 1 回の呼び出しで読める。代わりに一部の
    // 名前が C# らしくないが、この名前に触るのは WorldFileReader と WorldWriter
    // だけで、以降は WorldFrames や ObstacleFactory を通して読む。

    /// <summary>
    /// Units declared by the file; fixed to metres and degrees.
    /// ファイルが宣言する単位。m と度に固定。
    /// </summary>
    [Serializable]
    public class WorldUnits
    {
        public string length;
        public string angle;
    }

    /// <summary>
    /// One line of Japanese and English text describing the world's purpose.
    /// 空間のねらいを日本語と英語で 1 行ずつ書いたもの。
    /// </summary>
    [Serializable]
    public class WorldText
    {
        public string ja;
        public string en;
    }

    /// <summary>
    /// The rectangular room. Its floor is the plane z=0 and its footprint is
    /// centred on the origin (Schemas/README.md §2.1).
    /// 直方体の部屋。床は z=0 の平面で、水平方向は原点中心（README §2.1）。
    /// </summary>
    [Serializable]
    public class WorldRoom
    {
        /// <summary>Interior extent [x_east, y_north, z_up] in metres. / 内寸（m）。</summary>
        public float[] size;

        /// <summary>Whether the four side walls are solid. / 側面 4 枚の壁を作るか。</summary>
        public bool walls;

        /// <summary>Whether the ceiling is solid. / 天井を作るか。</summary>
        public bool ceiling;
    }

    /// <summary>
    /// Appearance of the floor and how well optical flow can track it
    /// (Schemas/README.md §2.2).
    /// 床の見た目とオプティカルフローの追従しやすさ（README §2.2）。
    /// </summary>
    [Serializable]
    public class WorldFloor
    {
        /// <summary>checker | grid | plain | noise | stripes.</summary>
        public string pattern;

        /// <summary>Pattern repeat length in metres. / 模様の繰り返し長さ（m）。</summary>
        public float pitch;

        /// <summary>The pattern's two colours, "#rrggbb". / 模様の 2 色。</summary>
        public string[] colors;

        // 0 is a meaningful value on the floor (a featureless floor), so unlike
        // an obstacle this field never receives a default.
        // 床では 0 が意味を持つ値（模様の無い床）なので、障害物と違って既定を当てない。
        public float flow_quality;
    }

    /// <summary>
    /// Where the vehicle is placed at reset (Schemas/README.md §2.3).
    /// リセット時に機体を置く場所（README §2.3）。
    /// </summary>
    [Serializable]
    public class WorldSpawn
    {
        /// <summary>Centre of gravity [x_east, y_north, z_up] in metres. / 重心の位置（m）。</summary>
        public float[] position;

        /// <summary>Heading, degrees counter-clockwise from east. / 東から反時計回りの機首方位。</summary>
        public float yaw_deg;
    }

    /// <summary>
    /// One obstacle. Every kind shares this flat shape, because JsonUtility
    /// cannot read per-type nesting (Schemas/README.md §9.1).
    /// 障害物 1 個。JsonUtility が種類ごとの入れ子を読めないため、全種類が
    /// この平らな形を共有する（README §9.1）。
    /// </summary>
    [Serializable]
    public class WorldObstacle
    {
        public string id;

        /// <summary>One of the ten kinds listed by WorldObstacleTypes. / 10 種のいずれか。</summary>
        public string type;

        /// <summary>The type's own origin, per Schemas/README.md §3.1. / その種類の原点。</summary>
        public float[] position;

        /// <summary>[roll, pitch, yaw] in degrees. / [roll, pitch, yaw]（度）。</summary>
        public float[] rotation_deg;

        /// <summary>Three lengths whose meaning depends on `type`. / 意味は type ごと。</summary>
        public float[] size;

        /// <summary>gate | ring | tunnel only; 0 on every other kind. / 他の種類では 0。</summary>
        public float thickness;

        /// <summary>ring only; 0 means "absent", i.e. the default. / ring のみ。0 は省略。</summary>
        public int segments;

        public string color;

        /// <summary>0 means "absent" here, unlike on the floor. / 床と違い 0 は省略を意味する。</summary>
        public float flow_quality;
    }

    /// <summary>
    /// A whole world file. Read with <see cref="WorldFileReader"/> so the format
    /// marker, the version and the frame are checked before anything uses it.
    /// 空間ファイル全体。目印・版・座標系を確かめてから使うため、読み込みは
    /// <see cref="WorldFileReader"/> を通す。
    /// </summary>
    [Serializable]
    public class WorldFile
    {
        public string format;
        public int version;
        public WorldUnits units;
        public string frame;
        public string name;
        public WorldText description;
        public WorldRoom room;
        public WorldFloor floor;

        /// <summary>day | indoor | dim; empty means indoor. / 空なら indoor。</summary>
        public string light;

        public WorldSpawn spawn;
        public WorldObstacle[] obstacles;
    }
}
