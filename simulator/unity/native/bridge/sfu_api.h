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
 *   sfu_shutdown()           — optional; the module is discarded either way
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
#define SFU_ABI_VERSION 1

/** Number of motors. Fixed by the airframe. モータの数。機体で決まる。 */
#define SFU_MOTOR_COUNT 4

/** Longest parameter name the bridge reports, including the terminator.
 *  橋渡しが報告するパラメータ名の最大長（終端を含む）。 */
#define SFU_PARAM_NAME_MAX 64

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
    float dt_s;                  /**< virtual seconds to advance the firmware by */
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

/** Unwind the firmware's tasks. Optional — discarding the module is enough.
 *  ファームのタスクを巻き戻す。省略可 — モジュールを捨てるだけでも足りる。 */
int32_t sfu_shutdown(void);

/* =========================================================================
 * The main path / 主経路
 * ========================================================================= */

/**
 * One tick: inject the host's state, advance the firmware by `in->dt_s` of
 * virtual time, and report what the rotors produced plus the firmware's state.
 * 1 刻み: ホストの状態を注入し、ファームを `in->dt_s` ぶんの仮想時間だけ進め、
 * ロータが出したものとファームの状態を返す。
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

/** Number of log lines dropped because the ring buffer was full since boot.
 *  リングバッファが満杯で捨てられたログ行の数（起動からの累計）。 */
int32_t sfu_log_dropped(void);

#ifdef __cplusplus
}  /* extern "C" */
#endif

#endif  /* SFU_API_H */
