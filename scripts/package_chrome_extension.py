#!/usr/bin/env python3
"""Build the allowlisted, reproducible manual-install Chrome extension ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ('manifest.json', 'background.js', 'capture.js', 'client.js', 'ui.js',
         'popup.html', 'popup.js', 'options.html', 'options.js', 'style.css', 'INSTALL.txt', 'LICENSE')
ARCHIVE_NAME = 'imprint-chrome-extension.zip'


def package(source=ROOT / 'extensions/chrome', output=ROOT / 'dist' / ARCHIVE_NAME):
    source, output = Path(source), Path(output)
    files = {}
    for name in FILES:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing or unsafe extension runtime file: {name}')
        files[name] = path.read_bytes()
    manifest = json.loads(files['manifest.json'])
    if manifest.get('manifest_version') != 3 or not manifest.get('version'):
        raise ValueError('Expected a versioned Manifest V3 extension')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.zip', delete=False) as temporary:
        staged = Path(temporary.name)
    try:
        with zipfile.ZipFile(staged, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                info = zipfile.ZipInfo('imprint-chrome-extension/' + name, (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        staged.replace(output)
    finally:
        staged.unlink(missing_ok=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(f'{digest}  {output.name}\n', encoding='utf-8')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / ARCHIVE_NAME)
    args = parser.parse_args()
    print(package(output=args.output))


if __name__ == '__main__':
    main()
