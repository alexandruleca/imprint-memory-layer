---
title: ADR 0039 — shadcn overlays and desktop drag handles
description: Dashboard dialogs, menus, popovers and selects use the shadcn (Base UI) components, and the frameless window's drag handles stand down while one is open.
---

- Status: Accepted
- Date: 2026-10-24
- Amends: [ADR 0008](0008-dashboard-retrieval-and-accessibility.md) — replaces its native dialog primitive
- Related: [ADR 0013](0013-remediate-security-scan-dependencies.md), [ADR 0025](0025-desktop-owned-background-service.md)
- Restructured: 2026-10-25

## Context

The dashboard mixed three overlay systems: a native `<dialog>` wrapper (ADR 0008) for memory, folder-picker, update and destructive confirmations, Base UI popovers styled by hand (workspace menu, graph settings), and native `<select>` elements. Buttons, inputs, progress bars, spinners and empty states were hand-rolled per page. The owner asked for the shadcn kit across the dashboard, with dropdowns and other controls built from it. The repository already vendors shadcn's base-nova components on `@base-ui/react` (ADR 0013).

The Electron window is frameless. Its drag handles are the top bar's empty space and the sidebar header, marked with `app-region: drag`. Chromium hit-tests drag regions before the page and ignores stacking. Commit 5075bd2 marked the top layer, backdrops and Base UI portals `no-drag`, assuming a later no-drag box carves a handle out. v0.19.0 shipped that commit, and the Update dialog still could not be clicked: dragging over it moved the window. A no-drag box drawn over a handle does not reliably carve it out.

## Decision

Build every dashboard overlay and control from the vendored shadcn components: `Dialog` and `AlertDialog` replace the native modal and the confirmation wrapper, which are deleted; `DropdownMenu`, `Popover` and `Select` replace hand-styled popups and native selects; `Button`, `Input`, `Checkbox`, `RadioGroup`, `Switch`, `ToggleGroup`, `Progress`, `Spinner`, `Empty`, `Table`, `Tooltip` and a `sonner` toaster replace their hand-rolled counterparts. Hover hints use a small `Hint` wrapper around `Tooltip`. Base UI provides the behavior ADR 0008 required of the native dialog: a labelled modal, focus trap, Escape, an inert background, focus restoration and nested dialogs where Escape closes only the innermost.

Drag handles carry the `app-drag` class. One rule in `globals.css` makes them `no-drag` while a dialog, alert dialog, sheet, popover, menu or select is open. It keys on the shadcn content slots with Base UI's `data-open`, so a closed Select whose list Base UI mounts early does not count. Tooltips take no clicks and leave the handles alone. The per-overlay `no-drag` rules are removed.

## Rejected alternatives

- **Keep the native modal and restyle it.** That keeps two overlay systems, and native top-layer dialogs were exactly what the v0.19.0 window could not click.
- **Keep the carve-out rules and add more of them.** v0.19.0 showed that carve-outs do not reliably subtract from a drag handle underneath.
- **Track open overlays in JavaScript and toggle a class.** That is a second channel for state the DOM already exposes. Every overlay would have to register itself.
- **Leave controls hand-rolled where they work.** The owner asked for one kit, and the hand-rolled controls diverged in focus rings, sizing and disabled states.

## Consequences

Dialogs, menus, popovers and selects render in Base UI portals with one look and one keyboard model. Some roles changed with the components. Confirmations are `alertdialog`, the workspace switcher is a `menu` with `menuitemradio` workspaces, and selects are comboboxes with listboxes. Browser tests changed only those selectors. The window cannot be dragged while an overlay is open; the user closes the overlay first. The drag rule depends on the shadcn `data-slot` names, so a new overlay component must be added to that list. The overlay set is all the components that exist today.

## Invalidation conditions

- If Electron's drag regions start honoring no-drag boxes drawn over them, the open-overlay rule can give way to per-element carve-outs.
- If the window gets a native title bar, the drag-handle rule is unnecessary.
- If shadcn or Base UI rename the `data-slot` or `data-open` attributes the rule keys on, the rule must change with them.

## Validation

`tests/desktop.spec.ts` mocks the Electron bridge and checks the computed `app-region` of both handles. It opens each overlay type: the Update dialog, the workspace menu, the manager dialog with its nested folder picker and an alert dialog, the graph settings popover and a select. Each check covers open and closed, a hint tooltip, and a focused but closed select. All 43 Playwright scenarios pass, along with TypeScript and lint. Real Electron hit-testing on Windows, macOS and Linux still needs a native check: Chromium's drag regions cannot be observed from a browser test.
