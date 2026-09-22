using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// Writes a world back out as JSON, in ENU, metres and degrees. Unity
    /// coordinates never appear in the output (Schemas/README.md §9.3).
    ///
    /// JsonUtility is not used for writing, for two reasons the format makes
    /// unavoidable:
    ///   * it writes every field of the class, so `thickness` and `segments`
    ///     would appear on kinds where the schema forbids them, and
    ///     `description` and `light` would appear even when absent;
    ///   * it writes floats with full round-trip precision, turning 0.1 into
    ///     0.10000000149011612 and making the file unreadable to a person.
    /// The writer is therefore written out by hand, which also lets it order
    /// the keys as the shipped files do.
    ///
    /// 空間を JSON として書き戻す。ENU・m・度のままで、Unity の座標は出力に
    /// 現れない（README §9.3）。
    ///
    /// 書き出しに JsonUtility を使わない理由は 2 つあり、どちらも形式から来る:
    ///   * クラスの全フィールドを書くので、スキーマが禁じる種類にも
    ///     thickness・segments が現れ、省略された description・light も現れる;
    ///   * float を往復精度で書くので、0.1 が 0.10000000149011612 になり、人が
    ///     読めないファイルになる。
    /// そこで手で書き出す。同梱のファイルと同じ鍵の順に並べられる利点もある。
    /// </summary>
    public static class WorldWriter
    {
        // Two spaces per level, as the shipped world files use.
        // 1 段につき空白 2 つ。同梱の空間ファイルに合わせる。
        private const string IndentUnit = "  ";

        // Lengths are written with at most this many decimals. A millimetre is
        // 0.001 m and the smallest number any shipped world carries is 0.008 m,
        // so six decimals keep every value exactly while cutting the noise a
        // round-trip float would otherwise show.
        // 長さはこの桁数までの小数で書く。1 mm は 0.001 m で、同梱の空間が持つ
        // いちばん小さい数は 0.008 m なので、6 桁あれば全ての値をそのまま保ち
        // つつ、往復精度の float が見せる雑音を落とせる。
        private const string NumberFormat = "0.######";

        /// <summary>
        /// Serialise a world to JSON text. Reading the result back with
        /// <see cref="WorldFileReader"/> gives the same world.
        /// 空間を JSON の文字列にする。結果を <see cref="WorldFileReader"/> で
        /// 読み戻すと同じ空間になる。
        /// </summary>
        public static string Write(WorldFile world)
        {
            StringBuilder text = new StringBuilder();
            text.Append("{\n");

            WriteHeader(text, world);
            WriteRoom(text, world.room);
            WriteFloor(text, world.floor);
            WriteLight(text, world.light);
            WriteSpawn(text, world.spawn);
            WriteObstacles(text, world.obstacles);

            text.Append("\n}\n");
            return text.ToString();
        }

        /// <summary>
        /// The format marker, the version, the units, the frame, the name and
        /// the description. / 目印・版・単位・座標系・名前・説明。
        /// </summary>
        private static void WriteHeader(StringBuilder text, WorldFile world)
        {
            Indent(text, 1).Append(Key("format")).Append(Quote(WorldFormat.Marker)).Append(",\n");
            Indent(text, 1).Append(Key("version")).Append(WorldFormat.Version).Append(",\n");
            Indent(text, 1).Append(Key("units")).Append("{\n");
            Indent(text, 2).Append(Key("length")).Append(Quote(WorldFormat.LengthUnit)).Append(",\n");
            Indent(text, 2).Append(Key("angle")).Append(Quote(WorldFormat.AngleUnit)).Append('\n');
            Indent(text, 1).Append("},\n");
            Indent(text, 1).Append(Key("frame")).Append(Quote(WorldFormat.Frame)).Append(",\n");
            Indent(text, 1).Append(Key("name")).Append(Quote(world.name)).Append(",\n");

            // description is optional, and writing an empty one would fail the
            // checker's "must be a non-empty string".
            // description は任意で、空で書くと検査の「空でない文字列」に反する。
            bool hasDescription = world.description != null
                && !string.IsNullOrEmpty(world.description.ja)
                && !string.IsNullOrEmpty(world.description.en);
            if (!hasDescription)
            {
                return;
            }

            Indent(text, 1).Append(Key("description")).Append("{\n");
            Indent(text, 2).Append(Key("ja")).Append(Quote(world.description.ja)).Append(",\n");
            Indent(text, 2).Append(Key("en")).Append(Quote(world.description.en)).Append('\n');
            Indent(text, 1).Append("},\n");
        }

        private static void WriteRoom(StringBuilder text, WorldRoom room)
        {
            Indent(text, 1).Append(Key("room")).Append("{\n");
            Indent(text, 2).Append(Key("size")).Append(Vector(room.size)).Append(",\n");
            Indent(text, 2).Append(Key("walls")).Append(Boolean(room.walls)).Append(",\n");
            Indent(text, 2).Append(Key("ceiling")).Append(Boolean(room.ceiling)).Append('\n');
            Indent(text, 1).Append("},\n");
        }

        private static void WriteFloor(StringBuilder text, WorldFloor floor)
        {
            Indent(text, 1).Append(Key("floor")).Append("{\n");
            Indent(text, 2).Append(Key("pattern")).Append(Quote(floor.pattern)).Append(",\n");
            Indent(text, 2).Append(Key("pitch")).Append(Number(floor.pitch)).Append(",\n");
            Indent(text, 2).Append(Key("colors"))
                .Append($"[{Quote(floor.colors[0])}, {Quote(floor.colors[1])}]").Append(",\n");
            // The floor's flow_quality is written as it stands; 0 is a real
            // value there (Schemas/README.md §9.3).
            // 床の flow_quality はそのまま書く。床では 0 が本物の値（§9.3）。
            Indent(text, 2).Append(Key("flow_quality")).Append(Number(floor.flow_quality)).Append('\n');
            Indent(text, 1).Append("},\n");
        }

        private static void WriteLight(StringBuilder text, string light)
        {
            Indent(text, 1).Append(Key("light"))
                .Append(Quote(string.IsNullOrEmpty(light) ? WorldFormat.DefaultLight : light))
                .Append(",\n");
        }

        private static void WriteSpawn(StringBuilder text, WorldSpawn spawn)
        {
            Indent(text, 1).Append(Key("spawn")).Append("{\n");
            Indent(text, 2).Append(Key("position")).Append(Vector(spawn.position)).Append(",\n");
            Indent(text, 2).Append(Key("yaw_deg")).Append(Number(spawn.yaw_deg)).Append('\n');
            Indent(text, 1).Append("},\n");
        }

        private static void WriteObstacles(StringBuilder text, WorldObstacle[] obstacles)
        {
            if (obstacles == null || obstacles.Length == 0)
            {
                Indent(text, 1).Append(Key("obstacles")).Append("[]");
                return;
            }

            Indent(text, 1).Append(Key("obstacles")).Append("[\n");
            for (int index = 0; index < obstacles.Length; index++)
            {
                WriteObstacle(text, obstacles[index]);
                text.Append(index + 1 < obstacles.Length ? ",\n" : "\n");
            }

            Indent(text, 1).Append(']');
        }

        /// <summary>
        /// One obstacle. `thickness` and `segments` are written only on the
        /// kinds that may carry them, because the schema forbids them
        /// elsewhere. / 障害物 1 個。thickness と segments は、持ってよい種類に
        /// だけ書く。他の種類ではスキーマが禁じているためである。
        /// </summary>
        private static void WriteObstacle(StringBuilder text, WorldObstacle obstacle)
        {
            Indent(text, 2).Append("{\n");

            List<string> lines = new List<string>
            {
                Key("id") + Quote(obstacle.id),
                Key("type") + Quote(obstacle.type),
                Key("position") + Vector(obstacle.position),
                Key("rotation_deg") + Vector(obstacle.rotation_deg),
                Key("size") + Vector(obstacle.size),
            };

            if (WorldObstacleTypes.IsHollow(obstacle.type))
            {
                lines.Add(Key("thickness") + Number(obstacle.thickness));
            }

            if (obstacle.type == WorldObstacleTypes.Ring)
            {
                int segments = obstacle.segments > 0 ? obstacle.segments : WorldFormat.DefaultRingSegments;
                lines.Add(Key("segments") + segments.ToString(CultureInfo.InvariantCulture));
            }

            lines.Add(Key("color") + Quote(obstacle.color));
            lines.Add(Key("flow_quality") + Number(FlowQualityOf(obstacle)));

            for (int index = 0; index < lines.Count; index++)
            {
                Indent(text, 3).Append(lines[index]).Append(index + 1 < lines.Count ? ",\n" : "\n");
            }

            Indent(text, 2).Append('}');
        }

        /// <summary>
        /// The flow quality to write. A zero means the reader has not filled in
        /// the default yet, so the default is written rather than a 0 that the
        /// reader would then read back as "absent" again.
        /// 書き出すフロー品質。0 は読み込み側がまだ既定を埋めていないことを表す
        /// ので、既定を書く。0 のまま書くと、読み戻したときにまた「省略」と
        /// 解釈されてしまう。
        /// </summary>
        private static float FlowQualityOf(WorldObstacle obstacle)
        {
            return obstacle.flow_quality > 0.0f
                ? obstacle.flow_quality
                : WorldFormat.DefaultObstacleFlowQuality;
        }

        // -------------------------------------------------------------------
        // Small formatting helpers.
        // 整形の補助。
        // -------------------------------------------------------------------

        private static StringBuilder Indent(StringBuilder text, int depth)
        {
            for (int level = 0; level < depth; level++)
            {
                text.Append(IndentUnit);
            }

            return text;
        }

        private static string Key(string name) => $"\"{name}\": ";

        private static string Boolean(bool value) => value ? "true" : "false";

        /// <summary>
        /// A number with the trailing zeros trimmed, and never in exponent
        /// notation, which JSON allows but which reads badly in a world file.
        /// 末尾の 0 を落とした数。指数表記にはしない。JSON では許されるが、空間
        /// ファイルでは読みにくい。
        /// </summary>
        private static string Number(float value)
        {
            // Round to the written precision first, so -0 from a rounding of a
            // tiny negative value does not survive into the file.
            // 先に書き出す桁に丸める。わずかな負の値が −0 として残らないように
            // するためである。
            string formatted = value.ToString(NumberFormat, CultureInfo.InvariantCulture);
            return formatted == "-0" ? "0" : formatted;
        }

        private static string Vector(float[] values)
        {
            return $"[{Number(values[0])}, {Number(values[1])}, {Number(values[2])}]";
        }

        /// <summary>
        /// Quote and escape a JSON string, keeping Japanese text literal so the
        /// description stays readable in the file.
        /// JSON の文字列を引用しエスケープする。日本語はそのまま残し、説明が
        /// ファイルの中で読める状態を保つ。
        /// </summary>
        private static string Quote(string value)
        {
            StringBuilder quoted = new StringBuilder((value?.Length ?? 0) + 2);
            quoted.Append('"');
            foreach (char character in value ?? string.Empty)
            {
                AppendEscaped(quoted, character);
            }

            quoted.Append('"');
            return quoted.ToString();
        }

        private static void AppendEscaped(StringBuilder text, char character)
        {
            const char lowestPrintable = ' ';
            switch (character)
            {
                case '"':
                    text.Append("\\\"");
                    return;
                case '\\':
                    text.Append("\\\\");
                    return;
                case '\n':
                    text.Append("\\n");
                    return;
                case '\r':
                    text.Append("\\r");
                    return;
                case '\t':
                    text.Append("\\t");
                    return;
                default:
                    if (character < lowestPrintable)
                    {
                        text.Append("\\u").Append(((int)character).ToString("x4", CultureInfo.InvariantCulture));
                        return;
                    }

                    text.Append(character);
                    return;
            }
        }

        /// <summary>
        /// Read a world back out of a built scene, so the editing UI (a later
        /// stage) can save what the person moved. Each obstacle's root
        /// transform is converted back to ENU; the room, the floor and the
        /// spawn point come from the loaded world, which the editing UI updates
        /// in place.
        /// 生成済みの場面から空間を読み戻す。編集 UI（後の段階）が、人が動かした
        /// 結果を保存できるようにするためである。各障害物の根の Transform を ENU
        /// へ戻す。部屋・床・出発点は読み込んだ空間から取り、編集 UI がそちらを
        /// その場で書き換える。
        /// </summary>
        public static WorldFile FromScene(WorldFile loaded, Transform worldRoot)
        {
            if (loaded == null || worldRoot == null)
            {
                return loaded;
            }

            Dictionary<string, Transform> roots = new Dictionary<string, Transform>();
            foreach (Transform child in worldRoot)
            {
                ObstacleInfo info = child.GetComponent<ObstacleInfo>();
                if (info != null && !roots.ContainsKey(info.ObstacleId))
                {
                    roots.Add(info.ObstacleId, child);
                }
            }

            foreach (WorldObstacle obstacle in loaded.obstacles)
            {
                if (!roots.TryGetValue(obstacle.id, out Transform placed))
                {
                    continue;
                }

                // A fallen pin is simulation state, not the initial layout to save.
                // 倒れたピンはシミュレーション状態であり保存する初期配置ではない。
                bool dynamicBody = placed.GetComponent<DynamicObstacleBody>() != null;
                if (dynamicBody) continue;

                obstacle.position = WorldFrames.UnityToEnu(placed.localPosition);
                obstacle.rotation_deg = WorldFrames.UnityRotationToEnu(placed.localRotation);
            }

            return loaded;
        }
    }
}
