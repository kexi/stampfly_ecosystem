# SILS Coordinate Frames — MuJoCo ↔ StampFly
# SILS 座標系 — MuJoCo ↔ StampFly 対応

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。
>
> これは `simulator/sils/frames/` 変換モジュールの**仕様**であり、MuJoCo と StampFly の座標系の**唯一の対応表**です。SILS 内で座標変換を行うのはこの `frames` モジュールだけとし、他のどこでも座標変換をしないこと（旧 SILS が座標系不整合4箇所で崩れた反省）。

## 1. この文書について

### 決定（2026-05-31）

**MuJoCo は自然な座標系（Z 上）のまま使い、座標変換はソフト（`frames` モジュール）で行う。**

- **理由（ユーザー判断）**: 完成後にビューアで新ファームや制御理論を確認するとき、MuJoCo を StampFly に合わせて Z 下にすると、画面で**感覚と逆さまに動いて見えて混乱する**。ビューアの目的（視覚確認・レビュー動画）を守るため、MuJoCo は正立のままにする。
- 代償（座標変換が SILS に入る）は、**この1モジュールに閉じ込めて単体テストで保証**し、本文書で対応を明文化することで管理する。

### 決定（2026-05-31）— センサのドライバ正規化（論点2）

**各センサのチップ軸→機体軸の振り替え・符号合わせは各ドライバ（HAL）の中で行い、ドライバは機体 FRD の正しい物理量を返す。**

- **理由（ユーザー判断）**: StampFly は単一ハードで搭載向きは固定 → 一度正しく決めれば永久に正しい定数。上位（推定器/制御器を書く人）はチップ軸・搭載向き・符号に煩わされず、機体軸の綺麗な物理量だけを見ればよい。例: 加速度計は重力を Z 軸 **−9.8 m/s²** として返す（NED Z 下と整合。旧ファームの生値 +9.8＋`ba_z≈+2g` ハックは新ファームでは不要）。
- **含意**: チップ軸の混乱（右手/左手混在・鏡映・極性/軸性符号）は全部ドライバ内に閉じ込め、各ドライバで一度だけ実機検証。→ **SILS の sim ドライバは機体 FRD の量を直接返す**ので、この `frames` モジュールは**チップ軸の写像を持たず、§3 の世界/機体回転だけ**を担う。
- **旧ファーム不可侵**: `firmware/vehicle_old`(87飛行) と `firmware/vehicle` は別コードベース（別ドライバコピー・相互参照なし）。変更は vehicle のみ。

### 対象読者

- `frames` モジュールの実装者
- SILS の合成センサ・物理・真値を扱う者
- MuJoCo のビューアで挙動を確認する研究者・学生

---

## 2. 2つの座標系

### StampFly（ファーム本体が固定。動かせない）

本体ソースから裏取り済み（`eskf_core.cpp` / `imu_task.cpp` / `calibration.cpp`）。

| 区分 | 系 | 軸 |
|------|----|----|
| ワールド | **NED** | X=North, Y=East, **Z=Down**（下が正）。重力 `g_ned = [0,0,+9.81]` |
| 機体 | **FRD** | X=Forward, Y=Right, **Z=Down** |

```
        StampFly NED / FRD（Z 下）
              X(North/Forward)
               ↑
               │
   Y(East/Right)│
        ←───────┼
               ╱│
              ╱ │
   (Z=Down: 紙面の向こう・下向き)
```

- 姿勢 `attitude[w,x,y,z] = q_nb`（**body→NED**、Hamilton, R=to_dcm=R_nb）。
- 高度 = `−pos_z`（NED の z は下が正なので、上空で負）。

### MuJoCo（自然なまま。Z 上）

MuJoCo は右手系・Z 上が標準。本 SILS では MuJoCo ワールドを **ENU**、機体を **FLU** と定義する。

| 区分 | 系 | 軸 |
|------|----|----|
| ワールド | **ENU** | X=East, Y=North, **Z=Up**（上が正）。重力 `<option gravity="0 0 -9.81">` |
| 機体 | **FLU** | X=Forward, Y=Left, **Z=Up** |

