/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (vehicle firmware).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file vl53l3cx_wrapper.hpp
 * @brief C++ wrapper for VL53L3CX ToF Sensor
 *
 * This header provides a modern C++ interface for the VL53L3CX ToF sensor
 * with RAII support, type safety, and dual sensor management for StampFly.
 */

#ifndef VL53L3CX_WRAPPER_HPP
#define VL53L3CX_WRAPPER_HPP

#include <cstdint>
#include "esp_err.h"
#include "driver/i2c_master.h"
#include "driver/gpio.h"

// C driver headers
extern "C" {
#include "vl53lx_api.h"
#include "vl53lx_platform.h"
}

namespace stampfly {

/**
 * @brief ToF sensor location
 */
enum class ToFLocation {
    BOTTOM,  ///< Bottom sensor (altitude)
    FRONT    ///< Front sensor (obstacle detection)
};

/**
 * @brief Distance measurement data
 */
struct DistanceData {
    int16_t distance_mm;      ///< Distance in millimeters
    uint8_t range_status;     ///< Range status (0 = valid)
    uint8_t num_objects;      ///< Number of detected objects
    float signal_rate;        ///< Signal rate (MCPS)
    float ambient_rate;       ///< Ambient rate (MCPS)
    float sigma_mm;           ///< Sigma in mm (measurement uncertainty)
    uint32_t timestamp;       ///< Timestamp (stream count)
};

/**
 * @brief C++ wrapper class for VL53L3CX ToF sensor
 *
 * This class provides:
 * - RAII (Resource Acquisition Is Initialization)
 * - ESP-IDF error handling (esp_err_t)
 * - Dual sensor management (front and bottom)
 * - XSHUT control for address assignment
 */
class VL53L3CXWrapper {
public:
    static constexpr uint8_t DEFAULT_I2C_ADDR = 0x29;
    static constexpr uint8_t BOTTOM_I2C_ADDR = 0x30;
    static constexpr uint8_t FRONT_I2C_ADDR = 0x31;

    /// Time the part needs after XSHUT is released before it answers on I2C.
    /// The VL53L3CX datasheet gives t_boot = 1.2ms max (firmware boot after the
    /// supply and XSHUT are valid); 4ms is that bound with margin for supply
    /// ramp on this board, and is what init() has always waited.
    /// XSHUT 解除後、I2C に応答するまでに部品が要する時間。VL53L3CX のデータシートは
    /// t_boot 最大 1.2ms（電源と XSHUT 確定後のファーム起動）を規定する。4ms はその
    /// 上限に本基板の電源立ち上がり分の余裕を足した値で、init() が従来待ってきた量でもある。
    static constexpr uint32_t BOOT_TIME_MS = 4;

    /// IDENTIFICATION__MODEL_ID register and the value a VL53L3CX reports there.
    /// 0xEA is the model id of ST's VL53L1-generation die, which the VL53L3CX
    /// shares (module type 0xAA at 0x0110). Why not 0xEB/0xEC: those are what the
    /// bundled driver's IsL4() (vl53lx_api.c) tests for, i.e. the VL53L4CX, a
    /// different part. An earlier version of this probe took them for the
    /// VL53L3CX and, on the real vehicle (2026-09-19), turned away a front
    /// sensor that had ACKed at 0x29. A mismatch is logged with the value read.
    /// IDENTIFICATION__MODEL_ID レジスタと、VL53L3CX がそこで返す値。0xEA は ST の
    /// VL53L1 世代のダイの model id で、VL53L3CX も同じ値を返す（0x0110 の module type
    /// は 0xAA）。0xEB/0xEC にしない理由: それは同梱ドライバの IsL4()（vl53lx_api.c）が
    /// 調べる値、つまり別の部品 VL53L4CX のものである。この在否確認の以前の版はそれを
    /// VL53L3CX の値と取り違え、実機（2026-09-19）で 0x29 に ACK を返した前方センサーを
    /// 拒否した。不一致のときは読めた値をログに出す。
    static constexpr uint16_t MODEL_ID_REG = 0x010F;
    static constexpr uint8_t  MODEL_ID     = 0xEA;

