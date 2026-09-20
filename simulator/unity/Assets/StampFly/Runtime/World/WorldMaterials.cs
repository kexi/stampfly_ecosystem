using System;
using System.Collections.Generic;
using StampFly.Core;
using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// Makes and reuses the URP Lit materials a world needs. A world such as
    /// pillar_forest holds 39 obstacles of one colour, so a material per colour
    /// rather than per obstacle keeps the draw calls and the allocations down.
    /// The cache is owned by whoever created it and is destroyed with the
    /// world, because the materials are created at runtime and Unity does not
    /// collect those on its own.
    ///
    /// 空間が要る URP Lit のマテリアルを作り、使い回す。pillar_forest のように
    /// 同じ色の障害物が 39 個ある空間があるので、障害物ごとではなく色ごとに 1 つ
    /// 持つことで描画呼び出しと確保を抑える。実行時に作ったマテリアルは Unity が
    /// 自動では片付けないため、この入れ物を持つ側が空間と一緒に捨てる。
    ///
    /// Each one is a copy of the shared template <see cref="ShadedMaterials"/>
    /// hands out, which is the only thing that puts URP Lit into a player build.
    /// どれも <see cref="ShadedMaterials"/> が配る共有の複製元の複製である。
    /// プレイヤーのビルドに URP Lit を入れるのはその複製元だけである。
    /// </summary>
    public sealed class WorldMaterials : IDisposable
    {
        // Obstacles are matte: a plastic-looking highlight would compete with
        // the floor pattern that the flow sensor's story depends on.
        // 障害物はつや消しにする。プラスチックのような光沢は、フローの説明が頼る
        // 床の模様と見た目で競合する。
        private const float ObstacleSmoothness = 0.15f;
        private const float ObstacleMetallic = 0.0f;

        private static readonly int BaseMapId = Shader.PropertyToID("_BaseMap");
        private static readonly int SmoothnessId = Shader.PropertyToID("_Smoothness");
        private static readonly int MetallicId = Shader.PropertyToID("_Metallic");

        private readonly Dictionary<string, Material> byColor = new Dictionary<string, Material>();
        private readonly List<UnityEngine.Object> owned = new List<UnityEngine.Object>();

        /// <summary>
        /// A shared opaque material of the given "#rrggbb" colour.
        /// 指定した "#rrggbb" の色の、共有の不透明マテリアル。
        /// </summary>
        public Material Solid(string colorText)
        {
            string key = Normalize(colorText);
            if (byColor.TryGetValue(key, out Material existing))
            {
                return existing;
            }

            Material created = Create(ParseColor(key));
            created.name = $"World {key}";
            byColor.Add(key, created);
            return created;
        }

        /// <summary>
        /// A material carrying a generated texture, for the floor pattern. It
        /// is not shared, because each floor has its own texture.
        /// 生成したテクスチャを持つマテリアル。床の模様に使う。床ごとにテクスチャ
        /// が違うので共有しない。
        /// </summary>
        public Material Textured(Texture2D texture, Vector2 tiling)
        {
            // White, because the base colour multiplies the texture: any other
            // colour would tint the floor pattern the world file describes.
            // 白にする。基本の色はテクスチャに掛かるので、他の色にすると空間
            // ファイルが述べる床の模様に色が付いてしまう。
            Material created = Create(Color.white);
            created.name = "World floor";
            created.SetTexture(BaseMapId, texture);
            created.mainTextureScale = tiling;
            owned.Add(texture);
            return created;
        }

        /// <summary>
        /// Take ownership of a runtime object so it is destroyed with the world.
        /// 実行時に作った物を引き取り、空間と一緒に捨てられるようにする。
        /// </summary>
        public void Own(UnityEngine.Object runtimeObject)
        {
            owned.Add(runtimeObject);
        }

        /// <summary>
        /// Destroy every material and texture this cache created.
        /// この入れ物が作ったマテリアルとテクスチャを全て捨てる。
        /// </summary>
        public void Dispose()
        {
            foreach (Material material in byColor.Values)
            {
                DestroyRuntimeObject(material);
            }

            byColor.Clear();

            foreach (UnityEngine.Object runtimeObject in owned)
            {
                DestroyRuntimeObject(runtimeObject);
            }

            owned.Clear();
        }

        /// <summary>
        /// Parse "#rrggbb" into a colour, falling back to a visible magenta so
        /// a malformed colour shows itself instead of disappearing.
        /// "#rrggbb" を色に読む。読めないときは目立つマゼンタにして、間違いが
        /// 消えるのではなく見えるようにする。
        /// </summary>
        public static Color ParseColor(string colorText)
        {
            if (!string.IsNullOrEmpty(colorText) && ColorUtility.TryParseHtmlString(colorText, out Color parsed))
            {
                return parsed;
            }

            return Color.magenta;
        }

        /// <summary>
        /// A fresh opaque material in one colour, made from the shared template
        /// rather than from a shader looked up by name: the name lookup is what
        /// left a player build with no URP Lit at all (see
        /// <see cref="ShadedMaterials"/>).
        /// 1 色の新しい不透明マテリアル。名前で引いたシェーダからではなく、共有の
        /// 複製元から作る。名前で引くやり方が、プレイヤーのビルドに URP Lit を 1 つ
        /// も残さなかった原因である（<see cref="ShadedMaterials"/> を見よ）。
        /// </summary>
        private Material Create(Color colour)
        {
            Material created = ShadedMaterials.NewOpaque(colour);
            if (created.HasProperty(SmoothnessId))
            {
                created.SetFloat(SmoothnessId, ObstacleSmoothness);
            }

            if (created.HasProperty(MetallicId))
            {
                created.SetFloat(MetallicId, ObstacleMetallic);
            }

            return created;
        }

        /// <summary>
        /// Lower-case the colour so "#FFF000" and "#fff000" share a material.
        /// 色を小文字に揃え、"#FFF000" と "#fff000" でマテリアルを共有する。
        /// </summary>
        private static string Normalize(string colorText)
        {
            return string.IsNullOrEmpty(colorText) ? string.Empty : colorText.ToLowerInvariant();
        }

        /// <summary>
        /// Destroy immediately in the editor, on the next frame while playing.
        /// エディタでは即座に、再生中は次のフレームで捨てる。
        /// </summary>
        private static void DestroyRuntimeObject(UnityEngine.Object runtimeObject)
        {
            if (runtimeObject == null)
            {
                return;
            }

            if (Application.isPlaying)
            {
                UnityEngine.Object.Destroy(runtimeObject);
                return;
            }

            UnityEngine.Object.DestroyImmediate(runtimeObject);
        }
    }
}
