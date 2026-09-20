using NUnit.Framework;
using StampFly.World;
using UnityEditor;

namespace StampFly.Tests.EditMode
{
    /// <summary>
    /// What the catalog guarantees: the build carries every shipped world, and
    /// each entry's JSON is the file of the same name. The catalog is generated
    /// by an editor menu, so a world added without rerunning it would silently
    /// be missing from a WebGL build; this test catches that at desk time.
    /// 一覧が保証すること。ビルドが同梱の空間を全て持ち、各項目の JSON が同じ
    /// 名前のファイルであること。一覧はエディタのメニューが作るので、作り直さ
    /// ずに空間を足すと WebGL のビルドから黙って抜け落ちる。この試験が手元で
    /// それを捕まえる。
    /// </summary>
    public sealed class WorldCatalogTest
    {
        private const string CatalogPath = "Assets/StampFly/Worlds/WorldCatalog.asset";

        [Test]
        public void CatalogHoldsEveryShippedWorld()
        {
            WorldCatalog catalog = AssetDatabase.LoadAssetAtPath<WorldCatalog>(CatalogPath);
            Assert.That(catalog, Is.Not.Null,
                        $"'{CatalogPath}' is missing; run StampFly > World > Rebuild Catalog");

            Assert.That(catalog.Names(), Is.EquivalentTo(ShippedWorlds.Names),
                        "the catalog is out of date; run StampFly > World > Rebuild Catalog");
        }

        [Test]
        [TestCaseSource(typeof(ShippedWorlds), nameof(ShippedWorlds.AllNames))]
        public void CatalogEntryLoadsAsThatWorld(string worldName)
        {
            WorldCatalog catalog = AssetDatabase.LoadAssetAtPath<WorldCatalog>(CatalogPath);
            Assert.That(catalog, Is.Not.Null);

            string json = catalog.Json(worldName);
            Assert.That(json, Is.Not.Null, $"the catalog holds no JSON for '{worldName}'");

            WorldReadResult result = WorldFileReader.Read(json);

            Assert.That(result.Ok, Is.True, result.Reason);
            Assert.That(result.World.name, Is.EqualTo(worldName),
                        "a catalog entry's name must match the world it carries");
        }

        [Test]
        public void AnUnknownNameReturnsNothingRatherThanThrowing()
        {
            WorldCatalog catalog = AssetDatabase.LoadAssetAtPath<WorldCatalog>(CatalogPath);

            Assert.That(catalog.Json("no_such_world"), Is.Null);
        }
    }
}
