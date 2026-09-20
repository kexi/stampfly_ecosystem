/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — firmware log records to lines).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Collections.Generic;

namespace StampFly.Core
{
    /// <summary>
    /// The levels <c>SfuLogRecord::level</c> carries, with the same numbers as
    /// <c>esp_log_level_t</c> so the two never need translating
    /// (<c>simulator/unity/native/bridge/sfu_api.h</c>).
    /// <c>SfuLogRecord::level</c> が持つ段。<c>esp_log_level_t</c> と数値を
    /// 同じにしてあり、読み替えは要らない
    /// （<c>simulator/unity/native/bridge/sfu_api.h</c>）。
    /// </summary>
    public static class FirmwareLogLevels
    {
        /// <summary>Nothing is logged. / 何も出さない。</summary>
        public const int None = 0;

        /// <summary>ESP_LOGE. </summary>
        public const int Error = 1;

        /// <summary>ESP_LOGW. </summary>
        public const int Warn = 2;

        /// <summary>ESP_LOGI. </summary>
        public const int Info = 3;

        /// <summary>ESP_LOGD. </summary>
        public const int Debug = 4;

        /// <summary>ESP_LOGV. </summary>
        public const int Verbose = 5;

        /// <summary>
        /// The <see cref="LogLevel"/> a firmware level becomes.
        ///
        /// Verbose has no spelling of its own in AGENTS.md's four levels, so it
        /// joins debug -- the alternative would be inventing a fifth level that
        /// <c>lib/sfcli/utils/jsonl_log.py</c> would reject.
        ///
        /// ファームの段が対応する <see cref="LogLevel"/>。
        ///
        /// AGENTS.md の 4 段に verbose の綴りは無いので debug に合わせる。
        /// 5 つ目の段を作れば <c>lib/sfcli/utils/jsonl_log.py</c> に拒否される。
        /// </summary>
        public static LogLevel ToLogLevel(int firmwareLevel)
        {
            switch (firmwareLevel)
            {
                case Error: return LogLevel.Error;
                case Warn: return LogLevel.Warn;
                case Debug:
                case Verbose: return LogLevel.Debug;
                default: return LogLevel.Info;
            }
        }
    }

    /// <summary>
    /// One firmware log record as C# receives it, before it becomes a line.
    ///
    /// The fields are the four the <c>ESP_LOGx</c> macro actually had -- when,
    /// how severe, from where and what -- taken from <c>SfuLogRecord</c> with
    /// the format already applied but no level or tag pasted onto the text.
    /// AGENTS.md requires exactly that: the receiving side takes the structure,
    /// and nobody parses a string back into its parts.
    ///
    /// C# が受け取るファームのログ 1 記録。行になる前の形。
    ///
    /// 項目はマクロが実際に持っていた 4 つ ― いつ・どの重さ・どこから・何を。
    /// <c>SfuLogRecord</c> から取り、書式は適用済みだが、段やタグを本文へ
    /// 貼り付けてはいない。AGENTS.md が求めるのはまさにこれで、受ける側が構造を
    /// 受け取り、誰も文字列を解析して部分へ戻さない。
    /// </summary>
    public struct FirmwareLogRecord
    {
        /// <summary>
        /// <c>SFU_LOG_ERROR</c> … <c>SFU_LOG_VERBOSE</c>.
        /// </summary>
        public int Level;

        /// <summary>The virtual clock when it was logged [µs]. / 記録時の仮想時刻 [µs]。</summary>
        public long SimulationMicroseconds;

        /// <summary>The ESP_LOGx tag. / ESP_LOGx のタグ。</summary>
        public string Tag;

        /// <summary>The body, the format already applied. / 書式適用済みの本文。</summary>
        public string Message;
    }

    /// <summary>
    /// Turns firmware log records into structured lines.
    ///
    /// Who reads the records out of the module is somebody else's work: the
    /// owner of <c>IFirmware</c> calls <c>sfu_log_read_record</c> in a loop and
    /// hands each one here. This class only decides the shape of the line, so
    /// that shape is the same whether the records came from the WebGL module,
    /// the editor's native plugin, or a test.
    ///
    /// ファームのログの記録を構造化された行にする。
    ///
    /// 記録をモジュールから取り出すのは別の担当の仕事である。<c>IFirmware</c> の
    /// 持ち主が <c>sfu_log_read_record</c> を繰り返し呼び、1 件ずつここへ渡す。
    /// このクラスは行の形だけを決める。記録が WebGL のモジュール、エディタの
    /// ネイティブプラグイン、試験のどれから来ても、形が同じになるようにする。
    /// </summary>
    public sealed class FirmwareLogPump
    {
        /// <summary>
        /// The <c>event</c> every firmware line carries. One name for all of
        /// them, because what distinguishes them is the <c>tag</c>: the line is
        /// the firmware's, not an event this project defined.
        /// ファームの行が全て持つ <c>event</c>。名前を 1 つにするのは、行を
        /// 分けるのが <c>tag</c> だからである。行はファームのものであり、この
        /// 企画が定めた事象ではない。
        /// </summary>
        public const string EventName = "fw.log";

