/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (vehicle firmware).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file tof_task.cpp
 * @brief ToF sensor reading task (30Hz, bottom + optional front)
 *        ToFセンサ読み取りタスク（30Hz、底面 + 任意の前方）
 *
 * Reads both VL53L3CX parts over the shared I2C bus owned by sf_board and
 * publishes each one to its OWN topic: the bottom sensor to sensor_tof and the
 * forward sensor to sensor_tof_front.
 *
 * sf_board が所有する共有 I2C バス経由で VL53L3CX 2 個を読み取り、それぞれを
 * 「専用」トピックに発行する: 底面は sensor_tof、前方は sensor_tof_front。
 *
 * THE BOTTOM SENSOR ALWAYS COMES FIRST. It is the vehicle's only vertical
 * observation (the barometer is not fused by default), so altitude hold depends
 * on its 30Hz cadence. Everything about the front sensor here — the bring-up
 * order, the per-cycle read order, the skip rule, the failure handling — exists
 * so that a front-sensor fault cannot reach the bottom sensor.
 *
 * 底面センサを常に優先する。底面は機体唯一の鉛直観測であり（気圧は既定で非融合）、
 * 高度保持はその 30Hz の刻みに依存する。本ファイルの前方センサまわり — 起動順序、
 * 周期内の読み出し順、間引き規則、失敗時の扱い — はすべて、前方の異常が底面に
 * 及ばないようにするために存在する。
 *
 * @publisher  sensor_tof, sensor_tof_front
 *
 * @design architecture.md §6 — TofTask: Sensing(ToF)                   [OK]
 * @design architecture.md §2 — R1: sf_board owns the I2C bus           [OK]
 * @design architecture.md §2 — R4: Optional sensor → present=false     [OK]
 * @design architecture.md §2 — R5: one data source = one variable      [OK]
 * @design detailed_design.md §8 — TofTask: 30Hz, priority 14           [OK]
 * @design detailed_design.md §10 — front ToF supply contract, steps 1-2 [OK]
 * @design hardware_init.md §3 — sf_board が i2c_bus を所有 (R1)        [OK]
 * @design hardware_init.md §5 — Bottom=Critical, Front=Optional        [OK]
 * @design topic_reference.md §3 — sensor_tof / sensor_tof_front: Q2, 30Hz [OK]
 *
 * Failure classification (hardware_init.md §5):
 *   - Bottom VL53L3CX init failure → Critical for ALT/POS modes. Currently
 *     logged as an error and the task aborts; escalation to abort() is pending
 *     the failure handling story (M5 で sensor_health Topic と一緒に整備).
 *     UNCHANGED by the front-ToF work.
 *   - Front VL53L3CX init failure → Optional. XSHUT returns to low, the task
 *     reports sensor_present(FrontToF)=false and keeps serving the bottom
 *     sensor at full rate. The front sensor needs battery power: on USB alone
 *     its logic partially powers up, so I2C can ACK while ranging fails.
 *
 * Takeoff/Landing detection logic (TakeoffLandingMgr) runs in a different layer
 * and consumes sensor_tof via ImuTask — the front sensor never reaches it.
 */

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "driver/gpio.h"   // front-ToF XSHUT hold-low / 前方ToF XSHUT固定

#include "topics.hpp"
#include "config.hpp"
#include "params.hpp"
#include "sf_board.hpp"
#include "vl53l3cx_wrapper.hpp"

static const char* TAG = "TofTask";

/// Cycle period [ticks]: 33ms ≈ 30Hz, matching VL53L3CX timing budget.
/// 周期 [tick]: 33ms ≈ 30Hz、VL53L3CX のタイミング予算と整合
static constexpr TickType_t kPeriodTicks = pdMS_TO_TICKS(33);

/// Throttle interval for read-failure warnings.
/// At 30Hz, log at most once every ~3 seconds during persistent failure.
/// 読み取り失敗ログの抑制間隔。30Hz で持続的失敗時は約 3 秒に 1 回まで警告
static constexpr uint32_t kReadFailLogIntervalCycles = 90;

/// Parameter name for the front-ToF kill switch (params.cpp holds the SSOT).
/// 前方 ToF の無効化スイッチのパラメータ名（SSOT は params.cpp）。
static constexpr const char* kParamFrontEnable = "tof.front.enable";

