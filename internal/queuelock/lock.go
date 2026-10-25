// Package queuelock is the Go side of the shared imprint command-queue lock.
//
// The FastAPI dispatcher and the Go CLI both compete for a single advisory
// lock on {dataDir}/queue.lock. Only one ingest/refresh/retag/ingest-url
// job runs at a time regardless of which side started it. The lock file
// body is JSON describing the holder so each side can print a useful
// error when it can't acquire.
//
// A holder may declare itself preemptible (the dashboard's background
// tagging job, ADR 0067). TryAcquire stops such a holder and takes the lock
// instead of reporting it busy; imprint/queue_lock.py does the same.
package queuelock

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// Holder describes the process currently holding the queue lock.
type Holder struct {
	PID         int     `json:"pid"`
	JobID       string  `json:"job_id"`
	Command     string  `json:"command"`
	StartedAt   float64 `json:"started_at"`
	Preemptible bool    `json:"preemptible,omitempty"`
}

// How long TryAcquire waits for a stopped preemptible holder to release the
// lock, and when it escalates to a forced kill (Unix; Windows terminates at once).
var (
	preemptWait      = 6 * time.Second
	preemptKillAfter = 3 * time.Second
)

func lockPath(dataDir string) string {
	return filepath.Join(dataDir, "queue.lock")
}

// TryAcquire attempts a non-blocking exclusive lock on the queue lock file.
// On success it writes holder metadata and returns a Handle. A preemptible
// holder is stopped and the lock taken once it exits, which takes up to a few
// seconds. On failure it returns a *BusyError wrapping the current holder
// (best effort).
func TryAcquire(dataDir, command string) (*Handle, error) {
	if err := os.MkdirAll(dataDir, 0o755); err != nil {
		return nil, fmt.Errorf("mkdir data dir: %w", err)
	}
	h := Holder{
		PID:       os.Getpid(),
		JobID:     fmt.Sprintf("cli-%d", os.Getpid()),
		Command:   command,
		StartedAt: float64(time.Now().UnixNano()) / 1e9,
	}
	body, _ := json.Marshal(h)

	handle, err := acquireNative(lockPath(dataDir), body)
	if _, busy := err.(*busySentinel); busy {
		holder, _ := ReadHolder(dataDir)
		if holder != nil && holder.Preemptible && holder.PID > 0 && holder.PID != os.Getpid() && processAlive(holder.PID) {
			handle, err = takeFromPreemptible(dataDir, body, holder.PID)
		}
		if _, still := err.(*busySentinel); still {
			holder, _ = ReadHolder(dataDir)
			return nil, &BusyError{Holder: holder}
		}
	}
	if err != nil {
		return nil, err
	}
	return handle, nil
}

// takeFromPreemptible stops a preemptible holder and polls for the lock while
// it exits. The holder loses at most the work it had in progress.
func takeFromPreemptible(dataDir string, body []byte, pid int) (*Handle, error) {
	stopProcess(pid, false)
	start := time.Now()
	escalated := false
	for {
		handle, err := acquireNative(lockPath(dataDir), body)
		if _, busy := err.(*busySentinel); !busy {
			return handle, err
		}
		if time.Since(start) >= preemptWait {
			return nil, err
		}
		if !escalated && time.Since(start) >= preemptKillAfter {
			stopProcess(pid, true)
			escalated = true
		}
		time.Sleep(50 * time.Millisecond)
	}
}

// Release drops the lock. Safe to call on a nil handle.
func (h *Handle) Release() {
	if h == nil {
		return
	}
	releaseNative(h)
}

// ReadHolder best-effort reads the lock file body.
func ReadHolder(dataDir string) (*Holder, error) {
	data, err := os.ReadFile(lockPath(dataDir))
	if err != nil {
		return nil, err
	}
	if len(data) == 0 {
		return nil, fmt.Errorf("lock file empty")
	}
	var h Holder
	if err := json.Unmarshal(data, &h); err != nil {
		return nil, err
	}
	return &h, nil
}

// BusyError is returned by TryAcquire when another process holds the lock.
type BusyError struct {
	Holder *Holder
}

func (e *BusyError) Error() string {
	if e.Holder == nil {
		return "another imprint job is running (queue lock held)"
	}
	age := "just started"
	if e.Holder.StartedAt > 0 {
		d := time.Since(time.Unix(int64(e.Holder.StartedAt), 0))
		age = d.Round(time.Second).String() + " ago"
	}
	return fmt.Sprintf(
		"another imprint job is running\n  command: %s\n  pid:     %d\n  started: %s\nCancel it from the UI (/queue) or: kill %d",
		e.Holder.Command, e.Holder.PID, age, e.Holder.PID,
	)
}

// busySentinel is returned by per-OS implementations when the lock is held
// by another process; TryAcquire translates it to *BusyError.
type busySentinel struct{}

func (*busySentinel) Error() string { return "lock held" }
