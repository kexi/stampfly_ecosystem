/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the lit material templates).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine;

namespace StampFly.Core
{
    /// <summary>
    /// The one place that hands out a URP Lit material to copy from.
    ///
    /// <c>Shader.Find("Universal Render Pipeline/Lit")</c> works in the editor
    /// and fails in a player: a shader reaches a build only when an asset in
    /// the build refers to it, and nothing in this project's scenes did. The
    /// player then either found no shader at all (a magenta object) or fell
    /// back to an unlit one (a white, flat room), with nothing said in any log.
    /// Two templates saved as ASSETS under <c>Resources</c> fix that at its
    /// root: the build carries the shader because these assets refer to it, and
    /// it carries exactly the variants these two need.
    ///
    /// URP Lit のマテリアルの複製元を配る、ただ 1 か所。
    ///
    /// <c>Shader.Find("Universal Render Pipeline/Lit")</c> はエディタでは通り、
    /// プレイヤーでは通らない。シェーダがビルドに入るのは、ビルドの中の資産が
    /// それを参照しているときだけで、この企画の場面はどれも参照していなかった。
    /// その結果プレイヤーは、シェーダを 1 つも見つけられない（マゼンタの物体）か、
    /// 照明の効かないものに落ちる（白く平らな部屋）かのどちらかになり、どのログ
    /// にも何も出なかった。<c>Resources</c> の下に**資産**として保存した 2 つの
    /// 複製元がこれを根から直す。資産が参照するのでシェーダはビルドに入り、この
    /// 2 つが要る変種だけが入る。
    ///
    /// Callers copy a template rather than use it: a template is a shared asset,
    /// and painting it would paint every object that shares it.
    /// 呼ぶ側は複製元をそのまま使わず複製する。複製元は共有の資産で、色を塗れば
    /// それを使う全ての物体が塗り変わるためである。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（見た目）
    /// </summary>
    public static class ShadedMaterials
    {
        /// <summary>
        /// Where the templates live, as <c>Resources.Load</c> spells it — the
        /// path under any <c>Resources</c> folder, without an extension. The
        /// assets themselves are at
        /// <c>Assets/StampFly/Runtime/Resources/StampFly/Materials/</c>.
        /// 複製元の置き場を <c>Resources.Load</c> の綴りで。どこかの
        /// <c>Resources</c> の下での、拡張子を除いたパスである。資産そのものは
        /// <c>Assets/StampFly/Runtime/Resources/StampFly/Materials/</c> に在る。
        /// </summary>
        public const string OpaqueResourcePath = "StampFly/Materials/LitOpaqueTemplate";

        /// <summary>
        /// The translucent template. Its transparent surface keywords are
        /// enabled IN THE ASSET, not at runtime: enabling a keyword on a copy
        /// cannot bring back a shader variant the build never compiled.
        /// 半透明の複製元。透明の面を表すキーワードは実行時ではなく**資産の側**で
        /// 有効にしてある。複製に対してキーワードを立てても、ビルドが翻訳しなかった
        /// シェーダ変種は戻らないためである。
        /// </summary>
        public const string TranslucentResourcePath = "StampFly/Materials/LitTranslucentTemplate";

        /// <summary>The shader every template must carry. / どの複製元も持つべきシェーダ。</summary>
        public const string LitShaderName = "Universal Render Pipeline/Lit";

        /// <summary>
        /// The keyword that makes URP Lit render as a transparent surface. The
        /// asset has it on; a test checks that it stayed on.
        /// URP Lit を透明な面として描かせるキーワード。資産で有効にしてあり、
        /// 試験が有効のままかを確かめる。
        /// </summary>
        public const string TransparentSurfaceKeyword = "_SURFACE_TYPE_TRANSPARENT";

        /// <summary>The colour every material carries. / どのマテリアルも持つ色。</summary>
        public static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");

        private static Material opaqueTemplate;
        private static Material translucentTemplate;

        /// <summary>
        /// A NEW opaque URP Lit material in the given colour. The caller owns it
        /// and destroys it, as with any material made at runtime.
        /// 指定した色の、**新しい** 不透明な URP Lit のマテリアル。実行時に作った
        /// マテリアルの常で、呼ぶ側が持ち、呼ぶ側が捨てる。
        /// </summary>
        public static Material NewOpaque(Color colour)
        {
            return Paint(CopyOf(LoadOpaqueTemplate(), OpaqueResourcePath), colour);
        }

