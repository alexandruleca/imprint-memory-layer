import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('release_validation', Path(__file__).resolve().parents[1] / 'scripts' / 'validate-release.py')
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def executable(machine):
    header = bytearray(64)
    header[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<H', header, 18, machine)
    return header


class ReleaseValidationTests(unittest.TestCase):
    def test_personaplex_test_dependencies_are_never_distributed(self):
        for name in ('personaplex-test.txt', 'personaplex-test.lock.txt'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.tree(root)
                (root / 'requirements' / name).write_text('test-only dependencies')
                with self.assertRaisesRegex(ValueError, 'Development state.*personaplex-test'):
                    VALIDATOR.validate(root, 'linux/amd64')

    def tree(self, root):
        for rel in ('imprint/__main__.py', 'imprint/desktop_server.py', 'imprint/wsl_bridge.py', 'imprint/wsl_paths.py', 'requirements/base.txt', 'requirements/base.lock.txt', 'requirements/cpu.txt', 'requirements/cpu.lock.txt', 'requirements/gpu.txt',
                    'requirements/llm.txt', 'requirements/gpu.lock.txt', 'requirements/llm.lock.txt',
                    'requirements/llm-gpu.txt', 'requirements/llm-gpu.lock.txt',
                    'requirements/runtime-manifest.json', 'LICENSE', 'licenses/uv/LICENSE-APACHE',
                    'licenses/uv/LICENSE-MIT', 'imprint/ui/out/index.html', 'imprint/ui/out/_next/app.js'):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')
        for rel in ('bin/imprint', 'bin/uv'):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(executable(62))
            path.chmod(0o755)

    def test_required_payload_and_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.tree(root)
            VALIDATOR.validate(root, 'linux/amd64')
            with self.assertRaisesRegex(ValueError, 'Wrong architecture'):
                VALIDATOR.validate(root, 'linux/arm64')
            (root / 'imprint/ui/out/index.html').unlink()
            with self.assertRaisesRegex(ValueError, 'Missing/empty'):
                VALIDATOR.validate(root, 'linux/amd64')

    def test_desktop_service_entry_point_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.tree(root)
            (root / 'imprint/desktop_server.py').unlink()
            with self.assertRaisesRegex(ValueError, 'Missing/empty.*desktop_server'):
                VALIDATOR.validate(root, 'linux/amd64')

    def test_wrong_electron_and_private_state_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.tree(root)
            (root / 'bin/imprint-ui').write_bytes(executable(183))
            with self.assertRaisesRegex(ValueError, 'AppImage architecture'):
                VALIDATOR.validate(root, 'linux/amd64')

            (root / 'bin/imprint-ui').unlink()
            (root / 'imprint/node_modules').mkdir()
            with self.assertRaisesRegex(ValueError, 'Development state'):
                VALIDATOR.validate(root, 'linux/amd64')

    def test_wsl_bridge_and_private_selections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.tree(root)
            bridge = root / 'imprint/wsl_bridge.py'
            bridge.unlink()
            with self.assertRaisesRegex(ValueError, 'Missing/empty.*wsl_bridge'):
                VALIDATOR.validate(root, 'linux/amd64')
            bridge.write_text('fixture')
            (root / '.wsl-integrations.txt').write_text('Ubuntu|codex\n')
            with self.assertRaisesRegex(ValueError, 'User state'):
                VALIDATOR.validate(root, 'linux/amd64')
            (root / '.wsl-integrations.txt').unlink()
            (root / '.wsl-integrations.txt.imprint-123.bak').write_text('Ubuntu|codex\n')
            with self.assertRaisesRegex(ValueError, 'User state'):
                VALIDATOR.validate(root, 'linux/amd64')

    def test_private_uninstall_journal_is_never_distributed(self):
        for name in ('.wsl-registrations.json', '.wsl-registrations.json.imprint-123.bak', 'uninstall-cleanup.log'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.tree(root)
                (root / name).write_text('private fixture')
                with self.assertRaisesRegex(ValueError, 'User state'):
                    VALIDATOR.validate(root, 'linux/amd64')

    def test_required_voice_bundle_and_model_exclusion(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.tree(root)
            with self.assertRaises(ValueError):
                VALIDATOR.validate(root, 'linux/amd64', require_voice=True)
            engine = root / 'imprint/voice-engine'; engine.mkdir()
            binary = engine / 'worker'; binary.write_bytes(executable(62)); binary.chmod(0o755)
            (engine / 'NOTICE.md').write_text('fixture notices')
            (engine / 'licenses/gcc').mkdir(parents=True)
            for name in ('COPYING3', 'COPYING.RUNTIME'):
                (engine / 'licenses/gcc' / name).write_text('fixture runtime license')
            # Source/patch/binary verification is exercised by test_voice_bundle.
            with patch('imprint.voice_runtime.verify_engine', return_value=binary) as verify:
                VALIDATOR.validate(root, 'linux/amd64', require_voice=True)
                verify.assert_called_once_with(engine, 'linux-x64')
                (engine / 'weights.gguf').write_bytes(b'must stay outside the release')
                with self.assertRaisesRegex(ValueError, 'weights must not be bundled'):
                    VALIDATOR.validate(root, 'linux/amd64', require_voice=True)
                (engine / 'weights.gguf').unlink()
                binary.write_bytes(executable(183))
                with self.assertRaisesRegex(ValueError, 'engine architecture'):
                    VALIDATOR.validate(root, 'linux/amd64', require_voice=True)
            with self.assertRaisesRegex(ValueError, 'not supported'):
                VALIDATOR.validate(root, 'linux/arm64', require_voice=True)
