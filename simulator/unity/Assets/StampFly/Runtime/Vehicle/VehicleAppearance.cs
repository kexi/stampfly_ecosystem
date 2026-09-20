/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — how the vehicle looks).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using StampFly.Core;
using StampFly.Sim;
using UnityEngine;

namespace StampFly.Vehicle
{
    /// <summary>
    /// The vehicle's appearance, built from primitives: a plate for the frame,
    /// four motor cans, and four generated three-blade propellers that turn at
    /// the speeds the firmware reports.
    ///
    /// 機体の見た目を基本形状で作る。機体の板、モータ缶 4 つ、そして生成した
    /// 3 枚羽根のプロペラ 4 つで、プロペラはファームが報告する角速度で回る。
    ///
    /// Everything here is appearance only — it carries no collider, so PhysX
    /// still sees only the box `VehicleBody` set up. That is what makes the
    /// appearance swappable: a later commit can replace these parts with
    /// imported meshes without touching the physics.
    ///
    /// ここに在るのは見た目だけで、コライダは持たない。PhysX が見るのは
    /// `VehicleBody` が設定した箱だけのままである。見た目を差し替えられるのは
    /// このためで、後のコミットはこれらを取り込んだメッシュに替えても物理に触れずに済む。
    ///
    /// ## Motor layout / モータの並び
    ///
    /// An X-quad. The offsets come from `StampFly.Sim.GeneratedParams`, which the
    /// repository generates from `control/models/stampfly_physical.yaml` — the same
    /// file `simulator/sils/models/stampfly.xml`'s rotor sites are checked against,
    /// so the picture and the physics place the rotors alike. The turn directions
    /// alternate so the reaction torques cancel in a hover.
    ///
    /// X 型の 4 発。位置は `StampFly.Sim.GeneratedParams` から来る。同クラスは
    /// `control/models/stampfly_physical.yaml` から生成され、
    /// `simulator/sils/models/stampfly.xml` のロータ site も同じファイルに対して
    /// 照合されるので、絵と物理は同じ場所にロータを置く。回転方向は交互で、
    /// ホバリング時に反トルクが打ち消し合う。
    ///
    /// | Motor | Place | FLU | Unity (x right, y up, z forward) | Turn |
    /// |---|---|---|---|---|
    /// | M1 | front right | (+0.023, -0.023) | (+0.023, ·, +0.023) | CCW |
    /// | M2 | rear right | (-0.023, -0.023) | (+0.023, ·, -0.023) | CW |
    /// | M3 | rear left | (-0.023, +0.023) | (-0.023, ·, -0.023) | CCW |
    /// | M4 | front left | (+0.023, +0.023) | (-0.023, ·, +0.023) | CW |
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（機体の見た目）
    /// </summary>
    public sealed class VehicleAppearance : MonoBehaviour
    {
        // The rotor places come from the generated parameters, so the picture
        // stands where the physics acts: the same two numbers position the
        // MuJoCo model's rotor sites. Editing them means editing
        // control/models/stampfly_physical.yaml and running `sf params generate`.
        // ロータの位置は生成されたパラメータから来るので、絵は物理が働く場所に
        // 立つ。MuJoCo モデルのロータ site を置くのも同じ 2 つの数値である。
        // 変えるには control/models/stampfly_physical.yaml を編集して
        // `sf params generate` を実行する。

        /// <summary>Half the distance between diagonal rotors, along one axis [m]. / 対角のロータの間隔の半分。1 軸あたり [m]。</summary>
        public const float RotorOffsetMeters = GeneratedParams.RotorOffsetMeters;

        /// <summary>How far above the body's centre a rotor sits [m]. / ロータが機体の中心より上に在る高さ [m]。</summary>
        public const float RotorHeightMeters = GeneratedParams.RotorHeightMeters;

        /// <summary>The frame plate's size [m]: 82 mm square, 3 mm thick. / 機体の板の寸法 [m]。82 mm 角で厚さ 3 mm。</summary>
        public static readonly Vector3 PlateSize = new Vector3(0.082f, 0.003f, 0.082f);

