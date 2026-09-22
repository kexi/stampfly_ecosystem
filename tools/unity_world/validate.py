#!/usr/bin/env python3
"""
Structural and semantic checks for StampFly world files (*.world.json).
StampFly 空間ファイル（*.world.json）の形式検査と意味検査。

Two layers of checking:
検査は 2 段:

1. Structure -- keys, types, ranges, enums, per-type required fields. These
   mirror simulator/unity/Schemas/world.schema.json, which stays the written
   specification. A test (tools/unity_world/tests/test_validate.py) compares the
   two so they cannot drift apart.
   形式 -- 鍵・型・範囲・列挙・種類ごとの必須項目。仕様の文書である
   world.schema.json と対応し、試験が両者の一致を守る。

2. Meaning -- duplicate ids, obstacles outside the room, obstacle overlap,
   the spawn point being inside an obstacle or outside the room, takeoff
   clearance above the spawn point, and openings narrower than the vehicle.
   意味 -- id の重複、部屋の外、重なり、出発点、離陸の余地、狭すぎる開口。

Errors block loading; warnings only advise. validate_file() returns errors only.
誤りは読み込みを止める。警告は助言のみ。validate_file() は誤りだけを返す。

Standalone use / 単体実行:
    python3 tools/unity_world/validate.py <file...>
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Constants shared with the schema and the C# loader.
# スキーマと C# 読み込み側で共有する定数。
# ---------------------------------------------------------------------------

FORMAT_MARKER = "stampfly-world"
FORMAT_VERSION = 1
FRAME = "ENU"

#: Obstacle kinds, in the order the schema lists them.
#: 障害物の種類（スキーマの並び順）。
OBSTACLE_TYPES = (
    "box",
    "pillar",
    "wall",
    "gate",
    "ring",
    "tunnel",
    "table",
    "chair",
    "sofa",
    "shelf",
    "bed",
    "step",
    "ramp",
    "pad",
    "bowling_pin",
)

#: Kinds that are hollow and therefore require a frame/tube thickness.
#: 中空なので肉厚が必須の種類。
HOLLOW_TYPES = ("gate", "ring", "tunnel")

FLOOR_PATTERNS = ("checker", "grid", "plain", "noise", "stripes")
LIGHT_PRESETS = ("day", "indoor", "dim")

#: Vehicle collision box from simulator/sils/models/stampfly.xml:
#: 0.0816 x 0.0816 x 0.0206 m. The width is what must fit through an opening.
#: 機体の衝突箱（simulator/sils/models/stampfly.xml）。開口を通る幅はこれ。
VEHICLE_WIDTH_M = 0.0816
VEHICLE_HEIGHT_M = 0.0206

#: Free height required directly above the spawn point so the vehicle can climb
#: out of the downward-ToF blind zone and settle before it meets anything.
#: 出発点の真上に要る余地。下向き ToF の盲点を抜けて落ち着くまでに要る高さ。
TAKEOFF_CLEARANCE_M = 0.6

#: An opening must exceed the vehicle width by at least this margin, otherwise
#: passing through it is a matter of luck rather than of control.
#: 開口はこの余裕以上に機体より広いこと。でなければ通過は運任せになる。
OPENING_MARGIN_M = 0.04

#: Overlap of this fraction of the smaller obstacle's volume is reported as a
#: warning: obstacles may touch on purpose (a pad on a table), but a deep
#: interpenetration is usually a mistake in the placement.
#: この割合を超える食い込みは警告にする。接するのは意図的なこともあるが、
#: 深く貫いているのは大抵置き間違いである。
OVERLAP_WARN_FRACTION = 0.25

_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

#: Per-type meaning of size[0..2] and where the origin sits. Kept next to the
#: checks that use it, and compared against the schema's docs by the tests.
#: size[0..2] の意味と原点の位置。検査のすぐ隣に置き、試験でスキーマと照合する。
SIZE_SEMANTICS: dict[str, dict[str, str]] = {
    "bowling_pin": {
        "size": "overall diameter along x / overall diameter along y / total height (z)",
        "origin": "centre of the bottom face",
    },
    "box": {
        "size": "width (x) / depth (y) / height (z)",
        "origin": "centre of the bottom face",
    },
    "pillar": {
        "size": "width (x) / depth (y) / height (z)",
        "origin": "centre of the bottom face",
    },
    "wall": {
        "size": "length (x) / thickness (y) / height (z)",
        "origin": "centre of the bottom face",
    },
    "gate": {
        "size": "opening width (x) / depth of the frame along y / opening height (z)",
        "origin": "centre of the opening's bottom edge, at floor level",
    },
    "ring": {
        "size": "inner diameter (x) / inner diameter (z, equal to x) / unused, set equal",
        "origin": "centre of the ring's opening",
    },
    "tunnel": {
        "size": "opening width (x) / length along y / opening height (z)",
        "origin": "centre of the entry opening's bottom edge",
    },
    "chair": {
        "size": "footprint width (x) / footprint depth (y) / total height (z)",
        "origin": "centre of the bottom face",
    },
    "sofa": {
        "size": "footprint width (x) / footprint depth (y) / total height (z)",
        "origin": "centre of the bottom face",
    },
    "shelf": {
        "size": "footprint width (x) / footprint depth (y) / total height (z)",
        "origin": "centre of the bottom face",
    },
    "bed": {
        "size": "footprint width (x) / footprint depth (y) / total height (z)",
        "origin": "centre of the bottom face",
    },
    "table": {
        "size": "top width (x) / top depth (y) / height of the top surface (z)",
        "origin": "centre of the footprint on the floor",
    },
    "step": {
        "size": "width (x) / depth (y) / height (z)",
        "origin": "centre of the bottom face",
    },
    "ramp": {
        "size": "width (x) / run along y / rise (z)",
        "origin": "centre of the low edge, at the bottom",
    },
    "pad": {
        "size": "width (x) / depth (y) / thickness (z)",
        "origin": "centre of the bottom face",
    },
}


# ---------------------------------------------------------------------------
# Small helpers.
# 補助関数。
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    """True for a JSON number (bool is not a number here). / JSON の数値か。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite_number(value: Any) -> bool:
    """True for a finite JSON number. / 有限の数値か。"""
    return _is_number(value) and math.isfinite(float(value))


