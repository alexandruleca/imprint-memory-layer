package cmd

import (
	"os/exec"
	"strconv"
	"strings"
)

func listProcesses() ([]processInfo, error) {
	// Separate calls: a path in one column could contain spaces.
	exes, err := psColumn("comm")
	if err != nil {
		return nil, err
	}
	args, err := psColumn("args")
	if err != nil {
		return nil, err
	}
	procs := make([]processInfo, 0, len(exes))
	for pid, exe := range exes {
		procs = append(procs, processInfo{PID: pid, Exe: exe, Cmdline: args[pid]})
	}
	return procs, nil
}

// psColumn maps each pid to one `ps` column, untruncated.
func psColumn(column string) (map[int]string, error) {
	out, err := exec.Command("ps", "-axww", "-o", "pid=", "-o", column+"=").Output()
	if err != nil {
		return nil, err
	}
	values := map[int]string{}
	for _, line := range strings.Split(string(out), "\n") {
		pid, value, ok := strings.Cut(strings.TrimSpace(line), " ")
		if n, err := strconv.Atoi(pid); ok && err == nil {
			values[n] = strings.TrimSpace(value)
		}
	}
	return values, nil
}