```
        MuJoCo ENU / FLU（Z 上）
              Z(Up)
               ↑
               │
               │
               ┼───────→ Y(North/Forward は X… 下記注意)
              ╱
             ╱
          X(East)
```

- MuJoCo の機体姿勢 `framequat[w,x,y,z]` は **body(FLU)→world(ENU)**（Hamilton, body→world）。
- MJCF では機体を**プロペラ上向き（自然）**に記述する。

> **NED も ENU も右手系**なので、両者の間には正規回転（det=+1）が存在する。左手系問題は起きない。

---

## 3. 変換（これが `frames` モジュールの中身）

### 3.1 ワールドのベクトル変換 ENU ↔ NED

```
v_ned = (N, E, D) = ( enu.y,  enu.x,  −enu.z )      # XY 入替 ＋ Z 反転
v_enu = (E, N, U) = ( ned.y,  ned.x,  −ned.z )      # 同形（involution）
```

行列 `M_we = [[0,1,0],[1,0,0],[0,0,−1]]`（det=+1）。これは軸 `(1,1,0)/√2` 周りの **180° 回転**。

### 3.2 機体のベクトル変換 FLU ↔ FRD

```
v_frd = (F, R, D) = ( flu.x, −flu.y, −flu.z )       # Y, Z 反転（body X 周り 180°）
v_flu = (F, L, U) = ( frd.x, −frd.y, −frd.z )       # 同形（involution）
```

行列 `M_bf = [[1,0,0],[0,−1,0],[0,0,−1]]`（det=+1）。body X 軸周りの **180° 回転**。

### 3.3 姿勢クォータニオン MuJoCo framequat → StampFly q_nb

固定クォータニオン（単位）:

```
q_we = [0,  1/√2,  1/√2,  0]     # ENU→NED（§3.1 の M_we）
q_bf = [0,  1,     0,     0]     # FRD↔FLU（§3.2 の M_bf, body X 周り 180°）
```

変換:

```
q_nb = q_we ⊗ q_mj ⊗ q_bf        # ⊗ は Hamilton 積（左が後から作用）
       （q_mj = MuJoCo framequat = body(FLU)→world(ENU)）
```

導出: `v_frd →(M_bf)→ v_flu →(q_mj)→ v_enu →(M_we)→ v_ned` なので `R_nb = M_we · R(q_mj) · M_bf`。

**検算（水平・北向き）:** 機体が水平で北を向くとき、StampFly では body FRD が NED と一致 → `q_nb = 単位元`。このとき MuJoCo は `q_mj = [0.7071,0,0,0.7071]`（ENU で北=+Y を向く 90°ヨー）。上式に代入すると `q_nb = (−1,0,0,0)`＝単位元（`q` と `−q` は同一回転）。✓

> 逆変換が要る場合（StampFly→MuJoCo）は各回転の共役/転置を使う: `q_mj = q_we* ⊗ q_nb ⊗ q_bf*`、`v_enu = M_we^T·v_ned`、`v_flu = M_bf·v_frd`。

---

## 4. センサ・アクチュエータの対応

合成センサは**物理から第一原理で StampFly 系（FRD）で作る**。MuJoCo 内蔵センサ（`<accelerometer>`/`<gyro>`/`<rangefinder>`）は**検算リファレンス**として使う（規約差に注意）。

### 4.1 加速度計（ドライバ正規化後）

