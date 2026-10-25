import hashlib
import importlib.util
import plistlib
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


def macho(cpu, identifier=None, specials=()):
    """A 64-bit Mach-O slice; with an identifier it carries a SHA-256
    CodeDirectory whose special slots hold `specials` (slot 1 first)."""
    if identifier is None:
        return struct.pack('<IIIIIIII', 0xfeedfacf, cpu, 0, 2, 0, 0, 0, 0)
    ident = identifier.encode() + b'\0'
    hashes = b''.join(reversed(specials))  # slot n first, slot 1 last
    hash_offset = 44 + len(ident) + len(hashes)
    directory = struct.pack('>IIIIIIIIIBBBBI', 0xfade0c02, hash_offset, 0x20001, 0, hash_offset, 44,
                            len(specials), 0, 0, 32, 2, 0, 12, 0) + ident + hashes
    blob = struct.pack('>IIIII', 0xfade0cc0, 20 + len(directory), 1, 0, 20) + directory
    header = struct.pack('<IIIIIIII', 0xfeedfacf, cpu, 0, 2, 1, 16, 0, 0)
    return header + struct.pack('<IIII', 0x1D, 16, 48, len(blob)) + blob


def fat(*slices):
    data = bytearray(struct.pack('>II', 0xcafebabe, len(slices)))
    offset = 8 + 20 * len(slices)
    for cpu, image in slices:
        data += struct.pack('>IIIII', cpu, 0, offset, len(image), 0)
        offset += len(image)
    return bytes(data) + b''.join(image for _, image in slices)


AMD64, ARM64 = 0x01000007, 0x0100000C


def bundle(path, identifier, *, signed=True, signer=None, stale_plist=False):
    """An Electron-style .app whose executable is signed (or not) as a bundle."""
    contents = path / 'Contents'
    (contents / 'MacOS').mkdir(parents=True)
    info = plistlib.dumps({'CFBundleExecutable': 'Imprint', 'CFBundleIdentifier': identifier})
    (contents / 'Info.plist').write_bytes(info)
    if not signed:  # What electron-builder ships when it skips signing: a linker signature only.
        (contents / 'MacOS/Imprint').write_bytes(fat((AMD64, macho(AMD64)), (ARM64, macho(ARM64, 'Electron'))))
        return
    resources = contents / '_CodeSignature/CodeResources'
    resources.parent.mkdir()
    resources.write_bytes(plistlib.dumps({'files': {}}))
    sealed_plist = hashlib.sha256(info + (b' ' if stale_plist else b'')).digest()
    specials = (sealed_plist, b'\0' * 32, hashlib.sha256(resources.read_bytes()).digest())
    name = signer or identifier
    (contents / 'MacOS/Imprint').write_bytes(fat((AMD64, macho(AMD64, name, specials)), (ARM64, macho(ARM64, name, specials))))


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

    def test_macos_desktop_bundle_must_be_signed_as_a_bundle(self):
        # v0.22.1 shipped the linker-only state: macOS privacy consent could
        # not read the sealed microphone usage text and the app crashed.
        cases = {
            'signed': ({}, None),
            'linker signature only': ({'signed': False}, 'Unsigned macOS bundle'),
            'signed under the binary name': ({'signer': 'Electron'}, "signed as 'Electron'"),
            'Info.plist edited after signing': ({'stale_plist': True}, 'does not seal its Info.plist'),
        }
        for name, (options, error) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.tree(root)
                app = root / 'bin/imprint-ui.app'
                bundle(app, 'tech.brightspaces.imprint', **options)
                bundle(app / 'Contents/Frameworks/Imprint Helper.app', 'tech.brightspaces.imprint.helper')
                if error is None:
                    VALIDATOR.validate(root, 'darwin/amd64')
                else:
                    with self.assertRaisesRegex(ValueError, error):
                        VALIDATOR.validate(root, 'darwin/amd64')

    def test_unsigned_macos_helper_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.tree(root)
            app = root / 'bin/imprint-ui.app'
            bundle(app, 'tech.brightspaces.imprint')
            bundle(app / 'Contents/Frameworks/Imprint Helper.app', 'tech.brightspaces.imprint.helper', signed=False)
            with self.assertRaisesRegex(ValueError, 'Unsigned macOS bundle.*Imprint Helper'):
                VALIDATOR.validate(root, 'darwin/amd64')

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
