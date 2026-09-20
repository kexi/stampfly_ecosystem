/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — writing the vehicle's meshes).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.IO;
using StampFly.Vehicle;
using UnityEditor;
using UnityEngine;

namespace StampFly.Editor.MeshTools
{
    /// <summary>
    /// Writes one Unity mesh asset per part of the vehicle, converted from the
    /// STL files the other simulators draw from.
    ///
    /// 他のシミュレータが描くのに使う STL のファイルから変換して、機体の部品 1 つ
    /// につき Unity のメッシュ資産を 1 つ書く。
    ///
    /// ## Why assets and not a runtime import / 実行時に読まず資産にする理由
    ///
    /// The STL files stay where they are, in `simulator/shared/assets/`, and are
    /// never copied into `Assets/`: one copy of a part is the whole point of
    /// that shared folder. The meshes Unity draws are written here instead,
    /// under `Resources`, for the same reason
    /// <see cref="StampFly.Core.ShadedMaterials"/> gives for the materials — a
    /// WebGL player carries only what an asset in the build refers to, and
    /// nothing in a browser can read a file out of the repository.
    ///
    /// STL のファイルは `simulator/shared/assets/` に在るままにし、`Assets/` へ
    /// 複製しない。部品の実体を 1 つにすることが、その共有フォルダの趣旨である。
    /// 代わりに Unity が描くメッシュをここ、`Resources` の下に書く。理由は
    /// <see cref="StampFly.Core.ShadedMaterials"/> がマテリアルについて述べるのと
    /// 同じで、WebGL のプレイヤーが持つのはビルドの中の資産が参照するものだけで
    /// あり、ブラウザの中ではリポジトリのファイルは読めないからである。
    ///
    /// ## Determinism / 何度実行しても同じになること
    ///
    /// Running this twice on an unchanged STL must leave the repository
    /// unchanged. The mesh's contents follow from the file alone, and each asset
    /// is reused rather than recreated, so its guid — and any reference to it —
    /// survives a rebuild.
    ///
    /// 変わっていない STL に対して 2 度実行しても、リポジトリは変わらないままで
    /// なければならない。メッシュの中身はファイルだけから決まり、資産は作り直さず
    /// 使い回すので、guid と、それへの参照は作り直しを越えて残る。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（機体の見た目）
    /// </summary>
    public static class VehicleMeshBuilder
    {
        /// <summary>
        /// Where the STL files live, relative to the repository root. The
        /// converter reads them and never writes there.
        /// STL のファイルの置き場。リポジトリの根からの相対。変換ツールはここを
        /// 読むだけで、書くことはない。
        /// </summary>
        public const string StlFolderFromRepositoryRoot =
            "simulator/shared/assets/meshes/parts";

        /// <summary>Where the mesh assets are written. / メッシュ資産の書き出し先。</summary>
        public const string MeshFolder =
            "Assets/StampFly/Runtime/Resources/" + VehicleParts.MeshResourceFolder;

        private const string MenuPath = "StampFly/Meshes/Rebuild Vehicle Meshes";

        /// <summary>
        /// How far up the Unity project sits from the repository root. The
        /// editor's working directory is the project folder
        /// (`simulator/unity`), so the shared assets are two levels above it.
        /// Unity の企画がリポジトリの根からどれだけ下に在るか。エディタの作業
        /// ディレクトリは企画のフォルダ（`simulator/unity`）なので、共有の資産は
        /// その 2 つ上に在る。
        /// </summary>
        private const string RepositoryRootFromProject = "../..";

        /// <summary>
        /// Convert every part and write its mesh, then save. Returns false and
        /// says which file was missing when one of the thirteen is not there.
        /// 部品を全て変換してメッシュを書き、保存する。13 のうち 1 つでも無ければ
        /// false を返し、どのファイルが無かったかを述べる。
        /// </summary>
        public static bool Rebuild()
        {
            string stlFolder = Path.GetFullPath(
                Path.Combine(RepositoryRootFromProject, StlFolderFromRepositoryRoot));

            bool folderIsMissing = !Directory.Exists(stlFolder);
            if (folderIsMissing)
            {
                Debug.LogError(
                    $"[VehicleMeshBuilder] no STL folder at {stlFolder}. The editor must be " +
                    "started with the Unity project (simulator/unity) as its working directory.");
                return false;
            }

            EnsureFolders();
            bool allWritten = WriteEveryPart(stlFolder);

            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            return allWritten;
        }

        /// <summary>
        /// Write each part named in <see cref="VehicleParts"/>. One missing file
        /// does not stop the rest: a half-converted vehicle with a clear error
        /// per part says more than a single failure at the first gap.
        /// <see cref="VehicleParts"/> が挙げる部品をそれぞれ書く。1 つ欠けても残り
        /// は止めない。部品ごとの明確な誤りとともに半分変換された機体のほうが、
        /// 最初の欠けで一度きり失敗するより多くを語る。
        /// </summary>
        private static bool WriteEveryPart(string stlFolder)
        {
            bool allWritten = true;

            foreach (VehicleParts.Part part in VehicleParts.All())
            {
                string stlPath = Path.Combine(stlFolder, $"{part.Name}.stl");
                bool fileIsMissing = !File.Exists(stlPath);
                if (fileIsMissing)
                {
                    Debug.LogError($"[VehicleMeshBuilder] no STL at {stlPath}");
                    allWritten = false;
                    continue;
                }

                WritePart(part.Name, stlPath);
            }

            return allWritten;
        }

