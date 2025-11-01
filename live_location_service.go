package main

import (
	"context"
	"encoding/json"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/gorilla/mux"
	"github.com/gorilla/websocket"
	"github.com/nats-io/nats.go"
)

// Location is the canonical message for location updates.
type Location struct {
	Type     string                 `json:"type,omitempty"`
	DriverID string                 `json:"driver_id"`
	Lat      float64                `json:"lat"`
	Lng      float64                `json:"lng"`
	TS       float64                `json:"ts"`
	Meta     map[string]interface{} `json:"meta,omitempty"`

	// internal bookkeeping field (not serialized to clients)
	_receivedAt float64 `json:"-"`
}

// Manager holds websocket connections and last-known locations.
type Manager struct {
	drivers       map[string]*websocket.Conn // driver_id -> websocket (optional)
	monitors      map[*websocket.Conn]bool   // set of monitor websockets
	monitorSubs   map[*websocket.Conn]string // option subscription driver_id (empty => all)
	lastKnown     map[string]Location
	mu            sync.RWMutex
	natsConn      *nats.Conn
	natsSubject   string
	natsEnabled   bool
	broadcastChan chan Location
}

func NewManager() *Manager {
	return &Manager{
		drivers:       make(map[string]*websocket.Conn),
		monitors:      make(map[*websocket.Conn]bool),
		monitorSubs:   make(map[*websocket.Conn]string),
		lastKnown:     make(map[string]Location),
		broadcastChan: make(chan Location, 1024),
	}
}

// SetNATS configures NATS publishing
func (m *Manager) SetNATS(nc *nats.Conn, subject string) {
	if nc == nil {
		m.natsEnabled = false
		return
	}
	m.natsEnabled = true
	m.natsConn = nc
	m.natsSubject = subject
}

// Run broadcaster loop (non-blocking) to send updates to monitors and optionally publish to NATS.
func (m *Manager) RunBroadcaster(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case loc := <-m.broadcastChan:
			// publish to NATS (best-effort)
			if m.natsEnabled && m.natsConn != nil {
				b, _ := json.Marshal(loc)
				_ = m.natsConn.Publish(m.natsSubject, b) // ignore error: non-fatal
			}

			// send to monitors (concurrent-safe)
			m.mu.RLock()
			monitors := make([]*websocket.Conn, 0, len(m.monitors))
			subs := make(map[*websocket.Conn]string, len(m.monitorSubs))
			for ws := range m.monitors {
				monitors = append(monitors, ws)
			}
			for ws, sub := range m.monitorSubs {
				subs[ws] = sub
			}
			m.mu.RUnlock()

			msg, _ := json.Marshal(loc)
			for _, ws := range monitors {
				// if this ws has a subscription, check
				if subID, ok := subs[ws]; ok && subID != "" && subID != loc.DriverID {
					continue
				}
				// write with a small timeout
				ws.SetWriteDeadline(time.Now().Add(3 * time.Second))
				if err := ws.WriteMessage(websocket.TextMessage, msg); err != nil {
					// remove dead monitor
					m.removeMonitor(ws)
				}
			}
		}
	}
}

// receiveFromDriver updates last-known and enqueues broadcast.
func (m *Manager) receiveFromDriver(loc Location) {
	now := float64(time.Now().Unix())
	loc.TS = loc.TS
	if loc.TS == 0 {
		loc.TS = now
	}
	loc._receivedAt = now
	if loc.Type == "" {
		loc.Type = "location"
	}

	m.mu.Lock()
	m.lastKnown[loc.DriverID] = loc
	m.mu.Unlock()

	// non-blocking send to broadcaster channel (drop if full)
	select {
	case m.broadcastChan <- loc:
	default:
		// broadcaster busy, drop location (rare). Could log.
		log.Printf("broadcast channel full, dropped location for driver %s", loc.DriverID)
	}
}

func (m *Manager) getLastKnown(driverID string) (Location, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	loc, ok := m.lastKnown[driverID]
	return loc, ok
}

func (m *Manager) getAllLastKnown() map[string]Location {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make(map[string]Location, len(m.lastKnown))
	for k, v := range m.lastKnown {
		out[k] = v
	}
	return out
}

func (m *Manager) addDriverConn(driverID string, ws *websocket.Conn) {
	m.mu.Lock()
	m.drivers[driverID] = ws
	m.mu.Unlock()
}

