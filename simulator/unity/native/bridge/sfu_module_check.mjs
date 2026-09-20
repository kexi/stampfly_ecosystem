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
// and the same flight comes out. The physics here is deliberately the same
// simple integrator `sfu_external_smoke.cpp` uses, so the two can be compared.
//
// これは「モジュールが C++ の外から使えるか」を確かめるものである。export に
// 届くこと、Asyncify を使っていても `sfu_step` が同期に返ること、手書きの
// 呼び出し側が前提にする構造体の配置がモジュールのコンパイル結果と一致すること、
// そして同じ飛行が出てくること。ここの物理は `sfu_external_smoke.cpp` と同じ
// 単純な積分器を意図的に使っており、2 つを突き合わせられるようにしてある。
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

// ---------------------------------------------------------------------------
// The struct layout, written out by hand exactly as the C# side will have to.
// `sfu_struct_size` lets us check each total against what the module compiled,
// which catches a field added or reordered on the C side.
// 構造体の配置。C# 側がそうせざるを得ないのと同じく、手で書き下ろしてある。
// `sfu_struct_size` により、各合計をモジュールのコンパイル結果と突き合わせられる
// ので、C 側で欄が増えたり並べ替えられたりすれば捕まる。
// ---------------------------------------------------------------------------
const SFU_ABI_VERSION = 2;
const MOTOR_COUNT = 4;

const STRUCT_CONFIG = 0;
const STRUCT_STEP_IN = 1;
const STRUCT_STEP_OUT = 2;
const STRUCT_LOG_RECORD = 4;

const SFU_OK = 0;
const SFU_ERR_BAD_ARGUMENT = -9;
const SFU_ERR_SHUT_DOWN = -10;

/// The cap on one step [µs], as sfu_api.h defines it.
/// 1 刻みの上限 [µs]。sfu_api.h の定義どおり。
const DT_US_MAX = 100000;

// SfuConfig: struct_size, battery_model, ground_effect_gain, turbulence_n,
// thrust_efficiency, torque_authority, motor_delay_ms, noise_level, noise_seed,
// boot_calibration, host_owns_body, start_height_m — all 4 bytes each.
const CONFIG_FIELDS = 12;
const CONFIG_SIZE = CONFIG_FIELDS * 4;
const CONFIG_STRUCT_SIZE = 0;
const CONFIG_BATTERY_MODEL = 4;
const CONFIG_BOOT_CALIBRATION = 36;
const CONFIG_HOST_OWNS_BODY = 40;
const CONFIG_START_HEIGHT = 44;

// SfuStepIn.
const IN_STRUCT_SIZE = 0;
const IN_POSITION = 4;          // 3 floats
const IN_ROTATION = 16;         // 4 floats, x,y,z,w
const IN_VELOCITY_WORLD = 32;   // 3 floats
const IN_ANGULAR_VELOCITY = 44; // 3 floats, world frame
const IN_ACCEL_LOCAL = 56;      // 3 floats, body frame
const IN_RANGE_DOWN = 68;
const IN_RANGE_VALID = 72;
const IN_GROUND_HEIGHT = 76;
const IN_RC_THROTTLE = 80;      // 4 uint16 then 1 uint8 of flags, then 3 padding
const IN_RC_FLAGS = 88;
const IN_DT_US = 92;            // uint32, whole microseconds
const IN_SIZE = 96;