/// VL53L3CX sensor wrappers. File-scope statics — owned by this task only,
/// never shared (per the @publisher annotation above). Separate objects, each
/// with its own I2C device handle and I2C address.
/// VL53L3CX センサのラッパー。ファイルスコープ static で本タスクが所有し、外部共有
/// しない（上記 @publisher アノテーション参照）。別オブジェクトで、それぞれ固有の
/// I2C デバイスハンドルと I2C アドレスを持つ。
static stampfly::VL53L3CXWrapper g_tof_bottom;
static stampfly::VL53L3CXWrapper g_tof_front;

/// Convert wrapper distance reading to sf::TofData topic format.
/// Conversion: mm → m, range_status==0 means valid measurement.
///
/// Shared by both sensors on purpose. "One data source = one variable" (R5) is
/// about where a reading is STORED — separate topics, fields and timestamps —
/// not about the arithmetic that converts it. A pure function holding no state
/// cannot let one sensor's reading reach the other.
///
/// ラッパーの距離値を sf::TofData トピック形式に変換する。
/// 変換: mm → m、range_status==0 を有効計測とする。
///
/// 2 個で共用するのは意図的である。「1 データソース = 1 変数」（R5）が問うのは値の
/// 「置き場所」— トピック・フィールド・タイムスタンプを分けること — であって、
/// 変換の計算ではない。状態を持たない純粋関数は、一方の値を他方へ漏らしようがない。
static sf::TofData buildTofTopic(const stampfly::DistanceData& src,
                                  uint32_t now_us)
{
    sf::TofData out{};
    out.timestamp = now_us;
    out.distance  = static_cast<float>(src.distance_mm) * 1e-3f;
    out.status    = src.range_status;
    out.valid     = (src.range_status == 0);
    return out;
}

/// Drive the front sensor's XSHUT pin, holding it in reset when `awake` is false.
/// Reset is the safe state: a sleeping part cannot answer on I2C at all, so it
/// can neither alias the bottom sensor's address nor stretch the bus.
/// 前方センサの XSHUT を駆動する。`awake` が false ならリセット保持。リセットが安全側:
/// 眠っている部品は I2C に一切応答しないので、底面のアドレスと衝突することも、
/// バスを引き延ばすこともできない。
static void setFrontXshut(bool awake)
{
    gpio_config_t xshut_front = {};
    xshut_front.pin_bit_mask = 1ULL << config::GPIO_TOF_XSHUT_FRONT;
    xshut_front.mode         = GPIO_MODE_OUTPUT;
    gpio_config(&xshut_front);
    gpio_set_level(static_cast<gpio_num_t>(config::GPIO_TOF_XSHUT_FRONT),
                   awake ? 1 : 0);
}

/// Bring the bottom sensor up and start continuous ranging.
/// Returns false on failure, having already reported presence=false.
/// The front sensor MUST still be in reset when this runs — see startTofSensors().
/// 底面センサを起動し連続測距を開始する。失敗時は presence=false を報告済みで
/// false を返す。実行時点で前方はリセット中でなければならない — startTofSensors() 参照。
static bool startBottomSensor()
{
    auto cfg = stampfly::VL53L3CXWrapper::Config::defaultBottom(
        sf::internal::board::i2c_bus()
    );

    esp_err_t init_result = g_tof_bottom.init(cfg);
    if (init_result != ESP_OK) {
        ESP_LOGE(TAG, "VL53L3CX bottom init failed: %s — task aborting",
                 esp_err_to_name(init_result));
        sf::internal::board::set_sensor_present(
            sf::internal::board::SensorId::BottomToF, false);
        return false;
    }

    // Begin continuous ranging (≈30Hz, matching timing_budget_ms=33).
    // 連続測距を開始 (≈30Hz、timing_budget_ms=33 に整合)。
    esp_err_t range_result = g_tof_bottom.startRanging();
    if (range_result != ESP_OK) {
        ESP_LOGE(TAG, "VL53L3CX startRanging failed: %s — task aborting",
                 esp_err_to_name(range_result));
        sf::internal::board::set_sensor_present(
            sf::internal::board::SensorId::BottomToF, false);
        return false;
    }

    // ToF up and ranging — report presence for the sensor_health snapshot (R15).
    // ToF 起動・測距開始 — sensor_health 用に presence を報告 (R15)。
    sf::internal::board::set_sensor_present(
        sf::internal::board::SensorId::BottomToF, true);
    ESP_LOGI(TAG, "VL53L3CX bottom ready at 0x%02X (continuous ranging at ~30Hz)",
             stampfly::VL53L3CXWrapper::BOTTOM_I2C_ADDR);
    return true;
}

