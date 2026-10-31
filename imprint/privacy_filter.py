"""OpenAI Privacy Filter spans for opt-in secret redaction (ADR 0060).

Privacy Filter labels every token of a text in one pass and decodes the
labels into BIOES spans with a constrained Viterbi pass. Imprint keeps only
its ``secret`` and ``account_number`` spans: names, emails, addresses,
phone numbers, dates and URLs are memory content, not credentials.

The pinned ONNX export downloads on first use and is checked against its
SHA-256 digests on every load. It runs on a GPU when the installed ONNX
Runtime build offers one that passes a probe (ADR 0122): CUDA on NVIDIA,
MIGraphX on AMD with ROCm, DirectML on Windows, Core ML on Apple Silicon.
The probe labels fixed texts on that provider and on the CPU and keeps the
GPU only when the labels are identical and it is clearly faster; providers
that cannot run some of the model's operators hand them to the CPU, which
can be slower than the CPU alone. The verdict is remembered per model,
provider and ONNX Runtime version. A GPU that fails at load or run time
falls back to the CPU with ``model.threads`` threads.

Long texts are labelled in windows of ``WINDOW`` tokens, each with
``CONTEXT`` tokens of context on both sides (the model's attention band), so
memory stays bounded while every labelled token sees its neighbourhood.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import numpy as np

from . import model_profiles as profiles

# Label ids of the pinned export: 0 is background; category c, in this
# order, is B, I, E, S at 1 + 4c to 4 + 4c (its config.json id2label).
CATEGORIES = ("account_number", "private_address", "private_date", "private_email",
              "private_person", "private_phone", "private_url", "secret")
KINDS = {"account_number": "account-number", "secret": "secret"}
WINDOW = 1024
CONTEXT = 128
_NEG = -1e9


def _decoder_tables():
    """Viterbi start, end and transition scores that allow only valid BIOES
    paths. Every bias is zero: the default operating point of the pinned
    revision's viterbi_calibration.json."""
    tags = ["O"] + [tag for _ in CATEGORIES for tag in "BIES"]
    categories = [None] + [index for index in range(len(CATEGORIES)) for _ in "BIES"]
    start = np.array([0.0 if tag in "OBS" else _NEG for tag in tags])
    end = np.array([0.0 if tag in "OES" else _NEG for tag in tags])
    transitions = np.full((len(tags), len(tags)), _NEG)
    for i, (tag, category) in enumerate(zip(tags, categories)):
        for j, (following, next_category) in enumerate(zip(tags, categories)):
            if (following in "OBS") if tag in "OES" else (following in "IE" and next_category == category):
                transitions[i, j] = 0.0
    return start, end, transitions


_START, _END, _TRANSITIONS = _decoder_tables()
_lock = threading.Lock()
_model = None
_log = logging.getLogger(__name__)

CPU = "CPUExecutionProvider"
# GPU providers in order of preference. An installation holds one ONNX
# Runtime build (ADR 0032), which offers at most one of them besides Core ML.
ACCELERATORS = ("CUDAExecutionProvider", "MIGraphXExecutionProvider", "DmlExecutionProvider",
                "CoreMLExecutionProvider")
SPEEDUP = 1.5  # A GPU must label the probe texts at least this much faster.
PROBE_TOKENS = (64, 580, 1100)  # Distinct lengths around a mean memory (581); providers that compile per shape pay for it.
_PROBE_TEXT = ("Deploy notes for release {n}: the staging host db-{n}.internal reads its config from "
               "/etc/app/{n}.yaml, the API key is sk-test-{n}abcdef0123456789 and the card on file ends "
               "in 4242 4242 4242 {n:04d}. Maria Lopez reviewed it on 2026-03-{d:02d}; call +1 555 01{n:02d}. ")
_provider = CPU


def provider() -> str:
    """The execution provider the loaded model runs on."""
    return _provider


def _options(ort):
    from . import config
    options = ort.SessionOptions()
    options.enable_cpu_mem_arena = False  # Bounded memory in long-running servers.
    options.enable_mem_pattern = False  # Also required by DirectML.
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    options.log_severity_level = 4  # Failures raise; ONNX Runtime's log adds nothing.
    options.intra_op_num_threads = config.CPU_THREADS
    options.inter_op_num_threads = 1
    return options


