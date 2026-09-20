# 機体形状ファイル（STL）

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。

## 1. 概要

### このドキュメントについて

このディレクトリは、StampFly の**見た目を描くための形状ファイル**を置く場所である。形式は STL（Stereolithography、三角形の集まりで立体の表面を表すファイル形式）で、どのシミュレータもここを読み、同じ機械を見せる。

**物理（質量・慣性・衝突）はここからは来ない。** SILS（Software In the Loop Simulation）の MuJoCo モデルも Genesis 版も、形状は見た目だけに使い、物理は別に明示した値を使う。

### 出所とライセンスは未確認

**元の CAD（Computer-Aided Design、計算機による設計）データの作者と利用条件は、確認できていない。**

`stampfly_v1.stl` は 2026-01-05 のコミット `9f5aa130` で `https://github.com/kouhei1970/stampfly_sim` から `StampFly.stl` という名前で移されたもので、その先の出所（誰が作った CAD か、どの条件で使えるか）を記した文書はこのリポジトリのどこにも無い。ここに置いてある 13 個の部品は、その 1 ファイルを切り分けたものなので、同じことが当てはまる。

このリポジトリのコードは MIT ライセンスだが、**それがこれらの形状ファイルにも及ぶかは確かめられていない。** 推測で出所を書かないため、ここには「未確認である」という事実だけを書く。

**確認できたら、この節を書き換える。** 記すべきは、元の CAD の作者、利用条件（ライセンス名か許諾の内容）、確認の経緯（誰にどう確認したか、日付）である。あわせて `docs/plans/unity-simulator.md` §8 の「機体メッシュの出所」の行も更新する。

### 対象読者

- シミュレータの見た目を変更・追加する開発者
- これらのファイルを別の用途に使おうとして、利用条件を知る必要がある人

## 2. ファイル一覧

### 読まれているファイル

| ファイル | 中身 | 読む側 |
|---|---|---|
| `stampfly_v1.stl` | 機体全体が 1 つになった形状（9,740 三角形） | VPython 版 |
| `parts/frame.stl` | 機体の殻 | SILS・Genesis 版・`sf sils gui`・`sf telemetry --web`・Unity 版 |
| `parts/pcb.stl` | 基板 | 同上 |
| `parts/m5stamps3.stl` | 制御器（M5StampS3） | 同上 |
| `parts/battery.stl` | 電池 | 同上 |
| `parts/battery_adapter.stl` | 電池の受け | 同上 |
| `parts/motor_fl.stl`・`motor_fr.stl`・`motor_rl.stl`・`motor_rr.stl` | モータ缶 4 つ | 同上 |
| `parts/propeller_fl.stl`・`propeller_fr.stl`・`propeller_rl.stl`・`propeller_rr.stl` | プロペラ 4 つ | SILS・Genesis 版・Unity 版のみ（下記「プロペラを読まない 3 つの表示」を参照） |
| `parts/stampfly_fixed.urdf` | 13 部品を 1 つのリンクに固定した Genesis 版用の記述（URDF＝Unified Robot Description Format、ロボットの構造を書く XML の形式）。部品ごとの色（`rgba`）もここにある | Genesis 版、`sf params check` |
| `parts/stampfly.urdf` | 5 リンク・4 関節版（プロペラが回る）。現在どのシミュレータも読んでいない | `sf params check`（`base_link` の質量の照合）、`control/models/stampfly_physical.yaml` が慣性の出所として参照 |
| `parts/parts_config.json` | 13 部品それぞれの名前・色・不透明度と、`stampfly_v1.stl` の何番目から何番目の三角形かの範囲 | 切り分けの記録。実行時には読まれない（下記 5 章） |

### どこからも読まれていないファイル

消さずに残してあるが、**どのコードからも読まれていない**。

| ファイル | 事実 |
|---|---|
| `parts/*_BAK.stl`（13 個） | 座標変換（`bd382ddc`）の前の状態。`simulator/sandbox/coord_transformer/` が変換時に `_BAK` を付けて保存したもので、13 個とも同じ名前の変換後のファイルとは中身が違う |
| `parts/m5stamps3_backup.stl` | 法線の修正（`09346953`）の前の状態。`m5stamps3` だけ、別の作業ごとの控えが 2 つ残っている（下記） |
| `parts/parts_config_BAK.json` | 同じく変換前の記録。中身は `parts_config.json` と同一（変換は STL 側にだけ入ったため） |
| `parts/classification_progress.json` | 切り分け作業の途中経過（2026-01-10 の日時つき）。`simulator/sandbox/classifier/` が書き出したもの |
| `../loaders/stl_loader.py` | `stampfly_sim` から移された VPython 用の読み込みスクリプト（元の名前は `stl2object.py`）。どの Python ファイルもこれを import しておらず、中で開く `StampFly.stl` は改名後の名前に直されていないのでこのリポジトリに存在しない。同じ処理は `vpython_backend.py` に写して使われている |

