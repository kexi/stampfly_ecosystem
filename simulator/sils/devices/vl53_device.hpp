/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 * Part of StampFly Ecosystem (SILS host bench — StampFly emulator).
 */

/**
 * @file vl53_device.hpp
 * @brief VL53L3CX ToF chip model (I2C-byte level) — the unmodified ST BareDriver
 *        runs on the host against this model. Extracted from virtual_board.cpp so
 *        both the emulator (virtual_board) and the offline gen4 probe
 *        (smoke/vl53_probe.cpp) link the SAME model.
 *        VL53L3CX ToF チップモデル（I2Cバイトレベル）。無改変 ST ベアドライバが
 *        ホストでこのモデルに対し動く。エミュレータとオフライン gen4 プローブが
 *        同一モデルをリンクできるよう virtual_board.cpp から分離。
 *
 * @design simulator/sils/RESET_PLAN.md §E2 — I2C device model (VL53L3CX)  [--]
 */

#pragma once

#include <cstddef>
#include <cstdint>

namespace sils_vl53 {

// Power-on / re-addressed I2C addresses (the model is stateless w.r.t. address;
// the board routes both to xfer()). front 0x31 is left to the board catch-all.
// 起動時 / 再アドレス後の I2C アドレス（モデルはアドレス非依存; 両方を xfer へ）。
constexpr uint16_t ADDR_DEFAULT = 0x29;   // power-on I2C address
constexpr uint16_t ADDR_BOTTOM  = 0x30;   // re-addressed bottom altimeter

// Which physical part a transaction belongs to. Each part owns its own chip
// state, because the gen4 decode is STATEFUL: the driver interleaves two VCSEL
// configs and checks that consecutive frames' phases agree, so the per-frame
// counters must advance once per frame OF THAT PART. Sharing one counter between
// two sensors halves each part's frame sequence, breaks the A/B parity and the
// phase-consistency check, and the driver reports "no target" forever -- with
// every individual value looking correct along the way.
//
// トランザクションがどの部品のものか。部品ごとに固有のチップ状態を持つ。gen4 の
// 復号は「状態を持つ」からである: ドライバは 2 つの VCSEL 設定を交互に使い、連続
// フレームの位相が整合するかを確認する。したがってフレーム計数器は「その部品の」
// フレームごとに 1 回進まねばならない。1 つの計数器を 2 センサで共有すると各部品の
// フレーム列が半分になり、A/B のパリティと位相整合の確認が破れ、ドライバは永久に
// 「対象なし」を報告する ―― 途中のどの値も正しく見えたままで。
enum class Part { Bottom, Front };

// Push the current target distance [mm] that the synthesized histogram should
// encode for `part` (the board supplies the Plant range each transaction). The
// env override SILS_VL53_TEST_MM, when set, always wins over this pushed value.
// `part` の合成 histogram が符号化すべき目標距離[mm]を渡す（ボードが毎取引で
// Plant の距離を供給）。環境変数 SILS_VL53_TEST_MM があれば常にそちらが優先。
void set_distance_mm(float mm, Part part = Part::Bottom);

// One VL53 I2C transaction for `part`. 16-bit BE register pointer in wbuf[0..1]
// (write-then-read = repeated-START). Returns 0 (ACK). Mirrors the real chip's
// register map only where the ST driver reads it; everything else is zero/ACK
// self-heal.
// `part` の VL53 の1 I2Cトランザクション。16bit BE レジスタポインタは wbuf[0..1]。
int xfer(const uint8_t* wbuf, size_t wlen, uint8_t* rbuf, size_t rlen,
         Part part = Part::Bottom);

}  // namespace sils_vl53
