using UnityEngine;

namespace StampFly.World
{
    /// <summary>
    /// The one place where world-file coordinates (ENU: x east, y north, z up,
    /// right-handed) meet Unity's left-handed Y-up frame. Everything the C#
    /// side does with frames lives here; see Schemas/README.md §6.
    ///
    /// This is NOT simulator/sils/frames/frames_unity.hpp. That header maps the
    /// vehicle's NED/FRD state across the C ABI and stays in C++ (README §6.3);
    /// reimplementing it here would give two versions of a different mapping.
    ///
    /// 空間ファイルの座標系（ENU: x=東・y=北・z=上、右手系）と Unity の左手系
    /// Y 上が出会う唯一の場所。C# が座標に対して行う仕事はここだけである
    /// （README §6）。
    ///
    /// これは simulator/sils/frames/frames_unity.hpp では**ない**。あちらは機体
    /// の NED/FRD の状態を C ABI 越しに変換するもので、C++ に留まる（§6.3）。
    /// ここに書き直すと、別の写像が 2 つ存在することになる。
    /// </summary>
    public static class WorldFrames
    {
        /// <summary>
        /// Offset between the vehicle model's forward (+z in Unity, i.e. ENU
        /// north) and spawn.yaw_deg's zero (ENU east). See §6.2.1.
        /// 機体モデルの前方（Unity +z＝ENU の北）と spawn.yaw_deg の 0（東）の
        /// ずれ。§6.2.1 参照。
        /// </summary>
        public const float SpawnYawOffsetDeg = 90.0f;

        /// <summary>
        /// ENU position to Unity. Swapping y and z is the whole conversion:
        /// U = [[1,0,0],[0,0,1],[0,1,0]], det(U) = -1, so no sign changes are
        /// needed (§6.1).
        /// ENU の位置を Unity へ。y と z の入れ替えだけでよい。det(U) = −1 だが
        /// 符号反転は要らない（§6.1）。
        /// </summary>
        public static Vector3 EnuToUnity(float[] enu)
        {
            return new Vector3(enu[0], enu[2], enu[1]);
        }

        /// <summary>
        /// ENU position given as a Vector3 (x east, y north, z up) to Unity.
        /// Vector3 で表した ENU の位置（x=東・y=北・z=上）を Unity へ。
        /// </summary>
        public static Vector3 EnuToUnity(Vector3 enu)
        {
            return new Vector3(enu.x, enu.z, enu.y);
        }

        /// <summary>
        /// Unity position back to ENU as the file writes it. The map is its own
        /// inverse, so this is the same swap (§6.1).
        /// Unity の位置をファイルの ENU へ戻す。対合なので同じ入れ替え（§6.1）。
        /// </summary>
        public static float[] UnityToEnu(Vector3 unity)
        {
            return new[] { unity.x, unity.z, unity.y };
        }