- **ドライバ出力（機体 FRD、重力を −9.8 として返す）**: `out_frd = R_bn·(a_world_ned − g_ned)`、`g_ned=[0,0,+9.81]`、`R_bn = inv_rotate(q_nb)`。水平静止で `[0,0,−9.81]`（重力が下軸 −9.8）。
- a_world_ned は機体 CG の運動加速度（MuJoCo の機体加速度を §3.1 で NED へ）。
- **MuJoCo `<accelerometer>` と直接一致**: MuJoCo の加速度計は a−g を site(FLU) 系で返す。FLU site・水平静止で `[0,0,+9.81]`、これを §3.2 で FRD にすると `[0,0,−9.81]` ＝ **ドライバ出力と符号まで一致**（新方針は MuJoCo の加速度計規約と整合 → 検算が素直）。
- 旧ファームの `ba_z≈+2g` 起動ハックは**新ファームでは不要**（ドライバが −9.8 を直接返す）。SILS の sim ドライバも `out_frd` を直接返す。

### 4.2 ジャイロ

- StampFly（body FRD, rad/s, [roll-x, pitch-y, yaw-z]）= MuJoCo `<gyro>`（FLU site の角速度）を §3.2 で FRD へ: `gyro_frd = M_bf·gyro_flu`。符号は RH about FRD で一致。

### 4.3 センサ系 → 機体系（ドライバ正規化, 論点2）

各センサのチップ軸→機体軸の振り替え（remap）・符号合わせは**各ドライバ（HAL）の中**で行い、ドライバは**機体 FRD の正しい物理量**を返す。

- チップ軸は右手/左手混在しうる → 写像は**鏡映（det=−1）にもなり得る**。極性ベクトル（加速度・速度）はそのまま、**軸性ベクトル（ジャイロ・磁気）は鏡映時に符号が1つ余分**（`a' = det(R)·R·a`）。これらは**ドライバ内で一度だけ正しく実装し、実機で検証**する固定値。
- 旧ファームは imu_task で remap していた（`body.x=sensor.y` 等）。**新ファームはこれをドライバへ移す**ので imu_task は薄くなる。
- → **SILS の sim ドライバは機体 FRD の量を直接返す**（チップ系の往復モデルは不要）。この `frames` モジュールはチップ remap を持たない。

### 4.4 ToF / Baro / Flow / Mag

| センサ | 規約 | SILS 合成 |
|--------|------|---------|
| ToF（下向き距離 m） | `pos_z = −height`、`height = distance·cosR·cosP` | `distance = −pos_z/(cosR·cosP)`。MuJoCo rangefinder を body **+Z（FRD 下）**に向けると自然 |
| Baro（高度 m, 上正） | `altitude = −pos_z` | `altitude = −pos_z` ＋ノイズ |
| Flow（生 dx,dy counts） | **body remap なし**。dx=前, dy=右 | `dx ∝ (vx_body/height)·dt/flow_rad_per_pixel`（+ジャイロ誘起）, dy 同様。`flow_rad_per_pixel=0.00222` |
| Mag（既定OFF, body µT） | ref=`{20,0,40}` NED | body 磁場 = `R_bn·mag_ref` |

### 4.5 ミキサー → 物理（モータ力の適用）

- `actuator_motor.duty[4]`（0..1）を読み、per-motor 推力 `T_i = k_thrust·duty_i²`（k_thrust=0.168 N）。
- 推力は機体を**上に押す** = FRD で `−Z` 方向（上＝−Z）。各モータ位置（FRD: ±0.023m, CG上 −0.005m）に力 `[0,0,−T_i]` を、ヨー反トルク `τ_yaw,i = ±κ·T_i`（κ=4.10e-3, CCW=M1/M3。2026-08-03改定——Cq/Ctのコーストダウン実測・確定値÷Ct暫定採用値。2026-07-17実測反映時の旧値0.00971、2026-07-17〜2026-08-02は撤回済みthrust stand Ct由来の中間値κを採用していた）を与える。
- 機体 FRD の力・トルクを §3.2 で MuJoCo FLU に直して適用: `F_flu = M_bf·F_frd`（推力 −Z_frd → +Z_flu＝上、整合）。

---

## 5. 検証（`frames` 単体テスト・正準ケース）

