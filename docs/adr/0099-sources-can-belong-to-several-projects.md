---
title: ADR 0099 — Sources can belong to several projects
description: Reviewable local project associations add retrieval membership while preserving canonical source identity and primary placement.
---

- Status: Accepted
- Date: 2026-10-28
- Related: [ADR 0036](0036-move-memories-between-projects.md), [ADR 0081](0081-verifiable-redacted-source-snapshots.md), [ADR 0094](0094-the-dashboard-reads-whole-sources-from-their-snapshots.md)

## Context

Directory metadata and catch-all desktop export projects do not reliably describe what a conversation discusses. A conversation can concern multiple repositories. Moving it to one project loses that relationship; duplicating its chunks would inflate storage and complicate source integrity.

## Decision

Keep one canonical source and its existing primary project, IDs, vectors and provenance. Store workspace-scoped, device-local project links in `project-associations.sqlite3`, keyed by origin project, source and target project. Materialize accepted links as `related_projects` payloads. Read filters include primary or related membership; canonical ownership still governs moves, purge and source integrity. Counts are unique within each project, but project totals can overlap.

After a full source snapshot is published, scan its complete readable messages for known project names and registered paths. Injected context, reasoning and tool output do not count as identity evidence. Save suggestions with character spans and source version; source text remains exclusively in snapshots. Review and manual linking are available in Projects. An explicit queued scan handles existing snapshots and newly introduced project names. Optional local-helper analysis reads sequential bounded sections, accepts only known project names with verbatim supporting quotes, has no tools and never grants membership automatically. It requires an installed helper and does not download one.

Default to suggestions. Users can enable automatic acceptance of exact registered-path evidence; name and model matches always require review. Manual accept/reject decisions survive source updates and moves. Inferred decisions are replaced when a changed source is analyzed. Stale evidence is hidden, while manual membership persists. All writer boundaries recompute related memberships from local policy, including peer imports. Links are not synced. Source deletion removes its associations; a primary move preserves them.

Preserve existing nonempty conversation project naming. For new sources in other directory layouts, use an existing project mapping or nearby manifest. Preserve an indexed source's origin when improved naming would change its IDs.

## Rejected alternatives

- Copy chunks into each project: duplicates vectors and canonical-source state.
- Replace topics with projects: shared themes do not establish repository identity.
- Automatically accept model confidence: plausible names and quoted passages still need human interpretation.
- Run a model on every import: adds latency and resource contention to ingestion. Lightweight identifier scans are automatic; model analysis is explicit.

## Consequences

Whole-source links make the conversation retrievable from several projects. Passage spans explain each suggestion; this version does not partition membership by passage. Generic or unmentioned project identities need local-helper review or manual links. A source must have a full snapshot; legacy records need refresh or learning first. A new project catalog needs an explicit rescan of existing sources. Local analysis is limited to 256 sections and 120 seconds per source, with a visible failed job when exceeded. A failed derived scan does not invalidate a successfully published source and can be retried from Projects.

## Invalidation conditions

If linking a whole source produces unacceptable retrieval noise, introduce passage membership in a new decision while retaining canonical source identity. If cross-device shared project organization becomes required, define a conflict policy before syncing user decisions.

## Validation

Linux/WSL in-memory Qdrant tests exercise full-source evidence, multiple accepted projects without new points, canonical reading through links, refresh, manual rejection, primary moves, deletion, imported payload authority, exact path acceptance, context exclusion and local-model quote validation. Native local-model quality for project classification and native Windows/macOS execution remain unverified.
