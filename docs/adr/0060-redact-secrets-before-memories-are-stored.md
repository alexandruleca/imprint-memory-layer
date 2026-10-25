---
title: ADR 0060 — Redact secrets before memories are stored
description: Every memory and fact write, and every prompt to a background model, replaces credentials with length-bearing placeholders; the most redacted version of a memory wins on every device, so one scrub reaches all of them. Deterministic rules always run; OpenAI Privacy Filter is opt-in.
---

- Status: Accepted
- Date: 2026-10-25
- Related: [ADR 0005](0005-source-lifecycle-and-hybrid-retrieval.md), [ADR 0015](0015-workspace-roots-and-source-purge.md), [ADR 0017](0017-device-provenance-and-local-project-paths.md), [ADR 0018](0018-single-sync-implementation-and-end-to-end-encryption.md), [ADR 0033](0033-stored-secret-settings.md), [ADR 0036](0036-move-memories-between-projects.md), [ADR 0051](0051-synced-tags-follow-the-latest-retag.md), [ADR 0058](0058-recall-batched-reads-with-optional-local-planning.md), [ADR 0059](0059-chat-speaks-each-models-native-tool-protocol.md)
- Restructured: 2026-10-25

## Context

Imprint skipped whole secret files (`.env*`, SSH keys, `.key`/`.p12`/`.pfx`; `imprint/source_state.py`, `imprint/cli_index.py`) and redacted nothing inside content. A key pasted into a conversation, written in a config file or quoted in a summary had three fates:

- it was stored in Qdrant as text, embedded, and shown by `search` and `get_memory`;
- sync copied it to every paired device and teammate;
- with LLM tagging or session summaries on, it was sent to the configured provider, which may be a remote API, and fragments could come back as stored tags.

The user asked for an open-source secret cleaner on chunks at ingestion and in sync. They also asked that a scrub done on one device propagate the way retags do (ADR 0051), and that everything the opt-in tagger sends be scrubbed first.

Text reaches storage through few functions:

- `vectorstore.store` writes one note: MCP `store`, summaries, auto-memory files, extracted decisions.
- `vectorstore.store_batch`, and `replace_source` which calls it, write chunks: ingest, refresh, learn, `ingest_url`, `ingest_content`, browser capture, desktop exports and the Lance import.
- `vectorstore.store_batch_precomputed` writes what sync receives, with the sender's vectors.
- Facts go through `imprint_graph.add` (MCP `kg_edit`, summaries, chat) and `imprint_graph.import_fact` (sync).

Workspace migration and project moves copy existing points. `imprint sync import` restores a raw Qdrant snapshot and SQLite file. Text reaches models through `tagger._call_llm_raw_text` (classification for every provider), `cli_summarize._call_summarizer_llm`, `recall._generate` and the dashboard chat.

Memory IDs hash project, source, content and position (`vectorstore._make_id`, ADR 0018). Sync validates that a received ID matches its content, deduplicates by ID, and has no deletions.

## Decision

### One step at every write

`imprint/redaction.py` replaces each secret with `[REDACTED:<kind>:<length>]`, where the length is that of the text it replaced. Neither the value nor a hash of it is kept anywhere. The write functions apply the step to content before anything is derived from it:

- `store` redacts the note before its ID and embedding.
- `store_batch` and `replace_source` redact placed records once and store them through the internal `_store_redacted`. The IDs `replace_source` keeps when pruning are therefore those of the redacted chunks.
- `store_batch_precomputed` first validates every sender ID against the content as sent, then redacts. A record whose content changes takes the ID of its new content (`vectorstore.relabeled_id`, which keeps the note or positioned form, as placement does). It drops the sender's vector, which was computed from the secret, and is embedded again; unchanged records keep their vectors. Older peers, and peers that redact less, are cleaned on arrival.
- `imprint_graph.add` and `import_fact` redact subject, predicate and object.

The placeholders are the audit record: `redaction.kinds` counts them. Their recorded lengths make versions of a memory recognisable (below).

### Deterministic rules, always on

A curated rule set in Python `re`, not a dependency:

