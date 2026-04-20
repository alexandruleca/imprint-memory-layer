// @ts-check
import { defineConfig } from 'astro/config';
import { loadEnv } from 'vite';
import starlight from '@astrojs/starlight';
import mdx from '@astrojs/mdx';
import tailwindcss from '@tailwindcss/vite';

const env = loadEnv(process.env.NODE_ENV ?? '', process.cwd(), 'PUBLIC_');
const gtmId = env.PUBLIC_GTM_ID || process.env.PUBLIC_GTM_ID;
const ga4Id = env.PUBLIC_GA4_ID || process.env.PUBLIC_GA4_ID;

/** @type {import('@astrojs/starlight/types').HeadConfig} */
const analyticsHead = [];
if (gtmId) {
  analyticsHead.push({
    tag: 'script',
    content:
      `(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':` +
      `new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],` +
      `j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;` +
      `j.src='https://www.googletagmanager.com/gtm.js?id='+i+dl;` +
      `f.parentNode.insertBefore(j,f);` +
      `})(window,document,'script','dataLayer','${gtmId}');`,
  });
}
if (ga4Id) {
  analyticsHead.push(
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
  );
}

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
      head: analyticsHead,
      customCss: ['./src/styles/starlight.css'],
      sidebar: [
        {
          label: 'Start',
          items: [
            { label: 'Installation', slug: 'installation' },
            { label: 'MCP tools', slug: 'mcp' },
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
