/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — one command as it arrives).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.Collections.Generic;
using StampFly.Core;

namespace StampFly.Remote
{
    /// <summary>
    /// One command as the page received it:
    /// <c>{"cmd_id": "...", "command": "world.load", "args": {...}}</c>.
    ///
    /// Read with <see cref="SimCommandArgs"/>'s reader rather than
    /// <c>JsonUtility</c>, for the same reason the arguments are: <c>args</c> is
    /// an object whose keys differ per command, which <c>JsonUtility</c> cannot
    /// express.
    ///
    /// ページが受け取った命令 1 つ。
    /// <c>{"cmd_id": "...", "command": "world.load", "args": {...}}</c>。
    ///
    /// <c>JsonUtility</c> ではなく <see cref="SimCommandArgs"/> の読み手で読む。
    /// 理由は引数と同じで、<c>args</c> は命令ごとに鍵が変わるオブジェクトであり、
    /// <c>JsonUtility</c> には表せない。
    /// </summary>
    public readonly struct PendingCommand
    {
        private PendingCommand(string commandId, string command, SimCommandArgs args)
        {
            CommandId = commandId;
            Command = command;
            Args = args;
        }

        /// <summary>
        /// The id every line raised while handling this command carries. The
        /// server's value is returned unchanged; a command the page raised
        /// itself gets one of the same shape.
        /// この命令の処理中に出た全ての行が持つ識別子。サーバの値はそのまま
        /// 返し、ページ自身が起こした命令には同じ形のものを付ける。
        /// </summary>
        public string CommandId { get; }

        /// <summary>The dotted name, e.g. <c>world.load</c>. / 点区切りの名前。</summary>
        public string Command { get; }

        /// <summary>Its arguments. / その引数。</summary>
        public SimCommandArgs Args { get; }

        /// <summary>
        /// The same command with a freshly issued id, for one the page raised
        /// itself (a URL argument) and that therefore has none.
        /// 同じ命令に新しい識別子を付けたもの。ページ自身が起こした命令（URL の
        /// 引数）は識別子を持たないため。
        /// </summary>
        public PendingCommand WithNewId()
        {
            bool hasId = !string.IsNullOrEmpty(CommandId);
            return hasId
                ? this
                : new PendingCommand(RunIdentifiers.NewCommandId(), Command, Args);
        }

        /// <summary>
        /// Read one request. An unreadable payload becomes a command with no
        /// name, which the registry refuses with a reason -- better than
        /// throwing inside a <c>SendMessage</c> callback, where nothing would
        /// catch it.
        /// 要求を 1 つ読む。読めない中身は名前の無い命令になり、登録簿が理由を
        /// 付けて断る。<c>SendMessage</c> の折り返しの中で例外を投げても誰も
        /// 捕まえないので、そちらより良い。
        /// </summary>
        public static PendingCommand Parse(string payload)
        {
            SimCommandArgs envelope = SimCommandArgs.Parse(payload);
            return new PendingCommand(
                envelope.String("cmd_id", string.Empty),
                envelope.String("command", string.Empty),
                SimCommandArgs.Parse(envelope.String("args", "{}")));
        }

        /// <summary>
        /// Read a JSON array of requests, as <c>SfuRemoteUrlCommands</c> returns
        /// it. The array is split at the top level and each element read on its
        /// own, which is all the structure a list of commands needs.
        /// <c>SfuRemoteUrlCommands</c> が返す要求の JSON 配列を読む。一番上の
        /// 段で分けて要素ごとに読む。命令の並びに要る構造はそれだけである。
        /// </summary>
        public static void ParseList(string json, List<PendingCommand> into)
        {
            foreach (string element in SplitTopLevelObjects(json))
            {
                PendingCommand request = Parse(element);
                if (!string.IsNullOrEmpty(request.Command))
                {
                    into.Add(request);
                }
            }
        }

        /// <summary>
        /// The objects of a JSON array, each as its own text. Counts braces and
        /// skips quoted text, so a brace inside a world's name does not split
        /// an element.
        /// JSON の配列の各オブジェクトを、それぞれの文字列として返す。括弧を
        /// 数え、引用された文字列を読み飛ばすので、空間の名前の中の括弧で要素が
        /// 割れることはない。
        /// </summary>
        private static IEnumerable<string> SplitTopLevelObjects(string json)
        {
            List<string> elements = new List<string>();
            if (string.IsNullOrEmpty(json))
            {
                return elements;
            }

            int depth = 0;
            int start = -1;
            bool inString = false;

            for (int index = 0; index < json.Length; index++)
            {
                char character = json[index];
                if (inString)
                {
                    bool escaped = character == '\\';
                    if (escaped)
                    {
                        index++;
                        continue;
                    }

                    inString = character != '"';
                    continue;
                }

                if (character == '"')
                {
                    inString = true;
                    continue;
                }

                if (character == '{')
                {
                    if (depth == 0)
                    {
                        start = index;
                    }

                    depth++;
                    continue;
                }

                if (character != '}')
                {
                    continue;
                }

                depth--;
                bool closedOne = depth == 0 && start >= 0;
                if (closedOne)
                {
                    elements.Add(json.Substring(start, index - start + 1));
                    start = -1;
                }
            }

            return elements;
        }
    }
}
