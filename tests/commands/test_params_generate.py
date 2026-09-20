"""
What `sf params generate` guarantees for the Unity simulator's C# constants

The Unity simulator's physical parameters used to be hand-copied numbers in
`simulator/unity/Assets/StampFly/Runtime/Sim/VehicleBody.cs`. They are now
generated into `GeneratedParams.cs` from
`control/models/stampfly_physical.yaml`, the same file the SILS C++ plant and
the MuJoCo model draw from, so the two simulators cannot drift apart.

`sf params check` already compares the numbers once they are on disk. What it
cannot see is the step before that: whether the generator PUT the right number
there in the first place, and whether its C#-specific choices (the FLU-to-Unity
axis mapping, half extents doubled into full extents, the literal syntax C#
accepts) are right. That is what this module guarantees.

Unity 版シミュレータの物理パラメータは、かつて
`simulator/unity/Assets/StampFly/Runtime/Sim/VehicleBody.cs` の中の手で書き
写した数値だった。現在は `control/models/stampfly_physical.yaml` から
`GeneratedParams.cs` へ生成する。同ファイルからは SILS の C++ プラントと
MuJoCo モデルも値を引くので、2 つのシミュレータが離れていくことがない。

ディスクに置かれた後の数値の突き合わせは `sf params check` が既に行う。その
手前 ― 生成器がそもそも正しい数値を置いたのか、C# 固有の判断（FLU から Unity
への軸の割り当て、半長を倍にして全長にすること、C# が受け取るリテラルの書き方）
が正しいのか ― は見えない。本モジュールが保証するのはそこである。

Guaranteed here / ここで保証すること:
  (a) every constant the Unity code reads is emitted, and each carries the
      value the source YAML holds
  (b) the inertia is mapped onto Unity's axes as Ixx->z, Iyy->x, Izz->y
  (c) the collision box is emitted as a FULL extent, twice the YAML's half
      extent, with the same axis swap
  (d) the motor coefficients are NOT emitted (the C++ plant owns the motors)
  (e) the file says it is generated and names how to regenerate it
  (f) every literal is written in a form C# accepts
  (g) `--check` fails once the generated file is edited by hand, and the
      checkout is currently up to date
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

# The generator lives in tools/; tests run against the checkout, not an install.
# 生成器は tools/ にある。試験は導入物ではなくチェックアウトを対象にする。
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

yaml = pytest.importorskip("yaml", reason="the generator needs PyYAML")

from params_audit import generate  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / 前準備
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def spec():
    """The source YAML, parsed. / 元の YAML をパースしたもの。"""
    return generate.load_spec(REPO_ROOT)


@pytest.fixture(scope="module")
def constants(spec):
    """`constants:` as a plain name -> value mapping. / `constants:` を名前→値の対応にしたもの。"""
    return {name: float(entry["value"]) for name, entry in spec["constants"].items()}


@pytest.fixture(scope="module")
def rendered(spec):
    """The C# the generator produces right now. / 生成器が今出す C#。"""
    view = generate.build_view(spec)
    return generate.render_csharp(view)


def constant_value(text: str, name: str) -> float:
    """Read one `public const float <name> = <literal>f;` out of the C#.
    C# から `public const float <名前> = <リテラル>f;` を 1 つ読み取る。

    Anchors on the NAME, never on the value, so the assertion can actually
    observe a wrong number (the rule tools/params_audit/params_manifest.py
    states for its own regexes).
    値ではなく「名前」にアンカーするので、誤った数値を実際に観測できる
    （tools/params_audit/params_manifest.py が自身の正規表現に課す規則）。
    """
    match = re.search(rf"\b{name}\s*=\s*([0-9.eE+-]+)f;", text)
    assert match is not None, f"{name} is missing from the generated C#"
    return float(match.group(1))


