package cmd

import (
	"errors"
	"fmt"
	"runtime"
	"time"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/queuelock"
)

// venvChange is what setup holds while it changes the venv (ADR 0084).
var venvChange struct {
	projectDir string
	locks      []*queuelock.Handle
	freed      bool
}

// holdRuntime takes the queue lock, so running jobs finish first and no job
// or other setup starts meanwhile, and the runtime lock, which the dashboard
// server's launchers wait for before they start it. Changes planned after
// this read the venv as it stays until releaseRuntime.
func holdRuntime(projectDir string) {
	if venvChange.locks != nil {
		return
	}
	dataDir := platform.DataDir(projectDir)
	queue := acquireOrFail(dataDir, "setup")
	runtimeLock, err := queuelock.HoldRuntime(dataDir)
	if err != nil {
		queue.Release()
		output.Fail("Could not take the runtime lock: " + err.Error())
	}
	venvChange.projectDir, venvChange.locks = projectDir, []*queuelock.Handle{runtimeLock, queue}
}

// releaseRuntime lets the dashboard server start on the changed venv and
// jobs run again.
func releaseRuntime() {
	for _, lock := range venvChange.locks {
		lock.Release()
	}
	venvChange.projectDir, venvChange.locks, venvChange.freed = "", nil, false
}

// freeRuntime readies the held venv, once, for a change that replaces or
// removes files. On Windows it stops every process running from the runtime:
// Windows refuses to replace a loaded file unless another hard link to it
// remains, and nothing guarantees one. A running dashboard server is
// restarted instead, so its owner keeps it; it loads the venv again once
// releaseRuntime lets it.
func freeRuntime() {
	if venvChange.freed {
		return
	}
	if venvChange.locks == nil {
		panic("setup changed the venv without holding the runtime")
	}
	venvChange.freed = true
	dataDir := platform.DataDir(venvChange.projectDir)
	roots := []string{platform.VenvDir(venvChange.projectDir), platform.UvPythonInstallDir(venvChange.projectDir)}
	var server []int
	if st, ok := readUIState(dataDir); ok && uiHealthy(st.Port) {
		server = withLauncher(st.PID, roots)
	}
	if runtime.GOOS == "windows" {
		output.Info("Stopping Imprint's processes so their files can be replaced; AI clients reconnect Imprint afterwards")
		if err := stopOwnedProcesses(roots, "", server...); err != nil {
			output.Fail(err.Error() + " — close them and run setup again")
		}
	}
	if server == nil {
		return
	}
	if _, err := requestUIRestart(dataDir); errors.Is(err, errNoUIServer) {
		return
	} else if err != nil {
		output.Fail(err.Error() + " — close Imprint and run setup again")
	}
	output.Info("Restarting the dashboard server; it starts again once setup is done")
	if !waitForExit(server, 30*time.Second) {
		output.Fail(fmt.Sprintf("The dashboard server (pid %d) did not exit for its restart — close Imprint and run setup again", server[0]))
	}
}