    /**
     * @brief Cheaply test whether a VL53L3CX is actually present at `addr`.
     *
     * Reads ONE register (IDENTIFICATION__MODEL_ID). Returns true only when the
     * part answers AND reports a VL53L3CX model id.
     *
     * DOES NOT WAIT AND DOES NOT TOUCH XSHUT. The caller must already have
     * released the part's XSHUT and let at least BOOT_TIME_MS pass. That is
     * deliberate: a caller on a fixed cycle (TofTask) raises XSHUT in one cycle
     * and calls this in the next, so the boot wait is absorbed by the cycle it
     * was going to sleep through anyway and the cycle's phase never moves.
     *
     * Why this exists: init() below is expensive when the part is ABSENT —
     * VL53LX_WaitDeviceBooted() polls for up to
     * VL53LX_BOOT_COMPLETION_POLLING_TIMEOUT_MS (500ms) before giving up. A
     * caller that must not block for that long (TofTask, whose bottom sensor is
     * the vehicle's only vertical observation) calls this first and skips init()
     * entirely on a negative result, paying a few milliseconds instead.
     *
     * Why a model-id read and not a bare address ACK: an ACK only proves that
     * SOMETHING answered. Reading the id distinguishes a real VL53L3CX from a
     * bus that ACKs by default.
     *
     * The caller owns the XSHUT pin and is responsible for returning it to low
     * on a negative result.
     *
     * @param i2c_bus    Bus handle borrowed from sf_board (R1/R2)
     * @param addr       Address to test (the part's power-on address)
     * @return true if a VL53L3CX answered with a known model id
     *
     * @brief `addr` に VL53L3CX が実在するかを安価に確かめる。
     *
     * レジスタを 1 本だけ（IDENTIFICATION__MODEL_ID）読む。応答があり、かつ
     * VL53L3CX の model id を返したときだけ true を返す。
     *
     * 「待たない」「XSHUT に触れない」。呼び出し側が事前に XSHUT を解除し、
     * BOOT_TIME_MS 以上経過させておくこと。これは意図的である: 一定周期で回る
     * 呼び出し側（TofTask）は、ある周期で XSHUT を上げ、次の周期でこれを呼ぶ。
     * そうすれば起動待ちは「どのみち眠る予定だった周期」に吸収され、周期の位相は
     * 一切動かない。
     *
     * 存在理由: 下の init() は部品が「無い」ときに高くつく — VL53LX_WaitDeviceBooted()
     * が諦めるまでに VL53LX_BOOT_COMPLETION_POLLING_TIMEOUT_MS（500ms）ポーリングする。
     * それだけ止まってはならない呼び出し側（底面が機体唯一の鉛直観測である TofTask）は
     * 先にこれを呼び、否定ならば init() を一切呼ばずに数ミリ秒で済ませる。
     *
     * なぜ素のアドレス ACK ではなく model id を読むか: ACK は「何かが応答した」ことしか
     * 示さない。id を読めば、既定で ACK を返すバスと本物の VL53L3CX を区別できる。
     *
     * `xshut_pin` は呼び出し側の所有物であり、否定時に low へ戻す責任も呼び出し側にある。
     * 本関数はピンを high のままにする。
     */
    static bool isPresentAt(i2c_master_bus_handle_t i2c_bus, uint8_t addr);

    /**
     * @brief Configuration structure for VL53L3CX
     */
    struct Config {
        i2c_master_bus_handle_t i2c_bus;  ///< I2C bus handle (must be initialized)
        gpio_num_t xshut_pin;             ///< XSHUT pin for this sensor
        uint8_t i2c_addr;                 ///< Target I2C address
        ToFLocation location;             ///< Sensor location
        uint32_t timing_budget_ms;        ///< Timing budget in ms

        /**
         * @brief Get default configuration for bottom sensor
         */
        static Config defaultBottom(i2c_master_bus_handle_t bus) {
            Config config;
            config.i2c_bus = bus;
            config.xshut_pin = GPIO_NUM_7;
            config.i2c_addr = BOTTOM_I2C_ADDR;
            config.location = ToFLocation::BOTTOM;
            config.timing_budget_ms = 33;  // 30Hz
            return config;
        }

        /**
         * @brief Get default configuration for front sensor
         */
        static Config defaultFront(i2c_master_bus_handle_t bus) {
            Config config;
            config.i2c_bus = bus;
            config.xshut_pin = GPIO_NUM_9;
            config.i2c_addr = FRONT_I2C_ADDR;
            config.location = ToFLocation::FRONT;
            config.timing_budget_ms = 33;  // 30Hz
            return config;
        }
    };

    /**
     * @brief Distance mode
     */
    enum class DistanceMode {
        SHORT = VL53LX_DISTANCEMODE_SHORT,    ///< Short range (up to 1.3m)
        MEDIUM = VL53LX_DISTANCEMODE_MEDIUM,  ///< Medium range (up to 3m)
        LONG = VL53LX_DISTANCEMODE_LONG       ///< Long range (up to 4m)
    };

    /**
     * @brief Default constructor (uninitialized)
     */
    VL53L3CXWrapper() = default;

    /**
     * @brief Destructor
     */
    ~VL53L3CXWrapper();

