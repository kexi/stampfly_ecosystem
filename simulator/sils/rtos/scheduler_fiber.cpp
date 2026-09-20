/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (SILS host bench — thread-free variant).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file scheduler_fiber.cpp
 * @brief Thread-free cooperative scheduler + the same FreeRTOS active-surface
 *        shims, for WebAssembly (and for native validation of the logic).
 *        スレッドを使わない協調スケジューラ ＋ 同じ FreeRTOS 能動面のシム。
 *        WebAssembly 用（および同じ論理をネイティブで検証するため）。
 *
 * Drop-in alternative to rtos/scheduler.cpp. Link EITHER this file OR
 * scheduler.cpp, never both — they define the same symbols. The scheduling
 * LOGIC is a line-by-line transcription of scheduler.cpp: same pick_ready
 * (highest priority, ties by lowest id), same virtual-clock advance (earliest
 * of a delay wake and a periodic-timer fire), same timer firing order
 * (registration order, before pick_ready), same on_advance placement (once at
 * t=0, then after every clock advance). Therefore the trace is identical.
 *
 * rtos/scheduler.cpp の差し替え版。本ファイルか scheduler.cpp の「どちらか一方」を
 * リンクする（同じ記号を定義するので両方は不可）。スケジューリングの論理は
 * scheduler.cpp の行単位の書き写し: pick_ready（最高優先度・同点は最小 id）、
 * 仮想時計の進め方（遅延起床と周期タイマ作動のうち早い方）、タイマの作動順
 * （登録順、pick_ready の前）、on_advance の位置（t=0 で1回、以後は時計を進めた
 * 直後）がすべて同じ。よってトレースは一致する。
 *
 * What changes is only HOW a task parks. The thread version has each task on
 * its own std::thread and hands the run-token over with a condition variable;
 * here each task owns a stack and control moves by an explicit context switch.
 * Both keep the same invariant — at most one task runs at any instant — but the
 * fiber version gets it structurally (there is one flow of control) instead of
 * by locking, so no mutex, no condition variable and no hang budget are needed.
 *
 * 変わるのはタスクの「待機のしかた」だけ。スレッド版は各タスクを個別の
 * std::thread に載せ条件変数で実行トークンを受け渡すが、ここでは各タスクが
 * スタックを持ち、明示的な文脈切り替えで制御が移る。「常に高々1タスクしか動かない」
 * という不変条件は両者で同じだが、fiber 版はそれを（制御の流れが1本なので）
 * 構造的に得る。ロックによらないため mutex も条件変数もハング上限も要らない。
 *
 * @design docs/plans/unity-simulator.md — 段階 1(a) 技術検証, 段階 2 ネイティブコア
 */

// The fiber Scheduler's own declarations. Named scheduler_fiber.hpp (not
// scheduler.hpp) because this file sits in the same directory as the thread
// version's header, where a quoted "scheduler.hpp" would always resolve to the
// neighbour regardless of the include path. Consumers still see the fiber
// declarations as plain "scheduler.hpp", via the drop-in copy in
// simulator/unity/native/rtos_fiber/ placed earlier on the include path.
//
// fiber 版 Scheduler の宣言。scheduler.hpp ではなく scheduler_fiber.hpp という名に
// するのは、本ファイルがスレッド版ヘッダと同じディレクトリにあり、引用符の
// "scheduler.hpp" は include パスに関わらず必ず隣のそれを指すため。
// 利用側はこれまで通り "scheduler.hpp" として fiber 版の宣言を見る —
// simulator/unity/native/rtos_fiber/ の差し替え用の写しを include パスの前に置く。
#include "scheduler_fiber.hpp"
#include "esp_timer.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>

