package cmd

import (
	"net/http"
	"os"
	"sync/atomic"
	"testing"
	"time"
)

// Setup restarts a running dashboard server before it replaces what the
// server loaded, and goes on once that server has exited (ADR 0084).
func TestFreeRuntimeRestartsTheDashboardServer(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	server, exited := startSleeper(t, self)
	asked := 0
	var killed atomic.Bool
	dataDir := registeredServer(t, func(w http.ResponseWriter, r *http.Request) {
		asked++
		// The server answers, then exits for its launcher to start it again.
		go func() {
			time.Sleep(500 * time.Millisecond)
			killed.Store(true)
			_ = server.Process.Kill()
		}()
		_, _ = w.Write([]byte(`{"restarting": true}`))
	})
	st, _ := readUIState(dataDir)
	register(t, dataDir, server.Process.Pid, st.Port)
	t.Setenv("IMPRINT_DATA_DIR", dataDir)

	holdRuntime(t.TempDir())
	defer releaseRuntime()
	freeRuntime()
	freeRuntime()
	if asked != 1 {
		t.Fatalf("asked the server to restart %d times, want once", asked)
	}
	// Setup sees the server gone once it is dead; this test's own goroutine
	// reaps it, possibly a moment later, so it cannot be the measure.
	if !killed.Load() {
		t.Fatal("setup went on before the dashboard server exited")
	}
	<-exited
}
