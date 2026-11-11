"""Both macOS distribution entry points need a microphone purpose (ADR 0075) and a
system audio purpose: TCC asks for the enclosing app."""
import json
import plistlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MacMicrophoneBundleTests(unittest.TestCase):
    def test_installer_wrapper_and_standalone_shell_declare_microphone_purpose(self):
        # The .pkg encloses the signed Electron shell in another Imprint.app.
        # Checking only electron-builder misses the enclosing app TCC can inspect.
        wrapper = plistlib.loads((ROOT / 'installers/macos/Imprint.app.template/Contents/Info.plist').read_bytes())
        shell = json.loads((ROOT / 'ui-electron/package.json').read_text())['build']['mac']['extendInfo']
        for name, info in [('installer wrapper', wrapper), ('Electron shell', shell)]:
            with self.subTest(bundle=name):
                for key in ('NSMicrophoneUsageDescription', 'NSAudioCaptureUsageDescription'):
                    purpose = info.get(key)
                    self.assertIsInstance(purpose, str, key)
                    self.assertTrue(purpose.strip(), key)

    def test_the_desktop_shell_and_the_installer_wrapper_have_their_own_identifiers(self):
        # One identifier for two nested bundles would leave macOS unsure which app a permission belongs to.
        wrapper = plistlib.loads((ROOT / 'installers/macos/Imprint.app.template/Contents/Info.plist').read_bytes())
        shell = json.loads((ROOT / 'ui-electron/package.json').read_text())['build']['appId']
        self.assertEqual(wrapper['CFBundleIdentifier'], 'com.alexandruleca.imprint')
        self.assertEqual(shell, 'com.alexandruleca.imprint.desktop')


if __name__ == '__main__':
    unittest.main()
