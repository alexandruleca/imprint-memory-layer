#!/usr/bin/env python3
"""Resident memory and disk use of Imprint's managed Qdrant server (ADR 0063).

Starts the server through ``qdrant_runner.ensure_running()`` in a throwaway
data directory on free loopback ports, so the binary, its environment and the
collection settings (``vectorstore._ensure_collection``: 1024-dim cosine
vectors on disk, int8 scalar quantization kept in RAM, on-disk payload, HNSW
m=16, every payload index) are the ones Imprint uses. It never contacts an
existing server or data directory.

    python scripts/measure-qdrant-memory.py [--binary PATH] [--points 10000,100000] [--work DIR]

Without ``--binary`` Imprint's own lookup applies: a ``qdrant`` on PATH, else
the pinned release, downloaded and verified against
``requirements/runtime-manifest.json``; the first line printed names the
binary and version used. Points are synthetic: random unit vectors (not
embeddings) with Imprint-shaped payloads whose ``content`` is this checkout's
tracked text cut into 2,000-character chunks, reused in turn. At each size the
server is measured after ingestion settles, after a restart, and after 200
filtered searches. Memory comes from ``/proc/<pid>/status``, so this runs on
Linux only. Qdrant sizes its thread pools and default segment count by the
CPUs it may use, so memory depends on them; run under ``taskset -c 0-7`` to
measure an 8-CPU machine.
"""
import argparse
import json
import os
import platform
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SUFFIXES = (".py", ".go", ".md", ".ts", ".tsx")
LANGS = {".py": "python", ".go": "go", ".md": "markdown", ".ts": "typescript", ".tsx": "typescript"}
FIELDS = ("VmRSS", "RssAnon", "RssFile", "VmHWM")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def chunks(size: int = 2000) -> list[tuple[str, str]]:
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    out = []
    for name in files:
        if name.endswith(SUFFIXES) and not name.startswith("imprint/ui/out/"):
            text = (ROOT / name).read_text(encoding="utf-8", errors="replace")
            out += [(name, text[i:i + size]) for i in range(0, len(text), size)]
    return out


def memory(pid: int) -> dict[str, int]:
    """Selected /proc/<pid>/status fields in KiB."""
    values = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        key, _, rest = line.partition(":")
        if key in FIELDS:
            values[key] = int(rest.split()[0])
    return values


