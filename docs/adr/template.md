---
title: ADR template
description: Template for a new architecture decision record.
---

Copy this file to `docs/adr/NNNN-short-title.md` and follow [How to write and maintain ADRs](README.md#how-to-write-and-maintain-adrs). Replace the front matter with `title: ADR NNNN — Decision title` and a one-sentence `description` of the decision. Delete these instructions and every header field that does not apply, and keep the remaining fields in this order:

- Status: Proposed
- Date: YYYY-MM-DD
- Recorded: YYYY-MM-DD (retrospective records only)
- Retrospective: reconstructed from … (retrospective records only)
- Scope: limits of the acceptance, if any
- Supersedes: `[ADR NNNN](NNNN-short-title.md)`
- Superseded by: `[ADR NNNN](NNNN-short-title.md)`
- Amends: `[ADR NNNN](NNNN-short-title.md)` — the part this record changes
- Amended by: `[ADR NNNN](NNNN-short-title.md)` — the part that record changes
- Related: records and docs in the same area that this record does not change

## Context

What concrete problem or constraint requires a decision? Cite the evidence: code, logs, measurements, the request.

## Decision

What is chosen, and how far does it reach? One decision per record.

## Rejected alternatives

Each option that was actually considered, tried or measured, and why it was discarded, with the evidence. List only options that were really weighed. If none were recorded, write: *No alternative evaluation was recorded.*

## Consequences

Benefits, costs, compatibility effects, migrations and known gaps.

## Invalidation conditions

Concrete, observable conditions that would repeal the decision or reopen it, each with its effect: *If …, then …*. Required once the record is accepted.

## Validation

What was executed, on which platforms, and what remains unverified. Never present an unexecuted check as passing.
