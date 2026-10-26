"""Both macOS distribution entry points need a microphone purpose (ADR 0075)."""
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
                purpose = info.get('NSMicrophoneUsageDescription')
                self.assertIsInstance(purpose, str)
                self.assertTrue(purpose.strip())


if __name__ == '__main__':
    unittest.main()