/// Give up on the front sensor: say why, put it back in reset and mark it
/// absent. Always returns false so a caller can `return giveUpOnFront(...)`.
///
/// Returning the pin to low is what makes the give-up safe rather than merely
/// tidy: a half-initialised part left awake could still answer to 0x29 and
/// collide with something later.
///
/// 前方を諦める: 理由を告げ、リセットへ戻し、不在として記録する。呼び出し側が
/// `return giveUpOnFront(...)` と書けるよう常に false を返す。
///
/// ピンを low に戻すのは、後片付けではなく安全のためである: 半端に初期化された
/// 部品を起こしたままにすると、0x29 に応答して後から衝突しうる。
static bool giveUpOnFront(const char* what, esp_err_t err)
{
    // Warning, not error: the front sensor is Optional and the vehicle flies
    // fine without it. This is the ordinary outcome on USB power alone, where
    // its logic powers up just enough to ACK on I2C while ranging fails.
    // error ではなく warning: 前方は Optional で、無くても機体は正常に飛ぶ。
    // USB 給電のみではこれが通常の結果である（ロジックが I2C に ACK する程度に
    // だけ立ち上がり、測距は失敗する）。
    ESP_LOGW(TAG, "VL53L3CX front %s failed: %s — continuing without it "
                  "(Optional; needs battery power)",
             what, esp_err_to_name(err));
    setFrontXshut(false);
    sf::internal::board::set_sensor_present(
        sf::internal::board::SensorId::FrontToF, false);
    return false;
}

/// Bring the front sensor up, AFTER the bottom sensor is already ranging at its
/// own address. Returns true only when it is ranging.
///
/// Any failure goes through giveUpOnFront(), which leaves the part back in
/// reset and reports presence=false: the front sensor is Optional
/// (hardware_init.md §5) and the flight must continue without it.
///
/// THE CHEAP CHECK COMES FIRST. init() is expensive when the part is ABSENT —
/// the driver polls for boot completion for up to 500ms before giving up, which
/// is about fifteen 33ms cycles in which the bottom sensor would not be read.
/// isPresentAt() settles "absent" in a few milliseconds instead, so that case
/// never reaches init() at all.
///
/// 底面が「自分のアドレスで測距を始めた後」に前方を起動する。測距開始まで到達した
/// ときだけ true を返す。
///
/// 失敗はすべて giveUpOnFront() を通り、部品をリセットへ戻して presence=false を
/// 報告する: 前方は Optional（hardware_init.md §5）であり、無くても飛行は続ける。
///
/// 「安価な確認を先に」行う。init() は部品が「無い」ときに高くつく — ドライバが
/// 起動完了を最大 500ms ポーリングしてから諦め、これは 33ms 周期の約 15 回分、
/// 底面が読まれない時間になる。isPresentAt() なら「無い」を数ミリ秒で決められるので、
/// その場合は init() に到達すらしない。
static bool startFrontSensor()
{
    // The part was woken a cycle ago (FrontBringUp::Wake) and has had a full
    // 33ms to boot. Probing 0x29 is safe ONLY because the bottom sensor has
    // already moved to 0x30: both parts boot at 0x29, so from here on anything
    // answering there can only be the front part.
    // 部品は 1 周期前に起こされ（FrontBringUp::Wake）、33ms まるごと起動時間を得て
    // いる。0x29 を確認してよいのは、底面が既に 0x30 へ移っているからに他ならない:
    // 2 個とも 0x29 で起動するため、ここから先そこに応答するのは前方だけである。
    const bool front_answers =
        stampfly::VL53L3CXWrapper::isPresentAt(
            sf::internal::board::i2c_bus(),
            stampfly::VL53L3CXWrapper::DEFAULT_I2C_ADDR);
    if (!front_answers) {
        // Not fitted, or powered from USB alone where it cannot answer. Back to
        // reset without ever calling the costly init().
        // 非搭載、あるいは応答できない USB 給電のみ。高くつく init() を呼ばずに
        // リセットへ戻す。
        ESP_LOGI(TAG, "Front ToF not detected — continuing without it "
                      "(Optional; needs battery power)");
        setFrontXshut(false);
        sf::internal::board::set_sensor_present(
            sf::internal::board::SensorId::FrontToF, false);
        return false;
    }

    auto cfg = stampfly::VL53L3CXWrapper::Config::defaultFront(
        sf::internal::board::i2c_bus()
    );

    esp_err_t init_result = g_tof_front.init(cfg);
    if (init_result != ESP_OK) {
        return giveUpOnFront("init", init_result);
    }

    esp_err_t range_result = g_tof_front.startRanging();
    if (range_result != ESP_OK) {
        return giveUpOnFront("startRanging", range_result);
    }

    sf::internal::board::set_sensor_present(
        sf::internal::board::SensorId::FrontToF, true);
    ESP_LOGI(TAG, "VL53L3CX front ready at 0x%02X (continuous ranging at ~30Hz)",
             stampfly::VL53L3CXWrapper::FRONT_I2C_ADDR);
    return true;
}

