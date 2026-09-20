/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file sfu_log_ring.cpp
 * @brief The ring buffer behind bridge/shim/esp_log.h, sfu_log_read_record and
 *        sfu_log_read.
 *        bridge/shim/esp_log.h と sfu_log_read_record・sfu_log_read の裏にある
 *        リングバッファ。
 *
 * The firmware logs from inside its own tasks, at whatever moment it likes,
 * while the host reads once per tick. A fixed-size ring decouples the two: the
 * firmware never blocks and never allocates, and the host gets whatever
 * accumulated since its last read. When the host reads too rarely, the OLDEST
 * records are dropped — the newest are the ones a pilot or a developer wants —
 * and the count of what was dropped is kept so nothing disappears silently.
 *
 * ファームは自身のタスクの中から任意の時点で書き、ホストは 1 刻みに 1 回読む。
 * 大きさの決まったリングが両者を切り離す。ファームは止まらず確保もせず、ホストは
 * 前回の読み出し以降に溜まったものを受け取る。ホストの読み出しが遅れたときは
 * **古い**記録から捨てる ― 操縦者や開発者が見たいのは新しい方である ― が、捨てた数を
 * 覚えておくので、黙って消えることはない。
 *
 * ## Why a record and not a line / なぜ行ではなく記録なのか
 *
 * What `ESP_LOGx` actually has is four separate things: a level, a tag, a
 * format plus its arguments, and (from the virtual clock) a moment. Pasting
 * them into one string here would force the host to parse them back apart to
 * write a structured log, and a `%s` argument containing "] " would make that
 * parse wrong. So the ring stores the four SEPARATELY and applies the format
 * only to the body. `sfu_log_read` still composes the old one-line text for a
 * caller that only wants to print something, but it is built FROM the record,
 * never parsed back into one.
 *
 * `ESP_LOGx` が実際に持っているのは 4 つの別々のもの ― 段・タグ・書式とその引数・
 * （仮想時計からの）時点 ― である。ここで 1 本の文字列に貼ってしまうと、ホストは
 * 構造化ログを書くためにそれを解析し直すほかなく、しかも `%s` の引数に "] " が
 * 入っていれば解析は誤る。よってリングは 4 つを**別々に**持ち、書式は本文にだけ
 * 適用する。`sfu_log_read` は、ただ何か印字したい呼び出し側のために従来どおり
 * 1 行の文字列を組み立てるが、それは記録**から**作るのであって、記録へ解析し戻す
 * ことはしない。
 *
 * @design docs/plans/unity-simulator.md §4 設計上の決定（ログ）
 *         AGENTS.md「新しく書くコードのログの決まり」
 */

#include "sfu_api.h"

#include <cstdarg>
#include <cstdio>
#include <cstring>

#include "esp_timer.h"

namespace {

/// How many records the ring holds. The log is busiest at the firmware's boot —
/// roughly 200 lines over the first second — so this covers a host that reads
/// only a few times per second during start-up.
/// リングが保持する記録の数。ログが最も多いのはファームの起動時で、最初の 1 秒に
/// およそ 200 行が出る。起動中に毎秒数回しか読まないホストでも賄える数にしてある。
constexpr int kRecordCount = 512;

SfuLogRecord g_records[kRecordCount];

/// Index of the oldest record held, and how many are held.
/// 保持している最も古い記録の添字と、保持している数。
int g_head = 0;
int g_size = 0;

/// Records dropped because the ring was full, counted from boot.
/// リングが満杯で捨てた記録の数（起動からの累計）。
int g_dropped = 0;

/// The lowest level kept. Records above it are discarded where they are written.
/// Default: INFO, so ESP_LOGD / ESP_LOGV cost nothing — the same levels the
/// SILS host build silences.
/// 残す最も低い段。これより上の段は書かれた場所で捨てる。既定は INFO で、
/// ESP_LOGD／ESP_LOGV は何も費やさない ― SILS のホスト版が黙らせる段と同じ。
int g_level = SFU_LOG_INFO;

/// The record level each ESP_LOGx macro passes, as a string, mapped to its
/// number. Kept here rather than in the macro so the shim header stays a header.
/// 各 ESP_LOGx マクロが渡す段の文字列を数値へ対応させる。マクロ側ではなくここに
/// 置くのは、シムのヘッダをヘッダのままにしておくためである。
const char* level_name(int level)
{
    switch (level) {
        case SFU_LOG_ERROR:   return "ERR ";
        case SFU_LOG_WARN:    return "WARN";
        case SFU_LOG_INFO:    return "INFO";
        case SFU_LOG_DEBUG:   return "DEBUG";
        default:              return "VERB";
    }
}

}  // namespace

