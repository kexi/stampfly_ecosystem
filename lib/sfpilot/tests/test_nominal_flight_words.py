"""
test_nominal_flight_words.py - a healthy flight must not be DESCRIBED as
an unhealthy one.

test_nominal_flight_words.py - 健全な飛行が、健全でない飛行として**書かれ**
ないこと。

These tests exist because of what the live Jev flights of 2026-09-19
exposed. `run nominal` landed after 7.4 seconds, and every judgement Jev
made was correct FOR THE STATE IT WAS GIVEN: the state said the aircraft
was "below target" throughout a perfectly held hover, and that the battery
"fell sharply in the last few seconds" while the pack was essentially
full. The fault was entirely in the words this package produced.

本試験は、2026-09-19 の Jev 実走が明らかにしたことに由来する。`run nominal` は
7.4 秒で着陸したが、Jev の判断は**与えられた state に対しては**すべて妥当だった。
state のほうが、完璧に保持されたホバリングを終始「目標より低い」と書き、ほぼ満充電の
パックを「この数秒で急に低下」と書いていた。誤りはすべて、本パッケージが作る
言葉の側にあった。

Why no existing test caught it: `FakeJudge` does not read the state. It
returns a fixed answer whatever it is handed, so a state full of alarming
words produced exactly the same green test run as a correct one. Nothing
in the suite ever looked at the WORDS. These tests do, and they do it
against samples measured from a real SILS flight rather than composed by
hand -- a hand-written sample would have encoded the same wrong
assumptions the code did (see `fixtures/README.md`).

既存の試験が捕まえられなかった理由: `FakeJudge` は state を読まない。何を渡され
ても固定の答えを返すので、警告だらけの state も正しい state も、まったく同じ
「全件成功」になった。試験一式のどこにも**言葉**を見るものが無かった。本試験は
それを見る。しかも手で組んだサンプルではなく、実際の SILS 飛行から実測した
サンプルに対して見る — 手で書いたサンプルは、コードと同じ誤った前提を埋め込んで
しまうからである（`fixtures/README.md` 参照）。
"""

import json
from pathlib import Path

from sfpilot.config import DEFAULT_CONFIG
from sfpilot.link import Sample, battery_percent
from sfpilot.monitor import Monitor
from sfpilot.summarizer import signature, summarize

FIXTURES = Path(__file__).parent / "fixtures"

# Words that assert something is wrong with the flight. None of them may
# appear while a healthy hover is being described. They are matched
# against the state's English values, which is what Jev actually reads.
# 飛行に異常があると主張する語。健全なホバリングを述べている間、どれも現れて
# はならない。照合先は state の英語の値、すなわち Jev が実際に読むものである。
ALARMING_WORDS = (
    "below target",
    "above target",
    "fell sharply",
    "falling gradually",
    "drifting",
    "diverged",
    "not trustworthy",
    "running low",
    "dangerously low",
)

# The hover is judged from here on, which leaves the takeoff climb out.
# A climb genuinely is "climbing" and genuinely is not yet at its hold
# altitude, so describing it that way is correct, not a fault.
# ホバリングの判定はここから行い、離陸の上昇は対象外とする。上昇は本当に
# 「上昇中」であり、本当にまだ保持高度に達していない。そう書くことは誤りでは
# なく正しい。
SETTLED_AFTER_S = 15.0


def _load(name: str) -> list:
    """The measured samples of one flight, in order.
    1 回の飛行の実測サンプルを順に読む。"""
    path = FIXTURES / name
    return [Sample(**json.loads(line)) for line in path.read_text().splitlines() if line]


def _walk(samples: list):
    """Feed samples one per cycle and yield (elapsed, state).

    One per cycle because that is what a flight does (`pilot.step` calls
    `update` once per monitor period), and because the Monitor's debounce
    advances per call.

    1 周期 1 件で与え、(経過秒, state) を返す。

    飛行がそうするからである（`pilot.step` は監視周期ごとに `update` を 1 回
    呼ぶ）。また Monitor の保持判定も呼び出しごとに進むためである。
    """
    monitor = Monitor()
    for sample in samples:
        assessment = monitor.update([sample])
        yield sample["t"], summarize(assessment), monitor


def test_a_healthy_hover_is_never_described_as_a_problem():
    """No alarming word appears anywhere while a good hover is held.

    This is the test that would have prevented the 7.4 s landing: the
    aircraft was holding 0.441-0.483 m with a healthy pack, and the state
    nonetheless said "below target" and "fell sharply".

    良好なホバリングを保っている間、警告の語がどこにも現れないこと。

    7.4 秒の着陸を防げたはずの試験である。機体は健全なパックで 0.441〜0.483m を
    保っていたのに、state は「目標より低い」「急に低下」と書いていた。
    """
    offenders = []
    for elapsed, state, _monitor in _walk(_load("nominal_hover_states.jsonl")):
        if elapsed < SETTLED_AFTER_S:
            continue
        rendered = json.dumps(state)
        for word in ALARMING_WORDS:
            if word in rendered:
                offenders.append((round(elapsed, 2), word, rendered))

    assert not offenders, (
        f"{len(offenders)} cycle(s) described a healthy hover as a problem; "
        f"first: t={offenders[0][0]}s said {offenders[0][1]!r} in {offenders[0][2]}"
        if offenders else ""
    )


