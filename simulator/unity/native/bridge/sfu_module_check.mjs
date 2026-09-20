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
//
// @design docs/plans/unity-simulator.md §4 設計上の決定（スレッド）, §5 段階 2

import { createRequire } from "node:module";
import { resolve } from "node:path";

// ---------------------------------------------------------------------------
// The struct layout, written out by hand exactly as the C# side will have to.
// `sfu_struct_size` lets us check each total against what the module compiled,
// which catches a field added or reordered on the C side.
// 構造体の配置。C# 側がそうせざるを得ないのと同じく、手で書き下ろしてある。
// `sfu_struct_size` により、各合計をモジュールのコンパイル結果と突き合わせられる
// ので、C 側で欄が増えたり並べ替えられたりすれば捕まる。
// ---------------------------------------------------------------------------
const SFU_ABI_VERSION = 1;
const MOTOR_COUNT = 4;

const STRUCT_CONFIG = 0;
const STRUCT_STEP_IN = 1;
const STRUCT_STEP_OUT = 2;

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
const IN_DT_S = 92;
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
const OUT_SIZE = 160;                // 156 rounded up to the 8-byte alignment

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

const TICK_SECONDS = 0.0025;
const REST_HEIGHT_M = 0.013;
const MASS_KG = 0.037;
const GRAVITY_MS2 = 9.81;
const FLOOR_STIFFNESS_N_PER_M = 800.0;
const FLOOR_DAMPING_NS_PER_M = 3.0;
const HOVER_MIN_M = 0.15;
const HOVER_MAX_M = 3.0;

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

function fail(message) {
  console.error(`[module_check] ${message}`);
  process.exit(1);
}

