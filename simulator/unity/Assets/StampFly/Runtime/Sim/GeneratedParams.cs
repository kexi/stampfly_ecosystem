/*
 * AUTO-GENERATED -- DO NOT EDIT.
 * Source: control/models/stampfly_physical.yaml
 * Regenerate: sf params generate
 *
 * 自動生成 -- 編集しないこと。
 * 元データ: control/models/stampfly_physical.yaml
 * 再生成: sf params generate
 */

using UnityEngine;

namespace StampFly.Sim
{
    /// <summary>
    /// The vehicle's physical parameters, generated from the repository's
    /// single source of truth. Every number here also reaches the SILS C++
    /// plant and the MuJoCo model from that same file, so the Unity
    /// simulator and the SILS integrate the same rigid body.
    ///
    /// 機体の物理パラメータ。リポジトリの基準ファイルから生成する。ここに在る
    /// 数値は同じファイルから SILS の C++ プラントと MuJoCo モデルにも渡るので、
    /// Unity 版と SILS は同じ剛体を積分する。
    ///
    /// The motor coefficients are absent on purpose: the firmware's C++ plant
    /// computes the rotor forces and this side only receives them.
    /// モータの係数は意図的に無い。ロータの力を計算するのはファームの C++ の
    /// プラントで、こちら側はそれを受け取るだけだからである。
    /// </summary>
    public static class GeneratedParams
    {
        /// <summary>Vehicle mass [kg]. / 機体の質量 [kg]。</summary>
        public const float MassKilograms = 0.037f;

        /// <summary>Roll inertia Ixx, about the body's forward axis [kg*m^2]. / ロール慣性 Ixx。機体の前方向のまわり [kg·m²]。</summary>
        public const float InertiaForwardAxis = 9.16e-6f;

        /// <summary>Pitch inertia Iyy, about the body's left axis [kg*m^2]. / ピッチ慣性 Iyy。機体の左右方向のまわり [kg·m²]。</summary>
        public const float InertiaLeftAxis = 1.33e-5f;

        /// <summary>Yaw inertia Izz, about the body's up axis [kg*m^2]. / ヨー慣性 Izz。機体の上方向のまわり [kg·m²]。</summary>
        public const float InertiaUpAxis = 2.04e-5f;

        /// <summary>
        /// The principal moments in Unity's axis order (x right, y up,
        /// z forward): Ixx to z, Iyy to x, Izz to y.
        /// Unity の軸順（x 右・y 上・z 前）に並べ替えた主慣性モーメント。
        /// Ixx を z へ、Iyy を x へ、Izz を y へ。
        /// </summary>
        public static Vector3 InertiaTensorUnityAxes =>
            new Vector3(InertiaLeftAxis, InertiaUpAxis, InertiaForwardAxis);

        /// <summary>
        /// A rotor's offset from the centre along one axis [m]. The four
        /// rotors sit at the four sign combinations of this offset.
        /// 中心からロータまでの 1 軸あたりの距離 [m]。4 つのロータは、この
        /// 距離の符号の 4 通りの組み合わせの位置に在る。
        /// </summary>
        public const float RotorOffsetMeters = 0.023f;

        /// <summary>How far above the centre a rotor sits [m]. / ロータが中心より上に在る高さ [m]。</summary>
        public const float RotorHeightMeters = 0.005f;

        /// <summary>The propeller's radius [m]; appearance only. / プロペラの半径 [m]。見た目にのみ使う。</summary>
        public const float PropellerRadiusMeters = 0.01499f;

        /// <summary>Gravity [m/s^2], the value every implementation shares. / 重力加速度 [m/s²]。全実装が揃って使う値。</summary>
        public const float GravityMetersPerSecondSquared = 9.81f;

        // The collision box's full extents [m], in Unity's axis order. The
        // source file stores HALF extents in FLU order, as MuJoCo's <geom
        // size> does; the doubling and the axis swap are already applied.
        // 衝突箱の全長 [m]。Unity の軸順。基準ファイルは MuJoCo の <geom size>
        // と同じく FLU 順の半長を持つ。2 倍と軸の入れ替えは適用済みである。
        public const float BoxSizeRight = 0.0816f;
        public const float BoxSizeUp = 0.0206f;
        public const float BoxSizeForward = 0.0816f;

        /// <summary>The collision box's full size in Unity's axis order. / Unity の軸順の衝突箱の全長。</summary>
        public static Vector3 BoxSizeUnityAxes =>
            new Vector3(BoxSizeRight, BoxSizeUp, BoxSizeForward);

        /// <summary>
        /// Half the box's thickness: the centre height of a body resting on
        /// the floor.
        /// 箱の厚みの半分。床に載った機体の中心の高さ。
        /// </summary>
        public const float RestingCentreHeightMeters = 0.5f * BoxSizeUp;
    }
}
