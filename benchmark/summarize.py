#!/usr/bin/env python3
"""Parse benchmark JSON results and emit markdown summary tables.

Usage:
    python3 benchmark/summarize.py benchmark/results/raw
    python3 benchmark/summarize.py benchmark/results/raw --out BENCHMARK_RESULTS.md
    python3 benchmark/summarize.py benchmark/results/raw --llm-quality   # LLM-based quality scoring
"""

import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median


def load_results(results_dir: str) -> dict:
    """Load all JSON result files, grouped by (prompt_id, mode)."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for fname in sorted(os.listdir(results_dir)):
        if not fname.endswith(".json"):
            continue
        # Format: {prompt_id}_{mode}_{run}.json
        parts = fname.rsplit(".", 1)[0].rsplit("_", 2)
        if len(parts) != 3:
            continue
        prompt_id, mode, _run = parts

        fpath = os.path.join(results_dir, fname)
        try:
            with open(fpath) as f:
                data = json.load(f)
            groups[(prompt_id, mode)].append(data)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  WARN: skipping {fname}: {e}", file=sys.stderr)

    return groups


def extract_metrics(data: dict) -> dict:
    """Extract token metrics from a single run's JSON output.

    Uses modelUsage (summed across all models) instead of usage,
    because Claude Code may spawn sub-agents (e.g. Haiku) whose
    tokens only appear in modelUsage, not in the top-level usage.
    """
    inp = out = cache_create = cache_read = cost = 0
    for stats in data.get("modelUsage", {}).values():
        inp += stats.get("inputTokens", 0)
        out += stats.get("outputTokens", 0)
        cache_create += stats.get("cacheCreationInputTokens", 0)
        cache_read += stats.get("cacheReadInputTokens", 0)
        cost += stats.get("costUSD", 0)

    turns = data.get("num_turns", 0)
    duration = data.get("duration_ms", 0)
    total = inp + out + cache_create + cache_read

    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_create": cache_create,
        "cache_read": cache_read,
        "total_tokens": total,
        "cost": cost,
        "turns": turns,
        "duration_ms": duration,
    }


def median_metrics(runs: list[dict]) -> dict:
    """Compute median of each metric across runs."""
    if not runs:
        return {}
    metrics_list = [extract_metrics(r) for r in runs]
    result = {}
    for key in metrics_list[0]:
        values = [m[key] for m in metrics_list]
        result[key] = median(values)
    return result


def load_prompts(prompts_file: str) -> dict:
    """Load prompt metadata keyed by ID."""
    try:
        with open(prompts_file) as f:
            prompts = json.load(f)
        return {p["id"]: p for p in prompts}
    except (OSError, json.JSONDecodeError):
        return {}


def fmt_tokens(n: float) -> str:
    return f"{int(n):,}"


def fmt_cost(n: float) -> str:
    return f"${n:.4f}"


def fmt_pct(n: float) -> str:
    return f"{n:+.1f}%"


def fmt_duration(ms: float) -> str:
    if ms >= 60000:
        return f"{ms / 60000:.1f}m"
    return f"{ms / 1000:.1f}s"


def extract_quality(data: dict) -> dict:
    """Extract response quality signals from a single run's JSON output."""
    result_text = data.get("result", "")
    return {"chars": len(result_text), "result": result_text}


