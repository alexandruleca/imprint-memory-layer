---
title: ADR 0082 — Retain readable conversation evidence
description: Learn ordered readable conversation events including available analysis and tool evidence, with client context and unavailable content explicitly distinguished.
---

- Status: Amended
- Date: 2026-10-26
- Amends: [ADR 0079](0079-learn-only-what-the-claude-code-user-typed.md) — retain client-written context as labelled evidence while keeping human-question attribution
- Amended by: [ADR 0088](0088-bytes-a-crash-never-wrote-are-counted-omissions.md) — a line made malformed only by NUL bytes no longer fails the reading
- Related: [ADR 0034](0034-learn-from-client-stores-by-conversation-ownership.md), [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md), [ADR 0080](0080-conversations-are-relearned-when-their-reading-changes.md), [ADR 0081](0081-verifiable-redacted-source-snapshots.md)

## Context

The owner wants findings, available analysis and decision rationale retained to improve future recall. Legacy learning extracts Q+A text, drops brief answers and loses tool evidence. On this device's audit corpus, 15,140 tool-use blocks were excluded; 158 Codex reasoning records had no readable summary. Generic XML cleanup also removed an HTML-only user request and its answer. Treating client notifications as human questions was already ruled out by ADR 0079.

## Decision

Learn an ordered readable event representation from owned client transcripts. Preserve user and assistant text, brief messages, available visible analysis/reasoning summaries, tool calls and outputs and readable attachment contents. Include event role, kind, human turn and available timestamps/tool-call IDs. Label injected client content as `context` rather than a human message. Preserve user markup; client-specific query wrappers are handled using their explicit format rather than generic XML deletion.

Do not decode, infer or fabricate inaccessible model thinking. Count recognized opaque reasoning and binary attachments in source metadata; the source is explicitly not an exact copy of transcript JSON. Fail malformed transcripts before claiming a complete reading. Existing ownership rules still exclude unrelated/subagent transcript files.

Redact the whole representation before source snapshots, chunk IDs, embeddings or prompts. Keep model findings and rationale as attributed evidence, without promoting them to facts. Queue background LLM tagging only for chunks containing human/assistant messages or analysis; tool-only/context-only chunks use deterministic metadata. Learner version 3 refreshes prior owned conversations once.

## Rejected alternatives

- **Keep only final answers and generated summaries.** The audit and paired synthetic retrieval cases show missing tool-only facts, short decisions and available analysis; a summary cannot guarantee preserving that evidence.
- **Flatten every event into a question and answer.** It would undo ADR 0079's attribution fix and make client/system/tool context look like a human request.
- **Preserve raw transcript JSON as the authoritative source.** It includes opaque/binary payloads and client bookkeeping outside this readable-memory contract; source reconstruction must state its scope honestly.

## Consequences

More evidence becomes available for search and full-source reads, at substantially higher conversation embedding and storage cost. The paired local corpus grows from 1,580 to 3,586 chunks and from 2.65 to 19.12 million indexed characters. Twelve synthetic evidence queries improve from 4/12 to 12/12 at rank one; this is a targeted regression result, not a general answer-quality claim. Event adapters still need maintenance as clients change. Reading available analysis does not recover encrypted reasoning or guarantee every unknown event field is supported.

## Invalidation conditions

- If a client changes its event schema or attribution markers, update its adapter and fixtures before treating a reading as complete.
- If held-out recall measurements show tool/context noise crowding out useful evidence, change search selection or add explicit event filters while retaining the canonical readable source.
- If actual embedding latency or storage measurements make full event indexing impractical, evaluate selective searchable spans backed by the same complete source, with explicit coverage semantics.

## Validation

Native macOS arm64: conversation fixtures cover short text, user markup, tool arguments/results, analysis, client-injected context, unavailable reasoning and malformed JSON. Client-store tests cover Claude, Cursor, Codex, Cline and Copilot representations. A paired synthetic experiment uses the real local embedding model and hybrid retrieval with evidence-bearing chunk checks; only aggregate coverage data from the owner's 171 transcripts is retained. No production reindex, downstream answer-quality evaluation, or Windows/Linux execution was performed for this change.
