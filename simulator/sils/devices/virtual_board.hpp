/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 * Part of StampFly Ecosystem (SILS host bench — StampFly emulator).
 */

/**
 * @file virtual_board.hpp
 * @brief StampFly virtual board — the bus-level device models the ESP-IDF host
 *        shims dispatch to (SPI chips, LEDC motors), connected to the Plant.
 *        StampFly 仮想ボード — ESP-IDF host シムが分岐する先のバスレベル
 *        デバイスモデル（SPIチップ・LEDCモータ）。Plant に接続。
 *
 * The driver shims (driver/spi_master.h, driver/ledc.h) call these C-linkage
 * hooks instead of touching real hardware. The board routes an SPI transaction
 * by chip-select to the right device model (E1: BMI270 register-level from the
 * Plant IMU), and LEDC duty to the 4 motors → Plant. Firmware-agnostic: any
 * firmware driving these chips over ESP-IDF reaches the same models.
 *
 * ドライバシムがこれら C リンケージのフックを呼ぶ。CS でSPIトランザクションを
 * デバイスへ振り分け（E1: BMI270 をレジスタレベルで Plant IMU から）、LEDC duty を
 * 4モータ→Plant へ。ファーム非依存。
 *
 * @design simulator/sils/RESET_PLAN.md §5-6 — register-level chip emulation  [--]
 */

#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Attach the MuJoCo Plant (opaque sils::Plant*). Called once from emu_main.
// MuJoCo Plant（不透明 sils::Plant*）を接続。emu_main から1回。
void sils_board_attach_plant(void* plant);

// Advance physics one step: push the latched motor duties into the Plant, step.
// 物理を1ステップ進める: ラッチされたモータ duty を Plant に渡して step。
void sils_board_step_plant(float dt_s);

// SPI transaction hook (driver/spi_master.h → here). cs = chip-select GPIO.
// tx/rx are the full-duplex buffers, nbytes the transfer length in bytes.
// SPI トランザクションフック。cs = CS GPIO、tx/rx は全二重バッファ。
int sils_board_spi_transfer(int cs, const uint8_t* tx, uint8_t* rx, size_t nbytes);

// I2C transaction hook (driver/i2c_master.h → here). addr = 7/10-bit device
// address (decoded from the opaque handle by the shim). A transfer has an
// optional write phase (write_buf/write_size) followed by an optional read phase
// (read_buf/read_size); write-then-read in one call models a repeated-START
// register read. Returns 0 on ACK. Unmodeled addresses zero-fill the read buffer
// and still return 0, matching the old inert shim (Optional sensors fail
// gracefully on their chip-ID check until their device models land).
// I2C トランザクションフック。addr = デバイスアドレス（シムが不透明ハンドルから復号）。
// 書き込み相＋読み出し相（同一呼び出しで repeated-START のレジスタ読みを表現）。
// 0 = ACK。未モデルアドレスは read をゼロ埋めし 0 を返す（旧 inert シムと同一）。
int sils_board_i2c_xfer(uint16_t addr,
                       const uint8_t* write_buf, size_t write_size,
                       uint8_t* read_buf, size_t read_size);

// LEDC duty hook (driver/ledc.h → here). channel 0..3 = motors M1..M4, duty is
// the raw N-bit LEDC value; max_duty is the resolution full-scale (e.g. 255).
// LEDC duty フック。channel 0..3 = モータ M1..M4、duty は生 LEDC 値。
void sils_board_ledc_set_duty(int channel, uint32_t duty, uint32_t max_duty);

// Read the latched per-motor duties [0,1] (M1..M4) the board last received over
// LEDC. Used by the review-video trajectory recorder; does not touch the Plant.
// ボードが LEDC で最後に受け取った各モータ duty[0,1]（M1..M4）を読む。動画軌跡記録用。
void sils_board_get_motor_duty(float out[4]);

// P7 disturbance hooks (scenario wind/fault events → Plant). Set the external wind
// force in NED [N], or degrade one motor's thrust health gain (motor 0..3, 1=healthy,
// 0=dead). No-op until a Plant is attached. RESET_PLAN §13 P7.
// P7 外乱フック（シナリオの wind/fault → Plant）。NED 風力 [N] の設定、または1モータの
// 推力健全度ゲインの劣化（motor 0..3, 1=正常/0=停止）。Plant 未接続なら no-op。
void sils_board_set_wind(float fx, float fy, float fz);
void sils_board_set_motor_health(int motor, float gain);

