package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"nhooyr.io/websocket"
)

// newTestRelay spins up relayHandler on an httptest.Server.
// Returns the ws:// base URL (e.g. "ws://127.0.0.1:PORT").
func newTestRelay(t *testing.T) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(relayHandler))
	t.Cleanup(srv.Close)
	u, err := url.Parse(srv.URL)
	if err != nil {
		t.Fatalf("parse httptest URL: %v", err)
	}
	return "ws://" + u.Host
}

// dialRole opens a WebSocket to the relay as the given role.
func dialRole(t *testing.T, base, roomID, role string) *websocket.Conn {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	wsURL := fmt.Sprintf("%s/%s?role=%s", base, roomID, role)
	conn, _, err := websocket.Dial(ctx, wsURL, nil)
	if err != nil {
		t.Fatalf("dial %s: %v", role, err)
	}
	t.Cleanup(func() { conn.Close(websocket.StatusNormalClosure, "") })
	return conn
}

// uniqueRoomID keeps tests from colliding on the package-global rooms map.
var roomCounter int

func nextRoom(t *testing.T) string {
	t.Helper()
	roomCounter++
	return fmt.Sprintf("t%d%d", time.Now().UnixNano(), roomCounter)
}

// TestRelayForwardsLargePayload exercises the fix for the 32 KiB default
// read limit: a multi-MB message must traverse provider → relay → consumer
// without being dropped or truncated.
func TestRelayForwardsLargePayload(t *testing.T) {
	base := newTestRelay(t)
	room := nextRoom(t)

	// 2 MiB payload — well over the old 32 KiB default.
	payload := make([]byte, 2*1024*1024)
	for i := range payload {
		payload[i] = byte(i % 251)
	}

	var got []byte
	done := make(chan struct{})

	go func() {
		defer close(done)
		conn := dialRole(t, base, room, "consumer")
		conn.SetReadLimit(-1)
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_, data, err := conn.Read(ctx)
		if err != nil {
			t.Errorf("consumer read: %v", err)
			return
		}
		got = data
	}()

	// Let the consumer register on the relay before the provider writes.
	time.Sleep(100 * time.Millisecond)

	conn := dialRole(t, base, room, "provider")
	conn.SetReadLimit(-1)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := conn.Write(ctx, websocket.MessageBinary, payload); err != nil {
		t.Fatalf("provider write: %v", err)
	}

	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("consumer hung — read-limit still pinching?")
	}

	if len(got) != len(payload) {
		t.Fatalf("payload size mismatch: got %d, want %d", len(got), len(payload))
	}
	for i := range got {
		if got[i] != payload[i] {
			t.Fatalf("payload byte %d differs: got %d, want %d", i, got[i], payload[i])
		}
	}
}

// TestRelayClosesPeerOnDisconnect covers the secondary bug: when one side
// errors or closes, the relay must close the peer too so the peer does not
// hang on Read until TCP keepalive.
func TestRelayClosesPeerOnDisconnect(t *testing.T) {
	base := newTestRelay(t)
	room := nextRoom(t)

	consumer := dialRole(t, base, room, "consumer")
	consumer.SetReadLimit(-1)

	// Let the consumer register before the provider connects.
	time.Sleep(50 * time.Millisecond)

	provider := dialRole(t, base, room, "provider")
	provider.SetReadLimit(-1)

	// Provider slams the door.
	provider.Close(websocket.StatusNormalClosure, "bye")

	// Consumer Read must return promptly, not block indefinitely.
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	_, _, err := consumer.Read(ctx)
	if err == nil {
		t.Fatal("consumer Read returned nil error — peer close not propagated")
	}
	if ctx.Err() != nil {
		t.Fatalf("consumer Read timed out — relay did not close peer (got ctx err %v)", ctx.Err())
	}
}

func TestRelayRejectsUnknownRole(t *testing.T) {
	base := newTestRelay(t)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	_, resp, err := websocket.Dial(ctx, fmt.Sprintf("%s/%s?role=observer", base, nextRoom(t)), nil)
	if err == nil {
		t.Fatal("dial with an unknown role succeeded")
	}
	if resp == nil || resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("want 400, got %v", resp)
	}
}

// TestRelayRefusesATakenRole: a second consumer must not replace the one a
// session is already talking to.
func TestRelayRefusesATakenRole(t *testing.T) {
	base := newTestRelay(t)
	room := nextRoom(t)
	provider := dialRole(t, base, room, "provider")
	first := dialRole(t, base, room, "consumer")
	intruder := dialRole(t, base, room, "consumer")

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if _, _, err := intruder.Read(ctx); websocket.CloseStatus(err) != websocket.StatusPolicyViolation {
		t.Fatalf("intruder not refused: %v", err)
	}
	if err := provider.Write(ctx, websocket.MessageBinary, []byte("for the first consumer")); err != nil {
		t.Fatalf("provider write: %v", err)
	}
	_, got, err := first.Read(ctx)
	if err != nil || string(got) != "for the first consumer" {
		t.Fatalf("first consumer lost its session: %q, %v", got, err)
	}
}

// TestRelayClosesOversizedMessages bounds what one client can make the
// relay buffer.
func TestRelayClosesOversizedMessages(t *testing.T) {
	base := newTestRelay(t)
	room := nextRoom(t)
	_ = dialRole(t, base, room, "consumer")
	provider := dialRole(t, base, room, "provider")
	provider.SetReadLimit(-1)

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = provider.Write(ctx, websocket.MessageBinary, make([]byte, relayMaxMessage+1))
	if _, _, err := provider.Read(ctx); websocket.CloseStatus(err) != websocket.StatusMessageTooBig {
		t.Fatalf("oversized message not refused: %v", err)
	}
}

// TestRelayShowsBrowsersAPage: a browser on any path gets the page about the
// relay, never the protocol errors meant for sync peers.
func TestRelayShowsBrowsersAPage(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(relayHandler))
	t.Cleanup(srv.Close)
	for path, want := range map[string]int{
		"/":                       http.StatusOK,
		"/asfsafas":               http.StatusNotFound,
		"/7f3a9c21?role=consumer": http.StatusNotFound,
	} {
		resp, err := http.Get(srv.URL + path)
		if err != nil {
			t.Fatalf("GET %s: %v", path, err)
		}
		body, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil {
			t.Fatalf("read %s: %v", path, err)
		}
		if resp.StatusCode != want || resp.Header.Get("Content-Type") != "text/html; charset=utf-8" {
			t.Errorf("GET %s = %d %q, want %d text/html", path, resp.StatusCode, resp.Header.Get("Content-Type"), want)
		}
		if !bytes.Equal(body, relayPage) {
			t.Errorf("GET %s did not serve the relay page: %.80q", path, body)
		}
	}
}

// TestRelayHealthReportsVersion: a deploy waits until /health names the
// release it rolled out.
func TestRelayHealthReportsVersion(t *testing.T) {
	srv := httptest.NewServer(relayHealth("v1.2.3"))
	t.Cleanup(srv.Close)
	resp, err := http.Get(srv.URL + "/health")
	if err != nil {
		t.Fatalf("GET /health: %v", err)
	}
	defer resp.Body.Close()
	var health struct {
		Status  string `json:"status"`
		Version string `json:"version"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&health); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if health.Status != "ok" || health.Version != "v1.2.3" {
		t.Fatalf("health = %+v, want status ok and version v1.2.3", health)
	}
}
