import * as THREE from "./vendor/three.module.min.js";

const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------- DOM sprite layer (always runs, even without WebGL) ----------
(function spawnSprites() {
  const layer = document.getElementById("sprite-layer");
  if (!layer || REDUCED) return;
  const N = 12;
  for (let i = 0; i < N; i++) {
    const img = document.createElement("img");
    img.src = "/static/assets/grape.svg";
    img.className = "grape-sprite";
    const size = 26 + Math.random() * 58;
    img.style.width = `${size}px`;
    img.style.height = `${size}px`;
    img.style.left = `${Math.random() * 94}%`;
    img.style.top = `${Math.random() * 88}%`;
    img.style.setProperty("--sp-dx", `${(Math.random() - 0.5) * 160}px`);
    img.style.setProperty("--sp-dy", `${-40 - Math.random() * 140}px`);
    img.style.setProperty("--sp-rot", `${(Math.random() - 0.5) * 50}deg`);
    img.style.setProperty("--sp-dur", `${9 + Math.random() * 14}s`);
    img.style.setProperty("--sp-delay", `${-Math.random() * 14}s`);
    img.style.setProperty("--sp-opacity", `${0.18 + Math.random() * 0.35}`);
    layer.appendChild(img);
  }
})();

// ---------- section reveal ----------
(function watchSections() {
  const io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) e.target.classList.toggle("in-view", e.isIntersecting);
    },
    { threshold: 0.3 }
  );
  document.querySelectorAll(".land-section").forEach((s) => io.observe(s));
})();

// ---------- three.js scene ----------
const canvas = document.getElementById("grape-canvas");
let renderer = null;
try {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
} catch {
  canvas.style.display = "none";
}

