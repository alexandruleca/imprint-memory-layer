package cmd

import (
	"context"
	"fmt"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/hunter/imprint/internal/output"
	"nhooyr.io/websocket"
)

type relayRoom struct {
	provider *websocket.Conn
	consumer *websocket.Conn
	created  time.Time
	mu       sync.Mutex
}

var (
	rooms   = make(map[string]*relayRoom)
	roomsMu sync.Mutex
)

const (
	// Peers cap frames at 64 MiB (imprint/sync_ws.py MAX_FRAME_BYTES); this
	// leaves room for framing and refuses anything larger.
	relayMaxMessage = 80 << 20
	// A peer that stops reading must not hold a forwarded frame forever.
	relayWriteTimeout = 5 * time.Minute
	// A room still waiting for its second peer after this long is closed.
	relayUnpairedTTL = time.Hour
)

func Relay(args []string) {
	port := 8430
	for i, arg := range args {
		if arg == "--port" && i+1 < len(args) {
			p, err := strconv.Atoi(args[i+1])
			if err == nil {
				port = p
			}
		}
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
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		roomsMu.Lock()
		count := len(rooms)
		roomsMu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"status":"ok","rooms":%d}`, count)
	})
	mux.HandleFunc("/", relayHandler)

	addr := fmt.Sprintf("0.0.0.0:%d", port)
	fmt.Println()
	output.Header("═══ Imprint Relay ═══")
	fmt.Printf("  Listening on %s\n", addr)
	fmt.Printf("  Unpaired rooms expire after 1 hour\n")
	fmt.Println()

	server := &http.Server{Addr: addr, Handler: mux}
	if err := server.ListenAndServe(); err != nil {
		output.Fail("Relay error: " + err.Error())
	}
}

func relayHandler(w http.ResponseWriter, r *http.Request) {
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
	slot := &room.consumer
	if role == "provider" {
		slot = &room.provider
	}
	// A taken role is refused: a later connection must not replace the
	// peer a session is already talking to.
	taken := *slot != nil
	if !taken {
		*slot = conn
	}
	room.mu.Unlock()
	roomsMu.Unlock()
	if taken {
		conn.Close(websocket.StatusPolicyViolation, "role already taken in this room")
		return
	}

	// Forward messages from this conn to the peer
	ctx := context.Background()
	for {
		msgType, data, err := conn.Read(ctx)
		if err != nil {
			break
		}
		room.mu.Lock()
		var peer *websocket.Conn
		if role == "provider" {
			peer = room.consumer
		} else {
			peer = room.provider
		}
		room.mu.Unlock()

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
		room.provider = nil
		peer = room.consumer
		room.consumer = nil
	}
	if room.consumer == conn {
		room.consumer = nil
		if peer == nil {
			peer = room.provider
		}
		room.provider = nil
	}
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