/// Bring the bottom sensor up, with the front part held in reset throughout.
/// Returns false when the bottom sensor did not reach continuous ranging, which
/// the caller treats as fatal for this task.
///
/// Only the bottom sensor is started here. Both parts boot at 0x29, so the
/// front one must be asleep while the bottom one is re-addressed to 0x30 —
/// otherwise that address assignment reaches BOTH and the two alias. Waking the
/// front part is a separate, later step (see TofTask), because an ABSENT front
/// part costs 500ms of boot polling before it fails, and the bottom sensor must
/// not go unread for that long (safety req. 2).
///
/// 前方をリセット保持したまま底面を起動する。底面が連続測距に到達しなければ false を
/// 返し、呼び出し側はそれを本タスクにとって致命的として扱う。
///
/// ここで起動するのは底面だけである。2 個とも 0x29 で起動するため、底面を 0x30 へ
/// 振り直す間は前方が眠っていなければならない — さもなければそのアドレス割り当てが
/// 「両方」に届いて混線する。前方を起こすのは別の後段（TofTask 参照）である。前方が
/// 「無い」場合、失敗するまでに 500ms の起動ポーリングを要し、その間ずっと底面を
/// 読まずにおくわけにはいかないからである（安全要件 2）。
static bool startBottomWithFrontHeldInReset()
{
    // The front part must be asleep before anything touches 0x29.
    // 0x29 に触れる前に前方を眠らせる。
    setFrontXshut(false);

    // Bottom sensor, by exactly the sequence that shipped before the front
    // sensor existed — its behaviour must not change (safety req. 1).
    // 底面センサ。前方が無かった頃と全く同じ手順で起動する —
    // その挙動を変えてはならない（安全要件 1）。
    return startBottomSensor();
}

