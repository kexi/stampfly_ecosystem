/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file sfu_api.h
 * @brief The C ABI Unity calls. One module is one power-on of one vehicle.
 *        Unity が呼ぶ C ABI。1 モジュールが 1 機体の 1 回の電源投入にあたる。
 *
 * Everything crossing this boundary is in Unity's own conventions — left-handed,
 * Y up, quaternion in x,y,z,w order, metres, seconds, radians per second. The
 * conversion to the firmware's NED/FRD happens on the C++ side, in
 * `simulator/sils/frames/frames_unity.hpp` and nowhere else, so the C# side
 * never learns a second frame convention.
 *
 * この境界を渡るものはすべて Unity 自身の規約 ― 左手系・Y 上・クォータニオンは
 * x,y,z,w の順・単位は m と秒と rad/s ― で書いてある。ファームの NED／FRD への
 * 変換は C++ 側の `simulator/sils/frames/frames_unity.hpp` だけで行うので、C# 側は
 * 2 つ目の座標規約を覚えずに済む。
 *
 * ## The call order / 呼ぶ順序
 *
 *   sfu_abi_version()        — check it equals SFU_ABI_VERSION before anything else
 *   sfu_boot(&config)        — once per module; a second call is an error
 *   sfu_param_set_* ...      — optional, any time after boot
 *   loop: sfu_step(&in, &out)
 *   sfu_shutdown()           — after it, every other entry point is an error
 *
 * ## Reading sfu_step's result under WebAssembly / wasm での sfu_step の結果
 *
 * **Under wasm the RETURN VALUE of `sfu_step` is meaningless — read
 * `out->status` (or `sfu_last_status()`) instead.** The fiber scheduler switches
 * stacks inside the call, and Asyncify implements that by unwinding and
 * rewinding the whole wasm stack. The value JavaScript receives is the one the
 * rewind stub produced (0), not the one the C code returned. `out->status` is
 * written to memory as the LAST thing `sfu_step` does, so it survives.
 * A native caller may use either; a caller that must work in both places uses
 * `status`. The same applies to any entry point that can reach the scheduler.
 *
 * **wasm では `sfu_step` の戻り値は意味を持たない。`out->status`（または
 * `sfu_last_status()`）を読むこと。** fiber 版スケジューラは呼び出しの途中で
 * スタックを切り替え、Asyncify はそれを wasm スタック全体の巻き戻しと巻き直しで
 * 実現する。JavaScript が受け取るのは巻き直しの際に生じた値（0）であって、C の
 * コードが返した値ではない。`out->status` は `sfu_step` が**最後に**記憶域へ書く
 * ものなので残る。ネイティブの呼び出し側はどちらを読んでもよいが、両方で動く
 * 必要のある呼び出し側は `status` を読む。スケジューラへ届き得る他の入口にも
 * 同じことが当てはまる。
 *
 * ## Why every struct starts with struct_size / 先頭に struct_size を置く理由
 *
 * The C# side declares these structs itself and hands the bridge a raw pointer,
 * so the two declarations must agree. `struct_size` lets the bridge reject a
 * mismatch loudly instead of reading past the end of a shorter struct, and lets
 * a later version grow a struct at the end without breaking an older caller.
 * The caller writes `sizeof` of ITS OWN declaration; the bridge compares.
 *
 * C# 側はこれらの構造体を自分で宣言し、生のポインタを渡す。よって 2 つの宣言は
 * 一致していなければならない。`struct_size` があれば、短い構造体の末尾を越えて
 * 読む代わりに、食い違いをその場で拒否できる。また後の版が構造体を末尾に伸ばし
 * ても、古い呼び出し側が壊れない。呼び出し側は自分の宣言の `sizeof` を書き、
 * 橋渡し側がそれと比べる。
 *
 * @design docs/plans/unity-simulator.md §3 1 刻みの処理, §4 設計上の決定
 */

