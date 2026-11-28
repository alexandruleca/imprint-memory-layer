// Google Analytics' configuration, run by Volenti only once the visitor allows
// Statistics (ADR 0231). It stays inert until then: the page tags it
// type="text/plain" data-volenti-category="statistics" data-ga4="G-…", after
// gtag.js (src/lib/volenti.mjs). The install snippet defines gtag() and has
// already sent Volenti's consent update, so this view is reported as allowed.
(() => {
  const id = document.currentScript?.dataset.ga4 || '';
  if (!/^G-[A-Z0-9]+$/.test(id) || typeof window.gtag !== 'function') return;
  window[`ga-disable-${id}`] = false;
  window.gtag('js', new Date());
  window.gtag('config', id, { allow_google_signals: false, allow_ad_personalization_signals: false });

  // Withdrawing on this page: Volenti denies analytics_storage and deletes the
  // cookies, and this stops Google Analytics from sending even cookieless
  // pings until the visitor allows Statistics again.
  window.addEventListener('volenti:change', (event) => {
    window[`ga-disable-${id}`] = event.detail?.categories?.statistics !== true;
  });
})();
