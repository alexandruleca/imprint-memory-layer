package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/hunter/imprint/internal/configfile"
	"github.com/hunter/imprint/internal/instructions"
)

// rulesFor is the rules block client gets (ADR 0214): the short one when it
// loads skills, unless instructions.full_rules lists it, else the full one.
// The first time a client gets the short block is recorded in
// data/instructions.json, where upkeep measures whether its agents still
// search first.
func rulesFor(client, dataDir string) string {
	full := !instructions.LoadsSkills(client) || fullRulesListed(client, dataDir)
	if !full {
		recordShortRules(client, dataDir)
	}
	return instructions.Rule(client, full)
}

// fullRulesListed reads instructions.full_rules as the Python runtime does:
// IMPRINT_INSTRUCTIONS_FULL_RULES, else data/config.json, a comma-separated
// list of clients.
func fullRulesListed(client, dataDir string) bool {
	value, set := os.LookupEnv("IMPRINT_INSTRUCTIONS_FULL_RULES")
	if !set && filepath.IsAbs(dataDir) {
		var doc struct {
			Instructions struct {
				FullRules any `json:"full_rules"`
			} `json:"instructions"`
		}
		if raw, err := os.ReadFile(filepath.Join(dataDir, "config.json")); err == nil && json.Unmarshal(raw, &doc) == nil {
			switch listed := doc.Instructions.FullRules.(type) {
			case string:
				value = listed
			case []any:
				for _, item := range listed {
					if name, ok := item.(string); ok {
						value += "," + name
					}
				}
			}
		}
	}
	for _, name := range strings.Split(value, ",") {
		if strings.TrimSpace(name) == client {
			return true
		}
	}
	return false
}

// recordShortRules keeps the time client first got the short block, once,
// leaving whatever else the file holds.
func recordShortRules(client, dataDir string) {
	if !filepath.IsAbs(dataDir) {
		return
	}
	path := filepath.Join(dataDir, "instructions.json")
	doc := map[string]any{}
	if raw, err := os.ReadFile(path); err == nil {
		if json.Unmarshal(raw, &doc) != nil {
			return // Never replace a file this cannot read.
		}
	} else if !os.IsNotExist(err) {
		return
	}
	clients, _ := doc["clients"].(map[string]any)
	if clients == nil {
		clients = map[string]any{}
		doc["clients"] = clients
	}
	entry, _ := clients[client].(map[string]any)
	if entry == nil {
		entry = map[string]any{}
		clients[client] = entry
	}
	if _, recorded := entry["short_since"]; recorded {
		return
	}
	entry["short_since"] = float64(time.Now().UnixNano()) / 1e9
	if raw, err := json.MarshalIndent(doc, "", "  "); err == nil {
		_ = configfile.Write(path, append(raw, '\n'))
	}
}
