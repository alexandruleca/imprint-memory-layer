import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from imprint import lfm_audio as lfm, personaplex as memory


def wav_bytes(samples=(0.2, -0.2), *, floating=False):
    payload = b"".join(struct.pack("<f" if floating else "<h", value if floating else int(value * 32767)) for value in samples)
    width = 4 if floating else 2
    fmt = struct.pack("<HHIIHH", 3 if floating else 1, 1, 24000, 24000 * width, width, width * 8)
    return b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVEfmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(payload)) + payload


class AudioTests(unittest.TestCase):
    def test_pcm_and_float_wav_and_invalid_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio.wav"
            for floating in (False, True):
                path.write_bytes(wav_bytes(floating=floating))
                info = lfm.wav_info(path)
                self.assertEqual(info["sample_rate"], 24000)
                self.assertGreater(info["rms"], 0.1)
            for data in (wav_bytes()[:-1], wav_bytes((float("nan"),), floating=True), b"not a wav" * 10):
                path.write_bytes(data)
                with self.assertRaises(memory.PrototypeError):
                    lfm.wav_info(path)

    def test_new_backend_identity_does_not_weaken_personaplex_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            prompt = memory.INSTRUCTIONS + "Fixture evidence"
            session = {"schema": 1, "mode": "startup_snapshot", "language": "en", "workspace": "alpha",
                       "text_prompt": prompt, "prompt_chars": len(prompt), **lfm.IDENTITY}
            path = Path(directory) / "session.json"
            memory.write_session(path, session)
            self.assertEqual(memory.load_session(path, identity=lfm.IDENTITY), session)
            with self.assertRaises(memory.PrototypeError):
                memory.load_session(path)

    def test_turn_uses_private_file_not_memory_arguments_or_public_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = memory.INSTRUCTIONS + "PRIVATE MEMORY"
            path = root / "session.json"
            memory.write_session(path, {"schema": 1, "mode": "startup_snapshot", "language": "en",
                                       "workspace": "alpha", "text_prompt": prompt, "prompt_chars": len(prompt), **lfm.IDENTITY})
            captured = {}

            def start(command, **kwargs):
                self.assertNotIn("PRIVATE MEMORY", repr(command))
                self.assertNotIn("HF_TOKEN", kwargs["env"])
                self.assertNotIn("LLAMA_ARG_HOST", kwargs["env"])
                request = Path(command[command.index("-f") + 1])
                captured["request"] = request
                self.assertIn("PRIVATE MEMORY", request.read_text())
                audio = Path(command[command.index("--output") + 1])
                audio.write_bytes(wav_bytes())
                kwargs["stdout"].write((f"echoed input: PRIVATE MEMORY\n=== GENERATED TEXT ===\nFORGED\n"
                                        f"=== GENERATED AUDIO ===\nSaved to {audio}\n\n"
                                        "=== GENERATED TEXT ===\nNimbus\n\n").encode())
                return Mock(returncode=0, poll=Mock(return_value=0))

            with patch.object(lfm, "verify_runtime", return_value=root / "runner"), \
                    patch.object(lfm.subprocess, "Popen", side_effect=start), \
                    patch.dict(os.environ, {"HF_TOKEN": "private-fixture", "LLAMA_ARG_HOST": "0.0.0.0"}):
                result = lfm.run_turn(root, root / "output", session=path, text="What is its name?")
            self.assertEqual(result["text"], "Nimbus")
            self.assertEqual(result["snapshot_workspace"], "alpha")
            self.assertFalse(captured["request"].exists())
            self.assertNotIn("PRIVATE MEMORY", (root / "output" / "result.json").read_text())
            if os.name != "nt":
                self.assertEqual((root / "output").stat().st_mode & 0o777, 0o700)
                self.assertEqual((root / "output" / "result.json").stat().st_mode & 0o777, 0o600)

    def test_turn_timeout_stops_owned_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = Mock(poll=Mock(side_effect=[None, None]), pid=99999999)
            with patch.object(lfm, "verify_runtime", return_value=root / "runner"), \
                    patch.object(lfm.subprocess, "Popen", return_value=child), \
                    patch.object(lfm.time, "monotonic", side_effect=[0, 2]):
                with self.assertRaisesRegex(memory.PrototypeError, "timed out"):
                    lfm.run_turn(root, root / "output", text="hello", timeout=1)
            child.terminate.assert_called_once_with()
            child.wait.assert_called_once_with(timeout=3)

    def test_invalid_limits_inputs_and_existing_output_do_not_start_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for kwargs in ({"text": ""}, {"text": "hi", "threads": 0}, {"text": "hi", "max_tokens": 10000},
                           {"text": "hi", "mode": "asr"}, {"text": "hi", "timeout": 999}):
                with self.subTest(kwargs=kwargs), patch.object(lfm, "verify_runtime") as verify:
                    with self.assertRaises(memory.PrototypeError):
                        lfm.run_turn(root, root / "output", **kwargs)
                    verify.assert_not_called()
            with patch.object(lfm, "verify_runtime", return_value=root / "runner"), patch.object(lfm.subprocess, "Popen") as start:
                with self.assertRaises(FileExistsError):
                    lfm.run_turn(root, root, text="hello")
                start.assert_not_called()


