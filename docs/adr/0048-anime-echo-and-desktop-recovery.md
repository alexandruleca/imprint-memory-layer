---
title: ADR 0048 — Anime Echo and recoverable desktop controls
description: A modeled anime hologram, viewport-fitting controls and a draggable desktop launcher with explicit recovery.
---

- Status: Amended
- Date: 2026-10-24
- Amends: [ADR 0043](0043-live-voice-and-echo-companion.md) — replaces its MediaPipe particle asset; voice/session boundaries are unchanged
- Amended by:
  - [ADR 0054](0054-echo-looks-declare-their-motion.md) — Echo's look is now a generated core; the adapted anime head is removed; linked on restructuring, confirmed by the owner on 2026-10-25
  - [ADR 0070](0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md) — only the head and header drag the companion, and while the dashboard is hidden the companion docks beside the launcher and moves with it
- Related: [ADR 0025](0025-desktop-owned-background-service.md), [ADR 0044](0044-native-desktop-voice-platforms.md)
- Restructured: 2026-10-25

## Context

The user rejected the procedural face and requested recognizable anime anatomy,
using Suzumiya Haruhi as a visual reference, while retaining a transparent floating
hologram with fine connected lines. Header-only dragging was difficult to find,
and an offscreen companion needed recovery. Closing/minimizing the dashboard
should leave an accessible draggable icon. Compact controls must fit the window.

## Decision

Adapt pixiv's explicitly CC0 beta Sendagaya Shino mesh into a head-only glTF with
shortened rear hair, a new headband/clasp, mouth/blink morphs, a cool holographic
surface and fine wire contours. Pin and verify source bytes; ship the model and
its CC0 provenance locally. Imprint’s modifications follow the repository’s
Apache-2.0 license; both notices accompany the downloadable model. This is an adaptation of a credited sample, not a
new original sculpt or a licensed Haruhi asset. It introduces no VRM runtime,
remote model service or face tracking dependency. Texture/geometry processing
is reproducible in the authoring script. The GPU-less renderer projects the same
textured geometry and expressions onto transparent canvas.

Drag any non-interactive area of the companion, including the head. Renderer
pointer capture sends validated screen coordinates through IPC scoped to the
owned companion main frame. Controls retain their normal interactions. Save
bounds privately under Electron userData (discarding malformed dimensions) and clamp to the available work area on
open, drag completion and display changes. No memory/configuration schema changes.

Register `CommandOrControl+Alt+Shift+E` after app readiness. Recovery shows, restores,
centers and focuses Echo on the cursor's display, without enabling its microphone.
Tray/launcher menus expose the same recovery if a desktop reserves the shortcut;
the footer reflects registration failure. Desktop compositor/shortcut policy can
still prevent registration; physical global-key behavior needs native validation.

Dashboard minimize and Close hide it to the system tray and show a transparent
56-pixel draggable launcher. Click restores the dashboard; right-click exposes
Open Dashboard, Show Echo and Quit. The launcher uses a separate sandboxed route,
scoped IPC, no microphone permission and private persisted/clamped bounds. Quit
remains explicit and retains API ownership policy. The Mac menu-bar image is
resized to two-thirds (32 to 21 pixels); other platforms retain the existing image.

Use a flex viewport for the companion, retaining visible controls and a flexible
head area. Long transcripts are visually bounded; download details remain in the
full Voice page. Dashboard skip navigation, connection banners, overlays and toasts do not mount in companion/launcher
routes. Transparent pixels stay transparent without an enclosing panel.

## Rejected alternatives

- Continue deforming the sparse face template: failed the requested visual quality.
- Embed an arbitrary fan-made Haruhi model: no verified redistribution provenance.
- Keep only native header dragging: leaves most of the visible avatar immovable.
- Hide without a tray/launcher: makes the process difficult to recover.

## Consequences

The adapted character has a fixed design; custom model import is not implemented.
Speech energy drives mouth opening, not phoneme alignment. Hardware/software
rendering limits remain bounded to protect local inference. Software rendering
uses 2 fps during connecting/thinking; the dashboard holds its last frame while
the companion is open, avoiding two simultaneous software rasterizers. CPU rendering is a
compatibility path with lower visual fidelity/performance than WebGL.

## Invalidation conditions

- If the companion window gets a native frame or title bar, dragging the whole head through scoped IPC can be revisited.
- If desktops commonly refuse to register the recovery shortcut, as the record expects some compositor policies to do, the tray and launcher carry recovery alone and the shortcut can be revisited.

## Validation

Electron fixtures cover scoped drag/recovery, tray sizing, display fitting,
launcher lifecycle and owned-service retention. Browser scenarios cover actual
asset loading, transparent GPU-less rendering and compact setup/ready/error
layouts at different viewport sizes. Native desktop smoke checks movement of the
actual head and launcher windows, recovery, shortcut registration, Close/minimize,
and microphone/session teardown using disposable profiles. Platform validation
records what executed and the remaining physical-key/Wayland/acoustic gates.
