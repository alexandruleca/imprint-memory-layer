package main

import (
	"fmt"
	"os"

	"github.com/hunter/knowledge/cmd"
)

var version = "dev"

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}

	switch os.Args[1] {
	case "setup":
		// `knowledge setup`              → claude-code (back-compat default)
		// `knowledge setup claude-code`  → SetupClaudeCode
		// `knowledge setup cursor`       → SetupCursor
		target := "claude-code"
		if len(os.Args) >= 3 {
			target = os.Args[2]
		}
		fmt.Fprintf(os.Stderr, "\n→ knowledge setup target: %s\n\n", target)
		switch target {
		case "claude-code", "claude":
			cmd.SetupClaudeCode()
		case "cursor":
			cmd.SetupCursor()
		default:
			fmt.Fprintf(os.Stderr, "unknown setup target %q (expected: claude-code | cursor)\n", target)
			os.Exit(1)
		}
	case "ingest":
		cmd.Ingest(os.Args[2:])
	case "refresh":
		cmd.Refresh(os.Args[2:])
	case "sync":
		cmd.Sync(os.Args[2:])
	case "relay":
		cmd.Relay(os.Args[2:])
	case "viz":
		cmd.Viz(os.Args[2:])
	case "server":
		cmd.Server(os.Args[2:])
	case "enable":
		cmd.Enable(os.Args[2:])
	case "disable":
		cmd.Disable(os.Args[2:])
	case "status":
		cmd.Status(os.Args[2:])
	case "version", "--version":
		fmt.Printf("knowledge %s\n", version)
	default:
		printUsage()
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Fprintf(os.Stderr, `knowledge — AI memory for Claude Code

Usage:
  knowledge setup [target]     Install deps, register MCP server, configure host AI tool
                                 target: claude-code (default) | cursor
  knowledge ingest [dir]       Import memories + conversations [+ index project files]
  knowledge refresh <dir>      Re-index only files that changed since last index
  knowledge sync serve --relay <host>  Expose KB for syncing via relay
  knowledge sync <host>/<id>   Pull + push to a remote peer
  knowledge relay              Run the sync relay server
  knowledge viz                3D brain cluster visualization
  knowledge server <cmd>       Manage the local Qdrant server
                                 cmd: start | stop | status | log
  knowledge status             Show enabled/disabled state, server pid, hook count, memory stats
  knowledge disable            Stop server, unregister MCP, strip hooks (data preserved)
  knowledge enable [target]    Re-wire MCP + hooks + start server (target: claude-code | cursor)
  knowledge version            Print version

Examples:
  knowledge setup
  knowledge setup cursor
  knowledge ingest ~/code
  knowledge sync serve --relay sync.example.com
  knowledge sync sync.example.com/abc123
  knowledge viz
`)
}
