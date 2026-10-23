// Docs diagrams: render ```mermaid blocks (turned into <pre class="mermaid"> by
// the markdown plugin in astro.config.mjs) in the current theme's palette, and
// give each a viewer with zoom, pan and fullscreen.

const blocks = [...document.querySelectorAll('pre.mermaid')];

const PALETTES = {
  light: {
    darkMode: false, background: '#fafafa', primaryColor: '#f4f4f5', primaryTextColor: '#171717',
    primaryBorderColor: '#525252', lineColor: '#404040', secondaryColor: '#fafafa', tertiaryColor: '#f4f4f5',
    noteBkgColor: '#ffffff', noteBorderColor: '#d4d4d4', noteTextColor: '#171717',
  },
  dark: {
    darkMode: true, background: '#0d0d0d', primaryColor: '#1a1a1a', primaryTextColor: '#ededed',
    primaryBorderColor: '#737373', lineColor: '#a3a3a3', secondaryColor: '#141414', tertiaryColor: '#111111',
    noteBkgColor: '#1f1f1f', noteBorderColor: '#404040', noteTextColor: '#ededed',
  },
};

const ICONS = {
  out: '<path d="M5 12h14"/>',
  in: '<path d="M12 5v14M5 12h14"/>',
  reset: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
  full: '<path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/>',
  exit: '<path d="M8 3v3a2 2 0 0 1-2 2H3M21 8h-3a2 2 0 0 1-2-2V3M3 16h3a2 2 0 0 1 2 2v3M16 21v-3a2 2 0 0 1 2-2h3"/>',
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;

const MIN = 0.25;
const MAX = 8;
const clamp = (k) => Math.min(MAX, Math.max(MIN, k));

/** Wrap one diagram in a viewer. Its transform lives on the <pre>, whose
 * layout box is the untransformed diagram, so every zoom is computed from
 * where the diagram is drawn now. */
function viewer(pre) {
  const figure = document.createElement('figure');
  // not-content: Starlight's prose spacing does not apply inside the viewer.
  figure.className = 'mermaid-viewer not-content';
  const viewport = document.createElement('div');
  viewport.className = 'mv-viewport';
  viewport.tabIndex = 0;
  viewport.setAttribute('role', 'group');
  viewport.setAttribute('aria-label', 'Diagram. Plus and minus zoom, arrow keys pan, 0 resets.');
  const toolbar = document.createElement('div');
  toolbar.className = 'mv-toolbar';
  const hint = document.createElement('p');
  hint.className = 'mv-hint';
  hint.textContent = 'Drag to pan · Ctrl + scroll to zoom';
  pre.replaceWith(figure);
  viewport.append(pre);
  figure.append(viewport, toolbar, hint);

  let x = 0, y = 0, k = 1;
  const apply = () => {
    pre.style.transform = `translate(${x}px, ${y}px) scale(${k})`;
    figure.classList.toggle('mv-zoomed', k !== 1 || x !== 0 || y !== 0);
  };
  // Zoom to `next` keeping the diagram point under (cx, cy) in place.
  const zoomAt = (next, cx, cy) => {
    next = clamp(next);
    const r = pre.getBoundingClientRect();
    x += (cx - r.left) * (1 - next / k);
    y += (cy - r.top) * (1 - next / k);
    k = next;
    apply();
  };
  const zoomCenter = (factor) => {
    const v = viewport.getBoundingClientRect();
    zoomAt(k * factor, v.left + v.width / 2, v.top + v.height / 2);
  };
  const fit = () => {
    x = 0; y = 0; k = 1;
    apply();
    if (!isFull()) return;
    // Fullscreen: as large as the screen allows, centred.
    const v = viewport.getBoundingClientRect();
    const r = pre.getBoundingClientRect();
    const next = clamp(Math.min((v.width * 0.94) / r.width, (v.height * 0.94) / r.height));
    x = v.left + (v.width - r.width * next) / 2 - r.left;
    y = v.top + (v.height - r.height * next) / 2 - r.top;
    k = next;
    apply();
  };

  // Fullscreen, or a fixed overlay where the Fullscreen API is missing (iOS).
  const isFull = () => document.fullscreenElement === figure || figure.classList.contains('mv-expanded');
  const toggleFull = async () => {
    if (isFull()) {
      if (document.fullscreenElement === figure) await document.exitFullscreen();
      figure.classList.remove('mv-expanded');
    } else if (figure.requestFullscreen) {
      await figure.requestFullscreen().catch(() => figure.classList.add('mv-expanded'));
    } else {
      figure.classList.add('mv-expanded');
    }
    if (!document.fullscreenElement) onFullChange();
  };
  const onFullChange = () => {
    const full = isFull();
    fullButton.innerHTML = icon(full ? 'exit' : 'full');
    fullButton.setAttribute('aria-label', full ? 'Exit full screen' : 'Full screen');
    fullButton.title = fullButton.getAttribute('aria-label');
    hint.textContent = full ? 'Drag to pan · scroll to zoom · Esc to close' : 'Drag to pan · Ctrl + scroll to zoom';
    requestAnimationFrame(fit);
  };
  document.addEventListener('fullscreenchange', () => {
    if (document.fullscreenElement === figure || figure.dataset.wasFull) onFullChange();
    figure.dataset.wasFull = document.fullscreenElement === figure ? '1' : '';
  });

  const button = (name, label, onClick) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.innerHTML = icon(name);
    b.setAttribute('aria-label', label);
    b.title = label;
    b.addEventListener('click', onClick);
    toolbar.append(b);
    return b;
  };
  button('out', 'Zoom out', () => zoomCenter(1 / 1.25));
  button('in', 'Zoom in', () => zoomCenter(1.25));
  button('reset', 'Reset view', fit);
  const fullButton = button('full', 'Full screen', toggleFull);
  figure.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && figure.classList.contains('mv-expanded')) toggleFull();
  });

  // Ctrl/⌘ + wheel (and trackpad pinch, which sets ctrlKey) zooms; plain wheel
  // scrolls the page, except in fullscreen where there is nothing to scroll.
  viewport.addEventListener('wheel', (e) => {
    if (!(e.ctrlKey || e.metaKey || isFull())) return;
    e.preventDefault();
    zoomAt(k * Math.exp(-e.deltaY * 0.0015), e.clientX, e.clientY);
  }, { passive: false });
  viewport.addEventListener('dblclick', (e) => zoomAt(k * 1.6, e.clientX, e.clientY));

  // Drag to pan. A finger scrolls the page unless the diagram is zoomed or full.
  let drag = null;
  viewport.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    if (e.pointerType === 'touch' && !figure.classList.contains('mv-zoomed') && !isFull()) return;
    drag = { id: e.pointerId, px: e.clientX, py: e.clientY };
    viewport.setPointerCapture(e.pointerId);
    figure.classList.add('mv-dragging');
  });
  viewport.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    x += e.clientX - drag.px;
    y += e.clientY - drag.py;
    drag.px = e.clientX;
    drag.py = e.clientY;
    apply();
  });
  const end = () => { drag = null; figure.classList.remove('mv-dragging'); };
  viewport.addEventListener('pointerup', end);
  viewport.addEventListener('pointercancel', end);

  viewport.addEventListener('keydown', (e) => {
    const step = 48;
    const actions = {
      '+': () => zoomCenter(1.25), '=': () => zoomCenter(1.25), '-': () => zoomCenter(1 / 1.25),
      '0': fit, ArrowLeft: () => { x += step; apply(); }, ArrowRight: () => { x -= step; apply(); },
      ArrowUp: () => { y += step; apply(); }, ArrowDown: () => { y -= step; apply(); },
    };
    if (actions[e.key]) { e.preventDefault(); actions[e.key](); }
  });

  return { reset: fit };
}

if (blocks.length) {
  const mermaid = (await import('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs')).default;
  const sources = blocks.map((b) => b.textContent);
  const viewers = blocks.map(viewer);
  let shown = '';
  const render = async () => {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
    if (theme === shown) return;
    shown = theme;
    mermaid.initialize({
      startOnLoad: false, theme: 'base', securityLevel: 'loose',
      themeVariables: { ...PALETTES[theme], fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif' },
      flowchart: { htmlLabels: true, curve: 'basis' },
      sequence: { actorMargin: 40 },
    });
    blocks.forEach((b, i) => { b.removeAttribute('data-processed'); b.textContent = sources[i]; });
    await mermaid.run({ nodes: blocks });
    viewers.forEach((v) => v.reset());
  };
  await render();
  new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
}
