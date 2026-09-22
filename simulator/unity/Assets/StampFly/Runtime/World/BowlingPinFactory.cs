/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kei Nakayama (kexi)
 */
using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// A lightweight pin tips through ordinary rigid-body contact with the drone.
    /// 軽量なピンがドローンとの通常の剛体接触によって倒れる。
    /// @design docs/plans/unity-simulator.md §4 [OK]
    /// </summary>
    public static class BowlingPinFactory
    {
        public const float MassKilograms = 0.012f;
        public const float DefaultHeight = 0.18f;
        public const float DefaultWidth = 0.05f;
        private const float FootHeightFraction = 0.06f;
        private const float BodyHeightFraction = 0.70f;
        private const float BodyBottomFraction = 0.03f;
        private const float HeadDiameterFraction = 0.55f;
        private const float NeckDiameterFraction = 0.32f;
        private const float NeckHeightFraction = 0.30f;
        private const float NeckBottomFraction = 0.62f;
        private const float ContactOffsetMeters = 0.001f;
        private const float ContactOffsetSizeFraction = 0.02f;
        private const float AngularDamping = 0.05f;
        private const int SolverIterations = 12;
        private const int SolverVelocityIterations = 4;

        /// <summary>Assemble colliders first so PhysX derives inertia from the complete shape. / 完成した形状から慣性を計算するため先にコライダを組む。</summary>
        public static void Build(WorldObstacle obstacle, Transform root, Material material, WorldMaterials materials)
        {
            float width = Mathf.Min(obstacle.size[0], obstacle.size[1]);
            float height = obstacle.size[2];
            float bodyHeight = height * BodyHeightFraction;
            float bodyDiameter = Mathf.Min(width, bodyHeight);
            float headDiameter = Mathf.Min(width * HeadDiameterFraction, height * HeadDiameterFraction);
            float neckHeight = height * NeckHeightFraction;
            AddConvexCylinder("foot", new Vector3(obstacle.size[0], height * FootHeightFraction, obstacle.size[1]),
                height * FootHeightFraction / 2, obstacle, root, material);
            AddPrimitive("body", PrimitiveType.Capsule, new Vector3(bodyDiameter, bodyHeight / 2, bodyDiameter),
                height * BodyBottomFraction + bodyHeight / 2, obstacle, root, material);
            AddPrimitive("head", PrimitiveType.Sphere, Vector3.one * headDiameter,
                height - headDiameter / 2, obstacle, root, material);
            AddConvexCylinder("neck", new Vector3(width * NeckDiameterFraction, neckHeight, width * NeckDiameterFraction),
                height * NeckBottomFraction + neckHeight / 2, obstacle, root, materials.Solid("#ce3535"));
            Rigidbody body = root.gameObject.AddComponent<Rigidbody>();
            body.mass = MassKilograms;
            body.useGravity = true;
            body.isKinematic = false;
            body.collisionDetectionMode = CollisionDetectionMode.ContinuousDynamic;
            body.angularDamping = AngularDamping;
            body.solverIterations = SolverIterations;
            body.solverVelocityIterations = SolverVelocityIterations;
            root.gameObject.AddComponent<DynamicObstacleBody>().Initialize();
        }

        /// <summary>Use a flat convex cylinder; Unity's default cylinder collider has a rounded bottom. / Unity既定の円柱コライダは底が丸いため平底の凸円柱を使う。</summary>
        private static void AddConvexCylinder(string name, Vector3 size, float centreHeight,
            WorldObstacle obstacle, Transform root, Material material)
        {
            GameObject source = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            Mesh mesh = source.GetComponent<MeshFilter>().sharedMesh;
            source.SetActive(false);
            bool playing = Application.isPlaying;
            if (playing) Object.Destroy(source);
            else Object.DestroyImmediate(source);
            GameObject part = new GameObject(name);
            part.AddComponent<MeshFilter>().sharedMesh = mesh;
            part.AddComponent<MeshRenderer>();
            MeshCollider flat = part.AddComponent<MeshCollider>();
            flat.sharedMesh = mesh;
            flat.convex = true;
            Finish(part, name, new Vector3(size.x, size.y / 2, size.z), centreHeight, obstacle, root, material);
        }

        /// <summary>The primitive sphere is diameter one and the capsule is height two. / 基本球は直径1、カプセルは高さ2で生成される。</summary>
        private static void AddPrimitive(string name, PrimitiveType shape, Vector3 scale, float centreHeight,
            WorldObstacle obstacle, Transform root, Material material)
        {
            GameObject part = GameObject.CreatePrimitive(shape);
            // Explicit component references keep native colliders in stripped WebGL builds.
            // 型を明示して参照し、WebGLの不要コード除去で当たり判定が消えるのを防ぐ。
            bool capsule = shape == PrimitiveType.Capsule;
            if (capsule)
            {
                bool missing = part.GetComponent<CapsuleCollider>() == null;
                if (missing) part.AddComponent<CapsuleCollider>();
            }
            else
            {
                bool missing = part.GetComponent<SphereCollider>() == null;
                if (missing) part.AddComponent<SphereCollider>();
            }
            Finish(part, name, scale, centreHeight, obstacle, root, material);
        }

        /// <summary>Give every hit surface its normal world metadata. / 衝突する各面へ通常の空間メタデータを付ける。</summary>
        private static void Finish(GameObject part, string name, Vector3 scale, float centreHeight,
            WorldObstacle obstacle, Transform root, Material material)
        {
            part.name = name;
            part.transform.SetParent(root, false);
            part.transform.localPosition = Vector3.up * centreHeight;
            part.transform.localScale = scale;
            part.GetComponent<MeshRenderer>().sharedMaterial = material;
            foreach (Collider shape in part.GetComponents<Collider>())
            {
                bool active = shape.enabled;
                if (!active) continue;
                shape.contactOffset = Mathf.Min(ContactOffsetMeters, Mathf.Min(obstacle.size[0], obstacle.size[1]) * ContactOffsetSizeFraction);
            }
            ObstacleInfo.Attach(part, obstacle.id, obstacle.type, obstacle.flow_quality);
        }
    }
}