// SfuStepOut.
const OUT_STRUCT_SIZE = 0;
const OUT_FORCE_LOCAL = 4;      // 3 floats
const OUT_TORQUE_LOCAL = 16;    // 3 floats
const OUT_WIND_WORLD = 28;      // 3 floats
const OUT_WRENCH_DT = 40;
const OUT_MOTOR_DUTY = 44;      // 4 floats
const OUT_MOTOR_OMEGA = 60;     // 4 floats
const OUT_BATTERY_VOLTAGE = 76;
// now_us is int64 and must be 8-byte aligned; 80 already is, so no padding.
const OUT_NOW_US = 80;
const OUT_ARMED = 88;
const OUT_FLIGHT_STATE = 92;
const OUT_FLIGHT_MODE = 96;
const OUT_ESTIMATED_ROTATION = 100;  // 4 floats
const OUT_ESTIMATED_POSITION = 116;  // 3 floats
const OUT_TRUTH_POSITION = 128;      // 3 floats
const OUT_TRUTH_ROTATION = 140;      // 4 floats
// The result of the call. Under Asyncify this is the ONLY place a JavaScript
// caller can read it: the value `_sfu_step` returns to JS is the rewind stub's
// (0), not the one the C function returned, because the fiber scheduler
// switches stacks inside the call. See the head note of sfu_api.h.
// 呼び出しの結果。Asyncify のもとでは、JavaScript の呼び出し側がこれを読める場所は
// ここだけである。`_sfu_step` が JS へ返す値は、C の関数が返したものではなく巻き
// 直しの補助関数のもの（0）になる。fiber 版スケジューラが呼び出しの途中でスタックを
// 切り替えるためである。sfu_api.h 冒頭の注記を参照。
const OUT_STATUS = 156;
const OUT_SIZE = 168;                // 164 rounded up to the 8-byte alignment

// SfuLogRecord: struct_size, level, sim_us (int64, 8-aligned), tag, message.
const LOG_STRUCT_SIZE = 0;
const LOG_LEVEL = 4;
const LOG_SIM_US = 8;
const LOG_TAG = 16;                  // 32 bytes
const LOG_TAG_MAX = 32;
const LOG_MESSAGE = 48;              // 224 bytes
const LOG_MSG_MAX = 224;
const LOG_SIZE = 272;

/// The level names AGENTS.md's logging rules use, by SfuLogRecord::level.
/// AGENTS.md のログの決まりが使う段の名前。SfuLogRecord::level の値で引く。
const LEVEL_NAMES = { 1: "error", 2: "warn", 3: "info", 4: "debug", 5: "debug" };

// ---------------------------------------------------------------------------
// The flight. Same script and same physics constants as the C++ smoke checks.
// 飛行。C++ の最小動作確認と同じ台本・同じ物理定数である。
// ---------------------------------------------------------------------------
const ADC_CENTRE = 2048;
const FLAG_ARM = 0x01;
const FLAG_ALT_MODE = 0x08;

const RC_SCRIPT = [
  { tEndUs: 4000000, throttle: ADC_CENTRE, flags: 0 },
  { tEndUs: 5000000, throttle: ADC_CENTRE, flags: FLAG_ARM },
  { tEndUs: 6300000, throttle: 3243, flags: FLAG_ARM },
  { tEndUs: Number.MAX_SAFE_INTEGER, throttle: ADC_CENTRE, flags: FLAG_ARM | FLAG_ALT_MODE },
];

const TICK_US = 2500;
const TICK_SECONDS = TICK_US * 1e-6;
const REST_HEIGHT_M = 0.013;
const MASS_KG = 0.037;

/// The body-axis inertias in Unity's order (X = right, Y = up, Z = forward), so
/// the firmware's roll/pitch/yaw inertias land on Z/X/Y. Same numbers as
/// sfu_external_smoke.cpp, from simulator/sils/models/stampfly.xml.
/// Unity の順（X = 右・Y = 上・Z = 前）に並べた機体軸まわりの慣性。ファームの
/// ロール/ピッチ/ヨーの慣性は Z/X/Y に対応する。数値は sfu_external_smoke.cpp と
/// 同じで、出典は simulator/sils/models/stampfly.xml。
const INERTIA_PITCH_KG_M2 = 13.3e-6;   // about Unity X
const INERTIA_YAW_KG_M2 = 20.4e-6;     // about Unity Y
const INERTIA_ROLL_KG_M2 = 9.16e-6;    // about Unity Z
const GRAVITY_MS2 = 9.81;
const FLOOR_STIFFNESS_N_PER_M = 800.0;
const FLOOR_DAMPING_NS_PER_M = 3.0;
const HOVER_MIN_M = 0.15;
const HOVER_MAX_M = 3.0;

