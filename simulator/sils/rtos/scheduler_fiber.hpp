/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file scheduler_fiber.hpp
 * @brief Thread-free drop-in replacement for simulator/sils/rtos/scheduler.hpp.
 *        simulator/sils/rtos/scheduler.hpp の、スレッドを使わない差し替え版。
 *
 * IDENTICAL public API to the thread version — Scheduler::instance/create/
 * set_on_advance/run/now_us/trace, the blocking primitives, and the periodic
 * timers — so every consumer (smoke/rtos_smoke.cpp, rtos/esp_timer_shim.cpp,
 * emu/emu_main.cpp) compiles against it UNMODIFIED. It is selected purely by
 * putting this directory BEFORE simulator/sils/rtos on the include path and
 * linking scheduler_fiber.cpp instead of scheduler.cpp. No existing file is
 * edited.
 *
 * 公開 API はスレッド版と同一 — Scheduler::instance/create/set_on_advance/run/
 * now_us/trace、ブロッキングプリミティブ、周期タイマ — なので利用側
 * （smoke/rtos_smoke.cpp、rtos/esp_timer_shim.cpp、emu/emu_main.cpp）は無改変で
 * コンパイルできる。選択は「このディレクトリを include パス上で
 * simulator/sils/rtos より前に置き、scheduler.cpp の代わりに scheduler_fiber.cpp を
 * リンクする」だけで行う。既存ファイルは1つも編集しない。
 *
 * WHY a fiber version: WebAssembly in a browser has no std::thread unless the
 * page is cross-origin isolated (COOP/COEP headers), which GitHub Pages cannot
 * set. Each task instead gets its own stack and a context switch: block_current
 * switches task→scheduler, granting the token switches scheduler→task. The
 * cooperative run-token model of the thread version is preserved exactly — only
 * the mechanism for parking a task changes — so the SCHEDULE is bit-identical.
 *
 * なぜ fiber 版か: ブラウザの WebAssembly は、ページが cross-origin isolated
 * （COOP/COEP ヘッダ）でない限り std::thread を使えず、GitHub Pages はそのヘッダを
 * 付けられない。そこで各タスクに専用スタックと文脈切り替えを持たせる:
 * block_current はタスク→スケジューラ、トークン付与はスケジューラ→タスクへ切り替える。
 * スレッド版の協調的な実行トークンのモデルはそのまま保たれ、タスクを待機させる
 * 機構だけが変わるので、スケジュール自体はビット単位で同一になる。
 *
 * Mechanism: emscripten_fiber_t under Emscripten (-sASYNCIFY), ucontext
 * elsewhere (so the same logic can be validated natively).
 * 実装機構: Emscripten では emscripten_fiber_t（-sASYNCIFY）、それ以外では
 * ucontext（同じ論理をネイティブでも検証できるようにするため）。
 *
 * No mutex, no condition variable, no wall-clock hang budget: with a single
 * stack of control there is nothing to race and nothing to dead-lock on.
 * mutex も条件変数も壁時計のハング上限も持たない: 制御の流れが1本なので、
 * 競合するものもデッドロックするものも存在しない。
 *
 * @design docs/plans/unity-simulator.md — 段階 1(a) / 段階 2, thread decision
 */

#pragma once

#include <cstdint>
#include <cstddef>
#include <functional>
#include <string>
#include <vector>

