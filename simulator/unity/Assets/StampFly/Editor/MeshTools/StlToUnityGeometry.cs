/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — STL geometry into Unity's frame).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.Collections.Generic;
using UnityEngine;

namespace StampFly.Editor.MeshTools
{
    /// <summary>
    /// Turns the triangles <see cref="BinaryStlReader"/> hands back into the
    /// vertices and indices a Unity mesh holds. It knows two things and nothing
    /// else: which frame the STL files are written in, and how a triangle's
    /// winding survives the move into Unity's.
    ///
    /// <see cref="BinaryStlReader"/> が返す三角形を、Unity のメッシュが持つ頂点と
    /// 索引に変える。知っていることは 2 つだけで、他は何も知らない。STL の
    /// ファイルがどの座標系で書かれているか、そして三角形の巻きが Unity の
    /// 座標系へ移したあとどうなるか、である。
    ///
    /// ## The two frames / 2 つの座標系
    ///
    /// The STL files are millimetres in a right-handed frame with Y up, Z
    /// forward and **X to the left** — `motor_fl`, the front-left motor, has its
    /// centroid at x = +22.804. Unity is left-handed with X to the **right**, so
    /// the move is a mirror about the YZ plane: negate x, and scale from
    /// millimetres to metres.
    ///
    /// STL のファイルはミリメートルの右手系で、Y が上・Z が前・**X が左**である。
    /// 前左のモータ `motor_fl` の重心が x = +22.804 であることがそれを示す。Unity
    /// は左手系で X が**右**なので、移動は YZ 平面での鏡映になる。x の符号を
    /// 反転し、ミリメートルからメートルへ尺度を変える。
    ///
    /// A mirror reverses the handedness of every triangle, so the winding must be
    /// reversed to compensate: without that swap, all thirteen parts would face
    /// inward and the vehicle would be drawn as its own inside surface. The two
    /// together leave the signed volume positive, which is what a test checks.
    ///
    /// 鏡映は全ての三角形の向きを裏返すので、それを打ち消すよう巻きも反転する。
    /// この入れ替えが無ければ 13 部品すべてが内向きになり、機体は自身の内面として
    /// 描かれる。2 つを合わせると符号付き体積は正のままで、試験が確かめるのはそれ
    /// である。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（機体の見た目）
    /// </summary>
    public static class StlToUnityGeometry
    {
        /// <summary>
        /// The STL files' unit. Every coordinate is divided by this to reach the
        /// metres Unity and the physics both work in.
        /// STL のファイルの単位。Unity と物理がともに使うメートルにするため、
        /// 全ての座標をこれで割る。
        /// </summary>
        public const float MillimetresPerMetre = 1000.0f;

        /// <summary>
        /// The mirror applied to x, which is what turns the STL's left-handed-
        /// looking X-to-the-left into Unity's X-to-the-right.
        /// x に掛ける鏡映。STL の X が左を向く並びを、Unity の X が右を向く並びに
        /// するのはこれである。
        /// </summary>
        public const float MirrorX = -1.0f;

        /// <summary>
        /// How close two vertices must be to count as the same one [m]. It is a
        /// tenth of the finest feature these parts carry (the propeller blade's
        /// trailing edge is some tenths of a millimetre), so the weld joins what
        /// the modeller meant to be one corner and never two that differ.
        /// 2 つの頂点を同じものと見なす近さ [m]。これらの部品が持つ最も細かい形
        /// （プロペラの後縁は 0.1 mm 台）の 1 割であり、設計者が 1 つの角と考えた
        /// ものだけを溶接し、異なる 2 つを繋ぐことはない。
        /// </summary>
        public const float WeldToleranceMetres = 1.0e-5f;

        /// <summary>
        /// Above this angle between two faces, the edge between them stays hard:
        /// the shared vertex is split again so each face keeps its own normal.
        /// 30 degrees keeps a motor can's rim and the frame's chamfers crisp
        /// while still smoothing the many small facets a turned cylinder is made
        /// of.
        /// 2 つの面がこの角度より開いていれば、その間の稜線は硬いままにする。共有
        /// した頂点を分け直し、各面が自身の法線を保つ。30 度はモータ缶の縁や機体の
        /// 面取りをはっきり残しつつ、旋削された円筒を成す細かい面の多くを滑らかに
        /// する。
        /// </summary>
        public const float HardEdgeAngleDegrees = 30.0f;

        /// <summary>
        /// The mesh of one part, as Unity holds it: positions in metres in the
        /// vehicle's own frame, one normal per position, and three indices per
        /// triangle.
        /// 部品 1 つのメッシュを Unity が持つ形で。機体自身の座標系でのメートル単位
        /// の位置、位置ごとに 1 つの法線、そして三角形あたり 3 つの索引である。
        /// </summary>
        public readonly struct Geometry
        {
            public readonly Vector3[] Vertices;
            public readonly Vector3[] Normals;
            public readonly int[] Triangles;

            public Geometry(Vector3[] vertices, Vector3[] normals, int[] triangles)
            {
                Vertices = vertices;
                Normals = normals;
                Triangles = triangles;
            }
        }

