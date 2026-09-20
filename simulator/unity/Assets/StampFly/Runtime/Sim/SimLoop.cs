/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the simulation loop).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using StampFly.Input;
using StampFly.Native;
using UnityEngine;

namespace StampFly.Sim
{
    /// <summary>
    /// Drives one tick of the closed loop at a time: the unmodified C++ firmware
    /// on one side, PhysX on the other, 2.5 ms apart.
    ///
    /// 閉ループを 1 刻みずつ回す。片側に無改変の C++ ファームウェア、もう片側に
    /// PhysX を置き、2.5 ms ごとに進める。
    ///
    /// ## One tick / 1 刻みの処理
    ///
    /// | Step | Side | Work |
    /// |---|---|---|
    /// | 1 | C# | pack the body's pose, velocities, the PREVIOUS tick's accelerometer reading, the ToF beam and the sticks |
    /// | 2 | C++ | inject that state, advance the firmware 2.5 ms, return the interval-averaged wrench |
    /// | 3 | C# | apply force, torque, wind and the gyroscopic term, then `Physics.Simulate` |
    /// | 4 | C# | accelerometer reading = R⁻¹·((v_after − v_before)/dt − g), for the NEXT tick |
    ///
    /// Step 4 needs no contact model: the velocity change `Physics.Simulate`
    /// produced already contains the contact force, so a body resting on the
    /// floor reads +9.81 on its own up axis, exactly as the real vehicle's
    /// accelerometer does.
    ///
    /// 4 に接触のモデルは要らない。`Physics.Simulate` が生んだ速度変化に接触力が
    /// すでに入っているので、床に静止した機体は実機の加速度計と同じく自分の上方向に
    /// +9.81 を読む。
    ///
    /// ## What C# must NOT do / C# がしてはならないこと
    ///
    /// No frame conversion. `Rigidbody.position`, `.rotation`,
    /// `.linearVelocity` and `.angularVelocity` go across unchanged — no sign
    /// flip, no axis swap, and the angular velocity stays in the WORLD frame.
    /// The NED/FRD conversion lives in C++, in `frames_unity.hpp`, and nowhere
    /// else. The force and torque that come back are already in Unity's body
    /// frame, so they go straight into `AddRelativeForce` and
    /// `AddRelativeTorque` — never scaled by `WrenchSeconds`, which is a
    /// diagnostic.
    ///
    /// 座標変換をしない。`Rigidbody` の `position`・`rotation`・`linearVelocity`・
    /// `angularVelocity` はそのまま渡す ― 符号の反転も軸の入替もせず、角速度は
    /// **世界系**のままにする。NED／FRD への変換は C++ の `frames_unity.hpp` だけで
    /// 行う。返る力とトルクは Unity の機体系なので、そのまま
    /// `AddRelativeForce`・`AddRelativeTorque` へ渡す ― `WrenchSeconds` で
    /// 割ったり掛けたりしない。あれは診断用である。
    ///
    /// @design docs/plans/unity-simulator.md §3 1 刻みの処理, §5 段階 3
    /// @design simulator/unity/native/bridge/sfu_api.h
    /// </summary>
    [RequireComponent(typeof(Rigidbody))]
    public sealed class SimLoop : MonoBehaviour
    {
        /// <summary>Raised after every power cycle, once the firmware is up. / 電源の入れ直しのたび、ファームが起きた後に発火する。</summary>
        public event Action Booted;

        /// <summary>
        /// Called once a frame with the firmware, so somebody else can drain its
        /// log ring. The loop does not decide where a line goes: the structured
        /// log that carries `run_id` belongs to another assembly, and draining
        /// the ring never affects the simulation, so a frame with no listener
        /// flies exactly as one with a listener does.
        /// フレームごとに 1 回、ファームとともに呼ばれる。ログのリングを空ける役は
        /// 他にあるからである。行の行き先をループは決めない。`run_id` を持つ構造化
        /// ログは別のアセンブリのもので、リングを空けてもシミュレーションは変わら
        /// ないので、聞き手の無いフレームも在るフレームと全く同じように飛ぶ。
        /// </summary>
        public event Action<IFirmware> DrainLog;

