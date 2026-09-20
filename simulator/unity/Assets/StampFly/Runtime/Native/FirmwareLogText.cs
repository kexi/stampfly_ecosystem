/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — firmware log text).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Text;
using StampFly.Core;

namespace StampFly.Native
{
    /// <summary>
    /// Turns an ABI log record's fixed byte arrays into C# strings, once, so
    /// nothing downstream handles raw bytes. The arrays are null-terminated and
    /// padded, so the text ends at the first zero rather than at the array's end.
    ///
    /// ABI のログ記録の固定長のバイト列を、ここで 1 回だけ C# の文字列に直す。
    /// 以後のコードが生のバイト列に触れずに済むようにするためである。バイト列は
    /// null 終端で詰め物が付くので、文字列は配列の末尾ではなく最初の 0 で終わる。
    /// </summary>
    public static class FirmwareLogText
    {
        /// <summary>
        /// Builds the readable record from the raw one.
        /// 生の記録から、読める形の記録を作る。
        /// </summary>
        public static FirmwareLogRecord From(in SfuLogRecord raw)
        {
            return new FirmwareLogRecord
            {
                Level = raw.Level,
                SimulationMicroseconds = raw.SimMicroseconds,
                Tag = ToText(raw.Tag),
                Message = ToText(raw.Message),
            };
        }

        /// <summary>
        /// Reads a null-terminated UTF-8 string out of a fixed byte array.
        /// A missing terminator is treated as "the whole array", so a corrupt
        /// record produces odd text rather than reading past the end.
        /// 固定長のバイト列から null 終端の UTF-8 文字列を読む。終端が無い場合は
        /// 配列全体として扱う。壊れた記録は、末尾を越えて読む代わりに妙な文字列に
        /// なる。
        /// </summary>
        public static string ToText(byte[] bytes)
        {
            bool isEmpty = bytes == null || bytes.Length == 0;
            if (isEmpty)
            {
                return string.Empty;
            }

            int length = 0;
            while (length < bytes.Length && bytes[length] != 0)
            {
                length++;
            }

            return Encoding.UTF8.GetString(bytes, 0, length);
        }
    }
}
