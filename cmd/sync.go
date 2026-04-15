package cmd

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math/big"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/hunter/imprint/internal/output"
	"github.com/hunter/imprint/internal/platform"
	"github.com/hunter/imprint/internal/runner"
	"nhooyr.io/websocket"
)

// defaultRelayHost is used when the user does not pass --relay or a host
// prefix on the sync target. Supports WSS.
const defaultRelayHost = "imprint.alexandruleca.com"

type deviceIdentity struct {
	Hostname    string `json:"hostname"`
	User        string `json:"user"`
	OS          string `json:"os"`
	Fingerprint string `json:"fingerprint"`
}

type trustedDevice struct {
	Fingerprint string `json:"fingerprint"`
	Hostname    string `json:"hostname"`
	Added       int64  `json:"added"`
}

type helloRequest struct {
	Method      string `json:"method"`
	Hostname    string `json:"hostname"`
	User        string `json:"user"`
	OS          string `json:"os"`
	Fingerprint string `json:"fingerprint"`
	PIN         string `json:"pin"`
}

func Sync(args []string) {
	if len(args) == 0 {
		fmt.Fprintf(os.Stderr, `Usage:
  imprint sync serve --relay <host>    Expose this machine's KB via relay
  imprint sync <relay-url>/<id>        Pull from + push to remote machine

Examples:
  imprint sync serve --relay sync.example.com
  imprint sync sync.example.com/abc123
`)
		os.Exit(1)
	}

	if args[0] == "serve" {
		syncServe(args[1:])
	} else {
		syncPull(args)
	}
}

func syncServe(args []string) {
	relay := ""
	for i, arg := range args {
		if arg == "--relay" && i+1 < len(args) {
			relay = args[i+1]
		}
	}

	scheme, host := parseRelayHost(relay)

	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Run 'imprint setup' first")
	}

	// Generate room ID
	idBytes := make([]byte, 4)
	rand.Read(idBytes)
	roomID := hex.EncodeToString(idBytes)

	// Generate per-session PIN (always required, even for trusted devices)
	pin := generatePIN()

	// Load or create this device's stable fingerprint
	fingerprint := loadOrCreateFingerprint(dataDir)

	wsURL := fmt.Sprintf("%s://%s/%s?role=provider", scheme, host, roomID)

	fmt.Println()
	output.Header("═══ Imprint Sync Server ═══")
	output.Info("Connecting to relay: " + scheme + "://" + host)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	conn, _, err := websocket.Dial(ctx, wsURL, nil)
	if err != nil {
		output.Fail("Cannot connect to relay: " + err.Error())
	}
	defer conn.Close(websocket.StatusNormalClosure, "done")

	hostname, _ := os.Hostname()
	fmt.Println()
	output.Success("Connected! Share this with the other machine:")
	// Peer only needs the room ID when using the default host; otherwise
	// print the full host/id so they know which relay to hit.
	peerTarget := roomID
	if host != defaultRelayHost || scheme != "wss" {
		peerTarget = scheme + "://" + host + "/" + roomID
	}
	fmt.Printf("\n  imprint sync %s --pin %s\n\n", peerTarget, pin)
	fmt.Printf("  This device: %s (id: %s)\n\n", hostname, fingerprint)
	output.Info("Waiting for peer... (Ctrl+C to stop)")

	// Handle interrupt
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt)
	go func() {
		<-sigCh
		fmt.Println("\n  Disconnecting...")
		cancel()
	}()

	decide := func(hello helloRequest) (bool, bool) {
		trusted := loadTrustedDevices(dataDir)
		if _, ok := trusted[hello.Fingerprint]; ok {
			output.Success(fmt.Sprintf("Trusted device connected: %s (%s)", hello.Hostname, hello.Fingerprint))
			return true, false
		}
		return promptAccept(hello)
	}

	if _, err := providerHandshake(ctx, conn, pin, dataDir, decide); err != nil {
		output.Warn("Handshake failed: " + err.Error())
		return
	}

	// Read requests from consumer via relay, process locally, send response back
	for {
		_, data, err := conn.Read(ctx)
		if err != nil {
			break
		}

		var req struct {
			Method string          `json:"method"`
			Path   string          `json:"path"`
			Body   json.RawMessage `json:"body,omitempty"`
		}
		if err := json.Unmarshal(data, &req); err != nil {
			continue
		}

		var resp []byte

		switch req.Path {
		case "/sync/pull":
			output.Info("Peer requesting pull...")
			out, _ := runner.RunCapture(venvPython, "-c", syncExportScript(projectDir, dataDir))
			resp, _ = json.Marshal(map[string]any{"status": 200, "body": json.RawMessage(out)})
			output.Success("Sent data to peer")

		case "/sync/push":
			output.Info("Peer pushing data...")
			// Write body to temp file, import
			tmpFile := dataDir + "/.sync_incoming.json"
			os.WriteFile(tmpFile, req.Body, 0644)
			out, _ := runner.RunCapture(venvPython, "-c", syncImportScript(projectDir, dataDir, tmpFile))
			os.Remove(tmpFile)
			resp, _ = json.Marshal(map[string]any{"status": 200, "body": json.RawMessage(out)})
			output.Success("Merged peer data: " + out)

		case "/sync/status":
			out, _ := runner.RunCapture(venvPython, "-c", syncStatusScript(projectDir, dataDir))
			resp, _ = json.Marshal(map[string]any{"status": 200, "body": json.RawMessage(out)})

		default:
			resp, _ = json.Marshal(map[string]any{"status": 404})
		}

		conn.Write(ctx, websocket.MessageText, resp)
	}

	fmt.Println("  Connection closed.")
}

