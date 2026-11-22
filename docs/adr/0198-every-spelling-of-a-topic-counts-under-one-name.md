---
title: ADR 0198 — Every spelling of a topic counts under one name
description: A topic's key folds case, separators and plural endings; the spelling most memories use names the key. The tagger stores new topics under that name, and every view that counts or filters topics merges the key's spellings. Stored tags and the tagging prompt are unchanged.
---

- Status: Accepted
- Date: 2026-11-22
- Related: [ADR 0163](0163-tagging-asks-for-its-classification-as-one-line-of-json.md) and [ADR 0164](0164-the-tagging-prompt-keeps-its-layout-despite-checkpoints.md) (the prompt, unchanged), [ADR 0150](0150-topics-page-on-the-server-and-workspace-copies-are-previewed.md) (Topics page), [ADR 0148](0148-the-graph-draws-itself-on-a-canvas-and-as-a-star-system.md) (graph), [ADR 0051](0051-synced-tags-follow-the-latest-retag.md) (synced tags), [ADR 0199](0199-topics-form-a-map-of-their-own.md) (the topic map built on this), [tagging](../tagging.md)

## Context

LLM tagging asks for "1-4 short topic tags" (`tagger.py`). `_sanitize_tags` lowercases them and turns other characters into hyphens. Nothing relates one topic to another: the prompt lists existing types and domains but not topics, and `taxonomy.merge` only redirects registry counts (nothing calls it).

The owner's store was audited on 2026-11-22 (388,255 memories):

- **Topics are fragmented.** It held 126,302 distinct topics; 79,010 were used once.
- **One idea appears under several spellings.** The key below finds 5,097 keys with more than one spelling among the 100,000 most used, for example:
  - `unit-tests` | `unit-test` | `unittest`
  - `health-checks` | `health-check` | `healthcheck`
  - `stylesheets` | `style-sheets` | `style-sheet`
- **Each spelling is counted on its own.** `region-definitions` (2,570) and `region-definition` (2,285) were two Topics-page rows and two graph nodes, and a filter on either missed the other.

ADRs 0163 and 0164 measured that small changes to the tagging prompt shift its classifications: the share of domains reused from the registry, agreement on types, and hand-labelled acceptability.

## Decision

1. **Key.** A topic's key is its lowercase words without separators (space, `_`, `-`), each word's plural ending folded:
   - `-ies` → `-y`;
   - `-sses`, `-xes`, `-zes`, `-ches`, `-shes` lose `-es`;
   - a final `-s` is dropped, except after `ss`, `us` or `is`, or when the word has three letters or fewer.
2. **Name.** Across the workspace (one facet of up to 100,000 values on `tags.topics`), the spelling most memories use names its key; a tie goes to the shorter spelling. The vocabulary is read again when the collection's point count changes, or after five minutes.
3. **Written under the name.** After the LLM classification is parsed, each topic takes its key's name when the store already has one. The prompt, its layout and the reply format are unchanged.
4. **Counted and matched under the name.** These merge every spelling of a key, and filter on a topic with `MatchAny` over all its spellings:
   - the graph's scopes (root, project, topic, source and chunk);
   - the Topics page and topic detail;
   - the project overview and statistics;
   - the dashboard summary;
   - the source reader's topic highlight;
   - workspace migration by topic.
5. **Never starts a store.** The naming and filtering helpers read the vocabulary only from a collection this process has already opened. A process that never opened one gets the spellings as given. Graph views open the collection, as they already do.
6. **Stored tags are not rewritten.**

## Rejected alternatives

- **List the most used topics in the tagging prompt.** About 150 topics would add a few hundred tokens to a prompt of about 1,000 (ADR 0164's figure), roughly half again its prefill time. ADRs 0163 and 0164 showed that prompt changes shift classifications, so this one would need its own agreement and blind-label measurement first.
- **Rewrite stored tags.** Every changed memory would be retagged and sent to every peer again (ADR 0051), for a result that merging at read time already gives. A new name would also need another rewrite.
- **Fold `-ing`, `-ed` and other suffixes.** On this store that would merge different ideas: `building-layout` and `building-data` (floor plans) with `build` (a topic of the infra group). Plural folding alone already found 5,097 keys.
- **Merge synonyms by embedding similarity.** This needs a threshold per pair and the embedding model at read time, and different devices or runs could disagree. It belongs in a reviewed Organize proposal, not in a silent merge.
- **Use `taxonomy.merge`.** It redirects only the registry's counts, never payloads or facets.

## Consequences

- **Fewer, larger topics.** The Topics page and the graph show one row or node per key, with the counts of all its spellings. Topic URLs and filters keep working with any spelling.
- **New tags converge.** Once a spelling leads, new memories use it.
- **Some spellings stay separate:**
  - Synonyms such as `unit-testing` and `unit-tests`.
  - Irregular plurals (`aliases` → `aliase`).
- **Small inaccuracies:**
  - A memory tagged with two spellings of one key is counted twice in merged counts. A distinct-memory count would need a scan.
  - Names are derived on each device from its own counts. Paired devices hold nearly the same memories and so nearly always agree; a tie can name a topic differently on two devices until counts move.
- **Each process reads the vocabulary periodically.** That is one facet of up to 100,000 values, measured at 0.42 s on the owner's store from WSL and about 2.7 s through the Windows runtime including connection setup, cached for five minutes or until the count changes. Without an opened collection, the tagger keeps topics as the model wrote them.
- **Compatibility:** older devices and existing API and MCP clients see the same payloads. Only the counts and names in views change.

## Invalidation conditions

- If folding merges topics the owner considers different (a reported pair), then add an exception list to the key, or stop folding that ending.
- If a measured prompt that lists the vocabulary keeps ADR 0164's classification agreement and costs little, then add it: names would converge before storage, not after.
- If paired devices show different names for one topic often enough to confuse, then sync the vocabulary's names, as placement rules are synced.
- If the vocabulary facet grows slow (well above a second) on larger stores, then compute it in the idle slot and keep it on disk.

## Validation

- `tests/test_topics.py` (15 tests, Windows Python 3.13 venv, native copy) covers:
  - **Keys:** spellings share a key; different topics keep different keys.
  - **Naming:** the most used spelling names a key and ties go to the shorter. With no opened store, merging and naming use only their input and never reach a store.
  - **Tagging:** new tags and the tagger's result take the stored name.
  - **Filters and views:** a filter on one spelling matches all of them; the project root, the Topics page and topic detail count every spelling under one name.
- `test_graph_scope`, `test_dashboard_summary`, `test_background_tagging`, `test_tagging_throughput` and `test_data_files` pass.
- **On the live store** (read only): 94,781 keys from 100,000 spellings, 5,097 of them with more than one spelling.
- **Not yet measured:** how quickly names converge in practice, which needs a week of background tagging.
