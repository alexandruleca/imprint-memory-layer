"""Where the Privacy Filter runs (ADR 0122): a GPU provider only after a probe
shows the CPU's labels and a real speedup, the verdict cached, and a GPU that
fails at load or run time falls back to the CPU. No GPU or model needed: the
ONNX Runtime module and its sessions are stand-ins."""
import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imprint import model_profiles, privacy_filter as pf  # noqa: E402

CPU, CUDA, DML, COREML, MIGRAPHX = ("CPUExecutionProvider", "CUDAExecutionProvider", "DmlExecutionProvider",
                                    "CoreMLExecutionProvider", "MIGraphXExecutionProvider")


class Tokenizer:
    """One token per word; ``secret`` words get the label 32 (S-secret)."""

    def encode(self, text, add_special_tokens=True):
        spans = [m.span() for m in re.finditer(r"\S+", text)]

        class Encoding:
            offsets = spans
            ids = [32 if text[a:b].startswith("sk-") else 0 for a, b in spans]
        return Encoding()


class Session:
    """Labels each token with its id; ``wrong`` flips every label, ``broken``
    raises from run()."""

    def __init__(self, provider, wrong=False, broken=False):
        self.provider, self.wrong, self.broken, self.runs = provider, wrong, broken, 0

    def get_providers(self):
        return [self.provider] + ([CPU] if self.provider != CPU else [])

    def run(self, names, inputs):
        self.runs += 1
        if self.broken:
            raise RuntimeError("CUDA error: an illegal memory access was encountered")
        ids = inputs["input_ids"][0]
        logits = np.zeros((1, len(ids), 33), dtype=np.float32)
        logits[0, np.arange(len(ids)), (ids + 1) % 33 if self.wrong else ids] = 12.0
        return [logits]


class FakeRuntime(types.SimpleNamespace):
    """The onnxruntime module: ``available`` providers; ``loads`` maps a
    provider to the session it creates, or to an exception."""

    def __init__(self, available, loads=None):
        super().__init__(__version__="9.9.9", created=[], preloaded=0)
        self.available, self.loads = available, loads or {}
        self.GraphOptimizationLevel = types.SimpleNamespace(ORT_ENABLE_BASIC=1)

    def get_available_providers(self):
        return list(self.available)

    def preload_dlls(self):
        self.preloaded += 1

    def SessionOptions(self):
        return types.SimpleNamespace()

    def InferenceSession(self, path, sess_options=None, providers=()):
        first = providers[0]
        name, options = first if isinstance(first, tuple) else (first, {})
        self.created.append((name, options))
        made = self.loads.get(name, Session(name))
        if isinstance(made, Exception):
            raise made
        return made


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, ignore_errors=True))
        pf._model, pf._provider = None, CPU
        self.addCleanup(setattr, pf, "_model", None)
        self.addCleanup(setattr, pf, "_provider", CPU)
        downloaded = patch.object(pf, "_download", return_value=(self.directory, self.directory / "model.onnx",
                                                                 Tokenizer()))
        downloaded.start()
        self.addCleanup(downloaded.stop)

    def runtime(self, available, loads=None, speedup=4.0):
        ort = FakeRuntime(available, loads)
        modules = patch.dict(sys.modules, {"onnxruntime": ort})
        modules.start()
        self.addCleanup(modules.stop)
        # The CPU timing loop takes ``speedup`` times the GPU's.
        clock = iter([0.0, speedup, 10.0, 11.0] * 10)
        timer = patch.object(pf.time, "perf_counter", side_effect=lambda: next(clock))
        timer.start()
        self.addCleanup(timer.stop)
        return ort

    def verdicts(self):
        return json.loads((self.directory / "providers.json").read_text())

    def test_the_cpu_build_runs_on_the_cpu_without_a_probe(self):
        ort = self.runtime([CPU])
        pf._load()
        self.assertEqual(pf.provider(), CPU)
        self.assertEqual([name for name, _ in ort.created], [CPU])
        self.assertFalse((self.directory / "providers.json").exists())

    def test_a_gpu_with_the_cpus_labels_and_a_speedup_is_used_and_remembered(self):
        ort = self.runtime([CUDA, CPU])
        session, _ = pf._load()
        self.assertEqual((pf.provider(), session.provider), (CUDA, CUDA))
        self.assertEqual(ort.preloaded, 1)  # NVIDIA's pip libraries before the CUDA session.
        self.assertEqual(dict(ort.created)[CUDA], {"arena_extend_strategy": "kSameAsRequested", "use_tf32": "0"})
        key = f"{model_profiles.PRIVACY_FILTER_IDENTITY}|{CUDA}|onnxruntime 9.9.9"
        self.assertTrue(self.verdicts()[key]["accepted"])
        # Next start: the verdict stands, no CPU session, no probe.
        pf._model, ort.created = None, []
        pf._load()
        self.assertEqual(([name for name, _ in ort.created], pf.provider()), ([CUDA], CUDA))

    def test_a_gpu_that_labels_differently_is_refused(self):
        self.runtime([CUDA, CPU], loads={CUDA: Session(CUDA, wrong=True)})
        pf._load()
        self.assertEqual(pf.provider(), CPU)
        self.assertEqual(list(self.verdicts().values())[0]["detail"], "labels differ from the CPU's")

    def test_a_gpu_that_is_not_clearly_faster_is_refused_and_not_tried_again(self):
        ort = self.runtime([DML, CPU], speedup=1.2)
        pf._load()
        self.assertEqual(pf.provider(), CPU)
        verdict = list(self.verdicts().values())[0]
        self.assertEqual((verdict["accepted"], verdict["detail"]), (False, "not faster than the CPU"))
        pf._model, ort.created = None, []
        pf._load()
        self.assertEqual([name for name, _ in ort.created], [CPU])

    def test_a_provider_that_does_not_load_falls_back_and_is_tried_again_next_start(self):
        # A CUDA build without a usable driver puts the session on the CPU.
        self.runtime([CUDA, CPU], loads={CUDA: Session(CPU)})
        pf._load()
        self.assertEqual(pf.provider(), CPU)
        self.assertFalse((self.directory / "providers.json").exists())

    def test_an_exception_at_load_tries_the_next_provider(self):
        ort = self.runtime([MIGRAPHX, COREML, CPU], loads={MIGRAPHX: RuntimeError("hipErrorNoDevice")})
        pf._load()
        self.assertEqual(pf.provider(), COREML)
        self.assertEqual(dict(ort.created)[COREML], {"ModelFormat": "MLProgram"})
        self.assertEqual(ort.preloaded, 0)

    def test_providers_are_tried_in_order_of_preference(self):
        self.assertEqual(pf.ACCELERATORS, (CUDA, MIGRAPHX, DML, COREML))
        with patch.dict(sys.modules, {"onnxruntime": FakeRuntime([CPU, COREML, DML, CUDA])}):
            self.assertEqual(pf.accelerators_installed(), [CUDA, DML, COREML])

    def test_a_gpu_failing_at_run_time_falls_back_to_the_cpu(self):
        broken = Session(CUDA, broken=True)
        self.runtime([CUDA, CPU])
        pf._load()
        pf._model = (broken, *pf._model[1:])  # Passed the probe, then the device fails.
        self.assertEqual(pf.spans("the key sk-live-123 is here"), [(8, 19, "secret")])
        self.assertEqual(pf.provider(), CPU)
        self.assertEqual(pf._model[0].provider, CPU)
        self.assertEqual(pf.spans("sk-a"), [(0, 4, "secret")])
        self.assertEqual(broken.runs, 1)

    def test_a_cpu_failure_still_fails(self):
        self.runtime([CPU], loads={CPU: Session(CPU, broken=True)})
        with self.assertRaisesRegex(RuntimeError, "illegal memory"):
            pf.spans("sk-a")

    def test_accelerated_loads_nothing_without_a_gpu_provider(self):
        self.runtime([CPU])
        with patch.object(pf, "_load") as load:
            self.assertFalse(pf.accelerated())
        load.assert_not_called()
        self.runtime([CUDA, CPU])
        self.assertTrue(pf.accelerated())

    def test_the_mark_names_the_model_not_the_provider(self):
        identity = model_profiles.PRIVACY_FILTER_IDENTITY
        self.assertIn(model_profiles.PRIVACY_FILTER_REVISION, identity)
        self.assertIn(model_profiles.PRIVACY_FILTER_FILE, identity)
        self.assertNotIn("Execution", identity)


