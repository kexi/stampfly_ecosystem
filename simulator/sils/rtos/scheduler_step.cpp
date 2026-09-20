/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (SILS host bench).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file scheduler_step.cpp
 * @brief Tick-driven entry points for the THREAD scheduler: run_until/shutdown.
 *        スレッド版スケジューラの刻み実行用の入口: run_until / shutdown。
 *
 * The thread scheduler's run() owns its loop from t=0 to the end of the run.
 * A host that draws frames — the Unity editor plugin, and the hover spike that
 * stands in for it — owns the loop instead and wants the firmware advanced by
 * one physics tick at a time. run_until() is that entry point: the same loop
 * body as run(), stopping at a horizon and returning rather than tearing down.
 *
 * スレッド版の run() は t=0 から実行終了までループを所有する。描画を行うホスト
 * （Unity のエディタ用プラグインと、その代役である hover spike）は逆にループを
 * 自分で所有し、ファームウェアを物理 1 刻みずつ進めたい。その入口が run_until()
 * で、ループ本体は run() と同じまま、期限で止まって（後始末をせずに）戻る。
 *
 * WHY a separate translation unit: the existing executables (emu_vehicle,
 * rtos_smoke, hover_smoke, rate_tune, cores_smoke) do not link this file, so
 * their object code is exactly what it was before these entry points existed.
 * The two declarations added to scheduler.hpp are non-virtual member functions
 * and add no data member, so Scheduler's layout is unchanged as well.
 *
 * なぜ別の翻訳単位にするか: 既存の実行ファイル（emu_vehicle・rtos_smoke・
 * hover_smoke・rate_tune・cores_smoke）は本ファイルをリンクしないので、それらの
 * オブジェクトコードはこの入口が無かったときと厳密に同じになる。scheduler.hpp へ
 * 足した 2 つの宣言も仮想でないメンバ関数でデータメンバを増やさないため、
 * Scheduler のレイアウトも変わらない。
 *
 * Two deliberate differences from run()'s loop, both required by an interactive
 * host and neither of which changes the SCHEDULE:
 *
 *   1. No trace_ append. run() records every (time, task) pair; a host that
 *      calls run_until() forever would grow that vector without bound. Define
 *      SILS_SCHEDULER_STEP_TRACE=1 to record it anyway, for the cross-check
 *      against the fiber scheduler (spike/step_trace.cpp does exactly that).
 *   2. A longer hang budget, and no abort. run()'s grant_and_wait() gives a task
 *      ten wall-clock seconds and then calls std::abort(). Aborting the process
 *      is right for a batch run and wrong inside an application, so this file
 *      waits up to sixty seconds (an interactive host may sit at a breakpoint
 *      or be descheduled, and must not be mistaken for a hung task) and reports
 *      a failure to hand the token over by returning false. The caller decides
 *      what to do about it; the bridge treats it as fatal and latches it.
 *
 * run() のループとの、意図的な相違は 2 点。どちらも対話的なホストに必要なもので、
 * スケジュール自体は変えない:
 *
 *   1. trace_ へ追記しない。run() は (時刻, タスク) を全て記録するが、run_until()
 *      を延々と呼ぶホストではこの列が際限なく伸びる。fiber 版との突き合わせで
 *      記録が要るときは SILS_SCHEDULER_STEP_TRACE=1 を定義する
 *      （spike/step_trace.cpp がそうしている）。
 *   2. ハング上限が長く、abort しない。run() の grant_and_wait() はタスクに壁時計
 *      10 秒を与え、過ぎると std::abort() する。一括実行では正しいが、
 *      アプリケーションの中でプロセスを落とすのは誤り。よって本ファイルは 60 秒まで
 *      待ち（対話的なホストはブレークポイントで止まったり OS に退避させられたり
 *      するので、ハングしたタスクと取り違えてはならない）、トークンの受け渡しに
 *      失敗したことは false を返して伝える。どうするかは呼び出し側が決める。
 *      橋渡しはこれを致命的なものとして保持する。
 *
 * @design docs/plans/unity-simulator.md — 段階 2 ネイティブコア
 */

#include "scheduler.hpp"
#include "esp_timer.h"

#include <cstdio>