        [Tooltip("Where the WebGL build serves the firmware module from. " +
                 "WebGL ビルドがファームのモジュールを配信する場所。")]
        public string firmwareUrl = WebGlFirmware.DefaultModuleUrl;

        [Tooltip("Height above the floor the vehicle rests at [m]. " +
                 "機体が床に静止する高さ [m]。")]
        public float restingHeightMeters = VehicleBody.RestingCentreHeight;

        [Tooltip("Whether the firmware calibrates at boot, as the real vehicle does. " +
                 "実機と同じく起動時に校正するか。")]
        public bool bootCalibration = true;

        [Tooltip("Whether the battery's sag and discharge are modelled. " +
                 "電池の電圧降下と放電を模擬するか。")]
        public bool batteryModel = true;

        private readonly SimClock clock = new SimClock();
        private readonly DownwardRangefinder rangefinder = new DownwardRangefinder();

        private IFirmware firmware;
        private IRcSource rcSource;
        private Rigidbody body;

        private SfuStepOut lastResult;
        private RangeReading lastRange;
        private Vector3 accelerometerReading;
        private Vector3 spawnPosition;
        private Quaternion spawnRotation = Quaternion.identity;
        private int powerCycles;

        /// <summary>The clock, for the panel and for the command surface. / 時計。表示板と操作の受け口のため。</summary>
        public SimClock Clock => clock;

        /// <summary>What the last tick returned. / 直前の刻みが返したもの。</summary>
        public SfuStepOut LastResult => lastResult;

        /// <summary>What the downward beam last found. / 下向きの光線が直前に見つけたもの。</summary>
        public RangeReading LastRange => lastRange;

        /// <summary>The firmware, or null before the first power-on. / ファーム。最初の電源投入の前は null。</summary>
        public IFirmware Firmware => firmware;

        /// <summary>How many times the power has been cycled. / 電源を入れ直した回数。</summary>
        public int PowerCycles => powerCycles;

        /// <summary>
        /// Where the sticks come from. Assigning null puts every stick at centre,
        /// which is how the relay overrides a person's input without fighting it.
        /// スティックの値をどこから取るか。null を入れると全て中央になる。中継が
        /// 人の操作と競わずに上書きするための仕組みでもある。
        /// </summary>
        public IRcSource RcSource
        {
            get => rcSource;
            set => rcSource = value;
        }

        private void Awake()
        {
            body = GetComponent<Rigidbody>();
            PhysicsStepSettings.Apply();
            spawnPosition = body.position;
            spawnRotation = body.rotation;
            rcSource = new KeyboardRc();
        }

        private void Start()
        {
            PowerOn();
        }

        private void OnDestroy()
        {
            firmware?.Dispose();
            firmware = null;
        }

        /// <summary>
        /// Place the vehicle before the first tick. The world package hands
        /// already-converted Unity coordinates, so nothing is transformed here.
        /// 最初の刻みの前に機体を置く。空間の一式が変換済みの Unity の座標を渡して
        /// くるので、ここでは何も変換しない。
        /// </summary>
        public void SetSpawn(Vector3 position, Quaternion rotation)
        {
            spawnPosition = position;
            spawnRotation = rotation;
            MoveToSpawn();
        }

        /// <summary>
        /// Build a firmware and start it. Each call is one power-on: the
        /// firmware's tasks hold statics that cannot be reset in place, so the
        /// old instance is disposed and a new one takes its place.
        /// ファームを作って起こす。1 回の呼び出しが 1 回の電源投入にあたる。
        /// ファームのタスクはその場では戻せない静的変数を持つので、古い実体を
        /// 破棄して新しいものに入れ替える。
        /// </summary>
        public void PowerOn()
        {
            firmware?.Dispose();
            firmware = FirmwareFactory.Create();
            firmware.BeginLoad();

            clock.Reset();
            MoveToSpawn();
            accelerometerReading = new Vector3(
                0.0f, AccelerometerModel.GravityMetersPerSecondSquared, 0.0f);
            lastResult = default;
            powerCycles += 1;

            (rcSource as KeyboardRc)?.Reset();
        }

