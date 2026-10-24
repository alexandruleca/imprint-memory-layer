---
title: ADR 0041 — PersonaPlex startup memory prototype
description: Keep the experimental voice model in a separate runtime and supply a bounded read-only MCP snapshot at startup.
---

- Status: Accepted (experimental adapter only; real speech and platform support remain unvalidated)
- Date: 2026-10-24
- Related: [ADR 0004](0004-runtime-dependencies-and-local-boundaries.md), [ADR 0021](0021-budgeted-mcp-evidence-and-selective-expansion.md), [ADR 0024](0024-enforce-local-service-boundaries.md), [ADR 0038](0038-gpu-runtime-ships-cuda-libraries.md)

## Context

The user requested starting voice/avatar work on a separate branch after considering PersonaPlex with Imprint MCP, and confirmed English first. PersonaPlex supplies a full speech conversational model but has no stock live tool-result interface. Imprint's retrieval does not require the dashboard Qwen model. A bounded startup memory experiment can establish a useful integration seam before attempting live tools or a finished avatar. See the [feasibility assessment](../voice-avatar-feasibility.md).

The inspected upstream server starts on import, logs its system prompt and accepts `--host` without forwarding it to its actual listener. Passing memory through the stock browser's `text_prompt` query would expose it to URL/console logs. Its PyTorch/CUDA runtime should not modify Imprint's existing Python/GPU dependency policy.

## Decision

Implement an explicitly invoked Python prototype with two steps: prepare an exclusive plaintext session export through local stdio MCP, then serve that snapshot with a separately provisioned PersonaPlex interpreter. Require an explicit data directory and workspace; call only read-only `device_context` and compact `search`. Preserve whole evidence responses and provenance within a character budget. Do not claim token budgeting or live memory operations.

Pin the external source commit and server checksum, require a clean tracked checkout, and pin model repository revision. Model/voice/client assets must already be cached; the adapter forces cache-only lookups. External dependency provisioning remains operator-managed and experimental, with no installer or production runtime-profile change.

Remove only the verified source's exact final startup block in memory, install narrow adapters, and invoke the original server main. Inject memory server-side at connection startup, redact its prompt log and disable access logs. Enforce a literal loopback listener, peer/Host/Origin validation, a browser capability cookie and bundled voice filenames. Start and stop only the child runtime owned by the launcher. Keep test-only aiohttp dependencies in a separate hashed overlay used by CI, not installed in product profiles; exclude its definitions/lock from release staging and reject them in archive validation.

## Alternatives

- Connect MCP directly to PersonaPlex: the protocol client cannot provide the missing model/tool-result interface.
- Build the ASR → existing chat → TTS cascade first: remains the recommended architecture when retaining the same chat model is required, but does not explore the user's PersonaPlex question.
- Put the prompt in browser query parameters or process arguments: unnecessarily spreads memory content into histories and diagnostics.
- Vendor or modify NVIDIA's whole application: increases maintenance and distribution obligations before the hardware experiment succeeds.
- Install PyTorch into Imprint's existing environment: couples an optional research stack to embeddings and ordinary memory use.

## Consequences

This yields a reviewable startup-snapshot experiment with actual MCP and transport tests. It does not establish a working speech demonstration, bounded interruption, tool use, persistent voice history, installer support or a 3D avatar. Every conversation served by one launcher uses its prepared snapshot; changing context requires a new snapshot/restart. The export contains sensitive plaintext and must be kept in a private location. Windows ACLs inherit from that directory.

The source adapter is deliberately revision-specific and fails on drift. A source/model pin does not fully lock the external environment or verify every weight artifact. Before supported distribution, produce hashed runtime/weight inventories, audit the bundled web client and run native GPU, microphone, lifecycle and interruption checks. The implementation does not change dashboard chat, real client registrations or user memory schemas.

## Validation

Linux/WSL2 focused tests exercise real MCP discovery/calls (including a stdio child) with disposable fixture storage, workspace isolation, read-only routing, bounded/escaped evidence, export validation, source drift checks, browser connection boundaries, server-side prompt injection, binary WebSocket transport, cache-only model lookup and owned-child shutdown. No real model or audio device is used by those tests.

Native CI installs the test overlay and discovers the tests on its existing OS matrix; configuring that job is not evidence that native CI ran. Record executed results and remaining limitations in the [prototype guide](../personaplex-prototype.md) and [platform validation](../platform-validation.md).
