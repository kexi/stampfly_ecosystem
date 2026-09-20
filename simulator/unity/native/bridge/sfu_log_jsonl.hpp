/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity/WebAssembly native core — stage 2 bridge).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

/**
 * @file sfu_log_jsonl.hpp
 * @brief Writes the firmware's log records, and the host's own events, as JSON
 *        Lines in the shape `AGENTS.md`'s logging rules define.
 *        ファームのログ記録とホスト自身の事象を、`AGENTS.md` のログの決まりが
 *        定める形の JSON Lines で書き出す。
 *
 * One record is one line, with the keys the rules name: `ts`, `level`, `src`,
 * `event`, `run_id`, `boot_id`, `sim_us`, `tag`, `msg`. Firmware records carry
 * `src: "fw"` and `event: "fw.log"`; the host's own lines carry `src: "bridge"`
 * and an event of their own. Nothing here parses a firmware line back apart —
 * the parts arrive already separated, in `SfuLogRecord`.
 *
 * 1 記録が 1 行で、鍵は決まりが挙げるもの ― `ts`・`level`・`src`・`event`・
 * `run_id`・`boot_id`・`sim_us`・`tag`・`msg`。ファームの記録は `src: "fw"` と
 * `event: "fw.log"` を持ち、ホスト自身の行は `src: "bridge"` と自前の event を
 * 持つ。ここでファームの行を解析して部分へ戻すことはしない ― 部分は
 * `SfuLogRecord` の形で、既に分かれたまま届く。
 *
 * ## Determinism / 決定論
 *
 * Draining the ring and writing these lines touches no clock, no task and no
 * plant state, so a run that writes a log and a run that does not fly
 * identically. The only fields that differ between two runs of the same flight
 * are `ts` and the random tail of `run_id`, both of which come from the wall
 * clock; `sim_us`, `level`, `tag` and `msg` repeat exactly. A comparison of two
 * runs therefore compares those four and leaves the first two out.
 *
 * リングを空にしてこれらの行を書くことは、時計にもタスクにもプラントの状態にも
 * 触れない。よってログを書く実行と書かない実行は同じ飛行をする。同じ飛行を 2 回
 * 行ったときに違うのは `ts` と `run_id` の末尾の乱数だけで、どちらも壁時計から
 * 来る。`sim_us`・`level`・`tag`・`msg` は厳密に繰り返される。2 回の実行の比較は
 * よってこの 4 つを比べ、前の 2 つを除く。
 *
 * @design AGENTS.md「新しく書くコードのログの決まり」
 */

#ifndef SFU_LOG_JSONL_HPP
#define SFU_LOG_JSONL_HPP

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <string>

#if defined(_WIN32)
#include <direct.h>
#else
#include <sys/stat.h>
#endif

#include "sfu_api.h"