#ifndef SFU_API_H
#define SFU_API_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * The ABI revision. Bumped whenever a struct's existing fields move or change
 * meaning; growing a struct at the end and raising its struct_size does not.
 * ABI の版。既存の欄が動くか意味が変わるたびに上げる。構造体を末尾に伸ばして
 * struct_size を上げるだけなら上げない。
 */
#define SFU_ABI_VERSION 2

/** Number of motors. Fixed by the airframe. モータの数。機体で決まる。 */
#define SFU_MOTOR_COUNT 4

/** Longest parameter name the bridge reports, including the terminator.
 *  橋渡しが報告するパラメータ名の最大長（終端を含む）。 */
#define SFU_PARAM_NAME_MAX 64

/** Longest single step [µs] = 100 ms. A host that has fallen this far behind
 *  should slow the simulation down, not hand the firmware a huge catch-up tick.
 *  1 刻みの上限 [µs] = 100 ms。これほど遅れたホストは、ファームへ巨大な追いつき
 *  刻みを渡すのではなく、シミュレーションを遅らせるべきである。 */
#define SFU_DT_US_MAX 100000u

/** The plant's physics sub-step [µs]. A dt_us that is a multiple of this
 *  divides evenly into sub-steps and leaves no remainder to carry over.
 *  プラントの物理の副刻み [µs]。これの倍数の dt_us は副刻みに割り切れ、繰り越す
 *  端数が出ない。 */
#define SFU_PLANT_SUBSTEP_US 250u

/* Return codes. 0 is success; every failure is negative so a caller can test
 * `< 0` without knowing the list.
 * 戻り値。0 が成功で、失敗は全て負にしてある。呼び出し側が一覧を知らなくても
 * `< 0` で判定できるようにするため。 */
#define SFU_OK                    0
#define SFU_ERR_ALREADY_BOOTED   -1   /**< sfu_boot called twice in one module */
#define SFU_ERR_NOT_BOOTED       -2   /**< called before sfu_boot succeeded */
#define SFU_ERR_STRUCT_SIZE      -3   /**< struct_size does not match this build */
#define SFU_ERR_NULL_ARGUMENT    -4   /**< a required pointer was null */
#define SFU_ERR_SCHEDULER_STALL  -5   /**< a firmware task did not yield */
#define SFU_ERR_UNKNOWN_PARAM    -6   /**< no parameter of that name */
#define SFU_ERR_PARAM_REJECTED   -7   /**< the value was out of the allowed range */
#define SFU_ERR_BAD_INDEX        -8   /**< index outside the valid range */
#define SFU_ERR_BAD_ARGUMENT     -9   /**< an argument was outside its allowed range */
#define SFU_ERR_SHUT_DOWN       -10   /**< called after sfu_shutdown; the module is finished */

/* SFU_ERR_SCHEDULER_STALL is LATCHED: a task that did not yield leaves the
 * firmware's stacks in an unknown state, so the module refuses to run further
 * and every later sfu_step returns the same code without touching the scheduler.
 * SFU_ERR_SCHEDULER_STALL は**保持される**。トークンを返さなかったタスクは
 * ファームのスタックを不明な状態に残すので、モジュールはそれ以上動かず、以後の
 * sfu_step はスケジューラに触れずに同じ値を返す。 */

/* Parameter value types, matching sf::params::ParamType one for one.
 * パラメータの型。sf::params::ParamType と 1 対 1 で対応する。 */
#define SFU_PARAM_FLOAT 0
#define SFU_PARAM_BOOL  1
#define SFU_PARAM_INT   2

/**
 * The settings that must be in place before the firmware's tasks first run.
 * ファームのタスクが最初に動き出す前に決まっていなければならない設定。
 *
 * These are the knobs `emu_main.cpp` read from environment variables. A browser
 * has no environment, and the values here are read during boot (the IMU task's
 * setup reads `calibration.enable`, the plant's config is fixed by init), so
 * they cannot be left to `sfu_param_set_*` after the fact.
 * これらは `emu_main.cpp` が環境変数から読んでいたノブである。ブラウザに環境変数は
 * 無く、また値は起動中に読まれる（IMU タスクの setup が `calibration.enable` を
 * 読み、プラントの設定は init で固まる）ため、後から `sfu_param_set_*` で渡すこと
 * はできない。
 */