1. **水平静止 → 加速度計のドライバ出力 `out_frd = [0,0,−9.81]`（重力が下軸 −9.8）、ジャイロ `[0,0,0]`**（最重要）。
2. **姿勢往復**: 水平北向きで `q_nb = 単位元`（±）。任意姿勢で MuJoCo→StampFly→（逆）が元に戻る。
3. **固定回転の性質**: `M_we`, `M_bf` が det=+1・involution。`q_we ⊗ q_we* = 単位`。
4. **軸符号**: 純ロール/ピッチ/ヨー角速度 → 対応軸だけ正符号で立つ。
5. **速度往復**: +X 前進速度(NED) ↔ MuJoCo(ENU) 往復一致。
6. **重力の向き**: 機体を前方へ θ 傾ける → 合成加速度計 X が想定符号で現れる。
7. **MuJoCo 内蔵センサとの一致**: §4.1/4.2 の規約差を補正後、内蔵 `<accelerometer>`/`<gyro>` と合成値が一致。

---

## 6. 実装

- 変換は `simulator/sils/frames/`（`frames.hpp` / `frames.cpp`）に集約。`sf_math`（Vec3/Quat, Hamilton）を共有して本体と同じクォータニオン規約を使う。
- 単体テスト `simulator/sils/frames/frames_test.cpp`（§5 の正準ケース）。
- **SILS の他のどこでも座標変換をしない。** 物理・合成センサ・真値は全てこのモジュール経由。

---

## 7. Unity 版シミュレータとの対応

Unity 版シミュレータ（`docs/plans/unity-simulator.md`）が使う座標変換。MuJoCo との対応（§2〜§6）とは別の写像で、実装は `simulator/sils/frames/frames_unity.hpp`（ヘッダのみ・MuJoCo 非依存）にある。

### 7.1 Unity の規約

| 区分 | 規約 |
|------|------|
| 利き手 | **左手系**（MuJoCo・StampFly はどちらも右手系） |
| 上方向 | **Y 軸**が上 |
| 単位 | m |
| クォータニオン | 成分の順が **x, y, z, w**（`sf::math::Quat` は w, x, y, z） |
| `Rigidbody.angularVelocity` | **世界系**の角速度（機体系ではない） |

### 7.2 軸の対応

世界系（NED ↔ Unity の世界）も機体系（FRD ↔ Unity の機体）も、同じ 1 つの対応を使う。

```
  北 / 前   →  Unity +Z
  東 / 右   →  Unity +X
  下        →  Unity −Y
```

成分で書くと、StampFly の (n, e, d) を Unity の成分へ送る行列は次のとおり。

```
M = [[0, 1,  0],
     [0, 0, −1],
     [1, 0,  0]]           u = M·v,   v = Mᵀ·u
```

`M` は直交（`M·Mᵀ = I`）だが **`det(M) = −1`**。回転ではなく**利き手の反転**である。§3.1・§3.2 の MuJoCo 対応が `det = +1`（純粋な回転）だったのと、ここが決定的に違う。

### 7.3 極性ベクトルと軸性ベクトルで符号が違う理由

`det(M) = −1` のため、量の種類で扱いが分かれる。

| 種類 | 量の例 | Unity へ | Unity から |
|------|--------|---------|-----------|
| **極性ベクトル** | 位置・速度・力・加速度計の測定値 | `u = M·v = (v.y, −v.z, v.x)` | `v = Mᵀ·u = (u.z, u.x, −u.y)` |
| **軸性ベクトル** | 角速度・トルク | `u = det(M)·M·v = (−w.y, w.z, −w.x)` | `v = det(M)·Mᵀ·u = (−u.z, −u.x, u.y)` |

極性ベクトルは素の成分の組なので、そのまま写る。軸性ベクトルは外積で作られ、外積の向きは系の利き手で決まるため、`det = −1` の写像では符号が 1 つ余分に付く。言い換えると、右手則で反時計回りの同じ回転が Unity の左手則では時計回りになるので、同じ運動を表すには軸を反転させる必要がある。

### 7.4 姿勢クォータニオン

2 つの回転行列が一致する条件 `R_unity = M·R(q_nb)·Mᵀ` で決まる。

