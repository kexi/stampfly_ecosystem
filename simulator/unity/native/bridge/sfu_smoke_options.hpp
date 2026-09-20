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
#include <new>
#include <string>

namespace sfu {

/**
 * The parsed command line. The two text fields are `const char*` into `argv`,
 * NOT `std::string`, because this struct is built before `sfu_boot` and holding
 * a pointer into `argv` is simply the cheaper way to read a command line that
 * outlives the parse. It is no longer a correctness requirement.
 *
 * It used to be one. Any allocation ahead of `sfu_boot` — a single
 * `std::string` in `main` was enough — changed the whole flight: measured,
 * `sfu_bridge_smoke`'s peak altitude moved from 0.684 m to 0.552 m and the
 * vehicle stopped holding. The cause was NOT the allocation: the fiber
 * scheduler was handing each task a stack from `std::malloc`, which guarantees
 * only 8-byte alignment under wasm32, while the compiler places 16-byte-aligned
 * locals by masking the stack pointer — so on a stack that started 8-mod-16 two
 * unrelated locals overlapped inside the firmware's float math and tripped the
 * impact failsafe. Whether a stack landed on the boundary depended only on the
 * heap offset, which is why one allocation flipped it. Fixed in e4ed60cd by
 * allocating those stacks with `std::aligned_alloc(16, ...)`.
 *
 * Allocating before the boot is therefore safe now, and `--preallocate` below
 * exists so every run of `just unity-native-test` proves it rather than
 * assuming it.
 *
 * 解析したコマンド行。2 つの文字列の欄が `std::string` ではなく `argv` を指す
 * `const char*` なのは、この構造体が `sfu_boot` の前に作られ、解析より長生きする
 * コマンド行を読むには `argv` を指すほうが単に安いからである。もはや正しさの
 * 要件ではない。
 *
 * かつては要件だった。`sfu_boot` より前の確保は ― `main` の `std::string` 1 つで
 * 足りた ― 飛行全体を変えた。実測では `sfu_bridge_smoke` の最大高度が 0.684 m から
 * 0.552 m へ動き、機体は保持しなくなった。原因は確保そのものではない。fiber 版
 * スケジューラが各タスクに `std::malloc`（wasm32 では 8 バイト整列しか保証しない）で
 * 取ったスタックを渡しており、一方コンパイラは 16 バイト整列のローカルをスタック
 * ポインタのマスクで配置する。よって 8 mod 16 で始まるスタックでは、ファームの
 * 浮動小数演算の中で無関係な 2 つのローカルが重なり、衝撃検出フェイルセーフを
 * 誤作動させた。スタックが境界に乗るかはヒープのずれだけで決まるので、確保 1 回で
 * 反転した。e4ed60cd で、そのスタックを `std::aligned_alloc(16, ...)` で取るように
 * 直っている。
 *
 * したがって起動前の確保は現在は安全であり、下の `--preallocate` は、それを前提に
 * するのではなく `just unity-native-test` の実行ごとに確かめるためにある。
 */
struct SmokeOptions {
    double      sim_seconds = 30.0;
    const char* log_jsonl_path = nullptr;  ///< null = write no log file / null ならログを書かない
    const char* run_id = nullptr;          ///< null = generate one / null なら発行する
    /// Bytes to leak onto the heap before `sfu_boot`; 0 = allocate nothing.
    /// `sfu_boot` の前にヒープへ捨てるバイト数。0 なら何も確保しない。
    unsigned long preallocate_bytes = 0;
    bool        ok = true;
};

/**
 * Take `bytes` from the heap and never give them back, so that everything the
 * firmware allocates afterwards — the 14 fiber stacks above all — lands at a
 * different offset. Also builds one `std::string`, because that is the exact
 * shape of the allocation that used to change the flight.
 *
 * The memory is deliberately leaked: freeing it would put the heap back where
 * it was and undo the shift. One allocation per process, and the process exits
 * straight after the flight.
 *
 * ヒープから `bytes` を取り、返さない。ファームがその後に行う確保 ― とりわけ
 * fiber スタック 14 本 ― が違うずれに落ちるようにするためである。併せて
 * `std::string` を 1 つ作る。かつて飛行を変えたのがまさにその形の確保だからである。
 *
 * 記憶域は意図的に捨てている。解放すればヒープが元の位置へ戻り、ずらした意味が
 * 無くなる。1 プロセスにつき 1 回で、プロセスは飛行の直後に終了する。
 */
inline void shift_heap_before_boot(unsigned long bytes)
{
    if (bytes == 0) return;

    // The raw block, leaked on purpose.
    // 生の塊。意図的に捨てる。
    void* leaked = std::malloc(bytes);
    if (leaked != nullptr) std::memset(leaked, 0, bytes);

    // And a std::string long enough to allocate rather than sit in the small
    // string buffer — the very thing the old rule forbade here.
    // さらに、小文字列最適化の領域に収まらず確保を伴う長さの std::string を作る。
    // かつての決まりがここで禁じていたのは、まさにこれである。
    auto* text = new std::string(64, 'x');
    (void)text->size();
}

/**
 * Parse `[seconds] [--log-jsonl <path>] [--run-id <id>] [--preallocate <bytes>]`.
 *
 * `--run-id` exists so a caller that already issued an identifier — a test
 * runner covering several processes in one run — can pass it down and have the
 * whole run share it, which is what the logging rules mean by issuing the
 * identifier at the entrance.
 * `--run-id` があるのは、既に識別子を発行した呼び出し側 ― 1 回の実行で複数の
 * プロセスを回す試験の実行役 ― がそれを渡し、実行全体で共有できるようにするため
 * である。ログの決まりが言う「入口で発行する」とはこのことである。
 *
 * `--preallocate <bytes>` shifts the heap before `sfu_boot` (see the note on
 * `SmokeOptions`). `just unity-native-test` runs each check once with it and
 * once without and requires byte-identical stdout, which is what keeps the
 * fiber stacks' alignment from regressing on the C++ side.
 * `--preallocate <bytes>` は `sfu_boot` の前にヒープをずらす（`SmokeOptions` の
 * 注記を参照）。`just unity-native-test` は各確認を付けて 1 回・付けずに 1 回
 * 実行し、標準出力がバイト単位で一致することを求める。C++ 側で fiber スタックの
 * 整列が退行しないのは、これによる。
 */
inline SmokeOptions parse_smoke_options(int argc, char** argv)
{
    SmokeOptions options;
    for (int i = 1; i < argc; ++i) {
        const char* arg = argv[i];
        const bool wants_value = (std::strcmp(arg, "--log-jsonl") == 0 ||
                                  std::strcmp(arg, "--run-id") == 0 ||
                                  std::strcmp(arg, "--preallocate") == 0);
        if (wants_value) {
            if (i + 1 >= argc) {
                options.ok = false;
                return options;
            }
            if (std::strcmp(arg, "--log-jsonl") == 0) {
                options.log_jsonl_path = argv[i + 1];
            } else if (std::strcmp(arg, "--run-id") == 0) {
                options.run_id = argv[i + 1];
            } else {
                options.preallocate_bytes = std::strtoul(argv[i + 1], nullptr, 10);
            }
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
