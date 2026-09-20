/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the vehicle drawn from its meshes).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using StampFly.Core;
using UnityEngine;

namespace StampFly.Vehicle
{
    /// <summary>
    /// Builds the vehicle's picture from the thirteen meshes converted out of
    /// `simulator/shared/assets/meshes/parts/`: the shell, the board, the
    /// controller, the battery and its holder, four motor cans and four
    /// propellers — the same parts, in the same colours, the Genesis simulator
    /// draws.
    ///
    /// `simulator/shared/assets/meshes/parts/` から変換した 13 のメッシュで機体の
    /// 絵を組み立てる。殻・基板・制御器・電池とその受け・モータ缶 4 つ・プロペラ
    /// 4 つで、Genesis 版が描くのと同じ部品を同じ色で描く。
    ///
    /// ## Where each part stands / 各部品が立つ場所
    ///
    /// Nowhere in particular: every STL is modelled in the vehicle's own frame
    /// already, so a part is drawn at the body's origin and lands where it
    /// belongs. The one exception is a propeller, which must turn about its
    /// motor's axis rather than about the body's centre. Its object is placed at
    /// that axis and its mesh shifted back by the same amount, which leaves the
    /// blades exactly where the STL put them while giving them a pivot to turn
    /// about.
    ///
    /// どこにも置かない。どの STL も既に機体自身の座標系で作られているので、部品は
    /// 機体の原点に描けば、あるべき場所に立つ。例外はプロペラだけで、機体の中心では
    /// なく自身のモータの軸のまわりに回らねばならない。プロペラの物体はその軸に
    /// 置き、メッシュを同じだけ戻す。羽根は STL が置いたとおりの場所に残り、回る
    /// ための軸を得る。
    ///
    /// ## Appearance only / 見た目だけであること
    ///
    /// Nothing here carries a collider, so PhysX keeps seeing only the box
    /// <see cref="StampFly.Sim.VehicleBody"/> set up. The meshes are the
    /// picture; the physics is unchanged.
    ///
    /// ここに在るものはコライダを持たないので、PhysX が見るのは
    /// <see cref="StampFly.Sim.VehicleBody"/> が設定した箱だけのままである。
    /// メッシュは絵であって、物理は変わらない。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（機体の見た目）
    /// </summary>
    public sealed class MeshAppearance : MonoBehaviour
    {
        /// <summary>
        /// Where a propeller turns about, per motor, in the firmware's order
        /// M1..M4. The two numbers are the centroid of the matching motor can in
        /// the STL files — measured, not chosen — and they agree with the
        /// physics' own rotor places
        /// (<see cref="StampFly.Sim.GeneratedParams.RotorOffsetMeters"/>, 0.023 m)
        /// to within 0.2 mm. The mesh is not stretched to close that gap: the
        /// picture shows the machine as it was drawn.
        ///
        /// プロペラが回る軸を、モータごとに、ファームの順 M1〜M4 で。2 つの数値は
        /// STL のファイルでの対応するモータ缶の重心であり、選んだのではなく測った
        /// 値である。物理側のロータの位置
        /// （<see cref="StampFly.Sim.GeneratedParams.RotorOffsetMeters"/>、0.023 m）
        /// とは 0.2 mm の内で一致する。この差を埋めるためにメッシュを伸ばすことは
        /// しない。絵は機械を、描かれたとおりに見せる。
        /// </summary>
        public const float MotorAxisOffsetMetres = 0.022804f;

        /// <summary>
        /// How far above the body's origin a propeller's own plane sits [m], the
        /// centroid height of the propeller meshes. It is the pivot's height and
        /// nothing more: the blades keep their modelled shape either side of it.
        /// 機体の原点からプロペラ自身の面までの高さ [m]。プロペラのメッシュの重心の
        /// 高さである。軸の高さであるだけで、それ以上の意味は無い。羽根はその上下で
        /// 作られたとおりの形を保つ。
        /// </summary>
        public const float PropellerAxisHeightMetres = 0.005444f;

