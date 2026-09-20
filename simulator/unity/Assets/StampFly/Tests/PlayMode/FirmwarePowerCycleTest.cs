/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — power-cycle checks).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 */

using NUnit.Framework;
using StampFly.Native;

namespace StampFly.Tests.PlayMode
{
    /// <summary>
    /// Guarantees that a second power-on works, which is what a second play
    /// session in the editor amounts to.
    ///
    /// 2 回目の電源投入が通ることを保証する。エディタでの 2 回目の再生が行うのは
    /// これである。
    ///
    /// The editor never unloads a native library it has loaded, so a second play
    /// session would otherwise meet a firmware that is still booted and get
    /// <see cref="SfuAbi.ErrorAlreadyBooted"/> — one module is one power-on, by
    /// design. <see cref="EditorFirmware"/> gets around it by copying the dylib
    /// to a unique name and opening that with <c>dlopen</c>, which this test is
    /// here to hold in place: running it twice in one editor process is the same
    /// thing a second play session does.
    ///
    /// エディタは読み込んだネイティブライブラリを解放しないので、そうでなければ
    /// 2 回目の再生は起動済みのままのファームに出会い、
    /// <see cref="SfuAbi.ErrorAlreadyBooted"/> を受け取る ― 設計どおり、1 モジュール
    /// ＝1 回の電源投入だからである。<see cref="EditorFirmware"/> は dylib を一意な
    /// 名前へ複写して <c>dlopen</c> で開くことでこれを避けており、この試験はそれを
    /// 保つために在る。1 つのエディタのプロセスで 2 回走らせることが、2 回目の
    /// 再生と同じことだからである。
    ///
    /// @design docs/plans/unity-simulator.md §4 設計上の決定（再初期化）
    /// </summary>
    public sealed class FirmwarePowerCycleTest
    {
        [SetUp]
        public void SetUp()
        {
            bool cannotFly = !FirmwareFlightScene.DylibExists;
            if (cannotFly)
            {
                Assert.Ignore(
                    "the firmware dylib is not built — run " +
                    "`nix develop -c just unity-native-build` first");
            }
        }

        /// <summary>
        /// Two firmwares in a row both boot and both tick. The second is what a
        /// second play session gets.
        /// ファームを続けて 2 つ作り、どちらも起動して刻める。2 つ目が、2 回目の
        /// 再生が得るものである。
        /// </summary>
        [Test]
        public void ASecondPowerOnBootsFromScratch()
        {
            for (int powerOn = 1; powerOn <= 2; powerOn++)
            {
                var scene = new FirmwareFlightScene();
                try
                {
                    Assert.That(scene.Build(), Is.True,
                                $"power-on {powerOn} failed: {scene.Firmware?.LastError}");
                    Assert.That(scene.RunOneTick(), Is.EqualTo(SfuAbi.Ok),
                                $"power-on {powerOn} could not step");

                    // A fresh firmware starts its clock at one tick, not where
                    // the previous one left off.
                    // 新しいファームは、前のファームが止めた所ではなく、1 刻みから
                    // 時計を始める。
                    Assert.That(scene.LastResult.NowMicroseconds,
                                Is.EqualTo((long)Sim.SimClock.TickMicroseconds),
                                $"power-on {powerOn} did not start from a fresh clock");
                }
                finally
                {
                    scene.Dispose();
                }
            }
        }

        /// <summary>
        /// Each power-on opens its own copy of the dylib, which is the mechanism
        /// the check above relies on. Two copies with the same name would mean
        /// the editor handed back the image it already had.
        /// 電源投入のたびに dylib の自分だけの複写を開く。上の検査が頼るのはこの
        /// 仕組みである。同じ名前の複写が 2 つ在れば、エディタが既に持っていた像を
        /// 返したということになる。
        /// </summary>
        [Test]
        public void EachPowerOnOpensItsOwnCopy()
        {
            var first = new EditorFirmware();
            var second = new EditorFirmware();
            try
            {
                first.BeginLoad();
                second.BeginLoad();

                Assert.That(first.Status, Is.EqualTo(FirmwareStatus.Loaded),
                            first.LastError);
                Assert.That(second.Status, Is.EqualTo(FirmwareStatus.Loaded),
                            second.LastError);
                Assert.That(second.OpenedPath, Is.Not.EqualTo(first.OpenedPath));
            }
            finally
            {
                first.Dispose();
                second.Dispose();
            }
        }

        /// <summary>
        /// A firmware refuses to boot twice, as the ABI says it must. A host
        /// that wants another power-on builds another instance.
        /// ファームは ABI の定めどおり、2 回目の起動を拒む。もう 1 回電源を入れたい
        /// ホストは、別の実体を作る。
        /// </summary>
        [Test]
        public void BootingTheSameFirmwareTwiceIsRefused()
        {
            var firmware = new EditorFirmware();
            try
            {
                firmware.BeginLoad();
                Assert.That(firmware.Status, Is.EqualTo(FirmwareStatus.Loaded),
                            firmware.LastError);

                var config = new SfuConfig
                {
                    BatteryModel = 1,
                    BootCalibration = 1,
                    HostOwnsBody = 1,
                    StartHeightMeters = Sim.VehicleBody.RestingCentreHeight,
                };

                Assert.That(firmware.Boot(config), Is.EqualTo(SfuAbi.Ok));
                Assert.That(firmware.Boot(config), Is.Not.EqualTo(SfuAbi.Ok));
            }
            finally
            {
                firmware.Dispose();
            }
        }

        /// <summary>
        /// The dylib reports the struct sizes this build declares, which is the
        /// check the loop runs before its first call.
        /// dylib が、このビルドの宣言と同じ構造体の大きさを報告する。ループが最初の
        /// 呼び出しの前に走らせる検査である。
        /// </summary>
        [Test]
        public void TheDylibAgreesOnEveryStructSize()
        {
            var scene = new FirmwareFlightScene();
            try
            {
                // Build boots, and boot refuses on a size mismatch, so a
                // successful build IS the agreement.
                // Build は起動を行い、起動は大きさの食い違いで拒む。よって Build が
                // 成功したこと自体が一致の証である。
                Assert.That(scene.Build(), Is.True, scene.Firmware?.LastError);
            }
            finally
            {
                scene.Dispose();
            }
        }
    }
}
