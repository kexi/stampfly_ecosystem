/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — editor command probe).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using StampFly.Sim;
using Unity.Pipeline.Commands;
using UnityEditor;
using UnityEngine;

namespace StampFly.Editor.Commands
{
    /// <summary>
    /// The stage 1(e) probe for <c>unity command</c>: confirms a
    /// <c>[CliCommand]</c> declared in this project reaches the editor, and
    /// reports whether it also arrives while the editor is in play mode.
    /// Stage 6 replaces this with the real <c>ISimCommands</c> surface.
    ///
    /// 段階 1(e) の <c>unity command</c> 検証。このプロジェクトで宣言した
    /// <c>[CliCommand]</c> がエディタに届くこと、再生中にも届くかを確かめる。
    /// 段階 6 で本来の <c>ISimCommands</c> に置き換える。
    /// </summary>
    public static class SimulatorProbeCommands
    {
        /// <summary>
        /// Reports the editor's play state and the vehicle's physical constants,
        /// so the answer proves the command ran inside this project's editor.
        /// エディタの再生状態と機体の物理定数を返す。このプロジェクトのエディタで
        /// 実行されたことが答えから分かる。
        /// </summary>
        [CliCommand("stampfly_probe",
            "Report the editor play state and the vehicle's physical constants",
            Tags = new[] { "editor" })]
        public static object Probe()
        {
            return new
            {
                isPlaying = EditorApplication.isPlaying,
                isCompiling = EditorApplication.isCompiling,
                massKilograms = VehicleBody.MassKilograms,
                inertiaTensor = VehicleBody.InertiaTensorUnityAxes.ToString("E4"),
                physicsSimulationMode = Physics.simulationMode.ToString(),
                frameCount = Time.frameCount,
            };
        }
    }
}
