"""LongMemEval-S retrieval benchmark for Imprint, measured the way ai-memory measures it.

Per question: a fresh Imprint workspace (its own Qdrant collection and graph
database), every haystack turn stored in order through the MCP ``store`` tool,
the question asked through the MCP ``search`` tool with ``limit=10``, the
returned hits scored at session level, then the workspace dropped. Imprint runs
from this checkout as real MCP stdio server processes, against its own data
directory and Qdrant port, so nothing touches the user's installed Imprint.

Usage: ``evals/longmemeval/run.sh [run] [options]``, ``run.sh report <run dir>``
or ``run.sh fetch``. See README.md.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Run with ``python -I``: the checkout is imported explicitly, nothing from
# the working directory or PYTHONPATH.
sys.path.insert(0, str(REPO))

from evals.longmemeval import dataset, report, scoring  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = HERE / ".data"
DEFAULT_RUNS = HERE / "runs"
DEFAULT_PORT = 17333
# The ai-memory evaluator this harness copies (read at this commit).
AI_MEMORY_REFERENCE = {
    "repo": "https://github.com/akitaonrails/ai-memory",
    "commit": "8591d758a13b0f27a14d468312868dd17ff0a5cb",
    "files": ["evals/README.md", "evals/src/retrieval/mod.rs", "evals/src/retrieval/query.rs",
              "evals/src/retrieval/score.rs", "evals/src/retrieval/ingest.rs", "evals/src/retrieval/dataset.rs",
              "docs/benchmarks/longmemeval-s-3.5.0-release.md"],
}

# Query variants, each one MCP ``search`` call on the same ingested store.
# Only arguments that differ from the tool's defaults are sent.
MODES = {
    # The tool as shipped: mode hybrid (dense + full-text, rank fusion), compact, max_chars 4000.
    "hybrid": {},
    # Dense only (Qdrant vector search, no lexical channel).
    "semantic": {"mode": "semantic"},
    # Same rankings with the largest output budget the tool accepts, so all ten hits are shown.
    "hybrid-wide": {"max_chars": 12000},
    "semantic-wide": {"mode": "semantic", "max_chars": 12000},
}
STORED = re.compile(r"Stored \[([0-9a-f]{16})\]")
TOOL_TIMEOUT_S = 900


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"{dt.datetime.now().strftime('%H:%M:%S')} {message}", flush=True)


# ── Environment ─────────────────────────────────────────────────────────────
def bench_env(data_dir: Path, port: int, threads: int | None = None) -> dict:
    """The environment for the harness and every server: inherited IMPRINT_*
    settings are dropped so only this benchmark's data dir, port and the
    shipped defaults apply, and so is Claude Code's session id, which would
    make every server act as the conversation that started the run on the
    Board (ADR 0210)."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("IMPRINT_") and key != "CLAUDE_CODE_SESSION_ID"}
    env.update(IMPRINT_DATA_DIR=str(data_dir), IMPRINT_QDRANT_PORT=str(port),
               IMPRINT_QDRANT_GRPC_PORT=str(port + 1),
               # The shipped default, stated so no LLM can run during ingest or search.
               IMPRINT_LLM_TAGS="0",
               PYTHONPATH=str(REPO))
    if threads:
        env["IMPRINT_ONNX_THREADS"] = str(threads)
    return env


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True).stdout.strip()


def imprint_provenance() -> dict:
    dirty = [line for line in git("status", "--porcelain", "--", "imprint", "requirements").splitlines() if line]
    return {"commit": git("rev-parse", "HEAD"), "describe": git("describe", "--tags", "--always"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"), "imprint_code_clean": not dirty,
            "dirty_files": dirty}


def hardware() -> dict:
    cpu = platform.processor()
    with contextlib.suppress(OSError):
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    memory = None
    with contextlib.suppress(OSError, ValueError, IndexError):
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal"):
                memory = round(int(line.split()[1]) / 1024 / 1024, 1)
    gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                         capture_output=True, text=True).stdout.strip() if shutil.which("nvidia-smi") else ""
    release = platform.release()
    return {"cpu": cpu, "logical_cpus": os.cpu_count(), "memory_gib": memory, "os": platform.platform(),
            "kernel": release, "wsl": "microsoft" in release.lower(), "gpu_present": gpu or None,
            "loadavg_at_start": [round(x, 2) for x in os.getloadavg()]}


