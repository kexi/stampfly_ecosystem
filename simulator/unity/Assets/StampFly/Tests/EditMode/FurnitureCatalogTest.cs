// Copyright (c) 2026 Kei Nakayama (kexi). MIT License.
using System;
using NUnit.Framework;
using StampFly.World;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// Defaults remain independent and compatible with format v1. / 既定値の独立性と v1 との互換を保証する。
    /// @design docs/plans/unity-simulator.md §4 [OK]
    /// </summary>
    public sealed class FurnitureCatalogTest
    {
        [TestCase("table")]
        [TestCase("chair")]
        [TestCase("sofa")]
        [TestCase("shelf")]
        [TestCase("bed")]
        public void DefaultsAreKnownIndependentBottomCentredFurniture(string type)
        {
            WorldObstacle first = FurnitureCatalog.CreateDefault(type, "first");
            WorldObstacle second = FurnitureCatalog.CreateDefault(type, "second");
            Assert.That(WorldObstacleTypes.IsKnown(type), Is.True);
            Assert.That(FurnitureCatalog.IsFurniture(type), Is.True);
            Assert.That(first.position, Is.EqualTo(new float[3]));
            Assert.That(first.rotation_deg, Is.EqualTo(new float[3]));
            Assert.That(first.size, Has.All.GreaterThan(0));
            Assert.That(first.color, Does.Match("^#[0-9a-f]{6}$"));
            first.size[0] = -1;
            Assert.That(second.size[0], Is.GreaterThan(0));
            Assert.That(first.size, Is.Not.SameAs(second.size));
        }

        [Test]
        public void UnknownKindCannotSilentlyBecomeFurniture()
        {
            Assert.That(FurnitureCatalog.IsFurniture("box"), Is.False);
            Assert.Throws<ArgumentException>(() => FurnitureCatalog.CreateDefault("unknown", "invalid"));
        }
    }
}
