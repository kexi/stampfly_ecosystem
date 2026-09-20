# StampFly World File Format

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。

## 1. 概要

### このドキュメントについて

Unity 版シミュレータが読み込む**空間ファイル**（`*.world.json`）の形式を定める。障害物を置いた飛行空間を 1 つの JSON ファイルで表し、同梱のプリセットと利用者の保存ファイルが同じ形式を使う。

形式の**仕様**は [`world.schema.json`](world.schema.json)（JSON Schema）である。本文書はその解説であり、種類ごとの `size` の意味・原点・座標変換など、スキーマだけでは伝わらない取り決めを補う。検査の**実装**は `tools/unity_world/validate.py` にあり、スキーマと実装が食い違わないことを `tools/unity_world/tests/test_validate.py` が守る。

### 対象読者

| 読者 | 読むところ |
|------|-----------|
| 空間を作る・足す人 | §2 全体の構造、§3 障害物の種類、§7 検査、§8 空間を足す手順 |
| Unity の C# 側を実装する人 | §3 原点と回転、§5 座標系、§6 Unity への変換、§9 JsonUtility 向けのクラス |
| 他の道具（MuJoCo・Genesis）から読む人 | §5 座標系、§3 障害物の種類 |

### 関連文書

| 文書 | 内容 |
|------|------|
| [`world.schema.json`](world.schema.json) | 形式の仕様（JSON Schema） |
| [`../../../docs/plans/unity-simulator.md`](../../../docs/plans/unity-simulator.md) | Unity 版シミュレータの計画（§4 空間ファイル、§5 段階 4・5） |
| [`../../../docs/architecture/coordinate-systems.md`](../../../docs/architecture/coordinate-systems.md) | リポジトリ全体の座標系 |
| [`../../sils/docs/coordinate_frames.md`](../../sils/docs/coordinate_frames.md) | SILS の座標系（§7 が Unity との対応） |

## 2. 全体の構造

1 つの空間ファイルは次の項目を持つ。`description` と `light` 以外は必須である。

| 項目 | 型 | 内容 |
|------|-----|------|
| `format` | 文字列 | 形式の目印。必ず `"stampfly-world"` |
| `version` | 整数 | 形式の版。現在は `1` のみ |
| `units` | オブジェクト | `{"length": "m", "angle": "deg"}` 固定。他の道具が単位を推測しなくて済むよう明示する |
| `frame` | 文字列 | 必ず `"ENU"`（x=東・y=北・z=上、右手系）。§5 参照 |
| `name` | 文字列 | 空間の識別子。英小文字・数字・下線のみ。同梱の空間ではファイル名の幹と一致させる |
| `description` | オブジェクト | `{"ja": "...", "en": "..."}`。空間のねらいを 1 行で。任意だが同梱の空間では必ず書く |
| `room` | オブジェクト | 部屋。§2.1 |
| `floor` | オブジェクト | 床の見た目とフロー品質。§2.2 |
| `light` | 文字列 | `day` / `indoor` / `dim`。省略時は `indoor` |
| `spawn` | オブジェクト | 機体の出発点。§2.3 |
| `obstacles` | 配列 | 障害物の一覧。空でもよい。§3 |

### 2.1 `room`（部屋）

部屋は原点を中心とする直方体で、**床は z=0 の平面**である。

| 項目 | 型 | 内容 |
|------|-----|------|
| `size` | 数 3 つ | 内寸 `[x_東, y_北, z_上]`（m） |
| `walls` | 真偽 | 側面 4 枚の壁を作るか（衝突し、ToF に映る） |
| `ceiling` | 真偽 | 天井を作るか |

```
内部の範囲:
  x : −size[0]/2 〜 +size[0]/2
  y : −size[1]/2 〜 +size[1]/2
  z :          0 〜  size[2]
```

### 2.2 `floor`（床）

| 項目 | 型 | 内容 |
|------|-----|------|
| `pattern` | 文字列 | `checker` / `grid` / `plain` / `noise` / `stripes` |
| `pitch` | 数 | 模様の繰り返し長さ（m）。`plain` では無視されるが記載は必須 |
| `colors` | 文字列 2 つ | 模様の 2 色 `"#rrggbb"`。`plain` では 1 色目のみ使う |
| `flow_quality` | 数 0..1 | §4 参照 |

### 2.3 `spawn`（出発点）

| 項目 | 型 | 内容 |
|------|-----|------|
| `position` | 数 3 つ | 機体**重心**の位置 `[x, y, z]`（m） |
| `yaw_deg` | 数 | 機首方位。上から見て +x（東）から**反時計回り**の度数。0 = 東向き、90 = 北向き |

出発点の真上には**離陸の余地 0.6 m** が要る（検査が確かめる）。下向き ToF には 30 mm の盲点があり（`stampfly-tof-30mm-blind-zone`）、そこを抜けて高度が落ち着くまでの余裕として決めた値である。

## 3. 障害物の種類

障害物は**平らな共通の項目**だけを持つ。種類ごとに違う入れ子や、辞書、可変の型は使わない（Unity の `JsonUtility` が読めないため。§9 参照）。

| 項目 | 型 | 必須 | 内容 |
|------|-----|------|------|
| `id` | 文字列 | 必須 | ファイル内で重複しない識別子。英小文字・数字・下線 |
| `type` | 文字列 | 必須 | §3.1 の 10 種のいずれか |
| `position` | 数 3 つ | 必須 | **その種類の原点**の位置 `[x, y, z]`（m）。§3.1 |
| `rotation_deg` | 数 3 つ | 必須 | `[roll, pitch, yaw]`（度）。§3.2 |
| `size` | 数 3 つ | 必須 | 3 つの長さ（m）。意味は種類ごとに決まる。§3.1 |
| `thickness` | 数 | `gate`・`ring`・`tunnel` のみ | 枠・管の肉厚（m）。他の種類では**書いてはならない** |
| `segments` | 整数 | `ring` のみ・任意 | 円を近似する分割数（6..128、既定 24）。他の種類では**書いてはならない** |
| `color` | 文字列 | 必須 | `"#rrggbb"` |
| `flow_quality` | 数 0..1 | 任意 | 上面のフロー品質。省略時は 0.8。§4 |

### 3.1 種類ごとの `size` の意味と原点

**原点**とは `position` が指す点である。種類によって底面の中心だったり形の中心だったりするので、ここで確定させる。

| 種類 | `size[0]`（x） | `size[1]`（y） | `size[2]`（z） | 原点 | `thickness` |
|------|---------------|---------------|---------------|------|-------------|
| `box` | 幅 | 奥行き | 高さ | 底面の中心 | 不可 |
| `pillar` | 幅 | 奥行き | 高さ | 底面の中心 | 不可 |
| `wall` | 長さ | 厚み | 高さ | 底面の中心 | 不可 |
| `gate` | **開口**の幅 | 枠の奥行き | **開口**の高さ | 開口の下辺の中心（床の高さ） | 必須（枠の太さ） |
| `ring` | **内径** | 内径（`size[0]` と同値） | 同上 | **開口の中心** | 必須（輪の太さ） |
| `tunnel` | **開口**の幅 | 管の長さ | **開口**の高さ | 入口の開口の下辺の中心 | 必須（管の肉厚） |
| `table` | 天板の幅 | 天板の奥行き | **天板上面**の高さ | 接地面の中心 | 不可 |
| `step` | 幅 | 奥行き | 高さ | 底面の中心 | 不可 |
| `ramp` | 幅 | 水平距離（+y 方向） | 立ち上がり | **低い辺**の中心（底） | 不可 |
| `pad` | 幅 | 奥行き | 厚み | 底面の中心 | 不可 |

