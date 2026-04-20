package main

import (
	"fmt"
	"os"
	"strings"

	"github.com/hunter/imprint/cmd"
)

var version = "dev"

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}

	switch os.Args[1] {
	case "setup":
		// `imprint setup`              → all (try every detected host)
		// `imprint setup <target>`     → dispatch to a single handler
		// `imprint setup all`          → run every handler; each self-skips
		//                                  if its host tool isn't installed.
		// Flags:
		//   --retry-gpu           clear sticky GPU failure cache
		//   --profile <cpu|gpu|auto>   force install profile (persists)
		//   --with-llm            install llama-cpp-python local tagger
		//   --no-llm              skip llama-cpp-python
		//   --non-interactive     never prompt; fail fast on ambiguity
		target := "all"
		rest := os.Args[2:]
		for i := 0; i < len(rest); i++ {
			a := rest[i]
			switch {
			case a == "--retry-gpu":
				cmd.SetRetryGPU(true)
			case a == "--with-llm":
				cmd.SetWithLLM(true)
			case a == "--no-llm":
				cmd.SetWithLLM(false)
			case a == "--non-interactive":
				cmd.SetNonInteractive(true)
			case a == "--profile" && i+1 < len(rest):
				cmd.SetInstallProfile(rest[i+1])
				i++
			case strings.HasPrefix(a, "--profile="):
				cmd.SetInstallProfile(strings.TrimPrefix(a, "--profile="))
			default:
				target = a
			}
		}
		fmt.Fprintf(os.Stderr, "\n→ imprint setup target: %s\n\n", target)
		if !cmd.DispatchSetup(target) {
			fmt.Fprintf(os.Stderr, "unknown setup target %q (expected: claude-code | claude-desktop | chatgpt-desktop | cursor | codex | copilot | cline | openclaw | all)\n", target)
			os.Exit(1)
		}
	case "bootstrap":
		cmd.Bootstrap(os.Args[2:])
	case "profile":
		cmd.Profile(os.Args[2:])
	case "ingest":
		cmd.Ingest(os.Args[2:])
	case "learn":
		cmd.Learn(os.Args[2:])
	case "ingest-url":
		cmd.IngestURL(os.Args[2:])
	case "refresh":
		cmd.Refresh(os.Args[2:])
	case "refresh-urls":
		cmd.RefreshURLs(os.Args[2:])
	case "sync":
		cmd.Sync(os.Args[2:])
	case "relay":
		cmd.Relay(os.Args[2:])
	case "ui":
		cmd.UI(os.Args[2:])
	case "retag":
		cmd.Retag(os.Args[2:])
	case "migrate":
		cmd.Migrate(os.Args[2:])
	case "server":
		cmd.Server(os.Args[2:])
	case "enable":
		cmd.Enable(os.Args[2:])
	case "disable":
		cmd.Disable(os.Args[2:])
	case "uninstall":
		cmd.Uninstall(os.Args[2:])
	case "status":
		cmd.Status(os.Args[2:])
	case "config":
		cmd.Config(os.Args[2:])
	case "workspace":
		cmd.Workspace(os.Args[2:])
	case "wipe":
		cmd.Wipe(os.Args[2:])
	case "update":
		cmd.Update(os.Args[2:], version)
	case "version", "--version":
		fmt.Printf("imprint %s\n", version)
	default:
		printUsage()
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Fprintf(os.Stderr, `imprint — AI memory for Claude Code

Usage:
  imprint setup [target] [--profile cpu|gpu|auto] [--with-llm|--no-llm] [--retry-gpu] [--non-interactive]
                             Install deps, register MCP server, configure host AI tool
                               target: claude-code (default) | claude-desktop | chatgpt-desktop | cursor | codex | copilot | cline | openclaw | all
  imprint bootstrap [--profile cpu|gpu|auto] [--with-llm] [--non-interactive]
                             Provision venv + selected dependencies only (no MCP registration).
                             Intended for installer scripts; end users should run 'imprint setup'.
  imprint profile            Show active install profile (cpu|gpu + with-llm flag)
  imprint profile set <cpu|gpu|auto>   Swap profile; reinstalls deps via uv
  imprint profile add-llm    Install llama-cpp-python into active profile
  imprint profile drop-llm   Uninstall llama-cpp-python
  imprint ingest <path>      Index project source files (directory or single file)
  imprint learn              Index Claude Code conversations + memory files
  imprint ingest-url <url>   Fetch URL(s), extract content, and index (html/pdf/etc)
  imprint refresh <dir>      Re-index only files that changed since last index
  imprint refresh-urls       Re-check stored URLs (ETag/Last-Modified) and re-index changed
  imprint sync serve --relay <host>  Expose KB for syncing via relay
  imprint sync <host>/<id>   Pull + push to a remote peer
  imprint sync export         Export snapshot bundle (no re-embed on import)
  imprint sync import <dir>   Import snapshot bundle from another device
  imprint relay              Run the sync relay server
  imprint ui [--port N]      Dashboard UI foreground (FastAPI + Next.js; Ctrl+C to stop)
  imprint ui start [--port N]  Start the UI server detached in the background
  imprint ui stop            Stop the background UI server
  imprint ui status          Show UI server pid + reachability
  imprint ui open [--port N]  Ensure server is running, then open a browser window
  imprint ui restart         Stop + start the background UI server
  imprint ui log             Print the UI log file path (for tail -f)
  imprint retag [--project] [--all]  Re-tag existing memories (--all re-runs even already-tagged chunks)
  imprint migrate --from WS1 --to WS2 --project NAME | --topic TAG [--dry-run]
                             Move memories between workspaces (preserves vectors)
  imprint server <cmd>       Manage the local Qdrant server
                               cmd: start | stop | status | log
  imprint config             Show all settings and current values
  imprint config set <k> <v> Persist a setting (e.g. model.name, qdrant.port)
  imprint config get <key>   Show one setting
  imprint config reset <key> Remove override, revert to default
  imprint status             Show enabled/disabled state, server pid, hook count, memory stats
  imprint disable            Stop server, unregister MCP, strip hooks (data preserved)
  imprint enable [target]    Re-wire MCP + hooks + start server (target: claude-code | cursor | codex | copilot | cline | all)
  imprint uninstall [-y] [--keep-data]
                             Full removal: disable + strip CLAUDE.md block + drop alias/symlink + delete venv/data/install dir
  imprint workspace          List workspaces and show active
  imprint workspace switch <n>  Switch to workspace (create if new)
  imprint workspace delete <n>  Delete a workspace and its data
  imprint wipe [--force]     Wipe active workspace (--all for everything)
  imprint update [--version vX.Y.Z] [--dev] [-y] [--check]
                             Upgrade imprint in place. Preserves data/ and .venv/.
  imprint version            Print version

Examples:
  imprint setup
  imprint setup cursor
  imprint setup codex
  imprint setup copilot
  imprint setup cline
  imprint setup all
  imprint learn
  imprint ingest ~/code
  imprint sync serve --relay sync.example.com
  imprint sync sync.example.com/abc123
`)
}