def _vec3(value: Any) -> list[float] | None:
    """Return a 3-vector of finite numbers, or None. / 有限数 3 つなら返す。"""
    if not isinstance(value, list) or len(value) != 3:
        return None
    if not all(_is_finite_number(v) for v in value):
        return None
    return [float(v) for v in value]


def _fmt(value: float) -> str:
    """Format a length for a message without trailing noise. / 長さの整形。"""
    return f"{value:.4g}"


# ---------------------------------------------------------------------------
# Structural checks (mirroring the JSON Schema).
# 形式検査（JSON Schema と対応する）。
# ---------------------------------------------------------------------------


def _check_root_keys(world: dict, errors: list[str]) -> None:
    """Check the presence and the absence of top-level keys. / 最上位の鍵。"""
    required = ("format", "version", "units", "frame", "name", "room", "floor", "spawn", "obstacles")
    allowed = set(required) | {"description", "light"}
    for key in required:
        if key not in world:
            errors.append(f"root: required key '{key}' is missing")
    for key in sorted(set(world) - allowed):
        errors.append(f"root: unknown key '{key}' is not part of the format")


def _check_header(world: dict, errors: list[str]) -> None:
    """Check the format marker, version, units and frame. / 目印・版・単位・座標系。"""
    if "format" in world and world["format"] != FORMAT_MARKER:
        errors.append(
            f"format: must be '{FORMAT_MARKER}', but it is {world['format']!r}; "
            "this file is not a StampFly world file"
        )
    if "version" in world and world["version"] != FORMAT_VERSION:
        errors.append(
            f"version: must be {FORMAT_VERSION}, but it is {world['version']!r}; "
            "no other version of this format exists"
        )
    if "frame" in world and world["frame"] != FRAME:
        errors.append(
            f"frame: must be '{FRAME}' (x=east, y=north, z=up, right-handed), "
            f"but it is {world['frame']!r}"
        )

    units = world.get("units")
    if "units" in world:
        if not isinstance(units, dict):
            errors.append("units: must be an object with 'length' and 'angle'")
        else:
            if units.get("length") != "m":
                errors.append(f"units.length: must be 'm', but it is {units.get('length')!r}")
            if units.get("angle") != "deg":
                errors.append(f"units.angle: must be 'deg', but it is {units.get('angle')!r}")
            for key in sorted(set(units) - {"length", "angle"}):
                errors.append(f"units: unknown key '{key}' is not part of the format")

    name = world.get("name")
    if "name" in world and not (isinstance(name, str) and _ID_RE.match(name)):
        errors.append(
            f"name: must be lower-case letters, digits and underscore (1-64 chars), "
            f"but it is {name!r}"
        )

    if "description" in world:
        desc = world["description"]
        if not isinstance(desc, dict):
            errors.append("description: must be an object with 'ja' and 'en'")
        else:
            for lang in ("ja", "en"):
                text = desc.get(lang)
                if not isinstance(text, str) or not text.strip():
                    errors.append(f"description.{lang}: must be a non-empty string")
            for key in sorted(set(desc) - {"ja", "en"}):
                errors.append(f"description: unknown key '{key}'; only 'ja' and 'en' are allowed")

    if "light" in world and world["light"] not in LIGHT_PRESETS:
        errors.append(
            f"light: must be one of {', '.join(LIGHT_PRESETS)}, but it is {world['light']!r}"
        )