```
q_nb(w,x,y,z) → Unity(x,y,z,w) = (−q.y,  q.z, −q.x,  q.w)
Unity(x,y,z,w) → q_nb(w,x,y,z) = ( u.w, −u.z, −u.x,  u.y)
```

Unity の左手則と `det = −1` の反転が打ち消し合うため、得られた Unity のクォータニオンは通常の Hamilton の式でそのまま読める（余分な共役は不要）。

**検算（符号の具体例）:**

| 事例 | StampFly | Unity |
|------|----------|-------|
| 右ヨー（機首が右） | `q_nb = (cos, 0, 0, sin)` | +Y まわりの回転 |
| 右ロール | 機体レート `+ωx`（FRD 前） | `−Z` まわり |
| 機首上げ | 機体レート `+ωy`（FRD 右） | `−X` まわり |
| 右ヨーのレート | 機体レート `+ωz`（FRD 下） | `+Y` まわり |
| 静止して接地 | 加速度計 `[0, 0, −9.81]`（FRD） | `[0, +9.81, 0]`（上向き） |

### 7.5 C# 側との取り決め

C ABI は Unity の生の規約のまま双方向に受け渡し、**変換は `frames_unity.hpp` だけで行う**。C# 側はこれを再実装しない（「座標変換は 1 か所」という SILS の方針。§1 を参照）。

- `Rigidbody` の値（`position`・`rotation`・`linearVelocity`・`angularVelocity`）をそのまま渡す。符号反転も軸の入替もしない
- `Rigidbody.angularVelocity` は**世界系のまま**渡す。あらかじめ機体系へ直さない
- 力とトルクは Unity の機体系で返るので、`AddRelativeForce` ／ `AddRelativeTorque` にそのまま渡せる
- 空間ファイルは ENU（x=東・y=北・z=上）のまま。空間ファイルと Unity の変換だけが C# 側に残る座標の仕事

### 7.6 検証

単体試験 `simulator/sils/frames/frames_unity_test.cpp`。依存は `frames_unity.hpp` と `sf_math` だけで MuJoCo を含まないため、単体でビルドして実行できる。

```bash
/usr/bin/clang++ -std=c++17 -O2 \
  -I simulator/sils/frames \
  -I firmware/vehicle/components/sf_math/include \
  -o /tmp/frames_unity_test simulator/sils/frames/frames_unity_test.cpp
/tmp/frames_unity_test
```

確かめている項目:

1. 軸の対応そのもの（北→+Z、東→+X、下→−Y）と、極性・軸性それぞれの往復変換
2. 乱数でなく**固定の 7 姿勢**について、回転行列での対応 `M·R(q_nb)·Mᵀ` とクォータニオンの対応式が一致すること
3. 符号の具体例（§7.4 の表）
4. 静止して接地しているときの加速度計の測定値が Unity で `[0,+9.81,0]`、FRD で `[0,0,−9.81]` になること
5. 世界系の角速度を `q_nb` で機体系へ直す経路の往復と、大きさが保たれること

---

<a id="english"></a>

## 1. About This Document

This is the **specification** for the `simulator/sils/frames/` transform module and the **single authoritative mapping** between MuJoCo and StampFly coordinate systems. **Only** this `frames` module performs coordinate transforms in the SILS — nowhere else (the old SILS broke with 4 scattered frame inconsistencies).

### Decision (2026-05-31)

**Keep MuJoCo in its natural (Z-up) frame; do the coordinate transform in software (`frames` module).**

- **Rationale (user):** when later using the MuJoCo viewer to check new firmware / control theory, aligning MuJoCo to StampFly's Z-down would make the scene move **inverted vs. intuition and cause confusion**. To preserve the viewer's purpose (visual checking, review video), MuJoCo stays upright. The cost (a transform inside the SILS) is contained in this one module, guaranteed by unit tests, and documented here.

## 2. Two Coordinate Systems