func syncPull(args []string) {
	if len(args) == 0 {
		output.Fail("Expected: imprint sync <room-id> --pin <pin>  (or <host>/<id> for a custom relay)")
	}
	target := args[0]

	pin := ""
	for i, arg := range args {
		if arg == "--pin" && i+1 < len(args) {
			pin = args[i+1]
		}
	}
	if pin == "" {
		output.Fail("--pin <pin> is required (shown on the provider machine)")
	}

	scheme, host, roomID := parseSyncTarget(target)
	if roomID == "" {
		output.Fail("Missing room ID. Expected: <room-id> or <host>/<room-id>")
	}

	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Run 'imprint setup' first")
	}

	fingerprint := loadOrCreateFingerprint(dataDir)
	hostname, _ := os.Hostname()
	user := os.Getenv("USER")
	if user == "" {
		user = os.Getenv("USERNAME")
	}

	wsURL := fmt.Sprintf("%s://%s/%s?role=consumer", scheme, host, roomID)

	fmt.Println()
	output.Header("═══ Imprint Sync ═══")
	output.Info("Connecting to " + scheme + "://" + host + "/" + roomID + "...")

	ctx := context.Background()
	conn, _, err := websocket.Dial(ctx, wsURL, nil)
	if err != nil {
		output.Fail("Cannot connect: " + err.Error())
	}
	defer conn.Close(websocket.StatusNormalClosure, "done")

	output.Success("Connected to peer")

	// Handshake: send HELLO with identity + PIN
	output.Info("Sending handshake...")
	identity := deviceIdentity{
		Hostname:    hostname,
		User:        user,
		OS:          runtime.GOOS,
		Fingerprint: fingerprint,
	}
	if err := consumerHandshake(ctx, conn, identity, pin); err != nil {
		output.Fail(err.Error())
	}
	output.Success("Handshake accepted")

	// Step 1: Pull from remote
	output.Info("Pulling remote data...")
	pullReq, _ := json.Marshal(map[string]string{"method": "GET", "path": "/sync/pull"})
	conn.Write(ctx, websocket.MessageText, pullReq)

	_, pullResp, err := conn.Read(ctx)
	if err != nil {
		output.Fail("Pull failed: " + err.Error())
	}

	var pullResult struct {
		Status int             `json:"status"`
		Body   json.RawMessage `json:"body"`
	}
	json.Unmarshal(pullResp, &pullResult)

	// Merge remote data into local
	tmpFile := dataDir + "/.sync_incoming.json"
	os.WriteFile(tmpFile, pullResult.Body, 0644)
	mergeOut, _ := runner.RunCapture(venvPython, "-c", syncImportScript(projectDir, dataDir, tmpFile))
	os.Remove(tmpFile)
	output.Success("Merged remote → local: " + mergeOut)

	// Step 2: Push local data to remote
	output.Info("Pushing local data...")
	localData, _ := runner.RunCapture(venvPython, "-c", syncExportScript(projectDir, dataDir))
	pushReq, _ := json.Marshal(map[string]any{
		"method": "POST",
		"path":   "/sync/push",
		"body":   json.RawMessage(localData),
	})
	conn.Write(ctx, websocket.MessageText, pushReq)

	_, pushResp, err := conn.Read(ctx)
	if err != nil {
		output.Warn("Push failed: " + err.Error())
	} else {
		var pushResult struct {
			Status int             `json:"status"`
			Body   json.RawMessage `json:"body"`
		}
		json.Unmarshal(pushResp, &pushResult)
		output.Success("Pushed local → remote: " + string(pushResult.Body))
	}

	fmt.Println()
	output.Header("═══ Sync Complete ═══")
	fmt.Println()
}

