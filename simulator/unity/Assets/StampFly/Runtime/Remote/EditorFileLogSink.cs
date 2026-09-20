/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the editor's log file).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.IO;
using StampFly.Core;
using UnityEngine;

namespace StampFly.Remote
{
    /// <summary>
    /// Appends lines to <c>simulator/unity/Logs/stampfly/&lt;run_id&gt;.jsonl</c>.
    ///
    /// The editor's play mode has no <c>/api/hello</c> to answer it, so nothing
    /// would collect its lines: <c>sf unity serve</c> is not involved when
    /// somebody presses play. This writes the same JSON Lines to the same shape
    /// of path, so the same <c>jq</c> and the same reading apply. The directory
    /// is outside git (AGENTS.md, "git で管理されないもの").
    ///
    /// <c>simulator/unity/Logs/stampfly/&lt;run_id&gt;.jsonl</c> へ行を追記する。
    ///
    /// エディタの再生には応える <c>/api/hello</c> が無く、行を集める者がいない。
    /// 再生ボタンを押すとき <c>sf unity serve</c> は関わらないためである。ここでは
    /// 同じ JSON Lines を同じ形のパスへ書き、同じ <c>jq</c> と同じ読み方が通る
    /// ようにする。この置き場は git 管理外である（AGENTS.md「git で管理されない
    /// もの」）。
    /// </summary>
    public sealed class EditorFileLogSink : ILogSink, IDisposable
    {
        /// <summary>
        /// Where the editor's runs are kept, relative to the Unity project.
        /// Unity プロジェクトから見た、エディタの実行の置き場。
        /// </summary>
        public const string Subdirectory = "Logs/stampfly";

        private StreamWriter writer;

        /// <summary>
        /// Open the file for this run. A run that cannot open its file logs
        /// that once to the console and then keeps quiet; play mode must not
        /// stop because a log file could not be created.
        /// この実行のファイルを開く。開けなかった実行はその旨を 1 回コンソールへ
        /// 出し、以後黙る。ログファイルを作れなかったことで再生が止まっては
        /// ならない。
        /// </summary>
        public EditorFileLogSink(string runId)
        {
            try
            {
                string directory = DirectoryPath();
                Directory.CreateDirectory(directory);
                Path = System.IO.Path.Combine(directory, runId + ".jsonl");
                writer = new StreamWriter(Path, append: true) { AutoFlush = true };
            }
            catch (Exception failure)
            {
                Debug.LogWarning($"[StampFly] cannot write the editor's log: {failure.Message}");
                writer = null;
            }
        }

        /// <summary>The file being written, or null. / 書いているファイル。</summary>
        public string Path { get; private set; }

        /// <summary>
        /// <c>simulator/unity/Logs/stampfly</c>, found from
        /// <c>Application.dataPath</c> (which ends in <c>/Assets</c>).
        /// <c>Application.dataPath</c>（末尾が <c>/Assets</c>）から求めた
        /// <c>simulator/unity/Logs/stampfly</c>。
        /// </summary>
        public static string DirectoryPath()
        {
            string projectRoot = Directory.GetParent(Application.dataPath).FullName;
            return System.IO.Path.Combine(projectRoot, Subdirectory);
        }

        /// <inheritdoc />
        public void Write(string line)
        {
            // AutoFlush keeps every line on disk as it happens, so `tail -f` and
            // a play session that is stopped abruptly both see complete lines --
            // the same reason lib/sfcli/utils/jsonl_log.py flushes per line.
            // AutoFlush により各行がその場でディスクに載る。`tail -f` も、途中で
            // 止めた再生も、完全な行だけを見る。lib/sfcli/utils/jsonl_log.py が
            // 1 行ごとに flush するのと同じ理由である。
            writer?.WriteLine(line);
        }

        /// <summary>Close the file. / ファイルを閉じる。</summary>
        public void Dispose()
        {
            writer?.Dispose();
            writer = null;
        }
    }
}
