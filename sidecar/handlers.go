// handlers.go — platform-agnostic tool handlers + handler registry
package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// buildHandlers returns the full tool registry for this platform.
func buildHandlers() map[string]ToolHandler {
	m := map[string]ToolHandler{
		// Terminal
		"run_command": handleRunCommand,
		// Filesystem
		"read_file":      handleReadFile,
		"write_file":     handleWriteFile,
		"list_directory": handleListDirectory,
		"delete_file":    handleDeleteFile,
		// Clipboard
		"get_clipboard": handleGetClipboard,
		"set_clipboard": handleSetClipboard,
		// Screenshot
		"capture_screen": handleCaptureScreen,
		// System
		"get_system_info": handleGetSystemInfo,
	}
	// Desktop tools — platform-specific
	addDesktopHandlers(m)

	// New coordinate-input tools (registered after addDesktopHandlers so
	// platform stubs can override if needed — darwin provides real impl)
	m["click_at"]          = handleClickAt
	m["double_click_at"]   = handleDoubleClickAt
	m["right_click_at"]    = handleRightClickAt
	m["get_screen_info"]   = handleGetScreenInfo
	m["get_cursor_pos"]    = handleGetCursorPos
	m["capture_annotated"] = handleCaptureAnnotated
	m["type_clipboard"]    = handleTypeClipboard

	// API relay — forward HTTP calls to the desktop app's local server
	m["api_relay"] = handleAPIRelay

	// Screen intelligence — screenshot capture (vision LLM dispatch in Python)
	m["screen_analyze"] = handleScreenAnalyze

	// Python executor — run arbitrary Python scripts
	m["run_python"] = handleRunPython

	return m
}

// ── Terminal ──────────────────────────────────────────────────────────────────

func handleRunCommand(params map[string]any) (any, error) {
	command, _ := params["command"].(string)
	if command == "" {
		return nil, fmt.Errorf("missing required parameter: command")
	}
	cwd, _ := params["cwd"].(string)
	if cwd == "" {
		cwd, _ = os.Getwd()
	}
	timeoutMs := 30000
	if t, ok := params["timeout"].(float64); ok && t > 0 {
		timeoutMs = int(t)
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeoutMs)*time.Millisecond)
	defer cancel()

	var cmd *exec.Cmd
	if runtime.GOOS == "windows" {
		cmd = exec.CommandContext(ctx, "cmd.exe", "/C", command)
	} else {
		shell := os.Getenv("SHELL")
		if shell == "" {
			shell = "/bin/bash"
		}
		cmd = exec.CommandContext(ctx, shell, "-c", command)
	}
	cmd.Dir = cwd

	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Run()
	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else {
			exitCode = 1
		}
	}

	out := stdout.String()
	if len(out) > 20000 {
		out = out[:20000] + "\n... [truncated]"
	}

	return map[string]any{
		"stdout":    out,
		"stderr":    stderr.String(),
		"exit_code": exitCode,
	}, nil
}

// ── Filesystem ────────────────────────────────────────────────────────────────

func handleReadFile(params map[string]any) (any, error) {
	path, _ := params["path"].(string)
	if path == "" {
		return nil, fmt.Errorf("missing required parameter: path")
	}
	path = expandHome(path)

	info, err := os.Stat(path)
	if err != nil {
		return nil, fmt.Errorf("file not found: %s", path)
	}
	if info.IsDir() {
		return nil, fmt.Errorf("path is a directory: %s", path)
	}
	if info.Size() > 500*1024 {
		return nil, fmt.Errorf("file too large (>500KB): %s", path)
	}

	content, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return map[string]any{"content": string(content), "size": info.Size()}, nil
}

func handleWriteFile(params map[string]any) (any, error) {
	path, _ := params["path"].(string)
	content, _ := params["content"].(string)
	if path == "" {
		return nil, fmt.Errorf("missing required parameter: path")
	}
	path = expandHome(path)

	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return nil, err
	}
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		return nil, err
	}
	return map[string]any{"success": true, "path": path, "bytes": len(content)}, nil
}