        /// <summary>A motor can's diameter and height [m]. / モータ缶の直径と高さ [m]。</summary>
        public const float MotorDiameterMeters = 0.0082f;
        public const float MotorHeightMeters = 0.007f;

        /// <summary>
        /// Above this speed a propeller is drawn at a fixed rate instead of its
        /// true one. A 2000 rad/s prop passes a blade every 1 ms, far faster than
        /// a 60 fps frame can show, so drawing the true angle produces a
        /// meaningless stutter. Slowing it keeps the picture readable while the
        /// number on the panel stays true.
        /// この角速度を越えたプロペラは、真の値ではなく一定の速さで描く。2000 rad/s
        /// のプロペラは 1 ms ごとに羽根が 1 枚通り、60fps の 1 フレームで見せられる
        /// より遥かに速い。真の角度で描けば意味の無いちらつきになる。遅くして絵を
        /// 読めるようにするが、表示板の数値は真のままである。
        /// </summary>
        public const float VisualSpeedCapRadPerSecond = 40.0f;

        // Which way each motor turns about the body's up axis, M1..M4.
        // 機体の上方向のまわりに各モータが回る向き。M1〜M4。
        private static readonly float[] TurnDirections = { 1.0f, -1.0f, 1.0f, -1.0f };

        [Tooltip("The loop whose motor speeds drive the propellers. " +
                 "プロペラを回す元になる、モータ角速度を持つループ。")]
        public SimLoop simLoop;

        private readonly Transform[] propellers = new Transform[4];
        private readonly float[] propellerAngles = new float[4];

        private void Awake()
        {
            BuildPlate();
            BuildRotors();
        }

        private void Update()
        {
            bool hasLoop = simLoop != null;
            if (!hasLoop)
            {
                return;
            }

            SpinPropellers(simLoop.LastResult, Time.unscaledDeltaTime);
        }

        /// <summary>
        /// Turn each propeller by what its motor did this frame. Called from
        /// <c>Update</c> with the frame's own elapsed time, not per firmware
        /// tick: this is appearance, and a picture only needs to be redrawn once
        /// a frame.
        /// 各プロペラを、このフレームで対応するモータが回したぶん回す。ファームの
        /// 刻みごとではなく、フレームの経過時間とともに <c>Update</c> から呼ぶ。
        /// これは見た目であり、絵は 1 フレームに 1 回描き直せば足りるからである。
        /// </summary>
        public void SpinPropellers(in Native.SfuStepOut state, float elapsedSeconds)
        {
            var speeds = new[]
            {
                state.MotorOmega0, state.MotorOmega1,
                state.MotorOmega2, state.MotorOmega3,
            };

            for (int motor = 0; motor < propellers.Length; motor++)
            {
                Transform propeller = propellers[motor];
                bool isMissing = propeller == null;
                if (isMissing)
                {
                    continue;
                }

                float shown = Mathf.Min(
                    Mathf.Abs(speeds[motor]), VisualSpeedCapRadPerSecond);
                propellerAngles[motor] +=
                    TurnDirections[motor] * shown * Mathf.Rad2Deg * elapsedSeconds;
                propeller.localRotation =
                    Quaternion.Euler(0.0f, propellerAngles[motor], 0.0f);
            }
        }

        /// <summary>The flat frame the electronics sit on. / 電子部品が載る平らな機体の板。</summary>
        private void BuildPlate()
        {
            GameObject plate = GameObject.CreatePrimitive(PrimitiveType.Cube);
            plate.name = "Frame";
            plate.transform.SetParent(transform, false);
            plate.transform.localScale = PlateSize;
            StripCollider(plate);
            Paint(plate, new Color(0.16f, 0.17f, 0.20f));
        }

        /// <summary>The four motor cans and the propellers above them. / モータ缶 4 つと、その上のプロペラ。</summary>
        private void BuildRotors()
        {
            var places = new[]
            {
                new Vector3(RotorOffsetMeters, RotorHeightMeters, RotorOffsetMeters),    // M1 front right
                new Vector3(RotorOffsetMeters, RotorHeightMeters, -RotorOffsetMeters),   // M2 rear right
                new Vector3(-RotorOffsetMeters, RotorHeightMeters, -RotorOffsetMeters),  // M3 rear left
                new Vector3(-RotorOffsetMeters, RotorHeightMeters, RotorOffsetMeters),   // M4 front left
            };

            Mesh bladeMesh = PropellerMesh.Build();
            for (int motor = 0; motor < places.Length; motor++)
            {
                BuildMotorCan(places[motor], motor);
                propellers[motor] = BuildPropeller(places[motor], motor, bladeMesh);
            }
        }

