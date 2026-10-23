package cmd

import (
	"context"
	"fmt"
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
