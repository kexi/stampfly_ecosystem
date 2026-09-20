/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the WebGL firmware).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.Runtime.InteropServices;
using StampFly.Core;

namespace StampFly.Native
{
    /// <summary>
    /// The firmware as a separate wasm module, reached through
    /// <c>Plugins/WebGL/SfuFirmware.jslib</c>. Loading is asynchronous, so
    /// <see cref="BeginLoad"/> starts it and <see cref="Status"/> is polled;
    /// every call after that is plain and synchronous.
    ///
    /// ファームを別の wasm モジュールとして持ち、
    /// <c>Plugins/WebGL/SfuFirmware.jslib</c> 経由で呼ぶ。読み込みは非同期なので
    /// <see cref="BeginLoad"/> が始め、<see cref="Status"/> をポーリングする。
    /// それ以降の呼び出しはただの同期呼び出しである。
    ///
    /// Marshalling is by hand: each struct is pinned, its address handed to the
    /// .jslib, and the .jslib copies the bytes between the two heaps. The two
    /// modules have separate memories, so there is nothing to share.
    ///
    /// marshal は手作業で行う。構造体を固定してその番地を .jslib へ渡し、.jslib が
    /// 2 つのヒープの間でバイトを写す。2 つのモジュールは別々のメモリを持つので、
    /// 共有できるものは無い。
    ///
    /// Outside WebGL every call is a stub reporting <see cref="FirmwareStatus.Failed"/>,
    /// so a scene holding this still opens in the editor; the editor uses
    /// <see cref="EditorFirmware"/> instead.
    /// WebGL 以外では全ての呼び出しが <see cref="FirmwareStatus.Failed"/> を返す
    /// 張りぼてになり、これを持つ場面もエディタで開ける。エディタは
    /// <see cref="EditorFirmware"/> を使う。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（スレッド）
    /// </summary>
    public sealed class WebGlFirmware : IFirmware
    {
        /// <summary>
        /// Where the firmware module is served from, relative to the player's
        /// own page. <c>WebGLBuilder</c> copies the pair there after a build.
        /// ファームのモジュールの配信元。プレイヤー自身のページからの相対で書く。
        /// ビルドの後に <c>WebGLBuilder</c> が 2 つのファイルをそこへ複写する。
        /// </summary>
        public const string DefaultModuleUrl = "StreamingAssets/sfu_firmware.js";

        private readonly string moduleUrl;
        private string failureReason = string.Empty;

        // Unmanaged buffers the ABI structs are written into, allocated once.
        //
        // They are NOT pinned managed structs: under IL2CPP,
        // `GCHandle.Alloc(aStruct, Pinned)` boxes the value and
        // `AddrOfPinnedObject` answers the box's own address, which is not where
        // the fields begin. The bridge then wrote past the fields and every tick
        // came back with the caller's own sentinel still in `status`. Measured in
        // Chrome on 2026-09-20; unmanaged memory has no such ambiguity.
        //
        // ABI の構造体を書き込む非管理の領域。1 回だけ確保する。
        //
        // 管理された構造体を固定したものでは**ない**。IL2CPP では
        // `GCHandle.Alloc(構造体, Pinned)` が値を箱に入れ、`AddrOfPinnedObject` は
        // その箱自身の番地を答える。そこは欄の始まりではない。結果として橋渡しは
        // 欄の外へ書き、どの刻みも呼び出し側の目印が `status` に残ったまま返って
        // きた。2026-09-20 に Chrome で実測した。非管理の記憶域にこの曖昧さは無い。
        private IntPtr configBuffer = IntPtr.Zero;
        private IntPtr stepInBuffer = IntPtr.Zero;
        private IntPtr stepOutBuffer = IntPtr.Zero;

