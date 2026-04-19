// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import mdx from '@astrojs/mdx';
import tailwindcss from '@tailwindcss/vite';

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
