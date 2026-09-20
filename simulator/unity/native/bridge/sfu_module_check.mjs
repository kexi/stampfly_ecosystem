// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Kouhei Ito
//
// Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
// https://github.com/M5Fly-kanazawa/stampfly_ecosystem
//
// Flies the shipped WebAssembly module from JavaScript, the way Unity's .jslib
// will: load sfu_firmware.js, lay the structs out in the module's heap by hand,
// call sfu_step once per 2.5 ms tick, and integrate the rigid body in JS.
//
// 出荷する WebAssembly モジュールを JavaScript から飛ばす。Unity の .jslib が
// 行うのと同じ形で、sfu_firmware.js を読み込み、構造体をモジュールのヒープに
// 手で並べ、2.5 ms の刻みごとに sfu_step を 1 回呼び、剛体を JS で積分する。
//
// This is the check that the module is usable from outside C++ at all: the
// exports are reachable, `sfu_step` returns synchronously despite Asyncify, the
// struct layout a hand-written caller assumes matches what the module compiled,
// and the same flight comes out. The flight itself lives in `sfu_flight.mjs`,
// shared with `sfu_heap_layout_check.mjs`; the physics there is deliberately the
// same simple integrator `sfu_external_smoke.cpp` uses, so the two can be
// compared.
//
// これは「モジュールが C++ の外から使えるか」を確かめるものである。export に
// 届くこと、Asyncify を使っていても `sfu_step` が同期に返ること、手書きの
// 呼び出し側が前提にする構造体の配置がモジュールのコンパイル結果と一致すること、
// そして同じ飛行が出てくること。飛行そのものは `sfu_flight.mjs` にあり、
// `sfu_heap_layout_check.mjs` と共有している。そこの物理は
// `sfu_external_smoke.cpp` と同じ単純な積分器を意図的に使っており、2 つを
// 突き合わせられるようにしてある。
//
// Usage / 使い方:
//   node sfu_module_check.mjs <path to sfu_firmware.js> [simulated seconds]
//                             [--log-jsonl <path>] [--run-id <id>]
//
// @design docs/plans/unity-simulator.md §4 設計上の決定（スレッド）, §5 段階 2
//         AGENTS.md「新しく書くコードのログの決まり」

import { createRequire } from "node:module";
import { resolve } from "node:path";
import { mkdirSync, createWriteStream } from "node:fs";
import { dirname } from "node:path";

import {
  SFU_ABI_VERSION, SFU_OK, SFU_ERR_BAD_ARGUMENT, SFU_ERR_SHUT_DOWN, DT_US_MAX,
  STRUCT_CONFIG, STRUCT_STEP_IN, STRUCT_STEP_OUT, STRUCT_LOG_RECORD,
  CONFIG_SIZE, IN_SIZE, OUT_SIZE, LOG_SIZE,
  LOG_STRUCT_SIZE, LOG_LEVEL, LOG_SIM_US, LOG_TAG, LOG_TAG_MAX,
  LOG_MESSAGE, LOG_MSG_MAX,
  OUT_ARMED, OUT_BATTERY_VOLTAGE, OUT_FLIGHT_STATE,
  TICK_US,
  allocateStructs, boot, fly, stepForStatus,
} from "./sfu_flight.mjs";

/// The level names AGENTS.md's logging rules use, by SfuLogRecord::level.
/// AGENTS.md のログの決まりが使う段の名前。SfuLogRecord::level の値で引く。
const LEVEL_NAMES = { 1: "error", 2: "warn", 3: "info", 4: "debug", 5: "debug" };

/// UTC, RFC 3339, milliseconds — the `ts` the logging rules ask for.
/// UTC・RFC 3339・ミリ秒 ― ログの決まりが求める `ts` である。
function utcTimestamp() {
  return new Date().toISOString().replace(/\.(\d{3})\d*Z$/, ".$1Z");
}

