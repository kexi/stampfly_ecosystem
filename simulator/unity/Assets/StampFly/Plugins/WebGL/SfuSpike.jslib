/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — stage 1(b)(d) spike).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 *
 * The WebGL bridge to the firmware's own wasm module. The firmware is NOT
 * linked into the Unity player: it is a separate module created at run time by
 * createSfuFirmware(), which is the plan's approach 2. This file is the seam —
 * C# calls these functions through [DllImport("__Internal")], and they forward
 * to the firmware module's exports.
 *
 * ファーム自身の wasm モジュールへ渡る WebGL 側の橋。ファームは Unity の
 * プレイヤーにリンクしない。実行時に createSfuFirmware() が作る別モジュールに
 * する — これが計画の案 2。このファイルがその継ぎ目で、C# が
 * [DllImport("__Internal")] で呼び、ここからファームのモジュールの輸出関数へ
 * 転送する。
 *
 * Loading is asynchronous (the factory returns a Promise), so SfuSpikeLoad
 * starts it and SfuSpikeStatus reports progress; C# polls. Everything after
 * that — the per-tick Step — is a plain synchronous call, which is what stage
 * 1(b) set out to confirm.
 *
 * 読み込みは非同期（工場関数が Promise を返す）なので、SfuSpikeLoad が開始し
 * SfuSpikeStatus が進み具合を返す。C# はそれをポーリングする。それ以降、
 * 1 刻みごとの Step はただの同期呼び出しであり、これが段階 1(b) で確かめたい点。
 */

