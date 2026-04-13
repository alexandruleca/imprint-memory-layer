package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/hunter/knowledge/internal/output"
	"github.com/hunter/knowledge/internal/platform"
	"github.com/hunter/knowledge/internal/runner"
)

// Status prints a one-screen health view of the whole Knowledge system:
// is the MCP registered, are the hooks wired, is Qdrant up, how many
// memories are stored. The top-level verdict is enabled vs disabled —
// "enabled" means MCP + at least one hook are present.
func Status(args []string) {
	_ = args
	fmt.Println()
	output.Header("═══ Knowledge Status ═══")
	fmt.Println()

	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	// ── MCP registration ──────────────────────────────────────
	mcpRegistered := false
	if _, ok := runner.Exists("claude"); ok {
		mcpOut, err := runner.RunCapture("claude", "mcp", "list")
		if err == nil && strings.Contains(mcpOut, "knowledge") {
			mcpRegistered = true
		}
	}

	// ── Hooks ────────────────────────────────────────────────
	hooksCount := countKnowledgeHooks(platform.ClaudeSettingsPath(), dataDir)

	// ── Qdrant server ────────────────────────────────────────
	serverStatus := map[string]any{}
	memTotal := 0
	byProject := map[string]int{}
	if platform.FileExists(venvPython) {
		out, err := runner.RunCaptureEnv(venvPython,
			[]string{"PYTHONPATH=" + projectDir, "KNOWLEDGE_DATA_DIR=" + dataDir},
			"-c", `import json
from knowledgebase import qdrant_runner as q
s = q.status()
mem_total = 0
by_project = {}
if s.get('running'):
    try:
        from knowledgebase import vectorstore as vs
        st = vs.status()
        mem_total = st.get('total_memories', 0)
        by_project = st.get('by_project', {})
    except Exception:
        pass
print(json.dumps({**s, 'mem_total': mem_total, 'by_project': by_project}))`)
		if err == nil {
			_ = json.Unmarshal([]byte(out), &serverStatus)
			if v, ok := serverStatus["mem_total"].(float64); ok {
				memTotal = int(v)
			}
			if bp, ok := serverStatus["by_project"].(map[string]any); ok {
				for k, v := range bp {
					if c, ok := v.(float64); ok {
						byProject[k] = int(c)
					}
				}
			}
		}
	}

	// ── Verdict ──────────────────────────────────────────────
	enabled := mcpRegistered && hooksCount > 0
	if enabled {
		output.Success("ENABLED")
	} else {
		output.Warn("DISABLED")
	}
	fmt.Println()

	// ── Components ───────────────────────────────────────────
	check := func(ok bool) string {
		if ok {
			return "\033[0;32m✓\033[0m"
		}
		return "\033[0;31m✗\033[0m"
	}

	fmt.Printf("  %s MCP server registered (Claude Code)\n", check(mcpRegistered))
	fmt.Printf("  %s Hooks installed (%d entries)\n", check(hooksCount > 0), hooksCount)

	running, _ := serverStatus["running"].(bool)
	pid, _ := serverStatus["pid"].(float64)
	host, _ := serverStatus["host"].(string)
	port, _ := serverStatus["port"].(float64)
	if running {
		fmt.Printf("  %s Qdrant server  http://%s:%d  (pid %d)\n", check(true), host, int(port), int(pid))
	} else {
		fmt.Printf("  %s Qdrant server  (not running — auto-spawns on next call)\n", check(false))
	}

	venvOk := platform.FileExists(venvPython)
	fmt.Printf("  %s Python venv    %s\n", check(venvOk), venvPython)
	fmt.Printf("  %s Data dir       %s\n", check(platform.DirExists(dataDir)), dataDir)
	fmt.Println()

	// ── Memory stats ─────────────────────────────────────────
	if memTotal > 0 {
		fmt.Printf("  Memories: %d  across %d projects\n", memTotal, len(byProject))
		printed := 0
		for _, name := range topProjects(byProject, 10) {
			fmt.Printf("    %s (%d)\n", name, byProject[name])
			printed++
		}
		if len(byProject) > printed {
			fmt.Printf("    ... %d more\n", len(byProject)-printed)
		}
	} else if running {
		fmt.Println("  Memories: 0 — run `knowledge ingest <dir>` to populate")
	}
	fmt.Println()

	if !enabled {
		fmt.Println("  Run `knowledge enable` to re-wire MCP + hooks.")
		fmt.Println()
	}

	_ = os.Stdout
}

// countKnowledgeHooks walks settings.json and counts hook entries whose
// command string mentions any of our shell-out modules / sentinels.
func countKnowledgeHooks(settingsPath, dataDir string) int {
	raw, err := os.ReadFile(settingsPath)
	if err != nil {
		return 0
	}
	var parsed map[string]any
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return 0
	}
	hooks, ok := parsed["hooks"].(map[string]any)
	if !ok {
		return 0
	}
	needles := []string{
		"knowledgebase.cli_conversations",
		"knowledgebase.cli_extract",
		"knowledgebase.cli_index",
		"COMPACTION IMMINENT",
		"Knowledge MCP available",
		"Knowledge MCP gate",
		dataDir + "/.sessions",
	}
	count := 0
	for _, raw := range hooks {
		groups, ok := raw.([]any)
		if !ok {
			continue
		}
		for _, g := range groups {
			grp, ok := g.(map[string]any)
			if !ok {
				continue
			}
			entries, _ := grp["hooks"].([]any)
			for _, h := range entries {
				hm, ok := h.(map[string]any)
				if !ok {
					continue
				}
				cmd, _ := hm["command"].(string)
				for _, n := range needles {
					if strings.Contains(cmd, n) {
						count++
						break
					}
				}
			}
		}
	}
	return count
}

func topProjects(by map[string]int, limit int) []string {
	type kv struct {
		k string
		v int
	}
	pairs := make([]kv, 0, len(by))
	for k, v := range by {
		pairs = append(pairs, kv{k, v})
	}
	// simple selection sort — small N
	for i := 0; i < len(pairs) && i < limit; i++ {
		max := i
		for j := i + 1; j < len(pairs); j++ {
			if pairs[j].v > pairs[max].v {
				max = j
			}
		}
		pairs[i], pairs[max] = pairs[max], pairs[i]
	}
	if len(pairs) > limit {
		pairs = pairs[:limit]
	}
	out := make([]string, len(pairs))
	for i, p := range pairs {
		out[i] = p.k
	}
	return out
}
