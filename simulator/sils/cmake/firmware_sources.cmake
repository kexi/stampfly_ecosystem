# =============================================================================
# The unmodified firmware's source and include lists, shared by every build that
# compiles firmware/vehicle on the host.
# ホストで firmware/vehicle をコンパイルする全ビルドが共有する、無改変ファームの
# ソース一覧と include 一覧。
#
# Lifted out of simulator/sils/CMakeLists.txt so that
# simulator/unity/native/CMakeLists.txt — a separate project that does not fetch
# MuJoCo — collects exactly the same translation units. The variable names and
# the contents are UNCHANGED from where they were written inline, so every
# existing target's source list is the same as before the move.
#
# simulator/sils/CMakeLists.txt から括り出したもの。MuJoCo を取得しない別の
# プロジェクトである simulator/unity/native/CMakeLists.txt が、まったく同じ翻訳
# 単位を集められるようにするためである。変数名も中身も、直書きされていたときの
# ままにしてあるので、既存の各ターゲットのソース一覧は移動前と同じである。
#
# MOVING THIS FILE MEANS FIXING THE RELATIVE PATHS INSIDE IT. The paths below
# are written relative to THIS file's directory (simulator/sils/cmake/), so a
# move to another depth changes how many levels each `../` has to climb. CMake
# will not warn: a glob that matches nothing simply yields an empty list, and
# the build then fails much later with undefined symbols.
# このファイルを別のディレクトリへ移すときは、中の相対パスの段数を直すこと。
# 下のパスは**このファイル**のディレクトリ（simulator/sils/cmake/）からの相対で
# 書いてあるので、深さの違う場所へ移すと `../` が登るべき段数が変わる。CMake は
# 警告しない。何にも一致しない glob はただ空の一覧になり、ビルドはずっと後に
# なって未定義記号で落ちる。
#
# The includer must define VN (the firmware/vehicle directory) beforehand.
# It sets: EMU_FW_SRCS, EMU_FW_INCS, CMAKE_C_STANDARD.
# 読み込む側は VN（firmware/vehicle のディレクトリ）を先に定義しておくこと。
# 設定するもの: EMU_FW_SRCS・EMU_FW_INCS・CMAKE_C_STANDARD。
#
# @design docs/plans/unity-simulator.md §4 設計上の決定（既存コードへの変更）
# =============================================================================

  # All firmware sources under main/tasks/components, EXCLUDING the VL53L3CX
  # vendor docs/ (STM32 CubeIDE examples) and any examples/tests/build output.
  # main/tasks/components の全ソース。VL53 の docs/（STM32例）・examples/test は除外。
  file(GLOB_RECURSE EMU_FW_SRCS CONFIGURE_DEPENDS
       ${VN}/main/*.cpp ${VN}/main/*.c
       ${VN}/tasks/*.cpp ${VN}/tasks/*.c
       ${VN}/components/*.cpp ${VN}/components/*.c)
  list(FILTER EMU_FW_SRCS EXCLUDE REGEX "/(docs|examples|test|build)/|_test\\.|/CubeIDE")

  # The C HAL/driver sources (BMI270, PMW3901, VL53L3CX BareDriver) compile as C
  # against the C-compatible host shims — exactly as on real ESP-IDF. The .cpp
  # firmware compiles as C++. (Earlier attempt compiled .c as C++ and hit
  # extern "C" + C++-stdlib clashes; C-compiling the C drivers is the right fix.)
  # C の HAL/ドライバは C 互換シムに対し C でコンパイル（実 ESP-IDF と同じ）。
  set(CMAKE_C_STANDARD 11)

  # Public include dirs of every component + the driver-internal subdirs.
  # 各コンポーネントの公開 include ＋ ドライバ内部の src サブディレクトリ。
  file(GLOB EMU_FW_INCS LIST_DIRECTORIES true ${VN}/components/*/include)
  list(APPEND EMU_FW_INCS
       ${VN}/components/sf_hal_vl53l3cx/include/vl53lx
       ${VN}/components/sf_hal_vl53l3cx/src
       ${VN}/components/sf_hal_vl53l3cx/src/vl53lx
       ${VN}/components/sf_hal_bmi270/src
       ${VN}/components/sf_hal_pmw3901/src
       ${VN}/main ${VN}/tasks
       ${CMAKE_CURRENT_LIST_DIR}/../../../firmware/common/protocol/include)  # shared protocol (sf_comm)
