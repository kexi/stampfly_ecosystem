/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 1 spike).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file heap_layout_check.mjs
 * @brief Regression check: the flight result must NOT depend on where the heap
 *        happens to sit. Flies the same module repeatedly, shifting the heap by
 *        _malloc(N) before boot, and requires every run to agree exactly.
 *        回帰試験: 飛行結果がヒープの位置で変わらないことを確かめる。起動前に
 *        _malloc(N) でヒープをずらしながら同じモジュールを繰り返し飛ばし、
 *        全ての実行が完全に一致することを要求する。
 *
 * WHY this exists / なぜこの試験があるか
 *
 * The fiber scheduler hands each task a stack it allocated itself. std::malloc
 * guarantees only max_align_t, which is 8 bytes under wasm32, while the C ABI
 * wants a 16-byte-aligned stack pointer — and the compiler MASKS the stack
 * pointer to place over-aligned locals rather than rounding it up. A stack that
 * started 8-mod-16 therefore made two unrelated locals overlap inside the
 * firmware's float math, which changed the accelerometer enough to trip the
 * impact failsafe during take-off. Whether that happened was decided purely by
 * the heap offset, so the same bytes flew differently depending on the length of
 * the directory they sat in. scheduler_fiber.cpp now allocates those stacks with
 * std::aligned_alloc(16, ...); this check is what keeps that from regressing.
 *
 * fiber 型スケジューラは各タスクに、自分で確保したスタックを渡す。std::malloc が
 * 保証するのは max_align_t までで wasm32 では 8 バイトだが、C ABI が求めるのは
 * 16 バイト整列のスタックポインタであり、コンパイラは 16 バイト超整列のローカルを
 * 置くときスタックポインタを切り上げるのではなく「マスク」する。よって 8 mod 16 で
 * 始まるスタックでは、ファームの浮動小数演算の中で無関係な 2 つのローカルが重なり、
 * 加速度計の値が衝撃検出フェイルセーフを離陸中に誤作動させるほど変わった。それが
 * 起きるかどうかはヒープのずれだけで決まるため、同じバイト列が、置いたディレクトリ
 * 名の長さ次第で違う飛び方をした。現在 scheduler_fiber.cpp は std::aligned_alloc(16,
 * ...) でそのスタックを確保する。この試験はその再発を防ぐためのものである。
 *
 * Usage / 使い方:
 *   node heap_layout_check.mjs <sfu_firmware.js> [seconds]
 *
 * @design docs/plans/unity-simulator.md — 段階 1(a) 技術検証
 */

import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

// Heap shifts to try, in bytes, passed to _malloc() before sfu_spike_boot().
// The allocator's quantum is 16 bytes, so the offsets that used to split the
// result apart lie within any 16-byte window; the list walks a full window one
// byte at a time and then samples larger sizes, including the 48 bytes Unity's
// .jslib allocates before boot (which is how this reached the Unity build).
//
// 試すヒープのずれ [バイト]。sfu_spike_boot() の前に _malloc() へ渡す。確保の
// 刻みは 16 バイトなので、結果を分けていたずれは 16 バイトの窓の中に必ず現れる。
// 下の一覧は窓 1 つを 1 バイトずつ歩いてから、より大きな値を抜き取りで見る。
// 48 は Unity の .jslib が起動前に確保する大きさで、これが Unity 版まで届いた経路。
const SHIFTS = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
    17, 20, 24, 28, 32, 40, 48, 64, 96, 128, 256, 512, 1024, 2048, 4096,
];

const modulePath = process.argv[2];
const seconds = parseFloat(process.argv[3] ?? '10');

if (!modulePath) {
    console.error('usage: node heap_layout_check.mjs <sfu_firmware.js> [seconds]');
    process.exit(2);
}

const createSfuFirmware =
    (await import(pathToFileURL(resolve(modulePath)).href)).default;

/**
 * Fly one freshly created module after shifting the heap by `shift` bytes.
 * Returns the outcome as a comparable record.
 * ヒープを `shift` バイトずらした上で、新しく作ったモジュールを 1 回飛ばす。
 * 結果を比較できる記録として返す。
 */
async function fly(shift) {
    const mod = await createSfuFirmware({ print: () => {}, printErr: () => {} });

    // Shift the heap BEFORE boot, so every allocation the firmware makes —
    // including the 14 fiber stacks — lands at a different offset.
    // 起動の前にヒープをずらす。ファームが行う確保（fiber スタック 14 本を含む）が
    // すべて違うずれに落ちるようにするため。
    if (shift > 0) mod._malloc(shift);

    const boot = mod.cwrap('sfu_spike_boot', 'number', []);
    const step = mod.cwrap('sfu_spike_step', 'number', []);
    const altitude = mod.cwrap('sfu_spike_altitude', 'number', []);
    const state = mod.cwrap('sfu_spike_state', 'number', []);

    if (!boot()) throw new Error(`boot failed at shift=${shift}`);

    const ticks = Math.round(seconds * 400);
    let peak = 0;
    for (let i = 0; i < ticks; i++) {
        step();
        const a = altitude();
        if (a > peak) peak = a;
    }

    // Fixed decimals, not raw doubles: the check is that the FLIGHT agrees, and
    // a millimetre of print precision is far finer than the 0.2 m the defect
    // moved the peak altitude by.
    // 生の double ではなく固定小数で比べる。見たいのは「飛行が一致すること」で、
    // ミリメートルの表示精度は、この不具合が最大高度を動かした 0.2 m よりずっと細かい。
    return {
        peak: peak.toFixed(3),
        final: altitude().toFixed(3),
        state: state(),
    };
}

const baseline = await fly(SHIFTS[0]);
const baselineKey = JSON.stringify(baseline);
let failures = 0;

for (const shift of SHIFTS) {
    const result = await fly(shift);
    const same = JSON.stringify(result) === baselineKey;
    if (!same) failures++;
    console.log(
        `[heap_layout] shift=${String(shift).padStart(4)} ` +
        `peak=${result.peak} final=${result.final} state=${result.state} ` +
        (same ? 'same' : 'DIFFERS'));
}

// The firmware must also actually have flown: a run that never left the ground
// would agree with itself trivially and hide a regression.
// ファームが実際に飛んだことも要る。一度も地面を離れない実行は自分自身とは自明に
// 一致してしまい、退行を隠すため。
const flew = parseFloat(baseline.peak) > 0.5 && baseline.state === 5;

if (failures > 0) {
    console.log(`[heap_layout] FAILED — ${failures} of ${SHIFTS.length} runs differ ` +
                `from the baseline (peak ${baseline.peak} m, state ${baseline.state})`);
    console.log('[heap_layout] 不合格 — ヒープの位置で飛行結果が変わっている');
    process.exit(1);
}

if (!flew) {
    console.log(`[heap_layout] FAILED — every run agrees, but the craft never flew ` +
                `(peak ${baseline.peak} m, state ${baseline.state}); the check would ` +
                `pass vacuously`);
    console.log('[heap_layout] 不合格 — 全て一致したが機体が飛んでいない（無意味な合格）');
    process.exit(1);
}

console.log(`[heap_layout] OK — all ${SHIFTS.length} heap offsets agree ` +
            `(peak ${baseline.peak} m, final ${baseline.final} m, state ${baseline.state})`);
console.log(`[heap_layout] 合格 — ヒープのずれ ${SHIFTS.length} 通りが全て一致`);
