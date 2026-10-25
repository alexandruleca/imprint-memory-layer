package cmd

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/queuelock"
)

// acquireOrEnqueue tries the queue lock non-blocking. On busy it tries three
// things in order:
//
//  1. IMPRINT_QUEUE_NO_WAIT=1 → print the busy error and exit 1 (scripts).
//  2. IMPRINT_QUEUE_FOREGROUND=1 (set by the dispatcher when it spawns a
//     child) → wait-poll the lock so the subprocess doesn't re-enqueue
//     itself into the API and loop forever.
//  3. If “enqueueArgs“ is non-nil and the UI server is running, POST the
//     command to /api/commands/<cmd>, print the job id, and exit 0 so the
//     user's terminal isn't tied to a waiting job. The dashboard runs the
//     job from its own directory, so paths in enqueueArgs must be absolute
//     (queueArgs), and it merges the job into an identical waiting one.
//  4. Otherwise fall back to wait-poll in the foreground.
//
// The dashboard's background tagging job never counts as busy: TryAcquire
// stops it and takes the lock (ADR 0067).
//
// Callers must defer Release() on the returned handle.
func acquireOrEnqueue(dataDir, command string, enqueueArgs []string) *queuelock.Handle {
	h, err := queuelock.TryAcquire(dataDir, command)
	if err == nil {
		return h
	}

	var busy *queuelock.BusyError
	if !errors.As(err, &busy) {
		fmt.Fprintln(os.Stderr, err.Error())
		os.Exit(1)
	}

	if os.Getenv("IMPRINT_QUEUE_NO_WAIT") == "1" {
		fmt.Fprintln(os.Stderr, busy.Error())
		os.Exit(1)
	}

	foreground := os.Getenv("IMPRINT_QUEUE_FOREGROUND") == "1"

	if !foreground && enqueueArgs != nil {
		if job, apierr := enqueueViaAPI(dataDir, command, enqueueArgs); apierr == nil {
			printEnqueued(job, busy)
			os.Exit(0)
		}
	}

	return waitForLock(dataDir, command, busy)
}

// acquireOrFail kept as a thin wrapper for commands that don't yet pass
// enqueue args. Behaves like acquireOrEnqueue with wait-poll semantics.
func acquireOrFail(dataDir, command string) *queuelock.Handle {
	return acquireOrEnqueue(dataDir, command, nil)
}

func waitForLock(dataDir, command string, busy *queuelock.BusyError) *queuelock.Handle {
	announceBusy(busy)
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	reminder := time.NewTicker(30 * time.Second)
	defer reminder.Stop()

	for {
		select {
		case <-ticker.C:
			h, err := queuelock.TryAcquire(dataDir, command)
			if err == nil {
				output.Info("  Queue clear — starting.")
				return h
			}
			if !errors.As(err, &busy) {
				fmt.Fprintln(os.Stderr, err.Error())
				os.Exit(1)
			}
		case <-reminder.C:
			announceBusy(busy)
		}
	}
}

// queuedJob is the dashboard's answer to POST /api/commands/<cmd>.
type queuedJob struct {
	JobID    string `json:"job_id"`
	Position int    `json:"position"`
	// An identical job was already waiting; this is its ID (ADR 0068).
	Merged bool   `json:"merged"`
	Error  string `json:"error"`
}

func enqueueViaAPI(dataDir, command string, args []string) (queuedJob, error) {
	var r queuedJob
	st, ok := readUIState(dataDir)
	if !ok {
		return r, errors.New("ui server not running")
	}
	body, _ := json.Marshal(map[string]any{"args": args, "workspace": os.Getenv("IMPRINT_WORKSPACE")})
	url := fmt.Sprintf("http://127.0.0.1:%d/api/commands/%s", st.Port, command)
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Post(url, "application/json", bytes.NewReader(body))
	if err != nil {
		return r, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return r, fmt.Errorf("api %d: %s", resp.StatusCode, string(raw))
	}
	if err := json.Unmarshal(raw, &r); err != nil {
		return r, err
	}
	if r.Error != "" {
		return r, errors.New(r.Error)
	}
	return r, nil
}

func printEnqueued(job queuedJob, busy *queuelock.BusyError) {
	short := job.JobID
	if len(short) > 8 {
		short = short[:8]
	}
	line := fmt.Sprintf("Queued as %s", short)
	if job.Merged {
		line = fmt.Sprintf("Already queued as %s", short)
	}
	if job.Position > 0 {
		line += fmt.Sprintf(" (position %d)", job.Position)
	}
	output.Success(line + ".")
	if job.Merged {
		output.Info("  An identical job was waiting; it runs once for both requests.")
	}
	if busy != nil && busy.Holder != nil {
		output.Info(fmt.Sprintf("  Will run after: %s (pid %d).", busy.Holder.Command, busy.Holder.PID))
	}
	output.Info("  Monitor or cancel: open the /queue page.")
}

// queueArgs returns args with their paths made absolute, for a job handed to
// the dashboard: the CLI it queues runs from the dashboard's directory, and
// the queue compares paths when it merges identical jobs. The first
// positional argument is a path when firstIsPath is set; pathFlags name
// options whose value is a path, and valueFlags the other options that take
// a value. Windows paths are translated under WSL as the commands do.
func queueArgs(args []string, firstIsPath bool, pathFlags, valueFlags []string) []string {
	abs := func(value string) string {
		if path, err := filepath.Abs(platform.TranslateWSLPath(value)); err == nil {
			return path
		}
		return value
	}
	out := append([]string(nil), args...)
	positional := false
	for i := 0; i < len(out); i++ {
		name, value, hasValue := strings.Cut(out[i], "=")
		switch {
		case strings.HasPrefix(name, "--") && slices.Contains(pathFlags, name):
			if hasValue {
				out[i] = name + "=" + abs(value)
			} else if i+1 < len(out) {
				i++
				out[i] = abs(out[i])
			}
		case strings.HasPrefix(name, "--"):
			if !hasValue && slices.Contains(valueFlags, name) {
				i++
			}
		case firstIsPath && !positional:
			out[i] = abs(out[i])
			positional = true
		default:
			positional = true
		}
	}
	return out
}

func announceBusy(busy *queuelock.BusyError) {
	if busy.Holder == nil {
		output.Info("  Queue busy — waiting for current job to finish...")
		return
	}
	output.Info(fmt.Sprintf(
		"  Queued behind: %s (pid %d). Waiting...",
		busy.Holder.Command, busy.Holder.PID,
	))
}