class OtherOnnxUsersStayOnTheCpu(unittest.TestCase):
    def test_ocr_asks_rapidocr_for_the_cpu_only(self):
        from imprint.extractors import ocr
        captured = {}

        class RapidOCR:
            def __init__(self, params):
                captured.update(params)
        ocr._engine.cache_clear()
        self.addCleanup(ocr._engine.cache_clear)
        with patch.dict(sys.modules, {"rapidocr": types.SimpleNamespace(RapidOCR=RapidOCR)}):
            ocr._engine()
        for key in ("use_cuda", "use_dml", "use_coreml", "use_cann"):
            self.assertIs(captured[f"EngineConfig.onnxruntime.{key}"], False)

    def test_rapidocr_picks_the_cpu_on_a_gpu_build(self):
        try:
            from rapidocr.inference_engine.onnxruntime import provider_config
            from omegaconf import OmegaConf
        except ImportError:
            self.skipTest("rapidocr is not installed")
        import yaml
        import rapidocr
        defaults = yaml.safe_load((Path(rapidocr.__file__).parent / "config.yaml").read_text())
        engine = OmegaConf.create(defaults["EngineConfig"]["onnxruntime"])
        for key in ("use_cuda", "use_dml", "use_coreml", "use_cann"):
            engine[key] = False
        with patch.object(provider_config, "get_available_providers", return_value=[CUDA, DML, COREML, CPU]), \
             patch.object(provider_config, "get_device", return_value="GPU"):
            providers = provider_config.ProviderConfig(engine).get_ep_list()
        self.assertEqual([name for name, _ in providers], [CPU])

    def test_audio_vad_asks_for_the_cpu(self):
        try:
            from faster_whisper import vad
        except ImportError:
            self.skipTest("faster-whisper is not installed")
        calls = []
        fake = FakeRuntime([CUDA, CPU])
        fake.InferenceSession = lambda path, providers=(), sess_options=None: calls.append(providers)
        with patch.dict(sys.modules, {"onnxruntime": fake}):
            vad.SileroVADModel("silero.onnx")
        self.assertEqual(calls, [["CPUExecutionProvider"]])


if __name__ == "__main__":
    unittest.main()
