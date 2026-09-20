/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — zooming the chase camera).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine;
using UnityEngine.InputSystem;

namespace StampFly.Ui
{
    /// <summary>
    /// The wheel and the keys that move the chase camera in and out.
    ///
    /// 追跡カメラを寄せ・引きするホイールとキー。
    ///
    /// | Input / 入力 | Effect / 働き |
    /// |---|---|
    /// | wheel forward / ホイール前 | closer — 寄る |
    /// | wheel back / ホイール後 | further out — 引く |
    /// | F | closer, one step — 1 段寄る |
    /// | G | further out, one step — 1 段引く |
    /// | C | back to the default distance — 既定の距離へ戻す |
    ///
    /// F, G and C were chosen because every other letter near them is already
    /// taken: W A S D and Z are the sticks, R and H latch ARM and ALT_HOLD, and
    /// P N B and the brackets drive the simulation. F and G sit next to each
    /// other under the same hand, and C is "camera".
    ///
    /// F・G・C を選んだのは、近くの他の文字が既に使われているからである。W A S D
    /// と Z はスティック、R と H は ARM と ALT_HOLD、P N B と角括弧はシミュレーション
    /// の操作である。F と G は同じ手の下で隣り合い、C は camera である。
    ///
    /// A scroll reading is turned into whole notches, and how large one notch is
    /// depends on the platform: measured in Chrome, Unity's WebGL mouse reports
    /// 1 per notch, while a desktop player reports the 120 Windows uses. Rather
    /// than pick one, the reading is scaled by whichever unit it is closer to
    /// (see <see cref="NotchesOf"/>), so the same build behaves the same way in
    /// the browser and in the editor.
    ///
    /// スクロールの値は丸めた刻みの数にするが、1 刻みの大きさは環境によって違う。
    /// Chrome で測ったところ Unity の WebGL のマウスは 1 刻みにつき 1 を報告し、
    /// デスクトップのプレイヤーは Windows が使う 120 を報告する。どちらかを選ぶ
    /// のではなく、値に近い方の単位で割る（<see cref="NotchesOf"/> を見よ）。
    /// 同じビルドがブラウザとエディタで同じように振る舞うようにするためである。
    ///
    /// @design docs/plans/unity-simulator.md §5 段階 3
    /// </summary>
    [RequireComponent(typeof(FollowCamera))]
    public sealed class ChaseCameraControls : MonoBehaviour
    {
        /// <summary>
        /// What one notch of a wheel reports where a scroll is counted in
        /// notches. This is what Unity's WebGL mouse reports, measured in Chrome
        /// by reading <c>Mouse.current.scroll</c> against a known
        /// <c>deltaY</c> of -120.
        /// 刻みの数でスクロールを数える環境で、ホイール 1 刻みが報告する値。Unity の
        /// WebGL のマウスが報告するのがこれである。既知の <c>deltaY</c> = -120 に
        /// 対して <c>Mouse.current.scroll</c> を読み、Chrome で測った。
        /// </summary>
        public const float ScrollPerNotchInNotches = 1.0f;

        /// <summary>
        /// What one notch reports where a scroll is counted in the units Windows
        /// uses, which a desktop player does.
        /// Windows の単位でスクロールを数える環境で、1 刻みが報告する値。デスクトップ
        /// のプレイヤーがこれである。
        /// </summary>
        public const float ScrollPerNotchInWindowsUnits = 120.0f;

        /// <summary>
        /// Above this the reading is taken to be in Windows units rather than in
        /// notches. It sits well above the few notches a hand produces in one
        /// frame and well below one Windows notch, so neither convention lands
        /// near it.
        /// これを超える値は、刻みの数ではなく Windows の単位だとみなす。手が 1 フレーム
        /// で作る数刻みよりは十分大きく、Windows の 1 刻みよりは十分小さいので、
        /// どちらの流儀もこの境目の近くには来ない。
        /// </summary>
        public const float WindowsUnitsThreshold = 20.0f;

        /// <summary>
        /// The most steps one frame's scroll may move. A trackpad flick reports
        /// a large amount in one frame, and without a ceiling one flick would
        /// cross the whole range and leave a person wondering where the vehicle
        /// went.
        /// 1 フレームのスクロールが動かせる段の上限。トラックパッドのはじきは
        /// 1 フレームで大きな量を報告し、上限が無いと 1 回で範囲の端から端まで
        /// 動いて、機体がどこへ行ったのか分からなくなる。
        /// </summary>
        public const int MaximumStepsPerFrame = 4;

        private FollowCamera follow;

        private void Awake()
        {
            follow = GetComponent<FollowCamera>();
        }

