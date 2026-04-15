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
	"os/exec"
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

// defaultBatchSize is the default number of records per streamed batch.
const defaultBatchSize = 500

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

// streamMsg is the wire format for streamed pull/push payloads.
// Newline-delimited JSON of this shape flows over the WS and to/from Python.
type streamMsg struct {
	Kind     string            `json:"kind"`               // "meta" | "batch" | "done" | "error" | "progress" | "summary"
	Datasets map[string]int    `json:"datasets,omitempty"` // on meta: dataset name → total count
	Dataset  string            `json:"dataset,omitempty"`  // on batch/progress: "memories" | "facts"
	Seq      int               `json:"seq,omitempty"`
	Records  []json.RawMessage `json:"records,omitempty"`
	Message  string            `json:"message,omitempty"` // on error
	Done     int               `json:"done,omitempty"`    // on progress
	Total    int               `json:"total,omitempty"`   // on progress
	Stats    json.RawMessage   `json:"stats,omitempty"`   // on summary
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
	// Disable 32 KiB default — sync payloads may be MBs per batch.
	conn.SetReadLimit(-1)

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

		switch req.Path {
		case "/sync/pull":
			output.Info("Peer requesting pull...")
			if err := streamExport(ctx, conn, venvPython, projectDir, dataDir, req.Body, "Sending"); err != nil {
				output.Warn("Export failed: " + err.Error())
			} else {
				output.Success("Export complete")
			}

		case "/sync/push":
			output.Info("Peer pushing data...")
			result, err := streamImport(ctx, conn, venvPython, projectDir, dataDir, "Receiving")
			if err != nil {
				writeEnvelope(ctx, conn, 500, []byte(fmt.Sprintf("%q", err.Error())))
				output.Warn("Import failed: " + err.Error())
			} else {
				writeEnvelope(ctx, conn, 200, []byte(result))
				output.Success("Merged peer data: " + result)
			}

		case "/sync/status":
			out, _ := runner.RunCapture(venvPython, "-c", syncStatusScript(projectDir, dataDir))
			writeEnvelope(ctx, conn, 200, []byte(out))

		default:
			writeEnvelope(ctx, conn, 404, []byte(`"unknown path"`))
		}
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
	conn.SetReadLimit(-1)

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
	pullReq, _ := json.Marshal(map[string]any{
		"method": "GET",
		"path":   "/sync/pull",
		"body":   map[string]int{"batch_size": defaultBatchSize},
	})
	if err := conn.Write(ctx, websocket.MessageText, pullReq); err != nil {
		output.Fail("Pull request failed: " + err.Error())
	}
	if result, err := streamImport(ctx, conn, venvPython, projectDir, dataDir, "Receiving"); err != nil {
		output.Fail("Pull failed: " + err.Error())
	} else {
		output.Success("Merged remote → local: " + result)
	}

	// Step 2: Push local data to remote
	output.Info("Pushing local data...")
	pushReq, _ := json.Marshal(map[string]any{
		"method": "POST",
		"path":   "/sync/push",
		"body":   map[string]int{"batch_size": defaultBatchSize},
	})
	if err := conn.Write(ctx, websocket.MessageText, pushReq); err != nil {
		output.Fail("Push request failed: " + err.Error())
	}
	if err := streamExport(ctx, conn, venvPython, projectDir, dataDir, nil, "Sending"); err != nil {
		output.Warn("Push send failed: " + err.Error())
	}

	// Final ack from provider
	_, ack, err := conn.Read(ctx)
	if err != nil {
		output.Warn("Push ack failed: " + err.Error())
	} else {
		var ackResp struct {
			Status int             `json:"status"`
			Body   json.RawMessage `json:"body"`
		}
		json.Unmarshal(ack, &ackResp)
		output.Success("Pushed local → remote: " + string(ackResp.Body))
	}

	fmt.Println()
	output.Header("═══ Sync Complete ═══")
	fmt.Println()
}

// -----------------------------------------------------------------------------
// Streaming helpers
// -----------------------------------------------------------------------------

// writeEnvelope sends {"status":N,"body":<raw>} on the WS.
func writeEnvelope(ctx context.Context, conn *websocket.Conn, status int, body []byte) error {
	if len(body) == 0 {
		body = []byte("null")
	}
	out, _ := json.Marshal(map[string]any{
		"status": status,
		"body":   json.RawMessage(body),
	})
	return conn.Write(ctx, websocket.MessageText, out)
}