// The same gust and the same verdict as sfu_external_smoke.cpp, for the same
// reason: a vertical take-off into an undisturbed hover never asks the attitude
// loop to correct anything, so it passes just as well with every torque
// component reversed. The gust tips the vehicle, and the run is judged on
// coming back near level and still FLYING.
// sfu_external_smoke.cpp と同じ突風・同じ判定を、同じ理由で用いる。外乱の無い
// ホバリングへ鉛直に離陸する飛行は姿勢ループに何も直させないので、トルクの全成分を
// 反転させても同じように通ってしまう。突風で機体を傾け、水平付近へ戻って FLYING の
// ままであることで判定する。
const GUST_START_US = 10000000;
const GUST_END_US = 12000000;
const GUST_FORCE_N = 0.07;
const LEVEL_MAX_DEG = 20.0;
const FLIGHT_STATE_FLYING = 5;

function rcScriptAt(nowUs) {
  for (const entry of RC_SCRIPT) {
    if (nowUs < entry.tEndUs) return entry;
  }
  return RC_SCRIPT[RC_SCRIPT.length - 1];
}

/// Rotate a body-frame vector into the world frame with a unit quaternion
/// held in Unity's x,y,z,w order. v' = v + 2·q_w·(q_vec × v) + 2·q_vec × (q_vec × v).
/// 機体系のベクトルを、Unity の x,y,z,w の順に持った単位クォータニオンで世界系へ回す。
function rotateToWorld(q, v) {
  const t = [
    2 * (q[1] * v[2] - q[2] * v[1]),
    2 * (q[2] * v[0] - q[0] * v[2]),
    2 * (q[0] * v[1] - q[1] * v[0]),
  ];
  return [
    v[0] + q[3] * t[0] + (q[1] * t[2] - q[2] * t[1]),
    v[1] + q[3] * t[1] + (q[2] * t[0] - q[0] * t[2]),
    v[2] + q[3] * t[2] + (q[0] * t[1] - q[1] * t[0]),
  ];
}

function rotateToBody(q, v) {
  return rotateToWorld([-q[0], -q[1], -q[2], q[3]], v);
}

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

/// The angle between the vehicle's own up axis and the world's, in degrees.
/// Zero when level, 180 when inverted.
/// 機体自身の上方向と世界の上方向のなす角 [度]。水平で 0、反転で 180。
function tiltFromLevelDeg(rotation) {
  const bodyUp = rotateToWorld(rotation, [0, 1, 0]);
  const cosine = Math.min(1, Math.max(-1, bodyUp[1]));
  return Math.acos(cosine) * (180 / Math.PI);
}

function fail(message) {
  console.error(`[module_check] ${message}`);
  process.exit(1);
}

/// One step, reading the result the way a wasm caller must: `status` out of the
/// struct, never the return value. Returns the status.
/// 1 刻み。結果は wasm の呼び出し側がそうせざるを得ない読み方 ― 戻り値ではなく
/// 構造体の `status` ― で読む。status を返す。
function stepForStatus(sfu, inPtr, outPtr, i32, dtUs) {
  sfu.HEAP32[i32(inPtr, IN_DT_US)] = dtUs;
  sfu.HEAP32[i32(outPtr, OUT_STATUS)] = 1;   // a value the bridge never writes
  sfu._sfu_step(inPtr, outPtr);
  return sfu.HEAP32[i32(outPtr, OUT_STATUS)];
}