/// Read one sensor if it has a new sample, publish it to `topic`, and stamp the
/// BSP freshness slot for `id`. Returns the wrapper's error so the caller can
/// throttle its own logging; ESP_OK also covers "no new sample yet".
///
/// Both sensors go through this one function so the bottom and the front are
/// read exactly alike — but each call carries its own topic, its own SensorId
/// and its own timestamp, so nothing is shared between the two (R5).
///
/// 新しいサンプルがあればセンサを 1 個読み、`topic` へ発行し、`id` の BSP 鮮度欄に
/// 時刻を刻む。ラッパーのエラーをそのまま返すので、呼び出し側が自分のログを抑制できる。
/// ESP_OK は「まだ新サンプルが無い」場合も含む。
///
/// 2 個とも同じこの関数を通すので読み方は揃う — ただし呼び出しごとに専用のトピック、
/// 専用の SensorId、専用のタイムスタンプを渡すため、両者の間で共有されるものは無い（R5）。
static esp_err_t pollAndPublish(stampfly::VL53L3CXWrapper& sensor,
                                sf::Topic<sf::TofData, sf::Queue, 2>& topic,
                                sf::internal::board::SensorId id)
{
    // Step 1: poll for new data
    // ステップ 1: 新規データの有無をポーリング
    bool ready = false;
    esp_err_t err = sensor.isDataReady(ready);
    if (err != ESP_OK) {
        return err;
    }
    if (!ready) {
        // No new sample yet — nothing to do this cycle.
        // 新サンプル未到達 — 本周期は何もしない。
        return ESP_OK;
    }

    // Step 2: read distance and rearm interrupt
    // ステップ 2: 距離を読み、割り込みを再アーム
    stampfly::DistanceData reading{};
    err = sensor.getDistance(reading);
    if (err == ESP_OK) {
        uint32_t now_us = static_cast<uint32_t>(esp_timer_get_time());
        topic.publish(buildTofTopic(reading, now_us));
        // Report freshness to the BSP for the 1 Hz sensor_health snapshot (R15).
        // 1Hz の sensor_health 用に鮮度を BSP へ報告する (R15)。
        sf::internal::board::set_sensor_update(id, now_us);
    }

    // Required after each read to rearm the sensor for the next measurement
    // (per VL53L3CX driver contract). Done even when getDistance failed, so a
    // single bad read does not leave the part waiting forever.
    // 各読み取り後、次の計測用にセンサを再アームする必要がある（VL53L3CX ドライバの
    // 契約）。getDistance が失敗しても行う — 1 回の失敗で部品を待ちぼうけにしないため。
    sensor.clearInterruptAndStartMeasurement();
    return err;
}

/// Per-sensor read state: everything the cycle loop must keep apart between the
/// two sensors. Having the two counters live in separate objects is the same
/// rule as the separate topics — one data source, one variable (R5). A shared
/// counter would let a chattering front sensor silence the bottom sensor's
/// warnings, which are the operator's only sign that the vertical observation
/// is failing.
/// センサ別の読み出し状態: 周期ループが 2 つのセンサの間で分けて保つべきものをまとめる。
/// 2 つの計数器を別オブジェクトに持つのは、トピックを分けるのと同じ規則である
/// （1 データソース = 1 変数, R5）。共有すると、うるさい前方センサが底面の警告を
/// 黙らせてしまう — その警告は、鉛直観測が失われつつあることを操作者が知る唯一の印である。
///
/// The references bind to the file-scope wrapper statics and the global topics,
/// both of which outlive every SensorReadState: these are built once inside
/// TofTask, which never returns. Reference members also make the struct
/// non-copyable and non-assignable — that is intended, not an oversight, since
/// a copy would carry a second, diverging failure counter for the same sensor.
/// 参照の束縛先はファイルスコープの static ラッパーとグローバルのトピックで、
/// どちらも SensorReadState より長生きする: これらは TofTask の中で 1 度だけ作られ、
/// TofTask は戻らないためである。参照メンバゆえに本 struct はコピー不可・代入不可に
/// なるが、これは意図した制約であり見落としではない — コピーすると同じセンサに対して
/// 食い違う 2 つ目の失敗計数器を持つことになるからである。
struct SensorReadState {
    stampfly::VL53L3CXWrapper&           sensor;
    sf::Topic<sf::TofData, sf::Queue, 2>& topic;
    sf::internal::board::SensorId        id;
    const char*                          name;
    uint32_t                             last_fail_log_cycle;
};

/// Where the front sensor's one-time bring-up has got to. The bring-up is spread
/// across cycles so that NO step ever moves the bottom sensor's 30Hz phase
/// (safety req. 2): each step runs in the spare time after the bottom sensor has
/// been read, and the boot wait is absorbed by a cycle the task was going to
/// sleep through anyway.
/// 前方の一度きりの起動が、どこまで進んだか。どの段も底面の 30Hz の位相を動かさない
/// よう、周期をまたいで分割する（安全要件 2）: 各段は底面を読んだ後の余り時間で走り、
/// 起動待ちは「どのみち眠る予定だった周期」に吸収させる。
enum class FrontBringUp : uint8_t {
    Wake,      ///< raise XSHUT; the cycle's own sleep becomes the boot wait
    Probe,     ///< read the model id; decide present/absent
    Done,      ///< nothing left to do (ranging, or given up)
};