func (m *Manager) removeDriverConn(driverID string) {
	m.mu.Lock()
	if ws, ok := m.drivers[driverID]; ok {
		ws.Close()
		delete(m.drivers, driverID)
	}
	m.mu.Unlock()
}

func (m *Manager) addMonitor(ws *websocket.Conn, sub string) {
	m.mu.Lock()
	m.monitors[ws] = true
	if sub != "" {
		m.monitorSubs[ws] = sub
	}
	m.mu.Unlock()
}

func (m *Manager) removeMonitor(ws *websocket.Conn) {
	m.mu.Lock()
	if _, ok := m.monitors[ws]; ok {
		delete(m.monitors, ws)
	}
	if _, ok := m.monitorSubs[ws]; ok {
		delete(m.monitorSubs, ws)
	}
	m.mu.Unlock()
	ws.Close()
}

// --- HTTP / WS handlers below ---

var upgrader = websocket.Upgrader{
	ReadBufferSize:  1024,
	WriteBufferSize: 1024,
	// allow cross-origin in dev; change or tighten in prod
	CheckOrigin: func(r *http.Request) bool { return true },
}

// WebSocket endpoint for drivers: /ws/driver/{driver_id}
// Drivers send JSON messages like {"lat": 5.6, "lng": -0.1, "ts": 1234567890, "meta": {...}}
func (m *Manager) wsDriverHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	driverID := vars["driver_id"]
	if strings.TrimSpace(driverID) == "" {
		http.Error(w, "missing driver_id", http.StatusBadRequest)
		return
	}

	ws, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("driver ws upgrade failed: %v", err)
		return
	}
	defer ws.Close()

	m.addDriverConn(driverID, ws)
	defer m.removeDriverConn(driverID)
	log.Printf("driver connected: %s", driverID)

	// read loop
	for {
		mt, msg, err := ws.ReadMessage()
		if err != nil {
			if websocket.IsCloseError(err, websocket.CloseNormalClosure) {
				log.Printf("driver ws closed: %s", driverID)
			} else {
				log.Printf("driver ws read error (%s): %v", driverID, err)
			}
			return
		}
		if mt != websocket.TextMessage && mt != websocket.BinaryMessage {
			continue
		}
		var in map[string]interface{}
		if err := json.Unmarshal(msg, &in); err != nil {
			log.Printf("invalid json from driver %s: %v", driverID, err)
			continue
		}
		lat, latOK := parseFloat(in["lat"])
		lng, lngOK := parseFloat(in["lng"])
		if !latOK || !lngOK { // ignore invalid messages
			log.Printf("driver %s sent message without lat/lng: %v", driverID, in)
			continue
		}
		ts, _ := parseFloat(in["ts"])
		meta := make(map[string]interface{})
		if mval, ok := in["meta"]; ok {
			if mm, ok2 := mval.(map[string]interface{}); ok2 {
				meta = mm
			}
		}
		loc := Location{
			DriverID: driverID,
			Lat:      lat,
			Lng:      lng,
			TS:       ts,
			Meta:     meta,
		}
		m.receiveFromDriver(loc)
	}
}

// WebSocket endpoint for monitors: /ws/monitor?filter_driver=ID
// Optionally monitors can send "subscribe:<driver_id>" text to subscribe.
func (m *Manager) wsMonitorHandler(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()
	filter := query.Get("filter_driver") // optional

	ws, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("monitor ws upgrade failed: %v", err)
		return
	}
	// add to monitors
	m.addMonitor(ws, filter)
	log.Printf("monitor connected (filter=%s)", filter)

	// send an initial snapshot of last-known (filtered)
	if filter != "" {
		if loc, ok := m.getLastKnown(filter); ok {
			msg, _ := json.Marshal(loc)
			ws.WriteMessage(websocket.TextMessage, msg)
		}
	} else {
		all := m.getAllLastKnown()
		if len(all) > 0 {
			msg, _ := json.Marshal(map[string]interface{}{"type": "snapshot", "locations": all})
			ws.WriteMessage(websocket.TextMessage, msg)
		}
	}

	// read loop: listen for subscribe: messages or pings; monitors rarely send large messages
	for {
		_, message, err := ws.ReadMessage()
		if err != nil {
			m.removeMonitor(ws)
			log.Printf("monitor disconnected")
			return
		}
		txt := string(message)
		if strings.HasPrefix(txt, "subscribe:") {
			sub := strings.TrimSpace(strings.TrimPrefix(txt, "subscribe:"))
			m.mu.Lock()
			m.monitorSubs[ws] = sub
			m.mu.Unlock()
			// send ack
			ack := map[string]interface{}{"type": "subscribed", "driver_id": sub}
			if b, err := json.Marshal(ack); err == nil {
				ws.WriteMessage(websocket.TextMessage, b)
			}
			// send last-known for that driver if present
			if loc, ok := m.getLastKnown(sub); ok {
				if b, err := json.Marshal(loc); err == nil {
					ws.WriteMessage(websocket.TextMessage, b)
				}
			}
		}
	}
}

