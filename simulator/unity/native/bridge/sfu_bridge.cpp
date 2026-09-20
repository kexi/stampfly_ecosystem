/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file sfu_bridge.cpp
 * @brief The implementation behind sfu_api.h: the whole unmodified firmware,
 *        the MuJoCo-free plant and the fiber/thread scheduler, behind a C ABI.
 *        sfu_api.h の実装。無改変のファーム全体・MuJoCo を使わないプラント・
 *        fiber 版／スレッド版のスケジューラを、C ABI の裏に置く。
 *
 * The startup sequence follows `simulator/sils/emu/emu_main.cpp` (plant init →
 * `set_on_advance` → seed the pairing store → `app_main` → parameter overrides →
 * run). `emu_main.cpp` itself is deliberately NOT linked: it owns `main`, reads
 * its configuration from environment variables, rewires `STDIN_FILENO` and ends
 * with `std::_Exit` — none of which a browser has or wants. The settings it read
 * from the environment are fields of `SfuConfig` instead.
 *
 * 起動の手順は `simulator/sils/emu/emu_main.cpp` に倣う（プラント初期化 →
 * `set_on_advance` → ペアリングの記録 → `app_main` → パラメータの上書き → 実行）。
 * `emu_main.cpp` 自体は意図的にリンクしない。同ファイルは `main` を持ち、設定を
 * 環境変数から読み、`STDIN_FILENO` を差し替え、`std::_Exit` で終わる ― どれも
 * ブラウザには無く、また要らないものである。環境変数から読んでいた設定は
 * `SfuConfig` の欄に置き換えた。
 *
 * @design docs/plans/unity-simulator.md §3 1 刻みの処理, §5 段階 2
 */

#include "sfu_api.h"

#include <cmath>
#include <cstring>

#include "data_types.hpp"
#include "flight_state.hpp"
#include "frames_unity.hpp"
#include "params.hpp"
#include "plant.hpp"
#include "plant_external_state.hpp"
#include "scenario_inject.hpp"
#include "scheduler.hpp"
#include "topics.hpp"
#include "virtual_board.hpp"

// The firmware's own entry point, unmodified: BSP init plus all 14 tasks.
// ファーム自身の入口、無改変: BSP 初期化と 14 タスク。
extern "C" void app_main(void);

