package queuelock

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// Progress is how far the job holding the queue lock is: the record in
// {dataDir}/progress.json that the dashboard, the desktop app and a command
// waiting for the lock all read (ADR 0089). imprint/progress.py writes it for
// Python jobs; setup writes it here. Processed of Total counts Unit; a Total
// of 0 is a step whose size is unknown.
type Progress struct {
	PID       int      `json:"pid"`
	Command   string   `json:"command"`
	Phase     string   `json:"phase"`
	Processed int      `json:"processed"`
	Total     int      `json:"total"`
	Unit      string   `json:"unit"`
	Stored    int      `json:"stored"`
	Skipped   int      `json:"skipped"`
	StartedAt float64  `json:"started_at"`
	UpdatedAt float64  `json:"updated_at"`
	Projects  []string `json:"projects"`
}

func progressPath(dataDir string) string {
	return filepath.Join(dataDir, "progress.json")
}

// WriteProgress replaces the record in one rename, so a reader never sees
// half of it. It is this process's.
func WriteProgress(dataDir string, p Progress) error {
	p.PID = os.Getpid()
	p.UpdatedAt = float64(time.Now().UnixNano()) / 1e9
	if p.Projects == nil {
		p.Projects = []string{}
	}
	data, err := json.Marshal(p)
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dataDir, "progress-*.tmp")
	if err != nil {
		return err
	}
	_, err = tmp.Write(data)
	if closeErr := tmp.Close(); err == nil {
		err = closeErr
	}
	if err == nil {
		err = os.Rename(tmp.Name(), progressPath(dataDir))
	}
	if err != nil {
		os.Remove(tmp.Name())
	}
	return err
}

// ClearProgress removes the record when this process's job is done.
func ClearProgress(dataDir string) {
	if p, ok := ReadProgress(dataDir); ok && p.PID == os.Getpid() {
		os.Remove(progressPath(dataDir))
	}
}

// ReadProgress returns the record while the process that wrote it runs.
func ReadProgress(dataDir string) (Progress, bool) {
	var p Progress
	data, err := os.ReadFile(progressPath(dataDir))
	if err != nil || json.Unmarshal(data, &p) != nil || p.PID <= 0 || !processAlive(p.PID) {
		return Progress{}, false
	}
	return p, true
}

// String reads like "learn: embedding, 340/1200 chunks".
func (p Progress) String() string {
	text := p.Command
	if p.Phase != "" {
		text += ": " + p.Phase
	}
	if p.Total > 0 {
		text += fmt.Sprintf(", %d/%d %s", p.Processed, p.Total, p.Unit)
	}
	return text
}
