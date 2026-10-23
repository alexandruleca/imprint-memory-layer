---
title: Security scanning
description: Nightly Trivy coverage, reports, failure policy and local reproduction.
---

The **Nightly security scan** GitHub Actions workflow runs on the default branch daily at **01:23 UTC**: 03:23 in Bucharest during winter and 04:23 during summer. GitHub can delay scheduled jobs. It also supports **Run workflow** for an immediate scan.

Trivy scans the checked-out repository for dependency vulnerabilities, supported configuration misconfigurations and secrets. Coverage includes Go modules, dashboard/site/Electron npm locks with development dependencies, all three hashed Python profile locks (`base`, `gpu`, `llm`), and both Dockerfiles. `trivy.yaml` explicitly associates the custom Python lockfile names with the pip analyzer and `Dockerfile.imprint` with the Dockerfile analyzer. No application dependencies are installed and no repository code is executed by the scan.

Generated binaries, build/static output, dependency directories, Git internals, virtual environments and `data/` are excluded. The workflow scans source and dependency manifests; it does not build or scan container images, installers, runtime model weights or a deployed machine. The generated reports live outside the checkout. Local source scans must likewise keep private state outside the target or covered by explicit exclusions.

## Reports and failure policy

- All severities are retained in JSON and SARIF. Unfixed vulnerabilities and development dependencies are included.
- The **Security → Code scanning** page receives SARIF results under `trivy-repository`.
- Every completed scan retains the `trivy-security-reports` workflow artifact for 30 days, including when findings fail the final gate. Artifacts also remain available if SARIF publication fails.
- **High or critical findings fail the workflow** after report publication. Scanner/download/publication errors also fail visibly. GitHub workflow notifications follow each maintainer's notification settings.

Inspect the package/path, affected version, advisory and fix availability before choosing remediation. Re-run the manual workflow after a fix. Do not add blanket ignores or suppress unfixed vulnerabilities just to make the job pass. Any intentional exception should document its reason, owner and review date in the same change. A successful scan reflects the scanner's supported formats and current databases, not a guarantee that the application has no vulnerabilities.

Initial local baseline, 2026-10-23: 60 dependency findings and two Dockerfile findings; 31 dependency findings and one configuration finding are high/critical. Counts can repeat an advisory across lockfiles. The severity gate correctly fails on this baseline. These are findings to triage, rather than a claim that the repository passes a clean security audit.

## Reproduction and maintenance

Use the workflow's pinned Trivy version (initially 0.76.1), with a separate output directory:

```bash
trivy fs --config trivy.yaml --format json --output /tmp/imprint-trivy.json .
trivy convert --format sarif --output /tmp/imprint-trivy.sarif /tmp/imprint-trivy.json
trivy convert --format table --severity HIGH,CRITICAL --exit-code 1 /tmp/imprint-trivy.json
```

The initial scan deliberately returns zero for findings so both reports can be published; the final conversion enforces the failure threshold. Fatal scan errors still return nonzero. Trivy refreshes its vulnerability/check databases; database downloads are not disabled or reused indefinitely.

The workflow checksum-verifies its pinned Linux Trivy archive before execution. Update its version and archive digest together after checking the [upstream release](https://github.com/aquasecurity/trivy/releases). Workflow actions are pinned to commit SHAs and checkout does not retain credentials. Only the scan job receives `security-events: write`; repository content remains read-only. See [ADR 0011](adr/0011-nightly-trivy-security-scanning.md).
