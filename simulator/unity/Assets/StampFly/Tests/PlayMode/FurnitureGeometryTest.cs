// Copyright (c) 2026 Kei Nakayama (kexi). MIT License.
using NUnit.Framework;
using StampFly.World;
using UnityEngine;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// Furniture dimensions, sensor surfaces and openings agree. / 家具寸法・センサ面・隙間の一致を保証する。
    /// @design docs/plans/unity-simulator.md §4 [OK]
    /// </summary>
    public sealed class FurnitureGeometryTest
    {
        private GameObject host;
        private WorldMaterials materials;

        [SetUp]
        public void SetUp()
        {
            host = new GameObject("FurnitureTest");
            materials = new WorldMaterials();
        }

        [TearDown]
        public void TearDown()
        {
            Object.DestroyImmediate(host);
            materials.Dispose();
        }

        [TestCase("table")]
        [TestCase("chair")]
        [TestCase("sofa")]
        [TestCase("shelf")]
        [TestCase("bed")]
        public void PartsMatchDeclaredExtentAndCarrySurfaceIdentity(string type)
        {
            WorldObstacle obstacle = FurnitureCatalog.CreateDefault(type, "furniture_test");
            GameObject furniture = ObstacleFactory.Create(obstacle, host.transform, materials);
            Physics.SyncTransforms();
            Collider[] parts = furniture.GetComponentsInChildren<Collider>();
            Assert.That(parts.Length, Is.GreaterThan(1));
            Assert.That(furniture.GetComponent<Collider>(), Is.Null);
            Bounds bounds = parts[0].bounds;
            foreach (Collider part in parts)
            {
                bounds.Encapsulate(part.bounds);
                Assert.That(part.GetComponent<ObstacleInfo>(), Is.Not.Null);
                Assert.That(part.GetComponent<Renderer>().sharedMaterial, Is.SameAs(materials.Solid(obstacle.color)));
            }
            Vector3 expected = new Vector3(obstacle.size[0], obstacle.size[2], obstacle.size[1]);
            Assert.That(Vector3.Distance(bounds.size, expected), Is.LessThan(0.001f));
            Assert.That(Vector3.Distance(bounds.center, Vector3.up * expected.y / 2), Is.LessThan(0.001f));
        }

        [TestCase("table", 0.2f)]
        [TestCase("chair", 0.2f)]
        [TestCase("sofa", 0.1f)]
        [TestCase("bed", 0.1f)]
        public void RaysPassBetweenLegs(string type, float relativeHeight)
        {
            WorldObstacle obstacle = FurnitureCatalog.CreateDefault(type, "legs_test");
            ObstacleFactory.Create(obstacle, host.transform, materials);
            Physics.SyncTransforms();
            Vector3 start = new Vector3(0, obstacle.size[2] * relativeHeight, -obstacle.size[1]);
            Assert.That(Physics.Raycast(start, Vector3.forward, obstacle.size[1] * 2), Is.False);
        }

        [Test]
        public void ShelfOpeningIsEmptyAndRayStopsAtBackPanel()
        {
            WorldObstacle obstacle = FurnitureCatalog.CreateDefault("shelf", "shelf_test");
            ObstacleFactory.Create(obstacle, host.transform, materials);
            Physics.SyncTransforms();
            Vector3 cavity = new Vector3(0, obstacle.size[2] * 0.18f, 0);
            Assert.That(Physics.CheckSphere(cavity, 0.02f), Is.False);
            Assert.That(Physics.Raycast(cavity, Vector3.forward, out RaycastHit hit, obstacle.size[1]), Is.True);
            Assert.That(hit.collider.name, Is.EqualTo("back"));
        }
    }
}