def _check_room(world: dict, errors: list[str]) -> list[float] | None:
    """Check the room block and return its interior size. / 部屋を検査し内寸を返す。"""
    room = world.get("room")
    if not isinstance(room, dict):
        if "room" in world:
            errors.append("room: must be an object with 'size', 'walls' and 'ceiling'")
        return None

    for key in ("size", "walls", "ceiling"):
        if key not in room:
            errors.append(f"room: required key '{key}' is missing")
    for key in sorted(set(room) - {"size", "walls", "ceiling"}):
        errors.append(f"room: unknown key '{key}' is not part of the format")

    for key in ("walls", "ceiling"):
        if key in room and not isinstance(room[key], bool):
            errors.append(f"room.{key}: must be true or false, but it is {room[key]!r}")

    size = _vec3(room.get("size"))
    if size is None:
        if "size" in room:
            errors.append("room.size: must be three finite numbers [x_east, y_north, z_up]")
        return None
    for axis, value in zip("xyz", size):
        if value <= 0:
            errors.append(f"room.size: the {axis} extent must be greater than 0, but it is {_fmt(value)} m")
        elif value > 100:
            errors.append(f"room.size: the {axis} extent must be at most 100 m, but it is {_fmt(value)} m")
    return size if all(v > 0 for v in size) else None


def _check_floor(world: dict, errors: list[str]) -> None:
    """Check the floor block. / 床を検査する。"""
    floor = world.get("floor")
    if not isinstance(floor, dict):
        if "floor" in world:
            errors.append("floor: must be an object with 'pattern', 'pitch', 'colors' and 'flow_quality'")
        return

    for key in ("pattern", "pitch", "colors", "flow_quality"):
        if key not in floor:
            errors.append(f"floor: required key '{key}' is missing")
    for key in sorted(set(floor) - {"pattern", "pitch", "colors", "flow_quality"}):
        errors.append(f"floor: unknown key '{key}' is not part of the format")

    if "pattern" in floor and floor["pattern"] not in FLOOR_PATTERNS:
        errors.append(
            f"floor.pattern: must be one of {', '.join(FLOOR_PATTERNS)}, "
            f"but it is {floor['pattern']!r}"
        )
    if "pitch" in floor:
        pitch = floor["pitch"]
        if not _is_finite_number(pitch) or pitch <= 0:
            errors.append(f"floor.pitch: must be a number greater than 0 m, but it is {pitch!r}")
        elif pitch > 10:
            errors.append(f"floor.pitch: must be at most 10 m, but it is {_fmt(float(pitch))} m")
    if "colors" in floor:
        colors = floor["colors"]
        if not isinstance(colors, list) or len(colors) != 2:
            errors.append("floor.colors: must be exactly two colors, each '#rrggbb'")
        else:
            for index, color in enumerate(colors):
                if not (isinstance(color, str) and _COLOR_RE.match(color)):
                    errors.append(f"floor.colors[{index}]: must be '#rrggbb', but it is {color!r}")
    if "flow_quality" in floor:
        quality = floor["flow_quality"]
        if not _is_finite_number(quality) or not 0.0 <= float(quality) <= 1.0:
            errors.append(
                f"floor.flow_quality: must be a number between 0 and 1, but it is {quality!r}"
            )


def _check_spawn(world: dict, errors: list[str]) -> list[float] | None:
    """Check the spawn block and return its position. / 出発点を検査し位置を返す。"""
    spawn = world.get("spawn")
    if not isinstance(spawn, dict):
        if "spawn" in world:
            errors.append("spawn: must be an object with 'position' and 'yaw_deg'")
        return None

    for key in ("position", "yaw_deg"):
        if key not in spawn:
            errors.append(f"spawn: required key '{key}' is missing")
    for key in sorted(set(spawn) - {"position", "yaw_deg"}):
        errors.append(f"spawn: unknown key '{key}' is not part of the format")

    yaw = spawn.get("yaw_deg")
    if "yaw_deg" in spawn:
        if not _is_finite_number(yaw):
            errors.append(f"spawn.yaw_deg: must be a number in degrees, but it is {yaw!r}")
        elif not -360.0 <= float(yaw) <= 360.0:
            errors.append(f"spawn.yaw_deg: must be between -360 and 360, but it is {_fmt(float(yaw))}")

    position = _vec3(spawn.get("position"))
    if position is None and "position" in spawn:
        errors.append("spawn.position: must be three finite numbers [x_east, y_north, z_up]")
    return position