// HTTP POST /location
// Accepts JSON: {"driver_id":"123","lat":5.6,"lng":-0.1,"ts":1234567890,"meta":{...}}
func (m *Manager) httpPostLocation(w http.ResponseWriter, r *http.Request) {
	var payload Location
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		http.Error(w, "invalid json", http.StatusBadRequest)
		return
	}
	if payload.DriverID == "" {
		http.Error(w, "missing driver_id", http.StatusBadRequest)
		return
	}
	if payload.Lat == 0 && payload.Lng == 0 {
		http.Error(w, "missing lat/lng", http.StatusBadRequest)
		return
	}
	if payload.TS == 0 {
		payload.TS = float64(time.Now().Unix())
	}
	payload.Type = "location"
	m.receiveFromDriver(payload)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]bool{"ok": true})
}

// GET /locations
func (m *Manager) httpGetLocations(w http.ResponseWriter, r *http.Request) {
	all := m.getAllLastKnown()
	out := map[string]interface{}{
		"count":     len(all),
		"locations": all,
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(out)
}

// GET /locations/{driver_id}
func (m *Manager) httpGetLocation(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	driverID := vars["driver_id"]
	loc, ok := m.getLastKnown(driverID)
	if !ok {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(loc)
}

func parseFloat(v interface{}) (float64, bool) {
	if v == nil {
		return 0, false
	}
	switch t := v.(type) {
	case float64:
		return t, true
	case float32:
		return float64(t), true
	case int:
		return float64(t), true
	case int64:
		return float64(t), true
	case json.Number:
		f, err := t.Float64()
		if err != nil {
			return 0, false
		}
		return f, true
	case string:
		if t == "" {
			return 0, false
		}
		f, err := strconv.ParseFloat(t, 64)
		if err != nil {
			return 0, false
		}
		return f, true
	default:
		return 0, false
	}
}

func main() {
	var (
		addr      = flag.String("addr", "0.0.0.0:9000", "listen address")
		natsURL   = flag.String("nats", os.Getenv("NATS_URL"), "NATS server URL (optional)")
		natsTopic = flag.String("nats_subject", "drivers.locations", "NATS subject to publish locations")
	)
	flag.Parse()

	manager := NewManager()

	// Optional NATS connect
	if *natsURL != "" {
		nc, err := nats.Connect(*natsURL)
		if err != nil {
			log.Printf("warning: failed to connect to NATS at %s: %v", *natsURL, err)
		} else {
			manager.SetNATS(nc, *natsTopic)
			log.Printf("connected to NATS %s, publishing to %s", *natsURL, *natsTopic)
		}
	} else {
		log.Printf("NATS not configured; skipping NATS publishing")
	}

	r := mux.NewRouter()
	// WS endpoints
	r.HandleFunc("/ws/driver/{driver_id}", manager.wsDriverHandler)
	r.HandleFunc("/ws/monitor", manager.wsMonitorHandler)

	// HTTP endpoints
	r.HandleFunc("/location", manager.httpPostLocation).Methods("POST")
	r.HandleFunc("/locations", manager.httpGetLocations).Methods("GET")
	r.HandleFunc("/locations/{driver_id}", manager.httpGetLocation).Methods("GET")

	server := &http.Server{
		Addr:    *addr,
		Handler: r,
	}

	// Run broadcaster in background
	ctx, cancel := context.WithCancel(context.Background())
	go manager.RunBroadcaster(ctx)

	// graceful shutdown
	go func() {
		log.Printf("live location service listening on %s", *addr)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("ListenAndServe: %v", err)
		}
	}()

	// Wait for SIGTERM/SIGINT
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	<-stop
	log.Printf("shutting down...")

	// stop broadcaster and gracefully shutdown
	cancel()
	ctxShutdown, cancelShutdown := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancelShutdown()
	if err := server.Shutdown(ctxShutdown); err != nil {
		log.Printf("server Shutdown: %v", err)
	}
	log.Printf("stopped")
}
