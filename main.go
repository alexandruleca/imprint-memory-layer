package main

import (
	"fmt"
	"os"

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
		// `imprint setup`              → claude-code (back-compat default)
		// `imprint setup <target>`     → dispatch to a single handler
		// `imprint setup all`          → run every handler; each self-skips
		//                                  if its host tool isn't installed.
		target := "claude-code"
		if len(os.Args) >= 3 {
			target = os.Args[2]
		}
		fmt.Fprintf(os.Stderr, "\n→ imprint setup target: %s\n\n", target)
		if !cmd.DispatchSetup(target) {
			fmt.Fprintf(os.Stderr, "unknown setup target %q (expected: claude-code | cursor | codex | copilot | cline | all)\n", target)
			os.Exit(1)
		}
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
	case "status":
		cmd.Status(os.Args[2:])
	case "config":
		cmd.Config(os.Args[2:])
	case "workspace":
		cmd.Workspace(os.Args[2:])
	case "wipe":
		cmd.Wipe(os.Args[2:])
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
  imprint setup [target]     Install deps, register MCP server, configure host AI tool
                               target: claude-code (default) | cursor | codex | copilot | cline | all
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
  imprint ui [--port N]      Dashboard UI (FastAPI + Next.js)
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
  imprint workspace          List workspaces and show active
  imprint workspace switch <n>  Switch to workspace (create if new)
  imprint workspace delete <n>  Delete a workspace and its data
  imprint wipe [--force]     Wipe active workspace (--all for everything)
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
