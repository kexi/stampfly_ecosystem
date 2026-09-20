/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — stage 1(b)(d) spike).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Runtime.InteropServices;
using UnityEngine;

namespace StampFly.Sim
{
    /// <summary>
    /// How far the firmware module has got. Mirrors the numbers in
    /// <c>SfuSpike.jslib</c>; the two must stay in step.
    /// ファームのモジュールの進み具合。<c>SfuSpike.jslib</c> の数値と対応する。
    /// 両者は一致させておく。
    /// </summary>
    public enum SfuSpikeStatus
    {
        Idle = 0,
        Loading = 1,
        Ready = 2,
        Failed = 3,
    }

    /// <summary>
    /// The C# side of the stage 1(b) check: the firmware lives in a separate
    /// wasm module, and this calls into it through <c>SfuSpike.jslib</c>.
    /// Loading is asynchronous, so <see cref="Status"/> is polled; everything
    /// after that is a plain synchronous call.
    ///
    /// 段階 1(b) の検証の C# 側: ファームは別の wasm モジュールにあり、ここから
    /// <c>SfuSpike.jslib</c> 経由で呼ぶ。読み込みは非同期なので
    /// <see cref="Status"/> をポーリングし、それ以降はただの同期呼び出しになる。
    ///
    /// Outside WebGL every call is a stub that reports Idle, so a scene holding
    /// this component still opens in the editor. The editor's own native plugin
    /// is stage 3's work.
    /// WebGL 以外では全ての呼び出しが Idle を返す張りぼてになり、この部品を持つ
    /// 場面もエディタで開ける。エディタ用のネイティブプラグインは段階 3 の作業。
    ///
    /// @design docs/plans/unity-simulator.md — 段階 1(b)(d) 技術検証
    /// </summary>
    public static class SfuSpikeFirmware
    {
        // How many pose numbers sfu_spike_pose writes: east, north, up,
        // roll, pitch, yaw.
        // sfu_spike_pose が書き込む数の個数: 東・北・上・ロール・ピッチ・ヨー。
        public const int PoseValueCount = 6;

#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")] private static extern int SfuSpikeLoad(string url);
        // The jslib function is SfuSpikeStatus; the C# name differs because the
        // type SfuSpikeStatus already owns that identifier here.
        // jslib 側の関数名は SfuSpikeStatus。C# 側で名前を変えているのは、
        // ここではその識別子を型 SfuSpikeStatus が既に使っているため。
        [DllImport("__Internal", EntryPoint = "SfuSpikeStatus")]
        private static extern int SfuSpikeStatusCode();
        [DllImport("__Internal")] private static extern System.IntPtr SfuSpikeError();
        [DllImport("__Internal")] private static extern int SfuSpikeBoot();
        [DllImport("__Internal")] private static extern double SfuSpikeStep();
        [DllImport("__Internal")] private static extern double SfuSpikeStepUntil(double targetMicroseconds);
        [DllImport("__Internal")] private static extern double SfuSpikeAltitude();
        [DllImport("__Internal")] private static extern int SfuSpikeState();
        [DllImport("__Internal")] private static extern double SfuSpikeBattery();
        [DllImport("__Internal")] private static extern int SfuSpikePose(double[] destination);
        [DllImport("__Internal")] private static extern double SfuSpikeHeapBytes();
        [DllImport("__Internal")] private static extern int SfuSpikeGeneration();
        [DllImport("__Internal")] private static extern void SfuSpikeRelease();
        [DllImport("__Internal")] private static extern double SfuSpikeJsHeapBytes();
#endif

        /// <summary>
        /// Start creating a firmware module from <paramref name="url"/>.
        /// Returns the generation number this load carries, so one power cycle
        /// can be told from the next.
        /// <paramref name="url"/> からファームのモジュールの生成を開始する。
        /// 戻り値はこの読み込みの世代番号で、電源の入れ直しを区別できる。
        /// </summary>
        public static int Load(string url)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuSpikeLoad(url);
#else
            Debug.Log($"[SfuSpikeFirmware] Load ignored outside WebGL: {url}");
            return 0;
#endif
        }