def software(port: int) -> dict:
    import fastmcp
    import llama_cpp
    import urllib.request
    qdrant = None
    with contextlib.suppress(Exception):
        qdrant = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read()).get("version")
    return {"python": platform.python_version(), "fastmcp": fastmcp.__version__, "llama_cpp": llama_cpp.__version__,
            "llama_system_info": llama_cpp.llama_print_system_info().decode().strip(),
            "llama_gpu_offload": bool(llama_cpp.llama_supports_gpu_offload()), "qdrant": qdrant}


def imprint_settings() -> dict:
    from imprint import config, config_schema, model_profiles
    keys = ["model.name", "model.revision", "model.file", "model.dim", "model.seq_length", "model.device",
            "model.pooling", "model.query_instruction", "tagger.llm", "tagger.zero_shot", "redact.privacy_filter",
            "recall.llm", "chunker.size_prose"]
    values = {}
    for key in keys:
        with contextlib.suppress(Exception):
            values[key] = config_schema.resolve(key)[0]
    values["resolved_query_instruction"] = config.QUERY_INSTRUCTION
    values["embedding_sha256"] = model_profiles.EMBEDDING_SHA256
    values["model_threads_default"] = "half the logical CPUs (model.threads=0)"
    return values


def _listening_inodes(port: int) -> set[str]:
    inodes = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        with contextlib.suppress(OSError):
            for line in open(table).read().splitlines()[1:]:
                fields = line.split()
                if fields[3] == "0A" and int(fields[1].rsplit(":", 1)[1], 16) == port:
                    inodes.add(fields[9])
    return inodes


def _owner_of(port: int) -> int | None:
    """The pid holding a listening socket on ``port`` (this user's processes only)."""
    inodes = _listening_inodes(port)
    if not inodes:
        return None
    for pid in filter(str.isdigit, os.listdir("/proc")):
        with contextlib.suppress(OSError):
            for fd in os.listdir(f"/proc/{pid}/fd"):
                with contextlib.suppress(OSError):
                    if os.readlink(f"/proc/{pid}/fd/{fd}").removeprefix("socket:[").rstrip("]") in inodes:
                        return int(pid)
    return -1  # held, by a process this user cannot inspect


def own_qdrant(data_dir: Path, port: int) -> bool:
    """Whether the benchmark may use ``port``: nothing listens on it or its
    gRPC neighbour, or both are held by the Qdrant binary in ``data_dir``.
    Imprint treats any server answering on its port as its own daemon, so
    without this check a run could write into another Qdrant."""
    binary = (data_dir / "qdrant-bin").resolve()
    for candidate in (port, port + 1):
        pid = _owner_of(candidate)
        if pid is None:
            continue
        exe = ""
        with contextlib.suppress(OSError):
            exe = os.readlink(f"/proc/{pid}/exe")
        if pid < 0 or not Path(exe).is_relative_to(binary):
            log(f"port {candidate} is held by pid {pid} ({exe or 'unknown'}), not this benchmark's Qdrant "
                f"in {binary}; pick another --qdrant-port")
            return False
    return True


# ── Workspaces (setup and teardown, not measured) ───────────────────────────
_workspace_lock = threading.Lock()


def drop_workspace(ws: str) -> None:
    """Remove a workspace's collection, graph database, WAL and registration."""
    from imprint import config, vectorstore, workspace_policy
    with _workspace_lock:
        client = vectorstore._get_client()
        collection = config.collection_name(ws)
        if client.collection_exists(collection):
            client.delete_collection(collection)
        for path in (config.graph_db_path(ws), config.wal_path(ws)):
            for sibling in (path, Path(f"{path}-wal"), Path(f"{path}-shm"), Path(f"{path}-journal")):
                sibling.unlink(missing_ok=True)
        workspace_policy.remove_workspace(ws)
        config.remove_workspace(ws)


def create_workspace(ws: str) -> None:
    from imprint import config
    drop_workspace(ws)
    with _workspace_lock:
        config.register_workspace(ws)


def leftover_workspaces() -> list[str]:
    from imprint import config
    return [ws for ws in config.get_known_workspaces() if ws.startswith("lme-")]


# ── MCP servers ─────────────────────────────────────────────────────────────
class ToolError(RuntimeError):
    pass


