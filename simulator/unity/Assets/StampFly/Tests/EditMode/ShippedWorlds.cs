using System.Collections.Generic;
using System.IO;
using StampFly.World;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// The ten shipped worlds, as the tests reach them. They are read straight
    /// from Assets/StampFly/Worlds rather than through the WorldCatalog asset,
    /// so the tests check the files themselves and still pass on a checkout
    /// where the catalog has not been rebuilt yet.
    /// 同梱の 10 空間を試験から読むための入口。WorldCatalog の資産ではなく
    /// Assets/StampFly/Worlds から直接読む。ファイルそのものを試験でき、一覧を
    /// まだ作り直していない作業ツリーでも通るためである。
    /// </summary>
    public static class ShippedWorlds
    {
        /// <summary>
        /// The names, matching SHIPPED_WORLDS in
        /// tools/unity_world/tests/test_validate.py.
        /// 名前の一覧。tools/unity_world/tests/test_validate.py の
        /// SHIPPED_WORLDS と一致する。
        /// </summary>
        public static readonly string[] Names =
        {
            "bedroom",
            "bowling",
            "corridor_tunnel",
            "empty_room",
            "featureless_floor",
            "gate_course",
            "living_room",
            "pillar_forest",
            "stepped_floor",
            "study",
        };

        /// <summary>Folder holding the shipped worlds. / 同梱の空間の置き場。</summary>
        public const string Folder = "Assets/StampFly/Worlds";

        /// <summary>
        /// The JSON of one shipped world. / 同梱の空間 1 つの JSON。
        /// </summary>
        public static string Json(string worldName)
        {
            return File.ReadAllText(Path.Combine(Folder, worldName + WorldFormat.FileSuffix));
        }

        /// <summary>
        /// Read one shipped world, failing the caller's assertion if refused.
        /// 同梱の空間を 1 つ読む。断られたら呼び出し側の判定が落ちる。
        /// </summary>
        public static WorldReadResult Read(string worldName)
        {
            return WorldFileReader.Read(Json(worldName));
        }

        /// <summary>
        /// Every shipped world's name, for a [TestCaseSource].
        /// [TestCaseSource] に渡す、同梱の空間の名前。
        /// </summary>
        public static IEnumerable<string> AllNames()
        {
            foreach (string name in Names)
            {
                yield return name;
            }
        }
    }
}
