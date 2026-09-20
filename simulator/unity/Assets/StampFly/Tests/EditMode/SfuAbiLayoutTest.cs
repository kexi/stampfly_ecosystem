/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — ABI layout tests).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Runtime.InteropServices;
using NUnit.Framework;
using StampFly.Native;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// Guarantees that the C# declarations of the ABI structs are byte-for-byte
    /// what <c>simulator/unity/native/bridge/sfu_api.h</c> fixes.
    ///
    /// ABI の構造体の C# の宣言が、
    /// <c>simulator/unity/native/bridge/sfu_api.h</c> の定めるものとバイト単位で
    /// 一致することを保証する。
    ///
    /// A pointer to a struct is handed across the boundary raw, so a field added
    /// or reordered on either side sends the firmware a wrong number rather than
    /// failing. These checks turn that into a named test failure, without
    /// needing a firmware module — which is why they are EditMode tests that run
    /// in seconds rather than a flight that has to be read.
    ///
    /// 構造体へのポインタは生のまま境界を渡るので、どちらかの側で欄が増えたり
    /// 並べ替えられたりすると、失敗せずにファームへ誤った数値が届く。この検査は
    /// それを、名前の付いた試験の不合格に変える。ファームのモジュールを要さない
    /// ので、飛ばして読み取るのではなく数秒で終わる EditMode の試験にしてある。
    /// </summary>
    public sealed class SfuAbiLayoutTest
    {
        [Test]
        public void ConfigIsFortyEightBytes()
        {
            Assert.That(Marshal.SizeOf<SfuConfig>(), Is.EqualTo(48));
        }

        [Test]
        public void StepInIsNinetySixBytes()
        {
            Assert.That(Marshal.SizeOf<SfuStepIn>(), Is.EqualTo(96));
        }

        [Test]
        public void StepOutIsOneHundredAndSixtyEightBytes()
        {
            Assert.That(Marshal.SizeOf<SfuStepOut>(), Is.EqualTo(168));
        }

        [Test]
        public void ParamInfoIsEightyFourBytes()
        {
            Assert.That(Marshal.SizeOf<SfuParamInfo>(), Is.EqualTo(84));
        }

        [Test]
        public void LogRecordIsTwoHundredAndSeventyTwoBytes()
        {
            Assert.That(Marshal.SizeOf<SfuLogRecord>(), Is.EqualTo(272));
        }

        /// <summary>
        /// The step length sits at byte 92, the last field of SfuStepIn.
        /// 刻みの長さは 92 バイト目、SfuStepIn の最後の欄に在る。
        /// </summary>
        [Test]
        public void StepMicrosecondsSitsAtByteNinetyTwo()
        {
            Assert.That(
                SfuStructSizes.OffsetOf<SfuStepIn>(nameof(SfuStepIn.StepMicroseconds)),
                Is.EqualTo(92));
        }

        /// <summary>
        /// The status sits at byte 156. A wasm caller reads the result from
        /// nowhere else, so this offset being wrong would make every tick look
        /// like it failed — or worse, like it succeeded.
        /// status は 156 バイト目に在る。wasm の呼び出し側が結果を読む場所は他に
        /// 無いので、この位置が違えばどの刻みも失敗に見える ― あるいはもっと悪い
        /// ことに、成功に見える。
        /// </summary>
        [Test]
        public void StatusSitsAtByteOneHundredAndFiftySix()
        {
            Assert.That(
                SfuStructSizes.OffsetOf<SfuStepOut>(nameof(SfuStepOut.Status)),
                Is.EqualTo(156));
        }

        /// <summary>
        /// The virtual clock is an int64 and must be 8-byte aligned; byte 80
        /// already is, so the ABI inserts no padding before it.
        /// 仮想時計は int64 で 8 バイト境界に載る必要がある。80 バイト目は既に
        /// そうなので、ABI はその前に詰め物を入れない。
        /// </summary>
        [Test]
        public void NowMicrosecondsSitsAtByteEighty()
        {
            Assert.That(
                SfuStructSizes.OffsetOf<SfuStepOut>(nameof(SfuStepOut.NowMicroseconds)),
                Is.EqualTo(80));
        }

        /// <summary>
        /// The log record's TOTAL is what has to agree, not the position of the
        /// arrays inside it: Mono and IL2CPP-under-WebGL report different
        /// offsets for a <c>ByValArray</c> field while copying the same 272
        /// bytes, and nothing indexes into this struct by hand. Measured
        /// 2026-09-20: Mono says the body is at 48, the WebGL player says 16.
        ///
        /// 一致していなければならないのはログの記録の**合計**であって、その中の
        /// 配列の位置ではない。Mono と WebGL の IL2CPP は <c>ByValArray</c> の欄の
        /// 位置を違う値で報告するが、写すのは同じ 272 バイトであり、この構造体を
        /// 手で添字で読む箇所は無い。2026-09-20 の実測では、Mono が本文を 48、
        /// WebGL のプレイヤーが 16 と答えた。
        /// </summary>
        [Test]
        public void LogRecordTotalIsWhatAgrees()
        {
            Assert.That(Marshal.SizeOf<SfuLogRecord>(),
                        Is.EqualTo(SfuStructSizes.LogRecord));
            Assert.That(SfuStructSizes.CheckDeclarations(), Is.Null);
        }

        /// <summary>
        /// The one check that covers every struct and every recorded offset at
        /// once, and is also what the loop runs before its first call.
        /// 全ての構造体と記録した位置を一度に覆う検査であり、ループが最初の呼び出しの
        /// 前に走らせるものでもある。
        /// </summary>
        [Test]
        public void DeclarationsAgreeWithTheAbi()
        {
            Assert.That(SfuStructSizes.CheckDeclarations(), Is.Null);
        }

        /// <summary>
        /// A module reporting a different size is refused with a message naming
        /// the struct, rather than being trusted.
        /// 違う大きさを報告するモジュールは、信じるのではなく、構造体の名前を
        /// 含む文とともに拒まれる。
        /// </summary>
        [Test]
        public void ADisagreeingModuleIsRefused()
        {
            string problem = SfuStructSizes.CheckAgainstModule(
                which => which == SfuAbi.StructStepIn ? 100 : SizeFor(which));

            Assert.That(problem, Is.Not.Null);
            Assert.That(problem, Does.Contain("SfuStepIn"));
        }

        /// <summary>
        /// A module that agrees on every struct is accepted.
        /// 全ての構造体で一致するモジュールは受け入れられる。
        /// </summary>
        [Test]
        public void AnAgreeingModuleIsAccepted()
        {
            Assert.That(SfuStructSizes.CheckAgainstModule(SizeFor), Is.Null);
        }

        /// <summary>The ABI's size for one struct id. / 構造体の id 1 つに対する ABI の大きさ。</summary>
        private static int SizeFor(int which)
        {
            switch (which)
            {
                case SfuAbi.StructConfig: return SfuStructSizes.Config;
                case SfuAbi.StructStepIn: return SfuStructSizes.StepIn;
                case SfuAbi.StructStepOut: return SfuStructSizes.StepOut;
                case SfuAbi.StructParamInfo: return SfuStructSizes.ParamInfo;
                case SfuAbi.StructLogRecord: return SfuStructSizes.LogRecord;
                default: return -1;
            }
        }

        /// <summary>
        /// The ABI revision this build follows is the one the native core was
        /// written against.
        /// このビルドが従う ABI の版は、ネイティブコアが書かれた版である。
        /// </summary>
        [Test]
        public void AbiRevisionIsTwo()
        {
            Assert.That(SfuAbi.Version, Is.EqualTo(2));
        }
    }
}