        /// <summary>
        /// What is reported when the firmware's ring buffer overflowed.
        /// <c>sfu_log_dropped</c> counts since boot, so the pump reports the
        /// increase rather than the total.
        /// ファームのリングバッファが溢れたときに報告する事象。
        /// <c>sfu_log_dropped</c> は起動からの累計なので、汲み出しは総数では
        /// なく増えた分を報告する。
        /// </summary>
        public const string DroppedEvent = "fw.dropped";

        private readonly StructuredLog log;
        private int reportedDropped;

        /// <summary>
        /// Write the lines into <paramref name="log"/>.
        /// 行を <paramref name="log"/> へ書く。
        /// </summary>
        public FirmwareLogPump(StructuredLog log)
        {
            this.log = log;
        }

        /// <summary>
        /// Turn one record into a line.
        ///
        /// The record's own <c>sim_us</c> is used, not the loop's current one:
        /// the firmware logged at its own instant, and reading the ring happens
        /// afterwards. The <c>cmd_id</c> comes from whatever command scope is
        /// open, which is how a firmware line raised while handling
        /// <c>vehicle.arm</c> ends up beside the server's and the CLI's lines
        /// under one <c>sf unity logs --cmd</c>.
        ///
        /// 記録 1 件を行にする。
        ///
        /// 使うのは輪のいまの時刻ではなく記録自身の <c>sim_us</c> である。
        /// ファームはその時点で記録し、リングを読むのはその後だからである。
        /// <c>cmd_id</c> は開いている命令の範囲から付く。<c>vehicle.arm</c> の
        /// 処理中に出たファームの行が、1 つの <c>sf unity logs --cmd</c> で
        /// サーバと CLI の行の隣に並ぶのはこのためである。
        /// </summary>
        public void Pump(in FirmwareLogRecord record)
        {
            log?.WriteRecord(new LogRecord
            {
                Level = FirmwareLogLevels.ToLogLevel(record.Level),
                Source = LogSources.Firmware,
                EventName = EventName,
                Message = record.Message ?? string.Empty,
                SimulationMicroseconds = record.SimulationMicroseconds,
                Tag = record.Tag ?? string.Empty,
            });
        }

        /// <summary>
        /// Report the firmware's own losses. <paramref name="droppedSinceBoot"/>
        /// is <c>sfu_log_dropped</c>'s cumulative count; only the increase since
        /// the last report becomes a line, so calling this every frame costs one
        /// line per actual loss rather than one per frame.
        /// ファーム側の損失を報告する。<paramref name="droppedSinceBoot"/> は
        /// <c>sfu_log_dropped</c> の累計である。前回の報告からの増分だけを行に
        /// するので、毎フレーム呼んでも行は実際に失った回数だけ出る。
        /// </summary>
        public void NoteDropped(int droppedSinceBoot)
        {
            int newlyDropped = droppedSinceBoot - reportedDropped;
            if (newlyDropped <= 0)
            {
                return;
            }

            reportedDropped = droppedSinceBoot;
            log?.Write(LogLevel.Warn, LogSources.Firmware, DroppedEvent,
                       $"the firmware's log ring dropped {newlyDropped} record(s)",
                       new Dictionary<string, object>
                       {
                           { "dropped", newlyDropped },
                           { "dropped_since_boot", droppedSinceBoot },
                       });
        }

        /// <summary>
        /// Forget the loss count, because a power cycle restarts
        /// <c>sfu_log_dropped</c> at zero and the next total must not read as a
        /// negative increase.
        /// 損失の数を忘れる。電源の入れ直しで <c>sfu_log_dropped</c> は 0 から
        /// 数え直すので、次の累計が負の増分に見えてはならない。
        /// </summary>
        public void ResetForNewBoot()
        {
            reportedDropped = 0;
        }
    }
}
