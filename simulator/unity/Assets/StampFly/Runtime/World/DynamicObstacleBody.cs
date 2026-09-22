/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kei Nakayama (kexi)
 */
using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// Retain the layout pose independently of simulated rigid-body motion.
    /// 剛体のシミュレーション状態から独立して初期配置を保持する。
    /// @design docs/plans/unity-simulator.md §4 [OK]
    /// </summary>
    [RequireComponent(typeof(Rigidbody))]
    public sealed class DynamicObstacleBody : MonoBehaviour
    {
        private Rigidbody body;
        private Vector3 initialPosition;
        private Quaternion initialRotation;

        /// <summary>Capture the local pose after the factory places the root. / 工場が配置した根の局所姿勢を記録する。</summary>
        public void Initialize()
        {
            body = GetComponent<Rigidbody>();
            initialPosition = transform.localPosition;
            initialRotation = transform.localRotation;
        }

        /// <summary>Restore the initial pose and remove residual momentum. / 初期姿勢へ戻し残った運動量を除く。</summary>
        public void ResetPose()
        {
            bool hasParent = transform.parent != null;
            body.position = hasParent ? transform.parent.TransformPoint(initialPosition) : initialPosition;
            body.rotation = hasParent ? transform.parent.rotation * initialRotation : initialRotation;
            body.linearVelocity = Vector3.zero;
            body.angularVelocity = Vector3.zero;
            body.Sleep();
        }
    }
}