// streamExport runs the Python exporter and forwards each newline-delimited
// JSON line as a WebSocket message. Shows a per-dataset progress counter.
// reqBody may carry {"batch_size":N}; defaults to defaultBatchSize.
func streamExport(
	ctx context.Context,
	conn *websocket.Conn,
	venvPython, projectDir, dataDir string,
	reqBody json.RawMessage,
	label string,
) error {
	batchSize := defaultBatchSize
	if len(reqBody) > 0 {
		var opts struct {
			BatchSize int `json:"batch_size"`
		}
		_ = json.Unmarshal(reqBody, &opts)
		if opts.BatchSize > 0 {
			batchSize = opts.BatchSize
		}
	}

	cmd := exec.CommandContext(ctx, venvPython, "-c", syncExportStreamScript(projectDir, dataDir, batchSize))
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("stdout pipe: %w", err)
	}
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start exporter: %w", err)
	}

	prog := newProgress(label)
	sent := map[string]int{}
	totals := map[string]int{}

	scanner := bufio.NewScanner(stdout)
	// Allow very large lines (one batch = up to ~batchSize * record_size).
	scanner.Buffer(make([]byte, 64*1024), 512*1024*1024)

	for scanner.Scan() {
		line := scanner.Bytes()
		// Copy because we forward async and scanner reuses the buffer.
		payload := make([]byte, len(line))
		copy(payload, line)

		if err := conn.Write(ctx, websocket.MessageText, payload); err != nil {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
			return fmt.Errorf("ws write: %w", err)
		}

		var m streamMsg
		if err := json.Unmarshal(payload, &m); err != nil {
			continue
		}
		switch m.Kind {
		case "meta":
			for ds, n := range m.Datasets {
				totals[ds] = n
			}
			prog.Meta(totals)
		case "batch":
			sent[m.Dataset] += len(m.Records)
			prog.Update(m.Dataset, sent[m.Dataset], totals[m.Dataset])
		case "done":
			prog.Finish(sent, totals)
		case "error":
			prog.Finish(sent, totals)
			_ = cmd.Wait()
			return fmt.Errorf("exporter error: %s", m.Message)
		}
	}
	if err := scanner.Err(); err != nil {
		_ = cmd.Wait()
		return fmt.Errorf("scan: %w", err)
	}
	if err := cmd.Wait(); err != nil {
		return fmt.Errorf("exporter exit: %w", err)
	}
	return nil
}

// streamImport reads WS messages until {"kind":"done"} and pipes each batch
// to the Python importer over stdin. Returns the importer's final JSON summary.
// streamImport runs in two clearly separated phases so the user always knows
// what's happening:
//
//  1. RECEIVE — read every WS message into a temp file. Progress reflects
//     bytes/records actually pulled off the wire. The peer can disconnect as
//     soon as it finishes sending; nothing is blocked on local processing.
//
//  2. PROCESS — pre-warm the embedding model (with feedback), then spawn the
//     Python importer pointed at the temp file. The importer emits progress
//     messages per batch so the second progress bar reflects real local work
//     (embed + upsert), not network receive.
//
// Returns the importer's final JSON summary (memories/facts inserted+skipped).
func streamImport(
	ctx context.Context,
	conn *websocket.Conn,
	venvPython, projectDir, dataDir string,
	label string,
) (string, error) {
	// ── Phase 1: receive everything to disk ────────────────────────────────
	tmp, err := os.CreateTemp("", "imprint-sync-*.jsonl")
	if err != nil {
		return "", fmt.Errorf("temp file: %w", err)
	}
	tmpPath := tmp.Name()
	defer os.Remove(tmpPath)

	prog := newProgress(label)
	received := map[string]int{}
	totals := map[string]int{}

receiveLoop:
	for {
		_, data, err := conn.Read(ctx)
		if err != nil {
			_ = tmp.Close()
			return "", fmt.Errorf("ws read: %w", err)
		}
		if _, err := tmp.Write(append(data, '\n')); err != nil {
			_ = tmp.Close()
			return "", fmt.Errorf("buffer write: %w", err)
		}

		var m streamMsg
		if err := json.Unmarshal(data, &m); err != nil {
			continue
		}
		switch m.Kind {
		case "meta":
			for ds, n := range m.Datasets {
				totals[ds] = n
			}
			prog.Meta(totals)
		case "batch":
			received[m.Dataset] += len(m.Records)
			prog.Update(m.Dataset, received[m.Dataset], totals[m.Dataset])
		case "done":
			prog.Finish(received, totals)
			break receiveLoop
		case "error":
			prog.Finish(received, totals)
			_ = tmp.Close()
			return "", fmt.Errorf("peer error: %s", m.Message)
		}
	}
	if err := tmp.Close(); err != nil {
		return "", fmt.Errorf("close temp file: %w", err)
	}

	// Nothing inbound? Skip the expensive model warmup entirely.
	if totals["memories"] == 0 && totals["facts"] == 0 {
		return `{"memories":{"inserted":0,"skipped":0},"facts":{"inserted":0,"skipped":0}}`, nil
	}

	// ── Phase 2a: warm the embedding model with explicit UX ────────────────
	if totals["memories"] > 0 {
		if err := warmupEmbeddings(ctx, venvPython, projectDir, dataDir); err != nil {
			return "", fmt.Errorf("load embedding model: %w", err)
		}
	}

	// ── Phase 2b: process buffered batches with per-batch progress ─────────
	// `-u` forces unbuffered stdout so progress messages reach Go immediately
	// even though the importer's stdout is a pipe (block-buffered by default).
	cmd := exec.CommandContext(ctx, venvPython, "-u", "-c", syncImportStreamScript(projectDir, dataDir), tmpPath)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return "", fmt.Errorf("stdout pipe: %w", err)
	}
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		return "", fmt.Errorf("start importer: %w", err)
	}

	prog2 := newProgress("Storing")
	prog2.Meta(totals)
	processed := map[string]int{}
	var summary string

	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 64*1024), 16*1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		var m streamMsg
		if err := json.Unmarshal(line, &m); err != nil {
			continue
		}
		switch m.Kind {
		case "progress":
			processed[m.Dataset] = m.Done
			prog2.Update(m.Dataset, m.Done, m.Total)
		case "summary":
			summary = string(m.Stats)
		case "error":
			prog2.Finish(processed, totals)
			_ = cmd.Wait()
			return "", fmt.Errorf("importer error: %s", m.Message)
		}
	}
	prog2.Finish(processed, totals)
	if err := scanner.Err(); err != nil {
		_ = cmd.Wait()
		return "", fmt.Errorf("importer scan: %w", err)
	}
	if err := cmd.Wait(); err != nil {
		return "", fmt.Errorf("importer exit: %w", err)
	}
	if summary == "" {
		return "", fmt.Errorf("importer produced no summary")
	}
	return summary, nil
}

