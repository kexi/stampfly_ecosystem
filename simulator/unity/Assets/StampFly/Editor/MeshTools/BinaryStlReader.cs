/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — reading a binary STL).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.IO;

namespace StampFly.Editor.MeshTools
{
    /// <summary>
    /// Reads a binary STL into plain triangles. It is deliberately the whole of
    /// what this tool knows about the format: no colour extensions, no ASCII
    /// form, and no use of the per-facet normal.
    ///
    /// バイナリ STL を素の三角形として読む。この道具が形式について知ることは
    /// 意図してこれで全部である。色の拡張も、ASCII 形式も扱わず、面ごとの法線も
    /// 使わない。
    ///
    /// ## Why the stored normal is ignored / 保存された法線を無視する理由
    ///
    /// A facet carries a normal, but in these files it cannot be trusted:
    /// `m5stamps3.stl` was left out of the fix in commit 09346953 and its 108
    /// facets all store a normal opposite to their own winding. Winding is the
    /// one description every one of the thirteen files agrees on — checked by
    /// signed volume, all thirteen enclose a positive volume — so winding is
    /// what the converter carries across and Unity recalculates the normals
    /// from. Reading the stored normal instead would have drawn the orange
    /// controller inside out.
    ///
    /// 面は法線を持つが、これらのファイルでは信用できない。`m5stamps3.stl` は
    /// コミット 09346953 の修正から漏れており、108 面すべてが自身の巻きと逆の
    /// 法線を保存している。13 ファイルすべてが揃って正しいと言える記述は巻きだけ
    /// である（符号付き体積で確かめたところ、13 ファイルとも正の体積を囲む）。
    /// よって変換ツールが運ぶのは巻きで、法線は Unity が巻きから計算し直す。
    /// 保存された法線を読んでいたら、橙色の制御器が裏返しに描かれていた。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（機体の見た目）
    /// </summary>
    public static class BinaryStlReader
    {
        /// <summary>
        /// The header every binary STL starts with: 80 bytes of free text, then
        /// a 32-bit count of facets.
        /// どのバイナリ STL も持つ冒頭。自由文が 80 バイト、続いて面の数が 32 ビット。
        /// </summary>
        public const int HeaderBytes = 80;

        /// <summary>
        /// One facet's size on disk: a normal and three vertices, each three
        /// 32-bit floats, then a 16-bit attribute word.
        /// 面 1 つの大きさ。法線と頂点 3 つがそれぞれ 32 ビット浮動小数 3 つ、
        /// 続いて 16 ビットの属性語。
        /// </summary>
        public const int FacetBytes = 50;

        /// <summary>Floats in a facet's normal plus its three vertices. / 面の法線と頂点 3 つを合わせた浮動小数の数。</summary>
        private const int FloatsPerFacet = 12;

        /// <summary>Bytes of the attribute word that follows a facet's floats. / 面の浮動小数に続く属性語のバイト数。</summary>
        private const int AttributeBytes = 2;

        /// <summary>Vertices in a triangle. / 三角形の頂点の数。</summary>
        public const int VerticesPerTriangle = 3;

        /// <summary>
        /// Every vertex of every facet, in file order: three per triangle, with
        /// no sharing between triangles. STL has no vertex index, so this is
        /// the file's own structure rather than a choice made here.
        /// 全ての面の全ての頂点を、ファイルの順で。三角形あたり 3 つで、三角形
        /// どうしで共有しない。STL に頂点の索引は無いので、これはここでの選択では
        /// なくファイル自身の構造である。
        /// </summary>
        public static float[] ReadVertices(string filePath)
        {
            byte[] bytes = File.ReadAllBytes(filePath);
            int facets = ReadFacetCount(bytes, filePath);

            var vertices = new float[facets * VerticesPerTriangle * 3];
            int offset = HeaderBytes + sizeof(uint);

            for (int facet = 0; facet < facets; facet++)
            {
                // The normal is the first three floats and is skipped; see the
                // class comment for why it is not read.
                // 最初の 3 つが法線で、読み飛ばす。読まない理由は概要を参照。
                int floatsIn = offset + 3 * sizeof(float);
                int floatsOut = facet * VerticesPerTriangle * 3;

                for (int component = 0; component < VerticesPerTriangle * 3; component++)
                {
                    vertices[floatsOut + component] =
                        BitConverter.ToSingle(bytes, floatsIn + component * sizeof(float));
                }

                offset += FacetBytes;
            }

            return vertices;
        }

        /// <summary>
        /// The facet count, checked against the file's length. A truncated or
        /// ASCII file would otherwise be read as a huge count and fail far from
        /// the real fault.
        /// 面の数。ファイルの長さと照合する。そうしないと、途中で切れたファイルや
        /// ASCII のファイルが途方もない数として読まれ、本当の不具合から遠い所で
        /// 失敗する。
        /// </summary>
        private static int ReadFacetCount(byte[] bytes, string filePath)
        {
            bool isTooShortForHeader = bytes.Length < HeaderBytes + sizeof(uint);
            if (isTooShortForHeader)
            {
                throw new InvalidDataException(
                    $"{filePath} is {bytes.Length} bytes, too short to be a binary STL.");
            }

            uint facets = BitConverter.ToUInt32(bytes, HeaderBytes);
            long wanted = (long)HeaderBytes + sizeof(uint) + (long)facets * FacetBytes;
            bool lengthDisagrees = bytes.Length != wanted;
            if (lengthDisagrees)
            {
                throw new InvalidDataException(
                    $"{filePath} says it holds {facets} facets, which needs {wanted} bytes, " +
                    $"but the file is {bytes.Length} bytes. It is truncated, or it is an " +
                    "ASCII STL, which this reader does not read.");
            }

            return (int)facets;
        }

        /// <summary>
        /// The attribute word's size, for a caller checking the layout above.
        /// 属性語の大きさ。上の配置を確かめる呼び出し側のために公開する。
        /// </summary>
        public static int AttributeWordBytes => AttributeBytes;

        /// <summary>Floats a facet holds on disk. / 面がファイル上に持つ浮動小数の数。</summary>
        public static int FloatsInFacet => FloatsPerFacet;
    }
}
