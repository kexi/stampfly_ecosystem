// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Kouhei Ito
//
// Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
// https://github.com/M5Fly-kanazawa/stampfly_ecosystem
//
// Regression check: the flight must not depend on what the HOST does BETWEEN
// ticks. `sfu_heap_layout_check.mjs` shifts the heap once BEFORE `sfu_boot`;
// this one allocates, frees, drains the log and reads status and parameters
// WHILE the vehicle is flying, at moments a seeded random number generator
// picks, and requires every seed to produce the same altitude series tick for
// tick.
//
// 回帰試験: 飛行は、刻みと刻みの**あいだ**にホストが何をするかに依存しては
// ならない。`sfu_heap_layout_check.mjs` は `sfu_boot` の**前**に 1 回ヒープを
// ずらすだけだが、こちらは**飛行中**に、種から決まる乱数が選ぶ時点で、確保・
// 解放・ログの取り出し・状態とパラメータの読み出しを行い、どの種でも高度の
// 時系列が刻み単位で一致することを求める。
//
// WHY this exists / なぜこの試験があるか
//
// In the browser the same build flew the same script differently from one run
// to the next: ALT_HOLD held about 0.40 m for five seconds and then the
// altitude came apart, ending in `Impact detected` and a failsafe DISARM. The
// editor (native dylib, thread scheduler) flew it identically every time. The
// difference between the two is not the firmware but the HOST: the browser
// drains the log, reads status and reads parameters on frame boundaries that
// land on different ticks every run, whereas the editor's check does not. An
// earlier defect of exactly that family — the flight decided by where the heap
// happened to sit (fiber stacks aligned to 8 instead of 16, commit e4ed60cd) —
// is why this had to be ruled out rather than argued about.
//
// ブラウザでは、同じビルドが同じ台本を実行ごとに違って飛ばした。ALT_HOLD が
// 約 0.40 m を 5 秒ほど保った後に高度が崩れ、`Impact detected` とフェイルセーフ
// による DISARM で終わる。エディタ（ネイティブの dylib、スレッド型スケジューラ）
// は毎回同じに飛ばす。2 つの違いはファームではなく**ホスト**にある。ブラウザは
// ログの取り出し・状態の読み出し・パラメータの読み出しを、実行ごとに違う刻みに
// 落ちるフレームの切れ目で行うが、エディタ側の確認はそれを行わない。まさに同種の
// 不具合 ― ヒープの位置で飛行が決まっていた件（fiber のスタックが 16 ではなく
// 8 で整列、コミット e4ed60cd） ― があったからこそ、議論ではなく試験で否定する
// 必要があった。
//
// Usage / 使い方:
//   node sfu_inflight_alloc_check.mjs <sfu_firmware.js> [seconds] [--seeds N]
//                                     [--kinds all|alloc|log|status|param|none]
//                                     [--fill 0x00|0xAA|0xFF]
//                                     [--noise <metres>]
//
// `--kinds` narrows the interleaved calls to one family, which is how a
// difference is bisected down to the call that causes it. `--fill` paints every
// allocation before freeing it, so an uninitialised read shows as a result that
// moves with the paint. `--noise` perturbs the host's own integrator instead,
// which answers a different question: whether ALT_HOLD's hold is so sensitive
// that floating-point differences between two PhysX builds would be enough.
//
// `--kinds` は挟む呼び出しを 1 系統に絞る。違いが出たとき、それを起こしている
// 呼び出しまで二分するための手立てである。`--fill` は解放の前に確保した領域を
// 塗るので、未初期化の読み出しがあれば結果が塗った値につれて動く。`--noise` は
// 代わりにホスト自身の積分器を乱す。こちらが答えるのは別の問い ― ALT_HOLD の
// 保持が、2 つの PhysX のビルドの浮動小数の違いだけで崩れるほど敏感かどうかである。
//
// @design docs/plans/unity-simulator.md §5 段階 2

import { createRequire } from "node:module";
import { resolve } from "node:path";

import {
  SFU_OK, FLIGHT_STATE_FLYING, HOVER_MIN_M, TICK_US,
  LOG_STRUCT_SIZE, LOG_SIZE,
  allocateStructs, boot, flyTraced,
} from "./sfu_flight.mjs";

/// The default sweep. Sixty-four seeds is a few minutes of wall clock and has
/// been enough to separate every layout-dependent defect seen so far; the
/// `--seeds` option raises it for a deeper hunt.
/// 既定の走査。種 64 通りで実時間は数分であり、これまでに見た配置依存の不具合は
/// すべてこれで分かれた。より深く探すときは `--seeds` で増やす。
const DEFAULT_SEEDS = 64;