namespace sfu {

/// The level names the logging rules use. The firmware has no "fatal", and its
/// VERBOSE maps onto debug because the rules name only four.
/// ログの決まりが使う段の名前。ファームに「fatal」は無く、VERBOSE は決まりが
/// 4 つしか挙げないので debug に対応させる。
inline const char* level_name(int32_t level)
{
    switch (level) {
        case SFU_LOG_ERROR: return "error";
        case SFU_LOG_WARN:  return "warn";
        case SFU_LOG_INFO:  return "info";
        default:            return "debug";
    }
}

/// UTC, RFC 3339, milliseconds — the `ts` the rules ask for.
/// UTC・RFC 3339・ミリ秒 ― 決まりが求める `ts` である。
inline std::string utc_timestamp()
{
    const auto now = std::chrono::system_clock::now();
    const auto since_epoch = now.time_since_epoch();
    const auto seconds = std::chrono::duration_cast<std::chrono::seconds>(since_epoch);
    const auto millis =
        std::chrono::duration_cast<std::chrono::milliseconds>(since_epoch - seconds);

    const std::time_t as_time_t = (std::time_t)seconds.count();
    std::tm utc{};
#if defined(_WIN32)
    gmtime_s(&utc, &as_time_t);
#else
    gmtime_r(&as_time_t, &utc);
#endif

    char buffer[40];
    std::snprintf(buffer, sizeof(buffer), "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
                  utc.tm_year + 1900, utc.tm_mon + 1, utc.tm_mday,
                  utc.tm_hour, utc.tm_min, utc.tm_sec, (int)millis.count());
    return std::string(buffer);
}

/// A run identifier whose name sorts by time: the UTC moment first, then a short
/// random tail so two runs started in the same second stay apart.
/// 名前順が時刻順になる実行の識別子。先頭を UTC の時点にし、後ろに短い乱数を
/// 付けて、同じ秒に始まった 2 つの実行が分かれるようにする。
inline std::string make_run_id()
{
    const auto now = std::chrono::system_clock::now();
    const std::time_t as_time_t = std::chrono::system_clock::to_time_t(now);
    std::tm utc{};
#if defined(_WIN32)
    gmtime_s(&utc, &as_time_t);
#else
    gmtime_r(&as_time_t, &utc);
#endif

    char stamp[24];
    std::snprintf(stamp, sizeof(stamp), "%04d%02d%02dT%02d%02d%02dZ",
                  utc.tm_year + 1900, utc.tm_mon + 1, utc.tm_mday,
                  utc.tm_hour, utc.tm_min, utc.tm_sec);

    // `YYYYMMDDTHHMMSSZ-` plus eight hex digits, the exact shape
    // `lib/sfcli/utils/jsonl_log.py`'s `new_run_id()` produces, so an id from
    // here and an id from the `sf` side are the same kind of thing and sort
    // together. The tail only has to separate runs started within one second,
    // so the clock's sub-second part is enough entropy and needs no random
    // engine — which also keeps this header free of <random>.
    // `YYYYMMDDTHHMMSSZ-` に 16 進 8 桁。`lib/sfcli/utils/jsonl_log.py` の
    // `new_run_id()` が作るのと同じ形にしてあるので、ここで発行した識別子と `sf`
    // 側の識別子は同じ種類のものになり、並べ替えも揃う。末尾が分ければよいのは
    // 同じ秒に始まった実行だけなので、時計の秒未満の部分で十分であり、乱数生成器は
    // 要らない（このヘッダが <random> を引かずに済む利点もある）。
    const auto nanos = std::chrono::duration_cast<std::chrono::nanoseconds>(
        now.time_since_epoch()).count();
    char tail[12];
    std::snprintf(tail, sizeof(tail), "-%08x", (unsigned)(nanos & 0xffffffffu));

    return std::string(stamp) + tail;
}

/// Escape the characters JSON forbids in a string. The firmware's messages are
/// plain ASCII today, but a `%s` argument can carry anything, and one stray
/// quote would make the whole file unreadable to `jq`.
/// JSON が文字列中で許さない文字を逃がす。ファームの本文は今のところ素の ASCII
/// だが、`%s` の引数は何でも運べる。引用符が 1 つ紛れれば、ファイル全体が `jq`
/// から読めなくなる。
inline std::string json_escape(const char* text)
{
    std::string out;
    for (const char* p = text; *p != '\0'; ++p) {
        const unsigned char c = (unsigned char)*p;
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:
                if (c < 0x20) {
                    char escaped[8];
                    std::snprintf(escaped, sizeof(escaped), "\\u%04x", c);
                    out += escaped;
                } else {
                    out += (char)c;
                }
        }
    }
    return out;
}

/// Writes JSON Lines to one file for the length of one run. Closing it is the
/// destructor's job, so a check that returns early still leaves a valid file.
/// 1 回の実行のあいだ、1 つのファイルへ JSON Lines を書く。閉じるのはデストラクタ
/// の役目なので、途中で戻る確認でも妥当なファイルが残る。
class JsonlLog {
public:
    /// `path` empty = write nothing at all, so a caller can pass the option
    /// through unconditionally and the default stays "no file".
    /// `path` が空なら何も書かない。呼び出し側が指定を無条件に通しても、既定が
    /// 「ファイルを作らない」のままでいられるようにするため。
    JsonlLog(const std::string& path, const std::string& run_id)
        : run_id_(run_id), boot_id_(run_id + "-b1")
    {
        if (path.empty()) return;
        // Make the directories the path names first. The usual destination is
        // `<build dir>/logs/`, which a fresh build directory does not have, and
        // `fopen` will not create it — a check run right after a clean build
        // would otherwise write nothing and fail on an absent file.
        // パスが指すディレクトリを先に作る。ふつうの出力先は `<ビルドディレクトリ>/
        // logs/` で、作り直したビルドディレクトリにそれは無い。`fopen` は作って
        // くれないので、作り直した直後の確認は何も書けず、ファイルが無いことで
        // 落ちてしまう。
        make_parent_directories(path);
        file_ = std::fopen(path.c_str(), "w");
    }

