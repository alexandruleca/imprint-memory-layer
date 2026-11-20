export const extensionAssetName = 'imprint-chrome-extension.zip';
// Only the Mozilla-signed add-on installs in release Firefox; the -unsigned
// build stays on the releases page for Developer Edition and Nightly.
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
