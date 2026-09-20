"""
unity_world — world file (*.world.json) checks for the Unity simulator.
unity_world — Unity 版シミュレータの空間ファイル（*.world.json）検査。

The world file format is specified in simulator/unity/Schemas/world.schema.json
and explained in simulator/unity/Schemas/README.md. This package implements the
checks with the Python standard library only (no jsonschema dependency), so it
runs anywhere the sf CLI runs.

空間ファイルの形式は simulator/unity/Schemas/world.schema.json が仕様、
simulator/unity/Schemas/README.md が解説である。本パッケージは Python 標準
ライブラリだけで検査を実装する（jsonschema に依存しない）ので、sf CLI が動く
環境ならどこでも動く。

Public API used by `sf unity world validate`:
`sf unity world validate` から呼ばれる約束の形:

    validate_file(path) -> list[str]            # errors only; empty == pass
    validate_file_detailed(path) -> dict        # {"errors": [...], "warnings": [...]}
    list_worlds(root) -> list[dict]             # name / path / description / obstacle_count
"""

from .validate import (  # noqa: F401
    OBSTACLE_TYPES,
    VEHICLE_WIDTH_M,
    TAKEOFF_CLEARANCE_M,
    list_worlds,
    validate_file,
    validate_file_detailed,
)

__all__ = [
    "OBSTACLE_TYPES",
    "VEHICLE_WIDTH_M",
    "TAKEOFF_CLEARANCE_M",
    "list_worlds",
    "validate_file",
    "validate_file_detailed",
]
