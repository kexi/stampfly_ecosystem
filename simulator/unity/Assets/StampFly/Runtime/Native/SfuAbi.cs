/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the C ABI, declared in C#).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Runtime.InteropServices;

namespace StampFly.Native
{
    /// <summary>
    /// The constants and return codes of <c>simulator/unity/native/bridge/sfu_api.h</c>,
    /// written out here because C# cannot include a C header. The header is the
    /// specification; this file is its copy, and <see cref="SfuStructSizes"/>
    /// checks the copy against the module at start-up rather than trusting it.
    ///
    /// <c>simulator/unity/native/bridge/sfu_api.h</c> の定数と戻り値。C# は C の
    /// ヘッダを取り込めないので、ここに書き下ろしてある。仕様はヘッダの側であり、
    /// この写しが合っているかは信じるのではなく、起動時に
    /// <see cref="SfuStructSizes"/> がモジュールと突き合わせる。
    ///
    /// @design simulator/unity/native/bridge/sfu_api.h
    /// </summary>
    public static class SfuAbi
    {
        /// <summary>The ABI revision this declaration follows. / この宣言が従う ABI の版。</summary>
        public const int Version = 2;

        /// <summary>Number of motors, fixed by the airframe. / モータの数。機体で決まる。</summary>
        public const int MotorCount = 4;

        /// <summary>Longest parameter name including the terminator. / パラメータ名の最大長（終端を含む）。</summary>
        public const int ParamNameMax = 64;

        /// <summary>Longest log tag including the terminator. / ログのタグの最大長（終端を含む）。</summary>
        public const int LogTagMax = 32;

        /// <summary>Longest log message including the terminator. / ログ本文の最大長（終端を含む）。</summary>
        public const int LogMessageMax = 224;

        /// <summary>Longest single step, in microseconds. / 1 刻みの上限（マイクロ秒）。</summary>
        public const uint StepMicrosecondsMax = 100000u;

        /// <summary>The plant's physics sub-step, in microseconds. / プラントの物理の副刻み（マイクロ秒）。</summary>
        public const uint PlantSubstepMicroseconds = 250u;

        // Return codes. Zero is success and every failure is negative, so a
        // caller can test `< 0` without knowing the whole list.
        // 戻り値。0 が成功で失敗は全て負。呼び出し側は一覧を知らなくても
        // `< 0` で判定できる。
        public const int Ok = 0;
        public const int ErrorAlreadyBooted = -1;
        public const int ErrorNotBooted = -2;
        public const int ErrorStructSize = -3;
        public const int ErrorNullArgument = -4;
        public const int ErrorSchedulerStall = -5;
        public const int ErrorUnknownParam = -6;
        public const int ErrorParamRejected = -7;
        public const int ErrorBadIndex = -8;
        public const int ErrorBadArgument = -9;
        public const int ErrorShutDown = -10;

        // Which struct sfu_struct_size asks about.
        // sfu_struct_size が尋ねる構造体。
        public const int StructConfig = 0;
        public const int StructStepIn = 1;
        public const int StructStepOut = 2;
        public const int StructParamInfo = 3;
        public const int StructLogRecord = 4;

        // The control-packet flag bits, as firmware/common/protocol carries them.
        // 制御パケットのフラグのビット。firmware/common/protocol が持つものと同じ。
        public const byte FlagArm = 0x01;
        public const byte FlagFlip = 0x02;
        public const byte FlagMode = 0x04;
        public const byte FlagAltitudeMode = 0x08;
        public const byte FlagPositionMode = 0x10;

        /// <summary>
        /// Turns a return code into the text the header gives it, for a message
        /// a person reads. Unknown codes are reported as themselves.
        /// 戻り値をヘッダが与える語に直す。人が読む文のため。知らない値は
        /// そのまま数で示す。
        /// </summary>
        public static string Describe(int status)
        {
            switch (status)
            {
                case Ok: return "OK";
                case ErrorAlreadyBooted: return "already booted";
                case ErrorNotBooted: return "not booted";
                case ErrorStructSize: return "struct size mismatch";
                case ErrorNullArgument: return "null argument";
                case ErrorSchedulerStall: return "scheduler stall";
                case ErrorUnknownParam: return "unknown parameter";
                case ErrorParamRejected: return "parameter rejected";
                case ErrorBadIndex: return "bad index";
                case ErrorBadArgument: return "bad argument";
                case ErrorShutDown: return "shut down";
                default: return $"status {status}";
            }
        }
    }