def disk(path: Path) -> int:
    """Allocated bytes; Qdrant preallocates sparse files, so sizes overstate it."""
    return sum(f.stat().st_blocks * 512 for f in path.rglob("*") if f.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--binary", help="Qdrant binary to run (default: download the pinned release)")
    parser.add_argument("--points", default="10000,100000", help="comma-separated cumulative collection sizes")
    parser.add_argument("--work", help="parent of the throwaway data directory (default: the system temp dir)")
    parser.add_argument("--settle", type=float, default=10.0, help="seconds to wait before each reading")
    parser.add_argument("--out", help="also write the readings as JSON to this file")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("memory readings come from /proc; run this on Linux")
    sizes = sorted(int(n) for n in args.points.split(","))

    data = Path(tempfile.mkdtemp(prefix="imprint-qdrant-measure-", dir=args.work))
    os.environ.update({"IMPRINT_DATA_DIR": str(data), "IMPRINT_QDRANT_HOST": "127.0.0.1",
                       "IMPRINT_QDRANT_PORT": str(free_port()), "IMPRINT_QDRANT_GRPC_PORT": str(free_port()),
                       "IMPRINT_QDRANT_NO_SPAWN": "0"})
    if args.binary:
        os.environ["IMPRINT_QDRANT_BIN"] = str(Path(args.binary).resolve())
    else:
        os.environ.pop("IMPRINT_QDRANT_BIN", None)

    import numpy as np
    from qdrant_client import QdrantClient, models as qm
    from imprint import config, qdrant_runner, vectorstore

    readings = []
    try:
        if qdrant_runner.is_running():
            raise SystemExit(f"something already answers on port {config.QDRANT_PORT}; refusing to measure it")

        def start() -> int:
            qdrant_runner.ensure_running()
            return int((data / "qdrant.pid").read_text())

        def read(stage: str, points: int, pid: int, segments: int = 0) -> None:
            time.sleep(args.settle)
            row = {"stage": stage, "points": points, "segments": segments, **memory(pid),
                   "storage_bytes": disk(data / "qdrant_storage")}
            readings.append(row)
            print(json.dumps(row), flush=True)

        def settled(client: QdrantClient, collection: str) -> int:
            """Wait for the optimizers to finish; return the segment count."""
            while True:
                info = client.get_collection(collection)
                if info.status == qm.CollectionStatus.GREEN:
                    return info.segments_count
                time.sleep(1)

        pid = start()
        binary = Path(os.readlink(f"/proc/{pid}/exe"))
        version = subprocess.run([str(binary), "--version"], capture_output=True, text=True).stdout.strip()
        print(json.dumps({"binary": str(binary), "version": version, "binary_bytes": binary.stat().st_size,
                          "machine": platform.platform(), "cpus": len(os.sched_getaffinity(0))}), flush=True)
        read("no collections", 0, pid)

        client, collection = vectorstore._ensure_collection()
        corpus = chunks()
        rng = np.random.default_rng(1729)
        pick = random.Random(1729)
        projects = [f"project-{i}" for i in range(8)]
        types = ["source", "conversation", "decision", "finding", "pattern"]
        count = 0
        for target in sizes:
            bulk = QdrantClient(host="127.0.0.1", port=config.QDRANT_PORT, grpc_port=config.QDRANT_GRPC_PORT,
                                prefer_grpc=True, timeout=120)
            while count < target:
                batch = min(512, target - count)
                vectors = rng.standard_normal((batch, config.EMBEDDING_DIM), dtype=np.float32)
                vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
                points = []
                for i in range(batch):
                    source, content = corpus[(count + i) % len(corpus)]
                    points.append(qm.PointStruct(id=str(uuid.uuid4()), vector={config.QDRANT_VECTOR_NAME: vectors[i].tolist()}, payload={
                        "_mid": uuid.uuid4().hex, "content": content, "project": pick.choice(projects),
                        "origin_project": "", "type": pick.choice(types),
                        "tags": {"lang": LANGS[Path(source).suffix], "layer": pick.choice(["api", "ui", "docs", "tests"]),
                                 "kind": "source", "domain": pick.sample(["auth", "db", "search", "sync", "ui"], 2),
                                 "topics": pick.sample(["qdrant", "queue", "voice", "chat", "release", "hooks"], 2)},
                        "source": f"{source}#{count + i}", "source_path": source, "source_type": "file",
                        "source_url": "", "doc_metadata": {}, "etag": "", "last_modified": "",
                        "chunk_index": count + i, "source_mtime": time.time(), "timestamp": time.time()}))
                bulk.upsert(collection, points=points, wait=True)
                count += batch
            bulk.close()
            read("after ingestion", count, pid, settled(client, collection))

            qdrant_runner.stop()
            vectorstore._close_client()
            pid = start()
            client, collection = vectorstore._ensure_collection()
            read("after restart", count, pid, settled(client, collection))

            for _ in range(200):
                query = rng.standard_normal(config.EMBEDDING_DIM, dtype=np.float32)
                client.query_points(collection, query=(query / np.linalg.norm(query)).tolist(),
                                    using=config.QDRANT_VECTOR_NAME, limit=10, with_payload=True,
                                    query_filter=qm.Filter(must=[qm.FieldCondition(key="project", match=qm.MatchValue(value=pick.choice(projects)))]))
            read("after 200 searches", count, pid, settled(client, collection))
    finally:
        vectorstore._close_client()
        qdrant_runner.stop()
        shutil.rmtree(data, ignore_errors=True)
    if args.out:
        Path(args.out).write_text(json.dumps(readings, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
