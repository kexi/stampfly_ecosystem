/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kei Nakayama (kexi)
 */
using System.Collections.Generic;
using NUnit.Framework;
using StampFly.World;
using UnityEngine;

namespace StampFly.Tests.EditMode
{
    /// <summary>Restart restores all pins without rewriting saved layout or edit history. / やり直しで保存配置や編集履歴を変えず全ピンを復元することを保証する。</summary>
    public sealed class BowlingResetTest
    {
        private GameObject host;
        private WorldLoader loader;

        /// <summary>Load the shipped bowling room without starting firmware. / ファームを起動せず同梱のボーリング部屋を読む。</summary>
        [SetUp]
        public void SetUp()
        {
            host = new GameObject("Bowling reset test");
            loader = host.AddComponent<WorldLoader>();
            Assert.That(loader.Load(ShippedWorlds.Json("bowling")), Is.True);
        }

        /// <summary>Remove physics objects after every assertion. / 各試験の後に物理物体を取り除く。</summary>
        [TearDown]
        public void TearDown() => Object.DestroyImmediate(host);

        /// <summary>Every displaced, spinning pin returns upright and stationary. / 移動して回転する全ピンが元の直立静止状態に戻る。</summary>
        [Test]
        public void ResetRestoresEveryPoseAndClearsLinearAndAngularVelocity()
        {
            DynamicObstacleBody[] pins = host.GetComponentsInChildren<DynamicObstacleBody>();
            Assert.That(pins.Length, Is.EqualTo(10));
            var originalPositions = new List<Vector3>();
            var originalRotations = new List<Quaternion>();
            string originalLayout = WorldWriter.Write(loader.Current);
            foreach (DynamicObstacleBody pin in pins)
            {
                Rigidbody body = pin.GetComponent<Rigidbody>();
                originalPositions.Add(body.position);
                originalRotations.Add(body.rotation);
                body.position += new Vector3(0.4f, 0.1f, -0.3f);
                body.rotation = Quaternion.Euler(80, 25, 15);
                body.linearVelocity = Vector3.right;
                body.angularVelocity = Vector3.forward;
            }
            Physics.SyncTransforms();
            loader.ResetDynamicBodies();
            for (int index = 0; index < pins.Length; index++)
            {
                Rigidbody body = pins[index].GetComponent<Rigidbody>();
                Assert.That(Vector3.Distance(body.position, originalPositions[index]), Is.LessThan(0.0001f));
                Assert.That(Quaternion.Angle(body.rotation, originalRotations[index]), Is.LessThan(0.001f));
                Assert.That(body.linearVelocity, Is.EqualTo(Vector3.zero));
                Assert.That(body.angularVelocity, Is.EqualTo(Vector3.zero));
            }
            Assert.That(WorldWriter.Write(loader.Current), Is.EqualTo(originalLayout));
        }

        /// <summary>Reset is not a world reload, so undo and existing objects survive. / やり直しは空間の再読込ではなく、Undoと既存物体を維持する。</summary>
        [Test]
        public void ResetKeepsUndoHistoryAndDoesNotRebuildWorldObjects()
        {
            using (var edits = new WorldEditSession(loader))
            {
                Assert.That(edits.TryImport(ShippedWorlds.Json("bowling")), Is.True, edits.LastError);
                Assert.That(edits.CanUndo, Is.True);
                GameObject pin = loader.Find("pin_01");
                int notifications = 0;
                loader.Changed += () => notifications++;
                loader.ResetDynamicBodies();
                Assert.That(edits.CanUndo, Is.True);
                Assert.That(loader.Find("pin_01"), Is.SameAs(pin));
                Assert.That(notifications, Is.Zero);
            }
        }

        /// <summary>Saving a fallen pin preserves the authored starting arrangement. / 倒れたピンを保存しても初期配置を維持する。</summary>
        [Test]
        public void SavingFallenPinsPreservesTheInitialLayout()
        {
            string initial = WorldWriter.Write(loader.Current);
            GameObject pin = loader.Find("pin_01");
            pin.transform.localPosition += Vector3.right;
            pin.transform.localRotation = Quaternion.Euler(90, 0, 0);
            WorldFile saved = WorldWriter.FromScene(loader.Current, pin.transform.parent);
            Assert.That(WorldWriter.Write(saved), Is.EqualTo(initial));
        }

        /// <summary>A cleared world can be restarted without reconstructing it. / 空間を除去した後のやり直しも何も再生成せず成功する。</summary>
        [Test]
        public void ResetAfterClearIsHarmless()
        {
            loader.Clear();
            Assert.DoesNotThrow(loader.ResetDynamicBodies);
            Assert.That(loader.HasWorld, Is.False);
        }
    }
}
