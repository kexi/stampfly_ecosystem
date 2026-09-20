/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the lit material templates).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.IO;
using StampFly.Core;
using UnityEditor;
using UnityEngine;

namespace StampFly.Editor.MaterialTools
{
    /// <summary>
    /// Writes the two URP Lit material assets <see cref="ShadedMaterials"/>
    /// copies from, and the folders under <c>Resources</c> that hold them.
    ///
    /// The assets are what puts the shader in a player build — a shader reaches
    /// a build only through an asset that refers to it — so they are checked in
    /// rather than made at runtime. They are built from the editor rather than
    /// written by hand because URP Lit has some forty properties whose
    /// consistency (surface, blend, queue, keywords, passes) the material editor
    /// maintains and a hand-written YAML would only approximate.
    ///
    /// <see cref="ShadedMaterials"/> が複製元にする URP Lit のマテリアル資産 2 つ
    /// と、それを置く <c>Resources</c> の下のフォルダを書く。
    ///
    /// プレイヤーのビルドにシェーダを入れるのはこの資産である（シェーダがビルドに
    /// 入るのは、それを参照する資産を通してだけである）。よって実行時に作らず、
    /// git に入れる。手書きせずエディタから作るのは、URP Lit が 40 ほどの
    /// プロパティを持ち、その辻褄（面の種類・混ぜ方・描画の順・キーワード・パス）を
    /// マテリアルの編集画面が保つのに対し、手書きの YAML では近似にしかならない
    /// ためである。
    ///
    /// Run it from the menu after changing what a template must carry.
    /// 複製元が持つべきものを変えたあと、メニューから実行する。
    /// </summary>
    public static class MaterialTemplateBuilder
    {
        /// <summary>Where the template assets are written. / 複製元の資産の置き場。</summary>
        public const string MaterialsFolder =
            "Assets/StampFly/Runtime/Resources/StampFly/Materials";

        /// <summary>The opaque template's asset path. / 不透明の複製元の場所。</summary>
        public static readonly string OpaquePath =
            $"{MaterialsFolder}/{Path.GetFileName(ShadedMaterials.OpaqueResourcePath)}.mat";

        /// <summary>The translucent template's asset path. / 半透明の複製元の場所。</summary>
        public static readonly string TranslucentPath =
            $"{MaterialsFolder}/{Path.GetFileName(ShadedMaterials.TranslucentResourcePath)}.mat";

        private const string MenuPath = "StampFly/Materials/Rebuild Templates";

        // URP Lit's surface type: 0 opaque, 1 transparent. Setting the float
        // alone is not enough -- the keyword, the queue, the blend factors and
        // ZWrite all follow from it, and the shader reads each of them.
        // URP Lit の面の種類。0 が不透明、1 が透明。この値だけでは足りない。
        // キーワード・描画の順・混ぜ方の係数・ZWrite がこれに従い、シェーダは
        // そのどれも読む。
        private const float SurfaceOpaque = 0.0f;
        private const float SurfaceTransparent = 1.0f;

        // URP Lit's blend mode for a transparent surface: 0 is alpha, which is
        // what a propeller disc needs to let the floor show through.
        // 透明な面での URP Lit の混ぜ方。0 がアルファで、プロペラの円盤が床を
        // 透かすのに要るのはこれである。
        private const float BlendAlpha = 0.0f;

        // A transparent surface does not write depth: two blended surfaces must
        // both be drawn, and a depth write would hide whichever came second.
        // 透明な面は深度を書かない。混ぜる面が 2 つあればどちらも描く必要があり、
        // 深度を書けば後に来たほうが隠れてしまう。
        private const float ZWriteOff = 0.0f;
        private const float ZWriteOn = 1.0f;

        // Alpha blending: source times its own alpha, destination times what is
        // left. `_SrcBlend`/`_DstBlend` take UnityEngine.Rendering.BlendMode.
        // アルファの混ぜ方。元は自身のアルファを掛け、先は残りを掛ける。
        // `_SrcBlend`／`_DstBlend` は UnityEngine.Rendering.BlendMode を取る。
        private const float BlendSrcAlpha = (float)UnityEngine.Rendering.BlendMode.SrcAlpha;
        private const float BlendOneMinusSrcAlpha =
            (float)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha;
        private const float BlendOne = (float)UnityEngine.Rendering.BlendMode.One;
        private const float BlendZero = (float)UnityEngine.Rendering.BlendMode.Zero;

        // What a template starts as before a caller paints it. Mid grey at full
        // opacity, so an object drawn from an unpainted copy still reads as lit
        // geometry rather than as a mistake.
        // 呼ぶ側が色を塗る前の複製元の色。中間の灰で不透明度はいっぱい。塗られて
        // いない複製で描かれた物体も、誤りではなく照らされた形として見えるように。
        private static readonly Color OpaqueTemplateColor = new Color(0.5f, 0.5f, 0.5f, 1.0f);
        private static readonly Color TranslucentTemplateColor = new Color(0.5f, 0.5f, 0.5f, 0.5f);

        // URP Lit renders a transparent surface after everything opaque.
        // URP Lit は透明な面を、不透明なもの全ての後に描く。
        private const int TransparentQueue = (int)UnityEngine.Rendering.RenderQueue.Transparent;

        // A material's own queue follows its shader unless told otherwise.
        // マテリアルの描画の順は、指定しなければシェーダに従う。
        private const int QueueFromShader = -1;