- Known formats: PEM private keys (whole blocks, blocks cut by a chunk boundary on either side, keys inside JSON strings), AWS access key IDs, GitHub, GitLab, OpenAI, Anthropic, Hugging Face, Slack tokens and webhooks, Stripe, Google API keys and OAuth client secrets, npm, PyPI, SendGrid and JWTs. Several patterns follow gitleaks' rule definitions; its MIT license is in `licenses/gitleaks/LICENSE`.
- By position: the password of a `scheme://user:password@host` URL, `Authorization: Basic` credentials, and long random `Bearer` tokens.
- A literal assigned to a credential-named key (`password`, `secret`, `token`, `api_key`, `client_secret` and similar, ending the name) with `=`, `:`, `:=` or `=>`. A password-named key needs a value that is not one plain word. Other keys need a value of 12+ characters with letters and digits and Shannon entropy of at least 3. Placeholders (`${…}`, `<…>`, `{{…}}`, `****`, the words `password`/`secret`/`token`), paths, URLs, code expressions and quoted text with spaces are skipped.

Precision comes first. Git SHAs, UUIDs, lockfile hashes and base64 data contain none of the anchors the rules need. Cut into 1,000-, 2,000- and 4,000-character chunks (11,357 in all), this repository's tracked source and documentation gave two findings at every size: the same test token assigned to `TOKEN` in two test files. Overlapping matches merge under the kind of the earliest. Spans that overlap an existing placeholder are cut around it, so redacting redacted text changes nothing. Matching is linear: the patterns use possessive quantifiers, and the tail of a key that began in a previous chunk is read backwards from its END line.

### OpenAI Privacy Filter, opt-in

