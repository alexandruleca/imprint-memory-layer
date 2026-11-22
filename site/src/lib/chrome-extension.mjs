export const extensionAssetName = 'imprint-chrome-extension.zip';
// Stable releases are submitted here and Chrome updates installed copies (ADR 0202).
export const chromeWebStoreUrl = 'https://chromewebstore.google.com/detail/imprint-capture/ammleledmkghliflljeapomaaccnhmme';
// Only the Mozilla-signed add-on installs in release Firefox; the -unsigned
// build stays on the releases page for Developer Edition and Nightly. Releases
// after v0.59.0 go to addons.mozilla.org instead (ADR 0203), so this finds the
// last release that carried one.
export const firefoxAssetName = 'imprint-firefox-extension.xpi';

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
