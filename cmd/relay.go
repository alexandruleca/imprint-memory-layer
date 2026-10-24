package cmd

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/hunter/imprint/internal/output"
	"nhooyr.io/websocket"
)

type relayFrame struct {
	kind websocket.MessageType
	data []byte
}

type relayRoom struct {
	provider *websocket.Conn
	consumer *websocket.Conn
	// A peer receives forwarded frames once ready: after the frames held for
	// it are written, so they arrive first and in order.
	providerReady, consumerReady bool
	// Frames the connected peer sent before the other one joined. Paired
	// devices connect at the same moment and the consumer opens the key
	// exchange; dropping that frame left both sides waiting for each other.
	held      []relayFrame
	heldBytes int
	created   time.Time
	mu        sync.Mutex
}

var (
	rooms   = make(map[string]*relayRoom)
	roomsMu sync.Mutex

	// Rooms both machines joined since this relay started: the sessions it
	// has relayed. Kept in memory like everything else, so a restart resets it.
	relayedSessions atomic.Int64
	relayStarted    = time.Now().UTC().Truncate(time.Second)
)

// What a browser gets from the relay: what this server is, with no scripts
// or third-party requests unless the operator adds analytics (relayPageFor).
//
//go:embed relay.html
var relayPage []byte

var ga4IDPattern = regexp.MustCompile(`^G-[A-Z0-9]+$`)

const (
	// Peers cap frames at 64 MiB (imprint/sync_ws.py MAX_FRAME_BYTES); this
	// leaves room for framing and refuses anything larger.
	relayMaxMessage = 80 << 20
	// A peer that stops reading must not hold a forwarded frame forever.
	relayWriteTimeout = 5 * time.Minute
	// A room still waiting for its second peer after this long is closed.
	relayUnpairedTTL = time.Hour
	// Until both peers are in a room, a peer only opens the handshake. A
	// peer sending more than this before the other joins is disconnected.
	relayHeldLimit = 1 << 20
)

func Relay(args []string, version string) {
	port := 8430
	ga4ID := ""
	for i, arg := range args {
		if arg == "--port" && i+1 < len(args) {
			p, err := strconv.Atoi(args[i+1])
			if err == nil {
				port = p
			}
		}
		if arg == "--ga4-id" && i+1 < len(args) {
			ga4ID = args[i+1]
		}
	}
	if ga4ID != "" && !ga4IDPattern.MatchString(ga4ID) {
		output.Fail("--ga4-id takes a GA4 measurement ID such as G-XXXXXXXXXX")
	}

	// Close rooms that never paired. Paired rooms live as long as their
	// transfer: closing them would cut off a large sync mid-stream.
	go func() {
		for {
			time.Sleep(5 * time.Minute)
			roomsMu.Lock()
			for id, room := range rooms {
				room.mu.Lock()
				unpaired := room.provider == nil || room.consumer == nil
				if unpaired && time.Since(room.created) > relayUnpairedTTL {
					if room.provider != nil {
						room.provider.Close(websocket.StatusGoingAway, "expired")
					}
					if room.consumer != nil {
						room.consumer.Close(websocket.StatusGoingAway, "expired")
					}
					delete(rooms, id)
				}
				room.mu.Unlock()
			}
			roomsMu.Unlock()
		}
	}()

	mux := http.NewServeMux()
	mux.HandleFunc("/health", relayHealth(version))
	mux.HandleFunc("/", relayHandler(relayPageFor(ga4ID)))

	addr := fmt.Sprintf("0.0.0.0:%d", port)
	fmt.Println()
	output.Header("═══ Imprint Relay ═══")
	fmt.Printf("  Listening on %s\n", addr)
	fmt.Printf("  Unpaired rooms expire after 1 hour\n")
	if ga4ID != "" {
		fmt.Printf("  Browser page reports views to Google Analytics %s\n", ga4ID)
	}
	fmt.Println()

	server := &http.Server{Addr: addr, Handler: mux}
	if err := server.ListenAndServe(); err != nil {
		output.Fail("Relay error: " + err.Error())
	}
}

// relayPageFor adds Google Analytics to the relay page when the operator
// passes a GA4 measurement ID. Every view is reported as the root or as
// /not-found, so an address carrying a share code never leaves the browser.
func relayPageFor(ga4ID string) []byte {
	if ga4ID == "" {
		return relayPage
	}
	tag := fmt.Sprintf(`<script async src="https://www.googletagmanager.com/gtag/js?id=%[1]s"></script>
<script>
window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}
gtag('js',new Date());
gtag('config','%[1]s',{page_location:location.origin+(location.pathname==='/'?'/':'/not-found')});
</script>
</head>`, ga4ID)
	return bytes.Replace(relayPage, []byte("</head>"), []byte(tag), 1)
}

