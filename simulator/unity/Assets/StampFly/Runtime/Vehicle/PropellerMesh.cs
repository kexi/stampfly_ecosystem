/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the generated propeller).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Collections.Generic;
using UnityEngine;

namespace StampFly.Vehicle
{
    /// <summary>
    /// Builds a three-blade propeller as a mesh, rather than importing one.
    /// The shape follows the SILS viewer's `makeBlade` / `makeProp`
    /// (`simulator/sils/gui/static/app.js`): a paddle outline that widens from
    /// the root, narrows to a rounded tip, and twists linearly from root to tip.
    ///
    /// 3 枚羽根のプロペラを、取り込みではなく生成で作る。形は SILS の表示側の
    /// `makeBlade`／`makeProp`（`simulator/sils/gui/static/app.js`）に倣う。
    /// 根元から広がり、丸い先端へ細り、根元から先端へ線形にねじれるパドル形である。
    ///
    /// Generating it keeps the project free of a mesh whose provenance and
    /// licence are not recorded, which is why the existing STL files are not
    /// used (plan §4, "Vehicle appearance").
    /// 生成にしてあるのは、出所と利用条件が記録されていないメッシュをプロジェクトに
    /// 入れないためである。既存の STL を使わない理由がこれである（計画 §4「機体の
    /// 見た目」）。
    ///
    /// The prop spins about its LOCAL Y, which is the vehicle's up axis once it
    /// is parented to the body.
    /// プロペラは**ローカルの** Y のまわりに回る。機体にぶら下げれば、それが機体の
    /// 上方向になる。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（機体の見た目）
    /// </summary>
    public static class PropellerMesh
    {
        /// <summary>Blades on one propeller. / プロペラ 1 つの羽根の数。</summary>
        public const int BladeCount = 3;

        /// <summary>
        /// The propeller's radius [m]. Generated from
        /// control/models/stampfly_physical.yaml; not a number to edit here.
        /// プロペラの半径 [m]。control/models/stampfly_physical.yaml から生成
        /// される値で、ここで書き換える数値ではない。
        /// </summary>
        public const float RadiusMeters = StampFly.Sim.GeneratedParams.PropellerRadiusMeters;

        /// <summary>The hub's radius as a fraction of the propeller's. / ハブの半径。プロペラの半径に対する割合。</summary>
        public const float HubRadiusFraction = 0.22f;

        /// <summary>Blade twist at the root [rad]. / 根元でのねじれ [rad]。</summary>
        public const float TwistRootRadians = 0.38f;

        /// <summary>Blade twist at the tip [rad]. / 先端でのねじれ [rad]。</summary>
        public const float TwistTipRadians = 0.10f;

        /// <summary>Points along one edge of a blade's outline. / 羽根の輪郭の片側の点の数。</summary>
        private const int OutlineSteps = 12;

        /// <summary>
        /// Builds one propeller. The mesh is flat — two triangles' worth of
        /// thickness would not be visible at this size and would double the
        /// vertex count for nothing — and is drawn from both sides.
        /// プロペラ 1 つを作る。メッシュは平らにしてある。この大きさでは厚みは
        /// 見えず、頂点の数だけが倍になるからである。両面から描く。
        /// </summary>
        public static Mesh Build()
        {
            var vertices = new List<Vector3>();
            var triangles = new List<int>();

            AddHub(vertices, triangles);
            for (int blade = 0; blade < BladeCount; blade++)
            {
                float bladeAngle = blade * (360.0f / BladeCount);
                AddBlade(vertices, triangles, bladeAngle);
            }

            var mesh = new Mesh { name = "StampFlyPropeller" };
            mesh.SetVertices(vertices);
            mesh.SetTriangles(triangles, 0);
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            return mesh;
        }

