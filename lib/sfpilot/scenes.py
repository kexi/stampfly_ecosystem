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
`drift` is the Plant's established wind hook (the *.scn `wind` event), not
a new mechanism. Only `battery_drop` needed something new, because the
Plant's 300mAh pack barely sags over a one-minute run -- and even that is
the smallest possible seam (one stdin verb setting the voltage the INA3221
shim reports; the firmware is untouched).

状況を賄えるものは既存の SILS 故障注入をそのまま使う: `drift` は確立済みの
Plant の wind フック（*.scn の `wind` 事象）であって新しい機構ではない。
新設が要ったのは `battery_drop` だけである。Plant の 300mAh パックは
1 分程度の実行ではほとんど低下しないためで、それも可能な限り小さい継ぎ目に
留めた（INA3221 シムが報告する電圧を設定する stdin の 1 語。ファームは無改変）。
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import DEFAULT_CONFIG

NOMINAL = "nominal"
BATTERY_DROP = "battery_drop"
DRIFT = "drift"
WALL_AHEAD = "wall_ahead"
DEAD_END = "dead_end"

# The forward ToF is absent from SILS unless this is set. It is opt-in because
# a present forward sensor changes what TofTask does during bring-up, and the
# regression baseline (28 PASS and a SHA256) is taken with it absent.
# 前方 ToF は、これを設定しない限り SILS に存在しない。オプトインにするのは、前方が
# 居ると TofTask の起動処理が変わるためで、回帰の基準（28 PASS と SHA256）は
# 前方が居ない状態で取られている。
FRONT_TOF_ENV = {"SILS_EMU_FRONT_TOF": "1"}


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


def _walls_drive(walls, config=DEFAULT_CONFIG):
    """Place the scene's walls once, on the first cycle.

    Built once and left alone, like the wind level: walls do not move. The
    first cycle is the earliest point at which the Plant is certainly
    attached, which is why this is a hook rather than part of the launch
    environment -- an obstacle sent before the Plant exists would be dropped
    silently, and the flight would then measure an empty room while claiming
    to measure a wall.

    場面の壁を最初の 1 周期で 1 度だけ置く。

    風の水準と同じく、1 度組み立てたらそのままにする。壁は動かない。最初の周期は
    Plant が確実に接続されている最も早い時点であり、これを起動時の環境変数ではなく
    フックにしている理由でもある ―― Plant が存在する前に送られた障害物は黙って
    捨てられ、飛行は「壁を測っている」と言いながら空の部屋を測ることになる。
    """
    state = {"placed": False}

    def drive(link, elapsed_s: float, total_s: float) -> None:
        if state["placed"]:
            return
        state["placed"] = True
        for n0, e0, n1, e1 in walls:
            link.add_wall(n0, e0, n1, e1)

    return drive


def _wall_ahead_walls(config=DEFAULT_CONFIG) -> tuple:
    """One wall across the craft's path, `wall_distance_m` to the north.
    機体の進路を横切る壁 1 枚。北へ `wall_distance_m`。"""
    sils = config.sils
    north = sils.wall_distance_m
    half = sils.wall_half_width_m
    return ((north, -half, north, half),)


def _dead_end_walls(config=DEFAULT_CONFIG) -> tuple:
    """The wall ahead plus an east side wall; the west side is left open.
    正面の壁に東の側壁を加える。西側は開けたままにする。"""
    sils = config.sils
    north = sils.wall_distance_m
    half = sils.wall_half_width_m
    side = sils.dead_end_side_m
    return (
        (north, -half, north, half),      # the far wall, straight ahead
        (0.0, side, north, side),         # the east side, from beside to ahead
    )


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
            description="A steady sideways force pushes the aircraft faster than "
                        "position hold pulls it back. （水平に流される）",
            drive=_drift_drive(config),
        ),
        WALL_AHEAD: Scene(
            name=WALL_AHEAD,
            description="A wall stands across the flight path ahead. The "
                        "decision under test is stopping before it. "
                        "（正面に壁。手前で止まれるか）",
            env=dict(FRONT_TOF_ENV),
            drive=_walls_drive(_wall_ahead_walls(config), config),
        ),
        DEAD_END: Scene(
            name=DEAD_END,
            description="A wall ahead and one to the east; the west side is "
                        "open. （袋小路。西だけが開いている）",
            env=dict(FRONT_TOF_ENV),
            drive=_walls_drive(_dead_end_walls(config), config),
        ),
    }


def scene_names() -> tuple:
    """Scene names in the order they are offered. / 提示順の場面名。"""
    return (NOMINAL, BATTERY_DROP, DRIFT, WALL_AHEAD, DEAD_END)


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
