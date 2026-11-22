#!/usr/bin/env python3
"""Build the allowlisted, reproducible browser extension packages.

One source, extensions/chrome, makes the manual-install Chrome ZIP and the
Firefox add-on (ADR 0193): Firefox gets its own manifest and an archive with
the files at its root, as Mozilla's signing service and Firefox expect.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ('manifest.json', 'background.js', 'capture.js', 'files.js', 'client.js', 'ui.js', 'screenshot.js',
         'popup.html', 'popup.js', 'options.html', 'options.js', 'style.css',
         'icon-16.png', 'icon-32.png', 'icon-48.png', 'icon-128.png', 'INSTALL.txt', 'LICENSE')
# INSTALL.txt explains Chrome's Load unpacked; a Firefox add-on is installed whole.
FIREFOX_FILES = tuple(name for name in FILES if name != 'INSTALL.txt')
ARCHIVE_NAME = 'imprint-chrome-extension.zip'
FIREFOX_ARCHIVE_NAME = 'imprint-firefox-extension-unsigned.xpi'
# The add-on's permanent identity on addons.mozilla.org.
GECKO_ID = 'capture@imprintmcp.alexandruleca.com'
# data_collection_permissions arrived in Firefox 140; captures go only to Imprint on this device.
FIREFOX_MIN_VERSION = '140.0'
# Firefox for Android took the key in 142; without its own minimum, AMO's linter warns.
FIREFOX_ANDROID_MIN_VERSION = '142.0'


def release_version(tag):
    """(version, version_name) for a release tag: v0.57.1 or v0.57.1-dev.2.

    Browsers take up to four dot-separated numbers; a prerelease number becomes
    the fourth, and Chrome shows the tag through version_name.
    """
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-[A-Za-z]+(?:\.(\d+))?)?', tag or '')
    if not match:
        raise ValueError(f'Expected a release tag such as v1.2.3 or v1.2.3-dev.4, not {tag!r}')
    numbers = [str(int(part)) for part in match.groups() if part is not None]
    return '.'.join(numbers), tag.removeprefix('v')


def firefox_manifest(manifest):
    """Firefox runs the background as an event page and names the add-on itself."""
    result = copy.deepcopy(manifest)
    result['background'] = {'scripts': [manifest['background']['service_worker']], 'type': 'module'}
    result.pop('minimum_chrome_version', None)
    result.pop('version_name', None)
    result['options_ui'] = {'page': result.pop('options_page'), 'open_in_tab': True}
    result['browser_specific_settings'] = {'gecko': {
        'id': GECKO_ID, 'strict_min_version': FIREFOX_MIN_VERSION,
        'data_collection_permissions': {'required': ['none']},
    }, 'gecko_android': {'strict_min_version': FIREFOX_ANDROID_MIN_VERSION}}
    return result


def package(source=ROOT / 'extensions/chrome', output=None, browser='chrome', version=None):
    source = Path(source)
    firefox = browser == 'firefox'
    if browser not in ('chrome', 'firefox'):
        raise ValueError(f'Unknown browser: {browser}')
    output = Path(output or ROOT / 'dist' / (FIREFOX_ARCHIVE_NAME if firefox else ARCHIVE_NAME))
    files = {}
    for name in FIREFOX_FILES if firefox else FILES:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing or unsafe extension runtime file: {name}')
        files[name] = path.read_bytes()
    manifest = json.loads(files['manifest.json'])
    if manifest.get('manifest_version') != 3 or not manifest.get('version'):
        raise ValueError('Expected a versioned Manifest V3 extension')
    if version:
        manifest['version'], name = release_version(version)
        if name != manifest['version']:
            manifest['version_name'] = name
    if firefox:
        manifest = firefox_manifest(manifest)
    if version or firefox:
        files['manifest.json'] = (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    prefix = '' if firefox else 'imprint-chrome-extension/'
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.zip', delete=False) as temporary:
        staged = Path(temporary.name)
    try:
        with zipfile.ZipFile(staged, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                info = zipfile.ZipInfo(prefix + name, (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        staged.replace(output)
    finally:
        staged.unlink(missing_ok=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + '.sha256').write_text(f'{digest}  {output.name}\n', encoding='utf-8')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', choices=('chrome', 'firefox'), default='chrome')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--version', help='release tag to stamp into the manifest, e.g. v0.57.1')
    args = parser.parse_args()
    print(package(output=args.output, browser=args.browser, version=args.version))


if __name__ == '__main__':
    main()