        /// <summary>
        /// How far the whole picture is raised so the machine stands on the
        /// floor instead of sinking into it [m].
        ///
        /// The physics' collision box is centred on the body's origin and is
        /// 20.6 mm thick, so a resting vehicle has its origin 10.3 mm up. The
        /// meshes reach 15.748 mm below that origin (the frame's underside, the
        /// lowest point of the thirteen), which would put the shell 5.4 mm
        /// through the floor. Raising the picture by the difference stands the
        /// frame's underside on the floor without moving anything the physics
        /// integrates.
        ///
        /// 絵の全体を持ち上げる高さ [m]。機械が床にめり込まず床に立つようにする。
        ///
        /// 物理の衝突箱は機体の原点を中心とする厚さ 20.6 mm なので、床に載った機体
        /// の原点は 10.3 mm の高さに在る。メッシュはその原点より 15.748 mm 下まで
        /// 届く（機体の底面で、13 部品の最下点である）ため、そのままでは殻が床を
        /// 5.4 mm 突き抜ける。差のぶん絵を持ち上げると、機体の底面が床に立ち、物理が
        /// 積分するものは何も動かない。
        /// </summary>
        public static float GroundOffsetMetres =>
            LowestMeshPointMetres - Sim.VehicleBody.RestingCentreHeight;

        /// <summary>
        /// The deepest point of the thirteen meshes below the body's origin [m]:
        /// the frame's underside, measured from `frame.stl`.
        /// 13 のメッシュが機体の原点より下へ届く深さ [m]。`frame.stl` から測った
        /// 機体の底面である。
        /// </summary>
        public const float LowestMeshPointMetres = 0.015748f;

        [Tooltip("The loop whose motor speeds drive the propellers. " +
                 "プロペラを回す元になる、モータ角速度を持つループ。")]
        public Sim.SimLoop simLoop;

        private readonly Transform[] propellers = new Transform[VehicleParts.Propellers.Length];
        private readonly float[] propellerAngles = new float[VehicleParts.Propellers.Length];

        /// <summary>
        /// True when every mesh asset was found and the picture is the real
        /// machine. False means the converter has not been run, and a caller
        /// that offers a fallback shows it instead.
        /// メッシュ資産が全て見つかり、絵が本物の機械であるとき真。偽であれば変換
        /// ツールが実行されていないということで、代わりを用意する呼び出し側はそれを
        /// 見せる。
        /// </summary>
        public bool IsComplete { get; private set; }

