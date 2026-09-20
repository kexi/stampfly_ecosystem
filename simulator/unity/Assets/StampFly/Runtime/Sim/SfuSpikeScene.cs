/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — stage 1(b)(d) spike).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine;

namespace StampFly.Sim
{
    /// <summary>
    /// Builds the stage 1(b) scene in code — a camera, a floor, a cube for the
    /// vehicle and the driver — so no scene asset has to be hand-edited for a
    /// throwaway check. Stage 3 replaces this with a real scene.
    ///
    /// 段階 1(b) の場面をコードで組み立てる — カメラ・床・機体の立方体・
    /// 駆動部品。使い捨ての検証のために場面の資産を手で編集せずに済ませるため。
    /// 段階 3 で本物の場面に置き換える。
    ///
    /// @design docs/plans/unity-simulator.md — 段階 1(b)(d) 技術検証
    /// </summary>
    public static class SfuSpikeScene
    {
        // The vehicle cube's edge [m]: big enough to see, small enough that the
        // 0.58 m hover height still reads as a height.
        // 機体の立方体の 1 辺 [m]: 見える大きさで、かつホバリングの高さ 0.58 m が
        // 高さとして読める程度に小さい。
        private const float VehicleEdgeMetres = 0.12f;

        // The floor's edge [m]. A plane's default is 10 m, so 0.4 gives 4 m.
        // 床の 1 辺 [m]。Plane の既定は 10m なので 0.4 で 4m になる。
        private const float FloorScale = 0.4f;

        // Where the camera sits and looks, chosen so the cube stays in frame
        // from the ground to about 1 m.
        // カメラの位置と向き。立方体が地上から約 1m まで画面に収まるように選ぶ。
        private static readonly Vector3 CameraPosition = new Vector3(2.2f, 1.0f, -2.2f);
        private static readonly Vector3 CameraTarget = new Vector3(0.0f, 0.5f, 0.0f);

        /// <summary>
        /// Runs once when the player starts, before the first scene loads, so
        /// the check works whatever scene the build happens to contain.
        /// プレイヤーの起動時、最初の場面が読み込まれる前に 1 回だけ走る。ビルドに
        /// どの場面が入っていても検証が成り立つようにするため。
        /// </summary>
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        public static void Build()
        {
            // The name JavaScript uses with SendMessage, so a browser check can
            // cycle the power without a keystroke.
            // JavaScript が SendMessage で使う名前。ブラウザでの検証がキー操作
            // なしで電源を入れ直せるようにするため。
            var root = new GameObject("StampFlySpike");
            Object.DontDestroyOnLoad(root);

            CreateCamera(root.transform);
            CreateFloor(root.transform);
            Transform vehicle = CreateVehicle(root.transform);

            var driver = root.AddComponent<SfuSpikeDriver>();
            driver.vehicleTransform = vehicle;
        }

        /// <summary>A camera aimed at the hovering cube. / ホバリングする立方体を向いたカメラ。</summary>
        private static void CreateCamera(Transform parent)
        {
            var cameraObject = new GameObject("SpikeCamera");
            cameraObject.transform.SetParent(parent, false);
            cameraObject.transform.position = CameraPosition;
            cameraObject.transform.LookAt(CameraTarget);

            var camera = cameraObject.AddComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.10f, 0.12f, 0.16f);
        }

        /// <summary>A plain floor, for a sense of the height. / 高さが分かるだけの素の床。</summary>
        private static void CreateFloor(Transform parent)
        {
            GameObject floor = GameObject.CreatePrimitive(PrimitiveType.Plane);
            floor.name = "SpikeFloor";
            floor.transform.SetParent(parent, false);
            floor.transform.localScale = new Vector3(FloorScale, 1.0f, FloorScale);

            // No collider: nothing in this check touches PhysX.
            // コライダは外す。この検証は PhysX に一切触れない。
            Object.Destroy(floor.GetComponent<Collider>());
        }

        /// <summary>The cube standing in for the vehicle. / 機体の代わりの立方体。</summary>
        private static Transform CreateVehicle(Transform parent)
        {
            GameObject vehicle = GameObject.CreatePrimitive(PrimitiveType.Cube);
            vehicle.name = "SpikeVehicle";
            vehicle.transform.SetParent(parent, false);
            vehicle.transform.localScale = Vector3.one * VehicleEdgeMetres;

            Object.Destroy(vehicle.GetComponent<Collider>());
            return vehicle.transform;
        }
    }
}