**注意点:**

- `gate`・`tunnel` の `size` は**開口**の寸法であり、外形ではない。外形は開口に `thickness` を足した大きさになる
- `ring` は丸いので `size[0]` と `size[1]` は等しくなければならない（検査が確かめる）。輪は **x-z 平面**にあり、y 方向の厚みが `thickness` である。つまり回転が無いとき、輪を**北向きに**くぐることになる
- `ring` は `segments` 本の直線の棒で近似し、その棒を**半径 `size[0]/2 + thickness` の円に内接させる**（棒の長さはその外円の弦、すなわち `2·(size[0]/2 + thickness)·sin(π/segments)`）。検査が使う外形はこの外円を囲む箱なので、**外接**させると棒の角がその箱からはみ出してしまう。内接させれば分割数によらず外形が検査と一致し、内径の穴も塞がらない
- `tunnel` は側壁 2 枚と天井だけで、**床は作らない**。管は部屋の床の上に置かれる前提であり、中に床をもう 1 枚作ると機体が越える段差になるためである（`size[2]` の開口の高さは部屋の床から測る）
- `table` の `size[2]` は**天板上面**の高さである（天板の厚みではない）。脚は Unity 側が天板の四隅から床まで伸ばす
- `ramp` は +y 方向に向かって上がる。向きを変えるには `rotation_deg` の yaw を使う

#### 図: 底面中心の原点（`box`・`pillar`・`wall`・`step`・`pad`）

```
        ┌───────────────┐  ─┬─
        │               │   │ size[2]（高さ）
        │               │   │
        └───────●───────┘  ─┴─
                ↑
            原点 = 底面の中心（z は床の高さ）
        ├─── size[0] ───┤
```

#### 図: `gate`（開口の下辺の中心が原点）

```
      ┌─┬─────────────┬─┐  ─┬─ ← 枠の上辺（厚み thickness）
      │ │             │ │   │
      │ │   開　口    │ │   │ size[2]（開口の高さ）
      │ │             │ │   │
      └─┴──────●──────┴─┘  ─┴─
       ↑       ↑       ↑
  thickness  原点   thickness
        ├─ size[0] ─┤       ← 開口の幅（枠の内側どうし）

  y 方向の奥行きは size[1]（原点を中心に前後へ半分ずつ）
```

#### 図: `ring`（開口の中心が原点）

```
           ╭───────────╮   ─┬─
         ╭─┤           ├─╮  │
         │ │           │ │  │ size[0] = 内径
         │ │     ●     │ │  │   ●= 原点（開口の中心）
         │ │           │ │  │
         ╰─┤           ├─╯  │
           ╰───────────╯   ─┴─
          ├─ thickness ─┤ は輪の太さ
   輪は x-z 平面にあり、y 方向の厚みが thickness
```

#### 図: `tunnel`（入口の開口の下辺の中心が原点）

```
   横から見た図（+y 方向へ伸びる）

     ┌───────────────────────┐ ─┬─ thickness
     │                       │
     │        開　口         │  size[2]
     │                       │
     ●───────────────────────┘ ─┴─
     ↑
   原点 = 入口の開口の下辺の中心
     ├──────  size[1] ───────┤ ← 管の長さ
```

#### 図: `table`（接地面の中心が原点。`size[2]` は天板上面の高さ）

```
     ┌───────────────────┐  ← 天板（上面が z = size[2]）
     │                   │
     ╵ │             │   ╵  ← 脚（Unity 側が生成）
       │             │
    ───●─────────────────── 床
       ↑
   原点 = 接地面の中心（z は床の高さ）
```

#### 図: `ramp`（低い辺の中心が原点。+y 方向へ上がる）

```
   横から見た図
                      ╱│  ─┬─
                    ╱  │   │ size[2]（立ち上がり）
                  ╱    │   │
                ●──────┘  ─┴─
                ↑
        原点 = 低い辺の中心
                ├─ size[1] ─┤ ← 水平距離（+y 方向）
```

### 3.2 回転の順序

`rotation_deg` は `[roll, pitch, yaw]`（度）で、**内因性**に次の順で適用する。

```
  1. yaw   … +z（上）軸まわり
  2. pitch … 1 の後の +y 軸まわり
  3. roll  … 2 の後の +x 軸まわり

  回転行列: R = Rz(yaw) · Ry(pitch) · Rx(roll)
```

いずれも**正軸の側から見て反時計回り**（右手系の右ねじ）を正とする。ほとんどの障害物は yaw だけで済む。

## 4. `flow_quality`（フローから見た模様の豊かさ）

オプティカルフローのセンサ（PMW3901）は、下に見える面の模様を追って速度を測る。`flow_quality` はその面に模様がどれだけあるかを 0〜1 で表す。

| 値 | 意味 |
|----|------|
| 0.0 | 模様が無く、速度がまったく測れない |
| 0.3 前後 | 模様が乏しく、SQUAL が下がって位置保持が流れる |
| 0.8〜1.0 | 十分な模様があり、速度を確実に測れる |

床（`floor.flow_quality`）と、各障害物の**上面**（`obstacles[].flow_quality`）に効く。Unity 側はセンサの視線が当たった面の値を取り、距離による減衰と掛け合わせて SQUAL を作る（計画 §5 の段階 5）。

同梱の `featureless_floor` は、床を 0.05 にして位置保持が流れる状態を作り、その上に `flow_quality` の高い `pad` を島のように置くことで、効く条件と効かない条件を並べて見せられるようにしてある。

## 5. 座標系を ENU にした理由

空間ファイルの座標系は **ENU**（x=東、y=北、z=上、右手系）に固定する。Unity の左手系 Y 上は**ファイルに出さない**。

| 理由 | 説明 |
|------|------|
| 他の道具から読めるようにする | MuJoCo 版（`simulator/sils/`）の世界は ENU（`coordinate_frames.md` §2）であり、Genesis 版も Z 上である。同じ空間ファイルを将来そのまま読める |
| 左手系を閉じ込める | 左手系は極性ベクトルと軸性ベクトルで符号の扱いが変わる（`coordinate_frames.md` §7.3）。この厄介さを Unity の中だけに留め、ファイルに漏らさない |
| 人が読んで分かる | 「x が東、y が北、z が上」は地図と同じ並びで、数値を見ただけで配置を思い描ける |
| 1 か所で変換する | SILS の「座標変換は 1 か所」という方針に合わせる。C# 側に残る座標の仕事は、この空間ファイルと Unity の変換だけである |

## 6. Unity へ読み込むときの変換

### 6.1 位置

ENU の右手系から Unity の左手系（Y 上）へは、**y と z を入れ替える**だけでよい。

```
  Unity.x =  ENU.x     （東 → Unity の右）
  Unity.y =  ENU.z     （上 → Unity の上）
  Unity.z =  ENU.y     （北 → Unity の前）

  行列 U = [[1, 0, 0],
            [0, 0, 1],
            [0, 1, 0]]       det(U) = −1 → 利き手が反転する
```

`det(U) = −1` なのは、右手系から左手系へ移るのだから当然である。軸の入れ替えだけで済み、符号反転は要らない。逆変換は同じ式である（`U` は自分自身が逆行列＝対合）。

