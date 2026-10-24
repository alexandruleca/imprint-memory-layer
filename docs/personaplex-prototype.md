---
title: PersonaPlex memory prototype
description: Experimental English voice launcher with a read-only Imprint MCP memory snapshot and an isolated NVIDIA runtime.
---

## Current scope

This branch implements the first PersonaPlex experiment from the [feasibility plan](voice-avatar-feasibility.md): retrieve a bounded memory snapshot through Imprint MCP, then inject it into a separately provisioned PersonaPlex server at conversation startup. English is the confirmed first language. [ADR 0041](adr/0041-personaplex-startup-memory-prototype.md) records the boundary and runtime decisions.

Implemented commands:

- `python -m imprint.personaplex prepare`: discover read-only tools, validate an explicit workspace, perform up to four compact searches, and create a session JSON file.
- `python -m imprint.personaplex serve`: validate the session and pinned source checkout, then start the external Python runtime with server-side context injection.

This is an experimental source-checkout workflow. The integration has protocol/fixture validation, **not a completed real-speech demonstration**. It does not add dashboard controls, a floating head, live MCP calls, memory writes, conversation persistence or a production voice provider. Restart with a freshly prepared snapshot to change memory context. The earlier estimate for the complete voice/avatar product remains a planning estimate, not work completed here.

## Prepare memory with Imprint's Python

Use the provisioned Imprint interpreter, in the repository root. Specify the existing data directory and workspace deliberately. The following paths are examples; no profile setup or re-registration is involved:

```bash
/path/to/imprint-python -m imprint.personaplex prepare \
  --data-dir /path/to/imprint/data \
  --workspace default \
  --project my-project \
  --query "architecture decisions and current work" \
  --output /path/to/private/voice-session.json
```

Repeat `--query` up to four times. Omit `--project` for workspace-wide retrieval. `--max-chars` defaults to 8000 and accepts 2048–16000 **characters**, not tokens; each compact search requests at most 1200 characters. Entire search responses that exceed the remaining rendered prompt budget are omitted, with their zero-based query indexes reported. The process fails if none fit. Empty retrieval is still evidence that nothing relevant was found, not a license to invent project facts. `--timeout` defaults to 120 seconds per MCP operation, including potentially cold embedding work.

The MCP child uses this interpreter and the repository's `python -m imprint` entry point with explicit data/workspace environment settings. It calls only `device_context` and `search`; it does not load the dashboard chat model or invoke writes. Search may initialize embeddings, caches and the managed Qdrant daemon as ordinary Imprint retrieval does. This is read-only with respect to memories, not a claim of zero runtime/cache writes. A shared Qdrant daemon is not stopped when preparation ends.

The output is an explicit plaintext export containing memory excerpts and source references. It is created exclusively, with mode 0600 on POSIX, and existing files are never overwritten. Windows permissions inherit the destination directory's ACL: choose a private directory. Keep exports outside version control and distributions. Preparing a new snapshot requires a new output name or deliberately removing the old export. The command prints counts and paths, not memory content.

## Provision a separate PersonaPlex environment

