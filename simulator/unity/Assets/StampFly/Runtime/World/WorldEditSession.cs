/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kei Nakayama (kexi)
 */
using System;
using System.Collections.Generic;
using StampFly.Core;
using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// Edit checked copies, then replace the loaded world through its normal loader.
    /// 検査した複製を正規のローダーへ渡して空間を編集する。
    /// @design docs/plans/unity-simulator.md §4 [OK]
    /// </summary>
    public sealed class WorldEditSession : IDisposable
    {
        public const int MaximumObstacles = 500;
        public const int MaximumUndoSteps = 20;
        // Match the standalone world's takeoff check; no Sim dependency is allowed here.
        // 単独の空間検査に合わせる。ここでは Sim への依存を持てない。
        public const float SpawnHalfWidth = 0.0816f / 2.0f;
        public const float SpawnHalfHeight = 0.0206f / 2.0f;
        public const float TakeoffClearance = 0.6f;
        private const float BoundsTolerance = 0.0001f;
        private const float FullTurn = 360.0f;
        private const int MaximumRingSegments = 128;
        private const int MinimumRingSegments = 6;
        private readonly WorldLoader loader;
        private readonly List<string> undo = new List<string>();
        private bool applying;

        public string LastError { get; private set; } = string.Empty;
        public bool CanUndo => undo.Count > 0;
        public event Action Changed;

        /// <summary>Observe external loads as well as UI edits. / UI 外からの読込も監視する。</summary>
        public WorldEditSession(WorldLoader loader)
        {
            this.loader = loader ?? throw new ArgumentNullException(nameof(loader));
            loader.Changed += OnWorldChanged;
        }

        /// <summary>Release the loader subscription. / ローダーへの購読を解除する。</summary>
        public void Dispose() => loader.Changed -= OnWorldChanged;

        /// <summary>Add a catalog item at an ENU position. / カタログの家具を ENU 座標へ追加する。</summary>
        public bool TryAdd(string type, Vector3 position, out string id)
        {
            id = null;
            bool finite = IsFinite(position);
            if (!finite) return Fail("Position must be finite.");
            bool ready = TryCopy(out WorldFile candidate);
            if (!ready) return false;
            string newId = NextId(candidate, type);
            bool known = FurnitureCatalog.IsFurniture(type);
            if (!known) return Fail("Unknown furniture type.");
            WorldObstacle obstacle = FurnitureCatalog.CreateDefault(type, newId);
            obstacle.position = ToArray(position);
            var obstacles = new List<WorldObstacle>(candidate.obstacles) { obstacle };
            candidate.obstacles = obstacles.ToArray();
            bool accepted = Apply(candidate, "add", newId);
            if (!accepted) return false;
            id = newId;
            return true;
        }

        /// <summary>Move one item in ENU metres. / 家具を ENU の m 単位で動かす。</summary>
        public bool TryMove(string id, Vector3 position)
        {
            bool finite = IsFinite(position);
            if (!finite) return Fail("Position must be finite.");
            bool found = TryFindCopy(id, out WorldFile candidate, out WorldObstacle obstacle);
            if (!found) return false;
            obstacle.position = ToArray(position);
            return Apply(candidate, "move", id);
        }

        /// <summary>Rotate about ENU up, preserving the other angles. / ENU 上軸まわりに回す。</summary>
        public bool TryRotate(string id, float yawDeltaDegrees)
        {
            bool finite = !float.IsNaN(yawDeltaDegrees) && !float.IsInfinity(yawDeltaDegrees);
            if (!finite) return Fail("Rotation must be finite.");
            bool found = TryFindCopy(id, out WorldFile candidate, out WorldObstacle obstacle);
            if (!found) return false;
            obstacle.rotation_deg[2] = Mathf.Repeat(Mathf.Repeat(obstacle.rotation_deg[2], FullTurn) + Mathf.Repeat(yawDeltaDegrees, FullTurn), FullTurn);
            return Apply(candidate, "rotate", id);
        }

        /// <summary>Remove the selected id only. / 選択した id だけを取り除く。</summary>
        public bool TryRemove(string id)
        {
            bool found = TryFindCopy(id, out WorldFile candidate, out WorldObstacle obstacle);
            if (!found) return false;
            var obstacles = new List<WorldObstacle>(candidate.obstacles);
            obstacles.Remove(obstacle);
            candidate.obstacles = obstacles.ToArray();
            return Apply(candidate, "remove", id);
        }

        /// <summary>Restore the previous successful edit. / 成功した編集をひとつ戻す。</summary>
        public bool Undo()
        {
            bool available = CanUndo;
            if (!available) return Fail("Nothing to undo.");
            string json = undo[undo.Count - 1];
            applying = true;
            bool restored;
            try { restored = loader.Load(json, "editor.undo"); }
            finally { applying = false; }
            if (!restored) return Fail("Could not restore the previous layout.");
            undo.RemoveAt(undo.Count - 1);
            Complete("undo", string.Empty);
            return true;
        }

        /// <summary>Use the same JSON format as shipped worlds. / 同梱空間と同じ JSON を出力する。</summary>
        public string ExportJson()
        {
            bool available = loader.HasWorld;
            if (!available) { Fail("No world is loaded."); return null; }
            LastError = string.Empty;
            return WorldWriter.Write(loader.Current);
        }

        /// <summary>Reject invalid imports before touching the scene. / 不正な読込は場面を変える前に断る。</summary>
        public bool TryImport(string json)
        {
            WorldReadResult parsed = WorldFileReader.Read(json);
            bool readable = parsed.Ok;
            if (!readable) return Fail(parsed.Reason);
            return Apply(parsed.World, "import", string.Empty);
        }

        /// <summary>Clone data so a refused edit cannot mutate Current. / 拒否時に Current を変えないため複製する。</summary>
        private bool TryCopy(out WorldFile candidate)
        {
            candidate = null;
            bool available = loader.HasWorld;
            if (!available) return Fail("No world is loaded.");
            WorldReadResult parsed = WorldFileReader.Read(WorldWriter.Write(loader.Current));
            bool readable = parsed.Ok;
            if (!readable) return Fail(parsed.Reason);
            candidate = parsed.World;
            return true;
        }

        /// <summary>Find an id in the independent copy. / 独立した複製から id を探す。</summary>
        private bool TryFindCopy(string id, out WorldFile candidate, out WorldObstacle obstacle)
        {
            obstacle = null;
            bool copied = TryCopy(out candidate);
            if (!copied) return false;
            obstacle = Array.Find(candidate.obstacles, item => item.id == id);
            bool found = obstacle != null;
            return found || Fail("Select a furniture item first.");
        }

        /// <summary>Validate, save undo, and publish a complete new world. / 検査後に履歴を保存し空間全体を反映する。</summary>
        private bool Apply(WorldFile candidate, string operation, string id)
        {
            string reason = CheckPlacement(candidate);
            bool valid = reason == null;
            if (!valid) return Fail(reason);
            string json = WorldWriter.Write(candidate);
            WorldReadResult parsed = WorldFileReader.Read(json);
            bool readable = parsed.Ok;
            if (!readable) return Fail(parsed.Reason);
            string before = loader.HasWorld ? WorldWriter.Write(loader.Current) : null;
            applying = true;
            try { loader.Build(parsed.World); }
            finally { applying = false; }
            bool hasPrevious = before != null;
            if (hasPrevious) undo.Add(before);
            bool overflow = undo.Count > MaximumUndoSteps;
            if (overflow) undo.RemoveAt(0);
            Complete(operation, id);
            return true;
        }

        /// <summary>Check room boundaries and the initial takeoff column. / 部屋の境界と初期離陸の余地を検査する。</summary>
        private static string CheckPlacement(WorldFile world)
        {
            bool overLimit = world.obstacles.Length > MaximumObstacles;
            if (overLimit) return $"A layout can contain at most {MaximumObstacles} items.";
            bool validFloor = world.floor.pitch > 0 && !float.IsInfinity(world.floor.pitch);
            bool validYaw = !float.IsNaN(world.spawn.yaw_deg) && !float.IsInfinity(world.spawn.yaw_deg);
            bool validGlobals = validFloor && validYaw;
            if (!validGlobals) return "Floor pitch and spawn heading must be finite and valid.";
            var ids = new HashSet<string>();
            var room = new Bounds(new Vector3(0, world.room.size[2] / 2, 0),
                                  WorldFrames.EnuToUnity(world.room.size));
            Vector3 spawn = WorldFrames.EnuToUnity(world.spawn.position);
            var clearance = new Bounds();
            clearance.SetMinMax(spawn - new Vector3(SpawnHalfWidth, SpawnHalfHeight, SpawnHalfWidth),
                spawn + new Vector3(SpawnHalfWidth, TakeoffClearance, SpawnHalfWidth));
            bool spawnFits = Contains(room, clearance);
            if (!spawnFits) return "The starting position needs room for takeoff.";
            foreach (WorldObstacle obstacle in world.obstacles)
            {
                bool unique = !string.IsNullOrWhiteSpace(obstacle.id) && ids.Add(obstacle.id);
                if (!unique) return "Every item needs a unique, nonempty id.";
                bool invalidSegments = obstacle.type == WorldObstacleTypes.Ring
                    && (obstacle.segments < MinimumRingSegments || obstacle.segments > MaximumRingSegments);
                if (invalidSegments) return "Ring segment count must be between 6 and 128.";
                Bounds bounds = ObstacleBounds(obstacle);
                bool inside = Contains(room, bounds);
                if (!inside) return $"'{obstacle.id}' must fit inside the room.";
                bool blocksSpawn = Overlaps(bounds, clearance);
                if (blocksSpawn) return "Keep the starting position and space above it clear.";
            }
            return null;
        }

        /// <summary>Rotate all local AABB corners using the shared frame conversion. / 共通の座標変換で外形の八隅を回す。</summary>
        public static Bounds ObstacleBounds(WorldObstacle obstacle)
        {
            LocalBounds(obstacle, out Vector3 low, out Vector3 high);
            Quaternion rotation = WorldFrames.EnuRotationToUnity(obstacle.rotation_deg);
            Vector3 origin = WorldFrames.EnuToUnity(obstacle.position);
            var result = new Bounds(origin + rotation * WorldFrames.EnuToUnity(low), Vector3.zero);
            foreach (float east in new[] { low.x, high.x })
            foreach (float north in new[] { low.y, high.y })
            foreach (float up in new[] { low.z, high.z })
                result.Encapsulate(origin + rotation * WorldFrames.EnuToUnity(new Vector3(east, north, up)));
            return result;
        }

        /// <summary>Match the per-type origins in Schemas/README.md. / 仕様の種類別の原点に合わせる。</summary>
        private static void LocalBounds(WorldObstacle obstacle, out Vector3 low, out Vector3 high)
        {
            float x = obstacle.size[0], y = obstacle.size[1], z = obstacle.size[2];
            float thickness = obstacle.thickness;
            low = new Vector3(-x / 2, -y / 2, 0);
            high = new Vector3(x / 2, y / 2, z);
            switch (obstacle.type)
            {
                case WorldObstacleTypes.Gate:
                    low.x -= thickness; high.x += thickness; high.z += thickness;
                    break;
                case WorldObstacleTypes.Tunnel:
                    low = new Vector3(-x / 2 - thickness, 0, 0);
                    high = new Vector3(x / 2 + thickness, y, z + thickness);
                    break;
                case WorldObstacleTypes.Ramp:
                    low.y = 0; high.y = y;
                    break;
                case WorldObstacleTypes.Ring:
                    float radius = x / 2 + thickness;
                    low = new Vector3(-radius, -thickness / 2, -radius);
                    high = new Vector3(radius, thickness / 2, radius);
                    break;
            }
        }

        /// <summary>Allow numerical noise at touching surfaces. / 接面での数値誤差を許容する。</summary>
        private static bool Contains(Bounds outer, Bounds inner)
        {
            Vector3 slack = Vector3.one * BoundsTolerance;
            return outer.Contains(inner.min + slack) && outer.Contains(inner.max - slack);
        }

        /// <summary>Touching a pad below spawn is legitimate. / 出発点の下のパッドとの接面は許容する。</summary>
        private static bool Overlaps(Bounds a, Bounds b) =>
            a.min.x < b.max.x - BoundsTolerance && a.max.x > b.min.x + BoundsTolerance &&
            a.min.y < b.max.y - BoundsTolerance && a.max.y > b.min.y + BoundsTolerance &&
            a.min.z < b.max.z - BoundsTolerance && a.max.z > b.min.z + BoundsTolerance;

        /// <summary>Allocate ids independently of deletion and undo order. / 削除・復元の順序によらず id を割り当てる。</summary>
        private static string NextId(WorldFile world, string type)
        {
            int suffix = 1;
            string id;
            do { id = $"{type}_{suffix++:D3}"; }
            while (Array.Exists(world.obstacles, item => item.id == id));
            return id;
        }

        /// <summary>Reject non-finite positions before JSON can normalize them. / JSON が値を変える前に非有限値を断る。</summary>
        private static bool IsFinite(Vector3 value) =>
            !float.IsNaN(value.x) && !float.IsInfinity(value.x) &&
            !float.IsNaN(value.y) && !float.IsInfinity(value.y) &&
            !float.IsNaN(value.z) && !float.IsInfinity(value.z);

        /// <summary>Store position in file axes. / 位置をファイルの座標系へ格納する。</summary>
        private static float[] ToArray(Vector3 value) => new[] { value.x, value.y, value.z };

        /// <summary>External world changes invalidate history. / 外部から空間が変わったら履歴を破棄する。</summary>
        private void OnWorldChanged()
        {
            bool ownEdit = applying;
            if (ownEdit) return;
            undo.Clear();
            LastError = string.Empty;
            Changed?.Invoke();
        }

        /// <summary>Use the loader's logger to preserve its run and command context. / ローダーのログで実行と命令の文脈を保つ。</summary>
        private void Complete(string operation, string id)
        {
            LastError = string.Empty;
            loader.Log.Write(LogLevel.Info, WorldLogEvents.Source, "world.edited", "edited world",
                new Dictionary<string, object> { { "operation", operation }, { "obstacle_id", id },
                    { "world", loader.Current.name }, { "obstacles", loader.Current.obstacles.Length } });
            Changed?.Invoke();
        }

        /// <summary>Expose a refusal without modifying the world. / 空間を変更せず拒否理由を公開する。</summary>
        private bool Fail(string reason) { LastError = reason; return false; }
    }
}
