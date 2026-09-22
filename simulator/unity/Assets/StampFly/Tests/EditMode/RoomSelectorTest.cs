/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kei Nakayama (kexi)
 */
using NUnit.Framework;
using StampFly.Ui;
using StampFly.World;
using UnityEngine;

namespace StampFly.Tests.EditMode
{
    /// <summary>Room selection prioritizes furnished presets and restarts only on an explicit choice. / 家具付き部屋を優先し、明示的な選択時だけ再起動することを保証する。</summary>
    public sealed class RoomSelectorTest
    {
        private GameObject host;
        private WorldCatalog catalog;
        private TextAsset[] assets;
        private WorldLoader loader;
        private RoomSelector selector;
        private int canceled;
        private int restarted;

        /// <summary>Use a real loader with a small in-memory catalog. / 小さなメモリ上の一覧と実際のローダーを使う。</summary>
        [SetUp]
        public void SetUp()
        {
            host = new GameObject("Room selector test");
            loader = host.AddComponent<WorldLoader>();
            catalog = ScriptableObject.CreateInstance<WorldCatalog>();
            string[] names = { "empty_room", "bedroom", "study", "living_room" };
            assets = new TextAsset[names.Length];
            var entries = new WorldCatalog.Entry[names.Length];
            for (int index = 0; index < names.Length; index++)
            {
                assets[index] = new TextAsset(ShippedWorlds.Json("empty_room").Replace("\"empty_room\"", "\"" + names[index] + "\""));
                entries[index] = new WorldCatalog.Entry { name = names[index], json = assets[index] };
            }
            catalog.SetEntries(entries);
            loader.Catalog = catalog;
            loader.LoadByName("living_room");
            canceled = restarted = 0;
            selector = new RoomSelector(loader, () => canceled++, () => restarted++);
        }

        /// <summary>Release subscriptions before destroying their source. / 購読元の破棄前に購読を解除する。</summary>
        [TearDown]
        public void TearDown()
        {
            selector.Dispose();
            Object.DestroyImmediate(host);
            Object.DestroyImmediate(catalog);
            foreach (TextAsset asset in assets) Object.DestroyImmediate(asset);
        }

        /// <summary>Furnished rooms come first, while the empty room remains available. / 家具付き部屋を先頭にし、空の部屋も選べる。</summary>
        [Test]
        public void FurnishedRoomsComeFirstWithoutDroppingOtherWorlds()
        {
            Assert.That(selector.choices, Is.EqualTo(new[] {
                "Room: Living room", "Room: Study", "Room: Bedroom", "Room: Empty room" }));
            Assert.That(selector.value, Is.EqualTo("Room: Living room"));
            Assert.That(restarted, Is.Zero);
        }

        /// <summary>A room change cancels touch and restarts once; a loader notification does not recurse. / 部屋変更でタッチを解除して一度だけ再起動し、読み込み通知で再帰しない。</summary>
        [Test]
        public void SelectingARoomCancelsInputLoadsAndRestartsExactlyOnce()
        {
            Assert.That(selector.SelectWorld("study"), Is.True);
            Assert.That(loader.Current.name, Is.EqualTo("study"));
            Assert.That(selector.value, Is.EqualTo("Room: Study"));
            Assert.That(canceled, Is.EqualTo(1));
            Assert.That(restarted, Is.EqualTo(1));
            loader.LoadByName("bedroom");
            Assert.That(selector.value, Is.EqualTo("Room: Bedroom"));
            Assert.That(restarted, Is.EqualTo(1));
        }
    }
}