        /// <summary>Put the vehicle back at its spawn, without restarting the firmware. / ファームを再起動せず、機体を出発点へ戻す。</summary>
        public void MoveToSpawn()
        {
            body.position = spawnPosition;
            body.rotation = spawnRotation;
            body.linearVelocity = Vector3.zero;
            body.angularVelocity = Vector3.zero;
            Physics.SyncTransforms();
        }

        private void Update()
        {
            bool isLoading = firmware.Status == FirmwareStatus.Loading ||
                             firmware.Status == FirmwareStatus.Idle;
            if (isLoading)
            {
                return;
            }

            bool needsBoot = firmware.Status == FirmwareStatus.Loaded;
            if (needsBoot)
            {
                BootFirmware();
                return;
            }

            bool cannotRun = firmware.Status != FirmwareStatus.Running;
            if (cannotRun)
            {
                return;
            }

            RunFrame();
        }

        /// <summary>Power on the loaded firmware with this scene's settings. / 読み込んだファームを、この場面の設定で起こす。</summary>
        private void BootFirmware()
        {
            var config = new SfuConfig
            {
                BatteryModel = batteryModel ? 1 : 0,
                BootCalibration = bootCalibration ? 1 : 0,
                // Unity owns the rigid body: every tick injects this scene's
                // state and the plant only accumulates forces.
                // 剛体は Unity が持つ。毎刻みこの場面の状態を注入し、プラントは
                // 力を溜めるだけになる。
                HostOwnsBody = 1,
                StartHeightMeters = restingHeightMeters,
            };

            int status = firmware.Boot(config);
            bool bootFailed = status != SfuAbi.Ok;
            if (bootFailed)
            {
                Debug.LogError($"[SimLoop] sfu_boot failed: {SfuAbi.Describe(status)} " +
                               $"— {firmware.LastError}");
                return;
            }

            Booted?.Invoke();
        }

        /// <summary>
        /// Run this frame's share of ticks, timing the firmware calls apart from
        /// the rest so the panel can report what one tick costs.
        /// このフレームのぶんの刻みを進める。ファームの呼び出しだけを他と分けて
        /// 計り、1 刻みの所要時間を表示板に出せるようにする。
        /// </summary>
        private void RunFrame()
        {
            double frameSeconds = Time.unscaledDeltaTime;
            int ticks = clock.TicksForFrame(frameSeconds);

            double firmwareSeconds = 0.0;
            for (int tick = 0; tick < ticks; tick++)
            {
                firmwareSeconds += RunOneTick();
            }

            clock.RecordFrame(frameSeconds, ticks, firmwareSeconds);
            DrainLog?.Invoke(firmware);
        }

        /// <summary>
        /// One 2.5 ms tick, in the order the plan's §3 table gives. Returns the
        /// real time the firmware call itself took, apart from PhysX.
        /// 計画 §3 の表の順に 1 刻み（2.5 ms）進める。戻り値は、PhysX とは別に
        /// ファームの呼び出しそのものに掛かった実時間である。
        /// </summary>
        private double RunOneTick()
        {
            SfuStepIn input = PackState();

            double callStart = Time.realtimeSinceStartupAsDouble;
            int status = firmware.Step(input, ref lastResult);
            double callSeconds = Time.realtimeSinceStartupAsDouble - callStart;

            bool stepFailed = status != SfuAbi.Ok;
            if (stepFailed)
            {
                Debug.LogError($"[SimLoop] sfu_step failed: {SfuAbi.Describe(status)}");
                clock.Pause();
                return callSeconds;
            }

            Vector3 velocityBefore = body.linearVelocity;
            ApplyWrench();
            Physics.Simulate(PhysicsStepSettings.StepSeconds);

            accelerometerReading = AccelerometerModel.Read(
                velocityBefore, body.linearVelocity, body.rotation,
                PhysicsStepSettings.StepSeconds);

            return callSeconds;
        }