# ---------------------------------------------------------------------------
# (a) Every quantity the Unity code reads is emitted, with the YAML's value
# (a) Unity 側が読む量が全て、YAML の値で出ること
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("csharp_name,yaml_key", [
    ("MassKilograms", "mass"),
    ("InertiaForwardAxis", "Ixx"),
    ("InertiaLeftAxis", "Iyy"),
    ("InertiaUpAxis", "Izz"),
    ("RotorOffsetMeters", "arm_offset"),
    ("RotorHeightMeters", "rotor_height"),
    ("PropellerRadiusMeters", "propeller_radius"),
    ("GravityMetersPerSecondSquared", "gravity"),
])
def test_scalar_matches_the_source_yaml(rendered, constants, csharp_name, yaml_key):
    """Each scalar carries exactly the value the SSOT holds.
    各スカラが SSOT の値そのものを持つこと。"""
    assert constant_value(rendered, csharp_name) == pytest.approx(
        constants[yaml_key], rel=1e-12)


# ---------------------------------------------------------------------------
# (b) The inertia reaches Unity's axes / (b) 慣性が Unity の軸へ移ること
# ---------------------------------------------------------------------------

def test_inertia_vector_maps_flu_onto_unity_axes(rendered):
    """The assembled Vector3 is (Iyy, Izz, Ixx): the body's left axis becomes
    Unity x, its up axis becomes Unity y, its forward axis becomes Unity z.
    組み立てられた Vector3 は (Iyy, Izz, Ixx)。機体の左右軸が Unity の x、
    上軸が y、前軸が z になる。"""
    match = re.search(
        r"InertiaTensorUnityAxes\s*=>\s*\n?\s*new Vector3\(\s*"
        r"(\w+),\s*(\w+),\s*(\w+)\s*\)", rendered)
    assert match is not None, "the inertia Vector3 is missing"
    assert match.groups() == (
        "InertiaLeftAxis", "InertiaUpAxis", "InertiaForwardAxis")


# ---------------------------------------------------------------------------
# (c) The collision box is doubled and swapped
# (c) 衝突箱が倍にされ、軸が入れ替わること
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("csharp_name,yaml_key", [
    ("BoxSizeRight", "collision_half_y"),    # Unity x <- FLU y (left)
    ("BoxSizeUp", "collision_half_z"),       # Unity y <- FLU z (up)
    ("BoxSizeForward", "collision_half_x"),  # Unity z <- FLU x (forward)
])
def test_box_is_a_full_extent_of_the_yaml_half_extent(
        rendered, constants, csharp_name, yaml_key):
    """Unity's BoxCollider.size is a full extent while MuJoCo's <geom size> is
    a half extent, so the generated number is twice the YAML's — and it is the
    half extent of the FLU axis Unity's axis corresponds to.
    Unity の BoxCollider.size は全長、MuJoCo の <geom size> は半長なので、
    生成される数値は YAML の 2 倍になる。しかも、その Unity の軸に対応する
    FLU の軸の半長である。"""
    assert constant_value(rendered, csharp_name) == pytest.approx(
        2.0 * constants[yaml_key], rel=1e-12)


def test_resting_height_is_half_the_box_thickness(rendered):
    """A body on the floor rests with its centre half a thickness up. The C#
    derives it from BoxSizeUp rather than carrying a fourth copy of 0.0103.
    床に載った機体の中心は、厚みの半分だけ上に在る。C# は 0.0103 の 4 つ目の
    コピーを持つのではなく BoxSizeUp から導く。"""
    assert "RestingCentreHeightMeters = 0.5f * BoxSizeUp;" in rendered


# ---------------------------------------------------------------------------
# (d) The motor coefficients stay on the C++ side
# (d) モータの係数は C++ 側に留まること
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("symbol", ["CT", "CQ", "JMP", "DM", "QF", "RM", "KM", "KAPPA"])
def test_motor_coefficients_are_not_emitted(rendered, symbol):
    """Unity receives the rotor forces from the firmware's C++ plant and never
    computes them, so a motor coefficient here would be a copy nothing reads.
    Unity はロータの力をファームの C++ プラントから受け取るだけで自分では
    計算しないので、ここにモータの係数が在れば誰も読まないコピーになる。"""
    assert not re.search(rf"\b{symbol}\b", rendered), (
        f"{symbol} leaked into the Unity constants")


