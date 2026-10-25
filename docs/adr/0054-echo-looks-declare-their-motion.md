---
title: ADR 0054 — Echo's looks declare their motion and follow the pointer
description: Each look's glTF node extras declare gaze, spin and pulse; the heads' eyes turn at the source eye bones; Echo follows the pointer across the window, or the screen in the desktop app.
---

- Status: Accepted
- Date: 2026-10-25
- Amends: [ADR 0048](0048-anime-echo-and-desktop-recovery.md) — replaces the adapted anime head with a generated core as Echo's look; the desktop controls are unchanged
- Restructured: 2026-10-25

## Context

Echo's renderer animated one kind of model: an anime head whose `Echo_Face` mesh carries `jawOpen` and `blink` morphs. Three requests did not fit that:

- The default look should be a JARVIS-like holographic core instead of the Haruhi-style head. A core has no face, mouth or eyelids. Its motion is rings turning and a core that reacts to the voice.
- Head follow worked only while the pointer was over the avatar canvas. The renderer listened for `pointermove` on the canvas container, so the head stopped following as soon as the pointer moved to the controls, the sidebar or, in the desktop companion, anywhere outside its small window.
- The eyes did not follow at all. The heads were baked into a single face mesh, so the irises moved only with the whole head.

The VRoid sources already define how their eyes move. Their iris and highlight triangles are skinned entirely to the humanoid `leftEye` and `rightEye` bones (61 to 67 vertices per eye in each source), and their VRM `firstPerson` block declares a look-at range of 12° horizontally and 10° vertically.

## Decision

**Motion is data on the model.** The renderer applies three glTF node extras, whatever the look:

- `gaze: [yaw, pitch]` aims the node at the pointer within those limits, in radians.
- `spin: [x, y, z]` turns the node at those rates, in radians per second, faster while Echo connects or thinks (2.4×) and speaks (1.5×). With reduced motion nothing spins.
- `pulse: k` scales the node by `1 + k × voice level`.

The head morphs (`jawOpen`, `blink`) are unchanged.

**The heads' eyes are their own nodes.** `build-echo-avatar.mjs` moves each face triangle whose three vertices are all bound to the `leftEye` or `rightEye` bone into an `Echo_EyeL` or `Echo_EyeR` node. Each node is placed at that bone's bind position, and its vertices are stored relative to it. The node's `gaze` extra is the source's declared look-at range. Every other triangle stays in `Echo_Face`. At rest the head renders as before. Baked expression positions are now computed once per source buffer rather than once per primitive, which makes Kai about 230 KB smaller.

**Echo is a generated core.** The build script generates Echo from Three.js primitives instead of adapting a sample:

- a ring of 120 ticks, segmented and dashed rings, and a thin circle, each turning at its own speed;
- two tilted orbits that each carry a bead;
- a translucent core with a faceted shell;
- a lens on the core's face, carrying a `gaze` extra.

The model is 178 KB, down from 1.86 MB, and is original Apache-2.0 artwork. The Haruhi-style head and its ribbon accessories are removed. Iris and Kai stay as the other looks.

**Gaze uses the page's own coordinates.**

- In a browser the renderer follows `pointermove` on the window, so Echo follows the pointer anywhere in the page.
- In the desktop app the preload's `watchCursor` asks the main process for the cursor instead. While a trusted voice page watches, the main process sends `screen.getCursorScreenPoint()` about 30 times per second, translated into that page's CSS pixels by its content bounds and zoom factor, and only when the point changes relative to the page. The watch ends when the page unsubscribes, navigates or is destroyed.
- The head turns 70% of the way toward the pointer, up to 0.5 rad of yaw and 0.6 of the way up to 0.28 rad of pitch, easing over about 0.35 s. The eyes take the rest within their own limits, easing over 0.06 s. Before the first pointer sample, the head keeps its resting three-quarter turn.

## Rejected alternatives

- **A procedural core in the renderer with its own code path.** This would give Echo a second animation system beside the glTF looks and a branch per look in the renderer. Declaring motion as data keeps one renderer.
- **Moving the iris by offsetting its texture coordinates.** The VRoid irises are not texture windows over the eye white; they are separate geometry bound to the eye bones. Rotating that geometry about the bone is how the source animates them.
- **Hard-coded eye limits in the renderer.** These would duplicate the sources' look-at range and would not describe the core's lens.
- **Reading the cursor in the desktop app from `pointermove` alone.** The renderer receives no pointer events outside its window, which is where the companion's cursor usually is.
- **Sending raw screen coordinates to the renderer.** The renderer would then need window, frame and zoom offsets that only the main process knows.

## Consequences

- Echo follows the pointer across the window in browsers and across the whole screen in the desktop app, and the heads' eyes lead the head.
- A new look can turn, pulse or aim parts of itself without renderer changes. A look with no extras stays still apart from the shared idle sway.
- Echo's shipped model shrinks by about 1.7 MB.
- The desktop main process polls the cursor only while a voice page is watching.
- The Echo preview no longer depicts the earlier head. Look previews are now transparent PNGs, so they suit both the light Voice page and the dark companion.
- Existing `voice.avatar = echo` settings show the core.

## Invalidation conditions

- If a look needs motion the gaze, spin and pulse extras cannot express, extend the extras rather than branch the renderer per look.
- If Electron delivers pointer events from outside the window to the renderer, the main-process cursor feed can be retired.

## Validation

- `node --test` in `ui-electron` passed (27 tests). They include the cursor watch: a non-voice page is refused, the point is sent in page coordinates and scaled by zoom, unchanged points are not resent, nested watches are counted, and a navigation ends the watch.
- The dashboard type check and lint passed. The Playwright suite passed against the production build.
- Rebuilt heads diffed against the earlier previews differed by at most 17 of 255 levels, and only in the eye region; an enlarged crop looked the same.
- Headless Chromium on the production build moved the pointer to the sidebar, the start button, the top-right corner and the center. The head and eyes of Kai, and the core's lens, turned toward each point. Both the WebGL and the software canvas renderer draw the core.
- Not validated: the packaged desktop app's cursor feed on Windows and macOS, and multi-monitor setups with mixed scaling.
