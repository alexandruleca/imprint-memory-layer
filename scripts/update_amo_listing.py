#!/usr/bin/env python3
"""Apply extensions/store's listing to the Firefox add-on on addons.mozilla.org (ADR 0203).

    AMO_JWT_ISSUER=user:… AMO_JWT_SECRET=… python scripts/update_amo_listing.py [--replace-screenshots]

Sets the summary, description, categories, homepage, support link and privacy
policy from extensions/store/amo-listing.json and the files it names. The
screenshots are uploaded, with their captions, when the add-on has none, or
with --replace-screenshots, which deletes the current ones first: AMO keeps
its own copies, so a rerun cannot tell whether they changed.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from package_chrome_extension import GECKO_ID  # noqa: E402
from publish_firefox_extension import API, Client  # noqa: E402

LISTING = Path(__file__).resolve().parents[1] / 'extensions/store/amo-listing.json'


def multipart(fields, name, filename, data, content_type):
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
             for key, value in fields.items()]
    parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
              f'Content-Type: {content_type}\r\n\r\n'.encode(), data, f'\r\n--{boundary}--\r\n'.encode()]
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


def update(client, listing_path=LISTING, replace_screenshots=False, guid=GECKO_ID):
    listing_path = Path(listing_path)
    listing = json.loads(listing_path.read_text(encoding='utf-8'))
    read = lambda key: (listing_path.parent / listing[key]).read_text(encoding='utf-8').strip()
    addon = f'{API}/addon/{guid}'
    details = client.request('GET', f'{addon}/')
    locale = details.get('default_locale') or 'en-US'
    text = lambda value: {locale: value}
    # The API documents categories keyed by application but reads them back flat;
    # write them in the shape this add-on reads back.
    categories = listing['categories']
    if isinstance(details.get('categories'), dict):
        categories = {'firefox': categories}
    client.request('PATCH', f'{addon}/', {
        'summary': text(listing['summary']),
        'description': text(read('description_file')),
        'categories': categories,
        'homepage': text(listing['homepage']),
        'support_url': text(listing['support_url']),
    })
    client.request('PATCH', f'{addon}/eula_policy/', {'privacy_policy': text(read('privacy_policy_file'))})
    print(f'Updated the listing text of {guid}')
    previews = details.get('previews') or []
    if previews and not replace_screenshots:
        print(f'Kept its {len(previews)} screenshots; pass --replace-screenshots to upload them again')
        return
    for preview in previews:
        client.request('DELETE', f'{addon}/previews/{preview["id"]}/')
    for position, shot in enumerate(listing['screenshots']):
        path = listing_path.parent / shot['file']
        content_type = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'
        body, form = multipart({'position': position}, 'image', path.name, path.read_bytes(), content_type)
        created = client.request('POST', f'{addon}/previews/', body, form)
        # AMO takes a caption only in a separate call.
        client.request('PATCH', f'{addon}/previews/{created["id"]}/', {'caption': text(shot['caption'])})
    print(f'Uploaded {len(listing["screenshots"])} screenshots in place of {len(previews)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--listing', type=Path, default=LISTING)
    parser.add_argument('--replace-screenshots', action='store_true')
    args = parser.parse_args()
    issuer, secret = os.environ.get('AMO_JWT_ISSUER'), os.environ.get('AMO_JWT_SECRET')
    if not issuer or not secret:
        sys.exit('Set AMO_JWT_ISSUER and AMO_JWT_SECRET (addons.mozilla.org → Developer Hub → Manage API Keys).')
    client = Client(issuer, secret, os.environ.get('AMO_BASE_URL', 'https://addons.mozilla.org'))
    update(client, args.listing, args.replace_screenshots)


if __name__ == '__main__':
    main()
