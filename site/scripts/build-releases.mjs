#!/usr/bin/env node
// Fetch GitHub Releases for alexandruleca/imprint-memory-layer at build time
// and write a normalized manifest to site/src/data/releases.json. Astro pages
// import the JSON and render statically — no runtime API calls.
//
// Cache policy:
//   - Response cached to scripts/.release-cache.json for 1 hour so `npm run dev`
//     does not hit the GitHub anonymous rate limit (60/hr).
//   - Set IMPRINT_RELEASES_REFRESH=1 to bypass cache.
//   - Set GITHUB_TOKEN to raise the rate limit (optional).
//   - Offline / API failure falls back to the cached copy (any age) and, beyond
//     that, to a minimal stub so the build still succeeds.

import { readFileSync, writeFileSync, mkdirSync, existsSync, statSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { marked } from 'marked';

marked.setOptions({ gfm: true, breaks: false });

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO = process.env.IMPRINT_REPO || 'alexandruleca/imprint-memory-layer';
const OUT = resolve(__dirname, '../src/data/releases.json');
const CACHE = resolve(__dirname, '.release-cache.json');
const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour
const MAX_RELEASES = 30;
const TIMEOUT_MS = 8000;

const PLATFORM_MATCHERS = [
  { os: 'macos', arch: 'arm64',   kind: 'installer', rx: /darwin.*arm64.*\.(pkg|dmg)$/i },
  { os: 'macos', arch: 'amd64',   kind: 'installer', rx: /darwin.*(amd64|x86_64).*\.(pkg|dmg)$/i },
  { os: 'macos', arch: 'arm64',   kind: 'archive',   rx: /darwin.*arm64.*\.tar\.gz$/i },
  { os: 'macos', arch: 'amd64',   kind: 'archive',   rx: /darwin.*(amd64|x86_64).*\.tar\.gz$/i },
  { os: 'windows', arch: 'amd64', kind: 'installer', rx: /windows.*(amd64|x86_64).*\.(exe|msi)$/i },
  { os: 'windows', arch: 'amd64', kind: 'archive',   rx: /windows.*(amd64|x86_64).*\.zip$/i },
  { os: 'linux', arch: 'arm64',   kind: 'archive',   rx: /linux.*arm64.*\.tar\.gz$/i },
  { os: 'linux', arch: 'amd64',   kind: 'archive',   rx: /linux.*(amd64|x86_64).*\.tar\.gz$/i },
];

const OS_LABEL = { macos: 'macOS', windows: 'Windows', linux: 'Linux' };
const ARCH_LABEL = { amd64: 'Intel / x64', arm64: 'Apple Silicon / ARM64' };
const KIND_LABEL = { installer: 'Installer', archive: 'Archive' };

function classifyAsset(name) {
  for (const m of PLATFORM_MATCHERS) {
    if (m.rx.test(name)) return { os: m.os, arch: m.arch, kind: m.kind };
  }
  return null;
}

function fmtBytes(n) {
  if (typeof n !== 'number' || n <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

async function fetchReleases() {
  const url = `https://api.github.com/repos/${REPO}/releases?per_page=${MAX_RELEASES}`;
  const headers = {
    'User-Agent': 'imprint-site-build',
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
  };
  if (process.env.GITHUB_TOKEN) headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;

  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, { headers, signal: ctrl.signal });
    if (!res.ok) throw new Error(`GitHub API ${res.status}: ${await res.text().catch(() => '')}`);
    return await res.json();
  } finally {
    clearTimeout(t);
  }
}

function readCache() {
  if (!existsSync(CACHE)) return null;
  try {
    return JSON.parse(readFileSync(CACHE, 'utf8'));
  } catch {
    return null;
  }
}

function cacheFresh() {
  if (!existsSync(CACHE)) return false;
  if (process.env.IMPRINT_RELEASES_REFRESH === '1') return false;
  const age = Date.now() - statSync(CACHE).mtimeMs;
  return age < CACHE_TTL_MS;
}

function stubReleases() {
  return [{
    tag_name: 'unreleased',
    name: 'No releases cached',
    published_at: new Date().toISOString(),
    prerelease: false,
    draft: false,
    html_url: `https://github.com/${REPO}/releases`,
    body: '',
    assets: [],
  }];
}

function normalize(releases) {
  const sorted = [...releases]
    .filter((r) => !r.draft)
    .sort((a, b) => new Date(b.published_at) - new Date(a.published_at));

  const normalized = sorted.map((r) => {
    const assets = (r.assets || [])
      .filter((a) => !/\.(sha256|sha512|sig|asc)$/i.test(a.name) && !/^SHA(256|512)SUMS$/i.test(a.name))
      .map((a) => {
        const cls = classifyAsset(a.name);
        return {
          name: a.name,
          url: a.browser_download_url,
          size: a.size,
          sizeLabel: fmtBytes(a.size),
          downloads: a.download_count ?? 0,
          os: cls?.os ?? 'other',
          arch: cls?.arch ?? null,
          kind: cls?.kind ?? 'other',
          osLabel: cls ? OS_LABEL[cls.os] : 'Other',
          archLabel: cls?.arch ? ARCH_LABEL[cls.arch] : null,
          kindLabel: cls ? KIND_LABEL[cls.kind] : 'File',
        };
      })
      .sort((a, b) => {
        const order = { installer: 0, archive: 1, other: 2 };
        const osOrder = { macos: 0, windows: 1, linux: 2, other: 3 };
        if (osOrder[a.os] !== osOrder[b.os]) return osOrder[a.os] - osOrder[b.os];
        if (order[a.kind] !== order[b.kind]) return order[a.kind] - order[b.kind];
        return a.name.localeCompare(b.name);
      });

    const body = r.body || '';
    return {
      tag: r.tag_name,
      name: r.name || r.tag_name,
      publishedAt: r.published_at,
      prerelease: !!r.prerelease,
      htmlUrl: r.html_url,
      body,
      bodyHtml: body ? marked.parse(body) : '',
      assets,
    };
  });

  const latestStable = normalized.find((r) => !r.prerelease) || normalized[0] || null;
  const latestPrerelease = normalized.find((r) => r.prerelease) || null;

  return {
    generatedAt: new Date().toISOString(),
    repo: REPO,
    latestStable,
    latestPrerelease,
    releases: normalized,
  };
}

async function main() {
  let raw = null;
  let source = 'api';

  if (cacheFresh()) {
    raw = readCache();
    source = 'cache-fresh';
  }

  if (!raw) {
    try {
      raw = await fetchReleases();
      writeFileSync(CACHE, JSON.stringify(raw));
    } catch (err) {
      const stale = readCache();
      if (stale) {
        raw = stale;
        source = 'cache-stale';
        console.warn(`[build-releases] API fetch failed (${err.message}); using stale cache`);
      } else {
        raw = stubReleases();
        source = 'stub';
        console.warn(`[build-releases] API fetch failed (${err.message}); using stub`);
      }
    }
  }

  const payload = normalize(raw);
  mkdirSync(dirname(OUT), { recursive: true });
  writeFileSync(OUT, JSON.stringify(payload, null, 2));

  const latest = payload.latestStable?.tag || 'none';
  console.log(`[build-releases] source=${source} latest=${latest} total=${payload.releases.length} → ${OUT}`);
}

main().catch((err) => {
  console.error('[build-releases] fatal:', err);
  process.exit(1);
});