typedef struct SfuConfig {
    uint32_t struct_size;        /**< = sizeof(SfuConfig) in the CALLER's build */

    /* --- Plant physics / プラントの物理 --- */
    int32_t  battery_model;      /**< 1 = model sag and discharge, 0 = ideal supply */
    float    ground_effect_gain; /**< near-floor extra lift; 0 disables it */
    float    turbulence_n;       /**< 1–3 Hz lateral turbulence force [N]; 0 disables */
    float    thrust_efficiency;  /**< real-vs-ideal thrust scale; <= 0 keeps the default */
    float    torque_authority;   /**< roll/pitch differential-torque scale; <= 0 keeps the default */
    float    motor_delay_ms;     /**< duty-path transport delay [ms]; 0 disables */

    /* --- Sensor noise / センサノイズ --- */
    int32_t  noise_level;        /**< 0 = off, 1 = n0, 2 = n1, 3 = n2 (as SILS_EMU_NOISE) */
    uint32_t noise_seed;         /**< random seed; used only when noise_level > 0 */

    /* --- Firmware behaviour / ファームの挙動 --- */
    int32_t  boot_calibration;   /**< 1 = the firmware calibrates at boot (as on the real vehicle) */

    /* --- Who owns the rigid body / 剛体を誰が持つか --- */
    /* 1 = Unity: every sfu_step injects the host's state and the plant only
     *     accumulates forces. This is the Unity build.
     * 0 = the plant: its own simple 6-DOF integrator runs, the state fields of
     *     SfuStepIn are ignored, and the state fields of SfuStepOut report where
     *     the plant put the vehicle. This is what sfu_bridge_smoke flies with,
     *     and it lets the bridge be exercised end to end without Unity.
     * 1 = Unity 側: sfu_step のたびにホストの状態を注入し、プラントは力を溜める
     *     だけになる。Unity 版はこちら。
     * 0 = プラント側: 自前の簡易 6 自由度の積分器が動き、SfuStepIn の状態の欄は
     *     読まれず、SfuStepOut の状態の欄がプラントの置いた機体の位置を返す。
     *     sfu_bridge_smoke はこちらで飛ばす。Unity 無しで橋渡しを端から端まで
     *     動かせるようにするためである。 */
    int32_t  host_owns_body;

    /* --- Starting pose / 初期姿勢 --- */
    float    start_height_m;     /**< resting height above the floor [m], Unity +Y */
} SfuConfig;

/**
 * Everything the host has at the start of one tick.
 * ホストが 1 刻みの始めに持っている情報すべて。
 *
 * `accel_local` is the accelerometer READING, not the kinematic acceleration:
 * the host computes R⁻¹·((v_after − v_before)/dt − g) after its own physics step
 * and passes it on the NEXT tick. Contact forces are therefore already in it,
 * and a body resting on the floor reads +9.81 on Unity's +Y, as the real
 * vehicle's accelerometer does.
 * `accel_local` は運動としての加速度ではなく加速度計の測定値である。ホストは自分の
 * 物理を 1 刻み進めた後に R⁻¹·((v 後 − v 前)/dt − g) を計算し、次の刻みで渡す。
 * よって接触力はすでに入っており、床に静止した機体は実機の加速度計と同じく
 * Unity の +Y に +9.81 を読む。
 */
