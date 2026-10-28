---
title: Secret redaction
description: How Imprint replaces credentials with placeholders before memories and facts are stored or synced and before text is sent to background models, the opt-in Privacy Filter model, and imprint scrub, whose result reaches every synced device.
---

Imprint replaces credentials with placeholders before text is stored, embedded or synced, and before it is sent to a model for tagging or session summaries. A secret becomes `[REDACTED:<kind>:<length>]`, for example `export OPENAI_API_KEY=[REDACTED:openai-key:164]`. The length is that of the text it replaced. Neither the value nor a hash of it is kept. [ADR 0060](adr/0060-redact-secrets-before-memories-are-stored.md) records the design and its trade-offs.

## Where it applies

Every write passes through one redaction step before the memory's ID and embedding are computed:

- `imprint ingest`, `refresh`, `learn` (conversations, auto-memory files, desktop exports), `ingest-url`, `refresh-urls`;
- the MCP `store`, `ingest_url`, `ingest_content` and `kg_edit` tools, and the dashboard chat's tools;
- session summaries and their facts, browser captures;
- sync imports, live or from a snapshot bundle, whatever the sending device did.

A synced record that redaction changes is stored under the ID of its redacted content, and its vector is computed again from the redacted text; the sender's vector encoded the secret. Records redaction leaves unchanged keep the sender's ID and vector.

