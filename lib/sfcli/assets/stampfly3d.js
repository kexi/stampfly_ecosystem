// ============================================================================
// stampfly3d.js — the StampFly 3D view, shared by every live browser page.
//
// Extracted verbatim (2026-09-19) from the inline module of
// lib/sfcli/assets/telemetry_web.html, which had itself PORTED the scene from
// simulator/sils/gui/static/app.js: same StampFly model (landing-page geometry
// + the real STL parts), same lighting, same OrbitControls with the per-OS
// trackpad zoom, same duty-driven prop spin. `sf telemetry --web` and
// `sf pilot ... --web` both mount it, so the aircraft looks and handles the
// same in both -- which is the point of extracting it rather than copying it.
//
// Why a module file and not a second copy: two copies drift. The first thing
// that would have drifted here is `quatFromNedEuler`, whose comment below
// records a 90-degree body-yaw bug that took a numeric comparison against the
// SILS GUI to find. A copy would have to be fixed twice.
//
// stampfly3d.js — ライブ表示の各ページが共有する StampFly の 3D ビュー。
//
// lib/sfcli/assets/telemetry_web.html のインラインモジュールから、そのまま
// 切り出したもの（2026-09-19）。元は simulator/sils/gui/static/app.js からの
// 移植である（同じ StampFly モデル・照明・OS 別ズーム付き OrbitControls・
// duty 駆動のプロペラ回転）。`sf telemetry --web` と `sf pilot ... --web` の
// 双方がこれを読み込むため、機体の見た目も操作も両者で一致する — 複製せず
// 切り出す理由がこれである。
//
// なぜ複製ではなくモジュールか: 複製は食い違っていく。ここで最初に食い違う
// のは `quatFromNedEuler` である。下のコメントが記録しているとおり、これには
// 機体ヨーの 90 度ずれという不具合があり、発見には SILS GUI との数値比較を
// 要した。複製すれば同じ修正を 2 か所に施すことになる。
// ============================================================================

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

// --- StampFly model (SILS GUI / landing page, verbatim) ----------------------
const PART_MAT = {
  frame:           new THREE.MeshStandardMaterial({ color: 0xccd2db, roughness: 0.5,  metalness: 0.1 }),
  pcb:             new THREE.MeshStandardMaterial({ color: 0x0e1014, roughness: 0.55, metalness: 0.2 }),
  m5stamps3:       new THREE.MeshStandardMaterial({ color: 0xff6a00, roughness: 0.4,  metalness: 0.1 }),
  battery:         new THREE.MeshStandardMaterial({ color: 0x2a1d14, roughness: 0.6,  metalness: 0.1 }),
  battery_adapter: new THREE.MeshStandardMaterial({ color: 0x33312d, roughness: 0.6,  metalness: 0.1 }),
  motor_fl:        new THREE.MeshStandardMaterial({ color: 0xb9c1cb, roughness: 0.35, metalness: 0.85 }),
  motor_fr:        new THREE.MeshStandardMaterial({ color: 0xb9c1cb, roughness: 0.35, metalness: 0.85 }),
  motor_rl:        new THREE.MeshStandardMaterial({ color: 0xb9c1cb, roughness: 0.35, metalness: 0.85 }),
  motor_rr:        new THREE.MeshStandardMaterial({ color: 0xb9c1cb, roughness: 0.35, metalness: 0.85 }),
};
const bladeMat = new THREE.MeshStandardMaterial({ color: 0xff2b3d, transparent: true, opacity: 0.62,
  roughness: 0.18, metalness: 0.0, emissive: 0x4a0008, emissiveIntensity: 0.35, side: THREE.DoubleSide });
const hubMat = new THREE.MeshStandardMaterial({ color: 0x8e0512, roughness: 0.3, metalness: 0.15 });