        /// <summary>One motor can, a short cylinder under its propeller. / モータ缶 1 つ。プロペラの下の短い円柱。</summary>
        private void BuildMotorCan(Vector3 place, int motor)
        {
            GameObject can = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            can.name = $"Motor{motor + 1}";
            can.transform.SetParent(transform, false);
            can.transform.localPosition = place;

            // A Unity cylinder is 2 m tall and 1 m across before scaling.
            // Unity の円柱は、尺度を掛ける前で高さ 2 m・直径 1 m である。
            can.transform.localScale = new Vector3(
                MotorDiameterMeters, MotorHeightMeters * 0.5f, MotorDiameterMeters);
            StripCollider(can);
            Paint(can, new Color(0.78f, 0.83f, 0.84f));
        }

        /// <summary>One propeller, sitting on top of its motor can. / プロペラ 1 つ。モータ缶の上に載る。</summary>
        private Transform BuildPropeller(Vector3 place, int motor, Mesh mesh)
        {
            var propeller = new GameObject($"Propeller{motor + 1}");
            propeller.transform.SetParent(transform, false);
            propeller.transform.localPosition =
                place + new Vector3(0.0f, MotorHeightMeters * 0.5f, 0.0f);

            propeller.AddComponent<MeshFilter>().sharedMesh = mesh;
            var renderer = propeller.AddComponent<MeshRenderer>();
            renderer.sharedMaterial = TranslucentMaterial(new Color(1.0f, 0.17f, 0.24f, 0.62f));
            renderer.shadowCastingMode =
                UnityEngine.Rendering.ShadowCastingMode.Off;

            return propeller.transform;
        }

        /// <summary>
        /// Remove the collider a primitive comes with: this object is appearance,
        /// and PhysX must keep seeing only the vehicle's own box.
        /// 基本形状に付いてくるコライダを外す。この物体は見た目であり、PhysX には
        /// 機体自身の箱だけを見せ続けなければならない。
        /// </summary>
        private static void StripCollider(GameObject target)
        {
            Collider collider = target.GetComponent<Collider>();
            bool hasCollider = collider != null;
            if (hasCollider)
            {
                Destroy(collider);
            }
        }

        /// <summary>
        /// An opaque URP material in one colour, copied from the template
        /// <see cref="ShadedMaterials"/> holds. Looking the shader up by name
        /// here is what used to leave the vehicle magenta in a player build:
        /// nothing referred to URP Lit, so no build carried it.
        /// 1 色の不透明な URP のマテリアル。<see cref="ShadedMaterials"/> が持つ
        /// 複製元から複製する。ここでシェーダを名前で引くやり方が、プレイヤーの
        /// ビルドで機体をマゼンタにしていた。URP Lit を参照するものが無く、どの
        /// ビルドもそれを持たなかったためである。
        /// </summary>
        private static void Paint(GameObject target, Color colour)
        {
            target.GetComponent<MeshRenderer>().sharedMaterial =
                ShadedMaterials.NewOpaque(colour);
        }

        /// <summary>
        /// A translucent URP material, so a spinning propeller reads as a disc
        /// without hiding what is under it. Being transparent is settled in the
        /// template asset, not here: a keyword enabled at runtime asks for a
        /// shader variant the build never compiled.
        /// 半透明の URP のマテリアル。回るプロペラが、下にあるものを隠さずに円盤と
        /// して見えるようにするため。透明であることはここではなく複製元の資産の側で
        /// 決めてある。実行時に立てたキーワードは、ビルドが翻訳しなかったシェーダ
        /// 変種を求めることになるからである。
        /// </summary>
        private static Material TranslucentMaterial(Color colour)
        {
            return ShadedMaterials.NewTranslucent(colour);
        }
    }
}
