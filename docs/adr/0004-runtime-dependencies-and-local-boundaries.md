---
title: ADR 0004 — Runtime dependencies and local boundaries
description: Resolve base dependencies, restrict browser origins and make process health probes non-destructive.
---

- Status: Amended
- Date: 2026-10-23
- Amended by: [ADR 0007](0007-verified-runtime-and-desktop-baseline.md) — implements the deferred hash locks, Electron upgrade and Host defense; the consequences below preserve the original decision context
- Restructured: 2026-10-25

## Context

Unbounded Python requirements installed a Qdrant client incompatible with the pinned server's supported version range. The local API exposes filesystem/command operations with wildcard CORS. Electron omitted preload, disabled its sandbox and accepted URL hostname prefixes. Python's Unix-style process probes can terminate processes on Windows.

## Decision

Keep human-maintained `requirements/base.txt` and commit a universal Python 3.12 `base.lock.txt`; prefer the lock during bootstrap. Bound Qdrant client versions to the pinned server's compatible minor range. Declare multipart uploads and websockets directly. Materialize ONNX graph/weights/tokenizer together as regular files in the Imprint data directory to satisfy external-weight path validation; reuse that graph for CPU fallback. This can redownload weights after upgrade. Refresh dashboard dependencies within the current major version to remediate available advisories, preserving a lockfile.

Reject remote browser Origin headers and restrict CORS to loopback dashboard origins. Electron accepts an HTTP loopback dashboard, compares exact origins, opens only HTTP(S) external links and runs its preload in a sandboxed renderer. Use a shared Win32 liveness check rather than `os.kill(pid, 0)` on Windows.

## Rejected alternatives

Lower bounds alone make clean installs change over time. Silencing the Qdrant compatibility warning hides a real support constraint. CORS alone does not prevent requests from being sent. Disabling the Electron sandbox is unnecessary for the existing contextBridge preload. Adding psutil only for process probes adds an avoidable native dependency.

## Consequences

Remote browser dashboards are not supported by this local-origin policy. This is not remote authentication, DNS rebinding defense or a filesystem sandbox. Dependencies remain updateable through an explicit lock refresh. Base lock hashes, GPU/LLM locks, signed downloads, a supported Electron upgrade and fully offline installation remain future work.

## Invalidation conditions

- If Qdrant guarantees client compatibility across server minor versions, the client bound can widen to match.
- If ONNX Runtime accepts external weights outside the graph's directory, copying graph, weights and tokenizer into the data directory can stop.
- If remote browser access to the dashboard becomes a supported use, this local-origin policy must give way to real authentication; the record states it is not remote authentication.
- If the Electron preload needs Node APIs that a sandboxed renderer cannot provide, the sandbox must be revisited; it was kept because the contextBridge preload does not need them.

## Validation

Native-process regression test checks probes leave a child running; origin/navigation tests reject lookalike hosts and unwanted schemes; runtime smoke checks MCP, dashboard, origin rejection and actual Qdrant storage. These results are bounded by the platforms actually executed in the validation record.
