package cmd

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/hunter/imprint/internal/queuelock"
)

// A missing OCR import exits the extraction worker with 75. Bootstrap must
// run after the worker exits and its CLI releases the queue lock (ADR 0109).
// Never replace loaded Python dependencies or wait for our own queue lock.
func runWithOcrRepair(worker *exec.Cmd, held ...**queuelock.Handle) error {
	if len(held) == 0 {
		return worker.Run()
	}
	return runOcrRepair(worker, held[0], bootstrapOcrRuntime)
}

func runOcrRepair(worker *exec.Cmd, held **queuelock.Handle, bootstrap func(*exec.Cmd) error) error {
	worker.Env = append(worker.Environ(), "IMPRINT_OCR_REPAIR_REQUEST=1")
	err := worker.Run()
	var exited *exec.ExitError
	if !errors.As(err, &exited) || exited.ExitCode() != 75 {
		return err
	}
	dataDir := ""
	for _, entry := range worker.Env {
		if strings.HasPrefix(entry, "IMPRINT_DATA_DIR=") {
			dataDir = strings.TrimPrefix(entry, "IMPRINT_DATA_DIR=")
		}
	}
	if dataDir == "" {
		return fmt.Errorf("OCR repair requires the managed installation's data directory")
	}
	command := "ingest"
	if holder, err := queuelock.ReadHolder(dataDir); err == nil && holder.Command != "" {
		command = holder.Command
	}
	(*held).Release()
	if err := bootstrap(worker); err != nil {
		return fmt.Errorf("OCR automatic bootstrap failed: %w; run imprint bootstrap --non-interactive and retry", err)
	}
	*held = acquireOrFail(dataDir, command)
	// Start a fresh interpreter; do not reuse imported native modules. The
	// second worker reports any remaining missing dependency normally.
	retry := exec.Command(worker.Path, worker.Args[1:]...)
	retry.Dir, retry.Env = worker.Dir, append(worker.Environ(), "IMPRINT_OCR_REPAIR_REQUEST=0")
	retry.Stdin, retry.Stdout, retry.Stderr = worker.Stdin, worker.Stdout, worker.Stderr
	return retry.Run()
}

func bootstrapOcrRuntime(worker *exec.Cmd) error {
	self, err := os.Executable()
	if err != nil {
		return err
	}
	fmt.Fprintln(os.Stderr, "OCR runtime missing; attempting imprint bootstrap once…")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	bootstrap := exec.CommandContext(ctx, self, "bootstrap", "--non-interactive")
	bootstrap.Env = append(worker.Environ(), "IMPRINT_QUEUE_FOREGROUND=1")
	bootstrap.Stdout, bootstrap.Stderr = worker.Stderr, worker.Stderr
	if err := bootstrap.Run(); err != nil {
		if ctx.Err() != nil {
			return fmt.Errorf("bootstrap timed out: %w", ctx.Err())
		}
		return err
	}
	return nil
}
