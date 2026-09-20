/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — normals with hard edges kept).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Collections.Generic;
using UnityEngine;

namespace StampFly.Editor.MeshTools
{
    /// <summary>
    /// Gives a welded mesh its normals, smoothing a shared vertex only across
    /// the faces that nearly agree and splitting it again where they do not.
    ///
    /// 溶接したメッシュに法線を与える。共有した頂点を滑らかにするのは、ほぼ揃って
    /// いる面どうしの間だけで、揃っていなければ頂点を分け直す。
    ///
    /// ## Why not Mesh.RecalculateNormals / Mesh.RecalculateNormals を使わない理由
    ///
    /// Unity's own call averages across every face that touches a vertex, with
    /// no threshold. On these parts that is wrong in a way one can see: a motor
    /// can's rim is a right angle between its side and its top, and averaging
    /// there rounds the rim into a soft blob. Splitting at an angle is what a
    /// modeller means by a hard edge, and doing it here — rather than asking
    /// Unity — is also what makes the asset the same every time the converter
    /// runs.
    ///
    /// Unity 自身の呼び出しは、頂点に接する全ての面を、しきい値なしで平均する。
    /// これらの部品ではそれが目に見えて誤りになる。モータ缶の縁は側面と天面の間の
    /// 直角であり、そこで平均すると縁が丸く滲む。角度で分けることが、設計者の言う
    /// 硬い稜線である。Unity に頼まずここで行うことは、変換ツールを実行するたび
    /// 資産が同じになることでもある。
    ///
    /// ## What a split costs / 分けることの費用
    ///
    /// A split adds a vertex, never a triangle: the triangle that wanted a
    /// different normal is pointed at the new copy instead. A part with many
    /// right angles therefore ends up near its unwelded vertex count, and a
    /// turned cylinder near its welded one, which is the outcome asked for.
    ///
    /// 分けても増えるのは頂点だけで、三角形は増えない。違う法線を求めた三角形が、
    /// 新しい複製のほうを指すようになるだけである。直角の多い部品は溶接前の頂点数
    /// に近づき、旋削された円筒は溶接後の頂点数に近づく。求めた結果はそれである。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（機体の見た目）
    /// </summary>
    public static class SmoothNormals
    {
        /// <summary>
        /// One face's contribution to a vertex: the face's own normal, and where
        /// in the triangle list the corner that referred to it sits.
        /// 面 1 つが頂点に寄せるもの。面自身の法線と、それを参照していた角が三角形
        /// の一覧のどこに在るかである。
        /// </summary>
        private readonly struct Corner
        {
            public readonly Vector3 FaceNormal;
            public readonly int IndexSlot;

            public Corner(Vector3 faceNormal, int indexSlot)
            {
                FaceNormal = faceNormal;
                IndexSlot = indexSlot;
            }
        }

        /// <summary>
        /// The same geometry with normals, and with the vertices split wherever
        /// two faces meeting at one meet at more than <paramref name="hardEdgeAngleDegrees"/>.
        /// 同じ形に法線を付けたもの。1 つの頂点で出会う 2 つの面が
        /// <paramref name="hardEdgeAngleDegrees"/> を越えて開いていれば、そこで
        /// 頂点を分ける。
        /// </summary>
        public static StlToUnityGeometry.Geometry Apply(
            StlToUnityGeometry.Geometry welded, float hardEdgeAngleDegrees)
        {
            List<Corner>[] corners = GatherCorners(welded);
            float smoothLimit = Mathf.Cos(hardEdgeAngleDegrees * Mathf.Deg2Rad);

            var vertices = new List<Vector3>(welded.Vertices.Length);
            var normals = new List<Vector3>(welded.Vertices.Length);
            var triangles = new int[welded.Triangles.Length];

            for (int vertex = 0; vertex < welded.Vertices.Length; vertex++)
            {
                SplitVertex(welded.Vertices[vertex], corners[vertex], smoothLimit,
                            vertices, normals, triangles);
            }

            return new StlToUnityGeometry.Geometry(
                vertices.ToArray(), normals.ToArray(), triangles);
        }

        /// <summary>
        /// For each welded vertex, the faces that touch it. A face's normal is
        /// left unnormalised so that a large face counts for more than a sliver,
        /// which is the usual way to weight an averaged normal.
        /// 溶接した頂点ごとに、それに接する面。面の法線は正規化せずに置く。大きな
        /// 面が細長い切れ端より重く数えられるようにするためで、平均する法線の重み
        /// 付けとしてはこれが通例である。
        /// </summary>
        private static List<Corner>[] GatherCorners(StlToUnityGeometry.Geometry welded)
        {
            var corners = new List<Corner>[welded.Vertices.Length];
            for (int vertex = 0; vertex < corners.Length; vertex++)
            {
                corners[vertex] = new List<Corner>(FacesPerVertexGuess);
            }

            for (int slot = 0; slot < welded.Triangles.Length;
                 slot += BinaryStlReader.VerticesPerTriangle)
            {
                Vector3 faceNormal = FaceNormalAt(welded, slot);
                for (int corner = 0; corner < BinaryStlReader.VerticesPerTriangle; corner++)
                {
                    int vertex = welded.Triangles[slot + corner];
                    corners[vertex].Add(new Corner(faceNormal, slot + corner));
                }
            }

            return corners;
        }

