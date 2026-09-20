# =============================================================================
# StampFly Unity simulator — native core.
# StampFly Unity 版シミュレータ — ネイティブコア。
#
# A project of its own, separate from simulator/sils, because this one must NOT
# fetch MuJoCo: the Unity build gets its rigid body from PhysX and links
# plant/plant_external.cpp in place of plant/plant.cpp. The firmware source list
# is shared with the SILS through simulator/sils/cmake/firmware_sources.cmake, so
# the two builds cannot drift apart in what they compile.
#
# simulator/sils とは別のプロジェクトにしてある。こちらは MuJoCo を取得しては
# ならないからである。Unity 版は剛体を PhysX から受け取り、plant/plant.cpp の
# 代わりに plant/plant_external.cpp をリンクする。ファームのソース一覧は
# simulator/sils/cmake/firmware_sources.cmake を通じて SILS と共有するので、
# 2 つのビルドがコンパイルするものは離れようがない。
#
# Configure it for one of two toolchains:
#   native (macOS arm64, the editor plugin) — the thread scheduler, /usr/bin/clang++
#   wasm   (Emscripten, the WebGL module)   — the fiber scheduler, emcc
# 次の 2 つのツールチェーンのどちらかで構成する:
#   ネイティブ（macOS arm64、エディタ用プラグイン）— スレッド版、/usr/bin/clang++
#   wasm（Emscripten、WebGL 用モジュール）        — fiber 版、emcc
#
# `just unity-native-build` runs both; see that recipe for the exact invocation.
# `just unity-native-build` が両方を回す。正確な呼び出し方はそのレシピにある。
#
# @design docs/plans/unity-simulator.md §5 段階 2, §6 主なファイル
# =============================================================================