func syncExportScript(projectDir, dataDir string) string {
	return fmt.Sprintf(`
import os, sys, json
sys.path.insert(0, %q)
os.environ["IMPRINT_DATA_DIR"] = %q
from imprint import config, vectorstore as vs

client, coll = vs._ensure_collection()
info = client.get_collection(coll)
if (info.points_count or 0) == 0:
    print("[]")
    sys.exit(0)

records = []
for pl in vs._scroll_all([
    "_mid", "content", "project", "type", "tags", "source",
    "chunk_index", "source_mtime", "timestamp",
]):
    records.append({
        "id": pl.get("_mid", ""),
        "content": pl.get("content", ""),
        "project": pl.get("project", ""),
        "type": pl.get("type", ""),
        "tags": pl.get("tags", {}),
        "source": pl.get("source", ""),
        "chunk_index": pl.get("chunk_index", 0),
        "source_mtime": pl.get("source_mtime", 0),
        "timestamp": pl.get("timestamp", 0),
    })

print(json.dumps(records))
`, projectDir, dataDir)
}

func syncImportScript(projectDir, dataDir, tmpFile string) string {
	return fmt.Sprintf(`
import os, sys, json
sys.path.insert(0, %q)
os.environ["IMPRINT_DATA_DIR"] = %q
from imprint import vectorstore as vs

with open(%q) as f:
    records = json.load(f)

if not records:
    print(json.dumps({"inserted": 0, "skipped": 0}))
    sys.exit(0)

inserted = 0
skipped = 0

# store_batch chunks by default flush size, so pass everything in at once.
ins, sk = vs.store_batch([
    {
        "content": r["content"],
        "project": r.get("project", ""),
        "type": r.get("type", ""),
        "tags": r.get("tags", {}),
        "source": r.get("source", ""),
        "chunk_index": r.get("chunk_index", 0),
        "source_mtime": r.get("source_mtime", 0),
    }
    for r in records
])
inserted = ins
skipped = sk

print(json.dumps({"inserted": inserted, "skipped": skipped, "total": len(records)}))
`, projectDir, dataDir, tmpFile)
}

func syncStatusScript(projectDir, dataDir string) string {
	return fmt.Sprintf(`
import os, sys, json
sys.path.insert(0, %q)
os.environ["IMPRINT_DATA_DIR"] = %q
from imprint import vectorstore as vs
s = vs.status()
print(json.dumps(s))
`, projectDir, dataDir)
}

// parseRelayHost normalises a user-supplied --relay value into (scheme, host).
// Rules:
//   - empty → ("wss", defaultRelayHost)
//   - "wss://foo" or "ws://foo" → explicit scheme honoured
//   - bare "foo" → "ws" if localhost/127.*, else "wss"
func parseRelayHost(raw string) (scheme, host string) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "wss", defaultRelayHost
	}
	if strings.HasPrefix(raw, "wss://") {
		return "wss", strings.TrimPrefix(raw, "wss://")
	}
	if strings.HasPrefix(raw, "ws://") {
		return "ws", strings.TrimPrefix(raw, "ws://")
	}
	if strings.HasPrefix(raw, "http://") {
		return "ws", strings.TrimPrefix(raw, "http://")
	}
	if strings.HasPrefix(raw, "https://") {
		return "wss", strings.TrimPrefix(raw, "https://")
	}
	if strings.HasPrefix(raw, "localhost") || strings.HasPrefix(raw, "127.") {
		return "ws", raw
	}
	return "wss", raw
}

