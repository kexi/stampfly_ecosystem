/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the editor firmware).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using System.IO;
using System.Runtime.InteropServices;
using StampFly.Core;

namespace StampFly.Native
{
    /// <summary>
    /// The firmware as a macOS dylib, for the editor's play button. It exists so
    /// a change can be tried without waiting out a WebGL build, and so Play Mode
    /// tests can fly.
    ///
    /// エディタの再生ボタンのための、macOS の dylib としてのファーム。WebGL の
    /// ビルドを待たずに試せるようにし、Play Mode 試験が飛べるようにするためにある。
    ///
    /// ## Why the library is copied before it is opened / 開く前に複写する理由
    ///
    /// The editor never unloads a native library it has loaded with
    /// <c>[DllImport]</c>, so on a second play session the same firmware would
    /// still be booted and <c>sfu_boot</c> would return "already booted" — one
    /// module is one power-on, by design. The way out is not to let the editor
    /// own the handle: this class copies the dylib to a uniquely named file and
    /// opens THAT with <c>dlopen</c>, resolving each entry point with
    /// <c>dlsym</c>. A new play session copies to a new name and gets a new
    /// image with its statics at their initial values.
    ///
    /// エディタは <c>[DllImport]</c> で読み込んだネイティブライブラリを解放しない。
    /// よって 2 回目の再生では同じファームが起動済みのままで、<c>sfu_boot</c> は
    /// 「起動済み」を返す ― 1 モジュール＝1 回の電源投入という設計どおりである。
    /// 逃げ道は、ハンドルをエディタに持たせないことである。この実装は dylib を一意な
    /// 名前へ複写し、**その複写**を <c>dlopen</c> で開き、入口ごとに <c>dlsym</c> で
    /// 引く。新しい再生は新しい名前へ複写するので、静的変数が初期値の新しい像が得ら
    /// れる。
    ///
    /// <c>dlopen</c>, <c>dlsym</c> and <c>dlclose</c> themselves come from libSystem,
    /// which the editor already holds; loading them again is harmless.
    /// <c>dlopen</c>・<c>dlsym</c>・<c>dlclose</c> 自体は libSystem のもので、
    /// エディタは既にこれを抱えている。改めて読み込んでも害は無い。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（エディタでの開発）
    /// </summary>
    public sealed class EditorFirmware : IFirmware
    {
        /// <summary>
        /// Where <c>just unity-native-build</c> puts the development dylib,
        /// relative to the repository root.
        /// <c>just unity-native-build</c> が開発用の dylib を置く場所。リポジトリ
        /// 直下からの相対。
        /// </summary>
        public const string DylibRepositoryPath =
            "simulator/unity/native/build-native/libsfu_firmware.dylib";

        // dlopen's flags, from <dlfcn.h>. RRTLD_NOW resolves every symbol at
        // open time, so a missing entry point is reported here rather than on
        // the first tick. RTLD_LOCAL keeps the symbols out of the global
        // namespace, so two open copies cannot shadow one another.
        // <dlfcn.h> の dlopen のフラグ。RTLD_NOW は開いた時点で全ての記号を解決
        // するので、足りない入口は最初の刻みではなくここで分かる。RTLD_LOCAL は
        // 記号を大域の名前空間に出さないので、2 つ開いた複写が互いを覆わない。
        private const int RtldNow = 0x2;
        private const int RtldLocal = 0x4;

        private IntPtr libraryHandle = IntPtr.Zero;

        // The ABI structs are written into unmanaged memory rather than into a
        // pinned managed struct, for the reason spelled out in
        // `WebGlFirmware`: a boxed value's pinned address is not where its
        // fields begin. Mono happens to be forgiving about it and IL2CPP is
        // not, so both sides do the same unambiguous thing.
        // ABI の構造体は、固定した管理下の構造体ではなく非管理の記憶域へ書く。
        // 理由は `WebGlFirmware` に書いたとおりで、箱に入れた値を固定した番地は
        // その欄の始まりではない。Mono はたまたま大目に見るが IL2CPP は見ないので、
        // 両側とも同じ、曖昧さの無いやり方にしてある。
        private IntPtr configBuffer = IntPtr.Zero;
        private IntPtr stepInBuffer = IntPtr.Zero;
        private IntPtr stepOutBuffer = IntPtr.Zero;

        private string copyPath;
        private string failureReason = string.Empty;
        private FirmwareStatus status = FirmwareStatus.Idle;

        private readonly string sourcePath;

