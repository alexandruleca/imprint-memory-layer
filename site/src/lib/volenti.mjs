// Volenti asks for consent on every page of this website (ADR 0231), in builds
// with a GA4 measurement ID: Analytics.astro for the landing pages and the
// Starlight head in astro.config.mjs for docs. Both render the tags below.
//
// The snippet sets every Google consent signal to denied and queues
// window.volenti.openPreferences() calls until the loader arrives. The Volenti
// site behind this key belongs to imprintmcp.alexandruleca.com, uses Basic
// consent mode and runs on that host, localhost, *.test and *.local only.
export const volentiLoader = 'https://gdpr.sys.cloud.alexandruleca.com/v1/avwdzx6wliubqo2bazgk/loader.js';

export const volentiSnippet = `
  window.dataLayer = window.dataLayer || [];
  function gtag() { dataLayer.push(arguments); }
  gtag("consent", "default", { ad_storage: "denied", analytics_storage: "denied",
    ad_user_data: "denied", ad_personalization: "denied", wait_for_update: 500 });
  window.volenti = window.volenti || { q: [], openPreferences: function () { this.q.push("openPreferences"); } };
`;

// Google Analytics stays inert (type="text/plain") until the visitor allows
// Statistics; Volenti then runs these in order, gtag.js first. Withdrawing
// Statistics deletes the cookies named here on this host and its parents.
export function analyticsTags(ga4Id) {
  return [
    {
      type: 'text/plain',
      'data-volenti-category': 'statistics',
      'data-volenti-cookies': '_ga _ga_* _gid _gat*',
      src: `https://www.googletagmanager.com/gtag/js?id=${ga4Id}`,
    },
    { type: 'text/plain', 'data-volenti-category': 'statistics', 'data-ga4': ga4Id, src: '/scripts/analytics.js' },
  ];
}
