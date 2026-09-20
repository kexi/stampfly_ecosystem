/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file sfu_module_main.cpp
 * @brief The translation unit the shipped module links against. It holds no
 *        logic: the entry points are the sfu_* functions in sfu_bridge.cpp.
 *        出荷するモジュールがリンクする翻訳単位。論理は持たない。入口は
 *        sfu_bridge.cpp にある sfu_* の関数である。
 *
 * Both artefacts need one source of their own. The WebAssembly module needs a
 * `main` because Emscripten builds a program, not a library; it returns
 * immediately, and every `sfu_*` entry point stays alive through
 * `EXPORTED_FUNCTIONS`, called later from `.jslib`. The macOS dylib needs a
 * source file to be a target at all, and re-exports the same entry points
 * through `sfu_exports.txt`.
 *
 * どちらの成果物にも、自分自身のソースが 1 つ要る。WebAssembly のモジュールに
 * `main` が要るのは、Emscripten が作るのがライブラリでなくプログラムだからである。
 * それはすぐに戻り、各 `sfu_*` の入口は `EXPORTED_FUNCTIONS` によって生き続け、
 * 後から `.jslib` に呼ばれる。macOS の dylib はターゲットになるためにソースが要り、
 * 同じ入口を `sfu_exports.txt` で再 export する。
 *
 * @design docs/plans/unity-simulator.md §4 設計上の決定（スレッド）
 */

#include "sfu_api.h"

#ifdef __EMSCRIPTEN__

/**
 * Returns at once, leaving the module loaded and idle. The host then calls
 * sfu_boot when it is ready to power the vehicle on; `-sEXIT_RUNTIME=0` keeps
 * the runtime alive after this returns.
 * すぐに戻り、モジュールを読み込まれたまま待たせる。ホストは機体の電源を入れる
 * 用意ができてから sfu_boot を呼ぶ。`-sEXIT_RUNTIME=0` により、ここから戻った
 * 後もランタイムは動作している。
 */
int main(void)
{
    return 0;
}

#endif  /* __EMSCRIPTEN__ */
