/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the vehicle's parts list).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine;

namespace StampFly.Vehicle
{
    /// <summary>
    /// The one list of the parts the vehicle is drawn from, and the colour each
    /// one is painted. The editor's converter reads it to know which meshes to
    /// write; <see cref="MeshAppearance"/> reads it to know which to load and
    /// how to paint them. Keeping the two sides on one list is what stops a part
    /// from being converted and never drawn, or drawn in a colour the other
    /// simulators do not use.
    ///
    /// 機体を描く部品の一覧と、各部品を塗る色を置く、ただ 1 か所。エディタの変換
    /// ツールはどのメッシュを書くかをここから知り、<see cref="MeshAppearance"/> は
    /// どれを読みどう塗るかをここから知る。両側を 1 つの一覧に載せてあるのは、
    /// 変換したのに描かれない部品や、他のシミュレータと違う色で描かれる部品が
    /// 生まれないようにするためである。
    ///
    /// ## Where the colours come from / 色の出どころ
    ///
    /// Every colour is the `rgba` of the matching `&lt;material&gt;` in
    /// `simulator/shared/assets/meshes/parts/stampfly_fixed.urdf`, which is what
    /// the Genesis simulator draws with. Copying them keeps the two simulators
    /// showing the same machine.
    ///
    /// どの色も `simulator/shared/assets/meshes/parts/stampfly_fixed.urdf` の
    /// 対応する `&lt;material&gt;` の `rgba` である。Genesis 版が描くのに使う値で、
    /// 写すことで 2 つのシミュレータが同じ機械を見せる。
    ///
    /// The frame's alpha is 0.9 in the URDF and 1.0 here. A translucent frame
    /// would need the transparent material and would let the battery show
    /// through the shell, which reads as a fault rather than as a choice; the
    /// URDF's own comment gives no reason for it. Only the propellers keep
    /// their alpha, where it earns its place: a turning disc must not hide what
    /// is under it.
    ///
    /// 機体のアルファは URDF では 0.9、ここでは 1.0 である。半透明の機体は透明の
    /// マテリアルを要し、殻を透かして電池が見えることになる。これは選択ではなく
    /// 不具合に見える。URDF 自身の注記にも理由が無い。アルファを残すのはプロペラ
    /// だけで、そこでは意味がある。回る円盤は下にあるものを隠してはならない。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（機体の見た目）
    /// </summary>
    public static class VehicleParts
    {
        /// <summary>
        /// One part: the STL it comes from, the colour it is painted, and
        /// whether that colour is translucent.
        /// 部品 1 つ。元になる STL、塗る色、そしてその色が半透明かどうか。
        /// </summary>
        public readonly struct Part
        {
            /// <summary>
            /// The STL's name without its extension, which is also the mesh
            /// asset's name and the child object's name.
            /// 拡張子を除いた STL の名前。メッシュ資産の名前であり、子の物体の
            /// 名前でもある。
            /// </summary>
            public readonly string Name;

            /// <summary>What the part is painted. / 部品を塗る色。</summary>
            public readonly Color Colour;

            /// <summary>
            /// True when the part is drawn with the translucent material.
            /// 半透明のマテリアルで描く部品なら真。
            /// </summary>
            public readonly bool IsTranslucent;

            public Part(string name, Color colour, bool isTranslucent = false)
            {
                Name = name;
                Colour = colour;
                IsTranslucent = isTranslucent;
            }
        }

