"""Speaker diarization (imprint.diarize): the numpy port of pyannote's
pipeline. Most tests need no model; the model tests run when both pinned
models are in IMPRINT_DIARIZATION_MODELS (a folder) or already downloaded
in the data directory, and IMPRINT_DIARIZATION_SAMPLE may name a 16 kHz mono
WAV with two or more speakers (pyannote's tutorial sample.wav)."""
import math
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import wave

import numpy as np

from imprint import diarize as d


def unit(vectors):
    vectors = np.asarray(vectors, dtype=np.float64)
    return vectors / np.linalg.norm(vectors, axis=-1, keepdims=True)


def group(rng, center, count, spread=0.05):
    return unit(center + spread * rng.normal(size=(count, len(center))))


def at_distance(base, other, distance):
    """A unit vector at Euclidean ``distance`` from unit ``base``, towards ``other``."""
    other = other - (other @ base) * base
    other /= np.linalg.norm(other)
    cosine = 1 - distance ** 2 / 2
    return cosine * base + math.sqrt(1 - cosine ** 2) * other


def primitive_centroid_linkage(points):
    """The textbook algorithm: always merge the two clusters whose centroids
    are nearest."""
    clusters = {i: (np.array(p, dtype=np.float64), 1) for i, p in enumerate(points)}
    merges, n = [], len(points)
    for k in range(n - 1):
        keys = sorted(clusters)
        _, a, b = min((np.linalg.norm(clusters[a][0] - clusters[b][0]), a, b)
                      for i, a in enumerate(keys) for b in keys[i + 1:])
        (ca, na), (cb, nb) = clusters.pop(a), clusters.pop(b)
        merges.append((a, b, np.linalg.norm(ca - cb), na + nb))
        clusters[n + k] = ((na * ca + nb * cb) / (na + nb), na + nb)
    return np.array(merges)


class FbankTests(unittest.TestCase):
    def signal(self):
        t = np.arange(16000) / 16000
        return (0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 3000 * t)
                + 0.1 * np.sin(2 * np.pi * (200 + 1500 * t) * t) + 0.01 * np.sin(12345.678 * t ** 2)).astype(np.float32)

    def test_matches_torchaudio_kaldi_fbank(self):
        # torchaudio.compliance.kaldi.fbank(x * 32768, num_mel_bins=80, frame_length=25,
        # frame_shift=10, dither=0, window_type="hamming", use_energy=False), torchaudio 2.5.1,
        # frames 0, 50, 97 at bins 0, 10, 40, 79. The whole 30 s of pyannote's sample.wav
        # differed by at most 3.9e-4 (4.6e-4 from kaldi-native-fbank).
        expected = [[14.1494, 17.0407, 14.2027, 14.4167], [11.8058, 11.293, 21.5444, 14.638],
                    [14.2901, 14.3072, 15.1388, 13.9056]]
        features = d.fbank(self.signal())
        self.assertEqual(features.shape, (98, 80))
        np.testing.assert_allclose(features[[0, 50, 97]][:, [0, 10, 40, 79]], expected, atol=1e-3)

    def test_frames_snip_edges_and_a_tone_lands_in_its_mel_bin(self):
        self.assertEqual(d.fbank(np.zeros(399, dtype=np.float32)).shape, (0, 80))
        self.assertEqual(d.fbank(np.zeros(400, dtype=np.float32)).shape, (1, 80))
        self.assertEqual(d.fbank(np.zeros(160000, dtype=np.float32)).shape, (998, 80))
        t = np.arange(8000) / 16000
        features = d.fbank((0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32))
        mel = lambda f: 1127 * np.log(1 + f / 700)
        delta = (mel(8000) - mel(20)) / 81
        centers = mel(20) + delta * (np.arange(80) + 1)
        expected = int(np.argmin(abs(centers - mel(1000))))
        self.assertTrue(all(abs(int(b) - expected) <= 1 for b in features.argmax(axis=1)))
        # Silence is floored at float32's epsilon, not -inf.
        self.assertTrue(np.isfinite(d.fbank(np.zeros(1000, dtype=np.float32))).all())


class PowersetTests(unittest.TestCase):
    def test_mapping_and_conversion(self):
        mapping = d.powerset_mapping(3, 2)
        np.testing.assert_array_equal(mapping, [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
                                                [1, 1, 0], [1, 0, 1], [0, 1, 1]])
        scores = np.log(np.full((2, 7), 0.01))
        scores[0, 5] = 0.0  # speakers 0 and 2
        scores[1, 0] = 0.0  # nobody
        np.testing.assert_array_equal(d.to_multilabel(scores, mapping), [[1, 0, 1], [0, 0, 0]])


