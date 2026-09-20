/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — world.* on the registry).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Text;
using StampFly.Core;
using StampFly.World;
using UnityEngine;

namespace StampFly.Remote
{
    /// <summary>
    /// Puts <c>world.list</c> and <c>world.load</c> on the registry.
    ///
    /// A thin registration and nothing more: the work is
    /// <see cref="WorldLoader"/>'s and <see cref="WorldCatalog"/>'s, and this
    /// only names it so a terminal can reach it. The rest of the world group
    /// (<c>world.save</c>, <c>obstacle.*</c>) belongs to whoever owns the
    /// editing UI and registers alongside these.
    ///
    /// <c>world.list</c> と <c>world.load</c> を登録簿に載せる。
    ///
    /// 載せるだけで、仕事は <see cref="WorldLoader"/> と
    /// <see cref="WorldCatalog"/> のものである。ここは端末から届くよう名前を
    /// 付けるだけ。空間の組の残り（<c>world.save</c>・<c>obstacle.*</c>）は、
    /// 編集の画面を持つ担当がこれらの隣に登録する。
    /// </summary>
    [RequireComponent(typeof(WorldLoader))]
    public sealed class WorldCommands : MonoBehaviour
    {
        private WorldLoader loader;

        /// <summary>
        /// Register with the bridge, and send the world's own log lines through
        /// the run's log so <c>world.loaded</c> lands in the same file, under
        /// the same <c>cmd_id</c>, as the command that caused it.
        /// 橋へ登録し、空間自身のログの行をこの実行のログへ通す。
        /// <c>world.loaded</c> が、それを起こした命令と同じ <c>cmd_id</c> で
        /// 同じファイルに入るようにする。
        /// </summary>
        private void Start()
        {
            loader = GetComponent<WorldLoader>();

            RemoteBridge bridge = RemoteBridge.Instance;
            if (bridge == null)
            {
                return;
            }

            loader.Log = bridge.Log;
            bridge.Commands.Register("world.list", List,
                                     "List the worlds this build carries");
            bridge.Commands.Register("world.load", Load,
                                     "Load a world by name (args: name)");
        }

        /// <summary>
        /// Leave the registry as it was found, so a scene reload does not hit
        /// the duplicate-registration error.
        /// 登録簿を見つけたときの状態に戻す。場面を読み直したときに二重登録の
        /// 誤りにならないようにする。
        /// </summary>
        private void OnDestroy()
        {
            RemoteBridge bridge = RemoteBridge.Instance;
            if (bridge == null)
            {
                return;
            }

            bridge.Commands.Unregister("world.list");
            bridge.Commands.Unregister("world.load");
        }

        /// <summary>
        /// <c>world.list</c>: the catalog's names and what is loaded now.
        /// <c>world.list</c>: 一覧の名前と、いま読み込んでいるもの。
        /// </summary>
        private SimCommandResult List(SimCommandArgs args)
        {
            WorldCatalog catalog = loader.Catalog;
            if (catalog == null)
            {
                return SimCommandResult.Failure("no world catalog is assigned to the loader");
            }

            StringBuilder builder = new StringBuilder();
            builder.Append("{\"worlds\":[");
            string[] names = catalog.Names();
            for (int index = 0; index < names.Length; index++)
            {
                if (index > 0)
                {
                    builder.Append(',');
                }

                builder.Append(LogJson.Quote(names[index]));
            }

            builder.Append("],\"current\":")
                   .Append(loader.HasWorld ? LogJson.Quote(loader.Current.name) : "null")
                   .Append('}');
            return SimCommandResult.Success(builder.ToString());
        }

        /// <summary>
        /// <c>world.load</c>: build the named world into the scene. The loader
        /// explains its own refusals through <c>world.rejected</c>, so the
        /// failure here only has to say which name was asked for.
        /// <c>world.load</c>: 名前の空間を場面に生成する。断る理由は読み込み側が
        /// <c>world.rejected</c> で述べるので、ここの失敗はどの名前が求められた
        /// かを言えば足りる。
        /// </summary>
        private SimCommandResult Load(SimCommandArgs args)
        {
            string name = args.String("name", string.Empty);
            if (string.IsNullOrEmpty(name))
            {
                return SimCommandResult.Failure("world.load needs a name (--arg name=<world>)");
            }

            if (!loader.LoadByName(name))
            {
                return SimCommandResult.Failure($"could not load the world '{name}'");
            }

            return SimCommandResult.Success(
                "{\"loaded\":" + LogJson.Quote(name) + ",\"obstacles\":" +
                loader.Current.obstacles.Length + "}");
        }
    }
}
