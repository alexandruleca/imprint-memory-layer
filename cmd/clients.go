package cmd

import (
	"encoding/json"
	"fmt"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
)

// clientTarget is the optional client argument of connect and disconnect.
func clientTarget(command string, args []string) string {
	if len(args) > 1 {
		output.Fail("Usage: imprint " + command + " [" + ClientTargets() + "]")
	}
	if len(args) == 1 {
		return args[0]
	}
	return "all"
}

// Connect registers the installed runtime with the target's clients (default
// all): MCP server, hooks and instructions, as setup writes them. Unlike
// enable it installs and starts nothing, so the dashboard can run it while it
// runs from that runtime.
func Connect(args []string) {
	target := clientTarget("connect", args)
	if !connectClients(target, installedBackend) {
		output.Fail("unknown client: " + target + " (expected: " + ClientTargets() + ")")
	}
}

// Disconnect removes this runtime from the target's clients (default all): its
// MCP registrations, hooks, Claude permission and managed instructions, in
// local profiles and in the WSL profiles recorded for those clients. Unlike
// disable it stops nothing, so the dashboard can run it.
func Disconnect(args []string) {
	target := clientTarget("disconnect", args)
	selected, ok := selectClients(target)
	if !ok {
		output.Fail("unknown client: " + target + " (expected: " + ClientTargets() + ")")
	}
	if err := disconnectClients(selected, platform.FindProjectDir()); err != nil {
		output.Fail("Cleanup incomplete; run imprint disconnect again after fixing it: " + err.Error())
	}
	names := "every client"
	if target != "all" {
		names = selected[0].label
	}
	output.Success("Disconnected Imprint from " + names + ". Run imprint connect to reconnect.")
}

// clientStatus is one line of `imprint clients`.
type clientStatus struct {
	Name      string `json:"name"`
	Label     string `json:"label"`
	Connected bool   `json:"connected"`
	Error     string `json:"error,omitempty"`
}

// Clients lists the clients Imprint connects to and whether this runtime is
// registered with each. --json is what the dashboard reads.
func Clients(args []string) {
	asJSON := len(args) == 1 && args[0] == "--json"
	if len(args) > 0 && !asJSON {
		output.Fail("Usage: imprint clients [--json]")
	}
	project := platform.FindProjectDir()
	list := make([]clientStatus, 0, len(clients))
	for _, c := range clients {
		connected, err := isConnected(c, project)
		status := clientStatus{Name: c.name, Label: c.label, Connected: connected}
		if err != nil {
			status.Error = err.Error()
		}
		list = append(list, status)
	}
	if asJSON {
		data, _ := json.MarshalIndent(list, "", "  ")
		fmt.Println(string(data))
		return
	}
	for _, s := range list {
		state := "not connected"
		if s.Connected {
			state = "connected"
		}
		if s.Error != "" {
			state += " (" + s.Error + ")"
		}
		fmt.Printf("  %-16s %-16s %s\n", s.Label, s.Name, state)
	}
	fmt.Println()
	fmt.Println("  imprint connect <name> connects one client, imprint connect connects every installed client; imprint disconnect disconnects the same way.")
}
