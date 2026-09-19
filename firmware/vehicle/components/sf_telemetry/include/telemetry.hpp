/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (vehicle firmware).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file telemetry.hpp
 * @brief Telemetry — UDP packet construction and transmission
 *        テレメトリ — UDPパケット構築と送信
 *
 * Collects data from various topics (state estimate, control output,
 * sensor data, system mode) and sends unified telemetry packets
 * over UDP to connected clients.
 *
 * 各種トピック（状態推定、制御出力、センサデータ、システムモード）から
 * データを収集し、統合テレメトリパケットをUDP経由で接続クライアントに送信する。
 *
 * @design detailed_design.md §10 — UDP telemetry wire format           [OK]
 * @design architecture.md §6 — TelemetryTask (50Hz, priority 13)       [OK]
 * @design coding_and_education.md §2 — Bilingual comments              [OK]
 */

#pragma once

#include <cstddef>   // offsetof — wire-offset static_assert / 電文オフセット検査用
#include <cstdint>

#include "data_types.hpp"   // sf::SensorSnapshot (accumulateFlow) / フロー積算の引数型

namespace sf {

// -----------------------------------------------------------------------------
// Phase 2a basic telemetry packet (binary, fixed layout)
// Phase 2a 基本テレメトリパケット（バイナリ・固定レイアウト）
//
// All multi-byte fields are little-endian (ESP32 native order).
// Wire layout is locked by the packed attribute below.
//
// 全多バイトフィールドはリトルエンディアン（ESP32ネイティブ順）。
// パケットレイアウトは以下の packed 属性で固定する。
// -----------------------------------------------------------------------------

/// Magic word — sentinel for valid Phase 2a packets / 有効パケット識別子
inline constexpr uint16_t TELEM_MAGIC = 0xCAFE;

/// Telemetry protocol version — 2 since the v2 sensor block was appended
/// テレメトリプロトコルバージョン — v2 センサ部の追記により 2
///
/// Why the version moves but packet_type does not: the v2 block is APPENDED,
/// so the first 104 bytes are bit-identical to v1 and an old receiver keeps
/// decoding them. Receivers therefore dispatch on TOTAL LENGTH (104 = v1,
/// 140 = v2) and use this field only to validate — the same rule
/// data_stream_wire.hpp's WireStatusPayload already uses for its 17/53/57B
/// revisions. Bumping packet_type too would force a two-dimensional
/// (type, version) branch on the PC side and throw that compatibility away.
///
/// なぜ version だけ上げ packet_type を据え置くか: v2 部は「末尾追記」なので
/// 先頭 104B は v1 とビット単位で同一であり、旧受信機はそのまま復号を続けられる。
/// よって受信側は「全長」で判別し（104=v1、140=v2）、本フィールドは検証にのみ使う
/// — data_stream_wire.hpp の WireStatusPayload が 17/53/57B の版で既に採る規則と
/// 同じ。packet_type も変えると PC 側が (type, version) の2次元分岐になり、
/// この前方互換の利点を自ら捨てることになる。
inline constexpr uint8_t  TELEM_VERSION = 2;

/// Packet type — Phase 2a basic. Unchanged by design (see TELEM_VERSION).
/// パケット種別: Phase 2a 基本。設計上あえて据え置き（TELEM_VERSION 参照）。
inline constexpr uint8_t  TELEM_TYPE_PHASE2A_BASIC = 0x01;

/// Default broadcast destination port / 既定のブロードキャスト送信先ポート
inline constexpr uint16_t UDP_TELEMETRY_PORT = 5005;

// -----------------------------------------------------------------------------
// v2 sensor block — validity bits and freshness thresholds
// v2 センサ部 — 有効ビットと鮮度しきい値
// -----------------------------------------------------------------------------

/// Bit positions in TelemetryPacket::valid_flags.
/// TelemetryPacket::valid_flags のビット位置。
inline constexpr uint8_t TELEM_VALID_TOF_BOTTOM = 1u << 0;
inline constexpr uint8_t TELEM_VALID_TOF_FRONT  = 1u << 1;
inline constexpr uint8_t TELEM_VALID_FLOW       = 1u << 2;
inline constexpr uint8_t TELEM_VALID_MAG        = 1u << 3;
inline constexpr uint8_t TELEM_VALID_BARO       = 1u << 4;
inline constexpr uint8_t TELEM_VALID_POWER      = 1u << 5;

/// Placeholder distance sent when there is no front-ToF reading — the sensor is
/// absent, disabled by the tof.front.enable parameter, failed to initialise
/// (it needs battery power), or its last sample has gone stale.
/// Matches ws::tof_front()'s -1.0 so both surfaces agree on "no reading".
///
/// Why not a separate bool per sensor: valid_flags already exists and one bit
/// is enough, so a bool would cost a byte and create a second source of truth
/// for the same fact. THE AUTHORITY IS THE BIT — receivers must test
/// TELEM_VALID_TOF_FRONT and never compare against this value, because a real
/// sensor could legitimately report a negative number on error.
///
/// 前方 ToF の測定値が無いときに送る placeholder 距離 — センサ未実装、
/// tof.front.enable による無効化、初期化失敗（バッテリー電源が要る）、
/// あるいは最終サンプルが古くなった場合。ws::tof_front() の -1.0 と
/// 揃えてあり、両者で「測っていない」の表し方が一致する。
///
/// なぜセンサ毎の bool にしないか: valid_flags が既にあり 1 ビットで足りる。bool に
/// すると 1 バイト増える上、同じ事実に対する基準が二重になる。**正は「ビット」** で、
/// 受信側は TELEM_VALID_TOF_FRONT を見ること。この値との比較で判定してはならない
/// （実センサは異常時に負値を返しうるため）。
inline constexpr float kTelemTofFrontUnavailable = -1.0f;

/// Age beyond which a mirrored sensor sample is reported as invalid (R16).
/// The slowest mirrored sensor is the barometer at 50Hz (20ms); 500ms is
/// 25 of its periods, so a healthy sensor never trips it while a stopped
/// one is flagged within half a second.
///
/// ミラーされたセンサ値を無効として報告する経過時間のしきい値（R16）。ミラー対象で
/// 最も遅いのは 50Hz（20ms）の気圧計で、500ms はその 25 周期にあたる。正常なセンサが
/// 誤って無効になることはなく、停止したセンサは 0.5 秒以内に無効と分かる。
inline constexpr uint32_t kTelemSensorStaleUs = 500000;   // 500 ms

/// Battery voltage reported when the power monitor has never published.
/// PowerData zero-initialises to 0.0 V, which is also the "unknown" marker.
/// 電源モニタが一度も publish していないときに報告する電池電圧。PowerData は
/// 0.0V にゼロ初期化され、これが「不明」の印も兼ねる。
inline constexpr float kTelemVoltageUnknown = 0.0f;

#pragma pack(push, 1)
/// Unified Phase 2a telemetry packet
/// Phase 2a 統合テレメトリパケット
struct TelemetryPacket {
    // Header / ヘッダ
    uint16_t magic;          // 0xCAFE
    uint8_t  version;        // = TELEM_VERSION
    uint8_t  packet_type;    // = TELEM_TYPE_PHASE2A_BASIC
    uint32_t timestamp_us;   // Microsecond timestamp / マイクロ秒タイムスタンプ