class LinkageTests(unittest.TestCase):
    def test_matches_the_primitive_algorithm_with_inversions(self):
        rng = np.random.default_rng(3)
        for points in (rng.uniform(size=(40, 3)), group(rng, rng.normal(size=8), 25, 0.5)):
            ours, expected = d.centroid_linkage(points), primitive_centroid_linkage(points)
            np.testing.assert_array_equal(ours[:, [0, 1, 3]], np.c_[np.minimum(expected[:, 0], expected[:, 1]),
                                                                     np.maximum(expected[:, 0], expected[:, 1]),
                                                                     expected[:, 3]])
            np.testing.assert_allclose(ours[:, 2], expected[:, 2], atol=1e-12)
        inverted = d.centroid_linkage(np.array([[0.0, 0.0], [1.0, 0.0], [0.5, 0.9]]))
        np.testing.assert_allclose(inverted, [[0, 1, 1.0, 2], [2, 3, 0.9, 3]])

    def test_flat_clusters_are_numbered_like_scipy(self):
        points = np.array([[10.0], [0.0], [0.1], [5.0], [5.3]])
        merges = d.centroid_linkage(points)
        # scipy.cluster.hierarchy.fcluster(linkage(points, "centroid"), 1.0, "distance")
        np.testing.assert_array_equal(d.fcluster(merges, 5, merges[:, 2], 1.0), [3, 1, 1, 2, 2])
        # Below an inversion nothing merges: the subtree holds a larger distance.
        inverted = d.centroid_linkage(np.array([[0.0, 0.0], [1.0, 0.0], [0.5, 0.9]]))
        np.testing.assert_array_equal(d.fcluster(inverted, 3, inverted[:, 2], 0.95), [1, 2, 3])
        np.testing.assert_array_equal(d.fcluster(inverted, 3, inverted[:, 2], 1.0), [1, 1, 1])


class ClusteringTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(11)
        self.centers = unit(self.rng.normal(size=(3, 64)))

    def same_partition(self, labels, groups):
        for members in groups:
            self.assertEqual(len(set(labels[members])), 1)
        self.assertEqual(len({labels[members[0]] for members in groups}), len(groups))

    def test_clear_clusters(self):
        points = np.vstack([group(self.rng, c, 20) for c in self.centers])
        labels = d.agglomerative(points, 1, len(points))
        self.same_partition(labels, [range(0, 20), range(20, 40), range(40, 60)])

    def test_threshold_cuts_between_centroids(self):
        base = self.centers[0]
        for distance, expected in ((0.5, 1), (1.0, 2)):
            other = at_distance(base, self.centers[1], distance)
            points = np.vstack([group(self.rng, base, 20, 0.01), group(self.rng, other, 20, 0.01)])
            self.assertEqual(d.agglomerative(points, 1, len(points)).max() + 1, expected, distance)

    def test_small_clusters_join_the_nearest_large_one(self):
        a, b = self.centers[0], self.centers[1]
        near_a = at_distance(a, self.centers[2], 0.9)  # beyond the threshold, but nearer a than b
        points = np.vstack([group(self.rng, a, 30, 0.01), group(self.rng, b, 30, 0.01),
                            group(self.rng, near_a, 3, 0.01)])
        labels = d.agglomerative(points, 1, len(points))
        self.assertEqual(labels.max() + 1, 2)
        self.same_partition(labels, [list(range(0, 30)) + [60, 61, 62], range(30, 60)])

    def test_speaker_bounds_walk_the_dendrogram(self):
        b1 = self.centers[1]
        b2 = at_distance(b1, self.centers[2], 0.5)  # merged at the threshold
        points = np.vstack([group(self.rng, self.centers[0], 25, 0.01), group(self.rng, b1, 25, 0.01),
                            group(self.rng, b2, 25, 0.01)])
        n = len(points)
        self.same_partition(d.agglomerative(points, 1, n), [range(0, 25), range(25, 75)])
        self.same_partition(d.agglomerative(points, 3, n), [range(0, 25), range(25, 50), range(50, 75)])
        self.same_partition(d.agglomerative(points, 3, 3, 3), [range(0, 25), range(25, 50), range(50, 75)])
        self.assertEqual(set(d.agglomerative(points, 1, 1, 1)), {0})

    def test_cluster_marks_inactive_speakers_and_honours_max(self):
        embeddings = np.vstack([group(self.rng, c, 20) for c in self.centers]).reshape(20, 3, 64)
        active = np.ones((20, 3), dtype=bool)
        active[0, 2] = False
        embeddings[1, 1] = np.nan  # inactive speakers may have no embedding
        active[1, 1] = False
        hard = d.cluster(embeddings, active)
        self.assertEqual(hard[0, 2], -2)
        self.assertEqual(hard[1, 1], -2)
        self.assertEqual(len(set(hard[active])), 3)
        self.assertEqual(len(set(d.cluster(embeddings, active, 1, 2)[active])), 2)
        np.testing.assert_array_equal(d.cluster(embeddings, active, 1, 1)[active], 0)

    def test_speaker_bounds_are_validated(self):
        self.assertEqual(d._speaker_bounds(None, None), (1, math.inf, None))
        self.assertEqual(d._speaker_bounds(2, 2), (2, 2, 2))
        for bad in ((0, None), (None, 0), (3, 2), (1.5, None)):
            with self.assertRaises(ValueError):
                d._speaker_bounds(*bad)


