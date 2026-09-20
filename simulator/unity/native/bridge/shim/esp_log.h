/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file esp_log.h
 * @brief The Unity build's ESP_LOGx destination: a ring buffer the host reads
 *        with sfu_log_read_record, instead of the terminal's stderr.
 *        Unity 版での ESP_LOGx の行き先。端末の stderr ではなく、ホストが
 *        sfu_log_read_record で読み出すリングバッファへ送る。
 *
 * **When `simulator/sils/compat/esp_log.h` changes, review this file.** The two
 * are separate copies of the same surface — the same five macros, the same
 * `esp_log_level_t`, the same re-exports — and the firmware includes whichever
 * comes first on the include path. A macro or a declaration added there and not
 * here breaks the Unity build (or, worse, silently logs differently).
 * **`simulator/sils/compat/esp_log.h` を変えたらこのファイルも見直すこと。**
 * 両者は同じ面（同じ 5 つのマクロ・同じ `esp_log_level_t`・同じ再エクスポート）の
 * 別々の写しであり、ファームが読むのは include パスの先にある方である。向こうに
 * 足してこちらに足さなかったマクロや宣言は、Unity 版のビルドを壊す（もっと悪ければ、
 * 黙って違うログを出す）。
 *
 * `simulator/sils/compat/esp_log.h` sends every ESP_LOGx to `fprintf(stderr)`,
 * which is right for a terminal and useless in a browser — nothing there can
 * read a process's stderr back. This header has the SAME name and is placed
 * EARLIER on the include path, so the unmodified firmware's `#include
 * "esp_log.h"` resolves here instead, with not one byte of the firmware edited.
 * It is the same technique `compat_wasm/cstdio` already uses for musl's const
 * `stdout`, and that `esp_idf_host/cstdio` uses for Windows.
 *
 * `simulator/sils/compat/esp_log.h` は ESP_LOGx をすべて `fprintf(stderr)` へ
 * 送る。端末では正しいが、ブラウザでは役に立たない ― そこにはプロセスの stderr を
 * 読み戻せるものが無いためである。本ヘッダは**同じ名前**を持ち、include パスの
 * **より前**に置かれるので、無改変のファームの `#include "esp_log.h"` がこちらに
 * 解決される。ファームは 1 バイトも編集しない。musl の const な `stdout` に対して
 * `compat_wasm/cstdio` が、Windows に対して `esp_idf_host/cstdio` が使っているのと
 * 同じ手口である。
 *
 * The re-exports below (`esp_heap_caps.h`, `esp_rom_sys.h`, `esp_err.h`) and the
 * `esp_log_level_t` enum are carried over verbatim from the SILS header,
 * because the firmware's HAL sources rely on reaching them through this one.
 * 下の再エクスポート（`esp_heap_caps.h`・`esp_rom_sys.h`・`esp_err.h`）と
 * `esp_log_level_t` は SILS のヘッダからそのまま引き継いでいる。ファームの HAL の
 * ソースがこのヘッダ越しにそれらへ届くことを前提にしているためである。
 *
 * @design docs/plans/unity-simulator.md §4 設計上の決定（ログ）
 */

#pragma once

#include <stdint.h>

#include "esp_heap_caps.h"
#include "esp_rom_sys.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Append one record to the ring buffer: a level, a tag, and a body the format
 * is applied to — kept apart, not pasted into one string. Defined in
 * `sfu_log_ring.cpp`; declared here so the macros below can be used from the
 * firmware's C sources as well as its C++ ones.
 * 記録 1 つをリングバッファへ足す。段・タグ・書式を当てた本文を、1 本の文字列に
 * 貼らずに分けたまま渡す。定義は `sfu_log_ring.cpp` にあり、下のマクロをファームの
 * C のソースからも C++ のソースからも使えるよう、ここで宣言する。
 *
 * The `format` attribute makes the compiler check every ESP_LOGx call site's
 * arguments against its format string, exactly as it checks a printf. The
 * firmware is full of these calls and is never edited, so the check is the only
 * thing standing between a wrong `%d` and a wrong number in the log.
 * `format` 属性は、ESP_LOGx の各呼び出し箇所の引数を書式文字列と突き合わせる検査を
 * コンパイラに行わせる。printf に対する検査とまったく同じものである。ファームには
 * この呼び出しが多数あり、しかも編集しない。誤った `%d` とログ中の誤った数値の
 * 間に立つものは、この検査だけである。
 */
void sfu_log_ring_write(int32_t level, const char* tag, const char* format, ...)
#if defined(__GNUC__) || defined(__clang__)
    __attribute__((format(printf, 3, 4)))
#endif
    ;

#ifdef __cplusplus
}  /* extern "C" */
#endif

/* Log levels: carried over from the SILS header because the firmware calls
 * esp_log_level_set with them. The numbers are also what the ring stores and
 * what SfuLogRecord::level reports, so nothing translates between the two.
 * ログの段: ファームが esp_log_level_set に渡すため SILS のヘッダから引き継ぐ。
 * この数値はリングが保持する値であり SfuLogRecord::level が返す値でもあるので、
 * 両者を読み替えるものは無い。 */
typedef enum {
    ESP_LOG_NONE = 0, ESP_LOG_ERROR = 1, ESP_LOG_WARN = 2,
    ESP_LOG_INFO = 3, ESP_LOG_DEBUG = 4, ESP_LOG_VERBOSE = 5,
} esp_log_level_t;

/* The same five macros the SILS header defines. Unlike the SILS header, D and V
 * are NOT compiled out: they reach the ring and are discarded there against a
 * threshold the host moves with `sfu_set_log_level`, so a developer can turn
 * them on in a running simulator without a rebuild. The default threshold
 * discards them, so the default behaviour matches the SILS header's.
 * SILS のヘッダと同じ 5 つのマクロ。ただし SILS のヘッダと違い、D と V を
 * コンパイル時に消しはしない。リングまで届いてそこで閾値と比べて捨てられ、
 * その閾値はホストが `sfu_set_log_level` で動かせる。よって開発者は、動いている
 * シミュレータでビルドし直さずにそれらを出せる。既定の閾値は両者を捨てるので、
 * 既定の振る舞いは SILS のヘッダと同じである。 */
#define ESP_LOGE(tag, fmt, ...) sfu_log_ring_write(ESP_LOG_ERROR,   tag, fmt, ##__VA_ARGS__)
#define ESP_LOGW(tag, fmt, ...) sfu_log_ring_write(ESP_LOG_WARN,    tag, fmt, ##__VA_ARGS__)
#define ESP_LOGI(tag, fmt, ...) sfu_log_ring_write(ESP_LOG_INFO,    tag, fmt, ##__VA_ARGS__)
#define ESP_LOGD(tag, fmt, ...) sfu_log_ring_write(ESP_LOG_DEBUG,   tag, fmt, ##__VA_ARGS__)
#define ESP_LOGV(tag, fmt, ...) sfu_log_ring_write(ESP_LOG_VERBOSE, tag, fmt, ##__VA_ARGS__)

static inline void esp_log_level_set(const char* tag, esp_log_level_t level)
{
    (void)tag;
    (void)level;
}