/// How long to fly. The browser came apart between 11 and 13 simulated seconds,
/// so the default runs well past it and holds ALT_HOLD throughout.
/// 飛ばす長さ。ブラウザで崩れたのは仮想時刻 11〜13 秒なので、既定はそれを十分に
/// 越え、その間ずっと ALT_HOLD を保つ。
const DEFAULT_SECONDS = 25;

/// The largest block the interleaved allocator asks for [bytes]. Covers the
/// log record (272 B) the .jslib really allocates and a good deal more, so a
/// size-dependent effect has room to show.
/// 挟む確保が要求する最大の大きさ [バイト]。.jslib が実際に確保するログの記録
/// （272 B）を覆い、さらにその上まで取るので、大きさに依存する影響が出る余地がある。
const MAX_BLOCK_BYTES = 4096;

/// A parameter every build has, read to exercise `sfu_param_get`'s path through
/// the shared record buffer — the browser reads parameters through the very
/// bytes the log records pass through.
/// どのビルドにもあるパラメータ。`sfu_param_get` が共有の記録用領域を通る経路を
/// 動かすために読む。ブラウザはログの記録が通るまさにそのバイト列を経由して
/// パラメータを読むからである。
const PROBE_PARAM = "calibration.enable";

/// xorshift32: the same sequence for the same seed in every run of every
/// engine, which is what makes a differing seed reproducible.
/// xorshift32。どの実行・どの処理系でも同じ種から同じ列が出るので、食い違った種を
/// そのまま再現できる。
function makeRandom(seed) {
  let state = (seed | 0) || 0x9e3779b9;
  return function next() {
    state ^= state << 13; state |= 0;
    state ^= state >>> 17;
    state ^= state << 5; state |= 0;
    return (state >>> 0) / 4294967296;
  };
}

function parseOptions(argv) {
  const options = {
    seconds: DEFAULT_SECONDS, seeds: DEFAULT_SEEDS,
    kinds: "all", fill: null, noise: 0,
  };
  for (let i = 0; i < argv.length; ++i) {
    const arg = argv[i];
    const takesValue = arg === "--seeds" || arg === "--kinds" ||
                       arg === "--fill" || arg === "--noise";
    if (takesValue) {
      if (i + 1 >= argv.length) fail(`${arg} needs a value`);
      const value = argv[++i];
      if (arg === "--seeds") options.seeds = Number(value);
      else if (arg === "--kinds") options.kinds = value;
      else if (arg === "--fill") options.fill = Number(value);
      else options.noise = Number(value);
      continue;
    }
    if (arg.startsWith("-")) fail(`unknown option ${arg}`);
    options.seconds = Number(arg);
  }
  return options;
}

function fail(message) {
  console.error(`[inflight_alloc] ${message}`);
  process.exit(2);
}