        /// <summary>
        /// One part: read, convert, and store into the asset that is already
        /// there — or into a new one the first time round.
        /// 部品 1 つ。読んで変換し、既に在る資産へ書き入れる。初回だけは新しく作る。
        /// </summary>
        private static void WritePart(string partName, string stlPath)
        {
            float[] stlVertices = BinaryStlReader.ReadVertices(stlPath);
            StlToUnityGeometry.Geometry geometry =
                StlToUnityGeometry.FromStlVertices(stlVertices);

            string assetPath = $"{MeshFolder}/{partName}.asset";
            Mesh mesh = LoadOrCreate(assetPath, partName);
            Fill(mesh, geometry);

            EditorUtility.SetDirty(mesh);
        }

        /// <summary>
        /// Put the geometry on the mesh. The normals come with it rather than
        /// from <c>RecalculateNormals</c>: <see cref="SmoothNormals"/> says why,
        /// and computing them in this repository's own code is also what makes
        /// the asset identical on every run.
        /// メッシュに形を置く。法線は <c>RecalculateNormals</c> ではなく形と一緒に
        /// 来る。理由は <see cref="SmoothNormals"/> が述べる。このリポジトリ自身の
        /// コードで計算することは、どの実行でも資産が同一になることでもある。
        /// </summary>
        private static void Fill(Mesh mesh, StlToUnityGeometry.Geometry geometry)
        {
            mesh.Clear();
            mesh.indexFormat = IndexFormatFor(geometry.Vertices.Length);
            mesh.vertices = geometry.Vertices;
            mesh.normals = geometry.Normals;
            mesh.triangles = geometry.Triangles;
            mesh.RecalculateBounds();
            mesh.UploadMeshData(false);
        }

        /// <summary>
        /// The widest vertex count a 16-bit index reaches. Every part here is
        /// well inside it, and a narrower index is a smaller asset in the build.
        /// 16 ビットの索引が届く頂点数の上限。ここの部品はどれも十分その内に収まり、
        /// 索引が狭いほどビルドの中の資産は小さい。
        /// </summary>
        private const int SixteenBitIndexLimit = 65535;

        /// <summary>The index width the mesh needs. / メッシュに要る索引の幅。</summary>
        private static UnityEngine.Rendering.IndexFormat IndexFormatFor(int vertexCount)
        {
            bool needsWide = vertexCount > SixteenBitIndexLimit;
            return needsWide
                ? UnityEngine.Rendering.IndexFormat.UInt32
                : UnityEngine.Rendering.IndexFormat.UInt16;
        }

        /// <summary>
        /// The mesh at that path, created there when it is not there yet.
        /// Reusing the existing asset keeps its guid, so a scene or prefab
        /// referring to it does not lose the reference on a rebuild — the same
        /// reason <see cref="MaterialTools.MaterialTemplateBuilder"/> gives.
        /// その場所のメッシュ。まだ無ければそこに作る。在る資産を使い回すと guid が
        /// 変わらないので、それを参照する場面やプレハブが作り直しで参照を失わない。
        /// <see cref="MaterialTools.MaterialTemplateBuilder"/> が述べるのと同じ理由。
        /// </summary>
        private static Mesh LoadOrCreate(string assetPath, string partName)
        {
            Mesh existing = AssetDatabase.LoadAssetAtPath<Mesh>(assetPath);
            bool isThere = existing != null;
            if (isThere)
            {
                return existing;
            }

            var created = new Mesh { name = partName };
            AssetDatabase.CreateAsset(created, assetPath);
            return created;
        }

        /// <summary>
        /// Create every folder on the way to the mesh folder. Unity's
        /// CreateFolder makes one level at a time.
        /// メッシュの置き場までの各段のフォルダを作る。Unity の CreateFolder は
        /// 一段ずつしか作らない。
        /// </summary>
        private static void EnsureFolders()
        {
            string[] parts = MeshFolder.Split('/');
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

        /// <summary>
        /// The entry point `unity run . -- -executeMethod ...` calls. It must
        /// leave a non-zero exit code behind when a part is missing, which
        /// <c>EditorApplication.Exit</c> is the only way to do from a batch run.
        /// `unity run . -- -executeMethod ...` が呼ぶ入口。部品が欠けたときは 0 で
        /// ない終了コードを残さねばならず、まとめ実行からそれを行う手段は
        /// <c>EditorApplication.Exit</c> だけである。
        /// </summary>
        public static void RebuildFromCommandLine()
        {
            bool ok = Rebuild();
            Debug.Log($"[VehicleMeshBuilder] wrote {VehicleParts.All().Length} meshes to {MeshFolder}");
            EditorApplication.Exit(ok ? 0 : 1);
        }

        [MenuItem(MenuPath)]
        private static void RebuildFromMenu()
        {
            bool ok = Rebuild();
            if (ok)
            {
                Debug.Log(
                    $"[VehicleMeshBuilder] wrote {VehicleParts.All().Length} meshes to {MeshFolder}");
            }
        }
    }
}
