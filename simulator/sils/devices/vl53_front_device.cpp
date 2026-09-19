/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 * Part of StampFly Ecosystem (SILS host bench — StampFly emulator).
 */

/**
 * @file vl53_front_device.cpp
 * @brief Forward VL53L3CX model — see vl53_front_device.hpp for why it is separate
 *        from the bottom part's model.
 *        前方 VL53L3CX モデル。底面と別モデルにする理由はヘッダを参照。
 *
 * The chip protocol is identical to the bottom part's (same silicon, same
 * unmodified ST BareDriver on the host), so the histogram synthesis is NOT
 * duplicated here: this file owns only what actually differs — the XSHUT reset
 * gate, the address the part answers on, the model-id probe, and an "empty room"
 * reading that the bottom model has no concept of. Everything else delegates to
 * sils_vl53, whose histogram encoding was verified against the real gen4
 * pipeline by smoke/vl53_probe.cpp.
 *
 * チップの規約は底面と同一（同じシリコン、ホスト上で動く同じ無改変 ST ベアドライバ）
 * なので、histogram 合成をここに複製しない。本ファイルが持つのは実際に異なるものだけ
 * である ―― XSHUT のリセット関門、部品が応答するアドレス、model id の確認、そして
 * 底面モデルには概念すら無い「空の部屋」の読み値。残りは sils_vl53 に委譲する。その
 * histogram 符号化は smoke/vl53_probe.cpp で実 gen4 パイプラインに対し検証済みである。
 *
 * SHARED CODE, SEPARATE STATE: this model delegates to sils_vl53 for the chip
 * protocol but passes Part::Front, so the two sensors keep INDEPENDENT frame and
 * stream counters. That separation is required, not tidy: the gen4 decode is
 * stateful across frames (interleaved VCSEL configs plus a phase-consistency
 * check between consecutive frames), so two parts sharing one counter would each
 * advance it on the other's transactions. Measured: with a shared counter the
 * forward reading never became valid — the driver reported "no target" forever
 * while every individual value along the path looked correct.
 *
 * コードは共有・状態は別: 本モデルはチップ規約を sils_vl53 に委譲するが Part::Front
 * を渡すので、2 センサはフレーム/ストリーム計数器を「独立に」持つ。この分離は整頓で
 * はなく必須である: gen4 の復号はフレームをまたいで状態を持ち（VCSEL 設定の交互使用と、
 * 連続フレーム間の位相整合の確認）、計数器を共有すると各部品が他方のトランザクションで
 * それを進めてしまう。実測: 共有したままでは前方の読み値は一度も有効にならず、経路上の
 * どの値も正しく見えたまま、ドライバは永久に「対象なし」を報告した。
 *
 * @design docs/plans/jev-autopilot.md §4.9 — SILS forward distance  [--]
 */

#include "vl53_front_device.hpp"

#include <cstdlib>
#include <cstring>

#include "vl53_device.hpp"   // sils_vl53::xfer / set_distance_mm (shared chip protocol)

