/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the editor's route in).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using StampFly.Core;
using StampFly.Remote;
using Unity.Pipeline.Commands;
using UnityEditor;

namespace StampFly.Editor.Cli
{
    /// <summary>
    /// The editor's route to the same registry <c>sf unity cmd</c> reaches.
    ///
    /// `com.unity.pipeline` listens over local HTTP and does not reach a WebGL
    /// player, so the browser has its own path (this project's
    /// <c>RemoteBridge.jslib</c> and <c>sf unity serve</c>). What both routes
    /// share is the registry: the same handler answers, which is the plan's
    /// "different routes produce identical results" (§4).
    ///
    /// <c>sf unity cmd</c> が届くのと同じ登録簿への、エディタからの経路。
    ///
    /// `com.unity.pipeline` はローカルの HTTP で待ち受け、WebGL のプレイヤーへは
    /// 届かない。だからブラウザには別の経路（この企画の
    /// <c>RemoteBridge.jslib</c> と <c>sf unity serve</c>）がある。2 つの経路が
    /// 共有するのは登録簿で、答えるのは同じ処理である。計画 §4 の「経路が違っても
    /// 結果は同じ」がこれである。
    ///
    /// The declaration rules are the ones stage 1(e) measured
    /// (<c>simulator/unity/README.md</c> §4): a <b>static</b> method, any
    /// visibility, with <c>[CliArg]</c> on its parameters.
    /// 宣言の規則は段階 1(e) で確かめたもの（<c>simulator/unity/README.md</c>
    /// §4）。<b>static</b> メソッドで、公開範囲は問わず、引数に
    /// <c>[CliArg]</c> を付ける。
    /// </summary>
    public static class SimCommandCli
    {
        /// <summary>
        /// Run one command against the registry the running play mode owns.
        ///
        /// The arguments are one JSON object, the same shape
        /// <c>sf unity cmd --json</c> sends, because a command's arguments
        /// differ per command and a fixed parameter list could not carry them.
        ///
        /// 動いている再生が持つ登録簿に対して命令を 1 つ実行する。
        ///
        /// 引数は JSON のオブジェクト 1 つで、<c>sf unity cmd --json</c> が送る
        /// 形と同じである。引数は命令ごとに違い、決まった引数の並びでは運べない
        /// ためである。
        /// </summary>
        [CliCommand("stampfly_cmd",
            "Run one simulator command against the running play mode",
            Tags = new[] { "stampfly" })]
        public static object Run(
            [CliArg("command", "The command's dotted name, e.g. world.load", Required = true)]
            string command,
            [CliArg("args", "The arguments as a JSON object", DefaultValue = "{}")]
            string args)
        {
            RemoteBridge bridge = RemoteBridge.Instance;
            if (bridge == null)
            {
                return new
                {
                    ok = false,
                    error = EditorApplication.isPlaying
                        ? "the scene has no RemoteBridge component"
                        : "press play first: commands run against the running simulator",
                };
            }

            string commandId = RunIdentifiers.NewCommandId();
            using (bridge.Log.BeginCommand(commandId))
            {
                SimCommandResult result =
                    bridge.Commands.Execute(command, SimCommandArgs.Parse(args));

                // `unity command` answers from this one call and cannot hold a
                // request open across frames, so a handler that wanted to
                // answer later is reported as such rather than as a silent
                // success. The browser route (`sf unity cmd`) carries the
                // cmd_id and does wait.
                // `unity command` はこの 1 回の呼び出しで答え、要求をフレームを
                // またいで保持できない。よって後で答えたい処理は、黙った成功では
                // なくそのように報告する。ブラウザの経路（`sf unity cmd`）は
                // cmd_id を運ぶので待てる。
                if (result.IsDeferred)
                {
                    return new
                    {
                        ok = false,
                        cmd_id = commandId,
                        data = (string)null,
                        error = $"{command} answers from a later frame, which " +
                                "`unity command` cannot wait for; use " +
                                "`sf unity cmd` against a served page",
                    };
                }

                return new
                {
                    ok = result.Ok,
                    cmd_id = commandId,
                    data = result.Data,
                    error = result.Error,
                };
            }
        }

        /// <summary>
        /// The registered command names, so a person at a terminal can see what
        /// this scene offers without guessing.
        /// 登録された命令の名前。端末にいる人が、推測せずにこの場面が何を提供する
        /// かを見られるようにする。
        /// </summary>
        [CliCommand("stampfly_cmd_list",
            "List the simulator commands the running scene registered",
            Tags = new[] { "stampfly" })]
        public static object List()
        {
            RemoteBridge bridge = RemoteBridge.Instance;
            if (bridge == null)
            {
                return new { ok = false, error = "no RemoteBridge is running" };
            }

            return new { ok = true, commands = bridge.Commands.Names() };
        }
    }
}
