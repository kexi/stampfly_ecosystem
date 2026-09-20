/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — PhysX stepping settings).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine;

namespace StampFly.Sim
{
    /// <summary>
    /// The global PhysX settings the simulator needs, applied in one place.
    /// The loop drives <c>Physics.Simulate</c> itself, so Unity must not step
    /// physics on its own.
    ///
    /// シミュレータが要る PhysX の全体設定を 1 か所で適用する。刻みは
    /// <c>Physics.Simulate</c> を自前で呼ぶため、Unity には physics を自動で
    /// 進めさせない。
    /// </summary>
    public static class PhysicsStepSettings
    {
        // One control period: 400 Hz, matching the firmware's control task.
        // 制御周期 1 つぶん。400 Hz で、ファームウェアの制御タスクと同じ。
        public const float StepSeconds = 0.0025f;

        // The default contact offset (0.01 m) is the same order as the vehicle's
        // half thickness (0.0103 m), which would float the box off the floor by
        // nearly its own thickness. A tenth of that keeps the offset well under
        // the geometry it acts on.
        // 既定の接触オフセット（0.01 m）は機体の半分の厚み（0.0103 m）と同じ桁で、
        // 箱が厚み 1 つぶん近く浮く。10 分の 1 にして形状より十分小さくする。
        public const float ContactOffsetMeters = 0.001f;

        // Below this relative speed a contact does not bounce. Raised above any
        // speed a 37 g body reaches on landing so the vehicle settles instead of
        // chattering.
        // この相対速度より遅い接触は跳ねない。37 g の機体が着地で出す速度より
        // 大きくして、跳ね続けずに静止させる。
        public const float BounceThresholdMetersPerSecond = 20.0f;

        /// <summary>
        /// Applies the settings. Enhanced Determinism is not settable at runtime;
        /// it lives in ProjectSettings/DynamicsManager.asset and is already on.
        /// 設定を適用する。Enhanced Determinism は実行中に変えられないため
        /// ProjectSettings/DynamicsManager.asset で有効にしてある。
        /// </summary>
        public static void Apply()
        {
            Physics.simulationMode = SimulationMode.Script;
            Physics.defaultContactOffset = ContactOffsetMeters;
            Physics.defaultMaxDepenetrationVelocity = 1.0f;
            Physics.bounceThreshold = BounceThresholdMetersPerSecond;
            Physics.defaultSolverIterations = 12;
            Physics.defaultSolverVelocityIterations = 4;
            Physics.invokeCollisionCallbacks = false;
        }
    }
}