// warmupEmbeddings loads the embedding model so the user gets clear UX
// feedback ("Loading embedding model...") rather than a silent ~30s pause
// inside the importer when the first batch hits store_batch().
func warmupEmbeddings(ctx context.Context, venvPython, projectDir, dataDir string) error {
	output.Info("Loading embedding model (one-time download if not cached, ~500MB)...")
	cmd := exec.CommandContext(ctx, venvPython, "-c", syncEmbeddingWarmupScript(projectDir, dataDir))
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return err
	}
	output.Success("Embedding model ready")
	return nil
}

// -----------------------------------------------------------------------------
// Progress UX
// -----------------------------------------------------------------------------

type progressCtx struct {
	label string
	tty   bool
	start time.Time
}

func newProgress(label string) *progressCtx {
	info, err := os.Stdout.Stat()
	tty := err == nil && (info.Mode()&os.ModeCharDevice) != 0
	return &progressCtx{label: label, tty: tty, start: time.Now()}
}

// Meta prints the up-front totals line.
func (p *progressCtx) Meta(totals map[string]int) {
	parts := []string{}
	for _, ds := range []string{"memories", "facts"} {
		if n, ok := totals[ds]; ok {
			parts = append(parts, fmt.Sprintf("%d %s", n, ds))
		}
	}
	if len(parts) == 0 {
		return
	}
	output.Info(fmt.Sprintf("%s: %s", p.label, strings.Join(parts, ", ")))
}

// Update prints or overwrites the progress line for a dataset, including
// elapsed time and an ETA derived from the current rate. ETA only appears
// once a few records are done so we don't divide by zero or print wild
// estimates from the first sample.
func (p *progressCtx) Update(dataset string, current, total int) {
	if total <= 0 {
		return
	}
	elapsed := time.Since(p.start)
	suffix := fmt.Sprintf(" [%s]", formatDur(elapsed))
	if current > 0 && current < total {
		rate := float64(current) / elapsed.Seconds()
		if rate > 0 {
			remaining := time.Duration(float64(total-current)/rate) * time.Second
			suffix = fmt.Sprintf(" [%s elapsed, ~%s left]", formatDur(elapsed), formatDur(remaining))
		}
	}
	msg := fmt.Sprintf("  %s %s: %d/%d%s", p.label, dataset, current, total, suffix)
	if p.tty {
		fmt.Printf("\r\033[K%s", msg)
	} else {
		fmt.Println(msg)
	}
}