/// Raise the front sensor's XSHUT and hand the boot wait to the cycle's sleep.
/// Costs one GPIO write — it cannot disturb the bottom sensor's schedule.
/// The next cycle is at least kPeriodTicks (33ms) later, comfortably beyond the
/// part's BOOT_TIME_MS, so by the Probe step it has had time to come up.
/// 前方の XSHUT を上げ、起動待ちは周期のスリープに任せる。コストは GPIO 書き込み
/// 1 回だけで、底面の予定を乱しようがない。次の周期は少なくとも kPeriodTicks
/// （33ms）後であり、部品の BOOT_TIME_MS を十分に上回るので、Probe の段では
/// 既に立ち上がっている。
static void wakeFrontForProbe()
{
    // The Probe step runs one cycle later, so the cycle must be long enough to
    // serve as the part's boot wait. Checked here so shortening kPeriodTicks
    // below the part's boot time becomes a compile error rather than an
    // intermittent probe failure.
    // Probe の段は 1 周期後に走るので、周期は部品の起動待ちを兼ねられる長さでなければ
    // ならない。ここで検査することで、kPeriodTicks を部品の起動時間より短くした場合に、
    // 間欠的な確認失敗ではなくコンパイルエラーになる。
    static_assert(kPeriodTicks >=
                      pdMS_TO_TICKS(stampfly::VL53L3CXWrapper::BOOT_TIME_MS),
                  "one cycle must cover the part's boot time");
    setFrontXshut(true);
}

/// Advance the one-time front bring-up by exactly one step.
///
/// Called from the cycle loop's spare time, under the same schedule guard as the
/// front read. IT NEVER TOUCHES `last_wake`, so the bottom sensor's 30Hz phase is
/// identical whether or not a front sensor exists (safety req. 2) — that is the
/// property `test_an_absent_front_sensor_does_not_shift_the_bottom_sensor_timing`
/// measures.
///
/// Wake  — raise XSHUT. One GPIO write; the boot wait becomes this cycle's
///         ordinary sleep instead of a vTaskDelay of its own.
/// Probe — read the model id and, only if a real part answered, run the driver's
///         full init. When nothing answers this costs one failed register read,
///         not the driver's 500ms boot polling.
///
/// 一度きりの前方起動を、ちょうど 1 段だけ進める。
///
/// 周期ループの余り時間から、前方読み出しと同じ予定判定の下で呼ばれる。
/// **`last_wake` には一切触れない**ので、底面の 30Hz の位相は前方の有無に関わらず
/// 同一である（安全要件 2）— これは
/// `test_an_absent_front_sensor_does_not_shift_the_bottom_sensor_timing` が測る性質である。
///
/// Wake  — XSHUT を上げる。GPIO 書き込み 1 回で、起動待ちは専用の vTaskDelay では
///         なく本周期の通常のスリープになる。
/// Probe — model id を読み、本物が応答したときだけドライバの完全な init を走らせる。
///         何も応答しないときの代償はレジスタ読み 1 回の失敗であって、
///         ドライバの 500ms 起動ポーリングではない。
static void advanceFrontBringUp(FrontBringUp& step, bool& is_ranging)
{
    if (step == FrontBringUp::Wake) {
        wakeFrontForProbe();
        step = FrontBringUp::Probe;
        return;
    }
    is_ranging = startFrontSensor();
    step = FrontBringUp::Done;
}

/// Read one sensor for this cycle, logging read failures at a throttled rate.
/// 本周期でセンサを 1 個読み、読み取り失敗は間隔を空けてログする。
static void readSensorThisCycle(SensorReadState& state, uint32_t cycle_count)
{
    esp_err_t err = pollAndPublish(state.sensor, state.topic, state.id);
    if (err == ESP_OK) {
        return;
    }
    // Unsigned subtraction, deliberately: both operands are uint32 and the
    // difference stays correct even across cycle_count's wrap (~4.5 years at
    // 30Hz). Signed overflow would be undefined behaviour instead.
    // 符号なし減算は意図的: 両辺とも uint32 で、cycle_count の桁あふれ（30Hz で
    // 約 4.5 年）を跨いでも差は正しい。符号付きなら未定義動作になってしまう。
    if (cycle_count - state.last_fail_log_cycle >= kReadFailLogIntervalCycles) {
        ESP_LOGW(TAG, "%s read failed: %s", state.name, esp_err_to_name(err));
        state.last_fail_log_cycle = cycle_count;
    }
}

