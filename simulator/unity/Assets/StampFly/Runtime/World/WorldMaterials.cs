using System;
using System.Collections.Generic;
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
    /// </summary>
    public sealed class WorldMaterials : IDisposable
    {
        // URP's standard lit shader. The name is what Shader.Find takes, and it
        // is the shader the project's renderer already ships.
        // URP の標準の Lit シェーダ。Shader.Find に渡す名前で、この企画の描画側が
        // 既に持っているものである。
        private const string LitShaderName = "Universal Render Pipeline/Lit";

        // Fallback for an editor or a player without URP available, so a
        // missing shader never leaves an obstacle invisible.
        // URP が使えない場合の代わり。シェーダが無いせいで障害物が見えなくなる
        // ことを防ぐ。
        private const string FallbackShaderName = "Sprites/Default";

        // Obstacles are matte: a plastic-looking highlight would compete with
        // the floor pattern that the flow sensor's story depends on.
        // 障害物はつや消しにする。プラスチックのような光沢は、フローの説明が頼る
        // 床の模様と見た目で競合する。
        private const float ObstacleSmoothness = 0.15f;
        private const float ObstacleMetallic = 0.0f;

        private static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");
        private static readonly int BaseMapId = Shader.PropertyToID("_BaseMap");
        private static readonly int SmoothnessId = Shader.PropertyToID("_Smoothness");
        private static readonly int MetallicId = Shader.PropertyToID("_Metallic");

        private readonly Dictionary<string, Material> byColor = new Dictionary<string, Material>();
        private readonly List<UnityEngine.Object> owned = new List<UnityEngine.Object>();
        private Shader litShader;

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

            Material created = Create();
            created.name = $"World {key}";
            created.SetColor(BaseColorId, ParseColor(key));
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
            Material created = Create();
            created.name = "World floor";
            created.SetColor(BaseColorId, Color.white);
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

        private Material Create()
        {
            if (litShader == null)
            {
                litShader = Shader.Find(LitShaderName) ?? Shader.Find(FallbackShaderName);
            }

            Material created = new Material(litShader);
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
