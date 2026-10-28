---
title: ADR 0093 — Sequential chat section analysis
description: Two configurable logical workers analyze oversized chat inputs and evidence before coordinator synthesis.
---

- Status: Accepted
- Date: 2026-10-28
- Scope: dashboard chat and the same-chat voice route
- Related: [ADR 0059](0059-chat-speaks-each-models-native-tool-protocol.md), [ADR 0061](0061-local-tool-call-formats.md), [ADR 0092](0092-voice-generation-follows-playback.md)

## Context

The user requested section workers for chat context, defaulting to two sequential
workers and configurable in Settings. The local chat default is an 8,192-token
context. Existing fitting removes old conversation turns and eventually old
tool exchanges; oversized current input fails. Tool results were clipped to a
4,000-character prefix, which can lose relevant tail evidence before reasoning.
The local model's shared inference lock already serializes inference.

## Decision

Add `chat.analysis_workers`, default 2, with accepted values 0–8 through the
existing configuration API, CLI, environment precedence and Settings choices.
Zero disables analysis. For oversized current input or tool evidence, run at
most that many logical note-taking workers, strictly one at a time using the
same chat provider/model. Workers have no tools and never invoke the coordinator
recursively. Short requests retain the direct path, and old conversation history
is still dropped first.

Process contiguous source sections, with 64 characters of overlap and a
preference for newline/word boundaries. Size each fully formatted worker prompt
including output headroom. Limit worker contexts to 4,096 tokens, generation
to 120 seconds, notes to at most 384 output tokens, sources to 1,000,000 characters
and total analyzed sections to 64 per reply. Worker assignments rotate and each
worker rewrites its own bounded notes as more sections arrive. Count every
section; fail explicitly if a limit or worker failure prevents complete coverage.

Retain originals under random reply-specific IDs in memory. A tool-capable
coordinator gets an extra read-only `read_chat_section` function that validates
IDs and returns exact bounded character ranges. It cannot open paths, access
another reply or select a different workspace. Its schema is included in
coordinator budgeting. Original user messages and tool results follow existing
chat persistence; worker notes/progress and the in-memory originals registry are
not separately persisted. No global tool registry is mutated.

Use actual local template/tokenizer counts. Remote chat uses the configured
`chat.n_ctx` budget and an explicit UTF-8-byte-based estimate because its model's
tokenizer and server context are not available locally. Preserve the configured
output budget for coordinator synthesis. With analysis enabled, current tool
results are passed whole when they fit and analyzed when they do not; disabling
it restores the prior prefix-clipping behavior.

Report analysis progress separately from reply tokens, including through voice,
so worker notes are never spoken or shown as the final answer. Cancellation
closes a worker's generator before another section can run. Existing native
prefill and shared-inference interruption limits remain.

## Rejected alternatives

- Run workers concurrently: the user requested sequential execution, and the
  local inference lock serializes the selected model already.
- Treat the worker setting as a maximum number of sections: evidence beyond the
  configured count would remain unread. Workers instead process multiple sections.
- Increase context alone or keep clipping evidence: neither supplies bounded
  section analysis on the existing model or preserves tail evidence for review.
- Give workers the coordinator's tool authority: section analysis needs no tools;
  the existing coordinator retains control of reads and writes.

## Consequences

Questions and tool evidence can exceed one prompt window without automatically
dropping their tail. More workers preserve more separate notes, consume space
in the coordinator and may increase inference cost; they do not add concurrent
speed. Notes are lossy and may omit or misstate facts. Coverage and exact rereads
establish access to originals, not semantic correctness or prompt-injection
resistance. Remote context accounting remains an estimate. No dependency, model
or platform-support change is introduced.

## Invalidation conditions

- If a remote provider exposes an offline tokenizer and verified context limit,
  replace its estimated accounting with that evidence.
- If measured answer quality loses decisive facts during rolling-note updates,
  revise the reduction strategy before expanding input limits.
- If shared local inference supports useful concurrent calls and the user wants
  concurrency, reconsider scheduling using measured latency and memory use.

## Validation

On Linux/WSL, all 14 section-analysis tests and the targeted chat streaming,
voice, ADR, tool-format and configuration/token suites passed. These cover
contiguous Unicode coverage, tail evidence,
worker counts, sequential execution, local/remote coordinator integration,
reply-local exact rereads, cancellation and explicit failure paths.

An isolated checkout containing the chat/settings/voice changes passed its
production build and all 43 Chromium tests across those three surfaces. The
working checkout also passed TypeScript checking and targeted lint after ongoing
unrelated UI changes were updated. No unrelated source edits were reverted for
validation.
The working checkout's production build and both new chat-progress/settings
browser cases also passed against that build.

A real Qwen3.5-4B Q4_K_M CPU smoke on Linux/WSL used the pinned model, a temporary
data directory, two workers and a 2,048-token context. It processed a synthetic
2,796-token prompt in two sections; the resulting coordinator prompt was 818
tokens. The generated answer retained the initial port, its later correction
and a timeout from the middle of the source. This is a small functional check,
not a latency or quality benchmark. Native Windows/macOS execution and broader
analysis quality remain unverified.