typedef struct SfuStepIn {
    uint32_t struct_size;        /**< = sizeof(SfuStepIn) in the CALLER's build */

    /* --- Rigid-body state, Unity conventions / 剛体の状態、Unity の規約 --- */
    float position[3];           /**< Rigidbody.position [m] */
    float rotation[4];           /**< Rigidbody.rotation, x,y,z,w order */
    float velocity_world[3];     /**< Rigidbody.linearVelocity [m/s], world frame */
    float angular_velocity_world[3]; /**< Rigidbody.angularVelocity [rad/s], WORLD frame */
    float accel_local[3];        /**< accelerometer reading [m/s²], body-local frame */

    /* --- Raycast results / レイキャストの結果 --- */
    float range_down_m;          /**< downward ToF distance [m] along the beam */
    int32_t range_down_valid;    /**< 1 = the beam hit a surface within range */
    float ground_height_m;       /**< height above the surface below the rotors [m] */

    /* --- Latest stick values / スティックの最新値 --- */
    /* The host only stores these; the bridge injects them into ESP-NOW on the
     * firmware's own 50 Hz virtual-time cadence, so a long host tick never
     * queues up a burst of packets.
     * ホストはこれを置くだけで、橋渡しがファーム自身の 50 Hz の仮想時間の周期で
     * ESP-NOW へ注入する。よって長い刻みがパケットの束を作ることはない。 */
    uint16_t rc_throttle;        /**< raw 12-bit ADC, centre 2048 */
    uint16_t rc_roll;
    uint16_t rc_pitch;
    uint16_t rc_yaw;
    uint8_t  rc_flags;           /**< ARM / mode bits, as the ControlPacket carries them */
    uint8_t  reserved_padding[3];/**< keeps the next field 4-byte aligned in both languages */

    /* --- How far to advance / どれだけ進めるか --- */
    /* Whole MICROSECONDS, not seconds: the virtual clock is an integer count of
     * microseconds, so a float crossing the boundary would have to be rounded
     * here and the caller could never predict exactly where. With an integer,
     * N ticks of dt_us land the clock on exactly N·dt_us, every time, in every
     * language. Must be in (0, SFU_DT_US_MAX]; anything else is
     * SFU_ERR_BAD_ARGUMENT. A value that is not a multiple of
     * SFU_PLANT_SUBSTEP_US is accepted and advances the clock by exactly that
     * many microseconds — the plant carries its sub-step remainder over, so the
     * remainder appears in the NEXT tick's wrench_dt_s rather than being lost.
     *
     * 秒ではなく整数の**マイクロ秒**である。仮想時計はマイクロ秒の整数の数え上げ
     * なので、境界を渡る浮動小数はここで丸めるほかなく、呼び出し側はその結果を
     * 正確に予測できない。整数であれば、dt_us の N 刻みは必ず、どの言語からでも
     * ちょうど N·dt_us に時計を置く。範囲は (0, SFU_DT_US_MAX]。外れれば
     * SFU_ERR_BAD_ARGUMENT を返す。SFU_PLANT_SUBSTEP_US の倍数でない値も受理し、
     * 時計はちょうどその値だけ進む — プラントは副刻みの端数を繰り越すので、端数は
     * 失われるのではなく**次の**刻みの wrench_dt_s に現れる。 */
    uint32_t dt_us;
} SfuStepIn;

/**
 * Everything the host needs after one tick.
 * ホストが 1 刻みの後に要する情報すべて。
 *
 * `force_local` and `torque_local` are the interval averages of what the rotors
 * produced, so they go straight into `AddRelativeForce` and `AddRelativeTorque`.
 * `wind_force_world` is world-frame and goes into `AddForce`.
 * `force_local`・`torque_local` はロータが出したものの区間平均なので、そのまま
 * `AddRelativeForce`・`AddRelativeTorque` に渡せる。`wind_force_world` は世界系
 * なので `AddForce` に渡す。
 */