/// What dt_us accepts and refuses. Run AFTER the flight: a refused step touches
/// nothing, but the accepted one moves the virtual clock, and running it before
/// the flight would shift every scripted moment the flight is judged on.
/// dt_us が何を受理し何を拒むか。飛行の**後**で行う。拒まれる刻みは何にも触れないが、
/// 受理される刻みは仮想時計を動かす。飛行の前に行えば、判定に使う台本の時点が
/// すべてずれてしまう。
function checkDtUs(sfu, inPtr, outPtr, i32) {
  const cases = [
    [0, SFU_ERR_BAD_ARGUMENT, "dt_us = 0"],
    [DT_US_MAX + 1, SFU_ERR_BAD_ARGUMENT, "dt_us above the cap"],
    [DT_US_MAX, SFU_OK, "dt_us at the cap"],
  ];
  for (const [dtUs, expected, name] of cases) {
    const status = stepForStatus(sfu, inPtr, outPtr, i32, dtUs);
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
function checkAfterShutdown(sfu, inPtr, outPtr, i32) {
  if (sfu._sfu_shutdown() !== SFU_OK && sfu._sfu_last_status() !== SFU_OK) {
    console.error("[module_check] the first sfu_shutdown failed");
    return false;
  }

  const stepStatus = stepForStatus(sfu, inPtr, outPtr, i32, TICK_US);
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

  // Lay the structs out in the module's own heap, ALL of them before the boot.
  //
  // How much is allocated here, and in what order, must not change without
  // re-running the check: the flight is currently sensitive to the heap layout
  // at boot. Allocating 48 bytes more ahead of `sfu_boot` has been observed to
  // turn the hover into an "Impact detected: 8.0G" failure, and a 16-byte shift
  // is enough to flip it. That sensitivity is a known open fault being
  // investigated separately; until it is fixed, this block stays exactly as it
  // is and nothing new is allocated before the boot.
  //
  // 構造体をモジュール自身のヒープに並べる。**全て**起動の前に行う。
  //
  // ここで確保する量と順序は、確認をやり直さずに変えてはならない。飛行が現在、
  // 起動時点のヒープの配置に対して敏感だからである。`sfu_boot` の前に 48 バイト
  // 多く確保すると、ホバリングが「Impact detected: 8.0G」の失敗に変わることが
  // 観測されており、16 バイトずらすだけで反転する。この感度は既知の未解決の不具合
  // として別途調査中である。直るまで、この塊はこのままにし、起動の前に新しいものを
  // 確保しない。
  const configPtr = sfu._malloc(CONFIG_SIZE);
  const inPtr = sfu._malloc(IN_SIZE);
  const outPtr = sfu._malloc(OUT_SIZE);
  const recordPtr = sfu._malloc(LOG_SIZE);
  sfu.HEAPU8.fill(0, configPtr, configPtr + CONFIG_SIZE);
  sfu.HEAPU8.fill(0, inPtr, inPtr + IN_SIZE);
  sfu.HEAPU8.fill(0, outPtr, outPtr + OUT_SIZE);
  sfu.HEAPU8.fill(0, recordPtr, recordPtr + LOG_SIZE);

  const i32 = (ptr, offset) => ptr + offset >> 2;
  const f32 = (ptr, offset) => ptr + offset >> 2;

  sfu.HEAP32[i32(configPtr, CONFIG_STRUCT_SIZE)] = CONFIG_SIZE;
  sfu.HEAP32[i32(configPtr, CONFIG_BATTERY_MODEL)] = 1;
  sfu.HEAP32[i32(configPtr, CONFIG_BOOT_CALIBRATION)] = 1;
  sfu.HEAP32[i32(configPtr, CONFIG_HOST_OWNS_BODY)] = 1;
  sfu.HEAPF32[f32(configPtr, CONFIG_START_HEIGHT)] = REST_HEIGHT_M;

  const booted = sfu._sfu_boot(configPtr);
  if (booted !== 0) fail(`sfu_boot failed: ${booted}`);

  // Only NOW may this script allocate: the run_id string and the log file's
  // buffers are JavaScript-side, but keeping every allocation after the boot is
  // the same discipline the C++ checks follow (see the note above the _malloc
  // block, and sfu_smoke_options.hpp).
  // 確保を行ってよいのはここからである。run_id の文字列とログファイルの緩衝域は
  // JavaScript 側のものだが、全ての確保を起動の後に置くのは C++ の確認が守るのと
  // 同じ規律である（上の _malloc の塊の注記と sfu_smoke_options.hpp を参照）。
  const runId = options.runId ?? newRunId();
  const log = new JsonlLog(options.logPath, runId);
  log.writeBridge("info", "bridge.boot", null, "sfu_boot");

  sfu.HEAP32[i32(inPtr, IN_STRUCT_SIZE)] = IN_SIZE;
  sfu.HEAP32[i32(outPtr, OUT_STRUCT_SIZE)] = OUT_SIZE;
  sfu.HEAP32[i32(inPtr, IN_DT_US)] = TICK_US;

  const body = {
    position: [0, REST_HEIGHT_M, 0],
    rotation: [0, 0, 0, 1],
    velocityWorld: [0, 0, 0],
    angularVelocityWorld: [0, 0, 0],
    accelLocal: [0, GRAVITY_MS2, 0],
  };

  const totalUs = Math.round(simSeconds * 1e6);
  const wallStart = process.hrtime.bigint();
  let nowUs = 0;
  let ticks = 0;
  let clockExact = true;
  let maxAltitude = 0;
  let lastAltitude = 0;
  let lastTiltDeg = 0;
  let maxTiltDeg = 0;
  let lastFlightState = 0;
  let gustIsOn = false;

  while (nowUs < totalUs) {
    // Pack what the host has, the way Unity's .jslib will.
    // ホストが持っている情報を、Unity の .jslib が行うのと同じ形で詰める。
    for (let axis = 0; axis < 3; ++axis) {
      sfu.HEAPF32[f32(inPtr, IN_POSITION) + axis] = body.position[axis];
      sfu.HEAPF32[f32(inPtr, IN_VELOCITY_WORLD) + axis] = body.velocityWorld[axis];
      sfu.HEAPF32[f32(inPtr, IN_ANGULAR_VELOCITY) + axis] = body.angularVelocityWorld[axis];
      sfu.HEAPF32[f32(inPtr, IN_ACCEL_LOCAL) + axis] = body.accelLocal[axis];
    }
    for (let component = 0; component < 4; ++component) {
      sfu.HEAPF32[f32(inPtr, IN_ROTATION) + component] = body.rotation[component];
    }
    sfu.HEAPF32[f32(inPtr, IN_RANGE_DOWN)] = body.position[1];
    sfu.HEAP32[i32(inPtr, IN_RANGE_VALID)] = 1;
    sfu.HEAPF32[f32(inPtr, IN_GROUND_HEIGHT)] = body.position[1];

    const rc = rcScriptAt(nowUs + TICK_US);
    const rcBase = inPtr + IN_RC_THROTTLE;
    sfu.HEAPU8[rcBase] = rc.throttle & 0xff;
    sfu.HEAPU8[rcBase + 1] = (rc.throttle >> 8) & 0xff;
    for (let stick = 1; stick < 4; ++stick) {
      sfu.HEAPU8[rcBase + stick * 2] = ADC_CENTRE & 0xff;
      sfu.HEAPU8[rcBase + stick * 2 + 1] = (ADC_CENTRE >> 8) & 0xff;
    }
    sfu.HEAPU8[inPtr + IN_RC_FLAGS] = rc.flags;

    // The gust, switched on at its start and off at its end.
    // 突風を、始まりで入れ、終わりで切る。
    const gustShouldBeOn = nowUs >= GUST_START_US && nowUs < GUST_END_US;
    if (gustShouldBeOn !== gustIsOn) {
      gustIsOn = gustShouldBeOn;
      sfu._sfu_set_wind(gustIsOn ? GUST_FORCE_N : 0, 0, 0);
    }

    // `status`, NOT the return value: under Asyncify the value that reaches
    // JavaScript is the rewind stub's, because the fiber scheduler switches
    // stacks inside this call. Zeroed first so "the bridge wrote it" is
    // distinguishable from "nothing happened". See sfu_api.h's head note.
    // 戻り値ではなく `status` を読む。Asyncify のもとで JavaScript へ届く値は
    // 巻き直しの補助関数のものである。fiber 版スケジューラがこの呼び出しの途中で
    // スタックを切り替えるためである。先に 0 を入れておくと「橋渡しが書いた」と
    // 「何も起きなかった」を見分けられる。sfu_api.h 冒頭の注記を参照。
    sfu.HEAP32[i32(outPtr, OUT_STATUS)] = 1;
    sfu._sfu_step(inPtr, outPtr);
    const status = sfu.HEAP32[i32(outPtr, OUT_STATUS)];
    if (status !== SFU_OK) fail(`sfu_step failed at t=${nowUs} us: status ${status}`);

    // Read what came back and integrate, as sfu_external_smoke.cpp does.
    // 返ってきたものを読んで積分する。sfu_external_smoke.cpp と同じ手順である。
    const forceLocal = [
      sfu.HEAPF32[f32(outPtr, OUT_FORCE_LOCAL)],
      sfu.HEAPF32[f32(outPtr, OUT_FORCE_LOCAL) + 1],
      sfu.HEAPF32[f32(outPtr, OUT_FORCE_LOCAL) + 2],
    ];
    const torqueLocal = [
      sfu.HEAPF32[f32(outPtr, OUT_TORQUE_LOCAL)],
      sfu.HEAPF32[f32(outPtr, OUT_TORQUE_LOCAL) + 1],
      sfu.HEAPF32[f32(outPtr, OUT_TORQUE_LOCAL) + 2],
    ];
    const thrustWorld = rotateToWorld(body.rotation, forceLocal);
    const windWorld = [
      sfu.HEAPF32[f32(outPtr, OUT_WIND_WORLD)],
      sfu.HEAPF32[f32(outPtr, OUT_WIND_WORLD) + 1],
      sfu.HEAPF32[f32(outPtr, OUT_WIND_WORLD) + 2],
    ];

    const penetrationM = REST_HEIGHT_M - body.position[1];
    let contactUpwardN = 0;
    if (penetrationM > 0) {
      contactUpwardN = FLOOR_STIFFNESS_N_PER_M * penetrationM
                     - FLOOR_DAMPING_NS_PER_M * body.velocityWorld[1];
      if (contactUpwardN < 0) contactUpwardN = 0;
    }

    const velocityBefore = body.velocityWorld.slice();
    const appliedWorld = [
      thrustWorld[0] + windWorld[0],
      thrustWorld[1] + windWorld[1] + contactUpwardN - GRAVITY_MS2 * MASS_KG,
      thrustWorld[2] + windWorld[2],
    ];
    for (let axis = 0; axis < 3; ++axis) {
      body.velocityWorld[axis] += (appliedWorld[axis] / MASS_KG) * TICK_SECONDS;
      body.position[axis] += body.velocityWorld[axis] * TICK_SECONDS;
    }

    // Rotation. The torque arrives in the body frame, so the angular
    // acceleration is computed there and rotated into the world frame, which is
    // where Unity reports the angular velocity. Euler's equation,
    // ω̇ = I⁻¹·(τ − ω × I·ω); the gyroscopic term matters because the three
    // inertias differ by a factor of two. This is what makes the gust a real
    // test of the torque's sign — without integrating the attitude, reversing
    // every torque component would go unnoticed.
    // 回転。トルクは機体系で来るので、角加速度を機体系で作ってから世界系へ回す。
    // Unity が角速度を報告するのは世界系だからである。オイラーの式
    // ω̇ = I⁻¹·(τ − ω × I·ω)。3 つの慣性が 2 倍ほど違うのでジャイロ項が効く。
    // 突風がトルクの符号の本当の試験になるのはこのためである ― 姿勢を積分しなければ、
    // トルクの全成分を反転させても気づけない。
    const omegaBody = rotateToBody(body.rotation, body.angularVelocityWorld);
    const angularMomentum = [
      omegaBody[0] * INERTIA_PITCH_KG_M2,
      omegaBody[1] * INERTIA_YAW_KG_M2,
      omegaBody[2] * INERTIA_ROLL_KG_M2,
    ];
    const gyroscopic = [
      omegaBody[1] * angularMomentum[2] - omegaBody[2] * angularMomentum[1],
      omegaBody[2] * angularMomentum[0] - omegaBody[0] * angularMomentum[2],
      omegaBody[0] * angularMomentum[1] - omegaBody[1] * angularMomentum[0],
    ];
    const omegaBodyNext = [
      omegaBody[0] + ((torqueLocal[0] - gyroscopic[0]) / INERTIA_PITCH_KG_M2) * TICK_SECONDS,
      omegaBody[1] + ((torqueLocal[1] - gyroscopic[1]) / INERTIA_YAW_KG_M2) * TICK_SECONDS,
      omegaBody[2] + ((torqueLocal[2] - gyroscopic[2]) / INERTIA_ROLL_KG_M2) * TICK_SECONDS,
    ];
    body.angularVelocityWorld = rotateToWorld(body.rotation, omegaBodyNext);

    // q̇ = ½·ω⊗q, then renormalise so it stays a unit quaternion.
    // q̇ = ½·ω⊗q。その後、単位クォータニオンに保つため正規化する。
    const w = body.angularVelocityWorld;
    const q = body.rotation;
    const half = 0.5 * TICK_SECONDS;
    const next = [
      q[0] + half * (w[0] * q[3] + w[1] * q[2] - w[2] * q[1]),
      q[1] + half * (w[1] * q[3] + w[2] * q[0] - w[0] * q[2]),
      q[2] + half * (w[2] * q[3] + w[0] * q[1] - w[1] * q[0]),
      q[3] - half * (w[0] * q[0] + w[1] * q[1] + w[2] * q[2]),
    ];
    const norm = Math.hypot(next[0], next[1], next[2], next[3]);
    body.rotation = norm > 1e-12 ? next.map((c) => c / norm) : q;

    const kinematicWorld = [
      (body.velocityWorld[0] - velocityBefore[0]) / TICK_SECONDS,
      (body.velocityWorld[1] - velocityBefore[1]) / TICK_SECONDS + GRAVITY_MS2,
      (body.velocityWorld[2] - velocityBefore[2]) / TICK_SECONDS,
    ];
    body.accelLocal = rotateToBody(body.rotation, kinematicWorld);

    lastAltitude = body.position[1];
    if (lastAltitude > maxAltitude) maxAltitude = lastAltitude;
    lastTiltDeg = tiltFromLevelDeg(body.rotation);
    if (lastTiltDeg > maxTiltDeg) maxTiltDeg = lastTiltDeg;
    lastFlightState = sfu.HEAP32[i32(outPtr, OUT_FLIGHT_STATE)];

    // N ticks of dt_us must land the clock on exactly N × dt_us.
    // dt_us の N 刻みは、時計をちょうど N × dt_us に置かなければならない。
    nowUs += TICK_US;
    ++ticks;
    const moduleNowUs = Number(sfu.HEAPU32[i32(outPtr, OUT_NOW_US)]) +
                        Number(sfu.HEAP32[i32(outPtr, OUT_NOW_US + 4)]) * 4294967296;
    if (moduleNowUs !== ticks * TICK_US) clockExact = false;

    // Drain the firmware's log once per simulated second — what a host does,
    // rather than once at the end.
    // ファームのログをシミュレーション 1 秒に 1 回取り出す。最後に 1 回ではなく、
    // ホストが行うのはこちらである。
    if (nowUs % 1000000 === 0) drainFirmware(sfu, recordPtr, log);
  }

  const wallSeconds = Number(process.hrtime.bigint() - wallStart) / 1e9;
  const flightState = sfu.HEAP32[i32(outPtr, OUT_FLIGHT_STATE)];
  const armed = sfu.HEAP32[i32(outPtr, OUT_ARMED)];
  const batteryVoltage = sfu.HEAPF32[f32(outPtr, OUT_BATTERY_VOLTAGE)];

  console.log(`[module_check] simulated ${simSeconds.toFixed(1)} s in ` +
              `${wallSeconds.toFixed(3)} s of wall clock`);
  console.log(`[module_check] REAL TIME PER SIMULATED SECOND = ` +
              `${(wallSeconds / simSeconds).toFixed(4)} s  (target <= 0.30)`);
  console.log(`[module_check] max altitude ${maxAltitude.toFixed(3)} m, ` +
              `final altitude ${lastAltitude.toFixed(3)} m, ` +
              `state ${flightState}, armed ${armed}, vbatt ${batteryVoltage.toFixed(2)}`);

  console.log(`[module_check] max tilt ${maxTiltDeg.toFixed(1)} deg, ` +
              `final tilt ${lastTiltDeg.toFixed(1)} deg`);
  console.log(`[module_check] clock exact over ${ticks} tick(s): ` +
              `${clockExact ? "OK" : "FAILED"}`);

  // Three conditions, all necessary. Altitude alone passes with every torque
  // component reversed; the tilt and the flight state are what do not.
  // 3 つの条件で、どれも欠かせない。高度だけではトルクの全成分を反転させても通って
  // しまう。通らなくするのが傾きと飛行状態である。
  const hovering = lastAltitude > HOVER_MIN_M && lastAltitude < HOVER_MAX_M;
  const level = lastTiltDeg < LEVEL_MAX_DEG;
  const flying = lastFlightState === FLIGHT_STATE_FLYING;
  const passed = hovering && level && flying && clockExact;
  console.log(`[module_check] hover ${passed ? "OK" : "FAILED"} ` +
              `(altitude ${hovering ? "OK" : "FAILED"}, ` +
              `level ${level ? "OK" : "FAILED"}, ` +
              `FLYING ${flying ? "OK" : "FAILED"})`);

  // Everything after the flight: dt_us's range, then the refusals that must
  // follow a shutdown. Each reads `status` rather than the return value.
  // 飛行の後のもの。dt_us の範囲、続いて終了後に返らなければならない拒否。
  // どれも戻り値ではなく `status` を読む。
  const dtUsOk = checkDtUs(sfu, inPtr, outPtr, i32);
  console.log(`[module_check] dt_us range checks ${dtUsOk ? "OK" : "FAILED"}`);

  const refusedAfterShutdown = checkAfterShutdown(sfu, inPtr, outPtr, i32);
  console.log(`[module_check] refusals after shutdown ` +
              `${refusedAfterShutdown ? "OK" : "FAILED"}`);

  drainFirmware(sfu, recordPtr, log);
  const dropped = sfu._sfu_log_dropped();
  if (dropped > 0) console.log(`[module_check] ${dropped} log record(s) dropped`);
  log.writeBridge("info", "bridge.shutdown", null,
                  passed ? "hover OK" : "hover FAILED");
  await log.close();
  if (log.isOpen) {
    console.error(`[module_check] log ${options.logPath} (run_id ${runId})`);
  }

  sfu._free(configPtr);
  sfu._free(inPtr);
  sfu._free(outPtr);
  sfu._free(recordPtr);
  process.exit((passed && dtUsOk && refusedAfterShutdown) ? 0 : 2);
}

main().catch((error) => fail(error.stack ?? String(error)));