namespace {

using sf::math::Quat;
using sf::math::Vec3;
namespace unity = sils::frames::unity;

// -----------------------------------------------------------------------------
// Module state. One module is one power-on, so all of this is written once by
// sfu_boot and then only read and stepped.
// モジュールの状態。1 モジュール＝1 回の電源投入なので、ここは sfu_boot が 1 度
// 書いた後は読むか進めるかしかしない。
// -----------------------------------------------------------------------------

sils::Plant g_plant;
bool g_booted = false;

/// Whether the host injects the rigid body each tick (SfuConfig::host_owns_body).
/// ホストが剛体を毎刻み注入するかどうか（SfuConfig::host_owns_body）。
bool g_host_owns_body = true;

/// The virtual clock the host has asked the firmware to reach so far.
/// これまでにホストがファームへ要求した仮想時刻。
int64_t g_now_us = 0;

/// The virtual time the plant was last advanced to, so on_advance can step it
/// by exactly the elapsed interval — the same bookkeeping emu_main.cpp does.
/// プラントを最後に進めた仮想時刻。on_advance が経過ぶんだけ正確に進められる
/// ようにするため。emu_main.cpp と同じ帳簿の付け方である。
int64_t g_last_plant_us = 0;

/// The stick values the host last stored. The bridge re-sends these on the
/// firmware's own cadence; the host never sends packets itself.
/// ホストが最後に置いたスティックの値。橋渡しがファーム自身の周期で送り直す。
/// ホストがパケットを直接送ることはない。
uint16_t g_rc_throttle = sils::kAdcCentre;
uint16_t g_rc_roll     = sils::kAdcCentre;
uint16_t g_rc_pitch    = sils::kAdcCentre;
uint16_t g_rc_yaw      = sils::kAdcCentre;
uint8_t  g_rc_flags    = 0;

/// Transmitter cadence: 50 Hz, as a real transmitter sends and as
/// `devices/rc_stdin.cpp` re-injects at. Injecting on VIRTUAL time rather than
/// per host tick is what keeps a long host tick from queueing up a burst: one
/// 100 ms tick still sends five packets, not one per tick and not fifty.
/// 送信機の周期: 50Hz。実際の送信機と同じで、`devices/rc_stdin.cpp` の再注入とも
/// 同じ。ホストの刻みごとではなく**仮想時間**で注入することが、長い刻みでパケットが
/// 束にならない理由である。100 ms の刻み 1 回でも送るのは 5 個であって、刻みあたり
/// 1 個でも 50 個でもない。
constexpr int64_t kRcPeriodUs = 20000;
int64_t g_next_rc_us = 0;

// -----------------------------------------------------------------------------
// Scheduler advance hook. Called by the scheduler every time the virtual clock
// moves, which is where the physics and the transmitter belong: both must see
// the firmware's own time base, not the host's tick boundaries.
// スケジューラの advance フック。仮想時計が動くたびに呼ばれる。物理と送信機は
// ここに属する。どちらもホストの刻みの切れ目ではなく、ファーム自身の時間基準を
// 見なければならないためである。
// -----------------------------------------------------------------------------
void on_advance(int64_t now_us)
{
    if (now_us > g_last_plant_us) {
        const float dt = (float)(now_us - g_last_plant_us) * 1e-6f;
        sils_board_step_plant(dt);
        g_last_plant_us = now_us;
    }

    // Send the held stick values at the transmitter's cadence. A catch-up loop
    // rather than a single send, so a host tick longer than 20 ms still delivers
    // every packet the firmware would have received.
    // 保持しているスティックの値を送信機の周期で送る。1 回だけ送るのではなく
    // 追いつくまで回すので、20 ms より長いホストの刻みでも、ファームが受け取る
    // はずだったパケットをすべて届けられる。
    while (now_us >= g_next_rc_us) {
        sils::inject_rc(g_rc_throttle, g_rc_roll, g_rc_pitch, g_rc_yaw, g_rc_flags);
        g_next_rc_us += kRcPeriodUs;
    }
}

// -----------------------------------------------------------------------------
// Turn the host's SfuConfig into the plant's own Config. Mirrors what
// emu_main.cpp's plant_config_from_env() built out of environment variables.
// ホストの SfuConfig をプラント自身の Config にする。emu_main.cpp の
// plant_config_from_env() が環境変数から組み立てていたものに対応する。
// -----------------------------------------------------------------------------
sils::Plant::Config plant_config_from(const SfuConfig& config)
{
    sils::Plant::Config cfg;   // defaults: the clean path / 既定はクリーン経路

    cfg.batt_model_enable = (config.battery_model != 0);

    // A knob at or below zero means "leave the default", so a caller that zeroes
    // the struct gets the documented defaults rather than a dead airframe.
    // 0 以下のノブは「既定のまま」を意味する。構造体を 0 で埋めた呼び出し側が、
    // 飛べない機体ではなく文書どおりの既定値を得るようにするためである。
    if (config.ground_effect_gain > 0.0f) cfg.ge_gain = config.ground_effect_gain;
    if (config.turbulence_n > 0.0f)       cfg.turbulence_n = config.turbulence_n;
    if (config.thrust_efficiency > 0.0f)  cfg.thrust_efficiency = config.thrust_efficiency;
    if (config.torque_authority > 0.0f)   cfg.torque_authority = config.torque_authority;
    if (config.motor_delay_ms > 0.0f)     cfg.motor_delay_ms = config.motor_delay_ms;

    // Noise levels, named as SILS_EMU_NOISE spells them: n0 static, n1 adds
    // throttle vibration, n2 adds band-limiting and ToF/baro observation noise.
    // ノイズの段。SILS_EMU_NOISE の呼び方に合わせる: n0 は静的、n1 はスロットル
    // 振動を足し、n2 は帯域制限と ToF／baro の観測ノイズを足す。
    constexpr int32_t kNoiseN0 = 1;
    constexpr int32_t kNoiseN1 = 2;
    constexpr int32_t kNoiseN2 = 3;
    if (config.noise_level >= kNoiseN0) {
        cfg.noise.enable        = true;
        cfg.noise.vib_enable    = (config.noise_level >= kNoiseN1);
        cfg.noise.vib_bandlimit = (config.noise_level >= kNoiseN2);
        cfg.noise.obs_enable    = (config.noise_level >= kNoiseN2);
        if (config.noise_seed != 0) cfg.noise.seed = config.noise_seed;
    }

    return cfg;
}

/// Read the host's Unity-frame rigid-body state into the plant's NED/FRD types.
/// The conversion lives entirely in frames_unity.hpp.
/// ホストの Unity 系の剛体の状態を、プラントの NED／FRD の型へ読み込む。変換は
/// すべて frames_unity.hpp の中にある。
sils::ExternalState external_state_from(const SfuStepIn& in)
{
    const unity::UnityQuat rotation{in.rotation[0], in.rotation[1],
                                    in.rotation[2], in.rotation[3]};
    const Quat q_nb = unity::qnb_from_unity(rotation);

    sils::ExternalState state;
    state.pos_ned = unity::position_from_unity(
        Vec3{in.position[0], in.position[1], in.position[2]});
    state.vel_ned = unity::velocity_from_unity(
        Vec3{in.velocity_world[0], in.velocity_world[1], in.velocity_world[2]});
    state.q_nb = q_nb;
    // Unity reports the angular velocity in the WORLD frame; the firmware's gyro
    // reads the BODY frame, so the attitude is needed to turn one into the other.
    // Unity は角速度を**世界**系で報告し、ファームのジャイロは**機体**系を読む。
    // 一方から他方へ直すのに姿勢が要る。
    state.omega_frd = unity::body_rates_from_unity_world(
        Vec3{in.angular_velocity_world[0], in.angular_velocity_world[1],
             in.angular_velocity_world[2]}, q_nb);
    state.accel_frd = unity::accel_from_unity(
        Vec3{in.accel_local[0], in.accel_local[1], in.accel_local[2]});
    return state;
}

/// Write a Vec3 into three consecutive floats. 3 つの連続した float へ書く。
void store_vec3(float* out, const Vec3& v)
{
    out[0] = v.x;
    out[1] = v.y;
    out[2] = v.z;
}

/// Write a quaternion into four consecutive floats in Unity's x,y,z,w order.
/// クォータニオンを Unity の x,y,z,w の順で 4 つの連続した float へ書く。
void store_unity_quat(float* out, const unity::UnityQuat& q)
{
    out[0] = q.x;
    out[1] = q.y;
    out[2] = q.z;
    out[3] = q.w;
}

/// Fill the firmware-state half of SfuStepOut from the published topics.
/// SfuStepOut のうちファームの状態にあたる部分を、publish されたトピックから埋める。
void fill_firmware_state(SfuStepOut& out)
{
    const sf::SystemMode mode = sf::system_mode.latest();
    out.armed        = mode.armed ? 1 : 0;
    out.flight_state = (int32_t)mode.state;
    out.flight_mode  = (int32_t)mode.sub_mode;

    const sf::StateEstimate est = sf::estimate_state.latest();
    const Quat q_est(est.attitude[0], est.attitude[1], est.attitude[2], est.attitude[3]);
    store_unity_quat(out.estimated_rotation, unity::qnb_to_unity(q_est));
    store_vec3(out.estimated_position,
               unity::position_to_unity(
                   Vec3{est.position[0], est.position[1], est.position[2]}));

    const sils::Plant::Truth truth = g_plant.truth();
    store_vec3(out.truth_position, unity::position_to_unity(truth.pos_ned));
    store_unity_quat(out.truth_rotation, unity::qnb_to_unity(truth.q_nb));
}

/// Look a parameter's type up in the firmware's own table (the SSOT), the way
/// emu_main.cpp's SILS_EMU_PARAMS_FILE handler does.
/// パラメータの型をファーム自身のテーブル（SSOT）で引く。emu_main.cpp の
/// SILS_EMU_PARAMS_FILE の処理と同じやり方である。
const sf::params::ParamEntry* find_param(const char* name)
{
    for (int i = 0; i < sf::params::count(); ++i) {
        const sf::params::ParamEntry* entry = sf::params::entry(i);
        if (entry != nullptr && std::strcmp(name, entry->name) == 0) return entry;
    }
    return nullptr;
}

}  // namespace

