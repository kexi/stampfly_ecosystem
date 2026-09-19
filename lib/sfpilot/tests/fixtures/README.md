# テスト用の実測サンプル / Measured samples used by the tests

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。

## 1. 概要

### この資料について

`lib/sfpilot/tests/` の試験が使う、SILS の実測サンプル列を置く。これらは作り物ではなく、実際に飛ばして記録した `STATE` 行である。

### なぜ実測でなければならないか

`FakeJudge` は state の**言葉を読まない**（固定の答えを返すだけである）。そのため、state が健全な飛行を「目標より低い」「急に低下」と書いてしまう誤りは、`FakeJudge` を使うどの試験にも映らなかった。2026-09-19 の Jev 実走で初めて表に出たのがこの種の誤りであり、実測サンプルを Monitor → Summarizer に通して**出てきた言葉そのもの**を検査する試験が、再発を防ぐ唯一の手段である。

## 2. ファイル

| ファイル | 中身 | 採取方法 |
|---------|------|---------|
| `nominal_hover_states.jsonl` | 異常のない飛行 1 回ぶん、610 サンプル・60.3 秒。地上（`IDLE_GROUND`）→ 離陸 → 0.45m 前後での定常ホバリングまでを含む。電圧は 4.19V から 3.62V まで自然に低下する | `sf pilot run --sils --scene nominal` と同じ経路で採取し、3 サンプルに 1 件へ間引いたもの。時刻は先頭を 0 に揃えてある |

### 各行の形式

1 行 1 サンプルの JSON。キーは `sfpilot.link.Sample` の文書化済みのキーと同じである（`t`・`altitude_m`・`battery_v`・`flight_state` 等）。

## 3. 使い方

`lib/sfpilot/tests/test_nominal_flight_words.py` が読み、1 件ずつ Monitor に与える（飛行と同じく 1 周期 1 件）。

---

<a id="english"></a>

## 1. Overview

### About this file

Measured SILS sample streams used by the tests in `lib/sfpilot/tests/`. These are not synthetic: they are `STATE` lines recorded from a real flight.

### Why they have to be measured

`FakeJudge` does not READ the words in a state — it returns a fixed answer. So a fault where the state describes a healthy flight as "below target" or "fell sharply" was invisible to every test that used it. That is exactly the class of fault the live Jev flights of 2026-09-19 exposed, and running measured samples through Monitor → Summarizer and inspecting the WORDS THAT COME OUT is the only way to keep it from coming back.

## 2. Files

| File | Contents | How it was taken |
|------|----------|------------------|
| `nominal_hover_states.jsonl` | One flight with nothing wrong: 610 samples over 60.3 s, covering the ground (`IDLE_GROUND`), the takeoff, and a steady hover around 0.45 m. The pack voltage falls naturally from 4.19 V to 3.62 V | Recorded through the same path as `sf pilot run --sils --scene nominal`, then thinned to every third sample. Timestamps are shifted so the first is 0 |

### Line format

One JSON sample per line. The keys are the documented keys of `sfpilot.link.Sample` (`t`, `altitude_m`, `battery_v`, `flight_state`, ...).

## 3. Use

`lib/sfpilot/tests/test_nominal_flight_words.py` reads them and feeds the Monitor one at a time, as a flight does (one sample per cycle).
