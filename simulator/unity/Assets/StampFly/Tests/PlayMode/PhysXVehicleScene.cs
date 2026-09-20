/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — PlayMode test scaffolding).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using StampFly.Sim;
using UnityEngine;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// Builds and tears down the minimal scene the PhysX checks need: one vehicle
    /// rigid body, optionally a floor. It steps physics by hand so a check runs to
    /// completion inside a single frame.
    ///
    /// PhysX の検証に要る最小の場面（機体の剛体 1 つ、必要なら床）を作って片づける。
    /// physics は自前で進めるので、1 フレームの中で検証を走らせきれる。
    /// </summary>
    public sealed class PhysXVehicleScene
    {
        // A floor far wider than any trajectory these checks produce.
        // どの検証の軌跡より十分広い床。
        private const float FloorSizeMeters = 10.0f;
        private const float FloorThicknessMeters = 0.5f;

        public Rigidbody Body { get; private set; }
        public BoxCollider Box { get; private set; }

        private GameObject vehicleObject;
        private GameObject floorObject;

        /// <summary>
        /// Creates the scene. <paramref name="withFloor"/> adds a static box whose
        /// top face sits at y = 0. <paramref name="useGravity"/> turns gravity off
        /// for the free-rotation and torque checks.
        /// 場面を作る。<paramref name="withFloor"/> で上面が y = 0 の静的な箱を置き、
        /// <paramref name="useGravity"/> で自由回転・トルクの検証では重力を切る。
        /// </summary>
        public void Build(Vector3 startPosition, bool withFloor, bool useGravity)
        {
            PhysicsStepSettings.Apply();

            vehicleObject = new GameObject("Vehicle");
            vehicleObject.transform.position = startPosition;
            Box = vehicleObject.AddComponent<BoxCollider>();
            Body = vehicleObject.AddComponent<Rigidbody>();
            VehicleBody.Apply(Body, Box);
            Body.useGravity = useGravity;

            if (withFloor)
            {
                floorObject = GameObject.CreatePrimitive(PrimitiveType.Cube);
                floorObject.name = "Floor";
                floorObject.transform.position =
                    new Vector3(0.0f, -0.5f * FloorThicknessMeters, 0.0f);
                floorObject.transform.localScale =
                    new Vector3(FloorSizeMeters, FloorThicknessMeters, FloorSizeMeters);
            }
        }

        /// <summary>Advances physics by one control period. / 制御周期 1 つ進める。</summary>
        public void Step()
        {
            Physics.Simulate(PhysicsStepSettings.StepSeconds);
        }

        /// <summary>Advances physics by a given step length. / 指定の刻みで進める。</summary>
        public void Step(float stepSeconds)
        {
            Physics.Simulate(stepSeconds);
        }

        /// <summary>Removes everything this scene created. / 作ったものを全て片づける。</summary>
        public void Dispose()
        {
            if (vehicleObject != null)
            {
                Object.DestroyImmediate(vehicleObject);
            }

            if (floorObject != null)
            {
                Object.DestroyImmediate(floorObject);
            }

            Physics.simulationMode = SimulationMode.FixedUpdate;
        }
    }
}