if (renderer) {
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();

  const camera = new THREE.PerspectiveCamera(
    40,
    window.innerWidth / window.innerHeight,
    0.1,
    100
  );
  camera.position.set(0, 0, 8.5);

  // lights
  scene.add(new THREE.HemisphereLight(0xffffff, 0x7cb342, 1.15));
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  key.position.set(4, 6, 6);
  scene.add(key);
  const rim = new THREE.PointLight(0xc9e88f, 18, 30);
  rim.position.set(-5, 3, -4);
  scene.add(rim);

  // procedural environment for juicy speculars (core three only)
  {
    const env = new THREE.Scene();
    const geo = new THREE.SphereGeometry(10, 16, 16);
    const mat = new THREE.MeshBasicMaterial({ side: THREE.BackSide });
    mat.color.set(0xeef6dd);
    env.add(new THREE.Mesh(geo, mat));
    const glow = (color, x, y, z, s) => {
      const m = new THREE.Mesh(
        new THREE.PlaneGeometry(s, s),
        new THREE.MeshBasicMaterial({ color })
      );
      m.position.set(x, y, z);
      m.lookAt(0, 0, 0);
      env.add(m);
    };
    glow(0xffffff, 0, 9, 0, 12);      // overhead softbox
    glow(0xd9f0ae, -9, 2, 4, 8);      // green bounce
    glow(0xffffff, 9, -1, -5, 8);     // cool fill
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(env, 0.04).texture;
    pmrem.dispose();
  }

  // ---------- hero grape bunch ----------
  const bunch = new THREE.Group();

  const berryGeo = new THREE.SphereGeometry(1, 32, 24);
  const berryMats = [0x9ccf63, 0x86bc48, 0x6fae3a].map(
    (c) =>
      new THREE.MeshPhysicalMaterial({
        color: c,
        roughness: 0.14,
        clearcoat: 1,
        clearcoatRoughness: 0.12,
        transmission: 0.35,
        thickness: 1.2,
        ior: 1.35,
      })
  );

  const RING_COUNTS = [9, 9, 8, 8, 7, 6, 5, 4, 3, 2, 1];
  const rng = (a, b) => a + Math.random() * (b - a);
  const berries = [];

  RING_COUNTS.forEach((n, r) => {
    const t = r / (RING_COUNTS.length - 1);
    const ringRad = 1.12 * (1 - t * 0.85);
    const yy = 2.6 - r * 0.55;
    for (let j = 0; j < n; j++) {
      const a = (j / n) * Math.PI * 2 + r * 0.73;
      const berry = new THREE.Mesh(
        berryGeo,
        berryMats[Math.floor(Math.random() * berryMats.length)]
      );
      const s = rng(0.4, 0.52);
      berry.scale.setScalar(s);
      berry.position.set(
        Math.cos(a) * ringRad + rng(-0.09, 0.09),
        yy + rng(-0.07, 0.07),
        Math.sin(a) * ringRad + rng(-0.09, 0.09)
      );
      bunch.add(berry);
      berries.push(berry);
    }
  });
  // crown berries so the top isn't hollow
  for (let j = 0; j < 4; j++) {
    const berry = new THREE.Mesh(berryGeo, berryMats[1]);
    berry.scale.setScalar(rng(0.4, 0.5));
    const a = (j / 4) * Math.PI * 2;
    berry.position.set(Math.cos(a) * 0.4, 3.0, Math.sin(a) * 0.4);
    bunch.add(berry);
  }

  // stem — curved tube
  {
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(0, 2.6, 0),
      new THREE.Vector3(0.12, 3.4, 0.06),
      new THREE.Vector3(0.5, 3.9, 0.1),
      new THREE.Vector3(0.85, 4.15, 0.0),
    ]);
    const stem = new THREE.Mesh(
      new THREE.TubeGeometry(curve, 20, 0.09, 8),
      new THREE.MeshStandardMaterial({ color: 0x6d4c2f, roughness: 0.8 })
    );
    bunch.add(stem);
  }

  // leaf — flat shape at stem tip
  {
    const s = new THREE.Shape();
    s.moveTo(0, 0);
    s.bezierCurveTo(0.5, 0.45, 1.4, 0.5, 1.9, 0.05);
    s.bezierCurveTo(1.4, -0.5, 0.5, -0.42, 0, 0);
    const leaf = new THREE.Mesh(
      new THREE.ShapeGeometry(s, 16),
      new THREE.MeshStandardMaterial({
        color: 0x4f8a28,
        roughness: 0.5,
        side: THREE.DoubleSide,
      })
    );
    leaf.position.set(0.8, 4.1, 0);
    leaf.rotation.set(-0.5, 0.35, 0.25);
    bunch.add(leaf);
  }

  bunch.position.set(1.9, -0.2, 0);
  bunch.scale.setScalar(0.5);
  scene.add(bunch);

  // ---------- flying grapes ----------
  const flyerGeo = new THREE.SphereGeometry(0.16, 16, 12);
  const flyers = [];
  const FLYER_COUNT = REDUCED ? 6 : 24;
  for (let i = 0; i < FLYER_COUNT; i++) {
    const g = new THREE.Group();
    const berryN = 4 + Math.floor(Math.random() * 3);
    for (let j = 0; j < berryN; j++) {
      const m = new THREE.Mesh(
        flyerGeo,
        berryMats[Math.floor(Math.random() * berryMats.length)]
      );
      m.position.set(rng(-0.16, 0.16), rng(-0.16, 0.16), rng(-0.16, 0.16));
      g.add(m);
    }
    const stemBit = new THREE.Mesh(
      new THREE.CylinderGeometry(0.02, 0.03, 0.22, 5),
      new THREE.MeshStandardMaterial({ color: 0x6d4c2f })
    );
    stemBit.position.y = 0.24;
    stemBit.rotation.z = rng(-0.5, 0.5);
    g.add(stemBit);

    const sc = rng(0.5, 1.5);
    g.scale.setScalar(sc);
    scene.add(g);
    flyers.push({
      g,
      base: new THREE.Vector3(rng(-7, 7), rng(-4, 4), rng(-3.5, 1.5)),
      orbitR: rng(0.3, 1.4),
      orbitSpeed: rng(0.15, 0.5) * (Math.random() < 0.5 ? -1 : 1),
      phase: rng(0, Math.PI * 2),
      bobAmp: rng(0.2, 0.9),
      parallax: rng(0.5, 4.5),
      spin: rng(0.2, 1.2),
    });
  }

  // ---------- scroll choreography ----------
  const doc = document.querySelector(".land-scroll");
  let scrollP = 0;

  const clamp01 = (v) => Math.min(1, Math.max(0, v));
  const smooth = (a, b, v) => {
    const t = clamp01((v - a) / (b - a));
    return t * t * (3 - 2 * t);
  };
  const lerp = (a, b, t) => a + (b - a) * t;

  // keyframes: [progress, x, y, z, scale]
  const POSES = [
    [0.0, 1.9, -0.2, 0.0, 0.5],
    [0.16, 0.0, 0.0, 0.0, 1.05],
    [0.55, 0.0, 0.15, 0.0, 1.15],
    [0.8, -1.7, 0.5, -0.5, 0.85],
    [1.0, -2.1, 0.9, -0.8, 0.72],
  ];

  function poseAt(p) {
    for (let i = 0; i < POSES.length - 1; i++) {
      const [p0, x0, y0, z0, s0] = POSES[i];
      const [p1, x1, y1, z1, s1] = POSES[i + 1];
      if (p <= p1) {
        const t = smooth(p0, p1, p);
        return {
          x: lerp(x0, x1, t),
          y: lerp(y0, y1, t),
          z: lerp(z0, z1, t),
          s: lerp(s0, s1, t),
        };
      }
    }
    const [, x, y, z, s] = POSES[POSES.length - 1];
    return { x, y, z, s };
  }

  function measure() {
    const max = doc.scrollHeight - window.innerHeight;
    scrollP = max > 0 ? clamp01(window.scrollY / max) : 0;
  }

  window.addEventListener("scroll", measure, { passive: true });
  window.addEventListener("resize", () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
    measure();
  });
  measure();

  const clock = new THREE.Clock();

  function frame() {
    requestAnimationFrame(frame);
    if (document.hidden) return;

    const t = clock.getElapsedTime();
    const pose = poseAt(scrollP);

    // hero bunch: scroll-driven spin (the big spin) + gentle idle drift
    bunch.position.set(pose.x, pose.y + Math.sin(t * 0.8) * 0.06, pose.z);
    bunch.scale.setScalar(pose.s);
    bunch.rotation.y = scrollP * Math.PI * 3.5 + (REDUCED ? 0 : t * 0.12);
    bunch.rotation.x = Math.sin(scrollP * Math.PI) * 0.28 + Math.sin(t * 0.5) * 0.03;
    bunch.rotation.z = Math.sin(t * 0.35) * 0.04;

    // flying grapes: orbit + bob + scroll parallax sweep
    for (const f of flyers) {
      const o = f.orbitSpeed * t + f.phase;
      f.g.position.set(
        f.base.x + Math.cos(o) * f.orbitR,
        f.base.y + Math.sin(t * 0.6 + f.phase) * f.bobAmp - scrollP * f.parallax * 2.2,
        f.base.z + Math.sin(o) * f.orbitR * 0.6
      );
      f.g.rotation.y = t * f.spin;
      f.g.rotation.z = Math.sin(t * 0.4 + f.phase) * 0.3;
    }

    // subtle camera sway toward pointer
    camera.lookAt(0, 0, 0);
    renderer.render(scene, camera);
  }
  frame();
}
