// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Kouhei Ito
//
// Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
// https://github.com/M5Fly-kanazawa/stampfly_ecosystem
//
// The one flight both JavaScript checks fly, and the struct layout it needs.
//
// `sfu_module_check.mjs` asks whether the shipped module is usable from outside
// C++ at all; `sfu_heap_layout_check.mjs` asks whether the same flight comes out
// the same when the heap is shifted before boot. Both need the identical
// take-off, gust and verdict, so it lives here once: if the two ever drifted
// apart, the heap check would stop covering the flight the module check blesses.
//
// 2 つの JavaScript の確認が共通して飛ばす飛行と、そのための構造体の配置。
//
// `sfu_module_check.mjs` が問うのは「モジュールが C++ の外から使えるか」であり、
// `sfu_heap_layout_check.mjs` が問うのは「起動前にヒープをずらしても同じ飛行が
// 出てくるか」である。どちらも同一の離陸・突風・判定を要るので、ここに 1 つだけ
// 置く。2 つが食い違えば、ヒープの確認が、モジュールの確認の認めた飛行を覆わなく
// なってしまうからである。
//
// @design docs/plans/unity-simulator.md §5 段階 2

// ---------------------------------------------------------------------------
// The struct layout, written out by hand exactly as the C# side will have to.
// `sfu_struct_size` lets a caller check each total against what the module
// compiled, which catches a field added or reordered on the C side.
// 構造体の配置。C# 側がそうせざるを得ないのと同じく、手で書き下ろしてある。
// `sfu_struct_size` により、各合計をモジュールのコンパイル結果と突き合わせられる
// ので、C 側で欄が増えたり並べ替えられたりすれば捕まる。
// ---------------------------------------------------------------------------
export const SFU_ABI_VERSION = 2;

export const STRUCT_CONFIG = 0;
export const STRUCT_STEP_IN = 1;
export const STRUCT_STEP_OUT = 2;
export const STRUCT_LOG_RECORD = 4;

export const SFU_OK = 0;
export const SFU_ERR_BAD_ARGUMENT = -9;
export const SFU_ERR_SHUT_DOWN = -10;

/// The cap on one step [µs], as sfu_api.h defines it.
/// 1 刻みの上限 [µs]。sfu_api.h の定義どおり。
export const DT_US_MAX = 100000;

// SfuConfig: struct_size, battery_model, ground_effect_gain, turbulence_n,
// thrust_efficiency, torque_authority, motor_delay_ms, noise_level, noise_seed,
// boot_calibration, host_owns_body, start_height_m — all 4 bytes each.
const CONFIG_FIELDS = 12;
export const CONFIG_SIZE = CONFIG_FIELDS * 4;
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
export const IN_DT_US = 92;     // uint32, whole microseconds
export const IN_SIZE = 96;

// SfuStepOut.
const OUT_STRUCT_SIZE = 0;
const OUT_FORCE_LOCAL = 4;      // 3 floats
const OUT_TORQUE_LOCAL = 16;    // 3 floats
const OUT_WIND_WORLD = 28;      // 3 floats
const OUT_WRENCH_DT = 40;
const OUT_MOTOR_DUTY = 44;      // 4 floats
const OUT_MOTOR_OMEGA = 60;     // 4 floats
export const OUT_BATTERY_VOLTAGE = 76;
// now_us is int64 and must be 8-byte aligned; 80 already is, so no padding.
const OUT_NOW_US = 80;
export const OUT_ARMED = 88;
export const OUT_FLIGHT_STATE = 92;
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
export const OUT_STATUS = 156;
export const OUT_SIZE = 168;         // 164 rounded up to the 8-byte alignment

// SfuLogRecord: struct_size, level, sim_us (int64, 8-aligned), tag, message.
export const LOG_STRUCT_SIZE = 0;
export const LOG_LEVEL = 4;
export const LOG_SIM_US = 8;
export const LOG_TAG = 16;           // 32 bytes
export const LOG_TAG_MAX = 32;
export const LOG_MESSAGE = 48;       // 224 bytes
export const LOG_MSG_MAX = 224;
export const LOG_SIZE = 272;

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

