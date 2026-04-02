// DecisionsAI Sidecar — machine control agent
// Connects to the relay server via WebSocket and executes tool calls.
// Cross-compiles for macOS (arm64/amd64) and Windows (amd64).
//
// Build:
//   macOS:   go build -o decisionsai-sidecar .
//   Windows: GOOS=windows GOARCH=amd64 go build -o decisionsai-sidecar.exe .
//
// Run:
//   ./decisionsai-sidecar --server wss://your-relay-server/ws/sidecar --token YOUR_TOKEN
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"runtime"
	"syscall"
	"time"

	"github.com/gorilla/websocket"
)

// ── Wire protocol ─────────────────────────────────────────────────────────────

type RPCRequest struct {
	Type   string         `json:"type"`    // "tool_call"
	ID     string         `json:"id"`      // correlation ID
	Tool   string         `json:"tool"`    // tool name
	Params map[string]any `json:"params"`
}

type RPCResponse struct {
	Type   string `json:"type"`   // "tool_result"
	ID     string `json:"id"`     // matches request ID
	Result any    `json:"result,omitempty"`
	Error  string `json:"error,omitempty"`
}

type Registration struct {
	Type         string   `json:"type"`         // "sidecar_register"
	OS           string   `json:"os"`
	Hostname     string   `json:"hostname"`
	Capabilities []string `json:"capabilities"`
}

// ── Main ──────────────────────────────────────────────────────────────────────

func main() {
	serverURL := flag.String("server", os.Getenv("DECISIONSAI_SERVER_URL"), "Relay server WebSocket URL (e.g. wss://host/ws/sidecar)")
	token := flag.String("token", os.Getenv("DECISIONSAI_SIDECAR_TOKEN"), "Auth token")
	appUserID := flag.String("user", os.Getenv("DECISIONSAI_APP_USER_ID"), "App user ID")
	local := flag.Bool("local", false, "Run in local-only mode (HTTP tool API only, no relay WebSocket)")
	install := flag.Bool("install", false, "Install as a system service (launchd/Task Scheduler) and exit")
	uninstall := flag.Bool("uninstall", false, "Remove the system service and exit")
	status := flag.Bool("status", false, "Print service status and exit")
	flag.Parse()

	// ── Service management commands ───────────────────────────────────────────
	if *status {
		fmt.Println("Service status:", serviceStatus())
		return
	}

	if *uninstall {
		if err := uninstallService(); err != nil {
			log.Fatalf("Uninstall failed: %v", err)
		}
		return
	}

	if *install {
		if *serverURL == "" {
			log.Fatal("--server is required for --install")
		}
		if *token == "" {
			log.Fatal("--token is required for --install")
		}
		if err := installService(*serverURL, *token, *appUserID); err != nil {
			log.Fatalf("Install failed: %v", err)
		}
		return
	}

	// ── Normal run ────────────────────────────────────────────────────────────

	handlers := buildHandlers()

	// Start HTTP server for Python tool calls (no WebSocket needed)
	startHTTPServer(handlers)

	// Local-only mode: just serve HTTP, no relay WebSocket
	if *local {
		log.Printf("[sidecar] Local mode — HTTP tool API on port %s", func() string {
			p := os.Getenv("DECISIONSAI_SIDECAR_HTTP_PORT")
			if p == "" { return "11435" }
			return p
		}())
		interrupt := make(chan os.Signal, 1)
		signal.Notify(interrupt, os.Interrupt, syscall.SIGTERM)
		<-interrupt
		log.Println("[sidecar] Shutting down")
		return
	}

	if *serverURL == "" {
		log.Fatal("--server (or DECISIONSAI_SERVER_URL env var) is required")
	}
	if *token == "" {
		log.Fatal("--token or DECISIONSAI_SIDECAR_TOKEN env var is required")
	}

	u, err := url.Parse(*serverURL)
	if err != nil {
		log.Fatalf("invalid server URL: %v", err)
	}
	q := u.Query()
	q.Set("token", *token)
	if *appUserID != "" {
		q.Set("app_user_id", *appUserID)
	}
	q.Set("sidecar", "1")
	u.RawQuery = q.Encode()

	log.Printf("[sidecar] Connecting to %s (os=%s)", u.Host, runtime.GOOS)

	interrupt := make(chan os.Signal, 1)
	signal.Notify(interrupt, os.Interrupt, syscall.SIGTERM)

	for {
		if err := runLoop(u.String(), handlers); err != nil {
			log.Printf("[sidecar] Disconnected: %v — reconnecting in 5s", err)
		}
		select {
		case <-interrupt:
			log.Println("[sidecar] Shutting down")
			return
		case <-time.After(5 * time.Second):
		}
	}
}