    /**
     * @brief Copy constructor (deleted)
     */
    VL53L3CXWrapper(const VL53L3CXWrapper&) = delete;

    /**
     * @brief Copy assignment (deleted)
     */
    VL53L3CXWrapper& operator=(const VL53L3CXWrapper&) = delete;

    /**
     * @brief Move constructor
     */
    VL53L3CXWrapper(VL53L3CXWrapper&& other) noexcept;

    /**
     * @brief Move assignment operator
     */
    VL53L3CXWrapper& operator=(VL53L3CXWrapper&& other) noexcept;

    /**
     * @brief Initialize single VL53L3CX sensor
     *
     * This function:
     * 1. Controls XSHUT to reset the sensor
     * 2. Waits for boot
     * 3. Changes I2C address if needed
     * 4. Initializes the sensor
     *
     * @param config Configuration parameters
     * @return ESP_OK on success, error code otherwise
     */
    esp_err_t init(const Config& config);

    /**
     * @brief Start continuous ranging
     *
     * @return ESP_OK on success, error code otherwise
     */
    esp_err_t startRanging();

    /**
     * @brief Stop ranging
     *
     * @return ESP_OK on success, error code otherwise
     */
    esp_err_t stopRanging();

    /**
     * @brief Check if measurement data is ready
     *
     * @param ready Output: true if data is ready
     * @return ESP_OK on success, error code otherwise
     */
    esp_err_t isDataReady(bool& ready);

    /**
     * @brief Get distance measurement
     *
     * @param data Output: distance data
     * @return ESP_OK on success, error code otherwise
     */
    esp_err_t getDistance(DistanceData& data);

    /**
     * @brief Get distance in millimeters (simple interface)
     *
     * @param distance_mm Output: distance in mm
     * @param status Output: range status (0 = valid)
     * @return ESP_OK on success, error code otherwise
     */
    esp_err_t getDistance(uint16_t& distance_mm, uint8_t& status);

    /**
     * @brief Clear interrupt and start next measurement
     *
     * @return ESP_OK on success, error code otherwise
     */
    esp_err_t clearInterruptAndStartMeasurement();

    /**
     * @brief Set distance mode
     *
     * @param mode Distance mode
     * @return ESP_OK on success, error code otherwise
     */
    esp_err_t setDistanceMode(DistanceMode mode);

    /**
     * @brief Set timing budget
     *
     * @param budget_ms Timing budget in milliseconds
     * @return ESP_OK on success, error code otherwise
     */
    esp_err_t setTimingBudget(uint32_t budget_ms);

    /**
     * @brief Get sensor location
     *
     * @return Sensor location (BOTTOM or FRONT)
     */
    ToFLocation getLocation() const { return config_.location; }

    /**
     * @brief Check if sensor is initialized
     *
     * @return true if initialized
     */
    bool isInitialized() const { return initialized_; }

    /**
     * @brief Get raw C device handle (for advanced use)
     *
     * @return Pointer to VL53LX device structure
     */
    VL53LX_Dev_t* getDeviceHandle() { return &device_; }

    // ====== Static Helper Functions for Dual Sensor Setup ======

    /**
     * @brief Initialize dual ToF sensors (bottom and front)
     *
     * This is a convenience function that properly sequences the
     * initialization of both sensors with XSHUT control.
     *
     * @param bottom_sensor Output: bottom sensor wrapper
     * @param front_sensor Output: front sensor wrapper
     * @param i2c_bus I2C bus handle
     * @param bottom_xshut Bottom sensor XSHUT pin (default: GPIO7)
     * @param front_xshut Front sensor XSHUT pin (default: GPIO9)
     * @return ESP_OK on success, error code otherwise
     */
    static esp_err_t initDualSensors(
        VL53L3CXWrapper& bottom_sensor,
        VL53L3CXWrapper& front_sensor,
        i2c_master_bus_handle_t i2c_bus,
        gpio_num_t bottom_xshut = GPIO_NUM_7,
        gpio_num_t front_xshut = GPIO_NUM_9
    );

private:
    VL53LX_Dev_t device_;        ///< VL53LX device structure
    Config config_;              ///< Current configuration
    bool initialized_ = false;   ///< Initialization status
    bool ranging_ = false;       ///< Ranging active status
    i2c_master_dev_handle_t i2c_dev_handle_ = nullptr;

    /**
     * @brief Control XSHUT pin
     */
    esp_err_t setXshut(bool enable);

    /**
     * @brief Wait for device boot
     */
    esp_err_t waitDeviceBoot();
};

}  // namespace stampfly

#endif // VL53L3CX_WRAPPER_HPP
