/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the downward ToF sensor).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using StampFly.World;
using UnityEngine;

namespace StampFly.Sim
{
    /// <summary>
    /// What one downward beam found: how far, whether it found anything, and
    /// how well the surface it hit would be tracked by an optical-flow sensor.
    /// 下向きの光線 1 本が見つけたもの。距離、何かに当たったか、そして当たった面を
    /// オプティカルフローのセンサがどれだけよく追えるか。
    /// </summary>
    public readonly struct RangeReading
    {
        public RangeReading(float distance, bool isValid, float heightAboveSurface,
                            float flowQuality)
        {
            Distance = distance;
            IsValid = isValid;
            HeightAboveSurface = heightAboveSurface;
            FlowQuality = flowQuality;
        }

        /// <summary>Distance along the beam [m]. / 光線方向の距離 [m]。</summary>
        public float Distance { get; }

        /// <summary>Whether the beam hit a surface within range. / 有効範囲内で面に当たったか。</summary>
        public bool IsValid { get; }

        /// <summary>Height above the surface below, measured vertically [m]. / 真下の面からの高さ。鉛直に測る [m]。</summary>
        public float HeightAboveSurface { get; }

        /// <summary>
        /// How well the surface takes optical flow, 0..1, from the world file's
        /// per-surface `flow_quality`. Stage 3 reads it but has nowhere to send
        /// it — the ABI has no field for it — so it is only shown on the panel.
        /// 面がオプティカルフローをどれだけよく取れるか（0〜1）。空間ファイルの
        /// 面ごとの `flow_quality` から来る。段階 3 では読むだけで渡し先が無い ―
        /// ABI に欄が無い ― ので、いまは表示に使うだけである。
        /// </summary>
        public float FlowQuality { get; }
    }

    /// <summary>
    /// The downward Time-of-Flight sensor as a single ray, which is what stage 3
    /// asks for; the cone of nine rays is stage 5's work.
    ///
    /// 下向きの ToF（Time of Flight、光の飛行時間による測距）を光線 1 本で表す。
    /// 段階 3 が求めるのはこれで、9 本の円錐は段階 5 の作業である。
    ///
    /// The beam leaves along the vehicle's own down axis, so a tilted vehicle
    /// measures the slant distance — as the real sensor does. The height above
    /// the surface is measured separately, straight down in the world frame,
    /// because that is what the firmware's altitude estimate wants.
    ///
    /// 光線は機体自身の下方向へ出るので、傾いた機体は斜めの距離を測る ― 実機の
    /// センサと同じである。面からの高さはそれとは別に、世界系で真下へ測る。
    /// ファームの高度推定が欲しいのはそちらだからである。
    ///
    /// @design docs/plans/unity-simulator.md §5 段階 3
    /// </summary>
    public sealed class DownwardRangefinder
    {
        /// <summary>
        /// The VL53L3CX's longest useful reading [m]. Past this the beam is
        /// reported as finding nothing, which is what the real sensor does.
        /// VL53L3CX が有効に測れる最長の距離 [m]。これを越えると、実機と同じく
        /// 「何も見つからなかった」として報告する。
        /// </summary>
        public const float MaxRangeMeters = 4.0f;

        /// <summary>
        /// The 30 mm blind zone below the sensor. Closer than this the real part
        /// returns 0.000 m or an invalid reading, so a vehicle sitting on the
        /// floor reads invalid rather than zero — see the stampfly-tof knowledge
        /// note. A host that reported a confident 0 would tell the firmware the
        /// floor is at its own altitude.
        /// センサ直下 30 mm の盲点。これより近いと実機は 0.000 m か無効を返すので、
        /// 床に置いた機体は 0 ではなく無効を読む。自信を持って 0 を返すホストは、
        /// ファームに「床は自分と同じ高さにある」と伝えてしまう。
        /// </summary>
        public const float BlindZoneMeters = 0.030f;

        /// <summary>
        /// The flow quality reported when the beam found nothing. Zero, because
        /// a sensor looking at nothing tracks nothing.
        /// 光線が何にも当たらなかったときに返すフローの品質。何も見ていない
        /// センサは何も追えないので 0 とする。
        /// </summary>
        public const float NoSurfaceFlowQuality = 0.0f;

        private readonly int layerMask;

        /// <summary>
        /// Builds a rangefinder that sees <paramref name="layers"/>. The default
        /// sees everything but triggers, so world geometry counts and a trigger
        /// volume placed later does not.
        /// <paramref name="layers"/> を見る測距器を作る。既定はトリガ以外の全てを
        /// 見るので、空間の形は当たり、後から置いたトリガの領域は当たらない。
        /// </summary>
        public DownwardRangefinder(int layers = Physics.DefaultRaycastLayers)
        {
            layerMask = layers;
        }

        /// <summary>
        /// Casts the beam from <paramref name="origin"/> along the vehicle's
        /// down axis, and separately straight down for the height.
        /// <paramref name="origin"/> から機体の下方向へ光線を出し、高さのために
        /// 別途まっすぐ下へも出す。
        /// </summary>
        public RangeReading Measure(Vector3 origin, Quaternion rotation)
        {
            Vector3 beamDirection = rotation * Vector3.down;
            bool beamHit = Physics.Raycast(
                origin, beamDirection, out RaycastHit hit, MaxRangeMeters,
                layerMask, QueryTriggerInteraction.Ignore);

            float height = MeasureHeight(origin);
            if (!beamHit)
            {
                return new RangeReading(
                    MaxRangeMeters, false, height, NoSurfaceFlowQuality);
            }

            bool isInBlindZone = hit.distance < BlindZoneMeters;
            return new RangeReading(
                hit.distance, !isInBlindZone, height, FlowQualityAt(hit));
        }

        /// <summary>
        /// Height above whatever is straight below, in the world frame. A miss
        /// reports the beam's maximum, so a vehicle over a hole is treated as
        /// high rather than as touching down.
        /// 世界系で、真下にあるものからの高さ。当たらなければ光線の最大距離を
        /// 返すので、穴の上の機体は接地ではなく高い位置として扱われる。
        /// </summary>
        private float MeasureHeight(Vector3 origin)
        {
            bool hitBelow = Physics.Raycast(
                origin, Vector3.down, out RaycastHit hit, MaxRangeMeters,
                layerMask, QueryTriggerInteraction.Ignore);
            return hitBelow ? hit.distance : MaxRangeMeters;
        }

        /// <summary>
        /// The surface's own flow quality. Every collider the world package
        /// builds carries an <see cref="ObstacleInfo"/> — floor, walls and
        /// ceiling included — so a hit on the world always has an answer; a hit
        /// on anything else reports nothing trackable.
        /// 面が持つフローの品質。空間の一式が作るコライダは、床・壁・天井も含め
        /// すべて <see cref="ObstacleInfo"/> を持つので、空間に当たれば必ず答えが
        /// ある。それ以外に当たった場合は「追えない」として返す。
        /// </summary>
        private static float FlowQualityAt(RaycastHit hit)
        {
            var info = hit.collider.GetComponent<ObstacleInfo>();
            bool hasInfo = info != null;
            return hasInfo ? info.FlowQuality : NoSurfaceFlowQuality;
        }
    }
}
