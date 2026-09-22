/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kei Nakayama (kexi)
 */
using System;
using System.Collections.Generic;
using StampFly.World;
using UnityEngine.UIElements;

namespace StampFly.Ui
{
    /// <summary>
    /// Select a furnished room from the build's catalog without remote commands.
    /// 遠隔命令に依存せず、ビルドの一覧から家具付きの部屋を選ぶ。
    /// @design docs/plans/unity-simulator.md §4 [OK]
    /// </summary>
    public sealed class RoomSelector : DropdownField, IDisposable
    {
        private readonly WorldLoader loader;
        private readonly Action cancelInput;
        private readonly Action restart;
        private readonly List<string> worldNames = new List<string>();
        private bool compact;

        /// <summary>Put furnished presets first and retain other catalog worlds. / 家具付きの部屋を先に並べ、他の同梱空間も残す。</summary>
        public RoomSelector(WorldLoader loader, Action cancelInput, Action restart)
        {
            this.loader = loader;
            this.cancelInput = cancelInput;
            this.restart = restart;
            name = "room-selector";
            tooltip = "Change room and restart. Unsaved layout changes are replaced.";
            style.flexGrow = 1;
            style.minWidth = 120;
            style.minHeight = 48;
            style.marginTop = style.marginBottom = 3;
            VisualElement input = this.Q<VisualElement>(className: "unity-base-field__input");
            bool hasInput = input != null;
            if (hasInput) input.style.minHeight = 48;
            string[] priority = { "bowling", "living_room", "study", "bedroom" };
            string[] available = loader.Catalog?.Names() ?? Array.Empty<string>();
            foreach (string wanted in priority)
            {
                bool exists = Array.IndexOf(available, wanted) >= 0;
                if (exists) worldNames.Add(wanted);
            }
            foreach (string availableName in available)
            {
                bool listed = worldNames.Contains(availableName);
                if (!listed) worldNames.Add(availableName);
            }
            choices = worldNames.ConvertAll(LabelFor);
            SetEnabled(worldNames.Count > 0);
            RefreshCurrent();
            this.RegisterValueChangedCallback(OnChoice);
            loader.Changed += RefreshCurrent;
        }

        /// <summary>Human-readable names preserve a one-to-one catalog mapping. / 読みやすい名前と一覧の識別子を一対一で対応させる。</summary>
        private string LabelFor(string worldName)
        {
            string readable = worldName.Replace('_', ' ');
            return (compact ? string.Empty : "Room: ") + char.ToUpperInvariant(readable[0]) + readable.Substring(1);
        }

        /// <summary>Keep only the room name visible on touch screens while retaining the full touch target. / タッチ画面は部屋名だけを表示し、押す領域は維持する。</summary>
        public void SetCompact(bool enabled)
        {
            compact = enabled;
            style.flexGrow = enabled ? 0 : 1;
            style.width = enabled ? 128 : StyleKeyword.Auto;
            style.minWidth = enabled ? 96 : 120;
            style.maxWidth = enabled ? 128 : StyleKeyword.None;
            choices = worldNames.ConvertAll(LabelFor);
            TextElement text = this.Q<TextElement>(className: "unity-base-popup-field__text");
            bool hasText = text != null;
            if (hasText)
            {
                text.style.overflow = Overflow.Hidden;
                text.style.textOverflow = TextOverflow.Ellipsis;
                text.style.whiteSpace = WhiteSpace.NoWrap;
                text.style.fontSize = enabled ? 13 : StyleKeyword.Null;
            }
            RefreshCurrent();
        }

        /// <summary>Load first; only a successful load resets the flight. / 読み込みが成功した場合だけ飛行を初期化する。</summary>
        public bool SelectWorld(string worldName)
        {
            cancelInput();
            bool loaded = loader.LoadByName(worldName);
            if (!loaded) { RefreshCurrent(); return false; }
            restart();
            RefreshCurrent();
            return true;
        }

        /// <summary>Ignore unknown display values instead of guessing an identifier. / 不明な表示文字列から識別子を推測しない。</summary>
        private void OnChoice(ChangeEvent<string> change)
        {
            int choice = choices.IndexOf(change.newValue);
            bool known = choice >= 0 && choice < worldNames.Count;
            if (known) SelectWorld(worldNames[choice]);
        }

        /// <summary>Follow world loads and local saved layouts without firing another selection. / 空間の読み込みに追従し、選択イベントを再発火させない。</summary>
        private void RefreshCurrent()
        {
            string current = loader.Current?.name;
            bool known = current != null && worldNames.Contains(current);
            SetValueWithoutNotify(known ? LabelFor(current) : (compact ? "Custom layout" : "Room: Custom layout"));
        }

        /// <summary>Release the loader subscription with the HUD. / HUDとともにローダーの購読を解除する。</summary>
        public void Dispose() => loader.Changed -= RefreshCurrent;
    }
}