        /// <summary>
        /// The hub: a flat disc at the propeller's centre, so the blades do not
        /// appear to meet at a point.
        /// ハブ。プロペラの中心の平らな円板で、羽根が一点で交わって見えないように
        /// するためのもの。
        /// </summary>
        private static void AddHub(List<Vector3> vertices, List<int> triangles)
        {
            const int Segments = 16;
            float hubRadius = RadiusMeters * HubRadiusFraction;

            int centre = vertices.Count;
            vertices.Add(Vector3.zero);
            for (int step = 0; step <= Segments; step++)
            {
                float angle = step * (2.0f * Mathf.PI / Segments);
                vertices.Add(new Vector3(
                    hubRadius * Mathf.Cos(angle), 0.0f, hubRadius * Mathf.Sin(angle)));
            }

            for (int step = 0; step < Segments; step++)
            {
                AddQuadAsTriangles(triangles, centre, centre + step + 1,
                                   centre + step + 2, centre);
            }
        }

        /// <summary>
        /// One blade, swept out along +X and rotated into place. The outline is
        /// widest around a third of the way out and narrows to the tip, and each
        /// station is rolled by the twist that station carries.
        /// 羽根 1 枚を +X 方向へ伸ばし、所定の向きへ回して置く。輪郭は外へ 3 分の 1
        /// ほどの所で最も広く、先端へ細る。各断面は、その位置のねじれのぶん傾ける。
        /// </summary>
        private static void AddBlade(List<Vector3> vertices, List<int> triangles,
                                     float bladeAngleDegrees)
        {
            float hubRadius = RadiusMeters * HubRadiusFraction;
            float length = RadiusMeters - hubRadius * 0.6f;
            float maxWidth = length / 2.7f;
            Quaternion place = Quaternion.Euler(0.0f, bladeAngleDegrees, 0.0f);

            int first = vertices.Count;
            for (int step = 0; step <= OutlineSteps; step++)
            {
                float alongBlade = step / (float)OutlineSteps;
                float radius = hubRadius * 0.6f + alongBlade * length;
                float halfWidth = 0.5f * maxWidth * ChordFraction(alongBlade);
                float twist = Mathf.Lerp(TwistRootRadians, TwistTipRadians, alongBlade);

                // The chord lies in the prop's plane and is rolled about the
                // blade's own long axis, which is what gives the blade its pitch.
                // コードはプロペラの面に乗り、羽根自身の長手方向のまわりに傾ける。
                // これが羽根のピッチになる。
                float rise = halfWidth * Mathf.Sin(twist);
                float run = halfWidth * Mathf.Cos(twist);
                vertices.Add(place * new Vector3(radius, rise, run));
                vertices.Add(place * new Vector3(radius, -rise, -run));
            }

            for (int step = 0; step < OutlineSteps; step++)
            {
                int near = first + step * 2;
                AddQuadAsTriangles(triangles, near, near + 1, near + 3, near + 2);
            }
        }

        /// <summary>
        /// How wide the blade is at a given fraction of its length, 0 at the
        /// root and tip and widest around a third of the way out — the paddle
        /// outline of the SILS viewer, as a curve rather than as beziers.
        /// 羽根の長さのある割合の位置での幅。根元と先端で 0、外へ 3 分の 1 ほどの
        /// 所で最大になる。SILS の表示側のパドル形を、ベジエでなく曲線で表したもの。
        /// </summary>
        private static float ChordFraction(float alongBlade)
        {
            float root = Mathf.Sqrt(Mathf.Clamp01(alongBlade * 3.0f));
            float tip = Mathf.Clamp01((1.0f - alongBlade) * 3.0f);
            return Mathf.Min(1.0f, root) * Mathf.Min(1.0f, Mathf.Sqrt(tip));
        }

        /// <summary>
        /// Adds a quad as two triangles, wound both ways so the flat blade is
        /// visible from above and below.
        /// 四角形を三角形 2 つとして足す。巻きを両方向に付け、平らな羽根が上からも
        /// 下からも見えるようにする。
        /// </summary>
        private static void AddQuadAsTriangles(List<int> triangles, int a, int b, int c, int d)
        {
            triangles.Add(a); triangles.Add(b); triangles.Add(c);
            triangles.Add(a); triangles.Add(c); triangles.Add(d);
            triangles.Add(a); triangles.Add(c); triangles.Add(b);
            triangles.Add(a); triangles.Add(d); triangles.Add(c);
        }
    }
}