_PROVIDER_OPTIONS = {
    # TF32 matrix products round differently from the CPU and could flip a label.
    "CUDAExecutionProvider": {"arena_extend_strategy": "kSameAsRequested", "use_tf32": "0"},
    "CoreMLExecutionProvider": {"ModelFormat": "MLProgram"},
}


def _session(ort, path: Path, name: str):
    """A session of the model on ``name`` (the CPU takes the operators it
    cannot run). Raises when ONNX Runtime does not put the model on it:
    a CUDA build without a usable driver silently falls back to the CPU."""
    if name == "CUDAExecutionProvider" and hasattr(ort, "preload_dlls"):
        ort.preload_dlls()  # NVIDIA's pip-installed CUDA and cuDNN libraries.
    providers = [CPU] if name == CPU else [(name, _PROVIDER_OPTIONS.get(name, {})), CPU]
    session = ort.InferenceSession(str(path), sess_options=_options(ort), providers=providers)
    if session.get_providers()[0] != name:
        raise RuntimeError(f"{name} did not load")
    return session


def accelerators_installed() -> list[str]:
    """The GPU providers the installed ONNX Runtime build offers, without
    loading the model."""
    try:
        import onnxruntime as ort
        available = ort.get_available_providers()
    except Exception:
        return []
    return [name for name in ACCELERATORS if name in available]


def _probe_ids(tokenizer) -> list[list[int]]:
    texts, n = [], 0
    for size in PROBE_TOKENS:
        ids: list[int] = []
        while len(ids) < size:
            n += 1
            ids += tokenizer.encode(_PROBE_TEXT.format(n=n, d=n % 28 + 1), add_special_tokens=False).ids
        texts.append(ids[:size])
    return texts


def probe(cpu, accelerated, tokenizer) -> dict:
    """Whether ``accelerated`` labels the probe texts exactly as ``cpu`` does
    and at least ``SPEEDUP`` times faster. Each session labels one text
    first, then is timed over all of them."""
    texts = _probe_ids(tokenizer)
    for ids in texts:
        if not np.array_equal(_log_probabilities(cpu, ids).argmax(axis=1),
                              _log_probabilities(accelerated, ids).argmax(axis=1)):
            return {"accepted": False, "detail": "labels differ from the CPU's"}
    seconds = {}
    for name, session in (("cpu", cpu), ("gpu", accelerated)):
        _log_probabilities(session, texts[0])
        started = time.perf_counter()
        for _ in range(2):
            for ids in texts[::-1]:
                _log_probabilities(session, ids)
        seconds[name] = time.perf_counter() - started
    accepted = seconds["gpu"] * SPEEDUP <= seconds["cpu"]
    return {"accepted": accepted, "cpu_seconds": round(seconds["cpu"], 4), "gpu_seconds": round(seconds["gpu"], 4),
            "detail": "faster than the CPU" if accepted else "not faster than the CPU"}


def _verdict_key(ort, name: str) -> str:
    return f"{profiles.PRIVACY_FILTER_IDENTITY}|{name}|onnxruntime {ort.__version__}"


def _verdicts_path(directory: Path) -> Path:
    return directory / "providers.json"


