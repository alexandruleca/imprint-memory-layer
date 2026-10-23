// Package configfile preserves recoverable originals and writes configuration atomically.
package configfile

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

func Write(path string, content []byte) error {
	mode := os.FileMode(0600)
	old, err := os.ReadFile(path)
	if err == nil {
		if bytes.Equal(old, content) {
			return nil
		}
		if info, statErr := os.Stat(path); statErr == nil {
			mode = info.Mode().Perm()
		}
		backup := fmt.Sprintf("%s.imprint-%d.bak", path, time.Now().UnixNano())
		if err := os.WriteFile(backup, old, 0600); err != nil {
			return fmt.Errorf("backing up config: %w", err)
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".imprint-config-*")
	if err != nil {
		return err
	}
	name := temporary.Name()
	defer os.Remove(name)
	if err := temporary.Chmod(mode); err != nil {
		temporary.Close()
		return err
	}
	if _, err := temporary.Write(content); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(name, path)
}
