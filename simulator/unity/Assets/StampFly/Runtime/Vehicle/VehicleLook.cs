/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — choosing how the vehicle looks).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using UnityEngine;

namespace StampFly.Vehicle
{
    /// <summary>
    /// The one place that decides which of the two appearances the vehicle is
    /// drawn with, and the only caller either of them needs.
    ///
    /// 機体を 2 つの見た目のどちらで描くかを決める、ただ 1 か所。どちらの見た目も、
    /// 呼ぶ側はここだけで足りる。
    ///
    /// ## The two, and why both stay / 2 つの見た目と、両方を残す理由
    ///
    /// <see cref="MeshAppearance"/> draws the converted STL meshes and is what a
    /// viewer sees. <see cref="VehicleAppearance"/> draws primitives — a plate,
    /// four cans, four generated blades — and needs no asset at all. Keeping the
    /// primitive one is not nostalgia: the meshes live under `Resources` and a
    /// checkout that has not run the converter has none of them, and a vehicle
    /// that is invisible reads as a broken simulator rather than as a missing
    /// step. Falling back to shapes, beside a logged error saying which step was
    /// missed, says what actually happened.
    ///
    /// <see cref="MeshAppearance"/> は変換した STL のメッシュを描く。見る側が目に
    /// するのはこちらである。<see cref="VehicleAppearance"/> は基本形状（板 1 枚・
    /// 缶 4 つ・生成した羽根 4 枚）を描き、資産を一切必要としない。基本形状のほうを
    /// 残すのは懐古ではない。メッシュは `Resources` の下に在り、変換ツールを実行して
    /// いない作業複製にはそれが 1 つも無い。そのとき見えない機体は、手順の抜けでは
    /// なく壊れたシミュレータとして読まれてしまう。抜けた手順を述べる記録とともに
    /// 形状へ落ちることが、実際に起きたことを語る。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（機体の見た目）
    /// </summary>
    public static class VehicleLook
    {
        /// <summary>
        /// Which appearance a caller gets when it does not ask for one. The
        /// converted meshes are the machine as it is built, so they are what the
        /// simulator shows.
        /// 指定しない呼び出し側が得る見た目。変換したメッシュは組み上がった機械
        /// そのものであり、シミュレータが見せるのはそれである。
        /// </summary>
        public const bool UseMeshesByDefault = true;

        /// <summary>
        /// Give the vehicle its appearance and wire it to the loop that turns
        /// the propellers. Adds <see cref="MeshAppearance"/> when the meshes are
        /// wanted and were all found, and <see cref="VehicleAppearance"/>
        /// otherwise.
        /// 機体に見た目を与え、プロペラを回すループへつなぐ。メッシュを望み、かつ
        /// 全て見つかったときは <see cref="MeshAppearance"/> を、そうでなければ
        /// <see cref="VehicleAppearance"/> を足す。
        /// </summary>
        public static MonoBehaviour Attach(GameObject vehicle, Sim.SimLoop loop,
                                           bool useMeshes = UseMeshesByDefault)
        {
            bool wantsPrimitives = !useMeshes;
            if (wantsPrimitives)
            {
                return AttachPrimitives(vehicle, loop);
            }

            var meshes = vehicle.AddComponent<MeshAppearance>();
            meshes.simLoop = loop;

            bool isDrawable = meshes.IsComplete;
            if (isDrawable)
            {
                return meshes;
            }

            // MeshAppearance has already said which asset was missing, so the
            // fallback only needs to say what it is doing about it.
            // どの資産が無かったかは MeshAppearance が既に述べている。落ちる側は、
            // それに対して何をするかだけを述べれば足りる。
            Debug.LogError(
                "[VehicleLook] drawing the vehicle as primitives because a mesh asset " +
                "is missing; the picture is not the real machine.");
            Object.Destroy(meshes);
            return AttachPrimitives(vehicle, loop);
        }

        /// <summary>The primitive appearance, wired to the loop. / 基本形状の見た目。ループへつなぐ。</summary>
        private static MonoBehaviour AttachPrimitives(GameObject vehicle, Sim.SimLoop loop)
        {
            var shapes = vehicle.AddComponent<VehicleAppearance>();
            shapes.simLoop = loop;
            return shapes;
        }
    }
}