#ifdef __EMSCRIPTEN__
#include <emscripten/fiber.h>
#else
// macOS gates the ucontext routines behind _XOPEN_SOURCE (they are deprecated
// in POSIX 2008), so the NATIVE validation build must be compiled with
// -D_XOPEN_SOURCE=700. It cannot be defined here: by the time this header is
// read, other system headers have already been included and the macro would
// come too late. The wasm build uses emscripten_fiber_t and never takes this
// path.
// macOS は ucontext 群を _XOPEN_SOURCE の後ろに隠している（POSIX 2008 で非推奨の
// ため）。よってネイティブ検証ビルドは -D_XOPEN_SOURCE=700 付きでコンパイルする
// 必要がある。ここで定義することはできない: 本ヘッダが読まれる時点で他のシステム
// ヘッダが既に取り込まれており、マクロが手遅れになるため。wasm ビルドは
// emscripten_fiber_t を使うのでこの経路を通らない。
#include <ucontext.h>
#endif

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace sils {
namespace rtos {

// Kept for source compatibility with the thread version's teardown path. The
// fiber scheduler never throws it (a parked fiber is simply abandoned at
// shutdown — its stack is freed without unwinding, which is safe because the
// firmware task bodies hold only trivially destructible locals, the same
// property the thread version's StopTask unwinding relies on).
//
// スレッド版の後始末経路とのソース互換のために残す。fiber 版はこれを投げない
// （待機中の fiber はシャットダウン時に単に放棄し、巻き戻さずスタックを解放する。
// 本体タスクのローカルが自明に破棄可能な型だけであるため安全で、これはスレッド版の
// StopTask による巻き戻しが依拠しているのと同じ性質）。
struct StopTask {};

// Run state of a task. Identical to the thread version.
// タスクの実行状態。スレッド版と同一。
enum class TaskState {
    Ready,         // can run now (newly created, or notification arrived)
    Running,       // currently holds the token
    BlockedDelay,  // sleeping until wake_us
    BlockedNotify, // waiting for a notification
    Finished,      // returned / deleted
};

// One emulated task. Same scheduling fields as the thread version; the
// std::thread + condition_variable are replaced by a fiber and its stack.
// エミュレートされたタスク1個。スケジューリング用のフィールドはスレッド版と同じで、
// std::thread ＋ 条件変数を fiber とそのスタックに置き換えてある。
struct Task {
    std::string name;
    TaskFunction_t fn = nullptr;
    void* param = nullptr;
    UBaseType_t priority = 0;
    uint32_t id = 0;                 // creation order — deterministic tie-break

    TaskState state = TaskState::Ready;
    int64_t wake_us = 0;             // for BlockedDelay
    uint32_t notify_count = 0;       // notification value
    bool exited = false;             // fiber has left its body
    bool started = false;            // fiber has been entered at least once

#ifdef __EMSCRIPTEN__
    emscripten_fiber_t fiber = {};
    void* asyncify_stack = nullptr;  // Asyncify's own unwind buffer
    size_t asyncify_stack_size = 0;
#else
    ucontext_t ctx = {};
#endif
    void*  stack = nullptr;          // the task's C stack
    size_t stack_size = 0;
};

// One scheduling event (for the trace / determinism check). Identical layout.
// スケジューリング事象1個（トレース・決定論チェック用）。レイアウトは同一。
struct TraceEvent {
    int64_t time_us;
    uint32_t task_id;
};

// The cooperative scheduler. One instance per run (= one power-on).
// 協調スケジューラ。実行ごとに1インスタンス（＝1回の電源投入）。
class Scheduler {
public:
    static Scheduler& instance();

    // Register a task (does not run it yet). Returns its handle.
    // タスクを登録する（まだ動かさない）。ハンドルを返す。
    TaskHandle_t create(TaskFunction_t fn, void* param,
                        UBaseType_t priority, const char* name);

    // Optional per-step hook, called at the current virtual time before
    // picking the next task (scenario injection).
    // 任意のステップフック。次タスク選択前に現在仮想時刻で呼ばれる（シナリオ注入）。
    void set_on_advance(std::function<void(int64_t now_us)> hook);

    // Run the cooperative loop until the virtual clock reaches max_sim_us.
    // 仮想時計が max_sim_us に達するまで協調ループを回す。
    void run(int64_t max_sim_us);

    // Run until the virtual clock reaches target_us, then RETURN to the caller
    // (no teardown), so the host can interleave its own physics step. If the
    // next wake-up lies beyond target_us, the clock is set to target_us and
    // on_advance is called there before returning — the host therefore always
    // observes exactly the time it asked for.
    //
    // 仮想時計が target_us に達するまで回し、呼び出し側へ戻る（後始末はしない）。
    // ホスト側が自前の物理ステップを挟めるようにするため。次の起床が target_us を
    // 超える場合は、時計を target_us にして on_advance をそこで呼んでから戻る —
    // よってホストは常に、要求した時刻ちょうどを観測する。
    void run_until(int64_t target_us);

    // Free every task's stack. After this the Scheduler must not be reused.
    // 各タスクのスタックを解放する。以後この Scheduler は再利用できない。
    void shutdown();

    int64_t now_us() const { return now_us_; }
    const std::vector<TraceEvent>& trace() const { return trace_; }

    // --- Primitives called by the FreeRTOS shims (task.h) ---
    // --- FreeRTOS シム（task.h）から呼ばれるプリミティブ ---
    void delay_until_us(int64_t wake_us);
    uint32_t notify_take(bool clear_on_exit, int64_t timeout_us);
    void notify_give(Task* target);
    void delete_self();

    TaskHandle_t current_handle();

    // --- Periodic timers (driven by the esp_timer shim) ---
    // --- 周期タイマ（esp_timer シムが駆動）---
    using TimerCallback = void (*)(void*);
    int  add_periodic(TimerCallback cb, void* arg, int64_t period_us);
    void remove_periodic(int id);

    // Entry trampoline for a task's fiber. Public only so the C-linkage
    // trampoline in scheduler_fiber.cpp can reach it.
    // タスク fiber の入口トランポリン。scheduler_fiber.cpp の C リンケージの
    // トランポリンから届くようにするためだけに public にしてある。
    void task_entry(Task* self);

private:
    Scheduler() = default;

    Task* pick_ready();
    int64_t earliest_wake() const;
    int64_t earliest_timer_fire() const;
    void fire_due_timers();
    void block_current(TaskState new_state);   // task → scheduler switch
    void grant(Task* task);                    // scheduler → task switch
    bool step_once(int64_t horizon_us);        // one scheduling decision

    struct PeriodicTimer {
        TimerCallback cb = nullptr;
        void* arg = nullptr;
        int64_t period_us = 0;
        int64_t next_fire_us = 0;
        bool active = false;
    };

    std::vector<Task*> tasks_;
    Task* running_ = nullptr;
    int64_t now_us_ = 0;
    bool started_ = false;               // on_advance(0) already delivered
    std::function<void(int64_t)> on_advance_;
    std::vector<TraceEvent> trace_;
    std::vector<PeriodicTimer> timers_;

#ifdef __EMSCRIPTEN__
    emscripten_fiber_t sched_fiber_ = {};   // the fiber tasks switch back to
    void* sched_asyncify_stack_ = nullptr;
#else
    ucontext_t sched_ctx_ = {};
#endif
};

}  // namespace rtos
}  // namespace sils