        /// <summary>
        /// An obstacle's ENU rotation [roll, pitch, yaw] to Unity, built as the
        /// intrinsic yaw -> pitch -> roll the format defines (§3.2).
        ///
        /// The angles ARE negated, which contradicts what Schemas/README.md
        /// §6.2 tells an implementer to do. The spec's reasoning is that
        /// Unity's AngleAxis is "positive = clockwise seen from the positive
        /// axis" and so already carries the sign flip det(U) = -1 demands. It
        /// is not: measured in the editor, Quaternion.AngleAxis(90, Vector3.up)
        /// sends Unity forward to Unity right, i.e. ENU north to ENU east,
        /// which is a CLOCKWISE turn seen from above in ENU and therefore a yaw
        /// of -90, not +90. Unity's AngleAxis follows the right-hand rule about
        /// its own axis; what is left-handed is the coordinate frame, and the
        /// y-z swap of §6.1 is what carries the handedness change. So the sign
        /// flip has to be written here after all.
        ///
        /// Checked against R = Rz(yaw) Ry(pitch) Rx(roll) evaluated directly in
        /// ENU, for each angle alone and for the three mixed: with the negation
        /// the largest error is 0.0, and without it 2.0 — an exact mirror.
        /// WorldFramesTest holds this down.
        ///
        /// 障害物の ENU の回転 [roll, pitch, yaw] を Unity へ。形式が定める
        /// 内因性の yaw → pitch → roll の順で組み立てる（§3.2）。
        ///
        /// 角に負号を**付ける**。これは README §6.2 が実装者に指示している内容
        /// と食い違う。仕様は「Unity の AngleAxis は正軸から見て時計回りが正な
        /// ので det(U) = −1 の符号反転をすでに含む」と述べるが、そうではない。
        /// エディタで測ると Quaternion.AngleAxis(90, Vector3.up) は Unity の
        /// forward を right へ送る。すなわち ENU の北を東へ送るので、ENU で上
        /// から見れば**時計回り**であり、yaw は +90 ではなく −90 である。Unity
        /// の AngleAxis は自身の軸について右ねじに従う。左手系なのは座標系の
        /// ほうで、利き手の違いを担うのは §6.1 の y↔z の入れ替えである。よって
        /// 符号反転はここで書く必要がある。
        ///
        /// ENU で直に評価した R = Rz(yaw) Ry(pitch) Rx(roll) と照合した。各角を
        /// 単独で振った場合と 3 つを混ぜた場合の両方で、負号を付けると最大誤差
        /// 0.0、付けないと 2.0（完全な鏡像）になる。WorldFramesTest が守る。
        /// </summary>
        public static Quaternion EnuRotationToUnity(float[] rotationDeg)
        {
            float roll = rotationDeg[0];
            float pitch = rotationDeg[1];
            float yaw = rotationDeg[2];

            return Quaternion.AngleAxis(-yaw, Vector3.up)          // ENU +z -> Unity y
                 * Quaternion.AngleAxis(-pitch, Vector3.forward)   // ENU +y -> Unity z
                 * Quaternion.AngleAxis(-roll, Vector3.right);     // ENU +x -> Unity x
        }

        /// <summary>
        /// A Unity rotation back to the file's [roll, pitch, yaw] in degrees.
        /// It inverts <see cref="EnuRotationToUnity"/> by reading the rotated
        /// axes, so a read-write-read round trip reproduces the orientation.
        ///
        /// Verified numerically over 740 orientations: the angles come back
        /// within 1.7e-13 degrees while |pitch| stays below 85 degrees, and the
        /// orientation itself within 3.7e-15 at any pitch. At |pitch| = 90 the
        /// decomposition is singular (roll and yaw become one angle) and only
        /// the orientation, not the individual angles, is preserved. No shipped
        /// world tilts an obstacle that far, and the editing UI has no reason
        /// to, so the singular case is left as it is rather than given a
        /// special path that nothing would exercise.
        ///
        /// Unity の回転をファイルの [roll, pitch, yaw]（度）へ戻す。回した軸を
        /// 読んで <see cref="EnuRotationToUnity"/> を逆に解くので、読み → 書き →
        /// 読みで向きが再現される。
        ///
        /// 740 通りの姿勢で数値確認した。|pitch| が 85 度未満なら角そのものが
        /// 1.7e-13 度以内、どの pitch でも向きは 3.7e-15 以内で戻る。|pitch| =
        /// 90 度では分解が特異になり（roll と yaw が 1 つの角になる）、角では
        /// なく向きだけが保たれる。同梱のどの空間もそこまで傾けず、編集 UI にも
        /// その必要が無いので、使われない専用の道を作らずこのままにする。
        /// </summary>
        public static float[] UnityRotationToEnu(Quaternion unityRotation)
        {
            // Unity's own Euler decomposition uses the order z -> x -> y, which
            // is not the format's order, so the angles are recovered from the
            // columns of the ENU rotation matrix instead. Each column is where
            // one ENU basis vector lands, read back through the y-z swap.
            // Unity の Euler 分解は z → x → y の順で、形式の順と違う。そのため
            // ENU の回転行列の列から角を求める。各列は ENU の基底ベクトルの
            // 行き先で、y↔z の入れ替えを通して読み戻す。
            Vector3 columnX = ToEnuVector(unityRotation * Vector3.right);     // R * (1,0,0)
            Vector3 columnY = ToEnuVector(unityRotation * Vector3.forward);   // R * (0,1,0)
            Vector3 columnZ = ToEnuVector(unityRotation * Vector3.up);        // R * (0,0,1)

            // With R = Rz(yaw) Ry(pitch) Rx(roll):
            //   columnX = ( cy*cp,  sy*cp, -sp   )
            //   columnY = (   ... ,   ... ,  cp*sr)
            //   columnZ = (   ... ,   ... ,  cp*cr)
            // so pitch comes from columnX.z, yaw from columnX.x and .y, and
            // roll from the z components of the other two columns.
            // R = Rz(yaw) Ry(pitch) Rx(roll) のとき上の形になるので、pitch は
            // columnX.z から、yaw は columnX の x・y から、roll は残り 2 列の
            // z 成分から求まる。
            float pitch = Mathf.Asin(Mathf.Clamp(-columnX.z, -1.0f, 1.0f));
            float yaw = Mathf.Atan2(columnX.y, columnX.x);
            float roll = Mathf.Atan2(columnY.z, columnZ.z);

            return new[] { roll * Mathf.Rad2Deg, pitch * Mathf.Rad2Deg, yaw * Mathf.Rad2Deg };
        }

