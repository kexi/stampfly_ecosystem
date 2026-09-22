// Copyright (c) 2026 Kei Nakayama (kexi). MIT License.
using System;
using System.Collections.Generic;

namespace StampFly.World
{
    /// <summary>
    /// Defaults for furniture made from primitives. / 基本形状で作る家具の既定値。
    /// @design docs/plans/unity-simulator.md §4 [OK]
    /// </summary>
    public static class FurnitureCatalog
    {
        public static IReadOnlyList<string> Types { get; } = Array.AsReadOnly(new[]
        {
            WorldObstacleTypes.Table, WorldObstacleTypes.Chair, WorldObstacleTypes.Sofa,
            WorldObstacleTypes.Shelf, WorldObstacleTypes.Bed,
        });

        /// <summary>Recognize furniture kinds. / 家具の種類を判定する。</summary>
        public static bool IsFurniture(string type)
        {
            foreach (string known in Types)
            {
                bool matches = known == type;
                if (matches) return true;
            }
            return false;
        }

        /// <summary>
        /// Return independent editable defaults in ENU metres. / ENU の m 単位で独立した編集用既定値を返す。
        /// </summary>
        public static WorldObstacle CreateDefault(string type, string id)
        {
            float[] size;
            string color;
            switch (type)
            {
                case WorldObstacleTypes.Table: size = new[] { 1.2f, 0.8f, 0.75f }; color = "#a87950"; break;
                case WorldObstacleTypes.Chair: size = new[] { 0.5f, 0.5f, 0.9f }; color = "#b88b60"; break;
                case WorldObstacleTypes.Sofa: size = new[] { 1.8f, 0.85f, 0.85f }; color = "#527d91"; break;
                case WorldObstacleTypes.Shelf: size = new[] { 1.0f, 0.35f, 1.6f }; color = "#ad865c"; break;
                case WorldObstacleTypes.Bed: size = new[] { 1.4f, 2.0f, 0.7f }; color = "#7c91ac"; break;
                default: throw new ArgumentException("Unknown furniture kind", nameof(type));
            }
            return new WorldObstacle
            {
                id = id, type = type, size = size, color = color,
                position = new float[WorldFormat.VectorLength],
                rotation_deg = new float[WorldFormat.VectorLength],
                flow_quality = WorldFormat.DefaultObstacleFlowQuality,
            };
        }
    }
}
