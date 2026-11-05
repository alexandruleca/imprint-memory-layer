---
title: ADR 0130 — Reorganization is proposed by a background job and reviewed
description: A queued organize job writes a short profile of each project, proposes a project for the sources in catch-alls with evidence, and proposes core memories; the user reviews every proposal, and accepted moves apply as placement rules.
---

- Status: Amended
- Date: 2026-11-04
- Amended by:
  - [ADR 0132](0132-organize-runs-in-the-idle-slot-after-background-tagging.md) — Organize also runs by itself, incrementally, not only as a queued job
  - [ADR 0133](0133-strong-placements-apply-by-themselves.md) — placements with strong evidence apply without review
  - [ADR 0134](0134-placement-rules-and-organize-proposals-sync.md) — Organize's proposals and decisions travel to paired devices
  - [ADR 0136](0136-review-pages-use-paged-tabs-and-decisions-reopen.md) — the user's moves and keeps can be reopened, and review happens in paged tabs with a rail of target projects
- Related: [ADR 0036](0036-move-memories-between-projects.md), [ADR 0067](0067-background-llm-tagging-that-yields-to-every-job.md), [ADR 0099](0099-sources-can-belong-to-several-projects.md), [ADR 0100](0100-explicit-workspace-inbox-for-unassigned-conversations.md), [ADR 0105](0105-project-analysis-continues-after-model-timeouts.md), [ADR 0129](0129-core-memories-are-user-approved-and-start-every-session.md)

## Context

The user asked for memories to be organized better: a big-picture view of everything stored, and memories shifted into the right projects with the help of a model.

How memories get a project today:

- **Ingesting and refreshing files** name the project from manifest files, or from the ingested folder when there is no manifest. That folder fallback is where the `Desktop` catch-all comes from (ADR 0036).
- **Learning conversations** names it from the working directory. A transcript without one goes to `unassigned` (ADR 0099, 0100).
- **Several writers leave the project empty or fixed** (`imprint/server.py`, `imprint/cli_summarize.py`, `imprint/cli_extract.py`, `imprint/cli_desktop_learn.py`):
  - MCP `store` without a project, and session summaries, store `""`;
  - decision extraction recognises only Linux paths, so it stores `""` on macOS and Windows;
  - URLs go to `urls`, and desktop exports to `claude-desktop-convos` and `chatgpt-desktop-convos`.

What already exists for fixing it:

- **`imprint move`** writes placement rules and rewrites the affected memories (ADR 0036). It cannot select memories with an empty project: `projects.project_name` rejects `""`.
- **Project analysis** (ADR 0099, 0105) already has a model read whole sources and propose further projects they belong to. Each suggestion needs a verbatim quote, and none is accepted without the user.

What already exists for the big picture: `wake_up` lists project counts, recent important memories and facets, and `graph_scope` and the dashboard draw counts. None says in words what a project is about.

The rules a reorganizer must keep are already settled:

- content and vectors never change (ADR 0051);
- moves are placement rules, which do not sync (ADR 0036);
- nothing crosses workspaces by similarity (ADR 0100);
- model suggestions are reviewed (ADR 0099);
- prompts that can leave the device are redacted (ADR 0060);
- one heavy job runs at a time, and background work yields (ADR 0064, 0067).

## Decision

A queued **organize** job (`python -m imprint.organize`, **Organize memories** on the Commands page or **Run Organize** on the new Organize page) works on one workspace in three steps.

**1. Project profiles.** For up to 60 projects, the local helper model writes two or three sentences and the main topics. It reads the opening of up to 12 of the project's most central sources: files and decisions before conversations, larger sources first. The profile is stored as one replaceable note per project, source `profile/<project>`, type `summary`. Search and chat find profiles like any note, and the Organize page shows them.

**2. Placement proposals.** These cover up to 300 sources per run, newest first, from the catch-alls:

- no project, `unassigned`, `general`, `personal`, `conversations`, `urls`;
- projects named after container folders (`Desktop`, `Documents`, `Downloads`, `Code`, `Projects`, `repos`, `src`);
- every `*-convos` desktop-export project.