        /// <summary>
        /// Everything the firmware needs about this tick's starting state, in
        /// Unity's own conventions. Nothing here is converted.
        /// この刻みの始めの状態のうち、ファームが要るものすべてを Unity 自身の
        /// 規約で詰める。ここで変換するものは無い。
        /// </summary>
        private SfuStepIn PackState()
        {
            Vector3 position = body.position;
            Quaternion rotation = body.rotation;
            Vector3 velocity = body.linearVelocity;
            Vector3 angularVelocity = body.angularVelocity;

            lastRange = rangefinder.Measure(position, rotation);
            RcFrame sticks = rcSource != null ? rcSource.Read() : RcFrame.Centred;

            return new SfuStepIn
            {
                PositionX = position.x, PositionY = position.y, PositionZ = position.z,
                RotationX = rotation.x, RotationY = rotation.y,
                RotationZ = rotation.z, RotationW = rotation.w,
                VelocityWorldX = velocity.x,
                VelocityWorldY = velocity.y,
                VelocityWorldZ = velocity.z,
                AngularVelocityWorldX = angularVelocity.x,
                AngularVelocityWorldY = angularVelocity.y,
                AngularVelocityWorldZ = angularVelocity.z,
                AccelLocalX = accelerometerReading.x,
                AccelLocalY = accelerometerReading.y,
                AccelLocalZ = accelerometerReading.z,
                RangeDownMeters = lastRange.Distance,
                RangeDownValid = lastRange.IsValid ? 1 : 0,
                GroundHeightMeters = lastRange.HeightAboveSurface,
                RcThrottle = sticks.Throttle,
                RcRoll = sticks.Roll,
                RcPitch = sticks.Pitch,
                RcYaw = sticks.Yaw,
                RcFlags = sticks.Flags,
                StepMicroseconds = SimClock.TickMicroseconds,
            };
        }

        /// <summary>
        /// Apply what the rotors produced, plus the gyroscopic term PhysX leaves
        /// out. The engine holds the world angular velocity constant for a
        /// torque-free body, which for this vehicle's asymmetric inertia swings
        /// the angular momentum 15.16 degrees per second; the term is evaluated
        /// at the step's MIDPOINT, which measured 0.006% growth in |I·omega|
        /// against 10.8% for the step-start form.
        ///
        /// ロータが出したものと、PhysX が抜かしているジャイロ項を掛ける。エンジンは
        /// トルクの無い剛体の世界系の角速度を一定に保つので、この機体の非対称な
        /// 慣性では角運動量の向きが 1 秒に 15.16 度ずれる。項は刻みの**中点**で
        /// 評価する。実測で |I·omega| の増分は、先頭評価の 10.8% に対しこの形では
        /// 0.006% だった。
        /// </summary>
        private void ApplyWrench()
        {
            body.AddRelativeForce(new Vector3(
                lastResult.ForceLocalX, lastResult.ForceLocalY, lastResult.ForceLocalZ),
                ForceMode.Force);

            body.AddRelativeTorque(new Vector3(
                lastResult.TorqueLocalX, lastResult.TorqueLocalY, lastResult.TorqueLocalZ),
                ForceMode.Force);

            body.AddForce(new Vector3(
                lastResult.WindForceWorldX,
                lastResult.WindForceWorldY,
                lastResult.WindForceWorldZ),
                ForceMode.Force);

            Vector3 bodyRate = Quaternion.Inverse(body.rotation) * body.angularVelocity;
            Vector3 gyroscopic = GyroscopicTerm.BodyTorqueAtMidpoint(
                bodyRate, body.inertiaTensor, PhysicsStepSettings.StepSeconds);
            body.AddRelativeTorque(gyroscopic, ForceMode.Force);
        }
    }
}