typedef struct SfuStepOut {
    uint32_t struct_size;        /**< = sizeof(SfuStepOut) in the CALLER's build */

    /* --- What to apply to the Rigidbody / Rigidbody に加えるもの --- */
    /* All four are zero when host_owns_body = 0: the plant applied the forces to
     * its own body itself, so there is nothing left for a host to apply.
     * host_owns_body = 0 のときはこの 4 つとも 0 になる。プラントが自分の剛体に
     * 力を加えてしまっており、ホストが加えるものは残っていないためである。 */
    float force_local[3];        /**< actuator force [N], body-local Unity frame */
    float torque_local[3];       /**< actuator torque [N·m], body-local Unity frame */
    float wind_force_world[3];   /**< wind + turbulence force [N], world Unity frame */
    float wrench_dt_s;           /**< the interval those averages cover [s] */

    /* --- Actuator state, for the propeller animation and the HUD --- */
    /* --- アクチュエータの状態。プロペラの描画と HUD 用 --- */
    float motor_duty[SFU_MOTOR_COUNT];   /**< commanded duty [0..1], M1..M4 */
    float motor_omega[SFU_MOTOR_COUNT];  /**< propeller speed [rad/s], M1..M4 */
    float battery_voltage;               /**< terminal voltage [V] */

    /* --- Firmware state, for the HUD / ファームの状態。HUD 用 --- */
    int64_t now_us;              /**< the virtual clock after this step [µs] */
    int32_t armed;               /**< 1 = motors armed */
    int32_t flight_state;        /**< sf::FlightState as an integer */
    int32_t flight_mode;         /**< sf::FlightMode as an integer */
    float estimated_rotation[4]; /**< the firmware's attitude estimate, Unity x,y,z,w */
    float estimated_position[3]; /**< the firmware's position estimate [m], Unity world */

    /* --- Where the vehicle actually is / 機体が実際にいる場所 --- */
    /* With host_owns_body = 0 this is the plant's own integrated pose, and it is
     * the only place to read it. With host_owns_body = 1 it is what the host
     * injected on this tick, echoed back — the host already knows it.
     * host_owns_body = 0 ではプラントが積分した姿勢で、これを読めるのはここだけ
     * である。host_owns_body = 1 では、この刻みでホストが注入したものをそのまま
     * 返す ― ホストはすでにその値を持っている。 */
    float truth_position[3];     /**< true position [m], Unity world */
    float truth_rotation[4];     /**< true attitude, Unity x,y,z,w */

    /* --- The result of this call / この呼び出しの結果 --- */
    /* SFU_OK or a negative code — the same value sfu_step returns natively.
     * Written as the LAST thing sfu_step does, on every path including the
     * early rejections, so it is the one place a wasm caller can read the
     * result from (see the note at the head of this file). Zero it before the
     * call to tell "the bridge wrote it" from "nothing happened".
     * SFU_OK か負の値 — sfu_step がネイティブで返すのと同じ値である。早期の拒否も
     * 含めた全ての経路で、sfu_step が**最後に**書く。wasm の呼び出し側が結果を
     * 読めるのはここだけである（本ファイル冒頭の注記を参照）。呼び出し前に 0 を
     * 入れておくと「橋渡しが書いた」と「何も起きなかった」を見分けられる。 */
    int32_t status;
    /* The struct contains an int64_t (now_us), so its alignment is 8 and its
     * size is rounded up to a multiple of 8. Naming the pad rather than letting
     * the compiler insert it keeps the C# declaration a field-for-field copy of
     * this one, with no invisible bytes to get wrong.
     * この構造体は int64_t（now_us）を含むので配置境界は 8 で、大きさは 8 の倍数へ
     * 切り上がる。詰め物をコンパイラに入れさせず名前を付けておくと、C# 側の宣言を
     * この宣言の欄ごとの写しにできる。見えない byte を取り違える余地が無くなる。 */
    int32_t reserved_tail;
} SfuStepOut;

/** One parameter's identity, as `sfu_param_info` reports it.
 *  パラメータ 1 個の素性。`sfu_param_info` が返すもの。 */
typedef struct SfuParamInfo {
    uint32_t struct_size;        /**< = sizeof(SfuParamInfo) in the CALLER's build */
    char     name[SFU_PARAM_NAME_MAX];  /**< null-terminated parameter name */
    int32_t  type;               /**< SFU_PARAM_FLOAT / _BOOL / _INT */
    float    default_value;
    float    min_value;
    float    max_value;
} SfuParamInfo;

