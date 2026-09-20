/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — which appearance the vehicle gets).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Vehicle;
using UnityEngine;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// What this guarantees: a running simulator draws the vehicle from the
    /// converted meshes, all thirteen of them, and not from the primitives.
    ///
    /// The EditMode tests check the mesh assets themselves — their size, their
    /// sides, their winding. None of that reaches the screen unless the object
    /// built at run time actually carries them, and the two ways that can fail
    /// are silent: the wrong appearance is chosen, or a renderer is built but
    /// left disabled. This test is played rather than edited because
    /// <c>Awake</c> is what builds the parts.
    ///
    /// 保証すること。動いているシミュレータが機体を、変換したメッシュ 13 個から
    /// 描き、基本形状からは描かないこと。
    ///
    /// EditMode の試験が確かめるのはメッシュ資産そのもの（寸法・向き・巻き）で
    /// ある。実行時に組み立てた物体が実際にそれを持たなければ、そのどれも画面には
    /// 届かない。そして届かなくなる道は 2 つとも黙っている。見た目の選択を誤るか、
    /// 描画側を作ったまま無効にしておくかである。部品を組み立てるのは
    /// <c>Awake</c> なので、この試験は編集時ではなく再生時に行う。
    /// </summary>
    public sealed class VehicleLookTest
    {
        /// <summary>
        /// How many renderers the machine is drawn with: one per part.
        /// 機械を描く描画側の数。部品 1 つにつき 1 つである。
        /// </summary>
        private const int PartsOfTheMachine = 13;

        private GameObject vehicle;

        [SetUp]
        public void SetUp()
        {
            vehicle = new GameObject("Vehicle");
        }

        [TearDown]
        public void TearDown()
        {
            bool isThere = vehicle != null;
            if (isThere)
            {
                Object.DestroyImmediate(vehicle);
            }
        }

        [Test]
        public void TheDefaultAppearanceIsTheConvertedMeshes()
        {
            // No SimLoop is given: the propellers then stand still, which is
            // all this test needs. What is asked is which appearance was built.
            // SimLoop は渡さない。プロペラは止まったままになるが、この試験に要る
            // のはそれだけである。問うのはどの見た目が組まれたかである。
            MonoBehaviour appearance = VehicleLook.Attach(vehicle, null);

            Assert.That(appearance, Is.InstanceOf<MeshAppearance>(),
                        "the vehicle fell back to primitives; the mesh assets are missing, " +
                        "so run StampFly > Meshes > Rebuild Vehicle Meshes");
            Assert.That(((MeshAppearance)appearance).IsComplete, Is.True);
        }

        [Test]
        public void ThirteenRenderersAreBuiltAndEveryOneIsEnabled()
        {
            VehicleLook.Attach(vehicle, null);

            MeshRenderer[] renderers = vehicle.GetComponentsInChildren<MeshRenderer>(true);

            Assert.That(renderers.Length, Is.EqualTo(PartsOfTheMachine));
            foreach (MeshRenderer renderer in renderers)
            {
                Assert.That(renderer.enabled, Is.True,
                            $"{renderer.gameObject.name} would not be drawn");
                Assert.That(renderer.sharedMaterial, Is.Not.Null,
                            $"{renderer.gameObject.name} has no material");
                Assert.That(renderer.GetComponent<MeshFilter>().sharedMesh, Is.Not.Null,
                            $"{renderer.gameObject.name} has no mesh");
            }
        }

        [Test]
        public void TheAppearanceCarriesNoCollider()
        {
            VehicleLook.Attach(vehicle, null);

            // PhysX must keep seeing only the box VehicleBody sets up. A
            // collider added by the picture would change what the simulation
            // collides with, which is the one thing the appearance may not do.
            // PhysX が見るのは VehicleBody が設定する箱だけでなければならない。絵が
            // 足したコライダは、シミュレーションが何とぶつかるかを変えてしまう。
            // 見た目がしてはならない唯一のことがそれである。
            Assert.That(vehicle.GetComponentsInChildren<Collider>(true), Is.Empty);
        }

        [Test]
        public void AskingForPrimitivesStillGivesPrimitives()
        {
            MonoBehaviour appearance = VehicleLook.Attach(vehicle, null, useMeshes: false);

            Assert.That(appearance, Is.InstanceOf<VehicleAppearance>());
        }
    }
}