For each source the job reads what it says:

- a snapshot's text, or for a conversation only its user and assistant messages (ADR 0082);
- otherwise the source's chunks.

It first looks for deterministic evidence with project analysis's matcher (ADR 0099): the target's registered folder path, or the target's name. A name counts only when it looks like an identifier (a hyphen, underscore, dot, digit or inner capital), because plain names such as `client` or `platform` match ordinary text. Without such evidence, the model gets the project profiles and the source's opening 4,000 characters. It must return one project and a verbatim quote from the source, or nothing.

**3. Core memory candidates** for ADR 0129, from up to 100 preference-typed memories. With the model, each is a short statement backed by a quote; without it, preferences of at most 300 characters are proposed as they are.

Proposals wait on the **Organize** page, grouped by target project, with their evidence. The user moves or keeps them one at a time or a whole group at once. A rejected proposal stays rejected on later runs, and a source analysed before with the same text and candidates is not analysed again. A move applies through `projects.move` as a placement rule for its source. `move` now accepts the empty project as origin when sources are named; "no project" as a whole still cannot be moved. Nothing applies automatically.

Without the local helper model, or with **Without the local helper**, the job proposes only the deterministic placements and short preferences. The model runs only locally, as for project analysis (ADR 0099), so no prompt leaves the device.

## Rejected alternatives

- **Applying the model's placements directly.** ADR 0099 rejected auto-accepting model confidence, and ADR 0100 says semantic matches are not authorization. A wrong move also rewrites memory ids.
- **Clustering embeddings into new projects.** Clusters invent project boundaries the user never drew. They are kept only as a hint the job could later show next to a proposal.
- **Relabeling the project field in place.** ADR 0036 rejected it: the id includes the project, and placement must survive re-ingestion and sync.

## Consequences

- Catch-alls shrink as the user reviews, and each move holds through refreshes and imports because it is a rule.
- Placement rules stay on the device that wrote them (ADR 0036). Another device keeps its own layout until its user reviews there, unless rules gain sync later.
- Profiles are model-written summaries, labelled as such. They are evidence about a project, not facts.
- The job runs queued, holding the queue lock, not as a background holder. Running it in the background would need ADR 0067's single preemptible flag to become a priority order.
- A model pass costs time on a local model: about 14 s per source on an Apple M3 with Qwen3.5-4B. The job therefore caps a run at 300 sources, works newest first, and skips sources analysed before.

## Invalidation conditions

- If users accept nearly every proposal from deterministic evidence, offer auto-apply for that evidence class.
- If placement rules start to sync, accepted moves apply on every device and this record should say so.
- If proposals are mostly rejected, measure why before keeping the model step.

## Validation

- `tests/test_organize.py`:
  - identifier matches are proposed and nothing moves until accepted;
  - accepting moves a source with a placement rule, from no project too, and a rejection survives later runs;
  - "no project" as a whole is refused;
  - plain-word project names need more than their name, and folder-named and `*-convos` projects are catch-alls, not targets;
  - the model writes and replaces profiles;
  - a model placement needs a quote from the source;
  - preference memories become core memory proposals with and without the model.
- `imprint/ui/tests/organize.spec.ts`: proposals grouped by target with their evidence, moving one, keeping a group, the decided list, the last run's summary, and queueing a run without the model.
- Read-only dry runs on the maintainer's workspace on macOS, which stored nothing:
  - The workspace holds 5,732 memories without a project, 1,514 in `Code` and 6,057 in `cloud-ai-convos`, across 361 catch-all sources.
  - Matching every name proposed `Code` for 122 sources and plain words like `client` for dozens more. The identifier rule and the folder catch-alls replaced these with distinctive targets: 140 of the first 300 sources (129 by name, 11 by path).
  - The model first quoted the "Imprint conversation" header of conversation snapshots, which placed four sources in `imprint`. Reading conversations' messages only removed those.
  - Profiles took 4–12 s each.
- Not verified: a full run that stores proposals on real data, and how many proposals the maintainer accepts.
