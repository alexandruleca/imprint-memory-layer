---
title: ADR 0154 — The dashboard and desktop app follow a light, dark or system theme
description: The dashboard offers Light, Dark and System themes from colour tokens with dark values, applied before first paint and in every open window, and the desktop app follows the choice through a validated IPC message that sets Electron's native theme.
---

- Status: Accepted
- Date: 2026-11-08
- Amends: [ADR 0008](0008-dashboard-retrieval-and-accessibility.md) — the dashboard's theme, light only until now, becomes light, dark or the system's
- Related: [ADR 0039](0039-shadcn-overlays-and-desktop-drag-handles.md) (the component kit whose tokens carry the theme), [ADR 0135](0135-the-floating-launcher-is-a-handle-with-its-actions-around-it.md) (the orb and its ring), [ADR 0070](0070-companion-moves-with-the-launcher-and-keeps-options-in-settings.md) (Echo's floating window), [dashboard](../dashboard.md)

## Context

The dashboard documented one theme, "a light, black-and-white theme and the bundled Inter font", cited to ADR 0008. ADR 0008's own text does not discuss colour; the light-only theme was the dashboard's practice under it. The user approved a dark theme for the whole dashboard, with a Light / Dark / System choice.

A first pass added `.dark` values for every shadcn token in `imprint/ui/src/app/globals.css`, Tailwind's `dark:` variant keyed on a `dark` class on `<html>`, `src/lib/theme.ts`, an inline script in `src/app/layout.tsx` that applies the stored choice before first paint, and the top bar's theme menu. The pages were not swept. A search of `imprint/ui/src` (outside the Graph and Search pages, which another change owns) found fixed colours that read poorly or vanish on a dark background: status text such as `text-emerald-700`, `text-amber-700` and `bg-amber-50` pills on Sync, paired devices, Agents, Commands, Queue, Core memories, Organize, Settings, WSL profiles and Add to memory; the source reader's amber passage marks (`bg-amber-100`, `bg-amber-200`); the connection banner's `bg-background` with an amber border; modal backdrops at `bg-black/10`; and Echo's Voice page variables (`--echo-accent: #0a78a6`, `--echo-selected: #eaf6fb`, `--echo-faint` at `oklch(0.62 0 0)`, which also measured about 3.3:1 on the light page, below WCAG AA).

The desktop app (`ui-electron/main.js`) gave every opaque window `backgroundColor: '#09090b'` and its splash a fixed dark stylesheet, so a light dashboard flashed dark while it loaded. The orb's ring (`src/styles/launcher.css`) switched palettes on `prefers-color-scheme`, which follows the operating system, not the dashboard's choice.

## Decision

The dashboard has three themes, **Light**, **Dark** and **System**, chosen per browser or desktop profile (`imprint.theme` in `localStorage`), and every surface takes its colours from tokens that have a light and a dark value.

- **Tokens.** Besides shadcn's, `globals.css` defines `success` and `warning` for status text and icons (and their 10% tints), and `mark`, `mark-strong` and `mark-edge` for marked passages. Each keeps text at WCAG AA (4.5:1) on cards, the page and its own tint in both themes; red text on a red tint does not reach it in light, so a danger pill is a plain card with a red border. Fixed palette classes in the swept pages become these tokens. Project colours (`cli_viz.PROJECT_COLORS`) stay as they are: they are decorative dots, mid-saturated enough for both backgrounds.
- **Every window follows.** The inline script applies the choice before first paint and again on a `storage` event (another window changed it) or a change of the system's setting, so pages without the theme menu, such as the orb, its ring and Echo, follow too. The ring's palettes key on the `dark` class instead of `prefers-color-scheme`. Echo's Voice page gets dark values; its floating window keeps its dark glass palette in both themes because it sits over other applications' windows.
- **The desktop app follows the choice.** The preload exposes `electronAPI.setTheme(choice)`, sent as `theme-set`. The main process accepts it only from the dashboard window's main frame on the dashboard's origin (`trustedDashboardSender`) and only as `'light'`, `'dark'` or `'system'`; it sets `nativeTheme.themeSource`, so the operating system's menus and dialogs and `prefers-color-scheme` in every window follow, gives the dashboard and splash windows the dashboard's background (`#fafafa` or `#0a0a0a`), and keeps the choice in `theme.json` in the app profile so the next start opens in it before any page loads. `contextIsolation` and the sandbox are unchanged. The splash follows `prefers-color-scheme`.

## Rejected alternatives

- **`dark:` variants beside every fixed colour instead of tokens.** Several files already did this (`text-emerald-700 dark:text-emerald-400`). It doubles every status colour at each use, and the pairs drifted: some files had them and others did not. One token per role keeps the contrast decision in one place.
- **Keep the ring on `prefers-color-scheme` and rely on `nativeTheme` alone.** It works only inside the desktop app; in a browser, the media query would follow the system while the dashboard follows the stored choice. Keying on the class the inline script sets is right in both.
- **Let any app window (Echo, the orb) set the theme.** Only the dashboard has the theme menu; accepting the message from other windows widens what a compromised page in them could change, for no use.
- **A dark-only floating Echo replaced by a themed one.** Echo's floating window is transparent over whatever the user has open; its dark glass palette with a text shadow was designed for legibility over arbitrary backgrounds, which a light palette would lose.

## Consequences

- The dashboard reads in both themes; new code must use tokens (documented in [dashboard](../dashboard.md#design-and-tests)).
- In the desktop app, choosing a theme also changes the app's native surfaces; with **System**, Electron follows the operating system as before.
- The light theme changes slightly: warning text is a darker amber (`amber-800`'s value instead of `amber-700`), focused source passages a light amber instead of near-white, and Echo's faint text darker so it reaches AA.
- The Graph and Search pages were outside this sweep; their colours are their own change's.

## Invalidation conditions

- If Electron stops honouring `nativeTheme.themeSource` for `prefers-color-scheme` in renderer windows, then the splash needs the choice passed to it some other way.
- If the dashboard gains a theme beyond light and dark (high contrast, say), then the validated set of choices and the window backgrounds must grow with it rather than mapping it onto dark.
- If Echo's floating window becomes opaque, then it should follow the theme like the other windows.

## Validation

- `ui-electron/main.test.js` (Node test runner, Electron replaced by doubles): the saved choice applies before any window opens and sets the window backgrounds; a `theme-set` from the dashboard changes `nativeTheme.themeSource`, the background and `theme.json`; other values and other origins are ignored; **System** follows a change of the system's setting. Run on Linux; native Electron on Windows, macOS and Linux was not run.
- `imprint/ui/tests/theme.spec.ts` (Chromium): a choice applies at once and before paint on the next load; **System** follows the emulated system setting; a second window without the menu (the orb's ring) follows a choice made in the dashboard, its palette turns dark, and the desktop bridge is told the choice.
- With the dark theme stored, the Chromium specs of Sync, Agents, Core memories, Organize, Commands, Settings, WSL profiles and Recordings passed (77 tests), and their end-of-test screenshots were reviewed by eye. With the default theme, those specs and the theme, launcher, source reader, Echo voice, dashboard recovery, queue history, curation and desktop specs passed (143 tests). Light and dark screenshots of every page and floating window against a synthetic 120,000-memory workspace, plus mocked paired devices, the source reader's marks and the connection banner, were reviewed by eye. Contrast of the new tokens was computed from their OKLCH values against the page, card, muted and popover colours; no automated contrast audit runs.
