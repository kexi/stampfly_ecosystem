/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 */
using UnityEngine;
using UnityEngine.UIElements;

namespace StampFly.Ui
{
    /// <summary>
    /// Small vector symbols avoid a font or image download for the repository links.
    /// フォントや画像の追加取得を避け、リンク用の小さな図記号を描画する。
    /// </summary>
    public sealed class LinkIcon : VisualElement
    {
        public enum Kind { Repository, Fork, License }
        private readonly Kind kind;

        /// <summary>Use geometry rather than missing Unicode glyphs. / 未収録の文字に頼らず図形を使う。</summary>
        public LinkIcon(Kind kind)
        {
            this.kind = kind;
            pickingMode = PickingMode.Ignore;
            style.width = style.height = 24;
            generateVisualContent += Draw;
        }

        /// <summary>Draw a repository, branch, or notice sheet. / リポジトリ・分岐・文書を描く。</summary>
        private void Draw(MeshGenerationContext context)
        {
            Painter2D painter = context.painter2D;
            painter.strokeColor = new Color(0.9f, 0.95f, 1);
            painter.lineWidth = 2;
            bool isFork = kind == Kind.Fork;
            if (isFork)
            {
                Line(painter, new Vector2(7, 6), new Vector2(7, 18));
                painter.BeginPath();
                painter.MoveTo(new Vector2(17, 6));
                painter.BezierCurveTo(new Vector2(17, 12), new Vector2(7, 10), new Vector2(7, 15));
                painter.Stroke();
                Circle(painter, new Vector2(7, 4));
                Circle(painter, new Vector2(17, 4));
                Circle(painter, new Vector2(7, 20));
                return;
            }
            painter.BeginPath();
            painter.MoveTo(new Vector2(5, 3));
            painter.LineTo(new Vector2(19, 3));
            painter.LineTo(new Vector2(19, 21));
            painter.LineTo(new Vector2(5, 21));
            painter.ClosePath();
            painter.Stroke();
            bool isRepository = kind == Kind.Repository;
            if (isRepository)
            {
                Line(painter, new Vector2(8, 3), new Vector2(8, 21));
                return;
            }
            Line(painter, new Vector2(9, 8), new Vector2(16, 8));
            Line(painter, new Vector2(9, 12), new Vector2(16, 12));
            Line(painter, new Vector2(9, 16), new Vector2(14, 16));
        }

        /// <summary>A segment. / 線分。</summary>
        private static void Line(Painter2D painter, Vector2 from, Vector2 to)
        {
            painter.BeginPath();
            painter.MoveTo(from);
            painter.LineTo(to);
            painter.Stroke();
        }

        /// <summary>A branch endpoint. / 分岐の端点。</summary>
        private static void Circle(Painter2D painter, Vector2 centre)
        {
            painter.BeginPath();
            painter.Arc(centre, 2, 0, 360);
            painter.Stroke();
        }
    }
}
