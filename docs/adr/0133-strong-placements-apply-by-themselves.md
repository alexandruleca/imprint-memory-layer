---
title: ADR 0133 — Strong placements apply by themselves
description: Organize moves a catch-all source by itself when it names exactly one project's registered folder, or one distinctive project name at least twice and no other, early in what it says; such moves are listed with an undo, and every other placement still waits for the user.
---

- Status: Accepted
- Date: 2026-11-05
- Amends: [ADR 0130](0130-reorganization-is-proposed-by-a-background-job-and-reviewed.md) — placements with strong evidence apply without review
- Related: [ADR 0036](0036-move-memories-between-projects.md), [ADR 0099](0099-sources-can-belong-to-several-projects.md), [ADR 0132](0132-organize-runs-in-the-idle-slot-after-background-tagging.md), [ADR 0134](0134-placement-rules-and-organize-proposals-sync.md)

## Context

ADR 0130 had every placement wait for the user ("Nothing applies automatically"). One of its invalidation conditions was "If users accept nearly every proposal from deterministic evidence, offer auto-apply for that evidence class." The user then asked not to approve everything: approval only for the proposals with less confidence.

Organize's deterministic evidence is the project-analysis matcher (ADR 0099): a project's registered folder path, or its name when the name looks like an identifier (ADR 0130). A read-only dry run on the maintainer's workspace on macOS, 363 catch-all sources and 46 candidate projects, found:

- 130 sources with no deterministic match;
- 233 with a match: some name one project at least twice and no other, the rest name one project once or several projects.

A random sample of 14 from the first group was checked by hand. Conversations opening with a transaction-gateway pull request link, and memory notes about engine-3d or editor-2d, were right. One was wrong: a ChatGPT export file, `cloud-ai-convos/openai/conversations-014.json`, holding many conversations. It opens with Prague's January temperatures and names engine-3d twice, about 200,000 characters in. Three more such export files were in the group.

Size does not separate the export files from single sources: single conversations in the group reach 3.4 million characters, while the export files are 530,000–1,600,000. Where the first mention falls does. Every single conversation or note in the group first named its project within 40,000 characters of what the source says (a conversation's messages), and every export file only after 197,000.

## Decision

A placement is **strong** when all of these hold:

- exactly one candidate project's registered folder path appears; or, with no folder path, exactly one candidate's identifier-like name appears, at least twice;
- the first mention falls within the first 50,000 characters of what the source says: a snapshot's text, or a conversation's user and assistant messages.

With `organize.auto_move` on (the default), Organize moves a strong placement at once, as a source-level placement rule (ADR 0036), from a queued run and a background pass alike (ADR 0132). The proposal is recorded as `auto`. Every other placement waits on the Organize page as before, and so does every placement that rests on the local model's quote. Proposals carry their strength, so with `organize.auto_move` off, strong ones wait marked **Strong evidence**.

The Organize page lists **Moved automatically**, newest first, each with **Undo**. Undo removes that source's rule, which moves its memories back to where the rest of their origin is, and records the proposal as rejected, so it is not moved again.

Core memory proposals always wait for the user (ADR 0129). They become instructions in every agent session, and they are drawn from memory text that anything ingested could have written.

## Rejected alternatives

- **Auto-applying every deterministic match.** In the dry run that includes sources naming several projects, and sources naming one project once, as in a passing reference.
- **A size limit instead of the opening rule.** Measured above: single conversations reached 3.4 million characters, while export files started at 530,000.
- **Excluding projects named `*-convos`.** The bundles sat in a folder called `cloud-ai-convos` that someone ingested. Imprint's own desktop-export learner stores one source per conversation in `*-convos` projects, and those can be placed.
- **Auto-applying the model's placements.** ADR 0099 and ADR 0130 rejected acting on model confidence. A quote shows the model read the source, not that the source belongs to one project.
- **Auto-approving core memories.** They steer every session; text an attacker planted in an ingested page could otherwise become a standing instruction.

## Consequences

- On the maintainer's workspace, 142 of 363 catch-all sources move without review and 91 wait for it, against 233 waiting before.
- A wrong strong move is a placement rule like any other: Undo reverts it and keeps it from recurring. Its memories take new IDs and back, as any move (ADR 0036).
- Moves reach paired devices (ADR 0134), so an undo does too.
- A source that names its project only after 50,000 characters always waits, even when it does belong there.

## Invalidation conditions

- If users undo a noticeable share of automatic moves, tighten the rule (more mentions, an earlier opening) or turn `organize.auto_move` off by default.
- If users accept nearly every weak proposal of one kind, for example a single folder path among several names, make that kind strong.
- If the matcher counts more than four mentions, require a share of the source's mentions rather than a fixed two.

## Validation

- `tests/test_organize.py`, `ConfidenceTests`:
  - a strong match moves by itself, undo moves it back and records it as rejected, and it is not moved again;
  - a match naming two projects, and every match with `organize.auto_move` off, waits;
  - a source naming its project only after its opening waits;
  - a registered folder path is strong evidence.
- `imprint/ui/tests/organize.spec.ts`: the Moved automatically list with Undo, and strong proposals waiting with `organize.auto_move` off.
- The read-only dry run above, on the maintainer's macOS workspace, with the final rule: 142 strong, 91 weak, 130 without a match.
- Not verified: how many automatic moves the maintainer undoes, and the rule on Windows-recorded workspaces with registered project folders, where path evidence appears.