        /// <summary>
        /// Builds a firmware that will load from <paramref name="url"/>, or from
        /// <see cref="DefaultModuleUrl"/> when that is null or empty.
        /// <paramref name="url"/> から読み込むファームを作る。null か空なら
        /// <see cref="DefaultModuleUrl"/> から読み込む。
        /// </summary>
        public WebGlFirmware(string url = null)
        {
            bool hasUrl = !string.IsNullOrEmpty(url);
            moduleUrl = hasUrl ? url : DefaultModuleUrl;
        }

#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")] private static extern int SfuFirmwareLoad(string url);
        [DllImport("__Internal", EntryPoint = "SfuFirmwareStatus")]
        private static extern int SfuFirmwareStatusCode();
        [DllImport("__Internal")] private static extern IntPtr SfuFirmwareError();
        [DllImport("__Internal")] private static extern int SfuFirmwareAbiVersion();
        [DllImport("__Internal")] private static extern int SfuFirmwareStructSize(int which);
        [DllImport("__Internal")] private static extern int SfuFirmwareBoot(
            IntPtr config, int configSize, int inSize, int outSize, int recordSize, int paramSize);
        [DllImport("__Internal")] private static extern int SfuFirmwareStep(
            IntPtr input, int inSize, IntPtr output, int outSize, int statusOffset);
        [DllImport("__Internal")] private static extern int SfuFirmwareParamCount();
        [DllImport("__Internal")] private static extern int SfuFirmwareParamInfo(
            int index, IntPtr output, int outSize);
        [DllImport("__Internal")] private static extern int SfuFirmwareParamSet(
            string name, double value);
        [DllImport("__Internal")] private static extern int SfuFirmwareParamGet(
            string name, IntPtr value);
        [DllImport("__Internal")] private static extern int SfuFirmwareSetWind(
            float worldX, float worldY, float worldZ);
        [DllImport("__Internal")] private static extern int SfuFirmwareSetMotorHealth(
            int motor, float gain);
        [DllImport("__Internal")] private static extern int SfuFirmwareSetImuBias(
            float accelX, float accelY, float accelZ,
            float gyroX, float gyroY, float gyroZ);
        [DllImport("__Internal")] private static extern int SfuFirmwareLogRead(
            IntPtr output, int outSize);
        [DllImport("__Internal")] private static extern int SfuFirmwareLogDropped();
        [DllImport("__Internal")] private static extern int SfuFirmwareSetLogLevel(int level);
        [DllImport("__Internal")] private static extern void SfuFirmwareRelease();
        [DllImport("__Internal")] private static extern double SfuFirmwareHeapBytes();
        [DllImport("__Internal")] private static extern int SfuFirmwareGeneration();
#endif

        /// <inheritdoc/>
        public FirmwareStatus Status
        {
            get
            {
                bool hasOwnFailure = failureReason.Length > 0;
                if (hasOwnFailure)
                {
                    return FirmwareStatus.Failed;
                }

#if UNITY_WEBGL && !UNITY_EDITOR
                return (FirmwareStatus)SfuFirmwareStatusCode();
#else
                return FirmwareStatus.Failed;
#endif
            }
        }

        /// <inheritdoc/>
        public string LastError
        {
            get
            {
                bool hasOwnFailure = failureReason.Length > 0;
                if (hasOwnFailure)
                {
                    return failureReason;
                }

#if UNITY_WEBGL && !UNITY_EDITOR
                IntPtr pointer = SfuFirmwareError();
                string text = Marshal.PtrToStringUTF8(pointer);
                Marshal.FreeHGlobal(pointer);
                return text ?? string.Empty;
#else
                return "WebGlFirmware only runs in a WebGL player";
#endif
            }
        }

        /// <summary>
        /// WebAssembly memory the firmware module holds [bytes]. Watching it
        /// across power cycles shows whether discarded modules are collected.
        /// ファームのモジュールが持つ WebAssembly メモリ [バイト]。電源の入れ直しを
        /// 挟んで見ると、捨てたモジュールが回収されているかが分かる。
        /// </summary>
        public double HeapBytes
        {
            get
            {
#if UNITY_WEBGL && !UNITY_EDITOR
                return SfuFirmwareHeapBytes();
#else
                return 0.0;
#endif
            }
        }

        /// <summary>How many modules have been created. / これまでに作ったモジュールの数。</summary>
        public int Generation
        {
            get
            {
#if UNITY_WEBGL && !UNITY_EDITOR
                return SfuFirmwareGeneration();
#else
                return 0;
#endif
            }
        }

