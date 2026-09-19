#!/usr/bin/env python3
"""
The forward distance becomes words, and a close wall stops the craft (P4b).

前方距離が言葉になること、そして近い壁が機体を止めること（P4b）。

What is verified:

  (a) The words themselves, against MEASURED samples: a real approach to a
      wall 1.0 m away is described as a wall getting near, and a real flight
      in an empty room never produces that description.
  (b) An invalid forward reading is NOT clearance. This is the one that
      matters: the real part reports invalid both when nothing is ahead and
      when a surface is too close to resolve (jev-autopilot §4.8.6), so
      reading an absent value as "open" would fly the craft into the case it
      cannot see.
  (c) The immediate stop rule fires from the Monitor alone, without a
      judgement, and the Arbiter carries it through as a stop.

確認する内容:

  (a) 語そのものを「実測」サンプルに対して: 1.0m 先の壁への実際の接近が「壁が
      近づいている」と記述され、空の部屋での実際の飛行がその記述を一度も生まない
      こと。
  (b) 無効な前方の読み値が「空いている」では「ない」こと。ここが肝心である: 実機は
      前に何も無いときも、面が近すぎて復元できないときも無効を返す（§4.8.6）。
      無い値を「開けている」と読めば、見えていない方の場合へ機体を進めることになる。
  (c) 即時停止則が Monitor だけで、判断を待たずに発火し、Arbiter がそれを停止として
      通すこと。

WHY MEASURED FIXTURES: a FakeJudge does not read the state, so a wrong word
reaches no assertion of its own -- that is how five state bugs survived a
green suite until the first live-Jev flight (§4.5). The fixtures here are
captured from real SILS flights (see fixtures/README.md), so the words are
checked against distances the sensor actually produced.

実測の fixture を使う理由: FakeJudge は state を読まないので、誤った語はどの表明にも
届かない ―― 5 件の state の不具合が、実際に Jev を飛ばすまで全件成功の試験一式を
生き延びたのはそのためである（§4.5）。ここの fixture は実際の SILS 飛行から採取して
あり（fixtures/README.md 参照）、語はセンサが実際に出した距離に対して確認される。
"""

import json
from pathlib import Path

import pytest