/// `YYYYMMDDTHHMMSSZ-` plus eight hex digits, the same shape
/// `lib/sfcli/utils/jsonl_log.py`'s `new_run_id()` produces, so an id from here
/// sorts and reads the same as one from the `sf` side.
/// `YYYYMMDDTHHMMSSZ-` に 16 進 8 桁。`lib/sfcli/utils/jsonl_log.py` の
/// `new_run_id()` と同じ形なので、ここで発行した識別子も `sf` 側のものと同じように
/// 並び、同じように読める。
function newRunId() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
  const tail = Math.floor(Math.random() * 0x100000000).toString(16).padStart(8, "0");
  return `${stamp}-${tail}`;
}

/// Writes JSON Lines for the length of one run, in the shape AGENTS.md's
/// logging rules define. Firmware records carry `src: "fw"` and
/// `event: "fw.log"`; this script's own lines carry `src: "bridge"`.
///
/// Reading the log never affects the simulation: draining the ring moves no
/// clock, runs no task and touches no plant state. Of the keys written, only
/// `ts` and the random tail of `run_id` differ between two runs of the same
/// flight — `sim_us`, `level`, `tag` and `msg` repeat exactly, which is what a
/// comparison of two runs looks at.
///
/// 1 回の実行のあいだ、AGENTS.md のログの決まりが定める形で JSON Lines を書く。
/// ファームの記録は `src: "fw"` と `event: "fw.log"` を、このスクリプト自身の行は
/// `src: "bridge"` を持つ。
///
/// ログの取り出しがシミュレーションに影響することはない。リングを空にしても時計は
/// 動かず、タスクも走らず、プラントの状態にも触れない。書く鍵のうち、同じ飛行を
/// 2 回行って違うのは `ts` と `run_id` の末尾の乱数だけである。`sim_us`・`level`・
/// `tag`・`msg` は厳密に繰り返され、2 回の実行の比較が見るのはそちらである。
class JsonlLog {
  constructor(path, runId) {
    this.runId = runId;
    this.bootId = `${runId}-b1`;
    this.stream = null;
    if (!path) return;
    mkdirSync(dirname(path), { recursive: true });
    this.stream = createWriteStream(path, { encoding: "utf8" });
  }

  get isOpen() {
    return this.stream !== null;
  }

  /// One line from this script itself. `simUs` null leaves the field out.
  /// このスクリプト自身の 1 行。`simUs` が null なら欄ごと省く。
  writeBridge(level, event, simUs, msg) {
    if (this.stream === null) return;
    const line = {
      ts: utcTimestamp(), level, src: "bridge", event,
      run_id: this.runId, boot_id: this.bootId,
    };
    if (simUs !== null) line.sim_us = simUs;
    line.msg = msg;
    this.stream.write(`${JSON.stringify(line)}\n`);
  }

  /// One firmware record, its parts placed straight into their keys. Nothing
  /// here parses a line back apart: the parts arrive already separated.
  /// ファームの記録 1 つ。部分をそのまま対応する鍵へ置く。ここで行を解析して部分へ
  /// 戻すことはしない ― 部分は既に分かれたまま届く。
  writeFirmware(record) {
    if (this.stream === null) return;
    this.stream.write(`${JSON.stringify({
      ts: utcTimestamp(),
      level: LEVEL_NAMES[record.level] ?? "info",
      src: "fw",
      event: "fw.log",
      run_id: this.runId,
      boot_id: this.bootId,
      sim_us: record.simUs,
      tag: record.tag,
      msg: record.msg,
    })}\n`);
  }

  /// Close the file and wait for it to be on disk. `process.exit` discards
  /// whatever a write stream still holds in memory, so the caller must await
  /// this before exiting or the log comes out empty.
  /// ファイルを閉じ、ディスクに載るまで待つ。`process.exit` は書き込みの流れが
  /// まだ記憶域に抱えているものを捨てるので、呼び出し側は終了の前にこれを待たな
  /// ければならない。待たなければログは空になる。
  async close() {
    if (this.stream === null) return;
    await new Promise((resolve) => this.stream.end(resolve));
  }
}