        /// <inheritdoc/>
        public void BeginLoad()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            AllocateBuffers();
            SfuFirmwareLoad(moduleUrl);
#else
            failureReason = "WebGlFirmware only runs in a WebGL player";
#endif
        }

        /// <inheritdoc/>
        public int Boot(in SfuConfig config)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            string mismatch = SfuStructSizes.CheckAgainstModule(SfuFirmwareStructSize);
            if (mismatch != null)
            {
                failureReason = mismatch;
                return SfuAbi.ErrorStructSize;
            }

            int moduleAbi = SfuFirmwareAbiVersion();
            bool abiDisagrees = moduleAbi != SfuAbi.Version;
            if (abiDisagrees)
            {
                failureReason = $"ABI mismatch: the module follows revision {moduleAbi}, " +
                                $"this build follows {SfuAbi.Version}";
                return SfuAbi.ErrorStructSize;
            }

            SfuConfig local = config;
            local.StructSize = (uint)SfuStructSizes.Config;
            Marshal.StructureToPtr(local, configBuffer, false);

            return SfuFirmwareBoot(
                configBuffer,
                SfuStructSizes.Config,
                SfuStructSizes.StepIn,
                SfuStructSizes.StepOut,
                SfuStructSizes.LogRecord,
                SfuStructSizes.ParamInfo);
#else
            return SfuAbi.ErrorNotBooted;
#endif
        }

        /// <inheritdoc/>
        /// <remarks>
        /// The two buffers are allocated once and reused for the life of this
        /// firmware, because a tick happens 400 times a simulated second and
        /// allocating per tick would churn the heap for nothing.
        /// 2 つの領域はこのファームの生存期間ぶん 1 回だけ確保して使い回す。刻みは
        /// シミュレーションの 1 秒に 400 回あり、刻みごとに確保してもヒープをかき
        /// 回すだけで得るものが無いからである。
        /// </remarks>
        public int Step(in SfuStepIn input, ref SfuStepOut result)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            SfuStepIn local = input;
            local.StructSize = (uint)SfuStructSizes.StepIn;
            result.StructSize = (uint)SfuStructSizes.StepOut;

            Marshal.StructureToPtr(local, stepInBuffer, false);
            Marshal.StructureToPtr(result, stepOutBuffer, false);

            SfuFirmwareStep(
                stepInBuffer, SfuStructSizes.StepIn,
                stepOutBuffer, SfuStructSizes.StepOut,
                SfuStructSizes.StepOutStatusOffset);

            result = Marshal.PtrToStructure<SfuStepOut>(stepOutBuffer);
            return result.Status;
#else
            result.Status = SfuAbi.ErrorNotBooted;
            return SfuAbi.ErrorNotBooted;
#endif
        }

        /// <inheritdoc/>
        public int ParameterCount
        {
            get
            {
#if UNITY_WEBGL && !UNITY_EDITOR
                return SfuFirmwareParamCount();
#else
                return 0;
#endif
            }
        }

        /// <inheritdoc/>
        public int GetParameterInfo(int index, out SfuParamInfo info)
        {
            info = default;
#if UNITY_WEBGL && !UNITY_EDITOR
            IntPtr buffer = Marshal.AllocHGlobal(SfuStructSizes.ParamInfo);
            try
            {
                int status = SfuFirmwareParamInfo(index, buffer, SfuStructSizes.ParamInfo);
                if (status == SfuAbi.Ok)
                {
                    info = Marshal.PtrToStructure<SfuParamInfo>(buffer);
                }
                return status;
            }
            finally
            {
                Marshal.FreeHGlobal(buffer);
            }
#else
            return SfuAbi.ErrorNotBooted;
#endif
        }

        /// <inheritdoc/>
        public int SetParameter(string name, double value)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuFirmwareParamSet(name, value);
#else
            return SfuAbi.ErrorNotBooted;