func handleListDirectory(params map[string]any) (any, error) {
	path, _ := params["path"].(string)
	if path == "" {
		home, _ := os.UserHomeDir()
		path = home
	}
	path = expandHome(path)

	entries, err := os.ReadDir(path)
	if err != nil {
		return nil, err
	}

	result := make([]map[string]any, 0, len(entries))
	for _, e := range entries {
		t := "file"
		if e.IsDir() {
			t = "directory"
		}
		size := int64(0)
		if info, err := e.Info(); err == nil {
			size = info.Size()
		}
		result = append(result, map[string]any{
			"name": e.Name(),
			"type": t,
			"size": size,
		})
	}
	return map[string]any{"path": path, "entries": result}, nil
}

func handleDeleteFile(params map[string]any) (any, error) {
	path, _ := params["path"].(string)
	if path == "" {
		return nil, fmt.Errorf("missing required parameter: path")
	}
	path = expandHome(path)
	if err := os.Remove(path); err != nil {
		return nil, err
	}
	return map[string]any{"success": true}, nil
}

// ── Screenshot ────────────────────────────────────────────────────────────────

func handleCaptureScreen(params map[string]any) (any, error) {
	tmp := filepath.Join(os.TempDir(), fmt.Sprintf("dai-screen-%d.png", time.Now().UnixMilli()))
	defer os.Remove(tmp)

	if err := platformCaptureScreen(tmp); err != nil {
		return nil, fmt.Errorf("screenshot failed: %w", err)
	}

	data, err := os.ReadFile(tmp)
	if err != nil {
		return nil, err
	}

	result := map[string]any{
		"type":      "screenshot",
		"mime_type": "image/png",
		"data":      base64.StdEncoding.EncodeToString(data),
	}

	// Enrich with scale/dimension metadata on platforms that support it
	if info, err := getScreenDimensions(data); err == nil {
		result["scale_factor"]    = info.scaleFactor
		result["logical_width"]   = info.logicalW
		result["logical_height"]  = info.logicalH
		result["physical_width"]  = info.physicalW
		result["physical_height"] = info.physicalH
	}

	return result, nil
}

// ── System Info ───────────────────────────────────────────────────────────────

func handleGetSystemInfo(params map[string]any) (any, error) {
	hostname, _ := os.Hostname()
	home, _ := os.UserHomeDir()
	return map[string]any{
		"hostname": hostname,
		"os":       runtime.GOOS,
		"arch":     runtime.GOARCH,
		"cpus":     runtime.NumCPU(),
		"home_dir": home,
	}, nil
}

// ── Helpers ───────────────────────────────────────────────────────────────────

func expandHome(path string) string {
	if strings.HasPrefix(path, "~/") {
		home, err := os.UserHomeDir()
		if err == nil {
			return filepath.Join(home, path[2:])
		}
	}
	return path
}

func toInt(v any) int {
	switch n := v.(type) {
	case float64:
		return int(n)
	case int:
		return n
	}
	return 0
}

func toFloat(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case int:
		return float64(n)
	}
	return 0
}

func stringOrDefault(v any, def string) string {
	if s, ok := v.(string); ok && s != "" {
		return s
	}
	return def
}

// ── API Relay ─────────────────────────────────────────────────────────────────
// Forwards HTTP requests to the desktop app's local API server.
// The desktop app runs on a known local port (default 11434 or configured).
// Used by the agent loop to call /api/agent/complete for LLM inference
// using whatever model/provider the user has configured in the desktop app.

func handleAPIRelay(params map[string]any) (any, error) {
	method, _ := params["method"].(string)
	path, _ := params["path"].(string)
	body := params["body"]

	if method == "" {
		method = "GET"
	}
	if path == "" {
		return nil, fmt.Errorf("missing required parameter: path")
	}

	// Desktop app port — read from env or use default
	port := os.Getenv("DECISIONSAI_DESKTOP_PORT")
	if port == "" {
		port = "11434" // default desktop app port
	}
	baseURL := "http://127.0.0.1:" + port

	var reqBody io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("marshal body: %w", err)
		}
		reqBody = bytes.NewReader(b)
	}

	req, err := http.NewRequest(method, baseURL+path, reqBody)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	client := &http.Client{Timeout: 120 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request to desktop app failed: %w", err)
	}
	defer resp.Body.Close()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}

	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("desktop app returned %d: %s", resp.StatusCode, string(respBytes))
	}

	var result any
	if err := json.Unmarshal(respBytes, &result); err != nil {
		// Not JSON — return as string
		return map[string]any{"response": string(respBytes)}, nil
	}
	return result, nil
}