/* =========================================================================
 * Lifetime / 生存期間
 * ========================================================================= */

/** The ABI revision this module was built against. 本モジュールが従う ABI の版。 */
int32_t sfu_abi_version(void);

/* Which struct sfu_struct_size asks about. sfu_struct_size が尋ねる構造体。 */
#define SFU_STRUCT_CONFIG     0
#define SFU_STRUCT_STEP_IN    1
#define SFU_STRUCT_STEP_OUT   2
#define SFU_STRUCT_PARAM_INFO 3

/**
 * The size this module compiled a struct to, so a caller that declares the
 * struct in another language can check its own `sizeof` against it before
 * sending a single pointer across. Returns a negative code for an unknown id.
 * 本モジュールがある構造体をどの大きさにコンパイルしたかを返す。別の言語で同じ
 * 構造体を宣言した呼び出し側が、ポインタを 1 つも渡す前に自分の `sizeof` と
 * 突き合わせられるようにするため。該当しない id には負の値を返す。
 */
int32_t sfu_struct_size(int32_t which);

/**
 * Power on: bring up the plant, seed the pairing store, and run the firmware's
 * own `app_main` (BSP init plus all 14 tasks). Returns SFU_OK or a negative code.
 * 電源投入: プラントを起こし、ペアリングの記録を置き、ファーム自身の `app_main`
 * （BSP 初期化と 14 タスク）を実行する。SFU_OK か負の値を返す。
 *
 * One module is one power-on: a second call returns SFU_ERR_ALREADY_BOOTED,
 * because the firmware's tasks hold static state that cannot be returned to its
 * initial value without editing the firmware. A power cycle means a new module.
 * 1 モジュール＝1 回の電源投入である。2 回目の呼び出しは
 * SFU_ERR_ALREADY_BOOTED を返す。ファームのタスクは静的な状態を持ち、ファームを
 * 編集せずに初期値へ戻せないためである。電源の入れ直しはモジュールの作り直しを指す。
 */
int32_t sfu_boot(const SfuConfig* config);

/**
 * Unwind the firmware's tasks and free their stacks. Optional — discarding the
 * module is enough — but after it the module is FINISHED: `sfu_step`, every
 * `sfu_set_*` and a second `sfu_shutdown` all return SFU_ERR_SHUT_DOWN without
 * touching anything, because the fiber stacks they would run on have been freed.
 * ファームのタスクを巻き戻し、スタックを解放する。省略可 — モジュールを捨てる
 * だけでも足りる — が、この後モジュールは**終了**している。`sfu_step`・全ての
 * `sfu_set_*`・2 回目の `sfu_shutdown` は、何にも触れずに SFU_ERR_SHUT_DOWN を
 * 返す。それらが乗るはずの fiber のスタックが解放済みだからである。
 */
int32_t sfu_shutdown(void);

/**
 * The value the most recent `sfu_step` (or `sfu_shutdown`) produced. The way to
 * read a result under wasm without a SfuStepOut at hand; see the note at the
 * head of this file. SFU_OK before the first call.
 * 直近の `sfu_step`（または `sfu_shutdown`）が出した値。SfuStepOut を持たずに
 * wasm で結果を読む手立てである。本ファイル冒頭の注記を参照。最初の呼び出しの
 * 前は SFU_OK。
 */
int32_t sfu_last_status(void);

/* =========================================================================
 * The main path / 主経路
 * ========================================================================= */

/**
 * One tick: inject the host's state, advance the firmware by `in->dt_us` of
 * virtual time, and report what the rotors produced plus the firmware's state.
 * 1 刻み: ホストの状態を注入し、ファームを `in->dt_us` ぶんの仮想時間だけ進め、
 * ロータが出したものとファームの状態を返す。
 *
 * **Read `out->status`, not the return value, unless you know you are native.**
 * The two hold the same code; only `status` survives Asyncify (see the note at
 * the head of this file).
 * **ネイティブだと分かっている場合を除き、戻り値ではなく `out->status` を読む。**
 * どちらも同じ値を持つが、Asyncify を越えて残るのは `status` だけである
 * （本ファイル冒頭の注記を参照）。
 */
