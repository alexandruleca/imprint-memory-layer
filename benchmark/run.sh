#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Imprint Token Savings Benchmark Runner
#
# Runs identical prompts with Imprint OFF (baseline) vs ON,
# captures token usage via claude CLI JSON output.
#
# Usage:
#   bash benchmark/run.sh                  # default: 3 runs, sonnet
#   bash benchmark/run.sh --runs 1         # quick smoke test
#   bash benchmark/run.sh --model opus     # override model
#   bash benchmark/run.sh --prompt info-1  # run single prompt
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results/raw"
MCP_CONFIG="$SCRIPT_DIR/mcp_config.json"
PROMPTS_FILE="$SCRIPT_DIR/prompts.json"
CLAUDE_MD="$REPO_DIR/CLAUDE.md"
SUMMARIZE="$SCRIPT_DIR/summarize.py"

# Defaults
RUNS=3
MODEL="sonnet"
SINGLE_PROMPT=""
LLM_QUALITY=""

# ── Parse args ──
while [[ $# -gt 0 ]]; do
  case "$1" in
    --runs)          RUNS="$2";          shift 2 ;;
    --model)         MODEL="$2";         shift 2 ;;
    --prompt)        SINGLE_PROMPT="$2"; shift 2 ;;
    --llm-quality)   LLM_QUALITY="--llm-quality"; shift ;;
    --help|-h)
      echo "Usage: bash benchmark/run.sh [--runs N] [--model MODEL] [--prompt ID] [--llm-quality]"
      echo ""
      echo "Options:"
      echo "  --runs N        Number of runs per prompt per mode (default: 3)"
      echo "  --model MODEL   Claude model to use (default: sonnet)"
      echo "  --prompt ID     Run only this prompt ID (e.g. info-1, create-2)"
      echo "  --llm-quality   Use Claude (haiku) to judge response quality ON vs OFF"
      exit 0 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ── Preflight ──
command -v claude >/dev/null 2>&1 || { echo "ERROR: claude CLI not found"; exit 1; }
[[ -f "$PROMPTS_FILE" ]]          || { echo "ERROR: $PROMPTS_FILE not found"; exit 1; }
[[ -f "$MCP_CONFIG" ]]            || { echo "ERROR: $MCP_CONFIG not found"; exit 1; }
[[ -f "$CLAUDE_MD" ]]             || { echo "ERROR: $CLAUDE_MD not found"; exit 1; }

# Ensure Qdrant is running for ON-mode tests
echo "▸ Ensuring Qdrant is running..."
(cd "$REPO_DIR" && imprint server start 2>/dev/null) || echo "  (Qdrant already running or start skipped)"

mkdir -p "$RESULTS_DIR"

# ── Load prompts ──
PROMPT_COUNT=$(python3 -c "import json; print(len(json.load(open('$PROMPTS_FILE'))))")
echo ""
echo "════════════════════════════════════════════════════════"
echo "  Imprint Token Savings Benchmark"
echo "  Model: $MODEL | Runs: $RUNS | Prompts: $PROMPT_COUNT"
echo "════════════════════════════════════════════════════════"
echo ""

# ── Helper: extract token summary from JSON ──
extract_summary() {
  local json_file="$1"
  python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
# Sum across ALL models (modelUsage) — not just primary model (usage)
# Claude Code may spawn sub-agents (Haiku) that consume tokens too
inp = out = cr = cc = cost = 0
for stats in d.get('modelUsage', {}).values():
    inp += stats.get('inputTokens', 0)
    out += stats.get('outputTokens', 0)
    cc += stats.get('cacheCreationInputTokens', 0)
    cr += stats.get('cacheReadInputTokens', 0)
    cost += stats.get('costUSD', 0)
print(inp, out, cc, cr, cost, d.get('num_turns', 0), d.get('duration_ms', 0))
" "$json_file"
}

