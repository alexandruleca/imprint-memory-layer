# Imprint website

The marketing + docs site deployed to https://imprintmcp.alexandruleca.com via GitHub Pages.

## Stack

- **Astro 7** — static site generator, zero JS by default. `compressHTML: true` keeps HTML whitespace semantics in the `.astro` templates (Astro 7's default JSX rules would drop spaces between inline elements).
- **Starlight 0.42** — docs UI with Pagefind search; reads from `src/content/docs/` which is a symlink to the repo-root `../docs/` folder so Markdown stays the single source of truth. Markdown runs through Sätteri; a small mdast plugin in `astro.config.mjs` turns ```` ```mermaid ```` fences into `<pre class="mermaid">` before Expressive Code highlights code, and a head script renders them.
- **Tailwind CSS v4** — landing/benchmarks/FAQ styling. Design tokens and shared classes (`.wrap`, `.section`, `.card`, `.btn`, `.term`, …) live in `src/styles/global.css`; icons are inline SVG via `src/components/Icon.astro`.
- **Dashboard screenshots** in `src/assets/` are optimized by `astro:assets` at build time. They show the real dashboard with a sample workspace.
- `scripts/build-benchmarks.mjs` parses `../BENCHMARK.md` into `src/data/benchmarks.json` at build time so hero numbers, the benchmarks page, and the benefit cards stay in sync with reality.

## Pages

| Path | Source |
|------|--------|
| `/` | `src/pages/index.astro` — hand-authored landing |
| `/benchmarks` | `src/pages/benchmarks.astro` — full benchmark tables from `BENCHMARK.md` |
| `/faq` | `src/pages/faq.astro` — SEO-targeted "MCP memory layer" Q&A |
| `/chrome-extension/` | `src/pages/chrome-extension.astro` — Chrome preview download, manual installation and coming-soon integrations |
| `/installation`, `/mcp`, `/architecture`, … | `../docs/*.md` via Starlight content collection |
| `/404` | `src/pages/404.astro` |

## Develop

```bash
cd site
npm install
npm run dev          # http://localhost:4321
```

The dev script regenerates `src/data/benchmarks.json` on each start.

The Chrome page reuses the landing layout, design tokens, icons and navigation.
It selects the latest stable release containing `imprint-chrome-extension.zip`
from the normalized release manifest; desktop-only releases do not replace a
working extension download. Before a ZIP is published it links to releases and
explains the preview backend prerequisite. A successful Release triggers the
normal Pages rebuild so the new ZIP appears automatically. Preview source is
kept in `extensions/chrome`; runtime ZIP assembly and validation live in
`scripts/package_chrome_extension.py` and `extensions/chrome/tests`.

## Analytics

Builds with `PUBLIC_GA4_ID` set (the Pages workflow sets it) load Google Analytics on every page: `src/components/Analytics.astro` for the landing pages and the Starlight `head` in `astro.config.mjs` for docs. Both also load `public/scripts/events.js`, one capturing listener that turns visitor actions into GA4 events:

| Event | When | Parameters |
|-------|------|------------|
| `download` | A link to a release asset (`/releases/download/<tag>/<file>`) | `file_name`, `file_extension`, `version`, `link_text`, `link_url` |
| `copy_command` | A copy button marked `data-track="copy_command"` | `command` (`unix`, `win`, `docker`, `relay`, `chrome-extensions`) |
| `select_tab` | A `role="tab"` control | `tab_group` (the tablist's `aria-label`), `tab` |
| `expand` | Opening a `<details>` (FAQ answers, release notes, mobile menu) | `label` |
| `select_option` | Changing a `<select>` (the docs theme) | `option` |
| `link_click` | Any other link | `link_text`, `link_url`, `outbound` |
| `button_click` | Any other button | `label` |

Every event also carries `placement`: the id of the nearest `section[id]`, or the nearest `header`, `footer`, `nav`, `aside` or `main`. To give an element its own event, add `data-track="<event>"` and `data-track-<param>` attributes. `link_text`, `link_url`, `file_name`, `file_extension` and `outbound` fill GA's built-in dimensions; register `placement`, `command`, `tab_group`, `tab`, `label`, `option` and `version` as event-scoped custom dimensions to report on them.

The landing page's Sync section shows the public relay's live status (`src/components/RelayStatus.astro`): the browser reads `https://sync.alexandruleca.com/health` on load and every 30 seconds while the tab is visible, and links to the relay in a new tab.

## Build

```bash
npm run build        # writes static site to ./dist
npm run preview      # serve ./dist on :4321
```

## Deploy

Pushed automatically by `.github/workflows/pages.yml` on merges to `main` that touch `docs/`, `site/`, `README.md`, or `BENCHMARK.md`. One-time repo setup: **Settings → Pages → Source: GitHub Actions**, then point the custom domain's DNS at `<user>.github.io` (CNAME record) — the domain string lives in `public/CNAME`.

## Adding docs pages

Create `../docs/<slug>.md` at the repo root with `title:` frontmatter, then add its slug to the appropriate sidebar group in `astro.config.mjs`. The file stays a regular Markdown doc that renders cleanly on GitHub too.
