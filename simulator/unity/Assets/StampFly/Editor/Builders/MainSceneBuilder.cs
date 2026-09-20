/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — building the main scene).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.IO;
using StampFly.App;
using StampFly.World;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace StampFly.Editor.Builders
{
    /// <summary>
    /// Writes <c>Assets/StampFly/Scenes/Main.unity</c> and makes it the first
    /// scene in Build Settings.
    ///
    /// <c>Assets/StampFly/Scenes/Main.unity</c> を書き、ビルド設定の最初の場面に
    /// する。
    ///
    /// The scene holds exactly one object, carrying
    /// <see cref="SimulatorBootstrap"/>, which builds everything else at run
    /// time. Generating it from here rather than editing it by hand means the
    /// reference to the world catalog carries the right GUID without anyone
    /// typing one, and a scene lost to a merge can be made again with one
    /// command.
    ///
    /// 場面が持つのは、<see cref="SimulatorBootstrap"/> を載せた物体 1 つだけで、
    /// 残りは実行時にそれが組み立てる。手で編集せずここから生成するので、空間の
    /// 一覧への参照は誰も GUID を打たずに正しいものになり、統合で失った場面も
    /// コマンド 1 つで作り直せる。
    ///
    /// ```bash
    /// # From the editor's menu / エディタのメニューから:
    /// #   StampFly > Scenes > Rebuild Main Scene
    /// # From a terminal / 端末から:
    /// unity run . --execute-method \
    ///   StampFly.Editor.Builders.MainSceneBuilder.Rebuild --non-interactive
    /// ```
    ///
    /// The folder is <c>Editor/Builders/</c> rather than <c>Editor/Build/</c>
    /// because the repository-root <c>.gitignore</c> rule <c>build/</c> is
    /// matched without regard to case on macOS.
    /// 置き場を <c>Editor/Build/</c> ではなく <c>Editor/Builders/</c> にしてあるのは、
    /// リポジトリ直下の <c>.gitignore</c> の <c>build/</c> が macOS では大文字
    /// 小文字を区別せずに当たるためである。
    ///
    /// @design docs/plans/unity-simulator.md §5 段階 3
    /// </summary>
    public static class MainSceneBuilder
    {
        /// <summary>Where the scene is written. / 場面を書く場所。</summary>
        public const string ScenePath = "Assets/StampFly/Scenes/Main.unity";

        /// <summary>The catalog the bootstrap reads its world from. / 組み立て側が空間を読む一覧。</summary>
        public const string CatalogPath = "Assets/StampFly/Worlds/WorldCatalog.asset";

        /// <summary>
        /// Builds the scene, saves it, and puts it first in Build Settings.
        /// 場面を作って保存し、ビルド設定の先頭に置く。
        /// </summary>
        [MenuItem("StampFly/Scenes/Rebuild Main Scene")]
        public static void Rebuild()
        {
            Scene scene = EditorSceneManager.NewScene(
                NewSceneSetup.EmptyScene, NewSceneMode.Single);

            var root = new GameObject("StampFly");
            var bootstrap = root.AddComponent<SimulatorBootstrap>();
            bootstrap.worldName = SimulatorBootstrap.DefaultWorldName;
            bootstrap.worldCatalog = AssetDatabase.LoadAssetAtPath<WorldCatalog>(CatalogPath);

            bool catalogIsMissing = bootstrap.worldCatalog == null;
            if (catalogIsMissing)
            {
                Debug.LogWarning(
                    $"[MainSceneBuilder] no catalog at {CatalogPath}. Rebuild it with " +
                    "StampFly > World > Rebuild Catalog, then rebuild this scene.");
            }

            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));
            EditorSceneManager.SaveScene(scene, ScenePath);
            AssetDatabase.Refresh();

            MakeFirstInBuildSettings();
            Debug.Log($"[MainSceneBuilder] wrote {ScenePath} and made it the first " +
                      "scene in Build Settings");
        }

        /// <summary>
        /// Put the main scene first and enabled, leaving any other scene where
        /// it is. First, because a WebGL player loads the first enabled scene.
        /// 主の場面を先頭に置いて有効にし、他の場面はそのままにする。先頭にする
        /// のは、WebGL のプレイヤーが最初の有効な場面を読み込むためである。
        /// </summary>
        private static void MakeFirstInBuildSettings()
        {
            var scenes = new System.Collections.Generic.List<EditorBuildSettingsScene>
            {
                new EditorBuildSettingsScene(ScenePath, true),
            };

            foreach (EditorBuildSettingsScene scene in EditorBuildSettings.scenes)
            {
                bool isThisOne = scene.path == ScenePath;
                if (!isThisOne)
                {
                    scenes.Add(scene);
                }
            }

            EditorBuildSettings.scenes = scenes.ToArray();
        }
    }
}