class ReconstructTests(unittest.TestCase):
    # Frames of 0.5 s every 0.5 s; windows of 2 s (4 frames) every 1 s (2 frames).
    geometry = dict(window=2.0, step=1.0, frame_step=0.5, frame_duration=0.5)

    def segmentations(self):
        seg = np.zeros((3, 4, 2), dtype=np.uint8)
        seg[0, :, 0] = 1        # frames 0-3: speaker A
        seg[1, 0:2, 0] = 1      # frames 2-3: A again
        seg[1, 2:4, 1] = 1      # frames 4-5: B
        seg[2, 0:2, 0] = 1      # frames 4-5: B, the first local speaker of window 2
        return seg

    def test_turns_from_overlapping_windows(self):
        hard = np.array([[0, -2], [0, 1], [1, -2]])
        turns = d.reconstruct(self.segmentations(), hard, duration=10, **self.geometry)
        self.assertEqual(turns, [(0.25, 2.25, 0), (2.25, 3.25, 1)])
        # Speakers are numbered by their first turn, whatever their cluster.
        swapped = np.array([[1, -2], [1, 0], [0, -2]])
        self.assertEqual(d.reconstruct(self.segmentations(), swapped, duration=10, **self.geometry), turns)
        # Turns end with the audio.
        self.assertEqual(d.reconstruct(self.segmentations(), hard, duration=3.0, **self.geometry)[-1], (2.25, 3.0, 1))

    def test_overlap_and_no_phantom_speakers(self):
        seg = np.zeros((1, 4, 2), dtype=np.uint8)
        seg[0, :, :] = 1  # two local speakers talk at once
        both = d.reconstruct(seg, np.array([[0, 1]]), duration=10, **self.geometry)
        self.assertEqual(both, [(0.25, 2.25, 0), (0.25, 2.25, 1)])
        # The same overlap in one cluster: one speaker, not a padded extra one.
        one = d.reconstruct(seg, np.array([[0, 0]]), duration=10, **self.geometry)
        self.assertEqual(one, [(0.25, 2.25, 0)])
        self.assertEqual(d.reconstruct(seg, np.array([[0, 1]]), duration=10, max_speakers=1, **self.geometry),
                         [(0.25, 2.25, 0)])
        self.assertEqual(d.reconstruct(np.zeros((1, 4, 2), np.uint8), np.array([[-2, -2]]), duration=10,
                                       **self.geometry), [])


class FakeModels:
    """Two-second windows of ten frames: one speaker talks throughout."""
    window = 32000
    frame_step = frame_duration = 0.2
    weight = np.ones((4, 4))
    bias = np.zeros(4)

    def segment(self, windows):
        activity = np.zeros((len(windows), 10, 3), dtype=np.uint8)
        activity[:, :, 0] = windows.std(axis=1)[:, None] > 0
        return activity

    def frames(self, features):
        return np.ones((len(features), 2, 5), dtype=np.float32)


