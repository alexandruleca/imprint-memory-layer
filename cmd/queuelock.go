package cmd

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/queuelock"
)

// acquireOrEnqueue tries the queue lock non-blocking. On busy it tries three
// things in order:
//
//  1. IMPRINT_QUEUE_NO_WAIT=1 → print the busy error and exit 1 (scripts).
//  2. IMPRINT_QUEUE_FOREGROUND=1 (set by the dispatcher when it spawns a
//     child) → wait-poll the lock so the subprocess doesn't re-enqueue
//     itself into the API and loop forever.
//  3. If ``enqueueArgs`` is non-nil and the UI server is running, POST the
//     command to /api/commands/<cmd>, print the job id, and exit 0 so the
//     user's terminal isn't tied to a waiting job.
//  4. Otherwise fall back to wait-poll in the foreground.
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
		if jobID, pos, apierr := enqueueViaAPI(dataDir, command, enqueueArgs); apierr == nil {
			printEnqueued(jobID, pos, busy)
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

func enqueueViaAPI(dataDir, command string, args []string) (string, int, error) {
	st, ok := readUIState(dataDir)
	if !ok {
		return "", 0, errors.New("ui server not running")
	}
	body, _ := json.Marshal(map[string]any{"args": args})
	url := fmt.Sprintf("http://127.0.0.1:%d/api/commands/%s", st.Port, command)
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Post(url, "application/json", bytes.NewReader(body))
	if err != nil {
		return "", 0, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return "", 0, fmt.Errorf("api %d: %s", resp.StatusCode, string(raw))
	}
	var r struct {
		JobID    string `json:"job_id"`
		Position int    `json:"position"`
		Error    string `json:"error"`
	}
	if err := json.Unmarshal(raw, &r); err != nil {
		return "", 0, err
	}
	if r.Error != "" {
		return "", 0, errors.New(r.Error)
	}
	return r.JobID, r.Position, nil
}

func printEnqueued(jobID string, pos int, busy *queuelock.BusyError) {
	short := jobID
	if len(short) > 8 {
		short = short[:8]
	}
	line := fmt.Sprintf("Queued as %s", short)
	if pos > 0 {
		line += fmt.Sprintf(" (position %d)", pos)
	}
	output.Success(line + ".")
	if busy != nil && busy.Holder != nil {
		output.Info(fmt.Sprintf("  Will run after: %s (pid %d).", busy.Holder.Command, busy.Holder.PID))
	}
	output.Info("  Monitor or cancel: open the /queue page.")
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
