/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — generated-parameter tests).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Sim;
using UnityEngine;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// Guarantees that the generated physical parameters actually reach PhysX.
    ///
    /// The numbers in <see cref="GeneratedParams"/> come from
    /// `control/models/stampfly_physical.yaml`, and `sf params check` compares
    /// them against the MuJoCo model and the SILS C++ plant. That comparison
    /// only proves the FILE holds the right numbers. What it cannot see is
    /// whether <see cref="VehicleBody.Apply"/> puts them on the rigid body — a
    /// forgotten assignment, or one axis wired to the wrong component, would
    /// leave the audit green while the Unity simulator integrated a different
    /// body from the SILS. These tests close that gap.
    ///
    /// 生成された物理パラメータが実際に PhysX へ届くことを保証する。
    ///
    /// <see cref="GeneratedParams"/> の数値は
    /// `control/models/stampfly_physical.yaml` から来て、`sf params check` が
    /// MuJoCo モデルと SILS の C++ プラントに対して突き合わせる。しかしその
    /// 突き合わせが示すのは「ファイルが正しい数値を持つ」ことだけである。
    /// <see cref="VehicleBody.Apply"/> がそれを剛体へ書き込むかどうかは見え
    /// ない。書き忘れや、ある軸を誤った成分に繋いだ配線があっても、検査は緑の
    /// まま Unity 版が SILS とは別の剛体を積分してしまう。この試験がその隙間を
    /// 埋める。
    /// </summary>
    public sealed class VehicleBodyParamsTest
    {
        // PhysX stores these as 32-bit floats, so a comparison needs the slack
        // a float round trip can introduce — and nothing more, because the
        // point of the test is that the value is not merely close but the
        // generated one.
        // PhysX は 32bit の float で保持するので、比較には float を往復した
        // ときの誤差ぶんの余裕が要る。それ以上は要らない。この試験の主旨は、
        // 値が近いことではなく「生成された値そのもの」であることだからである。
        private const float FloatSlack = 1e-9f;

        private GameObject host;
        private Rigidbody body;
        private BoxCollider box;

        [SetUp]
        public void SetUp()
        {
            // Built from code rather than from a scene: Apply() is what the
            // simulator and every PlayMode scene call, so calling it directly
            // is the narrowest way to observe its effect.
            // 場面からではなくコードから組み立てる。シミュレータと全ての
            // PlayMode の場面が呼ぶのは Apply() なので、直接呼ぶのがその効果を
            // 見る最も狭い方法である。
            host = new GameObject("VehicleBodyParamsTest");
            body = host.AddComponent<Rigidbody>();
            box = host.AddComponent<BoxCollider>();

            VehicleBody.Apply(body, box);
        }

        [TearDown]
        public void TearDown()
        {
            Object.DestroyImmediate(host);
        }

        /// <summary>
        /// The mass PhysX integrates is the generated one, so a hover thrust
        /// computed by the firmware holds this vehicle up exactly as it holds
        /// the SILS one up.
        /// PhysX が積分する質量が生成された値であること。ファームが計算する
        /// ホバー推力が、SILS の機体を支えるのと同じようにこの機体を支える。
        /// </summary>
        [Test]
        public void TheMassOnTheBodyIsTheGeneratedMass()
        {
            Assert.That(body.mass,
                Is.EqualTo(GeneratedParams.MassKilograms).Within(FloatSlack));
        }

        /// <summary>
        /// Each principal moment reaches the Unity component that carries the
        /// body axis it belongs to: the body's left axis is Unity x, its up
        /// axis is Unity y, and its forward axis is Unity z. Checking the three
        /// separately is what catches a swapped pair, which a check of the
        /// assembled vector against itself never would.
        /// 各主慣性モーメントが、それが属する機体の軸を担う Unity の成分へ届く
        /// こと。機体の左右軸が Unity の x、上軸が y、前軸が z である。3 つを
        /// 別々に見ることが入れ違いを捕まえる。組み立て済みのベクトルを自分自身と
        /// 比べても、それは決して捕まらない。
        /// </summary>
        [Test]
        public void EachInertiaReachesItsUnityAxis()
        {
            Assert.That(body.inertiaTensor.x,
                Is.EqualTo(GeneratedParams.InertiaLeftAxis).Within(FloatSlack),
                "Unity x carries the body's left axis, Iyy");
            Assert.That(body.inertiaTensor.y,
                Is.EqualTo(GeneratedParams.InertiaUpAxis).Within(FloatSlack),
                "Unity y carries the body's up axis, Izz");
            Assert.That(body.inertiaTensor.z,
                Is.EqualTo(GeneratedParams.InertiaForwardAxis).Within(FloatSlack),
                "Unity z carries the body's forward axis, Ixx");
        }

        /// <summary>
        /// The inertia is written, not computed: PhysX would otherwise derive a
        /// tensor from the collider's shape and the mass, which is not the
        /// measured one. The same holds for the centre of mass.
        /// 慣性は計算させるのではなく書き込むこと。さもなければ PhysX は
        /// コライダの形と質量からテンソルを導いてしまい、それは実測値ではない。
        /// 重心も同様である。
        /// </summary>
        [Test]
        public void TheInertiaAndCentreAreWrittenRatherThanDerived()
        {
            Assert.That(body.automaticInertiaTensor, Is.False);
            Assert.That(body.automaticCenterOfMass, Is.False);
            Assert.That(body.centerOfMass, Is.EqualTo(Vector3.zero));
            Assert.That(body.inertiaTensorRotation, Is.EqualTo(Quaternion.identity));
        }

        /// <summary>
        /// The collider is the generated box, at the body's centre, so the
        /// vehicle rests on the floor at the height the SILS model rests at.
        /// コライダが生成された箱であり、機体の中心に在ること。よって機体は
        /// SILS のモデルが載る高さで床に載る。
        /// </summary>
        [Test]
        public void TheColliderIsTheGeneratedBox()
        {
            Assert.That(box.center, Is.EqualTo(Vector3.zero));
            Assert.That(box.size.x,
                Is.EqualTo(GeneratedParams.BoxSizeRight).Within(FloatSlack));
            Assert.That(box.size.y,
                Is.EqualTo(GeneratedParams.BoxSizeUp).Within(FloatSlack));
            Assert.That(box.size.z,
                Is.EqualTo(GeneratedParams.BoxSizeForward).Within(FloatSlack));
        }

        /// <summary>
        /// The resting height is half the box's thickness, which is what the
        /// PlayMode landing check and the simulator's own floor clamp expect.
        /// 床に載ったときの中心の高さが箱の厚みの半分であること。PlayMode の
        /// 着地の判定と、シミュレータ自身の床での押し上げが前提にする値である。
        /// </summary>
        [Test]
        public void TheRestingHeightIsHalfTheBoxThickness()
        {
            Assert.That(VehicleBody.RestingCentreHeight,
                Is.EqualTo(0.5f * GeneratedParams.BoxSizeUp).Within(FloatSlack));
        }

        /// <summary>
        /// The body carries no damping of its own: aerodynamic drag belongs to
        /// the firmware's C++ plant, and a second copy here would apply it
        /// twice.
        /// 剛体自身は減衰を持たないこと。空気抵抗はファームの C++ のプラントが
        /// 担い、ここに 2 つ目が在れば二重に掛かる。
        /// </summary>
        [Test]
        public void ThePhysicsEngineAddsNoDragOfItsOwn()
        {
            Assert.That(body.linearDamping, Is.EqualTo(VehicleBody.NoDamping));
            Assert.That(body.angularDamping, Is.EqualTo(VehicleBody.NoDamping));
        }

        /// <summary>
        /// Neither a rate clamp nor sleeping may interrupt the integration: a
        /// 37 g body reaches high rates, and a sleeping body stops moving
        /// mid-flight.
        /// 角速度の頭打ちも休止も積分を止めてはならない。37 g の機体は高い
        /// 角速度に達し、休止した剛体は飛行の途中で動かなくなる。
        /// </summary>
        [Test]
        public void NothingClampsOrHaltsTheIntegration()
        {
            Assert.That(body.maxAngularVelocity,
                Is.EqualTo(VehicleBody.AngularSpeedLimitRadPerSecond));
            Assert.That(body.sleepThreshold,
                Is.EqualTo(VehicleBody.NeverSleepThreshold));
        }

        /// <summary>
        /// The names the rest of the code reads from <see cref="VehicleBody"/>
        /// forward to the generated class rather than holding a second copy of
        /// the numbers — the drift this whole change removes.
        /// 他のコードが <see cref="VehicleBody"/> から読む名前が、数値の 2 つ目の
        /// コピーを持つのではなく生成されたクラスへ委ねること。この変更が
        /// 取り除いた食い違いそのものである。
        /// </summary>
        [Test]
        public void TheBodySForwardingNamesAgreeWithTheGeneratedOnes()
        {
            Assert.That(VehicleBody.MassKilograms,
                Is.EqualTo(GeneratedParams.MassKilograms));
            Assert.That(VehicleBody.InertiaTensorUnityAxes,
                Is.EqualTo(GeneratedParams.InertiaTensorUnityAxes));
            Assert.That(VehicleBody.BoxSizeUnityAxes,
                Is.EqualTo(GeneratedParams.BoxSizeUnityAxes));
        }

        /// <summary>
        /// The accelerometer measures against the same gravity the MuJoCo model
        /// and the SILS plant use, so a reading taken here means what the same
        /// reading means there.
        /// 加速度計が、MuJoCo モデルと SILS のプラントが使うのと同じ重力に対して
        /// 測ること。よってここで得た測定値は、あちらでの同じ測定値と同じ意味を持つ。
        /// </summary>
        [Test]
        public void TheAccelerometerUsesTheGeneratedGravity()
        {
            Assert.That(AccelerometerModel.GravityMetersPerSecondSquared,
                Is.EqualTo(GeneratedParams.GravityMetersPerSecondSquared));
        }
    }
}
