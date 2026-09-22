# Unity 版シミュレータ — プロジェクトの骨組みと段階 1(c)(e) の検証

> **Note:** [English version follows after the Japanese section.](#english) / 日本語の後に英語版があります。

## 1. 概要

### このディレクトリについて

`docs/plans/unity-simulator.md`（Unity 版シミュレータ計画）の Unity プロジェクト本体を置く。
このコミットの時点で入っているのは、以後の開発の土台になるプロジェクトの骨組みと、
計画 §9 の **(c) PhysX の剛体** と **(e) Unity CLI** の検証結果である。

ファームウェアを WebAssembly で動かす側（段階 1(a) の成果と `just unity-native-spike`）は
隣の [`native/README.md`](native/README.md) にある。Unity は `Assets`・`Packages`・
`ProjectSettings` 以外のルート直下のフォルダを見ないため、`native/` と同居できる。

### 対象読者

段階 2 以降でこのプロジェクトに手を入れる担当者。

### 版と構成

| 項目 | 値 |
|------|------|
| エディタ | 6000.6.2f1（arm64、Web モジュール入り）。`ProjectSettings/ProjectVersion.txt` で固定 |
| 元にしたテンプレート | `com.unity.template.urp-blank` 17.2.1（Universal 3D） |
| 描画 | URP（`com.unity.render-pipelines.universal` 17.6.0） |
| 入力 | Input System 1.20.0 |
| UI | UI Toolkit（`com.unity.modules.uielements`、組み込みモジュール） |
| 試験 | Test Framework 1.8.0 |
| 端末からの操作 | `com.unity.pipeline` 0.7.0-exp.1（`unity pipeline install` が追加） |
| 直列化 | Force Text（`m_SerializationMode: 2`）。`.meta` は全てコミットする |

## 2. 開き方・試験・ビルド

Unity CLI は `~/.unity/bin/unity`。以下のコマンドはこのディレクトリを作業場所として実行する。

### 開く

```bash
unity open .                      # エディタで開く（Hub に登録が無くてもパスとして開ける）
unity status --json               # 動いているエディタの接続口・PID・状態を見る
unity close .                     # 保存せずに終了する
```

### 試験

```bash
unity test . --mode PlayMode  --output test-results.xml --non-interactive
unity test . --mode EditMode  --output test-results.xml --non-interactive
unity test . --mode PlayMode --filter PhysXStabilityTest --output results.xml --non-interactive
```

`--mode` を省くとエディタの既定の試験プラットフォームが走る。報告書は NUnit 形式で
`--output` のパスに書かれ、`--report-format junit,nunit` と `--junit-output` で JUnit 形式も
出せる。**終了コードは合格で 0、1 件でも不合格なら 8**（Unity 自身の処理は 2 で終わる）。
名前が 1 つも当たらない `--filter` は、試験を走らせずに 0 で終わる。

### WebGL ビルド

WebGL には Unity 内蔵のコマンドライン用ビルドが無い。ビルドプロファイルか、
static メソッドの指定が要る。このプロジェクトは後者を持っている。

```bash
unity build . --target WebGL \
  --execute-method StampFly.Editor.Builders.WebGLBuilder.Build \
  -o /path/to/output --non-interactive --no-tail
```

`--execute-method` を付けずに `--target WebGL` だけを渡すと、
`Target WebGL has no built-in command-line build.` で終了コード 2 になる。

置き場を `Editor/Build/` ではなく `Editor/Builders/` にしてあるのは、リポジトリ直下の
`.gitignore` の `build/`（macOS では大文字小文字を区別しない）が `Editor/Build/` ごと
除外してしまうためである。

### 端末からエディタを操作する

```bash
unity pipeline install --project-path .   # com.unity.pipeline を追加する（1 回だけ）
unity command                             # 使える命令の一覧
unity command --query stampfly --json     # 名前で絞る
unity command stampfly_probe --json       # 実行する
```

## 3. 段階 1(c) の検証 — PhysX の剛体

### 確かめたこと

質量 0.037 kg、慣性 (9.16, 13.3, 20.4)×10⁻⁶ kg·m²（機体 FLU の x 前・y 左・z 上）の剛体を、
`Physics.simulationMode = Script` で 400 Hz（`Physics.Simulate(0.0025)`）で回して確かめた。
数値は `Assets/StampFly/Tests/PlayMode/PhysXStabilityTest.cs`（判定つき 6 件）と
`PhysXDiagnosticsProbe.cs`・`PhysXAngularMomentumProbe.cs`（判定を置かない計測）が出す。
16 件すべて合格、所要 2.25 秒。

### 結果

| 確認項目 | 測った値 | 判定 |
|------|--------|------|
| 1 トルク応答 | 3 軸それぞれ τ/I = 100 rad/s² に対し誤差 **0.0000%**（機体 x: I=1.33e-5、y: I=2.04e-5、z: I=9.16e-6） | 合格（許容 ±1%） |
| 2 着地 | 0.5 m から落として静止高 **0.010300 m**（箱の厚みの半分 0.0103 m と一致）。着地後 2 秒の高さの変動幅 **5.47×10⁻⁵ m**、残速度 2.21×10⁻⁷ m/s | 合格 |
| 3 刻みの比較 | 400 Hz と 1600 Hz の 1 秒後の位置差 **4.68 mm**（半刻みの遅れの予測 4.60 mm と 1.7% 差）。1600 Hz と 3200 Hz では 0.80 mm、比 5.87（1 次なら 6.0） | 合格（積分の次数どおりで、不安定ではない） |
| 4 ジャイロ項 | **明示的に足す必要がある。** 足さないと角運動量の向きが 1 秒で **15.1619 度**ずれ、この角度は刻みを 400→6400 Hz にしても変わらない | 合格（下記） |
| 5 加速度計 | 静止接地で 200 標本の平均 **9.809999 m/s²**（機体の上向き）、最大偏差 **2.06×10⁻³ m/s²** | 合格 |
| 6 再現性 | 同じ入力で 2 回走らせ、1 秒後の位置が **完全一致**（差 0.0 m） | 合格 |

### ジャイロ項についての結論

**PhysX はオイラーの運動方程式の −ω×(Iω) を積分していない。** トルクの無い剛体では
世界座標系の角速度 ω を一定に保つ（`[probeE]`: `worldRate` が 0.2 秒を通じて (20, 0, 30) のまま）。
慣性が非対称なので、これは世界座標系の角運動量 L = R I R⁻¹ ω の向きが回ってしまうことを意味する。
`|L|` は 7 桁一致で保たれるが、向きが 1 秒で 15.1619 度ずれる。この角度は刻みによらない
（400 / 800 / 1600 / 3200 / 6400 Hz で 15.1619・15.1619・15.1619・15.1619・15.1618 度）ので、
積分誤差ではなく物理の欠落である。主軸 1 本まわりだけの回転なら連成が無く、ずれは 0 になる（`[probeD]`）。

`AddRelativeTorque(-ω×Iω)` を明示的に足すと、機体座標系の角速度がオイラーの式どおりに
歳差し（`[probeF]`: (20, 0, 30) → (19.1, −3.0, 30.5) → … → (−19.3, −4.0, 30.8)）、
世界座標系の L の向きが止まる。**二重計上ではない**ことの証でもある。PhysX も足していれば、
2 つ目を足すと向きは良くならず悪くなるはずである。

足すときは **刻みの中点で評価する**（`GyroscopicTerm.BodyTorqueAtMidpoint`）。刻みの先頭で
評価すると陽的な積分にエネルギーが入り、400 Hz で `|L|` が 1 秒あたり **10.77% 増える**。
中点評価にすると **0.006% 増**に収まり、向きのずれも 1.1172 度から 0.4049 度に減る。
追加の費用は外積 1 つである。

| 400 Hz で 1 秒 | 向きのずれ | 角運動量の大きさの変化 |
|---|---|---|
| 項を足さない | 15.1619 度 | ×1.000000 |
| 先頭で評価して足す | 1.1172 度 | ×1.107671 |
| **中点で評価して足す** | **0.4049 度** | **×1.000060** |

### PhysX の設定

`ProjectSettings/DynamicsManager.asset` と `Assets/StampFly/Runtime/Sim/PhysicsStepSettings.cs`
の両方に同じ値を置いてある（前者は既定、後者は実行時に上書きする）。

| 設定 | 既定 | この値 | 理由 |
|------|------|--------|------|
| `Physics.simulationMode` | FixedUpdate | **Script** | 刻みを `SimLoop` が自前で回す（計画 §3） |
| `defaultContactOffset` | 0.01 m | **0.001 m** | 既定は機体の半分の厚み 0.0103 m と同じ桁で、箱が厚み 1 つぶん近く浮く |
| `bounceThreshold` | 2 m/s | **20 m/s** | 着地で跳ね続けさせない |
| `defaultSolverIterations` | 6 | **12** | 質量 0.037 kg での接触の収束を確かなものにする |
| `defaultSolverVelocityIterations` | 1 | **4** | 同上 |
| `defaultMaxDepenetrationVelocity` | 10 m/s | **1 m/s** | めり込みの押し戻しで機体が飛ばないようにする |
| `defaultMaxAngularSpeed` | 50 rad/s | **10⁶ rad/s** | 37 g の機体は高い角速度に達する。頭打ちにさせない |
| `m_EnableEnhancedDeterminism` | 0 | **1** | 実行ごとの再現性（確認項目 6）。実行中には変えられず、この設定でしか有効にできない |

剛体側（`VehicleBody.Apply`）では `automaticInertiaTensor` と `automaticCenterOfMass` を
どちらも false にし、慣性テンソルを直接書く。`linearDamping` と `angularDamping` は 0
（空気抵抗は C++ のプラントが担う）、`sleepThreshold` は 0（途中で休止させない）。

### 軸の割り当て

MuJoCo モデル（`simulator/sils/models/stampfly.xml`）は機体 FLU（x 前・y 左・z 上）。
Unity は左手系（x 右・y 上・z 前）。慣性と衝突箱は次のように移す。

| FLU | 値 | Unity | 値 |
|-----|-----|-------|-----|
| Ixx（前軸） | 9.16e-6 | z | 9.16e-6 |
| Iyy（左右軸） | 13.3e-6 | x | 13.3e-6 |
| Izz（上軸） | 20.4e-6 | y | 20.4e-6 |
| 衝突箱 半長 | 0.0408 / 0.0408 / 0.0103 | 全長 (x, y, z) | 0.0816 / 0.0206 / 0.0816 |

## 4. 段階 1(e) の検証 — Unity CLI

| 確認項目 | 結果 |
|------|------|
| `unity test` の指定 | `--mode PlayMode` ／ `--mode EditMode`。省略するとエディタの既定が走る |
| 報告書 | `--output <path>` に NUnit 形式。`--report-format nunit,junit,both` と `--junit-output` もある |
| 終了コード | 合格 **0**、不合格 **8**（内側の Unity は 2 で終わる）。名前が当たらない `--filter` は 0 |
| 所要 | PlayMode 16 件で CLI 全体 62 秒（うち試験そのものは 2.25 秒。残りは資産の取り込みとエディタの起動） |
| `unity build` の WebGL | `--target WebGL` だけでは通らない（終了コード 2、`Target WebGL has no built-in command-line build.`）。`--execute-method` かビルドプロファイルが要る |
| WebGL ビルドの所要と大きさ | ほぼ空の場面 1 つで、初回 **4 分 13 秒**・2 回目 **2 分 16 秒**（`Library/` が温まっているため）。出力 **11 MB**（`.wasm.br` 6.90 MB ＋ `.data.br` 3.21 MB ＋ `.framework.js.br` 66 KB ＋ `.loader.js` 28 KB） |
| `unity pipeline install` | `com.unity.pipeline` 0.7.0-exp.1 を `Packages/manifest.json` に追加する。終了コード 0 |
| `[CliCommand]` の宣言規則 | **static メソッド**（公開範囲は問わない。private でも登録される。インスタンスメソッドだけが登録されず警告になる）。`[CliCommand(name, description, MainThreadRequired = true, RuntimeOnly = false, Tags = new[]{...})]`。戻り値は任意（文字列・数値・匿名オブジェクト・null）で、サーバが応答の封筒に包む。引数は `[CliArg(name, description, Required = , DefaultValue = )]` で、属性は省略可 |
| 主スレッド | `MainThreadRequired` の既定は **true**。Unity の API の大半が主スレッドを要るため |
| 再生中に届くか | **届く。** 再生に入ってから `stampfly_probe` を実行し、`isPlaying: true`・`frameCount: 2` が返った。動いている再生ループの主スレッドで実行される |
| 接続口 | エディタ 1 つにつき 1 つ（実測で 127.0.0.1:7800）。`unity status --json` が接続口・プロジェクトのパス・PID・状態を返す |

検証用の命令は `Assets/StampFly/Editor/Commands/SimulatorProbeCommands.cs` にある。
段階 6 で計画の `ISimCommands` に置き換える。

`com.unity.pipeline` の `Unity.Pipeline` アセンブリは
`UNITY_EDITOR || DEVELOPMENT_BUILD || ENABLE_RUNTIME_PIPELINE` の条件付きでしか入らない。
計画 §4「中継の受け口は開発用ビルドだけに入れる」の検査は、この条件を手掛かりにできる。

## 5. 置き場の構成

```
simulator/unity/
├── Assets/
│   ├── Scenes/SampleScene.unity          テンプレート由来。段階 3 で作り直す
│   ├── Settings/                         テンプレート由来の URP 設定
│   ├── InputSystem_Actions.inputactions  テンプレート由来
│   └── StampFly/
│       ├── Runtime/Sim/                  StampFly.Sim アセンブリ
│       │   ├── VehicleBody.cs            質量・慣性・衝突箱・剛体の設定
│       │   ├── PhysicsStepSettings.cs    PhysX の全体設定と刻みの長さ
│       │   ├── GyroscopicTerm.cs         -ω×(Iω) と世界座標系の角運動量
│       │   └── AccelerometerModel.cs     R⁻¹((v後−v前)/dt − g)
│       ├── Runtime/Resources/StampFly/   実行時に Resources から読む材質とメッシュ
│       ├── Editor/Builders/              WebGL ビルドの入口
│       ├── Editor/Commands/              [CliCommand] の検証用
│       ├── Editor/MeshTools/             STL → メッシュ資産の変換（§9 の「機体の見た目」）
│       └── Tests/PlayMode/               判定 6 件＋計測 10 件
├── Packages/manifest.json
├── ProjectSettings/
└── native/                               段階 1(a) の WebAssembly 検証（別文書）
```

生成物（`Library/`・`Temp/`・`Logs/`・`obj/`・`UserSettings/`・`Build/`・`Data/`・
`*.csproj`・`*.sln`）はリポジトリ直下の `.gitignore` で除外する。`.meta` は除外しない
（参照が頼る GUID を持つため）。Git LFS は使わない。

## 6. 段階 3 への引き継ぎ

| 事項 | 内容 |
|------|------|
| ジャイロ項 | `SimLoop` は毎刻み `GyroscopicTerm.BodyTorqueAtMidpoint` を `AddRelativeTorque` で足す。足し忘れると非対称な慣性の挙動が実機と食い違う |
| 場面 | `SampleScene.unity` はテンプレートのまま。段階 3 で `SimLoop`・`VehicleBody`・床を持つ場面に作り直す |
| 圧縮 | WebGL の既定は Brotli（`.br`）。GitHub Pages は `Content-Encoding` を付けられないので、計画 §4 のとおり Decompression Fallback を有効にする必要がある。この検証では既定のまま測った |
| EditMode 試験 | まだ 1 件も無い。`unity test --mode EditMode` は通るが空で終わる |
| 刻みの比較 | 400 Hz と 1600 Hz の差 4.68 mm は固定刻みの積分に必ず伴う半刻みの遅れで、精度を上げたいなら刻みを細かくするほか無い。MuJoCo 版は 4000 Hz で積分しており、空中の軌跡を突き合わせるときはこの差を見込む |

## 7. 空間の読み込みと生成（`StampFly.World`）

### このアセンブリについて

空間ファイル（`*.world.json`）を読み、部屋・床・障害物を場面に生成する。形式の基準は
[`Schemas/README.md`](Schemas/README.md) で、飛行やファームウェアからは独立している。

```
Assets/StampFly/
├── Runtime/World/                    StampFly.World アセンブリ
│   ├── WorldFile.cs                  JsonUtility で読むデータクラス一式
│   ├── WorldFormat.cs                形式が定める定数（目印・版・既定値・15 種の名前）
│   ├── WorldFileReader.cs            読み込みと、目印・版・座標系の確認
│   ├── WorldWriter.cs                ENU のまま JSON へ書き戻す
│   ├── WorldFrames.cs                ENU ⇔ Unity の変換（C# の座標の仕事はここだけ）
│   ├── ObstacleFactory.cs            障害物15種の生成（家具5種と動的ピンを含む）
│   ├── BowlingPinFactory.cs          小型軽量ピンの複合コライダと動的剛体
│   ├── DynamicObstacleBody.cs        動的障害物の初期姿勢と速度の復元
│   ├── WedgeMesh.cs                  ramp のくさびの手続き生成
│   ├── RoomBuilder.cs                部屋・壁・天井・照明
│   ├── FloorTexture.cs               床の模様 5 種の手続き生成（WebGL2 で動く）
│   ├── WorldMaterials.cs             色ごとに使い回す URP Lit のマテリアル
│   ├── ObstacleInfo.cs               面の素性（id・種類・flow_quality）
│   ├── WorldCatalog.cs               同梱10空間を TextAsset で持つ ScriptableObject
│   ├── WorldLoader.cs                読み込み・生成・片付け（MonoBehaviour）
│   └── StructuredLog.cs              ログの出し口（`IStructuredLog`）と既定の実装
├── Editor/WorldTools/                WorldCatalog.asset を作り直すメニュー
└── Tests/EditMode/                   読み込み・変換・往復・一覧の試験
```

### ボーリング部屋と動的障害物

部屋選択の `Room` → `Bowling` から、12 g・高さ18 cm・直径5 cmのピン10本を並べた部屋を選ぶ。現在の実装は10空間・15種類の障害物を持つ。検証と公開の記録は `docs/plans/unity-simulator.md` §4を参照。

`bowling_pin` だけが動的な剛体で、平底を含む複合コライダを通じてドローンや他のピンと衝突する。既存の `SimLoop` による400 HzのPhysX計算を使い、他の家具・障害物は静的なまま維持する。タッチの `RESTART` またはBで `SimLoop.PowerCycled` が再起動を通知し、`WorldLoader.ResetDynamicBodies()` が全ピンの初期姿勢と速度を復元する。

空間ファイルとブラウザ内保存は初期配置を保持する。`WorldWriter.FromScene` は動的な根を除外し、転倒した姿勢を書き戻さない。家具編集を開く時にも初期姿勢へ戻し、姿勢だけの復元では配置のUndo履歴を変えない。スコアや投球回数は扱わない。

### 段階 3・5 への引き継ぎ

| 事項 | 使い方 |
|------|--------|
| 出発点 | `WorldLoader.SpawnPosition`（Unity の座標）と `SpawnRotation`（`spawn.yaw_deg` の 90 度のずれを含む）。変換済みなので、そのまま `VehicleBody` に渡せる |
| フロー品質 | レイの当たり先から `hit.collider.GetComponent<ObstacleInfo>().FlowQuality`。床・壁・天井を含め、この一式が作る全てのコライダが 1 つ持つので、空間に当たった結果が答えを持たずに返ることはない |
| 読み込み | `WorldLoader.LoadByName("gate_course")`（`WorldCatalog` から）または `Load(json)`。どちらも成否を返し、断った理由をログに出す |
| 片付け | `WorldLoader.Clear()`。`Load` は先に片付けるので、続けて両方を呼ぶ必要は無い |
| 刻みとの関係 | この企画は PhysX を `simulationMode = Script` で回すため、生成の直後に `Physics.SyncTransforms()` を呼んでいる。これが無いと最初の `Physics.Simulate` までコライダの外形が古く、レイも当たらない |
| ログ | `WorldLoader.Log` に `IStructuredLog` を注入すると、`world.loaded` ／ `world.rejected` ／ `world.cleared` の行の行き先を差し替えられる。既定は Unity のコンソールへ JSON 1 行。`run_id` を持つページ側のログ担当ができたら、ここへ差す |

### 一覧（`WorldCatalog.asset`）の作り直し

同梱の空間を足す・名前を変える・消したら、一覧を作り直す。`StreamingAssets` ではなく
TextAsset の参照で持つので、作り直さないと WebGL のビルドから抜け落ちる
（`WorldCatalogTest` が手元でそれを捕まえる）。

```bash
# エディタのメニュー: StampFly > World > Rebuild Catalog
# 端末から:
/Applications/Unity/Hub/Editor/6000.6.2f1/Unity.app/Contents/MacOS/Unity \
  -batchmode -quit -nographics -projectPath . \
  -executeMethod StampFly.Editor.WorldTools.WorldCatalogBuilder.Rebuild
```

置き場を `Editor/Build/` ではなく `Editor/WorldTools/` にしてあるのは、§2 の
`Editor/Builders/` と同じ理由（`.gitignore` の `build/` が大文字小文字を区別しない）である。

### 仕様（`Schemas/README.md`）の訂正（2026-09-20）

実装しながら Unity で実測した結果、仕様の §6.2・§6.2.1 に誤りが見つかったため、**仕様の
ほうを訂正した**。実装と仕様は現在一致している。経緯は `Schemas/README.md` の各節の
「訂正」の注に残してある。要点は次の 2 つ。

| 箇所 | 誤っていた記述 | 訂正後 |
|------|--------------|--------|
| §6.2 | 「`AngleAxis` の角に負号を付けてはならない」 | 負号が**要る**。`AngleAxis` は渡した軸について右ねじに回るので、ENU の正の角は Unity では負号が付く。§6.2 の表（−x／−z／−y）は当初から正しく、矛盾していたのは説明文とコード例のほうだった |
| §6.2.1 | `AngleAxis(yawDeg − 90, up)` | `AngleAxis(90 − yawDeg, up)`。誤った式では `yaw_deg = 0` が**西**を向き、同じ節の検算の表と矛盾していた |

§6.2.1 の誤りが今まで見つからなかったのは、**同梱 6 空間の出発点がすべて `yaw_deg` ±90 で、
その 2 つの値では両式が同じ結果になる**ためである。同じ隠れ方が二度と起きないよう、
(a) §6.2.1 の検算の表に ±90 以外（0・45・180・−135）を加え、EditMode 試験
`SpawnYawPointsTheVehicleWhereTheFormatSays` が 6 件すべてを確かめる、(b) 誤ったほうの式を
名指しして落とす `SpawnYawIsNotTheMirroredExpression` を置く、(c) `empty_room` の
`spawn.yaw_deg` を 45 に変え（何もない 6 m 四方の部屋なのでねらいを損なわない）、
PlayMode 試験 `AnOffAxisSpawnHeadingSurvivesTheWholeLoad` がファイルから生成まで端から端まで
確かめる、の 3 つを入れた。

`ring` の外形（外円の弦に切って内接させる）と `tunnel` に床を作らないことは、仕様 §3 の
「注意点」に明記した。

### 試験

```bash
unity test . --mode EditMode --output test-results.xml --non-interactive   # 63 件
unity test . --mode PlayMode --output test-results.xml --non-interactive   # 38 件
```

EditMode（63 件）は、同梱 6 空間が読めること、壊れた JSON・違う `format`・違う `version`・
違う `frame`・未知の種類・肉厚の無い中空が理由付きで断られること、省略時の既定（`light`・
`segments`・障害物の `flow_quality`）が効き**床の `flow_quality` には効かない**こと、
`WorldFrames` が §6 の数値と一致し逆変換できること、読み → 書き → 読みで内容が一致する
こと、一覧が 6 空間を持つことを見る。

PlayMode（38 件。うち 10 件がこの節のもの、28 件が §3 の PhysX の検証）は、6 空間それぞれで
障害物の数が JSON と一致すること、**種類ごとの外形が `tools/unity_world/validate.py` の
幾何の解釈と一致すること**（62 個すべて、10 種すべてを通る）、gate・ring・tunnel の開口を
レイが抜け枠には当たること、`pad` と床から `flow_quality` が引けること、前の空間を片付けて
から次を読むと古い障害物が残らないことを見る。

## 8. ログと端末からの操作（`StampFly.Core` ／ `StampFly.Remote`）

### この 2 つのアセンブリについて

ページが出すログの形と、端末から届く命令の受け口を持つ。基準は
[`docs/commands/sf-unity.md`](../../docs/commands/sf-unity.md) §8（ページ側が守る約束）と
`AGENTS.md` の「Logs」で、ここにはその実装だけを書く。

```
Assets/StampFly/
├── Runtime/Core/                     StampFly.Core アセンブリ（他のどれも参照しない）
│   ├── LogContract.cs                IStructuredLog・LogLevel・LogSources（`src` の語彙）
│   ├── LogJson.cs                    1 行を JSON にする（エスケープ・鍵の並び）
│   ├── StructuredLog.cs              run_id・cmd_id の範囲・仮想時刻・輪状バッファ・出力先
│   ├── LogSinks.cs                   コンソール／束ね／空の出力先と、識別子の発行
│   ├── SimCommandRegistry.cs         命令の登録簿と、誰にも依存しない命令
│   ├── SimCommandArgs.cs             引数の JSON の読み手（JsonUtility は辞書を扱えない）
│   └── FirmwareLogPump.cs            SfuLogRecord 1 件を `src: "fw"` の行にする
├── Runtime/Remote/                   StampFly.Remote アセンブリ（開発用ビルドだけ）
│   ├── RemoteBridge.cs               場面に 1 つ置く。ログ・登録簿・URL の引数を持つ
│   ├── RemoteNative.cs               .jslib との継ぎ目（WebGL 以外では何もしない）
│   ├── RemoteLogSink.cs              出来上がった行を .jslib へ渡す
│   ├── EditorFileLogSink.cs          エディタの再生の行き先（下の「エディタの再生」）
│   ├── PendingCommand.cs             届いた命令 1 つ（cmd_id・command・args）
│   └── WorldCommands.cs              world.list ／ world.load の薄い登録
├── Plugins/WebGL/RemoteBridge.jslib  /api/hello・/api/cmd/next・/api/cmd/result・/api/log
└── Editor/Cli/SimCommandCli.cs       `unity command stampfly_cmd`（同じ登録簿へ）
```

`StampFly.Remote` の `defineConstraints` は `UNITY_EDITOR || DEVELOPMENT_BUILD ||
STAMPFLY_REMOTE` で、配布用ビルドにはアセンブリごと入らない（計画 §4「中継の受け口は
開発用ビルドだけに入れる」）。`StampFly.Core` には制約を付けない。ログの形は配布用
ビルドでも要るためである。

### 3 つの経路

| 経路 | 入口 | 使う場面 |
|------|------|---------|
| 端末 | `sf unity cmd <命令>` → サーバ → `/api/cmd/next` → `RemoteBridge` | ローカル配信のページ |
| ページ | `window.stampfly.command({command, args})`（Promise を返す） | ブラウザの検証、公開ページ |
| URL の引数 | `?world=gate_course` ／ `?log=debug` → 起動時の命令列 | 公開サイトと共有のリンク |
| エディタ | `unity command stampfly_cmd --command … --args '{…}'` | 再生中のエディタ |

4 つとも同じ `SimCommandRegistry` を通る。答えるのが同じ処理だから、経路が違っても結果が
同じになる（計画 §4）。

### 命令の登録のしかた（`SimLoop` の担当へ）

登録簿は実体を持たない。`sim.*`・`vehicle.*`・`rc.*`・`param.*`・`plant.*` は刻みの輪の
担当が、`world.*`・`obstacle.*` は空間の担当が、それぞれ自分で登録する。

```csharp
private void Start()
{
    RemoteBridge bridge = RemoteBridge.Instance;
    if (bridge == null) { return; }   // 配布用ビルドには橋が無い

    bridge.Commands.Register("sim.pause", args =>
    {
        paused = args.Bool("paused", true);
        return SimCommandResult.Success("{\"paused\":" + (paused ? "true" : "false") + "}");
    }, "Pause or resume the simulation");
}

private void OnDestroy()
{
    RemoteBridge.Instance?.Commands.Unregister("sim.pause");
}
```

| 事項 | 決まり |
|------|--------|
| 登録の時機 | `Awake` でも `Start` でもよい。`RemoteBridge` は `[DefaultExecutionOrder(-10000)]` で、ログと登録簿は誰の `Awake` より先に出来ている |
| 二重登録 | 例外になる（後から登録した方が黙って勝つことを防ぐ）。場面を読み直す部品は `OnDestroy` で `Unregister` する |
| 引数 | `args.String` ／ `Double` ／ `Int` ／ `Bool`。`--arg key=value`（文字列）と `--json`（本物の JSON）のどちらで来ても同じように読める |
| 戻り値 | `SimCommandResult.Success(json)` ／ `Failure(reason)`。JSON は自分で組み立てる（`LogJson.Quote` が文字列を安全に引用する） |
| 例外 | 投げても橋が受け止めて失敗にする。ページの命令の輪は止まらない |

### `sim_us` の供給のしかた（`SimLoop` の担当へ）

刻みの輪が時計を差し込むまで、`sim_us`・`tick`・`frame` は行に付かない（`AGENTS.md` は
「相関の鍵は当てはまるときだけ入れる」と定める）。差し込みは 1 回だけでよい。

```csharp
private void Start()
{
    RemoteBridge.Instance?.Log.SetClockSource(() => new SimulationClock
    {
        SimulationMicroseconds = simulationMicroseconds,
        Tick = tickCount,
        Frame = Time.frameCount,
    });
}
```

毎行この関数が呼ばれるので、**フィールドを読むだけの軽いものにする**。ここで計算しない。

### ファームウェアのログの渡しかた（`IFirmware` の担当へ）

`sfu_log_read_record` が返す記録を、文字列にする前の形のまま渡す。`FirmwareLogPump` が
`src: "fw"`・`event: "fw.log"`・`tag`・`sim_us`・`level`・`msg` の行にする。

```csharp
FirmwareLogPump pump = RemoteBridge.Instance.FirmwareLog;
while (firmware.TryReadLogRecord(out FirmwareLogRecord record))
{
    pump.Pump(record);                      // 記録自身の sim_us と tag が残る
}
pump.NoteDropped(firmware.DroppedLogRecords());   // 累計を渡す。増分だけが 1 行になる
pump.ResetForNewBoot();                           // 電源の入れ直しのたびに 1 回
```

レベルの数値は `sfu_api.h` の `SFU_LOG_*` と同じで、`FirmwareLogLevels.ToLogLevel` が
`AGENTS.md` の 4 段へ写す（`verbose` は `debug` に合わせる。5 つ目の段を作るとサーバに
拒否されるため）。

### ログの行き先

| どこで動くか | 行き先 | 取り出し方 |
|-------------|--------|-----------|
| ローカル配信のページ | 数十行または数百ミリ秒ごとに `POST /api/log` → `logs/unity/<run_id>.jsonl` | `sf unity logs --cmd <id>` |
| 公開ページ | 送らない。ページの中に直近 2000 行を保持する | `window.stampfly.logs()` ／ `window.stampfly.download()` |
| エディタの再生 | `simulator/unity/Logs/stampfly/<run_id>.jsonl` とコンソール（git 管理外） | `jq -c 'select(.src=="fw")' simulator/unity/Logs/stampfly/<run_id>.jsonl` |

タブを閉じるときは `navigator.sendBeacon` で残りを送る。送信に失敗したまとまりは待ち行列の
先頭へ戻して再送し、待ち行列が上限（4000 行）を超えたときだけ古い行から捨て、捨てた数を
`log.dropped` の 1 行で報告する。

### `cmd_id` の範囲

1 つの命令の処理中に出た行すべてに、その命令の `cmd_id` が付く。付けるのは橋で、
`using (log.BeginCommand(cmdId)) { … }` の中で処理を呼ぶ。範囲は入れ子にでき、
`using` を抜ければ外へ漏れない。これがあるので、`sf unity logs --cmd <id>` が CLI・
サーバ・ページの行を 1 つの流れとして時刻順に並べられる。

### 試験

| 置き場 | 見ること |
|--------|---------|
| `Tests/EditMode/StructuredLogTest.cs` | 必須の鍵、`cmd_id` の範囲（入れ子・漏れ・順序違いの片付け）、エスケープと制御文字、`NaN` の引用、レベルの下限、輪状バッファの溢れ、仮想時刻の供給、`run_id` の採用、失った行の報告、ファームの記録の変換、識別子の形 |
| `Tests/EditMode/SimCommandRegistryTest.cs` | 未登録・重複・例外、`help` ／ `log.level`、引数が `--arg` と `--json` のどちらでも同じに読めること、エスケープ、入れ子の値、読めない中身 |
| `Tests/EditMode/LogSampleTest.cs` | 各種の行を 1 行ずつ `tests/fixtures/unity_page_log_sample.jsonl` へ作り直し、コミット済みのものと一致すること |
| `tests/commands/test_unity_page_log.py` | その見本を `lib/sfcli/utils/jsonl_log.py` の `validate_record` と本物の `POST /api/log` へ通す（**Unity 無しで回る**。CI 側の関門はこちら） |

C# と Python は同じ処理の中では出会えないので、ファイルで出会わせてある。形式を変えたら
EditMode の試験が見本を作り直して不合格になるので、差分を見てコミットする。

### 端から端までの確認（2026-09-20 実施）

橋と空間の読み込みだけを持つ場面をビルドして確かめる。企画の場面と Build Settings は
刻みの輪の担当のものなので触らない（`Editor/Cli/RemoteCheckBuilder.cs` が一時の場面を
自分で作り、ビルド後に消す）。

```bash
unity build . --target WebGL \
  --execute-method StampFly.Editor.Cli.RemoteCheckBuilder.Build \
  -o /tmp/webgl-check --non-interactive --no-tail
PYTHONPATH=lib python3 -m sfcli unity serve --dir /tmp/webgl-check --port <空きポート> --no-browser
# Chrome で開いたあと、別の端末から
PYTHONPATH=lib python3 -m sfcli unity cmd sim.ping
PYTHONPATH=lib python3 -m sfcli unity cmd world.load --arg name=gate_course
PYTHONPATH=lib python3 -m sfcli unity logs --cmd <上で返った cmd_id>
```

`world.load` の `cmd_id` で引くと、CLI・サーバ・**ページ**の行が時刻順に 1 つの流れに並ぶ。

```
12:25:52.168Z info  cli    cmd.issued     issued world.load
12:25:52.178Z info  server cmd.received   accepted world.load from the CLI
12:25:52.178Z info  server cmd.forwarded  handed world.load to the page
12:25:52.207Z info  world  world.loaded   loaded world 'gate_course' with 7 obstacles
12:25:52.209Z info  server cmd.completed  page returned a result
12:25:52.209Z info  cli    cmd.result     command succeeded
```

確かめたこと: `/api/hello` でサーバの `run_id` を採用すること（`remote.status` が
`mode: local`）、`world.load` が 7 個の障害物を生成すること、`window.stampfly.command`
が同じ処理へ届くこと（`world.list` が `current: gate_course` を返す）、未登録の命令が
理由付きで断られること、`log.level debug` が即座に効くこと、`window.stampfly.logs()` が
行を返すこと、タブを閉じるときの `navigator.sendBeacon` で残りが届くこと、そして
**サーバに拒否された行が 1 つも無いこと**（`sf unity logs --event log.rejected` が空）。

**自動操作のタブは背面扱いで `requestAnimationFrame` が来ず、Unity が起動しない**
（計画 §9 (e)）。検証では、Unity のローダより前に `requestAnimationFrame` を Worker の
タイマーで駆動する差し替えを入れたページから読み込んだ。人が前面のタブで開く分には
要らない。


## 9. 刻みの輪と飛行（`StampFly.Native` ／ `StampFly.Sim` ／ `StampFly.Input` ／ `StampFly.Vehicle` ／ `StampFly.Ui` ／ `StampFly.App`）

### この一式について

無改変の C++ ファームウェアと PhysX を 2.5 ms ごとに繋ぎ、ブラウザとエディタの両方で
飛ばす。計画 [`docs/plans/unity-simulator.md`](../../docs/plans/unity-simulator.md) の
**段階 3「最小の WebGL 版」** の中核である。

```
Assets/StampFly/
├── Runtime/Native/                   StampFly.Native（C ABI。StampFly.Core だけ参照する）
│   ├── SfuAbi.cs                     定数・戻り値・4 つの構造体の C# の写し
│   ├── SfuStructSizes.cs             大きさと位置の照合（起動時に必ず通る）
│   ├── IFirmware.cs                  ファームの継ぎ目（boot／step／param／外乱／ログ）
│   ├── WebGlFirmware.cs              .jslib 経由で別の wasm モジュールを呼ぶ
│   ├── EditorFirmware.cs             dylib を一意な名前へ複写して dlopen で呼ぶ
│   ├── FirmwareFactory.cs            ビルドに合う実装を選ぶ
│   └── FirmwareLogText.cs            固定長のバイト列を StampFly.Core の記録にする
├── Runtime/Sim/                      StampFly.Sim（Ui も Vehicle も参照しない）
│   ├── SimLoop.cs                    1 刻みの輪。計画 §3 の 4 手順
│   ├── SimClock.cs                   ペース配分（一時停止・コマ送り・倍率・実時間比）
│   ├── SimControls.cs                P／N／[ ]／B／Backspace
│   ├── DownwardRangefinder.cs        下向き ToF（レイ 1 本。30 mm の盲点つき）
│   ├── FlightStateNames.cs           飛行状態とモードの番号 → 名前
│   ├── VehicleBody.cs                質量・慣性・衝突箱（段階 1(c) から）
│   ├── PhysicsStepSettings.cs        PhysX の全体設定（同上）
│   ├── GyroscopicTerm.cs             −ω×(Iω)（同上）
│   └── AccelerometerModel.cs         R⁻¹((v後−v前)/dt − g)（同上）
├── Runtime/Input/                    StampFly.Input
│   ├── IRcSource.cs                  RcFrame（12 bit の生 ADC）と尺度
│   └── KeyboardRc.cs                 `sf sils fly` と同じ割り当て
├── Runtime/Vehicle/                  StampFly.Vehicle（見た目だけ。コライダを持たない）
│   ├── PropellerMesh.cs              3 枚羽根の手続き生成
│   ├── VehicleParts.cs               部品 13 個と色の一覧（色は stampfly_fixed.urdf の rgba）
│   └── VehicleAppearance.cs          板・モータ缶 4 つ・プロペラ 4 つ
├── Runtime/Resources/StampFly/       実行時に Resources から読むもの（プレイヤーのビルドに入る）
│   ├── Materials/                    不透明・半透明の材質の雛形
│   └── Meshes/                       STL から変換した機体の形状（<部品名>.asset。コミットする）
├── Editor/MeshTools/                 STL → メッシュ資産の変換（エディタでのみ動く）
│   └── BinaryStlReader.cs            バイナリ STL を素の三角形として読む（保存法線は使わない）
├── Runtime/Ui/                       StampFly.Ui
│   ├── SimHud.cs                     UI Toolkit の表示（実時間比・1 刻みの所要時間・fps）
│   ├── FollowCamera.cs               追従カメラ
│   ├── ChaseZoom.cs                  寄せ具合の算術（Unity の型を持たない）
│   └── ChaseCameraControls.cs        ホイール ／ F ／ G ／ C
├── Runtime/App/                      StampFly.App（上の全部を繋ぐ最上位）
│   ├── SimulatorBootstrap.cs         場面を実行時に組み立てる
│   ├── SimRemoteCommands.cs          `sim.*` ／ `rc.*` の登録（開発用ビルドだけ）
│   └── CameraRemoteCommands.cs       `camera.*` の登録（同上）
├── Plugins/WebGL/SfuFirmware.jslib   別の wasm モジュールとの継ぎ目
├── Scenes/Main.unity                 物体 1 つ（`SimulatorBootstrap`）だけを持つ
└── Assets/WebGLTemplates/StampFly/   配信するページ（`?raf=worker` を持つ）
```

アセンブリは一方向に積む。`Native` → `Sim` → `{Vehicle, Ui}` → `App` であり、
`Sim` は `Ui` も `Vehicle` も参照しない（参照すると循環になる）。3 者を繋ぐのは `App` だけで、
場面が持つ物体も `SimulatorBootstrap` の 1 つだけである。統合のたびに `.unity` の
テキストを読み合わせずに済ませるためである。

### 1 刻みの処理（計画 §3）

| 順 | 担当 | 処理 |
|---|---|---|
| 1 | C# | `Rigidbody` の位置・回転・世界系の速度と角速度、**前の刻みで求めた**加速度計の測定値、下向きレイの距離と地表からの高さ、RC の最新値を `SfuStepIn` に詰める |
| 2 | C++ | `sfu_step` が状態を注入し、ファームを 2.5 ms 進め、区間平均の合力・合トルクを返す |
| 3 | C# | `AddRelativeForce` ／ `AddRelativeTorque` ／ 風の `AddForce` ／ **ジャイロ項** → `Physics.Simulate(0.0025)` |
| 4 | C# | 加速度計の測定値 = R⁻¹((v後 − v前)/dt − g)。接触力が自動で入る |

**C# 側で座標変換を書かない。** `Rigidbody` の値はそのまま渡し（符号反転も軸の入替もせず、
角速度は世界系のまま）、返る力とトルクは Unity の機体系なので `AddRelativeForce` ／
`AddRelativeTorque` にそのまま渡す。`wrench_dt_s` を掛けたり割ったりしない（診断用である）。
NED／FRD への変換は C++ の `frames_unity.hpp` だけが行う。

**wasm では `sfu_step` の戻り値を読まない。** `SfuStepOut.Status`（168 バイトの 156 バイト目）
から読む。Asyncify が戻り値の中身を置き換えるためである。`.jslib` も `EditorFirmware` も
呼び出しの前にそこへ、橋渡しが決して書かない値（1）を入れてから読む。

### 遅れたときの扱い

ファームの刻みは飛ばさない。代わりに仮想時間を遅らせ、表示板に実時間比を出す。
1 フレームの上限は **12 刻み**（シミュレーション 30 ms）で、これが無いと遅いフレームが
より長い追いつきを求め、次のフレームをさらに遅くしてブラウザが描画をやめる。
**0.5 秒を越えるフレームは停止とみなし、溜まりを捨てる。** 背面に回ったタブは任意の
長さの停止を生むので（計画 §9 (e)）、戻ったときに 1 分ぶんを早送りさせないためである。

### 構造体の照合

C# は 4 つの構造体を自分で宣言し、生のポインタを渡す。よって `sfu_boot` の前に必ず
`sfu_struct_size` と突き合わせ、合わなければ**構造体の名前と両方の数値を添えて**止まる。
合計だけでなく位置も見る（入れ替わった 2 つの欄は合計では見えない）。

| 構造体 | 大きさ | 見る位置 |
|---|---|---|
| `SfuConfig` | 48 | — |
| `SfuStepIn` | 96 | `dt_us` が 92 |
| `SfuStepOut` | 168 | `status` が 156、`now_us` が 80 |
| `SfuParamInfo` | 84 | — |
| `SfuLogRecord` | 272 | 合計のみ（下記） |

**`SfuLogRecord` の中の位置は照合しない。** `ByValArray` の欄を
`Marshal.OffsetOf` がどこに置くかで 2 つの実行環境の答えが食い違う（Mono は本文を 48、
WebGL の IL2CPP は 16 と答える）のに、写すのはどちらも同じ 272 バイトだからである。
この構造体を手で添字で読む箇所は無く、境界を渡るのは `Marshal.PtrToStructure` 経由だけ
なので、合計が合っていれば足りる。刻みの 2 つの構造体は `.jslib` が数値で添字を作るため、
位置も照合する。

**構造体は非管理の記憶域へ書く。** `GCHandle.Alloc(構造体, Pinned)` を使ってはならない。
IL2CPP は値を箱に入れ、`AddrOfPinnedObject` はその箱自身の番地を答えるので、欄の始まりと
一致しない。実際 Chrome では、橋渡しが欄の外へ書き、どの刻みも呼び出し側の目印が
`status` に残ったまま返ってきた（`[SimLoop] sfu_step failed: status 1` が毎刻み）。
`Marshal.AllocHGlobal` で取った領域に `StructureToPtr` で書き、`PtrToStructure` で読み戻す。
領域はファーム 1 つにつき 1 回だけ確保して使い回す（刻みはシミュレーションの 1 秒に 400 回
ある）。Mono はたまたま箱でも動くが、両側とも同じやり方に揃えてある。

### エディタの 2 回目の再生

**エディタは `[DllImport]` で読み込んだネイティブライブラリを解放しない。** そのまま
では 2 回目の再生で `sfu_boot` が「起動済み」を返す（1 モジュール＝1 回の電源投入）。
`EditorFirmware` は、ハンドルをエディタに持たせないことでこれを避ける。dylib を一意な
名前へ複写し、**その複写**を `dlopen`（`RTLD_NOW | RTLD_LOCAL`）で開き、入口ごとに
`dlsym` で引き、`Dispose` で `dlclose` して複写を消す。**C 側にローダは要らない**
（`FirmwarePowerCycleTest` が 1 つのエディタのプロセスで 2 回続けて起動させて確かめる）。
Unity 抜きでも確かめてある（`dlopen` → boot → 400 刻み → `dlclose` を 1 プロセスで 2 回。
2 回とも `boot -> 0`、`now_us = 1000000`）。

### 既知の問題: `sfu_shutdown` がエディタの中でデッドロックする（2026-09-20）

**エディタの中では `sfu_shutdown` を呼ばない。** ABI はこれを省略可としており
（「モジュールを捨てるだけでも足りる」）、呼ぶと再生が返らなくなる。

| 事項 | 内容 |
|------|------|
| 症状 | `unity test --mode PlayMode` が Play Mode に入ったまま返らない。CPU は 0.2% で、計算はしていない |
| 場所 | `sfu_shutdown` → `sils::rtos::Scheduler::shutdown()` → `stop_all()`。14 本のタスクのスレッドは `ulTaskNotifyTake` → `block_current` → `_pthread_cond_wait` に停まったままで、合流しない |
| 切り分け | 同じ dylib を素のプロセスから `dlopen` して boot → 100 刻み → `sfu_shutdown` すると **1 秒以内に 0 を返す**。よって止めているのはエディタが加えるものである（`sample` で全スレッドの停止位置を確認） |
| 当座の対処 | `EditorFirmware.Dispose` は `dlclose` と複写の削除だけを行う。代償はタスクのスタック（14 MiB）がエディタのプロセスが終わるまで残ること |
| WebGL 側 | 影響しない。捨てた wasm モジュールは自分のメモリごと消え、fiber 版スケジューラにスレッドは無い |
| 直す場所 | `simulator/sils/rtos/scheduler_step.cpp` の `stop_all`（ネイティブ側の担当。刻みの輪の担当は触っていない） |

### 操縦（`sf sils fly` と同じ割り当て）

| キー | 働き | | キー | 働き |
|---|---|---|---|---|
| W / S | ピッチ 前 / 後 | | P | 一時停止・再開 |
| A / D | ロール 左 / 右 | | N | 一時停止中に 1 刻み |
| , / . | ヨー 左 / 右 | | [ / ] | 遅く / 速く（0.1〜4） |
| Space / Z | スロットル 上 / 下 | | B | 電源の入れ直し（INIT から） |
| R | ARM / DISARM | | Backspace | 出発点へ戻す（ファームはそのまま） |
| H | ALT_HOLD の入切 | | − / + | 振れ幅 10〜100 |

離したキーの軸は中央へ戻る。スロットルの意味はモードで変わるが C# 側は読み替えない。
ACRO と STABILIZE では推力の指令、ALT_HOLD では中央が「この高さを保つ」である。
どちらかを決めるのはファームで、実機の送信機のときと同じである。

#### ARM はモーメンタリボタン、ALT_HOLD はスイッチ（2026-09-22）

**R を 1 回押せば、ファームの ARM 状態が必ず反転する。** これを成り立たせている前提は、
ファームが定める次の区別である（`firmware/vehicle/tasks/state_task.cpp:339-379`）。

| フラグ | 電文上の意味 | ファームの動作 | C# 側の持ち方 |
|---|---|---|---|
| ARM | **押している間だけ** 1 | 立ち上がりごとに arm/disarm を**トグル**。離しても何も起きない | パルス（状態を持たない） |
| ALT_HOLD／ACRO／POS_HOLD | スイッチの**位置**（保持） | 位置が要求モード。そのエッジで適用 | ラッチ（従来どおり正しい） |

橋渡し（`simulator/unity/native/bridge/sfu_api.h` の `rc_flags`）はビットを変換せず
ControlPacket に写すので、C# が送るフラグがそのままファームのエッジ検出に見える。

**以前の誤り**: ARM のフラグを「利用者が望む状態」を保つラッチとして持っていた。ARM 中に
R を押すとラッチを落とすが、これは**立ち下がり**であり、ファームは立ち下がりでは何も
行わない。よって機体は ARM のまま残り、**床に当てずとも R は 2 回に 1 回しか効かなかった**
（ARM → 何も起きない押下 → 再び ARM）。これが症状の主因である。加えてファームには、
操縦者の操作が無くても DISARM する経路があり（下表）、そこでもラッチと実状態が食い違った。

| ファームが自律的に DISARM する経路 | 場所 |
|---|---|
| 衝撃（加速度の大きさが閾値超 × 連続 2 標本） | `sf_failsafe/failsafe.cpp:266` → `sf_state/state_manager.cpp:342` |
| ジャイロ異常（角速度が閾値超 × 連続 2 標本） | `sf_failsafe/failsafe.cpp:295` → `state_manager.cpp:342` |
| 着陸完了（LANDING → IDLE_GROUND、ToF 着陸検出） | `tasks/state_task.cpp:624` → `state_manager.cpp:237` |
| 通信断（空中でホバー 3 秒 → 自動着陸 → 接地で DISARM） | `state_manager.cpp:366` |
| 電池の緊急（3.0V、空中 → LANDING → 接地で DISARM） | `state_manager.cpp:383` |

ARM の**拒否**（`state_manager.cpp:90` の `requestArm`: IDLE_GROUND でない・ペアリング中・
USB 給電か低電圧・起動校正が未完了）も同じ食い違いを生んでいた。

**いまの形**: `KeyboardRc` は ARM の状態を一切持たない。R の押下が
`ArmPulseMicroseconds`（100 ms、実機のボタンと同じ長さ）のパルスを出すだけである。
どの押下が何を意味するかを決めるのはファームで、実機の送信機のときと同じである。
R を押し続けても 1 回の押下として扱う（キーの遷移で数えるため）。

**パルスはフレーム数ではなく仮想時刻で計る。** 描画 1 フレームはホストの追いつき具合に
よって 0〜12 刻みを回すので、フレーム数で数えると仮想時間での長さが環境によって変わって
しまう。100 ms のパルスはファームの 50 Hz の取り込みに約 5 回標本されるので、取り込みを
1 回逃しても押下は失われない。

そのため `IRcSource` は 2 つに分かれている。**キー**を読むのは 1 フレームに 1 回
（`Read()`。`wasPressedThisFrame` はフレームの間じゅう真を返すため）、**フラグ**を決めるのは
刻みごと（`FlagsAt(frame, nowMicroseconds)`）である。`StickReadCadenceTest` が
「読みはフレームごと・フラグは刻みごと」を両方確かめる。

表示板の `ARMED` ／ `disarmed` は**ファームの状態**（`SfuStepOut.Armed`）を出す。入力は
ARM の状態を持たないので、答えが在るのはそこだけである。パルスが出ている間は
`disarmed (arming...)` を添える。ARM が拒否された場合は、この案内が出て `disarmed` のまま
消えるので、拒否と打鍵の取りこぼしを区別できる。

端末の `rc.arm` も同じ形に直した。`armed=true/false` は「その状態にしたい」という意味の
まま残し、ファームの現在の状態が違うときだけボタンを 1 回押す（同じなら押さない）。
答えに `pressed` と `was_armed` が入る。`rc.set` の `arm` は**断る** ― ビットを押し下げ
続けることはファームには 1 回の押下であり、フレームが保持できる状態ではないためである。

### 追跡カメラの寄せ具合（`ChaseZoom` ／ `ChaseCameraControls`）

| 入力 | 働き |
|---|---|
| マウスホイール 前 / 後 | 寄る / 引く（1 刻み = 1 段。1 フレームに 4 段まで） |
| F / G | 1 段 寄る / 引く |
| C | 既定の距離へ戻す |

| 量 | 値 | 理由 |
|---|---|---|
| 既定の距離 | 0.28 m | 1280x720 の画面に機体が幅 200 px ほどで映り、白いフレーム・4 本の脚・オレンジの M5StampS3 がそれぞれ見分けられる。幅は**軸方向の** 0.0816 m で数える（衝突箱の footprint。±0.023 m のロータに半径 0.015 m のプロペラが届く幅）。よく挙がる 0.12 m は対角のロータ間で、それで決めると幅 130 px にしかならず脚が見分けられない（ブラウザで実測して分かった） |
| 既定の高さ | 0.128 m | 距離 × (0.55 / 1.2)。ズームが無かった頃の見下ろす角 |
| 範囲 | 0.15〜3.0 m | 近い端では機体が 1280 幅の 376 px を占め、脚 1 本を読み取れる。遠い端は機体が幅 20 px を下回る手前 |
| 1 段 | 1.15 倍 | 一定の**比**にする。0.15 m で 0.1 m の刻みは機体までのほとんどの距離になり、3.0 m では見て分からない。既定から近い端まで約 4 段、遠い端まで約 17 段、範囲の全体で 21 段。先に粗い比（1.25）を試すと 3 刻み未満で近い端に達し、寄ったのではなく飛んだように読めた |

**視野角（FOV、55°）は変えない。** 狭めると遠近感が平らになり、機体と壁の間の距離がその距離らしく
見えなくなる。目で飛ばす人はそこから接近の速さを判断するので、寄せるたびに変わるレンズはそれを奪う。
距離と高さに同じ倍率を掛けるので、見下ろす角はどの倍率でも変わらない。

ニアクリップ面（0.02 m）は従来のままで足りる。最も寄った位置（後ろ 0.15 m、上 0.069 m）でも、
最も近いプロペラの先端までは 0.10 m ほどある。ブラウザで実際に撮って、欠けが無いことを確かめた。

**ホイールの単位は環境によって違う。** Chrome で測ると Unity の WebGL のマウスは 1 刻みにつき
`Mouse.current.scroll.y` に **1** を報告する（ブラウザの `deltaY` = −120 に対して **+1**。符号も反転する）。
デスクトップのプレイヤーは Windows の 120 を報告する。`ChaseCameraControls` は翻訳時の環境判定ではなく
**値の大きさ**でどちらの単位かを決めるので、1 つのビルドがブラウザとエディタで同じように振る舞う。

`ChaseZoom` は Unity の型もフレームも持たない。刻みの大きさと限界を、場面もカメラも機体も無しに
EditMode の試験（`Tests/EditMode/ChaseZoomTest.cs`）で確かめられるようにするためである。
`FollowCamera` は求められた距離へ `Time.unscaledDeltaTime` で寄っていくので、一時停止中も寄せられる。
端末からは `camera.zoom` ／ `camera.state`（`docs/commands/sf-unity.md`）で動かせる。

### ビルド

```bash
# 先にファームのモジュールを作る（無ければビルドが理由を添えて失敗する）
nix develop -c just unity-native-build

# 開発用（中継の受け口が入る）
unity build . --target WebGL \
  --execute-method StampFly.Editor.Builders.WebGLBuilder.Build \
  -o Build/WebGL --non-interactive --no-tail

# 配布用（`-stampflyRelease` が STAMPFLY_REMOTE を外す）
sf unity build --release
```

ビルドの入口は `sfu_firmware.{js,wasm}` を出力の `StreamingAssets/` へ複写し、
**Decompression Fallback を有効にする**（GitHub Pages は `Content-Encoding` を付けられない）。
出力は `.gitignore` の `simulator/unity/Build/` に入るのでコミットされない。

### 60fps と実時間比 1.0 の確かめ方（人が行う）

**自動操作のタブは背面扱いで `requestAnimationFrame` が来ない**（計画 §9 (e)）ので、
自動の検証は `?raf=worker` で Worker のタイマーに差し替える。その差し替えは 16 ms の
固定周期で、Chrome 自身の垂直同期ではない。**よって 60fps と実時間比 1.0 は、人が
前面のタブで確かめる必要がある。**

```bash
PYTHONPATH=lib python3 -m sfcli unity serve --dir simulator/unity/Build/WebGL --port 8770
# 開いたタブを前面にしたまま、表示板の次の 3 つを読む:
#   real-time  1.000      ← 実時間比。0.99〜1.01 なら追いつけている
#   NN.N us/tick          ← 1 刻みの所要時間。2500 us に対する余裕
#   60 fps                ← 描画の速さ
```

`BEHIND` の字が出るのは、1 フレームの上限（12 刻み）に当たったときである。読み込みの
直後に一度出るのは正常で、飛行中に出続けるなら追いつけていない。

**実時間比は 1.0 を超えない。** 超えていたら不具合である。刻みは飛ばさず、遅れたら仮想時間を
遅らせるのがこの企画の決まりで、追いつくために実時間より速く走ることはしない（倍率を上げた
ときを除く）。2026-09-21 にここで 1.88 を実測した（背面のタブで溜まった借りを、以後のどの
フレームも 16 ms で 12 刻み＝30 ms 進めて返し続けていた）。`SimClock` が借り自体に
「1 フレームで返せる量」の上限を置くよう直し、`SimClockTest` の
`AtNormalSpeedTheSimulationNeverOutrunsRealTime` が保っている。

### 自動の飛行確認（`sf unity check fly`）

ページを開いた状態で、ARM → 離陸 → ALT_HOLD → 着地 → DISARM を端末から自動で飛ばし、
合否を判定する。命令は `sf unity cmd` と同じ `POST /api/cmd` を通る。使い方と判定の一覧は
[`docs/commands/sf-unity.md`](../../docs/commands/sf-unity.md) §6.5 にある。

```bash
nix develop -c just unity-native-build      # ファームのモジュール（無ければビルドが失敗する）
PYTHONPATH=lib python3 -m sfcli unity build
PYTHONPATH=lib python3 -m sfcli unity serve --dir simulator/unity/Build/WebGL --port 8791 --no-browser
# Chrome で http://127.0.0.1:8791/?raf=worker を開いてから、別の端末で
PYTHONPATH=lib python3 -m sfcli unity check fly --world empty_room --hold-seconds 10
```

時点を仮想時刻で刻む（`sim.wait`）ので、速い機械でも遅い機械でも同じ飛行になる。最初に
`sim.power_cycle` を入れるのは、しばらく開いていたページの仮想時計が既に進んでおり、
「4 秒を待つ」が全てその場で返ってしまうためである。

### 飛行をログで追う

1 回の飛行は `logs/unity/<run_id>.jsonl` に、サーバ・CLI・ページ・ファームの行が 1 つの
流れとして入る。事象の一覧は `docs/commands/sf-unity.md` §7。

```bash
LOG=logs/unity/$(cat logs/unity/latest).jsonl

# 飛行の筋書き（状態遷移と仮想時刻）
jq -r 'select(.event=="sim.flight_state")
       | "\(.sim_us/1000000)s \(.data.previous_state) -> \(.data.state) \(.data.mode)"' $LOG

# 追いつけていたか（1 秒ごと。刻みごとの行は出さない）
jq -r 'select(.event=="sim.stats") | "\(.data.real_time_ratio) \(.data.us_per_tick)us \(.data.fps)fps"' $LOG

# 1 つの命令を端から端まで（CLI → サーバ → ページ → ファーム → サーバ → CLI）
PYTHONPATH=lib python3 -m sfcli unity logs --cmd <cmd_id>

# サーバが拒否した行（0 であること）
jq -c 'select(.event=="log.rejected")' $LOG
```

### ブラウザでの確認の記録（2026-09-20）

`?raf=worker` の自動操作のタブで、無改変のファームが**ブラウザの中で起動して刻み続ける**
ことを確かめた。

| 項目 | 値 |
|------|------|
| ファームのモジュール | 読み込み・起動とも成功（`stampflyFirmwareState.stage = "booted"`） |
| 飛行状態 | INIT → **IDLE_GROUND**、モード STABILIZE、電池 4.19 V |
| 1 刻みの所要時間 | **41.7 µs**（刻みの長さ 2500 µs に対し約 60 倍の余裕） |
| 下向き ToF | 床置きで **(invalid)**。30 mm の盲点の扱いが実機と同じであることを示す |
| フローの品質 | 0.90（`empty_room` の床の値） |
| 出力の大きさ | 12 MB（`.wasm.unityweb` 7.2 MB ＋ `.data.unityweb` 4.0 MB ＋ ファーム 508 KB） |
| コンソール | ファーム関係の誤りなし（URP の後処理シェーダの除外を告げる既知の行のみ） |

**キーボードでの操縦は自動操作では確かめられない。** Unity の Input System はブラウザの
入力の待ち行列を直接読み、DOM の `KeyboardEvent` を見ない。よって `javascript_tool` で
合成したキーは届かず、ARM も押せない。自動で飛ばすには、別の担当の `RemoteBridge` を
場面に置き、`rc.set`（本実装が登録済み）で台本のスティックを流す。**人が前面のタブで
キーボードを叩く分には、そのまま動く。**

CDP（Chrome DevTools Protocol）の `Input.dispatchKeyEvent` ／ `dispatchMouseEvent` でも
同じである（2026-09-21 に確かめた）。背面のタブでは、既存の P（一時停止）を含めどのキーも
Unity へ届かない。タブが実際に前面にある短い間だけは届き、そのときは F ／ G ／ C とホイールが
設計どおりに働くのを確認した（ホイールは 1 刻み = 1 段、前へ回すと寄る）が、**前面かどうかで
結果が変わるので自動の確認には使えない。** キーとホイールの確認は
`Tests/PlayMode/ChaseCameraControlsTest.cs` が仮想の装置を Input System へ積んで行う。
`camera.zoom` ／ `camera.state` は入力の経路を通らないので、ブラウザでも確実に確かめられる。

`?raf=worker` の差し替えは 16 ms の固定周期であり、背面のタブでは実際には 10 fps 程度に
なる。表示板に `BEHIND` が出るのはそのためで、1 刻みの所要時間（41.7 µs）とは別の話である。

### 試験

```bash
unity test . --mode EditMode --output test-results.xml --non-interactive
unity test . --mode PlayMode --output test-results.xml --non-interactive
```

| 置き場 | 見ること |
|---|---|
| `Tests/EditMode/SfuAbiLayoutTest.cs` | 5 つの構造体の大きさ、4 つの欄の位置、ABI の版、食い違うモジュールが名前付きで拒まれること |
| `Tests/EditMode/RcScaleTest.cs` | 振れ幅 → 12 bit の生 ADC、頭打ち、中央、キーボードが無いときの中央、`Reset` が ARM を落とすこと、フラグのビットが ControlPacket と同じこと |
| `Tests/EditMode/SimClockTest.cs` | 60fps で 6 刻み、端数の繰り越し、1 フレームの上限、一時停止中に仮想時刻が進まないこと、コマ送り、倍率、停止の溜まりを捨てること、実測の速さ |
| `Tests/EditMode/ChaseZoomTest.cs` | 1 段が一定の比であること、複数段が 1 段ずつと一致すること、両端で止まること、範囲外の距離を収めること、既定へ戻ること、高さと距離の比が保たれること、0 段と数でない値が何も動かさないこと |
| `Tests/EditMode/ChaseWheelTest.cs` | ホイールの値 → 段の変換。ブラウザの単位（1 刻み = 1）とデスクトップの単位（120）の両方で 1 刻みが 1 段になること、符号、1 刻みに足りない値が何も動かさないこと、1 フレームの上限、単位を見分ける境目が両方から離れていること |
| `Tests/PlayMode/ChaseCameraControlsTest.cs` | **仮想のキーボードとマウスで** F ／ G ／ C とホイールが実際にズームへ届くこと、押し続けても 1 回しか働かないこと、ホイールの符号（前で寄る）、1 フレームの上限、`Time.timeScale` が 0（一時停止）でも寄せられること。ブラウザではキーの事象が Unity へ届かないので（下記「自動操作では確かめられないこと」）、入力の確認はここで行う |
| `Tests/PlayMode/CameraRemoteCommandsTest.cs` | `camera.zoom` ／ `camera.state` が登録され `help` に並ぶこと、返る鍵の全て、求めた距離へカメラが実際に寄ること、範囲外が収められて報告されること、引数が無ければ断ること、部品が消えると登録が外れること |
| `Tests/PlayMode/FirmwareFlightTest.cs` | **実物のファームで ARM → 離陸 → ALT_HOLD で保持 → 着地**、ALT_HOLD に達すること、N 刻みの時計がちょうど N×2500 µs であること、床に置いた機体が水平を保つこと |
| `Tests/PlayMode/FirmwarePowerCycleTest.cs` | **2 回目の電源投入が INIT から通ること**、電源投入ごとに別の複写を開くこと、同じファームの 2 回目の起動が拒まれること |

PlayMode の飛行の試験は `libsfu_firmware.dylib` を要り、無ければ
`nix develop -c just unity-native-build` を文に添えて見送られる（不合格にはしない）。

## 10. 機体の物理パラメータ（`GeneratedParams`）

### 出どころ

機体の質量・慣性・ロータの位置・衝突箱・プロペラの半径・重力は、このプロジェクトの
中では決まらない。リポジトリの基準ファイル `control/models/stampfly_physical.yaml`
から `sf params generate` が
`Assets/StampFly/Runtime/Sim/GeneratedParams.cs` を生成する。SILS の C++ プラント
（`simulator/sils/plant/generated_params.hpp`）と MuJoCo モデル
（`simulator/sils/models/stampfly.xml`）も同じファイルに紐づいているので、Unity 版と
SILS は同じ剛体を積分する。

```bash
# 値を変えるとき
# 1. control/models/stampfly_physical.yaml を編集
sf params generate      # GeneratedParams.cs を含む全生成物を作り直す
sf params check         # 生成の対象外の手書きコピーとの一致を確かめる
```

`GeneratedParams.cs` を手で編集してはならない。CI の `sf params generate --check` が
基準ファイルとの乖離を検出して失敗する。

### 持つ量・持たない量

| 量 | 名前 |
|---|---|
| 質量 | `MassKilograms` |
| 慣性（機体 FLU） | `InertiaForwardAxis`（Ixx）・`InertiaLeftAxis`（Iyy）・`InertiaUpAxis`（Izz） |
| 慣性（Unity 軸） | `InertiaTensorUnityAxes` |
| ロータの位置 | `RotorOffsetMeters`・`RotorHeightMeters` |
| 衝突箱の全長 | `BoxSizeRight`・`BoxSizeUp`・`BoxSizeForward`・`BoxSizeUnityAxes` |
| 床に載った中心の高さ | `RestingCentreHeightMeters` |
| プロペラの半径 | `PropellerRadiusMeters` |
| 重力加速度 | `GravityMetersPerSecondSquared` |

**モータの ODE と推力係数は持たない。** ロータの力を計算するのはファームウェアを
載せた C++ 側（`simulator/sils/plant/actuator_model.hpp`）で、Unity はその結果を
受け取るだけだからである。ここへ出せば、誰も読まないコピーが増える。

### 軸の割り当て

基準ファイルは機体 FLU（x 前・y 左・z 上）で書かれ、Unity は左手系（x 右・y 上・z 前）
である。割り当ては生成器の中で済ませてあるので、C# 側で入れ替える処理は無い。

| 基準ファイル | Unity | 備考 |
|---|---|---|
| Ixx（前軸） | z | |
| Iyy（左右軸） | x | |
| Izz（上軸） | y | |
| 衝突箱の半長 | 全長 | 生成時に 2 倍する（MuJoCo は半長、Unity の `BoxCollider.size` は全長） |

### 読む側

`VehicleBody`・`VehicleAppearance`・`PropellerMesh`・`AccelerometerModel` は、いずれも
自分では数値を持たず `GeneratedParams` を参照する。`VehicleBody` が公開する
`MassKilograms`・`InertiaTensorUnityAxes`・`BoxSizeUnityAxes`・`RestingCentreHeight` は
呼び出し側のための別名で、中身は `GeneratedParams` である。

### 試験

| 置き場 | 見ること |
|---|---|
| `Tests/EditMode/VehicleBodyParamsTest.cs` | `VehicleBody.Apply` が生成された質量・慣性・衝突箱を実際に `Rigidbody` と `BoxCollider` へ書き込むこと、慣性が軸ごとに正しい成分へ届くこと、慣性と重心を PhysX に導かせないこと、減衰・角速度の頭打ち・休止が積分を止めないこと |
| `tests/commands/test_params_generate.py`（Python 側） | 生成器が出す C# が基準ファイルの値を持つこと、軸の割り当てと半長→全長、モータの係数を出さないこと、`--check` が手編集を捕まえること |

---

<a id="english"></a>

# Unity Simulator — Project Skeleton and the Stage 1(c)(e) Checks

## 1. Overview

### About This Directory

Holds the Unity project itself for `docs/plans/unity-simulator.md` (the Unity simulator
plan). As of this commit it contains the project skeleton that later work builds on, plus
the results of the plan's §9 checks **(c) the PhysX rigid body** and **(e) the Unity CLI**.

The side that runs the firmware as WebAssembly — stage 1(a)'s deliverable and
`just unity-native-spike` — is in [`native/README.md`](native/README.md) next door. Unity
ignores root-level folders other than `Assets`, `Packages` and `ProjectSettings`, so
`native/` can sit here.

### Target Audience

Whoever works on this project from stage 2 onward.

### Versions and Composition

| Item | Value |
|------|-------|
| Editor | 6000.6.2f1 (arm64, with the Web module). Pinned in `ProjectSettings/ProjectVersion.txt` |
| Source template | `com.unity.template.urp-blank` 17.2.1 (Universal 3D) |
| Rendering | URP (`com.unity.render-pipelines.universal` 17.6.0) |
| Input | Input System 1.20.0 |
| UI | UI Toolkit (`com.unity.modules.uielements`, a built-in module) |
| Tests | Test Framework 1.8.0 |
| Terminal control | `com.unity.pipeline` 0.7.0-exp.1 (added by `unity pipeline install`) |
| Serialization | Force Text (`m_SerializationMode: 2`). Every `.meta` is committed |

## 2. Opening, Testing, Building

The Unity CLI is `~/.unity/bin/unity`. Run the commands below from this directory.

### Opening

```bash
unity open .                      # open in the editor (a path works without a Hub entry)
unity status --json               # port, PID and state of every running editor
unity close .                     # exit without saving
```

### Testing

```bash
unity test . --mode PlayMode  --output test-results.xml --non-interactive
unity test . --mode EditMode  --output test-results.xml --non-interactive
unity test . --mode PlayMode --filter PhysXStabilityTest --output results.xml --non-interactive
```

Omitting `--mode` runs the editor's default test platform. The report is NUnit XML at
`--output`; `--report-format nunit,junit,both` with `--junit-output` also produces JUnit.
**The exit code is 0 on pass and 8 when any test fails** (Unity's own process exits 2). A
`--filter` matching nothing exits 0 without running anything.

### WebGL Build

WebGL has no built-in command-line build in Unity; the target needs either a build profile
or a static method. This project carries the latter.

```bash
unity build . --target WebGL \
  --execute-method StampFly.Editor.Builders.WebGLBuilder.Build \
  -o /path/to/output --non-interactive --no-tail
```

Passing `--target WebGL` without `--execute-method` exits 2 with
`Target WebGL has no built-in command-line build.`

The folder is `Editor/Builders/` rather than `Editor/Build/` because the repository-root
`.gitignore` rule `build/` — matched case-insensitively on macOS — would exclude the whole
of `Editor/Build/`.

### Driving the Editor from a Terminal

```bash
unity pipeline install --project-path .   # add com.unity.pipeline (once)
unity command                             # list the available commands
unity command --query stampfly --json     # filter by name
unity command stampfly_probe --json       # execute
```

## 3. Stage 1(c) — The PhysX Rigid Body

### What Was Checked

A body of 0.037 kg with inertia (9.16, 13.3, 20.4)×10⁻⁶ kg·m² (body FLU: x forward, y
left, z up), stepped at 400 Hz with `Physics.simulationMode = Script` and
`Physics.Simulate(0.0025)`. The numbers come from
`Assets/StampFly/Tests/PlayMode/PhysXStabilityTest.cs` (6 checks with verdicts) and
`PhysXDiagnosticsProbe.cs` / `PhysXAngularMomentumProbe.cs` (measurements without
verdicts). All 16 pass, in 2.25 s.

### Results

| Check | Measured | Verdict |
|-------|----------|---------|
| 1 Torque response | τ/I = 100 rad/s² on each of the three axes, error **0.0000%** (Unity x: I=1.33e-5, y: I=2.04e-5, z: I=9.16e-6) | Pass (tolerance ±1%) |
| 2 Landing | Dropped from 0.5 m, resting height **0.010300 m**, matching the box's half thickness of 0.0103 m. Height spread over 2 s after landing **5.47×10⁻⁵ m**, residual speed 2.21×10⁻⁷ m/s | Pass |
| 3 Step rate | 400 Hz versus 1600 Hz differ by **4.68 mm** after 1 s (the half-step lag predicts 4.60 mm, a 1.7% gap). 1600 versus 3200 Hz: 0.80 mm, ratio 5.87 (first order predicts 6.0) | Pass — the integrator's order, not an instability |
| 4 Gyroscopic term | **Must be added explicitly.** Without it the angular-momentum direction moves **15.1619 degrees** in 1 s, and that angle does not change from 400 Hz to 6400 Hz | Pass (see below) |
| 5 Accelerometer | At rest on the floor, 200 samples average **9.809999 m/s²** along the body's up axis, maximum deviation **2.06×10⁻³ m/s²** | Pass |
| 6 Reproducibility | Two runs with identical input give an **exactly identical** position after 1 s (difference 0.0 m) | Pass |

### Conclusion on the Gyroscopic Term

**PhysX does not integrate Euler's −ω×(Iω).** For a torque-free body it holds the world
angular velocity ω constant (`[probeE]`: `worldRate` stays (20, 0, 30) throughout 0.2 s).
Because the inertia is asymmetric, that means the world angular momentum L = R I R⁻¹ ω
swings in direction instead of staying put. `|L|` is conserved to seven digits, but the
direction moves 15.1619 degrees per second. That angle is independent of the step (15.1619,
15.1619, 15.1619, 15.1619 and 15.1618 degrees at 400 / 800 / 1600 / 3200 / 6400 Hz), so it is
missing physics rather than integration error. Rotation about a single principal axis has no
coupling and shows zero drift (`[probeD]`).

Adding `AddRelativeTorque(-ω×Iω)` explicitly makes the body rate precess as Euler's equation
requires (`[probeF]`: (20, 0, 30) → (19.1, −3.0, 30.5) → … → (−19.3, −4.0, 30.8)) and holds
the world L direction. That is also the evidence it is **not double counted**: were PhysX
applying the term too, a second copy would make the direction worse, not better.

Evaluate the term at the **step's midpoint** (`GyroscopicTerm.BodyTorqueAtMidpoint`).
Evaluating at the step start injects energy into the explicit integrator: at 400 Hz `|L|`
grows **10.77% per second**. The midpoint form holds it to **0.006%** and cuts the direction
error from 1.1172 to 0.4049 degrees, for the cost of one more cross product.

| 400 Hz over 1 s | Direction error | Angular-momentum magnitude |
|---|---|---|
| Term not added | 15.1619 deg | ×1.000000 |
| Added, evaluated at the step start | 1.1172 deg | ×1.107671 |
| **Added, evaluated at the midpoint** | **0.4049 deg** | **×1.000060** |

### PhysX Settings

The same values live in both `ProjectSettings/DynamicsManager.asset` (the defaults) and
`Assets/StampFly/Runtime/Sim/PhysicsStepSettings.cs` (applied at runtime).

| Setting | Default | This project | Reason |
|---------|---------|--------------|--------|
| `Physics.simulationMode` | FixedUpdate | **Script** | `SimLoop` drives the stepping itself (plan §3) |
| `defaultContactOffset` | 0.01 m | **0.001 m** | The default is the same order as the vehicle's half thickness of 0.0103 m, floating the box off the floor by nearly its own thickness |
| `bounceThreshold` | 2 m/s | **20 m/s** | Stops the vehicle chattering on landing |
| `defaultSolverIterations` | 6 | **12** | Makes contact converge reliably at 0.037 kg |
| `defaultSolverVelocityIterations` | 1 | **4** | As above |
| `defaultMaxDepenetrationVelocity` | 10 m/s | **1 m/s** | Keeps a depenetration push from launching the vehicle |
| `defaultMaxAngularSpeed` | 50 rad/s | **10⁶ rad/s** | A 37 g body reaches high rates; do not clamp them |
| `m_EnableEnhancedDeterminism` | 0 | **1** | Run-to-run reproducibility (check 6). It cannot be set at runtime, only here |

On the body itself (`VehicleBody.Apply`), `automaticInertiaTensor` and
`automaticCenterOfMass` are both false and the inertia tensor is written directly.
`linearDamping` and `angularDamping` are 0 (drag belongs to the C++ plant) and
`sleepThreshold` is 0 (the body must never sleep mid-run).

### Axis Mapping

The MuJoCo model (`simulator/sils/models/stampfly.xml`) uses body FLU (x forward, y left,
z up); Unity is left-handed (x right, y up, z forward). Inertia and the collision box map
as follows.

| FLU | Value | Unity | Value |
|-----|-------|-------|-------|
| Ixx (forward axis) | 9.16e-6 | z | 9.16e-6 |
| Iyy (lateral axis) | 13.3e-6 | x | 13.3e-6 |
| Izz (up axis) | 20.4e-6 | y | 20.4e-6 |
| Collision box half extents | 0.0408 / 0.0408 / 0.0103 | Full size (x, y, z) | 0.0816 / 0.0206 / 0.0816 |

## 4. Stage 1(e) — The Unity CLI

| Item | Result |
|------|--------|
| Selecting a test mode | `--mode PlayMode` or `--mode EditMode`. Omitting it runs the editor's default |
| Report | NUnit XML at `--output <path>`. `--report-format nunit,junit,both` and `--junit-output` are also available |
| Exit code | **0** on pass, **8** on any failure (the inner Unity process exits 2). A `--filter` matching nothing exits 0 |
| Duration | 62 s for the whole CLI invocation on 16 PlayMode tests, of which the tests themselves take 2.25 s; the rest is asset import and editor startup |
| `unity build` for WebGL | `--target WebGL` alone does not work (exit 2, `Target WebGL has no built-in command-line build.`). It needs `--execute-method` or a build profile |
| WebGL build time and size | One near-empty scene: **4 min 13 s** cold, **2 min 16 s** on a repeat with a warm `Library/`. **11 MB** of output (`.wasm.br` 6.90 MB + `.data.br` 3.21 MB + `.framework.js.br` 66 KB + `.loader.js` 28 KB) |
| `unity pipeline install` | Adds `com.unity.pipeline` 0.7.0-exp.1 to `Packages/manifest.json`. Exit code 0 |
| `[CliCommand]` declaration | A **static method** (accessibility does not matter — a private static method registers too; only an instance method is skipped, with a warning). `[CliCommand(name, description, MainThreadRequired = true, RuntimeOnly = false, Tags = new[]{...})]`. The return type is free (string, number, anonymous object, null) and the server wraps it in the response envelope. Parameters take `[CliArg(name, description, Required = , DefaultValue = )]`, which is optional |
| Main thread | `MainThreadRequired` defaults to **true**, because most Unity APIs need the main thread |
| Arrival during play | **It arrives.** Running `stampfly_probe` after entering play mode returned `isPlaying: true` and `frameCount: 2`; it runs on the main thread of the live player loop |
| Port | One per editor (127.0.0.1:7800 in this measurement). `unity status --json` reports the port, project path, PID and state |

The probe command lives in `Assets/StampFly/Editor/Commands/SimulatorProbeCommands.cs` and
is replaced by the plan's `ISimCommands` in stage 6.

The `Unity.Pipeline` assembly in `com.unity.pipeline` is compiled only under
`UNITY_EDITOR || DEVELOPMENT_BUILD || ENABLE_RUNTIME_PIPELINE`. The plan's §4 rule — the
relay endpoint belongs only in development builds — can key its check off that constraint.

## 5. Layout

```
simulator/unity/
├── Assets/
│   ├── Scenes/SampleScene.unity          from the template; rebuilt in stage 3
│   ├── Settings/                         the template's URP settings
│   ├── InputSystem_Actions.inputactions  from the template
│   └── StampFly/
│       ├── Runtime/Sim/                  the StampFly.Sim assembly
│       │   ├── VehicleBody.cs            mass, inertia, collision box, body settings
│       │   ├── PhysicsStepSettings.cs    global PhysX settings and the step length
│       │   ├── GyroscopicTerm.cs         -ω×(Iω) and the world angular momentum
│       │   └── AccelerometerModel.cs     R⁻¹((v_after−v_before)/dt − g)
│       ├── Editor/Builders/              the WebGL build entry point
│       ├── Editor/Commands/              the [CliCommand] probe
│       └── Tests/PlayMode/               6 checks with verdicts + 10 measurements
├── Packages/manifest.json
├── ProjectSettings/
└── native/                               stage 1(a)'s WebAssembly work (separate document)
```

Generated folders (`Library/`, `Temp/`, `Logs/`, `obj/`, `UserSettings/`, `Build/`,
`Data/`, `*.csproj`, `*.sln`) are excluded by the repository-root `.gitignore`. `.meta`
files are not excluded, because they carry the GUIDs every reference relies on. Git LFS is
not used.

## 6. Hand-off to Stage 3

| Item | Detail |
|------|--------|
| Gyroscopic term | `SimLoop` must add `GyroscopicTerm.BodyTorqueAtMidpoint` through `AddRelativeTorque` every step. Omitting it makes the asymmetric-inertia behaviour disagree with the real vehicle |
| Scene | `SampleScene.unity` is still the template's. Stage 3 rebuilds it around `SimLoop`, `VehicleBody` and a floor |
| Compression | WebGL defaults to Brotli (`.br`). GitHub Pages cannot set `Content-Encoding`, so Decompression Fallback must be enabled as the plan's §4 says. This check measured the default |
| EditMode tests | There are none yet. `unity test --mode EditMode` succeeds but runs nothing |
| Step-rate gap | The 4.68 mm between 400 Hz and 1600 Hz is the half-step lag a fixed-step integrator necessarily has; only a finer step reduces it. The MuJoCo version integrates at 4000 Hz, so expect this gap when comparing airborne trajectories |

## 7. Loading and Building Worlds (`StampFly.World`)

### About This Assembly

Reads a world file (`*.world.json`) and builds its room, floor and obstacles into the
scene. The format's specification is [`Schemas/README.md`](Schemas/README.md); this part is
independent of flight and of the firmware.

```
Assets/StampFly/
├── Runtime/World/                    the StampFly.World assembly
│   ├── WorldFile.cs                  the data classes JsonUtility reads
│   ├── WorldFormat.cs                the constants the format fixes (marker, version, defaults, the fifteen kinds)
│   ├── WorldFileReader.cs            loading, and the marker / version / frame checks
│   ├── WorldWriter.cs                writing back out, still in ENU
│   ├── WorldFrames.cs                ENU <-> Unity (all the frame work C# does)
│   ├── ObstacleFactory.cs            fifteen obstacle kinds, including five furniture kinds and dynamic pins
│   ├── BowlingPinFactory.cs          miniature pins with compound colliders and dynamic bodies
│   ├── DynamicObstacleBody.cs        restoring authored poses and velocities of dynamic obstacles
│   ├── WedgeMesh.cs                  the generated wedge a ramp is made of
│   ├── RoomBuilder.cs                room, walls, ceiling, lighting
│   ├── FloorTexture.cs               the five floor patterns, generated (WebGL2-safe)
│   ├── WorldMaterials.cs             URP Lit materials, shared per colour
│   ├── ObstacleInfo.cs               what a surface is (id, type, flow_quality)
│   ├── WorldCatalog.cs               the ten shipped worlds, held as TextAssets
│   ├── WorldLoader.cs                load, build and clear (MonoBehaviour)
│   └── StructuredLog.cs              the log sink (`IStructuredLog`) and its default
├── Editor/WorldTools/                the menu that rebuilds WorldCatalog.asset
└── Tests/EditMode/                   loading, conversion, round trip and catalog tests
```

### Bowling and dynamic obstacles

Choose `Room` → `Bowling` for ten pins, each weighing 12 g and measuring 18 cm high by 5 cm in diameter. The current implementation contains ten worlds and fifteen obstacle kinds. See `docs/plans/unity-simulator.md` §4 for validation and publication records.

Only `bowling_pin` is dynamic. Its compound colliders, including a flat base, collide with the drone and other pins under the existing 400 Hz PhysX stepping in `SimLoop`. Other furniture and obstacles remain static. Touch `RESTART` or B raises `SimLoop.PowerCycled`; `WorldLoader.ResetDynamicBodies()` restores every pin's initial pose and velocity.

World files and browser saves retain authored initial layouts. `WorldWriter.FromScene` excludes dynamic roots rather than writing back fallen poses. Opening furniture editing also restores initial poses; resetting poses alone does not change layout undo history. There is no scoring or throw counter.

### Hand-off to Stages 3 and 5

| Item | How to use it |
|------|---------------|
| Spawn pose | `WorldLoader.SpawnPosition` (Unity coordinates) and `SpawnRotation` (carrying `spawn.yaw_deg`'s 90 degree offset). Both are already converted and can go straight to `VehicleBody` |
| Flow quality | From a raycast hit, `hit.collider.GetComponent<ObstacleInfo>().FlowQuality`. Every collider this package creates carries one — floor, walls and ceiling included — so a hit on the world never comes back without an answer |
| Loading | `WorldLoader.LoadByName("gate_course")` (from the `WorldCatalog`) or `Load(json)`. Both return whether it succeeded and log the reason for a refusal |
| Clearing | `WorldLoader.Clear()`. `Load` clears first, so the two are never both needed in a row |
| Relation to stepping | The project runs PhysX with `simulationMode = Script`, so the loader calls `Physics.SyncTransforms()` right after building. Without it a collider's bounds are stale and a raycast misses until the first `Physics.Simulate` |
| Logging | Assigning an `IStructuredLog` to `WorldLoader.Log` redirects the `world.loaded` / `world.rejected` / `world.cleared` lines. The default writes one JSON line to the Unity console; the page-side logger that carries `run_id` plugs in here once it exists |

### Rebuilding the Catalog (`WorldCatalog.asset`)

Rebuild the catalog after adding, renaming or removing a shipped world. It holds TextAsset
references rather than `StreamingAssets`, so a world left out of the catalog is silently
missing from a WebGL build (`WorldCatalogTest` catches that locally).

```bash
# Editor menu: StampFly > World > Rebuild Catalog
# From a terminal:
/Applications/Unity/Hub/Editor/6000.6.2f1/Unity.app/Contents/MacOS/Unity \
  -batchmode -quit -nographics -projectPath . \
  -executeMethod StampFly.Editor.WorldTools.WorldCatalogBuilder.Rebuild
```

The folder is `Editor/WorldTools/` rather than `Editor/Build/` for the same reason as
`Editor/Builders/` in section 2: the `.gitignore` rule `build/` is matched
case-insensitively.

### Corrections to the Specification (`Schemas/README.md`), 2026-09-20

Measuring in Unity while implementing turned up two errors in the specification's §6.2 and
§6.2.1, so **the specification was corrected**. The implementation and the specification now
agree; each section carries a "Correction" note recording what happened. The two points:

| Place | What it used to say | Corrected to |
|-------|---------------------|--------------|
| §6.2 | "Do not negate the angle when using `AngleAxis`" | The negation **is** required: `AngleAxis` turns right-handed about the axis it is given, so a positive ENU angle takes a minus sign in Unity. §6.2's table (-x / -z / -y) was right from the start; the prose and the code example were what contradicted it |
| §6.2.1 | `AngleAxis(yawDeg - 90, up)` | `AngleAxis(90 - yawDeg, up)`. Under the old form `yaw_deg = 0` faced **west**, contradicting that section's own check table |

§6.2.1's error went unnoticed because **every shipped world spawns at `yaw_deg` ±90, and the
two expressions agree at exactly those two values**. To keep that from hiding anything again:
(a) §6.2.1's check table now includes headings away from ±90 (0, 45, 180, -135) and the
EditMode test `SpawnYawPointsTheVehicleWhereTheFormatSays` checks all six; (b)
`SpawnYawIsNotTheMirroredExpression` names the wrong expression and fails on it; and (c)
`empty_room`'s `spawn.yaw_deg` is now 45 — harmless in an empty 6 m room — with the PlayMode
test `AnOffAxisSpawnHeadingSurvivesTheWholeLoad` checking it from the file through to the
built scene.

A `ring`'s extent (bars cut to the outer circle's chord so the polygon is inscribed) and a
`tunnel` having no floor are now stated in the specification's §3 "Points to note".

### Tests

```bash
unity test . --mode EditMode --output test-results.xml --non-interactive   # 63 tests
unity test . --mode PlayMode --output test-results.xml --non-interactive   # 38 tests
```

EditMode (63) checks that the six shipped worlds load; that malformed JSON, a wrong
`format`, a wrong `version`, a wrong `frame`, an unknown kind and a hollow kind without a
thickness are each refused with a reason; that the defaults for `light`, `segments` and an
obstacle's `flow_quality` apply while **the floor's `flow_quality` never takes one**; that
`WorldFrames` agrees with §6's numbers and inverts; that a read-write-read round trip
preserves the content; and that the catalog holds all six worlds.

PlayMode (38: ten from this section plus section 3's 28 PhysX checks) verifies, for each of
the six worlds, that the obstacle count matches the file; that **each kind's extent matches
the geometry `tools/unity_world/validate.py` assumes** (all 62 obstacles, covering all ten
kinds); that a ray passes through a gate's, a ring's and a tunnel's opening and stops at
their frames; that `flow_quality` can be read from a `pad` and from the floor; and that
loading another world leaves nothing of the previous one.

## 10. The Vehicle's Physical Parameters (`GeneratedParams`)

> Sections 8 and 9 of the Japanese text (logs and terminal control; the tick loop and
> flight) have no English counterpart yet. The numbering follows the Japanese side so the
> two stay in step.

### Where They Come From

The vehicle's mass, inertia, rotor positions, collision box, propeller radius and gravity
are not decided inside this project. `sf params generate` generates
`Assets/StampFly/Runtime/Sim/GeneratedParams.cs` from the repository's single source of
truth, `control/models/stampfly_physical.yaml`. The SILS C++ plant
(`simulator/sils/plant/generated_params.hpp`) and the MuJoCo model
(`simulator/sils/models/stampfly.xml`) are tied to that same file, so the Unity version and
the SILS integrate the same rigid body.

```bash
# To change a value
# 1. edit control/models/stampfly_physical.yaml
sf params generate      # rebuild every generated file, GeneratedParams.cs included
sf params check         # confirm the hand-copied places that are not generated agree
```

Never hand-edit `GeneratedParams.cs`: CI's `sf params generate --check` fails as soon as it
drifts from the source file.

### What It Holds, and What It Does Not

| Quantity | Name |
|---|---|
| Mass | `MassKilograms` |
| Inertia (body FLU) | `InertiaForwardAxis` (Ixx), `InertiaLeftAxis` (Iyy), `InertiaUpAxis` (Izz) |
| Inertia (Unity axes) | `InertiaTensorUnityAxes` |
| Rotor positions | `RotorOffsetMeters`, `RotorHeightMeters` |
| Collision box, full extents | `BoxSizeRight`, `BoxSizeUp`, `BoxSizeForward`, `BoxSizeUnityAxes` |
| Resting centre height | `RestingCentreHeightMeters` |
| Propeller radius | `PropellerRadiusMeters` |
| Gravity | `GravityMetersPerSecondSquared` |

**The motor ODE and the thrust coefficient are absent.** The rotor forces are computed by
the C++ side that runs the firmware (`simulator/sils/plant/actuator_model.hpp`) and Unity
only receives the result, so emitting them here would add a copy nothing reads.

### The Axis Mapping

The source file is written in the body FLU frame (x forward, y left, z up); Unity is
left-handed (x right, y up, z forward). The mapping is applied inside the generator, so no
C# code swaps anything.

| Source file | Unity | Note |
|---|---|---|
| Ixx (forward axis) | z | |
| Iyy (left axis) | x | |
| Izz (up axis) | y | |
| Collision box half extent | Full extent | Doubled at generation time (MuJoCo stores a half extent; Unity's `BoxCollider.size` is a full one) |

### Who Reads Them

`VehicleBody`, `VehicleAppearance`, `PropellerMesh` and `AccelerometerModel` hold no numbers
of their own and read `GeneratedParams` instead. The `MassKilograms`,
`InertiaTensorUnityAxes`, `BoxSizeUnityAxes` and `RestingCentreHeight` that `VehicleBody`
exposes are aliases for its callers; their contents are `GeneratedParams`.

### Tests

| Location | What it checks |
|---|---|
| `Tests/EditMode/VehicleBodyParamsTest.cs` | That `VehicleBody.Apply` really writes the generated mass, inertia and collision box onto the `Rigidbody` and `BoxCollider`; that each inertia reaches the right component; that PhysX is not left to derive the inertia or the centre of mass; and that damping, an angular-rate clamp and sleeping never halt the integration |
| `tests/commands/test_params_generate.py` (Python side) | That the generated C# carries the source file's values; the axis mapping and the half-to-full extent doubling; that the motor coefficients are not emitted; and that `--check` catches a hand edit |