    /// <summary>
    /// The settings fixed before the firmware's tasks first run —
    /// <c>SfuConfig</c>, 48 bytes. A browser has no environment variables, so
    /// what <c>emu_main.cpp</c> read from those arrives here instead.
    ///
    /// ファームのタスクが最初に動き出す前に決まる設定。<c>SfuConfig</c>、48 バイト。
    /// ブラウザに環境変数は無いので、<c>emu_main.cpp</c> がそこから読んでいたものは
    /// ここで渡す。
    /// </summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct SfuConfig
    {
        /// <summary>Must be <c>sizeof</c> of THIS declaration. / この宣言の <c>sizeof</c> を入れる。</summary>
        public uint StructSize;

        /// <summary>1 = model sag and discharge, 0 = ideal supply. / 1 = 電圧降下と放電を模擬、0 = 理想電源。</summary>
        public int BatteryModel;

        /// <summary>Near-floor extra lift; 0 disables it. / 地面効果の増分。0 で無効。</summary>
        public float GroundEffectGain;

        /// <summary>1-3 Hz lateral turbulence force [N]; 0 disables. / 1〜3 Hz の横方向の乱流 [N]。0 で無効。</summary>
        public float TurbulenceNewtons;

        /// <summary>Real-vs-ideal thrust scale; &lt;= 0 keeps the default. / 推力の実効係数。0 以下で既定のまま。</summary>
        public float ThrustEfficiency;

        /// <summary>Roll/pitch differential-torque scale; &lt;= 0 keeps the default. / 差動トルクの係数。0 以下で既定のまま。</summary>
        public float TorqueAuthority;

        /// <summary>Duty-path transport delay [ms]; 0 disables. / duty 経路の輸送遅れ [ms]。0 で無効。</summary>
        public float MotorDelayMilliseconds;

        /// <summary>0 = off, 1 = n0, 2 = n1, 3 = n2. / 0 = 無し、1 = n0、2 = n1、3 = n2。</summary>
        public int NoiseLevel;

        /// <summary>Random seed; used only when the noise level is above zero. / 乱数の種。ノイズ有りのときだけ使う。</summary>
        public uint NoiseSeed;

        /// <summary>1 = the firmware calibrates at boot, as on the real vehicle. / 1 = 実機と同じく起動時に校正する。</summary>
        public int BootCalibration;

        /// <summary>
        /// 1 = Unity owns the rigid body and injects its state every tick;
        /// 0 = the plant integrates its own. The Unity build is always 1.
        /// 1 = Unity が剛体を持ち、毎刻み状態を注入する。0 = プラントが自分で
        /// 積分する。Unity 版は常に 1。
        /// </summary>
        public int HostOwnsBody;

        /// <summary>Resting height above the floor [m], on Unity's +Y. / 床からの静止高 [m]。Unity の +Y。</summary>
        public float StartHeightMeters;
    }

