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
    ///
    /// Besides building, it does two things the player cannot do for itself:
    /// it copies the firmware's own wasm module beside the player (the firmware
    /// is a SEPARATE module, not linked in), and it turns on Decompression
    /// Fallback, because GitHub Pages cannot set <c>Content-Encoding</c> and a
    /// Brotli file served without that header is not decompressed by the browser.
    ///
    /// ビルドのほかに、プレイヤー自身にはできない 2 つを行う。ファーム自身の wasm
    /// モジュールをプレイヤーの隣へ複写すること（ファームは**別の**モジュールで
    /// あり、リンクされない）と、Decompression Fallback を有効にすることである。
    /// GitHub Pages は <c>Content-Encoding</c> を付けられず、そのヘッダ無しで
    /// 配られた Brotli のファイルをブラウザは展開しないためである。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（公開）
    /// </summary>
    public static class WebGLBuilder
    {
        // The flag the CLI passes the output path with.
        // CLI が出力先を渡してくる引数名。
        private const string OutputPathFlag = "-buildOutput";

        // The flag `sf unity build --release` passes. A distributed build must
        // not carry the command relay endpoint, so the symbol that compiles it
        // in is added for a development build and left off for a release one.
        // `sf unity build --release` が渡す引数。配布用ビルドには命令の中継の
        // 受け口を入れないので、それを入れる定義記号は開発用ビルドにだけ付け、
        // 配布用では付けない。
        private const string ReleaseFlag = "-stampflyRelease";

        // The symbol that compiles the relay endpoint in. Renaming it here means
        // renaming it in the code that tests for it.
        // 中継の受け口を入れる定義記号。ここで名前を変えるなら、判定している
        // コード側の名前も変える。
        private const string RemoteSymbol = "STAMPFLY_REMOTE";

        // Where the build lands when nothing says otherwise.
        // 指定が無いときの出力先。
        private const string DefaultOutputPath = "Build/WebGL";

        // The page the player is served in. "PROJECT:" means the template comes
        // from Assets/WebGLTemplates rather than from the editor's own set.
        // プレイヤーを載せるページ。"PROJECT:" は、エディタ自身の雛形ではなく
        // Assets/WebGLTemplates から取ることを表す。
        private const string WebGlTemplate = "PROJECT:StampFly";

        // The firmware module, relative to the repository root, and where it
        // goes in the output. StreamingAssets is served as plain files beside
        // the player, which is what the .jslib's <script> tag needs.
        // ファームのモジュールの、リポジトリ直下からの相対の場所と、出力での
        // 置き場。StreamingAssets はプレイヤーの隣に素のファイルとして配られる。
        // .jslib の <script> のタグが要るのはそれである。
        private const string FirmwareBuildDirectory = "simulator/unity/native/build-wasm";
        private const string FirmwareOutputDirectory = "StreamingAssets";
        private static readonly string[] FirmwareFiles =
        {
            "sfu_firmware.js",
            "sfu_firmware.wasm",
        };

        /// <summary>
        /// Builds the enabled scenes for WebGL and exits non-zero if anything
        /// did not succeed, so the CLI reports the failure.
        /// 有効な場面を WebGL 向けにビルドする。何かが失敗したら非ゼロで終了し、
        /// CLI に失敗を伝える。
        /// </summary>
        public static void Build()
        {
            string outputPath = ReadOutputPath();
            bool isRelease = HasFlag(ReleaseFlag);

            ApplyWebGlSettings(isRelease);

            var options = new BuildPlayerOptions
            {
                scenes = EnabledScenePaths(),
                locationPathName = outputPath,
                target = BuildTarget.WebGL,
                targetGroup = BuildTargetGroup.WebGL,
                options = BuildOptions.None,
            };

            BuildReport report = BuildPipeline.BuildPlayer(options);
            BuildSummary summary = report.summary;

            Debug.Log(
                $"[WebGLBuilder] result={summary.result} " +
                $"output={outputPath} scenes={options.scenes.Length} " +
                $"release={isRelease} size={summary.totalSize} bytes " +
                $"duration={summary.totalTime.TotalSeconds:F1} s");

            bool buildFailed = summary.result != BuildResult.Succeeded;
            if (buildFailed)
            {
                EditorApplication.Exit(1);
                return;
            }

            WriteManifest(outputPath, isRelease, report);

            bool copied = CopyFirmwareModule(outputPath);
            if (!copied)
            {
                EditorApplication.Exit(1);
            }
        }

        /// <summary>
        /// The file this build writes beside the player, saying what went into
        /// it. It names the managed assemblies the build actually included and
        /// whether the relay symbol was defined.
        /// このビルドがプレイヤーの隣に書くファイルの名前。何が入ったかを述べる。
        /// ビルドが実際に含めたマネージドのアセンブリと、中継の定義記号が付いて
        /// いたかを載せる。
        /// </summary>
        public const string ManifestFileName = "stampfly-build-manifest.json";

        /// <summary>
        /// Record what this build contains, so `sf unity build --release` can
        /// check the plan's "the relay endpoint is included only in
        /// development builds" (§4) against the build rather than against the
        /// intention.
        ///
        /// A WebGL player's own code ends up inside a Brotli-compressed
        /// `.unityweb`, which the PC side cannot open without a Brotli decoder
        /// it does not have. The editor, however, knows exactly which
        /// assemblies IL2CPP was handed; writing that list here turns a check
        /// that would have to guess into one that reads an answer.
        ///
        /// このビルドが何を含むかを記録する。`sf unity build --release` が計画 §4
        /// の「中継の受け口は開発用ビルドだけに入れる」を、意図に対してではなく
        /// ビルドに対して確かめられるようにするためである。
        ///
        /// WebGL のプレイヤー自身のコードは Brotli で圧縮された `.unityweb` の中に
        /// 入り、PC 側は持っていない Brotli の復号器なしには開けない。一方エディタ
        /// は、IL2CPP に何のアセンブリを渡したかを正確に知っている。その一覧を
        /// ここに書くことで、推測するほかなかった検査が、答えを読む検査になる。
        /// </summary>
        private static void WriteManifest(string outputPath, bool isRelease,
                                          BuildReport report)
        {
            // Asked of the compilation pipeline rather than read off the build's
            // files: WebGL compiles every managed assembly into the wasm module
            // with IL2CPP, so `report.GetFiles()` lists no `.dll` at all. This
            // API answers which player assemblies were compiled FOR this target
            // with these define symbols, which is exactly the question --
            // `StampFly.Remote`'s `defineConstraints` decide whether it is in
            // this list.
            // ビルドのファイルから読むのではなく、翻訳の仕組みに尋ねる。WebGL は
            // マネージドのアセンブリを全て IL2CPP で wasm モジュールへ翻訳するので、
            // `report.GetFiles()` に `.dll` は 1 つも並ばない。この API は、この
            // ターゲットに対しこの定義記号で、どのプレイヤーのアセンブリが翻訳
            // されたかを答える。問いはまさにそれで、`StampFly.Remote` が並ぶかを
            // 決めるのはその `defineConstraints` である。
            var assemblies = new System.Collections.Generic.List<string>();
            foreach (UnityEditor.Compilation.Assembly assembly in
                     UnityEditor.Compilation.CompilationPipeline.GetAssemblies(
                         UnityEditor.Compilation.AssembliesType.PlayerWithoutTestAssemblies))
            {
                assemblies.Add(assembly.name);
            }

            assemblies.Sort(StringComparer.Ordinal);

            Debug.Log($"[WebGLBuilder] build produced {report.GetFiles().Length} files");

            string symbols = PlayerSettings.GetScriptingDefineSymbols(
                UnityEditor.Build.NamedBuildTarget.WebGL);

            var text = new System.Text.StringBuilder();
            text.Append("{\"release\":").Append(isRelease ? "true" : "false")
                .Append(",\"define_symbols\":\"").Append(symbols.Replace("\"", "\\\""))
                .Append("\",\"assemblies\":[");
            for (int index = 0; index < assemblies.Count; index++)
            {
                if (index > 0) { text.Append(','); }
                text.Append('"').Append(assemblies[index]).Append('"');
            }

            text.Append("]}");

            string manifestPath = Path.Combine(outputPath, ManifestFileName);
            File.WriteAllText(manifestPath, text.ToString());
            Debug.Log($"[WebGLBuilder] wrote {manifestPath} with " +
                      $"{assemblies.Count} assemblies");
        }

        /// <summary>
        /// The player settings the browser build needs, set here so a build from
        /// any machine produces the same thing rather than depending on what was
        /// last ticked in the editor.
        /// ブラウザ向けのビルドに要るプレイヤーの設定。どの機械からのビルドでも
        /// 同じものが出るよう、エディタで最後に付けた印に頼らずここで設定する。
        /// </summary>
        private static void ApplyWebGlSettings(bool isRelease)
        {
            // Brotli with the fallback loader: GitHub Pages cannot set
            // Content-Encoding, and the fallback loader decompresses in
            // JavaScript when the header is missing.
            // フォールバックのローダ付きの Brotli。GitHub Pages は
            // Content-Encoding を付けられず、ヘッダが無ければフォールバックの
            // ローダが JavaScript で展開する。
            PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Brotli;
            PlayerSettings.WebGL.decompressionFallback = true;

            // Our own page: it fills the window, reports a load failure without
            // opening a dialog, and carries the `?raf=worker` shim an automated
            // browser check needs.
            // 自前のページ。窓いっぱいに広がり、読み込みの失敗をダイアログを開かずに
            // 報告し、自動のブラウザ検証が要る `?raf=worker` の差し替えを持つ。
            PlayerSettings.WebGL.template = WebGlTemplate;

            // The firmware module is fetched as a plain file beside the player,
            // so the player's own data need not be embedded in the loader.
            // ファームのモジュールはプレイヤーの隣の素のファイルとして取得する
            // ので、プレイヤー自身のデータをローダに埋め込む必要は無い。
            PlayerSettings.WebGL.dataCaching = true;

            string existing = PlayerSettings.GetScriptingDefineSymbols(
                UnityEditor.Build.NamedBuildTarget.WebGL);
            string updated = isRelease
                ? RemoveSymbol(existing, RemoteSymbol)
                : AddSymbol(existing, RemoteSymbol);
            PlayerSettings.SetScriptingDefineSymbols(
                UnityEditor.Build.NamedBuildTarget.WebGL, updated);
        }

        /// <summary>Adds a symbol if it is not already there. / まだ無ければ定義記号を足す。</summary>
        private static string AddSymbol(string symbols, string symbol)
        {
            var parts = new System.Collections.Generic.List<string>(
                symbols.Split(';', StringSplitOptions.RemoveEmptyEntries));
            bool alreadyThere = parts.Contains(symbol);
            if (!alreadyThere)
            {
                parts.Add(symbol);
            }
            return string.Join(";", parts);
        }

        /// <summary>Removes a symbol if it is there. / あれば定義記号を外す。</summary>
        private static string RemoveSymbol(string symbols, string symbol)
        {
            var parts = new System.Collections.Generic.List<string>(
                symbols.Split(';', StringSplitOptions.RemoveEmptyEntries));
            parts.Remove(symbol);
            return string.Join(";", parts);
        }

        /// <summary>
        /// Copies the firmware's wasm module into the build. Without it the page
        /// loads and then sits waiting for a module that never arrives, so a
        /// missing module is a build failure with the command that fixes it.
        /// ファームの wasm モジュールをビルドへ複写する。無ければページは読み込ま
        /// れた後、決して来ないモジュールを待ち続ける。よってモジュールが無いことは
        /// ビルドの失敗とし、直すためのコマンドを添えて報告する。
        /// </summary>
        private static bool CopyFirmwareModule(string outputPath)
        {
            string sourceDirectory = Path.Combine(
                RepositoryRoot(), FirmwareBuildDirectory);
            string destinationDirectory = Path.Combine(
                outputPath, FirmwareOutputDirectory);
            Directory.CreateDirectory(destinationDirectory);

            foreach (string fileName in FirmwareFiles)
            {
                string source = Path.Combine(sourceDirectory, fileName);
                bool sourceIsMissing = !File.Exists(source);
                if (sourceIsMissing)
                {
                    Debug.LogError(
                        $"[WebGLBuilder] the firmware module is not at {source}. " +
                        "Run `nix develop -c just unity-native-build` first.");
                    return false;
                }

                File.Copy(source, Path.Combine(destinationDirectory, fileName), true);
            }

            Debug.Log($"[WebGLBuilder] copied the firmware module into " +
                      $"{destinationDirectory}");
            return true;
        }

        /// <summary>
        /// The repository root. The Unity project sits at
        /// <c>simulator/unity</c>, so the root is two levels above it.
        /// リポジトリ直下。Unity プロジェクトは <c>simulator/unity</c> に在るので、
        /// 直下はその 2 つ上である。
        /// </summary>
        private static string RepositoryRoot()
        {
            string projectRoot = Directory.GetParent(Application.dataPath).FullName;
            return Directory.GetParent(
                Directory.GetParent(projectRoot).FullName).FullName;
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

        /// <summary>Whether a bare flag is on the command line. / 値を取らない引数が在るか。</summary>
        private static bool HasFlag(string flag)
        {
            foreach (string argument in Environment.GetCommandLineArgs())
            {
                bool isFlag = argument == flag;
                if (isFlag)
                {
                    return true;
                }
            }

            return false;
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