        private void Update()
        {
            ReadZoomKeys();
            ReadWheel();
        }

        /// <summary>
        /// F and G by one step, C back to the default. Each acts on the press
        /// rather than while held, so resting a finger on F does not run the
        /// camera into the airframe.
        /// F と G で 1 段、C で既定へ戻す。押している間ではなく押した瞬間に働くので、
        /// F に指を置いたままでもカメラが機体に突っ込まない。
        /// </summary>
        private void ReadZoomKeys()
        {
            Keyboard keyboard = Keyboard.current;
            bool hasKeyboard = keyboard != null;
            if (!hasKeyboard)
            {
                return;
            }

            bool wantsCloser = keyboard.fKey.wasPressedThisFrame;
            if (wantsCloser)
            {
                follow.Zoom.Step(1);
            }

            bool wantsFurtherOut = keyboard.gKey.wasPressedThisFrame;
            if (wantsFurtherOut)
            {
                follow.Zoom.Step(-1);
            }

            bool wantsDefault = keyboard.cKey.wasPressedThisFrame;
            if (wantsDefault)
            {
                follow.Zoom.Reset();
            }
        }

        /// <summary>
        /// The wheel, in whole notches. Forward is closer, which is the
        /// direction a map and a photo viewer both zoom in.
        ///
        /// A wheel pushed forward produces a NEGATIVE <c>deltaY</c> in the
        /// browser and Unity reports it to this side as a POSITIVE
        /// <c>scroll.y</c>, so the reading already means "closer" and is passed
        /// to <see cref="ChaseZoom.Step"/> unchanged. This was measured rather
        /// than assumed: a sign taken on faith here would have zoomed the wrong
        /// way round and read as a broken control.
        ///
        /// ホイールを、丸めた刻みの数で読む。前へ回すと寄る。地図も写真の閲覧も
        /// 同じ向きで拡大する。
        ///
        /// ホイールを前へ回すとブラウザの <c>deltaY</c> は**負**になり、Unity は
        /// それをこちら側へ**正**の <c>scroll.y</c> として報告する。よって読んだ値は
        /// そのまま「寄る」を意味し、<see cref="ChaseZoom.Step"/> へ手を加えずに
        /// 渡せる。ここは推測ではなく実測した。符号を当て推量で決めていれば、逆向きに
        /// 寄って、操作が壊れているように読めたはずである。
        /// </summary>
        private void ReadWheel()
        {
            Mouse mouse = Mouse.current;
            bool hasMouse = mouse != null;
            if (!hasMouse)
            {
                return;
            }

            int notches = NotchesOf(mouse.scroll.ReadValue().y);
            bool isTooSmallToCount = notches == 0;
            if (isTooSmallToCount)
            {
                return;
            }

            follow.Zoom.Step(notches);
        }

        /// <summary>
        /// A scroll reading turned into whole notches, at most
        /// <see cref="MaximumStepsPerFrame"/> of them either way.
        ///
        /// Which unit the reading is in is decided by its own size rather than by
        /// a compile-time platform check, so one build behaves the same in the
        /// browser and in the editor. Truncating means a scroll too small to make
        /// one notch moves nothing, rather than moving a fraction of a step every
        /// frame a finger rests on a trackpad.
        ///
        /// スクロールの値を丸めた刻みの数にする。どちらの向きも
        /// <see cref="MaximumStepsPerFrame"/> 段までである。
        ///
        /// どちらの単位かは、翻訳時の環境の判定ではなく値自身の大きさで決める。
        /// 1 つのビルドがブラウザとエディタで同じように振る舞うようにするためである。
        /// 切り捨てるので、1 刻みに足りないスクロールは何も動かさない。トラックパッド
        /// に指を置いている間、毎フレーム 1 段の何分の 1 かを動かすことにはならない。
        ///
        /// Public so an EditMode test can pin the conversion without a mouse: the
        /// units and the sign were measured in a browser, and a test is what keeps
        /// a later edit from quietly inverting the wheel again.
        /// EditMode の試験がマウス無しでこの変換を固定できるよう public にしてある。
        /// 単位と符号はブラウザで実測したもので、後の変更がホイールを黙って逆向きに
        /// してしまわないようにするのが試験の役目である。
        /// </summary>
        public static int NotchesOf(float scroll)
        {
            bool isInWindowsUnits = Mathf.Abs(scroll) >= WindowsUnitsThreshold;
            float perNotch = isInWindowsUnits
                ? ScrollPerNotchInWindowsUnits
                : ScrollPerNotchInNotches;

            int notches = (int)(scroll / perNotch);
            return Mathf.Clamp(notches, -MaximumStepsPerFrame, MaximumStepsPerFrame);
        }
    }
}
