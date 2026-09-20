/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — building the scene).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using StampFly.Sim;
using StampFly.Ui;
using StampFly.Vehicle;
using StampFly.World;
using UnityEngine;
using UnityEngine.UIElements;

namespace StampFly.App
{
    /// <summary>
    /// Builds the whole simulator into the scene at run time: the world, the
    /// vehicle, the loop, the camera and the readout.
    ///
    /// シミュレータ一式を実行時に場面へ組み立てる。空間・機体・ループ・カメラ・
    /// 表示である。
    ///
    /// Building in code rather than in a scene asset keeps the wiring reviewable
    /// as text and keeps `Main.unity` down to one object holding this component,
    /// which is what makes a merge between three people working on the same
    /// project tractable.
    ///
    /// 場面の資産ではなくコードで組み立てるのは、つなぎ方をテキストとして見られる
    /// ようにするためと、`Main.unity` をこの部品を持つ物体 1 つに保つためである。
    /// 同じプロジェクトを 3 人で触るとき、統合を扱える大きさに留めるのがこれである。
    ///
    /// Unlike the stage 1 spike this is NOT a
    /// <c>RuntimeInitializeOnLoadMethod</c>: it is a component in one scene, so
    /// a Play Mode test that builds its own scene is not interrupted by it.
    ///
    /// 段階 1 の検証と違い、これは <c>RuntimeInitializeOnLoadMethod</c> では**ない**。
    /// 場面 1 つに置いた部品なので、自前の場面を作る Play Mode の試験に割り込まない。
    ///
    /// @design docs/plans/unity-simulator.md §5 段階 3
    /// </summary>
    public sealed class SimulatorBootstrap : MonoBehaviour
    {
        /// <summary>The world loaded when nothing says otherwise. / 指定が無いときに読む空間。</summary>
        public const string DefaultWorldName = "empty_room";

        [Tooltip("Which shipped world to load. 読み込む同梱の空間。")]
        public string worldName = DefaultWorldName;

        [Tooltip("The catalog the world is read from. 空間を読み出す一覧。")]
        public WorldCatalog worldCatalog;

        [Tooltip("Where the WebGL build serves the firmware module from. " +
                 "WebGL ビルドがファームのモジュールを配信する場所。")]
        public string firmwareUrl = Native.WebGlFirmware.DefaultModuleUrl;

        private SimLoop simLoop;

        /// <summary>The loop this built, for a test or the command surface. / 組み立てたループ。試験と操作の受け口のため。</summary>
        public SimLoop Loop => simLoop;

        private void Awake()
        {
            PhysicsStepSettings.Apply();

            WorldLoader world = BuildWorld();
            simLoop = BuildVehicle(world);
            BuildCamera(simLoop.transform);
            BuildHud(simLoop);
        }

        /// <summary>
        /// Load the world and build it into the scene. A world that will not
        /// load leaves an empty scene rather than a half-built one; the loader
        /// logs why.
        /// 空間を読み込んで場面に生成する。読めない空間は、中途半端な場面ではなく
        /// 空の場面を残す。理由は読み込み側が記録する。
        /// </summary>
        private WorldLoader BuildWorld()
        {
            var holder = new GameObject("World");
            holder.transform.SetParent(transform, false);

            var loader = holder.AddComponent<WorldLoader>();
            bool hasCatalog = worldCatalog != null;
            if (hasCatalog)
            {
                loader.Catalog = worldCatalog;
            }

            // `world.list` and `world.load` belong to the world's own
            // registration, which lives beside the loader. Guarded by the same
            // symbols as the relay, so a distributed build -- where
            // `StampFly.Remote` is absent entirely -- still compiles.
            // `world.list` と `world.load` は空間側の登録のもので、読み込み側の
            // 隣に置かれる。中継と同じ記号で囲んであり、`StampFly.Remote` が
            // 丸ごと無い配布用ビルドでも翻訳が通る。
#if UNITY_EDITOR || DEVELOPMENT_BUILD || STAMPFLY_REMOTE
            holder.AddComponent<Remote.WorldCommands>();
#endif

            loader.LoadByName(worldName);
            return loader;
        }

