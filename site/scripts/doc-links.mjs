import { readFileSync, readdirSync, realpathSync, existsSync, statSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const docs = resolve(root, 'docs');
const routes = new Map();

function collect(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) collect(path);
    else if (entry.name.endsWith('.md')) {
      const text = readFileSync(path, 'utf8');
      const slug = text.match(/^slug:\s*['"]?([^'"\r\n]+)['"]?\s*$/m)?.[1]?.trim()
        ?? relative(docs, path).split(sep).join('/').replace(/\.md$/, '').toLowerCase();
      routes.set(realpathSync(path), `/${slug}/`);
    }
  }
}
collect(docs);

export function rewriteDocLink(url, sourceFile) {
  if (!url || /^(?:[a-z][a-z\d+.-]*:|\/|#)/i.test(url)) return url;
  const match = url.match(/^([^?#]+)([?#].*)?$/);
  if (!match) return url;
  let source, target;
  try {
    source = realpathSync(sourceFile);
    if (!source.startsWith(docs + sep)) return url;
    target = resolve(dirname(source), decodeURIComponent(match[1]));
  } catch { return url; }
  if (!target.startsWith(root + sep) || !existsSync(target)) return url;
  target = realpathSync(target);
  if (routes.has(target)) return routes.get(target) + (match[2] ?? '');
  const path = relative(root, target).split(sep).map(encodeURIComponent).join('/');
  if (path.startsWith('..')) return url;
  return `https://github.com/alexandruleca/imprint-memory-layer/${statSync(target).isDirectory() ? 'tree' : 'blob'}/main/${path}${match[2] ?? ''}`;
}

function link(node, context) {
  if (context.fileURL?.protocol !== 'file:') return;
  const url = rewriteDocLink(node.url, fileURLToPath(context.fileURL));
  if (url !== node.url) return { ...node, url };
}

// Sätteri visitor API; preserve repository-relative Markdown for GitHub while
// emitting actual Starlight routes and source links on the presentation site.
export const docLinks = { name: 'repository-doc-links', link, definition: link };
