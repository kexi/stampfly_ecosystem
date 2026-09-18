"""
sfpilot.scenes - the situations `sf pilot run --sils` rehearses a decision in.
sfpilot.scenes - `sf pilot run --sils` が判断を試す状況。

A scene is two things: what the emulator is started with (environment
variables), and what is done to it while it flies (a per-cycle hook). Both
are declared in the table below, so reading one entry tells you everything
a scene does.

場面とは 2 つのものである: エミュレータを何を付けて起動するか（環境変数）と、
飛行中に何をするか（周期ごとのフック）。その両方を下の表で宣言するので、
1 項目を読めばその場面がすることは全て分かる。

Existing SILS fault-injection is reused wherever it covers the situation:
`drift` is the established SILS_EMU_FLOW_SCALE knob (`sf sils scenario
--flow-scale`), not a new mechanism. Only `battery_drop` needed something
new, because the Plant's 300mAh pack barely sags over a one-minute run --
and even that is the smallest possible seam (one stdin verb setting the
voltage the INA3221 shim reports; the firmware is untouched).

状況を賄えるものは既存の SILS 故障注入をそのまま使う: `drift` は確立済みの
SILS_EMU_FLOW_SCALE（`sf sils scenario --flow-scale`）であって新しい機構では
ない。新設が要ったのは `battery_drop` だけである。Plant の 300mAh パックは
1 分程度の実行ではほとんど低下しないためで、それも可能な限り小さい継ぎ目に
留めた（INA3221 シムが報告する電圧を設定する stdin の 1 語。ファームは無改変）。
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import DEFAULT_CONFIG

NOMINAL = "nominal"
BATTERY_DROP = "battery_drop"
DRIFT = "drift"


@dataclass
class Scene:
    """One rehearsal situation. / 予行の状況 1 つ。"""

    name: str
    description: str
    env: dict = field(default_factory=dict)
    # Called once per monitor cycle with (link, elapsed_s). None means the
    # scene needs nothing done to it after launch -- the environment it was
    # started with is the whole scene.
    # 監視周期ごとに (link, 経過秒) で 1 回呼ばれる。None は起動後に何もしない
    # 場面（起動時の環境変数がその場面の全て）を意味する。
    drive: Optional[Callable] = None


def _battery_drive(config=DEFAULT_CONFIG):
    """Walk the reported pack voltage down over the flight.

    Linear in time rather than tied to the flight's own energy use: the
    point is to rehearse the DECISION at a repeatable moment, not to model
    a discharge. A real discharge is what the Plant's own battery model
    already does, and it is too slow to reach a decision in one minute.

    報告されるパック電圧を飛行中に下げていく。

    飛行自身の消費電力ではなく時間に対して線形にする。狙いは放電の模擬ではなく、
    再現できる時点で「判断」を予行することだからである。実際の放電は Plant 自身の
    電池モデルが既に行っており、1 分で判断に至るには遅すぎる。
    """
    sils = config.sils
    start_v = sils.battery_scene_start_v
    end_v = sils.battery_scene_end_v

    def drive(link, elapsed_s: float, total_s: float) -> None:
        fraction = 0.0 if total_s <= 0 else min(1.0, elapsed_s / total_s)
        link.set_battery_voltage(start_v + (end_v - start_v) * fraction)

    return drive


def _drift_drive(config=DEFAULT_CONFIG):
    """Apply the sideways force once, and leave it applied.

    The Plant's wind is a level, not a pulse: setting it once holds it
    until it is set again (virtual_board.hpp's sils_board_set_wind). So
    this hook only has to act on the first cycle -- re-sending it every
    cycle would fill the emulator's stdin with lines that change nothing.

    横力を 1 回かけ、かけたままにする。

    Plant の風は「水準」であってパルスではない。1 回設定すれば次に設定される
    まで保たれる（virtual_board.hpp の sils_board_set_wind）。そのため本フックは
    最初の 1 周期で動けばよい。毎周期送ると、何も変えない行でエミュレータの
    stdin を埋めることになる。
    """
    force_n = config.sils.drift_wind_n
    state = {"applied": False}

    def drive(link, elapsed_s: float, total_s: float) -> None:
        if state["applied"]:
            return
        state["applied"] = True
        # NED: x north, y east, z down. The push is along one axis only, so
        # the drift has a single unambiguous direction. See the note on
        # SilsConfig.drift_wind_n about which axis the firmware's estimate
        # then reports it on.
        # NED: x 北・y 東・z 下。押す向きを 1 軸だけにして、流れの向きを一意に
        # する。ファームの推定がそれをどの軸に出すかは SilsConfig.drift_wind_n
        # の注記を参照。
        link.set_wind(force_n, 0.0, 0.0)

    return drive


def scene_table(config=DEFAULT_CONFIG) -> dict:
    """Every scene, by name. / 全場面を名前で引ける表にする。"""
    return {
        NOMINAL: Scene(
            name=NOMINAL,
            description="Nothing wrong: a healthy hover. The decision under "
                        "test is that nothing is done about it. "
                        "（異常なし。何もしないことが試される判断）",
        ),
        BATTERY_DROP: Scene(
            name=BATTERY_DROP,
            description="The pack voltage falls through the low band into the "
                        "danger band. （電圧が下がっていく）",
            drive=_battery_drive(config),
        ),
        DRIFT: Scene(
            name=DRIFT,
            description="A steady sideways force pushes the aircraft while its "
                        "optical flow under-reads the motion, so position hold "
                        "does not fully correct it. （水平に流される）",
            env={"SILS_EMU_FLOW_SCALE": str(config.sils.drift_flow_scale)},
            drive=_drift_drive(config),
        ),
    }


def scene_names() -> tuple:
    """Scene names in the order they are offered. / 提示順の場面名。"""
    return (NOMINAL, BATTERY_DROP, DRIFT)


def get_scene(name: str, config=DEFAULT_CONFIG) -> Scene:
    """Look up a scene, or raise with the list of valid names.
    場面を引く。無ければ有効な名前を添えて例外にする。"""
    table = scene_table(config)
    if name not in table:
        raise KeyError(
            f"unknown scene {name!r} — choose from {', '.join(scene_names())}"
            f"（場面名が不正です）"
        )
    return table[name]
