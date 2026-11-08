---
title: ADR 0160 — Chat's search filters are hints, and repeated reads are not run again
description: When the chat model filters a search by tags it guessed, the result also lists up to three matches the filters left out; a read call repeated with the same arguments in one reply returns a note pointing to the earlier result instead of running again.
---

- Status: Accepted
- Date: 2026-11-08
- Related: [ADR 0102](0102-chat-research-follows-progress-and-resource-budgets.md) (no-progress patience, which the note feeds), [ADR 0157](0157-chat-research-is-charged-for-the-work-each-request-does.md) (research token charges), [local models](../local-models.md#how-chat-researches)

## Context

A live run of the chat pipeline on the maintainer's machine (Windows, Radeon 8060S, Qwen3.5-4B Q4_K_M, `chat.n_ctx` 65,536) asked six questions whose answers are in 38 indexed docs (`~/.cache/imprint-eval/eval_chat.py`). Five answers held their facts. The sixth, "What port does the local Qdrant daemon listen on, and which ADR made Qdrant a server?", named the wrong ADR after 13 tool calls and 421 seconds:

- Every search the model wrote carried `domain: "infra,db"`, a filter it guessed. ADR 0063, the answer, is not tagged with those domains. The same query ranked an ADR 0063 chunk second without the filter and left ADR 0063 out entirely with it.
- The model then ran the identical search (same query, same filter) four times in a row before the no-progress patience ended research. Each repeat cost a model round and a search for the same result.

## Decision

1. **Guessed filters are hints.** When chat's `search` tool is called with any of `domain`, `layer`, `lang`, `kind` or `type`, it also runs the query without those filters (keeping `project`, `source`, `limit` and paging) and appends up to three results the filtered search did not return, under "Also matching without <filters> (these memories are not tagged that way)". Searches without such filters run once and are unchanged. The MCP `search` tool is unchanged: an agent that sets a filter there gets the filter.
2. **Repeated reads are not run again.** Within one reply, a call to a read tool with the same name and the same arguments (compared as sorted JSON) as an earlier call returns `[repeated call] … already ran with these arguments in this reply (call N); its result is above …` instead of running. Writes, the personal scheduling tools and `ask_user` always run. The note counts as no new evidence for the no-progress patience.

## Rejected alternatives

- **Remove the filter parameters from chat's search tool.** Filters are useful when they are right (a Python bug with `lang=python`), and the strategy prompt recommends them; the evidence shows a wrong guess, not that filters never help.
- **Drop the filters silently when they return few results.** The model would not learn that its filter was wrong, and a filter returning many weak results would still hide the right one, as here (four results came back).
- **Rely on the no-progress patience alone for repeats.** It did stop the loop, after four identical rounds; each of those rounds was a full model turn on the 4B model.

## Consequences

A filtered chat search runs two queries and its result is up to three hits longer. With the change the same question took one search and 87 seconds, and ADR 0063's chunks were in the result; the 4B model still named ADR 0024, which the result also showed, so this fixes retrieval, not the model's reading of it. A legitimate repeat (re-reading after a write in the same reply) gets the note instead of a fresh result; status-style tools whose output changes during a reply are affected the same way.

## Invalidation conditions

- If filtered searches routinely push the filtered results out of the model's context, then show fewer unfiltered extras or only when the filtered search returns weak matches.
- If a reply needs to re-run a read after its own write (for example `kg_query` after `kg_add`), then reset the repeat record after a write.
- If tag quality improves so that guessed filters rarely exclude the answer (measured on stored replies), then reconsider the extra query.

## Validation

- `tests/test_chat_tool_coverage.py`: guessed filters add the unfiltered extras (without the paging footer) and keep `project`; unfiltered searches run once unchanged; nothing is added when the filters left nothing out; a repeated read returns the note whatever the argument order; writes and questions always run; the note is not new evidence.
- `tests/test_chat_stream.py`: research continues beyond twelve rounds while searches add evidence, with the repeated `status` call run once.
- The live question above: before, 13 calls, 421 s, wrong ADR; after, 1 call, 87 s, ADR 0063 in the result, answer still citing ADR 0024.