export const TICK_US = 2500;
const TICK_SECONDS = TICK_US * 1e-6;
export const REST_HEIGHT_M = 0.013;
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
export const HOVER_MIN_M = 0.15;
export const HOVER_MAX_M = 3.0;

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
export const LEVEL_MAX_DEG = 20.0;
export const FLIGHT_STATE_FLYING = 5;

function rcScriptAt(nowUs) {
  for (const entry of RC_SCRIPT) {
    if (nowUs < entry.tEndUs) return entry;
  }
  return RC_SCRIPT[RC_SCRIPT.length - 1];
}

/// Rotate a body-frame vector into the world frame with a unit quaternion
/// held in Unity's x,y,z,w order. v' = v + 2·q_w·(q_vec × v) + 2·q_vec × (q_vec × v).
/// 機体系のベクトルを、Unity の x,y,z,w の順に持った単位クォータニオンで世界系へ回す。
export function rotateToWorld(q, v) {
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

export function rotateToBody(q, v) {
  return rotateToWorld([-q[0], -q[1], -q[2], q[3]], v);
}

/// The angle between the vehicle's own up axis and the world's, in degrees.
/// Zero when level, 180 when inverted.
/// 機体自身の上方向と世界の上方向のなす角 [度]。水平で 0、反転で 180。
export function tiltFromLevelDeg(rotation) {
  const bodyUp = rotateToWorld(rotation, [0, 1, 0]);
  const cosine = Math.min(1, Math.max(-1, bodyUp[1]));
  return Math.acos(cosine) * (180 / Math.PI);
}

/// Where the four structs live in the module's heap, plus the two accessors
/// that turn a (pointer, offset) pair into a typed-array index.
/// 4 つの構造体がモジュールのヒープのどこに居るか、および (ポインタ, 変位) の組を
/// 型付き配列の添字へ直す 2 つの補助。
///
/// `preallocate` bytes are taken from the heap BEFORE anything else, which is
/// what `sfu_heap_layout_check.mjs` varies. Zero allocates nothing extra.
/// `preallocate` バイトを他の何より先にヒープから取る。
/// `sfu_heap_layout_check.mjs` が変えるのはこれである。0 なら余分に確保しない。
export function allocateStructs(sfu, preallocate = 0) {
  if (preallocate > 0) sfu._malloc(preallocate);

  const configPtr = sfu._malloc(CONFIG_SIZE);
  const inPtr = sfu._malloc(IN_SIZE);
  const outPtr = sfu._malloc(OUT_SIZE);
  const recordPtr = sfu._malloc(LOG_SIZE);
  sfu.HEAPU8.fill(0, configPtr, configPtr + CONFIG_SIZE);
  sfu.HEAPU8.fill(0, inPtr, inPtr + IN_SIZE);
  sfu.HEAPU8.fill(0, outPtr, outPtr + OUT_SIZE);
  sfu.HEAPU8.fill(0, recordPtr, recordPtr + LOG_SIZE);

  return {
    configPtr, inPtr, outPtr, recordPtr,
    i32: (ptr, offset) => ptr + offset >> 2,
    f32: (ptr, offset) => ptr + offset >> 2,
  };
}

/// Fill the config the two checks share and call `sfu_boot`. Returns the boot's
/// status, which the caller decides what to do about.
/// 2 つの確認が共有する config を詰めて `sfu_boot` を呼ぶ。起動の結果を返し、
/// それをどう扱うかは呼び出し側が決める。
export function boot(sfu, mem) {
  sfu.HEAP32[mem.i32(mem.configPtr, CONFIG_STRUCT_SIZE)] = CONFIG_SIZE;
  sfu.HEAP32[mem.i32(mem.configPtr, CONFIG_BATTERY_MODEL)] = 1;
  sfu.HEAP32[mem.i32(mem.configPtr, CONFIG_BOOT_CALIBRATION)] = 1;
  // 1 = the host owns the rigid body; this script integrates it below.
  // 1 = ホストが剛体を持つ。下でこのスクリプトが積分する。
  sfu.HEAP32[mem.i32(mem.configPtr, CONFIG_HOST_OWNS_BODY)] = 1;
  sfu.HEAPF32[mem.f32(mem.configPtr, CONFIG_START_HEIGHT)] = REST_HEIGHT_M;
  return sfu._sfu_boot(mem.configPtr);
}

/// One step, reading the result the way a wasm caller must: `status` out of the
/// struct, never the return value. Returns the status.
/// 1 刻み。結果は wasm の呼び出し側がそうせざるを得ない読み方 ― 戻り値ではなく
/// 構造体の `status` ― で読む。status を返す。
export function stepForStatus(sfu, mem, dtUs) {
  sfu.HEAP32[mem.i32(mem.inPtr, IN_DT_US)] = dtUs;
  sfu.HEAP32[mem.i32(mem.outPtr, OUT_STATUS)] = 1;   // a value the bridge never writes
  sfu._sfu_step(mem.inPtr, mem.outPtr);
  return sfu.HEAP32[mem.i32(mem.outPtr, OUT_STATUS)];
}

/// Fly `simSeconds` of the shared script on an already-booted module, with the
/// rigid body integrated here in JavaScript the way Unity's `SimLoop` will.
/// `onSecond(nowUs)` is called at every whole simulated second, which is where
/// `sfu_module_check.mjs` drains the firmware's log — a host does that once in a
/// while rather than once at the end. Returns the outcome as a record.
///
/// 既に起動したモジュールに対し、共有の台本を `simSeconds` ぶん飛ばす。剛体は
/// Unity の `SimLoop` がそうするのと同じく、ここ JavaScript の中で積分する。
/// `onSecond(nowUs)` はシミュレーション 1 秒ごとに呼ばれ、`sfu_module_check.mjs`
/// はそこでファームのログを取り出す（ホストが行うのは最後に 1 回ではなく時々で
/// ある）。結果を記録として返す。
export function fly(sfu, mem, simSeconds, onSecond = null) {
  sfu.HEAP32[mem.i32(mem.inPtr, IN_STRUCT_SIZE)] = IN_SIZE;
  sfu.HEAP32[mem.i32(mem.outPtr, OUT_STRUCT_SIZE)] = OUT_SIZE;
  sfu.HEAP32[mem.i32(mem.inPtr, IN_DT_US)] = TICK_US;

  const { inPtr, outPtr, i32, f32 } = mem;
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
  let failure = null;

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
    if (status !== SFU_OK) {
      failure = `sfu_step failed at t=${nowUs} us: status ${status}`;
      break;
    }

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

    if (onSecond !== null && nowUs % 1000000 === 0) onSecond(nowUs);
  }

  const wallSeconds = Number(process.hrtime.bigint() - wallStart) / 1e9;

  // Three conditions, all necessary. Altitude alone passes with every torque
  // component reversed; the tilt and the flight state are what do not.
  // 3 つの条件で、どれも欠かせない。高度だけではトルクの全成分を反転させても通って
  // しまう。通らなくするのが傾きと飛行状態である。
  const hovering = lastAltitude > HOVER_MIN_M && lastAltitude < HOVER_MAX_M;
  const level = lastTiltDeg < LEVEL_MAX_DEG;
  const flying = lastFlightState === FLIGHT_STATE_FLYING;

  return {
    failure,
    ticks, clockExact, wallSeconds,
    maxAltitude, lastAltitude, maxTiltDeg, lastTiltDeg, lastFlightState,
    hovering, level, flying,
    passed: failure === null && hovering && level && flying && clockExact,
  };
}
