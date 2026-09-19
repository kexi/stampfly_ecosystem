/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 * Part of StampFly Ecosystem (SILS host bench — StampFly emulator).
 */

/**
 * @file vl53_front_device.hpp
 * @brief Forward-facing VL53L3CX model (jev-autopilot P4b) — the second ToF part,
 *        held in reset by XSHUT until TofTask wakes it, then re-addressed to 0x31.
 *        前方 VL53L3CX モデル（jev-autopilot P4b）。TofTask が起こすまで XSHUT で
 *        リセット保持され、その後 0x31 へ振り直される 2 個目の ToF。
 *
 * WHY A SEPARATE MODEL FROM sils_vl53 (the bottom one): the two parts differ in
 * exactly the way this feature is about — the bottom sensor is always awake and
 * always answers, while the front one must NOT answer while XSHUT is low. That
 * asymmetry is the whole safety property `TofTask` relies on (both parts boot at
 * 0x29, so a front part answering too early would alias the bottom sensor's
 * re-addressing). Keeping one stateless model and bolting a reset flag onto it
 * would put that asymmetry inside the shared code the 33 regression scenarios
 * run through.
 *
 * 底面（sils_vl53）と別モデルにする理由: 2 つの部品は、まさにこの機能が扱う点で
 * 異なる ―― 底面は常時起きていて常に応答するのに対し、前方は XSHUT が LOW の間は
 * 応答して「はならない」。この非対称性こそ `TofTask` が拠って立つ安全性である
 * （2 個とも 0x29 で起動するため、前方が早く応答すると底面のアドレス振り直しと
 * 混線する）。1 つの状態なしモデルにリセットの旗を付け足すと、その非対称性を
 * 回帰 33 本が通る共有コードの中に置くことになる。
 *
 * DEFAULT OFF: enabled only by SILS_EMU_FRONT_TOF=1. With it unset the part is
 * absent from the bus, the firmware's cheap presence check says "not detected",
 * and the run is byte-identical to one built before this file existed.
 * 既定は無効: SILS_EMU_FRONT_TOF=1 のときだけ有効。未設定ならバス上に部品は存在せず、
 * ファームの安価な在否確認は「not detected」と言い、実行は本ファイル以前と完全に一致する。
 *
 * @design docs/plans/jev-autopilot.md §4.9 — SILS forward distance  [--]
 */

#pragma once

#include <cstddef>
#include <cstdint>

namespace sils_vl53_front {

// Power-on address (shared with the bottom part) and the address TofTask moves
// this part to. 起動時アドレス（底面と共通）と、TofTask が移す先のアドレス。
constexpr uint16_t ADDR_DEFAULT = 0x29;
constexpr uint16_t ADDR_FRONT   = 0x31;

/// True when SILS_EMU_FRONT_TOF selects the front part (cached on first call).
/// Everything else in this header is a no-op while this is false.
/// SILS_EMU_FRONT_TOF が前方の部品を選んでいれば true（初回呼び出しで確定）。
/// false の間、本ヘッダの他は全て no-op である。
bool enabled();

/// Drive the XSHUT pin. While low the part is in reset and answers nothing.
/// XSHUT を駆動する。LOW の間、部品はリセット状態で何も応答しない。
void set_xshut(bool awake);

/// True when this part currently owns `addr` on the bus — false while it is in
/// reset, which is what lets the bottom sensor re-address safely.
/// この部品が今そのアドレスをバス上で持っていれば true。リセット中は false であり、
/// それが底面の安全なアドレス振り直しを可能にする。
bool answers_at(uint16_t addr);

/// Push the forward distance [m] the synthesized reading should encode, and
/// whether a target is there at all (an empty room is not a distance).
/// 合成する読み値が符号化すべき前方距離 [m] と、そもそも対象が居るかを渡す。
void set_distance(float metres, bool valid);

/// One I2C transaction addressed to this part. Same 16-bit big-endian register
/// pointer convention as the bottom model.
/// この部品宛の I2C トランザクション 1 回。レジスタポインタの規約は底面と同じ。
int xfer(const uint8_t* wbuf, size_t wlen, uint8_t* rbuf, size_t rlen);

}  // namespace sils_vl53_front