from sfpilot.arbiter import Arbiter, VERDICT_STOP
from sfpilot.config import DEFAULT_CONFIG
from sfpilot.link import Sample
from sfpilot.monitor import (
    FORWARD_OPEN,
    FORWARD_SOMEWHAT_NEAR,
    FORWARD_UNKNOWN,
    FORWARD_WALL_NEAR,
    SAFETY_NONE,
    SAFETY_STOP,
    Monitor,
)
from sfpilot.summarizer import assert_no_numbers, summarize

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> list:
    """Measured samples, one per line, oldest first.
    実測サンプル。1 行 1 件、古い順。"""
    path = FIXTURES / name
    return [Sample(**json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines() if line]


def _words(samples: list) -> list:
    """Feed samples one per cycle and collect the forward word each time.

    One sample per `update` call because a flight does exactly that, and the
    hysteresis is a function of the sequence rather than of any one reading.
    1 周期 1 サンプルで流し、毎回の前方の語を集める。飛行がまさにそうするから
    であり、ヒステリシスは個々の読み値ではなく列の関数だからである。
    """
    monitor = Monitor()
    return [monitor.update([sample]).forward_clearance for sample in samples]


# =============================================================================
# (a) The words, against measured flights / 実測飛行に対する語
# =============================================================================

def test_a_real_approach_to_a_wall_ends_in_wall_near():
    """A measured approach is described as somewhat near, then as a wall.

    The fixture is a real approach: the reading starts at 1.017 m and closes
    to 0.11 m. Note it never passes through "open" -- the scene's wall stands
    1.0 m away, which is already inside the "somewhat near" band
    (`somewhat_near_m` = 1.5 m). That is the scene being deliberately small
    enough to fly in, not a misclassification: a wall the craft can reach
    before the envelope's 2 m radius has to start inside that band. The
    assertion therefore checks the transition the approach actually contains.

    実測の接近が「やや近い」→「壁が近い」と記述されること。

    fixture は実際の接近である（読み値は 1.017m から 0.11m へ詰まる）。「開けている」を
    一度も通らない点に注意 ―― 場面の壁は 1.0m 先に立っており、それは既に「やや近い」の
    帯域（`somewhat_near_m` = 1.5m）の内側である。これは誤区分ではなく、場面が飛べる
    大きさに意図して収めてある結果である: 飛行領域の半径 2m の内側で到達できる壁は、その
    帯域の中から始まるほかない。したがって表明は、この接近が実際に含む遷移を確認する。
    """
    samples = _load("wall_approach_states.jsonl")
    words = _words(samples)

    # The first rows are the boot seconds, before the forward part has been
    # brought up: they legitimately carry no reading. The approach is judged
    # from the first cycle that actually measured something.
    # 最初の数行は起動中で、前方の部品が立ち上がる前である。読み値を持たないのが
    # 正しい。接近の判定は、実際に何かを測った最初の周期から行う。
    first_measured = next(i for i, s in enumerate(samples) if "tof_front_m" in s)
    assert words[first_measured] == FORWARD_SOMEWHAT_NEAR, (
        f"the approach began at {samples[first_measured]['tof_front_m']:.3f} m "
        f"and was described as {words[first_measured]!r}")
    assert words[-1] == FORWARD_WALL_NEAR, (
        f"the approach ended at 0.11 m and was described as {words[-1]!r}")
    # Once the wall is near it stays near: the hysteresis must not let a
    # reading sitting on the threshold flip the description back to open.
    # 一度「壁が近い」になったら、そのままであること: しきい値上の読み値で記述が
    # 「開けている」へ戻ってはならない。
    first_near = words.index(FORWARD_WALL_NEAR)
    assert FORWARD_OPEN not in words[first_near:], (
        "the description returned to 'open' after the wall was already near")


def test_an_empty_room_is_never_described_as_a_wall():
    """A real flight with no obstacle never reports a wall, at any moment.

    Measured in the `nominal` scene, where the forward part is absent
    entirely, so every forward reading is invalid. The point is what that
    absence must NOT become: a wall that is not there would stop a healthy
    flight for the rest of its duration.

    障害物の無い実際の飛行が、どの瞬間にも壁を報告しないこと。

    前方の部品が丸ごと不在の `nominal` 場面での実測なので、前方の読み値は全て無効で
    ある。要点は、その不在が「何になってはならないか」である: 在りもしない壁は、
    健全な飛行を以後ずっと止めてしまう。
    """
    words = _words(_load("open_room_states.jsonl"))

    assert FORWARD_WALL_NEAR not in words, (
        "an empty room was described as having a wall near")
    assert FORWARD_SOMEWHAT_NEAR not in words, (
        "an empty room was described as having something somewhat near")


# =============================================================================
# (b) An invalid reading is not clearance / 無効は「空いている」ではない
# =============================================================================

def test_a_forward_reading_that_never_arrives_is_unknown_not_open():
    """With no forward sensor at all, the word is 'cannot measure'.

    This is the distinction the whole feature rests on. `open` would mean
    the code had decided there is clear space ahead on the strength of
    having measured nothing.
    前方センサが一切無いとき、語は「測定不能」であること。

    この機能全体が乗っている区別である。`open` であれば、「何も測らなかった」ことを
    根拠に「前は空いている」とコードが判断したことになる。
    """
    monitor = Monitor()
    assessment = monitor.update([Sample(t=0.0, altitude_m=0.5)])

    assert assessment.forward_clearance == FORWARD_UNKNOWN
    assert assessment.forward_clearance != FORWARD_OPEN


def test_a_dropped_reading_in_front_of_a_wall_keeps_the_wall():
    """A brief invalid reading holds the last valid word, not 'open'.

    A sample dropped in front of a wall is still a wall. Within the grace
    period the description must not improve just because the sensor blinked.
    壁の前での一時的な無効は、最後の有効な語を保つこと（「開けている」ではなく）。

    壁の前で落ちた 1 サンプルは、依然として壁である。猶予の間は、センサが瞬きした
    というだけで記述が好転してはならない。
    """
    monitor = Monitor()
    monitor.update([Sample(t=0.0, tof_front_m=0.4)])       # a wall, close
    grace = DEFAULT_CONFIG.forward.invalid_grace_s

    still_near = monitor.update([Sample(t=grace * 0.5)])   # reading drops out
    assert still_near.forward_clearance == FORWARD_WALL_NEAR, (
        "a dropped sample in front of a wall was read as the wall going away")


def test_a_reading_that_stays_missing_becomes_unknown():
    """Past the grace period the stale value is abandoned for 'unknown'.

    The last valid reading is evidence with an expiry: holding a wall
    forever on a memory would freeze the craft, and claiming clear space
    would be worse. Neither -- it becomes unknown.
    猶予を過ぎれば、古い値は捨てられ「測定不能」になること。

    最後の有効な読み値には期限がある。記憶だけで壁を保ち続ければ機体は固まり、
    空いていると言えばもっと悪い。どちらでもなく「測定不能」になる。
    """
    monitor = Monitor()
    monitor.update([Sample(t=0.0, tof_front_m=0.4)])
    grace = DEFAULT_CONFIG.forward.invalid_grace_s

    stale = monitor.update([Sample(t=grace + 0.5)])
    assert stale.forward_clearance == FORWARD_UNKNOWN


def test_unknown_does_not_by_itself_stop_the_craft():
    """'Cannot measure' is a reason for caution, not an automatic stop.

    Stopping on every unmeasurable reading would stop the craft on every
    flight without a forward sensor -- which is every flight the regression
    suite runs. Caution is Jev's to exercise on the word; the code stops
    only on a measured wall.
    「測定不能」は警戒の理由であって、自動的な停止ではないこと。

    測れない読み値のたびに止めるなら、前方センサの無い全ての飛行で止まることに
    なる ―― 回帰一式が走らせる飛行は全てそれである。語に対する警戒は Jev の仕事で
    あり、コードが止めるのは実測された壁に対してだけである。
    """
    monitor = Monitor()
    assessment = monitor.update([Sample(t=0.0, altitude_m=0.5)])

    assert assessment.forward_clearance == FORWARD_UNKNOWN
    assert assessment.safety_action == SAFETY_NONE


# =============================================================================
# (c) The immediate stop rule / 即時停止則
# =============================================================================

def test_a_wall_within_the_stop_distance_stops_without_asking_jev():
    """The Monitor alone returns a stop; no judgement is involved.

    The round trip has no guaranteed upper bound, so the decision that must
    happen before an answer could arrive is made by code.
    Monitor だけで停止を返すこと。判断は関与しない。

    往復時間に上限の保証は無いので、答えが届くより前に下さねばならない判断は
    コードが下す。
    """
    stop_at = DEFAULT_CONFIG.forward.stop_distance_m
    monitor = Monitor()

    assessment = monitor.update([Sample(t=0.0, tof_front_m=stop_at - 0.05)])

    assert assessment.safety_action == SAFETY_STOP
    assert assessment.forward_clearance == FORWARD_WALL_NEAR
    assert assessment.safety_reason, "a stop must say why it stopped"


def test_open_space_ahead_does_not_stop_the_craft():
    """A wall comfortably far away is not a reason to stop.
    十分に遠い壁は停止の理由にならないこと。"""
    monitor = Monitor()
    far = DEFAULT_CONFIG.forward.somewhat_near_m + 1.0

    assessment = monitor.update([Sample(t=0.0, tof_front_m=far)])

    assert assessment.forward_clearance == FORWARD_OPEN
    assert assessment.safety_action == SAFETY_NONE


def test_the_stop_does_not_chatter_at_the_threshold():
    """Once stopped, a reading just past the threshold stays stopped.

    Without hysteresis a reading sitting on the boundary alternates between
    stop and continue, which is a craft braking and accelerating at a wall.
    一度止まったら、しきい値をわずかに超えた読み値では止まったままであること。

    ヒステリシスが無いと、境界上の読み値で停止と継続が交互になる。それは壁の前で
    制動と加速を繰り返す機体である。
    """
    cfg = DEFAULT_CONFIG.forward
    monitor = Monitor()
    monitor.update([Sample(t=0.0, tof_front_m=cfg.stop_distance_m - 0.05)])

    # Just above the stop distance, but not yet past the release distance.
    # 停止距離のすぐ上。ただし解除距離にはまだ達していない。
    between = (cfg.stop_distance_m + cfg.release_distance_m) / 2.0
    held = monitor.update([Sample(t=0.1, tof_front_m=between)])
    assert held.safety_action == SAFETY_STOP, (
        "the stop was released while the wall was still within the release "
        "distance — the classification will chatter")

    # Clear of the release distance: the craft may move again.
    # 解除距離を超えた: 再び動いてよい。
    cleared = monitor.update([Sample(t=0.2, tof_front_m=cfg.release_distance_m + 0.3)])
    assert cleared.safety_action == SAFETY_NONE


def test_the_arbiter_carries_an_obstacle_stop_through_as_a_stop():
    """The Monitor's stop outranks any judgement, as the safety rules do.

    An obstacle stop is a monitor-sourced action, so the Arbiter must pass
    it through without consulting an answer -- including when no judgement
    has arrived at all.
    Monitor の停止が、他の安全則と同じくあらゆる判断に優先すること。

    障害物による停止は monitor 由来の動作なので、Arbiter は答えを参照せずに
    通さねばならない ―― 判断が一切届いていないときも含めて。
    """
    monitor = Monitor()
    assessment = monitor.update(
        [Sample(t=0.0, tof_front_m=DEFAULT_CONFIG.forward.stop_distance_m - 0.1)])

    verdict = Arbiter().decide(
        assessment, judgement=None, asked_signature="x",
        current_signature="x", now=0.0)

    assert verdict.action == VERDICT_STOP
    assert verdict.source == "monitor"


# =============================================================================
# The state Jev reads / Jev が読む state
# =============================================================================

def test_the_forward_word_reaches_jev_in_english_and_without_numbers():
    """The state carries a translated forward_clearance and no figures.

    An untranslated classification would send Japanese to a model that is
    most accurate in English, and would do so silently -- the failure mode
    that put an untranslated battery trend one config change away from
    reaching Jev (§4.5).
    state が訳された forward_clearance を持ち、数値を持たないこと。

    訳し漏らした区分は、英語で最も精度の高いモデルへ日本語を送ることになり、しかも
    黙ってそうなる ―― 訳されない電池の傾向が、設定変更 1 つで Jev へ届く寸前だった
    のと同じ失敗の形である（§4.5）。
    """
    monitor = Monitor()
    assessment = monitor.update([Sample(t=0.0, tof_front_m=0.4, altitude_m=0.5)])

    state = summarize(assessment)

    assert state["flight"]["forward_clearance"] == "wall near"
    assert_no_numbers(state)   # raises if any figure slipped in


@pytest.mark.parametrize("metres,expected", [
    (0.30, "wall near"),
    (0.70, "wall near"),          # above the stop band, still near
    (1.20, "somewhat near"),
    (2.50, "open"),
])
def test_each_band_translates_to_its_own_english_word(metres, expected):
    """Every band has a distinct English word, so none is silently merged.
    各帯域が固有の英語の語を持ち、黙って統合される帯域が無いこと。"""
    monitor = Monitor()
    assessment = monitor.update([Sample(t=0.0, tof_front_m=metres)])

    assert summarize(assessment)["flight"]["forward_clearance"] == expected
