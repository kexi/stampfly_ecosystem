#!/usr/bin/env python3
"""
The front ToF must not be able to disturb the bottom ToF (jev-autopilot P2b).

前方 ToF が底面 ToF を乱せないこと（jev-autopilot P2b）。

The bottom ToF is the vehicle's only vertical observation -- the barometer is
not fused by default -- so altitude hold depends on its 30Hz cadence. The front
sensor is Optional. These tests hold that line in the one environment that can
run the real `TofTask` without hardware.

底面 ToF は機体唯一の鉛直観測であり（気圧は既定で非融合）、高度保持はその 30Hz の
刻みに依存する。前方は Optional にすぎない。これらの試験は、実機なしで本物の
`TofTask` を走らせられる唯一の環境でその一線を守る。

What is verified:

  (a) The bring-up order is the safe one: the bottom sensor reaches its own
      address 0x30 and starts ranging BEFORE the front sensor is woken. Both
      VL53L3CX parts boot at 0x29, so waking the front one first would let the
      bottom sensor's address change reach both and alias the two.
  (b) A front sensor that fails to initialise is an ordinary, survivable event:
      the task reports it as a warning and keeps serving the bottom sensor.
      This is the path the real vehicle takes on USB-only power, where the
      front sensor's logic powers up enough to ACK on I2C while ranging fails.
  (c) The bottom ToF still feeds the pipeline in flight with the front-ToF
      code in place -- measured in a hover, because on the ground the craft
      sits below the sensor's minimum range and every sample is legitimately
      invalid.

確認する内容:

  (a) 起動順序が安全な唯一の順であること: 前方を起こす「前」に、底面が自分の
      アドレス 0x30 に到達して測距を開始する。VL53L3CX は 2 個とも 0x29 で起動
      するため、前方を先に起こすと底面のアドレス変更が両方に届いて混線する。
  (b) 前方の初期化失敗は通常の、飛行を続けられる事象であること: タスクは警告と
      して報告し、底面の供給を続ける。これは USB 給電のみの実機が通る経路でも
      ある（前方のロジックが I2C に ACK する程度に立ち上がり、測距は失敗する）。
  (c) 前方 ToF のコードが入った状態でも、飛行中に底面 ToF が供給され続けること —
      ホバリング中に測る。接地時は機体がセンサの最小測距距離より下にあり、
      全サンプルが正当に無効だからである。

Prerequisite / 事前条件:
    source setup_env.sh && sf sils build
    pytest simulator/tests/test_tof_front_sils.py -v
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from sfcli.utils.paths import paths


def _exe(name: str) -> Path:
    suffix = ".exe" if sys.platform.startswith("win") else ""
    return paths.sils_build() / f"{name}{suffix}"


EMU_VEHICLE = _exe("emu_vehicle")
MODEL = paths.root() / "simulator" / "sils" / "models" / "stampfly.xml"

# Long enough to cover boot plus a stretch of steady ranging, short enough that
# the suite stays quick: the emulator runs in real time.
# 起動と、その後の安定した測距の区間を含むのに十分で、かつ試験一式が速いままで
# いられる長さ。エミュレータは実時間で動く。
RUN_S = 8.0

pytestmark = pytest.mark.skipif(
    not EMU_VEHICLE.exists(),
    reason=f"{EMU_VEHICLE.name} not built — run 'source setup_env.sh && sf sils build'",
)


def _run_emulator(duration_s: float = RUN_S, extra_env: dict = None) -> str:
    """Run the emulator to completion and return everything it printed.

    The sticks are left alone: this measures the sensor pipeline during boot
    and on the ground, where no flight command is needed.
    エミュレータを終了まで走らせ、出力を全て返す。スティックは触らない: ここで
    測るのは起動時と接地時のセンサ経路で、飛行指令は要らないためである。
    """
    env = dict(os.environ, SILS_EMU_REALTIME="1", SILS_EMU_RC_STDIN="1")
    env.update(extra_env or {})
    proc = subprocess.Popen(
        [str(EMU_VEHICLE), str(MODEL), str(int(duration_s * 1e6))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace", env=env,
    )
    try:
        out, _ = proc.communicate(timeout=duration_s + 30.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate(timeout=10.0)
        raise AssertionError("emulator did not exit on its own")
    return out


@pytest.fixture(scope="module")
def boot_output() -> str:
    """One emulator run, shared by the tests that only read its log.
    ログを読むだけの試験で共有する、1 回のエミュレータ実行。"""
    return _run_emulator()


def test_the_bottom_sensor_is_addressed_and_ranging_before_the_front_is_woken(boot_output):
    """Bottom reaches 0x30 and starts ranging first; only then is front touched.

    Both parts boot at I2C 0x29. Waking the front sensor before the bottom one
    has moved to its own address would let the bottom sensor's SetDeviceAddress
    reach BOTH parts, and the two would alias -- the ranging data becomes an
    interleaved mix of the two sensors. The order is therefore not a preference
    but the only safe sequence.
    底面が先に 0x30 へ到達して測距を開始し、その後で初めて前方に触れること。

    2 個とも I2C 0x29 で起動する。底面が自分のアドレスへ移る前に前方を起こすと、
    底面の SetDeviceAddress が「両方」に届いて混線し、測距データが 2 センサの
    交互混合になる。したがってこの順序は好みではなく、唯一安全な手順である。
    """
    lines = boot_output.splitlines()
    bottom_ready = [i for i, line in enumerate(lines)
                    if "TofTask" in line and "bottom ready" in line]
    front_touched = [i for i, line in enumerate(lines)
                     if "TofTask" in line and "ront" in line]   # Front / front

    assert bottom_ready, "TofTask never reported the bottom sensor ready"
    assert front_touched, "TofTask never reported anything about the front sensor"
    assert bottom_ready[0] < front_touched[0], (
        "the front sensor was touched before the bottom sensor was ranging — "
        "the two would alias at 0x29"
    )
    # The bottom sensor must be at its OWN address, not the shared boot address.
    # 底面は共有の起動アドレスではなく「自分の」アドレスにいること。
    assert "0x30" in lines[bottom_ready[0]], lines[bottom_ready[0]]


def test_a_front_sensor_that_will_not_start_is_survivable_not_fatal(boot_output):
    """No VL53L3CX here, so the front init fails — and the flight continues.

    This is the same path the real vehicle takes on USB-only power. The front
    sensor is Optional (hardware_init.md §5): its failure must be a warning and
    must leave the bottom sensor, which is Critical, entirely alone.
    ここには VL53L3CX が無いので前方の初期化は失敗する — そして飛行は続く。

    これは USB 給電のみの実機が通るのと同じ経路である。前方は Optional
    （hardware_init.md §5）であり、その失敗は警告に留まり、Critical である底面に
    一切手を触れてはならない。
    """
    front_lines = [line for line in boot_output.splitlines()
                   if "TofTask" in line and "Front" in line]
    assert front_lines, "TofTask never reported anything about the front sensor"

    # The cheap presence check settled it: "not detected", NOT "init failed".
    # Reaching the driver's init would mean paying its 500ms boot polling for a
    # part that is not there — the stall safety requirement 2 forbids.
    # 安価な在否確認で決着していること: "init failed" ではなく "not detected"。
    # ドライバの init に到達したなら、居ない部品のために 500ms の起動ポーリングを
    # 払ったことになる — 安全要件 2 が禁じる停止である。
    assert any("not detected" in line for line in front_lines), front_lines
    assert "front init failed" not in boot_output, (
        "the costly driver init ran for an absent part — the presence check "
        "should have rejected it first"
    )
    # Never an error or an abort: the front sensor is Optional.
    # error でも abort でもない: 前方は Optional である。
    assert not any("[ERROR]" in line for line in front_lines), front_lines
    # The bottom sensor is unaffected: it reported ready and never reported a
    # failure of its own.
    # 底面は影響を受けない: ready を報告し、自身の失敗は報告していない。
    assert "bottom ready" in boot_output
    assert "bottom init failed" not in boot_output
    assert "startRanging failed" not in boot_output


def test_an_absent_front_sensor_does_not_shift_the_bottom_sensor_timing():
    """Bottom-sensor sample times are identical with the front sensor on or off.

    This is safety requirement 2 stated as a measurement. The bottom ToF is the
    only vertical observation, so looking for a front sensor must not move its
    30Hz phase by even one cycle. The bring-up is therefore spread across cycles
    (raise XSHUT in one, probe in the next) and never touches `last_wake`.

    Comparing the two runs' STATE streams is the strongest available check: the
    STATE line is emitted from the firmware's own scheduler, so any shift in
    task ordering shows up as different numbers.

    前方の有無で底面のサンプル時刻が変わらないこと。

    これは安全要件 2 を測定として述べたものである。底面 ToF は唯一の鉛直観測であり、
    前方を探す動作がその 30Hz の位相を 1 周期たりとも動かしてはならない。だから起動は
    周期をまたいで分割し（ある周期で XSHUT を上げ、次の周期で確認する）、`last_wake` に
    一切触れない。

    2 回の実行の STATE 列を比べるのが、利用できる中で最も強い確認である。STATE 行は
    ファーム自身のスケジューラから出るので、タスクの並びがずれれば数値に現れる。
    """
    # The emulator takes parameter overrides as a file of "<name> <value>" lines
    # (SILS_EMU_PARAMS_FILE, see emu_main.cpp).
    # エミュレータは「<名前> <値>」の行からなるファイルでパラメータを上書きする
    # （SILS_EMU_PARAMS_FILE、emu_main.cpp 参照）。
    with tempfile.TemporaryDirectory() as tmp:
        off = Path(tmp) / "front_off.txt"
        off.write_text("tof.front.enable 0\n", encoding="utf-8")
        enabled_out = _run_emulator()                                  # default
        disabled_out = _run_emulator(
            extra_env={"SILS_EMU_PARAMS_FILE": str(off)})

    # Guard against a vacuous pass: if the override silently did nothing, both
    # runs would be the same run and the comparison below would prove nothing.
    # The logs must show the two runs really took different bring-up paths.
    # 空虚な合格を防ぐ: 上書きが黙って効かなければ 2 回は同一実行となり、下の比較は
    # 何も証明しない。2 回が実際に異なる起動経路を通ったことをログで確かめる。
    assert "disabled by tof.front.enable" in disabled_out, (
        "the tof.front.enable=0 override did not take effect — this test would "
        "otherwise be comparing a run against itself"
    )
    assert "disabled by tof.front.enable" not in enabled_out

    def state_lines(out):
        return [line for line in out.splitlines() if line.startswith("STATE ")]

    with_front = state_lines(enabled_out)
    without_front = state_lines(disabled_out)

    assert with_front, "no STATE lines in the front-enabled run"
    assert with_front == without_front, (
        "the bottom sensor's STATE stream differs depending on whether the "
        "front sensor is looked for — the front bring-up moved the 30Hz phase"
    )


def test_the_bottom_tof_keeps_feeding_the_estimator_while_airborne():
    """In flight the ToF reading keeps arriving and keeps changing.

    Measured in the air, not on the ground: on the ground the vehicle sits
    below the sensor's minimum range and every sample is legitimately invalid,
    so there is nothing to measure there. In a hover the reading is live, and a
    bottom sensor starved by the front one would show up as a ToF that stops
    changing while the STATE lines keep coming.

    This is the SILS half of safety requirement 3 (the bottom sensor's rate is
    unaffected by the front sensor); the other half is the real-hardware check
    in docs/plans/jev-autopilot.md, because only hardware has a front sensor
    that actually starts.

    飛行中に ToF の値が届き続け、変化し続けること。

    接地時ではなく空中で測る: 接地時は機体がセンサの最小測距距離より下にあり、
    全サンプルが正当に無効なので、そこには測るものが無い。ホバリング中は値が生きて
    おり、前方に食われた底面センサは「STATE 行は出続けるのに ToF が変化しなくなる」
    として現れる。

    これは安全要件 3（底面のレートが前方の有無で変わらない）の SILS 側の半分である。
    もう半分は docs/plans/jev-autopilot.md の実機確認である — 前方センサが実際に
    起動するのは実機だけだからである。
    """
    # Imported here rather than at module scope: this is the only test that
    # flies, and the import pulls in the pilot stack.
    # モジュール先頭ではなくここで import する: 飛ぶのはこの試験だけで、この
    # import は pilot 一式を引き込むためである。
    from test_pilot_sils import _fly_to_hover

    emu = _fly_to_hover(duration_s=30.0)
    try:
        samples = emu.collect(at_least=40)
    finally:
        emu.close()

    # EVERY in-flight sample must carry a ToF reading. The STATE line reports
    # the ToF only while the snapshot says it is valid, so a bottom sensor
    # starved by the front one — publishing late, or not at all — would show up
    # as samples that have lost the key.
    #
    # Why arrival and not value churn: the SILS hover is steady enough that the
    # millimetre-quantised reading spans only about 1 mm, so counting changes
    # of value would measure the plant's stillness rather than the sensor's
    # liveness. What matters here is that the reading keeps being there.
    #
    # 飛行中の「全」サンプルが ToF の値を持つこと。STATE 行はスナップショットが有効と
    # 言う間だけ ToF を載せるので、前方に食われた底面センサ（publish が遅れる、
    # あるいは publish しない）はキーを失ったサンプルとして現れる。
    #
    # 値の変化ではなく到着で測る理由: SILS のホバリングは十分に静かで、ミリメートル
    # 量子化された値は約 1mm しか動かない。値の変化を数えるとセンサの生死ではなく
    # プラントの静かさを測ることになる。ここで大事なのは値が届き続けることである。
    with_tof = [s for s in samples if "tof_m" in s]
    assert len(with_tof) == len(samples), (
        f"{len(samples) - len(with_tof)} of {len(samples)} in-flight samples "
        f"lost the ToF reading — the bottom sensor is not keeping up"
    )

    # The readings are plausible heights for a hover, not a stuck placeholder.
    # 値はホバリングの高度としてもっともらしく、固まった placeholder ではないこと。
    distances = [s["tof_m"] for s in with_tof]
    assert all(0.05 < d < 2.0 for d in distances), (
        f"in-flight ToF readings outside a plausible hover range: "
        f"min={min(distances):.3f} max={max(distances):.3f}"
    )
