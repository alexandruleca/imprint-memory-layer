export const extensionAssetName = 'imprint-chrome-extension.zip';

// A desktop release without the extension must not turn the download into a 404.
export function latestChromeExtension(manifest) {
  const releases = [...(manifest.releases || [])]
    .filter((release) => !release.prerelease)
    .sort((a, b) => Date.parse(b.publishedAt) - Date.parse(a.publishedAt));
  for (const release of releases) {
    const asset = (release.assets || []).find((candidate) => candidate.name === extensionAssetName);
    if (asset) return {release, asset};
  }
  return null;
}
