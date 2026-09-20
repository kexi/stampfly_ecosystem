/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file sfu_shutdown_check.cpp
 * @brief Proves `sfu_shutdown` always returns and always reclaims its threads,
 *        from every point in the module's lifetime and from any thread.
 *        `sfu_shutdown` が、モジュールの生涯のどの時点からでも、どのスレッドから
 *        でも、必ず戻り必ずスレッドを回収することを確かめる。
 *
 * WHY this check exists / なぜこの確認が在るか
 * ------------------------------------------------------------------------
 * The Unity editor hung in `sfu_shutdown`. Fourteen task threads sat in
 * `_pthread_cond_wait` under `ulTaskNotifyTake` while the main thread waited in
 * `std::thread::join`, forever. It was NOT anything Unity adds: the trigger is
 * `sfu_boot` followed by `sfu_shutdown` with too few `sfu_step` calls in
 * between, which `SimLoop.BootFirmware` produces on its own because it boots and
 * returns without stepping that frame.
 *
 * `Scheduler::stop_all` made ONE pass over the task list, granting the run-token
 * to each task that had not exited. That is enough only for a task already
 * parked inside `block_current`, where the `StopTask` throw lives. A task whose
 * thread had never been granted the token was still in `run_task_thread`'s first
 * wait, before the firmware's task function; granting there made it run its
 * setup and park in a blocking primitive for the first time, needing a SECOND
 * grant that the single pass never made. `run()` never meets this because its
 * loop has already run every task before it tears down.
 *
 * Unity のエディタが `sfu_shutdown` で固まった。14 本のタスクのスレッドが
 * `ulTaskNotifyTake` の下の `_pthread_cond_wait` に停まり、主スレッドは
 * `std::thread::join` で永久に待っていた。Unity が加えるものが原因ではない。
 * 引き金は `sfu_boot` の後、間に `sfu_step` を十分に挟まずに `sfu_shutdown` を
 * 呼ぶことで、`SimLoop.BootFirmware` は起動してその frame では刻まずに戻るため、
 * 自力でその状況を作る。
 *
 * `Scheduler::stop_all` はタスクの一覧を **1 周**し、終了していないものへ実行
 * トークンを渡していた。それで足りるのは、`StopTask` の throw が在る
 * `block_current` の中で既に待機しているタスクだけである。一度もトークンを
 * 渡されていないスレッドは `run_task_thread` の最初の待機、ファームのタスク関数
 * より前に居り、そこで渡すと初めて setup を走らせてブロッキングプリミティブで
 * 待機する ― **2 度目の**付与が要るが、1 周はそれを行わない。`run()` がこれに
 * 出会わないのは、後始末の前にループが既に全タスクを走らせているからである。
 *
 * What this checks / 確かめること
 * ------------------------------------------------------------------------
 *   1. Shutdown returns for EVERY tick count from 0 upward, 0 above all — that
 *      is the count the editor hit, and the one a single pass cannot unwind.
 *   2. Shutdown returns when called from a DIFFERENT thread than the one that
 *      booted and stepped.
 *   3. Every repetition reclaims its threads: the process thread count comes
 *      back to what it was, so a host that plays over and over does not climb.
 *   4. All of it with TWO copies of the dylib open at once, as the editor has
 *      after a second play session.
 *
 *   1. 刻みの回数 0 から順に、**どの回数でも**終了が戻ること。とりわけ 0 ―
 *      エディタが当たった回数であり、1 周では巻き戻せない回数である
 *   2. 起動して刻んだスレッドとは**別の**スレッドから呼んでも戻ること
 *   3. 繰り返しのたびにスレッドを回収すること。プロセスのスレッド数が元へ戻り、
 *      再生を繰り返すホストで増え続けないこと
 *   4. 以上を、dylib の複写を**2 つ**同時に開いた状態で行うこと。2 回目の再生の
 *      後のエディタがそうなっているからである
 *
 * Usage / 使い方:
 *   sfu_shutdown_check <dylib path> [repeats, default 10]
 *
 * @design docs/plans/unity-simulator.md §5 段階 2
 */

#include <dlfcn.h>
#include <mach/mach.h>

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <unistd.h>

#include "sfu_api.h"