        /// <summary>
        /// A NEW translucent URP Lit material in the given colour, alpha
        /// included. Nothing is set up here beyond the colour: the blend mode,
        /// the render queue and the keywords all come from the template.
        /// 指定した色の、**新しい** 半透明な URP Lit のマテリアル。色には不透明度も
        /// 含む。ここで整えるのは色だけで、混ぜ方・描画の順・キーワードは全て複製元
        /// から来る。
        /// </summary>
        public static Material NewTranslucent(Color colour)
        {
            return Paint(CopyOf(LoadTranslucentTemplate(), TranslucentResourcePath), colour);
        }

        /// <summary>
        /// The opaque template itself, for a test that inspects it. Null when
        /// the asset is missing.
        /// 複製元そのもの。検査する試験のために公開する。資産が無ければ null。
        /// </summary>
        public static Material LoadOpaqueTemplate()
        {
            return opaqueTemplate != null
                ? opaqueTemplate
                : opaqueTemplate = Resources.Load<Material>(OpaqueResourcePath);
        }

        /// <summary>
        /// The translucent template itself, for a test that inspects it. Null
        /// when the asset is missing.
        /// 半透明の複製元そのもの。検査する試験のために公開する。資産が無ければ null。
        /// </summary>
        public static Material LoadTranslucentTemplate()
        {
            return translucentTemplate != null
                ? translucentTemplate
                : translucentTemplate = Resources.Load<Material>(TranslucentResourcePath);
        }

        /// <summary>
        /// Forget the loaded templates, so a test that rebuilds the assets sees
        /// the new ones.
        /// 読み込んだ複製元を忘れる。資産を作り直す試験が新しいほうを見るため。
        /// </summary>
        public static void Forget()
        {
            opaqueTemplate = null;
            translucentTemplate = null;
        }

        /// <summary>
        /// Copy a template, or say loudly why there is nothing to copy.
        ///
        /// There is deliberately no fallback shader here. The previous code fell
        /// back to <c>Sprites/Default</c>, which has no <c>_BaseColor</c> and no
        /// lighting, so every wall and obstacle came out the same flat white and
        /// the real fault — a missing shader — looked like a choice of colour.
        /// A build without these assets is broken and must say so.
        ///
        /// 複製元を複製する。複製するものが無ければ、その理由を大きく述べる。
        ///
        /// ここに代わりのシェーダは意図して置かない。以前のコードは
        /// <c>Sprites/Default</c> に落ちていたが、これには <c>_BaseColor</c> も
        /// 照明も無く、壁も障害物も同じ平らな白になり、本当の不具合（シェーダが
        /// 無いこと）が色の選択のように見えていた。この資産を欠いたビルドは壊れて
        /// おり、そう述べなければならない。
        ///
        /// It still returns a material rather than null, so one broken build
        /// shows itself as wrongly coloured objects beside a logged error
        /// instead of as a NullReferenceException in every renderer.
        /// それでも null ではなくマテリアルを返す。壊れたビルドが、全ての描画側での
        /// NullReferenceException ではなく、記録された誤りの傍らで色のおかしい物体
        /// として現れるようにするためである。
        /// </summary>
        private static Material CopyOf(Material template, string resourcePath)
        {
            bool templateIsMissing = template == null;
            if (templateIsMissing)
            {
                Debug.LogError(
                    $"[ShadedMaterials] no material asset at Resources/{resourcePath}. " +
                    "The build carries no URP Lit shader, so everything it draws will be " +
                    "magenta or unlit. Rebuild the templates from the editor menu " +
                    "'StampFly/Materials/Rebuild Templates'.");

                // Shader.Find is what does NOT work in a player — that is the
                // whole bug. It is used only here, where the editor is the only
                // place the call can still succeed and the player is already
                // known to be broken.
                // Shader.Find はプレイヤーで効かないもの、つまり不具合そのもので
                // ある。ここだけで使うのは、この呼び出しがまだ通り得るのはエディタ
                // だけで、プレイヤーは既に壊れていると分かっているからである。
                return new Material(Shader.Find(LitShaderName));
            }

            return new Material(template);
        }

        /// <summary>Set the base colour, the one thing a caller chooses. / 呼ぶ側が選ぶ唯一のもの、基本の色を置く。</summary>
        private static Material Paint(Material material, Color colour)
        {
            material.SetColor(BaseColorId, colour);
            return material;
        }
    }
}
