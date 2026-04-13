package cmd

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/signal"
	"strings"

	"github.com/hunter/knowledge/internal/output"
	"github.com/hunter/knowledge/internal/platform"
	"github.com/hunter/knowledge/internal/runner"
	"nhooyr.io/websocket"
)

func Sync(args []string) {
	if len(args) == 0 {
		fmt.Fprintf(os.Stderr, `Usage:
  knowledge sync serve --relay <host>    Expose this machine's KB via relay
  knowledge sync <relay-url>/<id>        Pull from + push to remote machine

Examples:
  knowledge sync serve --relay sync.example.com
  knowledge sync sync.example.com/abc123
`)
		os.Exit(1)
	}

	if args[0] == "serve" {
		syncServe(args[1:])
	} else {
		syncPull(args[0])
	}
}

func syncServe(args []string) {
	relay := ""
	for i, arg := range args {
		if arg == "--relay" && i+1 < len(args) {
			relay = args[i+1]
		}
	}
	if relay == "" {
		output.Fail("--relay <host> is required")
	}

	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Run 'knowledge setup' first")
	}

	// Generate room ID
	idBytes := make([]byte, 4)
	rand.Read(idBytes)
	roomID := hex.EncodeToString(idBytes)

	// Determine WebSocket URL
	scheme := "wss"
	if strings.HasPrefix(relay, "localhost") || strings.HasPrefix(relay, "127.") {
		scheme = "ws"
	}
	wsURL := fmt.Sprintf("%s://%s/%s?role=provider", scheme, relay, roomID)

	fmt.Println()
	output.Header("═══ Knowledge Sync Server ═══")
	output.Info("Connecting to relay: " + relay)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	conn, _, err := websocket.Dial(ctx, wsURL, nil)
	if err != nil {
		output.Fail("Cannot connect to relay: " + err.Error())
	}
	defer conn.Close(websocket.StatusNormalClosure, "done")

	fmt.Println()
	output.Success("Connected! Share this with the other machine:")
	fmt.Printf("\n  knowledge sync %s/%s\n\n", relay, roomID)
	output.Info("Waiting for peer... (Ctrl+C to stop)")

	// Handle interrupt
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt)
	go func() {
		<-sigCh
		fmt.Println("\n  Disconnecting...")
		cancel()
	}()

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

func syncPull(target string) {
	// Parse target: host/id
	parts := strings.SplitN(target, "/", 2)
	if len(parts) != 2 {
		output.Fail("Expected format: <relay-host>/<room-id>")
	}
	relay, roomID := parts[0], parts[1]

	projectDir := platform.FindProjectDir()
	venvPython := platform.VenvPython(projectDir)
	dataDir := platform.DataDir(projectDir)

	if !platform.FileExists(venvPython) {
		output.Fail("Run 'knowledge setup' first")
	}

	scheme := "wss"
	if strings.HasPrefix(relay, "localhost") || strings.HasPrefix(relay, "127.") {
		scheme = "ws"
	}
	wsURL := fmt.Sprintf("%s://%s/%s?role=consumer", scheme, relay, roomID)

	fmt.Println()
	output.Header("═══ Knowledge Sync ═══")
	output.Info("Connecting to " + relay + "/" + roomID + "...")

	ctx := context.Background()
	conn, _, err := websocket.Dial(ctx, wsURL, nil)
	if err != nil {
		output.Fail("Cannot connect: " + err.Error())
	}
	defer conn.Close(websocket.StatusNormalClosure, "done")

	output.Success("Connected to peer")

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
os.environ["KNOWLEDGE_DATA_DIR"] = %q
from knowledgebase import config, vectorstore as vs

client = vs._ensure_collection()
info = client.get_collection(config.QDRANT_COLLECTION)
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
os.environ["KNOWLEDGE_DATA_DIR"] = %q
from knowledgebase import vectorstore as vs

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
os.environ["KNOWLEDGE_DATA_DIR"] = %q
from knowledgebase import vectorstore as vs
s = vs.status()
print(json.dumps(s))
`, projectDir, dataDir)
}