def llm_quality_compare(
    prompt_name: str,
    prompt_text: str,
    off_response: str,
    on_response: str,
    model: str = "haiku",
) -> dict:
    """Call Claude to compare OFF vs ON response quality.

    Returns {"quality_pct": int, "assessment": str}.
    """
    judge_prompt = f"""You are a benchmark judge comparing two AI responses to the same question.

QUESTION: {prompt_text}

--- RESPONSE A (baseline, no memory) ---
{off_response[:6000]}
--- END RESPONSE A ---

--- RESPONSE B (with semantic memory) ---
{on_response[:6000]}
--- END RESPONSE B ---

Compare the quality of Response B relative to Response A. Consider:
- Completeness: does it cover all relevant aspects?
- Accuracy: are the technical details correct?
- Structure: is it well-organized with clear sections?
- Specificity: does it reference concrete code, functions, file paths?
- Conciseness: does it stay focused or overengineer beyond what was asked?

Respond with EXACTLY this JSON format, nothing else:
{{"quality_pct": <integer 50-200 where 100=equal, >100=B is better, <100=A is better>, "assessment": "<one sentence explaining the key difference>"}}"""

    try:
        result = subprocess.run(
            [
                "claude", "-p",
                "--output-format", "json",
                "--model", model,
                "--setting-sources", "",
                "--no-session-persistence",
                judge_prompt,
            ],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return {"quality_pct": 100, "assessment": "LLM judge unavailable."}

        data = json.loads(result.stdout)
        answer = data.get("result", "")

        # Extract JSON from the response (may have markdown wrapping)
        answer = answer.strip()
        if answer.startswith("```"):
            answer = answer.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        parsed = json.loads(answer)
        pct = int(parsed.get("quality_pct", 100))
        pct = max(50, min(200, pct))  # clamp
        assessment = parsed.get("assessment", "")
        return {"quality_pct": pct, "assessment": assessment}

    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, ValueError) as e:
        return {"quality_pct": 100, "assessment": f"LLM judge error: {e}"}


CATEGORY_ORDER = ["information", "decision", "debug", "cross-project", "summary", "creation"]


def _group_by_category(prompt_ids: list[str], prompts_meta: dict) -> dict[str, list[str]]:
    """Group prompt IDs by their category field. Falls back to id-prefix heuristic
    for any id not found in prompts_meta (e.g. stale raw files from removed prompts)."""
    by_cat: dict[str, list[str]] = {}
    for pid in prompt_ids:
        cat = (prompts_meta.get(pid) or {}).get("category")
        if not cat:
            # Legacy fallback: info-* / create-* prefix
            if pid.startswith("info-"):
                cat = "information"
            elif pid.startswith("create-"):
                cat = "creation"
            else:
                cat = pid.split("-", 1)[0] if "-" in pid else "other"
        by_cat.setdefault(cat, []).append(pid)
    return by_cat


def _category_title(cat: str) -> str:
    return {
        "information":   "Information Prompts",
        "decision":      "Decision Recall Prompts",
        "debug":         "Debugging Prompts",
        "cross-project": "Cross-Project Prompts",
        "summary":       "Session Summary Prompts",
        "creation":      "Creation Prompts",
    }.get(cat, cat.replace("-", " ").title() + " Prompts")