# ---------------------------------------------------------------------------
# (e) / (f) The file declares itself, and C# can read every literal
# (e) / (f) ファイルが自身を名乗り、C# が全リテラルを読めること
# ---------------------------------------------------------------------------

def test_header_says_generated_and_names_the_source(rendered):
    """A reader who opens the file must learn, in both languages, that editing
    it is pointless and what to edit instead.
    ファイルを開いた人が、編集しても無駄であることと、代わりに何を編集する
    のかを、両方の言語で知れること。"""
    assert "AUTO-GENERATED -- DO NOT EDIT." in rendered
    assert "自動生成 -- 編集しないこと。" in rendered
    assert generate.SSOT_REL in rendered
    assert "sf params generate" in rendered


def test_every_literal_is_valid_csharp(rendered):
    """C# needs the `f` suffix on a float literal and rejects a `+` in an
    exponent written without digits; repr()'s "9.16e-06" is rewritten to the
    "9.16e-6" form the surrounding hand-written code uses.
    C# は float リテラルに `f` サフィックスを要る。repr() の "9.16e-06" は、
    周りの手書きのコードが使う "9.16e-6" の形へ直される。"""
    literals = re.findall(r"=\s*([0-9][0-9.eE+-]*)f;", rendered)
    assert literals, "no float literals were emitted at all"
    for literal in literals:
        assert re.fullmatch(r"[0-9]+(\.[0-9]+)?(e-?[0-9]+)?", literal), (
            f"{literal!r} is not a form C# source normally carries")


def test_the_class_is_where_the_unity_code_looks_for_it(rendered):
    """`StampFly.Sim.GeneratedParams` is the name every call site uses.
    呼び出し側が使う名前は `StampFly.Sim.GeneratedParams` である。"""
    assert "namespace StampFly.Sim" in rendered
    assert "public static class GeneratedParams" in rendered


# ---------------------------------------------------------------------------
# (g) --check notices a hand edit / (g) --check が手編集に気づくこと
# ---------------------------------------------------------------------------

def _run_check() -> subprocess.CompletedProcess:
    """Run `sf params generate --check` against this checkout.
    このチェックアウトに対して `sf params generate --check` を走らせる。"""
    return subprocess.run(
        [sys.executable, "-m", "sfcli", "params", "generate", "--check"],
        cwd=REPO_ROOT, capture_output=True, text=True,
        env={"PYTHONPATH": f"{REPO_ROOT / 'lib'}:{REPO_ROOT / 'tools'}",
             "PATH": "/usr/bin:/bin"},
    )


def test_checkout_is_up_to_date():
    """The committed C# is what the generator produces from the committed YAML.
    コミット済みの C# が、コミット済みの YAML から生成器が出す物と一致すること。"""
    assert _run_check().returncode == 0


def test_check_fails_when_the_generated_file_is_edited_by_hand():
    """Editing a generated number must be caught, not silently kept: that is
    the whole reason the file is generated. The edit is undone afterwards
    whether or not the assertion holds.
    生成された数値の手編集は、黙って残されるのではなく捕まえられること。
    ファイルを生成にしてあるのはそのためである。編集は、判定の成否に関わらず
    後で元へ戻す。"""
    path = REPO_ROOT / generate.GEN_CS_REL
    original = path.read_text(encoding="utf-8")
    damaged = original.replace(
        "MassKilograms = 0.037f;", "MassKilograms = 0.099f;")
    assert damaged != original, "the mass literal moved; update this test"

    try:
        path.write_text(damaged, encoding="utf-8")
        result = _run_check()
        assert result.returncode == 1
        assert generate.GEN_CS_REL in result.stdout
    finally:
        path.write_text(original, encoding="utf-8")
