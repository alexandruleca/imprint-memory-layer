package cmd

import (
	"net/http"
	"os"
	"testing"
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
	dataDir := registeredServer(t, func(w http.ResponseWriter, r *http.Request) {
		asked++
		_ = server.Process.Kill() // The server exits for its launcher to start it again.
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
	select {
	case <-exited:
	default:
		t.Fatal("setup went on before the dashboard server exited")
	}
}