        // The entry points, resolved once when the library opens.
        // 入口の一式。ライブラリを開いたときに 1 回だけ引く。
        private AbiVersionDelegate abiVersion;
        private StructSizeDelegate structSize;
        private BootDelegate boot;
        private StepDelegate step;
        private ShutdownDelegate shutdown;
        private ParamCountDelegate paramCount;
        private ParamInfoDelegate paramInfo;
        private ParamSetDelegate paramSet;
        private ParamGetDelegate paramGet;
        private SetWindDelegate setWind;
        private SetMotorHealthDelegate setMotorHealth;
        private SetImuBiasDelegate setImuBias;
        private LogReadRecordDelegate logReadRecord;
        private LogDroppedDelegate logDropped;
        private SetLogLevelDelegate setLogLevel;

        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int AbiVersionDelegate();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int StructSizeDelegate(int which);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int BootDelegate(IntPtr config);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int StepDelegate(IntPtr input, IntPtr output);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int ShutdownDelegate();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int ParamCountDelegate();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int ParamInfoDelegate(int index, IntPtr output);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int ParamSetDelegate(IntPtr name, double value);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int ParamGetDelegate(IntPtr name, IntPtr value);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SetWindDelegate(float worldX, float worldY, float worldZ);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SetMotorHealthDelegate(int motor, float gain);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SetImuBiasDelegate(float accelX, float accelY, float accelZ,
                                                float gyroX, float gyroY, float gyroZ);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int LogReadRecordDelegate(IntPtr output);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int LogDroppedDelegate();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int SetLogLevelDelegate(int level);

        [DllImport("libSystem.dylib")] private static extern IntPtr dlopen(string path, int mode);
        [DllImport("libSystem.dylib")] private static extern IntPtr dlsym(IntPtr handle, string symbol);
        [DllImport("libSystem.dylib")] private static extern int dlclose(IntPtr handle);
        [DllImport("libSystem.dylib")] private static extern IntPtr dlerror();

        /// <summary>
        /// Builds a firmware that opens <paramref name="dylibPath"/>, or the
        /// development dylib under the repository when that is null or empty.
        /// <paramref name="dylibPath"/> を開くファームを作る。null か空なら、
        /// リポジトリ下の開発用 dylib を開く。
        /// </summary>
        public EditorFirmware(string dylibPath = null)
        {
            bool hasPath = !string.IsNullOrEmpty(dylibPath);
            sourcePath = hasPath ? dylibPath : DefaultDylibPath();
        }

        /// <summary>
        /// The development dylib's absolute path. The Unity project sits at
        /// <c>simulator/unity</c>, so the repository root is two levels above
        /// <c>Application.dataPath</c>'s parent.
        /// 開発用 dylib の絶対パス。Unity プロジェクトは <c>simulator/unity</c> に
        /// あるので、リポジトリ直下は <c>Application.dataPath</c> の親のさらに 2 つ上。
        /// </summary>
        public static string DefaultDylibPath()
        {
            string projectRoot = Directory.GetParent(UnityEngine.Application.dataPath).FullName;
            string repositoryRoot = Directory.GetParent(
                Directory.GetParent(projectRoot).FullName).FullName;
            return Path.Combine(repositoryRoot, DylibRepositoryPath);
        }

        /// <inheritdoc/>
        public FirmwareStatus Status => status;

        /// <inheritdoc/>
        public string LastError => failureReason;

        /// <summary>The unique copy this instance opened. / この実体が開いた一意な複写。</summary>
        public string OpenedPath => copyPath;

        /// <inheritdoc/>
        public void BeginLoad()
        {
            bool alreadyStarted = status != FirmwareStatus.Idle;
            if (alreadyStarted)
            {
                return;
            }

            status = FirmwareStatus.Loading;

            bool sourceIsMissing = !File.Exists(sourcePath);
            if (sourceIsMissing)
            {
                Fail($"the firmware dylib is not at {sourcePath}. " +
                     "Run `nix develop -c just unity-native-build` first.");
                return;
            }

            if (!OpenUniqueCopy())
            {
                return;
            }

            if (!ResolveEntryPoints())
            {
                return;
            }

            configBuffer = Marshal.AllocHGlobal(SfuStructSizes.Config);
            stepInBuffer = Marshal.AllocHGlobal(SfuStructSizes.StepIn);
            stepOutBuffer = Marshal.AllocHGlobal(SfuStructSizes.StepOut);
            status = FirmwareStatus.Loaded;
        }