def generate_markdown(groups: dict, prompts_meta: dict, use_llm: bool = False) -> str:
    """Generate the full markdown summary."""
    lines = []

    # Collect prompt IDs grouped by category (data-driven — adds new categories automatically)
    prompt_ids = sorted(set(pid for pid, _ in groups.keys()))
    ids_by_cat = _group_by_category(prompt_ids, prompts_meta)
    ordered_cats = [c for c in CATEGORY_ORDER if c in ids_by_cat]
    # Append any unknown categories so nothing is dropped
    for c in ids_by_cat:
        if c not in ordered_cats:
            ordered_cats.append(c)

    def make_table(ids: list[str]) -> list[str]:
        rows = []
        rows.append("| Prompt | OFF tokens | ON tokens | Savings | % | OFF cost | ON cost | Cost Δ |")
        rows.append("|--------|-----------|----------|---------|---|----------|---------|--------|")

        total_off = 0
        total_on = 0
        cost_off = 0.0
        cost_on = 0.0
        count = 0

        for pid in ids:
            off = median_metrics(groups.get((pid, "off"), []))
            on = median_metrics(groups.get((pid, "on"), []))

            if not off or not on:
                continue

            name = prompts_meta.get(pid, {}).get("name", pid)
            off_tok = off["total_tokens"]
            on_tok = on["total_tokens"]
            savings = off_tok - on_tok
            pct = (-savings / off_tok * 100) if off_tok > 0 else 0
            off_cost = off["cost"]
            on_cost = on["cost"]
            cost_delta = on_cost - off_cost

            total_off += off_tok
            total_on += on_tok
            cost_off += off_cost
            cost_on += on_cost
            count += 1

            rows.append(
                f"| {name} "
                f"| {fmt_tokens(off_tok)} "
                f"| {fmt_tokens(on_tok)} "
                f"| {fmt_tokens(savings)} "
                f"| {fmt_pct(pct)} "
                f"| {fmt_cost(off_cost)} "
                f"| {fmt_cost(on_cost)} "
                f"| {fmt_cost(cost_delta)} |"
            )

        if count > 0:
            avg_savings = (total_off - total_on) / total_off * 100 if total_off > 0 else 0
            avg_cost_savings = (cost_off - cost_on) / cost_off * 100 if cost_off > 0 else 0
            rows.append("")
            rows.append(
                f"**Average**: {fmt_pct(-avg_savings)} tokens, "
                f"{fmt_pct(-avg_cost_savings)} cost"
            )

        return rows

    # Detailed per-prompt breakdown (all metrics)
    def make_detail_table(ids: list[str]) -> list[str]:
        rows = []
        rows.append("| Prompt | Mode | Input | Output | Cache Read | Cache Create | Total | Cost | Turns | Duration |")
        rows.append("|--------|------|-------|--------|------------|-------------|-------|------|-------|----------|")

        for pid in ids:
            name = prompts_meta.get(pid, {}).get("name", pid)
            for mode in ["off", "on"]:
                m = median_metrics(groups.get((pid, mode), []))
                if not m:
                    continue
                label = "OFF" if mode == "off" else "ON"
                rows.append(
                    f"| {name} | {label} "
                    f"| {fmt_tokens(m['input_tokens'])} "
                    f"| {fmt_tokens(m['output_tokens'])} "
                    f"| {fmt_tokens(m['cache_read'])} "
                    f"| {fmt_tokens(m['cache_create'])} "
                    f"| {fmt_tokens(m['total_tokens'])} "
                    f"| {fmt_cost(m['cost'])} "
                    f"| {int(m['turns'])} "
                    f"| {fmt_duration(m['duration_ms'])} |"
                )

        return rows

    # Per-model breakdown — shows where tokens go (Sonnet vs Haiku sub-agents)
    def make_model_table(ids: list[str]) -> list[str]:
        rows = []
        rows.append("| Prompt | Mode | Model | Tokens | Cost |")
        rows.append("|--------|------|-------|--------|------|")

        for pid in ids:
            name = prompts_meta.get(pid, {}).get("name", pid)
            for mode in ["off", "on"]:
                runs = groups.get((pid, mode), [])
                if not runs:
                    continue
                # Use first run for model breakdown (median doesn't apply per-model)
                label = "OFF" if mode == "off" else "ON"
                model_totals: dict[str, dict] = {}
                for run in runs:
                    for model, stats in run.get("modelUsage", {}).items():
                        short = model.split("-")[1] if "-" in model else model
                        if short not in model_totals:
                            model_totals[short] = {"tokens": [], "cost": []}
                        tok = (stats.get("inputTokens", 0) + stats.get("outputTokens", 0)
                               + stats.get("cacheReadInputTokens", 0)
                               + stats.get("cacheCreationInputTokens", 0))
                        model_totals[short]["tokens"].append(tok)
                        model_totals[short]["cost"].append(stats.get("costUSD", 0))

                for model_short, vals in sorted(model_totals.items()):
                    med_tok = median(vals["tokens"])
                    med_cost = median(vals["cost"])
                    rows.append(
                        f"| {name} | {label} | {model_short} "
                        f"| {fmt_tokens(med_tok)} "
                        f"| {fmt_cost(med_cost)} |"
                    )

        return rows

    # Build output — one table per category, in canonical order
    for cat in ordered_cats:
        ids = ids_by_cat[cat]
        if not ids:
            continue
        lines.append(f"### {_category_title(cat)}")
        lines.append("")
        lines.extend(make_table(ids))
        lines.append("")

    # Overall summary (flatten all categories)
    all_ids = [pid for cat in ordered_cats for pid in ids_by_cat[cat]]
    if all_ids:
        total_off = 0
        total_on = 0
        cost_off = 0.0
        cost_on = 0.0
        for pid in all_ids:
            off = median_metrics(groups.get((pid, "off"), []))
            on = median_metrics(groups.get((pid, "on"), []))
            if off and on:
                total_off += off["total_tokens"]
                total_on += on["total_tokens"]
                cost_off += off["cost"]
                cost_on += on["cost"]

        if total_off > 0:
            tok_pct = (total_off - total_on) / total_off * 100
            cost_pct = (cost_off - cost_on) / cost_off * 100 if cost_off > 0 else 0
            lines.append("### Overall")
            lines.append("")
            lines.append(f"- **Token savings**: {fmt_pct(-tok_pct)} ({fmt_tokens(total_off)} → {fmt_tokens(total_on)})")
            lines.append(f"- **Cost savings**: {fmt_pct(-cost_pct)} ({fmt_cost(cost_off)} → {fmt_cost(cost_on)})")
            lines.append("")

    # Detailed breakdown
    if all_ids:
        lines.append("### Detailed Breakdown")
        lines.append("")
        lines.extend(make_detail_table(all_ids))
        lines.append("")

    # Per-model breakdown
    if all_ids:
        lines.append("### Per-Model Token Distribution")
        lines.append("")
        lines.append("Without Imprint, Claude Code often delegates file reading to cheaper Haiku sub-agents.")
        lines.append("With Imprint, the primary model answers directly from semantic search — fewer total tokens but all on the primary model.")
        lines.append("")
        lines.extend(make_model_table(all_ids))
        lines.append("")

    # ── Quality comparison (LLM-as-judge or placeholder) ──
    # Cache verdicts so we don't call the LLM twice per prompt
    quality_cache: dict[str, dict] = {}  # pid -> {"quality_pct": int, "assessment": str}

    def _judge(pid: str) -> dict:
        if pid in quality_cache:
            return quality_cache[pid]

        off_runs = groups.get((pid, "off"), [])
        on_runs = groups.get((pid, "on"), [])
        if not off_runs or not on_runs:
            return {"quality_pct": 100, "assessment": "No data."}

        off_mid = sorted(off_runs, key=lambda r: r.get("usage", {}).get("output_tokens", 0))[len(off_runs) // 2]
        on_mid = sorted(on_runs, key=lambda r: r.get("usage", {}).get("output_tokens", 0))[len(on_runs) // 2]

        if use_llm:
            name = prompts_meta.get(pid, {}).get("name", pid)
            prompt_text = prompts_meta.get(pid, {}).get("prompt", "")
            print(f"  Judging {pid}...", file=sys.stderr)
            verdict = llm_quality_compare(
                name, prompt_text,
                off_mid.get("result", ""), on_mid.get("result", ""),
            )
        else:
            verdict = {"quality_pct": 100, "assessment": "Run with --llm-quality for AI-judged assessment"}

        quality_cache[pid] = verdict
        return verdict

    def make_quality_section(ids: list[str], title: str) -> list[str]:
        rows = []
        rows.append(f"#### {title}")
        rows.append("")
        rows.append("| Prompt | OFF chars | ON chars | ON/OFF | Quality assessment | ON quality % |")
        rows.append("|--------|----------|---------|--------|-------------------|-------------|")

        quality_pcts: list[int] = []

        for pid in ids:
            off_runs = groups.get((pid, "off"), [])
            on_runs = groups.get((pid, "on"), [])
            if not off_runs or not on_runs:
                continue

            name = prompts_meta.get(pid, {}).get("name", pid)

            off_mid = sorted(off_runs, key=lambda r: r.get("usage", {}).get("output_tokens", 0))[len(off_runs) // 2]
            on_mid = sorted(on_runs, key=lambda r: r.get("usage", {}).get("output_tokens", 0))[len(on_runs) // 2]
            off_chars = len(off_mid.get("result", ""))
            on_chars = len(on_mid.get("result", ""))
            ratio = on_chars / max(off_chars, 1)

            verdict = _judge(pid)
            quality_pcts.append(verdict["quality_pct"])

            rows.append(
                f"| {name} "
                f"| {off_chars:,} "
                f"| {on_chars:,} "
                f"| {ratio:.1f}x "
                f"| {verdict['assessment']} "
                f"| **{verdict['quality_pct']}%** |"
            )

        if quality_pcts and use_llm:
            avg_q = sum(quality_pcts) / len(quality_pcts)
            rows.append("")
            rows.append(f"**Average ON quality: {avg_q:.0f}%** of OFF baseline")

        return rows

    if all_ids:
        lines.append("### Response Quality Comparison")
        lines.append("")
        if use_llm:
            lines.append("Quality judged by Claude (LLM-as-judge). Each response pair is evaluated on:")
            lines.append("completeness, accuracy, structure, specificity, and conciseness.")
        else:
            lines.append("_Quality assessment requires `--llm-quality` flag (uses Claude as judge)._")
        lines.append("100% = equal quality, >100% = ON is better, <100% = OFF is better.")
        lines.append("")

    for cat in ordered_cats:
        ids = ids_by_cat[cat]
        if not ids:
            continue
        lines.extend(make_quality_section(ids, _category_title(cat)))
        lines.append("")

    # Quality summary — per-category averages
    if all_ids and use_llm:
        lines.append("#### Key Observations")
        lines.append("")
        for cat in ordered_cats:
            ids = ids_by_cat[cat]
            qs = [_judge(p)["quality_pct"] for p in ids if (p, "off") in groups and (p, "on") in groups]
            if qs:
                lines.append(f"- **{_category_title(cat)}**: avg {sum(qs)/len(qs):.0f}% quality "
                             f"across {len(qs)} prompts")
        lines.append("- Without Imprint, Claude reads 1-2 files and answers from that narrower view. "
                     "Imprint search pulls relevant chunks from many files in a single call.")
        lines.append("")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2 or "--help" in sys.argv or "-h" in sys.argv:
        print(f"Usage: {sys.argv[0]} <results_dir> [--out FILE] [--llm-quality]", file=sys.stderr)
        print("", file=sys.stderr)
        print("Options:", file=sys.stderr)
        print("  --out FILE       Write markdown to file instead of stdout", file=sys.stderr)
        print("  --llm-quality    Use Claude (haiku) as LLM judge for quality comparison", file=sys.stderr)
        if "--help" in sys.argv or "-h" in sys.argv:
            sys.exit(0)
        sys.exit(1)

    results_dir = sys.argv[1]
    out_file = None
    use_llm = "--llm-quality" in sys.argv

    if "--out" in sys.argv:
        idx = sys.argv.index("--out")
        if idx + 1 < len(sys.argv):
            out_file = sys.argv[idx + 1]

    # Find prompts.json relative to this script
    script_dir = Path(__file__).parent
    prompts_file = script_dir / "prompts.json"
    prompts_meta = load_prompts(str(prompts_file))

    groups = load_results(results_dir)
    if not groups:
        print("No results found.", file=sys.stderr)
        sys.exit(1)

    if use_llm:
        unique_pids = len({pid for pid, _ in groups.keys()})
        print(f"▸ Running LLM quality judgments ({unique_pids} prompts × 1 Haiku call each)...", file=sys.stderr)

    md = generate_markdown(groups, prompts_meta, use_llm=use_llm)

    if out_file:
        with open(out_file, "w") as f:
            f.write(md)
        print(f"Written to {out_file}")
    else:
        print(md)


if __name__ == "__main__":
    main()