    // Attitude (Euler) / 姿勢（オイラー角）
    float roll, pitch, yaw;          // [rad]

    // Body-rate gyro / 機体角速度
    float gyro_x, gyro_y, gyro_z;    // [rad/s]

    // Body-frame acceleration / 機体加速度
    float accel_x, accel_y, accel_z; // [m/s^2]

    // Position (NED) / 位置（NED）
    float pos_x, pos_y, pos_z;       // [m]

    // Velocity (NED) / 速度（NED）
    float vel_x, vel_y, vel_z;       // [m/s]

    // Control output / 制御出力
    float thrust, roll_torque, pitch_torque, yaw_torque;

    // Motor duty M1..M4 / モーターduty
    float motor_duty[4];

    // System mode / システムモード
    uint8_t system_mode;             // FlightState enum value / FlightState値
    uint8_t reserved[3];             // Pad to 4-byte boundary / 4Bアライン

    // -------------------------------------------------------------------
    // v2 sensor block (offset 104..139) — APPEND ONLY, never reorder.
    // Everything above this line is the frozen v1 layout.
    // v2 センサ部（オフセット 104〜139）— 追記のみ、並べ替え禁止。
    // この行より上は凍結された v1 レイアウト。
    // -------------------------------------------------------------------

    float   voltage;         // [V] battery pack / 電池電圧（0 = 不明）
    float   tof_bottom;      // [m] downward ToF / 下向き ToF 距離
    float   tof_front;       // [m] forward ToF (kTelemTofFrontUnavailable = no reading) / 前方 ToF（測定値なしは kTelemTofFrontUnavailable）
    int16_t flow_dx_sum;     // [counts] displacement since last send / 前回送信からの変位
    int16_t flow_dy_sum;     // [counts] displacement since last send / 同上
    uint8_t flow_squal;      // Latest surface quality / 最新の表面品質
    uint8_t valid_flags;     // TELEM_VALID_* bits / TELEM_VALID_* ビット
    uint8_t reserved2[2];    // Zero; keeps mag on a 4-byte offset / 0。mag を4B境界に保つ
    float   mag_x, mag_y, mag_z;   // [uT] calibrated / 補正済み地磁気
    float   baro_altitude;   // [m] pressure altitude / 気圧高度
};
#pragma pack(pop)

static_assert(sizeof(TelemetryPacket) == 140,
              "TelemetryPacket layout drift — wire format must stay 140 bytes");

// Field offsets are the wire contract shared with the PC decoders
// (lib/sfcli/commands/telemetry.py). Pinning them here makes a silent
// reordering a COMPILE error rather than a field-shifted packet that only
// shows up as nonsense on a dashboard. lib/sfcli/commands/test_telemetry.py
// carries the same table and checks Python's struct format against it.
//
// フィールドのオフセットは PC 側デコーダ（lib/sfcli/commands/telemetry.py）と
// 共有する電文契約。ここで固定することで、並べ替えを「ダッシュボードに出た値が
// おかしい」ではなく「コンパイルエラー」として検出できる。
// lib/sfcli/commands/test_telemetry.py が同じ表を持ち、Python の struct 書式を検査する。
static_assert(offsetof(TelemetryPacket, voltage)      == 104, "v2 offset drift: voltage");
static_assert(offsetof(TelemetryPacket, tof_bottom)   == 108, "v2 offset drift: tof_bottom");
static_assert(offsetof(TelemetryPacket, tof_front)    == 112, "v2 offset drift: tof_front");
static_assert(offsetof(TelemetryPacket, flow_dx_sum)  == 116, "v2 offset drift: flow_dx_sum");
static_assert(offsetof(TelemetryPacket, flow_dy_sum)  == 118, "v2 offset drift: flow_dy_sum");
static_assert(offsetof(TelemetryPacket, flow_squal)   == 120, "v2 offset drift: flow_squal");
static_assert(offsetof(TelemetryPacket, valid_flags)  == 121, "v2 offset drift: valid_flags");
static_assert(offsetof(TelemetryPacket, reserved2)    == 122, "v2 offset drift: reserved2");
static_assert(offsetof(TelemetryPacket, mag_x)        == 124, "v2 offset drift: mag_x");
static_assert(offsetof(TelemetryPacket, baro_altitude) == 136, "v2 offset drift: baro_altitude");

/// Telemetry manager: collect, pack, and send via UDP
/// テレメトリマネージャー: 収集、パック、UDP送信
class Telemetry {
public:
    /// Initialize telemetry subsystem (open UDP socket)
    /// テレメトリサブシステムを初期化（UDPソケットを開く）
    void init();

