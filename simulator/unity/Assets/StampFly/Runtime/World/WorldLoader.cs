using System.Collections.Generic;
using System.Diagnostics;
using StampFly.Core;
using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// Loads a world into the scene and clears the previous one.
    ///
    /// What the SimLoop side needs from this component:
    ///   * <see cref="LoadByName"/> or <see cref="Load"/> builds a world and
    ///     returns whether it succeeded.
    ///   * <see cref="Clear"/> removes it; loading also clears first, so the
    ///     two are never both needed in a row.
    ///   * <see cref="SpawnPosition"/> and <see cref="SpawnRotation"/> give the
    ///     vehicle's starting pose in Unity coordinates, already converted.
    ///   * A raycast hit's collider carries an <see cref="ObstacleInfo"/> with
    ///     the surface's flow quality.
    ///
    /// 空間を場面へ読み込み、前の空間を片付ける。
    ///
    /// SimLoop 側がこの部品に求めるもの:
    ///   * <see cref="LoadByName"/> か <see cref="Load"/> が空間を生成し、成否を
    ///     返す。
    ///   * <see cref="Clear"/> が片付ける。読み込みも先に片付けるので、続けて
    ///     両方を呼ぶ必要は無い。
    ///   * <see cref="SpawnPosition"/> と <see cref="SpawnRotation"/> が、変換
    ///     済みの Unity の座標で機体の出発点を返す。
    ///   * レイの当たり先のコライダが、その面のフロー品質を持つ
    ///     <see cref="ObstacleInfo"/> を載せている。
    /// </summary>
    public sealed class WorldLoader : MonoBehaviour
    {
        [SerializeField]
        [Tooltip("The worlds this build carries. / このビルドが持つ空間。")]
        private WorldCatalog catalog;

        [SerializeField]
        [Tooltip("World loaded on Start, if any. / 起動時に読む空間（任意）。")]
        private string worldOnStart = "";

        private GameObject root;
        private WorldMaterials materials;
        private IStructuredLog log;

        /// <summary>The world now in the scene, or null. / いま場面にある空間。</summary>
        public WorldFile Current { get; private set; }

        /// <summary>Loaded data changed; editors discard old undo history. / 読込変更時に編集履歴を破棄する。</summary>
        public event System.Action Changed;

        /// <summary>Whether a world is loaded. / 空間が読み込まれているか。</summary>
        public bool HasWorld => Current != null;

        /// <summary>
        /// The vehicle's starting position in Unity coordinates (§6.1).
        /// 機体の出発点の位置を Unity の座標で（§6.1）。
        /// </summary>
        public Vector3 SpawnPosition =>
            Current == null ? Vector3.zero : WorldFrames.EnuToUnity(Current.spawn.position);

        /// <summary>
        /// The vehicle's starting rotation in Unity, with the -90 degree offset
        /// spawn.yaw_deg needs (§6.2.1).
        /// 機体の出発点の向きを Unity で。spawn.yaw_deg が要する −90 度のずれを
        /// 含む（§6.2.1）。
        /// </summary>
        public Quaternion SpawnRotation =>
            Current == null ? Quaternion.identity : WorldFrames.SpawnYawToUnity(Current.spawn.yaw_deg);

        /// <summary>
        /// The catalog this loader reads names from. / 名前を引く一覧。
        /// </summary>
        public WorldCatalog Catalog
        {
            get => catalog;
            set => catalog = value;
        }

        /// <summary>
        /// Where log lines go. Set it before loading to send them somewhere
        /// other than the Unity console; the page-side logger that carries
        /// `run_id` is somebody else's work.
        /// ログ 1 行の行き先。Unity のコンソール以外へ送るなら、読み込みの前に
        /// 設定する。`run_id` を持つページ側のログ担当は別の仕事である。
        /// </summary>
        public IStructuredLog Log
        {
            get => log ??= new UnityDebugStructuredLog();
            set => log = value;
        }

        /// <summary>
        /// Load the world named in the inspector, if one is named.
        /// 検査窓で指定された空間があれば読み込む。
        /// </summary>
        private void Start()
        {
            if (!string.IsNullOrEmpty(worldOnStart))
            {
                LoadByName(worldOnStart);
            }
        }

        private void OnDestroy()
        {
            Clear();
        }

        /// <summary>
        /// Load a world from the catalog by name. Returns false and logs a
        /// world.rejected when the catalog has no such world or the file is
        /// refused; the scene keeps whatever it had.
        /// 一覧から名前で空間を読み込む。一覧に無いか、ファイルを断ったときは
        /// false を返して world.rejected を出す。場面はそのまま残る。
        /// </summary>
        public bool LoadByName(string worldName)
        {
            if (catalog == null)
            {
                Reject(worldName, "no world catalog is assigned to the loader");
                return false;
            }

            string json = catalog.Json(worldName);
            if (json == null)
            {
                Reject(worldName, $"the catalog holds no world named '{worldName}'");
                return false;
            }

            return Load(json, worldName);
        }

        /// <summary>
        /// Load a world from JSON text. / JSON の文字列から空間を読み込む。
        /// </summary>
        public bool Load(string json, string sourceName = "")
        {
            WorldReadResult result = WorldFileReader.Read(json);
            if (!result.Ok)
            {
                Reject(sourceName, result.Reason);
                return false;
            }

            Build(result.World);
            return true;
        }

        /// <summary>
        /// Build a world that has already been read and checked.
        /// 読み込みと確認が済んだ空間を生成する。
        /// </summary>
        public void Build(WorldFile world)
        {
            Stopwatch clock = Stopwatch.StartNew();
            ClearWorld();

            materials = new WorldMaterials();
            root = new GameObject($"World:{world.name}");
            root.transform.SetParent(transform, false);

            RoomBuilder.Build(world, root.transform, materials);
            foreach (WorldObstacle obstacle in world.obstacles)
            {
                ObstacleFactory.Create(obstacle, root.transform, materials);
            }

            // The project runs PhysX with simulationMode = Script (the plan's
            // §3: SimLoop drives the stepping), and in that mode Unity does not
            // push transform changes into PhysX on its own. Without this call a
            // collider just created reports stale bounds and a raycast misses
            // it until the first Physics.Simulate. The world is built rarely,
            // so one sync here costs nothing.
            // この企画は PhysX を simulationMode = Script で回す（計画 §3: 刻みは
            // SimLoop が回す）。この方式では Transform の変更が PhysX へ自動で
            // は渡らない。この呼び出しが無いと、作ったばかりのコライダが古い
            // 外形を返し、最初の Physics.Simulate までレイが当たらない。空間の
            // 生成は稀なので、ここで 1 回同期しても費用は無い。
            Physics.SyncTransforms();

            Current = world;
            clock.Stop();
            ReportLoaded(world, clock.Elapsed.TotalMilliseconds);
            Changed?.Invoke();
        }

        /// <summary>
        /// Remove the world from the scene and destroy what it created. Safe to
        /// call when nothing is loaded.
        /// 空間を場面から外し、作った物を捨てる。何も読み込まれていなくても
        /// 呼んでよい。
        /// </summary>
        public void Clear()
        {
            ClearWorld();
            Changed?.Invoke();
        }

        /// <summary>Clear without exposing the intermediate load state. / 読込途中の空状態は通知しない。</summary>
        private void ClearWorld()
        {
            string cleared = Current?.name ?? string.Empty;
            bool hadWorld = root != null;

            if (root != null)
            {
                DestroyNow(root);
                root = null;
            }

            materials?.Dispose();
            materials = null;
            Current = null;

            if (hadWorld)
            {
                Log.Write(LogLevel.Info, WorldLogEvents.Source, WorldLogEvents.Cleared,
                          $"cleared world '{cleared}'",
                          new Dictionary<string, object> { { "world", cleared } });
            }
        }

        /// <summary>
        /// Every surface in the loaded world, for a caller that wants to look
        /// them over rather than raycast.
        /// 読み込んだ空間の全ての面。レイではなく一覧で見たい呼び出し側のため。
        /// </summary>
        public IReadOnlyList<ObstacleInfo> Surfaces()
        {
            if (root == null)
            {
                return System.Array.Empty<ObstacleInfo>();
            }

            return root.GetComponentsInChildren<ObstacleInfo>(true);
        }

        /// <summary>
        /// The root of one obstacle by id, or null. / id で障害物の根を引く。
        /// </summary>
        public GameObject Find(string obstacleId)
        {
            foreach (ObstacleInfo info in Surfaces())
            {
                bool isRoot = info.ObstacleId == obstacleId && info.transform.parent == root.transform;
                if (isRoot)
                {
                    return info.gameObject;
                }
            }

            return null;
        }

        /// <summary>
        /// Log a successful load with its cost and its size.
        /// 読み込みの成功を、所要と大きさとともに出す。
        /// </summary>
        private void ReportLoaded(WorldFile world, double elapsedMs)
        {
            Log.Write(LogLevel.Info, WorldLogEvents.Source, WorldLogEvents.Loaded,
                      $"loaded world '{world.name}' with {world.obstacles.Length} obstacles",
                      new Dictionary<string, object>
                      {
                          { "world", world.name },
                          { "obstacles", world.obstacles.Length },
                          { "elapsed_ms", (float)elapsedMs },
                          { "light", world.light },
                      });
        }

        /// <summary>
        /// Log a refusal with the reason the reader gave.
        /// 読み込み側が述べた理由を添えて、断ったことを出す。
        /// </summary>
        private void Reject(string sourceName, string reason)
        {
            Log.Write(LogLevel.Error, WorldLogEvents.Source, WorldLogEvents.Rejected,
                      $"refused world '{sourceName}': {reason}",
                      new Dictionary<string, object>
                      {
                          { "world", sourceName ?? string.Empty },
                          { "reason", reason },
                      });
        }

        /// <summary>
        /// Destroy at once in the editor, next frame while playing. Clearing
        /// must take effect before the next world is built, so an EditMode test
        /// and an editor tool both need the immediate form.
        /// エディタでは即座に、再生中は次のフレームで捨てる。次の空間を作る前に
        /// 片付けが効いている必要があるので、EditMode の試験もエディタの道具も
        /// 即座の形を要る。
        /// </summary>
        private static void DestroyNow(GameObject target)
        {
            if (Application.isPlaying)
            {
                Destroy(target);
                // Detaching makes the old root invisible to a search made
                // before Destroy takes effect at the end of the frame.
                // 親から外しておくと、フレーム末に Destroy が効くまでの間の
                // 検索から古い根が見えなくなる。
                target.transform.SetParent(null, false);
                target.SetActive(false);
                return;
            }

            DestroyImmediate(target);
        }
    }
}