void TofTask(void* /*pvParameters*/)
{
    ESP_LOGI(TAG, "TofTask started");

    // Read the kill switch once, before bring-up. The front sensor's address is
    // assigned during bring-up and cannot be handed back at runtime, so this is
    // a boot-time decision (see params.cpp: no live-reload callback).
    // 起動手順の「前」に無効化スイッチを 1 回読む。前方のアドレスは起動手順中に
    // 割り当てられ実行中に返上できないため、これは起動時の判断である
    // （params.cpp 参照: ライブ再読込コールバックを持たない）。
    bool front_requested = true;
    sf::params::get_bool(kParamFrontEnable, front_requested);

    if (!startBottomWithFrontHeldInReset()) {
        vTaskDelete(NULL);
        return;
    }

    FrontBringUp front_step = FrontBringUp::Wake;
    bool front_is_ranging = false;
    if (!front_requested) {
        front_step = FrontBringUp::Done;
        ESP_LOGI(TAG, "Front ToF disabled by %s — left in reset",
                 kParamFrontEnable);
        sf::internal::board::set_sensor_present(
            sf::internal::board::SensorId::FrontToF, false);
    }

    // One state object per sensor — separate topics, separate ids, separate
    // failure counters (R5).
    // センサごとに 1 つの状態オブジェクト — トピックも id も失敗計数器も別（R5）。
    SensorReadState bottom{g_tof_bottom, sf::sensor_tof,
                           sf::internal::board::SensorId::BottomToF, "bottom", 0};
    SensorReadState front{g_tof_front, sf::sensor_tof_front,
                          sf::internal::board::SensorId::FrontToF, "front", 0};

    TickType_t last_wake = xTaskGetTickCount();
    uint32_t cycle_count = 0;

    while (true) {
        ++cycle_count;

        // The bottom sensor is read FIRST, every cycle, unconditionally. Its
        // 30Hz cadence feeds the only vertical observation the estimator has,
        // so it must never wait behind the front sensor (safety req. 2).
        // 底面を「毎周期・無条件・最初に」読む。その 30Hz の刻みが推定器にとって
        // 唯一の鉛直観測を供給するため、前方の後ろで待たせてはならない（安全要件 2）。
        readSensorThisCycle(bottom, cycle_count);

        // The front sensor is read only with time left in the cycle. At 400kHz
        // the two sensors together occupy about 6.6ms of the 33ms budget (the
        // estimate is in detailed_design.md §10), so the skip should not
        // normally trigger — it is here so that an unexpectedly slow bus (clock
        // stretching, a retrying part) costs the FRONT sensor a sample rather
        // than delaying the bottom sensor's next read. Bottom first, front only
        // if the schedule still holds (safety req. 2).
        // 前方は周期に余裕があるときだけ読む。400kHz では 2 センサ合計で 33ms 予算の
        // うち約 6.6ms を占める（見積もりは detailed_design.md §10）ので、この間引きは
        // 通常は発動しない — 想定外にバスが遅い場合（クロックストレッチ、再試行中の
        // 部品）に、底面の次の読み出しを遅らせるのではなく「前方」のサンプルを 1 つ
        // 捨てるために置いてある。底面優先、前方は予定に間に合うときだけ（安全要件 2）。
        const bool cycle_still_on_schedule =
            (xTaskGetTickCount() - last_wake) < kPeriodTicks;
        if (front_is_ranging && cycle_still_on_schedule) {
            readSensorThisCycle(front, cycle_count);
        }

        if (front_step != FrontBringUp::Done && cycle_still_on_schedule) {
            advanceFrontBringUp(front_step, front_is_ranging);
        }

        vTaskDelayUntil(&last_wake, kPeriodTicks);
    }
}