def _check_obstacle_structure(index: int, obstacle: Any, errors: list[str]) -> bool:
    """
    Check one obstacle's keys, types and ranges. Return True when it is sound
    enough for the semantic checks to use.
    障害物 1 個の鍵・型・範囲を検査する。意味検査に回せるなら True。
    """
    where = f"obstacles[{index}]"
    if not isinstance(obstacle, dict):
        errors.append(f"{where}: must be an object")
        return False

    ident = obstacle.get("id")
    if "id" not in obstacle:
        errors.append(f"{where}: required key 'id' is missing")
    elif not (isinstance(ident, str) and _ID_RE.match(ident)):
        errors.append(
            f"{where}.id: must be lower-case letters, digits and underscore (1-64 chars), "
            f"but it is {ident!r}"
        )
    label = f"obstacle '{ident}'" if isinstance(ident, str) and ident else where

    kind = obstacle.get("type")
    if "type" not in obstacle:
        errors.append(f"{where}: required key 'type' is missing")
        return False
    if kind not in OBSTACLE_TYPES:
        errors.append(
            f"{label}: type {kind!r} is not a known obstacle type; "
            f"it must be one of {', '.join(OBSTACLE_TYPES)}"
        )
        return False

    allowed = {"id", "type", "position", "rotation_deg", "size", "color", "thickness", "segments", "flow_quality"}
    for key in ("position", "rotation_deg", "size", "color"):
        if key not in obstacle:
            errors.append(f"{label}: required key '{key}' is missing")
    for key in sorted(set(obstacle) - allowed):
        errors.append(f"{label}: unknown key '{key}' is not part of the format")

    ok = True

    if "position" in obstacle and _vec3(obstacle["position"]) is None:
        errors.append(f"{label}.position: must be three finite numbers [x_east, y_north, z_up]")
        ok = False

    rotation = obstacle.get("rotation_deg")
    if "rotation_deg" in obstacle:
        rotation_vec = _vec3(rotation)
        if rotation_vec is None:
            errors.append(f"{label}.rotation_deg: must be three finite numbers [roll, pitch, yaw] in degrees")
            ok = False
        else:
            for axis, value in zip(("roll", "pitch", "yaw"), rotation_vec):
                if not -360.0 <= value <= 360.0:
                    errors.append(
                        f"{label}.rotation_deg: {axis} must be between -360 and 360, "
                        f"but it is {_fmt(value)}"
                    )

    size = _vec3(obstacle.get("size"))
    if "size" in obstacle:
        if size is None:
            errors.append(f"{label}.size: must be three finite numbers in metres")
            ok = False
        else:
            meaning = SIZE_SEMANTICS[kind]["size"].split(" / ")
            for position, value in enumerate(size):
                what = meaning[position] if position < len(meaning) else f"size[{position}]"
                if value <= 0:
                    errors.append(
                        f"{label}.size[{position}] ({what}): must be greater than 0 m, "
                        f"but it is {_fmt(value)} m"
                    )
                    ok = False
                elif value > 100:
                    errors.append(
                        f"{label}.size[{position}] ({what}): must be at most 100 m, "
                        f"but it is {_fmt(value)} m"
                    )

    color = obstacle.get("color")
    if "color" in obstacle and not (isinstance(color, str) and _COLOR_RE.match(color)):
        errors.append(f"{label}.color: must be '#rrggbb', but it is {color!r}")

    thickness = obstacle.get("thickness")
    if kind in HOLLOW_TYPES:
        if "thickness" not in obstacle:
            errors.append(
                f"{label}: type '{kind}' is hollow, so the required key 'thickness' "
                "(frame or tube wall thickness in metres) is missing"
            )
            ok = False
        elif not _is_finite_number(thickness) or float(thickness) <= 0:
            errors.append(f"{label}.thickness: must be a number greater than 0 m, but it is {thickness!r}")
            ok = False
        elif float(thickness) > 5:
            errors.append(f"{label}.thickness: must be at most 5 m, but it is {_fmt(float(thickness))} m")
    elif "thickness" in obstacle:
        errors.append(
            f"{label}: type '{kind}' is solid, so it must not have 'thickness' "
            f"(only {', '.join(HOLLOW_TYPES)} do)"
        )

    if kind == "ring":
        if "segments" in obstacle:
            segments = obstacle["segments"]
            if not isinstance(segments, int) or isinstance(segments, bool):
                errors.append(f"{label}.segments: must be a whole number, but it is {segments!r}")
            elif not 6 <= segments <= 128:
                errors.append(f"{label}.segments: must be between 6 and 128, but it is {segments}")
        if size is not None and abs(size[0] - size[1]) > 1e-9:
            errors.append(
                f"{label}.size: a ring is round, so size[0] and size[1] (both the inner "
                f"diameter) must be equal, but they are {_fmt(size[0])} m and {_fmt(size[1])} m"
            )
    elif "segments" in obstacle:
        errors.append(f"{label}: only type 'ring' is round, so type '{kind}' must not have 'segments'")

    if "flow_quality" in obstacle:
        quality = obstacle["flow_quality"]
        if not _is_finite_number(quality) or not 0.0 <= float(quality) <= 1.0:
            errors.append(
                f"{label}.flow_quality: must be a number between 0 and 1, but it is {quality!r}"
            )

    return ok