// =============================================================================
// The C ABI. / C ABI。
// =============================================================================

extern "C" {

int32_t sfu_abi_version(void) { return SFU_ABI_VERSION; }

int32_t sfu_struct_size(int32_t which)
{
    switch (which) {
        case SFU_STRUCT_CONFIG:     return (int32_t)sizeof(SfuConfig);
        case SFU_STRUCT_STEP_IN:    return (int32_t)sizeof(SfuStepIn);
        case SFU_STRUCT_STEP_OUT:   return (int32_t)sizeof(SfuStepOut);
        case SFU_STRUCT_PARAM_INFO: return (int32_t)sizeof(SfuParamInfo);
        default:                    return SFU_ERR_BAD_INDEX;
    }
}

int32_t sfu_boot(const SfuConfig* config)
{
    if (config == nullptr) return SFU_ERR_NULL_ARGUMENT;
    if (config->struct_size != sizeof(SfuConfig)) return SFU_ERR_STRUCT_SIZE;
    if (g_booted) return SFU_ERR_ALREADY_BOOTED;

    // The plant takes no model file: this build links plant_external.cpp, which
    // has no MuJoCo and takes its rigid body from the host.
    // プラントはモデルファイルを取らない。この構成がリンクするのは MuJoCo を持たず
    // 剛体をホストから受け取る plant_external.cpp である。
    if (!g_plant.init(nullptr, plant_config_from(*config))) return SFU_ERR_NOT_BOOTED;
    g_host_owns_body = (config->host_owns_body != 0);
    g_plant.setStartHeight(config->start_height_m);
    sils_board_attach_plant(&g_plant);

    sils::rtos::Scheduler::instance().set_on_advance(on_advance);

    // Boot the vehicle already paired to the transmitter the bridge injects
    // from, so the sticks are accepted without a pairing handshake — the same
    // pre-binding emu_main.cpp performs for its flight scenarios.
    // 機体を、橋渡しが注入する送信機とペア済みで起動させ、スティックがペアリングの
    // ハンドシェイク無しで受理されるようにする。emu_main.cpp が飛行シナリオのために
    // 行う事前の結び付けと同じである。
    sils::seed_pairing_nvs();

    // The real firmware startup, unmodified.
    // 実ファームの起動そのまま。
    app_main();

    // The boot calibration is read by the IMU task's setup, which has not run
    // yet — app_main only creates the tasks. This is therefore the window
    // emu_main.cpp uses for SILS_EMU_NO_CALIB: after app_main, before the first
    // time the scheduler runs.
    // 起動校正の設定は IMU タスクの setup が読むが、それはまだ走っていない。
    // app_main はタスクを作るだけだからである。よってここが emu_main.cpp が
    // SILS_EMU_NO_CALIB に使う窓 ― app_main の後、スケジューラが初めて回る前 ―
    // にあたる。
    sf::params::set_bool("calibration.enable", config->boot_calibration != 0);

    g_booted = true;
    return SFU_OK;
}

int32_t sfu_shutdown(void)
{
    if (!g_booted) return SFU_ERR_NOT_BOOTED;
    sils::rtos::Scheduler::instance().shutdown();
    return SFU_OK;
}

int32_t sfu_step(const SfuStepIn* in, SfuStepOut* out)
{
    if (in == nullptr || out == nullptr) return SFU_ERR_NULL_ARGUMENT;
    if (in->struct_size != sizeof(SfuStepIn)) return SFU_ERR_STRUCT_SIZE;
    if (out->struct_size != sizeof(SfuStepOut)) return SFU_ERR_STRUCT_SIZE;
    if (!g_booted) return SFU_ERR_NOT_BOOTED;

    // 1. Inject what the host has: the rigid body, the raycasts, the sticks.
    //    The rigid body and the raycasts go in only when the host owns them;
    //    with host_owns_body = 0 the plant's own integrator and its own
    //    downward-range synthesis stay in charge (sfu_bridge_smoke's path).
    // 1. ホストが持っている情報を注入する: 剛体・レイキャスト・スティック。
    //    剛体とレイキャストを入れるのはホストがそれらを持つときだけである。
    //    host_owns_body = 0 ではプラント自身の積分器と、自前で作る下向きの距離が
    //    引き続き担当する（sfu_bridge_smoke の経路）。
    if (g_host_owns_body) {
        g_plant.setExternalState(external_state_from(*in));

        sils::RangeInput range;
        range.downward_m      = in->range_down_m;
        range.downward_valid  = (in->range_down_valid != 0);
        range.ground_height_m = in->ground_height_m;
        g_plant.setRange(range);
    }

    g_rc_throttle = in->rc_throttle;
    g_rc_roll     = in->rc_roll;
    g_rc_pitch    = in->rc_pitch;
    g_rc_yaw      = in->rc_yaw;
    g_rc_flags    = in->rc_flags;

    // 2. Advance the firmware. on_advance steps the plant and feeds the sticks
    //    along the way, so by the time this returns the whole tick has happened.
    // 2. ファームを進める。途中で on_advance がプラントを進めスティックを送るので、
    //    ここから戻るときには 1 刻みぶんがすべて済んでいる。
    // Round, never truncate: (double)0.0025f is 0.00249999994..., so a cast
    // would advance the firmware clock by 2499 us per tick and let it lag the
    // host physics by 0.04 % forever.
    // 切り捨てではなく四捨五入する。(double)0.0025f は 0.00249999994… なので、
    // キャストだと 1 刻みが 2499 us になり、ファームの時計がホストの物理に対して
    // 0.04 % 遅れ続ける。
    g_now_us += (int64_t)std::llround((double)in->dt_s * 1e6);
    const bool advanced = sils::rtos::Scheduler::instance().run_until(g_now_us);
    if (!advanced) return SFU_ERR_SCHEDULER_STALL;

    // 3. Report what the rotors produced and the firmware's own position estimate.
    // 3. ロータが出したものと、ファームが推定した自機の位置を返す。
    const sils::Wrench wrench = g_plant.takeWrench();
    store_vec3(out->force_local,  unity::force_to_unity(wrench.force_frd));
    store_vec3(out->torque_local, unity::torque_to_unity(wrench.torque_frd));
    store_vec3(out->wind_force_world, unity::force_to_unity(wrench.wind_ned));
    out->wrench_dt_s = wrench.dt_s;

    float duty[SFU_MOTOR_COUNT] = {0.0f, 0.0f, 0.0f, 0.0f};
    sils_board_get_motor_duty(duty);
    for (int motor = 0; motor < SFU_MOTOR_COUNT; ++motor) {
        out->motor_duty[motor]  = duty[motor];
        out->motor_omega[motor] = g_plant.motorOmega(motor);
    }
    out->battery_voltage = g_plant.batteryVoltage();
    out->now_us = g_now_us;

    fill_firmware_state(*out);
    return SFU_OK;
}

int32_t sfu_param_count(void) { return sf::params::count(); }

int32_t sfu_param_info(int32_t index, SfuParamInfo* out)
{
    if (out == nullptr) return SFU_ERR_NULL_ARGUMENT;
    if (out->struct_size != sizeof(SfuParamInfo)) return SFU_ERR_STRUCT_SIZE;
    if (index < 0 || index >= sf::params::count()) return SFU_ERR_BAD_INDEX;

    const sf::params::ParamEntry* entry = sf::params::entry(index);
    if (entry == nullptr) return SFU_ERR_BAD_INDEX;

    std::snprintf(out->name, sizeof(out->name), "%s", entry->name);
    out->type          = (int32_t)entry->type;
    out->default_value = entry->default_val;
    out->min_value     = entry->min_val;
    out->max_value     = entry->max_val;
    return SFU_OK;
}

int32_t sfu_param_set(const char* name, double value)
{
    if (name == nullptr) return SFU_ERR_NULL_ARGUMENT;
    const sf::params::ParamEntry* entry = find_param(name);
    if (entry == nullptr) return SFU_ERR_UNKNOWN_PARAM;

    bool accepted = false;
    switch (entry->type) {
        case sf::params::ParamType::FLOAT:
            accepted = sf::params::set_float(name, (float)value);
            break;
        case sf::params::ParamType::BOOL:
            accepted = sf::params::set_bool(name, value != 0.0);
            break;
        case sf::params::ParamType::INT:
            accepted = sf::params::set_int(name, (int32_t)value);
            break;
    }
    return accepted ? SFU_OK : SFU_ERR_PARAM_REJECTED;
}

int32_t sfu_param_get(const char* name, double* out)
{
    if (name == nullptr || out == nullptr) return SFU_ERR_NULL_ARGUMENT;
    const sf::params::ParamEntry* entry = find_param(name);
    if (entry == nullptr) return SFU_ERR_UNKNOWN_PARAM;

    switch (entry->type) {
        case sf::params::ParamType::FLOAT: {
            float value = 0.0f;
            if (!sf::params::get_float(name, value)) return SFU_ERR_UNKNOWN_PARAM;
            *out = (double)value;
            return SFU_OK;
        }
        case sf::params::ParamType::BOOL: {
            bool value = false;
            if (!sf::params::get_bool(name, value)) return SFU_ERR_UNKNOWN_PARAM;
            *out = value ? 1.0 : 0.0;
            return SFU_OK;
        }
        case sf::params::ParamType::INT: {
            int32_t value = 0;
            if (!sf::params::get_int(name, value)) return SFU_ERR_UNKNOWN_PARAM;
            *out = (double)value;
            return SFU_OK;
        }
    }
    return SFU_ERR_UNKNOWN_PARAM;
}

int32_t sfu_set_wind(float world_x, float world_y, float world_z)
{
    if (!g_booted) return SFU_ERR_NOT_BOOTED;
    g_plant.setWind(unity::force_from_unity(Vec3{world_x, world_y, world_z}));
    return SFU_OK;
}

int32_t sfu_set_motor_health(int32_t motor, float gain)
{
    if (!g_booted) return SFU_ERR_NOT_BOOTED;
    if (motor < 0 || motor >= SFU_MOTOR_COUNT) return SFU_ERR_BAD_INDEX;
    g_plant.setHealth((int)motor, gain);
    return SFU_OK;
}

int32_t sfu_set_imu_bias(float accel_x, float accel_y, float accel_z,
                         float gyro_x, float gyro_y, float gyro_z)
{
    if (!g_booted) return SFU_ERR_NOT_BOOTED;
    // The accelerometer bias is a polar vector and the gyro bias an axial one,
    // the same as the readings they offset.
    // 加速度計のバイアスは極性ベクトル、ジャイロのバイアスは軸性ベクトルである。
    // それぞれがずらす測定値と同じ扱いになる。
    g_plant.setImuBias(unity::accel_from_unity(Vec3{accel_x, accel_y, accel_z}),
                       unity::angular_velocity_from_unity(Vec3{gyro_x, gyro_y, gyro_z}));
    return SFU_OK;
}

}  // extern "C"