        /// <summary>
        /// Twice the area of the triangle at this slot, along its normal. The
        /// cross product of two edges is exactly that, and its direction follows
        /// the winding, so an outward-wound face gives an outward normal.
        /// この位置の三角形の面積の 2 倍を、その法線の向きに持つベクトル。辺 2 つの
        /// 外積がちょうどそれで、向きは巻きに従うので、外向きに巻かれた面は外向きの
        /// 法線を与える。
        /// </summary>
        private static Vector3 FaceNormalAt(StlToUnityGeometry.Geometry welded, int slot)
        {
            Vector3 a = welded.Vertices[welded.Triangles[slot]];
            Vector3 b = welded.Vertices[welded.Triangles[slot + 1]];
            Vector3 c = welded.Vertices[welded.Triangles[slot + 2]];
            return Vector3.Cross(b - a, c - a);
        }

        /// <summary>
        /// Emit one vertex per group of faces that agree, and point each face's
        /// corner at the copy it belongs to. A face joins the first group whose
        /// running average it is still within the angle of, so the grouping
        /// follows the order the triangles are stored in and is therefore the
        /// same on every run.
        /// 揃っている面の組ごとに頂点を 1 つ出し、各面の角を、属する複製のほうへ
        /// 向ける。面は、その時点の平均から角度の内にある最初の組に入るので、
        /// 組み分けは三角形の保存順に従い、よってどの実行でも同じになる。
        /// </summary>
        private static void SplitVertex(Vector3 place, List<Corner> corners, float smoothLimit,
                                        List<Vector3> vertices, List<Vector3> normals,
                                        int[] triangles)
        {
            var groupNormals = new List<Vector3>(corners.Count);
            var groupVertices = new List<int>(corners.Count);

            foreach (Corner corner in corners)
            {
                int group = GroupFor(groupNormals, corner.FaceNormal, smoothLimit);
                bool isNewGroup = group < 0;
                if (isNewGroup)
                {
                    group = groupNormals.Count;
                    groupNormals.Add(Vector3.zero);
                    groupVertices.Add(Emit(place, vertices, normals));
                }

                groupNormals[group] += corner.FaceNormal;
                triangles[corner.IndexSlot] = groupVertices[group];
            }

            WriteGroupNormals(groupNormals, groupVertices, normals);
        }

        /// <summary>
        /// The first group this face is within the angle of, or -1 when it
        /// belongs to none of them yet.
        /// この面が角度の内にある最初の組。まだどれにも属さなければ -1。
        /// </summary>
        private static int GroupFor(List<Vector3> groupNormals, Vector3 faceNormal,
                                    float smoothLimit)
        {
            Vector3 direction = faceNormal.normalized;

            for (int group = 0; group < groupNormals.Count; group++)
            {
                bool isFirstInGroup = groupNormals[group] == Vector3.zero;
                bool isWithinAngle = isFirstInGroup ||
                    Vector3.Dot(groupNormals[group].normalized, direction) >= smoothLimit;
                if (isWithinAngle)
                {
                    return group;
                }
            }

            return -1;
        }

        /// <summary>Add a copy of the vertex and reserve its normal's place. / 頂点の複製を足し、その法線の場所を取る。</summary>
        private static int Emit(Vector3 place, List<Vector3> vertices, List<Vector3> normals)
        {
            vertices.Add(place);
            normals.Add(Vector3.up);
            return vertices.Count - 1;
        }

        /// <summary>
        /// Store each group's averaged direction on the vertex it was emitted
        /// as. Done after the loop because a group's average is only complete
        /// once every face has joined it.
        /// 各組の平均した向きを、その組として出した頂点に書く。組の平均が揃うのは
        /// 全ての面が入り終えたあとなので、繰り返しの後で行う。
        /// </summary>
        private static void WriteGroupNormals(List<Vector3> groupNormals,
                                              List<int> groupVertices, List<Vector3> normals)
        {
            for (int group = 0; group < groupNormals.Count; group++)
            {
                normals[groupVertices[group]] = groupNormals[group].normalized;
            }
        }

        /// <summary>
        /// How many faces a vertex is expected to touch, used only to size a
        /// list before it is filled. Six is what a triangulated surface averages.
        /// 頂点に接する面の数の見込み。一覧を埋める前に大きさを決めるためだけに使う。
        /// 三角形に分けた面での平均が 6 である。
        /// </summary>
        private const int FacesPerVertexGuess = 6;
    }
}
