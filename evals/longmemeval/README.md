# LongMemEval-S retrieval benchmark

Measures Imprint's retrieval on [LongMemEval](https://huggingface.co/datasets/xiaowu0162/longmemeval)
(v1, S variant, 470 answerable questions) with the method of
[ai-memory's evaluator](https://github.com/akitaonrails/ai-memory/tree/main/evals), so the numbers sit in
the same table as their [published releases](https://github.com/akitaonrails/ai-memory/tree/main/docs/benchmarks).
It measures; it changes nothing in `imprint/`, and refuses to run when `imprint/` differs from `HEAD`
(`--allow-dirty` overrides that and the report says so).

```bash
evals/longmemeval/run.sh --sample 10          # smoke test: the first 10 questions
evals/longmemeval/run.sh --fetch              # full run, downloading the dataset if missing
evals/longmemeval/run.sh --run-dir <dir>      # resume an interrupted run
evals/longmemeval/run.sh report <dir>         # rebuild <dir>/report.md and report.json
python -m unittest tests.test_longmemeval_eval  # scorer and parser checks, no dataset needed
```

## Setup

The harness runs on Linux, WSL included: it reads `/proc` to see who holds its Qdrant port and locks its data
dir with `flock`. Release archives and container images leave `evals/` out. It runs Imprint from this checkout in the repository's `.venv` (gitignored), built from the
locked CPU profile with the uv version the release scripts pin:

```bash
UV_VERSION=0.13.6 bash scripts/fetch-uv.sh linux amd64 /tmp/uv   # checksum-verified uv
/tmp/uv/bin/uv venv --python 3.12 .venv
VIRTUAL_ENV=.venv /tmp/uv/bin/uv pip install --require-hashes \
  -r requirements/base.lock.txt -r requirements/cpu.lock.txt -r requirements/llm.lock.txt
```

`IMPRINT_BENCH_PYTHON` points `run.sh` at another interpreter.

## What a run does

1. **Dataset.** `longmemeval_s` from `xiaowu0162/longmemeval`, sha256
   `08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894`, in `datasets/` (gitignored). Any other
   file is refused. The 30 `*_abs` abstention questions are left out after `--sample`, as in ai-memory.
2. **Isolation.** The harness and every server use their own Imprint data dir (`.data/`, gitignored) and
   their own Qdrant on port 17333/17334 (`--qdrant-port`); inherited `IMPRINT_*` variables and Claude Code's
   `CLAUDE_CODE_SESSION_ID` are dropped. On a
   machine where Imprint is installed, its Qdrant on 6333 is never contacted. Imprint takes any server
   answering on its port for its own daemon, so the harness first checks that both ports are free or held by
   the Qdrant binary in `.data/`, and stops otherwise. Each question gets a new workspace
   (`lme-<question id>`: its own Qdrant collection, graph database and WAL), dropped once the question is
   scored. A workspace left by an interrupted run is dropped before the next one starts.
3. **Ingestion.** Every haystack session, in order, and every turn in it becomes one MCP `store` call to a
   `python -m imprint` stdio server, the tool an agent calls. The text is ai-memory's hook replay:
   `[session date: <date>] <turn>`, user turns capped at 16 KiB with the prefix, assistant turns cut to a
   2,000-byte excerpt before it. `project` is `lme-<question id>`, `source` an opaque per-session key (below);
   `type` is left to Imprint's classifier. No LLM runs: Imprint's `store` tags without one, the harness sets
   `IMPRINT_LLM_TAGS=0` (the default), and nothing starts the dashboard's background jobs.
4. **Query.** The question text through MCP `search` with `limit=10`, `workspace` and `project`, as
   ai-memory passes workspace, project and limit 10 to `memory_query`. Every mode below is a separate call on
   the same ingested store, timed as the MCP round trip.
5. **Scoring.** The hits the tool's text shows, in order, are mapped to their sessions through the memory ids
   `store` returned; repeated sessions keep their first rank; then hit@k (k = 1, 3, 5, 10), recall@k and
   recall-all@k per question, macro-averaged overall and per `question_type`. Latency is nearest-rank
   p50/p95 over whole milliseconds. Context tokens are the characters of the shown hits' text divided by 4,
   rounded up.
6. **Output.** `runs/<UTC time>[-label]/`: `config.json` (provenance), `results.jsonl` (one line per scored
   question, appended and fsynced as each finishes: the resume point), `errors.jsonl`, `progress.json`,
   `logs/` (server stderr), `report.md` and `report.json`.

## Modes

| mode | `search` arguments | what it measures |
|---|---|---|
| `hybrid` | defaults (`mode=hybrid`, `detail=compact`, `max_chars=4000`) | Imprint as shipped |
| `semantic` | `mode=semantic` | dense only: Qdrant vector search, no lexical channel |
| `hybrid-wide` | `max_chars=12000` | the same ranking with every one of the ten hits shown |
| `semantic-wide` | `mode=semantic, max_chars=12000` | dense only, every hit shown |

There is no graph ablation and no sparse/BM25 run, because neither changes anything at this commit:

- `search` never reads the graph (the SQLite temporal facts behind `kg_query`), and `store` writes no facts,
  with or without an LLM. A "graph off" run would make the same calls and get the same results.
- Imprint's hybrid search is its dense vector plus a lexical channel scored in Python over candidates read
  through Qdrant's full-text payload index, fused by reciprocal rank. Imprint creates no Qdrant sparse
  vectors, and no setting adds them.

## Where this differs from ai-memory's harness

- **Unit of retrieval.** ai-memory's hook replay compiles each session into one page (`sessions/<uuid>.md`)
  and `memory_query` ranks pages, so ten hits are usually ten sessions. Imprint stores and returns single
  turns, so several of its ten hits can come from one session and fewer than ten sessions reach the cutoff.
  Both are scored over distinct sessions, as ai-memory scores.
- **Output budget.** Imprint's `search` returns one text with a 4,000-character default budget and shows only
  the hits that fit, often fewer than ten. Scores count the hits shown, which is what the agent can read.
  The `-wide` modes show what the same ranking scores when all ten fit.
- **Context tokens.** ai-memory counts each hit's `title` + `snippet` and leaves out paths, scores and other
  JSON fields. Imprint has no title; the count is each hit's excerpt, without its provenance line or expand
  hint. The report also gives the whole response (`response tokens`), which is what an agent actually reads.
- **Session provenance.** ai-memory stores a UUIDv5 per session (`lme-<question>\n<raw id>`); so does this
  harness, and it puts the key in each memory's `source` as `s:` and 12 hex digits separated by hyphens.
  Imprint's lexical scorer credits any query term found inside `source`; a source with no two adjacent word
  characters cannot match a term (two characters or more), so provenance cannot move a ranking, and
  LongMemEval's own ids (`answer_…`) never reach the store.
- **Latency.** ai-memory times `memory_query` over HTTP MCP while other questions ingest concurrently.
  Here searches go to a separate query server with Imprint's default thread count, and ingestion pauses for
  the timed call, so the number is the search alone; other load on the host still counts. Before a
  question's first timed search the query server reads the workspace's status, untimed, as the server that
  stored the turns would already have its collection open. Modes run in the order above; `hybrid` is the
  first search on each new store.
- **Embedding model.** ai-memory's `local` mode embeds with all-MiniLM-L6-v2 (22M parameters, 256-token
  window); Imprint embeds with Qwen3-Embedding-0.6B (2,048 tokens) on llama.cpp, with a query instruction
  written for code and engineering decisions. Their FTS mode embeds nothing.
- **Isolation.** ai-memory replays every question into one data dir and workspace, a project per question,
  and scopes each query to that project; index-wide statistics can span the questions ingested before it.
  Here each question has its own collection, so no question's ranking can depend on another's haystack.
