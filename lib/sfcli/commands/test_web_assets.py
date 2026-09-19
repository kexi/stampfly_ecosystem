"""
What the shared browser assets guarantee after the 3D view was extracted
from `telemetry_web.html` into `stampfly3d.js` (2026-09-19).

`telemetry_web.html` から 3D 表示を `stampfly3d.js` へ切り出した後
（2026-09-19）、共有資材が保証すること。

The point of these tests is the regression risk of that extraction: both
pages must still get the same scene, and `sf telemetry --web` must behave
exactly as it did before.

これらの試験の眼目は、その切り出しによる既存動作の破壊である。両ページが
同じシーンを受け取り続けること、そして `sf telemetry --web` が従来どおりに
振る舞うことを確かめる。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sfcli.commands import web_assets                      # noqa: E402
from sfcli.commands import pilot_web, telemetry_web        # noqa: E402


class _FakeHandler:
    """Records what a request handler was told to write.
    要求処理側が何を書くよう言われたかを記録する。"""

    def __init__(self):
        self.status = None
        self.headers: dict = {}
        self.body = b""
        self.error = None
        self.wfile = self

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        pass

    def write(self, data):
        self.body += data

    def send_error(self, code, message=None):
        self.error = code


# =============================================================================
# Both pages mount the same scene / 両ページが同じシーンを載せる
# =============================================================================

def test_both_pages_import_the_same_shared_scene_module():
    """Neither page carries its own copy of the 3D scene.

    A copy would drift, and the first thing to drift would be the
    quaternion conversion whose 90-degree body-yaw bug took a numeric
    comparison against the SILS GUI to find.

    どちらのページも 3D シーンの複製を持たないこと。

    複製は食い違っていく。最初に食い違うのはクォータニオン変換であり、その
    機体ヨー 90 度のずれの発見には SILS GUI との数値比較を要した。
    """
    telemetry_html = (web_assets.ASSET_DIR / "telemetry_web.html").read_text(encoding="utf-8")
    pilot_html = (web_assets.ASSET_DIR / "pilot_web.html").read_text(encoding="utf-8")

    for html in (telemetry_html, pilot_html):
        assert "/asset/stampfly3d.js" in html

    # The scene's own code lives in exactly one file.
    # シーン本体のコードが置かれているのは 1 ファイルだけである。
    module = (web_assets.ASSET_DIR / "stampfly3d.js").read_text(encoding="utf-8")
    assert "quatFromNedEuler" in module
    for html in (telemetry_html, pilot_html):
        assert "function quatFromNedEuler" not in html
        assert "new THREE.WebGLRenderer" not in html


def test_the_shared_module_is_served_to_the_browser():
    """`/asset/stampfly3d.js` resolves to the module, as JavaScript.
    `/asset/stampfly3d.js` がモジュールを JavaScript として返すこと。"""
    handler = _FakeHandler()

    served = web_assets.serve(handler, "/asset/stampfly3d.js")

    assert served
    assert handler.status == 200
    assert "javascript" in handler.headers["Content-Type"]
    assert b"mountStampFly3D" in handler.body


@pytest.mark.parametrize("path", [
    "/asset/../../../etc/passwd.js",
    "/vendor/three/../../secret.js",
    "/mesh/../../../etc/passwd.stl",
    "/mesh/NOT_ALLOWED.stl",
])
def test_a_bad_asset_path_is_refused_before_the_filesystem(path):
    """A refused name never becomes a path that is opened.
    拒否される名前が、開かれるパスになることはないこと。"""
    handler = _FakeHandler()

    served = web_assets.serve(handler, path)

    assert served, "the asset handler must own (and refuse) this path"
    assert handler.error in (400, 404)
    assert handler.body == b""


def test_a_path_the_assets_do_not_own_is_left_to_the_page():
    """`/events` and `/` fall through to the page's own routes.
    `/events` や `/` は、ページ自身の経路へ素通しされること。"""
    for path in ("/", "/events", "/context"):
        assert web_assets.resolve(path) is None


# =============================================================================
# `sf telemetry --web` still behaves as before / 従来どおりであること
# =============================================================================

def test_the_telemetry_page_still_serves_its_own_assets_and_events():
    """The telemetry handler keeps its page, its meshes and its `/events`.

    This is the regression check for routing the shared assets through
    `web_assets`: the telemetry view is an established command and this
    change must be invisible to it.

    テレメトリ側の処理が、自分のページ・STL・`/events` を保ち続けること。

    共有資材を `web_assets` 経由にしたことによる既存動作の破壊を見る試験で
    ある。テレメトリ表示は既存のコマンドであり、この変更は見えてはならない。
    """
    assert telemetry_web._PAGE_PATH.exists()
    assert telemetry_web._PAGE_PATH.name == "telemetry_web.html"
    # Its own route, not the asset handler's / 資材側ではなく自分の経路
    assert web_assets.resolve("/events") is None

    handler = _FakeHandler()
    assert web_assets.serve(handler, "/vendor/three/three.module.min.js")
    assert handler.status == 200


def test_the_telemetry_page_keeps_its_offline_2d_fallback():
    """The 2D attitude indicator survives, for a browser without modules.

    It is defence in depth for the offline workshop LAN, so the extraction
    must not have taken it away.

    ES モジュール非対応のブラウザ向けに、2D の姿勢表示器が残っていること。

    オフラインの講習 LAN に対する多重防御であり、切り出しでこれを失っては
    ならない。
    """
    html = telemetry_web._PAGE_PATH.read_text(encoding="utf-8")

    assert "activate2DFallback" in html
    assert "drawAttitudeIndicator" in html


def test_the_pilot_page_is_a_separate_page():
    """`sf pilot --web` has its own page, not a patched telemetry one.
    `sf pilot --web` は自分のページを持ち、テレメトリ用の改変版ではないこと。"""
    assert pilot_web.page_path().exists()
    assert pilot_web.page_path().name == "pilot_web.html"

    html = pilot_web.page_path().read_text(encoding="utf-8")
    assert "/events" in html
    assert "判断" in html            # the decisions column / 判断の欄


def test_the_pilot_page_escapes_every_value_it_puts_into_markup():
    """
    The pilot page never interpolates an event value into markup unescaped.

    Part of the event stream is text a person typed (the instruction given to
    `sf pilot say`, leg names from a mission file), and the decision rows are
    assembled as HTML strings. Each untrusted field must go through `esc()`;
    a raw `${verdict.reason}` style interpolation is what this test refuses.

    操縦ページは、出来事の値をエスケープせずにマークアップへ差し込まない。

    出来事の流れの一部は人が打った文字列（`sf pilot say` の指示文、ミッション
    ファイルの区間名）であり、判断の行は HTML 文字列として組まれる。信頼できない
    各項目は `esc()` を通すこと。`${verdict.reason}` のような生の差し込みを、
    この試験が拒否する。
    """
    html = (web_assets.ASSET_DIR / "pilot_web.html").read_text(encoding="utf-8")

    has_escape_helper = "const esc = " in html
    assert has_escape_helper

    untrusted_fields = ("verdict.action", "verdict.reason", "verdict.source",
                        "ev.command", "answers.next_move.choice", "choice", "name", "v")
    for field in untrusted_fields:
        raw_interpolation = "${" + field + "}"
        raw_with_fallback = "${" + field + " ||"
        assert raw_interpolation not in html, field
        assert raw_with_fallback not in html, field