namespace sils_vl53_front {
namespace {

// IDENTIFICATION__MODEL_ID and the value a real VL53L3CX reports. The firmware's
// cheap presence check (VL53L3CXWrapper::isPresentAt) reads exactly this one
// register, and 0xEA is what distinguishes a real part from a bus that ACKs by
// default. 0xEB/0xEC would be a VL53L4CX — the real hardware corrected that
// expectation once already (jev-autopilot §4.7), so the value is named here.
// IDENTIFICATION__MODEL_ID と、実 VL53L3CX が返す値。ファームの安価な在否確認が
// 読むのは正確にこの 1 レジスタで、既定で ACK を返すバスと本物を分けるのが 0xEA で
// ある（0xEB/0xEC は VL53L4CX。実機が一度この期待値を訂正している ―― §4.7）。
constexpr uint16_t REG_MODEL_ID = 0x010F;
constexpr uint8_t  MODEL_ID     = 0xEA;

struct State {
    bool     xshut_high   = false;   ///< XSHUT level; low = in reset, answers nothing
    uint16_t addr         = ADDR_DEFAULT;  ///< address the part currently answers on
    uint16_t ptr          = 0;       ///< 16-bit register pointer (write-then-read)
    float    distance_m   = 0.0f;    ///< pushed forward distance
    bool     target_valid = false;   ///< whether anything is in range at all
};
State g_st;

}  // namespace

bool enabled()
{
    // Cached once: the emulator must not change device topology mid-run, and a
    // per-transaction getenv would cost on the shared I2C path.
    // 一度だけ確定させる: エミュレータは実行中にデバイス構成を変えてはならず、
    // 毎トランザクションの getenv は共有 I2C 経路の費用になる。
    static bool checked = false;
    static bool on = false;
    if (!checked) {
        checked = true;
        const char* e = std::getenv("SILS_EMU_FRONT_TOF");
        on = (e != nullptr && e[0] == '1');
    }
    return on;
}

void set_xshut(bool awake)
{
    if (!enabled()) return;
    // A falling edge returns the part to its power-on address, exactly as cutting
    // power to the real one does. TofTask relies on this when it gives up on the
    // front sensor and drops XSHUT: the part must not keep 0x31.
    // 立ち下がりで部品は起動時アドレスへ戻る（実機の電源を切ったときと同じ）。
    // TofTask が前方を諦めて XSHUT を落とすときこれに依存する: 0x31 を保持しては
    // ならない。
    const bool is_falling_edge = g_st.xshut_high && !awake;
    if (is_falling_edge) g_st.addr = ADDR_DEFAULT;
    g_st.xshut_high = awake;
}

bool answers_at(uint16_t addr)
{
    if (!enabled()) return false;
    // In reset the part is electrically absent: it answers at NO address. This
    // is the property that lets the bottom sensor move to 0x30 unambiguously.
    // リセット中、部品は電気的に不在である: どのアドレスにも応答しない。これが
    // 底面を曖昧さなく 0x30 へ移せるようにしている性質である。
    if (!g_st.xshut_high) return false;
    return addr == g_st.addr;
}

void set_distance(float metres, bool valid)
{
    g_st.distance_m   = metres;
    g_st.target_valid = valid;
}

int xfer(const uint8_t* wbuf, size_t wlen, uint8_t* rbuf, size_t rlen)
{
    if (wbuf != nullptr && wlen >= 2) {
        g_st.ptr = (uint16_t)((wbuf[0] << 8) | wbuf[1]);
    }

    // The driver re-addresses by writing the new address to I2C_SLAVE__DEVICE_ADDRESS.
    // Honour it so the part answers on 0x31 from the next transaction onward, which
    // is what TofTask's "front ready at 0x31" log line reports.
    // ドライバは I2C_SLAVE__DEVICE_ADDRESS へ新アドレスを書いて振り直す。これに従い、
    // 次のトランザクションから 0x31 で応答する（TofTask の「front ready at 0x31」が
    // 報告するのはこれである）。
    // EXACTLY three bytes: a 16-bit register pointer plus the one address byte.
    // The length test is what makes this an address write rather than a guess.
    // The driver also writes a 137-byte configuration block that STARTS at this
    // same register, and its third byte is ordinary configuration data -- taking
    // that for an address moved the part to 0x00, where it answered nothing and
    // the forward reading stayed "no target" forever. A register pointer alone
    // does not identify a write; the shape of the write does.
    //
    // 「ちょうど 3 バイト」: 16bit レジスタポインタ＋アドレス 1 バイト。この長さの
    // 判定こそが、これを推測ではなくアドレス書き込みたらしめる。ドライバは同じ
    // レジスタから「始まる」137 バイトの設定ブロックも書き、その 3 バイト目はただの
    // 設定データである。それをアドレスと取り違えると部品は 0x00 へ移り、どこにも
    // 応答せず、前方の読み値は永久に「対象なし」のままになった。書き込みを識別する
    // のはレジスタポインタではなく、書き込みの「形」である。
    constexpr uint16_t REG_SET_ADDRESS = 0x0001;
    constexpr size_t   kAddressWriteLen = 3;   // 2-byte pointer + 1 address byte
    const bool is_write = (rbuf == nullptr);
    const bool is_address_write =
        is_write && g_st.ptr == REG_SET_ADDRESS && wlen == kAddressWriteLen;
    if (is_address_write) {
        g_st.addr = (uint16_t)(wbuf[2] & 0x7F);
        return 0;
    }

    // The model-id probe is answered here rather than by the shared model: the
    // bottom part's model self-heals unknown registers to zero, and zero is not
    // a VL53L3CX. Answering it is what makes the firmware's presence check pass.
    // model id の確認はここで答える（共有モデルではない）: 底面のモデルは未知の
    // レジスタを 0 に自己修復するが、0 は VL53L3CX ではない。ここで答えることが
    // ファームの在否確認を通す。
    const bool is_model_id_read = (rbuf != nullptr && g_st.ptr == REG_MODEL_ID && rlen >= 1);
    if (is_model_id_read) {
        std::memset(rbuf, 0, rlen);
        rbuf[0] = MODEL_ID;
        return 0;
    }

    // Everything else is the ordinary chip protocol. Push this part's own
    // distance first so the histogram the shared model synthesizes encodes the
    // FORWARD range, then delegate. An empty room is pushed as a distance beyond
    // the usable histogram range, which the shared model already renders as an
    // ambient-only histogram -> the driver reports "no target", the same answer
    // the real part gives (§4.8.6: status=255, value 0).
    //
    // 残りは通常のチップ規約である。共有モデルが合成する histogram が「前方」距離を
    // 符号化するよう、まず自分の距離を渡してから委譲する。空の部屋は使用可能な
    // histogram レンジより遠い距離として渡す。共有モデルはそれを ambient のみの
    // histogram として描き、ドライバは「対象なし」を報告する ―― 実機と同じ答えで
    // ある（§4.8.6: status=255・値 0）。
    constexpr float kNoTargetMm = 100000.0f;   // far beyond MAX_MM -> no return pulse
    sils_vl53::set_distance_mm(g_st.target_valid ? g_st.distance_m * 1000.0f
                                                 : kNoTargetMm,
                               sils_vl53::Part::Front);
    return sils_vl53::xfer(wbuf, wlen, rbuf, rlen, sils_vl53::Part::Front);
}

}  // namespace sils_vl53_front