# ---------------------------------------------------------------------------
# Geometry used by the semantic checks.
# 意味検査で使う幾何。
# ---------------------------------------------------------------------------


def _local_bounds(kind: str, size: list[float], thickness: float) -> tuple[list[float], list[float]]:
    """
    Axis-aligned bounds of an obstacle in its own frame, before rotation and
    translation. The origin of that frame is the one documented per type in
    Schemas/README.md §3, which is why each kind gets its own expression.
    回転・移動の前の、障害物自身の座標系での軸平行な範囲。原点は種類ごとに
    README §3 で決めたところなので、種類ごとに式が違う。
    """
    sx, sy, sz = size
    is_bottom_centred = kind in ("box", "pillar", "wall", "step", "pad", "bowling_pin")
    if is_bottom_centred:
        # Origin at the centre of the bottom face. / 原点は底面の中心。
        return [-sx / 2, -sy / 2, 0.0], [sx / 2, sy / 2, sz]
    is_furniture = kind in ("table", "chair", "sofa", "shelf", "bed")
    if is_furniture:
        # Bound the complete furniture; openings are represented by Unity parts.
        # 家具全体の外形。隙間は Unity の部品形状で表す。
        return [-sx / 2, -sy / 2, 0.0], [sx / 2, sy / 2, sz]
    if kind == "ramp":
        # Origin at the centre of the low edge; it rises toward +y.
        # 原点は低い辺の中心。+y に向かって上がる。
        return [-sx / 2, 0.0, 0.0], [sx / 2, sy, sz]
    if kind == "gate":
        # Origin at the centre of the opening's bottom edge, at floor level.
        # The frame surrounds the opening, so it extends by the thickness.
        # 原点は開口の下辺の中心（床の高さ）。枠は開口の周りに肉厚ぶん広がる。
        return (
            [-sx / 2 - thickness, -sy / 2, 0.0],
            [sx / 2 + thickness, sy / 2, sz + thickness],
        )
    if kind == "tunnel":
        # Origin at the centre of the entry opening's bottom edge; it runs +y.
        # 原点は入口の開口の下辺の中心。+y 方向に伸びる。
        return (
            [-sx / 2 - thickness, 0.0, 0.0],
            [sx / 2 + thickness, sy, sz + thickness],
        )
    if kind == "ring":
        # Origin at the centre of the opening; the ring lies in the x-z plane
        # and is `thickness` deep along y.
        # 原点は開口の中心。輪は x-z 平面にあり、y 方向の厚みが thickness。
        outer = sx / 2 + thickness
        return [-outer, -thickness / 2, -outer], [outer, thickness / 2, outer]
    raise AssertionError(f"unhandled obstacle type: {kind}")  # pragma: no cover


def _rotation_matrix(roll_deg: float, pitch_deg: float, yaw_deg: float) -> list[list[float]]:
    """
    Rotation matrix for intrinsic yaw -> pitch -> roll, i.e. R = Rz*Ry*Rx.
    内因性の yaw → pitch → roll の回転行列、すなわち R = Rz*Ry*Rx。
    """
    cr, sr = math.cos(math.radians(roll_deg)), math.sin(math.radians(roll_deg))
    cp, sp = math.cos(math.radians(pitch_deg)), math.sin(math.radians(pitch_deg))
    cy, sy = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    return [
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ]