function makeBlade(L, Wm, thick, twistRoot, twistTip) {
  const s = new THREE.Shape();
  s.moveTo(0, 0.20 * Wm);
  s.bezierCurveTo(0.40 * L, 0.50 * Wm, 0.65 * L, 0.50 * Wm, 0.86 * L, 0.30 * Wm);
  s.quadraticCurveTo(L, 0.16 * Wm, L, 0.0);
  s.quadraticCurveTo(L, -0.12 * Wm, 0.86 * L, -0.20 * Wm);
  s.bezierCurveTo(0.65 * L, -0.58 * Wm, 0.40 * L, -0.58 * Wm, 0, -0.24 * Wm);
  s.closePath();
  const geo = new THREE.ExtrudeGeometry(s, { depth: thick, bevelEnabled: false });
  geo.translate(0, 0, -thick / 2);
  const p = geo.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
    const a = twistRoot + (twistTip - twistRoot) * Math.min(1, Math.max(0, x / L));
    const c = Math.cos(a), sn = Math.sin(a);
    p.setXYZ(i, x, y * c - z * sn, y * sn + z * c);
  }
  geo.rotateX(Math.PI / 2);
  geo.computeVertexNormals();
  return geo;
}
const PROP_RADIUS = 14.99;
const HUB_R = PROP_RADIUS * 0.22;
const bladeGeo = makeBlade(PROP_RADIUS - HUB_R * 0.6, (PROP_RADIUS - HUB_R * 0.6) / 2.7, 0.6, 0.38, 0.10);
const hubGeo = new THREE.CylinderGeometry(HUB_R, HUB_R * 0.9, 2.6, 24);
const domeGeo = new THREE.SphereGeometry(HUB_R * 0.85, 20, 12, 0, Math.PI * 2, 0, Math.PI / 2);

function makeProp() {
  const prop = new THREE.Group();
  prop.add(new THREE.Mesh(hubGeo, hubMat));
  const dome = new THREE.Mesh(domeGeo, hubMat); dome.position.y = 1.0; prop.add(dome);
  for (let i = 0; i < 3; i++) {
    const blade = new THREE.Mesh(bladeGeo, bladeMat);
    blade.rotation.y = i * (Math.PI * 2 / 3);
    blade.translateX(HUB_R * 0.6);
    prop.add(blade);
  }
  return prop;
}

// m1..m4 (packet) = M1 FR / M2 RR / M3 RL / M4 FL; CCW=+1 / CW=-1 (real turn direction).
// パケットの m1..m4 と実回転方向の対応（MJCF と同一）。
const PROP_HUBS = [
  { pos: [ 22.80, 7.81,  22.80], motor: "m4", dir: -1 },  // FL = M4  CW
  { pos: [-22.80, 7.81,  22.80], motor: "m1", dir: +1 },  // FR = M1  CCW
  { pos: [ 22.80, 7.81, -22.81], motor: "m3", dir: +1 },  // RL = M3  CCW
  { pos: [-22.80, 7.81, -22.81], motor: "m2", dir: -1 },  // RR = M2  CW
];
const BODY_PARTS = ["frame", "pcb", "m5stamps3", "battery", "battery_adapter",
                    "motor_fl", "motor_fr", "motor_rl", "motor_rr"];

// NED euler (FRD body) -> three.js quaternion for the FLU drone group inside the
// ENU worldGroup. q_nb from ZYX euler. The rotation R_nb (FRD->NED) is re-expressed
// in the display frames as R' = C · R_nb · D^T, where the WORLD basis changes by
// C: NED->ENU (x<->y swap, z negate) and the BODY basis changes by the DIFFERENT
// D: FRD->FLU = diag(1,-1,-1). Because C != D this is NOT a similarity, so the old
// "v'=C·v, w'=w" (which is C·R·C^T) was wrong: it left a constant 90° body-yaw
// offset that made ROLL and PITCH (the X and Y axes) appear SWAPPED. The closed
// form of the quaternion of C·R·D^T (verified numerically vs the SILS GUI's direct
// FLU-in-ENU quaternion) is below.
// NED オイラー角（FRD 機体）→ ENU worldGroup 内 FLU 機体の three.js クォータニオン。
// 回転 R_nb を表示系で R' = C·R_nb·D^T と表す。世界基底は C（NED→ENU, x↔y入替・z反転）、
// 機体基底は「別の」D（FRD→FLU = diag(1,-1,-1)）で変わる。C≠D ゆえ相似変換でなく、
// 旧 "v'=C·v"（= C·R·C^T）は誤り — 機体ヨーに一定 90° のずれが残り、ロールとピッチ
//（X 軸と Y 軸）が入れ替わって見えていた。C·R·D^T のクォータニオン閉形式が下記。
export function quatFromNedEuler(roll, pitch, yaw) {
  const cr = Math.cos(roll/2),  sr = Math.sin(roll/2);
  const cp = Math.cos(pitch/2), sp = Math.sin(pitch/2);
  const cy = Math.cos(yaw/2),   sy = Math.sin(yaw/2);
  const qw = cr*cp*cy + sr*sp*sy;
  const qx = sr*cp*cy - cr*sp*sy;
  const qy = cr*sp*cy + sr*cp*sy;
  const qz = cr*cp*sy - sr*sp*cy;
  const s = Math.SQRT1_2;   // 1/√2
  return [s*(qx+qy), s*(qx-qy), s*(qw-qz), s*(qw+qz)];   // (x,y,z,w) for three / three 用
}

