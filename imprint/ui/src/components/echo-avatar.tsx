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
      let softwareWebGL = false;
      try {
        renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "low-power" });
        renderer.setClearColor(0x000000, 0);
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        const gl = renderer.getContext();
        const debug = gl.getExtension("WEBGL_debug_renderer_info");
        const device = String(gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
        softwareWebGL = /swiftshader|llvmpipe|softpipe|software|microsoft basic render/i.test(device);
      } catch {
        const { EchoCanvasRenderer } = await import("@/lib/echo-canvas-renderer");
        if (cancelled) return;
        renderer = new EchoCanvasRenderer(); software = true;
      }
      container.dataset.renderer = software ? "canvas-3d" : "webgl";
      // Software WebGL can occupy every CPU with rasterizer threads, starving
      // local model verification/warmup when both dashboard and companion show.
      const frameRate = software ? 15 : softwareWebGL ? 10 : 30;
      container.dataset.frameRateLimit = String(frameRate);
      renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
      container.appendChild(renderer.domElement);
      renderer.domElement.setAttribute("aria-hidden", "true");
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(31, 1, .1, 30);
      camera.position.set(0, .04, compact ? 6.0 : 5.7);
      const group = new THREE.Group(); scene.add(group);
      const resize = new ResizeObserver(() => {
        const { width, height } = container.getBoundingClientRect();
        renderer.setSize(width, height); hasDrawn = false; camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix();
      });
      resize.observe(container);
      let frame = 0, previous = 0, jaw = 0, hasDrawn = false;
      let companionShowing = false;
      const unsubscribe = !compact ? window.electronAPI?.onCompanionState?.(state => { companionShowing = state.open; }) : undefined;
      if (!compact) void window.electronAPI?.getCompanionState?.().then(state => { if (state) companionShowing = state.open; });
      const hologramTime = { value: 0 };
      const reduced = matchMedia("(prefers-reduced-motion: reduce)");
      const pointer = new THREE.Vector2();
      function move(event: PointerEvent) {
        const rect = container.getBoundingClientRect();
        pointer.set(((event.clientX - rect.left) / rect.width - .5) * .22, ((event.clientY - rect.top) / rect.height - .5) * .1);
      }
      container.addEventListener("pointermove", move);
      const draw = (now: number) => {
        frame = requestAnimationFrame(draw);
        const phase = current.current.phase;
        const busy = phase === "connecting" || phase === "thinking";
        const effectiveRate = reduced.matches ? phase === "speaking" ? 10 : 1 : (software || softwareWebGL) && busy ? 2 : frameRate;
        if (document.hidden || (hasDrawn && companionShowing && !compact) || now - previous < 1000 / effectiveRate) return;
        previous = now;
        const t = now / 1000;
        hologramTime.value = t;
        const active = current.current.phase;
        const level = current.current.energy();
        jaw += (Math.min(1, level * 1.8) - jaw) * .4;
        const blink = reduced.matches ? 0 : Math.max(0, 1 - Math.abs((t % 5.3) - 5.12) / .13);
        group.rotation.y = reduced.matches ? -.26 : -.26 + Math.sin(t * .26) * .10 + pointer.x;
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
              material.opacity = (object.userData.opacity ?? (object.name === "Echo_Face" ? .80 : .60)) + (object.name === "Echo_Face" ? level * .10 : 0);
              material.color.set(active === "listening" ? "#beefff" : active === "thinking" ? "#9ebaff" : "#d9f5ff");
            }
          }
        });
        renderer.render(scene, camera); hasDrawn = true;
      };
      function disposeModel(root: import("three").Object3D) {
        const geometries = new Set<import("three").BufferGeometry>();
        const materials = new Set<import("three").Material>();
        const textures = new Set<import("three").Texture>();
        root.traverse(object => {
          if (!(object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof THREE.Points)) return;
          geometries.add(object.geometry);
          for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
            materials.add(material);
            for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
          }
        });
        geometries.forEach(g => g.dispose()); materials.forEach(m => m.dispose());
        const images = new Set<ImageBitmap>();
        textures.forEach(t => { t.dispose(); if (typeof ImageBitmap !== "undefined" && t.image instanceof ImageBitmap) images.add(t.image); });
        images.forEach(image => image.close());
      }
      function dispose() {
        cancelAnimationFrame(frame); unsubscribe?.(); resize.disconnect(); container.removeEventListener("pointermove", move);
        disposeModel(scene); renderer.dispose(); renderer.domElement.remove();
      }
      release = dispose;
      try {
        const model = await new GLTFLoader().loadAsync("/models/imprint-echo.glb");
        if (cancelled) { disposeModel(model.scene); return; }
        const surfaces: import("three").Mesh[] = [];
        model.scene.traverse(object => { if (object instanceof THREE.Mesh) surfaces.push(object); });
        for (const object of surfaces) {
          const material = object.material as import("three").MeshBasicMaterial;
          if (!software) {
            material.onBeforeCompile = shader => {
              shader.uniforms.echoTime = hologramTime;
              shader.vertexShader = "varying vec3 echoPosition; varying vec3 echoNormal; varying vec3 echoView;\n" + shader.vertexShader;
              shader.vertexShader = shader.vertexShader.replace("#include <project_vertex>",
                "#include <project_vertex>\nechoPosition = transformed; echoNormal = normalize(normalMatrix * normal); echoView = -mvPosition.xyz;");
              shader.fragmentShader = "uniform float echoTime; varying vec3 echoPosition; varying vec3 echoNormal; varying vec3 echoView;\n" + shader.fragmentShader;
              shader.fragmentShader = shader.fragmentShader.replace("#include <opaque_fragment>", `#include <opaque_fragment>
                float lightness = dot(gl_FragColor.rgb, vec3(.2126,.7152,.0722));
                vec3 projectionColor = vec3(.20,.69,.96) * (.16 + lightness * .95);
                gl_FragColor.rgb = mix(gl_FragColor.rgb, projectionColor, .62);
                float rim = pow(1.0 - abs(dot(normalize(echoNormal), normalize(echoView))), 3.0);
                gl_FragColor.rgb += rim * vec3(.12,.36,.48);
                gl_FragColor.rgb *= .965 + .035 * sin(echoPosition.y * 240.0 + echoTime * 1.3);
              `);
            };
            material.customProgramCacheKey = () => "echo-hologram-v3";
            // Shared geometry keeps eyelids/lips and their fine wire overlay aligned.
            if (/SKIN|HAIR_01/.test(material.name)) {
              const wire = new THREE.Mesh(object.geometry, new THREE.MeshBasicMaterial({ color: "#8bdeff",
                wireframe: true, transparent: true, opacity: /HAIR/.test(material.name) ? .045 : .075,
                depthWrite: false, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }));
              wire.name = "Echo_Contour";
              wire.morphTargetInfluences = object.morphTargetInfluences;
              object.add(wire);
            }
          }
        }
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
  return <div ref={host} className="echo-canvas" role="img" aria-label="Echo, an anime holographic head with luminous contours">
    {failed && <div className="echo-fallback"><span>✧</span><p>Echo</p><small>3D preview unavailable. Voice controls are still available.</small></div>}
  </div>;
}