def _world_aabb(obstacle: dict) -> tuple[list[float], list[float]]:
    """
    World-frame axis-aligned bounding box of an obstacle. The eight corners of
    the local box are rotated and translated, then bounded; this is exact for
    axis-aligned rotations and conservative otherwise, which is what the
    containment and overlap checks want.
    世界系での軸平行な外接直方体。局所の箱の 8 隅を回して動かし、その範囲を取る。
    軸に沿った回転では厳密、それ以外では外側に安全側となり、包含・重なりの検査に適う。
    """
    kind = obstacle["type"]
    size = [float(v) for v in obstacle["size"]]
    thickness = float(obstacle.get("thickness", 0.0))
    low, high = _local_bounds(kind, size, thickness)

    roll, pitch, yaw = (float(v) for v in obstacle["rotation_deg"])
    matrix = _rotation_matrix(roll, pitch, yaw)
    origin = [float(v) for v in obstacle["position"]]

    out_low = [math.inf] * 3
    out_high = [-math.inf] * 3
    for cx in (low[0], high[0]):
        for cy in (low[1], high[1]):
            for cz in (low[2], high[2]):
                corner = (cx, cy, cz)
                for axis in range(3):
                    value = (
                        matrix[axis][0] * corner[0]
                        + matrix[axis][1] * corner[1]
                        + matrix[axis][2] * corner[2]
                        + origin[axis]
                    )
                    out_low[axis] = min(out_low[axis], value)
                    out_high[axis] = max(out_high[axis], value)
    return out_low, out_high


def _box_volume(low: list[float], high: list[float]) -> float:
    """Volume of an axis-aligned box. / 軸平行な箱の体積。"""
    return max(0.0, high[0] - low[0]) * max(0.0, high[1] - low[1]) * max(0.0, high[2] - low[2])


def _intersection(
    a: tuple[list[float], list[float]], b: tuple[list[float], list[float]]
) -> tuple[list[float], list[float]] | None:
    """Overlap of two boxes, or None when they do not overlap. / 2 箱の重なり。"""
    low = [max(a[0][i], b[0][i]) for i in range(3)]
    high = [min(a[1][i], b[1][i]) for i in range(3)]
    if any(high[i] <= low[i] for i in range(3)):
        return None
    return low, high


def _opening_width(obstacle: dict) -> float:
    """
    Narrowest free dimension of a hollow obstacle's opening.
    中空の障害物の開口の、いちばん狭い自由寸法。
    """
    size = [float(v) for v in obstacle["size"]]
    if obstacle["type"] == "ring":
        return size[0]  # inner diameter / 内径
    return min(size[0], size[2])  # width and height of the opening / 開口の幅と高さ


# ---------------------------------------------------------------------------
# Semantic checks.
# 意味検査。
# ---------------------------------------------------------------------------


def _check_duplicate_ids(obstacles: list[dict], errors: list[str]) -> None:
    """Every obstacle id must be unique in the file. / id はファイル内で一意。"""
    seen: dict[str, int] = {}
    for index, obstacle in enumerate(obstacles):
        ident = obstacle.get("id")
        if not isinstance(ident, str):
            continue
        if ident in seen:
            errors.append(
                f"obstacle '{ident}': the id is already used by obstacles[{seen[ident]}], "
                f"but every id must be unique within the file"
            )
        else:
            seen[ident] = index


def _check_inside_room(
    obstacles: list[dict], room_size: list[float], errors: list[str]
) -> None:
    """Every obstacle must lie inside the room. / 障害物は部屋の中に収まること。"""
    limits_low = [-room_size[0] / 2, -room_size[1] / 2, 0.0]
    limits_high = [room_size[0] / 2, room_size[1] / 2, room_size[2]]
    axis_names = ("x (east)", "y (north)", "z (up)")
    tolerance = 1e-6

    for obstacle in obstacles:
        ident = obstacle["id"]
        low, high = _world_aabb(obstacle)
        for axis in range(3):
            if low[axis] < limits_low[axis] - tolerance:
                errors.append(
                    f"obstacle '{ident}': it reaches {_fmt(low[axis])} m along {axis_names[axis]}, "
                    f"which is outside the room (the room starts at {_fmt(limits_low[axis])} m)"
                )
            if high[axis] > limits_high[axis] + tolerance:
                errors.append(
                    f"obstacle '{ident}': it reaches {_fmt(high[axis])} m along {axis_names[axis]}, "
                    f"which is outside the room (the room ends at {_fmt(limits_high[axis])} m)"
                )