int32_t sfu_step(const SfuStepIn* in, SfuStepOut* out);

/* =========================================================================
 * Parameters / パラメータ
 * ========================================================================= */

/** How many tuning parameters the firmware exposes. ファームが公開する調整値の数。 */
int32_t sfu_param_count(void);

/** Fill `out` with parameter `index`'s identity. 添字 `index` の素性を `out` に書く。 */
int32_t sfu_param_info(int32_t index, SfuParamInfo* out);

/** Set a parameter by name. The bridge looks the type up and uses the matching
 *  setter, so one entry point covers float, bool and int.
 *  名前でパラメータを設定する。橋渡しが型を引いて対応するセッタを使うので、
 *  入口 1 つで float・bool・int を扱える。 */
int32_t sfu_param_set(const char* name, double value);

/** Read a parameter by name into `*out`. 名前でパラメータを読み `*out` に入れる。 */
int32_t sfu_param_get(const char* name, double* out);

/* =========================================================================
 * Disturbances / 外乱
 * ========================================================================= */

/** Constant wind force [N] in the Unity world frame.
 *  Unity の世界系での一定の風力 [N]。 */
int32_t sfu_set_wind(float world_x, float world_y, float world_z);

/** Per-motor thrust health: 1 = healthy, 0 = dead. Index 0..3 = M1..M4.
 *  モータごとの推力の健全度。1 = 正常、0 = 停止。添字 0..3 = M1..M4。 */
int32_t sfu_set_motor_health(int32_t motor, float gain);

/** Constant raw IMU bias in the body-local Unity frame: accelerometer [m/s²],
 *  gyroscope [rad/s]. Models a pre-calibration MEMS offset.
 *  機体ローカルの Unity 系での一定の生 IMU バイアス。加速度計 [m/s²]、
 *  ジャイロ [rad/s]。校正前の MEMS のずれを模擬する。 */
int32_t sfu_set_imu_bias(float accel_x, float accel_y, float accel_z,
                         float gyro_x, float gyro_y, float gyro_z);

/* =========================================================================
 * Log / ログ
 * ========================================================================= */

/**
 * Copy out the oldest log lines the firmware has written since the last read,
 * as one buffer of newline-separated text, and return the number of bytes
 * written (never more than `capacity`, always null-terminated when capacity > 0).
 * 前回の読み出し以降にファームが書いたログ行を、改行で区切った 1 つの文字列として
 * 取り出し、書いた長さを返す（`capacity` を超えず、capacity > 0 なら必ず null 終端）。
 *
 * The firmware's ESP_LOGx macros reach a ring buffer inside the module rather
 * than a terminal, because a browser has no stderr to read back.
 * ファームの ESP_LOGx マクロは端末ではなくモジュール内のリングバッファへ届く。
 * ブラウザには読み戻せる stderr が無いためである。
 */
int32_t sfu_log_read(char* buffer, int32_t capacity);

/** Number of log records dropped because the ring buffer was full since boot.
 *  リングバッファが満杯で捨てられたログ記録の数（起動からの累計）。 */
int32_t sfu_log_dropped(void);

/* Log levels, as SfuLogRecord::level carries them. The same order and the same
 * numbers as esp_log_level_t, so the two never need translating.
 * ログの段。SfuLogRecord::level が持つ値である。esp_log_level_t と順序も数値も
 * 同じにしてあるので、両者を読み替える必要はない。 */
#define SFU_LOG_NONE    0
#define SFU_LOG_ERROR   1
#define SFU_LOG_WARN    2
#define SFU_LOG_INFO    3
#define SFU_LOG_DEBUG   4
#define SFU_LOG_VERBOSE 5

