---
title: ADR 0143 — Agents receive generalised guidelines; core memories are kept as records
description: Specific core memories are recorded without approval and never sent to a session; Organize drafts generalised guidelines from them, the user approves those, and every agent session receives the active guidelines within 3,000 estimated tokens.
---

- Status: Amended
- Date: 2026-11-06
- Amends: [ADR 0129](0129-core-memories-are-user-approved-and-start-every-session.md) — sessions receive guidelines, not every approved core memory; agents' and Organize's statements are recorded without approval; the budget is 3,000 estimated tokens instead of 2,000 characters
- Amended by: [ADR 0144](0144-newer-core-memories-that-contradict-older-ones-wait-for-the-user.md) — groups and rewrites are also checked for contradictions the user settles, statements reach the model oldest first, and a pass makes up to 160 model calls
- Related: [ADR 0099](0099-sources-can-belong-to-several-projects.md), [ADR 0130](0130-reorganization-is-proposed-by-a-background-job-and-reviewed.md), [ADR 0132](0132-organize-runs-in-the-idle-slot-after-background-tagging.md), [ADR 0136](0136-review-pages-use-paged-tabs-and-decisions-reopen.md), [ADR 0138](0138-memories-that-wait-for-llm-tagging-travel-to-a-paired-device-that-tags.md), [dashboard](../dashboard.md), [sync](../sync.md)

## Context

