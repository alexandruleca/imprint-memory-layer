---
title: ADR 0103 — Echo has a generated eye with shared morph controls
description: Echo's generated iris follows gaze and uses named blink and voice morphs while opaque eye surfaces remain solid.
---

- Status: Accepted
- Date: 2026-10-28
- Recorded: 2026-10-28
- Retrospective: reconstructed from avatar working notes and the implementation committed in 75b62ff and 2748378
- Amends:
  - [ADR 0097](0097-echo-looks-fire-like-synapses.md) — Echo's eye, additional generated detail and the updated asset/performance observations
  - [ADR 0098](0098-head-surfaces-take-light-and-generated-parts-glow.md) — opaque cut-out eye surfaces stay solid while translucent generated parts fade
- Related: [ADR 0054](0054-echo-looks-declare-their-motion.md)

## Context

The avatar working notes record a request for a better variant of Echo's eyes and more detail after the first synaptic core. The previous lens was a flat disc, 0.1 units across, on the nucleus. These notes arrived after the earlier design records had been committed, so their added rationale belongs in a new record.

## Decision

**Echo's eye** turns about the nucleus's center toward the pointer, within the same `gaze` limits as the old lens:

- Its iris is an opaque cut-out disc, 0.46 units across, in front of the core. It is a shallow cone with the pupil set back, so it shows parallax as it turns, and the core shows beside it like the white of an eye.
- Its texture is a generated 256-pixel iris:
  - a dark pupil with a bright rim;
  - waving radial fibres;
  - a zigzag collarette;
  - faint crypts;
  - synapses that trail a streak toward the pupil;
  - a dashed ring;
  - a dark limbal band with a bright edge;
  - a catchlight.
- The disc is a polar grid with two morph targets. `jawOpen` widens the pupil and compresses the iris around it as Echo speaks. `blink` shuts the iris, together with the core, to a line.
- Two gimbal rings in the iris's plane turn in opposite directions: ticks and arcs with markers.

The renderer now drives morph targets by name (`jawOpen` follows the voice, `blink` blinks) instead of by index, so a look may carry either one or both.


The generated network also gains bulbs at the ends of its dendrites, twinkling motes outside the core and outer dots and chevrons. The nucleus's glow is behind the eye rather than veiling its iris. Opaque cut-out parts such as the iris stay solid; translucent generated parts retain ADR 0098's volume-like fade.

## Rejected alternatives

- **A larger flat lens.** The working notes reject it because it still has no pupil, iris or eyelid.
- **A transparent iris.** The working notes report that the core and front glow faces painted over it because blending has no fixed order. An opaque cut-out and back-hemisphere glow avoid that overlap.
- **Blinking the iris alone.** The working notes report that the bright core remained visible as a ball mid-blink, so the core and iris blink together.

## Consequences

The avatar working notes report the following updated observations; the publishing pass did not rerun the timing measurements.


- Echo is a 3D network that shows parallax as it turns toward the pointer. Its eye looks at the pointer, blinks, and widens its pupil as Echo speaks. It fires continuously, faster while it thinks, and its glow swells as it speaks. The heads share that visual language around their silhouettes.
- Model sizes:

  | Look | Before | After |
  | --- | --- | --- |
  | Echo | 178 KB, 4,760 triangles | 988 KB, 30,211 triangles (101 KB of it the iris texture) |
  | Iris | 1.74 MB, 14,998 triangles | 2.03 MB, 29,152 triangles |
  | Kai | 2.06 MB, 12,364 triangles | 2.37 MB, 26,518 triangles |

  Indices are now stored in 16 bits wherever they fit, which keeps these numbers down.
- The no-WebGL canvas renderer draws the networks and the eye, including its blink, but does not animate `flow`. Its CPU cost rises. The medians are per frame, from three runs while the machine's load average was 8 to 14:

  | Look | Before | After |
  | --- | --- | --- |
  | Echo | 3.6–5.8 ms | 49–83 ms |
  | Iris | 60–106 ms | 77–116 ms |
  | Kai | 67–110 ms | 79–106 ms |

  Echo now costs about what the heads already did. The fallback is already throttled to 15 frames per second, 2 while Echo is busy, and leaves a full frame interval after each paint.
- Viewers outside Imprint show the networks as still geometry; `flow`, like the other extras, is Imprint's own data.
- Node names are roles shared across looks: `Echo_Synapses`, `Echo_Neurons` and `Echo_Hubs` in every look. `Echo_Eye` is now a group holding `Echo_Iris`, `Echo_EyeTicks` and `Echo_EyeArcs`. The nucleus's glows are `Echo_Glow` and `Echo_Bloom`.
- Generated files no longer carry empty `textures`, `images` or `samplers` arrays, which glTF does not allow.

## Invalidation conditions

If the additional geometry prevents controls or speech from staying responsive on the canvas fallback, reduce generated detail and retain the shared morph controls. If another look needs different expression names, extend the named control mapping rather than relying on morph array positions.

## Validation

The publishing pass checked generator syntax/lint, rebuilt the production dashboard and passed all 32 Voice/setup browser scenarios on Linux/WSL. Generated PNG previews have valid 168×168 PNG headers. Hardware GPU behavior and packaged Windows/macOS rendering were not tested by this pass.

The visual-design working notes separately record the checks below; those rendering and timing observations were not independently rerun during publication:


- The build rebuilt Echo, Iris (from the pinned `Victoria_Rubin.vrm`) and Kai (from `HairSample_Male.vrm`) twice each with byte-identical output.
- In the dashboard sandbox, the type check, lint and production build passed, and the full Playwright suite passed (137 tests). Its voice specs cover the WebGL load and the no-WebGL canvas companion.
- Headless Chromium with SwiftShader WebGL rendered:
  - each look at 720 px, including four frames 0.3 s apart, where different neurons and fibres lit as the cascades moved;
  - the eye alone at four times zoom: at rest, with the pupil fully widened, mid-blink, and turned 0.4 rad, where it shows as an eyeball;
  - the Voice page at 1440×900 and 390×844, and the companion at 380×610, with no console errors and no horizontal overflow.
- The canvas costs above were measured in headless Chromium on WSL. WebGL was refused, so the companion used the canvas renderer. The time is each animation frame's callback over 4 s, with the committed and the new models served in turn, three times.
- Not verified: hardware GPUs (only SwiftShader), the packaged Electron app on Windows and macOS, and how the firing reads under reduced motion beyond the frozen clock.