- **StampFly (fixed by firmware):** World = **NED** (X-north, Y-east, **Z-down**; g_ned=[0,0,+9.81]); Body = **FRD** (X-fwd, Y-right, Z-down); attitude `q_nb` = body→NED; altitude = −pos_z.
- **MuJoCo (natural, Z-up):** World = **ENU** (X-east, Y-north, **Z-up**; gravity [0,0,−9.81]); Body = **FLU** (X-fwd, Y-left, Z-up); `framequat` = body(FLU)→world(ENU).

Both NED and ENU are right-handed, so a proper rotation (det=+1) exists between them.

## 3. Transforms (the `frames` module)

- **World ENU→NED:** `v_ned = (enu.y, enu.x, −enu.z)` (swap XY, negate Z; 180° about (1,1,0)/√2).
- **Body FLU→FRD:** `v_frd = (flu.x, −flu.y, −flu.z)` (180° about body X).
- **Attitude:** `q_nb = q_we ⊗ q_mj ⊗ q_bf`, with `q_we=[0,1/√2,1/√2,0]`, `q_bf=[0,1,0,0]`. Verified: level/north ⇒ q_nb = (−1,0,0,0) ≡ identity.

## 4. Sensors / Actuator

- **Accelerometer (driver-normalized):** the driver returns body-FRD acceleration with gravity as −9.8: `out_frd = R_bn·(a_world − g_ned)`, `[0,0,−9.81]` at rest. MuJoCo's built-in `<accelerometer>` (a−g, FLU) matches directly after FLU→FRD — same sign. The legacy `ba_z≈+2g` startup hack is no longer needed (per the driver-normalization decision, 論点2).
- **Gyro:** MuJoCo `<gyro>` (FLU) → FRD via `M_bf`.
- **BMI270 sensor frame:** SILS returns sensor-frame data = `(body.y, body.x, −body.z)` so `imu_task`'s fixed remap recovers body FRD (Code Identity).
- **ToF/Baro/Flow/Mag/Mixer:** see Japanese §4.4–4.5. Motor: duty→thrust `k·duty²` up = −Z_frd, applied at motor positions with yaw reaction κ, then FRD→FLU into MuJoCo.

## 5. Verification

Canonical unit tests (Japanese §5): level rest accel=[0,0,−9.81] (driver-normalized); attitude round-trip & level/north=identity; det/involution of fixed rotations; gyro axis signs; velocity round-trip; gravity-tilt sign; agreement with MuJoCo built-in sensors.

## 6. Implementation

`simulator/sils/frames/{frames.hpp,frames.cpp}` (shares `sf_math` Vec3/Quat), tests in `frames_test.cpp`. No coordinate transform anywhere else in the SILS.

## 7. Unity Simulator Mapping

The frame conversion used by the Unity simulator (`docs/plans/unity-simulator.md`). It is a different map from the MuJoCo one (§2–§6); the implementation is `simulator/sils/frames/frames_unity.hpp` (header-only, no MuJoCo dependency).

### 7.1 Unity's Conventions

| Item | Convention |
|------|------------|
| Handedness | **Left-handed** (both MuJoCo and StampFly are right-handed) |
| Up axis | **Y** |
| Units | m |
| Quaternion | component order **x, y, z, w** (`sf::math::Quat` uses w, x, y, z) |
| `Rigidbody.angularVelocity` | angular velocity in the **world** frame, not the body frame |

### 7.2 Axis Correspondence

The world pair (NED ↔ Unity world) and the body pair (FRD ↔ Unity body) use the same single correspondence.

```
  north / forward  →  Unity +Z
  east  / right    →  Unity +X
  down             →  Unity −Y
```

In components, the matrix taking StampFly's (n, e, d) to Unity's components is:

```
M = [[0, 1,  0],
     [0, 0, −1],
     [1, 0,  0]]           u = M·v,   v = Mᵀ·u
```

`M` is orthogonal (`M·Mᵀ = I`) but **`det(M) = −1`**: it is a **handedness flip**, not a rotation. This is the decisive difference from the MuJoCo maps of §3.1–§3.2, which have `det = +1` (proper rotations).

