---
title: ADR 0060 — Redact secrets before memories are stored
description: Every memory and fact write replaces credentials with placeholders before its ID and embedding are computed; deterministic rules always run, OpenAI Privacy Filter is opt-in, and imprint scrub cleans what was stored before.
---

- Status: Accepted
- Date: 2026-10-25
- Related: ADR 0005, ADR 0017, ADR 0018, ADR 0033, ADR 0036, ADR 0051

## Context

Imprint skipped whole secret files (`.env*`, SSH keys, `.key`/`.p12`/`.pfx`; `imprint/source_state.py`, `imprint/cli_index.py`) and redacted nothing inside content. A key pasted into a conversation, written in a config file or quoted in a summary was stored in Qdrant as text, embedded, shown by `search` and `get_memory`, and copied by sync to every paired device and teammate. The user asked for an open-source secret cleaner on the chunks at ingestion and in sync.

Text reaches storage through few functions. `vectorstore.store` writes one note (MCP `store`, summaries, auto-memory files, extracted decisions). `vectorstore.store_batch` and `replace_source`, which calls it, write chunks (ingest, refresh, learn, `ingest_url`, `ingest_content`, browser capture, desktop exports, the Lance import). `vectorstore.store_batch_precomputed` writes what sync receives, with the sender's vectors. Facts go through `imprint_graph.add` (MCP `kg_edit`, summaries, chat) and `imprint_graph.import_fact` (sync). Two paths copy storage without writing through these: workspace migration and project moves copy existing points, and `imprint sync import` restores a raw Qdrant snapshot and SQLite file.

Memory IDs hash project, source, content and position (`vectorstore._make_id`, ADR 0018). Sync validates that a received ID matches its content, deduplicates by ID, and stores the sender's vector unchanged when the embedding identity matches.

## Decision

### One step at every write

`imprint/redaction.py` replaces each secret with `[REDACTED:<kind>]`. Neither the value nor a hash of it is kept. The write functions above apply it to the content before anything derives from it:

- `store` redacts the note before its ID and embedding.
- `store_batch` redacts its records and passes them to the internal `_store_redacted`. `replace_source` redacts once and calls `_store_redacted` directly, so the IDs it keeps when pruning are those of the redacted chunks.
- `store_batch_precomputed` first validates every sender ID against the content as sent, then redacts. A record the redaction changes takes the ID of its redacted content (`vectorstore.relabeled_id`, which keeps the note or positioned form, as project placement already does) and drops the sender's vector, which was computed from the secret. Those records are embedded again; unchanged records keep their vectors. Peers on older releases, and peers with fewer rules, are therefore redacted on arrival.
- `imprint_graph.add` and `import_fact` redact subject, predicate and object. `import_fact` deduplicates by the redacted text.

Placeholders are the audit record: `redaction.kinds` counts them in any text. No payload field is added, because a field would have to travel through sync and could disagree with the content.

### Deterministic rules, always on

A curated rule set in Python `re`, not a dependency:

- Known formats: PEM private keys (whole blocks, blocks cut by a chunk boundary on either side, keys inside JSON strings), AWS access key IDs, GitHub, GitLab, OpenAI, Anthropic, Hugging Face, Slack tokens and webhooks, Stripe, Google API keys and OAuth client secrets, npm, PyPI, SendGrid and JWTs. Several patterns follow gitleaks' rule definitions; its MIT license is in `licenses/gitleaks/LICENSE`.
- By position: the password of a `scheme://user:password@host` URL, `Authorization: Basic` credentials, and long random `Bearer` tokens.
- A literal assigned to a credential-named key (`password`, `secret`, `token`, `api_key`, `client_secret` and similar, ending the name) with `=`, `:`, `:=` or `=>`. A password-named key needs a value that is not one plain word. Other keys need a value of 12+ characters with letters and digits and Shannon entropy of at least 3. Placeholders (`${…}`, `<…>`, `{{…}}`, `****`), paths, URLs, code expressions (calls, indexing, dotted names) and quoted text with spaces are skipped.

