/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the stick-input seam).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

namespace StampFly.Input
{
    /// <summary>
    /// One transmitter frame: four sticks as raw 12-bit ADC counts and the flag
    /// byte, exactly as the ControlPacket carries them. Nothing here is scaled
    /// or normalised, so a value means the same stick deflection a scenario file
    /// would encode.
    ///
    /// 送信機の 1 フレーム。スティック 4 本を 12 bit の生 ADC の値で、フラグの
    /// バイトとともに持つ。ControlPacket が運ぶのと同じ形である。ここで正規化も
    /// 尺度変換もしないので、値の意味は台本ファイルが書く振れ幅と同じになる。
    /// </summary>
    public readonly struct RcFrame
    {
        public RcFrame(ushort throttle, ushort roll, ushort pitch, ushort yaw, byte flags)
        {
            Throttle = throttle;
            Roll = roll;
            Pitch = pitch;
            Yaw = yaw;
            Flags = flags;
        }

        /// <summary>Raw 12-bit ADC, centre 2048. / 12 bit の生 ADC。中央 2048。</summary>
        public ushort Throttle { get; }
        public ushort Roll { get; }
        public ushort Pitch { get; }
        public ushort Yaw { get; }

        /// <summary>ARM and mode bits. / ARM とモードのビット。</summary>
        public byte Flags { get; }

        /// <summary>
        /// Every stick centred and no flag set — what a transmitter sends with
        /// nothing touched. Also what the loop uses before an input device
        /// appears, so a missing keyboard never arms anything.
        /// スティックが全て中央でフラグも無い状態 ― 送信機が、何にも触れずに送る
        /// ものである。入力装置が現れる前のループもこれを使うので、キーボードが
        /// 無いときに何かが ARM されることはない。
        /// </summary>
        public static RcFrame Centred => new RcFrame(
            RcScale.Centre, RcScale.Centre, RcScale.Centre, RcScale.Centre, 0);
    }

    /// <summary>
    /// Where the loop gets its stick values. Keyboard today; a gamepad and a
    /// scripted source come later, and each is one implementation of this.
    /// ループがスティックの値を得る先。いまはキーボードで、ゲームパッドと台本の
    /// 入力は後で足す。どれもこれの実装 1 つになる。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（操縦入力）
    /// </summary>
    public interface IRcSource
    {
        /// <summary>
        /// The latest frame. Called once per rendered frame, not once per
        /// firmware tick: the bridge injects into ESP-NOW on the firmware's own
        /// 50 Hz virtual-time cadence, so it only needs the newest value.
        /// 最新のフレームを返す。描画 1 フレームに 1 回呼ばれ、ファームの 1 刻みに
        /// 1 回ではない。橋渡しはファーム自身の 50 Hz の仮想時間の周期で ESP-NOW へ
        /// 注入するので、要るのは最新の値だけである。
        /// </summary>
        RcFrame Read();
    }

    /// <summary>
    /// The 12-bit ADC scale every stick value uses, and the conversion from the
    /// -100..100 deflection a person types. Same scale as `*.scn` rc events and
    /// `scenario_inject.hpp`, so a value means the same thing on both sides.
    ///
    /// スティックの値が使う 12 bit の ADC の尺度と、人が打つ -100..100 の振れ幅
    /// からの変換。`*.scn` の rc 事象・`scenario_inject.hpp` と同じ尺度なので、
    /// 値の意味は両側で一致する。
    /// </summary>
    public static class RcScale
    {
        /// <summary>The centre count: neither up nor down. / 中央の値。上でも下でもない。</summary>
        public const ushort Centre = 2048;

        /// <summary>The lowest count the ADC reports. / ADC が返す最小の値。</summary>
        public const ushort Minimum = 0;

        /// <summary>The highest count the ADC reports. / ADC が返す最大の値。</summary>
        public const ushort Maximum = 4095;

        /// <summary>
        /// Counts per unit of deflection. 100 units reach 2048 + 2047, the top
        /// of the range — the same 20.47 `sf sils fly` uses.
        /// 振れ幅 1 単位あたりの値。100 単位で 2048 + 2047 の上限に届く。
        /// `sf sils fly` が使う 20.47 と同じである。
        /// </summary>
        public const float CountsPerUnit = 20.47f;

        /// <summary>
        /// Turns a deflection of -100..100 into a raw ADC count, clamped to the
        /// ADC's range so an out-of-range deflection cannot wrap.
        /// -100..100 の振れ幅を生の ADC の値に直す。ADC の範囲に収めるので、
        /// 範囲外の振れ幅が回り込むことはない。
        /// </summary>
        public static ushort FromDeflection(float deflection)
        {
            float counts = Centre + deflection * CountsPerUnit;
            bool isBelow = counts < Minimum;
            if (isBelow)
            {
                return Minimum;
            }

            bool isAbove = counts > Maximum;
            if (isAbove)
            {
                return Maximum;
            }

            return (ushort)UnityEngine.Mathf.Round(counts);
        }
    }
}