The same step keeps one version of each memory: the most redacted one (see [Scrubs reach every device](#scrubs-reach-every-device)).

Complete source snapshots are redacted before splitting into spans. Export/import and `scrub` also apply the current policy to snapshot text and extracted metadata. Existing stricter chunk redactions carry into the full source. A chunk's `[path]` prefix is derived from the source's path, not read from the source, so redaction leaves it as it is: the path is stored as-is in the source fields anyway, and a prefix the Privacy Filter marked while reading a single chunk would no longer match its source. See [source memory](source-memory.md) and [ADR 0081](adr/0081-verifiable-redacted-source-snapshots.md).

## Text sent to models

The opt-in LLM tagger (`tagger.llm`) and the session summarizer (`summarizer.enabled`) redact their whole prompt before it reaches any provider: Anthropic, OpenAI, Gemini, Ollama, vLLM or the local model. With Privacy Filter on, it runs on the prompt too. Tags, types and summaries are therefore derived from redacted text, and no secret fragment can come back as a stored tag.

Two paths send text unchanged:

- **Recall's local planning.** Its evidence is already-redacted memory, and its model runs on this computer.
- **The dashboard chat.** What you type goes to the provider you chose, as you wrote it. Memories it reads are redacted, and memories it stores pass the write step.

Whole secret files (`.env*`, SSH keys, `.key`/`.p12`/`.pfx`) are still skipped by ingest, as before.

## What the rules find

Deterministic rules always run. They aim at credentials in known formats and avoid ordinary code: git SHAs, UUIDs, lockfile hashes and base64 data are left alone.

| Kind | Matches |
| --- | --- |
| `private-key` | PEM private key blocks (RSA, EC, OpenSSH, PGP, PKCS#8), including blocks cut by a chunk boundary and keys inside JSON strings |
| `aws-access-key` | AWS access key IDs |
| `github-token` | GitHub personal, OAuth, app and fine-grained tokens |
| `gitlab-token` | GitLab personal, deploy, runner, pipeline and other tokens |
| `openai-key`, `anthropic-key`, `huggingface-token` | API keys of those services |
| `slack-token`, `slack-webhook` | Slack bot/user/app tokens and incoming-webhook URLs |
| `stripe-key` | Stripe secret, restricted and webhook-signing keys (publishable keys are not secret) |
| `google-api-key`, `google-oauth-secret` | Google API keys and OAuth client secrets |
| `npm-token`, `pypi-token`, `sendgrid-key` | Package registry and SendGrid tokens |
| `jwt` | Signed JSON Web Tokens |
| `password` | The password in `scheme://user:password@host`, and a literal assigned to a password-named key that is not a single plain word |
| `secret` | A long, random literal (letters and digits, 12+ characters) assigned to a key named like `secret`, `token`, `api_key`, `client_secret`, `access_key` |
| `basic-auth`, `bearer-token` | `Authorization: Basic` credentials and long random bearer tokens |

A credential-named key followed by a reference instead of a value, such as `password = os.environ["DB_PASSWORD"]`, `token: ${{ secrets.GITHUB_TOKEN }}` or `api_key: <your key>`, is not redacted. Several token formats follow [gitleaks](https://github.com/gitleaks/gitleaks)' rules (MIT).

## Privacy Filter (opt-in)

```bash
imprint config set redact.privacy_filter true
```

The setting is also on the dashboard's Settings page, under Redaction. It adds [OpenAI Privacy Filter](https://huggingface.co/openai/privacy-filter), an Apache-2.0 token-classification model, after the rules. Imprint redacts only its `secret` (kind `secret`) and `account_number` (kind `account-number`) categories. Names, emails, addresses, phone numbers, dates and URLs stay, because they are useful memory content.

- The first write after enabling it downloads the pinned q4f16 ONNX export: 809 MB of weights plus a 28 MB tokenizer, into `data/models/privacy-filter/`. Every file is checked against a pinned SHA-256. A gated download uses `huggingface.token` like the embedding model.
- It runs on CPU with ONNX Runtime and `model.threads` threads (by default half the logical CPUs). On an i9-13900HX with 4 threads it takes about 2 s for a 1,000-character chunk and 5.5–6 s for a 4,000-character one, and adds about 1.4 GB of memory. Long-running processes, such as the MCP server and the dashboard, keep the model loaded after their first write. Ingest and learn become noticeably slower. Each new process verifies and loads the model once, which takes a few seconds; the Stop hook pays that on every run.
- If it is enabled and cannot download, verify, load or run, the write fails with an error naming the setting, and nothing is stored. Imprint never stores text less redacted than you chose. Fix the download, or turn the setting off: `imprint config set redact.privacy_filter false`.
- The model reads context, so it catches credentials the rules cannot name. It can also mark a hash or an identifier as a secret: in Imprint's tests it marked a UUID and a SHA-256 digest that the rules leave alone. On ordinary code it rarely marks anything.
- Devices that share a workspace may differ: a device with it on wins (see below).

## Data stored before: `imprint scrub`

Memories and facts stored before redaction existed, or under fewer rules, keep their secrets until they are scrubbed:

```bash
imprint scrub                  # count memories and facts with secrets, by kind; changes nothing
imprint scrub --apply          # redact them; changed memories are embedded again
imprint scrub --workspace team --apply
```

A scrubbed memory moves to the ID of its redacted content and keeps its project, source, tags, type, timestamps, `tagged_at` and device provenance. Facts are updated in place. The output lists counts and kinds, never values. Scrub takes the queue lock like `retag`; the dashboard offers it under Commands → Maintain, as a dry run unless **Redact them** is on. With Privacy Filter enabled, scrub runs it over every memory, which takes the per-chunk time above for the whole workspace.

`imprint sync import <bundle>` scrubs the restored workspace automatically, because a snapshot bundle is a raw copy of storage that may predate redaction. It holds the queue lock while it restores and scrubs.

## Scrubs reach every device

Scrub once, on any device. Two memories at the same place (project, source and chunk position) are versions of one memory when one is the other redacted further. Each placeholder records the length of the text it replaced, which is what makes the comparison exact. Every write keeps the most redacted version:

- **A device that receives a further-redacted version** stores it and deletes the version it held. It does not need to scrub.
- **A device that receives, or writes again, a less redacted version of a memory it already holds more redacted** keeps what it has and stores nothing.

So after device A scrubs:

1. Device B receives A's redacted memories at its next sync and drops its own copies.
2. Device C, which has not synced with A, cannot bring the old copies back: B keeps the redacted versions when C sends them.
3. C receives the redacted versions from B and drops its old copies too.

Facts behave the same way: a fact's versions share their creation time.

The same holds when devices redact differently, with Privacy Filter on one of them or a newer release with more rules. The device that redacts more wins on every device its memories reach, including when a looser device re-indexes the same file. Devices with the same release and settings produce the same text and IDs, and sync deduplicates as usual.

In a workspace synced both ways, one scrub is enough. A memory that only one device ever held can be cleaned only by that device. A device on an older release, before versions existed, keeps both copies.

## Limitations

- Each placeholder shows how many characters the secret had. Nothing that could confirm a guess of the secret is stored.
- Source keys and URLs are not redacted; refresh needs them to re-fetch.
- Recall prompts and dashboard chat messages are sent as written (see [Text sent to models](#text-sent-to-models)).
- A password that is not assigned to a credential-named key, or a token in a format the rules do not know, passes the rules. Privacy Filter narrows that gap, but it is a redaction aid, not a guarantee.