#endif
        }

        /// <inheritdoc/>
        public int GetParameter(string name, out double value)
        {
            value = 0.0;
#if UNITY_WEBGL && !UNITY_EDITOR
            IntPtr buffer = Marshal.AllocHGlobal(sizeof(double));
            try
            {
                int status = SfuFirmwareParamGet(name, buffer);
                if (status == SfuAbi.Ok)
                {
                    value = Marshal.PtrToStructure<double>(buffer);
                }
                return status;
            }
            finally
            {
                Marshal.FreeHGlobal(buffer);
            }
#else
            return SfuAbi.ErrorNotBooted;
#endif
        }

        /// <inheritdoc/>
        public int SetWind(float worldX, float worldY, float worldZ)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuFirmwareSetWind(worldX, worldY, worldZ);
#else
            return SfuAbi.ErrorNotBooted;
#endif
        }

        /// <inheritdoc/>
        public int SetMotorHealth(int motor, float gain)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuFirmwareSetMotorHealth(motor, gain);
#else
            return SfuAbi.ErrorNotBooted;
#endif
        }

        /// <inheritdoc/>
        public int SetImuBias(float accelX, float accelY, float accelZ,
                              float gyroX, float gyroY, float gyroZ)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuFirmwareSetImuBias(accelX, accelY, accelZ, gyroX, gyroY, gyroZ);
#else
            return SfuAbi.ErrorNotBooted;
#endif
        }

        /// <inheritdoc/>
        public bool TryReadLogRecord(out FirmwareLogRecord record)
        {
            record = default;
#if UNITY_WEBGL && !UNITY_EDITOR
            IntPtr buffer = Marshal.AllocHGlobal(SfuStructSizes.LogRecord);
            try
            {
                int result = SfuFirmwareLogRead(buffer, SfuStructSizes.LogRecord);
                bool wroteOne = result == 1;
                if (!wroteOne)
                {
                    return false;
                }

                SfuLogRecord raw = Marshal.PtrToStructure<SfuLogRecord>(buffer);
                record = FirmwareLogText.From(raw);
                return true;
            }
            finally
            {
                Marshal.FreeHGlobal(buffer);
            }
#else
            return false;
#endif
        }

        /// <inheritdoc/>
        public int DroppedLogRecords()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuFirmwareLogDropped();
#else
            return 0;
#endif
        }

        /// <inheritdoc/>
        public int SetLogLevel(int level)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuFirmwareSetLogLevel(level);
#else
            return SfuAbi.ErrorNotBooted;
#endif
        }

        /// <summary>
        /// Throw the module away. This is the power cycle: the caller builds
        /// another <see cref="WebGlFirmware"/> and loads a fresh module, and the
        /// browser collects the old one.
        /// モジュールを捨てる。これが電源の入れ直しである。呼び出し側は別の
        /// <see cref="WebGlFirmware"/> を作って新しいモジュールを読み込み、古い方は
        /// ブラウザが回収する。
        /// </summary>
        public void Dispose()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            SfuFirmwareRelease();
            FreeBuffers();
#endif
        }

#if UNITY_WEBGL && !UNITY_EDITOR
        /// <summary>Take the three buffers, once. / 3 つの領域を 1 回だけ取る。</summary>
        private void AllocateBuffers()
        {
            bool alreadyTaken = configBuffer != IntPtr.Zero;
            if (alreadyTaken)
            {
                return;
            }

            configBuffer = Marshal.AllocHGlobal(SfuStructSizes.Config);
            stepInBuffer = Marshal.AllocHGlobal(SfuStructSizes.StepIn);
            stepOutBuffer = Marshal.AllocHGlobal(SfuStructSizes.StepOut);
        }

        /// <summary>Give them back. / 返す。</summary>
        private void FreeBuffers()
        {
            bool nothingTaken = configBuffer == IntPtr.Zero;
            if (nothingTaken)
            {
                return;
            }

            Marshal.FreeHGlobal(configBuffer);
            Marshal.FreeHGlobal(stepInBuffer);
            Marshal.FreeHGlobal(stepOutBuffer);
            configBuffer = IntPtr.Zero;
            stepInBuffer = IntPtr.Zero;
            stepOutBuffer = IntPtr.Zero;
        }
#endif
    }
}