namespace sils {
namespace rtos {

namespace {

// Stack for one task fiber. The firmware's own stack depths (config::STACK_*)
// are ESP32 byte counts for a 32-bit target; on a 64-bit host frames are wider,
// so a uniform, generous size is used instead of honouring them. Measured peak
// usage of the deepest task (ControlTask through the ESKF) is far below this.
//
// タスク fiber 1個ぶんのスタック。本体の stack 指定（config::STACK_*）は 32bit
// 向けの ESP32 バイト数で、64bit ホストではフレームが太るため、それに従わず一律で
// 余裕のある大きさを使う。最も深いタスク（ESKF を通る ControlTask）の実測ピークでも
// これを大きく下回る。
constexpr size_t kTaskStackSize = 1u << 20;   // 1 MiB

// Asyncify needs its own buffer per fiber to spill the wasm locals into when
// control leaves that fiber. Unused (and not allocated) on native builds.
// Asyncify は、制御が fiber を離れるときに wasm のローカルを退避するための
// 専用バッファを fiber ごとに必要とする。ネイティブビルドでは使わない（確保もしない）。
constexpr size_t kAsyncifyStackSize = 1u << 16;   // 64 KiB

// The task whose fiber is currently executing (the fiber-local "self"). The
// thread version keeps this in thread_local storage; with one flow of control
// a single global is exactly equivalent and cheaper.
// いま fiber が実行中のタスク（fiber ローカルの self に相当）。スレッド版は
// thread_local に持つが、制御の流れが1本なら単一のグローバルで完全に等価で、かつ安い。
Task* g_self = nullptr;

#ifndef __EMSCRIPTEN__
// ucontext's makecontext passes only int arguments, so the Task* travels
// through a file-scope slot instead. grant() writes it immediately before the
// switch that enters a fiber for the first time, and the trampoline reads it as
// its first action — with one flow of control nothing can run in between, so a
// single slot is enough for any number of tasks.
// ucontext の makecontext は int 引数しか渡せないため、Task* はファイルスコープの
// 受け渡し口を経由する。grant() が「fiber に初めて入る切り替え」の直前に書き込み、
// トランポリンが最初の動作として読む。制御の流れが1本なのでその間に何も割り込めず、
// タスクが何個あっても受け渡し口は1つで足りる。
Task* g_entry_arg = nullptr;
#endif

}  // namespace

// Entry trampoline: runs the unmodified firmware task body on this fiber's own
// stack. Infinite-loop tasks never return; at shutdown their stack is simply
// released (see StopTask's comment in the header).
// 入口トランポリン: 無改変の本体タスク関数を、この fiber 専用スタック上で動かす。
// 無限ループのタスクは戻らない。シャットダウン時はスタックを解放するだけ
// （理由はヘッダの StopTask のコメント参照）。
void Scheduler::task_entry(Task* self)
{
    g_self = self;
    self->fn(self->param);

    // The body returned on its own (a task that is not an infinite loop).
    // Mark it finished and hand control back; it is never granted again.
    // 本体が自力で戻った（無限ループでないタスク）。終了として印をつけ制御を返す。
    // 以後このタスクにトークンは渡らない。
    self->state = TaskState::Finished;
    self->exited = true;
    block_current(TaskState::Finished);
}

extern "C" void sils_fiber_trampoline(void* arg)
{
    Scheduler::instance().task_entry(static_cast<Task*>(arg));
}

#ifndef __EMSCRIPTEN__
namespace {
// makecontext entry (no arguments); picks the Task* up from g_entry_arg.
// makecontext の入口（引数なし）。Task* は g_entry_arg から受け取る。
void ucontext_trampoline()
{
    sils_fiber_trampoline(g_entry_arg);
}
}  // namespace
#endif

Scheduler& Scheduler::instance()
{
    static Scheduler s;
    return s;
}

TaskHandle_t Scheduler::create(TaskFunction_t fn, void* param,
                               UBaseType_t priority, const char* name)
{
    Task* task = new Task();
    task->name = name ? name : "task";
    task->fn = fn;
    task->param = param;
    task->priority = priority;
    task->id = static_cast<uint32_t>(tasks_.size());
    task->state = TaskState::Ready;

    // Allocate the fiber's stack now; the fiber itself is only entered when the
    // scheduler first grants it the token (matching the thread version, whose
    // thread parks before entering the task body).
    // fiber のスタックはここで確保する。fiber 自体はスケジューラが最初にトークンを
    // 与えたときに初めて入る（スレッド版のスレッドが本体に入る前に待機するのと同じ）。
    task->stack_size = kTaskStackSize;
    task->stack = std::malloc(task->stack_size);
    if (task->stack == nullptr) {
        std::fprintf(stderr, "[scheduler_fiber] FATAL: out of memory for '%s' stack\n",
                     task->name.c_str());
        std::abort();
    }

#ifdef __EMSCRIPTEN__
    task->asyncify_stack_size = kAsyncifyStackSize;
    task->asyncify_stack = std::malloc(task->asyncify_stack_size);
    if (task->asyncify_stack == nullptr) {
        std::fprintf(stderr, "[scheduler_fiber] FATAL: out of memory for '%s' asyncify stack\n",
                     task->name.c_str());
        std::abort();
    }
    emscripten_fiber_init(&task->fiber, sils_fiber_trampoline, task,
                          task->stack, task->stack_size,
                          task->asyncify_stack, task->asyncify_stack_size);
#else
    getcontext(&task->ctx);
    task->ctx.uc_stack.ss_sp = task->stack;
    task->ctx.uc_stack.ss_size = task->stack_size;
    task->ctx.uc_link = nullptr;   // never falls off the end (see task_entry)
    makecontext(&task->ctx, ucontext_trampoline, 0);
#endif

    tasks_.push_back(task);
    return static_cast<TaskHandle_t>(task);
}

void Scheduler::set_on_advance(std::function<void(int64_t)> hook)
{
    on_advance_ = std::move(hook);
}

void Scheduler::block_current(TaskState new_state)
{
    // Give up the token and switch back to the scheduler's own stack. Control
    // resumes here when the scheduler next grants this task the token.
    // トークンを返し、スケジューラ自身のスタックへ切り替える。次にスケジューラが
    // このタスクにトークンを与えたとき、ここから再開する。
    Task* self = g_self;
    self->state = new_state;
    running_ = nullptr;

#ifdef __EMSCRIPTEN__
    emscripten_fiber_swap(&self->fiber, &sched_fiber_);
#else
    swapcontext(&self->ctx, &sched_ctx_);
#endif

    // Re-granted: this fiber is running again, so restore the fiber-local self.
    // 再付与された: この fiber が再び動いているので fiber ローカルの self を戻す。
    g_self = self;
    self->state = TaskState::Running;
}

void Scheduler::grant(Task* task)
{
    // Hand the token to `task` and switch to its stack. Returns here when the
    // task yields it back (block_current) — there is no waiting involved, the
    // switch itself is the hand-off.
    // トークンを `task` に渡し、そのスタックへ切り替える。タスクがトークンを返した
    // とき（block_current）ここへ戻る。待機は一切なく、切り替えそのものが受け渡し。
    running_ = task;
    task->state = TaskState::Running;

#ifdef __EMSCRIPTEN__
    // Register the scheduler's own (main) stack as a fiber before the first
    // swap. Without this, emscripten_fiber_swap has no valid context to save
    // into and the Asyncify unwind aborts on `unreachable`. Done lazily here
    // rather than in the constructor so it happens on whichever stack actually
    // runs the loop.
    // 最初の切り替えの前に、スケジューラ自身の（main の）スタックを fiber として
    // 登録する。これが無いと emscripten_fiber_swap に退避先の正しい文脈が無く、
    // Asyncify の巻き戻しが `unreachable` で異常終了する。実際にループを回す
    // スタック上で行うため、コンストラクタではなくここで遅延して実施する。
    if (sched_asyncify_stack_ == nullptr) {
        sched_asyncify_stack_ = std::malloc(kAsyncifyStackSize);
        if (sched_asyncify_stack_ == nullptr) {
            std::fprintf(stderr, "[scheduler_fiber] FATAL: out of memory for scheduler asyncify stack\n");
            std::abort();
        }
        emscripten_fiber_init_from_current_context(
            &sched_fiber_, sched_asyncify_stack_, kAsyncifyStackSize);
    }
    task->started = true;
    emscripten_fiber_swap(&sched_fiber_, &task->fiber);
#else
    // Hand the Task* to the ucontext trampoline just before its one and only
    // entry (makecontext cannot carry a pointer argument). See g_entry_arg.
    // ucontext のトランポリンへ、唯一の入口を通る直前に Task* を渡す
    // （makecontext はポインタ引数を運べない）。g_entry_arg 参照。
    if (!task->started) g_entry_arg = task;
    task->started = true;
    swapcontext(&sched_ctx_, &task->ctx);
#endif

    g_self = nullptr;   // back on the scheduler's own stack
}

void Scheduler::delay_until_us(int64_t wake_us)
{
    g_self->wake_us = wake_us;
    block_current(TaskState::BlockedDelay);
}

uint32_t Scheduler::notify_take(bool clear_on_exit, int64_t timeout_us)
{
    Task* self = g_self;
    if (self->notify_count == 0) {
        // Arm a timeout deadline so the scheduler can also wake us on time (not
        // only on a notification). INT64_MAX = wait forever (portMAX_DELAY).
        // タイムアウト期限を設定し、通知だけでなく時刻でも起床できるようにする。
        // INT64_MAX = 無限待ち（portMAX_DELAY）。
        self->wake_us = (timeout_us < 0) ? INT64_MAX : (now_us_ + timeout_us);
        block_current(TaskState::BlockedNotify);
    }
    uint32_t value = self->notify_count;
    self->notify_count = clear_on_exit ? 0
                          : (self->notify_count > 0 ? self->notify_count - 1 : 0);
    return value;
}

void Scheduler::notify_give(Task* target)
{
    // The giver keeps running (cooperative, no preempt); the target becomes
    // Ready and runs at the next scheduling point.
    // 与える側は動き続ける（協調・横取りなし）。対象は Ready になり次の
    // スケジューリング点で動く。
    target->notify_count++;
    if (target->state == TaskState::BlockedNotify) {
        target->state = TaskState::Ready;
    }
}

void Scheduler::delete_self()
{
    // The task removed itself (vTaskDelete(NULL)). Yield the token for good;
    // it is Finished, so pick_ready never selects it again.
    // タスクが自身を削除した（vTaskDelete(NULL)）。トークンを恒久的に返す。
    // Finished なので pick_ready が再び選ぶことはない。
    g_self->exited = true;
    block_current(TaskState::Finished);
}

Task* Scheduler::pick_ready()
{
    // Highest priority among Ready; ties broken by lowest id (deterministic).
    // Ready の中で最高優先度; 同点は最小 id（決定論的）。
    Task* best = nullptr;
    for (Task* task : tasks_) {
        if (task->state != TaskState::Ready) continue;
        if (best == nullptr || task->priority > best->priority ||
            (task->priority == best->priority && task->id < best->id)) {
            best = task;
        }
    }
    return best;
}

int64_t Scheduler::earliest_wake() const
{
    int64_t earliest = -1;
    for (const Task* task : tasks_) {
        const bool timed = task->state == TaskState::BlockedDelay ||
                           (task->state == TaskState::BlockedNotify && task->wake_us < INT64_MAX);
        if (!timed) continue;
        if (earliest < 0 || task->wake_us < earliest) {
            earliest = task->wake_us;
        }
    }
    return earliest;
}

int64_t Scheduler::earliest_timer_fire() const
{
    int64_t earliest = -1;
    for (const PeriodicTimer& t : timers_) {
        if (!t.active) continue;
        if (earliest < 0 || t.next_fire_us < earliest) earliest = t.next_fire_us;
    }
    return earliest;
}

int Scheduler::add_periodic(TimerCallback cb, void* arg, int64_t period_us)
{
    PeriodicTimer t;
    t.cb = cb;
    t.arg = arg;
    t.period_us = period_us;
    t.next_fire_us = now_us_ + period_us;
    t.active = true;
    for (size_t i = 0; i < timers_.size(); ++i) {
        if (!timers_[i].active && timers_[i].cb == nullptr) {  // reuse a freed slot
            timers_[i] = t;
            return static_cast<int>(i);
        }
    }
    timers_.push_back(t);
    return static_cast<int>(timers_.size() - 1);
}

void Scheduler::remove_periodic(int id)
{
    if (id >= 0 && id < static_cast<int>(timers_.size())) {
        timers_[id].active = false;
        timers_[id].cb = nullptr;
    }
}

void Scheduler::fire_due_timers()
{
    // Fire every timer due at now_us_, in registration order. The callback runs
    // on the scheduler's own stack (no task is executing during the advance
    // phase), exactly as in the thread version where it runs with the mutex
    // dropped and no task holding the token.
    // now_us_ に期限の来た全タイマを登録順で作動させる。コールバックはスケジューラ
    // 自身のスタックで動く（advance 中はどのタスクも実行していない）。スレッド版で
    // mutex を解放しトークンを誰も持たない状態で動くのと同じ条件。
    for (size_t i = 0; i < timers_.size(); ++i) {
        if (!timers_[i].active) continue;
        if (timers_[i].next_fire_us <= now_us_) {
            TimerCallback cb = timers_[i].cb;
            void* arg = timers_[i].arg;
            timers_[i].next_fire_us += timers_[i].period_us;
            cb(arg);
        }
    }
}

bool Scheduler::step_once(int64_t horizon_us)
{
    // One scheduling decision. Returns false when nothing is left to do
    // (no ready task and no pending wake-up), true otherwise.
    //
    // horizon_us only matters for run_until: it is INT64_MAX for run(), which
    // therefore behaves EXACTLY like the thread version's loop body — including
    // advancing the clock past max_sim_us on the final iteration, firing the
    // timers due there and delivering on_advance, before the caller's
    // `while (now_us_ < max_sim_us)` test ends the run. Reproducing that last
    // step is what makes the trace identical.
    //
    // スケジューリングの判断1回。もう何もすることがない（Ready も保留中の起床も
    // 無い）とき false、それ以外は true を返す。
    //
    // horizon_us が効くのは run_until のときだけで、run() では INT64_MAX になる。
    // よって run() の挙動はスレッド版のループ本体と厳密に一致する — 最後の周回で
    // 時計を max_sim_us の先へ進め、そこで期限の来たタイマを作動させ on_advance を
    // 届けてから、呼び出し側の `while (now_us_ < max_sim_us)` が実行を終える点まで
    // 含めて。この最後の一歩を再現することがトレース一致の要。
    Task* next = pick_ready();
    if (next != nullptr) {
        trace_.push_back({now_us_, next->id});
        grant(next);
        return true;
    }

    // No ready task → advance the virtual clock to the next wake-up: the
    // earliest of a BlockedDelay wake or a periodic-timer fire.
    // Ready が無い → 仮想時計を次の起床へ進める。BlockedDelay 起床か周期タイマ
    // 作動のうち最も早いもの。
    int64_t next_wake  = earliest_wake();
    int64_t next_timer = earliest_timer_fire();
    if (next_wake < 0 && next_timer < 0) return false;   // nothing pending → done
    if (next_wake < 0 || (next_timer >= 0 && next_timer < next_wake)) {
        next_wake = next_timer;
    }
    if (next_wake > horizon_us) {
        // run_until only: the next wake-up lies past the horizon, so stop short
        // and let run_until place the clock exactly on the target. run() passes
        // INT64_MAX here, so this branch is never taken there.
        // run_until 専用: 次の起床が期限の先にあるので手前で止め、時計を期限
        // ちょうどに置くのは run_until に任せる。run() は INT64_MAX を渡すため
        // この枝を通らない。
        return false;
    }

    now_us_ = next_wake;
    sils::compat::set_virtual_time_us(now_us_);
    for (Task* task : tasks_) {
        if ((task->state == TaskState::BlockedDelay ||
             task->state == TaskState::BlockedNotify) && task->wake_us <= now_us_) {
            task->state = TaskState::Ready;
        }
    }

    // Fire any periodic timers due now, before pick_ready, in registration
    // order. Their callbacks (xTaskNotifyGive) mark tasks Ready.
    // 期限の来た周期タイマを pick_ready 前に登録順で作動。コールバック
    // （xTaskNotifyGive）がタスクを Ready にする。
    fire_due_timers();

    if (on_advance_) on_advance_(now_us_);
    return true;
}

void Scheduler::run(int64_t max_sim_us)
{
    // Initial scenario injection at t = 0.
    // t = 0 でのシナリオ注入。
    if (!started_) {
        started_ = true;
        if (on_advance_) on_advance_(now_us_);
    }

    // INT64_MAX horizon: no clamping, so the loop body matches the thread
    // version's exactly (see step_once).
    // 期限を INT64_MAX に: 切り詰めが起きず、ループ本体がスレッド版と厳密に
    // 一致する（step_once 参照）。
    while (now_us_ < max_sim_us) {
        if (!step_once(INT64_MAX)) break;
    }
    shutdown();
}

void Scheduler::run_until(int64_t target_us)
{
    // Same loop as run(), but it RETURNS instead of tearing down, and it always
    // leaves the virtual clock exactly at target_us so the host's physics step
    // and the firmware's notion of time stay locked together.
    // run() と同じループだが、後始末をせずに戻る。また仮想時計を必ず target_us
    // ちょうどに置いて戻るので、ホストの物理ステップとファーム側の時刻認識が
    // ずれない。
    if (!started_) {
        started_ = true;
        if (on_advance_) on_advance_(now_us_);
    }

    while (now_us_ < target_us) {
        if (!step_once(target_us)) break;
    }

    if (now_us_ < target_us) {
        // Everything is parked beyond the horizon: jump the clock to the target
        // and deliver on_advance there, so the caller observes the time it asked
        // for (and scenario injection keeps its scripted cadence).
        // 全員が期限の先で待機している: 時計を期限まで飛ばし、そこで on_advance を
        // 届ける。呼び出し側は要求した時刻を観測でき、シナリオ注入の台本の刻みも保たれる。
        now_us_ = target_us;
        sils::compat::set_virtual_time_us(now_us_);
        if (on_advance_) on_advance_(now_us_);
    }
}

void Scheduler::shutdown()
{
    // Release every fiber's stack. Parked fibers are abandoned without
    // unwinding — safe because the firmware task bodies hold only trivially
    // destructible locals (the same property the thread version relies on).
    // 各 fiber のスタックを解放する。待機中の fiber は巻き戻さずに放棄する —
    // 本体タスクのローカルが自明に破棄可能な型だけなので安全（スレッド版が
    // 依拠しているのと同じ性質）。
    for (Task* task : tasks_) {
        std::free(task->stack);
        task->stack = nullptr;
#ifdef __EMSCRIPTEN__
        std::free(task->asyncify_stack);
        task->asyncify_stack = nullptr;
#endif
    }
}

TaskHandle_t Scheduler::current_handle()
{
    // Called from inside a running task, so running_ is that task.
    // 実行中のタスクから呼ばれるので running_ がそのタスク。
    return static_cast<TaskHandle_t>(running_);
}

}  // namespace rtos
}  // namespace sils