class Server:
    """One ``python -m imprint`` MCP stdio server from this checkout."""

    def __init__(self, name: str, env: dict, cwd: str, log_dir: Path):
        self.name, self.env, self.cwd, self.log_dir = name, env, cwd, log_dir
        self.client = None

    async def start(self) -> None:
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport
        transport = StdioTransport(command=sys.executable, args=["-m", "imprint"], env=self.env, cwd=self.cwd,
                                   keep_alive=True, log_file=self.log_dir / f"{self.name}.stderr.log")
        self.client = Client(transport, cache=False, timeout=TOOL_TIMEOUT_S, init_timeout=300)
        await self.client.__aenter__()

    async def close(self) -> None:
        if self.client is not None:
            with contextlib.suppress(Exception):
                await self.client.__aexit__(None, None, None)
            with contextlib.suppress(Exception):
                await self.client.transport.close()
            self.client = None

    async def call(self, tool: str, arguments: dict) -> tuple[str, float]:
        """The tool's text and the wall clock of the MCP round trip."""
        started = time.perf_counter()
        result = await self.client.call_tool(tool, arguments, timeout=TOOL_TIMEOUT_S, raise_on_error=False)
        elapsed = time.perf_counter() - started
        text = "\n".join(block.text for block in result.content if getattr(block, "type", "") == "text")
        if result.is_error:
            raise ToolError(f"{tool} failed on {self.name}: {text[:500]}")
        return text, elapsed


class Gate:
    """Stores share it; a measured search holds it alone, so the harness's
    own ingestion never competes with the timed call for the CPU."""

    def __init__(self):
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    @contextlib.asynccontextmanager
    async def shared(self):
        async with self._condition:
            await self._condition.wait_for(lambda: not self._writer and not self._waiting_writers)
            self._readers += 1
        try:
            yield
        finally:
            async with self._condition:
                self._readers -= 1
                self._condition.notify_all()

    @contextlib.asynccontextmanager
    async def exclusive(self):
        async with self._condition:
            self._waiting_writers += 1
            await self._condition.wait_for(lambda: not self._writer and not self._readers)
            self._waiting_writers -= 1
            self._writer = True
        try:
            yield
        finally:
            async with self._condition:
                self._writer = False
                self._condition.notify_all()


# ── One question ────────────────────────────────────────────────────────────
class IngestError(RuntimeError):
    pass


async def run_question(q: dict, ingest: Server, query: Server, gate: Gate, modes: dict, keep: bool) -> dict:
    qid = q["question_id"]
    ws, project = scoring.workspace_for(qid), scoring.project_for(qid)
    await asyncio.to_thread(create_workspace, ws)
    key_of_raw = {raw: scoring.stored_session_uuid(qid, raw) for raw in q["haystack_session_ids"]}
    source_of_key = {key: scoring.session_source(key) for key in key_of_raw.values()}
    if len(set(source_of_key.values())) != len(source_of_key):
        raise IngestError(f"{qid}: two sessions share a source key")
    raw_of_key: dict = {}
    for raw, key in key_of_raw.items():
        raw_of_key.setdefault(key, raw)

    id_to_key: dict[str, object] = {}
    store_ms, stored, dedup, skipped = [], 0, 0, 0
    started = time.perf_counter()
    for raw, date, session in zip(q["haystack_session_ids"], q["haystack_dates"], q["haystack_sessions"]):
        key = key_of_raw[raw]
        for turn in session:
            text = scoring.turn_text(turn["role"], date, turn["content"])
            if text is None:
                skipped += 1
                continue
            async with gate.shared():
                out, elapsed = await ingest.call("store", {"content": text, "project": project,
                                                           "source": source_of_key[key], "workspace": ws})
            match = STORED.search(out)
            if not match:
                raise IngestError(f"{qid}: store did not confirm: {out[:300]}")
            memory_id = match.group(1)
            if memory_id in id_to_key:
                dedup += 1
                if id_to_key[memory_id] != key:
                    raise IngestError(f"{qid}: memory {memory_id} stored for two sessions")
            id_to_key[memory_id] = key
            stored += 1
            store_ms.append(elapsed * 1000)
    ingest_seconds = time.perf_counter() - started

    evidence = {key_of_raw[raw] for raw in q["answer_session_ids"]}
    results = {}
    async with gate.exclusive():
        # Unmeasured: the server that stored would already hold this collection's handle.
        await query.call("status", {"workspace": ws})
        load = round(os.getloadavg()[0], 2)
        for mode, extra in modes.items():
            arguments = {"query": q["question"], "limit": scoring.QUERY_LIMIT, "workspace": ws,
                         "project": project, **extra}
            text, elapsed = await query.call("search", arguments)
            hits = scoring.parse_hits(text)
            ranked, unknown = [], 0
            for hit in hits:
                key = id_to_key.get(hit["id"])
                if key is None:
                    unknown += 1
                elif hit["source"] and hit["source"] != source_of_key[key]:
                    raise IngestError(f"{qid}: hit {hit['id']} shows source {hit['source']}, stored {source_of_key[key]}")
                ranked.append(key)
            results[mode] = {
                "latency_ms": int(elapsed * 1000), "latency_ms_exact": round(elapsed * 1000, 3),
                "hits_shown": len(hits), "budget_cut": "More results may exist: offset=" in text,
                "unknown_hits": unknown,
                "context_tokens": scoring.estimate_tokens(sum(len(hit["snippet"]) for hit in hits)),
                "response_tokens": scoring.estimate_tokens(len(text)),
                "ranked_sessions": [raw_of_key[key] if key is not None else None for key in ranked],
                **scoring.score_question(evidence, ranked),
            }
    if not keep:
        await asyncio.to_thread(drop_workspace, ws)
    return {
        "question_id": qid, "question_type": q["question_type"], "question": q["question"],
        "evidence_sessions": sorted(set(q["answer_session_ids"])), "haystack_sessions": len(q["haystack_sessions"]),
        "turns": sum(len(s) for s in q["haystack_sessions"]), "stored": stored, "skipped_turns": skipped,
        "distinct_memories": len(id_to_key), "dedup": dedup, "ingest_seconds": round(ingest_seconds, 3),
        "store_ms_p50": scoring.percentile([int(x) for x in store_ms], 0.5),
        "store_ms_p95": scoring.percentile([int(x) for x in store_ms], 0.95),
        "worker": ingest.name, "loadavg_1m_at_search": load, "finished_at": now(), "modes": results,
    }


