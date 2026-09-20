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
 *   2. No hang budget. run()'s grant_and_wait() gives a task ten wall-clock
 *      seconds and then calls std::abort(). Aborting the process is right for a
 *      batch run and wrong inside an application, so this file waits without a
 *      deadline for the token and reports a failure to hand it over by
 *      returning false.
 *
 * run() のループとの、意図的な相違は 2 点。どちらも対話的なホストに必要なもので、
 * スケジュール自体は変えない:
 *
 *   1. trace_ へ追記しない。run() は (時刻, タスク) を全て記録するが、run_until()
 *      を延々と呼ぶホストではこの列が際限なく伸びる。fiber 版との突き合わせで
 *      記録が要るときは SILS_SCHEDULER_STEP_TRACE=1 を定義する
 *      （spike/step_trace.cpp がそうしている）。
 *   2. ハング上限を持たない。run() の grant_and_wait() はタスクに壁時計 10 秒を
 *      与え、過ぎると std::abort() する。一括実行では正しいが、アプリケーションの
 *      中でプロセスを落とすのは誤り。よって本ファイルはトークンを期限なしで待ち、
 *      受け渡しに失敗したことは false を返して伝える。
 *
 * @design docs/plans/unity-simulator.md — 段階 2 ネイティブコア
 */

#include "scheduler.hpp"
#include "esp_timer.h"

#include <cstdio>

namespace sils {
namespace rtos {

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

// Wall-clock budget for one task step. The ONLY use of the wall clock here, and
// only as a detector for a task that never yields; the schedule itself uses the
// virtual clock alone. Generous, because a host that stops at a breakpoint or
// is descheduled by the OS must not be mistaken for a hung task.
// 1 タスクステップの壁時計上限。ここで壁時計を使うのはこれだけで、用途は「決して
// トークンを返さないタスク」の検出に限る。スケジュール自体は仮想時計しか使わない。
// 上限を大きく取るのは、ブレークポイントで止まったホストや OS に退避させられた
// ホストを、ハングしたタスクと取り違えないため。
constexpr std::chrono::seconds kStepHangBudget{60};

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
                             std::condition_variable& sched_cv)
{
    running = task;
    task->state = TaskState::Running;
    task->cv.notify_all();
    const bool yielded = sched_cv.wait_for(lk, kStepHangBudget,
                                           [&] { return running == nullptr; });
    if (!yielded) {
        std::fprintf(stderr,
                     "[scheduler] run_until: task '%s' did not yield within %llds — "
                     "infinite loop without a blocking primitive?\n",
                     task->name.c_str(),
                     static_cast<long long>(kStepHangBudget.count()));
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
            if (!grant_and_wait_no_abort(lk, next, running_, sched_cv_)) {
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
    // The same teardown run() performs at its end (unwind every parked task and
    // join its thread). Exposed so a run_until() driver can do it once its own
    // loop is finished.
    // run() が末尾で行うのと同じ後始末（待機中の各タスクを巻き戻してスレッドを
    // join する）。run_until() で回すホストが自分のループを終えたときに実行できる
    // よう公開する。
    stop_all();
    g_entry_hook_delivered = false;
}

}  // namespace rtos
}  // namespace sils
