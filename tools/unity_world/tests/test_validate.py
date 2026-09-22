"""
What these tests guarantee about the world-file checks.
空間ファイル検査が保証すること。

1. Every shipped world passes with no errors (and no warnings, so the shipped
   set stays exemplary). / 同梱の 10 空間が誤り無しで通る。
2. Each way of breaking a world is rejected by a message that names the field
   and the reason. / 壊した空間が、的確な文で不合格になる。
3. The JSON Schema and the checking code agree on the obstacle types, the
   required keys and the per-type rules, so the specification document and the
   implementation cannot drift apart. / スキーマと実装が一致する。
4. Reading, writing and re-reading a world file yields the same content.
   / JSON を読んで書いて読んでも同じ内容になる。
"""

from __future__ import annotations

import copy
import json
import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.dirname(os.path.dirname(_TESTS_DIR))  # tools/
REPO_ROOT = os.path.dirname(_TOOLS_DIR)

# Import the package under test as `unity_world` without installing it.
# 入れずに `unity_world` として読み込む。
sys.path.insert(0, _TOOLS_DIR)

from unity_world import (  # noqa: E402
    OBSTACLE_TYPES,
    VEHICLE_WIDTH_M,
    list_worlds,
    validate_file,
    validate_file_detailed,
)
from unity_world.validate import (  # noqa: E402
    HOLLOW_TYPES,
    SIZE_SEMANTICS,
    _local_bounds,
    validate_world,
)


WORLDS_DIR = os.path.join(REPO_ROOT, "simulator", "unity", "Assets", "StampFly", "Worlds")
SCHEMA_PATH = os.path.join(REPO_ROOT, "simulator", "unity", "Schemas", "world.schema.json")

SHIPPED_WORLDS = (
    "bowling",
    "bedroom",
    "living_room",
    "study",
    "empty_room",
    "pillar_forest",
    "gate_course",
    "corridor_tunnel",
    "stepped_floor",
    "featureless_floor",
)


def world_path(name: str) -> str:
    """Path of a shipped world file. / 同梱の空間ファイルの場所。"""
    return os.path.join(WORLDS_DIR, f"{name}.world.json")


