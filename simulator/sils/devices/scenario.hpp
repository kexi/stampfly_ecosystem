/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (SILS host bench — StampFly emulator).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file scenario.hpp
 * @brief Declarative input-scenario loader + deterministic driver task.
 *        宣言的な入力シナリオのローダ＋決定論的ドライバタスク。
 *
 * A scenario is a line-based *.scn timeline of prepared inputs fed to the
 * UNMODIFIED firmware at chosen VIRTUAL times. It generalizes the scripted
 * virtual pilot: ESP-NOW ControlPackets (rc / rc_ramp), console bytes (key) and
 * — later — GPIO button / wind / fault. The whole file is parsed, validated and
 * frozen into a fixed sorted vector BEFORE the scheduler starts (no parsing or
 * host I/O during the run), so a run is fully deterministic and reproducible.
 *
 * The driver runs as a TASK holding the run-token (so injecting into the firmware
 * is a normal cooperative step), waiting on the virtual clock between events.
 *
 * シナリオは行ベースの *.scn タイムライン。無改変ファームへ仮想時刻で用意した入力を流す。
 * 仮想 pilot の一般化。起動前に全体をパース・検証して固定 vector に凍結（実行中は IO 無し）
 * ＝完全に決定論的・再現可能。ドライバは run-token 保持タスクとして仮想時計を待つ。
 *
 * Grammar (one event per line; '#' comment; blank ignored):
 *   <t> rc      <thr> <roll> <pitch> <yaw> <arm> [hold_ms] [rate_hz]
 *   <t> rc_ramp <throttle|roll|pitch|yaw> <from> <to> <step> <rate_hz> <arm>
 *   <t> key     "<text with \n \t \r \\ \" escapes>"
 *   <t> btn     <down|up|click|hold_ms>            (deferred -> warn+skip)
 *   <t> wind    <fx> <fy> <fz> [dur_ms]            external force NED [N]
 *   <t> fault   <motor 0-3> <gain 0-1>             degrade one motor's thrust health
 *   <t> bias    <ax> <ay> <az> <gx> <gy> <gz>      deterministic raw IMU bias (FRD)
 *   <t> api     "<command line>"                  Tello-style API command into the
 *                                                  firmware ApiTask parser
 *   <t> handle  <carry_alt> <px> <py> <lift_ms> <carry_ms> <place_ms>
 *                                                  physical handling: lift→right→carry→place
 *                                                  (instantaneous trigger; Plant kinematic)
 * where <t> is either an absolute millisecond time, or '+<ms>' / '+' meaning
 * "<ms> after the previous event ends" (bare '+' = +0, i.e. immediately after).
 *
 * @design simulator/sils/RESET_PLAN.md §13 P8 — scripted input / scenario seam
 */

#pragma once

extern "C" {

// Parse + validate the scenario file. Returns:
//   1  = loaded a non-empty scenario (active),
//   0  = no scenario (path null/empty),
//  -1  = parse/validation error (message already printed to stderr with line no.)
// パース＋検証。1=有効なシナリオ, 0=シナリオ無し, -1=エラー（行番号付きで stderr 出力済）。
int sils_scenario_load(const char* path);

// True when a non-empty scenario is loaded.
// 有効なシナリオが読み込まれていれば true。
bool sils_scenario_active(void);

// Driver task: dispatches every event at its virtual time. Spawn only when
// sils_scenario_load() returned 1. Deletes itself when the timeline is exhausted.
// ドライバタスク: 各事象を仮想時刻で作動。load が 1 のときだけ起動。完了で自タスク削除。
void sils_scenario_driver_task(void* arg);

// Register the target's API-command entry point (vehicle registers
// sf_api_inject_line from emu_vehicle_glue.cpp). Targets without an ApiTask
// never call this, and the `api` channel then records the line as unsupported.
// ターゲットの API コマンド入口を登録する（vehicle は emu_vehicle_glue.cpp から
// sf_api_inject_line を登録する）。ApiTask を持たないターゲットは呼ばず、`api`
// チャネルはその行を未対応として記録する。
void sils_scenario_register_api_inject(void (*fn)(const char*));

// Feed one API command line through whatever was registered above. Returns
// false when nothing is registered (the caller reports it; this must not be a
// hard error, because the same emulator sources link for targets with no
// ApiTask). This is the seam rc_stdin.cpp's `api <line>` verb goes through, so
// that live stdin and a *.scn `api` event reach the firmware by one path.
// 上で登録されたものへ API コマンド行を 1 行流す。未登録なら false を返す
// （報告は呼び出し側が行う。ApiTask を持たないターゲットでも同じソースが
// リンクされるため、ここを致命的エラーにしてはならない）。rc_stdin.cpp の
// `api <行>` はこの継ぎ目を通る。ライブ stdin と *.scn の `api` 事象が 1 つの
// 経路でファームへ届くようにするためである。
bool sils_scenario_api_inject(const char* line);

}  // extern "C"