        /// <summary>
        /// The whole conversion: read the file's vertices, move them into
        /// Unity's frame, reverse each triangle's winding, weld the vertices
        /// that sit on top of each other, and give each one a normal.
        /// 変換の全体。ファイルの頂点を読み、Unity の座標系へ移し、各三角形の巻きを
        /// 反転し、重なっている頂点を溶接し、それぞれに法線を与える。
        /// </summary>
        public static Geometry FromStlVertices(float[] stlVertices)
        {
            bool isNotWholeTriangles =
                stlVertices.Length % (BinaryStlReader.VerticesPerTriangle * 3) != 0;
            if (isNotWholeTriangles)
            {
                throw new ArgumentException(
                    $"{stlVertices.Length} floats is not a whole number of triangles.",
                    nameof(stlVertices));
            }

            Vector3[] loose = ToUnityFrame(stlVertices);
            ReverseWinding(loose);
            Geometry shared = Weld(loose);
            return SmoothNormals.Apply(shared, HardEdgeAngleDegrees);
        }

        /// <summary>
        /// Every float triple as a Unity position: x mirrored, all three scaled
        /// to metres. The order is untouched, so triangle <c>t</c> is still at
        /// <c>3t</c>.
        /// 浮動小数 3 つ組を Unity の位置に。x を鏡映し、3 つとも尺度をメートルに
        /// する。並びには触れないので、三角形 <c>t</c> は <c>3t</c> のままである。
        /// </summary>
        private static Vector3[] ToUnityFrame(float[] stlVertices)
        {
            var points = new Vector3[stlVertices.Length / 3];

            for (int point = 0; point < points.Length; point++)
            {
                int component = point * 3;
                points[point] = new Vector3(
                    MirrorX * stlVertices[component] / MillimetresPerMetre,
                    stlVertices[component + 1] / MillimetresPerMetre,
                    stlVertices[component + 2] / MillimetresPerMetre);
            }

            return points;
        }

        /// <summary>
        /// Swap each triangle's second and third vertex. The mirror above
        /// reversed the handedness of every face; this puts it back, so a face
        /// that pointed out of the solid still does.
        /// 各三角形の 2 番目と 3 番目の頂点を入れ替える。上の鏡映が全ての面の向きを
        /// 裏返したので、これで戻す。立体の外を向いていた面は外を向いたままになる。
        /// </summary>
        private static void ReverseWinding(Vector3[] points)
        {
            for (int corner = 0; corner < points.Length; corner += BinaryStlReader.VerticesPerTriangle)
            {
                (points[corner + 1], points[corner + 2]) = (points[corner + 2], points[corner + 1]);
            }
        }

        /// <summary>
        /// Join the vertices that share a position, keeping the triangles that
        /// referred to them. An STL repeats every corner once per touching face
        /// — the frame's 4520 facets hold 13560 corners for far fewer distinct
        /// points — and Unity would otherwise light each copy separately, which
        /// is what makes an unwelded STL look faceted.
        ///
        /// 同じ位置を共有する頂点をまとめ、それを参照していた三角形は保つ。STL は
        /// 角を、接する面の数だけ繰り返し持つ（機体の 4520 面は 13560 の角を持つが、
        /// 相異なる点はそれよりずっと少ない）。そのままでは Unity が複製ごとに別々
        /// に照明を当てることになり、溶接していない STL が面ごとにカクついて見える
        /// のはこのためである。
        ///
        /// The grid cell is the tolerance itself, and a point is matched against
        /// the 27 cells around it, so two points a hair either side of a cell
        /// boundary still meet.
        /// 格子の升目は許容差そのもので、点は周りの 27 升と照合する。升目の境目を
        /// 挟んで僅かに離れた 2 点も、こうすれば出会う。
        /// </summary>
        private static Geometry Weld(Vector3[] loose)
        {
            var cells = new Dictionary<Vector3Int, List<int>>(loose.Length);
            var welded = new List<Vector3>(loose.Length);
            var triangles = new int[loose.Length];

            for (int corner = 0; corner < loose.Length; corner++)
            {
                Vector3 point = loose[corner];
                Vector3Int cell = CellOf(point);
                int found = FindNear(cells, welded, point, cell);

                bool isNew = found < 0;
                if (isNew)
                {
                    found = welded.Count;
                    welded.Add(point);
                    Remember(cells, cell, found);
                }

                triangles[corner] = found;
            }

            // The normals are filled in afterwards, by SmoothNormals; a welded
            // mesh has the shared vertices that pass needs to look at.
            // 法線はこのあと SmoothNormals が入れる。溶接したメッシュは、その処理が
            // 見るべき共有された頂点を持っている。
            return new Geometry(welded.ToArray(), null, triangles);
        }

