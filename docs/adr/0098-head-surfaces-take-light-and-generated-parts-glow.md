---
title: ADR 0098 — Head surfaces take light; generated parts glow
description: The heads export lit glTF materials that the renderer lights with a sky and a key light; generated parts stay unlit and render as light seen through a volume.
---

- Status: Accepted
- Date: 2026-10-28
- Related: [ADR 0054](0054-echo-looks-declare-their-motion.md) (the looks and their extras), [ADR 0097](0097-echo-looks-fire-like-synapses.md) (the synaptic parts), [live voice](../live-voice.md)

## Context

The user asked for the looks to be more detailed and more 3D. Every material in every look was `KHR_materials_unlit`, so the renderer drew the heads without light. Their only 3D cue was the hologram shader's rim term, and noses, cheeks and jaws read flat. In renders of Kai and Iris before this change, the face was one even tone from the forehead to the chin.

A first attempt added a key-light term to the hologram shader for every material. It shaded the heads well, but it also shaded Echo's generated neurons and nucleus. They read as solid balls on sticks, like a molecule model, rather than as light.

## Decision

Lighting is the glTF material's own property:

- **Head surfaces are lit.** `build-echo-avatar.mjs` exports the heads' skin, hair and eye materials without `KHR_materials_unlit`, so they load as standard materials (metallic 0, roughness 1). The renderer lights the scene with a hemisphere light (sky `#e6f6ff`, ground `#123247`, intensity 1.5) and a white key light from the upper left (intensity 2.7). The hologram shader then tints and rims the lit color as before.
- **Generated parts are light.** Echo's core, the networks and the frames stay `KHR_materials_unlit`. The renderer treats an unlit material as emitting light seen through a volume. Its opacity is scaled by how directly the surface faces the viewer, so tubes and spheres fade toward their silhouettes instead of showing a rim.
- **Every surface dims with depth.** Brightness falls by 45% at one unit or more behind the node's origin, and rises by up to 13.5% in front of it.

`extensionsUsed` lists `KHR_materials_unlit` only when a material in the file uses it.

## Rejected alternatives

- **One key-light term for every material.** This was tried. It made the generated parts look like solid shaded objects (see Context).
- **A per-look flag or node extra choosing lit or unlit shading.** glTF already states this per material with `KHR_materials_unlit`; a second flag would duplicate it and could disagree with it.
- **Keeping the heads unlit with a stronger rim.** The rim only brightens silhouettes; it cannot shape a nose or a cheek inside the outline.

## Consequences

- Faces and hair show their modelled form. In the new renders the nose, cheekbones and jaw are shaded and the key side is brighter.
- Standard materials cost more per pixel than unlit ones. The heads are a few hundred pixels across and the renderer runs at 30 frames per second (10 on software WebGL).
- The no-WebGL canvas renderer ignores lights, as before. It draws texture colors with its own hologram tint.
- Downloaded head models now open lit in other glTF viewers.
- New looks choose per material: lit if it is a surface, unlit if it is light.

## Invalidation conditions

- If lit standard materials prove too slow on software WebGL (frames well below the 10 per second limit with a head on screen), light the heads with a cheaper Lambert material instead of returning to unlit.
- If a look needs a surface that is both lit and partly glowing, use the glTF emissive factor or texture rather than mixing the two shadings by name.

## Validation

- Renders of Iris and Kai at 720 px, before and after, show the shading described above. Three key and sky balances were compared, and the strongest key (2.7 against 1.5) was chosen.
- The production dashboard sandbox loaded all three looks on the Voice page (1440×900, 390×844) and in the companion (380×610) under SwiftShader WebGL, with no console errors. The type check, lint, build and the voice Playwright specs passed (32 tests).
- Not verified: hardware GPUs, frame rate on software WebGL with lit materials, and the packaged Electron app.