def _check_overlap(obstacles: list[dict], warnings: list[str]) -> None:
    """
    Report obstacles that interpenetrate deeply. Touching is legitimate (a pad
    resting on a table), so only a large shared volume is worth mentioning.
    深く食い込んでいる障害物を報告する。接するのは正当（台の上の pad）なので、
    共有体積が大きいときだけ言う。
    """
    boxes = [(obstacle["id"], _world_aabb(obstacle)) for obstacle in obstacles]
    for i in range(len(boxes)):
        id_a, box_a = boxes[i]
        volume_a = _box_volume(*box_a)
        for j in range(i + 1, len(boxes)):
            id_b, box_b = boxes[j]
            shared = _intersection(box_a, box_b)
            if shared is None:
                continue
            volume_b = _box_volume(*box_b)
            shared_volume = _box_volume(*shared)
            smaller = min(volume_a, volume_b)
            if smaller <= 0:
                continue
            fraction = shared_volume / smaller
            if fraction >= OVERLAP_WARN_FRACTION:
                warnings.append(
                    f"obstacles '{id_a}' and '{id_b}': they overlap by "
                    f"{fraction * 100:.0f}% of the smaller one's volume, which usually "
                    f"means one of them is misplaced"
                )


def _check_spawn_placement(
    spawn: list[float],
    yaw_deg: float | None,
    room_size: list[float] | None,
    obstacles: list[dict],
    errors: list[str],
    warnings: list[str],
) -> None:
    """
    The spawn point must be inside the room, clear of every obstacle, and have
    room to climb straight up.
    出発点は部屋の中にあり、どの障害物にも入らず、真上に上がる余地があること。
    """
    half = VEHICLE_WIDTH_M / 2
    vehicle_low = [spawn[0] - half, spawn[1] - half, spawn[2] - VEHICLE_HEIGHT_M / 2]
    vehicle_high = [spawn[0] + half, spawn[1] + half, spawn[2] + VEHICLE_HEIGHT_M / 2]

    if room_size is not None:
        limits_low = [-room_size[0] / 2, -room_size[1] / 2, 0.0]
        limits_high = [room_size[0] / 2, room_size[1] / 2, room_size[2]]
        axis_names = ("x (east)", "y (north)", "z (up)")
        for axis in range(3):
            if vehicle_low[axis] < limits_low[axis] or vehicle_high[axis] > limits_high[axis]:
                errors.append(
                    f"spawn.position: the vehicle at {_fmt(spawn[axis])} m along "
                    f"{axis_names[axis]} does not fit inside the room "
                    f"({_fmt(limits_low[axis])} m to {_fmt(limits_high[axis])} m)"
                )
        # Room to climb, capped by the ceiling height.
        # 上がる余地。天井の高さで頭打ちになる。
        if spawn[2] + TAKEOFF_CLEARANCE_M > room_size[2]:
            errors.append(
                f"spawn.position: there is only {_fmt(room_size[2] - spawn[2])} m between the "
                f"spawn point and the ceiling, but takeoff needs {_fmt(TAKEOFF_CLEARANCE_M)} m "
                "of clear height above it"
            )

    vehicle_box = (vehicle_low, vehicle_high)
    column_low = [spawn[0] - half, spawn[1] - half, spawn[2]]
    column_high = [spawn[0] + half, spawn[1] + half, spawn[2] + TAKEOFF_CLEARANCE_M]
    column_box = (column_low, column_high)

    for obstacle in obstacles:
        ident = obstacle["id"]
        box = _world_aabb(obstacle)
        if _intersection(vehicle_box, box) is not None:
            errors.append(
                f"spawn.position: the spawn point is inside obstacle '{ident}'; "
                "the vehicle would start embedded in it"
            )
            continue
        if _intersection(column_box, box) is not None:
            errors.append(
                f"spawn.position: obstacle '{ident}' blocks the "
                f"{_fmt(TAKEOFF_CLEARANCE_M)} m of clear height that takeoff needs "
                "directly above the spawn point"
            )

    if yaw_deg is not None and spawn[2] < 0:
        warnings.append(
            f"spawn.position: the spawn height is {_fmt(spawn[2])} m, below the floor at 0 m"
        )


def _check_openings(obstacles: list[dict], errors: list[str], warnings: list[str]) -> None:
    """
    An opening the vehicle cannot fit through makes the world unusable.
    機体が通れない開口は、その空間を使えなくする。
    """
    for obstacle in obstacles:
        if obstacle["type"] not in HOLLOW_TYPES:
            continue
        ident = obstacle["id"]
        width = _opening_width(obstacle)
        if width <= VEHICLE_WIDTH_M:
            errors.append(
                f"obstacle '{ident}': its opening is {_fmt(width)} m across, which is narrower "
                f"than the vehicle ({_fmt(VEHICLE_WIDTH_M)} m); nothing can pass through it"
            )
        elif width < VEHICLE_WIDTH_M + OPENING_MARGIN_M:
            warnings.append(
                f"obstacle '{ident}': its opening is {_fmt(width)} m across, leaving only "
                f"{_fmt(width - VEHICLE_WIDTH_M)} m of total clearance around the vehicle; "
                "passing through will be a matter of luck"
            )


