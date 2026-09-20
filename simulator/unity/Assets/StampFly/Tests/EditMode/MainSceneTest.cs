/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — what the main scene holds).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using System.Collections.Generic;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using StampFly.App;
using StampFly.Remote;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// What <c>Main.unity</c> must hold for a served page to be reachable from
    /// a terminal: the bootstrap that builds the simulator, and a
    /// <see cref="RemoteBridge"/> on an object named <c>StampFlyBridge</c>.
    ///
    /// <c>Main.unity</c> が持たねばならないもの。配信されたページが端末から届く
    /// ようになるための、シミュレータを組み立てる部品と、<c>StampFlyBridge</c> と
    /// いう名前の物体に載った <see cref="RemoteBridge"/> である。
    ///
    /// The object's NAME is checked, not just the component, because that name
    /// is the address <c>RemoteBridge.jslib</c> hands to <c>SendMessage</c>.
    /// Renaming the object would leave the scene looking complete while every
    /// command quietly failed to arrive -- exactly the kind of break a test
    /// has to catch, since nothing in C# would stop compiling.
    ///
    /// 部品だけでなく物体の**名前**を確かめる。その名前が、
    /// <c>RemoteBridge.jslib</c> が <c>SendMessage</c> に渡す宛先だからである。
    /// 物体の名前を変えると、場面は揃って見えるのに、どの命令も静かに届かなくなる。
    /// C# の翻訳は何も止まらないので、まさに試験が捕まえるべき壊れ方である。
    ///
    /// @design docs/plans/unity-simulator.md §5 段階 3
    /// </summary>
    public sealed class MainSceneTest
    {
        private const string ScenePath = "Assets/StampFly/Scenes/Main.unity";

        /// <summary>The name the .jslib's SendMessage addresses. / .jslib の SendMessage の宛先の名前。</summary>
        private const string BridgeObjectName = "StampFlyBridge";

        private Scene opened;

        [SetUp]
        public void SetUp()
        {
            bool sceneIsMissing = AssetDatabase.LoadAssetAtPath<SceneAsset>(ScenePath) == null;
            if (sceneIsMissing)
            {
                Assert.Ignore(
                    $"{ScenePath} has not been built — run " +
                    "StampFly > Scenes > Rebuild Main Scene");
            }

            opened = EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Additive);
        }

        [TearDown]
        public void TearDown()
        {
            if (opened.IsValid())
            {
                EditorSceneManager.CloseScene(opened, true);
            }
        }

        [Test]
        public void TheSceneCarriesTheBridgeOnTheObjectTheJslibAddresses()
        {
            GameObject bridgeObject = FindRoot(BridgeObjectName);

            Assert.That(bridgeObject, Is.Not.Null,
                        $"no object named {BridgeObjectName}: the .jslib's " +
                        "SendMessage has nowhere to deliver a command");
            Assert.That(bridgeObject.GetComponent<RemoteBridge>(), Is.Not.Null,
                        $"{BridgeObjectName} carries no RemoteBridge");
        }

        [Test]
        public void TheSceneStillCarriesTheBootstrap()
        {
            bool hasBootstrap = false;
            foreach (GameObject root in opened.GetRootGameObjects())
            {
                hasBootstrap |= root.GetComponent<SimulatorBootstrap>() != null;
            }

            Assert.That(hasBootstrap, Is.True,
                        "the scene lost the object that builds the simulator");
        }

        /// <summary>
        /// Exactly one bridge. Two would mean two run ids and two registries,
        /// and the second would destroy itself at run time, which is a scene
        /// mistake worth catching here rather than in a browser.
        /// 橋はちょうど 1 つ。2 つあれば run_id も登録簿も 2 つになり、2 つ目は
        /// 実行時に自分を壊す。ブラウザではなくここで捕まえるべき場面の誤りである。
        /// </summary>
        [Test]
        public void TheSceneCarriesExactlyOneBridge()
        {
            var found = new List<RemoteBridge>();
            foreach (GameObject root in opened.GetRootGameObjects())
            {
                found.AddRange(root.GetComponentsInChildren<RemoteBridge>(true));
            }

            Assert.That(found.Count, Is.EqualTo(1));
        }

        /// <summary>The root object with this name, or null. / この名前の根の物体。無ければ null。</summary>
        private GameObject FindRoot(string name)
        {
            foreach (GameObject root in opened.GetRootGameObjects())
            {
                if (root.name == name)
                {
                    return root;
                }
            }

            return null;
        }
    }
}
