/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — WebGL build entry point).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace StampFly.Editor.Builders
{
    /// <summary>
    /// The entry point <c>unity build --target WebGL --execute-method</c> calls.
    /// Unity has no built-in command-line build for WebGL, so the target needs
    /// either a build profile or a static method such as this one.
    ///
    /// <c>unity build --target WebGL --execute-method</c> が呼ぶ入口。WebGL には
    /// Unity 内蔵のコマンドライン用ビルドが無く、ビルドプロファイルかこのような
    /// static メソッドのどちらかが要る。
    /// </summary>
    public static class WebGLBuilder
    {
        // The flag the CLI passes the output path with.
        // CLI が出力先を渡してくる引数名。
        private const string OutputPathFlag = "-buildOutput";

        // Where the build lands when nothing says otherwise.
        // 指定が無いときの出力先。
        private const string DefaultOutputPath = "Build/WebGL";

        /// <summary>
        /// Builds the enabled scenes for WebGL and exits non-zero if the build
        /// did not succeed, so the CLI reports the failure.
        /// 有効な場面を WebGL 向けにビルドする。失敗したら非ゼロで終了し、CLI に
        /// 失敗を伝える。
        /// </summary>
        public static void Build()
        {
            string outputPath = ReadOutputPath();
            string[] scenes = EnabledScenePaths();

            var options = new BuildPlayerOptions
            {
                scenes = scenes,
                locationPathName = outputPath,
                target = BuildTarget.WebGL,
                targetGroup = BuildTargetGroup.WebGL,
                options = BuildOptions.None,
            };

            BuildReport report = BuildPipeline.BuildPlayer(options);
            BuildSummary summary = report.summary;

            Debug.Log(
                $"[WebGLBuilder] result={summary.result} " +
                $"output={outputPath} scenes={scenes.Length} " +
                $"size={summary.totalSize} bytes " +
                $"duration={summary.totalTime.TotalSeconds:F1} s");

            bool succeeded = summary.result == BuildResult.Succeeded;
            if (!succeeded)
            {
                EditorApplication.Exit(1);
            }
        }

        /// <summary>Reads -buildOutput from the command line. / -buildOutput を読む。</summary>
        private static string ReadOutputPath()
        {
            string[] arguments = Environment.GetCommandLineArgs();
            for (int index = 0; index < arguments.Length - 1; index++)
            {
                bool isOutputFlag = arguments[index] == OutputPathFlag;
                if (isOutputFlag)
                {
                    return arguments[index + 1];
                }
            }

            return Path.GetFullPath(DefaultOutputPath);
        }

        /// <summary>
        /// The scenes ticked in Build Settings, or the one open scene when none are.
        /// Build Settings で有効にした場面。無ければ開いている場面 1 つ。
        /// </summary>
        private static string[] EnabledScenePaths()
        {
            var paths = new System.Collections.Generic.List<string>();
            foreach (EditorBuildSettingsScene scene in EditorBuildSettings.scenes)
            {
                if (scene.enabled)
                {
                    paths.Add(scene.path);
                }
            }

            return paths.ToArray();
        }
    }
}