### 7.3 Why Polar and Axial Vectors Get Different Signs

Because `det(M) = −1`, the treatment splits by the kind of quantity.

| Kind | Examples | To Unity | From Unity |
|------|----------|----------|------------|
| **Polar vector** | position, velocity, force, accelerometer reading | `u = M·v = (v.y, −v.z, v.x)` | `v = Mᵀ·u = (u.z, u.x, −u.y)` |
| **Axial vector** | angular velocity, torque | `u = det(M)·M·v = (−w.y, w.z, −w.x)` | `v = det(M)·Mᵀ·u = (−u.z, −u.x, u.y)` |

Polar vectors are plain component tuples, so they transform straight through. Axial vectors are built from a cross product, and a cross product takes its direction from the handedness of the frame, so under a `det = −1` map it gains one extra sign. Equivalently: the same physical spin that is counter-clockwise under the right-hand rule is clockwise under Unity's left-hand rule, so its axis must be flipped to describe the same motion.

### 7.4 Attitude Quaternion

Fixed by requiring the two rotation matrices to agree: `R_unity = M·R(q_nb)·Mᵀ`.

```
q_nb(w,x,y,z) → Unity(x,y,z,w) = (−q.y,  q.z, −q.x,  q.w)
Unity(x,y,z,w) → q_nb(w,x,y,z) = ( u.w, −u.z, −u.x,  u.y)
```

Because Unity's left-hand rule and the `det = −1` flip cancel each other, the resulting Unity quaternion is read with the ordinary Hamilton formula — no extra conjugation.

**Sign checks:**

| Case | StampFly | Unity |
|------|----------|-------|
| Right yaw (nose right) | `q_nb = (cos, 0, 0, sin)` | rotation about +Y |
| Right roll | body rate `+ωx` (FRD forward) | about `−Z` |
| Nose up | body rate `+ωy` (FRD right) | about `−X` |
| Right yaw rate | body rate `+ωz` (FRD down) | about `+Y` |
| At rest on the ground | accelerometer `[0, 0, −9.81]` (FRD) | `[0, +9.81, 0]` (pointing up) |

### 7.5 Contract with the C# Side

The C ABI passes Unity's raw conventions in both directions, and **all conversion happens in `frames_unity.hpp`**. The C# side does not reimplement any of it (the SILS rule that frame conversion lives in one place; see §1).

- Pass `Rigidbody` values verbatim (`position`, `rotation`, `linearVelocity`, `angularVelocity`). No sign flips, no axis swaps.
- Pass `Rigidbody.angularVelocity` **as the world frame**; do not pre-rotate it into the body frame.
- Force and torque come back in Unity's body frame, ready for `AddRelativeForce` / `AddRelativeTorque`.
- The world file stays in ENU (x east, y north, z up). Converting between the world file and Unity is the only frame work left on the C# side.

### 7.6 Verification

Unit test `simulator/sils/frames/frames_unity_test.cpp`. It depends only on `frames_unity.hpp` and `sf_math` — no MuJoCo — so it builds and runs standalone.

```bash
/usr/bin/clang++ -std=c++17 -O2 \
  -I simulator/sils/frames \
  -I firmware/vehicle/components/sf_math/include \
  -o /tmp/frames_unity_test simulator/sils/frames/frames_unity_test.cpp
/tmp/frames_unity_test
```

What it checks:

1. The axis correspondence itself (north→+Z, east→+X, down→−Y) and the round trip for both the polar and the axial map.
2. For **7 fixed attitudes** (not random), that the rotation-matrix relation `M·R(q_nb)·Mᵀ` agrees with the closed-form quaternion mapping.
3. The sign cases of §7.4.
4. That the accelerometer reading at rest on the ground is `[0,+9.81,0]` in Unity and `[0,0,−9.81]` in FRD.
5. The world-angular-velocity route through `q_nb` into body rates — round trip and magnitude preservation.