```csharp
// ENU (x=east, y=north, z=up, right-handed) -> Unity (left-handed, Y up)
// ENU（x=東・y=北・z=上、右手系）→ Unity（左手系・Y 上）
static Vector3 EnuToUnity(float[] p) => new Vector3(p[0], p[2], p[1]);

// Unity -> ENU. The same swap, because the map is its own inverse.
// Unity → ENU。対合なので同じ入れ替えでよい。
static float[] UnityToEnu(Vector3 v) => new[] { v.x, v.z, v.y };
```

### 6.2 回転

回転は、基底の取り替えによる**共役** `R_unity = U · R_enu · Uᵀ` で決まる。これを軸と角で読み直すと、軸は位置と同じ入れ替え（y↔z）に従い、向きは `det(U) = −1` のため反転する。

| ENU の回転 | Unity での軸（右手則で読んだ場合） |
|-----------|--------------------------------|
| roll（+x まわり） | **−x** まわり |
| pitch（+y まわり） | **−z** まわり |
| yaw（+z まわり） | **−y** まわり |

`Quaternion.AngleAxis` でこの表のとおりに組み立てるには、**角に負号を付ける**（軸は正のまま `Vector3.right`／`forward`／`up` を渡す）。`AngleAxis(−θ, +軸)` は `AngleAxis(θ, −軸)` と同じ回転なので、これが上の表の「負の軸まわり」そのものである。

Unity の `AngleAxis` は、**渡した軸について右ねじ**（軸の正の側から見て反時計回り）に回す。左手系なのは座標系のほうであり、利き手の違いを担うのは §6.1 の y↔z の入れ替えである。`AngleAxis` 自身が符号反転を含むわけではないので、負号は明示的に書く必要がある。

```csharp
// ENU intrinsic yaw -> pitch -> roll, expressed in Unity's frame.
// ENU の内因性 yaw → pitch → roll を、Unity の座標系で組み立てる。
//
// Why the minus signs: Unity's AngleAxis turns right-handed about the axis it
// is given, so a positive ENU angle must be written as a negative angle about
// the positive Unity axis -- which is the "about -x / -z / -y" of the table.
// The handedness change itself is carried by the y-z swap of 6.1, not by
// AngleAxis, so the sign flip has to be written out here.
// 負号を付ける理由: Unity の AngleAxis は渡した軸について右ねじに回る。よって
// ENU の正の角は、Unity の正の軸まわりの負の角として書く必要がある。これが表の
// 「−x／−z／−y まわり」である。利き手の違いを担うのは §6.1 の y↔z の入れ替えで
// あって AngleAxis ではないので、符号反転はここで明示する。
static Quaternion EnuRotationToUnity(float[] rotationDeg)
{
    float roll = rotationDeg[0], pitch = rotationDeg[1], yaw = rotationDeg[2];
    return Quaternion.AngleAxis(-yaw,   Vector3.up)        // ENU +z -> Unity -y
         * Quaternion.AngleAxis(-pitch, Vector3.forward)   // ENU +y -> Unity -z
         * Quaternion.AngleAxis(-roll,  Vector3.right);    // ENU +x -> Unity -x
}
```

**検算（Unity 6000.6.2f1 で実測）:**

`EnuRotationToUnity` が作った回転で ENU の基底ベクトルを回し、§6.1 の入れ替えで ENU に読み戻したものを、ENU で直に評価した `R = Rz(yaw)·Ry(pitch)·Rx(roll)` と成分ごとに比べた。roll・pitch・yaw を単独で振った場合と 3 つを混ぜた場合の両方で、**最大誤差 0.0**（負号を付けない式では **2.0**、すなわち完全な鏡像になる）。単独の角では次のようになる。

| ENU の回転 | ENU での効果 | 実測した Unity の `ToAngleAxis` |
|-----------|-------------|-------------------------------|
| roll = +90° | 北 `(0,1,0)` → 上 `(0,0,1)` | 角 +90°、軸 `(−1, 0, 0)` |
| pitch = +90° | 東 `(1,0,0)` → 下 `(0,0,−1)` | 角 +90°、軸 `(0, 0, −1)` |
| yaw = +90° | 東 `(1,0,0)` → 北 `(0,1,0)` | 角 +90°、軸 `(0, −1, 0)` |

`ToAngleAxis` が返す軸が上の表の「−x／−z／−y」と一致している。この 3 件は EditMode 試験の `RollTurnsTheEnuNorthAxisUpward`・`PitchTipsTheEnuEastAxisDownward`・`YawTurnsTheEnuEastAxisCounterClockwise`、混合の照合は `RotationIsAppliedYawThenPitchThenRoll` が守る。

> **訂正（2026-09-20）:** 本節は当初「`AngleAxis` は左手系なので角に負号を付けてはならない」と述べ、負号の無いコード例を載せていた。Unity C# 側の実装時に実測したところ、`AngleAxis(+90°, Vector3.up)` は Unity の前方 `+z` を右 `+x` へ送る（＝ENU の北を東へ送る＝ENU では上から見て時計回り＝yaw −90°）ことが分かり、負号が必要であると判明した。上の表（−x／−z／−y）は当初から正しく、誤っていたのは説明文とコード例のほうで、両者が互いに矛盾していた。

### 6.2.1 `spawn.yaw_deg` は 90° のずれを差し引く

上の `EnuRotationToUnity` は**障害物の向き**（`rotation_deg`）のための式である。`spawn.yaw_deg` は「どちらを向いて置くか」を表す**方位**であり、機体モデルの前方をどう定義したかが効くため、そのままでは使えない。

- `spawn.yaw_deg` の定義（§2.3）は **0 = 東（ENU +x）、90 = 北（ENU +y）**
- Unity の機体モデルの前方は慣例どおり **+z**。`U` で ENU に写すと **+y＝北**である

つまり yaw=0 のとき、機体モデルの素の前方（北）と、求める向き（東）が 90° ずれている。§6.2 と同じく ENU の方位は Unity では負号が付くので、角は `90 − yaw_deg` になる（`yaw_deg − 90` ではない）。

```csharp
// The vehicle model's forward (+z in Unity) maps to ENU north, but yaw_deg = 0
// means east. The heading is negated for the same reason as 6.2 -- AngleAxis
// turns right-handed about +y, which is clockwise in ENU -- so the angle is
// the 90 deg offset MINUS the heading.
// 機体モデルの前方（Unity +z）は ENU の北に当たるが、yaw_deg = 0 は東を指す。
// §6.2 と同じ理由（AngleAxis は +y について右ねじ＝ENU では時計回り）で方位に
// 負号が付くので、角は 90 度のずれから方位を引いたものになる。
static Quaternion SpawnYawToUnity(float yawDeg)
    => Quaternion.AngleAxis(90.0f - yawDeg, Vector3.up);
```

**検算（Unity 6000.6.2f1 で実測）:**

| `yaw_deg` | `AngleAxis` の角 | 機体の前方（ENU） | 意味 |
|-----------|-----------------|------------------|------|
| 0 | +90° | `(1, 0, 0)` | 東 ✓ |
| 45 | +45° | `(0.707, 0.707, 0)` | 北東 ✓ |
| 90 | 0° | `(0, 1, 0)` | 北 ✓ |
| 180 | −90° | `(−1, 0, 0)` | 西 ✓ |
| −90 | +180° | `(0, −1, 0)` | 南 ✓ |
| −135 | +225° | `(−0.707, −0.707, 0)` | 南西 ✓ |

機体モデルの前方を +z 以外にした場合は、この 90 を対応する値に直すこと。

