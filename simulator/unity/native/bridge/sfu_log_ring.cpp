/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file sfu_log_ring.cpp
 * @brief The ring buffer behind bridge/shim/esp_log.h and sfu_log_read.
 *        bridge/shim/esp_log.h と sfu_log_read の裏にあるリングバッファ。
 *
 * The firmware logs from inside its own tasks, at whatever moment it likes,
 * while the host reads once per tick. A fixed-size ring decouples the two: the
 * firmware never blocks and never allocates, and the host gets whatever
 * accumulated since its last read. When the host reads too rarely, the OLDEST
 * lines are dropped — the newest are the ones a pilot or a developer wants —
 * and the count of what was dropped is kept so nothing disappears silently.
 *
 * ファームは自身のタスクの中から任意の時点で書き、ホストは 1 刻みに 1 回読む。
 * 大きさの決まったリングが両者を切り離す。ファームは止まらず確保もせず、ホストは
 * 前回の読み出し以降に溜まったものを受け取る。ホストの読み出しが遅れたときは
 * **古い**行から捨てる ― 操縦者や開発者が見たいのは新しい行である ― が、捨てた数を
 * 覚えておくので、黙って消えることはない。
 *
 * @design docs/plans/unity-simulator.md §4 設計上の決定（ログ）
 */

#include <cstdarg>
#include <cstdio>
#include <cstring>

namespace {

/// Longest single log line kept, including the terminator. Longer lines are
/// truncated rather than dropped, so the beginning of the message survives.
/// 保持する 1 行の最大長（終端を含む）。これを超える行は捨てずに切り詰めるので、
/// 文言の先頭は残る。
constexpr int kLineMax = 256;

/// How many lines the ring holds. At the firmware's boot the log is busiest —
/// roughly 200 lines over the first second — so this covers a host that reads
/// only a few times per second during start-up.
/// リングが保持する行数。ログが最も多いのはファームの起動時で、最初の 1 秒に
/// およそ 200 行が出る。起動中に毎秒数回しか読まないホストでも賄える数にしてある。
constexpr int kLineCount = 512;

char g_lines[kLineCount][kLineMax];

/// Index of the oldest line held, and how many lines are held.
/// 保持している最も古い行の添字と、保持している行数。
int g_head = 0;
int g_size = 0;

/// Lines dropped because the ring was full, counted from boot.
/// リングが満杯で捨てた行数（起動からの累計）。
int g_dropped = 0;

}  // namespace

extern "C" {

// -----------------------------------------------------------------------------
// The ESP_LOGx destination. Formats the line the same way the SILS header's
// fprintf(stderr, "[LEVEL] tag: ...") does, so a reader sees the same text.
// ESP_LOGx の行き先。SILS のヘッダの fprintf(stderr, "[LEVEL] tag: ...") と同じ
// 書式で整形するので、読み手には同じ文面が見える。
// -----------------------------------------------------------------------------
void sfu_log_ring_write(const char* level, const char* tag, const char* format, ...)
{
    // Drop the oldest line when the ring is full, so the newest always fits.
    // リングが満杯なら最も古い行を捨て、最も新しい行が必ず入るようにする。
    const bool is_full = (g_size == kLineCount);
    if (is_full) {
        g_head = (g_head + 1) % kLineCount;
        --g_size;
        ++g_dropped;
    }

    const int slot = (g_head + g_size) % kLineCount;
    char* line = g_lines[slot];

    const int prefix_len =
        std::snprintf(line, kLineMax, "[%s] %s: ", level, tag);
    // snprintf reports what it WOULD have written, so a long prefix can report
    // past the buffer; clamp before using it as an offset.
    // snprintf は「書こうとした長さ」を返すため、長い接頭辞では領域の外を指し得る。
    // 位置として使う前に丸める。
    const int used = (prefix_len < 0) ? 0
                   : (prefix_len > kLineMax - 1 ? kLineMax - 1 : prefix_len);

    va_list args;
    va_start(args, format);
    std::vsnprintf(line + used, (size_t)(kLineMax - used), format, args);
    va_end(args);

    ++g_size;
}

// -----------------------------------------------------------------------------
// Copy out every line held, newline-separated, and forget them. A line that
// does not fit in the remaining capacity is left in the ring for the next read.
// 保持している行を改行区切りで取り出し、リングから消す。残り容量に収まらない行は
// リングに残し、次の読み出しに回す。
// -----------------------------------------------------------------------------
int32_t sfu_log_read(char* buffer, int32_t capacity)
{
    if (buffer == nullptr || capacity <= 0) return 0;

    int written = 0;
    while (g_size > 0) {
        const char* line = g_lines[g_head];
        const int line_len = (int)std::strlen(line);
        // One byte for the newline and one for the terminator.
        // 改行 1 バイトと終端 1 バイトのぶん。
        const bool fits = (written + line_len + 2 <= capacity);
        if (!fits) break;

        std::memcpy(buffer + written, line, (size_t)line_len);
        written += line_len;
        buffer[written++] = '\n';

        g_head = (g_head + 1) % kLineCount;
        --g_size;
    }

    buffer[written] = '\0';
    return written;
}

int32_t sfu_log_dropped(void) { return g_dropped; }

}  // extern "C"
