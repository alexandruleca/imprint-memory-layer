// @ts-check
import { defineConfig } from 'astro/config';
import { loadEnv } from 'vite';
import starlight from '@astrojs/starlight';
import mdx from '@astrojs/mdx';
import tailwindcss from '@tailwindcss/vite';
import { satteri } from '@astrojs/markdown-satteri';
import { docLinks } from './scripts/doc-links.mjs';

const env = loadEnv(process.env.NODE_ENV ?? '', process.cwd(), 'PUBLIC_');
const ga4Id = env.PUBLIC_GA4_ID || process.env.PUBLIC_GA4_ID;

/** @type {import('@astrojs/starlight/types').HeadConfig} */
const analyticsHead = ga4Id
  ? [
      {
        tag: 'script',
        attrs: { async: true, src: `https://www.googletagmanager.com/gtag/js?id=${ga4Id}` },
      },
      {
        tag: 'script',
        content:
          `window.dataLayer=window.dataLayer||[];` +
          `function gtag(){dataLayer.push(arguments);}` +
          `gtag('js',new Date());gtag('config','${ga4Id}');`,
      },
      // Visitor actions as events; the same script runs on the landing pages.
      { tag: 'script', attrs: { type: 'module', src: '/scripts/events.js' } },
    ]
  : [];

// Fenced ```mermaid blocks become <pre class="mermaid"> before Expressive Code
// highlights code, so the page script can hand Mermaid the untouched source.
const mermaidFences = {
  name: 'mermaid-fences',
  code(node) {
    if (node.lang !== 'mermaid') return;
    return {
      type: 'paragraph',
      data: { hName: 'pre', hProperties: { className: ['mermaid'] } },
      children: [{ type: 'text', value: node.value }],
    };
  },
};

// Render those blocks as live diagrams on Starlight docs pages, in the current
// theme's palette, each in a viewer with zoom, pan and full screen.
/** @type {import('@astrojs/starlight/types').HeadConfig} */
const mermaidHead = [
  { tag: 'script', attrs: { type: 'module', src: '/scripts/mermaid.js' } },
];

// Same faces as the landing pages; Starlight's theme already names them.
/** @type {import('@astrojs/starlight/types').HeadConfig} */
const fontHead = [
  { tag: 'link', attrs: { rel: 'preconnect', href: 'https://fonts.googleapis.com' } },
  { tag: 'link', attrs: { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: true } },
  {
    tag: 'link',
    attrs: {
      rel: 'stylesheet',
      href: 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap',
    },
  },
];

// https://astro.build/config
export default defineConfig({
  site: 'https://imprintmcp.alexandruleca.com',
  // Templates are written with HTML whitespace semantics; Astro 7's default
  // JSX rules would drop the spaces between inline elements and text.
  compressHTML: true,
  markdown: {
    processor: satteri({ mdastPlugins: [mermaidFences, docLinks] }),
  },
  vite: {
    plugins: [tailwindcss()],
  },
  integrations: [
    starlight({
      title: 'Imprint',
      description: 'Persistent MCP memory for Claude Code, Cursor, Copilot, Codex and Cline — shared across tools, machines and teammates. Local retrieval and end-to-end encrypted workspace sync.',
      logo: { light: './public/logo-lockup.svg', dark: './public/logo-lockup-dark.svg', replacesTitle: true },
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/alexandruleca/imprint-memory-layer' },
      ],
      head: [...analyticsHead, ...fontHead, ...mermaidHead],
      customCss: ['./src/styles/starlight.css'],
      sidebar: [
        {
          label: 'Start',
          items: [
            { label: 'Installation', slug: 'installation' },
            { label: 'Chrome extension (preview)', slug: 'browser-extension' },
            { label: 'MCP tools', slug: 'mcp' },
            { label: 'HTTP API', slug: 'api' },
          ],
        },
        {
          label: 'Architecture',
          items: [
            { label: 'Overview', slug: 'architecture' },
            { label: 'Chunking', slug: 'chunking' },
            { label: 'Tagging', slug: 'tagging' },
            { label: 'Embeddings', slug: 'embeddings' },
            { label: 'Workspaces', slug: 'workspaces' },
            { label: 'Queue', slug: 'queue' },
            { label: 'Sync', slug: 'sync' },
          ],
        },
        {
          label: 'Engineering',
          items: [
            { label: 'Documentation index', slug: 'readme' },
            { label: 'Repository assessment', slug: 'repository-assessment' },
            { label: 'Audit remediation', slug: 'audit-remediation' },
            { label: 'Indexing and retrieval', slug: 'indexing-retrieval' },
            { label: 'Local model selection', slug: 'local-model-selection' },
            { label: 'Local helper models', slug: 'local-models' },
            { label: 'UI and UX review', slug: 'ui-ux-review' },
            { label: 'Integrations', slug: 'integrations' },
            { label: 'Quality roadmap', slug: 'quality-roadmap' },
            { label: 'Platform validation', slug: 'platform-validation' },
            { label: 'Architecture decisions', slug: 'adr/readme' },
          ],
        },
        {
          label: 'Operate',
          items: [
            { label: 'Configuration', slug: 'configuration' },
            { label: 'Docker', slug: 'docker' },
            { label: 'Building from source', slug: 'building' },
          ],
        },
      ],
      components: {
        Header: './src/components/DocsHeader.astro',
        ThemeProvider: './src/components/ThemeProvider.astro',
        ThemeSelect: './src/components/ThemeSelect.astro',
      },
      pagination: true,
      lastUpdated: true,
      disable404Route: true,
    }),
    mdx(),
  ],
});