/// Build the `betweenTicks` hook the flight calls after every tick. It is the
/// whole point of this check: everything a host does to a running module goes
/// in here, at moments the seed decides.
/// 飛行が各刻みの後に呼ぶ `betweenTicks` のフックを作る。この確認の本体である。
/// 動いているモジュールに対してホストが行うことは、種が決める時点で、すべて
/// ここを通る。
function makeInterleaver(sfu, mem, seed, options) {
  const random = makeRandom(seed);
  const live = [];
  const wants = (kind) => options.kinds === "all" || options.kinds === kind;
  const counts = { alloc: 0, free: 0, log: 0, status: 0, param: 0 };

  // A scratch pointer for sfu_param_get's double, taken once so the reads
  // themselves do not allocate. 8 bytes, the size of a double.
  // sfu_param_get の double を受ける作業用のポインタ。読み出し自体が確保しない
  // よう 1 回だけ取る。double の大きさ、8 バイトである。
  const valuePtr = sfu._malloc(8);
  const namePtr = sfu._malloc(64);
  // Written byte by byte rather than through `stringToUTF8`: that helper is not
  // among the module's exported runtime methods, and the name is ASCII anyway.
  // `stringToUTF8` ではなく 1 バイトずつ書く。この補助はモジュールが export する
  // 実行時メソッドに入っておらず、名前はいずれにせよ ASCII だからである。
  for (let i = 0; i < PROBE_PARAM.length; ++i) {
    sfu.HEAPU8[namePtr + i] = PROBE_PARAM.charCodeAt(i);
  }
  sfu.HEAPU8[namePtr + PROBE_PARAM.length] = 0;

  return {
    counts,
    /// One host interlude. Called after every tick; each family fires on its
    /// own probability, so the pattern differs by seed the way frame
    /// boundaries differ by run.
    /// ホストの合間 1 回。各刻みの後に呼ばれる。系統ごとに別々の確率で起きるので、
    /// フレームの切れ目が実行ごとに違うのと同じように、種ごとに型が変わる。
    run() {
      // Allocation and release, the .jslib's per-record AllocHGlobal/FreeHGlobal
      // seen from the firmware's side.
      // 確保と解放。.jslib が記録ごとに行う AllocHGlobal/FreeHGlobal を、ファーム
      // 側から見たものである。
      if (wants("alloc") && random() < 0.35) {
        const size = 1 + Math.floor(random() * MAX_BLOCK_BYTES);
        const pointer = sfu._malloc(size);
        if (pointer !== 0) {
          if (options.fill !== null) sfu.HEAPU8.fill(options.fill, pointer, pointer + size);
          live.push(pointer);
          counts.alloc++;
        }
      }
      const shouldFree = wants("alloc") && live.length > 0 && random() < 0.30;
      if (shouldFree) {
        const index = Math.floor(random() * live.length);
        sfu._free(live[index]);
        live.splice(index, 1);
        counts.free++;
      }

      // Drain the log the way a frame boundary does: read until the ring is
      // empty, not a fixed number of records.
      // フレームの切れ目と同じようにログを取り出す。決まった数ではなく、リングが
      // 空になるまで読む。
      if (wants("log") && random() < 0.25) {
        for (;;) {
          sfu.HEAP32[(mem.recordPtr + LOG_STRUCT_SIZE) >> 2] = LOG_SIZE;
          if (sfu._sfu_log_read_record(mem.recordPtr) !== 1) break;
          counts.log++;
        }
      }

      if (wants("status") && random() < 0.20) {
        sfu._sfu_last_status();
        sfu._sfu_log_dropped();
        counts.status++;
      }

      if (wants("param") && random() < 0.15) {
        sfu._sfu_param_get(namePtr, valuePtr);
        sfu._sfu_param_count();
        counts.param++;
      }
    },
    /// Give back whatever is still held, so one seed's leftovers cannot shift
    /// the next seed's heap. (Each seed gets a fresh module anyway; this keeps
    /// that true even if that ever changes.)
    /// まだ抱えているものを返す。1 つの種の残りが次の種のヒープをずらさないように
    /// するためである（種ごとに新しいモジュールを作るので今は不要だが、そこが
    /// 変わっても成り立つようにしておく）。
    release() {
      for (const pointer of live) sfu._free(pointer);
      live.length = 0;
      sfu._free(valuePtr);
      sfu._free(namePtr);
    },
  };
}

/// Fly one freshly created module with the interludes `seed` decides. Returns
/// the flight's outcome plus its altitude trace.
/// `seed` が決める合間を挟みながら、新しく作ったモジュールを 1 回飛ばす。
/// 飛行の結果と高度の軌跡を返す。
async function flySeeded(createSfuFirmware, seed, options) {
  const sfu = await createSfuFirmware({ print: () => {}, printErr: () => {} });
  const mem = allocateStructs(sfu);

  const booted = boot(sfu, mem);
  if (booted !== SFU_OK) throw new Error(`sfu_boot failed at seed=${seed}: ${booted}`);

  const interleaver = makeInterleaver(sfu, mem, seed, options);
  const noiseRandom = options.noise > 0 ? makeRandom(seed ^ 0x5bf03635) : null;

  const result = flyTraced(sfu, mem, options.seconds, {
    betweenTicks: () => interleaver.run(),
    // Symmetric noise on the host's own position, which is what a different
    // PhysX build would differ by. Zero by default, so the allocation sweep
    // measures allocation alone.
    // ホスト自身の位置に載せる対称な雑音。PhysX のビルドが違えば違ってくるのが
    // ここである。既定は 0 なので、確保の走査は確保だけを測る。
    positionNoise: noiseRandom === null ? null
      : () => (noiseRandom() * 2 - 1) * options.noise,
  });
  interleaver.release();

  if (result.failure !== null) throw new Error(`seed=${seed}: ${result.failure}`);
  return { result, counts: interleaver.counts };
}

/// The first tick at which two traces differ, and by how much. `-1` when they
/// are identical.
/// 2 つの軌跡が最初に食い違う刻みと、その差。同一なら `-1`。
function firstDifference(a, b) {
  const shared = Math.min(a.length, b.length);
  for (let tick = 0; tick < shared; ++tick) {
    if (a[tick] !== b[tick]) {
      return { tick, a: a[tick], b: b[tick] };
    }
  }
  if (a.length !== b.length) return { tick: shared, a: a.length, b: b.length };
  return null;
}