        // The URDF's colours, as `rgba` writes them. Named rather than inlined
        // so a reader can see which part each one belongs to without counting
        // along a row of numbers.
        // URDF の色を `rgba` の綴りのまま。どの部品のものかを数字の並びを数えずに
        // 読めるよう、その場に書かずに名前を付ける。
        private static readonly Color MotorColour = new Color(0.7765f, 0.8314f, 0.8431f);
        private static readonly Color PropellerColour = new Color(1.0000f, 0.0000f, 0.0000f, 0.7f);
        private static readonly Color BatteryColour = new Color(0.1569f, 0.0667f, 0.0039f);
        private static readonly Color BatteryAdapterColour = new Color(0.2196f, 0.2118f, 0.2000f);
        private static readonly Color FrameColour = new Color(0.9000f, 0.9000f, 0.9000f);
        private static readonly Color PcbColour = new Color(0.1020f, 0.0980f, 0.0980f);
        private static readonly Color M5StampColour = new Color(1.0000f, 0.4000f, 0.0000f);

        /// <summary>
        /// The parts that do not turn: the shell, the board, the controller, the
        /// battery and its holder, and the four motor cans.
        /// 回らない部品。殻・基板・制御器・電池とその受け、そしてモータ缶 4 つ。
        /// </summary>
        public static readonly Part[] Fixed =
        {
            new Part("frame", FrameColour),
            new Part("pcb", PcbColour),
            new Part("m5stamps3", M5StampColour),
            new Part("battery", BatteryColour),
            new Part("battery_adapter", BatteryAdapterColour),
            new Part("motor_fr", MotorColour),
            new Part("motor_rr", MotorColour),
            new Part("motor_rl", MotorColour),
            new Part("motor_fl", MotorColour),
        };

        /// <summary>
        /// The four propellers, in the firmware's motor order M1..M4, so index
        /// <c>i</c> here is the propeller driven by <c>MotorOmega<i>i</i></c>.
        /// The order is the one <see cref="VehicleAppearance"/> documents:
        /// M1 front right, M2 rear right, M3 rear left, M4 front left.
        ///
        /// プロペラ 4 つ。ファームのモータの順 M1〜M4 で並べてあるので、ここの
        /// 添字 <c>i</c> は <c>MotorOmega<i>i</i></c> が回すプロペラである。順は
        /// <see cref="VehicleAppearance"/> が記すとおり、M1 が前右・M2 が後右・
        /// M3 が後左・M4 が前左。
        /// </summary>
        public static readonly Part[] Propellers =
        {
            new Part("propeller_fr", PropellerColour, isTranslucent: true),
            new Part("propeller_rr", PropellerColour, isTranslucent: true),
            new Part("propeller_rl", PropellerColour, isTranslucent: true),
            new Part("propeller_fl", PropellerColour, isTranslucent: true),
        };

        /// <summary>
        /// Every part, fixed and turning, which is what the converter walks.
        /// 回らない部品と回る部品の全て。変換ツールが辿るのはこれである。
        /// </summary>
        public static Part[] All()
        {
            var all = new Part[Fixed.Length + Propellers.Length];
            Fixed.CopyTo(all, 0);
            Propellers.CopyTo(all, Fixed.Length);
            return all;
        }

        /// <summary>
        /// Where a converted mesh lives, as <c>Resources.Load</c> spells it: the
        /// path under a <c>Resources</c> folder, with no extension. Loading
        /// through <c>Resources</c> rather than a direct reference is what puts
        /// the meshes in a player build, the same reason
        /// <see cref="StampFly.Core.ShadedMaterials"/> gives for the materials.
        ///
        /// 変換したメッシュの置き場を <c>Resources.Load</c> の綴りで。
        /// <c>Resources</c> の下での、拡張子を除いたパスである。直接の参照では
        /// なく <c>Resources</c> から読むことがメッシュをプレイヤーのビルドに
        /// 入れる。マテリアルについて
        /// <see cref="StampFly.Core.ShadedMaterials"/> が述べるのと同じ理由である。
        /// </summary>
        public static string ResourcePath(string partName)
        {
            return $"{MeshResourceFolder}/{partName}";
        }

        /// <summary>The folder the mesh assets sit in, under <c>Resources</c>. / <c>Resources</c> の下でメッシュ資産が在るフォルダ。</summary>
        public const string MeshResourceFolder = "StampFly/Meshes";
    }
}