    /// Collect latest data from topics and send one UDP packet
    /// トピックから最新データを収集し、UDPパケットを1回送信する
    void update();

    /// Override destination address/port (host byte order for port)
    /// 送信先アドレスとポートを上書き（portはホストバイトオーダ）
    void setDestination(uint32_t ip_be, uint16_t port);

private:
    /// Wait until WiFi STA has an IP address (poll-with-backoff)
    /// WiFi STA がIPを取得するまで待機（ポーリング＋バックオフ）
    /// @return true if the network became ready / ネットワーク準備完了なら true
    bool waitForWifi();

    /// Build the binary packet from current topic latest()
    /// 現在のトピック latest() からバイナリパケットを組み立てる
    void buildPacket(TelemetryPacket& pkt);

    /// Fill the frozen v1 region (header, estimate, IMU, control, motors)
    /// 凍結された v1 部（ヘッダ・推定値・IMU・制御・モータ）を詰める
    void packBaseFields(TelemetryPacket& pkt);

    /// Fill the appended v2 sensor block (power + mirrored async sensors)
    /// 追記した v2 センサ部（電源＋ミラーされた非同期センサ）を詰める
    void packSensorFields(TelemetryPacket& pkt);

    /// Displacement since the previous packet, from the snapshot's totals
    /// スナップショットの累積から、前回パケット以降の変位を求める
    void takeFlowDelta(const SensorSnapshot& snapshot,
                       int32_t& dx_out, int32_t& dy_out);

    /// Send packet via UDP, with rate-limited error logging
    /// UDP でパケット送信。エラーはレート制限付きでログ出力
    void sendPacket(const TelemetryPacket& pkt);

    int      socket_fd_   = -1;     // UDP socket fd / UDPソケットfd
    uint32_t dest_ip_be_  = 0;      // Destination IP (network/big-endian)
    uint16_t dest_port_   = 0;      // Destination port (host order)
    uint32_t err_count_   = 0;      // sendto failure counter / 送信失敗カウンタ
    bool     ready_       = false;  // True after socket bound / ソケット準備完了
    bool     network_up_  = false;  // WiFi ready (sends gated on this) / WiFi準備完了（送信の条件）

    // Where the flow totals stood when the previous packet was built. The
    // difference against the current totals is the displacement to report, and
    // it includes EVERY sample ImuTask added in between — nothing is lost to
    // the 50Hz sampling rate. Unsigned because the totals wrap (see
    // SensorSnapshot); see takeFlowDelta().
    //
    // 前回パケットを組み立てた時点のフロー累積。現在の累積との差が報告すべき変位で、
    // その間に ImuTask が加えた「全」サンプルを含む — 50Hz という周期のために失われる
    // ものは無い。累積は折り返すので符号なしである（SensorSnapshot 参照）。
    // 詳細は takeFlowDelta() を参照。
    uint32_t last_flow_dx_total_ = 0;   // [counts] at previous packet / 前回パケット時点
    uint32_t last_flow_dy_total_ = 0;   // [counts] at previous packet / 同上
    bool     flow_baseline_set_  = false;  // Baseline captured / 基準を取得済みか
};

}  // namespace sf