Precision comes first. Git SHAs, UUIDs, lockfile hashes and base64 data contain none of the anchors the rules need. Cut into 1,000-, 2,000- and 4,000-character chunks (11,357 in all), this repository's tracked source and documentation gave two findings at every size: the same test token assigned to `TOKEN` in two test files. Overlapping matches merge under the kind of the earliest. Spans that overlap an existing placeholder are cut around it, so redacting redacted text changes nothing. Matching is linear: the patterns use possessive quantifiers, and the tail of a key that began in a previous chunk is read backwards from its END line.

### OpenAI Privacy Filter, opt-in

`redact.privacy_filter` (`IMPRINT_REDACT_PRIVACY_FILTER`, default `false`) adds [OpenAI Privacy Filter](https://huggingface.co/openai/privacy-filter) (Apache-2.0) after the rules. Its placeholders are protected the same way.

- **Model.** `openai/privacy-filter` at revision `7ffa9a043d54d1be65afb281eddf0ffbe629385b`, `onnx/model_q4f16.onnx` with its external data and `tokenizer.json`. All three SHA-256 digests are pinned in `model_profiles.py` and checked on every load. It downloads on first use to `data/models/privacy-filter/7ffa9a043d54/` through `hf_hub_download`, with the configured Hugging Face token. The q4f16 export is the smallest (809 MB plus a 28 MB tokenizer). On the test machine it was also the fastest in a single 1,024-token pass with 4 threads: 4.1 s, against 5.0 s for q4 (917 MB) and 4.6 s for int8 (1.6 GB).
- **Runtime.** ONNX Runtime on CPU with the embedding session options, so `model.threads` applies. No PyTorch.
- **Decoding.** `privacy_filter.py` implements the model card's constrained BIOES Viterbi decoder in numpy, with the all-zero transition biases of the pinned `viterbi_calibration.json`.
- **Categories.** Only `secret` (kind `secret`) and `account_number` (kind `account-number`) are redacted. Names, emails, addresses, phone numbers, dates and URLs are what memories are for.
- **Long texts.** The model's attention band is 128 tokens, so texts are labelled in windows of 1,024 tokens with 128 tokens of context on each side and decoded as one sequence. Time and memory grow faster than length: one 4,096-token pass took 38.6 s and peaked at 4.0 GB, while a 1,280-token window took 6.9 s at 1.3 GB, and a whole benchmark process peaked near 1.45 GB.
- **Failure.** If the model cannot be downloaded, verified, loaded or run, the write raises an error that names the setting. Nothing is stored. Falling back to rules-only would store text less redacted than the user chose, and under IDs a working device would not produce. Every writer still holds its source: a file or transcript to re-index, the MCP caller's text, the sync buffer that resumes.

### Devices and IDs

Redaction runs before ID computation. Two devices with the same release and setting produce identical content and IDs for the same input, and sync deduplicates as before.

Devices that redact differently are handled the same way: a Privacy Filter setting that differs, or a release with more rules.

- The stricter device stores only its own redaction. An unredacted copy received from a peer becomes the redacted memory under its redacted ID, and later deliveries of the peer copy are recognised as that memory and skipped.
- The less strict device receives the stricter copy as a new memory and keeps its own as well, until it adopts the setting and scrubs.

No device ever stores less redaction than its own settings require. Devices that share a workspace should use the same setting.

Placeholder kind names are part of stored content and therefore of IDs. Renaming a kind is a storage-format change.

### Existing data: `imprint scrub`

`imprint scrub [--workspace NAME] [--apply]` (`imprint/cli_scrub.py`, Go `cmd/scrub.go`) runs today's redaction over every memory and fact of one workspace.

- **Dry run.** The default counts the memories and facts that hold secrets, by kind.
- **`--apply`.** A changed memory is stored under the ID of its redacted content with a new embedding, keeping its payload: project, source, tags, `type`, `timestamp`, `tagged_at`, `llm_tagged` and device provenance. The old point is deleted afterwards, so an interrupted scrub leaves the old point for the next run. Facts are updated in place.
- **Output.** Counts and kinds only, never values.
- **Locking.** It takes the queue lock like retag. The dashboard lists it under Maintain, as a dry run unless "Redact them" is switched on.

`imprint sync import` restores a snapshot bundle byte for byte, so it scrubs the restored workspace with `--apply` before reporting completion, and now holds the queue lock while it runs. Workspace migration and project moves copy points that are already stored; scrubbing either workspace covers them.

Scrub results do not propagate by sync. ADR 0051's stamp works because a retag keeps the memory's ID. A scrubbed memory has a new ID, and sync carries no deletions, so peers cannot be told to retire the old one. Each device scrubs its own store. After that, re-received unredacted copies are redacted on import and deduplicated, and devices with the same settings converge on the same IDs. Retiring IDs across devices (tombstones) is a follow-up that general deletion also needs.

## Alternatives

- **detect-secrets (Apache-2.0).** A new dependency and lock refresh. Its entropy and keyword plugins are noisy on code, which needs per-repository baselines. It reports lines and values, not spans.
- **Vendoring all ~220 gitleaks rules.** They are Go RE2 patterns with mid-pattern flags, keyword prefilters, entropy thresholds and allowlists. Porting them faithfully is a maintenance surface of its own, and the generic rules trade precision for recall. The curated set adopts the formats that matter here and credits gitleaks.
- **Redacting at chunking or tagging time.** Writers that skip those stages, such as the MCP store, sync, summaries and facts, would bypass it. The storage boundary is the only point every write crosses.
- **Redacting on export instead of import.** The receiver must enforce its own policy anyway (older peers), and export redaction would break the sender-ID check. Legacy data is scrubbed where it is stored.
- **A `redacted` payload field.** Redundant with the placeholders, and lost or contradicted when content is redacted again elsewhere.
- **Keeping the sender's ID and vector for a redacted import.** The ID would no longer describe the content, and the vector would encode the secret.
- **Privacy Filter as default.** See the costs below; it stays opt-in.
- **Rules-only fallback when the model fails.** Rejected above: a silent weakening of an explicit privacy choice.

## Consequences

- **Coverage.** New memories, facts, summaries and sync imports no longer carry credentials in known formats, and imports from older peers are cleaned on arrival.
- **Cost of the rules.** About 0.5 ms per 1,000-character chunk, 1.2 ms per 2,000 and 2.5 ms per 4,000 (medians on an i9-13900HX under WSL2). That is negligible next to embedding.
- **Cost of Privacy Filter.** With 4 CPU threads it labels about 5–7 ms per token. Medians per chunk were 1.9 s (1,000 characters), 3.1 s (2,000) and 6.2 s (4,000) on Linux, and 1.7, 2.7 and 5.5 s on native Windows on the same machine. Eight threads helped little under load. It also costs about 1.45 GB of process memory and an 837 MB download (103 s on first use on Windows, including verification). Each process verifies the pinned digests and creates the session on its first write: 5.4 s warm, 14–22 s from a cold disk cache, and 15 s on Windows. Short-lived writers, such as the Stop hook, pay that every time. Next to CPU embedding it is the smaller cost: the default Qwen embedding took 6.7 s and 27.6 s for the same chunk sizes, and in a paired run Privacy Filter took 16% of the embedding time. On a CPU install it therefore adds roughly 15–30% to per-chunk compute; on a GPU-embedding install it would dominate.
- **Precision of Privacy Filter.** It is quiet on ordinary code: no span in 40 sampled 2,000-character chunks of this repository. On the false-positive corpus it marked 3 of 22 texts, a UUID and a SHA-256 digest among them, where the rules marked none. It found nothing in 8 of the 28 rule fixtures, which are bare tokens with little context. It complements the rules rather than replacing them.
- **Recommendation.** Keep it opt-in for now. The per-chunk cost on CPU is acceptable, but the download, about 1.45 GB of extra memory and a 5–22 s load in every short-lived writer are not acceptable defaults. Over-redacting hashes and identifiers would also damage code memories without the user asking for it. It could become a default after recall and precision are measured on real Imprint memories, and when model loading is moved out of short-lived hook processes. A GPU execution provider, or a cheap prefilter that sends only credential-looking text to the model, would cut the remaining cost.
- **Limitations.**
  - Content is redacted at the storage boundary. The opt-in LLM tagger and session summarizer still read the original text, and send it to whichever provider they are configured to use.
  - Browser-capture job bodies in `data/queue.sqlite3` keep their text, as before.
  - Source keys and URLs are identifiers that refresh needs, so they are not redacted.
  - The rules cannot see a password that is not assigned to a credential-named key, or a novel token format; the model reduces but does not remove that gap.
- **Upgrade.** Data stored before the upgrade keeps its secrets until `imprint scrub --apply` runs on each device; sync exports from an unscrubbed device still carry them to peers on older releases.
- **Platforms.** The model needs ONNX Runtime's `QMoE` and `MatMulNBits` CPU kernels. They were validated with ONNX Runtime 1.31 on Linux x86_64 (CPU build) and on native Windows x64 (the installed runtime's GPU build, CPU provider), where the first-use download, verification and redaction also ran. Intel macOS pins ONNX Runtime 1.23.2 and was not tested.