// formatDur renders a duration as Hh M m or Mm S s, dropping the leading
// units when zero so the progress line stays compact.
func formatDur(d time.Duration) string {
	if d < time.Second {
		return "0s"
	}
	total := int(d.Seconds())
	h := total / 3600
	m := (total % 3600) / 60
	s := total % 60
	if h > 0 {
		return fmt.Sprintf("%dh%dm", h, m)
	}
	if m > 0 {
		return fmt.Sprintf("%dm%ds", m, s)
	}
	return fmt.Sprintf("%ds", s)
}

// Finish closes out the progress (newline on TTY) and prints a summary.
func (p *progressCtx) Finish(done, totals map[string]int) {
	if p.tty {
		fmt.Print("\r\033[K")
	}
	dur := time.Since(p.start).Round(time.Millisecond)
	parts := []string{}
	for _, ds := range []string{"memories", "facts"} {
		if t, ok := totals[ds]; ok && t > 0 {
			parts = append(parts, fmt.Sprintf("%d/%d %s", done[ds], t, ds))
		}
	}
	if len(parts) == 0 {
		output.Success(fmt.Sprintf("%s complete (%s)", p.label, dur))
		return
	}
	output.Success(fmt.Sprintf("%s complete: %s (%s)", p.label, strings.Join(parts, ", "), dur))
}

// -----------------------------------------------------------------------------
// Python scripts
// -----------------------------------------------------------------------------

// syncExportStreamScript emits newline-delimited JSON:
//   {"kind":"meta","datasets":{"memories":N,"facts":M}}
//   {"kind":"batch","dataset":"memories","seq":i,"records":[...]}   (xN/BATCH)
//   {"kind":"batch","dataset":"facts","seq":i,"records":[...]}       (xM/BATCH)
//   {"kind":"done"}
func syncExportStreamScript(projectDir, dataDir string, batchSize int) string {
	return fmt.Sprintf(`
import os, sys, json
sys.path.insert(0, %q)
os.environ["IMPRINT_DATA_DIR"] = %q
from imprint import vectorstore as vs
try:
    from imprint import imprint_graph as kg
except Exception:
    kg = None

BATCH = %d

def emit(obj):
    sys.stdout.write(json.dumps(obj, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()

# Counts
client, coll = vs._ensure_collection()
info = client.get_collection(coll)
mem_total = int(info.points_count or 0)

fact_total = 0
if kg is not None:
    try:
        conn = kg._get_conn()
        fact_total = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    except Exception:
        fact_total = 0

emit({"kind": "meta", "datasets": {"memories": mem_total, "facts": fact_total}})

# Stream memories
if mem_total > 0:
    buf = []
    seq = 0
    for pl in vs._scroll_all([
        "_mid", "content", "project", "type", "tags", "source",
        "chunk_index", "source_mtime", "timestamp",
    ]):
        buf.append({
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
        if len(buf) >= BATCH:
            seq += 1
            emit({"kind": "batch", "dataset": "memories", "seq": seq, "records": buf})
            buf = []
    if buf:
        seq += 1
        emit({"kind": "batch", "dataset": "memories", "seq": seq, "records": buf})

# Stream facts (knowledge graph)
if fact_total > 0 and kg is not None:
    conn = kg._get_conn()
    rows = conn.execute(
        "SELECT subject, predicate, object, valid_from, ended, source FROM facts"
    ).fetchall()
    records = [
        {
            "subject": r["subject"],
            "predicate": r["predicate"],
            "object": r["object"],
            "valid_from": r["valid_from"],
            "ended": r["ended"],
            "source": r["source"] or "",
        }
        for r in rows
    ]
    for i in range(0, len(records), BATCH):
        seq = i // BATCH + 1
        emit({"kind": "batch", "dataset": "facts", "seq": seq, "records": records[i:i + BATCH]})

emit({"kind": "done"})
`, projectDir, dataDir, batchSize)
}

// syncEmbeddingWarmupScript loads the embedding model so the first call to
// store_batch in the importer doesn't pay the download/load cost mid-stream.
// On a cold cache the HF library will print its own download progress; on a
// warm cache this returns in ~1s.
func syncEmbeddingWarmupScript(projectDir, dataDir string) string {
	return fmt.Sprintf(`
import os, sys
sys.path.insert(0, %q)
os.environ["IMPRINT_DATA_DIR"] = %q
from imprint import embeddings
embeddings._load()
# Tiny dummy embed forces ORT graph init so the first real batch is fast.
embeddings._embed_raw(["warmup"])
`, projectDir, dataDir)
}

