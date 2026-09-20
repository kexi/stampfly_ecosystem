/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file sfu_smoke_options.hpp
 * @brief The command line both C++ minimum-operation checks accept.
 *        2 つの C++ の最小動作確認が受け取るコマンド行。
 *
 * Header-only because both checks need the same options and neither should own
 * them, the same reason `sfu_rc_script.hpp` is a header.
 * ヘッダだけにしてあるのは、両方の確認が同じ指定を要り、どちらの持ち物でもない
 * ためである。`sfu_rc_script.hpp` をヘッダにしているのと同じ理由による。
 *
 * @design AGENTS.md「新しく書くコードのログの決まり」
 */

#ifndef SFU_SMOKE_OPTIONS_HPP
#define SFU_SMOKE_OPTIONS_HPP

#include <cstdlib>
#include <cstring>

namespace sfu {

/**
 * The parsed command line. The two text fields are `const char*` into `argv`,
 * NOT `std::string`, because this struct is built BEFORE `sfu_boot` and a
 * `std::string` would allocate there.
 *
 * **Nothing may touch the heap before `sfu_boot`.** The firmware's own start-up
 * allocates, and under Emscripten the addresses it gets depend on what was
 * allocated before it. A single `std::string` built in `main` ahead of the boot
 * is enough to change the whole flight: measured, `sfu_bridge_smoke`'s peak
 * altitude moves from 0.684 m to 0.552 m and the vehicle no longer holds. The
 * same allocation made AFTER `sfu_boot` changes nothing. Until that sensitivity
 * is understood and removed, every host — this check, the other check, and the
 * Unity side — must leave the heap alone until the firmware has booted.
 *
 * 解析したコマンド行。2 つの文字列の欄が `std::string` ではなく `argv` を指す
 * `const char*` なのは、この構造体が `sfu_boot` の**前**に作られ、`std::string`
 * ならそこで確保を行ってしまうからである。
 *
 * **`sfu_boot` の前にヒープへ触れてはならない。** ファーム自身の起動は確保を行い、
 * Emscripten ではそこで得る番地が、それ以前に何が確保されたかに依存する。起動より
 * 前に `main` で `std::string` を 1 つ作るだけで飛行全体が変わる。実測では
 * `sfu_bridge_smoke` の最大高度が 0.684 m から 0.552 m へ動き、機体は保持しなく
 * なる。同じ確保を `sfu_boot` の**後**で行えば何も変わらない。この感度の原因が
 * 分かって取り除かれるまで、どのホストも ― この確認も、もう一方の確認も、Unity
 * 側も ― ファームが起動するまでヒープに触れてはならない。
 */
struct SmokeOptions {
    double      sim_seconds = 30.0;
    const char* log_jsonl_path = nullptr;  ///< null = write no log file / null ならログを書かない
    const char* run_id = nullptr;          ///< null = generate one / null なら発行する
    bool        ok = true;
};

/**
 * Parse `[seconds] [--log-jsonl <path>] [--run-id <id>]`.
 *
 * `--run-id` exists so a caller that already issued an identifier — a test
 * runner covering several processes in one run — can pass it down and have the
 * whole run share it, which is what the logging rules mean by issuing the
 * identifier at the entrance.
 * `--run-id` があるのは、既に識別子を発行した呼び出し側 ― 1 回の実行で複数の
 * プロセスを回す試験の実行役 ― がそれを渡し、実行全体で共有できるようにするため
 * である。ログの決まりが言う「入口で発行する」とはこのことである。
 */
inline SmokeOptions parse_smoke_options(int argc, char** argv)
{
    SmokeOptions options;
    for (int i = 1; i < argc; ++i) {
        const char* arg = argv[i];
        const bool wants_value = (std::strcmp(arg, "--log-jsonl") == 0 ||
                                  std::strcmp(arg, "--run-id") == 0);
        if (wants_value) {
            if (i + 1 >= argc) {
                options.ok = false;
                return options;
            }
            if (std::strcmp(arg, "--log-jsonl") == 0) options.log_jsonl_path = argv[i + 1];
            else                                       options.run_id = argv[i + 1];
            ++i;
            continue;
        }
        if (arg[0] == '-') {
            options.ok = false;
            return options;
        }
        options.sim_seconds = std::atof(arg);
    }
    return options;
}

}  // namespace sfu

#endif  // SFU_SMOKE_OPTIONS_HPP
