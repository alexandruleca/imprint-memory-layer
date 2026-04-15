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

	// Clean expired rooms
	go func() {
		for {
			time.Sleep(5 * time.Minute)
			roomsMu.Lock()
			for id, room := range rooms {
				if time.Since(room.created) > 1*time.Hour {
					room.mu.Lock()
					if room.provider != nil {
						room.provider.Close(websocket.StatusGoingAway, "expired")
					}
					if room.consumer != nil {
						room.consumer.Close(websocket.StatusGoingAway, "expired")
					}
					room.mu.Unlock()
					delete(rooms, id)
				}
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
	fmt.Printf("  Rooms expire after 1 hour\n")
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

	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		InsecureSkipVerify: true,
	})
	if err != nil {
		return
	}
	// Disable 32 KiB default — sync payloads are MBs.
	conn.SetReadLimit(-1)

	roomsMu.Lock()
	room, exists := rooms[id]
	if !exists {
		room = &relayRoom{created: time.Now()}
		rooms[id] = room
	}
	roomsMu.Unlock()

	room.mu.Lock()
	if role == "provider" {
		room.provider = conn
	} else {
		room.consumer = conn
	}
	room.mu.Unlock()

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
		if err := peer.Write(ctx, msgType, data); err != nil {
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
