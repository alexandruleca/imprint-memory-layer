package cmd

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hunter/imprint/internal/queuelock"
)

func TestOcrRepairWorker(t *testing.T) {
	if os.Getenv("IMPRINT_TEST_OCR_WORKER") != "1" {
		return
	}
	f, err := os.OpenFile(os.Getenv("IMPRINT_TEST_OCR_TRACE"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		os.Exit(2)
	}
	fmt.Fprintf(f, "%d:%s\n", os.Getpid(), os.Getenv("IMPRINT_OCR_REPAIR_REQUEST"))
	f.Close()
	if os.Getenv("IMPRINT_TEST_OCR_NORMAL_FAILURE") == "1" {
		os.Exit(1)
	}
	if os.Getenv("IMPRINT_OCR_REPAIR_REQUEST") == "1" {
		os.Exit(75)
	}
	if os.Getenv("IMPRINT_TEST_OCR_STILL_MISSING") == "1" {
		os.Exit(1)
	}
	os.Exit(0)
}

func TestOcrRepairReleasesLockAndRetriesFreshWorkerOnce(t *testing.T) {
	for _, scenario := range []string{"fixed", "still missing", "bootstrap failed", "timeout", "unrelated failure"} {
		t.Run(scenario, func(t *testing.T) {
			data := t.TempDir()
			lock, err := queuelock.TryAcquire(data, "ingest")
			if err != nil {
				t.Fatal(err)
			}
			defer func() { lock.Release() }()
			self, _ := os.Executable()
			trace := filepath.Join(data, "workers")
			worker := exec.Command(self, "-test.run=^TestOcrRepairWorker$")
			worker.Env = append(os.Environ(), "IMPRINT_TEST_OCR_WORKER=1", "IMPRINT_DATA_DIR="+data, "IMPRINT_TEST_OCR_TRACE="+trace)
			if scenario == "still missing" {
				worker.Env = append(worker.Env, "IMPRINT_TEST_OCR_STILL_MISSING=1")
			}
			if scenario == "unrelated failure" {
				worker.Env = append(worker.Env, "IMPRINT_TEST_OCR_NORMAL_FAILURE=1")
			}
			calls := 0
			err = runOcrRepair(worker, &lock, func(*exec.Cmd) error {
				calls++
				setupLock, err := queuelock.TryAcquire(data, "setup")
				if err != nil {
					t.Fatal("bootstrap blocked by original ingest lock:", err)
				}
				setupLock.Release()
				if scenario == "bootstrap failed" {
					return fmt.Errorf("installer failed")
				}
				if scenario == "timeout" {
					return context.DeadlineExceeded
				}
				return nil
			})
			if scenario == "fixed" && err != nil {
				t.Fatal(err)
			}
			if scenario != "fixed" && err == nil {
				t.Fatal("expected failure")
			}
			expectedCalls, expectedWorkers := 1, 1
			if scenario == "unrelated failure" {
				expectedCalls = 0
			}
			if scenario == "fixed" || scenario == "still missing" {
				expectedWorkers = 2
			}
			if calls != expectedCalls {
				t.Fatalf("bootstrap calls %d, want %d", calls, expectedCalls)
			}
			content, _ := os.ReadFile(trace)
			workers := strings.Fields(string(content))
			if len(workers) != expectedWorkers {
				t.Fatalf("workers: %q", content)
			}
			if expectedWorkers == 2 {
				if strings.Split(workers[0], ":")[0] == strings.Split(workers[1], ":")[0] {
					t.Fatal("reused worker")
				}
				if !strings.HasSuffix(workers[1], ":0") {
					t.Fatal("retry permits another repair")
				}
				if other, err := queuelock.TryAcquire(data, "other"); err == nil {
					other.Release()
					t.Fatal("retry did not reacquire queue lock")
				}
			}
		})
	}
}
