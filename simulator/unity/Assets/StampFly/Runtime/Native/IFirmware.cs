/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the firmware seam).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System;
using StampFly.Core;

namespace StampFly.Native
{
    /// <summary>
    /// How far a firmware implementation has got. Loading is asynchronous under
    /// WebGL (a wasm module arrives over the network), so the loop polls this
    /// rather than assuming the firmware is there on the first frame.
    /// ファームの実装の進み具合。WebGL では読み込みが非同期（wasm モジュールが
    /// 網越しに届く）なので、最初のフレームから在るものとせず、ループはこれを
    /// ポーリングする。
    /// </summary>
    public enum FirmwareStatus
    {
        /// <summary>Nothing has been asked of it yet. / まだ何も頼まれていない。</summary>
        Idle,

        /// <summary>The module is being fetched or opened. / モジュールを取得・展開している。</summary>
        Loading,

        /// <summary>Loaded, but <see cref="IFirmware.Boot"/> has not run. / 読み込み済み。<see cref="IFirmware.Boot"/> は未実行。</summary>
        Loaded,

        /// <summary>Booted and stepping. / 起動済みで刻める。</summary>
        Running,

        /// <summary>Finished; every entry point refuses. / 終了済み。どの入口も拒む。</summary>
        ShutDown,

        /// <summary>Something failed; <see cref="IFirmware.LastError"/> says what. / 失敗した。理由は <see cref="IFirmware.LastError"/>。</summary>
        Failed,
    }

    /// <summary>
    /// The seam between the simulation loop and the unmodified C++ firmware.
    /// One instance is ONE POWER-ON of one vehicle: the firmware's tasks hold
    /// static state that cannot be returned to its initial value without editing
    /// the firmware, so a power cycle means disposing this and making another.
    ///
    /// シミュレーションのループと無改変の C++ ファームウェアの継ぎ目。1 つの実体は
    /// 1 機体の**1 回の電源投入**にあたる。ファームのタスクは、ファームを編集せずに
    /// 初期値へ戻せない静的な状態を持つ。よって電源の入れ直しは、これを破棄して
    /// 別のものを作ることを意味する。
    ///
    /// Two implementations exist: <see cref="WebGlFirmware"/> calls a separate
    /// wasm module through a .jslib, and <see cref="EditorFirmware"/> calls a
    /// macOS dylib through P/Invoke for the editor's play button.
    /// 実装は 2 つある。<see cref="WebGlFirmware"/> は .jslib 経由で別の wasm
    /// モジュールを呼び、<see cref="EditorFirmware"/> はエディタの再生ボタンの
    /// ために P/Invoke で macOS の dylib を呼ぶ。
    ///
    /// @design docs/plans/unity-simulator.md §3 1 刻みの処理, §4 設計上の決定
    /// @design simulator/unity/native/bridge/sfu_api.h
    /// </summary>
    public interface IFirmware : IDisposable
    {
        /// <summary>How far this implementation has got. / この実装の進み具合。</summary>
        FirmwareStatus Status { get; }

        /// <summary>Why it failed, or empty. / 失敗の理由。無ければ空。</summary>
        string LastError { get; }

        /// <summary>
        /// Start loading, if loading is needed. Returns at once; the caller
        /// polls <see cref="Status"/> until it leaves <see cref="FirmwareStatus.Loading"/>.
        /// 要るなら読み込みを開始する。すぐ返るので、呼び出し側は
        /// <see cref="FirmwareStatus.Loading"/> を抜けるまで <see cref="Status"/> を見る。
        /// </summary>
        void BeginLoad();

        /// <summary>
        /// Power on: bring up the plant and run the firmware's own
        /// <c>app_main</c>. Legal once, when <see cref="Status"/> is
        /// <see cref="FirmwareStatus.Loaded"/>. Returns an <see cref="SfuAbi"/> code.
        /// 電源投入。プラントを起こし、ファーム自身の <c>app_main</c> を走らせる。
        /// <see cref="Status"/> が <see cref="FirmwareStatus.Loaded"/> のときに
        /// 1 回だけ呼べる。<see cref="SfuAbi"/> の値を返す。
        /// </summary>
        int Boot(in SfuConfig config);

        /// <summary>
        /// One tick. Reads <c>status</c> out of <paramref name="result"/> rather
        /// than a return value, because under wasm that is the only place the
        /// result survives. Returns that status.
        /// 1 刻み。結果は戻り値ではなく <paramref name="result"/> の
        /// <c>status</c> から読む。wasm では結果が残るのがそこだけだからである。
        /// その値を返す。
        /// </summary>
        int Step(in SfuStepIn input, ref SfuStepOut result);

        /// <summary>How many tuning parameters the firmware exposes. / ファームが公開する調整値の数。</summary>
        int ParameterCount { get; }

        /// <summary>One parameter's identity. / パラメータ 1 個の素性。</summary>
        int GetParameterInfo(int index, out SfuParamInfo info);

        /// <summary>Set a parameter by name. / 名前でパラメータを設定する。</summary>
        int SetParameter(string name, double value);

        /// <summary>Read a parameter by name. / 名前でパラメータを読む。</summary>
        int GetParameter(string name, out double value);

        /// <summary>Constant wind force [N] in the Unity world frame. / Unity の世界系での一定の風力 [N]。</summary>
        int SetWind(float worldX, float worldY, float worldZ);

        /// <summary>Per-motor thrust health: 1 healthy, 0 dead. / モータごとの推力の健全度。1 = 正常、0 = 停止。</summary>
        int SetMotorHealth(int motor, float gain);

        /// <summary>Constant raw IMU bias, body-local Unity frame. / 一定の生 IMU バイアス。Unity の機体系。</summary>
        int SetImuBias(float accelX, float accelY, float accelZ,
                       float gyroX, float gyroY, float gyroZ);

        /// <summary>
        /// Take the oldest log record out of the ring. True when one was
        /// written, false when the ring is empty. Call it in a loop until it is
        /// false. Draining NEVER affects the simulation.
        /// リングから最も古い記録を 1 つ取り出す。書けたら true、空なら false。
        /// false になるまで繰り返し呼ぶ。取り出しがシミュレーションに影響する
        /// ことはない。
        /// </summary>
        bool TryReadLogRecord(out FirmwareLogRecord record);

        /// <summary>
        /// Records dropped because the ring was full, counted since boot.
        /// <c>FirmwareLogPump.NoteDropped</c> takes this total and reports only
        /// what is new.
        /// リングが満杯で捨てられた記録の数（起動からの累計）。
        /// <c>FirmwareLogPump.NoteDropped</c> がこの累計を受け取り、増えたぶんだけを
        /// 報告する。
        /// </summary>
        int DroppedLogRecords();

        /// <summary>The lowest level kept from here on; returns the previous. / これ以降に残す最も低い段。直前の段を返す。</summary>
        int SetLogLevel(int level);
    }
}
