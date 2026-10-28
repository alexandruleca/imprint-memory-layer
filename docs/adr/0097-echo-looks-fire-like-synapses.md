---
title: ADR 0097 — Echo's looks fire like synapses
description: A new "flow" node extra runs light through a node at moments stored in its texture coordinates; Echo becomes a generated 3D neural network and each head gets a generated synaptic halo.
---

- Status: Accepted
- Date: 2026-10-28
- Amends: [ADR 0054](0054-echo-looks-declare-their-motion.md) — adds the `flow` extra beside gaze, spin and pulse, and replaces the parts of Echo's generated core
- Related: [ADR 0098](0098-head-surfaces-take-light-and-generated-parts-glow.md) (how the looks are lit), [live voice](../live-voice.md)

## Context

The user asked for all three looks to be "a lot more detailed", and for Echo, "very basic", to look "more detailed and 3d and more synaptic". Echo was the ADR 0054 core: 4,760 triangles in 178 KB. Nearly all of it lay in the screen plane: a ring of ticks, segmented and dashed rings, a thin circle, two tilted orbits, a translucent core with a faceted shell, and a lens. Turning toward the pointer only foreshortened the rings. Iris and Kai were the adapted heads with nothing around them.

The looks animate only through glTF node extras (ADR 0054): `gaze`, `spin` and `pulse`. None of these can show a signal travelling along a fibre or a neuron lighting when a signal reaches it. ADR 0054's invalidation condition says to extend the extras in that case rather than branch the renderer per look.

## Decision

**A `flow` extra runs light through a node.** `flow: [rate, afterglow, swelling]`:

- The light passes each vertex once per cycle, at the moment (a fraction of the cycle) held in the vertex's first texture coordinate. `rate` is cycles per second.
- The vertex then glows, fading over `afterglow` cycles, and its surface swells along its normal by up to `swelling`.
- The cycle runs on its own clock, which advances at the same activity factor as `spin`. Signals therefore fire 2.4× faster while Echo connects or thinks and 1.5× faster while it speaks, and stop under reduced motion.

The renderer does this in the hologram shader for any node that declares `flow`; it has no per-look code. Only nodes that declare `flow` carry texture coordinates in the generated parts.

**Echo is a synaptic core.** `build-echo-avatar.mjs` generates it from a fixed seed, so every build is byte-identical:

- 210 neurons fill a shell around a bright nucleus. No two are closer than 0.09.
- Each neuron joins its three nearest neighbours with a slightly bowed, three-sided tube and sprouts two short dendrites.
- Nine hubs, spread as far apart as possible, start the cascades in turn. A neuron fires when the earliest signal reaches it, travelling at a fixed speed along the links (a shortest-path pass from the hubs). Each axon's moments run from its earlier end's moment along its length, so light leaves one neuron and arrives as the next one lights. Spokes feed each hub from the nucleus.
- The network turns slowly about the vertical axis. Three tilted orbits with beads, the ring of ticks and the segmented arcs frame it.
- The nucleus is a bright core inside two glow shells that grow with the voice, plus the faceted shell. The lens for the pointer sits on it, as before.

**Each head gets a synaptic halo.** The same generator places a sparser network (120 neurons, 0.13 apart) in a shell 1.1–1.35 units out, behind and beside the head (direction z ≤ 0.15). It turns about the view axis, so it never passes in front of the face.

## Rejected alternatives

- **glTF line primitives for the fibres.** WebGL draws lines one pixel wide, so a signal could not swell a line, and the software canvas renderer draws only triangles. Three-sided tubes work in both renderers.
- **Independent random timing per fibre.** Fibres would flash without any relation to their neurons. Moments from a shortest-path pass make a signal arrive where a neuron lights, which is what reads as firing.
- **A particle or signal system in the renderer for Echo.** That is the per-look branch ADR 0054 rejected. Moments stored on vertices keep the network's behaviour in the model.
- **Spinning the halo about the vertical axis.** That carries neurons from behind the head across the face. Turning about the view axis keeps them behind and beside it.

## Consequences

- Echo is a 3D network that shows parallax as it turns toward the pointer. It fires continuously, faster while it thinks, and its nucleus swells as it speaks. The heads share that visual language around their silhouettes.
- Model sizes:

  | Look | Before | After |
  | --- | --- | --- |
  | Echo | 178 KB, 4,760 triangles | 668 KB, 23,256 triangles |
  | Iris | 1.74 MB, 14,998 triangles | 1.97 MB, 27,232 triangles |
  | Kai | 2.06 MB, 12,364 triangles | 2.31 MB, 24,598 triangles |

  Indices are now stored in 16 bits wherever they fit, which keeps these numbers down.
- The no-WebGL canvas renderer draws the networks but does not animate `flow`. Its CPU cost rises:

  | Look | Before (median ms per frame) | After (median ms per frame) |
  | --- | --- | --- |
  | Echo | 4.8 | 44 |
  | Iris | 66 | 91 |
  | Kai | 84 | 88 |

  The fallback is already throttled to 15 frames per second, 2 while Echo is busy, and leaves a full frame interval after each paint.
- Viewers outside Imprint show the networks as still geometry; `flow`, like the other extras, is Imprint's own data.
- Node names are roles shared across looks: `Echo_Synapses`, `Echo_Neurons` and `Echo_Hubs` in every look. The nucleus's outer glow is `Echo_Bloom`.

## Invalidation conditions

- If a look needs light that does not repeat once per cycle (one-off bursts triggered by speech, for example), extend `flow` with that timing rather than adding renderer code for one look.
- If the canvas fallback's frame cost blocks controls or audio on machines without WebGL, generate fewer neurons and fibres for every renderer.
- If glTF gains a standard way to animate per-vertex emission, move `flow` to it.

## Validation

- The build rebuilt Echo, Iris (from the pinned `Victoria_Rubin.vrm`) and Kai (from `HairSample_Male.vrm`) twice each with byte-identical output.
- In the dashboard sandbox, the type check, lint and production build passed, and Playwright's `voice.spec.ts` and `voice-setup.spec.ts` passed (32 tests). Those specs cover the WebGL load and the no-WebGL canvas companion.
- Headless Chromium with SwiftShader WebGL rendered:
  - each look at 720 px, including four frames 0.3 s apart, where different neurons and fibres lit as the cascades moved;
  - the Voice page at 1440×900 and 390×844, and the companion at 380×610, with no console errors and no horizontal overflow.
- The canvas costs above were measured in headless Chromium on WSL. WebGL was refused, so the companion used the canvas renderer. The time is each animation frame's callback over 4 s, with the committed and the new models served in turn.
- Not verified: hardware GPUs (only SwiftShader), the packaged Electron app on Windows and macOS, and how the firing reads under reduced motion beyond the frozen clock.
