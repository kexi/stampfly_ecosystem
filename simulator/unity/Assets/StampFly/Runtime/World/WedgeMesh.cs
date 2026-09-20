using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// Generates the wedge a ramp is made of. Unity has no wedge primitive, and
    /// the plan's §2 rules out imported assets, so the five faces are written
    /// out here.
    ///
    /// The wedge is built in Unity coordinates, already converted from the
    /// ramp's ENU description (Schemas/README.md §3.1): the origin is the
    /// centre of the low edge at the bottom, the run goes along Unity +z (ENU
    /// +y, north) and the rise along Unity +y (ENU +z, up).
    ///
    ///        Unity +y (rise / 立ち上がり)
    ///          ^
    ///          |          /| (run, rise)
    ///          |        /  |
    ///          |      /    |
    ///          o----------->  Unity +z (run / 水平距離)
    ///        origin
    ///
    /// ramp を作るくさびを生成する。Unity にくさびの基本形状は無く、計画 §2 が
    /// 外部の素材を禁じているので、5 つの面をここで書き出す。
    ///
    /// くさびは、ramp の ENU での記述（README §3.1）を変換した後の Unity の
    /// 座標で作る。原点は低い辺の中心（底）で、水平距離は Unity の +z（ENU の
    /// +y＝北）、立ち上がりは Unity の +y（ENU の +z＝上）である。
    /// </summary>
    public static class WedgeMesh
    {
        // Corner numbering used by the triangle list below.
        // 下の三角形の一覧が使う頂点の番号。
        private const int BackLeft = 0;    // (-w/2, 0, 0)
        private const int BackRight = 1;   // (+w/2, 0, 0)
        private const int FrontLeft = 2;   // (-w/2, 0, run)
        private const int FrontRight = 3;  // (+w/2, 0, run)
        private const int TopLeft = 4;     // (-w/2, rise, run)
        private const int TopRight = 5;    // (+w/2, rise, run)

        private const int CornerCount = 6;

        /// <summary>
        /// Create a wedge mesh of the given width, run and rise.
        /// 指定した幅・水平距離・立ち上がりのくさびのメッシュを作る。
        /// </summary>
        public static Mesh Create(float width, float run, float rise)
        {
            float halfWidth = width / 2.0f;
            Vector3[] corners = new Vector3[CornerCount];
            corners[BackLeft] = new Vector3(-halfWidth, 0.0f, 0.0f);
            corners[BackRight] = new Vector3(halfWidth, 0.0f, 0.0f);
            corners[FrontLeft] = new Vector3(-halfWidth, 0.0f, run);
            corners[FrontRight] = new Vector3(halfWidth, 0.0f, run);
            corners[TopLeft] = new Vector3(-halfWidth, rise, run);
            corners[TopRight] = new Vector3(halfWidth, rise, run);

            Mesh mesh = new Mesh { name = "Ramp wedge" };
            mesh.vertices = corners;
            mesh.triangles = Triangles();
            // Vertices are shared between faces, so the normals are averaged
            // rather than flat. The ramp is a large, matte surface where that
            // is not visible, and sharing keeps the collider's vertex count at
            // six.
            // 面どうしが頂点を共有するので、法線は平均されて平坦にはならない。
            // ramp は大きなつや消しの面でその差が見えず、共有すればコライダの
            // 頂点は 6 個のままで済む。
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            return mesh;
        }

        /// <summary>
        /// The five faces as triangles, wound clockwise seen from outside,
        /// which is the facing Unity draws.
        /// 5 つの面を三角形で。外から見て時計回りに巻く。Unity が描く向きである。
        /// </summary>
        private static int[] Triangles()
        {
            return new[]
            {
                // Bottom, facing down. / 底面。下を向く。
                BackLeft, FrontLeft, BackRight,
                BackRight, FrontLeft, FrontRight,

                // The tall end at the far side of the run, facing +z.
                // 水平距離の先にある高い面。+z を向く。
                FrontLeft, TopLeft, FrontRight,
                FrontRight, TopLeft, TopRight,

                // The slope, from the low edge up to the top edge.
                // 斜面。低い辺から上の辺まで。
                BackLeft, BackRight, TopLeft,
                TopLeft, BackRight, TopRight,

                // The left side, a triangle facing -x.
                // 左の側面。−x を向く三角形。
                BackLeft, TopLeft, FrontLeft,

                // The right side, a triangle facing +x.
                // 右の側面。+x を向く三角形。
                BackRight, FrontRight, TopRight,
            };
        }
    }
}