/** Longest log tag kept, including the terminator. 保持するタグの最大長（終端を含む）。 */
#define SFU_LOG_TAG_MAX 32

/** Longest log message kept, including the terminator. Longer messages are
 *  truncated, never dropped. 保持する本文の最大長（終端を含む）。これを超える
 *  本文は捨てずに切り詰める。 */
#define SFU_LOG_MSG_MAX 224

/**
 * One firmware log record, BEFORE it is made into a line of text.
 * 文字列 1 本にする前の、ファームのログ 1 記録。
 *
 * The firmware is never edited: its `ESP_LOGx(tag, fmt, ...)` reaches this
 * through the `esp_log.h` placed earlier on the include path. What is kept is
 * the four things the macro actually had — when, how severe, from where, and
 * what — with the format applied but no level or tag prefix pasted on. The host
 * can therefore write a structured record without parsing a string back into
 * its parts, which is what `AGENTS.md`'s logging rules ask for.
 *
 * ファームは一切編集しない。`ESP_LOGx(tag, fmt, ...)` は include パスの前に置いた
 * `esp_log.h` を通ってここへ届く。保持するのはマクロが実際に持っていた 4 つ ―
 * いつ・どの重さ・どこから・何を ― であり、書式は適用するがレベルやタグの接頭辞は
 * 貼り付けない。よってホストは、文字列を解析して部分へ戻すことなく構造化された
 * 記録を書ける。`AGENTS.md` のログの決まりが求めるのはこれである。
 */
typedef struct SfuLogRecord {
    uint32_t struct_size;             /**< = sizeof(SfuLogRecord) in the CALLER's build */
    int32_t  level;                   /**< SFU_LOG_ERROR / _WARN / _INFO / _DEBUG / _VERBOSE */
    int64_t  sim_us;                  /**< the virtual clock when the firmware logged it [µs] */
    char     tag[SFU_LOG_TAG_MAX];    /**< null-terminated ESP_LOGx tag */
    char     message[SFU_LOG_MSG_MAX];/**< null-terminated body, the format already applied */
} SfuLogRecord;

/* Which struct sfu_struct_size asks about — SfuLogRecord's id.
 * sfu_struct_size が尋ねる構造体 — SfuLogRecord の id。 */
#define SFU_STRUCT_LOG_RECORD 4

/**
 * Take the oldest log record out of the ring. Returns 1 when one was written to
 * `*out`, 0 when the ring is empty, and a negative code on a bad argument.
 * Call it in a loop until it returns 0.
 * リングから最も古い記録を 1 つ取り出す。`*out` に書けば 1、リングが空なら 0、
 * 引数が不正なら負の値を返す。0 が返るまで繰り返し呼ぶ。
 *
 * Reading the log NEVER affects the simulation: it moves no clock, runs no task
 * and touches no plant state, so a host that reads and a host that does not
 * produce the same flight.
 * ログの取り出しがシミュレーションに影響することはない。時計を動かさず、タスクを
 * 走らせず、プラントの状態にも触れない。読むホストと読まないホストは同じ飛行を
 * 生む。
 */
int32_t sfu_log_read_record(SfuLogRecord* out);

/**
 * The lowest level kept from here on. Records at a level ABOVE this are
 * discarded at the moment the firmware logs them, so ESP_LOGD / ESP_LOGV cost
 * nothing while they are off. The default is SFU_LOG_INFO — debug and verbose
 * discarded, matching what the SILS host build does. Returns the previous level.
 * これ以降に残す最も低い段。これより**上**の段の記録は、ファームが書いたその場で
 * 捨てるので、ESP_LOGD／ESP_LOGV は切っている間は何も費やさない。既定は
 * SFU_LOG_INFO で、debug と verbose を捨てる ― SILS のホスト版と同じである。
 * 直前の段を返す。
 */
int32_t sfu_set_log_level(int32_t level);

#ifdef __cplusplus
}  /* extern "C" */
#endif

#endif  /* SFU_API_H */
