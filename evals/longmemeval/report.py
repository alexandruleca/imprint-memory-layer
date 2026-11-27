"""Aggregate a run dir's results.jsonl into report.json and report.md."""
from __future__ import annotations

import json
from pathlib import Path

from evals.longmemeval import scoring

HEADLINE = ["hit@1", "hit@3", "hit@5", "hit@10", "recall@5", "recall@10"]


def _rows(results: list[dict], mode: str) -> list[dict]:
    return [{"question_type": r["question_type"], **r["modes"][mode]} for r in results if mode in r["modes"]]


def build(run_dir: Path) -> tuple[dict, str]:
    run_dir = Path(run_dir)
    config = json.loads((run_dir / "config.json").read_text())
    results = []
    for line in (run_dir / "results.jsonl").read_text().splitlines() if (run_dir / "results.jsonl").exists() else []:
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    order = {qid: i for i, qid in enumerate(r["question_id"] for r in results)}
    results.sort(key=lambda r: order[r["question_id"]])
    modes = list(config["settings"]["modes"])
    aggregates = {mode: scoring.aggregate(_rows(results, mode)) for mode in modes if _rows(results, mode)}
    errors = []
    if (run_dir / "errors.jsonl").exists():
        errors = [json.loads(line) for line in (run_dir / "errors.jsonl").read_text().splitlines() if line.strip()]
    done = {r["question_id"] for r in results}
    ingest = {
        "stored": sum(r["stored"] for r in results),
        "distinct_memories": sum(r["distinct_memories"] for r in results),
        "dedup": sum(r["dedup"] for r in results),
        "skipped_turns": sum(r["skipped_turns"] for r in results),
        "ingest_seconds_sum": round(sum(r["ingest_seconds"] for r in results), 1),
        "store_ms_p50_median": scoring.percentile([r["store_ms_p50"] for r in results], 0.5),
        "unknown_hits": sum(m.get("unknown_hits", 0) for r in results for m in r["modes"].values()),
    }
    built = {"run_dir": str(run_dir), "config": config, "questions_done": len(results),
             "questions_selected": config["selection"]["scored"], "complete": len(results) == config["selection"]["scored"],
             "unresolved_errors": [e for e in errors if e["question_id"] not in done],
             "ingest": ingest, "modes": aggregates}
    return built, _markdown(built)


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def summary_table(built: dict) -> str:
    lines = ["| mode | hit@1 | hit@3 | hit@5 | hit@10 | recall@5 | recall@10 | p50 | p95 | mean context tokens |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for mode, slices in built["modes"].items():
        o = slices["overall"]
        lines.append(f"| {mode} | " + " | ".join(_fmt(o[m]) for m in HEADLINE)
                     + f" | {o['latency_p50_ms']} ms | {o['latency_p95_ms']} ms | {o['context_tokens_mean']:.1f} |")
    return "\n".join(lines)


def _markdown(built: dict) -> str:
    config = built["config"]
    hw, sw, imp = config["hardware"], config["software"], config["imprint"]
    settings = config["imprint_settings"]
    out = [f"# LongMemEval-S retrieval: Imprint ({Path(built['run_dir']).name})", ""]
    if not built["complete"]:
        out += [f"**Incomplete:** {built['questions_done']} of {built['questions_selected']} questions scored.", ""]
    out += [
        "## Provenance", "",
        f"- Imprint: `{imp['commit']}` ({imp['describe']}, branch {imp['branch']}); Imprint code "
        + ("unchanged from that commit" if imp["imprint_code_clean"] else f"**modified**: {imp['dirty_files']}"),
        f"- dataset: `{config['dataset']['name']}`, sha256 `{config['dataset']['sha256']}`",
        f"- questions: {config['selection']['scored']} scored, {config['selection']['abstention_excluded']} abstention "
        f"left out (sample={config['selection']['sample']}, stratify={config['selection']['stratify']}, "
        f"ids={config['selection']['question_ids'] or 'all'})",
        f"- method: ai-memory evaluator at `{config['ai_memory_reference']['commit']}`",
        f"- embeddings: `{settings.get('model.name')}` `{settings.get('model.file')}` (dim {settings.get('model.dim')}, "
        f"{settings.get('model.seq_length')} tokens), llama.cpp {sw['llama_cpp']} on the CPU "
        f"(GPU offload in this build: {sw['llama_gpu_offload']}); Qdrant {sw['qdrant']}",
        f"- hardware: {hw['cpu']}, {hw['logical_cpus']} logical CPUs, {hw['memory_gib']} GiB, {hw['os']}"
        + (" (WSL2)" if hw["wsl"] else "") + f"; load average at start {hw['loadavg_at_start']}",
        f"- harness: {config['settings']['ingest_workers']} ingest servers × {config['settings']['ingest_threads']} "
        f"threads, one query server with Imprint's default threads; searches run while ingestion is paused",
        f"- command: `evals/longmemeval/run.sh {' '.join(config['argv'])}`",
        "",
        "## Results", "",
        summary_table(built), "",
        "Mean context tokens follow ai-memory: characters of each shown hit's text (Imprint's excerpt; Imprint "
        "has no separate title), divided by 4 and rounded up. The whole tool response, provenance lines "
        "included, is in the output table below.", "",
        "### recall-all@k", "",
        "| mode | recall-all@1 | recall-all@3 | recall-all@5 | recall-all@10 |", "|---|---:|---:|---:|---:|",
    ]
    for mode, slices in built["modes"].items():
        o = slices["overall"]
        out.append(f"| {mode} | " + " | ".join(_fmt(o[f"recall_all@{k}"]) for k in scoring.KS) + " |")
    out += ["", "### Output", "",
            "| mode | hits shown (mean) | sessions shown (mean) | response tokens (mean) | context tokens (median) |",
            "|---|---:|---:|---:|---:|"]
    for mode, slices in built["modes"].items():
        o = slices["overall"]
        out.append(f"| {mode} | {o.get('hits_shown_mean', 0):.2f} | {o.get('retrieved_sessions_mean', 0):.2f} | "
                   f"{o.get('response_tokens_mean', 0):.1f} | {o['context_tokens_median']:.1f} |")
    out += ["", "## Slices", ""]
    for mode, slices in built["modes"].items():
        out += [f"### {mode}", "",
                "| slice | n | hit@1 | hit@5 | hit@10 | recall@10 | recall-all@10 | p50 | p95 |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for name, s in slices.items():
            if name == "overall":
                continue
            out.append(f"| {name} | {s['questions']} | {_fmt(s['hit@1'])} | {_fmt(s['hit@5'])} | {_fmt(s['hit@10'])} | "
                       f"{_fmt(s['recall@10'])} | {_fmt(s['recall_all@10'])} | {s['latency_p50_ms']} | {s['latency_p95_ms']} |")
        out.append("")
    ing = built["ingest"]
    out += ["## Ingest", "",
            f"{ing['stored']:,} store calls ({ing['distinct_memories']:,} distinct memories, {ing['dedup']:,} identical "
            f"turns deduplicated by Imprint, {ing['skipped_turns']} turns with an unknown role skipped); "
            f"{ing['ingest_seconds_sum']:,.0f} s of per-question ingest summed over parallel servers; median store "
            f"call {ing['store_ms_p50_median']} ms. Hits whose memory id the harness did not store: {ing['unknown_hits']}.", ""]
    if built["unresolved_errors"]:
        out += ["## Unresolved errors", ""] + [f"- {e['question_id']}: {e['error']}" for e in built["unresolved_errors"]] + [""]
    return "\n".join(out)