        private void Awake()
        {
            IsComplete = Build();
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
        /// Put every part in place, raised so the machine stands on the floor.
        /// Returns false when a mesh is missing, having said which.
        /// 部品を全て置く。機械が床に立つよう持ち上げる。メッシュが欠けていれば、
        /// どれかを述べたうえで false を返す。
        /// </summary>
        private bool Build()
        {
            bool isComplete = true;

            foreach (VehicleParts.Part part in VehicleParts.Fixed)
            {
                isComplete &= BuildFixedPart(part);
            }

            for (int motor = 0; motor < VehicleParts.Propellers.Length; motor++)
            {
                isComplete &= BuildPropeller(motor);
            }

            return isComplete;
        }

        /// <summary>
        /// One part that does not turn, drawn at the body's origin with only the
        /// ground offset applied.
        /// 回らない部品 1 つ。機体の原点に、床までのずれだけを掛けて描く。
        /// </summary>
        private bool BuildFixedPart(VehicleParts.Part part)
        {
            Mesh mesh = Load(part.Name);
            bool meshIsMissing = mesh == null;
            if (meshIsMissing)
            {
                return false;
            }

            GameObject drawn = NewPart(part, mesh);
            drawn.transform.localPosition = new Vector3(0.0f, GroundOffsetMetres, 0.0f);
            return true;
        }

        /// <summary>
        /// One propeller: an object at its motor's axis, holding the mesh pushed
        /// back by that same axis so the blades stay where they were modelled.
        /// プロペラ 1 つ。自身のモータの軸に物体を置き、その軸のぶんだけ戻した
        /// メッシュを持たせる。羽根は作られた場所に留まる。
        /// </summary>
        private bool BuildPropeller(int motor)
        {
            VehicleParts.Part part = VehicleParts.Propellers[motor];
            Mesh mesh = Load(part.Name);
            bool meshIsMissing = mesh == null;
            if (meshIsMissing)
            {
                return false;
            }

            var pivot = new GameObject($"Propeller{motor + 1}");
            pivot.transform.SetParent(transform, false);
            pivot.transform.localPosition = AxisOf(motor) + new Vector3(0.0f, GroundOffsetMetres, 0.0f);

            GameObject drawn = NewPart(part, mesh);
            drawn.transform.SetParent(pivot.transform, false);
            drawn.transform.localPosition = -AxisOf(motor);

            propellers[motor] = pivot.transform;
            return true;
        }

        /// <summary>
        /// The axis motor <paramref name="motor"/>'s propeller turns about, in
        /// the body's frame. The order is the firmware's: M1 front right,
        /// M2 rear right, M3 rear left, M4 front left — matching
        /// <see cref="VehicleParts.Propellers"/>.
        /// モータ <paramref name="motor"/> のプロペラが回る軸を、機体の座標系で。
        /// 順はファームのもので、M1 が前右・M2 が後右・M3 が後左・M4 が前左。
        /// <see cref="VehicleParts.Propellers"/> と同じ並びである。
        /// </summary>
        public static Vector3 AxisOf(int motor)
        {
            float right = motor <= 1 ? MotorAxisOffsetMetres : -MotorAxisOffsetMetres;
            bool isFront = motor == 0 || motor == 3;
            float forward = isFront ? MotorAxisOffsetMetres : -MotorAxisOffsetMetres;
            return new Vector3(right, PropellerAxisHeightMetres, forward);
        }

        /// <summary>
        /// Turn each propeller by what its motor did this frame, the same way
        /// and at the same drawn speed as the primitive appearance.
        /// 各プロペラを、このフレームで対応するモータが回したぶん回す。基本形状の
        /// 見た目と同じやり方、同じ描画上の速さで行う。
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

                propellerAngles[motor] += VehicleAppearance.DrawnTurnDegrees(
                    motor, speeds[motor], elapsedSeconds);
                propeller.localRotation =
                    Quaternion.Euler(0.0f, propellerAngles[motor], 0.0f);
            }
        }

        /// <summary>
        /// A child object drawing one mesh in the part's own colour. It carries
        /// no collider, because a collider here would change what PhysX sees.
        /// メッシュ 1 つを部品自身の色で描く子の物体。コライダは持たない。ここに
        /// コライダを置けば PhysX が見るものが変わってしまうためである。
        /// </summary>
        private GameObject NewPart(VehicleParts.Part part, Mesh mesh)
        {
            var drawn = new GameObject(part.Name);
            drawn.transform.SetParent(transform, false);

            drawn.AddComponent<MeshFilter>().sharedMesh = mesh;
            var renderer = drawn.AddComponent<MeshRenderer>();
            renderer.sharedMaterial = part.IsTranslucent
                ? ShadedMaterials.NewTranslucent(part.Colour)
                : ShadedMaterials.NewOpaque(part.Colour);

            bool castsNoShadow = part.IsTranslucent;
            if (castsNoShadow)
            {
                renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            }

            return drawn;
        }

        /// <summary>
        /// One part's mesh out of <c>Resources</c>, or null with the reason said
        /// aloud. The converter writes these assets; a build without them draws
        /// nothing, and saying so is what stops that looking like a blank scene.
        /// 部品 1 つのメッシュを <c>Resources</c> から。無ければ理由を大きく述べて
        /// null を返す。この資産を書くのは変換ツールで、それを欠いたビルドは何も
        /// 描かない。述べることが、それが空の場面に見えるのを防ぐ。
        /// </summary>
        private static Mesh Load(string partName)
        {
            Mesh mesh = Resources.Load<Mesh>(VehicleParts.ResourcePath(partName));
            bool meshIsMissing = mesh == null;
            if (meshIsMissing)
            {
                Debug.LogError(
                    $"[MeshAppearance] no mesh asset at " +
                    $"Resources/{VehicleParts.ResourcePath(partName)}. " +
                    "Rebuild the meshes from the editor menu " +
                    "'StampFly/Meshes/Rebuild Vehicle Meshes'.");
            }

            return mesh;
        }
    }
}
