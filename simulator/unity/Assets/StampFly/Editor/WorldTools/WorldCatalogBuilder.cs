using System.Collections.Generic;
using System.IO;
using StampFly.World;
using UnityEditor;
using UnityEngine;

namespace StampFly.Editor.WorldTools
{
    /// <summary>
    /// Builds and refreshes the WorldCatalog asset from the world files in
    /// Assets/StampFly/Worlds. The catalog holds TextAsset references so the
    /// worlds are embedded in a WebGL build rather than fetched at runtime.
    ///
    /// Run it from the menu after adding, renaming or removing a world file.
    ///
    /// Assets/StampFly/Worlds の空間ファイルから WorldCatalog の資産を作り直す。
    /// 一覧は TextAsset の参照を持つので、空間は WebGL のビルドに埋め込まれ、
    /// 実行時に取りに行くことにならない。
    ///
    /// 空間ファイルを足す・名前を変える・消したあとに、メニューから実行する。
    ///
    /// The folder is Editor/WorldTools rather than Editor/Build, because the
    /// repository-root .gitignore rule `build/` is matched case-insensitively
    /// on macOS and would exclude a folder named Build entirely (the same
    /// reason Editor/Builders carries that name).
    /// 置き場を Editor/Build ではなく Editor/WorldTools にしてあるのは、
    /// リポジトリ直下の .gitignore の build/ が macOS では大文字小文字を区別
    /// せず、Build という名前のフォルダをまるごと除外してしまうためである
    /// （Editor/Builders が同じ理由でその名前になっている）。
    /// </summary>
    public static class WorldCatalogBuilder
    {
        /// <summary>Where the shipped world files live. / 同梱の空間の置き場。</summary>
        public const string WorldsFolder = "Assets/StampFly/Worlds";

        /// <summary>Where the catalog asset is written. / 一覧の資産の置き場。</summary>
        public const string CatalogPath = "Assets/StampFly/Worlds/WorldCatalog.asset";

        private const string MenuPath = "StampFly/World/Rebuild Catalog";

        /// <summary>
        /// Rebuild the catalog from the folder, creating the asset when it does
        /// not exist yet. Returns how many worlds it holds afterwards.
        /// 置き場から一覧を作り直す。資産が無ければ作る。作り直した後に持って
        /// いる空間の数を返す。
        /// </summary>
        public static int Rebuild()
        {
            List<WorldCatalog.Entry> entries = CollectEntries();

            WorldCatalog catalog = AssetDatabase.LoadAssetAtPath<WorldCatalog>(CatalogPath);
            bool isNew = catalog == null;
            if (isNew)
            {
                catalog = ScriptableObject.CreateInstance<WorldCatalog>();
            }

            catalog.SetEntries(entries.ToArray());

            if (isNew)
            {
                AssetDatabase.CreateAsset(catalog, CatalogPath);
            }
            else
            {
                EditorUtility.SetDirty(catalog);
            }

            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            return entries.Count;
        }

        /// <summary>
        /// Read every *.world.json in the folder, sorted by name so the catalog
        /// is stable between rebuilds and its diff stays readable.
        /// 置き場の *.world.json を全て読む。名前順に並べ、作り直しても一覧が
        /// 変わらず、差分が読みやすい状態を保つ。
        /// </summary>
        private static List<WorldCatalog.Entry> CollectEntries()
        {
            List<WorldCatalog.Entry> entries = new List<WorldCatalog.Entry>();
            string[] paths = Directory.GetFiles(WorldsFolder, "*" + WorldFormat.FileSuffix);
            System.Array.Sort(paths, System.StringComparer.Ordinal);

            foreach (string path in paths)
            {
                string assetPath = path.Replace('\\', '/');
                TextAsset json = AssetDatabase.LoadAssetAtPath<TextAsset>(assetPath);
                if (json == null)
                {
                    Debug.LogWarning($"WorldCatalogBuilder: '{assetPath}' is not imported as a TextAsset "
                                   + "and was left out of the catalog");
                    continue;
                }

                entries.Add(new WorldCatalog.Entry { name = NameOf(assetPath), json = json });
            }

            return entries;
        }

        /// <summary>
        /// A world's name: the file stem, which the format requires to match
        /// the file's own `name` for a shipped world (Schemas/README.md §8).
        /// 空間の名前。ファイル名の幹で、同梱の空間では形式がファイル自身の
        /// name と一致させることを求めている（README §8）。
        /// </summary>
        private static string NameOf(string assetPath)
        {
            string fileName = Path.GetFileName(assetPath);
            return fileName.EndsWith(WorldFormat.FileSuffix)
                ? fileName.Substring(0, fileName.Length - WorldFormat.FileSuffix.Length)
                : Path.GetFileNameWithoutExtension(fileName);
        }

        [MenuItem(MenuPath)]
        private static void RebuildFromMenu()
        {
            int count = Rebuild();
            Debug.Log($"WorldCatalogBuilder: the catalog now holds {count} worlds ({CatalogPath})");
        }
    }
}
