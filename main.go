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
		//   --retry-gpu           forget a recorded CUDA failure (GPU embeddings)
		//   --profile <cpu|gpu|auto>   local LLM build: cpu, or GPU when available (persists)
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
		if !cmd.Setup(target) {
			fmt.Fprintf(os.Stderr, "unknown setup target %q (expected: %s)\n", target, cmd.ClientTargets())
			os.Exit(1)
		}
	case "bootstrap":
		cmd.Bootstrap(os.Args[2:])
	case "wsl":
		cmd.WSL(os.Args[2:])
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
		cmd.Relay(os.Args[2:], version)
	case "ui":
		cmd.UI(os.Args[2:])
	case "retag":
		cmd.Retag(os.Args[2:])
	case "scrub":
		cmd.Scrub(os.Args[2:])
	case "migrate":
		cmd.Migrate(os.Args[2:])
	case "server":
		cmd.Server(os.Args[2:])
	case "enable":
		cmd.Enable(os.Args[2:])
	case "disable":
		cmd.Disable(os.Args[2:])
	case "connect":
		cmd.Connect(os.Args[2:])
	case "disconnect":
		cmd.Disconnect(os.Args[2:])
	case "clients":
		cmd.Clients(os.Args[2:])
	case "uninstall":
		cmd.Uninstall(os.Args[2:])
	case "status":
		cmd.Status(os.Args[2:])
	case "doctor":
		cmd.Doctor(os.Args[2:])
	case "config":
		cmd.Config(os.Args[2:])
	case "workspace":
		cmd.Workspace(os.Args[2:])
	case "purge":
		cmd.Purge(os.Args[2:])
	case "move":
		cmd.Move(os.Args[2:])
	case "device":
		cmd.Device(os.Args[2:])
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
  imprint wsl list [--json]  Show WSL distributions' client profiles and which are enrolled (Windows only)
  imprint wsl setup --distribution NAME --target %[2]s
                             Connect a selected WSL profile to this Windows runtime
  imprint wsl remove --distribution NAME --target %[2]s
                             Remove that bridge while preserving memories and other clients
  imprint setup [target] [--profile cpu|gpu|auto] [--with-llm|--no-llm] [--retry-gpu] [--non-interactive]
                             Install deps, then connect AI clients (MCP server, hooks, instructions).
                             The embedding runtime follows model.device; --profile picks the local LLM's build
                               target (default all): %[1]s
  imprint bootstrap [--profile cpu|gpu|auto] [--with-llm] [--non-interactive]
                             Provision venv + selected dependencies only (no MCP registration).
                             Intended for installer scripts; end users should run 'imprint setup'.
  imprint profile            Show active install profile (local LLM build + with-llm flag)
  imprint profile set <cpu|gpu|auto>   Swap profile (cpu: CPU LLM; gpu/auto: GPU LLM when found); reinstalls deps via uv
  imprint profile add-llm    Install llama-cpp-python into active profile
  imprint profile drop-llm   Uninstall llama-cpp-python
  imprint ingest <path>      Index project source files (directory or single file)
  imprint learn              Index coding-client conversations (incl. WSL profiles) + memory files
  imprint ingest-url <url>   Fetch URL(s), extract content, and index (html/pdf/etc)
  imprint refresh <dir>      Re-index only files that changed since last index
  imprint refresh-urls       Re-check stored URLs (ETag/Last-Modified) and re-index changed
  imprint sync serve [--relay <host>] [--workspace <n>] [--send-only | --receive-only]
                             Serve a workspace for end-to-end encrypted sync; prints a share code
  imprint sync <code> [--workspace <n>] [--two-way | --send-only]
                             Receive from the serving machine; --two-way also sends (confirmed first)
  imprint sync export         Export snapshot bundle (no re-embed on import)
  imprint sync import <dir>   Import snapshot bundle from another device
  imprint relay [--port N] [--ga4-id G-…]
                             Run the sync relay server; --ga4-id adds Google Analytics to its browser page
  imprint ui [--port N]      Dashboard UI foreground (FastAPI + Next.js; Ctrl+C to stop)
  imprint ui start [--port N]  Start the UI server detached in the background
  imprint ui stop            Stop the running UI server (also the desktop app's)
  imprint ui status          Show UI server pid + reachability
  imprint ui open [--port N]  Ensure server is running, then open a browser window
  imprint ui restart         Restart the running UI server with the installed code
  imprint ui log             Print the UI log file path (for tail -f)
  imprint retag [--project] [--all]  Re-tag existing memories (--all re-runs even already-tagged chunks)
  imprint retag --pending    Tag now what learn and ingest left for background LLM tagging
  imprint scrub [--workspace NAME] [--apply]
                             Count secrets stored in memories and facts (--apply redacts them and re-embeds)
  imprint migrate --from WS1 --to WS2 --project NAME | --topic TAG | --source KEY [--copy] [--dry-run]
                             Move (or --copy) memories between workspaces (preserves vectors)
  imprint server <cmd>       Manage the local Qdrant server
                               cmd: start | stop | status | log
  imprint config             Show all settings and current values
  imprint config set <k> <v> Persist a setting (e.g. model.name, qdrant.port)
  imprint config get <key>   Show one setting
  imprint config reset <key> Remove override, revert to default
  imprint doctor [--json] [--probe-mcp]
                             Diagnose runtime, registration and MCP health without changing setup
  imprint status             Show enabled/disabled state, server pid, hook count, memory stats
  imprint disable            Disconnect managed clients and stop services (data preserved)
  imprint enable [target]    Re-wire MCP + hooks + start server (target: %[1]s)
  imprint clients [--json]   List the AI clients Imprint connects to and whether each is connected
  imprint connect [client]   Register the installed runtime with clients (MCP server, hooks, instructions);
                               installs and starts nothing (client: %[1]s)
  imprint disconnect [client]
                             Remove Imprint from clients, including their recorded WSL profiles; stops nothing
  imprint uninstall [-y] [--keep-data]
                             Disconnect clients, stop Imprint processes, remove runtime and memories (--keep-data keeps memories)
  imprint workspace          List workspaces and show active
  imprint workspace switch <n>  Switch to workspace (create if new)
  imprint workspace delete <n>  Delete a workspace and its data
  imprint workspace use [<n>]   Make this project use a workspace: approve it, write .imprint/workspace.json
  imprint workspace roots [add|remove] <path>  Approve filesystem roots (--allow-overlap confirms sharing)
  imprint workspace map <project> <directory>  Map shared project paths to this device's checkout
  imprint device            Show this device and the current working directory
  imprint purge <path>       Preview file/directory cleanup (--apply deletes; --source matches source keys)
  imprint move <project> <target> [--source KEY]...
                             Preview moving a project, or some of its sources, to another project (--apply moves)
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
  imprint sync sync.example.com/7f3a9c21-Ab3xY9Kq
`, cmd.ClientTargets(), cmd.WSLTargets())
}
