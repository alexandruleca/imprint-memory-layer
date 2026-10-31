package cmd

import (
	"bufio"
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/hunter/imprint/internal/testtime"
	"nhooyr.io/websocket"
)

// newTestRelay spins up relayHandler on an httptest.Server.
// Returns the ws:// base URL (e.g. "ws://127.0.0.1:PORT").
func newTestRelay(t *testing.T) string {
	t.Helper()
	srv := httptest.NewServer(relayHandler(relayPage))
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
	ctx, cancel := context.WithTimeout(context.Background(), testtime.Slack(10*time.Second))
	defer cancel()
	wsURL := fmt.Sprintf("%s/%s?role=%s", base, roomID, role)
	conn, _, err := websocket.Dial(ctx, wsURL, nil)
	if err != nil {
		t.Fatalf("dial %s: %v", role, err)
	}
	t.Cleanup(func() { conn.Close(websocket.StatusNormalClosure, "") })
	return conn
}

// waitRoom waits until roomID exists and done holds for it, under the room's lock.
func waitRoom(t *testing.T, roomID, what string, done func(*relayRoom) bool) {
	t.Helper()
	for deadline := time.Now().Add(testtime.Slack(10 * time.Second)); time.Now().Before(deadline); time.Sleep(5 * time.Millisecond) {
		roomsMu.Lock()
		room := rooms[roomID]
		roomsMu.Unlock()
		if room == nil {
			continue
		}
		room.mu.Lock()
		ok := done(room)
		room.mu.Unlock()
		if ok {
			return
		}
	}
	t.Fatalf("room %s never %s", roomID, what)
}