// --- per-OS trackpad zoom (SILS GUI, verbatim) -------------------------------
const OS = (() => {
  const p = ((navigator.userAgentData && navigator.userAgentData.platform) ||
             navigator.platform || "").toLowerCase();
  if (p.includes("mac")) return "mac";
  if (p.includes("win")) return "win";
  if (p.includes("linux")) return "linux";
  return "other";
})();
const ZOOM_TUNE = {
  mac:   { scroll: 0.0020, pinch: 0.013 },
  win:   { scroll: 0.0016, pinch: 0.012 },
  linux: { scroll: 0.0018, pinch: 0.012 },
  other: { scroll: 0.0018, pinch: 0.012 },
};

/**
 * Mount the shared 3D view on a canvas and return a handle to drive it.
 *
 * The caller owns the data: it calls `update(pose)` from whatever stream it
 * has (a UDP packet for `sf telemetry`, a SILS Sample for `sf pilot`), which
 * is why this takes a pose in plain SI/NED fields rather than either page's
 * own wire format.
 *
 * 共有 3D ビューを canvas に載せ、操作用のハンドルを返す。
 *
 * データは呼び出し側が持つ。手元のストリーム（`sf telemetry` なら UDP パケット、
 * `sf pilot` なら SILS の Sample）から `update(pose)` を呼ぶ。どちらかのページ
 * 固有の形式ではなく SI・NED の素の項目を受け取るのは、そのためである。
 *
 * @param {Object} options
 * @param {HTMLCanvasElement} options.canvas  where the scene renders / 描画先
 * @param {HTMLElement} options.wrap          the sized box around it / 大きさを決める箱
 * @param {number} [options.modelScale]       aircraft display scale / 機体表示倍率
 * @param {number} [options.trailMax]         trail points kept / 保持する軌跡の点数
 * @returns {{update: Function, setScale: Function, setTrailVisible: Function,
 *            resize: Function, clearTrail: Function}}
 */
