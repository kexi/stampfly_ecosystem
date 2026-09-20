using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// What a surface is, attached to every generated obstacle part and to the
    /// floor. The sensor models (plan stage 5) raycast against the scene and
    /// read the flow quality of whatever they hit, so the value has to live on
    /// the collider's own GameObject rather than in a lookup table.
    ///
    /// Reading it from a raycast hit:
    ///   if (hit.collider.TryGetComponent(out ObstacleInfo info)) { ... }
    /// Every collider this package creates carries one, so a hit on the world
    /// never comes back without an answer.
    ///
    /// 面が何であるかを表し、生成した障害物の各部品と床に付く。センサの模型
    /// （計画の段階 5）は場面へレイを飛ばして当たった面のフロー品質を読むので、
    /// 値は表ではなくコライダ自身の GameObject に載せる必要がある。
    ///
    /// レイの当たり先から読むとき:
    ///   if (hit.collider.TryGetComponent(out ObstacleInfo info)) { ... }
    /// この一式が作るコライダには必ず 1 つ付くので、空間に当たった結果が
    /// 答えを持たずに返ることはない。
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class ObstacleInfo : MonoBehaviour
    {
        [SerializeField]
        private string obstacleId;

        [SerializeField]
        private string obstacleType;

        [SerializeField]
        [Range(0.0f, 1.0f)]
        private float flowQuality = WorldFormat.DefaultObstacleFlowQuality;

        /// <summary>
        /// The obstacle's id from the world file, or <see cref="FloorId"/> for
        /// the room floor. / 空間ファイルの id。床は <see cref="FloorId"/>。
        /// </summary>
        public string ObstacleId => obstacleId;

        /// <summary>
        /// One of the ten kinds, or <see cref="FloorType"/> for the floor.
        /// 10 種のいずれか。床は <see cref="FloorType"/>。
        /// </summary>
        public string ObstacleType => obstacleType;

        /// <summary>
        /// Texture richness of this surface as the flow sensor sees it, 0..1.
        /// オプティカルフローのセンサから見た、この面の模様の豊かさ（0..1）。
        /// </summary>
        public float FlowQuality => flowQuality;

        /// <summary>Id given to the room floor. / 部屋の床に与える id。</summary>
        public const string FloorId = "floor";

        /// <summary>Type given to the room floor. / 部屋の床に与える種類。</summary>
        public const string FloorType = "floor";

        /// <summary>Type given to a room wall. / 部屋の壁に与える種類。</summary>
        public const string RoomSurfaceType = "room";

        /// <summary>
        /// Attach the description of a surface to a GameObject.
        /// 面の素性を GameObject に付ける。
        /// </summary>
        public static ObstacleInfo Attach(GameObject target, string id, string type, float flowQuality)
        {
            ObstacleInfo info = target.AddComponent<ObstacleInfo>();
            info.obstacleId = id;
            info.obstacleType = type;
            info.flowQuality = Mathf.Clamp01(flowQuality);
            return info;
        }
    }
}
