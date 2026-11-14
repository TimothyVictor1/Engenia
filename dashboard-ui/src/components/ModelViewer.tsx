// three.js STL viewer. Renders only when something changes, so an idle page costs nothing.

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { fetchFile } from "@/lib/api";
import { Loading } from "./States";

export interface ModelFile { name: string; url: string }
type Phase = { kind: "loading" } | { kind: "missing" } | { kind: "error"; message: string } | { kind: "ready" };

export function ModelViewer({ files, hidden = [], height = 420 }: { files: ModelFile[]; hidden?: string[]; height?: number }) {
  const host = useRef<HTMLDivElement>(null);
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const apply = useRef<() => void>(() => {});
  const key = files.map((f) => f.url).join("|");

  useEffect(() => {
    const el = host.current;
    if (!el || files.length === 0) { setPhase({ kind: "missing" }); return; }
    let dead = false;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }) /* transparent: the themed grid shows through */;
    } catch {
      setPhase({ kind: "error", message: "This browser could not start WebGL, so the 3D view is unavailable." });
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    el.prepend(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 1, 10000);
    camera.up.set(0, 0, 1); // CAD exports are Z-up
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8890a0, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 2.2);
    scene.add(sun);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.screenSpacePanning = true;
    const group = new THREE.Group();
    scene.add(group);
    const meshes = new Map<string, THREE.Mesh>();

    const draw = () => renderer.render(scene, camera);
    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      draw();
    };
    apply.current = () => { meshes.forEach((m, n) => { m.visible = !hiddenRef.current.includes(n); }); draw(); };
    controls.addEventListener("change", draw);
    const observer = new ResizeObserver(resize);
    observer.observe(el);

    setPhase({ kind: "loading" });
    Promise.all(files.map((f) => fetchFile(f.url))).then((buffers) => {
      if (dead) return;
      const loaded = buffers.map((b, i) => [files[i], b] as const).filter(([, b]) => b !== "missing");
      if (!loaded.length) { setPhase({ kind: "missing" }); return; }
      const palette = [0xaab0bd, 0xd6dae1, 0x8a919e];
      loaded.forEach(([file, buffer], i) => {
        const geometry = new STLLoader().parse(buffer as ArrayBuffer);
        const position = geometry.getAttribute("position");
        if (!position || position.count < 3 || Number.isNaN(position.array[0])) throw new Error(`${file.name} is not a valid STL.`);
        const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: palette[i % palette.length], roughness: 0.6, metalness: 0.1 }));
        meshes.set(file.name, mesh);
        group.add(mesh);
      });
      const box = new THREE.Box3().setFromObject(group);
      const size = box.getSize(new THREE.Vector3()), centre = box.getCenter(new THREE.Vector3());
      const radius = Math.max(size.x, size.y, size.z, 1);
      controls.target.copy(centre);
      camera.position.copy(centre).add(new THREE.Vector3(radius * 1.6, -radius * 1.8, radius * 1.1));
      camera.far = radius * 40;
      camera.updateProjectionMatrix();
      sun.position.copy(centre).add(new THREE.Vector3(radius, -radius, radius * 2));
      controls.update();
      apply.current();
      setPhase({ kind: "ready" });
    }).catch((error: Error) => { if (!dead) setPhase({ kind: "error", message: error.message || "Could not load the model." }); });

    return () => {
      dead = true;
      observer.disconnect();
      controls.dispose();
      meshes.forEach((m) => { m.geometry.dispose(); (m.material as THREE.Material).dispose(); });
      renderer.dispose();
      renderer.domElement.remove();
    };
    // `key` stands for the file list; the effect must restart when a URL changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt]);

  useEffect(() => { apply.current(); }, [hidden.join("|")]);

  return (
    <div ref={host} style={{ height }} className="engineering-grid relative w-full overflow-hidden rounded-lg border bg-surface">
      {phase.kind === "loading" && <div className="absolute inset-0 grid place-items-center bg-surface/70"><Loading text="Loading model…" /></div>}
      {phase.kind === "missing" && <div className="absolute inset-0 grid place-items-center p-6 text-center text-sm"><div><strong>Not built yet</strong><p className="text-muted-foreground">The mechanical stage has not produced a model.</p></div></div>}
      {phase.kind === "error" && (
        <div role="alert" className="absolute inset-0 grid place-items-center p-6 text-center text-sm"><div>
          <strong>Could not show the 3D model</strong><p className="text-muted-foreground">{phase.message}</p>
          <button onClick={() => setAttempt((n) => n + 1)} className="mt-3 rounded-md border px-3 py-1 text-xs hover:bg-muted">Try again</button></div></div>)}
      {phase.kind === "ready" && <p className="pointer-events-none absolute bottom-2 left-3 text-[11px] text-muted-foreground">Drag to rotate · scroll to zoom · right-drag to pan</p>}
    </div>
  );
}
