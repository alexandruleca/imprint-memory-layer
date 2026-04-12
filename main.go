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
		cmd.Setup()
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
  knowledge setup              Install deps, register MCP server, configure Claude Code
  knowledge ingest [dir]       Import memories + conversations [+ index project files]
  knowledge refresh <dir>      Re-index only files that changed since last index
  knowledge sync serve --relay <host>  Expose KB for syncing via relay
  knowledge sync <host>/<id>   Pull + push to a remote peer
  knowledge relay              Run the sync relay server
  knowledge viz                3D brain cluster visualization
  knowledge version            Print version

Examples:
  knowledge setup
  knowledge ingest ~/code
  knowledge sync serve --relay sync.example.com
  knowledge sync sync.example.com/abc123
  knowledge viz
`)
}