export function mountStampFly3D({ canvas, wrap, modelScale = 3.0, trailMax = 1500 }) {
  const props = [];
  const trailPts = [];
  let scale = modelScale;
  let trailVisible = true;

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 200);
  camera.position.set(0.6, 0.45, 0.6);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  controls.enableZoom = false;              // replaced by the per-OS handler below / 下の OS 別処理で置換
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  scene.add(new THREE.AmbientLight(0x8895b5, 0.9));
  const key = new THREE.DirectionalLight(0xffffff, 1.6); key.position.set(4, 8, 5); scene.add(key);
  const fill = new THREE.DirectionalLight(0xc9d4ff, 0.6); fill.position.set(-4, 3, -4); scene.add(fill);
  const cyan = new THREE.PointLight(0x22d3ee, 12, 14); cyan.position.set(-2, 1.5, 2); scene.add(cyan);
  const violet = new THREE.PointLight(0xa855f7, 12, 14); violet.position.set(2, 1, -2); scene.add(violet);

  const worldGroup = new THREE.Group(); worldGroup.rotation.x = -Math.PI / 2; scene.add(worldGroup);
  const grid = new THREE.GridHelper(8, 32, 0x2a3a55, 0x182336); grid.rotation.x = Math.PI / 2;
  worldGroup.add(grid);
  worldGroup.add(new THREE.AxesHelper(0.05));

  const drone = buildDrone();
  worldGroup.add(drone);
  const trailGeo = new THREE.BufferGeometry();
  const trailLine = new THREE.Line(trailGeo, new THREE.LineBasicMaterial({ color: 0x22d3ee }));
  worldGroup.add(trailLine);

  function buildDrone() {
    const g = new THREE.Group(); g.scale.setScalar(scale);
    const model = new THREE.Group();
    model.scale.setScalar(0.001);
    model.quaternion.set(0.5, 0.5, 0.5, 0.5);
    g.add(model);
    PROP_HUBS.forEach(h => {
      const prop = makeProp();
      prop.position.set(h.pos[0], h.pos[1] - 1.4, h.pos[2]);
      model.add(prop);
      props.push({ pivot: prop, dir: h.dir, motor: h.motor });
    });
    const loader = new STLLoader();
    BODY_PARTS.forEach(name => loader.load(`/mesh/${name}.stl`, (geo) => {
      geo.computeVertexNormals();
      model.add(new THREE.Mesh(geo, PART_MAT[name] || PART_MAT.frame));
    }, undefined, (err) => console.warn("[3D] STL load failed:", name, err)));
    return g;
  }

  function dolly(factor) {
    const dir = camera.position.clone().sub(controls.target);
    const dist = Math.max(0.15, Math.min(50, dir.length() * factor));
    camera.position.copy(controls.target).add(dir.setLength(dist));
  }
  const tune = ZOOM_TUNE[OS] || ZOOM_TUNE.other;
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    let px = e.deltaY;
    if (e.deltaMode === 1) px *= 16;
    else if (e.deltaMode === 2) px *= 100;
    const gain = e.ctrlKey ? tune.pinch : tune.scroll;
    let f = Math.exp(px * gain);
    f = Math.min(2.0, Math.max(0.5, f));
    dolly(f);
  }, { passive: false });
  let gscale = 1;
  canvas.addEventListener("gesturestart", (e) => { e.preventDefault(); gscale = e.scale; },
    { passive: false });
  canvas.addEventListener("gesturechange", (e) => {
    e.preventDefault();
    dolly(gscale / e.scale);
    gscale = e.scale;
  }, { passive: false });

  let lastW = 0, lastH = 0;
  function resize() {
    const w = wrap.clientWidth, h = wrap.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false); renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);

  // Latest pose, read by the animation loop. Kept as one reference so a burst
  // of updates between two frames costs nothing but the last assignment.
  // 最新の姿勢。描画ループが読む。参照 1 つに保つので、フレーム間に更新が
  // 集中しても最後の代入以外は費用ゼロである。
  let latest = null;
  let lastTrailPush = 0;

  function animate() {
    requestAnimationFrame(animate);
    const w = wrap.clientWidth, h = wrap.clientHeight;
    if (w && h && (w !== lastW || h !== lastH)) { lastW = w; lastH = h; resize(); }
    const p = latest;
    if (p) {
      // Live pose: ENU position (E,N,U)=(pos_e,pos_n,alt), attitude from euler.
      // ライブ姿勢: ENU 位置と オイラー角由来のクォータニオン。
      drone.position.set(p.pos_e, p.pos_n, p.alt);
      const q = quatFromNedEuler(p.roll, p.pitch, p.yaw);
      drone.quaternion.set(q[0], q[1], q[2], q[3]);
      // chase target (ENU -> three: (E,U,-N)) / チェイス注視点
      const tgt = new THREE.Vector3(p.pos_e, p.alt, -p.pos_n);
      controls.target.lerp(tgt, 0.12);
      const now = performance.now();
      if (now - lastTrailPush > 100) {          // ~10Hz trail / 約10Hz で軌跡を追加
        lastTrailPush = now;
        trailPts.push(p.pos_e, p.pos_n, p.alt);
        while (trailPts.length > trailMax * 3) trailPts.splice(0, 3);
        trailGeo.setAttribute("position",
          new THREE.BufferAttribute(new Float32Array(trailPts), 3));
        trailGeo.setDrawRange(0, trailVisible ? trailPts.length / 3 : 0);
      }
      // Prop spin: per-motor duty, real turn direction (same visual law as the
      // SILS GUI: idle creep + duty-proportional rate). A source without duty
      // (the SILS STATE line) still creeps, so the aircraft never looks dead.
      // プロペラ回転: モータ別 duty・実回転方向（SILS GUI と同じ視覚則）。duty を
      // 持たない入力源（SILS の STATE 行）でも最低速で回り、機体が止まって見えない。
      props.forEach(pr => {
        const duty = (p.motors && p.motors[pr.motor]) || 0;
        pr.pivot.rotation.y += pr.dir * (0.05 + duty * 1.6);
      });
    }
    controls.update();
    renderer.render(scene, camera);
  }

  resize();
  animate();

  return {
    /** Feed one pose: {pos_n, pos_e, alt [m], roll, pitch, yaw [rad], motors?}.
     *  姿勢を 1 つ渡す（位置 [m]・姿勢 [rad]・任意で duty）。 */
    update(pose) { latest = pose; },
    setScale(value) { scale = value; drone.scale.setScalar(scale); },
    setTrailVisible(visible) { trailVisible = visible; },
    clearTrail() { trailPts.length = 0; },
    resize,
  };
}
