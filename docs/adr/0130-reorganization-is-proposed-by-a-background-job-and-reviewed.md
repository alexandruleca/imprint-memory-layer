---
title: ADR 0130 — Reorganization is proposed by a background job and reviewed
description: Proposed background job that writes a short profile of each project, proposes a project for memories in catch-alls and without one, and proposes core memories; the user reviews every proposal, and accepted moves apply as placement rules.
---

- Status: Proposed
- Date: 2026-11-04
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

A queued **organize** job, which can also run in the background like ADR 0067's tagging and yields the same way, works on one workspace in three steps:

1. **Project profiles.** For each project, the model writes a short profile from its file summaries and most important memories: what the project is, its main topics and its key sources. It is stored as one replaceable note per project, `profile/<project>`, typed `summary`. Profiles give the big picture, on a dashboard **Overview** and to `wake_up` and chat.
2. **Placement proposals.** For each source in a catch-all (an empty project, `unassigned`, `Desktop`, `urls`, desktop conversations), the job first looks for deterministic evidence: registered paths, and the identifier matches of ADR 0099. Only then does it give the model the project profiles and the source's text. A proposal names a target project, the evidence, and a verbatim quote from the source that ties it to the target. Without a quote there is no proposal.
3. **Core memory candidates** for ADR 0129, from the same pass.

Proposals wait on a dashboard **Organize** page, grouped by target project. The user accepts or rejects them one at a time or in bulk. An accepted placement applies through `projects.move` as a rule for its source. `move` is extended to accept an empty origin project, which it now refuses. Nothing applies automatically. ADR 0099's opt-in exact registered-path match is the only exception, and only when opted in. The job uses the configured tagger provider, redacting for remote ones, and keeps working without a model: profiles are skipped, and only deterministic placements are proposed.

## Rejected alternatives

- **Applying the model's placements directly.** ADR 0099 rejected auto-accepting model confidence, and ADR 0100 says semantic matches are not authorization. A wrong move also rewrites memory ids.
- **Clustering embeddings into new projects.** Clusters invent project boundaries the user never drew. They are kept only as a hint the job could later show next to a proposal.
- **Relabeling the project field in place.** ADR 0036 rejected it: the id includes the project, and placement must survive re-ingestion and sync.

## Consequences

- Catch-alls shrink as the user reviews, and each move holds through refreshes and imports because it is a rule.
- Placement rules stay on the device that wrote them (ADR 0036). Another device keeps its own layout until its user reviews there, unless rules gain sync later.
- Profiles are model-written summaries, labelled as such. They are evidence about a project, not facts.
- One more background holder of the queue lock means turning ADR 0067's single preemptible flag into a priority order, as that record expected.
- A model pass over every catch-all source costs time on a local model. The job therefore works newest first and resumes where it stopped.

## Invalidation conditions

- If users accept nearly every proposal from deterministic evidence, offer auto-apply for that evidence class.
- If placement rules start to sync, accepted moves apply on every device and this record should say so.
- If proposals are mostly rejected, measure why before keeping the model step.

## Validation

None yet; this is a proposal. Before acceptance: a dry run over the maintainer's workspace that counts catch-all sources, proposals by evidence class, and how many the maintainer would accept, plus the time it takes on the local model.
