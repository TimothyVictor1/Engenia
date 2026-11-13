// three.js STL viewer. Loaded on demand by the Design view so the other views never wait for
// (or break on) the CDN. Renders only when something changes, so an idle page costs nothing.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';

/** Resolve a CSS colour token (which may use light-dark()) to a THREE.Color via a probe element. */
function tokenColor(probe, token, fallback) {
  probe.style.color = `var(${token})`;
  const resolved = getComputedStyle(probe).color;
  const color = new THREE.Color(fallback);
  try {
    color.setStyle(resolved);
  } catch (err) {
    /* keep fallback */
  }
  return color;
}

export function parseSTL(buffer) {
  const geometry = new STLLoader().parse(buffer);
  const position = geometry.getAttribute('position');
  if (!position || position.count < 3 || Number.isNaN(position.array[0])) {
    geometry.dispose();
    throw new Error('The file is not a valid STL.');
  }
  return geometry;
}

export function createViewer(container) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  container.prepend(renderer.domElement);
  renderer.domElement.setAttribute('role', 'img');
  renderer.domElement.setAttribute('aria-label', '3D view of the hull and lid. Drag to rotate, scroll to zoom, right-drag to pan.');
  renderer.domElement.tabIndex = 0;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 1, 10000);
  camera.up.set(0, 0, 1); // CAD exports are Z-up

  scene.add(new THREE.HemisphereLight(0xffffff, 0x8890a0, 1.6));
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.7);
  scene.add(fill);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = false; // render-on-demand: no animation loop
  controls.screenSpacePanning = true;

  const probe = document.createElement('span');
  probe.style.display = 'none';
  container.append(probe);

  const model = new THREE.Group();
  scene.add(model);
  let grid = null;
  let gridSpec = null;
  const parts = { hull: null, lid: null };
  let disposed = false;
  let home = null;

  const render = () => {
    if (!disposed) renderer.render(scene, camera);
  };
  controls.addEventListener('change', render);

  function placeGrid() {
    if (grid) {
      scene.remove(grid);
      grid.geometry.dispose();
      grid.material.dispose();
      grid = null;
    }
    if (!gridSpec) return;
    const major = tokenColor(probe, '--grid-major', '#b5bbc6');
    const minor = tokenColor(probe, '--grid-minor', '#d9dce3');
    grid = new THREE.GridHelper(gridSpec.size, gridSpec.divisions, major, minor);
    grid.rotation.x = Math.PI / 2; // lie in the XY plane (Z is up)
    grid.position.copy(gridSpec.origin);
    scene.add(grid);
  }

  function applyTheme() {
    renderer.setClearColor(tokenColor(probe, '--viewport-bg', '#f4f5f7'));
    const edge = tokenColor(probe, '--model-edge', '#7d8594');
    if (parts.hull) {
      parts.hull.mesh.material.color.copy(tokenColor(probe, '--model', '#b9bec8'));
      parts.hull.edges.material.color.copy(edge);
    }
    if (parts.lid) {
      parts.lid.mesh.material.color.copy(tokenColor(probe, '--model-lid', '#d6dae1'));
      parts.lid.edges.material.color.copy(edge);
    }
    placeGrid();
    render();
  }

  const media = window.matchMedia('(prefers-color-scheme: dark)');
  media.addEventListener('change', applyTheme);

  function resize() {
    const { clientWidth: w, clientHeight: h } = container;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    render();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(container);

  function addPart(name, geometry, colorToken) {
    const material = new THREE.MeshStandardMaterial({
      color: tokenColor(probe, colorToken, '#b9bec8'),
      roughness: 0.75,
      metalness: 0.05,
      side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(geometry, material);
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(geometry, 35),
      new THREE.LineBasicMaterial({ color: tokenColor(probe, '--model-edge', '#7d8594') }),
    );
    const group = new THREE.Group();
    group.add(mesh, edges);
    model.add(group);
    parts[name] = { group, mesh, edges };
  }

  function clearParts() {
    for (const name of Object.keys(parts)) {
      const part = parts[name];
      if (!part) continue;
      model.remove(part.group);
      part.mesh.geometry.dispose();
      part.mesh.material.dispose();
      part.edges.geometry.dispose();
      part.edges.material.dispose();
      parts[name] = null;
    }
  }

  function frame(keepCamera) {
    const box = new THREE.Box3().setFromObject(model);
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const radius = Math.max(size.x, size.y, size.z) * 0.5 || 1;

    const span = Math.ceil((radius * 4) / 50) * 50;
    gridSpec = { size: span, divisions: Math.round(span / 10), origin: new THREE.Vector3(center.x, center.y, box.min.z) };
    placeGrid();

    const distance = radius / Math.tan((camera.fov * Math.PI) / 360) * 1.9;
    home = {
      target: center.clone(),
      position: center.clone().add(new THREE.Vector3(1, -1.25, 0.8).normalize().multiplyScalar(distance)),
    };
    camera.near = Math.max(distance / 100, 0.1);
    camera.far = distance * 20;
    camera.updateProjectionMatrix();
    key.position.copy(center).add(new THREE.Vector3(1, -1, 2).multiplyScalar(radius * 2));
    fill.position.copy(center).add(new THREE.Vector3(-2, 1, 0.5).multiplyScalar(radius * 2));
    controls.minDistance = radius * 0.5;
    controls.maxDistance = distance * 6;
    if (!keepCamera) reset();
  }

  function reset() {
    if (!home) return;
    camera.position.copy(home.position);
    controls.target.copy(home.target);
    controls.update();
    render();
  }

  return {
    /** geometries: { hull?: BufferGeometry, lid?: BufferGeometry }. Takes ownership. */
    setModels(geometries, { keepCamera = false } = {}) {
      clearParts();
      if (geometries.hull) addPart('hull', geometries.hull, '--model');
      if (geometries.lid) addPart('lid', geometries.lid, '--model-lid');
      frame(keepCamera && Boolean(home));
      resize();
      applyTheme();
    },
    setLidVisible(visible) {
      if (parts.lid) parts.lid.group.visible = visible;
      render();
    },
    reset,
    dispose() {
      disposed = true;
      observer.disconnect();
      media.removeEventListener('change', applyTheme);
      controls.removeEventListener('change', render);
      controls.dispose();
      clearParts();
      gridSpec = null;
      placeGrid();
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
      probe.remove();
    },
  };
}