Do not install PersonaPlex, PyTorch or its CUDA dependencies into Imprint's Python environment. Follow [NVIDIA's prerequisites and installation guide](https://github.com/NVIDIA/personaplex/tree/3428dfd95309a7f3c84fd93259ded0f810d1ff91) using a disposable or dedicated environment. This prototype does not automate those installs and does not yet provide a hashed lock for that external model stack.

The source contract is:

| Artifact | Pinned identity |
| --- | --- |
| NVIDIA source checkout | `3428dfd95309a7f3c84fd93259ded0f810d1ff91` |
| `moshi/moshi/server.py` SHA256 | `cdb9f68a326328308fa4acd43e7e75d79030d2214c43f828d186a0e03a13da99` |
| Hugging Face repository | `nvidia/personaplex-7b-v1` |
| Model repository revision | `fdaf4090a61cb315c138a1faee287ffd6c716309` |

For example, check out the source with:

```bash
git clone https://github.com/NVIDIA/personaplex.git /path/to/personaplex
git -C /path/to/personaplex checkout 3428dfd95309a7f3c84fd93259ded0f810d1ff91
```

Accept the model's access terms on Hugging Face with your own account, authenticate the external environment, and explicitly download the pinned snapshot before serving. Using that environment's Hugging Face CLI:

```bash
hf download nvidia/personaplex-7b-v1 \
  --revision fdaf4090a61cb315c138a1faee287ffd6c716309
```

This can download many GB. The launcher itself requires cached model assets and forces its model, voice and client-bundle lookups to that revision with `local_files_only=True`; it does not silently fetch another version. Code is MIT; weights carry separate NVIDIA Open Model terms. These source/model pins establish the integration baseline, not an audited or reproducible external dependency environment. Weight integrity inventory, a hashed external runtime lock and actual GPU execution are required before distributing this as a supported feature.

## Launch the voice experiment

```bash
/path/to/imprint-python -m imprint.personaplex serve \
  --session /path/to/private/voice-session.json \
  --source /path/to/personaplex \
  --runtime-python /path/to/personaplex-venv/bin/python \
  --cpu-offload
```

Use the external environment's `Scripts/python.exe` path on Windows. `--cpu-offload` requires `accelerate` in that environment and may be necessary on the 12 GB laptop GPU; no real-time performance is claimed. Without it, PersonaPlex uses its upstream GPU behavior. GPU selection follows the external runtime, for example an explicitly selected `CUDA_VISIBLE_DEVICES`, rather than Imprint's embedding settings.

Open `http://127.0.0.1:8998` after upstream finishes loading and warming up. Use the upstream page's voice selection and microphone controls. Its text-prompt control is overridden by the prepared Imprint snapshot for every connection. `--port` selects another unprivileged port. The page and socket stay on loopback; no tunnel/public-host option is exposed. Loopback HTTP is a potentially trustworthy browser origin, but microphone availability still depends on the browser/OS. If HTTPS is needed, `--ssl-dir` forwards an explicit certificate directory to NVIDIA's certificate helper; native certificate/microphone behavior remains untested.

The first page request sets an HttpOnly, SameSite=Strict session cookie. The audio WebSocket requires that cookie and an exact local Origin, plus a loopback peer and matching Host. Host/Origin alone are not authentication against local processes. The capability protects the browser session; it is not an external service credential.

The adapter injects the snapshot inside the server, rather than embedding memories in a browser URL or process command line. It redacts upstream's explicit prompt log and disables HTTP access logging. It admits only the 18 bundled voice filenames and forces the actual listener to `127.0.0.1`—the pinned upstream source accepts `--host` but omits it from `web.run_app`. It removes only the exact known final auto-start block from the pinned server in memory, installs these adapters, then calls its original `main()` under `torch.no_grad()`; it does not edit the external checkout.

Ctrl+C stops the child runtime started by this launcher, escalating after ten seconds if needed. Closing a browser tab ends that conversation but leaves the explicitly launched model server available. Stop the launcher to release it. No other Imprint process, shared Qdrant daemon or unrelated PersonaPlex server is killed. The upstream client may retain conversation audio in browser memory and offer recording downloads; this prototype has not audited that client as a production Imprint UI.

## Validation and remaining gates

Install the test-only overlay into a development/test environment, never a distribution:

```bash
python -m pip install --require-hashes -r requirements/personaplex-test.lock.txt
python -m unittest discover -s tests -p test_personaplex.py -v
```

The overlay pins aiohttp and its dependencies against Imprint's existing base lock. CI installs it only for tests. Release staging excludes both overlay files, and the archive validator rejects them if present. Refresh deliberately with:

```bash
uv pip compile requirements/personaplex-test.txt -c requirements/base.lock.txt \
  --universal --python-version 3.12 --generate-hashes \
  -o requirements/personaplex-test.lock.txt
```

Focused tests cover actual MCP transport with disposable storage fixtures, workspace/project scoping, source references, prompt escaping/budgets, exclusive exports, source drift rejection, local browser boundaries, context injection, binary WebSocket frames, pinned cache-only asset lookup and owned-child shutdown. Binary fixture frames validate transport plumbing, not Opus decoding, speech quality or interruptions. Retrieved text remains untrusted reference material; structural escaping is not a model-level prompt-injection defense.

Executed on 2026-10-24 under Linux/WSL2: all 12 adapter tests and the full 287-test Python suite pass; an actual clean NVIDIA checkout passes the source revision/checksum check; documentation build/link checks pass; the real rsync staging filter excludes the test overlay and release-validator fixtures reject accidental inclusion. A metadata-only request for the pinned model returned `GatedRepoError` with the available credentials. No weights were downloaded, and real model inference/audio-device checks could not proceed. See [platform validation](platform-validation.md#personaplex-startup-memory-prototype-2026-10-24).

Next gates are a real pinned-runtime import/model load, audible English replies grounded in a disposable memory corpus, interruptions with headphones and speakers, VRAM/latency measurement and native OS testing. Dynamic MCP tools and the floating head follow separate implementation work after this startup-snapshot experiment is measured.
