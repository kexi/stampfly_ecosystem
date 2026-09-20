/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — struct layout verification).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace StampFly.Native
{
    /// <summary>
    /// What this build compiled the ABI structs to, and the check that the
    /// firmware module agrees. C# declares the structs itself and hands the
    /// bridge a raw pointer, so the two declarations must match byte for byte;
    /// a mismatch found here is reported with the field that moved, instead of
    /// becoming a wrong number in a flight.
    ///
    /// このビルドが ABI の構造体をどの大きさにコンパイルしたか、および
    /// ファームのモジュールがそれに同意するかの検査。C# は構造体を自分で宣言して
    /// 生のポインタを渡すので、2 つの宣言はバイト単位で一致していなければならない。
    /// ここで食い違いを見つければ、飛行中の誤った数値になる代わりに、動いた欄の
    /// 名前とともに報告できる。
    ///
    /// The expected numbers are the ABI's, from
    /// <c>simulator/unity/native/README.md</c> §3: 48 / 96 / 168 / 84 / 272.
    /// 期待する数値は ABI のもので、<c>simulator/unity/native/README.md</c> §3 に
    /// あるとおり 48 / 96 / 168 / 84 / 272 である。
    ///
    /// @design simulator/unity/native/bridge/sfu_api.h
    /// </summary>
    public static class SfuStructSizes
    {
        /// <summary>The size the ABI fixes for <c>SfuConfig</c>. / ABI が定める <c>SfuConfig</c> の大きさ。</summary>
        public const int Config = 48;

        /// <summary>The size the ABI fixes for <c>SfuStepIn</c>. / ABI が定める <c>SfuStepIn</c> の大きさ。</summary>
        public const int StepIn = 96;

        /// <summary>The size the ABI fixes for <c>SfuStepOut</c>. / ABI が定める <c>SfuStepOut</c> の大きさ。</summary>
        public const int StepOut = 168;

        /// <summary>The size the ABI fixes for <c>SfuParamInfo</c>. / ABI が定める <c>SfuParamInfo</c> の大きさ。</summary>
        public const int ParamInfo = 84;

        /// <summary>The size the ABI fixes for <c>SfuLogRecord</c>. / ABI が定める <c>SfuLogRecord</c> の大きさ。</summary>
        public const int LogRecord = 272;

        /// <summary>
        /// The byte offset of <c>SfuStepIn.StepMicroseconds</c>. Checking one
        /// offset as well as the total catches two fields that swapped places,
        /// which a total alone cannot see.
        /// <c>SfuStepIn.StepMicroseconds</c> の位置 [byte]。合計だけでなく位置も
        /// 見ると、入れ替わった 2 つの欄を捕まえられる。合計だけでは見えない。
        /// </summary>
        public const int StepInStepMicrosecondsOffset = 92;

        /// <summary>The byte offset of <c>SfuStepOut.Status</c>. / <c>SfuStepOut.Status</c> の位置 [byte]。</summary>
        public const int StepOutStatusOffset = 156;

        /// <summary>The byte offset of <c>SfuStepOut.NowMicroseconds</c>. / <c>SfuStepOut.NowMicroseconds</c> の位置 [byte]。</summary>
        public const int StepOutNowMicrosecondsOffset = 80;

        /// <summary>
        /// The byte offset of <c>SfuLogRecord.Message</c> in the ABI. It is NOT
        /// checked against this build, because the two runtimes disagree about
        /// where <see cref="Marshal.OffsetOf"/> puts a <c>ByValArray</c> field:
        /// Mono answers 48 and IL2CPP under WebGL answers 16, while both copy
        /// the same 272 bytes in and out. The total is what has to agree, and
        /// it does. Nothing indexes into this struct by hand — it crosses the
        /// boundary through <see cref="Marshal.PtrToStructure{T}(System.IntPtr)"/>
        /// only — so the offset is recorded here for a reader and left out of
        /// the check.
        ///
        /// ABI における <c>SfuLogRecord.Message</c> の位置 [byte]。このビルドとは
        /// **照合しない**。<c>ByValArray</c> の欄を
        /// <see cref="Marshal.OffsetOf"/> がどこに置くかで 2 つの実行環境の答えが
        /// 食い違うためである（Mono は 48、WebGL の IL2CPP は 16 と答えるが、
        /// どちらも同じ 272 バイトを出し入れする）。一致していなければならないのは
        /// 合計の方で、そちらは一致している。この構造体を手で添字で読む箇所は無く、
        /// 境界を渡るのは
        /// <see cref="Marshal.PtrToStructure{T}(System.IntPtr)"/> 経由だけなので、
        /// 位置は読む人のために書き残し、検査からは外す。
        /// </summary>
        public const int LogRecordMessageOffset = 48;

        /// <summary>
        /// One struct's name, its id for <c>sfu_struct_size</c>, the size this
        /// build made it, and the size the ABI fixes.
        /// 構造体 1 つの名前、<c>sfu_struct_size</c> に渡す id、このビルドでの
        /// 大きさ、ABI が定める大きさ。
        /// </summary>
        public readonly struct Entry
        {
            public Entry(string name, int which, int declared, int expected)
            {
                Name = name;
                Which = which;
                Declared = declared;
                Expected = expected;
            }

            public string Name { get; }
            public int Which { get; }
            public int Declared { get; }
            public int Expected { get; }
        }

        /// <summary>
        /// Every struct that crosses the boundary, with what this build made of
        /// it. <see cref="Marshal.SizeOf"/> reports the marshalled layout, which
        /// is what the pointer handed across actually points at.
        /// 境界を渡る構造体すべてと、このビルドでの大きさ。
        /// <see cref="Marshal.SizeOf"/> が返すのは marshal 後の配置で、境界へ渡す
        /// ポインタが実際に指すのはそちらである。
        /// </summary>
        public static IReadOnlyList<Entry> All()
        {
            return new[]
            {
                new Entry("SfuConfig", SfuAbi.StructConfig,
                          Marshal.SizeOf<SfuConfig>(), Config),
                new Entry("SfuStepIn", SfuAbi.StructStepIn,
                          Marshal.SizeOf<SfuStepIn>(), StepIn),
                new Entry("SfuStepOut", SfuAbi.StructStepOut,
                          Marshal.SizeOf<SfuStepOut>(), StepOut),
                new Entry("SfuParamInfo", SfuAbi.StructParamInfo,
                          Marshal.SizeOf<SfuParamInfo>(), ParamInfo),
                new Entry("SfuLogRecord", SfuAbi.StructLogRecord,
                          Marshal.SizeOf<SfuLogRecord>(), LogRecord),
            };
        }

        /// <summary>
        /// Checks this build's own declarations against the ABI, without needing
        /// a module. Returns null when everything agrees, or the reason it does
        /// not, naming the struct and both numbers.
        /// モジュールを要さず、このビルド自身の宣言を ABI と突き合わせる。全て
        /// 一致すれば null を、しなければ理由を返す。理由には構造体の名前と両方の
        /// 数値を入れる。
        /// </summary>
        public static string CheckDeclarations()
        {
            foreach (Entry entry in All())
            {
                bool sizeDisagrees = entry.Declared != entry.Expected;
                if (sizeDisagrees)
                {
                    return $"{entry.Name}: this build declares {entry.Declared} bytes, " +
                           $"the ABI fixes {entry.Expected}";
                }
            }

            return CheckOffsets();
        }

        /// <summary>
        /// Checks the field positions a total cannot see. Every one of these is
        /// a field somebody reads or writes BY HAND — the .jslib indexes into
        /// the two step structs with these numbers — so a swap that leaves the
        /// total alone would still send the firmware a wrong value.
        /// <c>SfuLogRecord</c> is left out on purpose; see the note on
        /// <see cref="LogRecordMessageOffset"/>.
        ///
        /// 合計では見えない欄の位置を検査する。どれも誰かが**手作業で**読み書き
        /// する欄である（.jslib はこの数値で 2 つの刻みの構造体を添字で読む）。
        /// 合計を変えない入れ替えでも、ファームへ誤った値が届いてしまうためである。
        /// <c>SfuLogRecord</c> を意図して外してあることは
        /// <see cref="LogRecordMessageOffset"/> の注記を参照。
        /// </summary>
        private static string CheckOffsets()
        {
            var checks = new (string Name, int Actual, int Expected)[]
            {
                ("SfuStepIn.StepMicroseconds",
                 OffsetOf<SfuStepIn>(nameof(SfuStepIn.StepMicroseconds)),
                 StepInStepMicrosecondsOffset),
                ("SfuStepOut.Status",
                 OffsetOf<SfuStepOut>(nameof(SfuStepOut.Status)),
                 StepOutStatusOffset),
                ("SfuStepOut.NowMicroseconds",
                 OffsetOf<SfuStepOut>(nameof(SfuStepOut.NowMicroseconds)),
                 StepOutNowMicrosecondsOffset),
            };

            foreach ((string name, int actual, int expected) in checks)
            {
                bool offsetDisagrees = actual != expected;
                if (offsetDisagrees)
                {
                    return $"{name}: this build puts it at byte {actual}, " +
                           $"the ABI puts it at {expected}";
                }
            }

            return null;
        }

        /// <summary>The marshalled byte offset of one field. / 欄 1 つの marshal 後の位置 [byte]。</summary>
        public static int OffsetOf<T>(string fieldName)
        {
            return Marshal.OffsetOf(typeof(T), fieldName).ToInt32();
        }

        /// <summary>
        /// Checks this build against a live module. <paramref name="moduleSize"/>
        /// is the module's <c>sfu_struct_size</c>. Returns null when everything
        /// agrees, or the reason it does not.
        /// 動いているモジュールと突き合わせる。<paramref name="moduleSize"/> は
        /// モジュールの <c>sfu_struct_size</c> である。全て一致すれば null を、
        /// しなければ理由を返す。
        /// </summary>
        public static string CheckAgainstModule(Func<int, int> moduleSize)
        {
            string declarationProblem = CheckDeclarations();
            if (declarationProblem != null)
            {
                return declarationProblem;
            }

            foreach (Entry entry in All())
            {
                int fromModule = moduleSize(entry.Which);
                bool moduleDisagrees = fromModule != entry.Declared;
                if (moduleDisagrees)
                {
                    return $"{entry.Name}: the firmware module compiled it to " +
                           $"{fromModule} bytes, this build declares {entry.Declared}. " +
                           "Rebuild one side — `just unity-native-build` rebuilds the module.";
                }
            }

            return null;
        }
    }
}
