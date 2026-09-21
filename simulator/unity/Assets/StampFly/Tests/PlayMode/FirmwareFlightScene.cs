/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — flight test scaffolding).
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
    /// A scripted flight against the real firmware, run as fast as the machine
    /// allows rather than in real time, so a test can fly ten simulated seconds
    /// inside one frame.
    ///
    /// 実物のファームウェアを相手にした台本どおりの飛行。実時間ではなく機械が許す
    /// 速さで回すので、試験は 1 フレームの中で 10 秒ぶん飛べる。
    ///
    /// It runs the SAME tick sequence `SimLoop` does — pack the state, step the
    /// firmware, apply the wrench and the gyroscopic term, simulate PhysX, and
    /// compute the next accelerometer reading — but without a MonoBehaviour, so
    /// the loop's timing does not have to be faked.
    ///
    /// `SimLoop` と**同じ**刻みの手順を踏む ― 状態を詰め、ファームを刻み、力と
    /// ジャイロ項を掛け、PhysX を進め、次の加速度計の測定値を作る ― が、
    /// MonoBehaviour を通さないので、ループの計時を偽る必要が無い。
    ///
    /// @design docs/plans/unity-simulator.md §5 段階 3
    /// </summary>
    public sealed class FirmwareFlightScene
    {
        /// <summary>A floor wider than any flight these checks produce. / どの飛行より広い床。</summary>
        private const float FloorSizeMeters = 20.0f;
        private const float FloorThicknessMeters = 0.5f;

        private GameObject vehicleObject;
        private GameObject floorObject;
        private IFirmware firmware;
        private Vector3 accelerometerReading;

        /// <summary>The vehicle's rigid body. / 機体の剛体。</summary>
        public Rigidbody Body { get; private set; }

        /// <summary>What the last tick returned. / 直前の刻みが返したもの。</summary>
        public SfuStepOut LastResult;

        /// <summary>The sticks this flight sends. / この飛行が送るスティックの値。</summary>
        public RcFrame Sticks = RcFrame.Centred;

        /// <summary>
        /// An input whose flags vary within a frame, when a flight needs one. Set
        /// it and <see cref="Sticks"/>'s own flags give way to
        /// <see cref="IRcSource.FlagsAt"/> at each tick's virtual time — which is
        /// how a momentary button's pulse is exercised against the real firmware.
        /// フレームの中でフラグが変わる入力。飛行がそれを要るときに設定する。設定すると
        /// <see cref="Sticks"/> 自身のフラグに代わって、刻みごとの仮想時刻で
        /// <see cref="IRcSource.FlagsAt"/> が使われる。モーメンタリボタンのパルスを
        /// 実物のファームウェアに対して動かす方法がこれである。
        /// </summary>
        public IRcSource FlagSource;

        /// <summary>The firmware, so a test can read its status and its errors. / ファーム。試験が状態と誤りを読めるように。</summary>
        public IFirmware Firmware => firmware;

        /// <summary>The floor's height above the world origin [m]. / 世界原点からの床の高さ [m]。</summary>
        public const float FloorTopMeters = 0.0f;

        /// <summary>
        /// Whether the development dylib has been built. Without it there is
        /// nothing to fly, and a test says so rather than failing obscurely.
        /// 開発用の dylib がビルドされているか。無ければ飛ばすものが無いので、
        /// 試験は分かりにくい失敗ではなくその旨を述べる。
        /// </summary>
        public static bool DylibExists => File.Exists(EditorFirmware.DefaultDylibPath());

        /// <summary>
        /// Build the scene and power the firmware on. Returns whether the
        /// firmware reached <see cref="FirmwareStatus.Running"/>.
        /// 場面を作り、ファームの電源を入れる。ファームが
        /// <see cref="FirmwareStatus.Running"/> に達したかを返す。
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
        /// Fly to a virtual time, calling <paramref name="beforeTick"/> with the
        /// clock before each tick so a script can move the sticks. Returns the
        /// status of the tick that failed, or <see cref="SfuAbi.Ok"/>.
        /// 仮想時刻まで飛ばす。刻みの前ごとに <paramref name="beforeTick"/> を時刻
        /// とともに呼ぶので、台本がスティックを動かせる。失敗した刻みの status を
        /// 返す。失敗が無ければ <see cref="SfuAbi.Ok"/>。
        /// </summary>
        public int FlyUntil(double untilSeconds, System.Action<double> beforeTick)
        {
            double nowSeconds = LastResult.NowMicroseconds * 1e-6;
            while (nowSeconds < untilSeconds)
            {
                beforeTick?.Invoke(nowSeconds);

                int status = RunOneTick();
                bool tickFailed = status != SfuAbi.Ok;
                if (tickFailed)
                {
                    return status;
                }

                nowSeconds = LastResult.NowMicroseconds * 1e-6;
            }

            return SfuAbi.Ok;
        }

        /// <summary>
        /// One tick, in the order <see cref="SimLoop"/> uses.
        /// <see cref="SimLoop"/> と同じ順序で 1 刻み進める。
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
        /// The state the firmware needs, packed exactly as the loop packs it:
        /// in Unity's own conventions, with no conversion.
        /// ファームが要る状態を、ループと全く同じ形 ― Unity 自身の規約で、変換せず
        /// に ― 詰める。
        /// </summary>
        private SfuStepIn PackState()
        {
            Vector3 position = Body.position;
            Quaternion rotation = Body.rotation;
            Vector3 velocity = Body.linearVelocity;
            Vector3 rate = Body.angularVelocity;
            float height = position.y - FloorTopMeters;

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
                RangeDownMeters = height,
                RangeDownValid =
                    height >= DownwardRangefinder.BlindZoneMeters ? 1 : 0,
                GroundHeightMeters = height,
                RcThrottle = Sticks.Throttle,
                RcRoll = Sticks.Roll,
                RcPitch = Sticks.Pitch,
                RcYaw = Sticks.Yaw,
                RcFlags = FlagsForThisTick(),
                StepMicroseconds = SimClock.TickMicroseconds,
            };
        }

        /// <summary>
        /// The rotors' wrench plus the gyroscopic term PhysX leaves out, the
        /// same four calls the loop makes.
        /// ロータの力と、PhysX が抜かしているジャイロ項。ループが行うのと同じ 4 回の
        /// 呼び出しである。
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
        /// This tick's flag byte, on the firmware's own clock — the same question
        /// <see cref="SimLoop"/> asks per tick. Without a
        /// <see cref="FlagSource"/> the sticks' own flags stand.
        /// この刻みのフラグのバイト。ファーム自身の時計で決める ―
        /// <see cref="SimLoop"/> が刻みごとに行うのと同じ問いである。
        /// <see cref="FlagSource"/> が無ければスティック自身のフラグを使う。
        /// </summary>
        private byte FlagsForThisTick()
        {
            bool hasNoFlagSource = FlagSource == null;
            if (hasNoFlagSource)
            {
                return Sticks.Flags;
            }

            return FlagSource.FlagsAt(Sticks, LastResult.NowMicroseconds);
        }

        /// <summary>The vehicle's height above the floor [m]. / 床からの機体の高さ [m]。</summary>
        public float AltitudeMeters => Body.position.y - FloorTopMeters;

        /// <summary>
        /// Shut the firmware down and remove everything this scene created.
        /// The firmware's shutdown is what lets the next flight open a fresh
        /// image of the dylib.
        /// ファームを終了し、この場面が作ったものを全て片づける。次の飛行が dylib の
        /// 新しい像を開けるのは、このファームの終了があるからである。
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