def test_the_hold_altitude_is_taken_from_the_aircraft():
    """The target is the altitude actually held, not a figure in the code.

    The firmware holds whatever the auto-takeoff climb reached
    (api_task.cpp seeds the guidance target from the pose at FLYING), so
    any constant here is wrong by however much the climb differs from it.

    目標高度は、コード内の数値ではなく、実際に保持している高度であること。

    ファームは自動離陸の上昇が到達した高度をそのまま保持する（api_task.cpp は
    FLYING 到達時の姿勢から誘導目標を作る）ため、ここに定数を置けば、上昇の
    到達高度との差だけ必ず誤る。
    """
    samples = _load("nominal_hover_states.jsonl")
    monitor = None
    for _elapsed, _state, monitor in _walk(samples):
        pass

    hover = [s["altitude_m"] for s in samples if s["t"] > SETTLED_AFTER_S]
    assert monitor.target_altitude_m is not None, "no hold altitude was ever adopted"

    # What matters is not that the target equals the eventual mean -- it is
    # taken the moment the climb levels off, and the craft settles a few
    # centimetres further afterwards -- but that every later hover sample
    # still classifies as "on target" against it. That is the property the
    # 7.4 s landing violated, by 0.35 m.
    # 重要なのは、目標がその後の平均と一致することではない。目標は上昇が水平に
    # なった時点で取るもので、機体はその後さらに数 cm 落ち着くからである。重要
    # なのは、以後のホバリングのサンプルがすべて、その目標に対して「目標どおり」
    # と区分されることである。7.4 秒の着陸が 0.35m 破っていたのはこの性質である。
    tolerance = DEFAULT_CONFIG.monitor.altitude_on_target_m
    worst = max(abs(altitude - monitor.target_altitude_m) for altitude in hover)
    assert worst <= tolerance, (
        f"the adopted target {monitor.target_altitude_m:.3f} m leaves the hover "
        f"(band {min(hover):.3f}..{max(hover):.3f} m) up to {worst:.3f} m off it, "
        f"beyond the {tolerance} m that still counts as on target"
    )


def test_the_situation_stops_changing_once_the_hover_settles():
    """The signature holds still while nothing about the flight changes.

    Every signature change is a new situation, and the Arbiter discards any
    answer whose signature moved while it was in flight (arbiter.py). On
    2026-09-19 the classifications flapped on their band edges and 7 of the
    11 answers in `run nominal` were thrown away as stale, which is why the
    aircraft spent most of that flight hovering rather than flying.

    飛行に何の変化も無い間、指紋が動かないこと。

    指紋の変化はすべて新しい状況であり、Arbiter は往復中に指紋が動いた答えを
    破棄する（arbiter.py）。2026-09-19 には区分が境目でばたつき、`run nominal` の
    11 件中 7 件が鮮度切れとして捨てられた。あの飛行のほとんどが飛行ではなく
    待機だった理由がこれである。
    """
    changes = []
    previous = None
    for elapsed, state, _monitor in _walk(_load("nominal_hover_states.jsonl")):
        current = signature(state)
        if elapsed >= SETTLED_AFTER_S and previous is not None and current != previous:
            changes.append(round(elapsed, 2))
        previous = current

    assert not changes, (
        f"the situation was re-classified {len(changes)} time(s) during a "
        f"steady hover, at t={changes[:10]}s"
    )


def test_the_takeoff_voltage_drop_is_not_reported_as_a_failing_battery():
    """Spinning the motors up is a load step, not a discharge.

    Measured on this very fixture: the reading falls 4.19 V -> 3.79 V
    within a second of the motors starting, which the old percentage-based
    trend read as a 44-point collapse and reported as "fell sharply in the
    last few seconds" on a full pack.

    モータの起動は負荷の段差であって放電ではないこと。

    この fixture 自身での実測: モータ始動から 1 秒以内に読みが 4.19V → 3.79V へ
    落ちる。旧来の百分率による傾向判定はこれを 44 ポイントの崩壊と読み、満充電の
    パックについて「この数秒で急に低下」と報告していた。
    """
    for elapsed, state, _monitor in _walk(_load("nominal_hover_states.jsonl")):
        trend = (state.get("battery") or {}).get("trend", "")
        assert "fell sharply" not in trend, (
            f"a full pack was reported as collapsing at t={elapsed:.2f}s"
        )


def test_a_genuinely_falling_battery_is_still_reported():
    """The fix must not have made the trend blind.

    Walks the pack down exactly as the `battery_drop` scene does
    (SilsConfig.battery_scene_start_v -> battery_scene_end_v) and requires
    that the words reach both "running low" and "fell sharply". A trend
    that never fires is as useless as one that always does.

    修正によって傾向が鈍感になっていないこと。

    `battery_drop` の場面とまったく同じようにパックを下げていき
    （SilsConfig.battery_scene_start_v → battery_scene_end_v）、言葉が
    「残り少ない」と「急に低下」の双方に達することを求める。一度も出ない傾向は、
    常に出る傾向と同じくらい役に立たない。
    """
    sils = DEFAULT_CONFIG.sils
    total_s = 60.0
    monitor = Monitor()
    seen_levels, seen_trends = set(), set()
    for step in range(int(total_s * 30)):
        elapsed = step / 30.0
        fraction = min(1.0, elapsed / total_s)
        volts = round(sils.battery_scene_start_v
                      + (sils.battery_scene_end_v - sils.battery_scene_start_v) * fraction, 2)
        state = summarize(monitor.update([Sample(
            t=elapsed, altitude_m=0.52, flight_state="FLYING",
            battery_v=volts, battery_pct=battery_percent(volts),
            vel_n=0.0, vel_e=0.0, vel_d=0.0, roll=0.0, pitch=0.0,
        )]))
        battery = state.get("battery") or {}
        seen_levels.add(battery.get("level", ""))
        seen_trends.add(battery.get("trend", ""))

    assert "running low" in seen_levels, "a draining pack never read as low"
    assert "dangerously low" in seen_levels, "a draining pack never reached danger"
    assert any("fell sharply" in trend for trend in seen_trends), (
        "a pack walked from 4.05 V to 3.35 V never read as falling sharply"
    )