// syncImportStreamScript reads newline-delimited JSON from a file (path
// passed as argv[1]), dispatches batches to store_batch / facts-insert, and
// emits per-batch progress + a final summary to stdout. Reading from a file
// (rather than stdin) lets the caller decouple network receive from local
// processing — the user gets two separate, accurate progress bars.
//
// Wire format on stdout (newline-delimited JSON):
//
//	{"kind":"progress","dataset":"memories","done":N,"total":T}
//	{"kind":"summary","stats":{...}}
func syncImportStreamScript(projectDir, dataDir string) string {
	return fmt.Sprintf(`
import os, sys, json
sys.path.insert(0, %q)
os.environ["IMPRINT_DATA_DIR"] = %q
from imprint import vectorstore as vs
try:
    from imprint import imprint_graph as kg
except Exception:
    kg = None

if len(sys.argv) < 2:
    sys.stderr.write("importer: missing input file path\n")
    sys.exit(2)

src_path = sys.argv[1]

# Sub-batch each 500-record sync batch into smaller chunks so the user sees
# progress every few seconds instead of every ~90s. The internal embed batch
# is 16 (CPU) / 2 (GPU); 32 keeps overhead low while updating the progress
# bar 16x more often than processing the full sync batch in one shot.
EMBED_CHUNK = 32

stats = {
    "memories": {"inserted": 0, "skipped": 0},
    "facts": {"inserted": 0, "skipped": 0},
}
totals = {"memories": 0, "facts": 0}
initial_emitted = {"memories": False, "facts": False}

def emit(obj):
    sys.stdout.write(json.dumps(obj, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()

def progress(dataset):
    done = stats[dataset]["inserted"] + stats[dataset]["skipped"]
    emit({"kind": "progress", "dataset": dataset, "done": done, "total": totals.get(dataset, 0)})

def ensure_initial(dataset):
    # Emit a 0/total progress as soon as we know we're about to work on a
    # dataset, so the user sees the bar immediately even if the first chunk
    # takes a while.
    if initial_emitted[dataset]:
        return
    initial_emitted[dataset] = True
    progress(dataset)

def import_memories(records):
    if not records:
        return
    ensure_initial("memories")
    payloads = [
        {
            "content": r.get("content", ""),
            "project": r.get("project", ""),
            "type": r.get("type", ""),
            "tags": r.get("tags", {}),
            "source": r.get("source", ""),
            "chunk_index": r.get("chunk_index", 0),
            "source_mtime": r.get("source_mtime", 0),
        }
        for r in records
    ]
    for i in range(0, len(payloads), EMBED_CHUNK):
        ins, sk = vs.store_batch(payloads[i:i + EMBED_CHUNK])
        stats["memories"]["inserted"] += int(ins)
        stats["memories"]["skipped"] += int(sk)
        progress("memories")

def import_facts(records):
    if not records:
        return
    ensure_initial("facts")
    if kg is None:
        stats["facts"]["skipped"] += len(records)
        progress("facts")
        return
    conn = kg._get_conn()
    ins = 0
    sk = 0
    for r in records:
        row = conn.execute(
            "SELECT 1 FROM facts WHERE subject=? AND predicate=? AND object=? AND valid_from=?",
            (r.get("subject", ""), r.get("predicate", ""), r.get("object", ""), r.get("valid_from", 0)),
        ).fetchone()
        if row:
            sk += 1
            continue
        conn.execute(
            "INSERT INTO facts (subject, predicate, object, valid_from, ended, source) VALUES (?,?,?,?,?,?)",
            (
                r.get("subject", ""),
                r.get("predicate", ""),
                r.get("object", ""),
                r.get("valid_from", 0),
                r.get("ended"),
                r.get("source", "") or "",
            ),
        )
        ins += 1
    conn.commit()
    stats["facts"]["inserted"] += ins
    stats["facts"]["skipped"] += sk
    progress("facts")

try:
    with open(src_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            kind = msg.get("kind")
            if kind == "meta":
                ds = msg.get("datasets") or {}
                for k, v in ds.items():
                    totals[k] = int(v)
                continue
            if kind == "done":
                break
            if kind != "batch":
                continue
            dataset = msg.get("dataset", "memories")
            records = msg.get("records") or []
            if dataset == "memories":
                import_memories(records)
            elif dataset == "facts":
                import_facts(records)
except Exception as exc:
    emit({"kind": "error", "message": str(exc)})
    sys.exit(1)

emit({"kind": "summary", "stats": stats})
`, projectDir, dataDir)
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