# ---------------------------------------------------------------------------
# Public API.
# 公開 API。
# ---------------------------------------------------------------------------


def validate_world(world: Any) -> dict[str, list[str]]:
    """
    Check an already-parsed world document.
    読み込み済みの空間データを検査する。

    Returns {"errors": [...], "warnings": [...]}, each entry one sentence
    naming the field and the reason.
    戻り値は誤りと警告の一覧。各項目は「どの項目が・なぜ」を含む 1 文。
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(world, dict):
        return {"errors": ["root: the file must contain a JSON object"], "warnings": []}

    _check_root_keys(world, errors)
    _check_header(world, errors)
    room_size = _check_room(world, errors)
    _check_floor(world, errors)
    spawn_position = _check_spawn(world, errors)

    raw_obstacles = world.get("obstacles")
    sound: list[dict] = []
    if "obstacles" in world:
        if not isinstance(raw_obstacles, list):
            errors.append("obstacles: must be a list of obstacle objects (it may be empty)")
        else:
            for index, obstacle in enumerate(raw_obstacles):
                if _check_obstacle_structure(index, obstacle, errors):
                    # Only obstacles whose geometry parsed can be reasoned about.
                    # 幾何が読めたものだけを意味検査に回す。
                    if isinstance(obstacle.get("id"), str) and _vec3(obstacle.get("position")) \
                            and _vec3(obstacle.get("rotation_deg")) and _vec3(obstacle.get("size")):
                        sound.append(obstacle)
            _check_duplicate_ids([o for o in raw_obstacles if isinstance(o, dict)], errors)

    if sound:
        if room_size is not None:
            _check_inside_room(sound, room_size, errors)
        _check_overlap(sound, warnings)
        _check_openings(sound, errors, warnings)

    if spawn_position is not None:
        spawn_block = world.get("spawn", {})
        yaw = spawn_block.get("yaw_deg") if isinstance(spawn_block, dict) else None
        yaw_value = float(yaw) if _is_finite_number(yaw) else None
        _check_spawn_placement(spawn_position, yaw_value, room_size, sound, errors, warnings)

    return {"errors": errors, "warnings": warnings}


def validate_file_detailed(path: str | os.PathLike[str]) -> dict[str, list[str]]:
    """
    Read and check one world file, reporting errors and warnings separately.
    空間ファイルを 1 つ読んで検査し、誤りと警告を分けて返す。
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            world = json.load(handle)
    except FileNotFoundError:
        return {"errors": [f"file: '{path}' does not exist"], "warnings": []}
    except UnicodeDecodeError as exc:
        return {"errors": [f"file: '{path}' is not valid UTF-8 ({exc.reason})"], "warnings": []}
    except json.JSONDecodeError as exc:
        return {
            "errors": [f"file: '{path}' is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})"],
            "warnings": [],
        }
    return validate_world(world)


def validate_file(path: str | os.PathLike[str]) -> list[str]:
    """
    Check one world file and return its errors. An empty list means it passes.
    空間ファイルを検査し、誤りの一覧を返す。空なら合格。
    """
    return validate_file_detailed(path)["errors"]


def list_worlds(root: str | os.PathLike[str]) -> list[dict]:
    """
    List the world files under `root`, sorted by name.
    root の下の空間ファイルを名前順に並べて返す。

    Each entry has "name", "path", "description" (the ja/en object, or an empty
    dict when the file has none) and "obstacle_count".
    各項目は name・path・description・obstacle_count を持つ。
    """
    found: list[dict] = []
    for directory, _subdirs, files in os.walk(root):
        for filename in files:
            if not filename.endswith(".world.json"):
                continue
            path = os.path.join(directory, filename)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    world = json.load(handle)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                world = {}
            if not isinstance(world, dict):
                world = {}
            name = world.get("name")
            obstacles = world.get("obstacles")
            found.append(
                {
                    "name": name if isinstance(name, str) else filename[: -len(".world.json")],
                    "path": path,
                    "description": world.get("description") if isinstance(world.get("description"), dict) else {},
                    "obstacle_count": len(obstacles) if isinstance(obstacles, list) else 0,
                }
            )
    found.sort(key=lambda entry: entry["name"])
    return found


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point. / コマンドラインの入口。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        print("\nusage: python3 tools/unity_world/validate.py <file.world.json>...")
        return 0 if args else 2

    failed = False
    for path in args:
        result = validate_file_detailed(path)
        for warning in result["warnings"]:
            print(f"WARN  {path}: {warning}")
        for error in result["errors"]:
            print(f"ERROR {path}: {error}")
        if result["errors"]:
            failed = True
        else:
            print(f"OK    {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