> **訂正（2026-09-20）:** 本節は当初 `AngleAxis(yawDeg − 90, up)` と述べていたが、その式では `yaw_deg = 0` が**西**を向き、同じ節の検算の表（0 = 東）と矛盾していた。Unity C# 側の実装時の実測で判明した。同梱 6 空間の出発点はすべて `yaw_deg` が ±90 で、この 2 つの値では両式が同じ結果になるため、同梱の空間だけでは誤りが表に出なかった。そこで上の表に ±90 以外の値（0・45・180・−135）を加え、EditMode 試験 `SpawnYawPointsTheVehicleWhereTheFormatSays` がこの 6 件すべてを確かめる。

### 6.3 `frames_unity.hpp` との関係

`simulator/sils/frames/frames_unity.hpp` は**別の写像**である。あちらは StampFly の **NED/FRD** と Unity の間を変換するもので（`coordinate_frames.md` §7）、この空間ファイルの **ENU** とは異なる。両者の関係は次のとおりである。

```
  ENU (x=東, y=北, z=上)  ──── U ────>  Unity
        │                                  ▲
        │ W（ENU ↔ NED）                   │ M（NED ↔ Unity, frames_unity.hpp）
        ▼                                  │
  NED (x=北, y=東, z=下) ───────────────────┘

  W = [[0,1,0],[1,0,0],[0,0,−1]]   （coordinate_frames.md §3.1、det = +1）
  M = [[0,1,0],[0,0,−1],[1,0,0]]   （coordinate_frames.md §7.2、det = −1）
  U = M · W = [[1,0,0],[0,0,1],[0,1,0]]                （det = −1）
```

検算: `W` は `(n,e,d) = (enu.y, enu.x, −enu.z)`、`M` は `(u.x,u.y,u.z) = (v.y, −v.z, v.x)`。合わせると `u = (enu.x, enu.z, enu.y)` となり、§6.1 の入れ替えと一致する。✓

**役割分担:** C ABI を挟んだ機体の状態（位置・姿勢・速度・力）の変換は C++ の `frames_unity.hpp` が一手に引き受ける。C# 側が行う座標の仕事は、この空間ファイルと Unity の変換（§6.1・§6.2）**だけ**である。C# で `frames_unity.hpp` を再実装してはならない。

## 7. 検査の使い方

検査は Python 標準ライブラリだけで動く（`jsonschema` パッケージに依存しない）。

```bash
# sf コマンドから（利用者向け）
sf unity world validate

# 直接実行（開発時）
python3 tools/unity_world/validate.py simulator/unity/Assets/StampFly/Worlds/*.world.json
```

誤りがあれば終了コード 1 を返す。出力は 1 行 1 件で、`OK` / `WARN` / `ERROR` のいずれかで始まる。

### 7.1 検査する内容

| 段 | 内容 |
|----|------|
| 形式 | 鍵の有無・未知の鍵・型・数値の範囲・列挙の値・色の書式・種類ごとの必須項目（`thickness`・`segments`） |
| 意味 | id の重複／障害物が部屋の外／障害物どうしの大きな重なり（**警告**）／出発点が障害物の中・部屋の外／出発点の真上の離陸の余地 0.6 m ／`gate`・`ring`・`tunnel` の開口が機体（幅 0.0816 m）より狭い／寸法が 0 以下 |

**誤りと警告の違い:** 誤りは読み込みを止める。警告は読み込めるが設計を見直したほうがよい状態を指す（障害物どうしの深い重なり、機体がかろうじて通る程度の開口）。

### 7.2 Python から呼ぶ

```python
from unity_world import validate_file, validate_file_detailed, list_worlds

errors = validate_file(path)                 # 誤りの一覧。空なら合格
detail = validate_file_detailed(path)        # {"errors": [...], "warnings": [...]}
worlds = list_worlds(worlds_dir)             # name / path / description / obstacle_count
```

## 8. 空間を足す手順

1. `simulator/unity/Assets/StampFly/Worlds/<name>.world.json` を作る。`name` はファイル名の幹と一致させる
2. `description` に日本語と英語で**ねらい**を 1 行ずつ書く（何を練習・観察する空間なのか）
3. 部屋の大きさは、室内で飛ばす小型機（0.08 m 角・37 g）に合う数 m 四方にする
4. `python3 tools/unity_world/validate.py <file>` を**誤りも警告も無く**通す
5. 同梱の空間として足すなら、`tools/unity_world/tests/test_validate.py` の `SHIPPED_WORLDS` に名前を足す
6. `.meta` ファイルは**作らない**（Unity エディタが自分で作る）

### 8.1 同梱の 6 空間

| 名前 | ねらい | 主な障害物 |
|------|--------|-----------|
| `empty_room` | 基本の操縦とホバリング | なし |
| `pillar_forest` | 前向き ToF での検知と回避。間隔 0.54 m、通り道が複数 | `pillar` × 39 |
| `gate_course` | ゲートと輪を順にくぐる周回コース。番号と色で順番を示す | `gate`・`ring`・`pad` |
| `corridor_tunnel` | 壁の近くでの位置保持。幅 0.70 m の L 字の廊下とトンネル | `wall`・`tunnel` |
| `stepped_floor` | 下向き ToF が急変するときの高度保持。着陸用の `pad` あり | `step`・`ramp`・`box`・`table`・`pad` |
| `featureless_floor` | フローが効きにくい条件。模様のある `pad` の上だけ位置保持が効く | `pad`・`box` |

## 9. Unity の C# 側が守る約束

### 9.1 `JsonUtility` で読めるようにした制約

Unity の `JsonUtility` は、辞書・多態・可変の型・`null` の区別を扱えない。そのため形式を次のように制限している。

| 制約 | 理由 |
|------|------|
| 障害物は種類ごとの入れ子を持たない | `JsonUtility` は多態を扱えないので、全種類を 1 つのクラスで受ける |
| 辞書を使わない | `JsonUtility` は `Dictionary` を読めない |
| 数値は固定長の配列（3 つ）で表す | `Vector3` は直接読めないが `float[]` なら読める |
| `thickness` と `segments` は任意項目 | 省略時は `JsonUtility` が 0 を入れるので、C# 側は「0 なら既定値」として扱う（`segments` の既定は 24、`thickness` が 0 になるのは `thickness` を持たない種類のときだけ） |

### 9.2 クラスの形（案）

```csharp
using System;
using UnityEngine;

[Serializable] public class WorldUnits  { public string length; public string angle; }
[Serializable] public class WorldText   { public string ja; public string en; }

[Serializable] public class WorldRoom {
    public float[] size;        // [x_east, y_north, z_up], metres / 内寸
    public bool walls;
    public bool ceiling;
}

[Serializable] public class WorldFloor {
    public string pattern;      // checker | grid | plain | noise | stripes
    public float pitch;
    public string[] colors;     // two "#rrggbb" / 2 色
    public float flowQuality;   // see the note on naming below / 命名は下記
}

[Serializable] public class WorldSpawn {
    public float[] position;    // [x, y, z] of the centre of gravity / 重心
    public float yawDeg;        // CCW from +x seen from above / 上から見て +x から反時計回り
}

[Serializable] public class WorldObstacle {
    public string id;
    public string type;         // one of the ten kinds / 10 種のいずれか
    public float[] position;    // the type's origin / その種類の原点
    public float[] rotationDeg; // [roll, pitch, yaw], intrinsic yaw->pitch->roll
    public float[] size;        // meaning depends on `type` / 意味は type ごと
    public float thickness;     // gate | ring | tunnel only; 0 otherwise
    public int segments;        // ring only; 0 means the default of 24
    public string color;        // "#rrggbb"
    public float flowQuality;   // 0 means "absent" -> use the default of 0.8
}

[Serializable] public class WorldFile {
    public string format;       // must be "stampfly-world" / 目印
    public int version;         // must be 1
    public WorldUnits units;
    public string frame;        // must be "ENU"
    public string name;
    public WorldText description;
    public WorldRoom room;
    public WorldFloor floor;
    public string light;        // day | indoor | dim; empty -> "indoor"
    public WorldSpawn spawn;
    public WorldObstacle[] obstacles;
}
```

