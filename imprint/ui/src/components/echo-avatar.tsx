"use client";

import { useEffect, useRef, useState } from "react";
import type { VoicePhase } from "@/lib/voice-client";

export default function EchoAvatar({ phase, energy, compact = false }: {
  phase: VoicePhase; energy: () => number; compact?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const current = useRef({ phase, energy });
  const [failed, setFailed] = useState(false);
  useEffect(() => { current.current = { phase, energy }; }, [phase, energy]);
  useEffect(() => {
    const container = host.current!;
    let cancelled = false;
    let release: (() => void) | undefined;
    async function mount() {
      const THREE = await import("three");
      const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
      if (cancelled) return;
      let renderer: import("three").WebGLRenderer | import("@/lib/echo-canvas-renderer").EchoCanvasRenderer;
      let software = false;
      try {
        renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "low-power" });
        renderer.setClearColor(0x000000, 0);
        renderer.outputColorSpace = THREE.SRGBColorSpace;
      } catch {
        const { EchoCanvasRenderer } = await import("@/lib/echo-canvas-renderer");
        if (cancelled) return;
        renderer = new EchoCanvasRenderer(); software = true;
      }
      container.dataset.renderer = software ? "canvas-3d" : "webgl";
      renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
      container.appendChild(renderer.domElement);
      renderer.domElement.setAttribute("aria-hidden", "true");
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(31, 1, .1, 30);
      camera.position.set(0, .04, compact ? 6.0 : 5.7);
      const group = new THREE.Group(); scene.add(group);
      const resize = new ResizeObserver(() => {
        const { width, height } = container.getBoundingClientRect();
        renderer.setSize(width, height); camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix();
      });
      resize.observe(container);
      let frame = 0, previous = 0, jaw = 0;
      const reduced = matchMedia("(prefers-reduced-motion: reduce)");
      const pointer = new THREE.Vector2();
      function move(event: PointerEvent) {
        const rect = container.getBoundingClientRect();
        pointer.set(((event.clientX - rect.left) / rect.width - .5) * .22, ((event.clientY - rect.top) / rect.height - .5) * .1);
      }
      container.addEventListener("pointermove", move);
      const draw = (now: number) => {
        frame = requestAnimationFrame(draw);
        if (document.hidden || now - previous < (reduced.matches ? 100 : software ? 66 : 33)) return;
        previous = now;
        const t = now / 1000;
        const active = current.current.phase;
        const level = current.current.energy();
        jaw += (Math.min(1, level * 1.8) - jaw) * .4;
        const blink = reduced.matches ? 0 : Math.max(0, 1 - Math.abs((t % 5.3) - 5.12) / .13);
        group.rotation.y = reduced.matches ? -.48 : -.48 + Math.sin(t * .26) * .10 + pointer.x;
        group.rotation.x = reduced.matches ? .01 : Math.sin(t * .35) * .025 + pointer.y;
        group.position.y = reduced.matches ? 0 : Math.sin(t * .75) * .035;
        group.traverse(object => {
          if (object instanceof THREE.Points || object instanceof THREE.Line || object instanceof THREE.Mesh) {
            if (object.morphTargetInfluences) {
              object.morphTargetInfluences[0] = jaw;
              object.morphTargetInfluences[1] = blink;
            }
            if (object instanceof THREE.Points) {
              const material = object.material as import("three").PointsMaterial;
              material.opacity = object.name === "Echo_Face" ? .80 + level * .18 : .60;
              material.color.set(active === "listening" ? "#beefff" : active === "thinking" ? "#9ebaff" : "#d9f5ff");
            }
          }
        });
        renderer.render(scene, camera);
      };
      function dispose() {
        cancelAnimationFrame(frame); resize.disconnect(); container.removeEventListener("pointermove", move);
        scene.traverse(object => {
          if (object instanceof THREE.Points || object instanceof THREE.Line || object instanceof THREE.Mesh) {
            object.geometry.dispose();
            (Array.isArray(object.material) ? object.material : [object.material]).forEach(material => material.dispose());
          }
        });
        renderer.dispose(); renderer.domElement.remove();
      }
      release = dispose;
      try {
        const model = await new GLTFLoader().loadAsync("/models/imprint-echo.glb");
        if (cancelled) { model.scene.traverse(object => {
          if (object instanceof THREE.Points || object instanceof THREE.Line) {
            object.geometry.dispose(); (Array.isArray(object.material) ? object.material : [object.material]).forEach(m => m.dispose());
          }
        }); return; }
        model.scene.traverse(object => {
          if (object instanceof THREE.Mesh && object.name === "Echo_Depth") {
            const material = object.material as import("three").MeshBasicMaterial;
            material.colorWrite = false; material.depthWrite = true; material.transparent = false;
            object.renderOrder = -1;
          }
          if (object instanceof THREE.Points) {
            const material = object.material as import("three").PointsMaterial;
            material.size = object.name === "Echo_Face" ? .009 : .011;
            material.transparent = true; material.depthWrite = false;
            material.blending = THREE.AdditiveBlending;
          }
        });
        group.add(model.scene);
        container.dataset.loaded = "true";
        frame = requestAnimationFrame(draw);
      } catch (error) { dispose(); throw error; }
    }
    void mount().catch(error => {
      if (!cancelled) {
        container.dataset.error = error instanceof Error ? error.message.slice(0, 160) : "WebGL preview unavailable";
        setFailed(true);
      }
    });
    return () => { cancelled = true; release?.(); };
  }, [compact]);
  return <div ref={host} className="echo-canvas" role="img" aria-label="Echo, an abstract holographic face made of luminous particles">
    {failed && <div className="echo-fallback"><span>✧</span><p>Echo</p><small>3D preview unavailable. Voice controls are still available.</small></div>}
  </div>;
}