`redact.privacy_filter` (`IMPRINT_REDACT_PRIVACY_FILTER`, default `false`) adds [OpenAI Privacy Filter](https://huggingface.co/openai/privacy-filter) (Apache-2.0) after the rules. Its placeholders are protected the same way.

- **Model.** `openai/privacy-filter` at revision `7ffa9a043d54d1be65afb281eddf0ffbe629385b`, `onnx/model_q4f16.onnx` with its external data and `tokenizer.json`. All three SHA-256 digests are pinned in `model_profiles.py` and checked on every load. It downloads on first use to `data/models/privacy-filter/7ffa9a043d54/` through `hf_hub_download`, with the configured Hugging Face token. The q4f16 export is the smallest (809 MB plus a 28 MB tokenizer). On the test machine it was also the fastest in a single 1,024-token pass with 4 threads: 4.1 s, against 5.0 s for q4 (917 MB) and 4.6 s for int8 (1.6 GB).
- **Runtime.** ONNX Runtime on CPU with the embedding session options, so `model.threads` applies. No PyTorch.
- **Decoding.** `privacy_filter.py` implements the model card's constrained BIOES Viterbi decoder in numpy, with the all-zero transition biases of the pinned `viterbi_calibration.json`.
- **Categories.** Only `secret` (kind `secret`) and `account_number` (kind `account-number`) are redacted. Names, emails, addresses, phone numbers, dates and URLs are what memories are for.
- **Long texts.** The model's attention band is 128 tokens, so texts are labelled in windows of 1,024 tokens with 128 tokens of context on each side and decoded as one sequence. Time and memory grow faster than length: one 4,096-token pass took 38.6 s and peaked at 4.0 GB, while a 1,280-token window took 6.9 s at 1.3 GB, and a whole benchmark process peaked near 1.45 GB.
- **Failure.** If the model cannot be downloaded, verified, loaded or run, the write raises an error that names the setting. Nothing is stored. Falling back to rules-only would store text less redacted than the user chose, and under IDs a working device would not produce. Every writer still holds its source: a file or transcript to re-index, the MCP caller's text, the sync buffer that resumes.

### Versions of a memory: the most redacted wins everywhere

Two memories at the same position (project, source and chunk index) are versions of one memory when one is the other redacted further. `redaction.supersedes(newer, older)` decides this. It holds when `older` equals `newer` outside `newer`'s placeholders. Inside them, `older` must hold either the same placeholder or text of exactly the recorded length, and the latter at least once. The relation is transitive and never holds both ways, so chains (unredacted, then rules, then Privacy Filter) resolve to the most redacted version. Another secret of the same length at the same spot is the same memory, which is correct: both redact to one ID. Any other difference, a different length included, is a different memory.

The write step applies it in both directions, for local writes and imports alike:

- **Adopt.** A record takes the content of the most redacted version of itself that the workspace holds, or that arrives in the same batch (`vectorstore._redacted`). Its ID is then that version's ID: nothing new is stored, and `replace_source` keeps that version instead of pruning it.
- **Retire.** After a write, stored versions that a written record redacts further are deleted (`vectorstore._retire_superseded`).

Facts work the same way: their versions share `valid_from`. `import_fact` skips a fact the workspace holds more redacted, and replaces a held fact that the received one redacts further.

This is how a scrub propagates, the way ADR 0051's retags do, without anything new travelling. Device A scrubs: X becomes R under a new ID. When B receives R it stores R and retires X, so B does not scrub. When C, which never scrubbed, sends X again, B redacts it, adopts R and stores nothing. B passes R on to C, which retires its X.

Settings that differ resolve the same way. Privacy Filter on A, or a newer release with more rules, produces a further redaction R2. Looser devices adopt R2 and retire R when it arrives, and keep R2 when they later write or re-index the looser text themselves. The strictest redaction any device applied wins on every device that has received it. In a fully synced workspace one scrub is enough. A memory only one device ever held can be cleaned only by that device.

Scrubbed or redacted versions travel as ordinary memories, so workspace isolation, purge exclusions (ADR 0015), placement (ADR 0036) and logical IDs (ADR 0018) are unchanged:

- adoption and retirement only compare versions at one position of one workspace;
- an excluded source is refused before either runs;
- every stored ID still hashes its own content.

### Text sent to models

`tagger._call_llm_raw_text` and `cli_summarize._call_summarizer_llm` are the only places those subsystems hand text to a model. Both pass the whole prompt through `redaction.redact` before choosing a provider. So `anthropic`, `openai`, `gemini`, `ollama`, `vllm` and the local llama.cpp model all receive redacted text, with Privacy Filter applied when it is on. Classifications, stored tags and session summaries are derived from redacted text. The unused `tagger.derive_llm` topic path, which called providers directly, was removed so there is no second way out.

Two model paths are deliberately not gated:

- **Recall (ADR 0058).** Its evidence is stored memory, already redacted. Its question is the caller's own text. Its providers are limited to models on this computer, and its interactive time limits would not absorb a Privacy Filter pass.
- **The dashboard chat (ADR 0059).** What the user types is sent to the provider they chose, as they wrote it; rewriting it would change the request. Memory content in the chat comes from redacted storage, and memories the chat stores pass the write step.

### Existing data: `imprint scrub`

`imprint scrub [--workspace NAME] [--apply]` (`imprint/cli_scrub.py`, Go `cmd/scrub.go`) runs the write step's redaction, adoption included, over every memory and fact of one workspace.

- **Dry run.** The default counts the memories and facts that would change, by kind.
- **`--apply`.** A changed memory is stored under the ID of its new content with a new embedding, keeping its payload: project, source, tags, `type`, `timestamp`, `tagged_at`, `llm_tagged` and device provenance. If that version is already stored, it is kept as it is. The old point is deleted afterwards, so an interrupted scrub leaves the old point for the next run. Facts are updated in place.
- **Output.** Counts and kinds only, never values.
- **Locking.** It takes the queue lock like retag. The dashboard lists it under Maintain, as a dry run unless "Redact them" is switched on.

`imprint sync import` restores a snapshot bundle byte for byte, so it scrubs the restored workspace with `--apply` before reporting completion, and holds the queue lock while it runs. Workspace migration and project moves copy points that are already stored; scrubbing either workspace covers them, and so does the next sync from a device that scrubbed.

## Rejected alternatives

- **detect-secrets (Apache-2.0).** A new dependency and lock refresh. Its entropy and keyword plugins are noisy on code, which needs per-repository baselines. It reports lines and values, not spans.
- **Vendoring all ~220 gitleaks rules.** They are Go RE2 patterns with mid-pattern flags, keyword prefilters, entropy thresholds and allowlists. Porting them faithfully is a maintenance surface of its own, and the generic rules trade precision for recall. The curated set adopts the formats that matter here and credits gitleaks.
- **Redacting at chunking or tagging time.** Writers that skip those stages, such as the MCP store, sync, summaries and facts, would bypass it. The storage boundary is the one point every write crosses, and the model calls are the one point every prompt crosses.
- **Redacting on export instead of import.** The receiver must enforce its own policy anyway (older peers), and export redaction would break the sender-ID check.
- **Synced supersession records `{old ID → new ID}`.** This was the suggested direction for propagating scrubs. It was rejected because the old ID is a 64-bit hash of the content that contained the secret, together with its project, source and position. Anyone holding the redacted memory knows everything else that went into that hash. Each peer, and each teammate a workspace is shared with, could test password guesses offline until the hash matched. Weak passwords would fall in seconds. Persisting the records on every device, so that they propagate transitively and prevent resurrection, would spread that hash furthest. The redacted memory itself, with lengths in its placeholders, carries enough to recognise its earlier versions without any hash.
- **A slow, salted hash of the old ID.** It only raises the cost of the same offline guessing, and still keeps a verifier of the secret.
- **Recognising versions by provenance (`recorded_device`, `recorded_at`, position).** `recorded_at` comes from the clock and can repeat on Windows, where it ticks coarsely, for notes written in quick succession. Data written before provenance existed has none.
- **Placeholders as unbounded wildcards.** A placeholder at the end of a note would absorb any trailing text. Distinct notes that share a source, such as one session's summary entries, could then retire each other. Recording the length makes recognition exact. The cost is that the stored text shows how long each secret was, which gives an attacker nothing to check guesses against.
- **The length in a payload field.** It would have to travel through sync and could disagree with the content. In the placeholder it is part of the content and its ID, and facts need no schema change.
- **Keeping the sender's ID and vector for a redacted import.** The ID would no longer describe the content, and the vector would encode the secret.
- **One shared model client for tagger, summarizer and recall.** Their configuration keys, limits and local-model handling differ. Merging them would be a refactor of recall, which had just landed, that the gate does not need.
- **Privacy Filter as default.** See the costs below; it stays opt-in.
- **Rules-only fallback when the model fails.** Rejected above: a silent weakening of an explicit privacy choice.

## Consequences

- **Coverage.** New memories, facts, summaries, sync imports and background model prompts no longer carry credentials in known formats. Imports from older peers are cleaned on arrival. A scrub on one device reaches every device that syncs with it, transitively, and the strictest redaction wins.
- **Cost of the rules.** About 0.5 ms per 1,000-character chunk, 1.2 ms per 2,000 and 2.5 ms per 4,000 (medians on an i9-13900HX under WSL2). That is negligible next to embedding.
- **Cost of versions.** Every write batch makes one filtered Qdrant query for redacted versions at its positions; it uses the existing full-text index on `content`. A batch that contains redacted text makes a second query, for the versions it may retire.
- **Cost of Privacy Filter.**
  - With 4 CPU threads it labels about 5–7 ms per token. Medians per chunk were 1.9 s (1,000 characters), 3.1 s (2,000) and 6.2 s (4,000) on Linux, and 1.7, 2.7 and 5.5 s on native Windows on the same machine. Eight threads helped little under load.
  - It costs about 1.45 GB of process memory and an 837 MB download, which took 103 s on first use on Windows, including verification.
  - Each process verifies the pinned digests and creates the session on its first write: 5.4 s warm, 14–22 s from a cold disk cache, and 15 s on Windows. Short-lived writers, such as the Stop hook, pay that every time.
  - It is smaller than CPU embedding: the default Qwen embedding took 6.7 s and 27.6 s for the same chunk sizes, and in a paired run Privacy Filter took 16% of the embedding time. On a CPU install it adds roughly 15–30% to per-chunk compute; on a GPU-embedding install it would dominate.
  - With LLM tagging also on, each classification prompt passes through it as well.
- **Precision of Privacy Filter.** It is quiet on ordinary code: no span in 40 sampled 2,000-character chunks of this repository. On the false-positive corpus it marked 3 of 22 texts, a UUID and a SHA-256 digest among them, where the rules marked none. It found nothing in 8 of the 28 rule fixtures, which are bare tokens with little context. It complements the rules rather than replacing them.
- **Recommendation.** Keep Privacy Filter opt-in for now. The per-chunk cost on CPU is acceptable. The download, about 1.45 GB of extra memory and a 5–22 s load in every short-lived writer are not acceptable defaults. Over-redacting hashes and identifiers would also damage code memories without the user asking for it. It could become a default after recall and precision are measured on real Imprint memories, and when model loading is moved out of short-lived hook processes. A GPU execution provider, or a cheap prefilter that sends only credential-looking text to the model, would cut the remaining cost.
- **Disclosure.** A placeholder shows how many characters the secret had.
- **Limitations.**
  - Browser-capture job bodies in `data/queue.sqlite3` keep their text, as before.
  - Source keys and URLs are identifiers that refresh needs, so they are not redacted.
  - The rules cannot see a password that is not assigned to a credential-named key, or a novel token format; the model reduces but does not remove that gap.
  - Recall prompts and dashboard chat messages are not redacted, for the reasons above.
  - Versions whose redactions are incomparable, each redacting a span the other kept, are both kept. That happens only between releases whose rules overlap partially.
  - A peer on a release older than this one ignores versions and keeps both copies.
- **Upgrade.** Data stored before the upgrade keeps its secrets until a scrub runs on some device holding it and the result syncs. Sync exports from an unscrubbed device still carry the secrets to peers on older releases.
- **Platforms.** The model needs ONNX Runtime's `QMoE` and `MatMulNBits` CPU kernels. They were validated with ONNX Runtime 1.31 on Linux x86_64 (CPU build) and on native Windows x64 (the installed runtime's GPU build, CPU provider), where the first-use download, verification and redaction also ran. Intel macOS pins ONNX Runtime 1.23.2 and was not tested.