**命名について:** JSON の鍵は下線区切り（`flow_quality`・`rotation_deg`・`yaw_deg`）で、C# の慣習（`flowQuality`）と違う。`JsonUtility` は鍵名をそのまま見るため、次のどちらかを選ぶ。

1. C# のフィールド名を JSON に合わせる（`public float flow_quality;`）。単純だが C# らしくない
2. 読み込む直前に鍵名を書き換える薄い層を置く。C# 側が読みやすくなる

どちらでもよいが、**選んだ方を `WorldFile` の実装のコメントに書いておくこと**。

### 9.3 読み込み側が守ること

| 約束 | 内容 |
|------|------|
| 目印の確認 | `format != "stampfly-world"` または `version != 1` なら読み込みを断る |
| 座標系の確認 | `frame != "ENU"` なら読み込みを断る（別の座標系のファイルを黙って誤解釈しない） |
| 変換 | 位置は §6.1、障害物の回転は §6.2、`spawn.yaw_deg` は §6.2.1（−90 のずれが要る）。**C# が持つ座標の仕事はこれだけ**で、`frames_unity.hpp` を再実装しない |
| 原点 | 種類ごとに §3.1 の原点に合わせて `GameObject` を置く。`Transform` の原点（多くのプリミティブは形の中心）とずれる種類があるので、生成時に寄せる |
| 省略時の既定 | `light` が空なら `indoor`、`segments` が 0 なら 24、`flow_quality` が 0 なら 0.8（ただし床の `flow_quality` は 0 が意味を持つ値なので、床では既定を当てない） |
| 保存 | 書き出すときも ENU・m・度のまま。Unity の座標を書き出さない |
| 往復 | 読み込み → 保存で内容が一致すること（検査が通る状態を保つ） |

---

<a id="english"></a>

# StampFly World File Format

## 1. Overview

### About This Document

This document defines the **world file** format (`*.world.json`) that the Unity simulator loads. One JSON file describes one flight space with its obstacles, and the shipped presets use exactly the same format as the files users save.

The **specification** is [`world.schema.json`](world.schema.json) (JSON Schema). This document explains it and adds the agreements a schema cannot express: what `size` means for each obstacle type, where each type's origin sits, and how the coordinates convert. The **checking implementation** is `tools/unity_world/validate.py`, and `tools/unity_world/tests/test_validate.py` keeps the schema and the implementation from drifting apart.

### Target Audience

| Reader | Sections |
|--------|----------|
| Building or adding a world | §2 structure, §3 obstacle types, §7 checking, §8 adding a world |
| Implementing the Unity C# side | §3 origins and rotation, §5 frame, §6 conversion, §9 JsonUtility classes |
| Reading these files from another tool (MuJoCo, Genesis) | §5 frame, §3 obstacle types |

### Related Documents

| Document | Content |
|----------|---------|
| [`world.schema.json`](world.schema.json) | The specification (JSON Schema) |
| [`../../../docs/plans/unity-simulator.md`](../../../docs/plans/unity-simulator.md) | The Unity simulator plan (§4 world files, §5 stages 4 and 5) |
| [`../../../docs/architecture/coordinate-systems.md`](../../../docs/architecture/coordinate-systems.md) | Repository-wide coordinate systems |
| [`../../sils/docs/coordinate_frames.md`](../../sils/docs/coordinate_frames.md) | SILS frames (§7 covers the Unity mapping) |

## 2. Structure

A world file has the following members. All but `description` and `light` are required.

| Member | Type | Content |
|--------|------|---------|
| `format` | string | Format marker; always `"stampfly-world"` |
| `version` | integer | Format version; only `1` exists |
| `units` | object | Fixed `{"length": "m", "angle": "deg"}`, stated so other tools need not assume |
| `frame` | string | Always `"ENU"` (x east, y north, z up, right-handed). See §5 |
| `name` | string | World identifier: lower-case letters, digits and underscore. Matches the file stem for shipped worlds |
| `description` | object | `{"ja": "...", "en": "..."}`, one line on the world's purpose. Optional, but always written for shipped worlds |
| `room` | object | The room. §2.1 |
| `floor` | object | Floor appearance and flow quality. §2.2 |
| `light` | string | `day` / `indoor` / `dim`; defaults to `indoor` |
| `spawn` | object | Where the vehicle starts. §2.3 |
| `obstacles` | array | The obstacles; may be empty. §3 |

### 2.1 `room`

The room is a box centred on the origin, and **its floor is the plane z=0**.

| Member | Type | Content |
|--------|------|---------|
| `size` | 3 numbers | Interior extent `[x_east, y_north, z_up]` in metres |
| `walls` | boolean | Whether the four side walls are solid (they collide and reflect ToF) |
| `ceiling` | boolean | Whether the ceiling is solid |

```
Interior:
  x : -size[0]/2 .. +size[0]/2
  y : -size[1]/2 .. +size[1]/2
  z :           0 ..  size[2]
```

### 2.2 `floor`

| Member | Type | Content |
|--------|------|---------|
| `pattern` | string | `checker` / `grid` / `plain` / `noise` / `stripes` |
| `pitch` | number | Pattern repeat length in metres. Ignored for `plain` but still required |
| `colors` | 2 strings | The pattern's two colours, `"#rrggbb"`. Only the first is used for `plain` |
| `flow_quality` | number 0..1 | See §4 |

### 2.3 `spawn`

| Member | Type | Content |
|--------|------|---------|
| `position` | 3 numbers | Position of the vehicle's **centre of gravity** `[x, y, z]` in metres |
| `yaw_deg` | number | Heading, degrees **counter-clockwise** from +x (east) seen from above. 0 faces east, 90 faces north |

The spawn point needs **0.6 m of clear height** directly above it, which the checker enforces. The downward ToF has a 30 mm blind zone (`stampfly-tof-30mm-blind-zone`), and this figure allows the vehicle to climb out of it and settle.

## 3. Obstacle Types

Obstacles carry only **flat, common members**: no per-type nesting, no dictionaries and no variable types, because Unity's `JsonUtility` cannot read those (see §9).

| Member | Type | Required | Content |
|--------|------|----------|---------|
| `id` | string | yes | Unique within the file; lower-case letters, digits and underscore |
| `type` | string | yes | One of the ten kinds in §3.1 |
| `position` | 3 numbers | yes | Position of **the type's origin** `[x, y, z]` in metres. §3.1 |
| `rotation_deg` | 3 numbers | yes | `[roll, pitch, yaw]` in degrees. §3.2 |
| `size` | 3 numbers | yes | Three lengths in metres, meaning set by the type. §3.1 |
| `thickness` | number | `gate`, `ring`, `tunnel` only | Frame or tube wall thickness in metres. **Must be absent** on other types |
| `segments` | integer | `ring` only, optional | Segments approximating the circle (6..128, default 24). **Must be absent** on other types |
| `color` | string | yes | `"#rrggbb"` |
| `flow_quality` | number 0..1 | optional | Flow quality of the upward-facing surface; defaults to 0.8. §4 |

