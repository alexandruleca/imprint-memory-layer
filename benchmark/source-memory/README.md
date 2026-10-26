---
title: Source memory paired experiment
description: Targeted evidence retrieval and aggregate source reconstruction checks on 2026-10-26.
---

Measured on native macOS arm64, Python 3.12, comparing dev `1e8eb53` with the candidate source hash recorded in [report.json](report.json). Candidate HEAD names the branch base; the implementation was uncommitted during measurement. Subsequent source edits tighten malformed-record rejection, scrub metadata, align reused sync chunk spans and update comments; they do not change these fixtures' content/chunking. No production index was modified.

The script freezes each input into a temporary directory before either preparation runs. Eighteen deterministic synthetic sources contain twelve declared queries and six noise sources. Relevant chunks must match both the source and the supporting text, not just the right conversation. Four controls put evidence in ordinary long answers or code; eight cases exercise brief answers, markup questions, available analysis, tool arguments/results, tiny files and fallback import preambles. Labels were declared before the run. Real retrieval uses the same cached `onnx-community/Qwen3-Embedding-0.6B-ONNX` model and production hybrid search in disposable in-memory Qdrant for both arms. The candidate runtime runs both searches, isolating preparation changes; this is not a full historical-runtime comparison.

Optional local coverage uses 171 frozen transcript files from this device and 594 supported tracked code/text files from the baseline checkout. Only aggregate local statistics are retained; local conversations are never embedded by this script. Source snapshot reconstruction is checked against the complete redacted readable representation for every candidate input. Baseline semantic sub-splitting is disabled during preparation to avoid additional model inference; native AST code chunking remains enabled except for the explicit fallback fixture. Existing legacy chunks have no canonical manifests, so zero verified snapshots is absence of proof, not proof of wholesale old data loss.

| Measure | Before | After |
| --- | ---: | ---: |
| Synthetic evidence present | 4/12 | 12/12 |
| Synthetic evidence Recall@1 / Recall@5 | 4/12 | 12/12 |
| Synthetic evidence MRR@5 | 0.333 | 1.000 |
| Exact reconstructed sources | No canonical manifest | 783/783 |
| Local conversation chunks | 1,580 | 3,586 |
| Local conversation indexed characters | 2,649,593 | 19,117,628 |
| Compressed local conversation source bodies | None | 3,879,498 bytes |
| Repository file chunks | 2,295 | 1,562 |
| Compressed repository source bodies | None | 1,738,501 bytes |
| Preparation, all inputs | 9.41 s | 15.17 s |
| Synthetic embedding/indexing | 4.86 s | 7.09 s |
| Twelve synthetic queries | 1.57 s | 2.08 s |

The known omission cases improve. The four existing controls retain rank one. This small deliberately targeted regression corpus is not held out, does not establish general ranking improvement, and measures no generated answers. Broad code retrieval may regress when syntax boundaries change. Timings have one repetition and uncontrolled order/cache effects; no speedup is claimed. Snapshots add disk/transfer work, and full local transcript embedding cost is unmeasured. Compressed body counts exclude manifests, SQLite pages, vector storage and other metadata. Private local inputs cannot be reproduced independently from this aggregate report; live transcripts can change between runs. The public synthetic fixtures and frozen baseline repository files are reproducible.

Run in a provisioned disposable Python environment with the baseline checkout and cached embedding model:

```sh
python scripts/measure-source-memory.py \
  --baseline-root /path/to/dev-checkout \
  --output /tmp/source-memory-report.json \
  --local-code --retrieval --model-cache /path/to/cached/models
# Add --local-transcripts for aggregate coverage of this device's client logs.
```

This measurement is separate from the [public retrieval/task benchmark](../../docs/benchmarks.md). Neither these numbers nor the unit suite imply that the owner's existing installed index has been migrated.

Validation after the final sync fix: 626 Python tests passed (two skipped), and `go test ./...` passed on native macOS arm64. A separate redaction-boundary test also passed after strengthening its fixture. Runtime/installers on other platforms and real paired-device end-to-end behavior remain unverified.
