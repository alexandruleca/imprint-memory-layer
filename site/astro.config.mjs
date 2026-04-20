// @ts-check
import { defineConfig } from 'astro/config';
import { loadEnv } from 'vite';
import starlight from '@astrojs/starlight';
import mdx from '@astrojs/mdx';
import tailwindcss from '@tailwindcss/vite';

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
    ]
  : [];

// Render fenced ```mermaid blocks as live diagrams on Starlight docs pages.
// Mirrors the MermaidInit component used by landing pages.
/** @type {import('@astrojs/starlight/types').HeadConfig} */
const mermaidHead = [
  {
    tag: 'style',
    content:
      `div.mermaid{background:#15172a;border:1px solid #262845;border-radius:.75rem;` +
      `padding:1rem;margin:1rem 0;overflow-x:auto;text-align:center}` +
      `div.mermaid svg{max-width:100%;height:auto}`,
  },
  {
    tag: 'script',
    attrs: { type: 'module' },
    content: `(async () => {
  const blocks = document.querySelectorAll('pre > code.language-mermaid, pre > code[data-language="mermaid"]');
  if (!blocks.length) return;
  blocks.forEach((code) => {
    const pre = code.parentElement;
    if (!pre || pre.dataset.mermaidRendered === 'true') return;
    const container = document.createElement('div');
    container.className = 'mermaid';
    container.textContent = code.textContent || '';
    pre.replaceWith(container);
  });
  const mermaid = (await import('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs')).default;
  mermaid.initialize({
    startOnLoad: false, theme: 'dark', securityLevel: 'loose',
    themeVariables: {
      background: '#15172a', primaryColor: '#1f2144', primaryTextColor: '#eef0ff',
      primaryBorderColor: '#6366f1', lineColor: '#a78bfa',
      secondaryColor: '#262845', tertiaryColor: '#0f1020',
      fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
    },
    flowchart: { htmlLabels: true, curve: 'basis' },
    sequence: { actorMargin: 40 },
  });
  await mermaid.run({ querySelector: 'div.mermaid' });
})();`,
  },
];

// https://astro.build/config
export default defineConfig({
  site: 'https://imprintmcp.alexandruleca.com',
  vite: {
    plugins: [tailwindcss()],
  },
  integrations: [
    starlight({
      title: 'Imprint',
      description: 'MCP memory layer for Claude Code, Cursor, Copilot. 100% local. −70.4% tokens, −31.7% cost.',
      logo: { src: './public/logo.svg', replacesTitle: false },
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/alexandruleca/imprint-memory-layer' },
      ],
      head: [...analyticsHead, ...mermaidHead],
      customCss: ['./src/styles/starlight.css'],
      sidebar: [
        {
          label: 'Start',
          items: [
            { label: 'Installation', slug: 'installation' },
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
      },
      pagination: true,
      lastUpdated: true,
      disable404Route: true,
    }),
    mdx(),
  ],
});
