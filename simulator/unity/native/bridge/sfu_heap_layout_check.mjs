// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Kouhei Ito
//
// Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
// https://github.com/M5Fly-kanazawa/stampfly_ecosystem
//
// Regression check: the flight the SHIPPED module produces must not depend on
// where the heap happens to sit. Flies `sfu_flight.mjs`'s script repeatedly,
// shifting the heap by one `_malloc(N)` before `sfu_boot`, and requires every
// run to agree exactly.
//
// 回帰試験: **出荷するモジュール**が出す飛行が、ヒープの位置で変わらないことを
// 確かめる。`sfu_boot` の前の `_malloc(N)` 1 回でヒープをずらしながら
// `sfu_flight.mjs` の台本を繰り返し飛ばし、全ての実行が完全に一致することを要求する。
//
// WHY this exists / なぜこの試験があるか
//
// The fiber scheduler hands each task a stack it allocated itself. std::malloc
// guarantees only max_align_t, which is 8 bytes under wasm32, while the C ABI
// wants a 16-byte-aligned stack pointer — and the compiler MASKS the stack
// pointer to place over-aligned locals rather than rounding it up. A stack that
// started 8-mod-16 therefore made two unrelated locals overlap inside the
// firmware's float math, which changed the accelerometer enough to trip the
// impact failsafe during take-off. Whether that happened was decided purely by
// the heap offset, so the same bytes flew differently depending on the length of
// the directory they sat in, or on a single allocation before the boot.
// scheduler_fiber.cpp now allocates those stacks with std::aligned_alloc(16, ...)
// (commit e4ed60cd); this check is what keeps that from regressing.
//
// `spike/heap_layout_check.mjs` asks the same question of the stage 1(a) spike
// module, which is where the cause was found. This one asks it of the module the
// Unity build actually loads, through the real `sfu_*` C ABI and the same flight
// `sfu_module_check.mjs` blesses — because Unity's `.jslib` has no choice but to
// allocate its structs before the boot, and only this module tells us whether
// that is safe.
//
// fiber 型スケジューラは各タスクに、自分で確保したスタックを渡す。std::malloc が
// 保証するのは max_align_t までで wasm32 では 8 バイトだが、C ABI が求めるのは
// 16 バイト整列のスタックポインタであり、コンパイラは 16 バイト超整列のローカルを
// 置くときスタックポインタを切り上げるのではなく「マスク」する。よって 8 mod 16 で
// 始まるスタックでは、ファームの浮動小数演算の中で無関係な 2 つのローカルが重なり、
// 加速度計の値が衝撃検出フェイルセーフを離陸中に誤作動させるほど変わった。それが
// 起きるかどうかはヒープのずれだけで決まるため、同じバイト列が、置いたディレクトリ
// 名の長さや起動前の 1 回の確保次第で違う飛び方をした。現在 scheduler_fiber.cpp は
// std::aligned_alloc(16, ...) でそのスタックを確保する（コミット e4ed60cd）。この
// 試験はその再発を防ぐためのものである。
//
// `spike/heap_layout_check.mjs` は同じ問いを段階 1(a) の技術検証のモジュールに対して
// 行う。原因が見つかったのはそちらである。こちらが問うのは、Unity のビルドが実際に
// 読み込むモジュールに対してであり、本物の `sfu_*` C ABI と、`sfu_module_check.mjs`
// が認めるのと同じ飛行を通す。Unity の `.jslib` は構造体を起動の前に確保せざるを
// 得ないので、それが安全かを教えてくれるのはこのモジュールだけだからである。
//
// Usage / 使い方:
//   node sfu_heap_layout_check.mjs <sfu_firmware.js> [simulated seconds]
//
// @design docs/plans/unity-simulator.md §5 段階 2

import { createRequire } from "node:module";
import { resolve } from "node:path";

import {
  SFU_OK, FLIGHT_STATE_FLYING, HOVER_MIN_M,
  allocateStructs, boot, fly,
} from "./sfu_flight.mjs";

// Heap shifts to try, in bytes, passed to _malloc() before sfu_boot(). The
// allocator's quantum is 16 bytes, so the offsets that used to split the result
// apart lie within any 16-byte window; the list walks a full window one byte at
// a time and then samples larger sizes, including the 48 bytes the .jslib and
// this script's own struct block allocate before the boot.
//
// 試すヒープのずれ [バイト]。sfu_boot() の前に _malloc() へ渡す。確保の刻みは
// 16 バイトなので、結果を分けていたずれは 16 バイトの窓の中に必ず現れる。下の一覧は
// 窓 1 つを 1 バイトずつ歩いてから、より大きな値を抜き取りで見る。48 は .jslib と
// このスクリプト自身の構造体の塊が起動前に確保する大きさである。
const SHIFTS = [
  0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
  48, 64, 128, 256, 512, 1024, 2048, 4096,
];

/// The peak a run must clear for the check to mean anything. Well below the
/// 0.68 m this flight reaches, and well above the 0.013 m it rests at.
/// この確認が意味を持つために、1 回の実行が超えていなければならない最大高度。
/// この飛行が届く 0.68 m よりずっと低く、静止時の 0.013 m よりずっと高い。
const MUST_HAVE_FLOWN_M = HOVER_MIN_M;

