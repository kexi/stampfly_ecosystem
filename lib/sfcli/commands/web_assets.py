"""
sfcli.commands.web_assets - the static half of every live browser view.

`sf telemetry --web` and `sf pilot ... --web` serve the SAME StampFly 3D
view, so they serve the same three files: the vendored three.js, the STL
body parts, and `stampfly3d.js` (the shared scene module extracted from
telemetry_web.html). This module holds the路 lookup and the request
handling for all three, so a page only has to say "serve my assets".

sfcli.commands.web_assets - ライブ表示ページに共通する静的配信の部分。

`sf telemetry --web` と `sf pilot ... --web` は**同じ** StampFly 3D ビューを
出すので、配信するファイルも同じである（同梱の three.js、STL の各パーツ、
telemetry_web.html から切り出した共有シーン `stampfly3d.js`）。その 3 つの
場所の解決と配信処理をここにまとめ、各ページは「資材を配信する」とだけ
言えばよいようにする。

Why everything is served locally and never from a CDN: these pages' normal
use is a PC whose Wi-Fi is associated 1:1 with the vehicle's own SoftAP (or
an offline workshop LAN), which has no route to any CDN at all -- that is
the normal case here, not an occasional outage. See
simulator/shared/assets/vendor/three/README.md.

すべてローカル配信で CDN を使わない理由: これらのページの通常の利用状況は、
PC の Wi-Fi が機体自身の SoftAP（またはオフラインの講習用 LAN）に 1 対 1 で
つながった状態であり、CDN への経路がそもそも無い — 稀な障害ではなく、これが
通常である。simulator/shared/assets/vendor/three/README.md 参照。
"""

import re
from pathlib import Path

from ..utils import paths

# Where this package keeps the pages and the shared scene module.
# ページと共有シーンモジュールを置く場所。
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"

# Names a request may ask for, as whole-string patterns. Anything else is
# refused before a path is built, so a traversal attempt never reaches the
# filesystem (the same rule the SILS GUI server follows).
# 要求を許す名前を、文字列全体の型として持つ。これ以外はパスを組み立てる前に
# 拒否するので、遡上を狙う要求がファイルシステムに届くことはない
#（SILS GUI のサーバと同じ規則）。
_MESH_NAME = re.compile(r"[a-z0-9_]+\.stl")
_VENDOR_PATH = re.compile(r"[A-Za-z0-9_./-]+\.js")
_ASSET_NAME = re.compile(r"[a-z0-9_]+\.js")

MESH_PREFIX = "/mesh/"
VENDOR_PREFIX = "/vendor/three/"
ASSET_PREFIX = "/asset/"


def mesh_dir() -> Path:
    """The STL body parts — the SAME files the SILS GUI and MuJoCo use.
    STL 本体パーツ。SILS GUI・MuJoCo と同一のファイル。"""
    return paths.root() / "simulator" / "shared" / "assets" / "meshes" / "parts"


def vendor_dir() -> Path:
    """The vendored three.js (see this module's docstring for why).
    同梱の three.js（理由は本モジュールの冒頭参照）。"""
    return paths.root() / "simulator" / "shared" / "assets" / "vendor" / "three"


def resolve(path: str):
    """Map a request path to (file, content type), or None if it is not ours.

    Returns None for a path this module does not own so the caller can fall
    through to its own routes; raises nothing for a bad name -- a refused
    name is reported as `(None, None)` so the caller answers 400 rather
    than 404, which distinguishes "you asked wrongly" from "not here".

    要求パスを (ファイル, 内容種別) に対応づける。本モジュールの担当外なら None。

    担当外は None を返し、呼び出し側が自分の経路へ進めるようにする。名前が
    不正な場合に例外は投げず `(None, None)` を返すので、呼び出し側は 404 では
    なく 400 を返せる — 「要求の誤り」と「ここには無い」を区別するためである。
    """
    if path.startswith(MESH_PREFIX):
        name = path[len(MESH_PREFIX):]
        if not _MESH_NAME.fullmatch(name):
            return (None, None)
        return (mesh_dir() / name, "model/stl")

    if path.startswith(VENDOR_PREFIX):
        rel = path[len(VENDOR_PREFIX):]
        is_safe = _VENDOR_PATH.fullmatch(rel) and ".." not in rel.split("/")
        if not is_safe:
            return (None, None)
        return (vendor_dir() / rel, "text/javascript; charset=utf-8")

    if path.startswith(ASSET_PREFIX):
        name = path[len(ASSET_PREFIX):]
        if not _ASSET_NAME.fullmatch(name):
            return (None, None)
        return (ASSET_DIR / name, "text/javascript; charset=utf-8")

    return None


def serve(handler, path: str) -> bool:
    """Answer a shared-asset request on `handler`. False if it was not one.

    `handler` is a BaseHTTPRequestHandler; this writes the whole response
    (or the error) and returns True once it has, so a page's `do_GET` reads
    as "if web_assets.serve(...): return".

    共有資材の要求に `handler` 上で応答する。担当外なら False。

    `handler` は BaseHTTPRequestHandler。応答（またはエラー）を最後まで書いて
    True を返すので、各ページの `do_GET` は「担当なら返る」と書ける。
    """
    resolved = resolve(path)
    if resolved is None:
        return False

    file_path, content_type = resolved
    if file_path is None:
        handler.send_error(400, "bad asset path")
        return True
    if not file_path.exists():
        handler.send_error(404)
        return True

    body = file_path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    return True


def send_page(handler, page_path: Path) -> None:
    """Write one HTML page as the whole response. / HTML ページを応答として書く。"""
    body = page_path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
