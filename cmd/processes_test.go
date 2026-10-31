package cmd

import (
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/hunter/imprint/internal/testtime"
)

func TestOwnedBy(t *testing.T) {
	root := filepath.Join(t.TempDir(), "Imprint")
	bin := filepath.Join(root, "bin")
	cases := []struct {
		name string
		p    processInfo
		want bool
	}{
		{"cli in the install", processInfo{Exe: filepath.Join(bin, "imprint")}, true},
		{"nested runtime file", processInfo{Exe: filepath.Join(bin, "imprint-ui.app", "Contents", "MacOS", "Imprint")}, true},
		{"sibling with a longer name", processInfo{Exe: filepath.Join(root+"2", "bin", "imprint")}, false},
		{"the root itself", processInfo{Exe: bin}, false},
		{"desktop shell from a temp copy", processInfo{Exe: filepath.Join(os.TempDir(), "x", "Imprint"), Cmdline: "Imprint\x00--imprint-url=http://127.0.0.1:8420\x00--project-dir=" + root + "\x00--data-dir=d"}, true},
		{"quoted Windows argument", processInfo{Cmdline: `"Imprint.exe" "--project-dir=` + root + `" "--data-dir=d"`}, true},
		{"another installation's shell", processInfo{Cmdline: "Imprint --project-dir=" + root + "2"}, false},
		{"browser opened on the dashboard", processInfo{Exe: "/usr/bin/firefox", Cmdline: "firefox\x00http://127.0.0.1:8420"}, false},
		{"editor showing a log", processInfo{Exe: "/usr/bin/gedit", Cmdline: "gedit\x00" + filepath.Join(root, "data", "imprint_ui.log")}, false},
	}
	for _, c := range cases {
		if got := ownedBy(c.p, []string{bin}, root); got != c.want {
			t.Errorf("%s: ownedBy = %v, want %v", c.name, got, c.want)
		}
	}
}

func TestListProcessesSeesThisProcess(t *testing.T) {
	procs, err := listProcesses()
	if err != nil {
		t.Fatal(err)
	}
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, p := range procs {
		if p.PID != os.Getpid() {
			continue
		}
		if !strings.EqualFold(filepath.Base(p.Exe), filepath.Base(exe)) {
			t.Errorf("exe = %q, want %q", p.Exe, exe)
		}
		if !strings.Contains(p.Cmdline, "-test.") {
			t.Errorf("cmdline %q lacks the test flags", p.Cmdline)
		}
		return
	}
	t.Fatalf("pid %d not among %d processes", os.Getpid(), len(procs))
}

// TestHelperSleep is the process the stop test starts; it is skipped in
// normal runs.
func TestHelperSleep(t *testing.T) {
	if os.Getenv("IMPRINT_TEST_SLEEPER") != "1" {
		t.Skip("helper process")
	}
	time.Sleep(testtime.Slack(time.Minute)) // past every wait of the test that started it
}

func TestStopOwnedProcessesEndsOnlyThisInstallation(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	bin := filepath.Join(root, "bin")
	copyFile(t, self, filepath.Join(bin, "sleeper"+filepath.Ext(self)))

	installed, fromInstall := startSleeper(t, filepath.Join(bin, "sleeper"+filepath.Ext(self)))
	skipUnderEmulation(t, installed.Process.Pid)
	_, shell := startSleeper(t, self, "--project-dir="+root)
	_, unrelated := startSleeper(t, self, "--project-dir="+root+"-other")
	waitUntil(t, func() bool { return countOwned(t, bin, root) == 2 })

	if err := stopOwnedProcesses([]string{bin}, root); err != nil {
		t.Fatal(err)
	}
	for name, done := range map[string]chan struct{}{"install process": fromInstall, "desktop shell": shell} {
		select {
		case <-done:
		case <-time.After(testtime.Slack(10 * time.Second)):
			t.Errorf("%s still running", name)
		}
	}
	select {
	case <-unrelated:
		t.Error("a process of another installation was stopped")
	default:
	}
}