async function main() {
  const modulePath = process.argv[2];
  if (!modulePath) {
    fail("usage: node sfu_inflight_alloc_check.mjs <sfu_firmware.js> [seconds] " +
         "[--seeds N] [--kinds all|alloc|log|status|param|none] " +
         "[--fill N] [--noise M]");
  }
  const options = parseOptions(process.argv.slice(3));

  // The module is MODULARIZE'd CommonJS, so it is required, not imported.
  // モジュールは MODULARIZE された CommonJS なので、import ではなく require する。
  const require = createRequire(import.meta.url);
  const createSfuFirmware = require(resolve(modulePath));

  console.log(`[inflight_alloc] ${options.seeds} seed(s), ${options.seconds} s each, ` +
              `kinds=${options.kinds}` +
              (options.fill === null ? "" : `, fill=0x${options.fill.toString(16)}`) +
              (options.noise > 0 ? `, noise=${options.noise} m` : ""));

  const baseline = await flySeeded(createSfuFirmware, 0, options);
  const baseTrace = baseline.result.trace;

  let differing = 0;
  let firstReport = null;
  // The band the altitude occupies while ALT_HOLD is holding, measured per
  // seed: this is what the noise sweep reads, and it is worth printing even
  // when every seed agrees.
  // ALT_HOLD が保持している間に高度が占める幅を、種ごとに測る。雑音の走査が読むの
  // はこれであり、全ての種が一致する場合でも出す価値がある。
  let holdBandMin = Infinity;
  let holdBandMax = -Infinity;
  let collapsed = 0;

  for (let seed = 0; seed < options.seeds; ++seed) {
    const { result, counts } = seed === 0 ? baseline
                                          : await flySeeded(createSfuFirmware, seed, options);
    const difference = firstDifference(baseTrace, result.trace);
    if (difference !== null) differing++;
    if (difference !== null && firstReport === null) {
      firstReport = { seed, ...difference };
    }
    holdBandMin = Math.min(holdBandMin, result.holdBand);
    holdBandMax = Math.max(holdBandMax, result.holdBand);
    if (!result.flying || result.lastAltitude < HOVER_MIN_M) collapsed++;

    const verdict = difference === null ? "same"
      : `DIFFERS at tick ${difference.tick} (${difference.a} vs ${difference.b})`;
    console.log(
      `[inflight_alloc] seed=${String(seed).padStart(3)} ` +
      `peak=${result.maxAltitude.toFixed(4)} final=${result.lastAltitude.toFixed(4)} ` +
      `hold_band=${result.holdBand.toFixed(4)} state=${result.lastFlightState} ` +
      `calls=${counts.alloc}/${counts.free}/${counts.log}/${counts.status}/${counts.param} ` +
      verdict);
  }

  console.log(`[inflight_alloc] hold band over all seeds: ` +
              `${holdBandMin.toFixed(4)}..${holdBandMax.toFixed(4)} m, ` +
              `${collapsed} seed(s) lost the hold`);

  // The flight must have actually happened, or agreement is vacuous.
  // 飛行が実際に起きていなければ、一致しても意味が無い。
  const flew = baseline.result.maxAltitude > HOVER_MIN_M &&
               baseline.result.lastFlightState === FLIGHT_STATE_FLYING;
  if (!flew) {
    console.log(`[inflight_alloc] FAILED — the craft never flew ` +
                `(peak ${baseline.result.maxAltitude.toFixed(3)} m, ` +
                `state ${baseline.result.lastFlightState}); the check would pass vacuously`);
    console.log("[inflight_alloc] 不合格 — 機体が飛んでいない（無意味な合格）");
    process.exit(1);
  }

  if (differing > 0) {
    console.log(`[inflight_alloc] FAILED — ${differing} of ${options.seeds} seeds differ; ` +
                `first at seed ${firstReport.seed}, tick ${firstReport.tick} ` +
                `(${firstReport.tick * TICK_US * 1e-6} s)`);
    console.log("[inflight_alloc] 不合格 — 飛行中のホストの呼び出しで飛行結果が変わっている");
    process.exit(1);
  }

  console.log(`[inflight_alloc] OK — all ${options.seeds} seeds agree tick for tick ` +
              `over ${baseTrace.length} ticks ` +
              `(peak ${baseline.result.maxAltitude.toFixed(3)} m, ` +
              `state ${baseline.result.lastFlightState})`);
  console.log(`[inflight_alloc] 合格 — 飛行中のホストの呼び出し ${options.seeds} 通りが全て一致`);
}

main().catch((error) => fail(error.stack ?? String(error)));