`m5stamps3` の控えが 2 つあるのは、**別の作業でそれぞれ取られたため**である。`git show` で中身を照合すると 3 つは次の順に並ぶ。

| ファイル | 大きさ | 状態 |
|---|---|---|
| `m5stamps3_BAK.stl` | 5,284 B | 座標変換の前（`bd382ddc` で保存） |
| `m5stamps3_backup.stl` | 5,284 B | 座標変換の後・法線の修正の前（`09346953` で保存） |
| `m5stamps3.stl` | 5,484 B | 現在。4 面増えている（5 章） |

他の 12 部品には `_BAK`（座標変換の前）だけがある。

## 3. どのシミュレータが何を読むか

| 読む側 | 読むファイル | 読み方 |
|---|---|---|
| VPython 版 | `stampfly_v1.stl` | `simulator/vpython/visualization/vpython_backend.py` が `numpy-stl` で直接開く（`STAMPFLY_STL_PATH`）。1 ファイルのまま使う唯一の読み手 |
| Genesis 版 | `parts/stampfly_fixed.urdf` と、それが指す 13 個の STL | `simulator/genesis/scripts/run_genesis_sim.py`・`run_genesis_headless.py` が URDF の名前で読み込む |
| SILS | `parts/` の 13 個 | `simulator/sils/models/stampfly.xml` が `<compiler meshdir="../../shared/assets/meshes/parts">` で参照する。**見た目だけ**（`contype="0" conaffinity="0"`）で、衝突にも慣性にも寄与しない |
| `sf sils gui` | `parts/` のうち 9 個 | `simulator/sils/gui/server.py` が `/mesh/<名前>.stl` で配信し、`static/app.js` の `BODY_PARTS` が名前を挙げて要求する |
| `sf telemetry --web` | `parts/` のうち 9 個 | `lib/sfcli/commands/telemetry_web.py` が同じ `/mesh/` の仕組みで配信し、`lib/sfcli/assets/telemetry_web.html` が要求する |
| Unity 版 | `parts/` の 13 個 | エディタで 1 回だけ変換し、Unity 側の資産をコミットする（4 章） |

### プロペラを読まない 3 つの表示

`sf sils gui`・`sf telemetry --web`・ランディングページの 3 つは、STL を 9 個しか読まない。プロペラは回るところを見せるため、**その場で 3 枚羽根を作って描く**（`app.js`・`telemetry_web.html` の `buildDrone`）。回らない 9 個だけを STL から読む形になっている。

### `landing/assets/model/` の 9 個について

ランディングページ（`landing/index.html`）は、`simulator/shared/` ではなく `landing/assets/model/` の下にある 9 個を読む。**この 9 個は `parts/` の同名ファイルと 1 バイトも違わない**（`shasum -a 256` で照合）。プロペラの 4 個が無いのは、上と同じく手続き生成で描くためである。

公開サイトは `landing/` の下だけを配信するので、配信物に含めるための複製である。複製が作られたのは紹介ページを新設した 2026-06-07（`ae3098a7`）で、以後どちらの側も中身は変わっていない。同じ 9 個を読む道具が他に 2 つあり、どちらも `landing/assets/model/` の側を見ている。

| 道具 | 用途 |
|---|---|
| `docs/events/_shared/tikz/scripts/render_stampfly_iso.py` | スライドの `imu_axes.tex` 用に機体の外形を描く |
| `tools/flasher_gui/assets/gen_icon_3d.py` | 書き込み用 GUI のアイコンを描く |

複製である以上、`parts/` 側を変えたら `landing/assets/model/` 側も揃えないと食い違う。**揃っているかを自動で確かめる仕組みは無い。**

## 4. 単位と座標系

| 項目 | 値 |
|---|---|
| 形式 | バイナリ STL（ASCII 形式のものは無い） |
| 単位 | ミリメートル。読む側が 0.001 倍して m にする（URDF の `scale="0.001 0.001 0.001"`、MuJoCo の `scale`） |
| 座標系 | 右手系・Y 上・Z 前・X 左（three.js とランディングページの規約） |
| 巻き | 13 ファイルとも、符号付き体積が正（外向き） |
| 保存された法線 | **信用しない。** `m5stamps3.stl` は 108 面すべてが自身の巻きと逆の法線を保存している |