namespace {

/// A shutdown that takes longer than this has not been bounded, whatever it
/// eventually returns. Generous next to the ~1 ms a healthy teardown costs, and
/// far below the point at which a person would call the editor hung.
/// これより長く掛かる終了は、最終的に戻るとしても上限が効いていない。健全な後始末
/// の約 1 ms に対しては十分に緩く、人がエディタを「固まった」と呼ぶ域よりはるかに
/// 短い。
constexpr double kShutdownBudgetMs = 2000.0;

/// How many ticks each repetition runs before shutting down. Zero comes first
/// and is the case that used to hang; the rest walk the window in which tasks
/// reach their first blocking primitive one after another.
/// 各繰り返しが終了前に走らせる刻みの回数。0 が先頭で、かつて固まった場合である。
/// 残りは、タスクが次々と最初のブロッキングプリミティブへ到達していく窓を歩く。
constexpr int kTickCounts[] = {0, 1, 2, 3, 5, 10, 40, 400};
constexpr int kTickCountN = (int)(sizeof(kTickCounts) / sizeof(kTickCounts[0]));

/// The three entry points this check needs out of one opened copy.
/// 開いた複写 1 つから、この確認が要る入口 3 つ。
struct Module {
    void*   handle = nullptr;
    int32_t (*boot)(const SfuConfig*) = nullptr;
    int32_t (*step)(const SfuStepIn*, SfuStepOut*) = nullptr;
    int32_t (*shutdown)(void) = nullptr;
};

/// The process's live thread count, straight from the kernel. Counting threads
/// rather than trusting shutdown's return value is what catches a teardown that
/// says SFU_OK and still leaves its threads parked.
/// プロセスの生きているスレッド数を、カーネルから直に取る。終了の戻り値を信じずに
/// スレッドを数えることが、SFU_OK と言いながらスレッドを待機させたままにする後始末
/// を捕まえる手立てである。
int live_thread_count()
{
    thread_act_array_t threads = nullptr;
    mach_msg_type_number_t count = 0;
    if (task_threads(mach_task_self(), &threads, &count) != KERN_SUCCESS) return -1;
    vm_deallocate(mach_task_self(), (vm_address_t)threads,
                  count * sizeof(thread_act_t));
    return (int)count;
}

/// Wait for the thread count to come back to `expected`, up to a short budget.
///
/// A settle is necessary and does not weaken the check. `std::thread::join`
/// returns when the thread's body is done, but the kernel takes the Mach thread
/// port down a moment later, so an immediate count can still see a thread that
/// has already finished. Measured: sampling with no settle reports one extra
/// thread in roughly one run out of ten. A teardown that genuinely leaks holds
/// its threads parked in `_pthread_cond_wait` forever, so it never settles and
/// is still caught — it just costs this budget to say so.
///
/// スレッド数が `expected` へ戻るのを、短い上限まで待つ。
///
/// 落ち着くのを待つ必要があり、それで検査が緩むことはない。`std::thread::join` は
/// スレッドの本体が終わった時点で戻るが、カーネルが Mach のスレッドポートを畳むのは
/// その少し後なので、直後に数えると既に終わったスレッドがまだ見えうる。実測では、
/// 待たずに数えると 10 回に 1 回ほど 1 本多く報告した。本当に漏らす後始末は
/// スレッドを `_pthread_cond_wait` で永久に待機させるので、決して落ち着かず、
/// 引き続き捕まる ― それを言うのにこの上限ぶんの時間が掛かるだけである。
bool thread_count_settles_to(int expected)
{
    constexpr int kAttempts = 100;              // 100 x 20 ms = 2 s
    constexpr auto kPause = std::chrono::milliseconds(20);
    for (int attempt = 0; attempt < kAttempts; ++attempt) {
        if (live_thread_count() == expected) return true;
        std::this_thread::sleep_for(kPause);
    }
    return false;
}

/// Copy the dylib to a name of its own and open THAT, which is exactly what
/// `EditorFirmware` does: one module is one power-on, and a second `dlopen` of
/// the same path would hand back the image already loaded.
/// dylib を自分だけの名前へ複写し、**その複写**を開く。`EditorFirmware` が行うのと
/// 同じである。1 モジュール＝1 回の電源投入であり、同じパスを 2 度目に `dlopen`
/// しても既に読み込まれている像が返るだけだからである。
bool open_copy(const char* source, int serial, Module& out, std::string& path)
{
    char name[1024];
    std::snprintf(name, sizeof(name), "/tmp/sfu_shutdown_check_%d_%d.dylib",
                  (int)getpid(), serial);
    path = name;

    char command[2200];
    std::snprintf(command, sizeof(command), "cp '%s' '%s'", source, name);
    if (std::system(command) != 0) {
        std::fprintf(stderr, "[shutdown_check] could not copy %s\n", source);
        return false;
    }

    out.handle = dlopen(name, RTLD_NOW | RTLD_LOCAL);
    if (out.handle == nullptr) {
        std::fprintf(stderr, "[shutdown_check] dlopen %s: %s\n", name, dlerror());
        return false;
    }
    out.boot = (int32_t (*)(const SfuConfig*))dlsym(out.handle, "sfu_boot");
    out.step = (int32_t (*)(const SfuStepIn*, SfuStepOut*))dlsym(out.handle, "sfu_step");
    out.shutdown = (int32_t (*)(void))dlsym(out.handle, "sfu_shutdown");

    const bool resolved = (out.boot != nullptr && out.step != nullptr &&
                           out.shutdown != nullptr);
    if (!resolved) {
        std::fprintf(stderr, "[shutdown_check] a sfu_* entry point is missing\n");
        return false;
    }
    return true;
}

/// Close the copy and delete the file, so a long run does not fill /tmp.
/// 複写を閉じ、ファイルを消す。長く回しても /tmp を埋めないためである。
void close_copy(Module& module, const std::string& path)
{
    if (module.handle != nullptr) dlclose(module.handle);
    module.handle = nullptr;
    unlink(path.c_str());
}

/// Boot and run `ticks` ticks. The sticks sit at centre: this check is about
/// teardown, not about flying, and a centred stick reaches every task's first
/// blocking primitive just as a flying one does.
/// 起動して `ticks` 回刻む。スティックは中央のままにする。この確認は後始末に
/// ついてのものであって飛行についてのものではなく、中央のスティックでも飛ぶ
/// スティックと同じく各タスクの最初のブロッキングプリミティブへ届くからである。
bool boot_and_step(Module& module, int ticks)
{
    SfuConfig config{};
    config.struct_size    = sizeof(SfuConfig);
    config.host_owns_body = 0;
    config.start_height_m = 0.013f;

    const int32_t booted = module.boot(&config);
    if (booted != SFU_OK) {
        std::fprintf(stderr, "[shutdown_check] sfu_boot returned %d\n", (int)booted);
        return false;
    }

    SfuStepIn  in{};
    SfuStepOut out{};
    in.struct_size  = sizeof(SfuStepIn);
    out.struct_size = sizeof(SfuStepOut);
    in.dt_us        = 2500;
    in.rc_throttle  = 2048;
    in.rc_roll      = 2048;
    in.rc_pitch     = 2048;
    in.rc_yaw       = 2048;

    for (int tick = 0; tick < ticks; ++tick) {
        const int32_t stepped = module.step(&in, &out);
        if (stepped != SFU_OK) {
            std::fprintf(stderr, "[shutdown_check] sfu_step returned %d at tick %d\n",
                         (int)stepped, tick);
            return false;
        }
    }
    return true;
}

/// What one power-on produced. / 1 回の電源投入が出したもの。
struct Outcome {
    bool   ok = false;
    double shutdown_ms = 0.0;
};

/// One power-on: open a copy, boot, step, shut down FROM ANOTHER THREAD, close.
/// The shutdown runs on a thread of its own on purpose. A host is free to tear
/// down from wherever it likes, and a teardown that only works on the thread
/// that stepped would be a trap waiting for the first host that does otherwise.
/// 1 回の電源投入。複写を開き、起動し、刻み、**別のスレッドから**終了し、閉じる。
/// 終了を専用のスレッドで走らせるのは意図的である。ホストはどこから片付けてもよく、
/// 刻んだスレッドでしか動かない後始末は、そうしない最初のホストを待つ罠になる。
Outcome power_cycle(const char* source, int serial, int ticks)
{
    Outcome outcome;

    Module     module;
    std::string path;
    if (!open_copy(source, serial, module, path)) return outcome;

    // Boot and step on a worker, so the thread that shuts down below is not the
    // one that ran the firmware.
    // 起動と刻みを作業スレッドで行い、下で終了するスレッドがファームを走らせた
    // ものとは別になるようにする。
    bool flew = false;
    {
        std::thread worker([&] { flew = boot_and_step(module, ticks); });
        worker.join();
    }
    if (!flew) {
        close_copy(module, path);
        return outcome;
    }

    int32_t   status = SFU_OK;
    const auto started = std::chrono::steady_clock::now();
    {
        std::thread closer([&] { status = module.shutdown(); });
        closer.join();
    }
    outcome.shutdown_ms = std::chrono::duration<double, std::milli>(
                              std::chrono::steady_clock::now() - started).count();

    close_copy(module, path);

    if (status != SFU_OK) {
        std::fprintf(stderr, "[shutdown_check] sfu_shutdown returned %d (ticks=%d)\n",
                     (int)status, ticks);
        return outcome;
    }
    if (outcome.shutdown_ms > kShutdownBudgetMs) {
        std::fprintf(stderr,
                     "[shutdown_check] sfu_shutdown took %.1f ms (ticks=%d), over the "
                     "%.0f ms budget — the teardown is not bounded\n",
                     outcome.shutdown_ms, ticks, kShutdownBudgetMs);
        return outcome;
    }

    outcome.ok = true;
    return outcome;
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc < 2) {
        std::fprintf(stderr,
                     "usage: sfu_shutdown_check <dylib path> [repeats, default 10]\n");
        return 2;
    }
    const char* source  = argv[1];
    const int   repeats = (argc > 2) ? std::atoi(argv[2]) : 10;

