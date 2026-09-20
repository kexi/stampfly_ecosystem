using System;
using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// The world files a build carries, held as TextAssets so they are embedded
    /// in the WebGL build. StreamingAssets would put them behind an extra HTTP
    /// request that the loader would have to wait for; a TextAsset reference is
    /// already in memory when the scene loads.
    ///
    /// The asset is generated from the shipped worlds by the editor menu
    /// StampFly &gt; World &gt; Rebuild Catalog.
    ///
    /// ビルドが持つ空間ファイルの一覧。ビルドに埋め込むため TextAsset で持つ。
    /// StreamingAssets にすると HTTP の要求が 1 つ増え、読み込み側が待つことに
    /// なる。TextAsset の参照は場面を読み込んだ時点ですでに手元にある。
    ///
    /// この資産はエディタのメニュー StampFly &gt; World &gt; Rebuild Catalog が
    /// 同梱の空間から作る。
    /// </summary>
    [CreateAssetMenu(fileName = "WorldCatalog", menuName = "StampFly/World Catalog")]
    public sealed class WorldCatalog : ScriptableObject
    {
        /// <summary>
        /// One entry: the world's name and the JSON that describes it.
        /// 項目 1 つ。空間の名前と、それを述べる JSON。
        /// </summary>
        [Serializable]
        public struct Entry
        {
            /// <summary>The world's `name`, i.e. the file stem. / 空間の name。</summary>
            public string name;

            /// <summary>The world file, embedded. / 埋め込んだ空間ファイル。</summary>
            public TextAsset json;
        }

        [SerializeField]
        private Entry[] entries = Array.Empty<Entry>();

        /// <summary>The entries, in the order the asset holds them. / 保持順の一覧。</summary>
        public Entry[] Entries => entries ?? Array.Empty<Entry>();

        /// <summary>How many worlds this catalog holds. / 持っている空間の数。</summary>
        public int Count => Entries.Length;

        /// <summary>
        /// Every world's name, in the catalog's order. / 空間の名前を保持順に。
        /// </summary>
        public string[] Names()
        {
            Entry[] held = Entries;
            string[] names = new string[held.Length];
            for (int index = 0; index < held.Length; index++)
            {
                names[index] = held[index].name;
            }

            return names;
        }

        /// <summary>
        /// The JSON of the named world, or null when the catalog has no such
        /// world. / 名前の空間の JSON。無ければ null。
        /// </summary>
        public string Json(string worldName)
        {
            foreach (Entry entry in Entries)
            {
                if (entry.name == worldName && entry.json != null)
                {
                    return entry.json.text;
                }
            }

            return null;
        }

        /// <summary>
        /// Replace the catalog's contents. Used by the editor tool that builds
        /// it; there is no reason to change a catalog at runtime.
        /// 中身を入れ替える。作る側のエディタの道具が使う。実行中に変える理由は
        /// 無い。
        /// </summary>
        public void SetEntries(Entry[] replacement)
        {
            entries = replacement ?? Array.Empty<Entry>();
        }
    }
}