## Validation

- `tests/test_redaction.py`:
  - every supported format, cut keys and position rules;
  - a false-positive corpus: SHAs, UUIDs, npm/uv/go.sum/pip hashes, a base64 image, ordinary Python/TypeScript/YAML/SQL, i18n strings, env dumps, documentation placeholders;
  - idempotence, and linear time on 200–350 KB blobs;
  - the setting's env > config > default;
  - Privacy Filter with a stand-in model: only the two categories, placeholders kept, windows of the expected sizes stitched into the unwindowed result, valid BIOES decoding, a failing model failing the write and storing nothing;
  - every write path against an in-memory Qdrant: store, `replace_source` twice, embedding input, file ingest, learning a transcript, MCP `store` and `ingest_content`, session summary and its facts, `kg.add` and `import_fact`;
  - sync import from an older peer: redacted, relabelled, embedded again with the sender's vector discarded, provenance and `tagged_at` kept, the unchanged record keeping its vector and ID, the peer copy skipped when sent again, a tampered ID still refused;
  - two workspaces standing in for devices with Privacy Filter off and on: the stricter one stores only its redaction and skips the weaker copy on every later sync, and the other keeps both;
  - `imprint scrub` dry run and apply with payload preservation and a clean second run, and a snapshot import that scrubs.
- A real-model smoke test runs when `IMPRINT_TEST_PRIVACY_FILTER_DATA` names a data directory. It passed with the pinned download, offline: names and email kept, password and IBAN redacted, idempotent.
- `scripts/benchmark-redaction.py` produced `docs/evidence/2026-10-25-secret-redaction-cpu.json`.
- Full Python suite: 400 tests pass, 1 skipped (the real-model smoke without its data directory). Go build, `go vet ./cmd/` and `go test ./...` pass. The dashboard type check and Settings page lint pass.
- Native Windows x64: the installed runtime's Python downloaded, verified and ran the pinned model from a temporary copy of `imprint/` and a temporary data directory, and redacted the smoke text the same way as Linux.
- Not validated:
  - macOS (Intel and Apple Silicon);
  - recall of the model on Imprint's own content;
  - the dashboard Commands page rendering the new entry in a browser (it is catalog-driven);
  - two physical devices with differing settings.
