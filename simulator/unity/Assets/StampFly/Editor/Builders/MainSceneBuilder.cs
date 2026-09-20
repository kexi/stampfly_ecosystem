/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — building the main scene).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.IO;
using StampFly.App;
using StampFly.Remote;
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
    /// The scene holds two objects: <see cref="SimulatorBootstrap"/>, which
    /// builds everything else at run time, and <see cref="RemoteBridge"/> on an
    /// object named <see cref="BridgeObjectName"/>, which is the address
    /// <c>RemoteBridge.jslib</c>'s <c>SendMessage</c> delivers commands to.
    /// Generating the scene from here rather than editing it by hand means the
    /// reference to the world catalog carries the right GUID without anyone
    /// typing one, and a scene lost to a merge can be made again with one
    /// command.
    ///
    /// 場面が持つ物体は 2 つ。実行時に残りを組み立てる
    /// <see cref="SimulatorBootstrap"/> と、<see cref="BridgeObjectName"/> という
    /// 名前の物体に載せた <see cref="RemoteBridge"/> である。後者の名前は
    /// <c>RemoteBridge.jslib</c> の <c>SendMessage</c> が命令を届ける宛先である。
    /// 手で編集せずここから生成するので、空間の一覧への参照は誰も GUID を打たずに
    /// 正しいものになり、統合で失った場面もコマンド 1 つで作り直せる。
    ///
    /// <c>StampFly.Remote</c> は <c>defineConstraints</c> を持ち、配布用ビルドには
    /// 入らない。それでも場面に置けるのは、<b>この組み立てがエディタでしか走らない</b>
    /// （<c>includePlatforms: ["Editor"]</c>）ためで、配布用ビルドではこの物体の
    /// 部品が欠けた状態、すなわち橋の無い場面として読み込まれる。<c>RemoteBridge</c>
    /// を探す側（<see cref="SimRemoteCommands"/>）は <c>RemoteBridge.Instance</c> の
    /// null を必ず確かめるので、欠けても壊れない。
    ///
    /// The relay assembly carries <c>defineConstraints</c> and is absent from a
    /// distributed build. Putting the bridge in the scene from here is still
    /// safe because this builder runs in the editor only, and a build without
    /// the assembly loads the scene with that component missing -- a scene with
    /// no bridge. Everything that looks for one checks
    /// <c>RemoteBridge.Instance</c> for null, so nothing breaks.
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
        /// What the object carrying <see cref="RemoteBridge"/> is called. This
        /// exact spelling is what <c>RemoteBridge.jslib</c> hands to
        /// <c>SendMessage</c>, so renaming the object here silently stops every
        /// command reaching the page. <see cref="RemoteBridge.Start"/> passes
        /// <c>gameObject.name</c> to JavaScript rather than assuming it, which
        /// is what keeps the two ends in step.
        /// <see cref="RemoteBridge"/> を載せる物体の名前。この綴りそのものを
        /// <c>RemoteBridge.jslib</c> が <c>SendMessage</c> に渡すので、ここで名前を
        /// 変えると命令がページへ届かなくなる（黙って）。
        /// <see cref="RemoteBridge.Start"/> は決め打ちせず
        /// <c>gameObject.name</c> を JavaScript へ渡すので、両端はこれで揃う。
        /// </summary>
        public const string BridgeObjectName = "StampFlyBridge";

        /// <summary>
        /// Builds the scene, saves it, and puts it first in Build Settings.
        /// 場面を作って保存し、ビルド設定の先頭に置く。
        /// </summary>
        [MenuItem("StampFly/Scenes/Rebuild Main Scene")]
        public static void Rebuild()
        {
            Scene scene = EditorSceneManager.NewScene(
                NewSceneSetup.EmptyScene, NewSceneMode.Single);

            AddBridge();

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
        /// Add the object the relay addresses. It goes in FIRST, so that the
        /// scene's serialized order puts it ahead of the bootstrap; that order
        /// is belt-and-braces only, because <see cref="RemoteBridge"/> already
        /// carries <c>[DefaultExecutionOrder(-10000)]</c> and its log and
        /// registry exist before any other <c>Awake</c> runs.
        /// 中継が宛先にする物体を足す。**先に**置き、場面の直列化の順で組み立てより
        /// 前に来るようにする。この順は念のためのもので、
        /// <see cref="RemoteBridge"/> は既に <c>[DefaultExecutionOrder(-10000)]</c>
        /// を持ち、ログと登録簿は他のどの <c>Awake</c> よりも先に出来ている。
        /// </summary>
        private static void AddBridge()
        {
            var bridge = new GameObject(BridgeObjectName);
            bridge.AddComponent<RemoteBridge>();
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
