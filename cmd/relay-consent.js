// Google Analytics only with the visitor's consent.
//
// Nothing from Google Analytics loads until the visitor accepts: no script, no
// cookie, no request. Declining, or a browser that sends Global Privacy
// Control, keeps it that way; withdrawing later stops it on the page and
// deletes its cookies. The choice is kept in localStorage for a year, then
// asked again. Any element with [data-consent-open] reopens the banner.
//
// The sync relay's page inlines it at the end of its body when started with --ga4-id
// (cmd/relay.go), with data-ga4="G-…", data-page-location="root", which
// reports every view as / or /not-found so an address holding a share code
// never reaches Google, and data-privacy, the policy to link (none if empty).
// The website asked with a copy of it until it moved to Volenti (ADR 0231).
(() => {
  const script = document.currentScript;
  const id = script?.dataset.ga4 || '';
  if (!/^G-[A-Z0-9]+$/.test(id)) return;
  const privacy = script.dataset.privacy ?? '/privacy/#website';
  const rootOnly = script.dataset.pageLocation === 'root';

  const KEY = 'imprint-consent';
  // Raise when the policy changes enough to ask everyone again.
  const VERSION = 1;
  const YEAR = 365 * 24 * 3600 * 1000;
  let memory = null; // the choice when localStorage is unavailable

  function stored() {
    let saved = memory;
    try {
      saved = JSON.parse(localStorage.getItem(KEY) || 'null') || memory;
    } catch {}
    if (!saved || saved.v !== VERSION || !['granted', 'denied'].includes(saved.analytics)) return null;
    return Date.now() - saved.at < YEAR ? saved.analytics : null;
  }

  function remember(analytics) {
    memory = { v: VERSION, analytics, at: Date.now() };
    try {
      localStorage.setItem(KEY, JSON.stringify(memory));
    } catch {}
  }

  function load() {
    window[`ga-disable-${id}`] = false;
    if (window.gtag) {
      window.gtag('consent', 'update', { analytics_storage: 'granted' });
      return;
    }
    window.dataLayer = window.dataLayer || [];
    window.gtag = function gtag() {
      window.dataLayer.push(arguments);
    };
    window.gtag('consent', 'default', {
      analytics_storage: 'granted',
      ad_storage: 'denied',
      ad_user_data: 'denied',
      ad_personalization: 'denied',
    });
    window.gtag('js', new Date());
    const config = { allow_google_signals: false, allow_ad_personalization_signals: false };
    if (rootOnly) config.page_location = location.origin + (location.pathname === '/' ? '/' : '/not-found');
    window.gtag('config', id, config);
    const tag = document.createElement('script');
    tag.async = true;
    tag.src = `https://www.googletagmanager.com/gtag/js?id=${id}`;
    document.head.append(tag);
  }

  // GA sets its cookies on the widest domain it can (.alexandruleca.com), so
  // expire them on this host and every parent domain.
  function clearCookies() {
    const names = document.cookie
      .split(';')
      .map((part) => part.split('=')[0].trim())
      .filter((name) => /^(_ga|_ga_.+|_gid|_gat.*)$/.test(name));
    if (!names.length) return;
    const labels = location.hostname.split('.');
    const domains = [''];
    for (let i = 0; i < labels.length - 1; i++) domains.push(`; domain=.${labels.slice(i).join('.')}`);
    for (const name of names) {
      for (const domain of domains) document.cookie = `${name}=; Max-Age=0; path=/${domain}`;
    }
  }

  function stop() {
    window[`ga-disable-${id}`] = true;
    window.gtag?.('consent', 'update', { analytics_storage: 'denied' });
    clearCookies();
  }

  // Dark where the page is: Starlight's data-theme="dark", or the OS setting on
  // a page with no theme of its own (the relay). The landing pages pin light.
  const dark = '--bg:#171717;--fg:#fafafa;--line:#333;--muted:#a3a3a3;--dim:#8a8a8a;--button-line:#404040;--hover:#262626;--hover-line:#737373';
  const css = `
#imprint-consent{--bg:#fff;--fg:#0a0a0a;--line:#e5e5e5;--muted:#525252;--dim:#737373;--button-line:#d4d4d4;--hover:#f5f5f5;--hover-line:#a3a3a3;
  position:fixed;z-index:1000;left:16px;right:16px;bottom:16px;max-width:25rem;box-sizing:border-box;
  padding:1.1rem 1.15rem 1rem;border:1px solid var(--line);border-radius:14px;background:var(--bg);color:var(--fg);
  box-shadow:0 1px 2px rgba(0,0,0,.06),0 18px 45px -12px rgba(0,0,0,.22);font:inherit;font-size:14px;line-height:1.55;
  text-align:left}
:root[data-theme="dark"] #imprint-consent{${dark}}
@media (prefers-color-scheme:dark){:root:not([data-theme]) #imprint-consent{${dark}}}
#imprint-consent[hidden]{display:none}
#imprint-consent h2{margin:0;font:inherit;font-size:14.5px;font-weight:600;letter-spacing:0;color:inherit}
#imprint-consent p{margin:.35rem 0 0;color:var(--muted)}
#imprint-consent a{color:inherit;text-decoration:underline;text-underline-offset:2px}
#imprint-consent .consent-state{font-size:12.5px;color:var(--dim)}
#imprint-consent .consent-actions{display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-top:.9rem}
#imprint-consent button{margin:0;padding:.55rem .75rem;border:1px solid var(--button-line);border-radius:8px;background:var(--bg);
  color:var(--fg);font:inherit;font-size:13.5px;font-weight:600;cursor:pointer}
#imprint-consent button:hover{background:var(--hover);border-color:var(--hover-line)}
#imprint-consent button:focus-visible{outline:2px solid var(--fg);outline-offset:2px}
@media (prefers-reduced-motion:no-preference){#imprint-consent{animation:imprint-consent-in .25s ease-out}}
@keyframes imprint-consent-in{from{opacity:0;transform:translateY(8px)}}
@media print{#imprint-consent{display:none}}`;

  let banner = null;
  let stateLine = null;
  let returnFocus = null; // the control that reopened the banner

  function el(tag, props = {}, ...children) {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...children);
    return node;
  }

  function build() {
    const style = el('style', { textContent: css });
    document.head.append(style);
    stateLine = el('p', { className: 'consent-state' });
    const decline = el('button', { type: 'button', textContent: 'Decline' });
    const accept = el('button', { type: 'button', textContent: 'Accept' });
    banner = el(
      'section',
      { id: 'imprint-consent', hidden: true },
      el('h2', { id: 'imprint-consent-title', textContent: 'Analytics cookies' }),
      el(
        'p',
        {},
        'May we count your visit? If you accept, Google Analytics sets cookies so we can see which pages and downloads are used. If you decline, nothing is sent to Google Analytics.',
        ...(privacy ? [' ', el('a', { href: privacy, textContent: 'Privacy policy' })] : []),
      ),
      stateLine,
      el('div', { className: 'consent-actions' }, decline, accept),
    );
    banner.setAttribute('role', 'region');
    banner.setAttribute('aria-labelledby', 'imprint-consent-title');
    // Choosing is not a visitor action to report (events.js).
    banner.setAttribute('data-track-ignore', '');
    decline.addEventListener('click', () => choose('denied'));
    accept.addEventListener('click', () => choose('granted'));
    // First in the document, so keyboard and screen-reader users reach it first.
    document.body.prepend(banner);
  }

  function show(current) {
    if (!banner) build();
    stateLine.textContent =
      current === 'granted' ? 'You accepted analytics cookies.' : current === 'denied' ? 'You declined analytics cookies.' : '';
    stateLine.hidden = !current;
    banner.hidden = false;
  }

  function choose(analytics) {
    const opened = banner && banner.contains(document.activeElement);
    remember(analytics);
    if (analytics === 'granted') load();
    else stop();
    banner.hidden = true;
    if (opened) returnFocus?.focus();
    returnFocus = null;
  }

  document.addEventListener('click', (event) => {
    const opener = event.target instanceof Element && event.target.closest('[data-consent-open]');
    if (!opener) return;
    event.preventDefault();
    returnFocus = opener;
    show(stored() ?? (navigator.globalPrivacyControl === true ? 'denied' : null));
    banner.querySelector('button')?.focus();
  });

  const choice = stored();
  if (choice === 'granted') load();
  else {
    clearCookies();
    if (choice !== 'denied' && navigator.globalPrivacyControl !== true) {
      // An inline copy in <head> runs before <body> exists.
      if (document.body) show(null);
      else document.addEventListener('DOMContentLoaded', () => show(null), { once: true });
    }
  }
})();
