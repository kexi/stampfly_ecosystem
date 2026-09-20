/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the converted vehicle meshes).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Editor.MeshTools;
using StampFly.Vehicle;
using UnityEngine;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// What the converted meshes guarantee: the simulator draws the real
    /// StampFly, at its real size, the right way up and the right way round,
    /// with every part present.
    ///
    /// 変換したメッシュが保証すること。シミュレータが本物の StampFly を、本物の
    /// 大きさで、上下も前後も正しく、部品を 1 つも欠かさずに描くこと。
    ///
    /// The conversion moves a right-handed, Y-up, X-to-the-LEFT, millimetre
    /// model into Unity's left-handed, X-to-the-RIGHT, metre frame. Every step
    /// of that has a silent failure: a missed mirror puts the front-left motor
    /// on the right, a missed winding swap draws the machine inside out, and a
    /// missed scale draws it 1000 times too big. None of them throws, and each
    /// of the tests below is one of them.
    ///
    /// 変換は、右手系で Y が上・X が**左**・ミリメートルのモデルを、Unity の
    /// 左手系で X が**右**・メートルの座標系へ移す。その各段には、黙って失敗する
    /// 道がある。鏡映の抜けは前左のモータを右に置き、巻きの入れ替えの抜けは機械を
    /// 裏返しに描き、尺度の抜けは 1000 倍の大きさで描く。どれも例外を投げない。
    /// 下の試験は、そのそれぞれである。
    /// </summary>
    public sealed class VehicleMeshTest
    {
        /// <summary>
        /// The frame's width across [m], measured from `frame.stl`: 81.842 mm
        /// between its outermost points. It is the one number that catches a
        /// missed millimetre-to-metre scale.
        /// 機体の差し渡しの幅 [m]。`frame.stl` から測った、最も外側の点の間の
        /// 81.842 mm である。ミリメートルからメートルへの尺度の抜けを捕まえるのは、
        /// この 1 つの数値である。
        /// </summary>
        private const float FrameWidthMetres = 0.081842f;

        /// <summary>How close a measured size must be [m]. / 測った寸法がどれだけ近ければよいか [m]。</summary>
        private const float SizeToleranceMetres = 0.0005f;

        /// <summary>
        /// How far a part's centre may be from where it is expected [m]. It is
        /// loose on purpose: the tests below ask which side of the machine a
        /// part is on, not where to a tenth of a millimetre.
        /// 部品の中心が、予想の場所からどれだけ離れていてよいか [m]。意図して緩く
        /// してある。下の試験が問うのは、部品が機械のどちら側に在るかであって、
        /// 0.1 mm の精度での位置ではない。
        /// </summary>
        private const float PlaceToleranceMetres = 0.002f;

        [Test]
        public void EveryPartHasAMeshUnderResources()
        {
            foreach (VehicleParts.Part part in VehicleParts.All())
            {
                Mesh mesh = Resources.Load<Mesh>(VehicleParts.ResourcePath(part.Name));

                Assert.That(mesh, Is.Not.Null,
                            $"Resources/{VehicleParts.ResourcePath(part.Name)} is missing; run " +
                            "StampFly > Meshes > Rebuild Vehicle Meshes");
                Assert.That(mesh.vertexCount, Is.GreaterThan(0),
                            $"{part.Name} loaded but holds no geometry");
            }
        }

        [Test]
        public void ThePartsListAndTheAssetsAreOneForOne()
        {
            // Thirteen is the count of STL files that describe the machine; the
            // folder also holds `_BAK` copies and an `m5stamps3_backup`, which
            // are not parts and must not be drawn.
            // 13 は機械を記す STL のファイルの数である。同じフォルダには `_BAK` の
            // 複製と `m5stamps3_backup` も在るが、これらは部品ではなく、描いては
            // ならない。
            const int PartsOfTheMachine = 13;

            Assert.That(VehicleParts.All().Length, Is.EqualTo(PartsOfTheMachine));

            Object[] assets = Resources.LoadAll(VehicleParts.MeshResourceFolder, typeof(Mesh));
            Assert.That(assets.Length, Is.EqualTo(PartsOfTheMachine),
                        "the mesh folder holds something the parts list does not name, or " +
                        "the other way round");
        }

        [Test]
        public void TheFrameIsEightyTwoMillimetresAcross()
        {
            Mesh frame = Load("frame");
            Bounds bounds = frame.bounds;

            Assert.That(bounds.size.x, Is.EqualTo(FrameWidthMetres).Within(SizeToleranceMetres),
                        "the frame is not 82 mm across; the millimetre-to-metre scale is wrong");
            Assert.That(bounds.size.z, Is.EqualTo(FrameWidthMetres).Within(SizeToleranceMetres));
        }

        [Test]
        public void EachMotorSitsInItsOwnQuadrant()
        {
            // Unity's frame: +x right, +z forward. The STL's is +x LEFT, so
            // `motor_fl` — the front-LEFT motor — must end up at negative x.
            // Unity の座標系は +x が右・+z が前。STL のそれは +x が**左**なので、
            // 前**左**のモータ `motor_fl` は負の x に来なければならない。
            AssertCentreQuadrant("motor_fl", -1.0f, +1.0f);
            AssertCentreQuadrant("motor_fr", +1.0f, +1.0f);
            AssertCentreQuadrant("motor_rl", -1.0f, -1.0f);
            AssertCentreQuadrant("motor_rr", +1.0f, -1.0f);
        }

        [Test]
        public void EachPropellerSitsOverItsOwnMotor()
        {
            AssertCentreQuadrant("propeller_fl", -1.0f, +1.0f);
            AssertCentreQuadrant("propeller_fr", +1.0f, +1.0f);
            AssertCentreQuadrant("propeller_rl", -1.0f, -1.0f);
            AssertCentreQuadrant("propeller_rr", +1.0f, -1.0f);
        }

        [Test]
        public void APropellerTurnsAboutItsOwnMotorAxis()
        {
            // The motor order is the firmware's M1..M4, which
            // VehicleParts.Propellers is written in: M1 front right, M2 rear
            // right, M3 rear left, M4 front left.
            // モータの順はファームの M1〜M4 で、VehicleParts.Propellers もその順で
            // 書かれている。M1 が前右・M2 が後右・M3 が後左・M4 が前左。
            var motorOfPropeller = new[] { "motor_fr", "motor_rr", "motor_rl", "motor_fl" };

            for (int motor = 0; motor < motorOfPropeller.Length; motor++)
            {
                Vector3 axis = MeshAppearance.AxisOf(motor);
                Vector3 can = Load(motorOfPropeller[motor]).bounds.center;

                Assert.That(axis.x, Is.EqualTo(can.x).Within(PlaceToleranceMetres),
                            $"propeller {motor + 1} does not turn over {motorOfPropeller[motor]}");
                Assert.That(axis.z, Is.EqualTo(can.z).Within(PlaceToleranceMetres),
                            $"propeller {motor + 1} does not turn over {motorOfPropeller[motor]}");
            }
        }

        [Test]
        public void EveryMeshFacesOutward()
        {
            foreach (VehicleParts.Part part in VehicleParts.All())
            {
                Mesh mesh = Load(part.Name);
                double volume = StlToUnityGeometry.SixTimesSignedVolume(
                    mesh.vertices, mesh.triangles);

                // A negative volume means the faces point into the solid, which
                // is what a missed winding swap leaves behind: the machine is
                // then drawn as its own inside surface and reads as a hollow,
                // wrongly lit shell.
                // 負の体積は面が立体の内を向いていることを意味し、巻きの入れ替えの
                // 抜けが残すのがそれである。そのとき機械は自身の内面として描かれ、
                // 中空で照明のおかしな殻に見える。
                Assert.That(volume, Is.GreaterThan(0.0),
                            $"{part.Name} is wound inside out");
            }
        }

        [Test]
        public void EveryMeshHasOneNormalPerVertex()
        {
            foreach (VehicleParts.Part part in VehicleParts.All())
            {
                Mesh mesh = Load(part.Name);

                Assert.That(mesh.normals.Length, Is.EqualTo(mesh.vertexCount),
                            $"{part.Name} has no normals; it would be drawn unlit");
            }
        }

        [Test]
        public void TheWholeMachineStandsOnTheFloorRatherThanThroughIt()
        {
            float lowest = float.MaxValue;
            foreach (VehicleParts.Part part in VehicleParts.All())
            {
                lowest = Mathf.Min(lowest, Load(part.Name).bounds.min.y);
            }

            Assert.That(lowest,
                        Is.EqualTo(-MeshAppearance.LowestMeshPointMetres).Within(SizeToleranceMetres),
                        "the deepest point of the meshes is not where MeshAppearance thinks it " +
                        "is, so the picture would sink into the floor or hover above it");

            // A resting vehicle has its origin one half-box above the floor, and
            // the picture is raised by the offset on top of that, so the lowest
            // point of the meshes lands exactly on the floor: neither through it
            // nor hovering over it.
            // 床に載った機体の原点は箱の半分の高さに在り、絵はそこからさらにずれの
            // ぶん持ち上がる。よってメッシュの最下点はちょうど床に来る。突き抜けも
            // しないし、浮きもしない。
            float restingBottom = Sim.VehicleBody.RestingCentreHeight
                                  + MeshAppearance.GroundOffsetMetres + lowest;
            Assert.That(restingBottom, Is.EqualTo(0.0f).Within(SizeToleranceMetres));
        }

        /// <summary>
        /// Check a part's centre is on the expected side in both x and z. The
        /// signs are what the frames disagree about, so they are what is asked.
        /// 部品の中心が、x でも z でも予想した側に在ることを確かめる。座標系どうしが
        /// 食い違うのは符号なので、問うのも符号である。
        /// </summary>
        private static void AssertCentreQuadrant(string partName, float rightSign, float forwardSign)
        {
            Vector3 centre = Load(partName).bounds.center;
            float wanted = MeshAppearance.MotorAxisOffsetMetres;

            Assert.That(centre.x, Is.EqualTo(rightSign * wanted).Within(PlaceToleranceMetres),
                        $"{partName} is on the wrong side left to right");
            Assert.That(centre.z, Is.EqualTo(forwardSign * wanted).Within(PlaceToleranceMetres),
                        $"{partName} is on the wrong side front to back");
        }

        /// <summary>One part's mesh, failing the test when it is missing. / 部品 1 つのメッシュ。無ければ試験を落とす。</summary>
        private static Mesh Load(string partName)
        {
            Mesh mesh = Resources.Load<Mesh>(VehicleParts.ResourcePath(partName));
            Assert.That(mesh, Is.Not.Null,
                        $"Resources/{VehicleParts.ResourcePath(partName)} is missing; run " +
                        "StampFly > Meshes > Rebuild Vehicle Meshes");
            return mesh;
        }
    }
}