    ~JsonlLog()
    {
        if (file_ != nullptr) std::fclose(file_);
    }

    JsonlLog(const JsonlLog&) = delete;
    JsonlLog& operator=(const JsonlLog&) = delete;

    bool is_open() const { return file_ != nullptr; }
    const std::string& run_id() const { return run_id_; }

    /// One line from the bridge itself: `src: "bridge"`, no tag, the event named
    /// by the caller. `sim_us` < 0 leaves the field out (nothing has a virtual
    /// time before the firmware boots).
    /// 橋渡し自身の 1 行。`src: "bridge"`、タグ無し、event は呼び出し側が付ける。
    /// `sim_us` が負なら欄ごと省く（ファーム起動前に仮想時刻を持つものは無い）。
    void write_bridge(const char* level, const char* event, int64_t sim_us,
                      const std::string& message)
    {
        if (file_ == nullptr) return;
        std::fprintf(file_,
                     "{\"ts\":\"%s\",\"level\":\"%s\",\"src\":\"bridge\",\"event\":\"%s\","
                     "\"run_id\":\"%s\",\"boot_id\":\"%s\"",
                     utc_timestamp().c_str(), level, event,
                     run_id_.c_str(), boot_id_.c_str());
        if (sim_us >= 0) std::fprintf(file_, ",\"sim_us\":%lld", (long long)sim_us);
        std::fprintf(file_, ",\"msg\":\"%s\"}\n", json_escape(message.c_str()).c_str());
    }

    /// One firmware record, with its parts placed straight into their keys.
    /// ファームの記録 1 つ。部分をそのまま対応する鍵へ置く。
    void write_firmware(const SfuLogRecord& record)
    {
        if (file_ == nullptr) return;
        std::fprintf(file_,
                     "{\"ts\":\"%s\",\"level\":\"%s\",\"src\":\"fw\",\"event\":\"fw.log\","
                     "\"run_id\":\"%s\",\"boot_id\":\"%s\",\"sim_us\":%lld,"
                     "\"tag\":\"%s\",\"msg\":\"%s\"}\n",
                     utc_timestamp().c_str(), level_name(record.level),
                     run_id_.c_str(), boot_id_.c_str(), (long long)record.sim_us,
                     json_escape(record.tag).c_str(),
                     json_escape(record.message).c_str());
    }

    /// Take everything the firmware has logged since the last drain and write it.
    /// Returns how many records were written.
    /// 前回の取り出し以降にファームが書いたものを全て取り出して書く。書いた記録の
    /// 数を返す。
    int drain_firmware()
    {
        if (file_ == nullptr) return 0;
        SfuLogRecord record{};
        int written = 0;
        for (;;) {
            record.struct_size = (uint32_t)sizeof(SfuLogRecord);
            if (sfu_log_read_record(&record) != 1) break;
            write_firmware(record);
            ++written;
        }
        return written;
    }

private:
    /// Create every directory on the way to `path`, ignoring the ones that are
    /// already there. `<filesystem>` would do this in one call, but it drags a
    /// library dependency into a header two small programs include, so the few
    /// lines are written out instead.
    /// `path` に至る各ディレクトリを作る。既に在るものは飛ばす。`<filesystem>` なら
    /// 1 回の呼び出しで済むが、小さなプログラム 2 つが読み込むヘッダにライブラリへの
    /// 依存を持ち込むことになる。数行なので書き下ろす。
    static void make_parent_directories(const std::string& path)
    {
        for (std::size_t i = 1; i < path.size(); ++i) {
            if (path[i] != '/') continue;
            const std::string directory = path.substr(0, i);
#if defined(_WIN32)
            _mkdir(directory.c_str());
#else
            // EEXIST is the normal case and not an error here.
            // EEXIST は普通の場合であり、ここでは誤りではない。
            ::mkdir(directory.c_str(), 0755);
#endif
        }
    }

    std::FILE*  file_ = nullptr;
    std::string run_id_;
    std::string boot_id_;
};

}  // namespace sfu

#endif  // SFU_LOG_JSONL_HPP