        /// <summary>
        /// Copies the dylib to a name nothing has opened before and opens that.
        /// The unique name is what lets a second play session get a fresh image.
        /// dylib を、まだ誰も開いたことのない名前へ複写し、その複写を開く。2 回目の
        /// 再生が新しい像を得られるのは、この一意な名前のおかげである。
        /// </summary>
        private bool OpenUniqueCopy()
        {
            try
            {
                string directory = Path.Combine(Path.GetTempPath(), "stampfly-firmware");
                Directory.CreateDirectory(directory);
                copyPath = Path.Combine(
                    directory, $"libsfu_firmware-{Guid.NewGuid():N}.dylib");
                File.Copy(sourcePath, copyPath, overwrite: true);
            }
            catch (IOException error)
            {
                Fail($"could not copy the firmware dylib: {error.Message}");
                return false;
            }

            libraryHandle = dlopen(copyPath, RtldNow | RtldLocal);
            bool openFailed = libraryHandle == IntPtr.Zero;
            if (openFailed)
            {
                Fail($"dlopen failed for {copyPath}: {ReadDlError()}");
                return false;
            }

            return true;
        }

        /// <summary>
        /// Resolves every entry point the loop uses. Resolving all of them up
        /// front means a missing one is reported by name, here, rather than as
        /// a crash on the tick that first needed it.
        /// ループが使う入口を全て引く。先にまとめて引けば、足りないものは、最初に
        /// 要った刻みでの異常終了ではなく、名前つきでここで分かる。
        /// </summary>
        private bool ResolveEntryPoints()
        {
            abiVersion = Resolve<AbiVersionDelegate>("sfu_abi_version");
            structSize = Resolve<StructSizeDelegate>("sfu_struct_size");
            boot = Resolve<BootDelegate>("sfu_boot");
            step = Resolve<StepDelegate>("sfu_step");

            // Resolved so a dylib missing it is rejected here, but never called
            // from the editor — see the note on Dispose.
            // 引いておくのは、これを持たない dylib をここで拒むためである。ただし
            // エディタから呼ぶことはない。Dispose の注記を参照。
            shutdown = Resolve<ShutdownDelegate>("sfu_shutdown");
            paramCount = Resolve<ParamCountDelegate>("sfu_param_count");
            paramInfo = Resolve<ParamInfoDelegate>("sfu_param_info");
            paramSet = Resolve<ParamSetDelegate>("sfu_param_set");
            paramGet = Resolve<ParamGetDelegate>("sfu_param_get");
            setWind = Resolve<SetWindDelegate>("sfu_set_wind");
            setMotorHealth = Resolve<SetMotorHealthDelegate>("sfu_set_motor_health");
            setImuBias = Resolve<SetImuBiasDelegate>("sfu_set_imu_bias");
            logReadRecord = Resolve<LogReadRecordDelegate>("sfu_log_read_record");
            logDropped = Resolve<LogDroppedDelegate>("sfu_log_dropped");
            setLogLevel = Resolve<SetLogLevelDelegate>("sfu_set_log_level");

            return status != FirmwareStatus.Failed;
        }

        /// <summary>One entry point, or a recorded failure. / 入口 1 つ。引けなければ失敗を記録する。</summary>
        private TDelegate Resolve<TDelegate>(string symbol) where TDelegate : Delegate
        {
            IntPtr address = dlsym(libraryHandle, symbol);
            bool isMissing = address == IntPtr.Zero;
            if (isMissing)
            {
                Fail($"the firmware dylib has no symbol {symbol}");
                return null;
            }

            return Marshal.GetDelegateForFunctionPointer<TDelegate>(address);
        }

        /// <summary>The message dlopen or dlsym left behind. / dlopen・dlsym が残した文。</summary>
        private static string ReadDlError()
        {
            IntPtr text = dlerror();
            bool hasText = text != IntPtr.Zero;
            return hasText ? (Marshal.PtrToStringUTF8(text) ?? "unknown") : "unknown";
        }

        /// <summary>Records why this firmware cannot be used. / このファームを使えない理由を記録する。</summary>
        private void Fail(string reason)
        {
            failureReason = reason;
            status = FirmwareStatus.Failed;
        }

        /// <inheritdoc/>
        public int Boot(in SfuConfig config)
        {
            bool notLoaded = status != FirmwareStatus.Loaded;
            if (notLoaded)
            {
                return SfuAbi.ErrorNotBooted;
            }

            string mismatch = SfuStructSizes.CheckAgainstModule(which => structSize(which));
            if (mismatch != null)
            {
                Fail(mismatch);
                return SfuAbi.ErrorStructSize;
            }

            int libraryAbi = abiVersion();
            bool abiDisagrees = libraryAbi != SfuAbi.Version;
            if (abiDisagrees)
            {
                Fail($"ABI mismatch: the dylib follows revision {libraryAbi}, " +
                     $"this build follows {SfuAbi.Version}");
                return SfuAbi.ErrorStructSize;
            }

            SfuConfig local = config;
            local.StructSize = (uint)SfuStructSizes.Config;
            Marshal.StructureToPtr(local, configBuffer, false);

            int result = boot(configBuffer);
            if (result == SfuAbi.Ok)
            {
                status = FirmwareStatus.Running;
            }
            else
            {
                Fail($"sfu_boot returned {SfuAbi.Describe(result)}");
            }
            return result;
        }