// ── move_mouse ────────────────────────────────────────────────────────────────
// Move the mouse to an element without clicking — useful for hover states
// or when the user says "move the mouse to X".

func handleMoveMouse(params map[string]any) (any, error) {
	// Can take element_id (from tree) or raw x,y coords
	if _, hasID := params["element_id"]; hasID {
		id := toInt(params["element_id"])
		elementCache.mu.Lock()
		var rect map[string]any
		if id >= 0 && id < len(elementCache.elements) {
			if r, ok := elementCache.elements[id]["rect"].(map[string]any); ok {
				rect = r
			}
		}
		elementCache.mu.Unlock()
		if rect == nil {
			return nil, fmt.Errorf("element [%d] not in cache — call get_window_tree first", id)
		}
		x := toInt(rect["x"]) + toInt(rect["w"])/2
		y := toInt(rect["y"]) + toInt(rect["h"])/2
		return platformMoveMouse(x, y)
	}

	x := toInt(params["x"])
	y := toInt(params["y"])
	return platformMoveMouse(x, y)
}

// ── screen_analyze ────────────────────────────────────────────────────────────
// Captures a screenshot and returns the base64 data. The vision/computer-use
// LLM dispatch happens in the Python ScreenAnalyzeTool directly.

func handleScreenAnalyze(params map[string]any) (any, error) {
	screenshotResult, err := handleCaptureScreen(nil)
	if err != nil {
		return nil, fmt.Errorf("screen_analyze: screenshot failed: %w", err)
	}
	return screenshotResult, nil
}

// ── run_python ────────────────────────────────────────────────────────────────
// Execute arbitrary Python code. The model writes Python, this tool runs it.
// Supports optional package installation via pip before execution.

func handleRunPython(params map[string]any) (any, error) {
	code, _ := params["code"].(string)
	if code == "" {
		return nil, fmt.Errorf("missing required parameter: code")
	}

	timeoutMs := 60000
	if t, ok := params["timeout"].(float64); ok && t > 0 {
		timeoutMs = int(t)
	}

	// Optional: install packages before running
	if pkgs, ok := params["packages"].([]any); ok && len(pkgs) > 0 {
		for _, pkg := range pkgs {
			pkgStr, ok := pkg.(string)
			if !ok || pkgStr == "" {
				continue
			}
			installCtx, installCancel := context.WithTimeout(context.Background(), 30*time.Second)
			installCmd := exec.CommandContext(installCtx, "python3", "-m", "pip", "install", "--quiet", pkgStr)
			installOut, installErr := installCmd.CombinedOutput()
			installCancel()
			if installErr != nil {
				return map[string]any{
					"stdout":    string(installOut),
					"stderr":    fmt.Sprintf("pip install %s failed: %v", pkgStr, installErr),
					"exit_code": 1,
				}, nil
			}
		}
	}

	// Write code to temp file
	tmpFile := filepath.Join(os.TempDir(), fmt.Sprintf("dai-python-%d.py", time.Now().UnixMilli()))
	if err := os.WriteFile(tmpFile, []byte(code), 0644); err != nil {
		return nil, fmt.Errorf("run_python: write temp file: %w", err)
	}
	defer os.Remove(tmpFile)

	// Execute
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeoutMs)*time.Millisecond)
	defer cancel()

	cmd := exec.CommandContext(ctx, "python3", tmpFile)
	var stdoutBuf, stderrBuf strings.Builder
	cmd.Stdout = &stdoutBuf
	cmd.Stderr = &stderrBuf

	err := cmd.Run()
	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else {
			exitCode = 1
		}
	}

	out := stdoutBuf.String()
	if len(out) > 20000 {
		out = out[:20000] + "\n... [truncated]"
	}

	return map[string]any{
		"stdout":    out,
		"stderr":    stderrBuf.String(),
		"exit_code": exitCode,
	}, nil
}

// ── screenDimensions — platform-provided ──────────────────────────────────────
// getScreenDimensions is implemented per-platform (darwin returns real info;
// other platforms return a stub/error so the caller omits the fields).

type screenDimInfo struct {
	scaleFactor float64
	logicalW    int
	logicalH    int
	physicalW   int
	physicalH   int
}