// Setup restarts the dashboard server, and on Windows the venv launcher
// running it, instead of stopping them (ADR 0084).
func TestStopOwnedProcessesSparesTheRestartedServer(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(t.TempDir(), "bin")
	sleeper := filepath.Join(bin, "sleeper"+filepath.Ext(self))
	copyFile(t, self, sleeper)
	server, serverDone := startSleeper(t, sleeper)
	skipUnderEmulation(t, server.Process.Pid)
	other, otherDone := startSleeper(t, sleeper)
	waitUntil(t, func() bool { return countOwned(t, bin, "") == 2 })

	// This test process started the server; it is its launcher only when it
	// runs from the runtime too.
	if got := withLauncher(server.Process.Pid, []string{bin}); !slices.Equal(got, []int{server.Process.Pid}) {
		t.Errorf("withLauncher outside the runtime = %v, want only the server", got)
	}
	if got := withLauncher(server.Process.Pid, []string{bin, filepath.Dir(self)}); !slices.Equal(got, []int{server.Process.Pid, os.Getpid()}) {
		t.Errorf("withLauncher = %v, want the server and this process", got)
	}

	if err := stopOwnedProcesses([]string{bin}, "", server.Process.Pid); err != nil {
		t.Fatal(err)
	}
	select {
	case <-otherDone:
	case <-time.After(testtime.Slack(10 * time.Second)):
		t.Errorf("process %d still running", other.Process.Pid)
	}
	select {
	case <-serverDone:
		t.Error("the excepted server was stopped")
	default:
	}
	if !waitForExit([]int{other.Process.Pid}, testtime.Slack(10*time.Second)) || waitForExit([]int{server.Process.Pid}, 200*time.Millisecond) {
		t.Error("waitForExit does not tell an exited process from a running one")
	}
}

// startSleeper runs binary as TestHelperSleep until the test ends.
func startSleeper(t *testing.T, binary string, extra ...string) (*exec.Cmd, chan struct{}) {
	cmd := exec.Command(binary, append([]string{"-test.run=^TestHelperSleep$", "--"}, extra...)...)
	cmd.Env = append(os.Environ(), "IMPRINT_TEST_SLEEPER=1")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{})
	go func() { _ = cmd.Wait(); close(done) }()
	t.Cleanup(func() { _ = cmd.Process.Kill(); <-done })
	return cmd, done
}

// skipUnderEmulation skips a test whose processes run under qemu-user, as
// CI's emulated arm64 runner does (ADR 0112): there /proc/<pid>/exe names
// the interpreter, not the program, so no process looks like the install's.
func skipUnderEmulation(t *testing.T, pid int) {
	deadline := time.Now().Add(testtime.Slack(10 * time.Second))
	for time.Now().Before(deadline) {
		procs, err := listProcesses()
		if err != nil {
			t.Fatal(err)
		}
		for _, p := range procs {
			if p.PID == pid && p.Exe != "" {
				if strings.HasPrefix(filepath.Base(p.Exe), "qemu-") {
					t.Skipf("process %d runs under emulation: its exe is %s", pid, p.Exe)
				}
				return
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
}

func countOwned(t *testing.T, bin, project string) int {
	procs, err := listProcesses()
	if err != nil {
		t.Fatal(err)
	}
	n := 0
	for _, p := range procs {
		if ownedBy(p, []string{bin}, project) {
			n++
		}
	}
	return n
}

func waitUntil(t *testing.T, ok func() bool) {
	deadline := time.Now().Add(testtime.Slack(10 * time.Second))
	for !ok() {
		if time.Now().After(deadline) {
			t.Fatal("condition not reached")
		}
		time.Sleep(50 * time.Millisecond)
	}
}

func copyFile(t *testing.T, src, dst string) {
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		t.Fatal(err)
	}
	in, err := os.Open(src)
	if err != nil {
		t.Fatal(err)
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o755)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := io.Copy(out, in); err != nil {
		t.Fatal(err)
	}
	if err := out.Close(); err != nil {
		t.Fatal(err)
	}
}
