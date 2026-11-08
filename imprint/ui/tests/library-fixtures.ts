import type { Page } from '@playwright/test';

// Mocked Projects and Sources data, shaped like imprint/api.py's /api/overview,
// /api/project/{name}, /api/sources, /api/workspace/roots and /api/organize.
// `library(scale)` builds a workspace at the maintainer's scale with scale 1:
// 51 projects, ~79,000 memories and ~18,800 sources, one project holding
// ~7,500 of them (17,782 and 7,561 in the real workspace). Names are made up;
// the sizes follow the real distribution.

const NAMES = [
  'storefront', 'chat-archive', 'data-pipeline', 'forms-engine', 'api', 'imprint', 'memory-layer', 'account',
  'floorplan-api', 'monorepo', 'naming-lab', 'payments-gateway', 'floorplan', 'engine-3d', 'content-discovery',
  'devtool', 'client-residential', 'Code', 'client-web', 'airport-challenge', 'service-template', 'furniture-3d', 'web',
  'floorplan-v2', 'pa-api', 'editor-2d', 'office-client', 'facility-admin', 'payments-dev', 'wickling', 'facility-api',
  'editor-2d-api', 'banking', 'flights', 'swag-shop', 'reporting-service', 'blender-agent', 'analytics-gateway', 'backend',
  'product-analytics', 'personal', 'general', 'frontend', 'production', 'discovery-api', 'shopware', 'nexus-client',
  'notes', 'model-analyzer', 'release-dashboard', 'scratch',
];
const MEMORIES = [
  19533, 6057, 5952, 5543, 4999, 4910, 3648, 3026, 2904, 2633, 2351, 1718, 1593, 1038, 971, 894, 878, 872, 728, 727, 665,
  532, 354, 330, 322, 308, 304, 261, 206, 195, 177, 162, 153, 108, 102, 92, 79, 64, 56, 56, 49, 33, 25, 22, 21, 15, 15, 11, 6, 3, 2,
];
const COLORS = ['#4ecdc4', '#ff6b6b', '#ffd166', '#06d6a0', '#118ab2', '#8338ec', '#ef476f', '#3a86ff', '#fb5607', '#8ac926'];
const DIRS = ['src', 'src/components', 'src/lib', 'src/app/settings', 'docs', 'tests', 'app/Resources/views/admin',
              'src/Plugin/VouchersBundle/Resources/xlsx', 'scripts', 'config', 'packages/core/src', 'internal/queue'];
const FILES = ['index.ts', 'page.tsx', 'README.md', 'handler.py', 'service.go', 'schema.sql', 'layout.css', 'routes.php',
               'worker.py', 'client.ts', 'utils.ts', 'notes.md', 'config.yaml', 'Dockerfile'];
const DOCS = ['source.xlsx', 'target.xlsx', 'handbook.pdf', 'contract.docx', 'diagram.png', 'roadmap.pdf'];
const QUESTIONS = [
  'Why do the panes scroll apart on the settings page?', 'How should the queue cancel a running ingest?',
  'Can we page the sources list on the server?', 'What breaks when the snapshot database is locked?',
  'Make the tagger skip binary files', 'Why is the overview count off by one after a merge?',
  'Draft the release notes for the next version', 'Fix the flaky playwright test on CI',
];

