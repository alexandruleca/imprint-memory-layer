package main

import (
	"fmt"
	"os"

	"github.com/hunter/knowledge/cmd"
)

var version = "dev"

// mempalace subcommands that we pass through
var passthroughCmds = map[string]bool{
	"search":       true,
	"status":       true,
	"mine":         true,
	"init":         true,
	"compress":     true,
	"wake-up":      true,
	"split":        true,
	"hook":         true,
	"instructions": true,
	"repair":       true,
}

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}

	switch os.Args[1] {
	case "setup":
		cmd.Setup()
	case "index":
		cmd.Index(os.Args[2:])
	case "migrate":
		cmd.Migrate()
	case "version", "--version":
		fmt.Printf("knowledge %s\n", version)
	default:
		if passthroughCmds[os.Args[1]] {
			cmd.Passthrough(os.Args[1:])
		} else {
			printUsage()
			os.Exit(1)
		}
	}
}

func printUsage() {
	fmt.Fprintf(os.Stderr, `knowledge — MemPalace setup and indexing tool

Usage:
  knowledge setup              Install mempalace, register MCP server, configure alias
  knowledge index <dir>        Run mempalace init + mine on every subdirectory of <dir>
  knowledge migrate            Migrate Claude Code auto-memory files into MemPalace
  knowledge version            Print version

  Passthrough to mempalace (auto-injects --palace):
  knowledge search <query>     Search the palace
  knowledge status             Show what's been filed
  knowledge mine <dir>         Mine a directory into the palace
  knowledge wake-up            Show L0+L1 wake-up context
  knowledge compress           Compress drawers using AAAK dialect
  knowledge repair             Rebuild palace vector index

Examples:
  knowledge setup
  knowledge index ~/code/brightspaces/node
  knowledge search "CORS wildcard"
  knowledge status
`)
}