/// Take every record the firmware has logged since the last drain and write it.
/// Returns how many were written.
/// 前回の取り出し以降にファームが書いた記録を全て取り出して書く。書いた数を返す。
function drainFirmware(sfu, recordPtr, log) {
  if (!log.isOpen) return 0;
  let written = 0;
  for (;;) {
    sfu.HEAP32[(recordPtr + LOG_STRUCT_SIZE) >> 2] = LOG_SIZE;
    if (sfu._sfu_log_read_record(recordPtr) !== 1) break;
    log.writeFirmware({
      level: sfu.HEAP32[(recordPtr + LOG_LEVEL) >> 2],
      // The virtual clock is int64; Number holds it exactly well past any run
      // length this check uses. 仮想時計は int64。この確認が扱う長さなら Number
      // で正確に表せる。
      simUs: Number(sfu.HEAPU32[(recordPtr + LOG_SIM_US) >> 2]) +
             Number(sfu.HEAP32[(recordPtr + LOG_SIM_US + 4) >> 2]) * 4294967296,
      tag: sfu.UTF8ToString(recordPtr + LOG_TAG, LOG_TAG_MAX),
      msg: sfu.UTF8ToString(recordPtr + LOG_MESSAGE, LOG_MSG_MAX),
    });
    ++written;
  }
  return written;
}

/// `[seconds] [--log-jsonl <path>] [--run-id <id>]` after the module path.
/// モジュールのパスの後の `[秒数] [--log-jsonl <path>] [--run-id <id>]`。
function parseOptions(argv) {
  const options = { simSeconds: 30, logPath: null, runId: null };
  for (let i = 0; i < argv.length; ++i) {
    const arg = argv[i];
    if (arg === "--log-jsonl" || arg === "--run-id") {
      if (i + 1 >= argv.length) fail(`${arg} needs a value`);
      if (arg === "--log-jsonl") options.logPath = argv[i + 1];
      else options.runId = argv[i + 1];
      ++i;
      continue;
    }
    if (arg.startsWith("-")) fail(`unknown option ${arg}`);
    options.simSeconds = Number(arg);
  }
  return options;
}

function fail(message) {
  console.error(`[module_check] ${message}`);
  process.exit(1);
}

/// What dt_us accepts and refuses. Run AFTER the flight: a refused step touches
/// nothing, but the accepted one moves the virtual clock, and running it before
/// the flight would shift every scripted moment the flight is judged on.
/// dt_us が何を受理し何を拒むか。飛行の**後**で行う。拒まれる刻みは何にも触れないが、
/// 受理される刻みは仮想時計を動かす。飛行の前に行えば、判定に使う台本の時点が
/// すべてずれてしまう。
function checkDtUs(sfu, mem) {
  const cases = [
    [0, SFU_ERR_BAD_ARGUMENT, "dt_us = 0"],
    [DT_US_MAX + 1, SFU_ERR_BAD_ARGUMENT, "dt_us above the cap"],
    [DT_US_MAX, SFU_OK, "dt_us at the cap"],
  ];
  for (const [dtUs, expected, name] of cases) {
    const status = stepForStatus(sfu, mem, dtUs);
    if (status !== expected) {
      console.error(`[module_check] ${name}: expected ${expected}, got ${status}`);
      return false;
    }
  }
  return true;
}