namespace sils {
namespace rtos {

// Whether the last shutdown() gave up on a task that would not yield the
// run-token and therefore left its thread and its 1 MiB stack behind. A host
// that keeps running can report the leak; a host about to exit can ignore it.
//
// A FREE function declared here rather than a member on Scheduler, because
// scheduler.hpp must not be touched at all: the existing builds (emu_vehicle
// and the smoke targets) compile against that header, and the fiber scheduler
// mirrors it. Anything that wants this declares it itself — only a host that
// links THIS file can call it, which is exactly the set of callers it is for.
//
// 直近の shutdown() が、実行トークンを返さないタスクを諦め、そのスレッドと 1 MiB
// のスタックを残したかどうか。動き続けるホストはその漏れを報告でき、終了間際の
// ホストは無視してよい。
//
// Scheduler のメンバではなくここの**自由関数**にするのは、scheduler.hpp に一切
// 手を触れないためである。既存のビルド（emu_vehicle と各スモーク）は同ヘッダを
// コンパイル対象としており、fiber 版もそれに倣っている。必要とする側が自分で
// 宣言すればよく、呼べるのは**本ファイル**をリンクしたホストだけ ― それこそが
// この関数の宛先の全体である。
bool shutdown_left_threads();

namespace {

// The entry-time on_advance has already been delivered at the current virtual
// time, so a repeated run_until() does not deliver it twice. A function-local
// flag rather than a member: Scheduler is a singleton (instance()), so one flag
// is exactly one scheduler, and adding a data member would change the class
// layout that the existing builds compile against.
// 入口での on_advance を現在の仮想時刻で配り終えたことを示す。run_until() を
// 繰り返し呼んでも二重に配らないため。メンバではなく関数外の変数にするのは、
// Scheduler が単一インスタンス（instance()）であり、1 つの旗がちょうど 1 つの
// スケジューラに対応するため。データメンバを足すと既存ビルドがコンパイル対象と
// しているクラスのレイアウトが変わってしまう。
bool g_entry_hook_delivered = false;

// What shutdown_left_threads() above reports. Set by shutdown() when it gave up
// on a task that would not yield the token and therefore left that thread and
// its stack behind. A file-local flag for the same reason as the one above.
// 上の shutdown_left_threads() が報告するもの。shutdown() が、トークンを返さない
// タスクを諦め、そのスレッドとスタックを残したときに立つ。ファイル内の変数に
// する理由は上のものと同じである。
bool g_shutdown_incomplete = false;

// Wall-clock budget for one task step. The ONLY use of the wall clock here, and
// only as a detector for a task that never yields; the schedule itself uses the
// virtual clock alone. Generous, because a host that stops at a breakpoint or
// is descheduled by the OS must not be mistaken for a hung task.
// 1 タスクステップの壁時計上限。ここで壁時計を使うのはこれだけで、用途は「決して
// トークンを返さないタスク」の検出に限る。スケジュール自体は仮想時計しか使わない。
// 上限を大きく取るのは、ブレークポイントで止まったホストや OS に退避させられた
// ホストを、ハングしたタスクと取り違えないため。
constexpr std::chrono::seconds kStepHangBudget{60};

// Wall-clock budget for ONE grant during shutdown(), which is much shorter than
// the one above on purpose. A task being torn down does no work: it wakes in a
// blocking primitive and throws. Anything that takes seconds there is not slow,
// it is stuck, and the caller is a host waiting to close a play session — the
// one place where waiting a minute is worse than leaking a thread.
// shutdown() での付与 1 回あたりの壁時計上限。上のものよりずっと短いのは意図的で
// ある。片付けられるタスクは仕事をしない。ブロッキングプリミティブの中で起きて
// throw するだけである。そこで何秒も掛かるものは、遅いのではなく詰まっている。
// そして呼び出し側は再生の終了を待つホストである ― 1 分待つことがスレッド 1 本を
// 漏らすことより悪くなる、唯一の場面である。
constexpr std::chrono::seconds kTeardownGrantBudget{2};

// Hand the run-token to `task` and wait until it yields it back. Identical to
// run()'s grant_and_wait() except for what happens when the budget expires:
// that one calls std::abort(), which must not happen inside an interactive
// host, so this returns false and lets the caller decide. Written here rather
// than declared on the class so the class gains nothing beyond the two entry
// points. The private members it needs are passed in by the member function.
//
// 実行トークンを `task` に渡し、返すまで待つ。run() の grant_and_wait() とは、上限に
// 達したときの振る舞いだけが違う。あちらは std::abort() するが、対話的なホストの中で
// プロセスを落としてはならないので、こちらは false を返して判断を呼び出し側に委ねる。
// クラスに宣言せずここに置くのは、クラスに増えるものを 2 つの入口だけに留めるため。
// 必要な private メンバはメンバ関数側から渡す。
bool grant_and_wait_no_abort(std::unique_lock<std::mutex>& lk,
                             Task* task,
                             Task*& running,
                             std::condition_variable& sched_cv,
                             std::chrono::seconds budget,
                             const char* caller)
{
    running = task;
    task->state = TaskState::Running;
    task->cv.notify_all();
    const bool yielded = sched_cv.wait_for(lk, budget,
                                           [&] { return running == nullptr; });
    if (!yielded) {
        std::fprintf(stderr,
                     "[scheduler] %s: task '%s' did not yield within %llds — "
                     "infinite loop without a blocking primitive?\n",
                     caller, task->name.c_str(),
                     static_cast<long long>(budget.count()));
    }
    return yielded;
}

}  // namespace

bool Scheduler::run_until(int64_t target_us)
{
    // Entry-time scenario injection, once per scheduler — the counterpart of
    // run()'s injection at t = 0.
    // 入口でのシナリオ注入。スケジューラにつき 1 回で、run() の t=0 での注入に当たる。
    if (!g_entry_hook_delivered) {
        g_entry_hook_delivered = true;
        if (on_advance_) on_advance_(now_us_);
    }

    std::unique_lock<std::mutex> lk(m_);
    while (now_us_ < target_us) {
        // Same order as run()'s loop body: run every Ready task first, and only
        // advance the clock when none is left.
        // run() のループ本体と同じ順序: まず Ready のタスクを全て走らせ、1 つも
        // 無くなって初めて時計を進める。
        Task* next = pick_ready();
        if (next != nullptr) {
#ifdef SILS_SCHEDULER_STEP_TRACE
            trace_.push_back({now_us_, next->id});
#endif
            if (!grant_and_wait_no_abort(lk, next, running_, sched_cv_,
                                         kStepHangBudget, "run_until")) {
                return false;
            }
            continue;
        }

        // No ready task → the next wake-up is the earliest of a BlockedDelay
        // deadline and a periodic-timer fire.
        // Ready が無い → 次の起床は、BlockedDelay の期限と周期タイマ作動のうち
        // 早い方。
        int64_t next_wake = earliest_wake();
        const int64_t next_timer = earliest_timer_fire();
        if (next_wake < 0 && next_timer < 0) break;   // nothing pending → done
        if (next_wake < 0 || (next_timer >= 0 && next_timer < next_wake)) {
            next_wake = next_timer;
        }
        if (next_wake > target_us) break;   // past the horizon → leave it parked

        now_us_ = next_wake;
        sils::compat::set_virtual_time_us(now_us_);
        // Wake both delay-blocked and notify-with-timeout tasks at their
        // deadline. A wait-forever notify (wake_us = INT64_MAX) is never
        // time-woken — only by a notification. Same as run()'s loop.
        // 期限到達で BlockedDelay と timeout 付き BlockedNotify を起床させる。
        // 無限待ちの通知待ち（wake_us = INT64_MAX）は時刻では起きない — 通知のみ。
        // run() のループと同じ。
        for (Task* task : tasks_) {
            const bool timed_out = (task->state == TaskState::BlockedDelay ||
                                    task->state == TaskState::BlockedNotify) &&
                                   task->wake_us <= now_us_;
            if (timed_out) task->state = TaskState::Ready;
        }

        // Fire any periodic timers due now, before pick_ready, in registration
        // order. Their callbacks (xTaskNotifyGive) mark tasks Ready.
        // 期限の来た周期タイマを pick_ready の前に登録順で作動させる。
        // コールバック（xTaskNotifyGive）がタスクを Ready にする。
        fire_due_timers(lk);

        // Scenario injection at the new virtual time (unlock so the hook can
        // touch topics without holding the scheduler mutex, as run() does).
        // 新しい仮想時刻でシナリオ注入（run() と同じく、フックがスケジューラ
        // ミューテックスを持たずにトピックを触れるよう一旦解放する）。
        if (on_advance_) {
            lk.unlock();
            on_advance_(now_us_);
            lk.lock();
        }
    }

    if (now_us_ < target_us) {
        // Everything left is parked beyond the horizon: put the clock exactly on
        // the target and deliver on_advance there, so the caller observes the
        // time it asked for and scenario injection keeps its scripted cadence.
        // 残りは全て期限の先で待機している: 時計を期限ちょうどに置き、そこで
        // on_advance を届ける。呼び出し側は要求した時刻を観測でき、シナリオ注入の
        // 台本の刻みも保たれる。
        now_us_ = target_us;
        sils::compat::set_virtual_time_us(now_us_);
        lk.unlock();
        if (on_advance_) on_advance_(now_us_);
        return true;
    }

    lk.unlock();
    return true;
}

void Scheduler::shutdown()
{
    // Unwind every task thread and join it, the same end state run() reaches
    // through stop_all(). The loop below is NOT stop_all(), and the difference
    // is the whole reason this function exists — see the note above.
    // 各タスクのスレッドを巻き戻して join し、run() が stop_all() で到達するのと
    // 同じ終状態にする。下のループは stop_all() ではなく、その違いこそがこの関数の
    // 存在理由である。上の注記を参照。
    g_shutdown_incomplete = false;

    {
        std::unique_lock<std::mutex> lk(m_);
        shutdown_ = true;

        // Keep granting the token to whatever has not exited yet, sweep after
        // sweep, until a whole sweep finds nothing left to do.
        //
        // One sweep is not enough. A task whose thread has never been granted
        // the token is still parked in run_task_thread's FIRST wait, before it
        // has entered the firmware's task function at all. Granting the token
        // there does not unwind it: run_task_thread does not test shutdown_
        // before the call, so the task runs its setup for the first time and
        // then parks in a blocking primitive — now inside block_current, where
        // the throw lives. It needs a SECOND grant to unwind, and a single pass
        // over tasks_ has already moved on.
        //
        // まだ終了していないものへトークンを渡す掃きを、何も残らない掃きに至るまで
        // 繰り返す。
        //
        // 1 周では足りない。一度もトークンを渡されていないタスクのスレッドは、
        // run_task_thread の**最初の**待機に留まっており、ファームのタスク関数へ
        // まだ入ってすらいない。そこでトークンを渡しても巻き戻しは起きない。
        // run_task_thread は呼び出しの前に shutdown_ を見ないので、タスクは初めて
        // 自分の setup を走らせ、そしてブロッキングプリミティブで待機する ―
        // throw が在る block_current の中である。巻き戻すには**2 度目の**付与が
        // 要るが、tasks_ の 1 周はもう先へ進んでしまっている。
        //
        // This is the deadlock the Unity editor met: `sfu_boot` creates the
        // fourteen task threads but runs none of them, so a host that boots and
        // shuts down without a single `sfu_step` in between left every task
        // parked in `ulTaskNotifyTake`/`vTaskDelay` with nobody to grant it
        // again, and the join at the end waited forever. run() never meets it
        // because its loop has already run every task by the time it tears down.
        //
        // Unity のエディタが出会ったデッドロックがこれである。`sfu_boot` は 14 本の
        // タスクのスレッドを作るがどれも走らせないので、間に `sfu_step` を 1 回も
        // 挟まずに起動して終了したホストでは、全タスクが `ulTaskNotifyTake`／
        // `vTaskDelay` で待機したまま、再び付与する者が居なくなり、末尾の join が
        // 永久に待った。run() がこれに出会わないのは、後始末に入る時点でループが
        // 既に全タスクを走らせているからである。
        bool made_progress = true;
        while (made_progress) {
            made_progress = false;
            for (Task* task : tasks_) {
                if (task->exited) continue;
                made_progress = true;
                if (!grant_and_wait_no_abort(lk, task, running_, sched_cv_,
                                             kTeardownGrantBudget, "shutdown")) {
                    // The task never gave the token back. Granting the token to
                    // anything else now would race that task, so stop handing it
                    // out and leave the rest parked: a leak is recoverable, a
                    // schedule with two tasks running is not.
                    // トークンが返らなかった。ここで他へ渡せばそのタスクと競合する
                    // ので、付与をやめ、残りは待機させたままにする。漏れは取り返せる
                    // が、2 つのタスクが同時に走るスケジュールは取り返せない。
                    g_shutdown_incomplete = true;
                    made_progress = false;
                    break;
                }
            }
        }
    }

    // Join what has exited. A thread that never yielded the token is NOT joined
    // — joining it would be the very wait this function exists to bound — so its
    // stack and its thread stay behind and `shutdown_left_threads()` says so.
    // Leaking is the lesser evil: the alternative is the hang.
    // 終了したものを join する。トークンを返さなかったスレッドは join **しない** ―
    // それこそが、この関数が上限を設けている当の待ちだからである ― ので、その
    // スタックとスレッドは残り、`shutdown_left_threads()` がそれを伝える。漏らす
    // 方がましである。そうしない道は、あの固まりだからである。
    for (Task* task : tasks_) {
        const bool joinable_and_done = task->exited && task->thread.joinable();
        if (joinable_and_done) {
            task->thread.join();
        } else if (task->thread.joinable()) {
            // Detach so the std::thread object can be destroyed without
            // std::terminate, which is what a joinable thread's destructor does.
            // std::thread の破棄で std::terminate にならないよう detach する。
            // join 可能なままのスレッドのデストラクタはそうするからである。
            task->thread.detach();
        }
    }

    g_entry_hook_delivered = false;
}

bool shutdown_left_threads()
{
    return g_shutdown_incomplete;
}

}  // namespace rtos
}  // namespace sils
