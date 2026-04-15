package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
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

func testIdentity() deviceIdentity {
	return deviceIdentity{
		Hostname:    "laptop-test",
		User:        "tester",
		OS:          "linux",
		Fingerprint: "deadbeef",
	}
}

// runPair runs provider and consumer concurrently against the relay and
// returns both results. The provider uses the supplied decide func.
func runPair(
	t *testing.T,
	pin string,
	consumerPIN string,
	dataDir string,
	decide func(helloRequest) (bool, bool),
) (providerErr, consumerErr error, hello helloRequest) {
	t.Helper()
	base := newTestRelay(t)
	room := nextRoom(t)

	var wg sync.WaitGroup
	wg.Add(2)

	go func() {
		defer wg.Done()
		conn := dialRole(t, base, room, "provider")
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		h, err := providerHandshake(ctx, conn, pin, dataDir, decide)
		providerErr = err
		hello = h
	}()

	go func() {
		defer wg.Done()
		// Give provider a moment to register on the relay so its conn is the
		// provider slot when the consumer's HELLO is forwarded.
		time.Sleep(50 * time.Millisecond)
		conn := dialRole(t, base, room, "consumer")
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		consumerErr = consumerHandshake(ctx, conn, testIdentity(), consumerPIN)
	}()

	wg.Wait()
	return
}

// -----------------------------------------------------------------------------
// Unit tests for helpers
// -----------------------------------------------------------------------------

func TestGeneratePINFormat(t *testing.T) {
	seen := map[string]struct{}{}
	for i := 0; i < 50; i++ {
		p := generatePIN()
		if len(p) != 8 {
			t.Fatalf("PIN length = %d, want 8 (got %q)", len(p), p)
		}
		for _, c := range p {
			if !strings.ContainsRune(pinCharset, c) {
				t.Fatalf("PIN contains invalid char %q in %q", c, p)
			}
		}
		seen[p] = struct{}{}
	}
	if len(seen) < 40 {
		t.Fatalf("PIN not random enough: only %d unique in 50 tries", len(seen))
	}
}

func TestPinEqualConstantTime(t *testing.T) {
	cases := []struct {
		a, b string
		want bool
	}{
		{"Ab3xY9Kq", "Ab3xY9Kq", true},
		{"Ab3xY9Kq", "ab3xY9Kq", false}, // case sensitive
		{"Ab3xY9Kq", "Ab3xY9K", false},  // different length
		{"", "", true},
		{"Ab3xY9Kq", "", false},
	}
	for _, c := range cases {
		if got := pinEqual(c.a, c.b); got != c.want {
			t.Errorf("pinEqual(%q, %q) = %v, want %v", c.a, c.b, got, c.want)
		}
	}
}

func TestFingerprintIsStable(t *testing.T) {
	dir := t.TempDir()
	fp1 := loadOrCreateFingerprint(dir)
	if len(fp1) != 8 {
		t.Fatalf("fingerprint length = %d, want 8", len(fp1))
	}
	fp2 := loadOrCreateFingerprint(dir)
	if fp1 != fp2 {
		t.Fatalf("fingerprint not stable: %q → %q", fp1, fp2)
	}
	// Raw file should contain the fingerprint.
	data, err := os.ReadFile(filepath.Join(dir, "device_id.txt"))
	if err != nil {
		t.Fatalf("read device_id.txt: %v", err)
	}
	if strings.TrimSpace(string(data)) != fp1 {
		t.Fatalf("device_id.txt contents = %q, want %q", string(data), fp1)
	}
}

func TestParseRelayHost(t *testing.T) {
	cases := []struct {
		in           string
		wantScheme   string
		wantHost     string
	}{
		{"", "wss", defaultRelayHost},
		{"relay.example.com", "wss", "relay.example.com"},
		{"wss://relay.example.com", "wss", "relay.example.com"},
		{"ws://relay.example.com", "ws", "relay.example.com"},
		{"https://relay.example.com", "wss", "relay.example.com"},
		{"http://localhost:8430", "ws", "localhost:8430"},
		{"localhost:8430", "ws", "localhost:8430"},
		{"127.0.0.1:8430", "ws", "127.0.0.1:8430"},
	}
	for _, c := range cases {
		gotS, gotH := parseRelayHost(c.in)
		if gotS != c.wantScheme || gotH != c.wantHost {
			t.Errorf("parseRelayHost(%q) = (%q, %q), want (%q, %q)",
				c.in, gotS, gotH, c.wantScheme, c.wantHost)
		}
	}
}

func TestParseSyncTarget(t *testing.T) {
	cases := []struct {
		in                          string
		wantScheme, wantHost, wantID string
	}{
		{"abc123", "wss", defaultRelayHost, "abc123"},
		{"relay.example.com/abc123", "wss", "relay.example.com", "abc123"},
		{"wss://relay.example.com/abc123", "wss", "relay.example.com", "abc123"},
		{"ws://localhost:8430/abc123", "ws", "localhost:8430", "abc123"},
		{"localhost:8430/abc123", "ws", "localhost:8430", "abc123"},
		{"127.0.0.1:8430/abc123", "ws", "127.0.0.1:8430", "abc123"},
		{"https://relay.example.com/abc123", "wss", "relay.example.com", "abc123"},
	}
	for _, c := range cases {
		gs, gh, gid := parseSyncTarget(c.in)
		if gs != c.wantScheme || gh != c.wantHost || gid != c.wantID {
			t.Errorf("parseSyncTarget(%q) = (%q, %q, %q), want (%q, %q, %q)",
				c.in, gs, gh, gid, c.wantScheme, c.wantHost, c.wantID)
		}
	}
}