```
                Y (上)
                │
                │
                └──── Z (前)
               ╱
              ╱
            X (左)
```

### 保存された法線を使わない理由

法線を直す `09346953` は 12 ファイルを名前で挙げて処理しており、`m5stamps3` がその一覧から漏れている。そのため 13 ファイルが揃って正しいと言える記述は**巻きだけ**である。読む側は法線を巻きから計算し直す。Genesis 版は URDF 経由で、Unity 版は変換時に、それぞれ計算し直している。

### 物理の値との関係

モータ 4 つの重心は ±22.804 mm にある。SILS の物理が使うロータ位置は ±0.023 m（`simulator/sils/models/stampfly.xml`）で、差は 0.2 mm である。**見た目を物理に合わせて歪めることはしない。** 形状は見た目のためのもので、物理の値は `control/models/stampfly_physical.yaml` を基準とする。

## 5. 13 部品がどう作られたか

`parts/` の 13 個は、新しく作った CAD ではなく、**`stampfly_v1.stl` を三角形の番号で切り分けたもの**である。`parts_config.json` に、部品ごとに `stampfly_v1.stl` の何番目から何番目の三角形かが範囲で残っている。

範囲を合計すると 0〜9739 の 9,740 個をちょうど 1 回ずつ覆う。つまり**元の 1 ファイルの全ての三角形が、重複も取りこぼしも無く 13 部品のどれかに入っている**。`frame` だけが先頭の連続した 4,520 個で、`pcb` と `m5stamps3` は範囲が入り組んでいる（基板と制御器が番号の上で交互に現れるため、`pcb` は 5 つ、`m5stamps3` は 4 つの範囲に分かれる）。

ひとつ食い違いがある。`parts_config.json` は `m5stamps3` を 104 三角形と記録しているが、`m5stamps3.stl` は 108 面を持つ。他の 12 部品は記録と実ファイルが一致する。

差が生じたのは法線を直したコミット `09346953` である。同コミットで `m5stamps3.stl` だけが 5,284 → 5,484 バイトに増えており（1 面 50 バイトなので 4 面ぶん）、そのとき `m5stamps3_backup.stl` に変更前が残された。**増えた 4 面が何であるかは確かめられていない。** `parts_config.json` は切り分け時（`7e936b00`）の記録のままなので、104 という数はその時点の値である。

| 日付 | コミット | できごと |
|---|---|---|
| 2026-01-05 | `9f5aa130` | `stampfly_sim` から `StampFly.stl` を移し、`assets/meshes/stampfly_v1.stl` に改名 |
| 2026-01-10 | `7e936b00` | ブラウザ上の切り分け道具（`simulator/sandbox/classifier/`）で手作業で分類し、部品ごとの STL を書き出した |
| 2026-01-11 | `bd382ddc` | 座標を WebGL の規約（Y 上・Z 前・X 左、右手系のまま）へ変換。変換前を `_BAK` 付きで保存 |
| 2026-01-11 | `09346953` | 巻きが逆だった **12 ファイル**の法線を直した（`m5stamps3` は含まれない） |
| 2026-01-15 | `ca226776` | `simulator/assets/` を `simulator/shared/assets/` へ移した |

いずれも作者は Kouhei Ito である。切り分けに使った道具は `simulator/sandbox/` に残っている（`classifier/`・`coord_transformer/`・`stl_splitter/`）。

## 6. Unity 版への変換

Unity は右手系でも Y 上でもないので、そのままでは読めない。**エディタで 1 回だけ変換し、変換後の資産をコミットする。** STL の複製は Unity 側に置かない。

### 変換の内容

| 手順 | 内容 | 理由 |
|---|---|---|
| x の符号反転 | 右手系（X 左）→ Unity の左手系（X 右） | Unity は左手系で、軸の向きを合わせるだけでは鏡像になる |
| 三角形の巻きの反転 | 頂点 3 つのうち 2 つを入れ替える | 符号反転だけだと面が裏返るため、巻きも戻す |
| 0.001 倍 | mm → m | 他の読み手（URDF・MuJoCo）と同じ尺度 |
| 法線の計算し直し | 保存された法線は読まず、巻きから求める | 4 章「保存された法線を使わない理由」 |