    // A second copy, opened first and kept open for the whole run and never shut
    // down. This is the editor after a second play session: an earlier module
    // still loaded, with its own fourteen threads parked. Every teardown below
    // therefore has to find its OWN threads among them.
    // もう 1 つの複写。先に開き、実行の間ずっと開いたまま、一度も終了しない。
    // 2 回目の再生の後のエディタがこれである。前のモジュールが読み込まれたまま、
    // 自分の 14 本のスレッドを待機させている。よって以下の後始末はいずれも、
    // それらの中から**自分の**スレッドを見つけ出さなければならない。
    Module      resident;
    std::string resident_path;
    if (!open_copy(source, 9000, resident, resident_path)) return 2;
    if (!boot_and_step(resident, 40)) {
        close_copy(resident, resident_path);
        return 2;
    }
    const int resident_threads = live_thread_count();
    std::printf("[shutdown_check] a resident module stays booted: %d live threads\n",
                resident_threads);

    bool passed  = true;
    int  serial  = 0;
    double worst = 0.0;

    for (int repeat = 0; repeat < repeats && passed; ++repeat) {
        for (int index = 0; index < kTickCountN && passed; ++index) {
            const int ticks = kTickCounts[index];

            const Outcome outcome = power_cycle(source, ++serial, ticks);
            if (!outcome.ok) {
                passed = false;
                break;
            }
            if (outcome.shutdown_ms > worst) worst = outcome.shutdown_ms;

            // Back to the resident module's thread count, every time. A teardown
            // that returned but left its threads would show up here as a count
            // that climbs with each repetition.
            // 毎回、常駐モジュールのスレッド数へ戻ること。戻りはしたがスレッドを
            // 残す後始末は、繰り返しごとに増える数としてここに現れる。
            if (!thread_count_settles_to(resident_threads)) {
                std::fprintf(stderr,
                             "[shutdown_check] %d live threads after repeat %d "
                             "(ticks=%d); expected %d — threads are leaking\n",
                             live_thread_count(), repeat, ticks, resident_threads);
                passed = false;
                break;
            }
        }
    }

    const int ended_with = live_thread_count();
    close_copy(resident, resident_path);

    std::printf("[shutdown_check] %d power cycles over %d tick counts "
                "(0, 1, 2, 3, 5, 10, 40, 400)\n",
                serial, kTickCountN);
    std::printf("[shutdown_check] slowest shutdown %.1f ms (budget %.0f ms)\n",
                worst, kShutdownBudgetMs);
    std::printf("[shutdown_check] live threads %d at the start, %d at the end\n",
                resident_threads, ended_with);
    std::printf("[shutdown_check] %s\n", passed ? "OK" : "FAILED");
    return passed ? 0 : 2;
}
