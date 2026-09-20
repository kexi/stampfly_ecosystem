/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the WebGL firmware bridge).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 *
 * The seam between Unity's WebGL player and the firmware's OWN wasm module.
 * The firmware is not linked into the player: `createSfuFirmware()` builds a
 * separate module at run time (the plan's approach 2), which keeps Asyncify's
 * instrumentation off the Unity engine, needs no COOP/COEP headers, and makes a
 * power cycle a matter of calling the factory again.
 *
 * Unity の WebGL プレイヤーとファーム**自身**の wasm モジュールの継ぎ目。ファームは
 * プレイヤーにリンクしない。`createSfuFirmware()` が実行時に別のモジュールを作る
 * （計画の案 2）。こうすると Asyncify の計装が Unity のエンジンに掛からず、
 * COOP/COEP のヘッダも要らず、電源の入れ直しは工場関数をもう一度呼ぶだけで済む。
 *
 * The two modules have SEPARATE memories. Nothing is shared: every struct is
 * copied from Unity's heap into the firmware's before a call and back after it,
 * which is what the Copy* functions below do.
 *
 * 2 つのモジュールは**別々の**メモリを持つ。共有するものは無い。構造体は呼び出しの
 * 前に Unity のヒープからファームのヒープへ、後にその逆へ写す。下の Copy* の各関数が
 * 行うのはそれである。
 *
 * A .jslib must not declare a bare top-level `var`: only the `$`-prefixed object
 * and the functions are emitted into the player, and anything else is dropped.
 * All shared state therefore lives inside `$SfuFirmware`.
 *
 * .jslib で素の大域 `var` を宣言してはならない。プレイヤーへ出るのは `$` で始まる
 * オブジェクトと関数だけで、それ以外は落とされる。よって共有する状態はすべて
 * `$SfuFirmware` の中に置く。
 *
 * @design docs/plans/unity-simulator.md §4 設計上の決定（スレッド）
 * @design simulator/unity/native/bridge/sfu_api.h
 */

var SfuFirmwareLibrary = {

  $SfuFirmware: {
    // Status codes shared with FirmwareStatus in C#. They live inside this
    // object for the reason given in the header comment.
    // C# の FirmwareStatus と共有する状態コード。このオブジェクトの中に置く理由は
    // 冒頭の注記のとおり。
    STATUS_IDLE: 0,
    STATUS_LOADING: 1,
    STATUS_LOADED: 2,
    STATUS_RUNNING: 3,
    STATUS_SHUT_DOWN: 4,
    STATUS_FAILED: 5,

    module: null,
    status: 0,
    error: '',
    generation: 0,
    reportedUnwritten: false,

    // Scratch buffers inside the FIRMWARE's heap, allocated once per module and
    // reused every tick. Allocating per tick would churn that heap for no gain.
    // **ファーム側の**ヒープに置く作業用の領域。モジュールごとに 1 回だけ確保し、
    // 毎刻み使い回す。刻みごとに確保してもヒープをかき回すだけで得るものが無い。
    configBuffer: 0,
    inBuffer: 0,
    outBuffer: 0,
    recordBuffer: 0,
    nameBuffer: 0,
    paramBuffer: 0,

    // The longest parameter name the ABI reports, including the terminator.
    // Same value as SFU_PARAM_NAME_MAX.
    // ABI が報告するパラメータ名の最大長（終端を含む）。SFU_PARAM_NAME_MAX と同じ。
    NAME_BYTES: 64,

    /**
     * Copy `length` bytes out of Unity's heap into the firmware's.
     * Unity のヒープから `length` バイトをファーム側のヒープへ写す。
     */
    copyToFirmware: function (unityPointer, firmwarePointer, length) {
      SfuFirmware.module.HEAPU8.set(
        SfuFirmware.unityBytes().subarray(unityPointer, unityPointer + length),
        firmwarePointer);
    },

    /**
     * Copy `length` bytes out of the firmware's heap into Unity's.
     * ファーム側のヒープから `length` バイトを Unity のヒープへ写す。
     */
    copyToUnity: function (firmwarePointer, unityPointer, length) {
      SfuFirmware.unityBytes().set(
        SfuFirmware.module.HEAPU8.subarray(
          firmwarePointer, firmwarePointer + length), unityPointer);
    },

    /**
     * Unity's own heap as bytes, looked up at the moment of use.
     *
     * A `.jslib` function may not close over `HEAPU8`: when the player's memory
     * grows, every typed-array view over it is replaced, and a name captured
     * earlier goes on pointing at the detached one. Reading it through
     * `Module` (or from the global, whichever this build exposes) on each call
     * always gives the live view.
     *
     * Unity 自身のヒープをバイト列として、使う時点で引く。
     *
     * `.jslib` の関数は `HEAPU8` を閉じ込めてはならない。プレイヤーのメモリが
     * 伸びると、その上の型付き配列は全て作り直され、先に捕まえた名前は切り離された
     * 方を指し続ける。呼び出しのたびに `Module`（またはこのビルドが出す大域の方）
     * を通して読めば、常に生きている方が得られる。
     */
    unityBytes: function () {
      var fromModule = typeof Module !== 'undefined' && Module.HEAPU8;
      if (fromModule) { return Module.HEAPU8; }
      return HEAPU8;
    },

    /**
     * Drop the current module so the browser can collect it, and forget every
     * pointer into its heap — those addresses mean nothing once it is gone.
     * 現在のモジュールを捨ててブラウザが回収できるようにし、そのヒープを指す
     * ポインタを全て忘れる。モジュールが消えれば番地に意味は無い。
     */
    release: function () {
      SfuFirmware.module = null;
      SfuFirmware.configBuffer = 0;
      SfuFirmware.inBuffer = 0;
      SfuFirmware.outBuffer = 0;
      SfuFirmware.recordBuffer = 0;
      SfuFirmware.nameBuffer = 0;
      SfuFirmware.paramBuffer = 0;
      SfuFirmware.status = SfuFirmware.STATUS_IDLE;
      SfuFirmware.reportedUnwritten = false;
    },

    /** True while a call into the firmware is legal. / ファームを呼んでよい間だけ真。 */
    isUsable: function () {
      return SfuFirmware.module !== null &&
             (SfuFirmware.status === SfuFirmware.STATUS_LOADED ||
              SfuFirmware.status === SfuFirmware.STATUS_RUNNING);
    },

    /**
     * Publish the bridge's state on window, so a check running in the browser
     * can see how far a load got and why it stopped.
     * この橋の状態を window に出す。ブラウザで動く検証が、読み込みがどこまで進み
     * なぜ止まったかを見られるようにするため。
     */
    note: function (stage) {
      window.stampflyFirmwareState = {
        stage: stage,
        status: SfuFirmware.status,
        error: SfuFirmware.error,
        generation: SfuFirmware.generation,
      };
    },
  },

  /**
   * Start creating a firmware module from `url`. Returns the generation number
   * this load carries, so one power cycle can be told from the next.
   *
   * The previous module is dropped first. The <script> tag is added only once
   * per URL: the factory it defines is global and reusable, so re-adding the
   * tag on every power cycle would pile up elements for nothing.
   *
   * `url` からファームのモジュールの生成を開始する。戻り値はこの読み込みが持つ
   * 世代番号で、電源の入れ直しを区別できるようにする。
   *
   * 先に前のモジュールを捨てる。<script> のタグは URL ごとに 1 回だけ足す。
   * そこで定義される工場関数は大域で使い回せるので、電源の入れ直しのたびに足すと
   * 要素が無駄に積み上がるだけである。
   */
  SfuFirmwareLoad__deps: ['$SfuFirmware'],
  SfuFirmwareLoad: function (urlPointer) {
    var url = UTF8ToString(urlPointer);

    SfuFirmware.release();
    SfuFirmware.status = SfuFirmware.STATUS_LOADING;
    SfuFirmware.error = '';
    SfuFirmware.generation += 1;
    var generation = SfuFirmware.generation;

    var buildModule = function () {
      SfuFirmware.note('script loaded');
      if (typeof createSfuFirmware !== 'function') {
        SfuFirmware.status = SfuFirmware.STATUS_FAILED;
        SfuFirmware.error = 'createSfuFirmware is not defined after loading ' + url;
        SfuFirmware.note('factory missing');
        return;
      }

      createSfuFirmware({
        // The firmware writes about 190 lines to stderr while booting. Both
        // streams are dropped unless window.stampflyFirmwareVerbose is set, so
        // nothing floods the console and nothing can open a dialog.
        // ファームは起動中に約 190 行を stderr へ書く。
        // window.stampflyFirmwareVerbose を立てない限り両方の流れを捨てるので、
        // コンソールが溢れることもダイアログが開くこともない。
        print: function (text) {
          if (window.stampflyFirmwareVerbose) { console.log('[sfu] ' + text); }
        },
        printErr: function (text) {
          if (window.stampflyFirmwareVerbose) { console.warn('[sfu] ' + text); }
        },
        locateFile: function (path) {
          return url.replace(/[^/]*$/, '') + path;
        },
      }).then(function (module) {
        // A power cycle during the load makes this result stale: a newer load
        // has already taken the generation number.
        // 読み込み中に電源を入れ直すと、この結果は古くなる。より新しい読み込みが
        // すでに世代番号を取っているためである。
        var isStale = generation !== SfuFirmware.generation;
        if (isStale) { return; }

        SfuFirmware.module = module;
        SfuFirmware.nameBuffer = module._malloc(SfuFirmware.NAME_BYTES);
        SfuFirmware.status = SfuFirmware.STATUS_LOADED;
        SfuFirmware.note('loaded');
      }).catch(function (reason) {
        SfuFirmware.status = SfuFirmware.STATUS_FAILED;
        SfuFirmware.error = String(reason);
        SfuFirmware.note('factory failed');
      });
    };

    // The loader defines a global factory, so one <script> per URL suffices.
    // ローダは大域の工場関数を定義するので、URL ごとに <script> 1 つで足りる。
    if (typeof createSfuFirmware === 'function') {
      buildModule();
    } else {
      var script = document.createElement('script');
      script.src = url;
      script.onload = buildModule;
      script.onerror = function () {
        SfuFirmware.status = SfuFirmware.STATUS_FAILED;
        SfuFirmware.error = 'failed to fetch ' + url;
        SfuFirmware.note('script failed');
      };
      document.head.appendChild(script);
      SfuFirmware.note('script requested');
    }

    return generation;
  },

  /** Current status. / いまの状態。 */
  SfuFirmwareStatus__deps: ['$SfuFirmware'],
  SfuFirmwareStatus: function () {
    return SfuFirmware.status;
  },

  /** The last error text, as a string C# must free. / 直近の誤りの文。C# 側で解放する。 */
  SfuFirmwareError__deps: ['$SfuFirmware'],
  SfuFirmwareError: function () {
    var text = SfuFirmware.error || '';
    var size = lengthBytesUTF8(text) + 1;
    var pointer = _malloc(size);
    stringToUTF8(text, pointer, size);
    return pointer;
  },

  /** The module's ABI revision, or -1 when there is no module. / モジュールの ABI の版。無ければ -1。 */
  SfuFirmwareAbiVersion__deps: ['$SfuFirmware'],
  SfuFirmwareAbiVersion: function () {
    if (SfuFirmware.module === null) { return -1; }
    return SfuFirmware.module._sfu_abi_version();
  },

  /** The size the module compiled a struct to. / モジュールが構造体をした大きさ。 */
  SfuFirmwareStructSize__deps: ['$SfuFirmware'],
  SfuFirmwareStructSize: function (which) {
    if (SfuFirmware.module === null) { return -1; }
    return SfuFirmware.module._sfu_struct_size(which);
  },

  /**
   * Power on. The per-tick buffers are allocated here, once the sizes the
   * caller uses are known, and the config is copied across before the call.
   * 電源投入。刻みごとの作業領域は、呼び出し側が使う大きさが分かるここで 1 回だけ
   * 確保し、config は呼び出しの前に写す。
   */
  SfuFirmwareBoot__deps: ['$SfuFirmware'],
  SfuFirmwareBoot: function (configPointer, configSize, inSize, outSize, recordSize, paramSize) {
    if (!SfuFirmware.isUsable()) { return -2; }

    var module = SfuFirmware.module;
    SfuFirmware.configBuffer = module._malloc(configSize);
    SfuFirmware.inBuffer = module._malloc(inSize);
    SfuFirmware.outBuffer = module._malloc(outSize);
    SfuFirmware.recordBuffer = module._malloc(recordSize);
    SfuFirmware.paramBuffer = module._malloc(paramSize);

    SfuFirmware.copyToFirmware(configPointer, SfuFirmware.configBuffer, configSize);
    var status = module._sfu_boot(SfuFirmware.configBuffer);
    if (status === 0) {
      SfuFirmware.status = SfuFirmware.STATUS_RUNNING;
    } else {
      SfuFirmware.status = SfuFirmware.STATUS_FAILED;
      SfuFirmware.error = 'sfu_boot returned ' + status;
    }
    SfuFirmware.note('booted');
    return status;
  },

  /**
   * One tick. Copies the input across, calls `sfu_step`, copies the output
   * back, and returns the status read OUT OF THE STRUCT — under Asyncify the
   * value `_sfu_step` returns to JavaScript is the rewind stub's (0), not the
   * one the C code produced, because the fiber scheduler switches stacks inside
   * the call. See the head note of sfu_api.h.
   *
   * 1 刻み。入力を写し、`sfu_step` を呼び、出力を写し戻し、**構造体から**読んだ
   * status を返す。Asyncify のもとで `_sfu_step` が JavaScript へ返す値は、
   * C のコードが出したものではなく巻き直しの補助関数のもの（0）である。fiber 版
   * スケジューラが呼び出しの途中でスタックを切り替えるためである。sfu_api.h 冒頭の
   * 注記を参照。
   */
  SfuFirmwareStep__deps: ['$SfuFirmware'],
  SfuFirmwareStep: function (inPointer, inSize, outPointer, outSize, statusOffset) {
    if (!SfuFirmware.isUsable()) { return -2; }

    var module = SfuFirmware.module;
    SfuFirmware.copyToFirmware(inPointer, SfuFirmware.inBuffer, inSize);

    // The OUT struct is copied in as well, not just out. Its first field is the
    // caller's own `struct_size`, and the bridge refuses a call whose out struct
    // does not declare one — leaving this buffer as the module found it made
    // every tick fail with SFU_ERR_STRUCT_SIZE against a size of zero.
    // **出**の構造体も、取り出すだけでなく入れる。先頭の欄は呼び出し側自身の
    // `struct_size` で、橋渡しは出の構造体がそれを名乗らない呼び出しを拒む。この
    // 領域をモジュールが見つけたままにしておくと、どの刻みも大きさ 0 に対する
    // SFU_ERR_STRUCT_SIZE で失敗した。
    SfuFirmware.copyToFirmware(outPointer, SfuFirmware.outBuffer, outSize);

    // A value the bridge never writes, so "the bridge wrote it" and "nothing
    // happened" stay distinguishable.
    // 橋渡しが決して書かない値を入れておく。「橋渡しが書いた」と「何も起きなかった」
    // を見分けられるようにするため。
    module.HEAP32[(SfuFirmware.outBuffer + statusOffset) >> 2] = 1;
    module._sfu_step(SfuFirmware.inBuffer, SfuFirmware.outBuffer);

    var status = module.HEAP32[(SfuFirmware.outBuffer + statusOffset) >> 2];

    // The first tick that comes back holding the sentinel says the module never
    // wrote its result, which means the bytes did not arrive where the module
    // reads them. Report it once with what was actually in the two buffers,
    // rather than letting every later tick repeat a bare code.
    // 目印を持ったまま返ってきた最初の刻みは、モジュールが結果を書かなかったこと、
    // つまりバイトがモジュールの読む場所へ届かなかったことを表す。以後の刻みが
    // 素の値を繰り返すに任せず、2 つの領域に実際に何が入っていたかを添えて 1 回
    // だけ報告する。
    var isUnwritten = status === 1 && !SfuFirmware.reportedUnwritten;
    if (isUnwritten) {
      SfuFirmware.reportedUnwritten = true;
      console.error('[sfu] sfu_step left the status untouched. ' +
        'in.struct_size=' + module.HEAP32[SfuFirmware.inBuffer >> 2] +
        ' in.dt_us=' + module.HEAPU32[(SfuFirmware.inBuffer + 92) >> 2] +
        ' out.struct_size=' + module.HEAP32[SfuFirmware.outBuffer >> 2] +
        ' last_status=' + module._sfu_last_status() +
        ' sizes=' + inSize + '/' + outSize + ' statusOffset=' + statusOffset);
    }

    SfuFirmware.copyToUnity(SfuFirmware.outBuffer, outPointer, outSize);
    return status;
  },

  /** How many tuning parameters the firmware exposes. / ファームが公開する調整値の数。 */
  SfuFirmwareParamCount__deps: ['$SfuFirmware'],
  SfuFirmwareParamCount: function () {
    if (!SfuFirmware.isUsable()) { return -2; }
    return SfuFirmware.module._sfu_param_count();
  },

  /** One parameter's identity, copied back into Unity's heap. / パラメータ 1 個の素性を Unity のヒープへ写して返す。 */
  SfuFirmwareParamInfo__deps: ['$SfuFirmware'],
  SfuFirmwareParamInfo: function (index, outPointer, outSize) {
    if (!SfuFirmware.isUsable()) { return -2; }

    var module = SfuFirmware.module;
    module.HEAP32[SfuFirmware.paramBuffer >> 2] = outSize;
    var status = module._sfu_param_info(index, SfuFirmware.paramBuffer);
    if (status === 0) {
      SfuFirmware.copyToUnity(SfuFirmware.paramBuffer, outPointer, outSize);
    }
    return status;
  },

  /** Set a parameter by name. / 名前でパラメータを設定する。 */
  SfuFirmwareParamSet__deps: ['$SfuFirmware'],
  SfuFirmwareParamSet: function (namePointer, value) {
    if (!SfuFirmware.isUsable()) { return -2; }

    var module = SfuFirmware.module;
    var name = UTF8ToString(namePointer);
    module.stringToUTF8(name, SfuFirmware.nameBuffer, SfuFirmware.NAME_BYTES);
    return module._sfu_param_set(SfuFirmware.nameBuffer, value);
  },

  /**
   * Read a parameter by name. The value comes back through the 8 bytes at the
   * head of the record buffer, which is idle between log drains.
   * 名前でパラメータを読む。値は記録用の領域の先頭 8 バイトを経由して返る。
   * この領域はログの取り出しの合間は空いている。
   */
  SfuFirmwareParamGet__deps: ['$SfuFirmware'],
  SfuFirmwareParamGet: function (namePointer, valuePointer) {
    if (!SfuFirmware.isUsable()) { return -2; }

    var module = SfuFirmware.module;
    var name = UTF8ToString(namePointer);
    module.stringToUTF8(name, SfuFirmware.nameBuffer, SfuFirmware.NAME_BYTES);

    var status = module._sfu_param_get(SfuFirmware.nameBuffer, SfuFirmware.recordBuffer);
    if (status === 0) {
      // Through the live view, for the reason given on `unityBytes`.
      // `unityBytes` に書いた理由により、生きている方を通して書く。
      var unityDoubles = new Float64Array(
        SfuFirmware.unityBytes().buffer, valuePointer, 1);
      unityDoubles[0] = module.HEAPF64[SfuFirmware.recordBuffer >> 3];
    }
    return status;
  },

  /** Constant wind force [N] in the Unity world frame. / Unity の世界系での一定の風力 [N]。 */
  SfuFirmwareSetWind__deps: ['$SfuFirmware'],
  SfuFirmwareSetWind: function (worldX, worldY, worldZ) {
    if (!SfuFirmware.isUsable()) { return -2; }
    return SfuFirmware.module._sfu_set_wind(worldX, worldY, worldZ);
  },

  /** Per-motor thrust health. / モータごとの推力の健全度。 */
  SfuFirmwareSetMotorHealth__deps: ['$SfuFirmware'],
  SfuFirmwareSetMotorHealth: function (motor, gain) {
    if (!SfuFirmware.isUsable()) { return -2; }
    return SfuFirmware.module._sfu_set_motor_health(motor, gain);
  },

  /** Constant raw IMU bias, body-local Unity frame. / 一定の生 IMU バイアス。Unity の機体系。 */
  SfuFirmwareSetImuBias__deps: ['$SfuFirmware'],
  SfuFirmwareSetImuBias: function (accelX, accelY, accelZ, gyroX, gyroY, gyroZ) {
    if (!SfuFirmware.isUsable()) { return -2; }
    return SfuFirmware.module._sfu_set_imu_bias(accelX, accelY, accelZ,
                                                gyroX, gyroY, gyroZ);
  },

  /** Take one log record out of the ring. 1 = written, 0 = empty. / リングから記録を 1 つ取り出す。1 = 書けた、0 = 空。 */
  SfuFirmwareLogRead__deps: ['$SfuFirmware'],
  SfuFirmwareLogRead: function (outPointer, outSize) {
    if (!SfuFirmware.isUsable()) { return -2; }

    var module = SfuFirmware.module;
    module.HEAP32[SfuFirmware.recordBuffer >> 2] = outSize;
    var result = module._sfu_log_read_record(SfuFirmware.recordBuffer);
    if (result === 1) {
      SfuFirmware.copyToUnity(SfuFirmware.recordBuffer, outPointer, outSize);
    }
    return result;
  },

  /** Records dropped because the ring was full. / リングが満杯で捨てられた記録の数。 */
  SfuFirmwareLogDropped__deps: ['$SfuFirmware'],
  SfuFirmwareLogDropped: function () {
    if (!SfuFirmware.isUsable()) { return 0; }
    return SfuFirmware.module._sfu_log_dropped();
  },

  /** The lowest level kept from here on; returns the previous. / これ以降に残す最も低い段。直前の段を返す。 */
  SfuFirmwareSetLogLevel__deps: ['$SfuFirmware'],
  SfuFirmwareSetLogLevel: function (level) {
    if (!SfuFirmware.isUsable()) { return -2; }
    return SfuFirmware.module._sfu_set_log_level(level);
  },

  /**
   * Throw the current module away. This IS the power cycle: the firmware's
   * statics cannot be reset in place, so the module is discarded and the
   * caller loads a new one.
   * 現在のモジュールを捨てる。これが電源の入れ直しそのものである。ファームの
   * 静的変数はその場では戻せないので、モジュールごと捨てて呼び出し側が新しく
   * 読み込む。
   */
  SfuFirmwareRelease__deps: ['$SfuFirmware'],
  SfuFirmwareRelease: function () {
    SfuFirmware.release();
    SfuFirmware.note('released');
  },

  /** WebAssembly memory the firmware module holds [bytes]. / ファームのモジュールが持つ WebAssembly メモリ [バイト]。 */
  SfuFirmwareHeapBytes__deps: ['$SfuFirmware'],
  SfuFirmwareHeapBytes: function () {
    var hasModule = SfuFirmware.module !== null && SfuFirmware.module.HEAPU8;
    return hasModule ? SfuFirmware.module.HEAPU8.length : 0;
  },

  /** How many modules have been created so far. / これまでに作ったモジュールの数。 */
  SfuFirmwareGeneration__deps: ['$SfuFirmware'],
  SfuFirmwareGeneration: function () {
    return SfuFirmware.generation;
  },
};

autoAddDeps(SfuFirmwareLibrary, '$SfuFirmware');
mergeInto(LibraryManager.library, SfuFirmwareLibrary);
