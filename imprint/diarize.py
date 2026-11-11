"""Speaker diarization on this computer: who spoke when in one channel.

``diarize(samples)`` takes 16 kHz mono float32 samples in [-1, 1] and
returns speaker turns ``[(start_s, end_s, speaker), ...]`` sorted by start,
with speakers numbered 0, 1, ... in the order they first speak. Turns of
different speakers may overlap (overlapped speech). Nothing is loaded on
import; the two pinned models download on first use (``ensure_models``).

This is a numpy and ONNX Runtime port of pyannote.audio's
``SpeakerDiarization`` pipeline with the pyannote/speaker-diarization-3.1
configuration, ported from pyannote.audio 3.4.0 (git commit
853b2ab42c3ccd9ec898459d0ad24adc65167b3d, the last 3.x release, which runs
that configuration; pyannote.audio is MIT licensed, notice below). 3.4.0
differs from 3.1.1 (6a972c0c4e95de04637d7221208736c64c8b972a) only in placing
frames by the segmentation model's receptive field (3.2.0) and in skipping
embeddings when at most one speaker is allowed (3.3.0). The pipeline:

1. Segmentation: pyannote/segmentation-3.0 on 10 s windows every 1 s. Its
   powerset output (7 classes) becomes the activity of up to 3 local speakers
   per window, frame by frame (16.875 ms frames).
2. Embeddings: WeSpeaker ResNet34-LM (VoxCeleb) for each active (window, local
   speaker): the window's Kaldi fbank, centred over the window, through the
   ResNet, then statistics pooling weighted by the frames where only that
   speaker talks (``embedding_exclude_overlap``; all of its frames when fewer
   than 3 are clean).
3. Clustering: agglomerative clustering with centroid linkage on L2-normalised
   embeddings, cut at 0.7045654963945799; clusters smaller than 12 (fewer for
   few embeddings) join the large cluster with the nearest centroid; then each
   embedding goes to the nearest centroid. ``min_speakers``/``max_speakers``
   walk the dendrogram for a cut with that many large clusters.
4. Reconstruction: each window's speakers are summed per cluster over the
   overlapping windows; the instantaneous number of speakers (averaged over
   windows and rounded) picks that many top clusters per frame; runs of active
   frames become turns.

Deviations from pyannote, each for a reason:

- Models: ONNX exports by k2-fsa (sherpa-onnx) of the same weights pyannote
  uses in PyTorch. The embedding export pools over all frames itself, so on
  load its graph also returns the ResNet's frame-level output (a protobuf edit
  of the bytes, no ``onnx`` package), and the weighted pooling and the final
  linear layer run here as in pyannote. Its trailing ``mean_vec`` subtraction
  is all zeros and is skipped with the rest of the export's pooling.
- The ResNet runs once per window instead of once per (window, speaker): its
  output does not depend on the speaker's weights. Windows without speech are
  not embedded: pyannote discards those embeddings. Fbank frames of windows
  1 s apart coincide (100 frames), so log-mel energies are computed once per
  batch of windows; each window still centres its own 998 frames.
- The instantaneous speaker count is capped by the number of clusters:
  pyannote pads missing clusters with silent speakers, which become extra,
  "phantom" speaker labels in overlapped speech.
- Turns are clipped to the audio's end (the last window is zero padded).

Validated against pyannote.audio 3.4.0 with its PyTorch models; the numbers
and the method are in the ADR that adopts this module.

Clustering reproduces SciPy's ``linkage(method="centroid")`` and
``fcluster(criterion="distance")`` (BSD-3-Clause), including the numbering
of flat clusters, without SciPy: a nearest-neighbour cache with lower bounds
(after Müllner's generic algorithm, as SciPy does) keeps memory linear in the
number of embeddings.

pyannote.audio, MIT License, Copyright (c) 2020- CNRS:
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions: The above copyright
notice and this permission notice shall be included in all copies or
substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS",
WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR
THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
from __future__ import annotations

from functools import cache
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path
import threading

import numpy as np

from .audio_ingest import ModelSpec, ensure_model

RATE = 16000
# pyannote/segmentation-3.0 (MIT, CNRS), exported to ONNX by k2-fsa.
SEGMENTATION = ModelSpec("csukuangfj/sherpa-onnx-pyannote-segmentation-3-0",
                         "9403a6902bb58e3d5ae8c7e77c3422de279db2e0", {
    "model.onnx": (5992913, "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079"),
})
# WeSpeaker ResNet34-LM trained on VoxCeleb (CC-BY-4.0), the embedding model
# of pyannote/speaker-diarization-3.1, exported to ONNX by WeSpeaker/k2-fsa.
EMBEDDING = ModelSpec("csukuangfj/speaker-embedding-models", "0743f301363dec56491a490f6d6cbc9d67f9a3bf", {
    "wespeaker_en_voxceleb_resnet34_LM.onnx": (26530550,
                                               "e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012"),
})
MODELS = (SEGMENTATION, EMBEDDING)

# Change when the output for the same audio changes (``profile``).
PIPELINE_VERSION = 1
# pyannote/speaker-diarization-3.1's configuration.
STEP = 0.1                      # window step, a share of the window
THRESHOLD = 0.7045654963945799  # clustering.threshold
MIN_CLUSTER_SIZE = 12           # clustering.min_cluster_size
# segmentation.min_duration_off is 0.0 there: same-speaker gaps stay.

# WeSpeaker's fbank as pyannote's WeSpeakerResNet34 computes it with
# torchaudio.compliance.kaldi.fbank: 80 mel bins, 25 ms frames every 10 ms,
# Hamming window, no dither, no energy, samples scaled by 1 << 15, then the
# mean over the window's frames removed. The rest are Kaldi's defaults.
MEL_BINS = 80
FRAME_LENGTH = 400              # 25 ms
FRAME_SHIFT = 160               # 10 ms
FFT_SIZE = 512                  # the frame rounded up to a power of two
PREEMPHASIS = 0.97
LOW_FREQUENCY = 20.0
# pyannote's ``min_num_samples`` for this model: one fbank frame.
MIN_EMBEDDING_SAMPLES = 400

THREADS = 4                     # ONNX Runtime threads, as transcription uses
# Windows per model run. Larger batches were barely faster on a CPU and each
# run's temporaries grow with them (SincNet's first layer: 5 MB a window).
SEGMENTATION_BATCH = 8
EMBEDDING_BATCH = 4

_inference = threading.Lock()
_models = None


def profile() -> str:
    """What the turns depend on besides the audio: transcripts made with
    another profile are out of date."""
    from .onnx_runtime import installed_version
    payload = [PIPELINE_VERSION, [(spec.repo, spec.revision, sorted(spec.files)) for spec in MODELS],
               STEP, THRESHOLD, MIN_CLUSTER_SIZE, installed_version() or "unavailable"]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def ensure_models(progress=None) -> tuple[Path, Path]:
    """Download (once) and verify both pinned models; the paths of the
    segmentation and the embedding model. ``progress`` is
    ``audio_ingest.ensure_model``'s: it replaces the job's progress record
    and may raise to cancel the download."""
    paths = []
    for spec in MODELS:
        root = ensure_model(spec, progress)
        paths.append(root / next(iter(spec.files)))
    return paths[0], paths[1]


def diarize(samples, *, min_speakers: int | None = None, max_speakers: int | None = None,
            progress=None) -> list[tuple[float, float, int]]:
    """Speaker turns of one channel: ``[(start_s, end_s, speaker), ...]``.

    ``min_speakers``/``max_speakers`` bound the number of speakers (equal
    values fix it). ``progress(done, total)`` is called with seconds of audio
    analysed; an exception it raises cancels and propagates.
    """
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim != 1:
        raise ValueError("Diarization takes one channel of samples")
    if not np.isfinite(np.sum(samples, dtype=np.float64)):  # NaN and inf propagate; no copy
        raise ValueError("The audio has samples that are not finite")
    lowest, highest, wanted = _speaker_bounds(min_speakers, max_speakers)
    if not samples.size:
        return []
    with _inference:
        models = _load()
        segmentations, embeddings = _analyse(models, samples, highest, progress)
    if not segmentations.any():
        return []
    active = segmentations.any(axis=1)
    if highest < 2:  # pyannote 3.3+: one speaker needs no embeddings.
        hard = np.where(active, 0, -2)
    else:
        hard = cluster(embeddings, active, lowest, highest, wanted)
    return reconstruct(segmentations, hard, window=models.window / RATE, step=round(STEP * models.window) / RATE,
                       frame_step=models.frame_step, frame_duration=models.frame_duration,
                       duration=len(samples) / RATE, max_speakers=highest)


# ── Models ──────────────────────────────────────────────────────

def _load():
    global _models
    if _models is None:
        _models = _Models(*ensure_models())
    return _models


def _session(ort, model):
    options = ort.SessionOptions()
    options.intra_op_num_threads = THREADS
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    # Without the arena, memory returns after each run; with it a process
    # kept about 1 GB for the rest of its life.
    options.enable_cpu_mem_arena = False
    # The DirectML build also offers a GPU; diarization stays on the CPU.
    return ort.InferenceSession(model, sess_options=options, providers=["CPUExecutionProvider"])


def powerset_mapping(num_speakers: int, max_set_size: int) -> np.ndarray:
    """pyannote's Powerset.build_mapping: row k marks the speakers of powerset
    class k, for the empty set, then single speakers, then pairs..."""
    rows = [subset for size in range(max_set_size + 1) for subset in combinations(range(num_speakers), size)]
    mapping = np.zeros((len(rows), num_speakers), dtype=np.uint8)
    for k, subset in enumerate(rows):
        mapping[k, list(subset)] = 1
    return mapping


def to_multilabel(scores: np.ndarray, mapping: np.ndarray) -> np.ndarray:
    """Hard multilabel activity (..., frames, speakers) from powerset scores
    (..., frames, classes): the most likely class's speakers."""
    return mapping[np.argmax(scores, axis=-1)]


