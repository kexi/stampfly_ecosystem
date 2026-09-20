/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the end-to-end check's build).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.IO;
using StampFly.Remote;
using StampFly.World;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace StampFly.Editor.Cli
{
    /// <summary>
    /// Builds a WebGL player carrying only the page bridge and a world loader,
    /// for checking the terminal-to-page path end to end.
    ///
    /// It makes its own scene in a temporary folder rather than using the
    /// project's: the real scene belongs to the simulation loop's owner and
    /// changes as that work proceeds, and Build Settings is shared. A scene of
    /// two components answers the question this check asks -- does a command
    /// typed in a terminal reach a handler in a browser, and do the lines come
    /// back under one <c>cmd_id</c> -- without depending on, or disturbing,
    /// anything else.
    ///
    /// ページの橋と空間の読み込みだけを持つ WebGL のプレイヤーをビルドする。
    /// 端末からページまでの経路を端から端まで確かめるためのもの。
    ///
    /// 企画の場面を使わず、一時の置き場に自分の場面を作る。本物の場面は刻みの
    /// 輪の担当のもので、その作業に合わせて変わり、Build Settings は共有である。
    /// この確認が問うこと ― 端末で打った命令がブラウザの処理へ届くか、その行が
    /// 1 つの <c>cmd_id</c> で戻るか ― には、部品 2 つの場面で答えられる。他の
    /// 何にも依存せず、何も乱さずに済む。
    /// </summary>
    public static class RemoteCheckBuilder
    {
        // The flag the CLI passes the output path with, spelled as
        // `WebGLBuilder` spells it so one `-buildOutput` serves both.
        // CLI が出力先を渡す引数名。`WebGLBuilder` と同じ綴りにして、1 つの
        // `-buildOutput` が両方に効くようにする。
        private const string OutputPathFlag = "-buildOutput";

        private const string DefaultOutputPath = "Build/RemoteCheck";

        // Somewhere Unity will accept a scene from and the repository ignores.
        // Unity が場面として受け入れ、リポジトリが無視する場所。
        private const string ScenePath = "Assets/StampFly/RemoteCheck.unity";

        private const string CatalogPath = "Assets/StampFly/Worlds/WorldCatalog.asset";

        /// <summary>
        /// Build the check's player, then remove the scene it needed.
        /// 確認用のプレイヤーをビルドし、そのために作った場面を片付ける。
        /// </summary>
        public static void Build()
        {
            string outputPath = ReadOutputPath();
            CreateScene();

            var options = new BuildPlayerOptions
            {
                scenes = new[] { ScenePath },
                locationPathName = outputPath,
                target = BuildTarget.WebGL,
                targetGroup = BuildTargetGroup.WebGL,

                // A development build is what carries `StampFly.Remote`: its
                // defineConstraints ask for UNITY_EDITOR, DEVELOPMENT_BUILD or
                // STAMPFLY_REMOTE, and only the second holds in a player.
                // `StampFly.Remote` を運ぶのは開発用ビルドである。その
                // defineConstraints は UNITY_EDITOR・DEVELOPMENT_BUILD・
                // STAMPFLY_REMOTE のいずれかを求め、プレイヤーで成り立つのは
                // 2 つ目だけである。
                options = BuildOptions.Development,
            };

            BuildReport report = BuildPipeline.BuildPlayer(options);
            AssetDatabase.DeleteAsset(ScenePath);

            Debug.Log($"[RemoteCheckBuilder] result={report.summary.result} " +
                      $"output={outputPath} " +
                      $"duration={report.summary.totalTime.TotalSeconds:F1} s");

            if (report.summary.result != BuildResult.Succeeded)
            {
                EditorApplication.Exit(1);
            }
        }

        /// <summary>
        /// Make the scene: one object carrying the bridge, and one carrying the
        /// world loader with the shipped catalog assigned.
        /// 場面を作る。橋を持つ対象 1 つと、同梱の一覧を割り当てた空間の読み込み
        /// を持つ対象 1 つ。
        /// </summary>
        private static void CreateScene()
        {
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects,
                                                      NewSceneMode.Single);

            // The object's name is what SendMessage addresses from JavaScript,
            // so it is the one thing here that must not be renamed casually.
            // JavaScript の SendMessage が宛先にするのはこの対象の名前なので、
            // ここで軽々に変えてはならないのはこれだけである。
            new GameObject("StampFlyBridge", typeof(RemoteBridge));

            GameObject worldHost = new GameObject("World", typeof(WorldLoader),
                                                  typeof(WorldCommands));
            worldHost.GetComponent<WorldLoader>().Catalog =
                AssetDatabase.LoadAssetAtPath<WorldCatalog>(CatalogPath);

            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));
            EditorSceneManager.SaveScene(scene, ScenePath);
        }

        /// <summary>Reads -buildOutput from the command line. / -buildOutput を読む。</summary>
        private static string ReadOutputPath()
        {
            string[] arguments = System.Environment.GetCommandLineArgs();
            for (int index = 0; index < arguments.Length - 1; index++)
            {
                if (arguments[index] == OutputPathFlag)
                {
                    return arguments[index + 1];
                }
            }

            return Path.GetFullPath(DefaultOutputPath);
        }
    }
}