        /// <summary>
        /// Build the vehicle at the world's spawn: the rigid body with its
        /// physical properties, the appearance, the loop and its controls.
        /// 空間の出発点に機体を組み立てる。物理量を持つ剛体、見た目、ループ、その
        /// 操作である。
        /// </summary>
        private SimLoop BuildVehicle(WorldLoader world)
        {
            var vehicle = new GameObject("Vehicle");
            vehicle.transform.SetParent(transform, false);
            vehicle.transform.SetPositionAndRotation(
                SpawnPosition(world), world.HasWorld ? world.SpawnRotation : Quaternion.identity);

            var body = vehicle.AddComponent<Rigidbody>();
            var box = vehicle.AddComponent<BoxCollider>();
            VehicleBody.Apply(body, box);

            var loop = vehicle.AddComponent<SimLoop>();
            loop.firmwareUrl = firmwareUrl;
            vehicle.AddComponent<SimControls>();

            // The command surface exists only in a development build, so the
            // relay endpoint stays out of what is distributed (plan §4).
            // 命令の受け口は開発用ビルドにしか無い。中継の受け口を配布物から
            // 外しておくためである（計画 §4）。
#if UNITY_EDITOR || DEVELOPMENT_BUILD || STAMPFLY_REMOTE
            vehicle.AddComponent<SimRemoteCommands>();
#endif

            var appearance = vehicle.AddComponent<VehicleAppearance>();
            appearance.simLoop = loop;

            return loop;
        }

        /// <summary>
        /// Where the vehicle starts: the world's spawn, raised so the body rests
        /// on the floor rather than intersecting it. The world package hands
        /// Unity coordinates already, so nothing is converted here.
        /// 機体の出発点。空間の出発点を、機体が床にめり込まず載るように持ち上げた
        /// もの。空間の一式は Unity の座標をそのまま渡すので、ここでは変換しない。
        /// </summary>
        private static Vector3 SpawnPosition(WorldLoader world)
        {
            Vector3 place = world.HasWorld ? world.SpawnPosition : Vector3.zero;
            bool isBelowRest = place.y < VehicleBody.RestingCentreHeight;
            if (isBelowRest)
            {
                place.y = VehicleBody.RestingCentreHeight;
            }
            return place;
        }

        /// <summary>The chase camera, aimed at the vehicle. / 機体を追うカメラ。</summary>
        private void BuildCamera(Transform vehicle)
        {
            var holder = new GameObject("ChaseCamera");
            holder.transform.SetParent(transform, false);

            var camera = holder.AddComponent<Camera>();
            camera.clearFlags = CameraClearFlags.Skybox;
            camera.nearClipPlane = 0.02f;
            camera.farClipPlane = 60.0f;
            camera.fieldOfView = 55.0f;

            var follow = holder.AddComponent<FollowCamera>();
            follow.target = vehicle;
        }

        /// <summary>The on-screen readout. / 画面の表示。</summary>
        private void BuildHud(SimLoop loop)
        {
            var holder = new GameObject("Hud");
            holder.transform.SetParent(transform, false);

            var document = holder.AddComponent<UIDocument>();
            document.panelSettings = PanelSettingsFactory.Create();

            var hud = holder.AddComponent<SimHud>();
            hud.simLoop = loop;
        }
    }

    /// <summary>
    /// Makes the <see cref="PanelSettings"/> a UI Toolkit document needs. It is
    /// built in code rather than committed as an asset, so the readout needs no
    /// asset of its own and cannot go missing from a build.
    /// UI Toolkit の文書が要る <see cref="PanelSettings"/> を作る。資産として
    /// コミットせずコードで作るので、表示は自前の資産を持たず、ビルドから抜け落ちる
    /// こともない。
    /// </summary>
    public static class PanelSettingsFactory
    {
        /// <summary>The reference resolution the layout is scaled against. / 版面を合わせる基準の解像度。</summary>
        public static readonly Vector2Int ReferenceResolution = new Vector2Int(1600, 900);

        /// <summary>Builds settings that scale with the window. / 窓に合わせて拡大縮小する設定を作る。</summary>
        public static PanelSettings Create()
        {
            var settings = ScriptableObject.CreateInstance<PanelSettings>();
            settings.scaleMode = PanelScaleMode.ScaleWithScreenSize;
            settings.referenceResolution = ReferenceResolution;
            settings.match = 0.5f;
            settings.themeStyleSheet =
                Resources.Load<ThemeStyleSheet>("UnityThemes/UnityDefaultRuntimeTheme");
            return settings;
        }
    }
}