// parseSyncTarget splits a consumer-side target into (scheme, host, roomID).
// Accepts:
//   - bare "<roomID>"                       → default host + scheme
//   - "<host>/<roomID>"                     → host, auto scheme
//   - "wss://<host>/<roomID>" / "ws://..."  → explicit scheme
func parseSyncTarget(raw string) (scheme, host, roomID string) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "wss", defaultRelayHost, ""
	}

	// Strip and remember an explicit scheme.
	explicit := ""
	switch {
	case strings.HasPrefix(raw, "wss://"):
		explicit = "wss"
		raw = strings.TrimPrefix(raw, "wss://")
	case strings.HasPrefix(raw, "ws://"):
		explicit = "ws"
		raw = strings.TrimPrefix(raw, "ws://")
	case strings.HasPrefix(raw, "https://"):
		explicit = "wss"
		raw = strings.TrimPrefix(raw, "https://")
	case strings.HasPrefix(raw, "http://"):
		explicit = "ws"
		raw = strings.TrimPrefix(raw, "http://")
	}

	if !strings.Contains(raw, "/") {
		// Bare room ID — use the default host.
		scheme = "wss"
		if explicit != "" {
			scheme = explicit
		}
		return scheme, defaultRelayHost, raw
	}

	parts := strings.SplitN(raw, "/", 2)
	host, roomID = parts[0], parts[1]
	if explicit != "" {
		scheme = explicit
	} else if strings.HasPrefix(host, "localhost") || strings.HasPrefix(host, "127.") {
		scheme = "ws"
	} else {
		scheme = "wss"
	}
	return scheme, host, roomID
}

// writeHandshakeResp sends a status/body response back on the handshake conn.
func writeHandshakeResp(ctx context.Context, conn *websocket.Conn, status int, body string) error {
	resp, _ := json.Marshal(map[string]any{
		"status": status,
		"body":   json.RawMessage(fmt.Sprintf("%q", body)),
	})
	return conn.Write(ctx, websocket.MessageText, resp)
}

// providerHandshake reads HELLO, validates PIN, runs decide() for unknown
// devices, persists trust if requested, and writes a status reply.
// Returns the accepted hello on success.
func providerHandshake(
	ctx context.Context,
	conn *websocket.Conn,
	pin string,
	dataDir string,
	decide func(helloRequest) (accept bool, trust bool),
) (helloRequest, error) {
	_, data, err := conn.Read(ctx)
	if err != nil {
		return helloRequest{}, fmt.Errorf("read HELLO: %w", err)
	}

	var hello helloRequest
	if err := json.Unmarshal(data, &hello); err != nil || hello.Method != "HELLO" {
		_ = writeHandshakeResp(ctx, conn, 400, "handshake required")
		return hello, fmt.Errorf("peer skipped handshake")
	}
	if !pinEqual(hello.PIN, pin) {
		_ = writeHandshakeResp(ctx, conn, 403, "invalid PIN")
		return hello, fmt.Errorf("wrong PIN from %s (%s)", hello.Hostname, hello.Fingerprint)
	}

	accept, trustNow := decide(hello)
	if !accept {
		_ = writeHandshakeResp(ctx, conn, 403, "rejected by user")
		return hello, fmt.Errorf("rejected by user")
	}

	if trustNow {
		if err := saveTrustedDevice(dataDir, hello); err != nil {
			output.Warn("Could not persist trust: " + err.Error())
		} else {
			output.Success("Device added to trusted list")
		}
	}

	if err := writeHandshakeResp(ctx, conn, 200, "ok"); err != nil {
		return hello, fmt.Errorf("write response: %w", err)
	}
	return hello, nil
}

