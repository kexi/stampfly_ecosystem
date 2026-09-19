#include "workshop_api.hpp"
#include <cmath>

// Tick counter for decimation
// デシメーション用のtickカウンタ
static uint32_t tick = 0;

// @@snippet: setup
void setup()
{
    ws::print("Lesson 10: API Overview & App Development - Solution");
}
// @@end-snippet: setup

// @@snippet: loop
void loop_400Hz(float dt)
{
    tick++;

    // Decimation: output at 50 Hz (every 8 ticks at 400 Hz)
    // デシメーション: 50Hz出力（400Hzの8tick毎）
    if (tick % 8 != 0) return;

    // Barometric data / 気圧データ
    ws::print(">baro_alt:%.2f", ws::baro_altitude());

    // ToF (Time-of-Flight) bottom distance / ToF下向き距離
    // Note: tof_front() also works now, but the forward sensor needs battery
    // power and does not start on USB alone, so this lesson stays on the
    // bottom ToF, which works while the board is being flashed over USB.
    // 注: tof_front() も動くようになったが、前方センサはバッテリー電源が要り
    // USB給電のみでは起動しない。そのため本レッスンは、USB書き込み中でも動く
    // 底面ToFに留める。
    ws::print(">tof_bottom:%.3f", ws::tof_bottom());

    // ESKF estimation values (ws:: API) / ESKF推定値（ws:: API）
    ws::print(">eskf_roll:%.1f", ws::estimated_roll() * 57.3f);
    ws::print(">eskf_pitch:%.1f", ws::estimated_pitch() * 57.3f);
    ws::print(">eskf_yaw:%.1f", ws::estimated_yaw() * 57.3f);
    ws::print(">eskf_alt:%.2f", ws::estimated_altitude());

    // Magnetic data and heading / 磁気データと方位角
    ws::print(">mag_x:%.1f", ws::mag_x());
    ws::print(">mag_y:%.1f", ws::mag_y());
    ws::print(">mag_z:%.1f", ws::mag_z());
    float heading = atan2f(-ws::mag_y(), ws::mag_x()) * 57.3f;
    ws::print(">heading:%.1f", heading);

    // Optical flow velocity / 光学フロー速度
    ws::print(">flow_vx:%.3f", ws::flow_vx());
    ws::print(">flow_vy:%.3f", ws::flow_vy());

    // Battery voltage / バッテリ電圧
    ws::print(">voltage:%.2f", ws::battery_voltage());
}
// @@end-snippet: loop
