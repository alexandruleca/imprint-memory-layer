---
title: ADR 0144 — Newer core memories that contradict older ones wait for the user
description: Organize notes when a newer recorded core memory contradicts an older one, or when newer statements contradict an approved guideline; the dashboard shows both versions and the user keeps the newer, the older or both, or overwrites the guideline or leaves it as is.
---

- Status: Accepted
- Date: 2026-11-06
- Amends: [ADR 0143](0143-agents-receive-generalised-guidelines-core-memories-are-kept-as-records.md) — each group of records is also checked for contradictions, each pair confirmed, statements reach the model oldest first and drafts follow the newest, a rewrite says when it contradicts the approved guideline, and a pass makes up to 160 model calls
- Related: [ADR 0129](0129-core-memories-are-user-approved-and-start-every-session.md), [ADR 0136](0136-review-pages-use-paged-tabs-and-decisions-reopen.md), [dashboard](../dashboard.md), [API](../api.md)

## Context

Core memories change over time: a preference changes, or a fact stops being true. On 2026-11-06 the user asked that core memories can be relearnt. When an older one conflicts with new information, it should be highlighted, and the user should decide whether to overwrite it or leave it as is.

Under ADR 0143, records accumulate and nothing compares them:

- **Records.** A newer statement that reverses an older one is kept beside it, and nothing says which is current.
- **Drafting.** A guideline drafted from a group of records saw them in no particular order, so it could follow either one.
- **Rewrites.** A rewrite of an approved guideline did not say whether it added detail or reversed it.

**The dry run.** The guideline pass was run on a copy of the maintainer's 350 records with the local Qwen3.5-4B. The group check alone named 19 pairs in 824 s, together with 37 guideline drafts. Nearly all were about different things:

- "Prefers bottle feeding over breastfeeding" against "Prefers high-meat, grain-free cat food for sterilized cats", with a reason that itself said they do not conflict;
- a baby's age at two different times;
- GNOME keybindings against login managers.

Embedding similarity did not separate them: a sleep-regression pair at different ages scored 0.87, the highest of the 19. A second question asked about each pair alone whether the newer statement means the earlier one is no longer true. It kept 1 of the 19 and dropped the rest. Each answer took 7–38 s on a busy machine.

## Decision

**Organize notes contradictions; the user settles them.** In the guideline pass of ADR 0143:

- **Oldest first.** A group's records and a rewrite's statements reach the model in the order they were recorded. The draft prompt says to follow the newest where they disagree.
- **A check per group, then a check per pair.** Each group with a new record gets one more call, which names pairs where a newer statement cannot be true together with an older one. A pair counts when the older one comes first and either is new.
- **Each pair is confirmed.** Up to three pairs per group are then asked about one at a time: whether the two are about the same thing, and whether the newer one means the earlier is no longer true. Only a confirmed pair is noted, on the newer record, with the reason the second answer gives. A pair noted once, settled or not, is not noted again.
- **Rewrites that contradict.** The rewrite prompt asks whether a newer statement contradicts the guideline. When it does, and the guideline is active, the rewrite proposal names that guideline as a conflict with the reason.

**How the user settles each kind.**

- **Record against record.** Use the newer, which retires the older records; keep the earlier, which retires the newer one; or keep both. A settlement can be undone, which restores what it retired.
- **Rewrite against guideline.** Overwrite approves the rewrite, which retires the guideline (ADR 0143). Leave as is rejects it.

The dashboard shows open conflicts first, in their own tab, with the earlier and newer text and the reason. Settlements are stamped like any other change to a core memory, so they sync. A pass makes up to 160 model calls, up from 60: each group now takes two, and each pair it names one more. A model error ends a pass with what it made, and leaves the records it had not finished for the next pass.

## Rejected alternatives

- **Replacing the older record automatically.** The model sometimes calls details that can both hold a conflict, and a wrong replacement would silently lose what the user said. The user asked to decide.
- **Comparing each new record with its nearest older records in its own call.** On the first pass that is one call per record: 350 calls of 3–9 s each on the maintainer's store. Checking each group takes one call per group, and groups already gather records about one subject.
- **Flagging the pairs the group check names without confirming them.** In the dry run, 18 of its 19 pairs were dismissed by the second question. The user would have been shown mostly false conflicts.
- **Filtering the pairs by embedding similarity.** The false pairs scored from 0.52 to 0.87, so no threshold kept real changes and dropped them.
- **Detecting contradictions within the drafting call.** It would save the extra call, but it asks a 4-billion-parameter model for two tasks at once in one JSON answer. A separate call keeps each answer checkable.

## Consequences

- **The user sees what changed.** A changed preference shows up as a conflict instead of two silent records, and a guideline changes only when the user overwrites it.
- **First passes take longer.** A pass makes up to two and a half times the model calls, and a large first pass spans more idle slots.
- **Real changes can be missed.** The second question is strict: in the dry run it also dropped "short flights for winter trips" against a later "car travel over flights", calling both compatible.
- **Some conflicts are missed.** Only records in the same group, or close to the same guideline, are compared. A contradiction between a new record and an older record that a guideline covers is caught only through that guideline's rewrite.
- **False conflicts cost a click.** Keep both dismisses one for good.

## Invalidation conditions

- If the user mostly keeps both, then the check finds false conflicts. Tighten its prompt, or check only pairs above a higher similarity.
- If contradictions reach sessions without being noted, then compare new records with their nearest older records across groups as well.

## Validation

- **`tests/test_organize.py`:**
  - a newer record that contradicts an older one is flagged, oldest first, only when the second question confirms it, and invalid pairs are ignored;
  - keep the newer retires the older and undo restores it;
  - keep the earlier and keep both work;
  - a pair is not noted twice;
  - a rewrite that contradicts an approved guideline is a conflict, and approving it retires the guideline;
  - a failing model ends the pass and leaves its records for the next one.
- **`tests/test_core_memories.py`:**
  - the dashboard settles a conflict and refuses unknown settlements and ids;
  - conflicts arrive through sync with their reasons.
- **The dry run:** the shipped pass on a copy of the maintainer's 350 records, and the second question on the 19 pairs it named, as described under Context.
- **Not verified:**
  - the pass with the confirmation step end to end on that store;
  - whether the confirmed conflicts are ones the user agrees with;
  - the dashboard's Conflicts tab on hardware.