        /// <summary>Current load status. / いまの読み込み状態。</summary>
        public static SfuSpikeStatus Status
        {
            get
            {
#if UNITY_WEBGL && !UNITY_EDITOR
                return (SfuSpikeStatus)SfuSpikeStatusCode();
#else
                return SfuSpikeStatus.Idle;
#endif
            }
        }

        /// <summary>The last error text, empty when there is none. / 直近の誤りの文。無ければ空。</summary>
        public static string LastError
        {
            get
            {
#if UNITY_WEBGL && !UNITY_EDITOR
                System.IntPtr pointer = SfuSpikeError();
                string text = Marshal.PtrToStringUTF8(pointer);
                Marshal.FreeHGlobal(pointer);
                return text ?? string.Empty;
#else
                return string.Empty;
#endif
            }
        }

        /// <summary>Power on the loaded firmware. / 読み込んだファームを起動する。</summary>
        public static bool Boot()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuSpikeBoot() != 0;
#else
            return false;
#endif
        }

        /// <summary>
        /// Advance one 2.5 ms tick and return the new virtual time [µs].
        /// This is the synchronous call stage 1(b) sets out to prove.
        /// 2.5ms を 1 刻み進め、新しい仮想時刻 [マイクロ秒] を返す。
        /// 段階 1(b) が成り立つことを示したいのはこの同期呼び出し。
        /// </summary>
        public static double Step()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuSpikeStep();
#else
            return 0.0;
#endif
        }

        /// <summary>Advance to a virtual time [µs]. / 仮想時刻 [マイクロ秒] まで進める。</summary>
        public static double StepUntil(double targetMicroseconds)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuSpikeStepUntil(targetMicroseconds);
#else
            return 0.0;
#endif
        }

        /// <summary>Altitude above the floor [m]. / 床からの高度 [m]。</summary>
        public static double Altitude()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuSpikeAltitude();
#else
            return 0.0;
#endif
        }

        /// <summary>Flight state as a number. / 飛行状態の番号。</summary>
        public static int State()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuSpikeState();
#else
            return 0;
#endif
        }

        /// <summary>Battery voltage [V]. / 電池電圧 [V]。</summary>
        public static double BatteryVolts()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuSpikeBattery();
#else
            return 0.0;
#endif
        }

        /// <summary>
        /// Fill <paramref name="destination"/> (length <see cref="PoseValueCount"/>)
        /// with east, north, up [m] and roll, pitch, yaw [degrees].
        /// <paramref name="destination"/>（長さ <see cref="PoseValueCount"/>）に
        /// 東・北・上 [m] とロール・ピッチ・ヨー [度] を書き込む。
        /// </summary>
        public static bool Pose(double[] destination)
        {
            bool isWrongLength = destination == null || destination.Length < PoseValueCount;
            if (isWrongLength)
            {
                return false;
            }

#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuSpikePose(destination) != 0;
#else
            return false;
#endif
        }

        /// <summary>
        /// WebAssembly memory the firmware module holds [bytes], for watching
        /// repeated power cycles.
        /// ファームのモジュールが持つ WebAssembly メモリ [バイト]。電源の入れ直しを
        /// 繰り返したときの推移を見るため。
        /// </summary>
        public static double HeapBytes()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuSpikeHeapBytes();
#else
            return 0.0;
#endif
        }

        /// <summary>JavaScript heap in use [bytes], 0 when unavailable. / 使用中の JavaScript ヒープ [バイト]。取れなければ 0。</summary>
        public static double JsHeapBytes()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuSpikeJsHeapBytes();
#else
            return 0.0;
#endif
        }

        /// <summary>How many modules have been created. / これまでに作ったモジュールの数。</summary>
        public static int Generation()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            return SfuSpikeGeneration();
#else
            return 0;
#endif
        }

        /// <summary>Throw the current module away (a power cycle). / 現在のモジュールを捨てる（電源の入れ直し）。</summary>
        public static void Release()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            SfuSpikeRelease();
#endif
        }
    }
}