// waitJoined waits until the relay forwards to role in roomID: a peer that
// sends before then is held, and more than relayHeldLimit is refused.
func waitJoined(t *testing.T, roomID, role string) {
	t.Helper()
	waitRoom(t, roomID, "forwarded to its "+role, func(room *relayRoom) bool {
		if role == "provider" {
			return room.providerReady
		}
		return room.consumerReady
	})
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
	consumer := dialRole(t, base, room, "consumer")
	consumer.SetReadLimit(-1)
	// The 2 MiB message is more than the relay holds for an absent peer.
	waitJoined(t, room, "consumer")

	go func() {
		defer close(done)
		conn := consumer
		ctx, cancel := context.WithTimeout(context.Background(), testtime.Slack(10*time.Second))
		defer cancel()
		_, data, err := conn.Read(ctx)
		if err != nil {
			t.Errorf("consumer read: %v", err)
			return
		}
		got = data
	}()

	conn := dialRole(t, base, room, "provider")
	conn.SetReadLimit(-1)
	ctx, cancel := context.WithTimeout(context.Background(), testtime.Slack(10*time.Second))
	defer cancel()
	if err := conn.Write(ctx, websocket.MessageBinary, payload); err != nil {
		t.Fatalf("provider write: %v", err)
	}

	select {
	case <-done:
	case <-time.After(testtime.Slack(10 * time.Second)):
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

	waitJoined(t, room, "consumer")

	provider := dialRole(t, base, room, "provider")
	provider.SetReadLimit(-1)

	// Provider slams the door.
	provider.Close(websocket.StatusNormalClosure, "bye")

	// Consumer Read must return promptly, not block indefinitely.
	ctx, cancel := context.WithTimeout(context.Background(), testtime.Slack(10*time.Second))
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
	ctx, cancel := context.WithTimeout(context.Background(), testtime.Slack(10*time.Second))
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
	ctx, cancel := context.WithTimeout(context.Background(), testtime.Slack(10*time.Second))
	defer cancel()
	provider := dialRole(t, base, room, "provider")
	first := dialRole(t, base, room, "consumer")
	// The relay takes a role after the handshake completes, so a finished
	// dial does not yet hold it. A frame delivered to the first consumer
	// proves it does before the intruder dials.
	if err := provider.Write(ctx, websocket.MessageBinary, []byte("hello")); err != nil {
		t.Fatalf("provider write: %v", err)
	}
	if _, got, err := first.Read(ctx); err != nil || string(got) != "hello" {
		t.Fatalf("first consumer did not join: %q, %v", got, err)
	}
	intruder := dialRole(t, base, room, "consumer")
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

// TestRelayHoldsFramesUntilThePeerJoins: paired devices connect at the same
// moment, and the consumer may open the key exchange before the provider is
// in the room. Its frames reach the provider when it joins, in order and
// before anything the consumer sends after that.
func TestRelayHoldsFramesUntilThePeerJoins(t *testing.T) {
	base := newTestRelay(t)
	room := nextRoom(t)
	ctx, cancel := context.WithTimeout(context.Background(), testtime.Slack(10*time.Second))
	defer cancel()

	consumer := dialRole(t, base, room, "consumer")
	for _, msg := range []string{"key exchange", "one", "two"} {
		if err := consumer.Write(ctx, websocket.MessageText, []byte(msg)); err != nil {
			t.Fatalf("consumer write %q: %v", msg, err)
		}
	}
	// The relay holds them while the provider is still absent.
	waitRoom(t, room, "held the consumer's frames", func(r *relayRoom) bool { return len(r.held) == 3 })

	provider := dialRole(t, base, room, "provider")
	if err := consumer.Write(ctx, websocket.MessageText, []byte("after")); err != nil {
		t.Fatalf("consumer write: %v", err)
	}
	for _, want := range []string{"key exchange", "one", "two", "after"} {
		if _, got, err := provider.Read(ctx); err != nil || string(got) != want {
			t.Fatalf("provider read %q, %v; want %q", got, err, want)
		}
	}
	if err := provider.Write(ctx, websocket.MessageText, []byte("reply")); err != nil {
		t.Fatalf("provider write: %v", err)
	}
	if _, got, err := consumer.Read(ctx); err != nil || string(got) != "reply" {
		t.Fatalf("consumer read %q, %v; want reply", got, err)
	}
}

// TestRelayDisconnectsAPeerSendingTooMuchAlone bounds what a peer can make
// the relay hold before the other one joins.
func TestRelayDisconnectsAPeerSendingTooMuchAlone(t *testing.T) {
	base := newTestRelay(t)
	room := nextRoom(t)
	consumer := dialRole(t, base, room, "consumer")

	ctx, cancel := context.WithTimeout(context.Background(), testtime.Slack(10*time.Second))
	defer cancel()
	chunk := make([]byte, 256<<10)
	for i := 0; i < 8; i++ {
		if err := consumer.Write(ctx, websocket.MessageBinary, chunk); err != nil {
			break
		}
	}
	if _, _, err := consumer.Read(ctx); websocket.CloseStatus(err) != websocket.StatusPolicyViolation {
		t.Fatalf("peer alone in a room was not stopped: %v", err)
	}
}

// TestRelayClosesOversizedMessages bounds what one client can make the
// relay buffer.
func TestRelayClosesOversizedMessages(t *testing.T) {
	base := newTestRelay(t)
	room := nextRoom(t)
	_ = dialRole(t, base, room, "consumer")

	// The relay refuses the frame once it has read past the limit, while the
	// rest is still being sent. A websocket client still writing holds its
	// write side, so it cannot answer the relay's close and its read waits
	// for the deadline instead (a flake on busy CI hosts); a raw connection
	// reads the close while it writes.
	host := strings.TrimPrefix(base, "ws://")
	conn, err := net.DialTimeout("tcp", host, testtime.Slack(10*time.Second))
	if err != nil {
		t.Fatalf("dial relay: %v", err)
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(testtime.Slack(10 * time.Second)))
	fmt.Fprintf(conn, "GET /%s?role=provider HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"+
		"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n", room, host)
	reader := bufio.NewReader(conn)
	resp, err := http.ReadResponse(reader, nil)
	if err != nil || resp.StatusCode != http.StatusSwitchingProtocols {
		t.Fatalf("websocket handshake: %v %v", resp, err)
	}
	// A final binary frame, masked as a client's must be, with a 64-bit length.
	// A zero mask leaves the zero payload as it is.
	header := binary.BigEndian.AppendUint64([]byte{0x82, 0x80 | 127}, relayMaxMessage+1)
	go conn.Write(append(append(header, 0, 0, 0, 0), make([]byte, relayMaxMessage+1)...))
	// The relay's reply: an unmasked close frame whose payload starts with the status.
	frame := make([]byte, 4)
	if _, err := io.ReadFull(reader, frame); err != nil {
		t.Fatalf("oversized message not refused: %v", err)
	}
	if frame[0] != 0x88 || websocket.StatusCode(binary.BigEndian.Uint16(frame[2:])) != websocket.StatusMessageTooBig {
		t.Fatalf("oversized message not refused with %v: frame % x", websocket.StatusMessageTooBig, frame)
	}
}

// TestRelayShowsBrowsersAPage: a browser on any path gets the page about the
// relay, never the protocol errors meant for sync peers.
func TestRelayShowsBrowsersAPage(t *testing.T) {
	srv := httptest.NewServer(relayHandler(relayPage))
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
// release it rolled out, and the website reads it from another origin.
func TestRelayHealthReportsVersion(t *testing.T) {
	health := getHealth(t)
	if health.Status != "ok" || health.Version != "v1.2.3" {
		t.Fatalf("health = %+v, want status ok and version v1.2.3", health)
	}
	if !health.Since.Equal(relayStarted) {
		t.Fatalf("since = %v, want the relay's start %v", health.Since, relayStarted)
	}
}

type relayHealthBody struct {
	Status   string    `json:"status"`
	Version  string    `json:"version"`
	Rooms    int       `json:"rooms"`
	Sessions int64     `json:"sessions"`
	Since    time.Time `json:"since"`
}

func getHealth(t *testing.T) relayHealthBody {
	t.Helper()
	srv := httptest.NewServer(relayHealth("v1.2.3"))
	t.Cleanup(srv.Close)
	resp, err := http.Get(srv.URL + "/health")
	if err != nil {
		t.Fatalf("GET /health: %v", err)
	}
	defer resp.Body.Close()
	if got := resp.Header.Get("Access-Control-Allow-Origin"); got != "*" {
		t.Fatalf("Access-Control-Allow-Origin = %q, want *", got)
	}
	var health relayHealthBody
	if err := json.NewDecoder(resp.Body).Decode(&health); err != nil {
		t.Fatalf("decode: %v", err)
	}
	return health
}

// TestRelayCountsPairedSessions: a room counts once both machines join it,
// and a room still waiting for its second machine does not.
func TestRelayCountsPairedSessions(t *testing.T) {
	base := newTestRelay(t)
	before := getHealth(t).Sessions

	waiting := nextRoom(t)
	_ = dialRole(t, base, waiting, "provider")
	paired := nextRoom(t)
	consumer := dialRole(t, base, paired, "consumer")
	provider := dialRole(t, base, paired, "provider")

	// The relay has registered both peers once a frame crosses the room.
	ctx, cancel := context.WithTimeout(context.Background(), testtime.Slack(10*time.Second))
	defer cancel()
	if err := provider.Write(ctx, websocket.MessageBinary, []byte("hello")); err != nil {
		t.Fatalf("provider write: %v", err)
	}
	if _, _, err := consumer.Read(ctx); err != nil {
		t.Fatalf("consumer read: %v", err)
	}
	if got := getHealth(t).Sessions - before; got != 1 {
		t.Fatalf("sessions grew by %d, want 1", got)
	}
}

// TestRelayPageAnalytics: the page stays script-free without a measurement
// ID; with one it reports views without the address, which may hold a code.
func TestRelayPageAnalytics(t *testing.T) {
	if !bytes.Equal(relayPageFor(""), relayPage) {
		t.Fatal("the page changed without a measurement ID")
	}
	page := string(relayPageFor("G-TEST123"))
	head := page[:strings.Index(page, "</head>")]
	for _, want := range []string{
		`<script async src="https://www.googletagmanager.com/gtag/js?id=G-TEST123"></script>`,
		`gtag('config','G-TEST123',{page_location:location.origin+(location.pathname==='/'?'/':'/not-found')});`,
	} {
		if !strings.Contains(head, want) {
			t.Errorf("head lacks %s", want)
		}
	}
	if strings.Count(page, "googletagmanager") != 1 || strings.Count(page, "</head>") != 1 {
		t.Fatal("analytics not added exactly once")
	}
	for _, id := range []string{"G-TEST123", "G-ZPW1M6BCC7"} {
		if !ga4IDPattern.MatchString(id) {
			t.Errorf("%s refused", id)
		}
	}
	for _, id := range []string{"UA-1234-1", "G-abc", "G-1');alert(1)//", ""} {
		if ga4IDPattern.MatchString(id) {
			t.Errorf("%q accepted", id)
		}
	}
}
