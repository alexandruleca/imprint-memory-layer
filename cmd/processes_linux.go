package cmd

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

func listProcesses() ([]processInfo, error) {
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return nil, err
	}
	var procs []processInfo
	for _, entry := range entries {
		pid, err := strconv.Atoi(entry.Name())
		if err != nil {
			continue
		}
		dir := filepath.Join("/proc", entry.Name())
		// Other users' processes, kernel threads and zombies have no readable exe.
		exe, _ := os.Readlink(filepath.Join(dir, "exe"))
		cmdline, _ := os.ReadFile(filepath.Join(dir, "cmdline"))
		if exe == "" && len(cmdline) == 0 {
			continue
		}
		procs = append(procs, processInfo{PID: pid, Exe: strings.TrimSuffix(exe, " (deleted)"), Cmdline: string(cmdline)})
	}
	return procs, nil
}
