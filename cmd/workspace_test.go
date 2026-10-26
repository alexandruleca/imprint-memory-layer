package cmd

import (
	"bytes"
	"strings"
	"testing"
)

func TestWorkspaceUseNeedsANameOrATerminal(t *testing.T) {
	if _, _, err := parseWorkspaceUse(nil, false); err == nil || !strings.Contains(err.Error(), "usage: imprint workspace use") {
		t.Fatalf("no name without a terminal = %v, want the usage", err)
	}
	if name, overlap, err := parseWorkspaceUse(nil, true); err != nil || name != "" || overlap {
		t.Fatalf("no name on a terminal = %q %v %v, want the picker", name, overlap, err)
	}
	name, overlap, err := parseWorkspaceUse([]string{"--allow-overlap", "client-x"}, false)
	if err != nil || name != "client-x" || !overlap {
		t.Fatalf("named = %q %v %v", name, overlap, err)
	}
	for _, args := range [][]string{{"Bad Name"}, {"a", "b"}, {"--force", "a"}} {
		if _, _, err := parseWorkspaceUse(args, true); err == nil {
			t.Errorf("%q was accepted", args)
		}
	}
}

func TestPickWorkspaceOffersKnownProposedAndNew(t *testing.T) {
	here := directoryWorkspace{Workspace: "default", Why: "the active workspace; nothing assigns this directory",
		Proposal: "client-x", File: "/p/.imprint/workspace.json"}
	known := []string{"default", "work"}
	pick := func(input string) (string, string, error) {
		var out bytes.Buffer
		name, err := pickWorkspace(known, here, "/p", strings.NewReader(input), &out)
		return name, out.String(), err
	}

	name, shown, err := pick("2\n")
	if err != nil || name != "work" {
		t.Fatalf("2 = %q %v", name, err)
	}
	for _, line := range []string{"Which workspace should /p use?", "1) default (now: the active workspace",
		"3) client-x (asked for by /p/.imprint/workspace.json; new)", "n) a new workspace"} {
		if !strings.Contains(shown, line) {
			t.Errorf("menu lacks %q:\n%s", line, shown)
		}
	}
	if name, _, _ := pick("3\n"); name != "client-x" {
		t.Errorf("the proposal = %q", name)
	}
	// A wrong choice or invalid name asks again; a new name is validated.
	name, shown, err = pick("9\nn\nBad Name\nresearch\n")
	if err != nil || name != "research" {
		t.Fatalf("new = %q %v", name, err)
	}
	if !strings.Contains(shown, "Enter a number from the list, or n.") || !strings.Contains(shown, "lowercase") {
		t.Errorf("no correction shown:\n%s", shown)
	}
	if _, _, err := pick(""); err == nil {
		t.Error("end of input chose a workspace")
	}
}
