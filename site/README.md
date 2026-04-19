# Imprint website

The marketing + docs site deployed to https://imprintmcp.alexandruleca.com via GitHub Pages.

## Stack

- **Astro 6** — static site generator, zero JS by default.
- **Starlight 0.38** — docs UI with Pagefind search; reads from `src/content/docs/` which is a symlink to the repo-root `../docs/` folder so Markdown stays the single source of truth.
- **Tailwind CSS v4** — landing/benchmarks/FAQ styling.
- `scripts/build-benchmarks.mjs` parses `../BENCHMARK.md` into `src/data/benchmarks.json` at build time so hero numbers, the benchmarks page, and the benefit cards stay in sync with reality.

## Pages

| Path | Source |
|------|--------|
| `/` | `src/pages/index.astro` — hand-authored landing |
| `/benchmarks` | `src/pages/benchmarks.astro` — full benchmark tables from `BENCHMARK.md` |
| `/faq` | `src/pages/faq.astro` — SEO-targeted "MCP memory layer" Q&A |
| `/installation`, `/mcp`, `/architecture`, … | `../docs/*.md` via Starlight content collection |
| `/404` | `src/pages/404.astro` |

## Develop

```bash
cd site
npm install
npm run dev          # http://localhost:4321
```

The dev script regenerates `src/data/benchmarks.json` on each start.

## Build

```bash
npm run build        # writes static site to ./dist
npm run preview      # serve ./dist on :4321
```

## Deploy

Pushed automatically by `.github/workflows/pages.yml` on merges to `main` that touch `docs/`, `site/`, `README.md`, or `BENCHMARK.md`. One-time repo setup: **Settings → Pages → Source: GitHub Actions**, then point the custom domain's DNS at `<user>.github.io` (CNAME record) — the domain string lives in `public/CNAME`.

## Adding docs pages

Create `../docs/<slug>.md` at the repo root with `title:` frontmatter, then add its slug to the appropriate sidebar group in `astro.config.mjs`. The file stays a regular Markdown doc that renders cleanly on GitHub too.
