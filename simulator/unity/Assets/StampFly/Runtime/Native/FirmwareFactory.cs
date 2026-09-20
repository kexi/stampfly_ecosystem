/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — choosing a firmware).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

namespace StampFly.Native
{
    /// <summary>
    /// Picks the firmware implementation this build can actually use, so no
    /// caller repeats the platform test. A WebGL player reaches the wasm module
    /// through a .jslib; the editor opens the development dylib.
    ///
    /// このビルドで実際に使えるファームの実装を選ぶ。どの呼び出し側も同じ環境判定を
    /// 繰り返さずに済むようにするためである。WebGL のプレイヤーは .jslib 経由で
    /// wasm モジュールへ届き、エディタは開発用の dylib を開く。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（エディタでの開発）
    /// </summary>
    public static class FirmwareFactory
    {
        /// <summary>
        /// A firmware that has not been asked to load yet. The caller calls
        /// <see cref="IFirmware.BeginLoad"/>, waits for
        /// <see cref="FirmwareStatus.Loaded"/>, and then boots it.
        /// まだ読み込みを頼んでいないファームを作る。呼び出し側は
        /// <see cref="IFirmware.BeginLoad"/> を呼び、
        /// <see cref="FirmwareStatus.Loaded"/> を待ってから起動する。
        /// </summary>
        public static IFirmware Create()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return new WebGlFirmware();
#else
            return new EditorFirmware();
#endif
        }
    }
}