func TestTrustedDeviceRoundTrip(t *testing.T) {
	dir := t.TempDir()
	hello := helloRequest{
		Method:      "HELLO",
		Hostname:    "peer",
		Fingerprint: "cafebabe",
	}
	if err := saveTrustedDevice(dir, hello); err != nil {
		t.Fatalf("save: %v", err)
	}
	trusted := loadTrustedDevices(dir)
	if _, ok := trusted["cafebabe"]; !ok {
		t.Fatalf("fingerprint not present after save; got %#v", trusted)
	}
	// Idempotent update.
	if err := saveTrustedDevice(dir, hello); err != nil {
		t.Fatalf("save again: %v", err)
	}
	if got := len(loadTrustedDevices(dir)); got != 1 {
		t.Fatalf("trusted count = %d, want 1", got)
	}
}

// -----------------------------------------------------------------------------
// Integration: relay + provider + consumer end-to-end handshake
// -----------------------------------------------------------------------------

func TestHandshakeHappyPath(t *testing.T) {
	dir := t.TempDir()
	pin := "Ab3xY9Kq"

	promptCalls := 0
	decide := func(h helloRequest) (bool, bool) {
		promptCalls++
		return true, false
	}

	pErr, cErr, hello := runPair(t, pin, pin, dir, decide)
	if pErr != nil {
		t.Fatalf("provider: %v", pErr)
	}
	if cErr != nil {
		t.Fatalf("consumer: %v", cErr)
	}
	if promptCalls != 1 {
		t.Fatalf("prompt calls = %d, want 1", promptCalls)
	}
	if hello.Fingerprint != "deadbeef" {
		t.Fatalf("hello fingerprint = %q, want deadbeef", hello.Fingerprint)
	}
}

func TestHandshakeWrongPINRejected(t *testing.T) {
	dir := t.TempDir()
	decide := func(h helloRequest) (bool, bool) {
		t.Fatalf("decide() must not be called on wrong PIN")
		return false, false
	}

	pErr, cErr, _ := runPair(t, "correctPw", "WRONGpin", dir, decide)
	if pErr == nil {
		t.Fatalf("provider error = nil, want wrong-PIN error")
	}
	if !strings.Contains(pErr.Error(), "wrong PIN") {
		t.Fatalf("provider error = %v, want contains 'wrong PIN'", pErr)
	}
	if cErr == nil || !strings.Contains(cErr.Error(), "status 403") {
		t.Fatalf("consumer error = %v, want 403 rejection", cErr)
	}
}

func TestHandshakeUserRejects(t *testing.T) {
	dir := t.TempDir()
	pin := "sessPIN1"
	decide := func(h helloRequest) (bool, bool) { return false, false }

	pErr, cErr, _ := runPair(t, pin, pin, dir, decide)
	if pErr == nil || !strings.Contains(pErr.Error(), "rejected by user") {
		t.Fatalf("provider error = %v, want 'rejected by user'", pErr)
	}
	if cErr == nil || !strings.Contains(cErr.Error(), "status 403") {
		t.Fatalf("consumer error = %v, want 403", cErr)
	}
}

func TestHandshakeTrustPersists(t *testing.T) {
	dir := t.TempDir()
	pin := "TrustMeX1"
	decide := func(h helloRequest) (bool, bool) { return true, true }

	pErr, cErr, _ := runPair(t, pin, pin, dir, decide)
	if pErr != nil {
		t.Fatalf("provider: %v", pErr)
	}
	if cErr != nil {
		t.Fatalf("consumer: %v", cErr)
	}
	trusted := loadTrustedDevices(dir)
	if _, ok := trusted["deadbeef"]; !ok {
		t.Fatalf("fingerprint not persisted; trusted = %#v", trusted)
	}
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

func TestHandshakeMalformedHELLO(t *testing.T) {
	dir := t.TempDir()
	base := newTestRelay(t)
	room := nextRoom(t)

	var wg sync.WaitGroup
	wg.Add(2)
	var pErr error
	var decideCalled int32

	go func() {
		defer wg.Done()
		conn := dialRole(t, base, room, "provider")
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_, pErr = providerHandshake(ctx, conn, "anyPIN12", dir, func(h helloRequest) (bool, bool) {
			atomic.StoreInt32(&decideCalled, 1)
			return false, false
		})
	}()

	go func() {
		defer wg.Done()
		time.Sleep(50 * time.Millisecond)
		conn := dialRole(t, base, room, "consumer")
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		// Send a non-HELLO frame.
		bad, _ := json.Marshal(map[string]string{"method": "PULL"})
		_ = conn.Write(ctx, websocket.MessageText, bad)
		// Drain the relay's forwarded reject so the goroutines unwind cleanly.
		_, _, _ = conn.Read(ctx)
	}()

	wg.Wait()
	if atomic.LoadInt32(&decideCalled) != 0 {
		t.Errorf("decide must not run when HELLO malformed")
	}
	if pErr == nil || !strings.Contains(pErr.Error(), "handshake") {
		t.Fatalf("provider error = %v, want handshake-required error", pErr)
	}
}
