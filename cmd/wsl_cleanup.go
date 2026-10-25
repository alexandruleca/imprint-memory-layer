package cmd

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"slices"
	"strings"

	"github.com/hunter/imprint/internal/configfile"
	"github.com/hunter/imprint/internal/output"
)

type wslRegistration struct {
	Profile       wslProfile `json:"profile"`
	User          string     `json:"user,omitempty"`
	Target        string     `json:"target"`
	CustomProfile bool       `json:"custom_profile,omitempty"`
}

func wslRegistrationFile(project string) string {
	return filepath.Join(project, ".wsl-registrations.json")
}
func readWSLRegistrations(project string) ([]wslRegistration, error) {
	content, err := os.ReadFile(wslRegistrationFile(project))
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var records []wslRegistration
	if err := json.Unmarshal(content, &records); err != nil {
		return nil, err
	}
	return records, nil
}

// sameWSLRegistration compares the registered client's own directory, so a
// record stays the same registration when the probe later reports another
// client's profile in that home.
func sameWSLRegistration(a, b wslRegistration) bool {
	aDir, _ := wslClientDir(a.Profile, a.Target)
	bDir, _ := wslClientDir(b.Profile, b.Target)
	return a.Profile.Distribution == b.Profile.Distribution && a.Target == b.Target && a.Profile.Home == b.Profile.Home && aDir == bDir && a.User == b.User
}
func saveWSLRegistration(project string, record wslRegistration, remove bool) error {
	records, err := readWSLRegistrations(project)
	if err != nil {
		return err
	}
	kept := make([]wslRegistration, 0, len(records)+1)
	for _, previous := range records {
		if !sameWSLRegistration(previous, record) {
			kept = append(kept, previous)
		}
	}
	if !remove {
		kept = append(kept, record)
	}
	encoded, err := json.MarshalIndent(kept, "", "  ")
	if err != nil {
		return err
	}
	return configfile.Write(wslRegistrationFile(project), append(encoded, '\n'))
}

// Removes the registrations of the targets' clients (wslTargets) from the
// recorded concrete profiles, including custom users/homes. No implicit
// discovery of unrelated Linux clients occurs during disable or uninstall.
func cleanupWSLRegistrations(project string, targets []string) error {
	records, err := readWSLRegistrations(project)
	if err != nil {
		return err
	}
	// Migrate installer selections written before the cleanup journal existed.
	selections, err := os.ReadFile(filepath.Join(project, ".wsl-integrations.txt"))
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	var failures []error
	var keptSelections []string
	for _, line := range strings.Split(strings.TrimPrefix(string(selections), "\ufeff"), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		fields := strings.Split(line, "|")
		if len(fields) != 2 || !validDistro(fields[0]) || !slices.Contains(wslTargets(), fields[1]) {
			return fmt.Errorf("invalid saved WSL selection %q", line)
		}
		if !slices.Contains(targets, fields[1]) {
			keptSelections = append(keptSelections, line)
			continue
		}
		found := false
		for _, r := range records {
			if r.Profile.Distribution == fields[0] && r.Target == fields[1] && r.User == "" && !r.CustomProfile {
				found = true
			}
		}
		if found {
			continue
		}
		p, err := probeWSL(fields[0], "", "", "", "")
		if err != nil {
			failures = append(failures, fmt.Errorf("WSL %s: %w", fields[0], err))
			continue
		}
		records = append(records, wslRegistration{Profile: p, Target: fields[1]})
	}
	for _, r := range records {
		dir, err := wslClientDir(r.Profile, r.Target)
		if err != nil || !validDistro(r.Profile.Distribution) || !validLinuxPath(r.Profile.Home) || !validLinuxPath(dir) {
			failures = append(failures, fmt.Errorf("invalid saved WSL profile"))
			continue
		}
		if !slices.Contains(targets, r.Target) {
			continue
		}
		local, err := wslProfileMapper(r.Profile, r.User)
		if err == nil {
			content, readErr := os.ReadFile(local(path.Join(dir, "imprint-wsl")))
			if readErr != nil && !os.IsNotExist(readErr) {
				err = readErr
			} else if readErr == nil && !strings.Contains(string(content), shellQuote(filepath.Join(project, "imprint", "wsl_bridge.py"))) {
				// A newer/different installation owns the shared launcher now.
				output.Skip("Preserving another runtime's WSL bridge in " + r.Profile.Distribution)
			} else {
				err = removeWSLProfile(r.Profile, r.Target, local)
			}
		}
		if err == nil {
			err = saveWSLRegistration(project, r, true)
		}
		if err != nil {
			failures = append(failures, fmt.Errorf("WSL %s (%s): %w", r.Profile.Distribution, r.Target, err))
		}
	}
	if len(failures) > 0 {
		return errors.Join(failures...)
	}
	// Disable must also stop repair/update from re-registering disconnected clients.
	if len(selections) > 0 {
		var kept []byte
		if len(keptSelections) > 0 {
			kept = []byte(strings.Join(keptSelections, "\n") + "\n")
		}
		return configfile.Write(filepath.Join(project, ".wsl-integrations.txt"), kept)
	}
	return nil
}