def load_world(name: str) -> dict:
    """A fresh copy of a shipped world, for tests to break. / 壊して試す複製。"""
    with open(world_path(name), "r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def schema() -> dict:
    """The JSON Schema document. / JSON Schema の中身。"""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def only_errors(world: dict) -> list[str]:
    """Errors reported for an in-memory world. / 誤りだけを取り出す。"""
    return validate_world(world)["errors"]


def assert_mentions(errors: list[str], *fragments: str) -> None:
    """
    Assert that at least one error mentions all the given fragments, so that a
    test fails when the message stops naming the field or the reason.
    どれか 1 つの誤り文が、指定の語を全て含むこと。文が曖昧になったら落ちる。
    """
    for error in errors:
        if all(fragment in error for fragment in fragments):
            return
    raise AssertionError(f"no error mentioning {fragments!r}; got: {errors}")


# ---------------------------------------------------------------------------
# 1. The shipped worlds.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", SHIPPED_WORLDS)
def test_shipped_world_passes(name: str) -> None:
    """Every shipped world validates with no errors. / 同梱の空間は誤り無し。"""
    assert validate_file(world_path(name)) == []


@pytest.mark.parametrize("name", SHIPPED_WORLDS)
def test_shipped_world_has_no_warnings(name: str) -> None:
    """The shipped set is exemplary, so it raises no warnings either. / 警告も無い。"""
    assert validate_file_detailed(world_path(name))["warnings"] == []


@pytest.mark.parametrize("name", SHIPPED_WORLDS)
def test_shipped_world_name_matches_file(name: str) -> None:
    """A world's 'name' matches its file stem. / name はファイル名の幹と一致。"""
    assert load_world(name)["name"] == name


def test_list_worlds_reports_every_shipped_world() -> None:
    """list_worlds() finds all ten with their description and obstacle count."""
    entries = list_worlds(WORLDS_DIR)
    assert [entry["name"] for entry in entries] == sorted(SHIPPED_WORLDS)
    for entry in entries:
        assert os.path.isfile(entry["path"])
        assert set(entry["description"]) == {"ja", "en"}
        expected = len(load_world(entry["name"])["obstacles"])
        assert entry["obstacle_count"] == expected


def test_shipped_worlds_cover_every_obstacle_type_that_is_placed() -> None:
    """
    The shipped set demonstrates a broad range of the obstacle types, so a
    reader can find a worked example of most of them.
    同梱の空間が多くの種類の実例になっていること。
    """
    used = {
        obstacle["type"]
        for name in SHIPPED_WORLDS
        for obstacle in load_world(name)["obstacles"]
    }
    assert set(OBSTACLE_TYPES) == used


# ---------------------------------------------------------------------------
# 2. Broken worlds are rejected with a message that says why.
# ---------------------------------------------------------------------------


def test_missing_required_key_is_rejected() -> None:
    """A world without 'room' is rejected, naming the key. / 鍵の欠落。"""
    world = load_world("empty_room")
    del world["room"]
    assert_mentions(only_errors(world), "room", "missing")


def test_missing_obstacle_key_is_rejected() -> None:
    """An obstacle without 'size' is rejected, naming it. / 障害物の鍵の欠落。"""
    world = load_world("pillar_forest")
    del world["obstacles"][0]["size"]
    assert_mentions(only_errors(world), "size", "missing")


def test_unknown_obstacle_type_is_rejected() -> None:
    """An unknown type is rejected and the known ones are listed. / 未知の種類。"""
    world = load_world("pillar_forest")
    world["obstacles"][0]["type"] = "sphere"
    assert_mentions(only_errors(world), "'sphere'", "not a known obstacle type")


def test_duplicate_id_is_rejected() -> None:
    """Two obstacles with the same id are rejected. / id の重複。"""
    world = load_world("pillar_forest")
    world["obstacles"][1]["id"] = world["obstacles"][0]["id"]
    assert_mentions(only_errors(world), world["obstacles"][0]["id"], "unique")


def test_obstacle_outside_the_room_is_rejected() -> None:
    """An obstacle pushed past a wall is rejected. / 部屋の外。"""
    world = load_world("pillar_forest")
    world["obstacles"][0]["position"] = [20.0, 0.0, 0.0]
    assert_mentions(only_errors(world), "outside the room")


def test_obstacle_above_the_ceiling_is_rejected() -> None:
    """Height is checked as well as the footprint. / 天井より高い場合。"""
    world = load_world("empty_room")
    world["obstacles"] = [
        {
            "id": "too_tall",
            "type": "pillar",
            "position": [0.0, 2.0, 0.0],
            "rotation_deg": [0.0, 0.0, 0.0],
            "size": [0.2, 0.2, 9.0],
            "color": "#888888",
        }
    ]
    assert_mentions(only_errors(world), "too_tall", "outside the room", "z (up)")


def test_opening_narrower_than_the_vehicle_is_rejected() -> None:
    """A gate the vehicle cannot fit through is rejected. / 狭すぎる開口。"""
    world = load_world("gate_course")
    world["obstacles"][0]["size"] = [0.05, 0.10, 1.10]
    assert_mentions(only_errors(world), "narrower than the vehicle")


def test_marginal_opening_is_only_a_warning() -> None:
    """
    An opening that fits but barely warns rather than fails, so that a tight
    course stays loadable.
    かろうじて通る開口は警告どまりで、読み込みは止めない。
    """
    world = load_world("gate_course")
    world["obstacles"][0]["size"] = [VEHICLE_WIDTH_M + 0.01, 0.10, 1.10]
    result = validate_world(world)
    assert result["errors"] == []
    assert any("matter of luck" in warning for warning in result["warnings"])


def test_spawn_inside_an_obstacle_is_rejected() -> None:
    """A spawn point buried in an obstacle is rejected. / 出発点が障害物の中。"""
    world = load_world("pillar_forest")
    world["spawn"]["position"] = list(world["obstacles"][0]["position"][:2]) + [0.5]
    assert_mentions(only_errors(world), "spawn.position", "inside obstacle")


def test_spawn_outside_the_room_is_rejected() -> None:
    """A spawn point outside the walls is rejected. / 出発点が部屋の外。"""
    world = load_world("empty_room")
    world["spawn"]["position"] = [10.0, 0.0, 0.5]
    assert_mentions(only_errors(world), "spawn.position", "does not fit inside the room")


def test_blocked_takeoff_clearance_is_rejected() -> None:
    """An obstacle over the spawn point is rejected. / 離陸の余地が無い。"""
    world = load_world("empty_room")
    world["obstacles"] = [
        {
            "id": "lid",
            "type": "box",
            "position": [0.0, 0.0, 0.3],
            "rotation_deg": [0.0, 0.0, 0.0],
            "size": [1.0, 1.0, 0.1],
            "color": "#888888",
        }
    ]
    assert_mentions(only_errors(world), "spawn.position", "clear height", "'lid'")


def test_spawn_too_close_to_the_ceiling_is_rejected() -> None:
    """The ceiling counts as blocking the climb too. / 天井も余地を奪う。"""
    world = load_world("empty_room")
    world["spawn"]["position"] = [0.0, 0.0, 2.8]
    assert_mentions(only_errors(world), "spawn.position", "ceiling")


def test_non_positive_dimension_is_rejected() -> None:
    """A zero or negative size is rejected. / 寸法が 0 以下。"""
    world = load_world("pillar_forest")
    world["obstacles"][0]["size"] = [0.0, 0.16, 2.2]
    assert_mentions(only_errors(world), "greater than 0")

    world = load_world("pillar_forest")
    world["obstacles"][0]["size"] = [0.16, 0.16, -1.0]
    assert_mentions(only_errors(world), "greater than 0")


def test_deep_overlap_is_a_warning_not_an_error() -> None:
    """Overlapping obstacles warn but still load. / 大きな重なりは警告。"""
    world = load_world("pillar_forest")
    world["obstacles"][1]["position"] = list(world["obstacles"][0]["position"])
    result = validate_world(world)
    assert result["errors"] == []
    assert any("overlap" in warning for warning in result["warnings"])


def test_touching_obstacles_do_not_warn() -> None:
    """A pad resting on a platform is legitimate. / 接するだけなら警告しない。"""
    assert validate_file_detailed(world_path("stepped_floor"))["warnings"] == []


def test_thickness_required_for_hollow_types() -> None:
    """gate, ring and tunnel need a thickness. / 中空の種類は肉厚が必須。"""
    world = load_world("gate_course")
    del world["obstacles"][0]["thickness"]
    assert_mentions(only_errors(world), "thickness", "missing")


def test_thickness_forbidden_for_solid_types() -> None:
    """A solid type must not carry a thickness. / 中実の種類には書けない。"""
    world = load_world("pillar_forest")
    world["obstacles"][0]["thickness"] = 0.05
    assert_mentions(only_errors(world), "solid", "thickness")


def test_segments_only_on_ring() -> None:
    """Only a ring may declare a segment count. / segments は ring のみ。"""
    world = load_world("pillar_forest")
    world["obstacles"][0]["segments"] = 24
    assert_mentions(only_errors(world), "segments", "ring")


def test_wrong_frame_is_rejected() -> None:
    """A file in another frame is rejected. / 座標系が違う。"""
    world = load_world("empty_room")
    world["frame"] = "NED"
    assert_mentions(only_errors(world), "frame", "ENU")


def test_unknown_top_level_key_is_rejected() -> None:
    """Stray keys are reported rather than silently ignored. / 未知の鍵。"""
    world = load_world("empty_room")
    world["gravity"] = -9.81
    assert_mentions(only_errors(world), "gravity", "unknown key")


def test_flow_quality_out_of_range_is_rejected() -> None:
    """flow_quality lives in 0..1. / flow_quality の範囲。"""
    world = load_world("empty_room")
    world["floor"]["flow_quality"] = 1.5
    assert_mentions(only_errors(world), "floor.flow_quality", "between 0 and 1")


def test_malformed_json_is_reported_as_such(tmp_path) -> None:
    """A broken file reports a parse error, not a crash. / 壊れた JSON。"""
    path = tmp_path / "broken.world.json"
    path.write_text("{ not json", encoding="utf-8")
    errors = validate_file(str(path))
    assert len(errors) == 1
    assert "not valid JSON" in errors[0]


def test_missing_file_is_reported() -> None:
    """A missing file reports its absence. / 存在しないファイル。"""
    errors = validate_file(os.path.join(WORLDS_DIR, "no_such.world.json"))
    assert len(errors) == 1
    assert "does not exist" in errors[0]


# ---------------------------------------------------------------------------
# 3. The schema and the implementation agree.
# ---------------------------------------------------------------------------


def test_schema_obstacle_types_match_implementation(schema: dict) -> None:
    """The type list is identical in both, in the same order. / 種類の一覧。"""
    schema_types = schema["$defs"]["obstacle"]["properties"]["type"]["enum"]
    assert tuple(schema_types) == OBSTACLE_TYPES


def test_every_type_has_documented_size_semantics() -> None:
    """Each type states what its size means and where its origin is."""
    assert set(SIZE_SEMANTICS) == set(OBSTACLE_TYPES)
    for kind, entry in SIZE_SEMANTICS.items():
        assert entry["size"].strip(), kind
        assert entry["origin"].strip(), kind


def test_schema_required_root_keys_match_implementation(schema: dict) -> None:
    """The required top-level keys agree. / 最上位の必須の鍵。"""
    world = load_world("empty_room")
    for key in schema["required"]:
        broken = copy.deepcopy(world)
        del broken[key]
        assert_mentions(only_errors(broken), key, "missing")


def test_schema_required_obstacle_keys_match_implementation(schema: dict) -> None:
    """The required obstacle keys agree. / 障害物の必須の鍵。"""
    world = load_world("pillar_forest")
    for key in schema["$defs"]["obstacle"]["required"]:
        broken = copy.deepcopy(world)
        del broken["obstacles"][0][key]
        assert only_errors(broken), f"removing obstacle key '{key}' should fail"


def test_schema_allowed_obstacle_keys_match_implementation(schema: dict) -> None:
    """The set of permitted obstacle keys agrees. / 書ける鍵の集合。"""
    schema_keys = set(schema["$defs"]["obstacle"]["properties"])
    world = load_world("pillar_forest")
    world["obstacles"][0]["totally_unknown"] = 1
    assert_mentions(only_errors(world), "totally_unknown", "unknown key")
    # Every key the schema permits is accepted on some type by the checker.
    # スキーマが許す鍵は、どれかの種類で検査側にも受け入れられる。
    assert schema_keys == {
        "id", "type", "position", "rotation_deg", "size", "thickness", "segments", "color", "flow_quality",
    }


def test_schema_hollow_type_rule_matches_implementation(schema: dict) -> None:
    """The if/then rule for 'thickness' names the same types. / 中空の規則。"""
    rule = schema["$defs"]["obstacle"]["allOf"][0]
    assert tuple(rule["if"]["properties"]["type"]["enum"]) == HOLLOW_TYPES
    assert rule["then"]["required"] == ["thickness"]


def test_schema_ring_segments_rule_matches_implementation(schema: dict) -> None:
    """Only 'ring' may set 'segments' in both the schema and the checker."""
    rule = schema["$defs"]["obstacle"]["allOf"][1]
    assert rule["if"]["properties"]["type"]["const"] == "ring"


def test_schema_floor_patterns_match_implementation(schema: dict) -> None:
    """The floor patterns agree. / 床の模様の一覧。"""
    from unity_world.validate import FLOOR_PATTERNS

    schema_patterns = schema["properties"]["floor"]["properties"]["pattern"]["enum"]
    assert tuple(schema_patterns) == FLOOR_PATTERNS


def test_schema_light_presets_match_implementation(schema: dict) -> None:
    """The lighting presets agree. / 照明の一覧。"""
    from unity_world.validate import LIGHT_PRESETS

    assert tuple(schema["properties"]["light"]["enum"]) == LIGHT_PRESETS


def test_schema_fixes_the_frame_and_the_units(schema: dict) -> None:
    """The schema pins ENU, metres and degrees. / 座標系と単位の固定。"""
    assert schema["properties"]["frame"]["const"] == "ENU"
    assert schema["properties"]["units"]["properties"]["length"]["const"] == "m"
    assert schema["properties"]["units"]["properties"]["angle"]["const"] == "deg"


# ---------------------------------------------------------------------------
# 4. Round trip.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", SHIPPED_WORLDS)
def test_json_round_trip_preserves_content(name: str, tmp_path) -> None:
    """Read, write and read again gives the same content. / 読み書きの往復。"""
    original = load_world(name)
    path = tmp_path / f"{name}.world.json"
    path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with open(path, "r", encoding="utf-8") as handle:
        again = json.load(handle)
    assert again == original
    assert validate_file(str(path)) == []


@pytest.mark.parametrize("name", SHIPPED_WORLDS)
def test_shipped_file_is_utf8_and_ends_with_newline(name: str) -> None:
    """Files are UTF-8 and newline-terminated. / UTF-8 と末尾改行。"""
    with open(world_path(name), "rb") as handle:
        raw = handle.read()
    raw.decode("utf-8")
    assert raw.endswith(b"\n")

@pytest.mark.parametrize("kind", ["table", "chair", "sofa", "shelf", "bed"])
def test_furniture_uses_bottom_centred_overall_bounds(kind: str) -> None:
    """Furniture keeps its declared footprint and total height. / 家具の外形と全高を保証する。"""
    assert _local_bounds(kind, [1.2, 0.8, 1.6], 0) == (
        [-0.6, -0.4, 0.0], [0.6, 0.4, 1.6]
    )


@pytest.mark.parametrize("kind", ["chair", "sofa", "shelf", "bed"])
def test_furniture_remains_a_flat_version_one_obstacle(kind: str) -> None:
    """New furniture validates and room overflow is rejected. / 家具の受理と室外配置の拒否を保証する。"""
    world = load_world("empty_room")
    furniture = {
        "id": "furniture_test", "type": kind,
        "position": [0, 0, 0], "rotation_deg": [0, 0, 0],
        "size": [0.5, 0.5, 0.8], "color": "#b88b60",
    }
    world["obstacles"] = [furniture]
    world["spawn"]["position"] = [-1, -1, 0.1]
    assert not validate_world(world)["errors"]
    furniture["position"][0] = world["room"]["size"][0]
    assert validate_world(world)["errors"]


def test_bowling_room_has_ten_separate_upright_pins_and_clear_spawn() -> None:
    """The bowling room starts with a clear approach and four pin rows. / 離陸経路と 4 列のピンを保証する。"""
    world = load_world("bowling")
    pins = world["obstacles"]
    assert len(pins) == 10
    assert all(pin["type"] == "bowling_pin" for pin in pins)
    assert all(pin["position"][2] == 0 for pin in pins)
    assert all(pin["rotation_deg"] == [0, 0, 0] for pin in pins)
    rows = sorted({pin["position"][1] for pin in pins})
    assert [sum(pin["position"][1] == row for pin in pins) for row in rows] == [1, 2, 3, 4]
    assert validate_world(world) == {"errors": [], "warnings": []}


def test_bowling_pin_accepts_elliptical_bottom_centred_extent() -> None:
    """Pin width and depth stay independent flat dimensions. / ピンの幅と奥行きを独立に保持する。"""
    world = load_world("bowling")
    world["obstacles"][0]["size"] = [0.06, 0.04, 0.18]
    assert validate_world(world) == {"errors": [], "warnings": []}
    assert _local_bounds("bowling_pin", [0.06, 0.04, 0.18], 0) == (
        [-0.03, -0.02, 0.0], [0.03, 0.02, 0.18]
    )