// serveRelayPage answers 404 off the root, so no other path, such as one
// carrying a share code, reads as a page of its own.
func serveRelayPage(w http.ResponseWriter, r *http.Request, page []byte) {
	status := http.StatusOK
	if r.URL.Path != "/" {
		status = http.StatusNotFound
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	_, _ = w.Write(page)
}

// relayHealth reports liveness, the running release (a deploy waits for it),
// the number of open rooms and the sessions relayed since the relay started.
// Any origin may read it: the Imprint website shows it live.
func relayHealth(version string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		roomsMu.Lock()
		count := len(rooms)
		roomsMu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		_ = json.NewEncoder(w).Encode(struct {
			Status   string    `json:"status"`
			Version  string    `json:"version"`
			Rooms    int       `json:"rooms"`
			Sessions int64     `json:"sessions"`
			Since    time.Time `json:"since"`
		}{"ok", version, count, relayedSessions.Load(), relayStarted})
	}
}

func relayHandler(page []byte) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// Sync peers always open a WebSocket, and only they see protocol errors.
		if !strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
			serveRelayPage(w, r, page)
			return
		}
		relayPeer(w, r)
	}
}

func relayPeer(w http.ResponseWriter, r *http.Request) {
	id := r.URL.Path[1:]
	if id == "" {
		http.Error(w, "room ID required", 400)
		return
	}
	role := r.URL.Query().Get("role")
	if role != "provider" && role != "consumer" {
		http.Error(w, "role must be provider or consumer", 400)
		return
	}

	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		InsecureSkipVerify: true,
	})
	if err != nil {
		return
	}
	// Above the 32 KiB default — sync batches are MBs — but bounded.
	conn.SetReadLimit(relayMaxMessage)

	roomsMu.Lock()
	room, exists := rooms[id]
	if !exists {
		room = &relayRoom{created: time.Now()}
		rooms[id] = room
	}
	room.mu.Lock()
	slot, ready := &room.consumer, &room.consumerReady
	if role == "provider" {
		slot, ready = &room.provider, &room.providerReady
	}
	// A taken role is refused: a later connection must not replace the
	// peer a session is already talking to.
	taken := *slot != nil
	if !taken {
		*slot = conn
		if room.provider != nil && room.consumer != nil {
			relayedSessions.Add(1)
		}
	}
	room.mu.Unlock()
	roomsMu.Unlock()
	if taken {
		conn.Close(websocket.StatusPolicyViolation, "role already taken in this room")
		return
	}

	// Deliver what the other peer sent before this one joined, including
	// frames it sends while that is under way, then receive directly.
	ctx := context.Background()
	open := true
	for open {
		room.mu.Lock()
		frames := room.held
		room.held, room.heldBytes = nil, 0
		if len(frames) == 0 {
			*ready = true
		}
		room.mu.Unlock()
		if len(frames) == 0 {
			break
		}
		for _, frame := range frames {
			writeCtx, cancel := context.WithTimeout(ctx, relayWriteTimeout)
			err := conn.Write(writeCtx, frame.kind, frame.data)
			cancel()
			if err != nil {
				open = false
				break
			}
		}
	}

	// Forward messages from this conn to the peer, holding them while the
	// peer has not joined or is still receiving what was held for it.
	for open {
		msgType, data, err := conn.Read(ctx)
		if err != nil {
			break
		}
		room.mu.Lock()
		var peer *websocket.Conn
		if role == "provider" && room.consumerReady {
			peer = room.consumer
		} else if role == "consumer" && room.providerReady {
			peer = room.provider
		}
		overflow := false
		if peer == nil {
			room.held = append(room.held, relayFrame{msgType, data})
			room.heldBytes += len(data)
			overflow = room.heldBytes > relayHeldLimit
		}
		room.mu.Unlock()

		if overflow {
			conn.Close(websocket.StatusPolicyViolation, "too much data before the other peer joined")
			break
		}
		if peer == nil {
			continue
		}
		writeCtx, cancel := context.WithTimeout(ctx, relayWriteTimeout)
		err = peer.Write(writeCtx, msgType, data)
		cancel()
		if err != nil {
			break
		}
	}

	// Cleanup. Close the peer too so the other side fails fast instead of
	// hanging on Read until TCP keepalive.
	roomsMu.Lock()
	room.mu.Lock()
	var peer *websocket.Conn
	if room.provider == conn {
		room.provider, room.providerReady = nil, false
		peer = room.consumer
		room.consumer, room.consumerReady = nil, false
	}
	if room.consumer == conn {
		room.consumer, room.consumerReady = nil, false
		if peer == nil {
			peer = room.provider
		}
		room.provider, room.providerReady = nil, false
	}
	room.held, room.heldBytes = nil, 0
	empty := room.provider == nil && room.consumer == nil
	room.mu.Unlock()
	if empty {
		delete(rooms, id)
	}
	roomsMu.Unlock()
	conn.Close(websocket.StatusNormalClosure, "done")
	if peer != nil {
		peer.Close(websocket.StatusGoingAway, "peer disconnected")
	}
}
