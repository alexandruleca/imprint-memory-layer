---
title: ADR 0011 — Nightly Trivy security scanning
description: Scheduled repository security checks with visible findings, pinned tooling and bounded permissions.
---

- Status: Accepted
- Date: 2026-10-23
- Related: [ADR 0002](0002-build-and-validate-before-publication.md), [ADR 0007](0007-verified-runtime-and-desktop-baseline.md); user request for nightly security scanning
- Restructured: 2026-10-25

## Context

Repository audits are dated snapshots. New advisories can affect unchanged Go, Python and npm dependencies, including development tooling used in builds. The user requested a nightly Trivy scan. Python profile locks and the runtime Dockerfile use custom filenames that need explicit analyzer mappings.

## Decision

Run a GitHub-hosted repository scan at 01:23 UTC daily and on manual dispatch. Scan vulnerabilities, misconfigurations and secrets; include development dependencies and unfixed vulnerabilities. Exclude generated/dependency directories and private local data, while retaining source and lockfiles. This does not add an image build or installer scan.

Publish all-severity JSON/SARIF artifacts and SARIF code-scanning results before applying a high/critical failure gate. Report scanner and publication failures rather than treating them as clean scans. Pin the scanner version/archive checksum and action commits, disable retained checkout credentials and limit write permission to security events. Record operation and triage in [security scanning](../security-scanning.md).

## Rejected alternatives

PR-only scans miss new advisories when source is unchanged. A non-failing report is easier to overlook. Filtering unfixed or development findings would hide known risks. Building every image nightly expands resource use and scope; image/artifact scanning can be added separately. A checksum-pinned standalone scanner avoids a separate third-party setup action.

## Consequences

Existing high/critical findings can make the first run fail. This is expected reporting, not a reason to suppress them. Maintainers must review scanner/action pins and intentional exceptions. GitHub scheduling, database availability and supported analyzer formats limit coverage. Reports are repository artifacts; secrets must not be intentionally stored in source.

## Invalidation conditions

- If GitHub scheduled workflows or the Trivy vulnerability database stop being reliably available, the nightly scan loses its basis: move the scan and keep the gate.
- If Trivy stops recognizing a lockfile or Dockerfile format the repository uses, coverage shrinks silently: revisit the analyzer mappings or the scanner.
- If shipped images or installers carry dependencies the repository scan cannot see, add the image and artifact scanning this record left out of scope.

## Validation

Completed locally: actionlint passes; the checksum-verified Trivy 0.76.1 scan identifies seven dependency manifests/locks and both Dockerfiles, including development dependencies. SARIF conversion produces 62 results. The gate exits 1 for 31 high/critical dependency findings and one high misconfiguration, as intended. These counts include repeated advisories across targets. The documentation site builds 42 pages.

Hosted [manual run 39921877128](https://github.com/alexandruleca/imprint-memory-layer/actions/runs/39921877128) completed on 2026-10-23: verified installation, scanning, SARIF generation, artifact retention and Security publication all passed. GitHub accepted 62 Trivy results on `main` with no processing error. The final severity gate failed on findings as designed. Nightly scheduling is configured; this manual run validates the job, not a completed future scheduled trigger.