/// After `sfu_shutdown` the module is finished: every entry point must refuse,
/// and refuse again the second time, without reaching the fiber stacks the
/// shutdown freed.
/// `sfu_shutdown` の後、モジュールは終了している。どの入口も拒まなければならず、
/// 2 回目も同じように拒まなければならない。終了が解放した fiber のスタックへ
/// 届かせずに、である。
function checkAfterShutdown(sfu, mem) {
  if (sfu._sfu_shutdown() !== SFU_OK && sfu._sfu_last_status() !== SFU_OK) {
    console.error("[module_check] the first sfu_shutdown failed");
    return false;
  }

  const stepStatus = stepForStatus(sfu, mem, TICK_US);
  const refusals = [
    [stepStatus, "sfu_step"],
    [sfu._sfu_set_wind(0, 0, 0), "sfu_set_wind"],
    [sfu._sfu_set_motor_health(0, 1), "sfu_set_motor_health"],
    [sfu._sfu_set_imu_bias(0, 0, 0, 0, 0, 0), "sfu_set_imu_bias"],
    [sfu._sfu_shutdown(), "a second sfu_shutdown"],
  ];
  for (const [got, name] of refusals) {
    if (got !== SFU_ERR_SHUT_DOWN) {
      console.error(`[module_check] ${name} after shutdown: ` +
                    `expected ${SFU_ERR_SHUT_DOWN}, got ${got}`);
      return false;
    }
  }
  return true;
}