class PipelineTests(unittest.TestCase):
    def test_input_checks_need_no_model(self):
        with patch.object(d, "_load", side_effect=AssertionError("no model needed")):
            self.assertEqual(d.diarize(np.zeros(0, dtype=np.float32)), [])
            with self.assertRaises(ValueError):
                d.diarize(np.zeros((2, 100), dtype=np.float32))
            with self.assertRaises(ValueError):
                d.diarize(np.array([0.0, np.nan], dtype=np.float32))
            with self.assertRaises(ValueError):
                d.diarize(np.zeros(100, dtype=np.float32), min_speakers=3, max_speakers=2)

    def test_progress_reports_seconds_and_cancels(self):
        samples = np.sin(np.arange(16000 * 5) / 7).astype(np.float32)
        seen = []
        with patch.object(d, "_load", return_value=FakeModels()):
            turns = d.diarize(samples, progress=lambda done, total: seen.append((done, total)))
            self.assertEqual([turn[2] for turn in turns], [0])
            self.assertEqual(seen[0], (0.0, 5.0))
            self.assertEqual(seen[-1], (5.0, 5.0))
            self.assertEqual([done for done, _ in seen], sorted(done for done, _ in seen))

            def cancel(done, total):
                if done:
                    raise InterruptedError
            with patch.object(d, "SEGMENTATION_BATCH", 1), self.assertRaises(InterruptedError):
                d.diarize(samples, progress=cancel)
            # The lock is released: the next call runs.
            self.assertEqual(len(d.diarize(samples, max_speakers=1)), 1)

    def test_profile_names_models_and_parameters(self):
        identity = d.profile()
        self.assertRegex(identity, r'^[0-9a-f]{64}$')
        self.assertEqual(d.profile(), identity)
        with patch.object(d, "THRESHOLD", 0.5):
            self.assertNotEqual(d.profile(), identity)
        with patch.object(d, "EMBEDDING", d.EMBEDDING._replace(revision="0" * 40)), \
                patch.object(d, "MODELS", (d.SEGMENTATION, d.EMBEDDING._replace(revision="0" * 40))):
            self.assertNotEqual(d.profile(), identity)


def model_paths():
    folder = os.environ.get("IMPRINT_DIARIZATION_MODELS")
    names = [next(iter(spec.files)) for spec in d.MODELS]
    if folder:
        paths = [Path(folder) / name for name in names]
    else:
        from imprint.audio_ingest import model_root
        paths = [model_root(spec) / name for spec, name in zip(d.MODELS, names)]
    return paths if all(path.exists() for path in paths) else None


@unittest.skipUnless(model_paths(), "diarization models are not downloaded")
class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = d._Models(*model_paths())

    def test_frame_output_and_pooling_reproduce_the_models_embedding(self):
        import onnxruntime as ort
        features = np.random.default_rng(5).normal(size=(2, 300, 80)).astype(np.float32)
        original = d._session(ort, str(model_paths()[1]))
        expected = original.run(None, {"feats": features})[0]
        maps = self.models.frames(features)
        ours = d.pool(maps, np.ones((2, 1, maps.shape[2])))[:, 0] @ self.models.weight.T + self.models.bias
        np.testing.assert_allclose(ours, expected, rtol=1e-3, atol=1e-3)

    def test_segmentation_metadata(self):
        self.assertEqual((self.models.window, self.models.frame_step, self.models.frame_duration),
                         (160000, 270 / 16000, 991 / 16000))
        self.assertEqual(self.models.segment(np.zeros((1, 160000), dtype=np.float32)).shape, (1, 589, 3))

    def test_silence_has_no_speakers(self):
        with patch.object(d, "_load", return_value=self.models):
            self.assertEqual(d.diarize(np.zeros(16000 * 12, dtype=np.float32)), [])

    @unittest.skipUnless(os.environ.get("IMPRINT_DIARIZATION_SAMPLE"), "no sample recording")
    def test_sample_recording_has_several_speakers(self):
        with wave.open(os.environ["IMPRINT_DIARIZATION_SAMPLE"]) as source:
            samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768
        with patch.object(d, "_load", return_value=self.models):
            turns = d.diarize(samples)
            two = d.diarize(samples, min_speakers=2, max_speakers=2)
        self.assertGreaterEqual(len({speaker for *_, speaker in turns}), 2)
        self.assertEqual({speaker for *_, speaker in two}, {0, 1})
        self.assertTrue(all(0 <= start < end <= len(samples) / 16000 for start, end, _ in turns))


if __name__ == "__main__":
    unittest.main()
