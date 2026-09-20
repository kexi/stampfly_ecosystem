/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the raycast flight scaffolding).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.IO;
using StampFly.Input;
using StampFly.Native;
using StampFly.Sim;
using UnityEngine;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// The flight <see cref="SimLoop"/> actually flies, without a MonoBehaviour:
    /// the downward range comes from <see cref="DownwardRangefinder"/> casting a
    /// real ray at a real floor collider, not from arithmetic on the body's own
    /// height.
    ///
    /// <see cref="SimLoop"/> が実際に飛ばす飛行を、MonoBehaviour 無しで走らせる。
    /// 下向きの距離は、機体自身の高さの計算ではなく、本物の床のコライダへ
    /// <see cref="DownwardRangefinder"/> が本物の光線を放って得る。
    ///
    /// That difference is the whole point. <see cref="FirmwareFlightScene"/>
    /// hands the firmware an analytic height, so it cannot see anything that
    /// goes wrong between `Physics.Simulate` and a scene query — which is
    /// exactly where the browser's frame-rate-dependent failure lived.
    ///
    /// その違いこそが眼目である。<see cref="FirmwareFlightScene"/> はファームへ
    /// 解析的な高さを渡すので、`Physics.Simulate` と場面への問い合わせの間で起きる
    /// ことを何も見られない。ブラウザのフレーム率に依存する不具合は、まさにそこに
    /// あった。
    ///
    /// <see cref="EndFrame"/> marks where a rendered frame would have ended, so
    /// a test can put the boundary anywhere and check the flight does not care.
    /// <see cref="EndFrame"/> は、描画のフレームが終わったであろう場所を示す。
    /// 試験は切れ目をどこにでも置いて、飛行がそれを気にしないことを確かめられる。
    ///
    /// @design docs/plans/unity-simulator.md §3 1 刻みの処理
    /// </summary>
    public sealed class RaycastFlightScene
    {
        /// <summary>A floor wider than any flight these checks produce. / どの飛行より広い床。</summary>
        private const float FloorSizeMeters = 20.0f;
        private const float FloorThicknessMeters = 0.5f;

        /// <summary>The floor's top face, at the world origin. / 床の上面。世界原点に置く。</summary>
        public const float FloorTopMeters = 0.0f;

        private readonly DownwardRangefinder rangefinder = new DownwardRangefinder();

        private GameObject vehicleObject;
        private GameObject floorObject;
        private IFirmware firmware;
        private Vector3 accelerometerReading;

        /// <summary>The vehicle's rigid body. / 機体の剛体。</summary>
        public Rigidbody Body { get; private set; }

        /// <summary>What the last tick returned. / 直前の刻みが返したもの。</summary>
        public SfuStepOut LastResult;

        /// <summary>What the beam last found. / 光線が直前に見つけたもの。</summary>
        public RangeReading LastRange { get; private set; }

        /// <summary>The sticks this flight sends. / この飛行が送るスティックの値。</summary>
        public RcFrame Sticks = RcFrame.Centred;

        /// <summary>The firmware, for its status and its errors. / ファーム。状態と誤りのため。</summary>
        public IFirmware Firmware => firmware;

        /// <summary>Whether the development dylib has been built. / 開発用 dylib があるか。</summary>
        public static bool DylibExists => File.Exists(EditorFirmware.DefaultDylibPath());

        /// <summary>The vehicle's height above the floor [m]. / 床からの機体の高さ [m]。</summary>
        public float AltitudeMeters => Body.position.y - FloorTopMeters;

        /// <summary>
        /// Build the scene and power the firmware on.
        /// 場面を作り、ファームの電源を入れる。
        /// </summary>
        public bool Build()
        {
            PhysicsStepSettings.Apply();
            BuildFloor();
            BuildVehicle();

            firmware = new EditorFirmware();
            firmware.BeginLoad();

            bool didNotLoad = firmware.Status != FirmwareStatus.Loaded;
            if (didNotLoad)
            {
                return false;
            }

            var config = new SfuConfig
            {
                BatteryModel = 1,
                BootCalibration = 1,
                HostOwnsBody = 1,
                StartHeightMeters = VehicleBody.RestingCentreHeight,
            };

            return firmware.Boot(config) == SfuAbi.Ok;
        }

        /// <summary>A static floor whose top face is the world's y = 0. / 上面が世界の y = 0 になる静的な床。</summary>
        private void BuildFloor()
        {
            floorObject = GameObject.CreatePrimitive(PrimitiveType.Cube);
            floorObject.name = "Floor";
            floorObject.transform.position =
                new Vector3(0.0f, FloorTopMeters - 0.5f * FloorThicknessMeters, 0.0f);
            floorObject.transform.localScale =
                new Vector3(FloorSizeMeters, FloorThicknessMeters, FloorSizeMeters);
        }

        /// <summary>The vehicle, resting on the floor. / 床に載った機体。</summary>
        private void BuildVehicle()
        {
            vehicleObject = new GameObject("Vehicle");
            vehicleObject.transform.position =
                new Vector3(0.0f, VehicleBody.RestingCentreHeight, 0.0f);

            BoxCollider box = vehicleObject.AddComponent<BoxCollider>();
            Body = vehicleObject.AddComponent<Rigidbody>();
            VehicleBody.Apply(Body, box);

            accelerometerReading = new Vector3(
                0.0f, AccelerometerModel.GravityMetersPerSecondSquared, 0.0f);
            Physics.SyncTransforms();
        }

        /// <summary>
        /// Mark the end of a rendered frame. Nothing in the loop should depend
        /// on where this falls; the method exists so a test can prove it.
        /// 描画のフレームの終わりを示す。ループの何ひとつ、これがどこに来るかに
        /// 依存してはならない。試験がそれを示せるようにこの関数がある。
        /// </summary>
        public void EndFrame()
        {
        }

        /// <summary>
        /// One tick, in the order <see cref="SimLoop.RunOneTick"/> uses,
        /// including the raycast the rangefinder makes.
        /// <see cref="SimLoop.RunOneTick"/> と同じ順序で 1 刻み進める。測距器が
        /// 行うレイキャストも含む。
        /// </summary>
        public int RunOneTick()
        {
            SfuStepIn input = PackState();

            int status = firmware.Step(input, ref LastResult);
            bool stepFailed = status != SfuAbi.Ok;
            if (stepFailed)
            {
                return status;
            }

            Vector3 velocityBefore = Body.linearVelocity;
            ApplyWrench();
            Physics.Simulate(PhysicsStepSettings.StepSeconds);

            accelerometerReading = AccelerometerModel.Read(
                velocityBefore, Body.linearVelocity, Body.rotation,
                PhysicsStepSettings.StepSeconds);

            return SfuAbi.Ok;
        }

        /// <summary>
        /// The state the firmware needs, packed exactly as the loop packs it.
        /// ファームが要る状態を、ループと全く同じ形で詰める。
        /// </summary>
        private SfuStepIn PackState()
        {
            Vector3 position = Body.position;
            Quaternion rotation = Body.rotation;
            Vector3 velocity = Body.linearVelocity;
            Vector3 rate = Body.angularVelocity;

            LastRange = rangefinder.Measure(position, rotation);

            return new SfuStepIn
            {
                PositionX = position.x, PositionY = position.y, PositionZ = position.z,
                RotationX = rotation.x, RotationY = rotation.y,
                RotationZ = rotation.z, RotationW = rotation.w,
                VelocityWorldX = velocity.x,
                VelocityWorldY = velocity.y,
                VelocityWorldZ = velocity.z,
                AngularVelocityWorldX = rate.x,
                AngularVelocityWorldY = rate.y,
                AngularVelocityWorldZ = rate.z,
                AccelLocalX = accelerometerReading.x,
                AccelLocalY = accelerometerReading.y,
                AccelLocalZ = accelerometerReading.z,
                RangeDownMeters = LastRange.Distance,
                RangeDownValid = LastRange.IsValid ? 1 : 0,
                GroundHeightMeters = LastRange.HeightAboveSurface,
                RcThrottle = Sticks.Throttle,
                RcRoll = Sticks.Roll,
                RcPitch = Sticks.Pitch,
                RcYaw = Sticks.Yaw,
                RcFlags = Sticks.Flags,
                StepMicroseconds = SimClock.TickMicroseconds,
            };
        }

        /// <summary>
        /// The rotors' wrench plus the gyroscopic term, the same four calls the
        /// loop makes.
        /// ロータの力とジャイロ項。ループが行うのと同じ 4 回の呼び出しである。
        /// </summary>
        private void ApplyWrench()
        {
            Body.AddRelativeForce(new Vector3(
                LastResult.ForceLocalX, LastResult.ForceLocalY, LastResult.ForceLocalZ),
                ForceMode.Force);
            Body.AddRelativeTorque(new Vector3(
                LastResult.TorqueLocalX, LastResult.TorqueLocalY, LastResult.TorqueLocalZ),
                ForceMode.Force);
            Body.AddForce(new Vector3(
                LastResult.WindForceWorldX,
                LastResult.WindForceWorldY,
                LastResult.WindForceWorldZ),
                ForceMode.Force);

            Vector3 bodyRate = Quaternion.Inverse(Body.rotation) * Body.angularVelocity;
            Body.AddRelativeTorque(
                GyroscopicTerm.BodyTorqueAtMidpoint(
                    bodyRate, Body.inertiaTensor, PhysicsStepSettings.StepSeconds),
                ForceMode.Force);
        }

        /// <summary>
        /// Shut the firmware down and remove everything this scene created.
        /// ファームを終了し、この場面が作ったものを全て片づける。
        /// </summary>
        public void Dispose()
        {
            firmware?.Dispose();
            firmware = null;

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