class _Models:
    def __init__(self, segmentation: Path, embedding: Path):
        import onnxruntime as ort
        self.segmentation = _session(ort, str(segmentation))
        meta = self.segmentation.get_modelmeta().custom_metadata_map
        if int(meta["sample_rate"]) != RATE:
            raise ValueError("The segmentation model expects another sample rate")
        self.window = int(meta["window_size"])
        self.frame_step = int(meta["receptive_field_shift"]) / RATE
        self.frame_duration = int(meta["receptive_field_size"]) / RATE
        self.mapping = powerset_mapping(int(meta["num_speakers"]), int(meta["powerset_max_classes"]))
        if len(self.mapping) != int(meta["num_classes"]):
            raise ValueError("The segmentation model's powerset does not match its classes")
        self.segmentation_input = self.segmentation.get_inputs()[0].name
        model, self.frames_output, self.weight, self.bias = open_embedding_model(embedding.read_bytes())
        self.embedding = _session(ort, model)
        self.embedding_input = self.embedding.get_inputs()[0].name

    def segment(self, windows: np.ndarray) -> np.ndarray:
        scores = self.segmentation.run(None, {self.segmentation_input: windows[:, None, :]})[0]
        return to_multilabel(scores, self.mapping)

    def frames(self, features: np.ndarray) -> np.ndarray:
        """The ResNet's output for (windows, frames, 80) features, flattened to
        (windows, channels x frequencies, frames)."""
        maps = self.embedding.run([self.frames_output], {self.embedding_input: features})[0]
        return maps.reshape(len(maps), -1, maps.shape[-1])