async function main() {
  const modulePath = process.argv[2];
  if (!modulePath) fail("usage: node sfu_module_check.mjs <sfu_firmware.js> [seconds]");
  const simSeconds = process.argv[3] ? Number(process.argv[3]) : 30;

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
  ];
  for (const [which, expected, name] of sizes) {
    const actual = sfu._sfu_struct_size(which);
    if (actual !== expected) fail(`${name}: module says ${actual}, this script assumes ${expected}`);
  }
  console.log(`[module_check] ABI ${SFU_ABI_VERSION}, struct sizes agree ` +
              `(config ${CONFIG_SIZE}, in ${IN_SIZE}, out ${OUT_SIZE})`);

  // Lay the three structs out in the module's own heap.
  // 3 つの構造体をモジュール自身のヒープに並べる。
  const configPtr = sfu._malloc(CONFIG_SIZE);
  const inPtr = sfu._malloc(IN_SIZE);
  const outPtr = sfu._malloc(OUT_SIZE);
  sfu.HEAPU8.fill(0, configPtr, configPtr + CONFIG_SIZE);
  sfu.HEAPU8.fill(0, inPtr, inPtr + IN_SIZE);
  sfu.HEAPU8.fill(0, outPtr, outPtr + OUT_SIZE);

  const i32 = (ptr, offset) => ptr + offset >> 2;
  const f32 = (ptr, offset) => ptr + offset >> 2;

  sfu.HEAP32[i32(configPtr, CONFIG_STRUCT_SIZE)] = CONFIG_SIZE;
  sfu.HEAP32[i32(configPtr, CONFIG_BATTERY_MODEL)] = 1;
  sfu.HEAP32[i32(configPtr, CONFIG_BOOT_CALIBRATION)] = 1;
  sfu.HEAP32[i32(configPtr, CONFIG_HOST_OWNS_BODY)] = 1;
  sfu.HEAPF32[f32(configPtr, CONFIG_START_HEIGHT)] = REST_HEIGHT_M;

  const booted = sfu._sfu_boot(configPtr);
  if (booted !== 0) fail(`sfu_boot failed: ${booted}`);

  sfu.HEAP32[i32(inPtr, IN_STRUCT_SIZE)] = IN_SIZE;
  sfu.HEAP32[i32(outPtr, OUT_STRUCT_SIZE)] = OUT_SIZE;
  sfu.HEAPF32[f32(inPtr, IN_DT_S)] = TICK_SECONDS;

  const body = {
    position: [0, REST_HEIGHT_M, 0],
    rotation: [0, 0, 0, 1],
    velocityWorld: [0, 0, 0],
    accelLocal: [0, GRAVITY_MS2, 0],
  };

  const totalUs = Math.round(simSeconds * 1e6);
  const wallStart = process.hrtime.bigint();
  let nowUs = 0;
  let maxAltitude = 0;
  let lastAltitude = 0;

  while (nowUs < totalUs) {
    // Pack what the host has. The rotation stays identity for this check:
    // the flight is a vertical take-off and hold, and a JS attitude integrator
    // would add a second thing to be wrong about without testing more of the
    // ABI than this script is for.
    // ホストが持っている情報を詰める。この確認では回転は単位のままにする。飛行は
    // 鉛直の離陸と保持であり、JS 側で姿勢を積分しても、このスクリプトの目的である
    // ABI の確認が増えるわけではなく、間違えうる箇所が 1 つ増えるだけである。
    for (let axis = 0; axis < 3; ++axis) {
      sfu.HEAPF32[f32(inPtr, IN_POSITION) + axis] = body.position[axis];
      sfu.HEAPF32[f32(inPtr, IN_VELOCITY_WORLD) + axis] = body.velocityWorld[axis];
      sfu.HEAPF32[f32(inPtr, IN_ANGULAR_VELOCITY) + axis] = 0;
      sfu.HEAPF32[f32(inPtr, IN_ACCEL_LOCAL) + axis] = body.accelLocal[axis];
    }
    for (let component = 0; component < 4; ++component) {
      sfu.HEAPF32[f32(inPtr, IN_ROTATION) + component] = body.rotation[component];
    }
    sfu.HEAPF32[f32(inPtr, IN_RANGE_DOWN)] = body.position[1];
    sfu.HEAP32[i32(inPtr, IN_RANGE_VALID)] = 1;
    sfu.HEAPF32[f32(inPtr, IN_GROUND_HEIGHT)] = body.position[1];

    const rc = rcScriptAt(nowUs + Math.round(TICK_SECONDS * 1e6));
    const rcBase = inPtr + IN_RC_THROTTLE;
    sfu.HEAPU8[rcBase] = rc.throttle & 0xff;
    sfu.HEAPU8[rcBase + 1] = (rc.throttle >> 8) & 0xff;
    for (let stick = 1; stick < 4; ++stick) {
      sfu.HEAPU8[rcBase + stick * 2] = ADC_CENTRE & 0xff;
      sfu.HEAPU8[rcBase + stick * 2 + 1] = (ADC_CENTRE >> 8) & 0xff;
    }
    sfu.HEAPU8[inPtr + IN_RC_FLAGS] = rc.flags;

    const stepped = sfu._sfu_step(inPtr, outPtr);
    if (stepped !== 0) fail(`sfu_step failed at t=${nowUs} us: ${stepped}`);

    // Read what came back and integrate, as sfu_external_smoke.cpp does.
    // 返ってきたものを読んで積分する。sfu_external_smoke.cpp と同じ手順である。
    const forceLocal = [
      sfu.HEAPF32[f32(outPtr, OUT_FORCE_LOCAL)],
      sfu.HEAPF32[f32(outPtr, OUT_FORCE_LOCAL) + 1],
      sfu.HEAPF32[f32(outPtr, OUT_FORCE_LOCAL) + 2],
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

    const kinematicWorld = [
      (body.velocityWorld[0] - velocityBefore[0]) / TICK_SECONDS,
      (body.velocityWorld[1] - velocityBefore[1]) / TICK_SECONDS + GRAVITY_MS2,
      (body.velocityWorld[2] - velocityBefore[2]) / TICK_SECONDS,
    ];
    body.accelLocal = rotateToBody(body.rotation, kinematicWorld);

    lastAltitude = body.position[1];
    if (lastAltitude > maxAltitude) maxAltitude = lastAltitude;
    nowUs += Math.round(TICK_SECONDS * 1e6);
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

  const hovering = lastAltitude > HOVER_MIN_M && lastAltitude < HOVER_MAX_M;
  console.log(`[module_check] hover ${hovering ? "OK" : "FAILED"}`);

  sfu._free(configPtr);
  sfu._free(inPtr);
  sfu._free(outPtr);
  process.exit(hovering ? 0 : 2);
}

main().catch((error) => fail(error.stack ?? String(error)));