    /// <summary>
    /// Everything the host has at the start of one tick — <c>SfuStepIn</c>,
    /// 96 bytes. Every value is in Unity's own conventions: left-handed, Y up,
    /// the quaternion in x,y,z,w order, and the angular velocity in the WORLD
    /// frame. No sign flip and no axis swap happens on this side.
    ///
    /// ホストが 1 刻みの始めに持っている情報すべて。<c>SfuStepIn</c>、96 バイト。
    /// 値はすべて Unity 自身の規約 ― 左手系・Y 上・クォータニオンは x,y,z,w の
    /// 順・角速度は**世界系** ― である。この側で符号を反転させることも軸を入れ
    /// 替えることもしない。
    /// </summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct SfuStepIn
    {
        /// <summary>Must be <c>sizeof</c> of THIS declaration. / この宣言の <c>sizeof</c> を入れる。</summary>
        public uint StructSize;

        /// <summary><c>Rigidbody.position</c> [m]. / <c>Rigidbody.position</c> [m]。</summary>
        public float PositionX;
        public float PositionY;
        public float PositionZ;

        /// <summary><c>Rigidbody.rotation</c>, x,y,z,w order. / <c>Rigidbody.rotation</c>、x,y,z,w の順。</summary>
        public float RotationX;
        public float RotationY;
        public float RotationZ;
        public float RotationW;

        /// <summary><c>Rigidbody.linearVelocity</c> [m/s], world frame. / 世界系の速度 [m/s]。</summary>
        public float VelocityWorldX;
        public float VelocityWorldY;
        public float VelocityWorldZ;

        /// <summary><c>Rigidbody.angularVelocity</c> [rad/s], WORLD frame. / 世界系の角速度 [rad/s]。</summary>
        public float AngularVelocityWorldX;
        public float AngularVelocityWorldY;
        public float AngularVelocityWorldZ;

        /// <summary>
        /// The accelerometer READING [m/s²] in the body frame, computed from the
        /// PREVIOUS tick's velocity change. Contact forces are already in it.
        /// 機体系の加速度計の**測定値** [m/s²]。**前の**刻みの速度変化から作る。
        /// 接触力はすでに入っている。
        /// </summary>
        public float AccelLocalX;
        public float AccelLocalY;
        public float AccelLocalZ;

        /// <summary>Downward ToF distance along the beam [m]. / 下向き ToF の光線方向の距離 [m]。</summary>
        public float RangeDownMeters;

        /// <summary>1 = the beam hit a surface within range. / 1 = 光線が有効範囲内で面に当たった。</summary>
        public int RangeDownValid;

        /// <summary>Height above the surface below the rotors [m]. / ロータの下の面からの高さ [m]。</summary>
        public float GroundHeightMeters;

        /// <summary>Raw 12-bit ADC, centre 2048. / 12 bit の生 ADC。中央 2048。</summary>
        public ushort RcThrottle;
        public ushort RcRoll;
        public ushort RcPitch;
        public ushort RcYaw;

        /// <summary>ARM and mode bits, as the ControlPacket carries them. / ARM とモードのビット。ControlPacket と同じ。</summary>
        public byte RcFlags;

        /// <summary>
        /// Named rather than left to the compiler, so this declaration is a
        /// field-for-field copy of the C one with no invisible bytes.
        /// コンパイラに入れさせず名前を付けてある。C 側の宣言の欄ごとの写しにして、
        /// 見えない byte を残さないため。
        /// </summary>
        public byte ReservedPadding0;
        public byte ReservedPadding1;
        public byte ReservedPadding2;

        /// <summary>
        /// How far to advance, in whole MICROSECONDS. Must be in
        /// (0, <see cref="SfuAbi.StepMicrosecondsMax"/>]; anything else is refused
        /// rather than clamped, so N ticks land the clock on exactly N·dt.
        /// どれだけ進めるか。整数の**マイクロ秒**。範囲は
        /// (0, <see cref="SfuAbi.StepMicrosecondsMax"/>]。外れた値は頭打ちにせず
        /// 拒むので、N 刻みはちょうど N·dt に時計を置く。
        /// </summary>
        public uint StepMicroseconds;
    }

    /// <summary>
    /// Everything the host needs after one tick — <c>SfuStepOut</c>, 168 bytes.
    /// The force and torque are interval AVERAGES in the body-local Unity frame,
    /// so they go straight into <c>AddRelativeForce</c> and
    /// <c>AddRelativeTorque</c> — never multiplied or divided by
    /// <see cref="WrenchSeconds"/>, which is only there for diagnosis.
    ///
    /// ホストが 1 刻みの後に要する情報すべて。<c>SfuStepOut</c>、168 バイト。
    /// 力とトルクは Unity の機体ローカル系での**区間平均**なので、そのまま
    /// <c>AddRelativeForce</c>・<c>AddRelativeTorque</c> へ渡す ―
    /// <see cref="WrenchSeconds"/> を掛けたり割ったりしない。同欄は診断用である。
    /// </summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct SfuStepOut
    {
        /// <summary>Must be <c>sizeof</c> of THIS declaration. / この宣言の <c>sizeof</c> を入れる。</summary>
        public uint StructSize;

        /// <summary>Actuator force [N], body-local Unity frame. / アクチュエータの力 [N]。Unity の機体系。</summary>
        public float ForceLocalX;
        public float ForceLocalY;
        public float ForceLocalZ;

        /// <summary>Actuator torque [N·m], body-local Unity frame. / トルク [N·m]。Unity の機体系。</summary>
        public float TorqueLocalX;
        public float TorqueLocalY;
        public float TorqueLocalZ;

        /// <summary>Wind and turbulence force [N], world Unity frame. / 風と乱流の力 [N]。Unity の世界系。</summary>
        public float WindForceWorldX;
        public float WindForceWorldY;
        public float WindForceWorldZ;

        /// <summary>The interval those averages cover [s]; diagnosis only. / 平均が覆う区間の長さ [s]。診断用。</summary>
        public float WrenchSeconds;

        /// <summary>Commanded duty [0..1], M1..M4. / 指令 duty [0..1]。M1〜M4。</summary>
        public float MotorDuty0;
        public float MotorDuty1;
        public float MotorDuty2;
        public float MotorDuty3;

        /// <summary>Propeller speed [rad/s], M1..M4. / プロペラの角速度 [rad/s]。M1〜M4。</summary>
        public float MotorOmega0;
        public float MotorOmega1;
        public float MotorOmega2;
        public float MotorOmega3;

        /// <summary>Terminal voltage [V]. / 端子電圧 [V]。</summary>
        public float BatteryVoltage;

        /// <summary>The virtual clock after this step [µs]. / この刻みの後の仮想時刻 [µs]。</summary>
        public long NowMicroseconds;

        /// <summary>1 = motors armed. / 1 = モータが ARM 済み。</summary>
        public int Armed;

        /// <summary><c>sf::FlightState</c> as an integer. / <c>sf::FlightState</c> の番号。</summary>
        public int FlightState;

        /// <summary><c>sf::FlightMode</c> as an integer. / <c>sf::FlightMode</c> の番号。</summary>
        public int FlightMode;

        /// <summary>The firmware's attitude estimate, Unity x,y,z,w. / ファームの姿勢推定。Unity の x,y,z,w。</summary>
        public float EstimatedRotationX;
        public float EstimatedRotationY;
        public float EstimatedRotationZ;
        public float EstimatedRotationW;

        /// <summary>The firmware's position estimate [m], Unity world. / ファームの位置推定 [m]。Unity の世界系。</summary>
        public float EstimatedPositionX;
        public float EstimatedPositionY;
        public float EstimatedPositionZ;

        /// <summary>True position [m], Unity world. / 真の位置 [m]。Unity の世界系。</summary>
        public float TruthPositionX;
        public float TruthPositionY;
        public float TruthPositionZ;

        /// <summary>True attitude, Unity x,y,z,w. / 真の姿勢。Unity の x,y,z,w。</summary>
        public float TruthRotationX;
        public float TruthRotationY;
        public float TruthRotationZ;
        public float TruthRotationW;

        /// <summary>
        /// The result of the call — the ONLY place a wasm caller can read it,
        /// because Asyncify replaces what the return value carries. Written last
        /// on every path, so zeroing it beforehand tells "the bridge wrote it"
        /// from "nothing happened".
        /// この呼び出しの結果。wasm の呼び出し側がこれを読める場所はここだけで
        /// ある。Asyncify が戻り値の中身を置き換えるためである。どの経路でも
        /// 最後に書かれるので、呼ぶ前に 0 を入れておけば「橋渡しが書いた」と
        /// 「何も起きなかった」を見分けられる。
        /// </summary>
        public int Status;

        /// <summary>Named padding, so no invisible byte is guessed at. / 名前を付けた詰め物。見えない byte を作らないため。</summary>
        public int ReservedTail;
    }

