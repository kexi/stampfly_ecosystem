"""
lib/sfpilot - Jev-assisted autopilot for StampFly (`sf pilot`).
lib/sfpilot - Jev による StampFly 自動操縦（`sf pilot`）。

The design document is `docs/plans/jev-autopilot.md`. The division of
labour it fixes, and that every module here obeys:

  - Numbers are judged by CODE (Monitor). Jev is not a calculator, so it
    never sees a raw number, a threshold or a duration.
  - Words are judged by JEV (Judge). It picks from a fixed, enumerated
    set of actions; it cannot invent one.
  - What actually runs is decided by CODE (Arbiter). A Jev answer is a
    proposal; late, stale, unconfident, or out-of-envelope proposals are
    replaced by "hover in place".

設計文書は `docs/plans/jev-autopilot.md`。そこで決めた役割分担は次のとおりで、
本パッケージの全モジュールがこれに従う:

  - 数値の判定は**コード**（Monitor）。Jev は計算機ではないため、生の数値・
    しきい値・時間を渡さない。
  - 言葉の判断は **Jev**（Judge）。あらかじめ列挙した有限の行動からのみ選ぶ。
  - 実行の可否は**コード**（Arbiter）。Jev の答えは提案であり、期限超過・
    鮮度切れ・低確信・包絡外の提案はすべて「その場で待機」に置き換える。

Import policy: this package's core (config / monitor / summarizer /
arbiter / trace) is standard-library only, so pytest runs without an API
key and without the optional `pilot` extra. `judge.py` imports the
TypeSafe SDK lazily, inside JevJudge, for the same reason.

import 方針: 中核（config / monitor / summarizer / arbiter / trace）は標準
ライブラリのみで書く。pytest を API キー無し・任意依存 `pilot` 無しで通す
ため。`judge.py` も同じ理由で TypeSafe SDK を JevJudge の中で遅延 import する。
"""

__version__ = "0.1.0"
