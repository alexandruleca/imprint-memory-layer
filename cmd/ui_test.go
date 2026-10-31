package cmd

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/hunter/imprint/internal/testtime"
)

// The test binary stands in for the Python server: it counts its starts in
// a file and asks for a restart until the configured number of starts.
func init() {
	counter := os.Getenv("IMPRINT_TEST_FAKE_SERVER")
	if counter == "" {
		return
	}
	b, _ := os.ReadFile(counter)
	starts := len(b) + 1
	_ = os.WriteFile(counter, append(b, '.'), 0o600)
	if os.Getenv("IMPRINT_TEST_FAKE_SERVER_WAIT") == "1" {
		time.Sleep(testtime.Slack(time.Minute)) // until signaled
	}
	if want, _ := strconv.Atoi(os.Getenv("IMPRINT_TEST_FAKE_SERVER_STARTS")); starts < want {
		os.Exit(uiRestartExitCode)
	}
	os.Exit(3)
}

func fakeServer(t *testing.T, starts int, wait bool) (func() *exec.Cmd, string) {
	counter := filepath.Join(t.TempDir(), "starts")
	return func() *exec.Cmd {
		cmd := exec.Command(os.Args[0], "-test.run=^$")
		cmd.Env = append(os.Environ(),
			"IMPRINT_TEST_FAKE_SERVER="+counter,
			"IMPRINT_TEST_FAKE_SERVER_STARTS="+strconv.Itoa(starts),
			"IMPRINT_TEST_FAKE_SERVER_WAIT="+map[bool]string{true: "1", false: "0"}[wait],
		)
		return cmd
	}, counter
}

func startsRecorded(t *testing.T, counter string) int {
	b, err := os.ReadFile(counter)
	if err != nil {
		t.Fatal(err)
	}
	return len(b)
}

func TestSupervisorStartsTheServerAgainOnlyForARestart(t *testing.T) {
	newCmd, counter := fakeServer(t, 3, false)
	err := superviseServer(newCmd, make(chan os.Signal))
	if exitCode(err) != 3 {
		t.Fatalf("supervision should end with the server's own exit status 3, got %v", err)
	}
	if got := startsRecorded(t, counter); got != 3 {
		t.Fatalf("two restart requests should start the server three times, got %d", got)
	}
}

func TestSupervisorPassesASignalOnAndDoesNotRestart(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows delivers console events to the server directly")
	}
	newCmd, counter := fakeServer(t, 99, true)
	signals := make(chan os.Signal, 1)
	done := make(chan error, 1)
	go func() { done <- superviseServer(newCmd, signals) }()
	for deadline := time.Now().Add(testtime.Slack(10 * time.Second)); ; time.Sleep(20 * time.Millisecond) {
		if b, _ := os.ReadFile(counter); len(b) == 1 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("server did not start")
		}
	}
	signals <- os.Interrupt
	select {
	case err := <-done:
		var exited *exec.ExitError
		if !errors.As(err, &exited) || exitCode(err) == uiRestartExitCode {
			t.Fatalf("the interrupted server should end supervision, got %v", err)
		}
	case <-time.After(testtime.Slack(10 * time.Second)):
		t.Fatal("supervision did not end after the signal")
	}
	if got := startsRecorded(t, counter); got != 1 {
		t.Fatalf("an interrupted server must not be started again, got %d starts", got)
	}
}

// registeredServer serves /api/ping and handles /api/restart, registered in
// a fresh data directory under pid 100.
func registeredServer(t *testing.T, restart http.HandlerFunc) string {
	dataDir := t.TempDir()
	mux := http.NewServeMux()
	mux.HandleFunc("/api/ping", func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) })
	mux.HandleFunc("/api/restart", restart)
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	u, _ := url.Parse(srv.URL)
	port, _ := strconv.Atoi(u.Port())
	register(t, dataDir, 100, port)
	return dataDir
}

func register(t *testing.T, dataDir string, pid, port int) {
	b, _ := json.Marshal(uiState{PID: pid, Port: port})
	if err := os.WriteFile(uiStateFile(dataDir), b, 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestRestartWaitsForTheRegisteredServersReplacement(t *testing.T) {
	var dataDir string
	dataDir = registeredServer(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		st, _ := readUIState(dataDir)
		// The replacement registers itself on the same port.
		go func() { time.Sleep(300 * time.Millisecond); register(t, dataDir, 101, st.Port) }()
		_, _ = w.Write([]byte(`{"restarting": true}`))
	})
	st, err := restartRegisteredUI(dataDir, testtime.Slack(10*time.Second))
	if err != nil {
		t.Fatal(err)
	}
	if st.PID != 101 {
		t.Fatalf("restart should report the replacement, got pid %d", st.PID)
	}
}

func TestRestartStartsNothingWhenNoServerRuns(t *testing.T) {
	dataDir := t.TempDir()
	if _, err := restartRegisteredUI(dataDir, time.Second); !errors.Is(err, errNoUIServer) {
		t.Fatalf("no registration: want errNoUIServer, got %v", err)
	}
	// A registration whose server no longer answers is stale.
	srv := httptest.NewServer(http.NotFoundHandler())
	u, _ := url.Parse(srv.URL)
	port, _ := strconv.Atoi(u.Port())
	srv.Close()
	register(t, dataDir, 100, port)
	if _, err := restartRegisteredUI(dataDir, time.Second); !errors.Is(err, errNoUIServer) {
		t.Fatalf("stale registration: want errNoUIServer, got %v", err)
	}
}

func TestRestartReportsAServerThatCannotRestartOrDoesNotReturn(t *testing.T) {
	refusing := registeredServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusMethodNotAllowed) // a release without /api/restart
	})
	if _, err := restartRegisteredUI(refusing, time.Second); err == nil || !strings.Contains(err.Error(), "HTTP 405") {
		t.Fatalf("want a refusal error, got %v", err)
	}
	unsupervised := registeredServer(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"restarting": true}`))
	})
	if _, err := restartRegisteredUI(unsupervised, 500*time.Millisecond); err == nil || !strings.Contains(err.Error(), "did not start again") {
		t.Fatalf("want a timeout error, got %v", err)
	}
}