        // URP reads this tag to decide which pass a material belongs to.
        // URP はこの札を読んで、マテリアルがどのパスに属するかを決める。
        private const string RenderTypeTag = "RenderType";
        private const string RenderTypeOpaque = "Opaque";
        private const string RenderTypeTransparent = "Transparent";

        private static readonly int SurfaceId = Shader.PropertyToID("_Surface");
        private static readonly int BlendId = Shader.PropertyToID("_Blend");
        private static readonly int SrcBlendId = Shader.PropertyToID("_SrcBlend");
        private static readonly int DstBlendId = Shader.PropertyToID("_DstBlend");
        private static readonly int ZWriteId = Shader.PropertyToID("_ZWrite");

        /// <summary>
        /// Write both templates, creating the folders they need. Returns false
        /// and says why when the URP Lit shader cannot be found, which in the
        /// editor means URP is not installed.
        /// 複製元 2 つを書く。要るフォルダは作る。URP Lit のシェーダが見つからない
        /// ときは false を返し、理由を述べる。エディタでそうなるのは URP が入って
        /// いない場合である。
        /// </summary>
        public static bool Rebuild()
        {
            Shader lit = Shader.Find(ShadedMaterials.LitShaderName);
            bool shaderIsMissing = lit == null;
            if (shaderIsMissing)
            {
                Debug.LogError(
                    $"[MaterialTemplateBuilder] the shader '{ShadedMaterials.LitShaderName}' " +
                    "is not in this project; install the Universal RP package first.");
                return false;
            }

            EnsureFolders();
            WriteOpaque(lit);
            WriteTranslucent(lit);

            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            ShadedMaterials.Forget();
            return true;
        }

        /// <summary>The opaque template: URP Lit as it comes. / 不透明の複製元。素の URP Lit。</summary>
        private static void WriteOpaque(Shader lit)
        {
            Material material = LoadOrCreate(OpaquePath, lit);

            material.SetFloat(SurfaceId, SurfaceOpaque);
            material.SetFloat(SrcBlendId, BlendOne);
            material.SetFloat(DstBlendId, BlendZero);
            material.SetFloat(ZWriteId, ZWriteOn);
            material.DisableKeyword(ShadedMaterials.TransparentSurfaceKeyword);
            material.SetOverrideTag(RenderTypeTag, RenderTypeOpaque);
            material.renderQueue = QueueFromShader;
            material.SetColor(ShadedMaterials.BaseColorId, OpaqueTemplateColor);

            EditorUtility.SetDirty(material);
        }

        /// <summary>
        /// The translucent template. Every part of being transparent is set
        /// HERE, in the asset, because a build compiles the variants its assets
        /// ask for: a copy that enables the keyword at runtime asks for a
        /// variant that was never compiled and gets nothing.
        /// 半透明の複製元。透明であることの全てをここ、資産の側で設定する。ビルドは
        /// 資産が求める変種を翻訳するので、実行時にキーワードを立てる複製は、翻訳
        /// されなかった変種を求めることになり、何も得られない。
        /// </summary>
        private static void WriteTranslucent(Shader lit)
        {
            Material material = LoadOrCreate(TranslucentPath, lit);

            material.SetFloat(SurfaceId, SurfaceTransparent);
            material.SetFloat(BlendId, BlendAlpha);
            material.SetFloat(SrcBlendId, BlendSrcAlpha);
            material.SetFloat(DstBlendId, BlendOneMinusSrcAlpha);
            material.SetFloat(ZWriteId, ZWriteOff);
            material.EnableKeyword(ShadedMaterials.TransparentSurfaceKeyword);
            material.SetOverrideTag(RenderTypeTag, RenderTypeTransparent);
            material.renderQueue = TransparentQueue;
            material.SetColor(ShadedMaterials.BaseColorId, TranslucentTemplateColor);

            EditorUtility.SetDirty(material);
        }

        /// <summary>
        /// The material at that path, created there when it is not there yet.
        /// Reusing the existing asset keeps its guid, so a scene or prefab
        /// referring to it does not lose the reference on a rebuild.
        /// その場所のマテリアル。まだ無ければそこに作る。在る資産を使い回すと
        /// guid が変わらないので、それを参照する場面やプレハブが作り直しで参照を
        /// 失わない。
        /// </summary>
        private static Material LoadOrCreate(string assetPath, Shader lit)
        {
            Material existing = AssetDatabase.LoadAssetAtPath<Material>(assetPath);
            bool isThere = existing != null;
            if (isThere)
            {
                existing.shader = lit;
                return existing;
            }

            var created = new Material(lit) { name = Path.GetFileNameWithoutExtension(assetPath) };
            AssetDatabase.CreateAsset(created, assetPath);
            return created;
        }

        /// <summary>
        /// Create every folder on the way to the materials folder. Unity's
        /// CreateFolder makes one level at a time.
        /// マテリアルの置き場までの各段のフォルダを作る。Unity の CreateFolder は
        /// 一段ずつしか作らない。
        /// </summary>
        private static void EnsureFolders()
        {
            string[] parts = MaterialsFolder.Split('/');
            string path = parts[0];

            for (int level = 1; level < parts.Length; level++)
            {
                string child = $"{path}/{parts[level]}";
                bool missing = !AssetDatabase.IsValidFolder(child);
                if (missing)
                {
                    AssetDatabase.CreateFolder(path, parts[level]);
                }

                path = child;
            }
        }

        [MenuItem(MenuPath)]
        private static void RebuildFromMenu()
        {
            bool ok = Rebuild();
            if (ok)
            {
                Debug.Log($"[MaterialTemplateBuilder] wrote {OpaquePath} and {TranslucentPath}");
            }
        }
    }
}
