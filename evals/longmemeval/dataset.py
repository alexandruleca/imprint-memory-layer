"""LongMemEval-S, pinned exactly as ai-memory's evaluator pins it.

Mirrors ``evals/src/retrieval/dataset.rs`` in github.com/akitaonrails/ai-memory:
the same file, URL and sha256, the same abstention rule and the same order
of question selection. The 278 MB file is not committed; it downloads to
``evals/longmemeval/datasets/`` (gitignored) and a file whose sha256 differs
from the pin is refused.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.request
from pathlib import Path

NAME = "longmemeval_s"
URL = "https://huggingface.co/datasets/xiaowu0162/longmemeval/resolve/main/longmemeval_s"
SHA256 = "08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894"
DEFAULT_PATH = Path(__file__).resolve().parent / "datasets" / "longmemeval_s.json"


class DatasetError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(path: Path = DEFAULT_PATH) -> None:
    """Download to ``path`` through a temporary file, verifying the pin before
    the rename lands it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    with urllib.request.urlopen(URL, timeout=600) as response, open(tmp, "wb") as out:
        shutil.copyfileobj(response, out)
    digest = sha256_file(tmp)
    if digest != SHA256:
        tmp.unlink(missing_ok=True)
        raise DatasetError(f"downloaded dataset sha256 {digest} does not match pin {SHA256}")
    os.replace(tmp, path)


def load(path: Path = DEFAULT_PATH) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise DatasetError(f"no dataset at {path}; run with --fetch to download it")
    digest = sha256_file(path)
    if digest != SHA256:
        raise DatasetError(f"dataset sha256 mismatch at {path}: got {digest}, pinned {SHA256} for {NAME}. "
                           "Refusing to benchmark against an unpinned file.")
    questions = json.loads(path.read_bytes())
    for q in questions:
        lengths = {len(q["haystack_sessions"]), len(q["haystack_session_ids"]), len(q["haystack_dates"])}
        if len(lengths) != 1:
            raise DatasetError(f"question {q['question_id']}: sessions/ids/dates length mismatch")
    return questions


def is_abstention(q: dict) -> bool:
    """``*_abs`` questions have a false premise and no evidence session."""
    return q["question_id"].endswith("_abs")


def select(questions: list[dict], *, question_ids=(), sample: int | None = None,
           stratify: int | None = None) -> tuple[list[dict], int]:
    """The scored questions and the number of abstention questions left out,
    in ai-memory's order: chosen ids, then the ``sample`` prefix, then the
    abstention filter, then ``stratify`` (first N of each type, types sorted)."""
    if question_ids:
        known = {q["question_id"] for q in questions}
        unknown = [qid for qid in question_ids if qid not in known]
        if unknown:
            raise DatasetError(f"--question-id not in the dataset: {unknown}")
        wanted = set(question_ids)
        questions = [q for q in questions if q["question_id"] in wanted]
    if sample is not None:
        questions = questions[:sample]
    abstention = sum(1 for q in questions if is_abstention(q))
    scored = [q for q in questions if not is_abstention(q)]
    if stratify is not None:
        buckets: dict[str, list[dict]] = {}
        for q in scored:
            bucket = buckets.setdefault(q["question_type"], [])
            if len(bucket) < stratify:
                bucket.append(q)
        scored = [q for kind in sorted(buckets) for q in buckets[kind]]
    return scored, abstention