var SfuSpikeLibrary = {

  $SfuSpike: {
    // Status codes shared with SfuSpikeFirmware.cs. They live inside this
    // object because only the object and the functions are emitted into the
    // player; a plain top-level `var` in a .jslib is dropped.
    // SfuSpikeFirmware.cs と共有する状態コード。このオブジェクトの中に置くのは、
    // プレイヤーへ出るのがオブジェクトと関数だけで、.jslib の素の大域 `var` は
    // 落とされるため。
    STATUS_IDLE: 0,
    STATUS_LOADING: 1,
    STATUS_READY: 2,
    STATUS_FAILED: 3,

    module: null,
    status: 0,          // SfuSpike.STATUS_IDLE
    error: '',
    poseBuffer: 0,      // a 6-double scratch buffer inside the firmware module
    generation: 0,      // how many modules have been created (power cycles)
    calls: {},          // the firmware exports, resolved once when it is ready

    /**
     * Drop the current module so the browser can collect it. The firmware's
     * tasks hold static state that cannot be reset in place, so a power cycle
     * means throwing the whole module away and building a new one.
     * 現在のモジュールを捨て、ブラウザが回収できるようにする。ファームのタスクは
     * その場では戻せない静的変数を持つため、電源の入れ直しはモジュールごと
     * 捨てて作り直すことを意味する。
     */
    release: function () {
      SfuSpike.module = null;
      SfuSpike.calls = {};
      SfuSpike.poseBuffer = 0;
      SfuSpike.status = SfuSpike.STATUS_IDLE;
    },

    /**
     * Publish the bridge's own state on window, so a check in the browser can
     * see how far a load got and why it stopped.
     * この橋の状態を window に出す。ブラウザでの検証が、読み込みがどこまで進み
     * なぜ止まったかを見られるようにするため。
     */
    note: function (stage) {
      window.sfuSpikeState = {
        stage: stage,
        status: SfuSpike.status,
        error: SfuSpike.error,
        generation: SfuSpike.generation,
      };
    },
  },

  /**
   * Start creating a firmware module from the given URL. Returns the
   * generation number this load will carry, so the caller can tell one power
   * cycle from the next.
   * 指定の URL からファームのモジュールの生成を開始する。戻り値はこの読み込みに
   * 付く世代番号で、呼び出し側が電源の入れ直しを区別できるようにする。
   */
  SfuSpikeLoad__deps: ['$SfuSpike'],
  SfuSpikeLoad: function (urlPointer) {
    var url = UTF8ToString(urlPointer);

    SfuSpike.release();
    SfuSpike.status = SfuSpike.STATUS_LOADING;
    SfuSpike.error = '';
    SfuSpike.generation += 1;
    var generation = SfuSpike.generation;

    // The firmware's loader is an ordinary script that defines a global
    // factory. Loading it as a <script> keeps Unity's own module system out of
    // it, and a second load is harmless because the factory is replaced.
    // ファームのローダは大域の工場関数を定義するただのスクリプトである。
    // <script> として読み込めば Unity 自身のモジュール機構と干渉しない。
    // 2 回目の読み込みも工場関数が置き換わるだけなので害は無い。
    var startFactory = function () {
      SfuSpike.note('script loaded');
      if (typeof createSfuFirmware !== 'function') {
        SfuSpike.status = SfuSpike.STATUS_FAILED;
        SfuSpike.error = 'createSfuFirmware is not defined after loading ' + url;
        return;
      }

      createSfuFirmware({
        // The firmware logs heavily on stderr. Dropping both streams by default
        // is stage 1(d): nothing reaches the page, and nothing can open a
        // dialog. window.sfuSpikeVerbose = true turns them back on when needed.
        // ファームは stderr へ大量に記録する。既定で両方を捨てるのが段階 1(d)。
        // ページには何も届かず、ダイアログも開きようがない。必要なときは
        // window.sfuSpikeVerbose = true で元に戻す。
        print: function (text) {
          if (window.sfuSpikeVerbose) { console.log('[sfu] ' + text); }
        },
        printErr: function (text) {
          if (window.sfuSpikeVerbose) { console.warn('[sfu] ' + text); }
        },
        locateFile: function (path) {
          return url.replace(/[^/]*$/, '') + path;
        },
      }).then(function (module) {
        var isStale = generation !== SfuSpike.generation;
        if (isStale) { return; }

        SfuSpike.module = module;
        SfuSpike.calls = {
          boot: module.cwrap('sfu_spike_boot', 'number', []),
          step: module.cwrap('sfu_spike_step', 'number', []),
          stepUntil: module.cwrap('sfu_spike_step_until', 'number', ['number']),
          altitude: module.cwrap('sfu_spike_altitude', 'number', []),
          state: module.cwrap('sfu_spike_state', 'number', []),
          pose: module.cwrap('sfu_spike_pose', null, ['number']),
          battery: module.cwrap('sfu_spike_battery_volts', 'number', []),
        };

        // Six doubles, owned by the firmware module, reused every frame.
        // ファームのモジュールが持つ double 6 個。毎フレーム使い回す。
        var poseDoubles = 6;
        var bytesPerDouble = 8;
        SfuSpike.poseBuffer = module._malloc(poseDoubles * bytesPerDouble);

        SfuSpike.status = SfuSpike.STATUS_READY;
        SfuSpike.note('ready');
      }).catch(function (reason) {
        SfuSpike.status = SfuSpike.STATUS_FAILED;
        SfuSpike.error = String(reason);
        SfuSpike.note('factory failed');
      });
    };

    var script = document.createElement('script');
    script.src = url;
    script.onload = startFactory;
    script.onerror = function () {
      SfuSpike.status = SfuSpike.STATUS_FAILED;
      SfuSpike.error = 'failed to fetch ' + url;
    };
    document.head.appendChild(script);
    SfuSpike.note('script requested');

    return generation;
  },

  /** Current load status. / いまの読み込み状態。 */
  SfuSpikeStatus__deps: ['$SfuSpike'],
  SfuSpikeStatus: function () {
    return SfuSpike.status;
  },

  /** The last error text, as a string C# must free. / 直近の誤りの文。C# 側で解放する。 */
  SfuSpikeError__deps: ['$SfuSpike'],
  SfuSpikeError: function () {
    var text = SfuSpike.error || '';
    var size = lengthBytesUTF8(text) + 1;
    var pointer = _malloc(size);
    stringToUTF8(text, pointer, size);
    return pointer;
  },

  /** Power on the loaded firmware. Returns 1 on success. / 読み込んだファームを起動する。成功 1。 */
  SfuSpikeBoot__deps: ['$SfuSpike'],
  SfuSpikeBoot: function () {
    if (SfuSpike.status !== SfuSpike.STATUS_READY) { return 0; }
    return SfuSpike.calls.boot();
  },

  /**
   * Advance one 2.5 ms tick, synchronously. This is the call stage 1(b) exists
   * to prove: Unity's frame calls it several times in a row and gets values
   * back without ever yielding to the browser.
   * 2.5ms を 1 刻み、同期で進める。段階 1(b) が確かめたいのはこの呼び出し:
   * Unity の 1 フレームがこれを続けて数回呼び、ブラウザに制御を返さずに値を得る。
   */
  SfuSpikeStep__deps: ['$SfuSpike'],
  SfuSpikeStep: function () {
    if (SfuSpike.status !== SfuSpike.STATUS_READY) { return 0; }
    return SfuSpike.calls.step();
  },

  /** Advance to a virtual time in microseconds. / 仮想時刻[マイクロ秒]まで進める。 */
  SfuSpikeStepUntil__deps: ['$SfuSpike'],
  SfuSpikeStepUntil: function (targetMicroseconds) {
    if (SfuSpike.status !== SfuSpike.STATUS_READY) { return 0; }
    return SfuSpike.calls.stepUntil(targetMicroseconds);
  },

  /** Altitude above the floor [m]. / 床からの高度 [m]。 */
  SfuSpikeAltitude__deps: ['$SfuSpike'],
  SfuSpikeAltitude: function () {
    if (SfuSpike.status !== SfuSpike.STATUS_READY) { return 0; }
    return SfuSpike.calls.altitude();
  },

  /** Flight state as a number. / 飛行状態の番号。 */
  SfuSpikeState__deps: ['$SfuSpike'],
  SfuSpikeState: function () {
    if (SfuSpike.status !== SfuSpike.STATUS_READY) { return 0; }
    return SfuSpike.calls.state();
  },

  /** Battery voltage [V]. / 電池電圧 [V]。 */
  SfuSpikeBattery__deps: ['$SfuSpike'],
  SfuSpikeBattery: function () {
    if (SfuSpike.status !== SfuSpike.STATUS_READY) { return 0; }
    return SfuSpike.calls.battery();
  },

  /**
   * Copy the six pose doubles into Unity's own heap. The two modules have
   * separate memories, so the values are read from the firmware's heap and
   * written into Unity's — there is no shared buffer between them.
   * 位置姿勢の double 6 個を Unity 側のヒープへ写す。2 つのモジュールは別々の
   * メモリを持つため、ファーム側のヒープから読み、Unity 側へ書く。両者に
   * 共有の受け皿は無い。
   */
  SfuSpikePose__deps: ['$SfuSpike'],
  SfuSpikePose: function (destinationPointer) {
    if (SfuSpike.status !== SfuSpike.STATUS_READY) { return 0; }

    SfuSpike.calls.pose(SfuSpike.poseBuffer);

    var doubleCount = 6;
    var source = SfuSpike.module.HEAPF64;
    var sourceIndex = SfuSpike.poseBuffer >> 3;
    var destinationIndex = destinationPointer >> 3;
    for (var index = 0; index < doubleCount; index++) {
      HEAPF64[destinationIndex + index] = source[sourceIndex + index];
    }
    return 1;
  },

  /**
   * Bytes of WebAssembly memory the firmware module currently holds. Stage
   * 1(b) uses this to show that repeated power cycles do not grow without end.
   * ファームのモジュールがいま持つ WebAssembly メモリの大きさ [バイト]。
   * 電源の入れ直しを繰り返してもきりなく増えないことを段階 1(b) で示すのに使う。
   */
  SfuSpikeHeapBytes__deps: ['$SfuSpike'],
  SfuSpikeHeapBytes: function () {
    var hasModule = SfuSpike.module !== null && SfuSpike.module.HEAPU8;
    return hasModule ? SfuSpike.module.HEAPU8.length : 0;
  },

  /** How many modules have been created so far. / これまでに作ったモジュールの数。 */
  SfuSpikeGeneration__deps: ['$SfuSpike'],
  SfuSpikeGeneration: function () {
    return SfuSpike.generation;
  },

  /** Throw the current module away (a power cycle). / 現在のモジュールを捨てる（電源の入れ直し）。 */
  SfuSpikeRelease__deps: ['$SfuSpike'],
  SfuSpikeRelease: function () {
    SfuSpike.release();
  },

  /**
   * JavaScript heap in use, when the browser exposes it (Chrome does, behind
   * performance.memory). Zero means the browser did not offer the figure.
   * ブラウザが公開していれば、使用中の JavaScript ヒープ（Chrome は
   * performance.memory で公開する）。0 はブラウザが値を出さなかったことを表す。
   */
  SfuSpikeJsHeapBytes: function () {
    var hasMemory = typeof performance !== 'undefined' && performance.memory;
    return hasMemory ? performance.memory.usedJSHeapSize : 0;
  },
};

autoAddDeps(SfuSpikeLibrary, '$SfuSpike');
mergeInto(LibraryManager.library, SfuSpikeLibrary);