# ── Run ─────────────────────────────────────────────────────────────────────
def read_results(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            with contextlib.suppress(json.JSONDecodeError):
                rows.append(json.loads(line))
    return rows


def append_line(path: Path, record: dict) -> None:
    with open(path, "a") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def ids_digest(questions: list[dict]) -> str:
    return hashlib.sha256("\n".join(q["question_id"] for q in questions).encode()).hexdigest()


async def run(args) -> int:
    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    env = bench_env(data_dir, args.qdrant_port)
    os.environ.clear()
    os.environ.update(env)

    dataset_path = Path(args.dataset)
    if args.fetch and not dataset_path.exists():
        log(f"downloading {dataset.URL}")
        dataset.fetch(dataset_path)
    questions, abstention = dataset.select(dataset.load(dataset_path), question_ids=args.question_id,
                                           sample=args.sample, stratify=args.stratify)
    modes = {name: MODES[name] for name in args.modes}
    provenance = imprint_provenance()
    if not provenance["imprint_code_clean"] and not args.allow_dirty:
        log(f"Imprint's code differs from {provenance['commit'][:12]}: {provenance['dirty_files']}. "
            "Commit or stash it, or pass --allow-dirty (recorded in the report).")
        return 2

    run_dir = Path(args.run_dir).resolve() if args.run_dir else (
        DEFAULT_RUNS / (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + (f"-{args.label}" if args.label else "")))
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(exist_ok=True)
    config_path, results_path = run_dir / "config.json", run_dir / "results.jsonl"

    lock = open(data_dir / ".benchmark.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log(f"another benchmark run is using {data_dir}")
        return 2

    from imprint import qdrant_runner
    if not own_qdrant(data_dir, args.qdrant_port):
        return 2
    qdrant_runner.ensure_running()
    if not own_qdrant(data_dir, args.qdrant_port) or _owner_of(args.qdrant_port) is None:
        log("the benchmark's Qdrant is not the one answering; stopping")
        return 2

    if config_path.exists():
        config = json.loads(config_path.read_text())
        if config["dataset"]["sha256"] != dataset.SHA256 or config["selection"]["question_ids_sha256"] != ids_digest(questions):
            log("this run dir was started with another dataset or question selection")
            return 2
        if config["imprint"]["commit"] != provenance["commit"] and not args.allow_commit_change:
            log(f"this run started at Imprint {config['imprint']['commit'][:12]}, the checkout is at "
                f"{provenance['commit'][:12]}; pass --allow-commit-change to mix them")
            return 2
        if list(config["settings"]["modes"]) != list(modes):
            log(f"this run measures modes {list(config['settings']['modes'])}; pass the same --modes")
            return 2
        config.setdefault("sessions", []).append({"started_at": now(), "argv": sys.argv[1:],
                                                  "imprint": provenance, "hardware": hardware()})
    else:
        config = {
            "benchmark": "LongMemEval-S session-level retrieval, ai-memory evaluator method",
            "created_at": now(), "argv": sys.argv[1:], "ai_memory_reference": AI_MEMORY_REFERENCE,
            "dataset": {"name": dataset.NAME, "url": dataset.URL, "sha256": dataset.SHA256, "path": str(dataset_path)},
            "selection": {"sample": args.sample, "stratify": args.stratify, "question_ids": args.question_id,
                          "scored": len(questions), "abstention_excluded": abstention,
                          "question_ids_sha256": ids_digest(questions)},
            "imprint": provenance, "imprint_settings": imprint_settings(),
            "settings": {"modes": modes, "query_limit": scoring.QUERY_LIMIT, "ks": list(scoring.KS),
                         "ingest_workers": args.workers, "ingest_threads": args.threads,
                         "query_threads": "Imprint default", "data_dir": str(data_dir), "qdrant_port": args.qdrant_port},
            "hardware": hardware(), "software": software(args.qdrant_port), "sessions": [],
        }
        config["sessions"].append({"started_at": now(), "argv": sys.argv[1:]})
    config_path.write_text(json.dumps(config, indent=2) + "\n")

    done = {row["question_id"] for row in read_results(results_path)}
    todo = [q for q in questions if q["question_id"] not in done]
    for ws in leftover_workspaces():
        log(f"dropping leftover workspace {ws}")
        drop_workspace(ws)
    log(f"run dir {run_dir}: {len(questions)} scored ({abstention} abstention left out), {len(done)} done, "
        f"{len(todo)} to go; modes {', '.join(modes)}")
    if not todo:
        write_report(run_dir)
        return 0

    server_cwd = tempfile.mkdtemp(prefix="imprint-lme-")
    gate = Gate()
    query = Server("query", bench_env(data_dir, args.qdrant_port), server_cwd, run_dir / "logs")
    workers = [Server(f"ingest-{i}", bench_env(data_dir, args.qdrant_port, args.threads), server_cwd, run_dir / "logs")
               for i in range(min(args.workers, len(todo)))]
    failures = 0
    try:
        await asyncio.gather(query.start(), *(w.start() for w in workers))
        await warm_up(query, workers)
        queue: asyncio.Queue = asyncio.Queue()
        for q in todo:
            queue.put_nowait(q)
        total_turns = sum(sum(len(s) for s in q["haystack_sessions"]) for q in todo)
        progress = {"questions": 0, "turns": 0, "started": time.time(), "failures": 0}

        async def worker(server: Server):
            while True:
                try:
                    q = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    row = await run_question(q, server, query, gate, modes, args.keep_workspaces)
                except Exception as error:  # one question's failure must not lose the run
                    progress["failures"] += 1
                    append_line(run_dir / "errors.jsonl", {"question_id": q["question_id"], "error": repr(error),
                                                           "worker": server.name, "at": now()})
                    log(f"FAILED {q['question_id']} on {server.name}: {error!r}; restarting {server.name}")
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(drop_workspace, scoring.workspace_for(q["question_id"]))
                    await server.close()
                    await server.start()
                    continue
                append_line(results_path, row)
                progress["questions"] += 1
                progress["turns"] += row["turns"]
                rate = progress["turns"] / max(1e-9, time.time() - progress["started"])
                eta_h = (total_turns - progress["turns"]) / rate / 3600 if rate else float("nan")
                head = row["modes"][next(iter(modes))]
                log(f"[{len(done) + progress['questions']}/{len(questions)}] {row['question_id']} "
                    f"{row['question_type']}: {row['stored']} stored in {row['ingest_seconds']:.0f}s | "
                    f"{next(iter(modes))} hit@1/5/10 {head['hit@1']:.0f}/{head['hit@5']:.0f}/{head['hit@10']:.0f} "
                    f"{head['latency_ms']} ms | {rate:.2f} turns/s, ETA {eta_h:.1f} h")
                (run_dir / "progress.json").write_text(json.dumps({
                    "done": len(done) + progress["questions"], "total": len(questions),
                    "failures": progress["failures"], "turns_per_second": round(rate, 3),
                    "eta_hours": round(eta_h, 2), "updated_at": now()}) + "\n")

        await asyncio.gather(*(worker(w) for w in workers))
        failures = progress["failures"]
    finally:
        await asyncio.gather(query.close(), *(w.close() for w in workers), return_exceptions=True)
        shutil.rmtree(server_cwd, ignore_errors=True)
        if not args.keep_qdrant:
            qdrant_runner.stop()
    write_report(run_dir)
    if failures:
        log(f"{failures} question(s) failed (errors.jsonl); run again with --run-dir {run_dir} to retry them")
        return 1
    return 0


async def warm_up(query: Server, workers: list[Server]) -> None:
    """Load every server's embedding model before anything is timed."""
    ws = "lme-warmup"
    await asyncio.to_thread(create_workspace, ws)
    try:
        for server in [query, *workers]:
            await server.call("store", {"content": f"[session date: 2023/01/01 (Sun) 00:00] warm-up {server.name}",
                                        "project": "lme-warmup", "source": "s:0", "workspace": ws})
        await query.call("search", {"query": "warm-up", "limit": scoring.QUERY_LIMIT, "workspace": ws})
    finally:
        await asyncio.to_thread(drop_workspace, ws)


def write_report(run_dir: Path) -> None:
    built, markdown = report.build(run_dir)
    (run_dir / "report.json").write_text(json.dumps(built, indent=2) + "\n")
    (run_dir / "report.md").write_text(markdown)
    log(f"report: {run_dir / 'report.md'}")
    print(report.summary_table(built), flush=True)


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"run", "report", "fetch", "-h", "--help"}:
        argv = ["run", *argv]
    parser = argparse.ArgumentParser(prog="evals/longmemeval/run.sh", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run or resume a benchmark (the default)")
    r.add_argument("--dataset", default=str(dataset.DEFAULT_PATH))
    r.add_argument("--fetch", action="store_true", help="download the pinned dataset when it is missing")
    r.add_argument("--sample", type=int, help="first N dataset questions (abstention ones then dropped)")
    r.add_argument("--stratify", type=int, help="first N scored questions of each type")
    r.add_argument("--question-id", type=lambda s: [x for x in s.split(",") if x], default=[],
                   help="comma-separated question ids")
    r.add_argument("--run-dir", help="resume this run dir, or start one there")
    r.add_argument("--label", default="", help="suffix for a new run dir's name")
    r.add_argument("--modes", type=lambda s: [x for x in s.split(",") if x], default=list(MODES),
                   help=f"comma-separated, from {', '.join(MODES)}")
    r.add_argument("--workers", type=int, default=4, help="questions ingested at once, one MCP server each")
    r.add_argument("--threads", type=int, default=5, help="llama.cpp threads per ingest server")
    r.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="the benchmark's own Imprint data dir")
    r.add_argument("--qdrant-port", type=int, default=DEFAULT_PORT, help="its Qdrant HTTP port (gRPC: +1)")
    r.add_argument("--keep-workspaces", action="store_true", help="keep each question's workspace (debugging)")
    r.add_argument("--keep-qdrant", action="store_true", help="leave the benchmark's Qdrant running at the end")
    r.add_argument("--allow-dirty", action="store_true", help="run although imprint/ differs from HEAD")
    r.add_argument("--allow-commit-change", action="store_true", help="resume at another Imprint commit")
    rep = sub.add_parser("report", help="rebuild report.md/json from a run dir")
    rep.add_argument("run_dir")
    f = sub.add_parser("fetch", help="download and verify the dataset")
    f.add_argument("--dataset", default=str(dataset.DEFAULT_PATH))
    args = parser.parse_args(argv)
    if args.command == "fetch":
        if Path(args.dataset).exists():
            dataset.load(Path(args.dataset))
            log(f"{args.dataset} matches sha256 {dataset.SHA256}")
        else:
            dataset.fetch(Path(args.dataset))
            log(f"downloaded {args.dataset} (sha256 {dataset.SHA256})")
        return 0
    if args.command == "report":
        write_report(Path(args.run_dir).resolve())
        return 0
    unknown = [m for m in args.modes if m not in MODES]
    if unknown:
        parser.error(f"unknown modes {unknown}")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