// consumerHandshake sends HELLO with identity+PIN and waits for approval.
// Returns nil iff the provider responded with status 200.
func consumerHandshake(
	ctx context.Context,
	conn *websocket.Conn,
	id deviceIdentity,
	pin string,
) error {
	msg, _ := json.Marshal(helloRequest{
		Method:      "HELLO",
		Hostname:    id.Hostname,
		User:        id.User,
		OS:          id.OS,
		Fingerprint: id.Fingerprint,
		PIN:         pin,
	})
	if err := conn.Write(ctx, websocket.MessageText, msg); err != nil {
		return fmt.Errorf("send HELLO: %w", err)
	}

	_, resp, err := conn.Read(ctx)
	if err != nil {
		return fmt.Errorf("read handshake response: %w", err)
	}
	var result struct {
		Status int             `json:"status"`
		Body   json.RawMessage `json:"body"`
	}
	if err := json.Unmarshal(resp, &result); err != nil {
		return fmt.Errorf("parse handshake response: %w", err)
	}
	if result.Status != 200 {
		return fmt.Errorf("handshake rejected (status %d): %s", result.Status, string(result.Body))
	}
	return nil
}

// pinCharset — uppercase + lowercase + digits (62 chars).
const pinCharset = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

// generatePIN returns an 8-char random alphanumeric PIN.
func generatePIN() string {
	out := make([]byte, 8)
	max := big.NewInt(int64(len(pinCharset)))
	for i := range out {
		n, err := rand.Int(rand.Reader, max)
		if err != nil {
			// Extremely unlikely; fall back to a deterministic seed to avoid panic.
			out[i] = pinCharset[i%len(pinCharset)]
			continue
		}
		out[i] = pinCharset[n.Int64()]
	}
	return string(out)
}

// pinEqual compares two PINs in constant time.
func pinEqual(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}

// loadOrCreateFingerprint returns a stable 8-char hex device identifier,
// creating it on first call.
func loadOrCreateFingerprint(dataDir string) string {
	path := filepath.Join(dataDir, "device_id.txt")
	if data, err := os.ReadFile(path); err == nil {
		fp := strings.TrimSpace(string(data))
		if fp != "" {
			return fp
		}
	}
	buf := make([]byte, 4)
	rand.Read(buf)
	fp := hex.EncodeToString(buf)
	_ = os.MkdirAll(dataDir, 0755)
	_ = os.WriteFile(path, []byte(fp+"\n"), 0644)
	return fp
}

// loadTrustedDevices returns a map keyed by fingerprint.
func loadTrustedDevices(dataDir string) map[string]trustedDevice {
	path := filepath.Join(dataDir, "trusted_devices.json")
	out := map[string]trustedDevice{}
	data, err := os.ReadFile(path)
	if err != nil {
		return out
	}
	var list []trustedDevice
	if err := json.Unmarshal(data, &list); err != nil {
		return out
	}
	for _, d := range list {
		out[d.Fingerprint] = d
	}
	return out
}

// saveTrustedDevice appends (or refreshes) a device in the trust list.
func saveTrustedDevice(dataDir string, hello helloRequest) error {
	path := filepath.Join(dataDir, "trusted_devices.json")
	existing := loadTrustedDevices(dataDir)
	existing[hello.Fingerprint] = trustedDevice{
		Fingerprint: hello.Fingerprint,
		Hostname:    hello.Hostname,
		Added:       time.Now().Unix(),
	}
	list := make([]trustedDevice, 0, len(existing))
	for _, d := range existing {
		list = append(list, d)
	}
	data, err := json.MarshalIndent(list, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dataDir, 0755); err != nil {
		return err
	}
	return os.WriteFile(path, data, 0644)
}

// promptAccept blocks on the provider's TTY. Returns (accept, trust).
// "y" = accept once, "t" = accept + persist trust, "n"/"" = reject.
func promptAccept(hello helloRequest) (bool, bool) {
	fmt.Println()
	output.Header("═══ Incoming Sync Request ═══")
	fmt.Printf("  Device:   %s\n", hello.Hostname)
	fmt.Printf("  User:     %s\n", hello.User)
	fmt.Printf("  OS:       %s\n", hello.OS)
	fmt.Printf("  ID:       %s\n", hello.Fingerprint)
	fmt.Println()
	fmt.Print("  Accept? [y]es / [n]o / [t]rust & accept: ")

	reader := bufio.NewReader(os.Stdin)
	line, err := reader.ReadString('\n')
	if err != nil {
		return false, false
	}
	switch strings.ToLower(strings.TrimSpace(line)) {
	case "y", "yes":
		return true, false
	case "t", "trust":
		return true, true
	default:
		return false, false
	}
}