// GPIO output hook (driver/gpio.h → here). Only pins wired to a device model do
// anything; every other pin is a no-op. Today the one wired pin is the front
// ToF's XSHUT, whose level decides whether that part answers on I2C at all
// (jev-autopilot P4b).
// GPIO 出力フック。デバイスモデルにつながったピンだけが作用し、他は無動作。現在
// つながっているのは前方 ToF の XSHUT で、そのレベルがその部品を I2C に応答させるか
// を決める（P4b）。
void sils_board_gpio_set_level(int gpio_num, int level);

// Place a vertical wall (NED metres) for the forward ToF to see. A scene builds
// its obstacles this way before the flight starts; the default world has none,
// so the 33 regression scenarios are unaffected. See Plant::addWall.
// 前方 ToF が見る垂直な壁を置く（NED メートル）。場面は飛行開始前にこうして障害物を
// 組み立てる。既定の世界には 1 枚も無いので回帰 33 本は影響を受けない。
void sils_board_add_wall(float n0_m, float e0_m, float n1_m, float e1_m);

// Override the battery terminal voltage [V] the INA3221 shim reports, or pass a
// non-positive value to return to the Plant's own discharge model. This is a
// SILS-only bench seam for rehearsing a low-battery decision: the Plant's 300mAh
// pack barely sags over a one-minute run, so `sf pilot run --scene battery_drop`
// walks this value down instead of waiting for a real discharge. The firmware is
// untouched — it reads the same INA3221 registers it always does, so power_task,
// the failsafe thresholds and the thrust→duty compensation all see one voltage.
// INA3221 シムが報告する電池端子電圧 [V] を上書きする。0 以下を渡すと Plant 自身の
// 放電モデルに戻る。電池低下の判断を試すための SILS 専用の継ぎ目である: Plant の
// 300mAh パックは 1 分程度の実行ではほとんど低下しないため、`sf pilot run
// --scene battery_drop` は実際の放電を待たずにこの値を下げていく。ファームは
// 無改変 — いつもどおり同じ INA3221 レジスタを読むので、power_task・フェイルセーフ
// のしきい値・thrust→duty 補償はすべて同じ電圧を見る。
void sils_board_set_battery_voltage(float volts);

// Set a deterministic raw IMU bias (body FRD): accel [m/s²], gyro [rad/s]. Models a
// pre-calibration MEMS offset so the firmware boot calibration has something to remove
// (P2-3 contrast test). No-op until a Plant is attached.
// 決定論的な生 IMU バイアス（機体 FRD）を設定: accel[m/s²], gyro[rad/s]。校正前 MEMS
// オフセットを模擬し、ファーム起動校正に除去対象を与える（P2-3 対照試験）。Plant 未接続なら no-op。
void sils_board_set_imu_bias(float ax, float ay, float az, float gx, float gy, float gz);

// P8 handling hook (scenario `handle` event → Plant). Begin a physical handling maneuver:
// the hand lifts the (crashed) craft to carry_alt [m up], rights it to level, carries it to
// place_x/place_y [NED m] and sets it back on the ground, over lift/carry/place seconds.
// The Plant prescribes a continuous kinematic trajectory and synthesizes the IMU/ToF/gyro
// (no teleport). No-op until a Plant is attached. RESET_PLAN §13 P8.
// P8 ハンドリングフック（シナリオ `handle` → Plant）。物理ハンドリング動作を開始: 手が（墜落
// した）機体を carry_alt[上方m]へ持上げ、水平へ起こし、place_x/place_y[NED m]へ運び地面に戻す
// （lift/carry/place 秒）。Plant が連続キネマティック軌道を規定し IMU/ToF/gyro を合成（teleport
// なし）。Plant 未接続なら no-op。
void sils_board_handle_place(float carry_alt_m, float place_x_ned, float place_y_ned,
                            float lift_s, float carry_s, float place_s);

#ifdef __cplusplus
}  // extern "C"
#endif