def _read_verdicts(directory: Path) -> dict:
    try:
        verdicts = json.loads(_verdicts_path(directory).read_text(encoding="utf-8"))
        return verdicts if isinstance(verdicts, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_verdict(directory: Path, key: str, verdict: dict) -> None:
    verdicts = {**_read_verdicts(directory), key: verdict}
    path = _verdicts_path(directory)
    try:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(verdicts, indent=1, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass  # The probe runs again next time.


def _accelerated_session(ort, path: Path, directory: Path, tokenizer):
    """A session on the first GPU provider that passes the probe, with its
    name; ``None`` when none does."""
    verdicts = _read_verdicts(directory)
    cpu = None
    for name in accelerators_installed():
        key = _verdict_key(ort, name)
        verdict = verdicts.get(key)
        if verdict is not None and not verdict.get("accepted"):
            continue
        try:
            session = _session(ort, path, name)
            if verdict is None:
                cpu = cpu or _session(ort, path, CPU)
                verdict = probe(cpu, session, tokenizer)
                _write_verdict(directory, key, verdict)
                _log.info("Privacy Filter on %s: %s", name, verdict)
        except Exception as error:
            # A failure is not remembered: drivers and libraries change.
            _log.warning("Privacy Filter cannot run on %s, trying the next provider: %s", name, error)
            continue
        if verdict.get("accepted"):
            return session, name
    return None


def _download():
    """The model's directory, its ONNX file and its tokenizer, downloaded
    once and checked against the pinned digests on every load."""
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    from . import config
    from .downloads import verify_archive
    directory = config.get_data_dir() / "models" / "privacy-filter" / profiles.PRIVACY_FILTER_REVISION[:12]
    paths = {}
    for name, digest in profiles.PRIVACY_FILTER_SHA256.items():
        paths[name] = Path(hf_hub_download(profiles.PRIVACY_FILTER_REPO, name,
                                           revision=profiles.PRIVACY_FILTER_REVISION,
                                           local_dir=directory, token=config.hub_token()))
        verify_archive(paths[name], digest)
    return directory, paths[profiles.PRIVACY_FILTER_FILE], Tokenizer.from_file(str(paths["tokenizer.json"]))


def _load():
    global _model, _provider
    with _lock:
        if _model is None:
            import onnxruntime as ort
            directory, model, tokenizer = _download()
            found = _accelerated_session(ort, model, directory, tokenizer)
            session, name = found or (_session(ort, model, CPU), CPU)
            _model, _provider = (session, tokenizer, model), name
        return _model[:2]


def _fall_back_to_cpu(failed) -> object:
    """The CPU session that replaces ``failed``, a GPU session that could not
    run; the next caller of a session that already fell back gets it too.
    ``failed`` itself when it ran on the CPU."""
    global _model, _provider
    with _lock:
        if _model is None:
            return failed
        if _model[0] is failed and _provider != CPU:
            import onnxruntime as ort
            _log.warning("Privacy Filter failed on %s; it runs on the CPU until Imprint restarts", _provider)
            _model = _session(ort, _model[2], CPU), _model[1], _model[2]
            _provider = CPU
        return _model[0]


def accelerated() -> bool:
    """Whether the Privacy Filter runs on a GPU here. Loads the model only
    when the installed ONNX Runtime offers a GPU provider."""
    if not accelerators_installed():
        return False
    _load()
    return _provider != CPU


def _log_probabilities(session, ids: list[int]) -> np.ndarray:
    rows = []
    for start in range(0, len(ids), WINDOW):
        left, right = max(0, start - CONTEXT), min(len(ids), start + WINDOW + CONTEXT)
        window = np.array([ids[left:right]], dtype=np.int64)
        logits = session.run(["logits"], {"input_ids": window, "attention_mask": np.ones_like(window)})[0][0]
        logits = logits[start - left:start - left + min(WINDOW, len(ids) - start)].astype(np.float64)
        peak = logits.max(axis=1, keepdims=True)
        rows.append(logits - peak - np.log(np.exp(logits - peak).sum(axis=1, keepdims=True)))
    return np.concatenate(rows)


def _decode(log_probabilities: np.ndarray) -> list[int]:
    score = log_probabilities[0] + _START
    back = np.empty((len(log_probabilities) - 1, len(_START)), dtype=np.int16)
    for step in range(1, len(log_probabilities)):
        candidates = score[:, None] + _TRANSITIONS
        back[step - 1] = candidates.argmax(axis=0)
        score = candidates.max(axis=0) + log_probabilities[step]
    path = [int((score + _END).argmax())]
    for step in range(len(back) - 1, -1, -1):
        path.append(int(back[step, path[-1]]))
    return path[::-1]


def labelled_spans(offsets: list[tuple[int, int]], path: list[int]) -> list[tuple[int, int, str]]:
    """Character spans of the redacted categories in a decoded label path."""
    found, begin = [], 0
    for index, label in enumerate(path):
        if not label:
            continue
        category, tag = CATEGORIES[(label - 1) // 4], "BIES"[(label - 1) % 4]
        if tag in "BS":
            begin = index
        if tag in "ES" and category in KINDS:
            found.append((offsets[begin][0], offsets[index][1], KINDS[category]))
    return found


def spans(text: str) -> list[tuple[int, int, str]]:
    """``(start, end, kind)`` of the secrets and account numbers in ``text``."""
    session, tokenizer = _load()
    encoding = tokenizer.encode(text, add_special_tokens=False)
    if not encoding.ids:
        return []
    try:
        log_probabilities = _log_probabilities(session, encoding.ids)
    except Exception:
        cpu = _fall_back_to_cpu(session)
        if cpu is session:
            raise
        log_probabilities = _log_probabilities(cpu, encoding.ids)
    return labelled_spans(encoding.offsets, _decode(log_probabilities))