cmake_minimum_required(VERSION 3.16)
project(stampfly_unity_native LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# -ffp-contract=off, on EVERY target in this project, both toolchains.
#
# By default a compiler may contract `a*b + c` into one fused multiply-add,
# which rounds once instead of twice. Clang and Emscripten make that choice
# independently, so the same source can produce different last bits on the two
# — and then the native and wasm minimum-operation checks cannot be compared
# byte for byte, which is exactly what `just unity-native-test` does. Turning
# contraction off costs a little speed (the checks run about 100x faster than
# real time either way) and buys an output that is identical everywhere.
#
# -ffp-contract=off を、本プロジェクトの**全ての**ターゲットに、両方の
# ツールチェーンで付ける。
#
# 既定では、コンパイラは `a*b + c` を 1 つの積和融合へ縮約してよいことになって
# いる。縮約すると丸めが 2 回でなく 1 回になる。Clang と Emscripten はその選択を
# 別々に行うので、同じソースから両者で下位ビットの違う結果が出うる。すると
# ネイティブと wasm の最小動作確認をバイト単位で比べられない ― それこそが
# `just unity-native-test` の行うことである。縮約を切ると速度をいくらか失うが
# （どちらにせよ確認は実時間の約 100 倍で走る）、どこでも同一の出力が得られる。
add_compile_options(-ffp-contract=off)

# Paths. NATIVE_DIR is this directory; the rest are derived from it so the
# project can be configured from anywhere.
# パス。NATIVE_DIR はこのディレクトリで、残りはそこから導く。どこから構成しても
# 動くようにするためである。
set(NATIVE_DIR ${CMAKE_CURRENT_SOURCE_DIR})
set(REPO_ROOT  ${NATIVE_DIR}/../../..)
set(SILS       ${REPO_ROOT}/simulator/sils)
set(VN         ${REPO_ROOT}/firmware/vehicle)

# EMU_FW_SRCS / EMU_FW_INCS / CMAKE_C_STANDARD — the same lists simulator/sils
# builds emu_vehicle from, so this build compiles exactly the same firmware.
# EMU_FW_SRCS／EMU_FW_INCS／CMAKE_C_STANDARD — simulator/sils が emu_vehicle を
# 作る一覧そのもの。このビルドがコンパイルするファームも同一になる。
include(${SILS}/cmake/firmware_sources.cmake)

# Emscripten targets the browser and gets the fiber scheduler; everything else
# is the macOS editor plugin and keeps the thread scheduler.
# Emscripten はブラウザ向けで fiber 版スケジューラを使う。それ以外は macOS の
# エディタ用プラグインで、スレッド版のままにする。
if(EMSCRIPTEN)
  set(SFU_SCHEDULER_SRC ${SILS}/rtos/scheduler_fiber.cpp)
else()
  set(SFU_SCHEDULER_SRC ${SILS}/rtos/scheduler.cpp ${SILS}/rtos/scheduler_step.cpp)
endif()

# -----------------------------------------------------------------------------
# sfu_firmware — the whole unmodified firmware, the device models, the scheduler,
# the MuJoCo-free plant and the C ABI, as one static library.
# sfu_firmware — 無改変のファーム全体・デバイスモデル・スケジューラ・MuJoCo を
# 使わないプラント・C ABI を、1 つの静的ライブラリにしたもの。
# -----------------------------------------------------------------------------
add_library(sfu_firmware STATIC
  ${EMU_FW_SRCS}
  ${SFU_SCHEDULER_SRC}
  ${SILS}/rtos/esp_timer_shim.cpp
  ${SILS}/plant/plant_external.cpp            # the plant without MuJoCo
  ${SILS}/devices/vl53_platform_glue.c        # a firmware platform fn the SILS supplies
  ${SILS}/devices/virtual_board.cpp           # BMI270 + motors ↔ the plant
  ${SILS}/devices/vl53_device.cpp             # VL53L3CX ToF chip model
  ${SILS}/devices/pmw3901_device.cpp          # PMW3901 optical-flow chip model
  ${SILS}/devices/espnow_hub.cpp              # the ESP-NOW hub comm.cpp talks to
  ${SILS}/devices/emu_record.cpp              # recorder (passive without a path)
  ${SILS}/devices/emu_flightlog.cpp           # flight-log bundle recorder
  ${SILS}/devices/emu_flightlog_vehicle.cpp   # vehicle-topic glue for the above
  ${SILS}/devices/emu_vehicle_glue.cpp        # ApiTask scenario-injection registration
  ${SILS}/devices/scenario.cpp                # *.scn driver (idle without a scenario)
  ${SILS}/devices/scenario_inject.cpp         # the 14-byte ControlPacket builder
  ${SILS}/devices/console_feeder.cpp          # scripted console bytes → firmware stdin
  ${SILS}/devices/esp_console_shim.cpp        # the CLI console registry cli_task uses
  ${SILS}/compat/clock_shim.cpp
  ${SILS}/compat/nvs_shim.cpp
  ${SILS}/esp_idf_host/win_stdio_shim.cpp     # empty off Windows; harmless to list
  ${NATIVE_DIR}/compat_wasm/wasm_stdio_shim.cpp
  ${NATIVE_DIR}/bridge/sfu_bridge.cpp
  ${NATIVE_DIR}/bridge/sfu_log_ring.cpp
)

# The include order is the whole mechanism by which the unmodified firmware and
# the unmodified SILS sources are redirected. Earlier wins, so:
#   bridge/shim  — esp_log.h → the ring buffer instead of stderr
#   compat_wasm  — <cstdio> that makes musl's const stdout assignable
#   rtos_fiber   — scheduler.hpp → the fiber version (wasm only)
# include の順序こそが、無改変のファームと無改変の SILS のソースを差し替える
# 仕組みそのものである。先にあるものが勝つので:
#   bridge/shim — esp_log.h を stderr でなくリングバッファへ
#   compat_wasm — musl の const な stdout を再代入できるようにする <cstdio>
#   rtos_fiber  — scheduler.hpp を fiber 版へ（wasm のときだけ）
set(SFU_SHIM_INCS ${NATIVE_DIR}/bridge/shim ${NATIVE_DIR}/compat_wasm)
if(EMSCRIPTEN)
  list(APPEND SFU_SHIM_INCS ${NATIVE_DIR}/rtos_fiber)
endif()

target_include_directories(sfu_firmware BEFORE PUBLIC
  ${SFU_SHIM_INCS}
  ${SILS}/esp_idf_host
  ${SILS}/compat
  ${SILS}/rtos
  ${SILS}/plant
  ${SILS}/frames
  ${SILS}/devices
  ${NATIVE_DIR}/bridge
  ${EMU_FW_INCS}
)

# SILS_PLANT_EXTERNAL drops plant.hpp's four MuJoCo-typed declarations and adds
# the externally supplied rigid-body interface. Every other declaration in that
# header is shared verbatim with the MuJoCo build.
# SILS_PLANT_EXTERNAL は plant.hpp の MuJoCo 型の宣言 4 つを落とし、外部供給の
# 剛体の口を足す。同ヘッダのそれ以外の宣言は MuJoCo 版とそのまま共有される。
target_compile_definitions(sfu_firmware PUBLIC SILS_PLANT_EXTERNAL=1)

# The VL53L3CX vendor driver is full of deprecated-declaration warnings that are
# not this project's to fix; silencing them keeps a real warning visible.
# VL53L3CX のベンダドライバは deprecated 宣言の警告で埋まるが、本プロジェクトが
# 直すものではない。黙らせておくと、本物の警告が見えるようになる。
target_compile_options(sfu_firmware PRIVATE -Wno-deprecated-declarations)

if(NOT EMSCRIPTEN)
  find_package(Threads REQUIRED)
  target_link_libraries(sfu_firmware PUBLIC Threads::Threads)
endif()

# -----------------------------------------------------------------------------
# frames_unity_test — the Unity coordinate transform on its own. No firmware, no
# plant, no MuJoCo: just the header and sf_math.
# frames_unity_test — Unity の座標変換だけの試験。ファームもプラントも MuJoCo も
# 使わず、ヘッダと sf_math だけである。
# -----------------------------------------------------------------------------
add_executable(frames_unity_test ${SILS}/frames/frames_unity_test.cpp)
target_include_directories(frames_unity_test PRIVATE
  ${SILS}/frames
  ${VN}/components/sf_math/include   # header-only Vec3/Quat
)

# -----------------------------------------------------------------------------
# The two minimum-operation checks. Both talk to sfu_api.h and nothing else, so
# they exercise the same surface the C# side will.
# 2 つの最小動作確認。どちらも触れるのは sfu_api.h だけなので、C# 側が触るのと
# 同じ面を動かすことになる。
#   sfu_bridge_smoke   — the plant integrates its own body
#   sfu_external_smoke — a C++ stand-in for Unity integrates it
#   sfu_bridge_smoke   — プラントが自分の剛体を積分する
#   sfu_external_smoke — Unity の代役の C++ が積分する
# -----------------------------------------------------------------------------
add_executable(sfu_bridge_smoke ${NATIVE_DIR}/bridge/sfu_bridge_smoke.cpp)
target_link_libraries(sfu_bridge_smoke PRIVATE sfu_firmware)

add_executable(sfu_external_smoke ${NATIVE_DIR}/bridge/sfu_external_smoke.cpp)
target_link_libraries(sfu_external_smoke PRIVATE sfu_firmware)

if(EMSCRIPTEN)
  # Whatever links the firmware under Emscripten needs ASYNCIFY — that is what
  # lets the fiber scheduler switch stacks inside WebAssembly — and the memory
  # settings that cover the 14 MiB of task stacks (1 MiB each). Both smoke
  # checks run under Node.js with these; the shipped module adds its own flags
  # further down.
  # Emscripten でファームをリンクするものには ASYNCIFY が要る。fiber 版スケジューラが
  # WebAssembly の中でスタックを切り替えられるのはこれによる。合わせてタスクスタック
  # 14 MiB（1 タスク 1 MiB）ぶんを賄う記憶域の設定も要る。2 つの最小動作確認は
  # これらを付けて Node.js で動く。出荷するモジュールは下で自分自身の指定を足す。
  set(SFU_WASM_RUNTIME_FLAGS
      "-sASYNCIFY -sALLOW_MEMORY_GROWTH=1 -sINITIAL_MEMORY=64MB -sSTACK_SIZE=1MB")
  set_target_properties(sfu_bridge_smoke sfu_external_smoke PROPERTIES
    LINK_FLAGS "${SFU_WASM_RUNTIME_FLAGS} -sEXIT_RUNTIME=1")

  # ---------------------------------------------------------------------------
  # sfu_firmware.js / .wasm — the module Unity WebGL loads through .jslib.
  #
  # Whole-program Asyncify instrumentation is kept: stage 1 measured that
  # narrowing it with ASYNCIFY_ONLY is unnecessary (§9 (a-2)). MODULARIZE +
  # EXPORT_NAME give the page a factory it can call again for a power cycle —
  # recreating the module IS the restart, since one module is one power-on.
  #
  # sfu_firmware.js／.wasm — Unity WebGL が .jslib 越しに読み込むモジュール。
  #
  # Asyncify の計装は全体のまま: ASYNCIFY_ONLY による絞り込みが不要であることは
  # 段階 1 で計測した（§9 (a-2)）。MODULARIZE ＋ EXPORT_NAME はページに、電源の
  # 入れ直しのために呼び直せる生成関数を与える。1 モジュール＝1 回の電源投入
  # なので、モジュールの作り直しがそのまま再起動である。
  # ---------------------------------------------------------------------------
  set(SFU_WASM_EXPORTS
      _sfu_abi_version,_sfu_struct_size,_sfu_boot,_sfu_shutdown,_sfu_last_status,_sfu_step,_sfu_param_count,_sfu_param_info,_sfu_param_set,_sfu_param_get,_sfu_set_wind,_sfu_set_motor_health,_sfu_set_imu_bias,_sfu_log_read,_sfu_log_read_record,_sfu_log_dropped,_sfu_set_log_level,_malloc,_free)

  # A library exports nothing on its own, so the module needs a translation unit
  # of its own to link. It is empty on purpose: every entry point comes from
  # sfu_firmware, kept alive by EXPORTED_FUNCTIONS.
  # ライブラリ自身は何も export しないので、モジュールにはリンクのための翻訳単位
  # が要る。中身が空なのは意図的である。入口はすべて sfu_firmware から来て、
  # EXPORTED_FUNCTIONS がそれらを生かす。
  add_executable(sfu_firmware_module ${NATIVE_DIR}/bridge/sfu_module_main.cpp)
  target_link_libraries(sfu_firmware_module PRIVATE sfu_firmware)
  set_target_properties(sfu_firmware_module PROPERTIES
    OUTPUT_NAME sfu_firmware
    SUFFIX ".js"
    LINK_FLAGS "${SFU_WASM_RUNTIME_FLAGS} \
-sMODULARIZE=1 -sEXPORT_NAME=createSfuFirmware -sEXIT_RUNTIME=0 \
-sEXPORTED_FUNCTIONS=[${SFU_WASM_EXPORTS}] \
-sEXPORTED_RUNTIME_METHODS=[ccall,cwrap,getValue,setValue,UTF8ToString,HEAPU8,HEAPF32,HEAP32,HEAPU32]")
else()
  # ---------------------------------------------------------------------------
  # libsfu_firmware.dylib — the macOS arm64 plugin the Unity editor loads, so the
  # play button works without waiting for a WebGL build. Only the sfu_* entry
  # points are visible: -fvisibility=hidden hides everything else, and each entry
  # point is marked visible by the attribute the header would carry.
  # Reloading it between play sessions comes with the Unity project (stage 3);
  # this is the dylib only.
  #
  # libsfu_firmware.dylib — Unity のエディタが読み込む macOS arm64 のプラグイン。
  # WebGL のビルドを待たずに再生ボタンで試せるようにするためである。見えるのは
  # sfu_* の入口だけにしてある。-fvisibility=hidden が他を隠し、各入口は属性で
  # 見えるようにする。再生のたびに読み込み直す仕組みは Unity プロジェクトと一緒に
  # 作る（段階 3）ので、ここでは dylib までとする。
  # ---------------------------------------------------------------------------
  add_library(sfu_firmware_shared SHARED ${NATIVE_DIR}/bridge/sfu_module_main.cpp)
  target_link_libraries(sfu_firmware_shared PRIVATE sfu_firmware)
  set_target_properties(sfu_firmware_shared PROPERTIES
    OUTPUT_NAME sfu_firmware
    CXX_VISIBILITY_PRESET hidden
    C_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    # Re-export the sfu_* entry points out of the static library. Without this
    # the linker drops them: nothing inside the dylib calls them.
    # 静的ライブラリから sfu_* の入口を再 export する。これが無いとリンカが落とす。
    # dylib の中からそれらを呼ぶものは無いからである。
    LINK_FLAGS "-Wl,-exported_symbols_list,${NATIVE_DIR}/bridge/sfu_exports.txt")

  # ---------------------------------------------------------------------------
  # sfu_shutdown_check — proves `sfu_shutdown` always returns and always gives
  # its threads back, from every point in the module's lifetime and from a
  # thread other than the one that stepped.
  #
  # It does NOT link sfu_firmware. It opens the dylib with `dlopen` exactly as
  # `EditorFirmware` does, because the deadlock it guards against lived in the
  # relationship between a host and a loaded module — a check that linked the
  # firmware statically could not hold two modules at once, which is the state
  # the editor is in after a second play session.
  #
  # sfu_shutdown_check — `sfu_shutdown` が、モジュールの生涯のどの時点からでも、
  # また刻んだのとは別のスレッドからでも、必ず戻り必ずスレッドを返すことを確かめる。
  #
  # sfu_firmware をリンク**しない**。`EditorFirmware` と同じく `dlopen` で dylib を
  # 開く。防ぐ対象のデッドロックが、ホストと読み込まれたモジュールの関係の中に
  # 在ったからである。ファームを静的にリンクする確認では、モジュールを 2 つ同時に
  # 持てない ― 2 回目の再生の後のエディタはその状態に在る。
  # ---------------------------------------------------------------------------
  add_executable(sfu_shutdown_check ${NATIVE_DIR}/bridge/sfu_shutdown_check.cpp)
  target_include_directories(sfu_shutdown_check PRIVATE ${NATIVE_DIR}/bridge)
  add_dependencies(sfu_shutdown_check sfu_firmware_shared)
endif()