    /// <summary>
    /// One parameter's identity — <c>SfuParamInfo</c>, 84 bytes.
    /// パラメータ 1 個の素性。<c>SfuParamInfo</c>、84 バイト。
    /// </summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct SfuParamInfo
    {
        /// <summary>Must be <c>sizeof</c> of THIS declaration. / この宣言の <c>sizeof</c> を入れる。</summary>
        public uint StructSize;

        /// <summary>Null-terminated parameter name. / null 終端のパラメータ名。</summary>
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = SfuAbi.ParamNameMax)]
        public byte[] Name;

        /// <summary>0 = float, 1 = bool, 2 = int. / 0 = float、1 = bool、2 = int。</summary>
        public int Type;

        public float DefaultValue;
        public float MinValue;
        public float MaxValue;
    }

    /// <summary>
    /// One firmware log record before it is made into a line of text —
    /// <c>SfuLogRecord</c>, 272 bytes. The four things <c>ESP_LOGx</c> actually
    /// had, so a host writes a structured record without parsing a string back
    /// into its parts.
    ///
    /// 文字列 1 本にする前の、ファームのログ 1 記録。<c>SfuLogRecord</c>、272 バイト。
    /// <c>ESP_LOGx</c> が実際に持っていた 4 つなので、ホストは文字列を解析して
    /// 部分へ戻すことなく構造化された記録を書ける。
    /// </summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct SfuLogRecord
    {
        /// <summary>Must be <c>sizeof</c> of THIS declaration. / この宣言の <c>sizeof</c> を入れる。</summary>
        public uint StructSize;

        /// <summary>1 = error, 2 = warn, 3 = info, 4 = debug, 5 = verbose. / 段。</summary>
        public int Level;

        /// <summary>The virtual clock when the firmware logged it [µs]. / ファームが書いた時点の仮想時刻 [µs]。</summary>
        public long SimMicroseconds;

        /// <summary>Null-terminated <c>ESP_LOGx</c> tag. / null 終端の <c>ESP_LOGx</c> のタグ。</summary>
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = SfuAbi.LogTagMax)]
        public byte[] Tag;

        /// <summary>Null-terminated body, the format already applied. / null 終端の本文。書式は適用済み。</summary>
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = SfuAbi.LogMessageMax)]
        public byte[] Message;
    }
}