### 3.1 What `size` Means, and Where the Origin Sits

The **origin** is the point `position` refers to. It is the centre of the bottom face for some types and the centre of the shape for others, so it is pinned down here.

| Type | `size[0]` (x) | `size[1]` (y) | `size[2]` (z) | Origin | `thickness` |
|------|---------------|---------------|---------------|--------|-------------|
| `box` | width | depth | height | centre of the bottom face | not allowed |
| `pillar` | width | depth | height | centre of the bottom face | not allowed |
| `wall` | length | thickness | height | centre of the bottom face | not allowed |
| `gate` | **opening** width | frame depth | **opening** height | centre of the opening's bottom edge, at floor level | required (frame thickness) |
| `ring` | **inner diameter** | inner diameter (equal to `size[0]`) | same | **centre of the opening** | required (ring thickness) |
| `tunnel` | **opening** width | tube length | **opening** height | centre of the entry opening's bottom edge | required (tube wall thickness) |
| `table` | top width | top depth | height of the **top surface** | centre of the footprint | not allowed |
| `step` | width | depth | height | centre of the bottom face | not allowed |
| `ramp` | width | run (along +y) | rise | centre of the **low edge**, at the bottom | not allowed |
| `pad` | width | depth | thickness | centre of the bottom face | not allowed |

**Points to note:**