        /// <summary>
        /// The index of an already-kept vertex within the tolerance of this
        /// point, or -1 when this point is the first one there.
        /// この点から許容差の内に既に保たれている頂点の索引。この点がそこでの最初
        /// のものなら -1。
        /// </summary>
        private static int FindNear(Dictionary<Vector3Int, List<int>> cells,
                                    List<Vector3> welded, Vector3 point, Vector3Int cell)
        {
            float toleranceSquared = WeldToleranceMetres * WeldToleranceMetres;

            for (int dx = -1; dx <= 1; dx++)
            {
                for (int dy = -1; dy <= 1; dy++)
                {
                    int near = FindInColumn(cells, welded, point, cell, dx, dy, toleranceSquared);
                    bool isFound = near >= 0;
                    if (isFound)
                    {
                        return near;
                    }
                }
            }

            return -1;
        }

        /// <summary>
        /// The three cells that share this x and y offset, searched for a vertex
        /// near the point. Split out of <see cref="FindNear"/> so neither
        /// function nests more than two levels deep.
        /// この x・y のずれを共有する 3 つの升目を、点の近くの頂点について探す。
        /// どちらの関数もネストが 2 段を越えないよう <see cref="FindNear"/> から
        /// 分けてある。
        /// </summary>
        private static int FindInColumn(Dictionary<Vector3Int, List<int>> cells,
                                        List<Vector3> welded, Vector3 point, Vector3Int cell,
                                        int dx, int dy, float toleranceSquared)
        {
            for (int dz = -1; dz <= 1; dz++)
            {
                var neighbour = new Vector3Int(cell.x + dx, cell.y + dy, cell.z + dz);
                bool isEmpty = !cells.TryGetValue(neighbour, out List<int> kept);
                if (isEmpty)
                {
                    continue;
                }

                int near = NearestWithin(welded, kept, point, toleranceSquared);
                bool isFound = near >= 0;
                if (isFound)
                {
                    return near;
                }
            }

            return -1;
        }

        /// <summary>
        /// The first vertex in this cell's list within the tolerance, or -1.
        /// この升目の一覧のうち、許容差の内にある最初の頂点。無ければ -1。
        /// </summary>
        private static int NearestWithin(List<Vector3> welded, List<int> kept,
                                         Vector3 point, float toleranceSquared)
        {
            foreach (int index in kept)
            {
                bool isSamePlace = (welded[index] - point).sqrMagnitude <= toleranceSquared;
                if (isSamePlace)
                {
                    return index;
                }
            }

            return -1;
        }

        /// <summary>Which grid cell a point falls in. / 点がどの升目に落ちるか。</summary>
        private static Vector3Int CellOf(Vector3 point)
        {
            return new Vector3Int(
                Mathf.FloorToInt(point.x / WeldToleranceMetres),
                Mathf.FloorToInt(point.y / WeldToleranceMetres),
                Mathf.FloorToInt(point.z / WeldToleranceMetres));
        }

        /// <summary>Note that a kept vertex lives in this cell. / 保った頂点がこの升目に在ることを控える。</summary>
        private static void Remember(Dictionary<Vector3Int, List<int>> cells,
                                     Vector3Int cell, int index)
        {
            bool isFirstHere = !cells.TryGetValue(cell, out List<int> kept);
            if (isFirstHere)
            {
                kept = new List<int>(1);
                cells[cell] = kept;
            }

            kept.Add(index);
        }

        /// <summary>
        /// Six times the volume the triangles enclose, signed. Positive means the
        /// faces point out of the solid, which is what every part must be after
        /// the mirror and the winding swap cancel out. Six times, rather than the
        /// volume itself, because the division would only lose precision for a
        /// caller that reads the sign.
        /// 三角形が囲む体積の 6 倍、符号付き。正なら面は立体の外を向いており、鏡映と
        /// 巻きの入れ替えが打ち消し合ったあと、全ての部品はそうなっていなければ
        /// ならない。体積そのものではなく 6 倍なのは、符号を読む呼び出し側にとって
        /// 割り算は精度を落とすだけだからである。
        /// </summary>
        public static double SixTimesSignedVolume(Vector3[] vertices, int[] triangles)
        {
            double total = 0.0;

            for (int corner = 0; corner < triangles.Length; corner += BinaryStlReader.VerticesPerTriangle)
            {
                Vector3 a = vertices[triangles[corner]];
                Vector3 b = vertices[triangles[corner + 1]];
                Vector3 c = vertices[triangles[corner + 2]];
                total += Vector3.Dot(a, Vector3.Cross(b, c));
            }

            return total;
        }
    }
}
