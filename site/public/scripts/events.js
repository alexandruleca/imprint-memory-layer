// GA4 events for what visitors do, from one listener on every page: release
// downloads, copied commands, tabs, opened answers and menus, the docs theme,
// and every other link and button. Loaded only when the build has a
// measurement ID (Analytics.astro and the Starlight head in astro.config.mjs).
//
// An element can name its own event: data-track="copy_command" plus
// data-track-* attributes for the parameters (data-track-command="unix").

const RELEASE_ASSET = /\/releases\/download\/([^/]+)\/([^/?#]+)$/;

const send = (name, params) => window.gtag?.('event', name, params);

const label = (el) =>
  (el.getAttribute('aria-label') || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 100);

// Where on the page: the nearest section with an id, or the landmark.
function placement(el) {
  const area = el.closest('section[id], header, footer, nav, aside, main');
  return area ? area.id || area.localName : 'page';
}

// data-track-file-name="x" → { file_name: 'x' }
function declared(el) {
  const params = {};
  for (const [key, value] of Object.entries(el.dataset)) {
    if (key.startsWith('track') && key !== 'track') {
      params[key.slice(5).replace(/(?!^)[A-Z]/g, (c) => `_${c}`).toLowerCase()] = value;
    }
  }
  return params;
}

function describe(el) {
  if (el.dataset.track) return [el.dataset.track, declared(el)];
  if (el.localName === 'summary') {
    // Clicking an open summary closes it; only opening is an action.
    return el.parentElement?.open ? null : ['expand', { label: label(el) }];
  }
  if (el.getAttribute('role') === 'tab') {
    const group = el.closest('[role="tablist"]')?.getAttribute('aria-label') ?? '';
    return ['select_tab', { tab_group: group, tab: label(el) }];
  }
  if (el.localName === 'a') {
    const url = new URL(el.href, location.href);
    const asset = url.pathname.match(RELEASE_ASSET);
    if (asset) {
      const [, version, file] = asset;
      return ['download', {
        file_name: file,
        file_extension: file.match(/\.(tar\.gz|[a-z0-9]+)$/i)?.[1] ?? '',
        version,
        link_text: label(el),
        link_url: url.href,
      }];
    }
    return ['link_click', { link_text: label(el), link_url: url.href, outbound: url.host !== location.host }];
  }
  return ['button_click', { label: label(el) }];
}

// Capture phase: a component handler that stops propagation still counts.
document.addEventListener('click', (event) => {
  const el = event.target instanceof Element && event.target.closest('a[href], button, summary, [data-track]');
  const found = el && describe(el);
  if (found) send(found[0], { ...found[1], placement: placement(el) });
}, { capture: true });

document.addEventListener('change', (event) => {
  const el = event.target;
  if (el instanceof HTMLSelectElement) send('select_option', { option: el.value, placement: placement(el) });
}, { capture: true });
