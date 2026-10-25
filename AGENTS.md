# Repository working agreement

- Read `docs/README.md` and the relevant subsystem documentation before making substantive changes. `imprint/ui/AGENTS.md` also applies to dashboard changes.
- Keep documentation accurate in the same change as behavior, integration, packaging, configuration or operational changes.
- Record significant architectural and product decisions in `docs/adr/NNNN-short-title.md`, following `docs/adr/README.md`. Explain context, alternatives, consequences and validation. Distinguish accepted decisions from proposals; do not rewrite old rationale when a decision is superseded.
- Add an ADR when changing storage/schema, runtime/dependency policy, client configuration/hooks, security boundaries, supported platforms, installer/update behavior or release architecture. Routine fixes can reference an existing ADR instead of creating another.
- Validate the behavior changed. Use native runners for OS-specific execution and installers; cross-compilation alone does not establish support. Record untested platforms and remaining limitations explicitly.
- Do not commit regenerated `bin/` binaries. `imprint/ui/out/` is not tracked: CI, releases and the container build the dashboard from source, and a checkout builds it with `make dashboard-build`. Do not include local user state or development dependencies in distributions.
- Keep setup idempotent and preserve unrelated client configuration and user memories. Never run setup against a contributor's real client profiles as a test; use temporary profiles or disposable CI runners.