        /// <inheritdoc/>
        public int Step(in SfuStepIn input, ref SfuStepOut result)
        {
            bool notRunning = status != FirmwareStatus.Running;
            if (notRunning)
            {
                result.Status = SfuAbi.ErrorNotBooted;
                return SfuAbi.ErrorNotBooted;
            }

            SfuStepIn local = input;
            local.StructSize = (uint)SfuStructSizes.StepIn;
            result.StructSize = (uint)SfuStructSizes.StepOut;

            // Zeroed first for the same reason the JavaScript side does it:
            // so "the bridge wrote it" and "nothing happened" stay apart.
            // 先に 0 を入れるのは JavaScript 側と同じ理由である。「橋渡しが書いた」と
            // 「何も起きなかった」を分けておくためである。
            result.Status = 1;

            Marshal.StructureToPtr(local, stepInBuffer, false);
            Marshal.StructureToPtr(result, stepOutBuffer, false);

            step(stepInBuffer, stepOutBuffer);

            result = Marshal.PtrToStructure<SfuStepOut>(stepOutBuffer);
            return result.Status;
        }

        /// <inheritdoc/>
        public int ParameterCount => status == FirmwareStatus.Running ? paramCount() : 0;

        /// <inheritdoc/>
        public int GetParameterInfo(int index, out SfuParamInfo info)
        {
            info = default;
            bool notRunning = status != FirmwareStatus.Running;
            if (notRunning)
            {
                return SfuAbi.ErrorNotBooted;
            }

            IntPtr buffer = Marshal.AllocHGlobal(SfuStructSizes.ParamInfo);
            try
            {
                Marshal.WriteInt32(buffer, SfuStructSizes.ParamInfo);
                int result = paramInfo(index, buffer);
                if (result == SfuAbi.Ok)
                {
                    info = Marshal.PtrToStructure<SfuParamInfo>(buffer);
                }
                return result;
            }
            finally
            {
                Marshal.FreeHGlobal(buffer);
            }
        }

        /// <inheritdoc/>
        public int SetParameter(string name, double value)
        {
            bool notRunning = status != FirmwareStatus.Running;
            if (notRunning)
            {
                return SfuAbi.ErrorNotBooted;
            }

            IntPtr text = Marshal.StringToHGlobalAnsi(name);
            try
            {
                return paramSet(text, value);
            }
            finally
            {
                Marshal.FreeHGlobal(text);
            }
        }

        /// <inheritdoc/>
        public int GetParameter(string name, out double value)
        {
            value = 0.0;
            bool notRunning = status != FirmwareStatus.Running;
            if (notRunning)
            {
                return SfuAbi.ErrorNotBooted;
            }

            IntPtr text = Marshal.StringToHGlobalAnsi(name);
            IntPtr buffer = Marshal.AllocHGlobal(sizeof(double));
            try
            {
                int result = paramGet(text, buffer);
                if (result == SfuAbi.Ok)
                {
                    value = Marshal.PtrToStructure<double>(buffer);
                }
                return result;
            }
            finally
            {
                Marshal.FreeHGlobal(text);
                Marshal.FreeHGlobal(buffer);
            }
        }

        /// <inheritdoc/>
        public int SetWind(float worldX, float worldY, float worldZ)
        {
            bool notRunning = status != FirmwareStatus.Running;
            return notRunning ? SfuAbi.ErrorNotBooted : setWind(worldX, worldY, worldZ);
        }

        /// <inheritdoc/>
        public int SetMotorHealth(int motor, float gain)
        {
            bool notRunning = status != FirmwareStatus.Running;
            return notRunning ? SfuAbi.ErrorNotBooted : setMotorHealth(motor, gain);
        }

        /// <inheritdoc/>
        public int SetImuBias(float accelX, float accelY, float accelZ,
                              float gyroX, float gyroY, float gyroZ)
        {
            bool notRunning = status != FirmwareStatus.Running;
            return notRunning
                ? SfuAbi.ErrorNotBooted
                : setImuBias(accelX, accelY, accelZ, gyroX, gyroY, gyroZ);
        }