extern "C" {

// -----------------------------------------------------------------------------
// The ESP_LOGx destination. Stores the four parts separately; only the body has
// the format applied to it.
// ESP_LOGx の行き先。4 つの部分を別々に持ち、書式を当てるのは本文だけである。
// -----------------------------------------------------------------------------
void sfu_log_ring_write(int32_t level, const char* tag, const char* format, ...)
{
    // Levels above the threshold never reach the ring, so a firmware built with
    // ESP_LOGD calls in its hot path costs only this comparison.
    // 閾値より上の段はリングへ届かない。よって主経路に ESP_LOGD を持つファームでも
    // 費やすのはこの比較だけである。
    if (level > g_level) return;

    // Drop the oldest record when the ring is full, so the newest always fits.
    // リングが満杯なら最も古い記録を捨て、最も新しい記録が必ず入るようにする。
    const bool is_full = (g_size == kRecordCount);
    if (is_full) {
        g_head = (g_head + 1) % kRecordCount;
        --g_size;
        ++g_dropped;
    }

    const int slot = (g_head + g_size) % kRecordCount;
    SfuLogRecord& record = g_records[slot];

    record.struct_size = (uint32_t)sizeof(SfuLogRecord);
    record.level       = level;
    // The virtual clock, not the wall clock: two runs of the same flight must
    // produce the same records, and the wall clock never repeats.
    // 壁時計ではなく仮想時計を使う。同じ飛行を 2 回行えば同じ記録が出なければ
    // ならず、壁時計が繰り返されることはないからである。
    record.sim_us      = (int64_t)esp_timer_get_time();

    std::snprintf(record.tag, sizeof(record.tag), "%s", (tag != nullptr) ? tag : "");

    va_list args;
    va_start(args, format);
    std::vsnprintf(record.message, sizeof(record.message), format, args);
    va_end(args);

    ++g_size;
}

int32_t sfu_set_log_level(int32_t level)
{
    const int32_t previous = (int32_t)g_level;
    if (level < SFU_LOG_NONE)    level = SFU_LOG_NONE;
    if (level > SFU_LOG_VERBOSE) level = SFU_LOG_VERBOSE;
    g_level = (int)level;
    return previous;
}

// -----------------------------------------------------------------------------
// Take the oldest record out. One call is one record, so a caller never has to
// guess a buffer size and a long message can never stall the queue.
// 最も古い記録を 1 つ取り出す。1 回の呼び出しが 1 記録なので、呼び出し側が領域の
// 大きさを見積もる必要は無く、長い本文が待ち行列を詰まらせることもない。
// -----------------------------------------------------------------------------
int32_t sfu_log_read_record(SfuLogRecord* out)
{
    if (out == nullptr) return SFU_ERR_NULL_ARGUMENT;
    if (out->struct_size != sizeof(SfuLogRecord)) return SFU_ERR_STRUCT_SIZE;
    if (g_size == 0) return 0;

    const SfuLogRecord& record = g_records[g_head];
    out->level   = record.level;
    out->sim_us  = record.sim_us;
    std::memcpy(out->tag, record.tag, sizeof(out->tag));
    std::memcpy(out->message, record.message, sizeof(out->message));

    g_head = (g_head + 1) % kRecordCount;
    --g_size;
    return 1;
}

// -----------------------------------------------------------------------------
// Compose the held records into newline-separated text and forget them. Kept
// for a caller that only wants to print; a caller that wants the parts uses
// sfu_log_read_record.
// 保持している記録を改行区切りの文字列に組み立て、リングから消す。ただ印字したい
// 呼び出し側のために残してある。部分が欲しい呼び出し側は sfu_log_read_record を使う。
// -----------------------------------------------------------------------------
int32_t sfu_log_read(char* buffer, int32_t capacity)
{
    if (buffer == nullptr || capacity <= 0) return 0;

    int written = 0;
    while (g_size > 0) {
        const SfuLogRecord& record = g_records[g_head];

        // Write straight into the remaining capacity and let snprintf truncate.
        // The head record must ALWAYS leave, whatever its length: leaving it
        // for "next time" when it cannot fit in an empty buffer would stall the
        // queue for good, since next time it would not fit either.
        // 残り容量へ直接書き、収まらなければ snprintf に切り詰めさせる。先頭の
        // 記録は長さによらず**必ず**出て行かなければならない。空の領域にすら
        // 収まらないものを「次回へ」と残すと、次回も収まらないので待ち行列は
        // 永久に詰まる。
        const int remaining = capacity - written;
        constexpr int kNewlineAndTerminator = 2;
        if (remaining < kNewlineAndTerminator) break;

        const int would_write = std::snprintf(buffer + written, (size_t)(remaining - 1),
                                              "[%s] %s: %s",
                                              level_name(record.level),
                                              record.tag, record.message);
        // snprintf reports what it WOULD have written; clamp to what it did.
        // snprintf は「書こうとした長さ」を返す。実際に書いた長さへ丸める。
        const int body = (would_write < 0) ? 0
                       : (would_write > remaining - 2 ? remaining - 2 : would_write);
        written += body;
        buffer[written++] = '\n';

        g_head = (g_head + 1) % kRecordCount;
        --g_size;

        // A record that had to be truncated filled the buffer, so stop rather
        // than spin on a capacity that can hold nothing more.
        // 切り詰めた記録は領域を埋め切っている。これ以上何も入らない容量で回り
        // 続けるのをやめる。
        if (would_write > body) break;
    }

    buffer[written] = '\0';
    return written;
}

int32_t sfu_log_dropped(void) { return g_dropped; }

}  // extern "C"