ADR 0129 gave every session the user's approved core memories, within 2,000 characters. By 2026-11-06 the maintainer's store looked like this (`data/core-memories.sqlite3` on the maintainer's laptop):

- **Volume:** 350 active core memories, all proposed by Organize and approved within about two minutes that day. Together they were 23,448 characters.
- **What sessions got:** the budget held the 25 oldest, then "… 325 more". Every Claude Code and Cursor session therefore started with lines such as "Prefers bottle feeding over breastfeeding" and "Asks about sugar and calorie comparisons between sodas", presented as the user's approved instructions.
- **Not preferences:** 81 of the 350 begin "Asks about", "User asks", "User seeks", "User is", "User has" or "User requested". They are the topic of one conversation.
- **Units:** the budget is 2,000 characters, roughly 500 tokens. The user took it for 2,000 tokens.

The user asked to keep every core memory recorded for reference, and to have Organize propose generalised memories that apply broadly. Those would be given to agents as guiding instructions, like a `CLAUDE.md`, within a 3,000-token budget. Asked whether coding agents and the assistant (Imprint's chat, Echo, desktop apps) should get different ones, the user chose that every approved guideline goes to every agent.

**Prototype on the maintainer's 350 records** (local Qwen3.5-4B Q4_K_M and Qwen3-Embedding-0.6B, the models Organize and indexing already use):

- **Embedding.** All 350 statements embedded in 4.4 s. Pairwise cosine similarity: median 0.41, 90th percentile 0.57, 99th percentile 0.71.
- **Grouping.** Average-link grouping at 0.55 kept groups to one subject: concise answers, a child's food, travel with a toddler, the dog's health, investing. At 0.5 groups mixed subjects, for example "Asks about social inquiry" next to insurance comparisons.
- **Groups of up to 25.** Drafts joined unrelated details ("…for children, infants, and specific devices (like the M3 MacBook Air or IKEA furniture)…") and kept ages and times.
- **Groups of up to 12, stricter prompt.** The prompt asked for an instruction to the assistant, left out ages, dates and one-off questions, and did not combine subjects. Its 24 drafts read like standing instructions, for example "Prioritize family-friendly itineraries with relaxed pacing, short travel times, and accommodations suitable for toddlers." Each took 3–9 s.
- **Duplicates.** Drafts repeated each other: lactose- and gluten-free food came up three times. Embedding similarity could not find the repeats, because short imperative sentences scored 0.79–0.91 against each other whatever their subject.
- **Merging.** One model call over the 24 drafts merged the two infant-care drafts and the two food-allergy drafts in 7.3 s, and left the two travel drafts apart.
- **One risky draft.** Two records ("Prefers information in Romanian", "Prefers Romanian language") produced "Always respond in Romanian". Applied to every session, that would be wrong.

## Decision

**Core memories have two tiers.**

- **Records** are the specific statements agents and Organize find. A `store` with type `preference` records one, and Organize records them from preference memories as before. A record is kept (active) or retired by the user. It is never a proposal and needs no approval, because no session receives it. The dashboard lists records for reference. The user can edit or retire one, and can make a guideline from one or several of them.
- **Guidelines** generalise records. Each names the records it covers. The user writes one, active at once, or approves one Organize proposes. Only active guidelines reach a session: in the MCP server's handshake instructions, in the dashboard's chat and in Echo.

**Organize drafts guidelines** after recording core memories, in its job and in its background pass. Core memories belong to no workspace, so the background pass drafts once after its workspaces. Records that no active or proposed guideline covers, and that no pass has looked at, are new.

- **A rewrite for a close guideline.** A new record whose embedding has a cosine similarity of at least 0.6 to a guideline goes to that guideline. The guideline's position is the mean of its text and its records. The model may rewrite the guideline to include the record. For an active guideline, that becomes a proposal that amends it, or revises the rewrite already waiting. A proposal that is still waiting is revised in place.
- **Groups for the rest.** The rest join the uncovered records in average-link groups: similarity above 0.55, at most 12 records. A group with a new record and at least two records is drafted into at most one guideline. The guideline must cover at least two records, named by their numbers in the prompt, which Organize checks. This is the form ADR 0099's evidence takes here.
- **Merging repeats.** One more call merges drafts that repeat each other.
- **Limits.** A pass makes at most 60 model calls and steps aside for queued jobs. Records it looked at are not offered again until a new record joins their group.

**Approving a rewrite retires the guideline it amends**, and undoing the approval restores it. Rejecting a rewrite leaves the guideline alone.

**One device drafts for a pair.** In the background, the device that runs LLM tagging drafts, or every device when no paired device tags. This follows ADR 0138's division of model work. A run the user starts drafts on any device.

**Budget.** A session receives the active guidelines, those for every project first, within 3,000 tokens estimated at four characters a token. The dashboard's chat takes at most a quarter of its model's context. Echo's speech model takes 750 tokens beside its retrieved evidence. A model that can search is told that more specific preferences are recorded as memories of type `preference`, where records come from.

**Migration.** Each device moves its existing rows the same way, without new sync stamps. A row the user wrote becomes a guideline; every other row becomes a record, and a proposed one is kept.

**Sync.** Core memories now carry their tier, the guideline a rewrite amends, and the records a guideline covers. A device sends core memories only to a peer whose handshake says it tells the tiers apart (`core_tiers`). It still receives them from older peers, and their rows take the tier their origin gives.

## Rejected alternatives

- **Separate guidelines for coding agents and for the assistant.** It would keep the children's food and the dog out of coding sessions. The user chose that every approved guideline goes to every agent.
- **Keeping specific core memories in sessions and only raising the budget.** 350 statements of about 67 characters each take roughly 6,000 tokens. Most are about one conversation. A larger budget would send more of them rather than better ones.
- **Groups of up to 25 records.** In the prototype these drafts joined unrelated subjects, and kept ages and dates that will be wrong as a child grows.
- **Finding repeated drafts by embedding similarity.** Unrelated imperative drafts scored up to 0.91 against each other, so any threshold that caught the repeats would also have merged different subjects.
- **Drafting guidelines without the user's approval.** The prototype drafted "Always respond in Romanian" from two records. A guideline is an instruction to every session, so ADR 0129's reason for approval holds for guidelines.
- **Asking records for approval too.** The 350 approvals took the user about two minutes, which is not review. Records reach no session, so approving them protects nothing.

## Consequences

- **Fewer, better instructions.** Sessions receive a short list of instructions written for the assistant, instead of the oldest specific statements.
- **Nothing reaches sessions at first.** From the update until the user approves the first guidelines, sessions receive only those the user wrote earlier, which become guidelines. The maintainer wrote none, so the 25 lines stop at once.
- **Model quality limits the drafts.** A 4-billion-parameter local model writes the drafts; a few will be wrong or too broad, and the user rejects or edits them. Merging is conservative and misses some repeats.
- **Every session pays for the guidelines.** 3,000 estimated tokens is six times the old budget of about 500. The estimate is rough for text that is not English.
- **Echo's speech model now gets instructions.** Its prompt previously held only retrieved evidence.
- **Older peers fall out of core memory sync.** A device on an earlier release receives no core memories from an updated one, and its own stay as they were until it updates.
- **The record extraction prompt is stricter.** It now treats a question or a topic the user asked about as no preference. Records already made are kept; the user can retire them.

## Invalidation conditions

- If the user rejects most drafted guidelines, or edits most of the ones they approve, then improve the drafting first, with a larger model or a different prompt. Failing that, drop drafting and keep guidelines the user writes.
- If clients or users find 3,000 tokens too many, then lower the budget. If they find it too few, raise it. A setting is the next step either way.
- If a client gains a standard way to receive user rules, then write the active guidelines there (ADR 0129's condition).
- If coding sessions are misled by guidelines about the user's family, then reconsider separate audiences, which the user turned down on 2026-11-06.

## Validation

- **`tests/test_core_memories.py`:**
  - the user writes guidelines, agents record, and Organize proposes;
  - a statement already held is not recorded or proposed again;
  - a record is kept or retired, never proposed;
  - approving a rewrite retires what it amends, and undoing restores it;
  - records never reach a session;
  - an older store moves into tiers without new stamps;
  - the session budget is in estimated tokens;
  - rows from a device before tiers take their tier from their origin;
  - core memories go only to a peer that offers `core_tiers`;
  - the dashboard makes a guideline from records and reports the budget in tokens;
  - a stored preference is recorded and not given to sessions.
- **`tests/test_organize.py`:**
  - preferences become records, with the stricter prompt;
  - alike records become one guideline and are looked at once;
  - a draft must cover two records;
  - a new record close to an approved guideline is offered as a rewrite, and the waiting rewrite is revised;
  - repeated drafts merge;
  - in the background only the device that tags for the pair drafts;
  - groups follow average likeness up to the cap.
- **`tests/test_placement_sync.py`:** the handshake offers `core_tiers`.
- **Python suite:** all 965 tests pass on macOS.
- **The prototype:** run on the maintainer's 350 records, as described under Context.
- **Not verified:**
  - a full pass over the 350 records through the shipped code;
  - how many of its drafts the user approves;
  - Codex and Copilot passing the handshake instructions to their models (unchanged from ADR 0129).
