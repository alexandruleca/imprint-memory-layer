# Synthetic session notes — benchmark fixture, not actual conversation history

The reference project uses a background thread and an in-memory FIFO Queue.
Configuration priority is REFERENCE_* environment variable, persisted config,
then defaults. Its queue has no process-shared lock and is not durable.

Three recent Imprint work areas, captured here as synthetic project memory:

1. Workspace boundaries and source cleanup: accidental ingestion needed approved
   roots, explicit confirmation for overlaps, and previewed purge exclusions
   that fence writes already embedding when cleanup starts.
2. Native installers and release CI: Intel macOS needed its supported ONNX wheel,
   Windows silent setup needed a bounded noninteractive path, and branches
   should compile/test while main builds release artifacts.
3. Portable shared memories: record source device, keep conversation chunks
   available on other devices, and map project-relative references into approved
   local checkouts without treating a foreign absolute path as a local file.

The production repository's docs and code are the authority for implementation
details. This fixture exists to give both benchmark modes identical historical
evidence and a second project; it is not a claim about another real repository.