- For `gate` and `tunnel`, `size` is the **opening**, not the outer extent. The outer extent is the opening plus `thickness`
- A `ring` is round, so `size[0]` and `size[1]` must be equal (the checker enforces this). The ring lies in the **x-z plane** and is `thickness` deep along y, so with no rotation it is flown through **heading north**
- A `ring` is approximated by `segments` straight bars **inscribed in the circle of radius `size[0]/2 + thickness`** (each bar is that outer circle's chord, `2·(size[0]/2 + thickness)·sin(π/segments)` long). The bounds the checker uses are the box around that outer circle, so **circumscribing** the polygon about it would push the bars' corners outside that box. Inscribing makes the extent match the checker for any segment count while leaving the inner hole clear
- A `tunnel` is two side walls and a roof, with **no floor**. The tube is meant to rest on the room floor, and a second floor inside it would be a step the vehicle has to climb (`size[2]`, the opening height, is measured from the room floor)
- A `table`'s `size[2]` is the height of the **top surface**, not the thickness of the top. Unity draws the legs from the top's corners down to the floor
- A `ramp` rises toward +y. Use the yaw of `rotation_deg` to point it elsewhere

#### Diagram: bottom-face origin (`box`, `pillar`, `wall`, `step`, `pad`)

```
        +---------------+  -+-
        |               |   | size[2] (height)
        |               |   |
        +-------o-------+  -+-
                ^
          origin = centre of the bottom face (z at floor level)
        |--- size[0] ---|
```

#### Diagram: `gate` (origin at the centre of the opening's bottom edge)

```
      +-+-------------+-+  -+- <- top of the frame (thickness)
      | |             | |   |
      | |   opening   | |   | size[2] (opening height)
      | |             | |   |
      +-+------o------+-+  -+-
       ^       ^       ^
  thickness  origin  thickness
        |- size[0] -|        <- opening width (inner face to inner face)

  Depth along y is size[1], half on each side of the origin
```

#### Diagram: `ring` (origin at the centre of the opening)

```
           .-----------.    -+-
         .-|           |-.   |
         | |           | |   | size[0] = inner diameter
         | |     o     | |   |   o = origin (centre of the opening)
         | |           | |   |
         '-|           |-'   |
           '-----------'    -+-
          |- thickness -| is the ring's thickness
   The ring lies in the x-z plane, thickness deep along y
```

#### Diagram: `tunnel` (origin at the centre of the entry opening's bottom edge)

```
   Seen from the side (it runs toward +y)

     +-----------------------+ -+- thickness
     |                       |
     |        opening        |  size[2]
     |                       |
     o-----------------------+ -+-
     ^
   origin = centre of the entry opening's bottom edge
     |------  size[1] -------|  <- tube length
```

#### Diagram: `table` (origin at the centre of the footprint; `size[2]` is the top surface height)

```
     +-------------------+  <- top (its upper face at z = size[2])
     |                   |
     '  |             |  '   <- legs (generated by Unity)
        |             |
    ----o------------------- floor
        ^
   origin = centre of the footprint (z at floor level)
```

#### Diagram: `ramp` (origin at the centre of the low edge; rises toward +y)

```
   Seen from the side
                      /|  -+-
                    /  |   | size[2] (rise)
                  /    |   |
                o------+  -+-
                ^
        origin = centre of the low edge
                |- size[1] -|  <- run (along +y)
```

### 3.2 Rotation Order

`rotation_deg` is `[roll, pitch, yaw]` in degrees, applied **intrinsically** in this order:

```
  1. yaw   ... about +z (up)
  2. pitch ... about the +y axis produced by step 1
  3. roll  ... about the +x axis produced by step 2

  Rotation matrix: R = Rz(yaw) . Ry(pitch) . Rx(roll)
```

Each is positive **counter-clockwise seen from the positive axis** (the right-hand rule). Most obstacles need only yaw.

## 4. `flow_quality`

The optical-flow sensor (PMW3901) measures velocity by tracking the texture of the surface below. `flow_quality` expresses, from 0 to 1, how much texture that surface offers.

| Value | Meaning |
|-------|---------|
| 0.0 | No texture at all; velocity cannot be measured |
| around 0.3 | Sparse texture; SQUAL falls and position hold drifts |
| 0.8 to 1.0 | Ample texture; velocity is measured reliably |

It applies to the floor (`floor.flow_quality`) and to each obstacle's **upward-facing surface** (`obstacles[].flow_quality`). Unity takes the value of whichever surface the sensor's ray strikes and combines it with a distance falloff to produce SQUAL (plan §5, stage 5).

The shipped `featureless_floor` sets the floor to 0.05 so position hold drifts, then places high-`flow_quality` pads as islands, so the working and non-working conditions can be demonstrated side by side.

## 5. Why the Frame Is ENU

The world file's frame is fixed to **ENU** (x east, y north, z up, right-handed). Unity's left-handed Y-up frame **never appears in the file**.

| Reason | Explanation |
|--------|-------------|
| Other tools can read it | The MuJoCo world in `simulator/sils/` is ENU (`coordinate_frames.md` §2) and Genesis is Z-up, so the same world files can be read later without change |
| The left-handed frame stays contained | Under a left-handed map, polar and axial vectors take different signs (`coordinate_frames.md` §7.3). That difficulty stays inside Unity instead of leaking into the data |
| It reads naturally | "x east, y north, z up" matches a map, so the numbers alone convey the layout |
| One place to convert | It follows the SILS rule that frame conversion lives in one place. The only frame work left on the C# side is this file's conversion to Unity |

## 6. Converting into Unity

### 6.1 Position

From right-handed ENU to Unity's left-handed Y-up frame, **swap y and z**; nothing else.

```
  Unity.x =  ENU.x     (east -> Unity right)
  Unity.y =  ENU.z     (up   -> Unity up)
  Unity.z =  ENU.y     (north-> Unity forward)

  Matrix U = [[1, 0, 0],
              [0, 0, 1],
              [0, 1, 0]]     det(U) = -1 -> handedness flips
```

`det(U) = -1` is expected, since the map goes from a right-handed to a left-handed frame. Only an axis swap is needed; no sign changes. The inverse is the same expression, because `U` is its own inverse (an involution).

```csharp
// ENU (x=east, y=north, z=up, right-handed) -> Unity (left-handed, Y up)
static Vector3 EnuToUnity(float[] p) => new Vector3(p[0], p[2], p[1]);

// Unity -> ENU. The same swap, because the map is its own inverse.
static float[] UnityToEnu(Vector3 v) => new[] { v.x, v.z, v.y };
```

### 6.2 Rotation

Rotation is fixed by **conjugation** under the change of basis: `R_unity = U . R_enu . U^T`. Read back as an axis and an angle, the axis follows the same y-z swap as position, and its orientation flips because `det(U) = -1`.

| ENU rotation | Unity axis (read with the right-hand rule) |
|--------------|--------------------------------------------|
| roll (about +x) | about **-x** |
| pitch (about +y) | about **-z** |
| yaw (about +z) | about **-y** |

To build exactly that table with `Quaternion.AngleAxis`, **negate the angle** and pass the positive axis (`Vector3.right` / `forward` / `up`). `AngleAxis(-θ, +axis)` is the same rotation as `AngleAxis(θ, -axis)`, which is precisely the "about the negative axis" of the table.

Unity's `AngleAxis` turns **right-handed about the axis it is given** (counter-clockwise seen from the positive end of that axis). What is left-handed is the coordinate frame, and the handedness change is carried by the y-z swap of §6.1 — not by `AngleAxis` — so the sign flip has to be written out explicitly.

```csharp
// ENU intrinsic yaw -> pitch -> roll, expressed in Unity's frame.
//
// Why the minus signs: Unity's AngleAxis turns right-handed about the axis it
// is given, so a positive ENU angle must be written as a negative angle about
// the positive Unity axis -- which is the "about -x / -z / -y" of the table.
// The handedness change itself is carried by the y-z swap of 6.1, not by
// AngleAxis, so the sign flip has to be written out here.
static Quaternion EnuRotationToUnity(float[] rotationDeg)
{
    float roll = rotationDeg[0], pitch = rotationDeg[1], yaw = rotationDeg[2];
    return Quaternion.AngleAxis(-yaw,   Vector3.up)        // ENU +z -> Unity -y
         * Quaternion.AngleAxis(-pitch, Vector3.forward)   // ENU +y -> Unity -z
         * Quaternion.AngleAxis(-roll,  Vector3.right);    // ENU +x -> Unity -x
}
```

**Check (measured in Unity 6000.6.2f1):**

Each ENU basis vector was rotated by the quaternion `EnuRotationToUnity` returns, read back into ENU through the swap of §6.1, and compared component by component against `R = Rz(yaw) Ry(pitch) Rx(roll)` evaluated directly in ENU. For each angle alone and for the three combined, the **largest error is 0.0** (with the un-negated expression it is **2.0**, an exact mirror). For the single angles:

| ENU rotation | Effect in ENU | Measured Unity `ToAngleAxis` |
|--------------|---------------|------------------------------|
| roll = +90 | north `(0,1,0)` becomes up `(0,0,1)` | angle +90, axis `(-1, 0, 0)` |
| pitch = +90 | east `(1,0,0)` becomes down `(0,0,-1)` | angle +90, axis `(0, 0, -1)` |
| yaw = +90 | east `(1,0,0)` becomes north `(0,1,0)` | angle +90, axis `(0, -1, 0)` |

The axes `ToAngleAxis` reports match the table's -x / -z / -y. These three are held down by the EditMode tests `RollTurnsTheEnuNorthAxisUpward`, `PitchTipsTheEnuEastAxisDownward` and `YawTurnsTheEnuEastAxisCounterClockwise`; the combined comparison by `RotationIsAppliedYawThenPitchThenRoll`.

> **Correction (2026-09-20):** this section originally said "Unity's `AngleAxis` is left-handed, so do not negate the angle" and carried an un-negated code example. Measuring while implementing the Unity C# side showed that `AngleAxis(+90, Vector3.up)` sends Unity forward `+z` to right `+x` — ENU north to ENU east, a clockwise turn seen from above in ENU, i.e. a yaw of -90 — so the negation is required. The table above (-x / -z / -y) was correct from the start; the prose and the code example were wrong, and the two contradicted each other.

### 6.2.1 `spawn.yaw_deg` Subtracts the Heading from a 90 Degree Offset

The `EnuRotationToUnity` above is for an **obstacle's orientation** (`rotation_deg`). `spawn.yaw_deg` is a **heading** — which way the vehicle faces — and so depends on how the vehicle model's forward direction is defined. It cannot use that expression unchanged.

- `spawn.yaw_deg` is defined (§2.3) as **0 = east (ENU +x), 90 = north (ENU +y)**
- The vehicle model's forward is, by convention, **+z** in Unity, which maps through `U` to **ENU +y, i.e. north**

So at yaw = 0 the model's bare forward (north) and the required heading (east) differ by 90 degrees. As in §6.2 an ENU heading takes a minus sign in Unity, so the angle is `90 - yaw_deg`, not `yaw_deg - 90`.

```csharp
// The vehicle model's forward (+z in Unity) maps to ENU north, but yaw_deg = 0
// means east. The heading is negated for the same reason as 6.2 -- AngleAxis
// turns right-handed about +y, which is clockwise in ENU -- so the angle is
// the 90 deg offset MINUS the heading.
static Quaternion SpawnYawToUnity(float yawDeg)
    => Quaternion.AngleAxis(90.0f - yawDeg, Vector3.up);
```

**Check (measured in Unity 6000.6.2f1):**

| `yaw_deg` | `AngleAxis` angle | Vehicle forward (ENU) | Meaning |
|-----------|-------------------|-----------------------|---------|
| 0 | +90 | `(1, 0, 0)` | east, correct |
| 45 | +45 | `(0.707, 0.707, 0)` | north-east, correct |
| 90 | 0 | `(0, 1, 0)` | north, correct |
| 180 | -90 | `(-1, 0, 0)` | west, correct |
| -90 | +180 | `(0, -1, 0)` | south, correct |
| -135 | +225 | `(-0.707, -0.707, 0)` | south-west, correct |

If the vehicle model's forward is defined as something other than +z, adjust this 90 accordingly.

> **Correction (2026-09-20):** this section originally gave `AngleAxis(yawDeg - 90, up)`, under which `yaw_deg = 0` faces **west**, contradicting this section's own check table (0 = east). It was found by measurement while implementing the Unity C# side. Every shipped world spawns with `yaw_deg` at ±90, and the two expressions agree at exactly those two values, so the shipped worlds alone never exposed the error. The table above therefore now includes values away from ±90 (0, 45, 180, -135), and the EditMode test `SpawnYawPointsTheVehicleWhereTheFormatSays` checks all six.

### 6.3 Relation to `frames_unity.hpp`

`simulator/sils/frames/frames_unity.hpp` is a **different map**. It converts between StampFly's **NED/FRD** and Unity (`coordinate_frames.md` §7), not between this file's **ENU** and Unity. The two relate as follows.

```
  ENU (x=east, y=north, z=up) ---- U ---->  Unity
        |                                     ^
        | W (ENU <-> NED)                     | M (NED <-> Unity, frames_unity.hpp)
        v                                     |
  NED (x=north, y=east, z=down) --------------+

  W = [[0,1,0],[1,0,0],[0,0,-1]]   (coordinate_frames.md §3.1, det = +1)
  M = [[0,1,0],[0,0,-1],[1,0,0]]   (coordinate_frames.md §7.2, det = -1)
  U = M . W = [[1,0,0],[0,0,1],[0,1,0]]                (det = -1)
```

Check: `W` gives `(n,e,d) = (enu.y, enu.x, -enu.z)` and `M` gives `(u.x,u.y,u.z) = (v.y, -v.z, v.x)`. Composing them yields `u = (enu.x, enu.z, enu.y)`, which is the swap of §6.1. Correct.

**Division of labour:** all conversion of vehicle state across the C ABI (position, attitude, velocity, forces) is handled solely by `frames_unity.hpp` in C++. The **only** frame work on the C# side is this file's conversion to Unity (§6.1 and §6.2). Do not reimplement `frames_unity.hpp` in C#.

## 7. Using the Checker

The checker runs on the Python standard library alone (it does not depend on the `jsonschema` package).

```bash
# Through the sf command (for users)
sf unity world validate

# Directly (during development)
python3 tools/unity_world/validate.py simulator/unity/Assets/StampFly/Worlds/*.world.json
```

It exits with status 1 if there is any error. Output is one finding per line, beginning with `OK`, `WARN` or `ERROR`.

### 7.1 What Is Checked

| Layer | Content |
|-------|---------|
| Structure | Presence of keys, unknown keys, types, numeric ranges, enumerated values, colour format, per-type requirements (`thickness`, `segments`) |
| Meaning | Duplicate ids; obstacles outside the room; deep overlap between obstacles (**warning**); the spawn point inside an obstacle or outside the room; 0.6 m of takeoff clearance above the spawn point; `gate`, `ring` and `tunnel` openings narrower than the vehicle (0.0816 m wide); non-positive dimensions |

**Errors versus warnings:** an error stops the file from loading. A warning marks something that loads but deserves a second look (deep interpenetration, or an opening the vehicle only barely fits through).

### 7.2 Calling It from Python

```python
from unity_world import validate_file, validate_file_detailed, list_worlds

errors = validate_file(path)                 # errors only; empty means it passes
detail = validate_file_detailed(path)        # {"errors": [...], "warnings": [...]}
worlds = list_worlds(worlds_dir)             # name / path / description / obstacle_count
```

## 8. Adding a World

1. Create `simulator/unity/Assets/StampFly/Worlds/<name>.world.json`, with `name` matching the file stem
2. Write one line of **purpose** in `description`, in both Japanese and English (what the world is for practising or observing)
3. Size the room for an indoor micro vehicle (0.08 m across, 37 g): a few metres on a side
4. Make `python3 tools/unity_world/validate.py <file>` pass with **neither errors nor warnings**
5. If it ships with the simulator, add its name to `SHIPPED_WORLDS` in `tools/unity_world/tests/test_validate.py`
6. Do **not** create `.meta` files; the Unity editor generates them

### 8.1 The Six Shipped Worlds

| Name | Purpose | Main obstacles |
|------|---------|----------------|
| `empty_room` | Basic piloting and hovering | none |
| `pillar_forest` | Forward-ToF detection and avoidance; 0.54 m gaps with several routes | 39 `pillar` |
| `gate_course` | A circuit through gates and rings; numbering and colour show the order | `gate`, `ring`, `pad` |
| `corridor_tunnel` | Position hold near walls; a 0.70 m L-shaped corridor and a tunnel | `wall`, `tunnel` |
| `stepped_floor` | Altitude hold when the downward ToF jumps; includes a landing `pad` | `step`, `ramp`, `box`, `table`, `pad` |
| `featureless_floor` | Conditions where flow barely works; position hold holds only over the textured pads | `pad`, `box` |

## 9. What the Unity C# Side Must Honour

### 9.1 Constraints That Keep `JsonUtility` Working

Unity's `JsonUtility` handles neither dictionaries, nor polymorphism, nor variable types, nor a distinction for `null`. The format is therefore constrained:

| Constraint | Reason |
|------------|--------|
| Obstacles have no per-type nesting | `JsonUtility` cannot do polymorphism, so one class receives every type |
| No dictionaries | `JsonUtility` cannot read a `Dictionary` |
| Numbers come as fixed-length arrays of three | `Vector3` is not readable directly, but `float[]` is |
| `thickness` and `segments` are optional | `JsonUtility` supplies 0 when they are absent, so C# treats 0 as "use the default" (24 for `segments`; `thickness` is only 0 on types that never carry one) |

### 9.2 Proposed Class Shape

```csharp
using System;
using UnityEngine;

[Serializable] public class WorldUnits  { public string length; public string angle; }
[Serializable] public class WorldText   { public string ja; public string en; }

[Serializable] public class WorldRoom {
    public float[] size;        // [x_east, y_north, z_up], metres
    public bool walls;
    public bool ceiling;
}

[Serializable] public class WorldFloor {
    public string pattern;      // checker | grid | plain | noise | stripes
    public float pitch;
    public string[] colors;     // two "#rrggbb"
    public float flowQuality;   // see the note on naming below
}

[Serializable] public class WorldSpawn {
    public float[] position;    // [x, y, z] of the centre of gravity
    public float yawDeg;        // CCW from +x seen from above
}

[Serializable] public class WorldObstacle {
    public string id;
    public string type;         // one of the ten kinds
    public float[] position;    // the type's origin
    public float[] rotationDeg; // [roll, pitch, yaw], intrinsic yaw->pitch->roll
    public float[] size;        // meaning depends on `type`
    public float thickness;     // gate | ring | tunnel only; 0 otherwise
    public int segments;        // ring only; 0 means the default of 24
    public string color;        // "#rrggbb"
    public float flowQuality;   // 0 means "absent" -> use the default of 0.8
}

[Serializable] public class WorldFile {
    public string format;       // must be "stampfly-world"
    public int version;         // must be 1
    public WorldUnits units;
    public string frame;        // must be "ENU"
    public string name;
    public WorldText description;
    public WorldRoom room;
    public WorldFloor floor;
    public string light;        // day | indoor | dim; empty -> "indoor"
    public WorldSpawn spawn;
    public WorldObstacle[] obstacles;
}
```

**On naming:** the JSON keys use underscores (`flow_quality`, `rotation_deg`, `yaw_deg`), unlike the C# convention (`flowQuality`). `JsonUtility` matches key names literally, so choose one of:

1. Name the C# fields as the JSON does (`public float flow_quality;`) — simple, but not idiomatic C#
2. Put a thin layer in front of the load that rewrites the key names — more readable on the C# side

Either is acceptable, but **record the choice in a comment in the `WorldFile` implementation**.

### 9.3 Obligations of the Loader

| Obligation | Content |
|------------|---------|
| Check the marker | Refuse to load when `format != "stampfly-world"` or `version != 1` |
| Check the frame | Refuse to load when `frame != "ENU"`, rather than silently misreading a file in another frame |
| Convert | Position by §6.1, obstacle rotation by §6.2, `spawn.yaw_deg` by §6.2.1 (it needs the -90 offset). **This is all the frame work C# does**; do not reimplement `frames_unity.hpp` |
| Origins | Place each `GameObject` at the origin defined per type in §3.1. Some types differ from a `Transform`'s natural origin (most primitives are centred on the shape), so offset them at creation |
| Defaults | Empty `light` means `indoor`; `segments` of 0 means 24; `flow_quality` of 0 means 0.8 — except on the floor, where 0 is a meaningful value and no default applies |
| Saving | Write ENU, metres and degrees back out. Never write Unity coordinates |
| Round trip | Loading then saving must preserve the content, keeping the file valid |