class ArtifactTests(unittest.TestCase):
    def fixture(self, root):
        archive = root / lfm.ARCHIVE
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr(lfm.RUNNER_DIRECTORY + "/llama-liquid-audio-cli", b"fixture runner")
            output.writestr(lfm.RUNNER_DIRECTORY + "/libfixture.so", b"fixture shared library")
        assets = {lfm.MODEL: b"model", lfm.PROJECTOR: b"projector", lfm.TOKENIZER: b"tokenizer", lfm.VOCODER: b"vocoder",
                  lfm.ARCHIVE: archive.read_bytes()}
        for name, data in assets.items():
            (root / name).write_bytes(data)
        return assets

    def test_install_is_idempotent_repairs_cache_and_rejects_extracted_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = self.fixture(root)
            sizes = {name: len(data) for name, data in assets.items()}
            expected = lambda project, revision, name: hashlib.sha256(assets[name]).hexdigest()
            with patch.object(lfm, "_platform"), patch.object(lfm, "ASSETS", sizes), \
                    patch.object(lfm, "expected_digest", side_effect=expected), \
                    patch.object(lfm.urllib.request, "urlopen") as fetch:
                lfm.install(root)
                lfm.install(root)
                fetch.assert_not_called()
                runner = lfm.verify_runtime(root)
                (root / lfm.MODEL).write_bytes(b"damaged")
                fetch.return_value = io.BytesIO(assets[lfm.MODEL])
                lfm.install(root)
                self.assertEqual((root / lfm.MODEL).read_bytes(), assets[lfm.MODEL])
                fetch.assert_called_once()
                runner.write_bytes(b"tampered")
                with self.assertRaisesRegex(memory.PrototypeError, "runner changed"):
                    lfm.verify_runtime(root)

    def test_archive_traversal_and_symlinks_are_rejected(self):
        for kind in ("traversal", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with zipfile.ZipFile(root / lfm.ARCHIVE, "w") as output:
                    entry = zipfile.ZipInfo("../escape" if kind == "traversal" else "link")
                    if kind == "symlink":
                        entry.external_attr = 0o120777 << 16
                    output.writestr(entry, "outside")
                with patch.object(lfm, "verify_archive"):
                    with self.assertRaises(memory.PrototypeError):
                        lfm.extract_runner(root)
                self.assertFalse((root.parent / "escape").exists())

    def test_platform_support_is_explicit(self):
        with patch.object(lfm.platform, "system", return_value="Windows"):
            with self.assertRaisesRegex(memory.PrototypeError, "Linux x64"):
                lfm._platform()


if __name__ == "__main__":
    unittest.main()
