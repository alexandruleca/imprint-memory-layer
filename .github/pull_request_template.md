## What

<!-- Short description of the change for reviewers. -->

## Why

<!-- Motivation: bug fix, perf, UX, internal refactor — link issues with Fixes #123 / Ref #456. -->

## How to test

<!-- Commands you ran, or manual steps (e.g. imprint ingest on sample repo). -->

```bash
go vet ./...
go test ./...
go build -ldflags "-s -w" -o /tmp/imprint .
```

## Checklist

- [ ] Targets the correct base branch (`dev` for ongoing work; `main` only when agreed with maintainers).
- [ ] `go vet ./...`, `go test ./...`, and `go build` succeed locally (matches [CI](.github/workflows/ci.yml)).
- [ ] If this PR should drive a **stable** release, commit messages (or merge commit title) follow [Conventional Commits](https://www.conventionalcommits.org/) as described in [docs/building.md](docs/building.md#release-channels-ci) — merges to `main` that squash history need a conventional **PR title**.
- [ ] User-visible behavior changed → README or `docs/` updated where appropriate.
- [ ] License headers / third-party notices updated if new dependencies were added (see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)).

---

_This checklist is practical guidance for reviewers. It does not create a contract or merge obligation; maintainers may waive items when reasonable._