async function main() {
  const modulePath = process.argv[2];
  if (!modulePath) {
    fail("usage: node sfu_module_check.mjs <sfu_firmware.js> [seconds] " +
         "[--log-jsonl <path>] [--run-id <id>]");
  }
  const options = parseOptions(process.argv.slice(3));
  const simSeconds = options.simSeconds;

  // The module is MODULARIZE'd CommonJS, so it is required, not imported.
  // モジュールは MODULARIZE された CommonJS なので、import ではなく require する。
  const require = createRequire(import.meta.url);
  const createSfuFirmware = require(resolve(modulePath));
  const sfu = await createSfuFirmware();

  if (sfu._sfu_abi_version() !== SFU_ABI_VERSION) {
    fail(`ABI mismatch: module ${sfu._sfu_abi_version()}, this script ${SFU_ABI_VERSION}`);
  }
  const sizes = [
    [STRUCT_CONFIG, CONFIG_SIZE, "SfuConfig"],
    [STRUCT_STEP_IN, IN_SIZE, "SfuStepIn"],
    [STRUCT_STEP_OUT, OUT_SIZE, "SfuStepOut"],
    [STRUCT_LOG_RECORD, LOG_SIZE, "SfuLogRecord"],
  ];
  for (const [which, expected, name] of sizes) {
    const actual = sfu._sfu_struct_size(which);
    if (actual !== expected) fail(`${name}: module says ${actual}, this script assumes ${expected}`);
  }
  console.log(`[module_check] ABI ${SFU_ABI_VERSION}, struct sizes agree ` +
              `(config ${CONFIG_SIZE}, in ${IN_SIZE}, out ${OUT_SIZE}, log ${LOG_SIZE})`);

  // Lay the structs out in the module's own heap. How much is allocated here,
  // and in what order, is free to change: the flight no longer depends on the
  // heap layout at boot. It once did — a 16-byte shift ahead of `sfu_boot`
  // turned the hover into an "Impact detected: 8.0G" failure — but the cause was
  // the fiber scheduler handing out task stacks that `malloc` had aligned to
  // only 8 bytes, fixed in e4ed60cd. `sfu_heap_layout_check.mjs` flies this same
  // script behind 24 different pre-boot allocations on every `unity-native-test`
  // and requires them all to agree, so a regression is caught rather than
  // guarded against by discipline.
  //
  // 構造体をモジュール自身のヒープに並べる。ここで確保する量と順序は自由に変えて
  // よい。飛行はもはや起動時点のヒープの配置に依存しないからである。かつては依存
  // した ― `sfu_boot` の前に 16 バイトずらすだけでホバリングが「Impact detected:
  // 8.0G」の失敗に変わった ― が、原因は fiber 版スケジューラが `malloc`（8 バイト
  // 整列）で取ったタスクスタックを配っていたことで、e4ed60cd で直っている。
  // `sfu_heap_layout_check.mjs` が `unity-native-test` のたびに、起動前の確保 24
  // 通りの裏でこの同じ台本を飛ばして全て一致することを求めるので、退行は規律では
  // なく試験で捕まる。
  const mem = allocateStructs(sfu);

  const booted = boot(sfu, mem);
  if (booted !== 0) fail(`sfu_boot failed: ${booted}`);

  const runId = options.runId ?? newRunId();
  const log = new JsonlLog(options.logPath, runId);
  log.writeBridge("info", "bridge.boot", null, "sfu_boot");

  // Drain the firmware's log once per simulated second — what a host does,
  // rather than once at the end.
  // ファームのログをシミュレーション 1 秒に 1 回取り出す。最後に 1 回ではなく、
  // ホストが行うのはこちらである。
  const result = fly(sfu, mem, simSeconds,
                     () => drainFirmware(sfu, mem.recordPtr, log));
  if (result.failure !== null) fail(result.failure);

  const flightState = sfu.HEAP32[mem.i32(mem.outPtr, OUT_FLIGHT_STATE)];
  const armed = sfu.HEAP32[mem.i32(mem.outPtr, OUT_ARMED)];
  const batteryVoltage = sfu.HEAPF32[mem.f32(mem.outPtr, OUT_BATTERY_VOLTAGE)];

  console.log(`[module_check] simulated ${simSeconds.toFixed(1)} s in ` +
              `${result.wallSeconds.toFixed(3)} s of wall clock`);
  console.log(`[module_check] REAL TIME PER SIMULATED SECOND = ` +
              `${(result.wallSeconds / simSeconds).toFixed(4)} s  (target <= 0.30)`);
  console.log(`[module_check] max altitude ${result.maxAltitude.toFixed(3)} m, ` +
              `final altitude ${result.lastAltitude.toFixed(3)} m, ` +
              `state ${flightState}, armed ${armed}, vbatt ${batteryVoltage.toFixed(2)}`);

  console.log(`[module_check] max tilt ${result.maxTiltDeg.toFixed(1)} deg, ` +
              `final tilt ${result.lastTiltDeg.toFixed(1)} deg`);
  console.log(`[module_check] clock exact over ${result.ticks} tick(s): ` +
              `${result.clockExact ? "OK" : "FAILED"}`);

  console.log(`[module_check] hover ${result.passed ? "OK" : "FAILED"} ` +
              `(altitude ${result.hovering ? "OK" : "FAILED"}, ` +
              `level ${result.level ? "OK" : "FAILED"}, ` +
              `FLYING ${result.flying ? "OK" : "FAILED"})`);

  // Everything after the flight: dt_us's range, then the refusals that must
  // follow a shutdown. Each reads `status` rather than the return value.
  // 飛行の後のもの。dt_us の範囲、続いて終了後に返らなければならない拒否。
  // どれも戻り値ではなく `status` を読む。
  const dtUsOk = checkDtUs(sfu, mem);
  console.log(`[module_check] dt_us range checks ${dtUsOk ? "OK" : "FAILED"}`);

  const refusedAfterShutdown = checkAfterShutdown(sfu, mem);
  console.log(`[module_check] refusals after shutdown ` +
              `${refusedAfterShutdown ? "OK" : "FAILED"}`);

  drainFirmware(sfu, mem.recordPtr, log);
  const dropped = sfu._sfu_log_dropped();
  if (dropped > 0) console.log(`[module_check] ${dropped} log record(s) dropped`);
  log.writeBridge("info", "bridge.shutdown", null,
                  result.passed ? "hover OK" : "hover FAILED");
  await log.close();
  if (log.isOpen) {
    console.error(`[module_check] log ${options.logPath} (run_id ${runId})`);
  }

  sfu._free(mem.configPtr);
  sfu._free(mem.inPtr);
  sfu._free(mem.outPtr);
  sfu._free(mem.recordPtr);
  process.exit((result.passed && dtUsOk && refusedAfterShutdown) ? 0 : 2);
}

main().catch((error) => fail(error.stack ?? String(error)));
