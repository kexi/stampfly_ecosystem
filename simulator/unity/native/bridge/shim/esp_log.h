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
 *        with sfu_log_read, instead of the terminal's stderr.
 *        Unity 版での ESP_LOGx の行き先。端末の stderr ではなく、ホストが
 *        sfu_log_read で読み出すリングバッファへ送る。
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

#include "esp_heap_caps.h"
#include "esp_rom_sys.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Append one already-formatted line to the ring buffer. Defined in
 * `sfu_log_ring.cpp`; declared here so the macros below can be used from the
 * firmware's C sources as well as its C++ ones.
 * 整形済みの 1 行をリングバッファへ足す。定義は `sfu_log_ring.cpp` にあり、
 * 下のマクロをファームの C のソースからも C++ のソースからも使えるよう、
 * ここで宣言する。
 */
void sfu_log_ring_write(const char* level, const char* tag, const char* format, ...);

#ifdef __cplusplus
}  /* extern "C" */
#endif

/* The same five macros the SILS header defines, with the same levels silenced,
 * so a firmware source compiled against either one produces the same lines.
 * SILS のヘッダと同じ 5 つのマクロで、無音にする段も同じ。どちらでコンパイルされた
 * ファームのソースも同じ行を出す。 */
#define ESP_LOGI(tag, fmt, ...) sfu_log_ring_write("INFO", tag, fmt, ##__VA_ARGS__)
#define ESP_LOGW(tag, fmt, ...) sfu_log_ring_write("WARN", tag, fmt, ##__VA_ARGS__)
#define ESP_LOGE(tag, fmt, ...) sfu_log_ring_write("ERR",  tag, fmt, ##__VA_ARGS__)
#define ESP_LOGD(tag, fmt, ...) ((void)0)
#define ESP_LOGV(tag, fmt, ...) ((void)0)

/* Log levels: carried over from the SILS header because the firmware calls
 * esp_log_level_set with them. Setting a level is a no-op here, as it is there.
 * ログの段: ファームが esp_log_level_set に渡すため SILS のヘッダから引き継ぐ。
 * 段の設定は向こうと同様ここでも何もしない。 */
typedef enum {
    ESP_LOG_NONE = 0, ESP_LOG_ERROR = 1, ESP_LOG_WARN = 2,
    ESP_LOG_INFO = 3, ESP_LOG_DEBUG = 4, ESP_LOG_VERBOSE = 5,
} esp_log_level_t;

static inline void esp_log_level_set(const char* tag, esp_log_level_t level)
{
    (void)tag;
    (void)level;
}