### 作り直し方

Unity エディタのメニュー `StampFly/Meshes/Rebuild Vehicle Meshes` を実行する。これが `parts/` の STL を読み、上の変換をして `simulator/unity/Assets/StampFly/Runtime/Resources/StampFly/Meshes/<部品名>.asset` に書く。書かれた `.asset` はコミットする。

どの部品をどの色で描くかの一覧は `simulator/unity/Assets/StampFly/Runtime/Vehicle/VehicleParts.cs` にある。色は `parts/stampfly_fixed.urdf` の `rgba` を写したもので、Genesis 版と同じ見た目になる。実行時は `Resources` から読み、材質は `StampFly.Core.ShadedMaterials` が用意する。

**この形状に差し替えた後も、基本形状（板・モータ缶・手続き生成の 3 枚羽根）の見た目は切り替え用に残す。**

---

<a id="english"></a>

# Vehicle Geometry Files (STL)

## 1. Overview

### About This Document

This directory holds the **geometry used to draw the StampFly**. The format is STL (Stereolithography, a file format describing a solid's surface as a collection of triangles), and every simulator reads it, so they all show the same machine.

**No physics comes from here.** Both the SILS (Software In the Loop Simulation) MuJoCo model and the Genesis version use these meshes for appearance only; mass, inertia, and collision are stated separately.

### Provenance and License Are Unconfirmed

**The author of the original CAD (Computer-Aided Design) data and the terms it may be used under have not been established.**

`stampfly_v1.stl` was moved in on 2026-01-05 by commit `9f5aa130` from `https://github.com/kouhei1970/stampfly_sim`, where it was called `StampFly.stl`. Nothing in this repository records what lies further back — who authored the CAD, or under what terms it may be used. The thirteen parts here were cut out of that one file, so the same applies to them.

This repository's code is MIT licensed, but **whether that extends to these geometry files has not been verified.** Rather than guess at a provenance, this section states only the fact that it is unconfirmed.

**When it is established, rewrite this section.** It should then record the original CAD's author, the terms of use (a license name or the substance of the permission), and how it was established — from whom, and on what date. Update the "vehicle mesh provenance" row in `docs/plans/unity-simulator.md` §8 at the same time.

### Target Audience

- Developers changing or adding to a simulator's appearance
- Anyone intending to use these files elsewhere, who therefore needs to know the terms

## 2. File Listing

### Files That Are Read

| File | Content | Read by |
|---|---|---|
| `stampfly_v1.stl` | The whole vehicle as one mesh (9,740 triangles) | The VPython version |
| `parts/frame.stl` | The shell | SILS, Genesis, `sf sils gui`, `sf telemetry --web`, Unity |
| `parts/pcb.stl` | The board | Same |
| `parts/m5stamps3.stl` | The controller (M5StampS3) | Same |
| `parts/battery.stl` | The battery | Same |
| `parts/battery_adapter.stl` | The battery holder | Same |
| `parts/motor_fl.stl`, `motor_fr.stl`, `motor_rl.stl`, `motor_rr.stl` | The four motor cans | Same |
| `parts/propeller_fl.stl`, `propeller_fr.stl`, `propeller_rl.stl`, `propeller_rr.stl` | The four propellers | SILS, Genesis, and Unity only (see "The Three Views That Do Not Read Propellers") |
| `parts/stampfly_fixed.urdf` | The Genesis description, with all thirteen parts fixed into one link (URDF: Unified Robot Description Format, an XML format describing a robot's structure). The per-part colours (`rgba`) live here too | The Genesis version, `sf params check` |
| `parts/stampfly.urdf` | The 5-link, 4-joint variant with turning propellers. No simulator currently reads it | `sf params check` (the `base_link` mass), and `control/models/stampfly_physical.yaml` cites it as the source of the inertia |
| `parts/parts_config.json` | Each part's name, colour, and opacity, plus the range of triangle indices it occupies in `stampfly_v1.stl` | A record of how the parts were cut; not read at run time (section 5) |

### Files Nothing Reads

Kept rather than deleted, but **read by no code**.

| File | Fact |
|---|---|
| `parts/*_BAK.stl` (thirteen) | The state before the coordinate transformation (`bd382ddc`). `simulator/sandbox/coord_transformer/` wrote them with the `_BAK` suffix during the conversion; all thirteen differ in content from the converted file of the same name |
| `parts/m5stamps3_backup.stl` | The state before the normal fix (`09346953`). `m5stamps3` alone has two backups, each from a different operation (below) |
| `parts/parts_config_BAK.json` | The pre-conversion record. Its contents are identical to `parts_config.json`, because the transformation only touched the STL files |
| `parts/classification_progress.json` | Working state from the cutting-up session, timestamped 2026-01-10, written by `simulator/sandbox/classifier/` |
| `../loaders/stl_loader.py` | A VPython loading script moved in from `stampfly_sim` (originally `stl2object.py`). No Python file imports it, and the `StampFly.stl` it opens was never updated to the renamed file, so it does not exist in this repository. The same processing was copied into `vpython_backend.py`, which is what actually runs |

`m5stamps3` has two backups because **each was taken during a different operation**. Comparing contents with `git show` orders the three as follows.

| File | Size | State |
|---|---|---|
| `m5stamps3_BAK.stl` | 5,284 B | Before the coordinate transformation (saved by `bd382ddc`) |
| `m5stamps3_backup.stl` | 5,284 B | After the transformation, before the normal fix (saved by `09346953`) |
| `m5stamps3.stl` | 5,484 B | Current, with four extra facets (section 5) |

The other twelve parts have only a `_BAK` (from before the coordinate transformation).

## 3. Which Simulator Reads What

| Reader | Files | How |
|---|---|---|
| VPython | `stampfly_v1.stl` | `simulator/vpython/visualization/vpython_backend.py` opens it directly with `numpy-stl` (`STAMPFLY_STL_PATH`). It is the only reader that uses the single-file mesh |
| Genesis | `parts/stampfly_fixed.urdf` and the thirteen STL files it names | `simulator/genesis/scripts/run_genesis_sim.py` and `run_genesis_headless.py` load it through the URDF |
| SILS | All thirteen in `parts/` | `simulator/sils/models/stampfly.xml` refers to them via `<compiler meshdir="../../shared/assets/meshes/parts">`. **Appearance only** (`contype="0" conaffinity="0"`): they contribute to neither collision nor inertia |
| `sf sils gui` | Nine of `parts/` | `simulator/sils/gui/server.py` serves them at `/mesh/<name>.stl`, and `static/app.js` requests them by name through `BODY_PARTS` |
| `sf telemetry --web` | Nine of `parts/` | `lib/sfcli/commands/telemetry_web.py` serves them the same `/mesh/` way, and `lib/sfcli/assets/telemetry_web.html` requests them |
| Unity | All thirteen in `parts/` | Converted once in the editor, with the Unity-side asset committed (section 6) |

### The Three Views That Do Not Read Propellers

`sf sils gui`, `sf telemetry --web`, and the landing page read only nine STL files. Because they must show the propellers turning, they **build three-blade propellers procedurally instead** (`buildDrone` in `app.js` and `telemetry_web.html`). Only the nine parts that do not turn come from STL.

### About the Nine Files in `landing/assets/model/`

The landing page (`landing/index.html`) reads nine files under `landing/assets/model/` rather than from `simulator/shared/`. **Those nine are byte-for-byte identical to the same-named files in `parts/`** (checked with `shasum -a 256`). The four propellers are absent for the reason above: they are generated procedurally.

The copy exists so the files ship with the published site, which serves only what is under `landing/`. It was made on 2026-06-07 (`ae3098a7`) when the introduction page was added, and neither side has changed since. Two other tools read the same nine, and both look at the `landing/assets/model/` copy.

| Tool | Purpose |
|---|---|
| `docs/events/_shared/tikz/scripts/render_stampfly_iso.py` | Draws the vehicle's outline for the slides' `imu_axes.tex` |
| `tools/flasher_gui/assets/gen_icon_3d.py` | Draws the flashing GUI's icon |

Being a copy, a change under `parts/` leaves the two sides inconsistent unless `landing/assets/model/` is updated to match. **Nothing checks automatically that they agree.**

## 4. Units and Coordinate System

| Item | Value |
|---|---|
| Format | Binary STL (none are in the ASCII form) |
| Unit | Millimetres. Readers scale by 0.001 to metres (`scale="0.001 0.001 0.001"` in the URDFs, `scale` in MuJoCo) |
| Frame | Right-handed, Y up, Z forward, X left (the three.js and landing-page convention) |
| Winding | All thirteen enclose a positive signed volume (outward facing) |
| Stored normals | **Not trusted.** All 108 facets of `m5stamps3.stl` store a normal opposite to their own winding |

```
                Y (up)
                │
                │
                └──── Z (forward)
               ╱
              ╱
            X (left)
```

### Why the Stored Normals Are Not Used

The normal fix in `09346953` names twelve files, and `m5stamps3` is missing from that list. Winding is therefore the **only** description all thirteen files agree on. Readers recalculate the normals from the winding: the Genesis version does so through the URDF, and the Unity version during conversion.

### Relation to the Physical Values

The four motors' centroids sit at ±22.804 mm. The rotor positions the SILS physics uses are ±0.023 m (`simulator/sils/models/stampfly.xml`), a difference of 0.2 mm. **The appearance is not distorted to match the physics.** The geometry exists to be drawn; the physical values are governed by `control/models/stampfly_physical.yaml`.

## 5. How the Thirteen Parts Were Made

The thirteen files under `parts/` are not new CAD. They were **cut out of `stampfly_v1.stl` by triangle index**. `parts_config.json` still records, for each part, which range of triangles in `stampfly_v1.stl` it came from.

Summing those ranges covers indices 0 through 9739 exactly once each: **every triangle of the original single file belongs to exactly one of the thirteen parts, with nothing duplicated and nothing left out.** Only `frame` is a single run (the leading 4,520). `pcb` and `m5stamps3` interleave by index — the board and the controller alternate — so `pcb` is split across five ranges and `m5stamps3` across four.

One discrepancy remains. `parts_config.json` records `m5stamps3` as 104 triangles, while `m5stamps3.stl` holds 108 facets; for the other twelve parts the record and the file agree.

The difference dates from the normal-fixing commit `09346953`, where `m5stamps3.stl` alone grew from 5,284 to 5,484 bytes — four facets, at 50 bytes each — and its previous state was left behind as `m5stamps3_backup.stl`. **What those four facets are has not been established.** `parts_config.json` still holds the record made when the parts were cut (`7e936b00`), so 104 is the count as of that moment.

| Date | Commit | Event |
|---|---|---|
| 2026-01-05 | `9f5aa130` | `StampFly.stl` was moved in from `stampfly_sim` and renamed `assets/meshes/stampfly_v1.stl` |
| 2026-01-10 | `7e936b00` | Parts were classified by hand in a browser-based tool (`simulator/sandbox/classifier/`) and written out as per-part STL files |
| 2026-01-11 | `bd382ddc` | Coordinates were converted to the WebGL convention (Y up, Z forward, X left, still right-handed). The originals were kept with the `_BAK` suffix |
| 2026-01-11 | `09346953` | The normals of the **twelve** files with reversed winding were corrected (`m5stamps3` was not among them) |
| 2026-01-15 | `ca226776` | `simulator/assets/` was moved to `simulator/shared/assets/` |

Kouhei Ito authored all of them. The tools used to cut the parts up remain in `simulator/sandbox/` (`classifier/`, `coord_transformer/`, `stl_splitter/`).

## 6. Conversion for the Unity Version

Unity is neither right-handed nor Y up, so it cannot read these as they are. **The conversion happens once in the editor and the converted asset is committed.** No copy of the STL files is placed on the Unity side.

### What the Conversion Does

| Step | Content | Reason |
|---|---|---|
| Flip the sign of x | Right-handed (X left) → Unity's left-handed (X right) | Unity is left-handed; merely matching axis directions would produce a mirror image |
| Flip the triangle winding | Swap two of the three vertices | The sign flip alone turns the faces inside out, so the winding is restored |
| Scale by 0.001 | mm → m | The same scale the other readers (URDF, MuJoCo) use |
| Recalculate the normals | The stored normals are not read; they come from the winding | Section 4, "Why the Stored Normals Are Not Used" |

### How to Rebuild Them

Run the Unity editor menu item `StampFly/Meshes/Rebuild Vehicle Meshes`. It reads the STL files under `parts/`, applies the conversion above, and writes `simulator/unity/Assets/StampFly/Runtime/Resources/StampFly/Meshes/<part>.asset`. The resulting `.asset` files are committed.

The list of which part is drawn in which colour is in `simulator/unity/Assets/StampFly/Runtime/Vehicle/VehicleParts.cs`. The colours are copied from the `rgba` values in `parts/stampfly_fixed.urdf`, so the appearance matches the Genesis version. At run time the meshes are loaded from `Resources`, and `StampFly.Core.ShadedMaterials` supplies the materials.

**After the switch to this geometry, the primitive-shape appearance (a plate, motor cans, and procedurally generated three-blade propellers) is kept so it can be switched back to.**
