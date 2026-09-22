/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kei Nakayama (kexi)
 */
using System;
using NUnit.Framework;
using StampFly.World;
using UnityEngine;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// Edits, persistence, and refusal keep the visible scene and data consistent.
    /// 編集・保存・拒否後にも場面とデータの一致を保証する。
    /// </summary>
    public sealed class WorldEditSessionTest
    {
        private GameObject host;
        private WorldLoader loader;
        private WorldEditSession session;

        [SetUp]
        public void SetUp()
        {
            host = new GameObject("world edit test");
            loader = host.AddComponent<WorldLoader>();
            loader.Build(ShippedWorlds.Read("empty_room").World);
            session = new WorldEditSession(loader);
        }

        [TearDown]
        public void TearDown()
        {
            session.Dispose();
            UnityEngine.Object.DestroyImmediate(host);
        }

        [Test]
        public void AddedMovedRotatedFurnitureSurvivesExportImportWithCollider()
        {
            Assert.That(session.TryAdd("chair", new Vector3(1, 1, 0), out string id), Is.True, session.LastError);
            Assert.That(session.TryMove(id, new Vector3(1.5f, 1, 0)), Is.True, session.LastError);
            Assert.That(session.TryRotate(id, 90), Is.True, session.LastError);
            string saved = session.ExportJson();
            Assert.That(session.TryRemove(id), Is.True);
            Assert.That(loader.Find(id), Is.Null);
            Assert.That(session.TryImport(saved), Is.True, session.LastError);
            WorldObstacle restored = loader.Current.obstacles[0];
            Assert.That(restored.position, Is.EqualTo(new[] { 1.5f, 1f, 0f }));
            Assert.That(restored.rotation_deg[2], Is.EqualTo(90));
            Assert.That(loader.Find(id).GetComponentsInChildren<Collider>(), Is.Not.Empty);
            Assert.That(session.ExportJson(), Is.EqualTo(saved));
        }

        [Test]
        public void RefusedMoveLeavesTheSceneAndUndoHistoryIntact()
        {
            Assert.That(session.TryAdd("table", new Vector3(1, 1, 0), out string id), Is.True);
            string before = session.ExportJson();
            GameObject root = loader.Find(id);
            Assert.That(session.TryMove(id, new Vector3(999, 0, 0)), Is.False);
            Assert.That(session.LastError, Is.Not.Empty);
            Assert.That(loader.Find(id), Is.SameAs(root));
            Assert.That(session.ExportJson(), Is.EqualTo(before));
            Assert.That(session.Undo(), Is.True);
            Assert.That(loader.Current.obstacles, Is.Empty);
        }

        [Test]
        public void SpawnAndInvalidImportsAreRejectedWithoutMutation()
        {
            Vector3 spawn = new Vector3(loader.Current.spawn.position[0], loader.Current.spawn.position[1], 0);
            string before = session.ExportJson();
            Assert.That(session.TryAdd("chair", spawn, out _), Is.False);
            Assert.That(session.TryImport("{}"), Is.False);
            Assert.That(session.TryAdd("missing", Vector3.zero, out _), Is.False);
            Assert.That(session.ExportJson(), Is.EqualTo(before));
            Assert.That(session.CanUndo, Is.False);
        }

        [Test]
        public void DuplicateIdsAndExcessiveItemCountsAreRejected()
        {
            Assert.That(session.TryAdd("chair", new Vector3(1, 1, 0), out _), Is.True);
            WorldFile candidate = WorldFileReader.Read(session.ExportJson()).World;
            WorldObstacle obstacle = candidate.obstacles[0];
            candidate.obstacles = new[] { obstacle, obstacle };
            Assert.That(session.TryImport(WorldWriter.Write(candidate)), Is.False);
            candidate.obstacles = new WorldObstacle[WorldEditSession.MaximumObstacles + 1];
            Array.Fill(candidate.obstacles, obstacle);
            Assert.That(session.TryImport(WorldWriter.Write(candidate)), Is.False);
            Assert.That(loader.Current.obstacles.Length, Is.EqualTo(1));
        }

        [Test]
        public void ExternalLoadAndClearDiscardUndoHistory()
        {
            Assert.That(session.TryAdd("chair", new Vector3(1, 1, 0), out _), Is.True);
            loader.Build(ShippedWorlds.Read("empty_room").World);
            Assert.That(session.CanUndo, Is.False);
            Assert.That(session.TryAdd("chair", new Vector3(1, 1, 0), out _), Is.True);
            loader.Clear();
            Assert.That(session.CanUndo, Is.False);
            Assert.That(session.Undo(), Is.False);
        }

        [Test]
        public void UndoRestoresDeletedFurnitureAndGeneratedIdsStayUnique()
        {
            Assert.That(session.TryAdd("chair", new Vector3(1, 1, 0), out string first), Is.True);
            Assert.That(session.TryAdd("chair", new Vector3(-1, 1, 0), out string second), Is.True);
            Assert.That(first, Is.Not.EqualTo(second));
            Assert.That(session.TryRemove(first), Is.True);
            Assert.That(session.Undo(), Is.True);
            Assert.That(loader.Find(first), Is.Not.Null);
            Assert.That(loader.Find(second), Is.Not.Null);
        }

        [Test]
        [TestCaseSource(typeof(ShippedWorlds), nameof(ShippedWorlds.AllNames))]
        public void ShippedLayoutsCanBeImportedWithoutChanges(string name)
        {
            string json = WorldWriter.Write(ShippedWorlds.Read(name).World);
            Assert.That(session.TryImport(json), Is.True, session.LastError);
            Assert.That(session.ExportJson(), Is.EqualTo(json));
        }

        [Test]
        public void NonFiniteInputsCannotReachTheScene()
        {
            Assert.That(session.TryAdd("chair", new Vector3(1, 1, 0), out string id), Is.True);
            string before = session.ExportJson();
            Assert.That(session.TryMove(id, new Vector3(float.NaN, 0, 0)), Is.False);
            Assert.That(session.TryAdd("chair", new Vector3(0, float.PositiveInfinity, 0), out _), Is.False);
            Assert.That(session.TryRotate(id, float.NegativeInfinity), Is.False);
            Assert.That(session.ExportJson(), Is.EqualTo(before));
        }

        [Test]
        public void UndoKeepsOnlyTheMostRecentTwentyEdits()
        {
            Assert.That(session.TryAdd("chair", new Vector3(1, 1, 0), out string id), Is.True);
            for (int index = 0; index < WorldEditSession.MaximumUndoSteps; index++)
                Assert.That(session.TryRotate(id, 90), Is.True, session.LastError);
            for (int index = 0; index < WorldEditSession.MaximumUndoSteps; index++)
                Assert.That(session.Undo(), Is.True, session.LastError);
            Assert.That(session.CanUndo, Is.False);
            Assert.That(loader.Find(id), Is.Not.Null);
        }

        [Test]
        public void SpawnCentreOnFloorIsRejectedAsInTheStandaloneValidator()
        {
            WorldFile candidate = ShippedWorlds.Read("empty_room").World;
            candidate.spawn.position[2] = 0;
            Assert.That(session.TryImport(WorldWriter.Write(candidate)), Is.False);
        }

        [Test]
        public void RotatedFootprintMustFitInRoom()
        {
            WorldFile candidate = WorldFileReader.Read(session.ExportJson()).World;
            var table = FurnitureCatalog.CreateDefault("table", "near_wall");
            table.size = new[] { 0.4f, 2f, 0.75f };
            table.position = new[] { candidate.room.size[0] / 2 - 0.3f, 0f, 0f };
            candidate.obstacles = new[] { table };
            Assert.That(session.TryImport(WorldWriter.Write(candidate)), Is.True, session.LastError);
            string before = session.ExportJson();
            Assert.That(session.TryRotate(table.id, 90), Is.False);
            Assert.That(session.ExportJson(), Is.EqualTo(before));
        }
    }
}
