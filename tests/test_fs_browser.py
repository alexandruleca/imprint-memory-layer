from pathlib import Path, PureWindowsPath
import tempfile
import unittest
from unittest.mock import patch

import httpx


class FsBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def get(self, route, **params):
        from imprint.api import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)),
                                     base_url='http://127.0.0.1:8420') as client:
            return await client.get(route, params=params)

    async def test_listing_returns_breadcrumbs_from_the_root(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory).resolve() / 'project'
            folder.mkdir()
            listing = (await self.get('/api/fs/list', path=str(folder))).json()
        # The anchor is / on POSIX and C:\ on Windows; its label drops the separator.
        anchor = folder.anchor
        self.assertEqual(listing['crumbs'][0], {'label': anchor.rstrip('\\/') or anchor, 'path': anchor})
        self.assertEqual(listing['crumbs'][-1], {'label': 'project', 'path': str(folder)})
        self.assertEqual([step['path'] for step in listing['crumbs']],
                         [str(step) for step in (*reversed(folder.parents), folder)])

    def test_breadcrumbs_follow_windows_drive_and_wsl_share_syntax(self):
        from imprint.api import _crumbs
        self.assertEqual(_crumbs(PureWindowsPath(r'C:\Users\me')), [
            {'label': 'C:', 'path': 'C:\\'},
            {'label': 'Users', 'path': r'C:\Users'},
            {'label': 'me', 'path': r'C:\Users\me'},
        ])
        # A UNC share is one anchor; the server above it cannot be listed.
        self.assertEqual(_crumbs(PureWindowsPath(r'\\wsl.localhost\Ubuntu\home\me')), [
            {'label': r'\\wsl.localhost\Ubuntu', 'path': '\\\\wsl.localhost\\Ubuntu\\'},
            {'label': 'home', 'path': r'\\wsl.localhost\Ubuntu\home'},
            {'label': 'me', 'path': r'\\wsl.localhost\Ubuntu\home\me'},
        ])

    async def test_windows_roots_offer_each_wsl_distribution(self):
        with patch('platform.system', return_value='Windows'), \
                patch('imprint.wsl_paths.distributions', return_value=['Ubuntu', 'Debian']):
            roots = (await self.get('/api/fs/roots')).json()['roots']
        self.assertIn({'label': 'WSL Ubuntu', 'path': '\\\\wsl.localhost\\Ubuntu\\'}, roots)
        self.assertIn({'label': 'WSL Debian', 'path': '\\\\wsl.localhost\\Debian\\'}, roots)


if __name__ == '__main__':
    unittest.main()
