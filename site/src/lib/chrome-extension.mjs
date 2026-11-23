export const extensionAssetName = 'imprint-chrome-extension.zip';
// Stable releases are submitted here and Chrome updates installed copies (ADR 0202).
export const chromeWebStoreUrl = 'https://chromewebstore.google.com/detail/imprint-capture/ammleledmkghliflljeapomaaccnhmme';
// Only the Mozilla-signed add-on installs in release Firefox; the -unsigned
// build stays on the releases page for Developer Edition and Nightly. Stable
// releases go to the add-on's addons.mozilla.org listing (ADR 0207) and carry no
// signed file, so this finds the last release that carried one (v0.60.0).
export const firefoxAssetName = 'imprint-firefox-extension.xpi';
// The add-on's ID on addons.mozilla.org (GECKO_ID in scripts/package_chrome_extension.py).
export const firefoxAddonId = 'capture@imprintmcp.alexandruleca.com';

// AMO's public detail of the add-on, read when the site is built: a listing
// page once Mozilla has approved a listed version. Until then AMO answers 401
// and there is no listing to link to.
export function amoListing(detail) {
  const url = detail?.url;
  const version = detail?.current_version?.version;
  if (detail?.status !== 'public' || !version || typeof url !== 'string') return null;
  return url.startsWith('https://addons.mozilla.org/') ? {url, version} : null;
}

// A desktop release without the extension must not turn the download into a 404.
export function latestReleaseAsset(manifest, name) {
  const releases = [...(manifest.releases || [])]
    .filter((release) => !release.prerelease)
    .sort((a, b) => Date.parse(b.publishedAt) - Date.parse(a.publishedAt));
  for (const release of releases) {
    const asset = (release.assets || []).find((candidate) => candidate.name === name);
    if (asset) return {release, asset};
  }
  return null;
}

export const latestChromeExtension = (manifest) => latestReleaseAsset(manifest, extensionAssetName);
export const latestFirefoxExtension = (manifest) => latestReleaseAsset(manifest, firefoxAssetName);

// The listing, which updates itself, else the newest signed file to install by hand.
export function firefoxInstall(manifest) {
  if (manifest.firefoxListing) return {url: manifest.firefoxListing.url, listed: true};
  const file = latestFirefoxExtension(manifest);
  return file && {url: file.asset.url, listed: false};
}
