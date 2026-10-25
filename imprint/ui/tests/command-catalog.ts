import type { Page } from '@playwright/test';

// A subset of GET /api/commands (imprint/commands.py) for the specs that
// open the Commands page: the commands they run and the client commands.
export const clients = [
  { name: 'claude-code', label: 'Claude Code', connected: true },
  { name: 'cursor', label: 'Cursor', connected: false },
  { name: 'codex', label: 'Codex', connected: false },
];

const client = { key: 'target', label: 'Client', kind: 'client' };

export const commands = [
  { name: 'learn', label: 'Learn conversations', group: 'Index', description: 'Index coding-assistant conversations and memory files',
    fields: [{ key: 'desktop', label: 'Desktop exports', kind: 'switch', hint: 'Also index new Claude Desktop and ChatGPT export zips from Downloads' }] },
  { name: 'refresh', label: 'Refresh files', group: 'Index', description: 'Re-index only files changed since the last run',
    fields: [{ key: 'dir', label: 'Directory', kind: 'path', browse: 'dir', required: true }] },
  { name: 'sync', label: 'Export snapshot', group: 'Maintain', description: 'Write a sync bundle of the active workspace',
    fields: [], fixed: { action: 'export' } },
  { name: 'connect', label: 'Connect clients', group: 'Clients',
    description: "Register Imprint's MCP server, hooks and instructions with your AI tools", fields: [client] },
  { name: 'disconnect', label: 'Disconnect clients', group: 'Clients',
    description: "Remove Imprint's MCP server, hooks and instructions from your AI tools", fields: [client],
    confirm: 'The chosen clients stop using Imprint: its MCP server, hooks and instructions are removed from their settings, including their recorded WSL profiles. Memories, the runtime and this dashboard stay; Connect clients restores the local ones.' },
  { name: 'doctor', label: 'Diagnose', group: 'Inspect', description: 'Check the runtime, dependencies and integrations without changing them',
    fields: [{ key: 'probe_mcp', label: 'Start the MCP server', kind: 'switch', hint: "Also start Imprint's MCP server once and list its tools, as a client would" }] },
];

/** Serve the catalog; `current` is read on every request, so a spec can change the clients it reports. */
export async function mockCatalog(page: Page, current: { clients: typeof clients } = { clients }) {
  let loads = 0;
  await page.route('**/api/commands', route => {
    loads++;
    return route.fulfill({ json: { commands, clients: current.clients } });
  });
  return { loads: () => loads };
}
