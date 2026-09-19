/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (vehicle firmware).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file telemetry.cpp
 * @brief Telemetry implementation — Phase 2a UDP broadcast
 *        テレメトリ実装 — Phase 2a UDPブロードキャスト
 *
 * Sends a single 140-byte binary packet (TelemetryPacket, see telemetry.hpp
 * static_assert) over UDP to the broadcast address 255.255.255.255:UDP_TELEMETRY_PORT
 * at 50 Hz. WiFi STA mode is owned by sf_comm; this module merely waits until WiFi
 * reports an IP address.
 *
 * 140バイトのバイナリパケット（TelemetryPacket、telemetry.hpp の static_assert 参照）を
 * 255.255.255.255:UDP_TELEMETRY_PORT に 50 Hz でUDPブロードキャストする。WiFi STAモードは
 * sf_comm が所有しており、本モジュールは IP 取得を待つだけ。
 *
 * Packet size does not affect cost here: lwIP's sendto() on ESP32 costs ~2.5ms
 * per CALL regardless of payload (117B and 840B measure the same), so the 104->140B
 * growth is free as long as the call rate stays at 50Hz. See
 * docs/architecture/udp-telemetry-design.md §3.
 * パケットサイズはコストに影響しない: ESP32 の lwIP sendto() はペイロードによらず
 * 1「回」あたり約2.5ms（117B と 840B で同じ実測）。呼び出し回数が 50Hz のままなら
 * 104→140B の増加は無償である。docs/architecture/udp-telemetry-design.md §3 参照。
 *
 * @design detailed_design.md §10 — UDP telemetry wire format           [OK]
 * @design architecture.md §6 — TelemetryTask (50Hz, priority 13)       [OK]
 */

#include "telemetry.hpp"
#include "comm.hpp"
#include "topics.hpp"
#include "sf_math.hpp"

#include "esp_log.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_netif.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "lwip/sockets.h"
#include "lwip/netdb.h"

#include <cstring>

static const char* TAG = "telemetry";

