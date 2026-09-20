/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 * Part of StampFly Ecosystem (Unity/WebAssembly native core).
 */

/**
 * @file wasm_stdio_shim.cpp
 * @brief Definitions backing compat_wasm/cstdio's stdout/stderr shadow —
 *        see that header for why this shim exists. Emscripten-only (empty
 *        translation unit elsewhere).
 *        compat_wasm/cstdio の stdout/stderr シャドウを実体化する定義
 *        （理由は同ヘッダ参照）。Emscripten 限定（他では空の翻訳単位）。
 */

#ifdef __EMSCRIPTEN__
// Include the REAL musl <stdio.h> directly, bypassing our own C++ shim, so
// the initialisers below read musl's genuine const streams rather than the
// shadow variables this file defines (which would be a self-reference).
// 本物の musl <stdio.h> を直接取り込み、自前の C++ シムを経由しない。これにより
// 下の初期化子は、このファイルが定義するシャドウ変数（自己参照になる）ではなく
// musl 本来の const ストリームを読む。
#include <stdio.h>

FILE* g_sils_wasm_stdout = stdout;
FILE* g_sils_wasm_stderr = stderr;
#endif
