/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kei Nakayama (kexi)
 */
using NUnit.Framework;
using StampFly.Sim;
using StampFly.World;
using UnityEngine;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// Pins stand under gravity and tip from a drone-sized body's physical impact.
    /// ピンが重力下で直立し、機体相当の剛体衝突によって倒れることを保証する。
    /// </summary>
    public sealed class BowlingPinPhysicsTest
    {
        private const float TickSeconds = 1.0f / 400;
        private const int SettlingTicks = 400;
        private const int ObservationTicks = 1200;
        private const float DroneMass = 0.037f;
        private const float DroneWidth = 0.0816f;
        private const float DroneHeight = 0.0206f;
        private const float ImpactHeight = 0.105f;
        private GameObject host;
        private WorldMaterials materials;
        private SimulationMode previousSimulation;

        [SetUp]
        public void SetUp()
        {
            previousSimulation = Physics.simulationMode;
            PhysicsStepSettings.Apply();
            host = new GameObject("bowling physics test");
            materials = new WorldMaterials();
            var floor = GameObject.CreatePrimitive(PrimitiveType.Cube);
            floor.transform.SetParent(host.transform);
            floor.transform.position = new Vector3(0, -0.025f, 0);
            floor.transform.localScale = new Vector3(10, 0.05f, 10);
        }

        [TearDown]
        public void TearDown()
        {
            Object.DestroyImmediate(host);
            materials.Dispose();
            Physics.simulationMode = previousSimulation;
        }

        [Test]
        public void UntouchedPinRemainsUprightAndNearlyStationary()
        {
            GameObject pin = AddPin();
            Physics.SyncTransforms();
            float maximumTilt = Simulate(pin, ObservationTicks);
            Rigidbody body = pin.GetComponent<Rigidbody>();
            TestContext.WriteLine($"no impact: max tilt={maximumTilt:F4} deg, drift={pin.transform.position.magnitude:F5} m, speed={body.linearVelocity.magnitude:F5} m/s");
            Assert.That(maximumTilt, Is.LessThan(2), "A flat foot must keep the untouched pin standing.");
            Assert.That(pin.transform.position.magnitude, Is.LessThan(0.01f));
            Assert.That(body.linearVelocity.magnitude, Is.LessThan(0.01f));
        }

        [TestCase(0.5f)]
        [TestCase(1.0f)]
        public void DroneSizedImpactPhysicallyTipsThePin(float speed)
        {
            GameObject pin = AddPin();
            Physics.SyncTransforms();
            Simulate(pin, SettlingTicks);
            var drone = new GameObject("drone equivalent");
            drone.transform.SetParent(host.transform);
            drone.transform.position = new Vector3(-0.16f, ImpactHeight, 0);
            BoxCollider collider = drone.AddComponent<BoxCollider>();
            collider.size = new Vector3(DroneWidth, DroneHeight, DroneWidth);
            collider.contactOffset = 0.001f;
            Rigidbody body = drone.AddComponent<Rigidbody>();
            body.mass = DroneMass;
            body.useGravity = false;
            body.collisionDetectionMode = CollisionDetectionMode.ContinuousDynamic;
            body.linearVelocity = Vector3.right * speed;
            Physics.SyncTransforms();
            float maximumTilt = Simulate(pin, ObservationTicks);
            float finalTilt = Vector3.Angle(pin.transform.up, Vector3.up);
            TestContext.WriteLine($"impact {speed:F1} m/s: max tilt={maximumTilt:F3} deg, final tilt={finalTilt:F3} deg, displacement={pin.transform.position.magnitude:F4} m");
            Assert.That(maximumTilt, Is.GreaterThan(60), "Rigid-body contact alone must topple the pin.");
            Assert.That(finalTilt, Is.GreaterThan(60), "The pin must remain down after impact.");
        }

        [TestCase(0.05f, 0.05f, 0.18f)]
        [TestCase(0.08f, 0.04f, 0.18f)]
        public void ColliderBoundsMatchTheDeclaredInitialSize(float east, float north, float up)
        {
            GameObject pin = AddPin(new[] { east, north, up });
            Physics.SyncTransforms();
            Collider[] colliders = pin.GetComponentsInChildren<Collider>();
            Bounds combined = colliders[0].bounds;
            foreach (Collider collider in colliders)
            {
                combined.Encapsulate(collider.bounds);
                Assert.That(collider.GetComponent<ObstacleInfo>(), Is.Not.Null);
            }
            Assert.That(combined.min.x, Is.EqualTo(-east / 2).Within(0.0001f));
            Assert.That(combined.max.x, Is.EqualTo(east / 2).Within(0.0001f));
            Assert.That(combined.min.z, Is.EqualTo(-north / 2).Within(0.0001f));
            Assert.That(combined.max.z, Is.EqualTo(north / 2).Within(0.0001f));
            Assert.That(combined.min.y, Is.EqualTo(0).Within(0.0001f));
            Assert.That(combined.max.y, Is.EqualTo(up).Within(0.0001f));
        }

        [Test]
        public void StaticObstacleKindsNeverAcquireRigidBodies()
        {
            foreach (string type in WorldObstacleTypes.All)
            {
                bool dynamic = type == WorldObstacleTypes.BowlingPin;
                if (dynamic) continue;
                WorldObstacle obstacle = Description();
                obstacle.type = type;
                obstacle.thickness = 0.01f;
                obstacle.segments = 12;
                GameObject item = ObstacleFactory.Create(obstacle, host.transform, materials);
                Assert.That(item.GetComponentInChildren<Rigidbody>(), Is.Null, type);
            }
        }

        /// <summary>Observe maximum tilt at the actual simulation cadence. / 実際のシミュレーション周期で最大傾斜を観測する。</summary>
        private static float Simulate(GameObject pin, int ticks)
        {
            float maximum = 0;
            for (int tick = 0; tick < ticks; tick++)
            {
                Physics.Simulate(TickSeconds);
                maximum = Mathf.Max(maximum, Vector3.Angle(pin.transform.up, Vector3.up));
            }
            return maximum;
        }

        /// <summary>Create the same type dispatched by world loading. / 空間読み込みと同じ種類別生成を使う。</summary>
        private GameObject AddPin(float[] size = null)
        {
            WorldObstacle obstacle = Description();
            bool customSize = size != null;
            if (customSize) obstacle.size = size;
            return ObstacleFactory.Create(obstacle, host.transform, materials);
        }

        /// <summary>Use the documented lightweight pin dimensions. / 文書で定めた軽量ピンの寸法を使う。</summary>
        private static WorldObstacle Description() => new WorldObstacle
        {
            id = "pin_test", type = WorldObstacleTypes.BowlingPin,
            position = new[] { 0f, 0f, 0f }, rotation_deg = new[] { 0f, 0f, 0f },
            size = new[] { BowlingPinFactory.DefaultWidth, BowlingPinFactory.DefaultWidth, BowlingPinFactory.DefaultHeight },
            color = "#ffffff", flow_quality = 0.8f,
        };
    }
}