func runLoop(serverURL string, handlers map[string]ToolHandler) error {
	conn, _, err := websocket.DefaultDialer.Dial(serverURL, nil)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()

	// Register capabilities
	hostname, _ := os.Hostname()
	caps := []string{"terminal", "filesystem", "clipboard", "screenshot", "desktop"}
	reg := Registration{
		Type:         "sidecar_register",
		OS:           runtime.GOOS,
		Hostname:     hostname,
		Capabilities: caps,
	}
	if err := conn.WriteJSON(reg); err != nil {
		return fmt.Errorf("register: %w", err)
	}
	log.Printf("[sidecar] Registered — capabilities: %v", caps)

	for {
		_, msg, err := conn.ReadMessage()
		if err != nil {
			return fmt.Errorf("read: %w", err)
		}

		var req RPCRequest
		if err := json.Unmarshal(msg, &req); err != nil {
			continue
		}
		if req.Type != "tool_call" {
			continue
		}

		go func(r RPCRequest) {
			resp := dispatch(handlers, r)
			if err := conn.WriteJSON(resp); err != nil {
				log.Printf("[sidecar] write error: %v", err)
			}
		}(req)
	}
}

func dispatch(handlers map[string]ToolHandler, req RPCRequest) RPCResponse {
	h, ok := handlers[req.Tool]
	if !ok {
		return RPCResponse{Type: "tool_result", ID: req.ID, Error: fmt.Sprintf("unknown tool: %s", req.Tool)}
	}
	result, err := h(req.Params)
	if err != nil {
		log.Printf("[sidecar] tool %s error: %v", req.Tool, err)
		return RPCResponse{Type: "tool_result", ID: req.ID, Error: err.Error()}
	}
	return RPCResponse{Type: "tool_result", ID: req.ID, Result: result}
}

type ToolHandler func(params map[string]any) (any, error)


// startHTTPServer starts a simple HTTP server so Python tools can call
// sidecar tools without needing a WebSocket connection.
// POST /tool/{name}  body: JSON params  →  JSON result
// GET  /health       →  {"ok":true}
func startHTTPServer(handlers map[string]ToolHandler) {
	httpPort := os.Getenv("DECISIONSAI_SIDECAR_HTTP_PORT")
	if httpPort == "" {
		httpPort = "11435"
	}

	mux := http.NewServeMux()

	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"ok":true}`))
	})

	mux.HandleFunc("/tool/", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		toolName := strings.TrimPrefix(r.URL.Path, "/tool/")
		handler, ok := handlers[toolName]
		if !ok {
			http.Error(w, fmt.Sprintf(`{"error":"unknown tool: %s"}`, toolName), http.StatusNotFound)
			return
		}
		var params map[string]any
		if err := json.NewDecoder(r.Body).Decode(&params); err != nil {
			params = map[string]any{}
		}
		result, err := handler(params)
		w.Header().Set("Content-Type", "application/json")
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]any{"error": err.Error()})
			return
		}
		json.NewEncoder(w).Encode(result)
	})

	log.Printf("[sidecar] HTTP tool server on :%s", httpPort)
	go http.ListenAndServe(":"+httpPort, mux)
}