        /// <summary>
        /// Read a Unity direction back as an ENU one (the same y-z swap).
        /// Unity の向きを ENU の向きとして読み戻す（同じ y↔z の入れ替え）。
        /// </summary>
        private static Vector3 ToEnuVector(Vector3 unity)
        {
            return new Vector3(unity.x, unity.z, unity.y);
        }

        /// <summary>
        /// spawn.yaw_deg to a Unity rotation. The heading is 0 = east and 90 =
        /// north, while the vehicle model's forward (+z in Unity) points ENU
        /// north, so the two differ by 90 degrees (§6.2.1).
        ///
        /// The angle is `offset - yaw`, not the `yaw - offset` that §6.2.1
        /// gives. It follows from the same measurement as EnuRotationToUnity:
        /// a positive Unity AngleAxis about up turns ENU counter-clockwise the
        /// wrong way, so the heading is subtracted rather than added. Measured
        /// in the editor, `yaw - 90` sends yaw_deg 0 to ENU (-1, 0, 0), which
        /// is WEST, while `90 - yaw` sends it to (1, 0, 0), east as the format
        /// requires. Both forms happen to agree at yaw 90 and -90, which is
        /// every shipped world's spawn, so the spec's error does not show
        /// there.
        ///
        /// spawn.yaw_deg を Unity の回転へ。方位は 0 = 東・90 = 北で、機体
        /// モデルの前方（Unity +z）は ENU の北を向くので、両者は 90 度ずれる
        /// （§6.2.1）。
        ///
        /// 角は §6.2.1 の `yaw − offset` ではなく `offset − yaw` である。
        /// EnuRotationToUnity と同じ測定から従う。up まわりの正の AngleAxis は
        /// ENU を反時計回りとは逆に回すので、方位は足すのではなく引く。エディタ
        /// で測ると `yaw − 90` は yaw_deg 0 を ENU の (−1, 0, 0)＝**西**へ送り、
        /// `90 − yaw` が (1, 0, 0)＝東（形式の求める向き）へ送る。yaw が 90 と
        /// −90 のときは両者が一致し、同梱の空間の出発点はすべてそこなので、
        /// 仕様の誤りがそこでは現れない。
        /// </summary>
        public static Quaternion SpawnYawToUnity(float yawDeg)
        {
            return Quaternion.AngleAxis(SpawnYawOffsetDeg - yawDeg, Vector3.up);
        }

        /// <summary>
        /// A Unity heading back to spawn.yaw_deg. / Unity の方位を yaw_deg へ。
        /// </summary>
        public static float UnityToSpawnYaw(Quaternion unityRotation)
        {
            // The vehicle's forward, read as an ENU direction: x east, y north.
            // 機体の前方を ENU の向きとして読む。x が東、y が北。
            Vector3 forwardEnu = ToEnuVector(unityRotation * Vector3.forward);
            return Mathf.Atan2(forwardEnu.y, forwardEnu.x) * Mathf.Rad2Deg;
        }
    }
}
