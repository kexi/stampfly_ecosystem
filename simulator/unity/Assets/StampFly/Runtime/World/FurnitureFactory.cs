// Copyright (c) 2026 Kei Nakayama (kexi). MIT License.
using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// Separate solid parts preserve leg and shelf openings for collision and sensors.
    /// 一体の箱では脚や棚の隙間を塞ぐため、衝突とセンサに使う各部品を分ける。
    /// @design docs/plans/unity-simulator.md §4 [OK]
    /// </summary>
    internal static class FurnitureFactory
    {
        private const float SeatHeight = 0.5f;
        private const float SeatThickness = 0.10f;
        private const float BackDepth = 0.10f;
        private const float LegWidth = 0.10f;
        private const float SofaSeatHeight = 0.45f;
        private const float SofaSeatThickness = 0.25f;
        private const float ArmWidth = 0.12f;
        private const float ArmHeight = 0.65f;
        private const float ShelfBoardThickness = 0.04f;
        private const int ShelfLevelCount = 4;
        private const float BedDeckHeight = 0.6f;
        private const float BedDeckThickness = 0.25f;

        /// <summary>Dispatch furniture geometry. / 家具形状を振り分ける。</summary>
        internal static void Build(WorldObstacle obstacle, Transform root, Material material)
        {
            switch (obstacle.type)
            {
                case WorldObstacleTypes.Chair: BuildChair(obstacle, root, material); return;
                case WorldObstacleTypes.Sofa: BuildSofa(obstacle, root, material); return;
                case WorldObstacleTypes.Shelf: BuildShelf(obstacle, root, material); return;
                case WorldObstacleTypes.Bed: BuildBed(obstacle, root, material); return;
            }
        }

        /// <summary>Seat, back and four legs. / 座面、背もたれと 4 本脚。</summary>
        private static void BuildChair(WorldObstacle obstacle, Transform root, Material material)
        {
            Box(obstacle, root, material, "seat", 0, SeatHeight - SeatThickness / 2, 0, 1, SeatThickness, 1);
            Box(obstacle, root, material, "back", 0, (1 + SeatHeight) / 2, (1 - BackDepth) / 2,
                1, 1 - SeatHeight, BackDepth);
            Legs(obstacle, root, material, SeatHeight - SeatThickness);
        }

        /// <summary>Upholstery with a foot gap underneath. / 脚下に隙間があるソファ。</summary>
        private static void BuildSofa(WorldObstacle obstacle, Transform root, Material material)
        {
            Box(obstacle, root, material, "seat", 0, SofaSeatHeight - SofaSeatThickness / 2, 0,
                1, SofaSeatThickness, 1);
            Box(obstacle, root, material, "back", 0, (1 + SofaSeatHeight) / 2, (1 - BackDepth) / 2,
                1, 1 - SofaSeatHeight, BackDepth);
            foreach (float side in new[] { -1f, 1f })
            {
                Box(obstacle, root, material, side < 0 ? "arm_left" : "arm_right",
                    side * (1 - ArmWidth) / 2, (ArmHeight + SofaSeatHeight) / 2, 0,
                    ArmWidth, ArmHeight - SofaSeatHeight, 1);
            }
            Legs(obstacle, root, material, SofaSeatHeight - SofaSeatThickness);
        }

        /// <summary>Open front with shelves, side panels and a thin back. / 棚板、側板、薄い背板と開いた前面。</summary>
        private static void BuildShelf(WorldObstacle obstacle, Transform root, Material material)
        {
            foreach (float side in new[] { -1f, 1f })
            {
                Box(obstacle, root, material, side < 0 ? "side_left" : "side_right",
                    side * (1 - ShelfBoardThickness) / 2, 0.5f, 0, ShelfBoardThickness, 1, 1);
            }
            Box(obstacle, root, material, "back", 0, 0.5f, (1 - ShelfBoardThickness) / 2,
                1, 1, ShelfBoardThickness);
            for (int level = 0; level < ShelfLevelCount; level++)
            {
                float height = ShelfBoardThickness / 2 + level * (1 - ShelfBoardThickness) / (ShelfLevelCount - 1);
                Box(obstacle, root, material, $"shelf_{level}", 0, height, 0, 1, ShelfBoardThickness, 1);
            }
        }

        /// <summary>Raised mattress and headboard preserve clearance beneath. / マットレスと頭板を持ち、床下を開けたベッド。</summary>
        private static void BuildBed(WorldObstacle obstacle, Transform root, Material material)
        {
            Box(obstacle, root, material, "mattress", 0, BedDeckHeight - BedDeckThickness / 2, 0,
                1, BedDeckThickness, 1);
            Box(obstacle, root, material, "headboard", 0, (1 + BedDeckHeight) / 2, (1 - BackDepth) / 2,
                1, 1 - BedDeckHeight, BackDepth);
            Legs(obstacle, root, material, BedDeckHeight - BedDeckThickness);
        }

        /// <summary>Inset corner legs fit even very small furniture. / 小さい家具でも外形内に収まる隅の脚。</summary>
        private static void Legs(WorldObstacle obstacle, Transform root, Material material, float height)
        {
            for (int index = 0; index < 4; index++)
            {
                float x = (index & 1) == 0 ? -1 : 1;
                float depth = (index & 2) == 0 ? -1 : 1;
                Box(obstacle, root, material, $"leg_{index}", x * (1 - LegWidth) / 2, height / 2,
                    depth * (1 - LegWidth) / 2, LegWidth, height, LegWidth);
            }
        }

        /// <summary>
        /// Scale normalized Unity coordinates by the ENU footprint and height.
        /// 正規化した Unity 座標を ENU の底面寸法と高さで拡大する。
        /// </summary>
        private static void Box(WorldObstacle obstacle, Transform root, Material material, string name,
                                float x, float up, float depth, float width, float height, float length)
        {
            Vector3 extent = new Vector3(obstacle.size[0], obstacle.size[2], obstacle.size[1]);
            ObstacleFactory.AddBox(root, name, material, obstacle,
                Vector3.Scale(new Vector3(x, up, depth), extent),
                Vector3.Scale(new Vector3(width, height, length), extent));
        }
    }
}