        /// <inheritdoc/>
        public bool TryReadLogRecord(out FirmwareLogRecord record)
        {
            record = default;
            bool notRunning = status != FirmwareStatus.Running;
            if (notRunning)
            {
                return false;
            }

            IntPtr buffer = Marshal.AllocHGlobal(SfuStructSizes.LogRecord);
            try
            {
                Marshal.WriteInt32(buffer, SfuStructSizes.LogRecord);
                bool wroteOne = logReadRecord(buffer) == 1;
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
        }

        /// <inheritdoc/>
        public int DroppedLogRecords()
        {
            bool notRunning = status != FirmwareStatus.Running;
            return notRunning ? 0 : logDropped();
        }

        /// <inheritdoc/>
        public int SetLogLevel(int level)
        {
            bool notRunning = status != FirmwareStatus.Running;
            return notRunning ? SfuAbi.ErrorNotBooted : setLogLevel(level);
        }

        /// <summary>
        /// Let the module go: close the image and delete the copy. Closing is
        /// what makes the next power-on a genuine one, because it opens a
        /// different file and gets fresh statics.
        /// モジュールを手放す。像を閉じ、複写を消す。次の電源投入が本物になるのは
        /// 閉じるからである。次は別のファイルを開き、静的変数は初期値になる。
        ///
        /// ## Why sfu_shutdown is NOT called here / ここで sfu_shutdown を呼ばない理由
        ///
        /// The ABI makes it optional — "discarding the module is enough" — and
        /// inside the editor it DEADLOCKS. The development dylib carries the
        /// thread-based scheduler, whose `Scheduler::stop_all` waits for all
        /// fourteen task threads to join while they sit blocked on a condition
        /// variable in `ulTaskNotifyTake`; the same call returns in a second in
        /// a plain process, so what the editor adds is what hangs it. Measured
        /// 2026-09-20 with `sample`: every thread parked in `_pthread_cond_wait`
        /// under `sfu_shutdown -> Scheduler::stop_all`.
        ///
        /// ABI はこれを省略可としており（「モジュールを捨てるだけでも足りる」）、
        /// エディタの中では**デッドロックする**。開発用 dylib はスレッド版の
        /// スケジューラを持ち、その `Scheduler::stop_all` は 14 本のタスクの
        /// スレッドの合流を待つが、それらは `ulTaskNotifyTake` の中で条件変数に
        /// 掛かったままである。素のプロセスでは同じ呼び出しが 1 秒で返るので、
        /// 止めているのはエディタが加えるものである。2026-09-20 に `sample` で
        /// 実測した。全スレッドが `sfu_shutdown -> Scheduler::stop_all` の下の
        /// `_pthread_cond_wait` に停まっていた。
        ///
        /// Not calling it leaks the task stacks (14 MiB) and leaves the threads
        /// parked until the editor's process ends. That is the price of a play
        /// session that finishes; the WebGL side has neither problem, because a
        /// discarded wasm module takes its whole memory with it.
        ///
        /// 呼ばないと、タスクのスタック（14 MiB）が残り、スレッドはエディタの
        /// プロセスが終わるまで停まったままになる。再生が終われることの代償で
        /// ある。WebGL 側にはどちらの問題も無い。捨てた wasm モジュールは自分の
        /// メモリごと消えるからである。
        /// </summary>
        public void Dispose()
        {
            status = FirmwareStatus.ShutDown;

            bool isOpen = libraryHandle != IntPtr.Zero;
            if (isOpen)
            {
                dlclose(libraryHandle);
                libraryHandle = IntPtr.Zero;
            }

            FreeBuffers();
            DeleteCopy();
        }

        /// <summary>Give the three buffers back. / 3 つの領域を返す。</summary>
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

        /// <summary>
        /// Remove the temporary copy. A failure here is ignored on purpose: the
        /// file is in the system's temporary directory and losing one is not a
        /// reason to fail a play session.
        /// 一時の複写を消す。ここでの失敗は意図して無視する。ファイルは系の一時
        /// ディレクトリに在り、1 つ残っても再生を失敗させる理由にはならない。
        /// </summary>
        private void DeleteCopy()
        {
            bool hasCopy = !string.IsNullOrEmpty(copyPath);
            if (!hasCopy)
            {
                return;
            }

            try
            {
                File.Delete(copyPath);
            }
            catch (IOException)
            {
                // Left behind in the temporary directory; harmless.
                // 一時ディレクトリに残るだけで害は無い。
            }

            copyPath = null;
        }
    }
}