# ── ONNX protobuf, just enough to read weights and add an output ─

def _varint(buffer, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = buffer[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte, value = value & 0x7F, value >> 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _fields(buffer):
    """(field number, value, start, end) of each field of a protobuf message;
    the value is an int (varint) or the payload's bytes."""
    pos = 0
    while pos < len(buffer):
        start = pos
        key, pos = _varint(buffer, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buffer, pos)
        elif wire == 2:
            length, pos = _varint(buffer, pos)
            value, pos = buffer[pos:pos + length], pos + length
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value, pos = buffer[pos:pos + size], pos + size
        else:
            raise ValueError("The embedding model is not a readable ONNX file")
        yield number, value, start, pos


def _message(number: int, payload: bytes) -> bytes:
    return _encode_varint(number << 3 | 2) + _encode_varint(len(payload)) + payload


def _tensor(proto) -> tuple[str, np.ndarray | None]:
    """A TensorProto's name and, for float32 data stored in the file, its
    values (None otherwise)."""
    name, dims, data, kind = "", [], None, 0
    for number, value, _, _ in _fields(proto):
        if number == 1:  # dims, packed or not
            if isinstance(value, int):
                dims.append(value)
            else:
                pos = 0
                while pos < len(value):
                    dim, pos = _varint(value, pos)
                    dims.append(dim)
        elif number == 2:
            kind = value
        elif number == 8:
            name = bytes(value).decode()
        elif number in (4, 9) and not isinstance(value, int):  # float_data (packed) or raw_data
            data = bytes(value)
    if kind != 1 or data is None or len(data) != 4 * math.prod(dims):
        return name, None
    return name, np.frombuffer(data, dtype="<f4").reshape(dims).astype(np.float64)


def open_embedding_model(data: bytes) -> tuple[bytes, str, np.ndarray, np.ndarray]:
    """The WeSpeaker ONNX model with its ResNet's frame-level output added,
    that output's name, and the final linear layer's weight and bias, which
    follow statistics pooling. ONNX graph: ModelProto.graph = 7; GraphProto
    node = 1, initializer = 5, output = 12; NodeProto input = 1, output = 2,
    op_type = 4."""
    model = memoryview(data)
    graph = next(((value, start, end) for number, value, start, end in _fields(model) if number == 7), None)
    if graph is None:
        raise ValueError("The embedding model has no graph")
    graph, start, end = graph
    nodes, initializers = [], {}
    for number, value, _, _ in _fields(graph):
        if number == 1:
            node = {1: [], 2: [], 4: [""]}
            for field, item, _, _ in _fields(value):
                if field in node:
                    node[field].append(bytes(item).decode())
            nodes.append((node[4][-1], node[1], node[2]))
        elif number == 5:
            name, array = _tensor(value)
            initializers[name] = array
    # Statistics pooling reads the last residual block's output; the linear
    # layer (Gemm) follows it.
    pooled = next((inputs[0] for op, inputs, _ in nodes if op == "ReduceMean"), None)
    linear = next((inputs for op, inputs, _ in nodes if op == "Gemm"), None)
    if pooled is None or linear is None or initializers.get(linear[1]) is None \
            or initializers.get(linear[2]) is None:
        raise ValueError("The embedding model does not have the expected pooling and linear layer")
    weight, bias = initializers[linear[1]], initializers[linear[2]]
    # ValueInfoProto{name = 1, type = 2: TypeProto{tensor_type = 1: {elem_type = 1: FLOAT}}}
    output = _message(1, pooled.encode()) + _message(2, _message(1, b"\x08\x01"))
    edited = bytes(graph) + _message(12, output)
    return bytes(model[:start]) + _message(7, edited) + bytes(model[end:]), pooled, weight, bias


# ── Features ────────────────────────────────────────────────────

@cache
def _mel_banks() -> np.ndarray:
    """torchaudio.compliance.kaldi.get_mel_banks for 80 bins from 20 Hz to
    Nyquist, with the Nyquist FFT bin's zero column: (80, 257)."""
    def mel(frequency):
        return 1127.0 * np.log(1.0 + frequency / 700.0)
    low, high = mel(LOW_FREQUENCY), mel(RATE / 2)
    delta = (high - low) / (MEL_BINS + 1)
    left = low + np.arange(MEL_BINS)[:, None] * delta
    center, right = left + delta, left + 2 * delta
    bins = mel(RATE / FFT_SIZE * np.arange(FFT_SIZE // 2))[None, :]
    banks = np.maximum(0.0, np.minimum((bins - left) / (center - left), (right - bins) / (right - center)))
    return np.pad(banks, ((0, 0), (0, 1)))


@cache
def _hamming() -> np.ndarray:
    return np.hamming(FRAME_LENGTH)  # torch.hamming_window(periodic=False)


def fbank(samples: np.ndarray) -> np.ndarray:
    """Kaldi log mel energies (frames, 80) of samples in [-1, 1], as
    torchaudio.compliance.kaldi.fbank computes them for WeSpeaker (before
    the per-window mean is removed). Frame i covers samples [160 i, 160 i +
    400); a partial last frame is dropped (``snip_edges``)."""
    x = np.asarray(samples, dtype=np.float64) * 32768.0
    if len(x) < FRAME_LENGTH:
        return np.zeros((0, MEL_BINS))
    count = 1 + (len(x) - FRAME_LENGTH) // FRAME_SHIFT
    frames = np.lib.stride_tricks.sliding_window_view(x, FRAME_LENGTH)[::FRAME_SHIFT][:count]
    frames = frames - frames.mean(axis=1, keepdims=True)  # remove_dc_offset
    frames = frames - PREEMPHASIS * np.concatenate((frames[:, :1], frames[:, :-1]), axis=1)
    spectrum = np.fft.rfft(frames * _hamming(), n=FFT_SIZE)
    power = spectrum.real ** 2 + spectrum.imag ** 2
    return np.log(np.maximum(power @ _mel_banks().T, np.finfo(np.float32).eps))


def pool(maps: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """pyannote's StatsPool with weights: the weighted mean and unbiased
    standard deviation over frames. ``maps`` (windows, features, frames),
    ``weights`` (windows, speakers, frames) -> (windows, speakers, 2 x features)."""
    maps = maps.astype(np.float64)
    stats = np.empty((len(maps), weights.shape[1], 2 * maps.shape[1]))
    for speaker in range(weights.shape[1]):
        w = weights[:, speaker, :].astype(np.float64)
        v1 = w.sum(axis=1) + 1e-8
        mean = np.einsum("bft,bt->bf", maps, w) / v1[:, None]
        v2 = np.square(w).sum(axis=1)
        var = np.einsum("bft,bt->bf", np.square(maps - mean[:, :, None]), w) / (v1 - v2 / v1 + 1e-8)[:, None]
        stats[:, speaker] = np.concatenate((mean, np.sqrt(var)), axis=1)
    return stats


def _nearest(size_in: int, size_out: int) -> np.ndarray:
    """torch.nn.functional.interpolate(mode="nearest") source indices."""
    scale = np.float32(size_in / size_out)
    return np.minimum(np.floor(np.arange(size_out, dtype=np.float32) * scale).astype(np.int64), size_in - 1)


# ── Segmentation and embeddings ─────────────────────────────────

def _analyse(models: _Models, samples: np.ndarray, max_speakers, progress):
    """Hard local-speaker activity (windows, frames, 3) of every window and the
    embeddings (windows, 3, 256) of the active ones (NaN elsewhere)."""
    window = models.window
    step = round(STEP * window)
    if step % FRAME_SHIFT:
        raise ValueError("Window step and fbank frames do not align")
    hop, frames_per_window = step // FRAME_SHIFT, 1 + (window - FRAME_LENGTH) // FRAME_SHIFT
    # pyannote's Inference.slide: whole windows, then a zero-padded last one
    # unless the whole windows end exactly at the end.
    whole = 1 + (len(samples) - window) // step if len(samples) >= window else 0
    count = whole + int(len(samples) < window or (len(samples) - window) % step > 0)
    duration = len(samples) / RATE
    if progress:
        progress(0.0, duration)
    segmentations = embeddings = None
    for first in range(0, count, SEGMENTATION_BATCH):
        last = min(count, first + SEGMENTATION_BATCH)
        region = samples[first * step:(last - 1) * step + window]
        region = np.pad(region, (0, (last - 1 - first) * step + window - len(region)))
        windows = np.lib.stride_tricks.sliding_window_view(region, window)[::step]
        activity = models.segment(np.ascontiguousarray(windows))
        if segmentations is None:
            segmentations = np.zeros((count, *activity.shape[1:]), dtype=np.uint8)
            embeddings = np.full((count, activity.shape[2], len(models.bias)), np.nan, dtype=np.float32)
        segmentations[first:last] = activity
        speaking = np.flatnonzero(activity.any(axis=(1, 2)))
        if max_speakers >= 2 and speaking.size:
            # Overlapping windows share fbank frames: one pass for the batch.
            logmel = fbank(region)
            min_frames = math.ceil(activity.shape[1] * MIN_EMBEDDING_SAMPLES / window)
            for part in range(0, len(speaking), EMBEDDING_BATCH):
                chosen = speaking[part:part + EMBEDDING_BATCH]
                features = np.stack([logmel[i * hop:i * hop + frames_per_window] for i in chosen])
                features -= features.mean(axis=1, keepdims=True)
                maps = models.frames(features.astype(np.float32))
                masks = activity[chosen].transpose(0, 2, 1).astype(np.float32)  # (windows, speakers, frames)
                clean = masks * (masks.sum(axis=1, keepdims=True) < 2)
                used = np.where(clean.sum(axis=2, keepdims=True) > min_frames, clean, masks)
                weights = used[:, :, _nearest(used.shape[2], maps.shape[2])]
                stats = pool(maps, weights)
                embeddings[first + chosen] = stats @ models.weight.T + models.bias
        if progress:
            progress(min(duration, ((last - 1) * step + window) / RATE), duration)
    return segmentations, embeddings


# ── Clustering ──────────────────────────────────────────────────

def _speaker_bounds(min_speakers, max_speakers):
    """pyannote's set_num_speakers: (lowest, highest, exact or None)."""
    for value in (min_speakers, max_speakers):
        if value is not None and (not isinstance(value, (int, np.integer)) or value < 1):
            raise ValueError("min_speakers and max_speakers must be positive integers")
    lowest = min_speakers or 1
    highest = max_speakers or math.inf
    if lowest > highest:
        raise ValueError("min_speakers must not exceed max_speakers")
    return lowest, highest, lowest if lowest == highest else None


def _cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """scipy.spatial.distance.cdist(a, b, "cosine")."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 1.0 - (a / np.linalg.norm(a, axis=1, keepdims=True)) @ (b / np.linalg.norm(b, axis=1, keepdims=True)).T


def centroid_linkage(points: np.ndarray) -> np.ndarray:
    """scipy.cluster.hierarchy.linkage(points, "centroid"): (n - 1, 4) rows
    (cluster id, cluster id, distance, size) in merge order; cluster n + k is
    formed by row k. Distances may decrease (centroid linkage is not
    monotonic). Memory is linear in n: each cluster keeps its nearest
    neighbour, or a lower bound of that distance which is made exact only
    when it is the smallest (Müllner's generic algorithm, as SciPy does)."""
    centroids = np.array(points, dtype=np.float64)
    n = len(centroids)
    merges = np.empty((max(n - 1, 0), 4))
    if n < 2:
        return merges
    norms = np.einsum("ij,ij->i", centroids, centroids)
    sizes = np.ones(n)
    ids = np.arange(n)
    nearest = np.empty(n, dtype=np.int64)
    distance = np.empty(n)
    exact = np.ones(n, dtype=bool)

    def distances(c, alive):
        squared = norms[:alive] - 2 * (centroids[:alive] @ centroids[c]) + norms[c]
        result = np.sqrt(np.maximum(squared, 0.0))
        result[c] = np.inf
        return result

    for start in range(0, n, 256):
        block = centroids[start:start + 256]
        squared = np.einsum("ij,ij->i", block, block)[:, None] + norms[None, :] - 2 * (block @ centroids.T)
        rows = np.arange(len(block))
        squared[rows, start + rows] = np.inf
        nearest[start:start + len(block)] = np.argmin(squared, axis=1)
        distance[start:start + len(block)] = np.sqrt(np.maximum(squared[rows, nearest[start:start + len(block)]], 0))

    alive = n
    for k in range(n - 1):
        while True:
            x = int(np.argmin(distance[:alive]))
            if exact[x]:
                break
            row = distances(x, alive)
            nearest[x] = int(np.argmin(row))
            distance[x] = row[nearest[x]]
            exact[x] = True
        y = int(nearest[x])
        a, b = min(x, y), max(x, y)
        merges[k] = (min(ids[a], ids[b]), max(ids[a], ids[b]), distance[x], sizes[a] + sizes[b])
        stale = (nearest[:alive] == a) | (nearest[:alive] == b)
        centroids[a] = (sizes[a] * centroids[a] + sizes[b] * centroids[b]) / (sizes[a] + sizes[b])
        norms[a] = centroids[a] @ centroids[a]
        sizes[a] += sizes[b]
        ids[a] = n + k
        # Remove b: the last cluster takes its place.
        last = alive - 1
        if b != last:
            for array in (centroids, norms, sizes, ids, nearest, distance, exact, stale):
                array[b] = array[last]
            nearest[:last][nearest[:last] == last] = b
        alive = last
        if alive < 2:
            continue
        stale = stale[:alive]
        stale[a] = False
        row = distances(a, alive)
        nearest[a] = int(np.argmin(row))
        distance[a] = row[nearest[a]]
        exact[a] = True
        # The new cluster is the nearest of every cluster it is closer to
        # than that cluster's (bound on its) nearest. A cluster whose nearest
        # was a or b keeps the old distance as a lower bound.
        update = (row < distance[:alive]) | (stale & (row <= distance[:alive]))
        nearest[:alive][update] = a
        distance[:alive][update] = row[update]
        exact[:alive][update] = True
        exact[:alive][stale & ~update] = False
    return merges


def fcluster(merges: np.ndarray, n: int, heights: np.ndarray, cutoff: float) -> np.ndarray:
    """scipy.cluster.hierarchy.fcluster(Z, cutoff, criterion="distance") with
    Z's distances replaced by ``heights``: 1-based flat cluster numbers, a
    cluster being a largest subtree whose heights are all <= cutoff, numbered
    in SciPy's depth-first order."""
    if n == 1:
        return np.ones(1, dtype=np.int64)
    left = merges[:, 0].astype(np.int64)
    right = merges[:, 1].astype(np.int64)
    highest = np.empty(n - 1)
    for k in range(n - 1):
        value = heights[k]
        if left[k] >= n:
            value = max(value, highest[left[k] - n])
        if right[k] >= n:
            value = max(value, highest[right[k] - n])
        highest[k] = value
    labels = np.zeros(n, dtype=np.int64)
    visited = np.zeros(2 * n - 1, dtype=bool)
    stack, number, leader = [2 * n - 2], 0, -1
    while stack:
        root = stack[-1] - n
        lc, rc = left[root], right[root]
        if leader == -1 and highest[root] <= cutoff:
            leader = root
            number += 1
        if lc >= n and not visited[lc]:
            visited[lc] = True
            stack.append(lc)
            continue
        if rc >= n and not visited[rc]:
            visited[rc] = True
            stack.append(rc)
            continue
        for leaf in (lc, rc):
            if leaf < n:
                if leader == -1:
                    number += 1
                labels[leaf] = number
        if leader == root:
            leader = -1
        stack.pop()
    return labels


def agglomerative(embeddings: np.ndarray, min_clusters: int, max_clusters: int,
                  num_clusters: int | None = None) -> np.ndarray:
    """pyannote's AgglomerativeClustering.cluster (centroid linkage, cosine
    metric): 0-based clusters of (n, dimension) embeddings."""
    n = len(embeddings)
    min_cluster_size = min(MIN_CLUSTER_SIZE, max(1, round(0.1 * n)))
    if n == 1:
        return np.zeros(1, dtype=np.int64)
    with np.errstate(divide="ignore", invalid="ignore"):
        normalized = embeddings / np.linalg.norm(embeddings, axis=-1, keepdims=True)
    merges = centroid_linkage(normalized)
    clusters = fcluster(merges, n, merges[:, 2], THRESHOLD) - 1
    unique, counts = np.unique(clusters, return_counts=True)
    large = unique[counts >= min_cluster_size]
    if len(large) < min_clusters:
        num_clusters = min_clusters
    elif len(large) > max_clusters:
        num_clusters = max_clusters
    if num_clusters is not None and len(large) != num_clusters:
        # pyannote tries the dendrogram's merges nearest the threshold first
        # and stops at one leaving exactly num_clusters large clusters,
        # otherwise takes the closest; the number of large clusters after
        # each merge is counted once here instead of cutting for each.
        size = np.concatenate((np.ones(n), merges[:, 3]))
        current = n if min_cluster_size <= 1 else 0
        large_after = np.empty(n - 1, dtype=np.int64)
        for k in range(n - 1):
            a, b = int(merges[k, 0]), int(merges[k, 1])
            current += int(merges[k, 3] >= min_cluster_size) - int(size[a] >= min_cluster_size) \
                - int(size[b] >= min_cluster_size)
            large_after[k] = current
        best, best_count, tried = n - 1, 1, None
        for iteration in np.argsort(np.abs(merges[:, 2] - THRESHOLD)):
            if merges[iteration, 3] < min_cluster_size:
                continue
            tried = int(iteration)
            if abs(large_after[iteration] - num_clusters) < abs(best_count - num_clusters):
                best, best_count = tried, int(large_after[iteration])
            if large_after[iteration] == num_clusters:
                break
        chosen = best if best_count != num_clusters else tried
        if chosen is not None:
            clusters = fcluster(merges, n, np.arange(n - 1, dtype=np.float64), chosen) - 1
            unique, counts = np.unique(clusters, return_counts=True)
            large = unique[counts >= min_cluster_size]
    if len(large) == 0:
        clusters[:] = 0
        return clusters
    small = unique[counts < min_cluster_size]
    if len(small) == 0:
        return clusters
    # Each small cluster joins the large cluster with the nearest centroid.
    large_centroids = np.vstack([normalized[clusters == k].mean(axis=0) for k in large])
    small_centroids = np.vstack([normalized[clusters == k].mean(axis=0) for k in small])
    for k, target in zip(small, np.argmin(_cosine(large_centroids, small_centroids), axis=0)):
        clusters[clusters == k] = large[target]
    return np.unique(clusters, return_inverse=True)[1]


def cluster(embeddings: np.ndarray, active: np.ndarray, min_clusters: int = 1, max_clusters=math.inf,
            num_clusters: int | None = None) -> np.ndarray:
    """pyannote's BaseClustering.__call__ followed by the pipeline marking
    inactive speakers: the cluster (>= 0) of each (window, local speaker),
    -2 for speakers inactive in that window. ``embeddings`` (windows,
    speakers, dimension), ``active`` (windows, speakers)."""
    windows, speakers, _ = embeddings.shape
    valid = ~np.isnan(embeddings).any(axis=2)
    chunk_index, speaker_index = np.nonzero(active & valid)
    train = embeddings[chunk_index, speaker_index].astype(np.float64)
    n = len(train)
    # set_num_clusters
    lowest = max(1, min(n, num_clusters or min_clusters or 1))
    highest = max(1, min(n, num_clusters or max_clusters or n))
    if lowest > highest:
        raise ValueError("min_speakers must not exceed max_speakers")
    if lowest == highest:
        num_clusters = lowest
    if highest < 2:
        hard = np.zeros((windows, speakers), dtype=np.int64)
    else:
        clusters = agglomerative(train, lowest, highest, num_clusters)
        centroids = np.vstack([train[clusters == k].mean(axis=0) for k in range(clusters.max() + 1)])
        with np.errstate(invalid="ignore"):
            soft = 2 - _cosine(embeddings.reshape(windows * speakers, -1), centroids)
        hard = np.argmax(soft, axis=1).reshape(windows, speakers)
    hard[~active] = -2
    return hard


# ── Reconstruction ──────────────────────────────────────────────

def reconstruct(segmentations: np.ndarray, hard: np.ndarray, *, window: float, step: float, frame_step: float,
                frame_duration: float, duration: float, max_speakers=math.inf) -> list[tuple[float, float, int]]:
    """Turns from each window's activity (windows, frames, local speakers)
    and its speakers' clusters (``hard``, -2 inactive): pyannote's
    speaker_count, reconstruct, to_diarization and to_annotation (Binarize
    at 0.5, no minimum durations). Times in seconds: windows of ``window``
    every ``step``, frames of ``frame_duration`` every ``frame_step``, turns
    clipped to ``duration``. Speakers are renumbered in order of their first
    turn."""
    windows, frames, _ = segmentations.shape
    # Inference.aggregate's frame grid: window k starts at the frame nearest
    # its start, frame i's middle is at i * frame_step + frame_duration / 2,
    # and the grid ends at the frame nearest the last window's end.
    starts = np.rint(np.arange(windows) * step / frame_step).astype(np.int64)
    total = max(int(np.rint((window + (windows - 1) * step) / frame_step)) + 1, int(starts[-1]) + frames)
    # speaker_count: local speakers per frame, averaged over windows, rounded.
    summed = np.zeros(total)
    covered = np.zeros(total)
    local = segmentations.sum(axis=2)
    for k in range(windows):
        summed[starts[k]:starts[k] + frames] += local[k]
        covered[starts[k]:starts[k] + frames] += 1
    count = np.rint(np.divide(summed, covered, out=np.zeros(total), where=covered > 0))
    clusters = int(hard.max()) + 1
    if clusters <= 0 or not count.any():
        return []
    count = np.minimum(count, min(max_speakers, clusters)).astype(np.int64)
    # reconstruct + to_diarization: each window's clusters (the max of its
    # local speakers in that cluster), summed over windows; each frame keeps
    # its ``count`` most active clusters, ties to the lower cluster.
    activations = np.zeros((total, clusters), dtype=np.float32)
    for k in range(windows):
        for c in np.unique(hard[k]):
            if c >= 0:
                activations[starts[k]:starts[k] + frames, c] += segmentations[k][:, hard[k] == c].max(axis=1)
    binary = np.zeros((total, clusters), dtype=bool)
    rows = np.arange(total)
    for place in range(int(count.max())):
        best = np.argmax(np.where(binary, -np.inf, activations), axis=1)
        chosen = count > place
        binary[rows[chosen], best[chosen]] = True
    # to_annotation: Binarize(onset=offset=0.5) at frame middles.
    middles = np.arange(total) * frame_step + frame_duration / 2
    turns = []
    for c in range(clusters):
        edges = np.flatnonzero(np.diff(np.concatenate(([0], binary[:, c].astype(np.int8), [0]))))
        for begin, end in zip(edges[::2], edges[1::2]):
            start = float(middles[begin])
            stop = float(min(middles[end] if end < total else middles[-1], duration))
            if stop > start:
                turns.append((start, stop, c))
    turns.sort()
    first = {}
    for _, _, c in turns:
        first.setdefault(c, len(first))
    return [(round(start, 3), round(stop, 3), first[c]) for start, stop, c in turns]
