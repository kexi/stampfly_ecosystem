using System;
using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// Outcome of reading a world file: either a world, or a reason it was
    /// refused. The loader never returns a half-filled world, because a world
    /// missing a room or a spawn point cannot be built.
    /// 空間ファイルを読んだ結果。空間そのものか、断った理由のどちらか。部屋や
    /// 出発点の欠けた空間は生成できないので、中途半端な結果は返さない。
    /// </summary>
    public readonly struct WorldReadResult
    {
        /// <summary>The world, or null when it was refused. / 断ったときは null。</summary>
        public readonly WorldFile World;

        /// <summary>Why it was refused, or null on success. / 成功時は null。</summary>
        public readonly string Reason;

        private WorldReadResult(WorldFile world, string reason)
        {
            World = world;
            Reason = reason;
        }

        /// <summary>Whether the file was accepted. / 受け入れられたか。</summary>
        public bool Ok => World != null;

        internal static WorldReadResult Accepted(WorldFile world) => new WorldReadResult(world, null);

        internal static WorldReadResult Refused(string reason) => new WorldReadResult(null, reason);
    }

    /// <summary>
    /// Reads a world file from JSON text and checks the agreements a loader
    /// must honour (Schemas/README.md §9.3): the format marker, the version,
    /// the frame, and enough structure for the builders to work from.
    /// It deliberately does NOT repeat the semantic checks of
    /// tools/unity_world/validate.py (obstacles outside the room, overlaps,
    /// takeoff clearance): those belong to the checker, which runs before a
    /// world ships, and duplicating them here would give two places to keep in
    /// step.
    /// JSON の文字列から空間ファイルを読み、読み込み側が守るべき約束
    /// （README §9.3）を確かめる。目印・版・座標系と、生成に足りるだけの構造。
    /// 意味検査（部屋の外・重なり・離陸の余地）は tools/unity_world/validate.py
    /// の仕事なので、ここでは繰り返さない。二重に持つと食い違いの元になる。
    /// </summary>
    public static class WorldFileReader
    {
        /// <summary>
        /// Read a world from JSON text, refusing it with a reason when the file
        /// is not a world file this build can use.
        /// JSON の文字列から空間を読む。使えないファイルは理由を添えて断る。
        /// </summary>
        public static WorldReadResult Read(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return WorldReadResult.Refused("the world file is empty");
            }

            WorldFile world;
            try
            {
                world = JsonUtility.FromJson<WorldFile>(json);
            }
            catch (ArgumentException exception)
            {
                // JsonUtility reports malformed JSON as an ArgumentException.
                // JsonUtility は壊れた JSON を ArgumentException で知らせる。
                return WorldReadResult.Refused($"the world file is not valid JSON: {exception.Message}");
            }

            if (world == null)
            {
                return WorldReadResult.Refused("the world file does not contain a JSON object");
            }

            string headerReason = CheckHeader(world);
            if (headerReason != null)
            {
                return WorldReadResult.Refused(headerReason);
            }

            string bodyReason = CheckBody(world);
            if (bodyReason != null)
            {
                return WorldReadResult.Refused(bodyReason);
            }

            ApplyDefaults(world);
            return WorldReadResult.Accepted(world);
        }

        /// <summary>
        /// Check the marker, the version, the frame and the units, so a file
        /// written for another tool or another frame is never misread.
        /// 目印・版・座標系・単位を確かめ、別の道具や別の座標系のファイルを
        /// 黙って誤解釈しないようにする。
        /// </summary>
        private static string CheckHeader(WorldFile world)
        {
            if (world.format != WorldFormat.Marker)
            {
                return $"format must be '{WorldFormat.Marker}', but it is '{world.format}'; "
                     + "this file is not a StampFly world file";
            }

            if (world.version != WorldFormat.Version)
            {
                return $"version must be {WorldFormat.Version}, but it is {world.version}; "
                     + "no other version of this format exists";
            }

            if (world.frame != WorldFormat.Frame)
            {
                return $"frame must be '{WorldFormat.Frame}' (x=east, y=north, z=up, right-handed), "
                     + $"but it is '{world.frame}'";
            }

            if (world.units == null
                || world.units.length != WorldFormat.LengthUnit
                || world.units.angle != WorldFormat.AngleUnit)
            {
                return $"units must be length '{WorldFormat.LengthUnit}' and angle "
                     + $"'{WorldFormat.AngleUnit}'";
            }

            return null;
        }

        /// <summary>
        /// Check that the room, the floor, the spawn point and the obstacles
        /// carry the numbers the builders read.
        /// 部屋・床・出発点・障害物が、生成側が読む数を持っているか確かめる。
        /// </summary>
        private static string CheckBody(WorldFile world)
        {
            if (world.room == null || !IsVector3(world.room.size))
            {
                return "room.size must be three numbers [x_east, y_north, z_up]";
            }

            foreach (float extent in world.room.size)
            {
                if (!(extent > 0.0f))
                {
                    return "room.size must be greater than 0 m on every axis";
                }
            }

            if (world.floor == null || world.floor.colors == null
                || world.floor.colors.Length != WorldFormat.FloorColorCount)
            {
                return $"floor must carry a pattern and exactly {WorldFormat.FloorColorCount} colours";
            }

            if (world.spawn == null || !IsVector3(world.spawn.position))
            {
                return "spawn.position must be three numbers [x_east, y_north, z_up]";
            }

            if (world.obstacles == null)
            {
                // An absent list and an empty list mean the same thing, and
                // empty_room genuinely has none.
                // 一覧が無いのと空なのは同じ意味で、empty_room は本当に 0 個。
                world.obstacles = Array.Empty<WorldObstacle>();
            }

            return CheckObstacles(world.obstacles);
        }

        /// <summary>
        /// Check every obstacle's kind and geometry. / 各障害物の種類と幾何を確かめる。
        /// </summary>
        private static string CheckObstacles(WorldObstacle[] obstacles)
        {
            for (int index = 0; index < obstacles.Length; index++)
            {
                WorldObstacle obstacle = obstacles[index];
                string where = obstacle?.id ?? $"obstacles[{index}]";

                if (obstacle == null)
                {
                    return $"obstacles[{index}] is missing";
                }

                if (!WorldObstacleTypes.IsKnown(obstacle.type))
                {
                    return $"obstacle '{where}': type '{obstacle.type}' is not one of "
                         + $"{string.Join(", ", WorldObstacleTypes.All)}";
                }

                string geometryReason = CheckObstacleGeometry(obstacle, where);
                if (geometryReason != null)
                {
                    return geometryReason;
                }
            }

            return null;
        }

        /// <summary>
        /// Check one obstacle's vectors and its thickness. / 障害物 1 個の幾何。
        /// </summary>
        private static string CheckObstacleGeometry(WorldObstacle obstacle, string where)
        {
            if (!IsVector3(obstacle.position) || !IsVector3(obstacle.rotation_deg)
                || !IsVector3(obstacle.size))
            {
                return $"obstacle '{where}': position, rotation_deg and size must each be "
                     + "three numbers";
            }

            foreach (float length in obstacle.size)
            {
                if (!(length > 0.0f))
                {
                    return $"obstacle '{where}': every size must be greater than 0 m";
                }
            }

            bool hollow = WorldObstacleTypes.IsHollow(obstacle.type);
            if (hollow && !(obstacle.thickness > 0.0f))
            {
                return $"obstacle '{where}': type '{obstacle.type}' is hollow, so thickness "
                     + "must be greater than 0 m";
            }

            return null;
        }

        /// <summary>
        /// Fill in what the format leaves out (Schemas/README.md §9.3). The
        /// floor's flow_quality is left alone on purpose: 0 is a real value
        /// there, and featureless_floor relies on being able to say 0.05.
        /// 省略された項目を埋める（README §9.3）。床の flow_quality には手を
        /// 付けない。床では 0 が本物の値で、featureless_floor がそれに頼る。
        /// </summary>
        private static void ApplyDefaults(WorldFile world)
        {
            if (string.IsNullOrEmpty(world.light))
            {
                world.light = WorldFormat.DefaultLight;
            }

            foreach (WorldObstacle obstacle in world.obstacles)
            {
                if (obstacle.flow_quality == 0.0f)
                {
                    obstacle.flow_quality = WorldFormat.DefaultObstacleFlowQuality;
                }

                if (obstacle.type == WorldObstacleTypes.Ring && obstacle.segments == 0)
                {
                    obstacle.segments = WorldFormat.DefaultRingSegments;
                }
            }
        }

        /// <summary>
        /// Whether an array holds exactly three finite numbers. / 有限数 3 つか。
        /// </summary>
        private static bool IsVector3(float[] values)
        {
            if (values == null || values.Length != WorldFormat.VectorLength)
            {
                return false;
            }

            foreach (float value in values)
            {
                if (float.IsNaN(value) || float.IsInfinity(value))
                {
                    return false;
                }
            }

            return true;
        }
    }
}