# ── Helper: run one benchmark ──
run_one() {
  local prompt_id="$1"
  local prompt_text="$2"
  local mode="$3"  # "off" or "on"
  local run_num="$4"
  local outfile="$RESULTS_DIR/${prompt_id}_${mode}_${run_num}.json"

  if [[ "$mode" == "off" ]]; then
    # Mode A: Imprint OFF — no MCP, no hooks, no CLAUDE.md
    claude -p \
      --output-format json \
      --model "$MODEL" \
      --setting-sources "" \
      --no-session-persistence \
      "$prompt_text" \
      > "$outfile" 2>/dev/null
  else
    # Mode B: Imprint ON — explicit MCP + CLAUDE.md system prompt
    claude -p \
      --output-format json \
      --model "$MODEL" \
      --setting-sources "" \
      --no-session-persistence \
      --mcp-config "$MCP_CONFIG" \
      --system-prompt "$(cat "$CLAUDE_MD")" \
      "$prompt_text" \
      > "$outfile" 2>/dev/null
  fi

  echo "$outfile"
}

# ── Main loop ──
TOTAL_RUNS=0
FAILED=0

for i in $(seq 0 $((PROMPT_COUNT - 1))); do
  PROMPT_ID=$(python3 -c "import json; print(json.load(open('$PROMPTS_FILE'))[$i]['id'])")
  PROMPT_TEXT=$(python3 -c "import json; print(json.load(open('$PROMPTS_FILE'))[$i]['prompt'])")
  PROMPT_NAME=$(python3 -c "import json; print(json.load(open('$PROMPTS_FILE'))[$i]['name'])")
  PROMPT_CAT=$(python3 -c "import json; print(json.load(open('$PROMPTS_FILE'))[$i]['category'])")

  # Skip if --prompt filter set and doesn't match
  if [[ -n "$SINGLE_PROMPT" && "$PROMPT_ID" != "$SINGLE_PROMPT" ]]; then
    continue
  fi

  echo "──────────────────────────────────────────────────────"
  echo "  [$PROMPT_ID] $PROMPT_NAME ($PROMPT_CAT)"
  echo "──────────────────────────────────────────────────────"

  for mode in off on; do
    MODE_LABEL=$([ "$mode" = "off" ] && echo "OFF" || echo "ON ")
    for run in $(seq 1 "$RUNS"); do
      printf "  %s run %d/%d ... " "$MODE_LABEL" "$run" "$RUNS"

      outfile=$(run_one "$PROMPT_ID" "$PROMPT_TEXT" "$mode" "$run") && {
        # Extract summary
        read -r inp out cache_create cache_read cost turns dur <<< "$(extract_summary "$outfile")"
        total=$((inp + out + cache_create + cache_read))
        printf "tokens=%d (in=%d out=%d cache_r=%d cache_c=%d) cost=\$%.4f turns=%s\n" \
          "$total" "$inp" "$out" "$cache_read" "$cache_create" "$cost" "$turns"
        TOTAL_RUNS=$((TOTAL_RUNS + 1))
      } || {
        echo "FAILED"
        FAILED=$((FAILED + 1))
      }

      # Clean up any files created by creation prompts
      for f in test_qdrant_connectivity.py count_memories_by_project.py benchmark_embed_speed.sh; do
        [[ -f "$REPO_DIR/$f" ]] && rm -f "$REPO_DIR/$f"
      done
    done
  done
  echo ""
done

# ── Summarize ──
echo "════════════════════════════════════════════════════════"
echo "  Runs completed: $TOTAL_RUNS | Failed: $FAILED"
echo "════════════════════════════════════════════════════════"
echo ""

if [[ -f "$SUMMARIZE" && $TOTAL_RUNS -gt 0 ]]; then
  echo "▸ Generating summary..."
  python3 "$SUMMARIZE" "$RESULTS_DIR" $LLM_QUALITY
  echo ""
  echo "▸ Results in: $RESULTS_DIR"
  echo "▸ Run 'python3 $SUMMARIZE $RESULTS_DIR' to regenerate summary"
  echo "▸ Run 'python3 $SUMMARIZE $RESULTS_DIR --llm-quality' for AI quality judgments"
fi