## Invalidation conditions

- If OpenAI Privacy Filter's recall and precision are measured on real Imprint memories and model loading moves out of short-lived hook processes, revisit making it the default, as the record says.
- If a write path stores memories or facts without passing through the write functions, as snapshot restore would without its scrub, the guarantee is broken: route it through them.
- If recall or the dashboard chat start sending text to remote providers that the user did not type, their exemption from redaction must be revisited.

## Validation

- `tests/test_redaction.py`:
  - every supported format, cut keys and position rules;
  - a false-positive corpus: SHAs, UUIDs, npm/uv/go.sum/pip hashes, a base64 image, ordinary Python/TypeScript/YAML/SQL, i18n strings, env dumps, documentation placeholders;
  - idempotence, and linear time on 200–350 KB blobs;
  - `supersedes` across a chain, against same-length and different-length secrets and against other changes;
  - the setting's env > config > default;
  - Privacy Filter with a stand-in model: only the two categories, placeholders kept, windows of the expected sizes stitched into the unwindowed result, valid BIOES decoding, a failing model failing the write and storing nothing;
  - every write path against an in-memory Qdrant: store, `replace_source` twice, embedding input, file ingest, learning a transcript, MCP `store` and `ingest_content`, session summary and its facts, `kg.add` and `import_fact`;
  - sync import from an older peer: redacted, relabelled, embedded again with the sender's vector discarded, provenance and `tagged_at` kept, the unchanged record keeping its vector and ID, the peer copy skipped when sent again, a tampered ID still refused;
  - three workspaces standing in for devices that all held a legacy memory and fact:
    - only A scrubs, and the result reaches B and C through sync;
    - C's unscrubbed copy is not stored again;
    - A then scrubs with Privacy Filter, and its stricter version replaces the rules-only one on B and C, whose setting is off;
    - B writing the old text itself stores nothing new;
    - provenance and `tagged_at` survive;
  - a looser device re-indexing a source keeps the stricter chunk;
  - two devices with differing settings converge on the stricter version;
  - the tagger (anthropic, openai, gemini, ollama, vllm, local) and the summarizer (the same six) receive only redacted prompts through stand-in providers, Privacy Filter included; these tests fail with the gate removed;
  - `imprint scrub` dry run and apply with payload preservation and a clean second run, and a snapshot import that scrubs.
