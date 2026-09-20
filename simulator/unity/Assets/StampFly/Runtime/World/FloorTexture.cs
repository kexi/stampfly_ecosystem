using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// Generates the floor patterns procedurally. Everything here is plain
    /// pixel writing on a Texture2D: it must run under WebGL2, where compute
    /// shaders are not available, and the plan's §2 rules out imported assets.
    ///
    /// One tile of the pattern is generated and then repeated by the material's
    /// tiling, so the texture stays small however large the room is.
    ///
    /// 床の模様を手続きで生成する。ここにあるのは Texture2D への素の画素の
    /// 書き込みだけである。計算シェーダの無い WebGL2 で動く必要があり、計画 §2
    /// が外部の素材を禁じているためである。
    ///
    /// 模様は 1 枚分だけ作り、あとはマテリアルの繰り返しで並べる。部屋が
    /// どれだけ広くてもテクスチャは小さいままになる。
    /// </summary>
    public static class FloorTexture
    {
        // One pattern tile in pixels. Large enough that a checker edge is not
        // visibly stepped from hovering height, small enough to build in a few
        // milliseconds on a browser.
        // 模様 1 枚の画素数。ホバリングの高さから市松の縁が階段に見えず、
        // ブラウザで数ミリ秒で作れる大きさ。
        private const int TileResolution = 128;

        // "noise" mixes the two colours per cell of this many pixels, so the
        // grain is visible to the flow sensor rather than being a blur.
        // "noise" はこの画素数の升ごとに 2 色を混ぜる。粒がぼやけず、フローの
        // センサに見える大きさになる。
        private const int NoiseCellPixels = 8;

        // A grid's line and a stripe's band, as a fraction of the tile.
        // grid の線と stripes の帯の、1 枚に対する割合。
        private const float GridLineFraction = 0.08f;
        private const float StripeFraction = 0.5f;

        // Deterministic, so two runs of the same world look identical and a
        // screenshot can be compared against another run.
        // 決まった値にする。同じ空間を 2 回動かせば同じ見た目になり、画面の
        // 写しを別の実行と比べられる。
        private const int NoiseSeed = 20260920;

        /// <summary>
        /// Build the tile for one floor pattern. The caller owns the texture.
        /// 床の模様 1 枚分を作る。テクスチャの持ち主は呼び出し側。
        /// </summary>
        public static Texture2D Create(string pattern, Color first, Color second)
        {
            Texture2D texture = new Texture2D(TileResolution, TileResolution, TextureFormat.RGBA32, true)
            {
                name = $"Floor {pattern}",
                wrapMode = TextureWrapMode.Repeat,
                // Bilinear keeps a checker edge from shimmering as the camera
                // moves; point sampling would alias badly at a shallow angle.
                // 二次線形にすると、視点が動いても市松の縁がちらつかない。最近傍
                // では浅い角度で激しく折り返す。
                filterMode = FilterMode.Bilinear,
            };

            Color[] pixels = new Color[TileResolution * TileResolution];
            Fill(pattern, pixels, first, second);
            texture.SetPixels(pixels);
            texture.Apply(true);
            return texture;
        }

        /// <summary>
        /// Write the pattern's pixels. / 模様の画素を書く。
        /// </summary>
        private static void Fill(string pattern, Color[] pixels, Color first, Color second)
        {
            switch (pattern)
            {
                case WorldFloorPatterns.Checker:
                    FillChecker(pixels, first, second);
                    return;
                case WorldFloorPatterns.Grid:
                    FillGrid(pixels, first, second);
                    return;
                case WorldFloorPatterns.Stripes:
                    FillStripes(pixels, first, second);
                    return;
                case WorldFloorPatterns.Noise:
                    FillNoise(pixels, first, second);
                    return;
                default:
                    // "plain" and anything unrecognised: the first colour only,
                    // as the format says (Schemas/README.md §2.2).
                    // "plain" と未知の値は 1 色目だけ（README §2.2）。
                    FillPlain(pixels, first);
                    return;
            }
        }

        /// <summary>Two squares per tile, alternating. / 1 枚に 2 升ずつの市松。</summary>
        private static void FillChecker(Color[] pixels, Color first, Color second)
        {
            int half = TileResolution / 2;
            for (int y = 0; y < TileResolution; y++)
            {
                for (int x = 0; x < TileResolution; x++)
                {
                    bool isSecond = (x < half) ^ (y < half);
                    pixels[y * TileResolution + x] = isSecond ? second : first;
                }
            }
        }

        /// <summary>Lines of the second colour along both edges. / 2 辺に沿った線。</summary>
        private static void FillGrid(Color[] pixels, Color first, Color second)
        {
            int lineWidth = Mathf.Max(1, Mathf.RoundToInt(TileResolution * GridLineFraction));
            for (int y = 0; y < TileResolution; y++)
            {
                for (int x = 0; x < TileResolution; x++)
                {
                    bool isLine = x < lineWidth || y < lineWidth;
                    pixels[y * TileResolution + x] = isLine ? second : first;
                }
            }
        }

        /// <summary>Bands along one axis. / 片方の軸に沿った帯。</summary>
        private static void FillStripes(Color[] pixels, Color first, Color second)
        {
            int bandWidth = Mathf.Max(1, Mathf.RoundToInt(TileResolution * StripeFraction));
            for (int y = 0; y < TileResolution; y++)
            {
                bool isSecond = y < bandWidth;
                Color color = isSecond ? second : first;
                for (int x = 0; x < TileResolution; x++)
                {
                    pixels[y * TileResolution + x] = color;
                }
            }
        }

        /// <summary>
        /// A random mix of the two colours, in cells so the grain is visible.
        /// 2 色をでたらめに混ぜる。升ごとにして粒が見えるようにする。
        /// </summary>
        private static void FillNoise(Color[] pixels, Color first, Color second)
        {
            Random.State previous = Random.state;
            Random.InitState(NoiseSeed);

            int cells = Mathf.Max(1, TileResolution / NoiseCellPixels);
            float[] cellMix = new float[cells * cells];
            for (int index = 0; index < cellMix.Length; index++)
            {
                cellMix[index] = Random.value;
            }

            for (int y = 0; y < TileResolution; y++)
            {
                for (int x = 0; x < TileResolution; x++)
                {
                    int cellX = Mathf.Min(cells - 1, x * cells / TileResolution);
                    int cellY = Mathf.Min(cells - 1, y * cells / TileResolution);
                    float mix = cellMix[cellY * cells + cellX];
                    pixels[y * TileResolution + x] = Color.Lerp(first, second, mix);
                }
            }

            // Leave the global generator as it was, so generating a floor does
            // not move anybody else's random sequence.
            // 全体の乱数の状態を元に戻す。床を作ったせいで他の乱数の並びが
            // ずれないようにする。
            Random.state = previous;
        }

        /// <summary>One colour everywhere. / 全面を 1 色で。</summary>
        private static void FillPlain(Color[] pixels, Color first)
        {
            for (int index = 0; index < pixels.Length; index++)
            {
                pixels[index] = first;
            }
        }
    }
}