const modulePath = process.argv[2];
const seconds = parseFloat(process.argv[3] ?? "14");

if (!modulePath) {
  console.error("usage: node sfu_heap_layout_check.mjs <sfu_firmware.js> [seconds]");
  process.exit(2);
}

// The module is MODULARIZE'd CommonJS, so it is required, not imported.
// モジュールは MODULARIZE された CommonJS なので、import ではなく require する。
const require = createRequire(import.meta.url);
const createSfuFirmware = require(resolve(modulePath));

/**
 * Fly one freshly created module after shifting the heap by `shift` bytes.
 * Returns the outcome as a comparable record.
 * ヒープを `shift` バイトずらした上で、新しく作ったモジュールを 1 回飛ばす。
 * 結果を比較できる記録として返す。
 */
async function flyShifted(shift) {
  // A new module per run, not a re-boot: the firmware's statics must start from
  // the same place every time, or the shifts would not be the only difference.
  // 1 回ごとに新しいモジュールを作る（起動し直しではない）。ファームの静的変数が
  // 毎回同じところから始まらなければ、違いがずれだけにならないからである。
  const sfu = await createSfuFirmware({ print: () => {}, printErr: () => {} });

  // Shift the heap BEFORE the structs and BEFORE the boot, so every allocation
  // the firmware makes — including the 14 fiber stacks — lands at a different
  // offset.
  // 構造体より前、起動より前にヒープをずらす。ファームが行う確保（fiber スタック
  // 14 本を含む）がすべて違うずれに落ちるようにするため。
  const mem = allocateStructs(sfu, shift);

  const booted = boot(sfu, mem);
  if (booted !== SFU_OK) throw new Error(`sfu_boot failed at shift=${shift}: ${booted}`);

  const result = fly(sfu, mem, seconds);
  if (result.failure !== null) throw new Error(`shift=${shift}: ${result.failure}`);

  // Fixed decimals, not raw doubles: the check is that the FLIGHT agrees, and a
  // millimetre of print precision is far finer than the 0.2 m the defect moved
  // the peak altitude by. The tilt and the flight state are here for the same
  // reason the flight is judged on them — the defect's signature was a failsafe
  // DISARM, which shows in the state before it shows in the altitude.
  // 生の double ではなく固定小数で比べる。見たいのは「飛行が一致すること」で、
  // ミリメートルの表示精度は、この不具合が最大高度を動かした 0.2 m よりずっと細かい。
  // 傾きと飛行状態を入れてあるのは、飛行の判定がそれらを見るのと同じ理由による ―
  // この不具合の兆候はフェイルセーフによる DISARM であり、高度に出るより先に状態に出る。
  return {
    peak: result.maxAltitude.toFixed(3),
    final: result.lastAltitude.toFixed(3),
    tilt: result.lastTiltDeg.toFixed(1),
    state: result.lastFlightState,
  };
}

const baseline = await flyShifted(SHIFTS[0]);
const baselineKey = JSON.stringify(baseline);
let failures = 0;

for (const shift of SHIFTS) {
  const result = await flyShifted(shift);
  const same = JSON.stringify(result) === baselineKey;
  if (!same) failures++;
  console.log(
    `[heap_layout] shift=${String(shift).padStart(4)} ` +
    `peak=${result.peak} final=${result.final} ` +
    `tilt=${result.tilt} state=${result.state} ` +
    (same ? "same" : "DIFFERS"));
}

// The firmware must also actually have flown: a run that never left the ground
// would agree with itself trivially and hide a regression.
// ファームが実際に飛んだことも要る。一度も地面を離れない実行は自分自身とは自明に
// 一致してしまい、退行を隠すため。
const flew = parseFloat(baseline.peak) > MUST_HAVE_FLOWN_M &&
             baseline.state === FLIGHT_STATE_FLYING;

if (failures > 0) {
  console.log(`[heap_layout] FAILED — ${failures} of ${SHIFTS.length} runs differ ` +
              `from the baseline (peak ${baseline.peak} m, state ${baseline.state})`);
  console.log("[heap_layout] 不合格 — ヒープの位置で飛行結果が変わっている");
  process.exit(1);
}

if (!flew) {
  console.log(`[heap_layout] FAILED — every run agrees, but the craft never flew ` +
              `(peak ${baseline.peak} m, state ${baseline.state}); the check would ` +
              `pass vacuously`);
  console.log("[heap_layout] 不合格 — 全て一致したが機体が飛んでいない（無意味な合格）");
  process.exit(1);
}

console.log(`[heap_layout] OK — all ${SHIFTS.length} heap offsets agree ` +
            `(peak ${baseline.peak} m, final ${baseline.final} m, ` +
            `tilt ${baseline.tilt} deg, state ${baseline.state})`);
console.log(`[heap_layout] 合格 — ヒープのずれ ${SHIFTS.length} 通りが全て一致`);