- A live run against a real Qdrant 1.17.1 server checked the version queries (full-text and keyword filters) with three workspaces: A scrubbed; B received the result and retired its copy; C's copy was skipped; the result reached C; B writing the old text stored nothing.
- A real-model smoke test runs when `IMPRINT_TEST_PRIVACY_FILTER_DATA` names a data directory. It passed with the pinned download, offline: names and email kept, password and IBAN redacted, idempotent.
- `scripts/benchmark-redaction.py` produced `docs/evidence/2026-10-25-secret-redaction-cpu.json`. Those measurements predate the length in placeholders, which does not change the work done.
- Full Python suite: 436 tests pass, 1 skipped (the real-model smoke without its data directory). `go build`, `go vet ./cmd/` and `go test ./...` pass. The dashboard type check and Settings page lint pass.
- Native Windows x64: the installed runtime's Python downloaded, verified and ran the pinned model from a temporary copy of `imprint/` and a temporary data directory, and redacted the smoke text the same way as Linux.
- Not validated:
  - macOS (Intel and Apple Silicon);
  - recall of the model on Imprint's own content;
  - the dashboard Commands page rendering the new entry in a browser (it is catalog-driven);
  - physical devices syncing over a relay; propagation was exercised with workspaces and the import buffer;
  - real provider endpoints for the model-input gate; stand-ins recorded what each SDK call received.
