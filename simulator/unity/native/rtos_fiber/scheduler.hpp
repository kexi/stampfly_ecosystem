/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core).
 */

/**
 * @file scheduler.hpp (selector)
 * @brief Makes `#include "scheduler.hpp"` resolve to the FIBER scheduler.
 *        `#include "scheduler.hpp"` を fiber 版スケジューラに解決させる。
 *
 * The consumers of the scheduler (simulator/sils/smoke/rtos_smoke.cpp,
 * simulator/sils/rtos/esp_timer_shim.cpp, simulator/sils/emu/emu_main.cpp)
 * include "scheduler.hpp" by that name. Putting THIS directory before
 * simulator/sils/rtos on the include path swaps in the fiber declarations
 * without editing a single one of them. The declarations themselves live in
 * simulator/sils/rtos/scheduler_fiber.hpp, next to their implementation — this
 * file only redirects, so there is exactly one copy to keep in step.
 *
 * スケジューラの利用側（simulator/sils/smoke/rtos_smoke.cpp、
 * simulator/sils/rtos/esp_timer_shim.cpp、simulator/sils/emu/emu_main.cpp）は
 * "scheduler.hpp" という名前で取り込む。このディレクトリを include パス上で
 * simulator/sils/rtos より前に置くことで、それらを1つも編集せずに fiber 版の宣言へ
 * 差し替えられる。宣言の実体は実装と同じ場所の
 * simulator/sils/rtos/scheduler_fiber.hpp にあり、本ファイルは向き先を変えるだけ —
 * よって同期を保つべき写しは1つも増えない。
 */

#pragma once

#include "scheduler_fiber.hpp"
