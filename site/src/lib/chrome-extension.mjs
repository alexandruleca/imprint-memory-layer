export const extensionAssetName = 'imprint-chrome-extension.zip';
// Stable releases are submitted here and Chrome updates installed copies (ADR 0202).
export const chromeWebStoreUrl = 'https://chromewebstore.google.com/detail/imprint-capture/ammleledmkghliflljeapomaaccnhmme';
// Only the Mozilla-signed add-on installs in release Firefox; the -unsigned
// build stays on the releases page for Developer Edition and Nightly. A release
// whose signing AMO throttled carries none (ADR 0206), and v0.61.0 and v0.61.1
// carry none either, so this finds the newest stable release that has one.
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