namespace sf {

// -----------------------------------------------------------------------------
// Internal constants — kept local to avoid leaking into headers
// 内部定数 — ヘッダに漏らさないためローカルに置く
// -----------------------------------------------------------------------------
namespace {

// Polling parameters while waiting for WiFi STA to acquire an IP.
// WiFi STA が IP を取得するまでの待機ポーリング設定。
constexpr TickType_t kWifiPollIntervalTicks = pdMS_TO_TICKS(200);
constexpr int        kWifiPollMaxAttempts   = 150;   // 200ms x 150 = 30s budget

// Rate-limit sendto errors so the log isn't flooded.
// sendto エラーログのレート制限（フラッディング防止）。
constexpr uint32_t kErrorLogEveryN = 50;             // ~1 per second at 50Hz

}  // namespace

// -----------------------------------------------------------------------------
// init — wait for WiFi, open the UDP socket, set defaults
// 初期化 — WiFi待機、UDPソケットを開き、既定値を設定
// -----------------------------------------------------------------------------
void Telemetry::init()
{
    // Block until sf_comm has WiFi up — we don't own WiFi. In ESP-NOW-only
    // operation (STA mode without credentials) the network never comes up:
    // remember that and keep update() silent instead of failing a broadcast
    // sendto 50 times a second (EHOSTUNREACH log spam).
    // sf_comm が WiFi を起動するまでブロックする（WiFi所有はsf_comm）。ESP-NOW のみ
    // の運用（資格情報なしの STA モード）ではネットワークは上がらない: それを記憶し、
    // 毎秒50回ブロードキャスト sendto を失敗させる（EHOSTUNREACH ログ氾濫）代わりに
    // update() を沈黙させる。
    network_up_ = waitForWifi();

    // Create a UDP datagram socket on IPv4.
    // IPv4 の UDP データグラムソケットを生成。
    socket_fd_ = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (socket_fd_ < 0) {
        ESP_LOGE(TAG, "socket() failed: errno=%d", errno);
        return;
    }

    // Enable broadcast so 255.255.255.255 is permitted.
    // ブロードキャストを許可（255.255.255.255 を有効化）。
    int broadcast_enable = 1;
    if (::setsockopt(socket_fd_, SOL_SOCKET, SO_BROADCAST,
                     &broadcast_enable, sizeof(broadcast_enable)) < 0) {
        ESP_LOGW(TAG, "SO_BROADCAST failed: errno=%d", errno);
    }

    // Make sendto() non-blocking so a missing receiver cannot stall the loop.
    // sendto をノンブロッキング化（受信者欠如でループ停滞を防ぐ）。
    int flags = ::fcntl(socket_fd_, F_GETFL, 0);
    if (flags >= 0) {
        ::fcntl(socket_fd_, F_SETFL, flags | O_NONBLOCK);
    }

    // Default destination: limited broadcast on UDP_TELEMETRY_PORT.
    // 既定送信先: UDP_TELEMETRY_PORT へのリミテッドブロードキャスト。
    dest_ip_be_ = htonl(INADDR_BROADCAST);   // 255.255.255.255
    dest_port_  = UDP_TELEMETRY_PORT;

    ready_ = true;
    ESP_LOGI(TAG, "Telemetry ready: dest=255.255.255.255:%u, packet=%u bytes",
             static_cast<unsigned>(dest_port_),
             static_cast<unsigned>(sizeof(TelemetryPacket)));
}

// -----------------------------------------------------------------------------
// waitForWifi — block on sf_comm's WiFi-ready EventGroup
// WiFi待機 — sf_comm の WiFi 準備完了 EventGroup を待つ
// -----------------------------------------------------------------------------
//
// Previously this polled esp_netif_get_ip_info() in a loop. Now it
// delegates to sf::waitForWifiReady() which sleeps efficiently on an
// EventGroup bit set by sf_comm's IP_EVENT_STA_GOT_IP handler (R16).
//
// 旧実装は esp_netif_get_ip_info() を polling ループで呼んでいた。
// 現在は sf::waitForWifiReady() に委譲し、sf_comm の
// IP_EVENT_STA_GOT_IP ハンドラがセットする EventGroup ビットで効率的に
// スリープする (R16)。
//
// On timeout we log a warning and return — callers must tolerate the
// case where IP is never obtained (e.g. ESP-NOW-only operation without
// AP association). The UDP socket is still opened; broadcast may or
// may not work depending on the actual network state.
//
// タイムアウト時は警告ログを出して return する。呼び出し側は IP を
// 取得しないケース (ESP-NOW のみで AP 非接続) を許容する必要がある。
// UDP ソケット自体は開かれるが、ブロードキャスト送信が機能するかは
// 実ネットワーク状態に依存する。
// -----------------------------------------------------------------------------
bool Telemetry::waitForWifi()
{
    constexpr uint32_t kReadyTimeoutMs = 30000;  // 30s: previous polling cap
    if (waitForWifiReady(kReadyTimeoutMs)) {
        esp_netif_t* sta_netif =
            esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
        esp_netif_ip_info_t ip{};
        if (sta_netif != nullptr &&
            esp_netif_get_ip_info(sta_netif, &ip) == ESP_OK) {
            ESP_LOGI(TAG, "WiFi ready: IP=" IPSTR, IP2STR(&ip.ip));
        } else {
            ESP_LOGI(TAG, "WiFi ready (IP info unavailable)");
        }
        return true;
    }
    ESP_LOGW(TAG, "No network after %lu ms — telemetry idle until WiFi comes up "
                  "(ESP-NOW control unaffected)",
             static_cast<unsigned long>(kReadyTimeoutMs));
    return false;
}

// -----------------------------------------------------------------------------
// update — build one packet from latest topic data and send it
// 更新 — 最新トピックデータから1パケットを構築して送信
// -----------------------------------------------------------------------------
void Telemetry::update()
{
    if (!ready_) return;

    // No network yet (ESP-NOW-only boot, or STA still associating): poll the
    // readiness bit without blocking so a late STA connection enables sends,
    // and stay silent meanwhile.
    // まだネットワークなし（ESP-NOW のみ起動、または STA 接続中）: 非ブロッキングで
    // 準備ビットをポーリングし、遅れて STA が繋がれば送信を開始。それまでは沈黙。
    if (!network_up_) {
        network_up_ = waitForWifiReady(0);
        if (!network_up_) {
            return;
        }
        ESP_LOGI(TAG, "Network up — telemetry broadcast enabled");
    }

    TelemetryPacket pkt{};
    buildPacket(pkt);
    sendPacket(pkt);
}

// -----------------------------------------------------------------------------
// buildPacket — fill the packet from sf::* topic latest() snapshots
// パケット構築 — sf::* トピックの latest() スナップショットを詰める
//
// Split into the frozen v1 region and the appended v2 sensor block so each
// half stays within the 50-line limit (coding_and_education.md §2).
// 凍結された v1 部と追記した v2 センサ部に分割し、各々を50行以内に保つ
// （coding_and_education.md §2）。
// -----------------------------------------------------------------------------
void Telemetry::buildPacket(TelemetryPacket& pkt)
{
    packBaseFields(pkt);
    packSensorFields(pkt);
}

// -----------------------------------------------------------------------------
// packBaseFields — the v1 region: header, estimate, IMU, control, motors
// v1 部 — ヘッダ・推定値・IMU・制御・モータ
// -----------------------------------------------------------------------------
void Telemetry::packBaseFields(TelemetryPacket& pkt)
{
    // Snapshot all topics first to minimize skew between fields.
    // フィールド間のずれを最小化するため、まず全トピックをスナップショット。
    const StateEstimate state   = estimate_state.latest();
    const ControlOutput control = control_output.latest();
    const MotorOutput   motor   = actuator_motor.latest();
    const ImuData       imu     = sensor_imu.latest();
    const SystemMode    mode    = system_mode.latest();

    // Header / ヘッダ
    pkt.magic        = TELEM_MAGIC;
    pkt.version      = TELEM_VERSION;
    pkt.packet_type  = TELEM_TYPE_PHASE2A_BASIC;
    pkt.timestamp_us = static_cast<uint32_t>(esp_timer_get_time());

    // Convert quaternion (w,x,y,z) to Euler (roll,pitch,yaw).
    // クォータニオン (w,x,y,z) をオイラー角 (roll,pitch,yaw) に変換。
    sf::math::Quat q(state.attitude[0], state.attitude[1],
                     state.attitude[2], state.attitude[3]);
    sf::math::Vec3 euler = q.to_euler();
    pkt.roll  = euler.x;
    pkt.pitch = euler.y;
    pkt.yaw   = euler.z;

    // IMU body-frame rates and acceleration (raw latest sample).
    // IMU 機体系角速度・加速度（最新サンプルの生値）。
    pkt.gyro_x  = imu.gyro[0];
    pkt.gyro_y  = imu.gyro[1];
    pkt.gyro_z  = imu.gyro[2];
    pkt.accel_x = imu.accel[0];
    pkt.accel_y = imu.accel[1];
    pkt.accel_z = imu.accel[2];

    // Position / Velocity in NED frame from estimator.
    // 推定器から得た NED 系の位置・速度。
    pkt.pos_x = state.position[0];
    pkt.pos_y = state.position[1];
    pkt.pos_z = state.position[2];
    pkt.vel_x = state.velocity[0];
    pkt.vel_y = state.velocity[1];
    pkt.vel_z = state.velocity[2];

    // Control output: thrust [N] + body torque vector [Nm].
    // 制御出力: 推力 [N] + 機体トルク [Nm]。
    pkt.thrust       = control.thrust;
    pkt.roll_torque  = control.torque[0];
    pkt.pitch_torque = control.torque[1];
    pkt.yaw_torque   = control.torque[2];

    // Motor duty M1..M4 (X-quad order).
    // モーターduty M1..M4（X-quad順）。
    for (int i = 0; i < 4; ++i) {
        pkt.motor_duty[i] = motor.duty[i];
    }

    // System mode (flight state byte; sub_mode/armed are not in Phase 2a).
    // システムモード（FlightState のみ。sub_mode/armed は Phase 2a 対象外）。
    pkt.system_mode = mode.state;
}

// -----------------------------------------------------------------------------
// packSensorFields — the v2 block: power + mirrored async sensors
// v2 部 — 電源＋ミラーされた非同期センサ
//
// Reads sensor_snapshot rather than sensor_tof/flow/mag/baro directly: those
// are destructive single-consumer queues drained by ImuTask, and a second
// reader would steal samples from the estimator (R5). sensor_snapshot exists
// precisely so monitors can peek without stealing.
// sensor_tof/flow/mag/baro を直接読まず sensor_snapshot を読む: 前者は ImuTask が
// 排出する破壊的読み出しの単一 consumer キューで、二人目の読み手は推定器から
// サンプルを奪う（R5）。sensor_snapshot はまさに「奪わずに覗く」ために存在する。
// -----------------------------------------------------------------------------
void Telemetry::packSensorFields(TelemetryPacket& pkt)
{
    const SensorSnapshot snapshot = sensor_snapshot.latest();
    const PowerData      power    = sensor_power.latest();
    const uint32_t       now_us   = static_cast<uint32_t>(esp_timer_get_time());

    // Freshness per sensor (R16): a mirrored value persists across cycles, so
    // without an age check a stopped sensor would be reported as working
    // indefinitely.
    // Timestamps are uint32 microseconds and wrap every ~71 minutes; unsigned
    // subtraction stays correct across that wrap, so it is used deliberately.
    // センサ毎の鮮度（R16）: ミラー値は周期跨ぎで保持されるため、経過時間を見ないと
    // 停止したセンサが動作中だと誤って報告され続ける。タイムスタンプは uint32 マイクロ秒で
    // 約71分で桁あふれして 0 に戻るが、符号なし減算はその折り返しを跨いでも正しいので
    // 意図的にそれを用いる。
    auto is_fresh = [now_us](uint32_t stamp) -> bool {
        if (stamp == 0) return false;                 // never published / 未発行
        return (now_us - stamp) < kTelemSensorStaleUs;
    };

    uint8_t flags = 0;

    // Power is a Latest topic like the sensor mirror, so it too keeps its last
    // value after PowerTask stops — the age check is what stops a dead monitor
    // from reporting a plausible voltage forever. 10Hz (100ms) is well inside
    // the 500ms threshold.
    // 電源もセンサミラーと同じ Latest トピックなので、PowerTask が止まっても最終値を
    // 保持し続ける。応答しなくなった監視装置がもっともらしい電圧を報告し続けるのを
    // 防ぐのが経過時間の判定である。10Hz（100ms）はしきい値 500ms に十分収まる。
    pkt.voltage = power.voltage;
    if (power.voltage > kTelemVoltageUnknown && is_fresh(power.timestamp)) {
        flags |= TELEM_VALID_POWER;
    }

    // Bottom ToF: forward the published fact (tof_valid), do not re-derive it
    // — detection belongs to the sensor layer, not to telemetry (INV-3).
    // 底面 ToF: publish 済みの事実（tof_valid）をそのまま転送し、判定をやり直さない
    // — 検出はセンサ層の責務でテレメトリの責務ではない（INV-3）。
    pkt.tof_bottom = snapshot.tof_distance;
    if (snapshot.tof_valid && is_fresh(snapshot.tof_timestamp)) {
        flags |= TELEM_VALID_TOF_BOTTOM;
    }

    // Front ToF: same two-condition rule as every other mirrored sensor — the
    // sensor's own published validity AND freshness (R16). Forward the fact,
    // do not re-derive it (INV-3).
    //
    // When the front sensor is absent (not fitted, disabled by
    // tof.front.enable, or failed to initialise on USB-only power), TofTask
    // never publishes, so the snapshot keeps its zero-initialised fields: the
    // stamp stays 0, the freshness check fails, and bit1 stays clear. We still
    // send kTelemTofFrontUnavailable rather than that 0.0 so a receiver reading
    // the raw value sees the documented "no reading" marker instead of a
    // distance of zero metres. THE BIT REMAINS THE AUTHORITY either way.
    //
    // 前方 ToF: 他のミラーされたセンサと同じ 2 条件 — センサ自身が publish した
    // 有効性「かつ」鮮度（R16）。事実を転送し、判定をやり直さない（INV-3）。
    //
    // 前方が無い場合（未実装、tof.front.enable による無効化、USB 給電のみでの初期化
    // 失敗）、TofTask は publish しないのでスナップショットはゼロ初期化のまま残る:
    // 刻印は 0 のままで鮮度判定が落ち、bit1 は立たない。それでも 0.0 ではなく
    // kTelemTofFrontUnavailable を送るのは、生値を読む受信側に「距離 0m」ではなく
    // 文書化された「測っていない」の印を見せるためである。いずれにせよ「正はビット」。
    const bool front_tof_measured =
        snapshot.tof_front_valid && is_fresh(snapshot.tof_front_timestamp);
    pkt.tof_front = front_tof_measured ? snapshot.tof_front_distance
                                       : kTelemTofFrontUnavailable;
    if (front_tof_measured) {
        flags |= TELEM_VALID_TOF_FRONT;
    }

    // Send the total itself, truncated to its low 16 bits, and let the
    // receiver take the difference. Truncation is exactly what is wanted here
    // — unlike a difference, a total is only ever read modulo 2^16, so the
    // discarded high bits carry no information the receiver needs.
    //
    // Why not the difference since the previous packet, which this field held
    // until 2026-09-19: UDP:5005 is a broadcast and WiFi does not retransmit
    // broadcasts. 42% of packets were measured lost on hardware, and a
    // difference that a lost packet was carrying is gone for good. A total
    // survives the loss: the next packet to arrive states the whole position
    // again. See kTelemFlowWrapSpan for the one remaining limit.
    //
    // 累計そのものを下位 16 ビットに切り詰めて送り、差を取るのは受信側に任せる。
    // ここでの切り捨てはまさに望むところである — 差分と違い、累計は常に 2^16 を法と
    // して読まれるので、捨てた上位ビットに受信側が必要とする情報は無い。
    //
    // なぜ 2026-09-19 までこの枠が持っていた「前回送信からの差分」をやめたか:
    // UDP:5005 はブロードキャストで、WiFi はブロードキャストを再送しない。実機では
    // 42% のパケットが失われており、失われたパケットが運んでいた差分は永久に戻らない。
    // 累計なら欠損を越えて生き残る。次に届いたパケットが現在地をあらためて述べる
    // からである。残る唯一の限界は kTelemFlowWrapSpan を参照。
    pkt.flow_dx_total16 = static_cast<uint16_t>(snapshot.flow_dx_total);
    pkt.flow_dy_total16 = static_cast<uint16_t>(snapshot.flow_dy_total);
    pkt.flow_squal      = snapshot.flow_squal;
    if (is_fresh(snapshot.flow_timestamp)) flags |= TELEM_VALID_FLOW;

    pkt.mag_x = snapshot.mag[0];
    pkt.mag_y = snapshot.mag[1];
    pkt.mag_z = snapshot.mag[2];
    if (is_fresh(snapshot.mag_timestamp)) flags |= TELEM_VALID_MAG;

    pkt.baro_altitude = snapshot.baro_altitude;
    if (is_fresh(snapshot.baro_timestamp)) flags |= TELEM_VALID_BARO;

    pkt.valid_flags = flags;
}

// -----------------------------------------------------------------------------
// sendPacket — UDP sendto with rate-limited error logging
// パケット送信 — UDP sendto。エラーはレート制限付きでログ
// -----------------------------------------------------------------------------
void Telemetry::sendPacket(const TelemetryPacket& pkt)
{
    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_port        = htons(dest_port_);
    addr.sin_addr.s_addr = dest_ip_be_;

    int sent = ::sendto(socket_fd_, &pkt, sizeof(pkt), 0,
                        reinterpret_cast<sockaddr*>(&addr), sizeof(addr));
    if (sent == static_cast<int>(sizeof(pkt))) return;

    // Rate-limit failure logging (~1/sec at 50Hz).
    // 送信失敗ログをレート制限（50Hz で約1回/秒）。
    if ((err_count_++ % kErrorLogEveryN) == 0) {
        ESP_LOGW(TAG, "sendto failed: ret=%d errno=%d (count=%lu)",
                 sent, errno, static_cast<unsigned long>(err_count_));
    }
}

// -----------------------------------------------------------------------------
// setDestination — override defaults (used by CLI or tests)
// 送信先設定 — 既定値を上書き（CLIやテスト用）
// -----------------------------------------------------------------------------
void Telemetry::setDestination(uint32_t ip_be, uint16_t port)
{
    dest_ip_be_ = ip_be;
    dest_port_  = port;
}

}  // namespace sf
