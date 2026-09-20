/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the .jslib seam).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Runtime.InteropServices;

namespace StampFly.Remote
{
    /// <summary>
    /// How the page reached this run's identity.
    /// このページがこの実行の素性をどう得たか。
    /// </summary>
    public enum RemoteMode
    {
        /// <summary><c>/api/hello</c> has not answered yet. / まだ応えが無い。</summary>
        Unknown = 0,

        /// <summary>Served by <c>sf unity serve</c>. / <c>sf unity serve</c> が配信。</summary>
        Local = 1,

        /// <summary>No local server; a public page. / ローカルサーバ無し。公開ページ。</summary>
        Public = 2,
    }

    /// <summary>
    /// The functions <c>RemoteBridge.jslib</c> exports, in one place.
    ///
    /// Outside a WebGL player there is no <c>__Internal</c> library to bind to,
    /// so every call falls back to a value that means "nothing is connected".
    /// That is what lets the editor run the same code without a
    /// <c>#if UNITY_WEBGL</c> at every call site, and what lets an EditMode test
    /// exercise the C# side at all.
    ///
    /// <c>RemoteBridge.jslib</c> が輸出する関数を 1 か所にまとめたもの。
    ///
    /// WebGL のプレイヤー以外に結び付ける <c>__Internal</c> は無いので、どの
    /// 呼び出しも「何も繋がっていない」を意味する値に落ちる。これにより、
    /// エディタでも呼び出しごとの <c>#if UNITY_WEBGL</c> 無しに同じコードが
    /// 動き、EditMode の試験が C# 側を動かせる。
    /// </summary>
    internal static class RemoteNative
    {
#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")]
        private static extern void SfuRemoteStart(string target, string method);

        [DllImport("__Internal")]
        private static extern int SfuRemoteMode();

        [DllImport("__Internal")]
        private static extern string SfuRemoteRunId();

        [DllImport("__Internal")]
        private static extern string SfuRemoteUrlCommands();

        [DllImport("__Internal")]
        private static extern void SfuRemoteLog(string line);

        [DllImport("__Internal")]
        private static extern int SfuRemoteTakeDropped();

        [DllImport("__Internal")]
        private static extern void SfuRemoteAnswer(string cmdId, int ok, string data,
                                                   string error);

        [DllImport("__Internal")]
        private static extern int SfuRemoteTrace(string body);

        /// <summary>Whether the .jslib is there to call. / 呼べる .jslib があるか。</summary>
        internal const bool Available = true;

        /// <summary>
        /// Post a whole trace to <c>/api/trace</c>. Returns whether the page had
        /// a local server to post to; a public page has none and says so rather
        /// than dropping several megabytes on the floor in silence.
        /// トレース全体を <c>/api/trace</c> へ送る。送り先のローカルサーバが
        /// あったかを返す。公開ページには無いので、数メガバイトを黙って捨てるの
        /// ではなく、その旨を返す。
        /// </summary>
        internal static bool Trace(string body)
        {
            return SfuRemoteTrace(body) != 0;
        }

        /// <summary>Start the bridge. / 橋を始める。</summary>
        internal static void Start(string target, string method)
        {
            SfuRemoteStart(target, method);
        }

        /// <summary>How far the handshake got. / 握手の進み具合。</summary>
        internal static RemoteMode Mode()
        {
            return (RemoteMode)SfuRemoteMode();
        }

        /// <summary>This run's id. / この実行の id。</summary>
        internal static string RunId()
        {
            return SfuRemoteRunId();
        }

        /// <summary>The commands the URL asks for, as JSON. / URL が求める命令の JSON。</summary>
        internal static string UrlCommands()
        {
            return SfuRemoteUrlCommands();
        }

        /// <summary>Hand one finished line over. / 出来上がった行を 1 つ渡す。</summary>
        internal static void Log(string line)
        {
            SfuRemoteLog(line);
        }

        /// <summary>Lines the page's queue threw away. / ページの待ち行列が捨てた行。</summary>
        internal static int TakeDropped()
        {
            return SfuRemoteTakeDropped();
        }

        /// <summary>Answer one command. / 命令 1 つに答える。</summary>
        internal static void Answer(string cmdId, bool ok, string data, string error)
        {
            SfuRemoteAnswer(cmdId, ok ? 1 : 0, data ?? string.Empty, error ?? string.Empty);
        }
#else
        /// <summary>Whether the .jslib is there to call. / 呼べる .jslib があるか。</summary>
        internal const bool Available = false;

        /// <summary>Nothing to start outside a browser. / ブラウザ以外に始めるものは無い。</summary>
        internal static void Start(string target, string method)
        {
        }

        /// <summary>Never connected outside a browser. / ブラウザ以外では繋がらない。</summary>
        internal static RemoteMode Mode()
        {
            return RemoteMode.Public;
        }

        /// <summary>No run id from a page that is not one. / ページでないので id は無い。</summary>
        internal static string RunId()
        {
            return string.Empty;
        }

        /// <summary>No URL to read arguments from. / 引数を読む URL が無い。</summary>
        internal static string UrlCommands()
        {
            return "[]";
        }

        /// <summary>Nowhere to send a line. / 行を送る先が無い。</summary>
        internal static void Log(string line)
        {
        }

        /// <summary>No queue, so nothing was dropped. / 待ち行列が無く、損失も無い。</summary>
        internal static int TakeDropped()
        {
            return 0;
        }

        /// <summary>Nobody is waiting for an answer. / 答えを待っている者はいない。</summary>
        internal static void Answer(string cmdId, bool ok, string data, string error)
        {
        }

        /// <summary>No server to post a trace to. / トレースを送るサーバが無い。</summary>
        internal static bool Trace(string body)
        {
            return false;
        }
#endif
    }
}
