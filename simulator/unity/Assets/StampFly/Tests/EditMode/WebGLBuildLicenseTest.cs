using System;
using System.IO;
using NUnit.Framework;
using StampFly.Editor.Builders;
using UnityEngine;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// A distribution preserves the original notice and rejects an absent or unwritable license.
    /// 配布物が元の表示を保持し、ライセンスの欠落や書込失敗を拒否することを保証する。
    /// </summary>
    public sealed class WebGLBuildLicenseTest
    {
        private string temporaryDirectory;

        /// <summary>Give every check its own output. / 各検査に専用の出力先を用意する。</summary>
        [SetUp]
        public void SetUp()
        {
            temporaryDirectory = Path.Combine(Path.GetTempPath(), "stampfly-license-" + Guid.NewGuid());
            Directory.CreateDirectory(temporaryDirectory);
        }

        /// <summary>Remove only this check's files. / この検査のファイルだけを消す。</summary>
        [TearDown]
        public void TearDown()
        {
            Directory.Delete(temporaryDirectory, true);
        }

        /// <summary>
        /// The actual repository notice survives byte for byte, including a rebuild.
        /// 実際のリポジトリの表示が、再ビルド時もバイト単位で保持される。
        /// </summary>
        [Test]
        public void DistributionContainsTheOriginalLicenseUnchanged()
        {
            string repositoryRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "../../.."));
            string output = Path.Combine(temporaryDirectory, "LICENSE.txt");
            File.WriteAllText(output, "stale notice");

            WebGLBuilder.CopyLicense(repositoryRoot, temporaryDirectory);

            Assert.That(File.ReadAllBytes(output),
                        Is.EqualTo(File.ReadAllBytes(Path.Combine(repositoryRoot, "LICENSE"))));
            Assert.That(File.ReadAllText(output), Does.Contain("Copyright (c) 2026 Kouhei Ito"));
        }

        /// <summary>A missing notice must fail. / 表示の欠落は失敗にする。</summary>
        [Test]
        public void MissingSourceCannotPassAsACopiedLicense()
        {
            Assert.Throws<FileNotFoundException>(() =>
                WebGLBuilder.CopyLicense(temporaryDirectory, temporaryDirectory));
        }

        /// <summary>An unwritable destination must fail. / 書き込めない出力先は失敗にする。</summary>
        [Test]
        public void CopyFailureCannotPassAsACopiedLicense()
        {
            File.WriteAllText(Path.Combine(temporaryDirectory, "LICENSE"), "notice");
            string blockedOutput = Path.Combine(temporaryDirectory, "blocked-output");
            File.WriteAllText(blockedOutput, "a file cannot hold LICENSE.txt");

            Assert.That(() => WebGLBuilder.CopyLicense(temporaryDirectory, blockedOutput),
                        Throws.InstanceOf<IOException>().Or.InstanceOf<UnauthorizedAccessException>());
        }
    }
}
