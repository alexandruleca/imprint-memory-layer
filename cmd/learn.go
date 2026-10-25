package cmd

import (
	"fmt"
	"os"
	"slices"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
)

// Learn indexes the conversations of every client store Imprint is
// integrated with: this computer's Claude Code transcripts and auto-memory
// files, Codex sessions, Cursor agent conversations and Cline sessions, and
// those of each profile registered for WSL (ADR 0037, ADR 0052, ADR 0056).
// The clients table names the stores (client.store, client.wslStore). With --desktop it also scans the user's Downloads folder(s) for
// Claude Desktop / ChatGPT Desktop export zips and ingests them. Separated from ingest so project file indexing doesn't
// re-process sessions.
//
//	imprint learn              # coding-client conversations, auto-memory files
//	imprint learn --desktop    # also index new desktop-app export zips
//	imprint learn --watch      # poll-loop the desktop scanner (implies --desktop)
//	imprint learn --path DIR   # add an extra scan root (repeatable)
func Learn(args []string) {
	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)
	pinWorkspace(dataDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Python venv not found — run 'imprint setup' first")
	}

	lock := acquireOrEnqueue(dataDir, "learn", args)
	defer lock.Release()

	// Learn reads the clients' own stores, so it needs no approved root. Each
	// conversation's recorded cwd decides whether it belongs to this workspace
	// (workspace_policy.transcript_allowed, ADR 0034).
	envVars := []string{
		"PYTHONPATH=" + projectDir,
		"IMPRINT_DATA_DIR=" + dataDir,
	}

	desktop := false
	watch := false
	var passThrough []string
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--desktop":
			desktop = true
		case "--watch":
			desktop = true
			watch = true
			passThrough = append(passThrough, "--watch")
		case "--interval":
			if i+1 < len(args) {
				passThrough = append(passThrough, "--interval", args[i+1])
				i++
			}
		case "--path":
			if i+1 < len(args) {
				passThrough = append(passThrough, "--path", args[i+1])
				i++
			}
		}
	}

	fmt.Println()
	output.Header("═══ Imprint Learn ═══")
	fmt.Println()

	// Always learn the client stores. --desktop is additive: on top of them,
	// also scan Downloads for Claude Desktop / ChatGPT Desktop export zips.
	groups, notLearned := learnStores(projectDir)
	steps := len(groups)
	if desktop {
		steps++
	}

	for i, g := range groups {
		output.Info(fmt.Sprintf("Step %d/%d: Learning conversations on %s...", i+1, steps, g.label))
		cmd := runner.CommandWithEnv(venvPython, g.learnArgs(), append(envVars, g.pathContext()...)...)
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		cmd.Stdin = os.Stdin
		if err := cmd.Run(); err != nil {
			notLearned = append(notLearned, fmt.Sprintf("%s: %v", g.label, err))
		}
		fmt.Println()
	}

	if desktop {
		label := fmt.Sprintf("Step %d/%d: Scanning Downloads for Claude Desktop / ChatGPT Desktop exports", steps, steps)
		if watch {
			label = fmt.Sprintf("Step %d/%d: Watching Downloads for Claude Desktop / ChatGPT Desktop exports", steps, steps)
		}
		output.Info(label)
		cmdArgs := append([]string{"-m", "imprint.cli_desktop_learn"}, passThrough...)
		cmd := runner.CommandWithEnv(venvPython, cmdArgs, envVars...)
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		cmd.Stdin = os.Stdin
		if err := cmd.Run(); err != nil {
			output.Fail("Desktop learning failed: " + err.Error())
		}
		fmt.Println()
	}

	fmt.Println()
	output.Header("═══ Learn Complete ═══")
	// A store that cannot be reached does not stop the others; say which
	// conversations this run did not learn.
	for _, problem := range notLearned {
		output.Warn("Not learned: " + problem)
	}

	runPython(venvPython, envVars, `
from imprint import vectorstore as vs
s = vs.status()
print(f"  Total memories: {s['total_memories']}")
for p, c in sorted(s['by_project'].items(), key=lambda x: -x[1]):
    print(f"    {p}: {c}")
`)
	fmt.Println()
}

// conversationStores are client stores that share one path context.
type conversationStores struct {
	label        string
	distribution string      // WSL distribution; empty for this computer
	user         string      // registered Linux user; empty for the default
	mounts       string      // its Windows drive mounts (wslDriveMounts)
	stores       [][2]string // client name, store directory
}

func (g conversationStores) learnArgs() []string {
	args := []string{"-m", "imprint.cli_conversations"}
	for _, store := range g.stores {
		args = append(args, "--store", store[0], store[1])
	}
	return args
}

// pathContext is the context the WSL bridge gives a distribution's hooks: a
// WSL profile's directories and recorded cwds are Linux paths, translated by
// imprint.wsl_paths exactly as for that distribution's Stop hook (ADR 0026).
// This computer's stores state an empty context, so a learn started through
// the bridge does not apply its caller's distribution to them.
func (g conversationStores) pathContext() []string {
	return []string{"IMPRINT_WSL_DISTRO=" + g.distribution, "IMPRINT_WSL_MOUNTS=" + g.mounts}
}

// learnStores lists the client stores Imprint is integrated with: this
// computer's store of each client whose conversations are learned
// (client.store), then each profile registered for WSL with the store in the
// registered target's recorded directory (client.wslStore). Reading a
// distribution's mount table may start it, as WSL discovery does.
// Registrations that cannot be learned are returned as problems instead of
// stopping the others.
func learnStores(project string) ([]conversationStores, []string) {
	local := conversationStores{label: "this computer"}
	for _, c := range clients {
		if c.store != nil {
			local.stores = append(local.stores, [2]string{c.name, c.store()})
		}
	}
	groups := []conversationStores{local}
	records, err := readWSLRegistrations(project)
	if err != nil {
		return groups, []string{"WSL profiles: " + err.Error()}
	}
	var problems []string
	wsl := map[[2]string]int{}
	for _, r := range records {
		dir, err := wslStoreDir(r.Profile, r.Target)
		if err != nil || !validDistro(r.Profile.Distribution) || !validLinuxPath(dir) {
			problems = append(problems, fmt.Sprintf("WSL %s %s: invalid saved WSL profile", r.Profile.Distribution, r.Target))
			continue
		}
		key := [2]string{r.Profile.Distribution, r.User}
		i, ok := wsl[key]
		if !ok {
			label := "WSL " + r.Profile.Distribution
			if r.User != "" {
				label += " (" + r.User + ")"
			}
			i = len(groups)
			wsl[key] = i
			groups = append(groups, conversationStores{label: label, distribution: r.Profile.Distribution, user: r.User})
		}
		if store := [2]string{r.Target, dir}; !slices.Contains(groups[i].stores, store) {
			groups[i].stores = append(groups[i].stores, store)
		}
	}
	reachable := []conversationStores{groups[0]}
	for _, g := range groups[1:] {
		mounts, err := runWSL(wslArgs(g.distribution, g.user, "sh", "-c", wslDriveMounts)...)
		if err != nil {
			problems = append(problems, fmt.Sprintf("%s is unavailable (%v)", g.label, err))
			continue
		}
		g.mounts = mounts
		reachable = append(reachable, g)
	}
	return reachable, problems
}