// =============================================================================
// FreeRTOS active-surface shims — byte-for-byte the same as scheduler.cpp's.
// FreeRTOS 能動面のシム — scheduler.cpp のものと1バイトも違わない。
// =============================================================================

using sils::rtos::Scheduler;
using sils::rtos::Task;

extern "C" {

BaseType_t xTaskCreatePinnedToCore(TaskFunction_t fn, const char* name,
                                   uint32_t /*stack_depth*/, void* param,
                                   UBaseType_t priority, TaskHandle_t* out_handle,
                                   BaseType_t /*core*/)
{
    TaskHandle_t handle = Scheduler::instance().create(fn, param, priority, name);
    if (out_handle) *out_handle = handle;
    return pdPASS;
}

void vTaskDelete(TaskHandle_t /*handle*/)
{
    // The firmware only ever deletes itself (vTaskDelete(NULL)).
    // 本体は自分自身しか削除しない（vTaskDelete(NULL)）。
    Scheduler::instance().delete_self();
}

void vTaskDelay(TickType_t ticks)
{
    Scheduler& s = Scheduler::instance();
    s.delay_until_us(s.now_us() + static_cast<int64_t>(ticks) * 1000);
}

void vTaskDelayUntil(TickType_t* last_wake, TickType_t period)
{
    // Absolute periodic wake (no drift), matching FreeRTOS. 1 tick = 1 ms.
    // 絶対周期起床（ドリフトなし）、FreeRTOS と同じ。1 tick = 1 ms。
    *last_wake += period;
    Scheduler::instance().delay_until_us(static_cast<int64_t>(*last_wake) * 1000);
}

TickType_t xTaskGetTickCount(void)
{
    return static_cast<TickType_t>(Scheduler::instance().now_us() / 1000);
}

TaskHandle_t xTaskGetCurrentTaskHandle(void)
{
    return Scheduler::instance().current_handle();
}

BaseType_t xTaskNotifyGive(TaskHandle_t handle)
{
    Scheduler::instance().notify_give(static_cast<Task*>(handle));
    return pdTRUE;
}

uint32_t ulTaskNotifyTake(BaseType_t clear_on_exit, TickType_t timeout)
{
    const int64_t timeout_us = (timeout == portMAX_DELAY)
                                   ? -1 : (int64_t)timeout * 1000;
    return Scheduler::instance().notify_take(clear_on_exit != pdFALSE, timeout_us);
}

}  // extern "C"