function rng(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export type Row = { source: string; chunks: number; title: string; project: string | null; linked: boolean; kind: string; updated: number };
export type Project = { id: string; name: string; count: number; types: Record<string, number>; color: string; topDomains: string[]; topLangs: string[] };

export interface Library {
  projects: Project[];
  sources: Row[];
  total: number;
}

/** A workspace at `scale` (1: the maintainer's) of the real size distribution. */
export function library(scale = 1, now = Date.now() / 1000): Library {
  const random = rng(7);
  const pick = <T,>(list: T[]) => list[Math.floor(random() * list.length)];
  const projects: Project[] = NAMES.map((name, i) => ({
    id: `proj_${name}`, name, count: Math.max(1, Math.round(MEMORIES[i] * scale)), types: { finding: 3, decision: 1 },
    color: COLORS[i % COLORS.length], topDomains: ['ui', 'api'], topLangs: ['typescript', 'python'],
  }));
  const sources: Row[] = [];
  projects.forEach((project, i) => {
    const n = i === 0 ? Math.round(7561 * scale) : Math.max(1, Math.round(project.count * 0.2));
    for (let s = 0; s < n; s++) {
      const roll = random();
      const updated = Math.round(now - random() * 120 * 86400);
      if (roll < 0.02) {
        sources.push({ source: `conversation/${Math.floor(random() * 0xffffffff).toString(16).padStart(8, '0')}`, chunks: 2 + Math.floor(random() * 60),
                       title: pick(QUESTIONS), project: project.name, linked: false, kind: 'conversation', updated });
      } else if (roll < 0.1) {
        const big = random() < 0.05;
        sources.push({ source: `${project.name}/${pick(DIRS)}/${s}-${pick(DOCS)}`, chunks: big ? 800 + Math.floor(random() * 1600) : 3 + Math.floor(random() * 40),
                       title: '', project: project.name, linked: false, kind: 'extracted', updated });
      } else if (roll < 0.112) {
        sources.push({ source: `notes/decision-${i}-${s}`, chunks: 1, title: '', project: null, linked: false, kind: '', updated: 0 });
      } else {
        sources.push({ source: `${project.name}/${pick(DIRS)}/${s}-${pick(FILES)}`, chunks: 1 + Math.floor(random() * random() * 120),
                       title: '', project: project.name, linked: false, kind: 'text', updated });
      }
    }
  });
  sources.sort((a, b) => b.chunks - a.chunks);
  return { projects, sources, total: projects.reduce((n, p) => n + p.count, 0) };
}

export const detail = (name: string, count: number) => ({
  project: name, count, color: '#4ecdc4',
  types: [{ name: 'finding', count: Math.round(count * 0.6) }, { name: 'decision', count: Math.round(count * 0.25) }, { name: 'pattern', count: Math.round(count * 0.15) }],
  domains: [{ name: 'ui', count: Math.round(count * 0.4) }, { name: 'api', count: Math.round(count * 0.3) }, { name: 'testing', count: Math.round(count * 0.2) }],
  langs: [{ name: 'typescript', count: Math.round(count * 0.5) }, { name: 'python', count: Math.round(count * 0.3) }, { name: 'markdown', count: Math.round(count * 0.2) }],
  topics: ['layout', 'paging', 'queue', 'redaction', 'search', 'sync', 'tagging', 'workspaces'].map((topic, i) => ({ name: topic, count: Math.round(count / (i + 2)) })),
});

/** Route the dashboard's calls for Projects and Sources to `lib`. Returns
 * what the pages sent, for the tests to check. */
export async function mockLibrary(page: Page, lib: Library, options: {
  exclusions?: { kind: 'path' | 'source'; path: string; recursive: boolean }[];
  roots?: string[];
  profiles?: { project: string; text: string; updated: number }[];
} = {}) {
  const sent = { sources: [] as URLSearchParams[], moves: [] as Record<string, unknown>[], purges: [] as Record<string, unknown>[],
                 roots: [] as Record<string, unknown>[], reads: [] as URLSearchParams[] };
  const scope = { workspace: 'alpha', roots: options.roots ?? ['/home/me/code'], exclusions: options.exclusions ?? [] };
  await page.route('**/api/**', route => route.fulfill({ json: {} }));
  await page.route('**/api/ping', route => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/version', route => route.fulfill({ json: { version: 'test' } }));
  await page.route('**/api/stream', route => route.fulfill({ status: 204 }));
  await page.route('**/api/queue**', route => route.fulfill({ json: { active: null, queued: [], recent: [] } }));
  await page.route('**/api/workspaces', route => route.fulfill({ json: { active: 'alpha', workspaces: ['alpha', 'beta'] } }));
  await page.route('**/api/{overview,projects}', route => route.fulfill({ json: { projects: lib.projects, total: lib.total, version: 1, facets: {} } }));
  await page.route('**/api/project/*', route => {
    const name = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '');
    return route.fulfill({ json: detail(name, lib.projects.find(p => p.name === name)?.count ?? 0) });
  });
  await page.route('**/api/sources?**', route => {
    const params = new URL(route.request().url()).searchParams;
    sent.sources.push(params);
    const project = params.get('project');
    const rows = project ? lib.sources.filter(row => row.project === project) : lib.sources;
    return route.fulfill({ json: { sources: rows, total: rows.length } });
  });
  await page.route('**/api/sources/summary/**', route => {
    const key = decodeURIComponent(new URL(route.request().url()).pathname.split('/api/sources/summary/')[1]);
    const row = lib.sources.find(r => r.source === key);
    return route.fulfill({ json: { source: key, project: row?.project ?? '', type: 'finding', source_type: row?.kind === 'conversation' ? 'conversation' : 'file',
      source_mtime: row?.updated ?? 0, chunk_count: row?.chunks ?? 0, first_chunk_preview: '',
      tags: { lang: 'typescript', layer: 'ui', kind: 'source', domain: ['ui'], topics: ['layout', 'paging'] },
      recorded_device: { id: 'dev1', name: 'Workstation', os: 'darwin' } } });
  });
  await page.route('**/api/source/**', route => {
    const key = decodeURIComponent(new URL(route.request().url()).pathname.split('/api/source/')[1]);
    const row = lib.sources.find(r => r.source === key);
    const n = Math.min(500, row?.chunks ?? 0);
    return route.fulfill({ json: { source: key, total: n, chunks: Array.from({ length: n }, (_, i) => ({
      id: `c${i}`, label: `Chunk ${i} of ${key.split('/').pop()}`, chunk_index: i, content: `Content of chunk ${i}.`, project: row?.project ?? '',
      type: 'finding', source: key, tags: { lang: '', layer: '', kind: '', domain: [], topics: [] } })) } });
  });
  await page.route('**/api/sources/read?**', route => {
    const params = new URL(route.request().url()).searchParams;
    sent.reads.push(params);
    return route.fulfill({ json: { source: params.get('source'), project: params.get('project') ?? '', kind: 'text', available: true,
      title: '', total_chars: 40, chunk_count: 1, updated_at: 1, details: {},
      blocks: [{ event: 0, start: 0, end: 40, chunks: [0], match: false, kind: 'text', text: `The whole text of ${params.get('source')}` }] } });
  });
  await page.route('**/api/organize', route => route.fulfill({ json: { workspace: 'alpha', proposals: [], profiles: options.profiles ?? [],
    last_run: null, background: { enabled: false, auto_move: true, last_pass: null } } }));
  await page.route('**/api/workspace/roots?**', route => route.fulfill({ json: scope }));
  await page.route('**/api/workspace/roots', async route => {
    const body = route.request().postDataJSON();
    sent.roots.push(body);
    if (body.action === 'restore') scope.exclusions = scope.exclusions.filter(item => !(item.path === body.path && item.kind === body.kind));
    await route.fulfill({ json: scope });
  });
  await page.route('**/api/sources/purge', async route => {
    const body = route.request().postDataJSON();
    sent.purges.push(body);
    if (body.apply) scope.exclusions.push({ kind: body.kind, path: body.target, recursive: body.recursive });
    await route.fulfill({ json: { ...body, workspace: body.workspace || 'alpha', chunks: 4, sources: 1, facts: 0, applied: !!body.apply,
      sample: [{ project: 'storefront', source: body.target }] } });
  });
  await page.route('**/api/projects/move', async route => {
    const body = route.request().postDataJSON();
    sent.moves.push(body);
    await route.fulfill({ json: { workspace: 'alpha', project: body.project, target: body.target, whole: !body.sources,
      chunks: 6, sources: body.sources?.length ?? 0, target_exists: true, sample: body.sources ?? [], applied: !!body.apply } });
  });
  return sent;
}
