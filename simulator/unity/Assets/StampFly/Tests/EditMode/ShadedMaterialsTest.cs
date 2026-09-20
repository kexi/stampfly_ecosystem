/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the lit material templates).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Core;
using StampFly.World;
using UnityEngine;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// What the material templates guarantee: a player build draws a lit,
    /// coloured room and a translucent propeller disc.
    ///
    /// The bug these tests stand against showed nothing in the editor and
    /// nothing in any log. The runtime code looked its shader up by name with
    /// <c>Shader.Find</c>, which succeeds in the editor, where every shader is
    /// loaded, and fails in a player, where a shader is included only when an
    /// asset refers to it. The served page came out a white room with a magenta
    /// vehicle. These tests check the two things that keep that from coming
    /// back: the template assets EXIST under Resources with the URP Lit shader
    /// on them, and the translucent one is transparent IN THE ASSET rather than
    /// by a call made at runtime.
    ///
    /// 複製元のマテリアルが保証すること。プレイヤーのビルドが、照らされた色付きの
    /// 部屋と半透明のプロペラの円盤を描くこと。
    ///
    /// この試験が備える不具合は、エディタにも、どのログにも何も出なかった。実行時の
    /// コードは <c>Shader.Find</c> でシェーダを名前で引いていた。これは全ての
    /// シェーダが読み込まれているエディタでは通り、資産が参照しているものしか含ま
    /// れないプレイヤーでは通らない。配信されたページは、白い部屋にマゼンタの機体と
    /// いう姿になった。この試験は、それが戻らないための 2 つを確かめる。複製元の
    /// 資産が URP Lit のシェーダを載せて Resources の下に**在る**ことと、半透明の
    /// ほうが実行時の呼び出しではなく**資産の側で**透明であることである。
    /// </summary>
    public sealed class ShadedMaterialsTest
    {
        /// <summary>A colour no default would produce by chance. / 既定では偶然生じない色。</summary>
        private static readonly Color DistinctColour = new Color(0.13f, 0.57f, 0.42f, 1.0f);

        /// <summary>A half-transparent colour, to check alpha survives. / 不透明度が残るかを見る、半分透けた色。</summary>
        private static readonly Color HalfTransparent = new Color(0.9f, 0.2f, 0.3f, 0.5f);

        /// <summary>How close two colour channels must be. / 色の成分がどれだけ近ければよいか。</summary>
        private const float ColourTolerance = 1e-4f;

        [SetUp]
        public void SetUp()
        {
            // The templates are cached after the first load, and an earlier test
            // may have cached a missing one.
            // 複製元は最初の読み込みの後は覚えられる。前の試験が「無い」状態を
            // 覚えている場合がある。
            ShadedMaterials.Forget();
        }

        [Test]
        public void BothTemplatesLoadFromResources()
        {
            Material opaque = ShadedMaterials.LoadOpaqueTemplate();
            Material translucent = ShadedMaterials.LoadTranslucentTemplate();

            Assert.That(opaque, Is.Not.Null,
                        $"Resources/{ShadedMaterials.OpaqueResourcePath} is missing; run " +
                        "StampFly > Materials > Rebuild Templates");
            Assert.That(translucent, Is.Not.Null,
                        $"Resources/{ShadedMaterials.TranslucentResourcePath} is missing; run " +
                        "StampFly > Materials > Rebuild Templates");
        }

        [Test]
        public void BothTemplatesCarryTheUrpLitShader()
        {
            // The shader's NAME is what matters: it is what decides that a URP
            // build compiles it at all, and a template on some other shader
            // would build but draw unlit.
            // 大事なのはシェーダの**名前**である。URP のビルドがそれを翻訳するか
            // どうかを決めるのはこれで、別のシェーダを載せた複製元はビルドは通る
            // が照明の効かない絵を描く。
            Assert.That(ShadedMaterials.LoadOpaqueTemplate().shader.name,
                        Is.EqualTo(ShadedMaterials.LitShaderName));
            Assert.That(ShadedMaterials.LoadTranslucentTemplate().shader.name,
                        Is.EqualTo(ShadedMaterials.LitShaderName));
        }

        [Test]
        public void TheTranslucentTemplateIsTransparentInTheAsset()
        {
            Material translucent = ShadedMaterials.LoadTranslucentTemplate();

            // Enabled in the asset, so the build compiles this variant. Calling
            // EnableKeyword on a copy at runtime cannot bring back a variant a
            // build left out, which is why the check is on the ASSET.
            // 資産の側で有効にしてあるので、ビルドはこの変種を翻訳する。実行時に
            // 複製へ EnableKeyword を呼んでも、ビルドが落とした変種は戻らない。
            // よって確かめるのは**資産**である。
            Assert.That(translucent.IsKeywordEnabled(ShadedMaterials.TransparentSurfaceKeyword),
                        Is.True,
                        $"'{ShadedMaterials.TransparentSurfaceKeyword}' is off in " +
                        $"Resources/{ShadedMaterials.TranslucentResourcePath}; the build would " +
                        "carry no transparent variant and the propellers would be opaque");

            Assert.That(translucent.renderQueue,
                        Is.EqualTo((int)UnityEngine.Rendering.RenderQueue.Transparent),
                        "a transparent surface must be drawn after the opaque ones");
        }

        [Test]
        public void TheOpaqueTemplateIsNotTransparent()
        {
            Material opaque = ShadedMaterials.LoadOpaqueTemplate();

            Assert.That(opaque.IsKeywordEnabled(ShadedMaterials.TransparentSurfaceKeyword),
                        Is.False,
                        "the opaque template must not blend; a blended wall would let the " +
                        "world behind it show through");
        }

        [Test]
        public void ANewOpaqueMaterialCarriesTheColourAsBaseColor()
        {
            Material made = ShadedMaterials.NewOpaque(DistinctColour);

            try
            {
                // _BaseColor, not _Color: URP Lit reads _BaseColor, and the
                // fallback shader this code used to land on had neither, which
                // is why a wrong colour looked like no colour at all.
                // _Color ではなく _BaseColor。URP Lit が読むのは _BaseColor で、
                // 以前このコードが落ちていた代わりのシェーダはそのどちらも持たな
                // かった。誤った色が色そのものの不在に見えたのはそのためである。
                Assert.That(made.HasProperty(ShadedMaterials.BaseColorId), Is.True);
                AssertSameColour(made.GetColor(ShadedMaterials.BaseColorId), DistinctColour);
            }
            finally
            {
                Object.DestroyImmediate(made);
            }
        }

        [Test]
        public void ANewTranslucentMaterialKeepsItsAlphaAndItsTransparency()
        {
            Material made = ShadedMaterials.NewTranslucent(HalfTransparent);

            try
            {
                AssertSameColour(made.GetColor(ShadedMaterials.BaseColorId), HalfTransparent);
                Assert.That(made.IsKeywordEnabled(ShadedMaterials.TransparentSurfaceKeyword),
                            Is.True, "a copy must inherit the template's transparency");
            }
            finally
            {
                Object.DestroyImmediate(made);
            }
        }

        [Test]
        public void EachCallReturnsItsOwnMaterialAndLeavesTheTemplateAlone()
        {
            Color templateColourBefore =
                ShadedMaterials.LoadOpaqueTemplate().GetColor(ShadedMaterials.BaseColorId);

            Material first = ShadedMaterials.NewOpaque(DistinctColour);
            Material second = ShadedMaterials.NewOpaque(Color.white);

            try
            {
                // Painting one must not paint the other, and neither must paint
                // the shared asset: a template tinted at runtime would take
                // every object made from it afterwards with it.
                // 片方を塗っても、もう片方が塗り変わってはならない。共有の資産も
                // 塗り変わってはならない。実行時に色の付いた複製元は、その後それ
                // から作られる物体を全て連れて行ってしまう。
                Assert.That(first, Is.Not.SameAs(second));
                AssertSameColour(first.GetColor(ShadedMaterials.BaseColorId), DistinctColour);
                AssertSameColour(second.GetColor(ShadedMaterials.BaseColorId), Color.white);
                AssertSameColour(
                    ShadedMaterials.LoadOpaqueTemplate().GetColor(ShadedMaterials.BaseColorId),
                    templateColourBefore);
            }
            finally
            {
                Object.DestroyImmediate(first);
                Object.DestroyImmediate(second);
            }
        }

        [Test]
        public void AWorldMaterialCarriesTheColourTheWorldFileAsked()
        {
            using (var materials = new WorldMaterials())
            {
                Material wall = materials.Solid("#3f7fbf");

                Assert.That(wall.shader.name, Is.EqualTo(ShadedMaterials.LitShaderName),
                            "a world surface must be lit, or the room comes out flat white");
                AssertSameColour(wall.GetColor(ShadedMaterials.BaseColorId),
                                 WorldMaterials.ParseColor("#3f7fbf"));
            }
        }

        [Test]
        public void TwoSurfacesOfOneColourShareOneMaterial()
        {
            using (var materials = new WorldMaterials())
            {
                // The colour is normalised, so the same colour written either
                // way is one material and one draw call's worth of state.
                // 色は揃えてから引くので、どちらの綴りで書かれた同じ色も 1 つの
                // マテリアル、1 回ぶんの描画の状態になる。
                Assert.That(materials.Solid("#FF8800"), Is.SameAs(materials.Solid("#ff8800")));
            }
        }

        /// <summary>Compare two colours channel by channel. / 色を成分ごとに比べる。</summary>
        private static void AssertSameColour(Color actual, Color wanted)
        {
            Assert.That(actual.r, Is.EqualTo(wanted.r).Within(ColourTolerance));
            Assert.That(actual.g, Is.EqualTo(wanted.g).Within(ColourTolerance));
            Assert.That(actual.b, Is.EqualTo(wanted.b).Within(ColourTolerance));
            Assert.That(actual.a, Is.EqualTo(wanted.a).Within(ColourTolerance));
        }
    }
}
