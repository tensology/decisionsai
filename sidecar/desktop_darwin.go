//go:build darwin

package main

import (
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// elementCache is declared in element_cache.go (shared across platforms)

// ── Multi-screen cache ────────────────────────────────────────────────────────
// Stores all connected screens so coordinate resolution can apply per-screen
// offsets for multi-monitor setups.

type screenDef struct {
	logicalW    int
	logicalH    int
	xOffset     int
	yOffset     int
	scaleFactor float64
}

var screensCache struct {
	mu      sync.Mutex
	screens []screenDef
	loaded  bool
}

// loadAllScreens fetches every screen's logical rect + scale factor.
// Falls back through two cheaper methods if Python/Cocoa is unavailable.
func loadAllScreens(force bool) []screenDef {
	screensCache.mu.Lock()
	defer screensCache.mu.Unlock()
	if !force && screensCache.loaded {
		return screensCache.screens
	}

	// ── Method 1: Python/Cocoa (most accurate, all screens) ──────────────────
	py := `import Cocoa,json
r=[]
for s in Cocoa.NSScreen.screens():
    f=s.frame()
    r.append([int(f.size.width),int(f.size.height),int(f.origin.x),int(f.origin.y),s.backingScaleFactor()])
print(json.dumps(r))`
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	out, err := exec.CommandContext(ctx, "python3", "-c", py).Output()
	cancel()
	if err == nil {
		var raw [][]float64
		if json.Unmarshal([]byte(strings.TrimSpace(string(out))), &raw) == nil && len(raw) > 0 {
			defs := make([]screenDef, len(raw))
			for i, s := range raw {
				if len(s) >= 5 {
					defs[i] = screenDef{
						logicalW: int(s[0]), logicalH: int(s[1]),
						xOffset: int(s[2]), yOffset: int(s[3]),
						scaleFactor: s[4],
					}
				}
			}
			screensCache.screens = defs
			screensCache.loaded = true
			return defs
		}
	}

	// ── Method 2: osascript desktop bounds (primary screen, logical size) ────
	osaOut, osaErr := runOsascript(`tell application "Finder" to get bounds of window of desktop`, 5*time.Second)
	if osaErr == nil {
		parts := strings.Split(strings.TrimSpace(osaOut), ",")
		if len(parts) == 4 {
			w, _ := strconv.Atoi(strings.TrimSpace(parts[2]))
			h, _ := strconv.Atoi(strings.TrimSpace(parts[3]))
			if w > 0 && h > 0 {
				// Scale factor unknown via this method; default to 2.0 for Retina.
				defs := []screenDef{{logicalW: w, logicalH: h, scaleFactor: 2.0}}
				screensCache.screens = defs
				screensCache.loaded = true
				return defs
			}
		}
	}

	// ── Method 3: hard fallback — 1440×900 at 2× (safe-ish for M-series) ─────
	defs := []screenDef{{logicalW: 1440, logicalH: 900, scaleFactor: 2.0}}
	screensCache.screens = defs
	screensCache.loaded = true
	return defs
}

// screenRect returns the logical rect for the given screen index (0 = primary).
func screenRect(idx int) screenDef {
	defs := loadAllScreens(false)
	if idx >= 0 && idx < len(defs) {
		return defs[idx]
	}
	if len(defs) > 0 {
		return defs[0]
	}
	return screenDef{logicalW: 1440, logicalH: 900, scaleFactor: 2.0}
}

// logicalScreenSize returns primary screen dimensions for backward compat.
func logicalScreenSize() (w, h int, sf float64) {
	s := screenRect(0)
	return s.logicalW, s.logicalH, s.scaleFactor
}

// resolveCoords converts any coordinate format to logical pixel coords.
//
// Accepted formats (in priority order):
//  1. norm_x / norm_y  (float 0.0–1.0) — fraction of screen width/height
//  2. model_x / model_y + optional model_space (default 1000) — UI-TARS style
//  3. x / y — raw logical pixels (passed through unchanged)
//
// Optional param "screen" (int) selects which monitor; 0 = primary (default).
// For norm and model formats the screen's x/y offset is added automatically.
func resolveCoords(params map[string]any) (int, int) {
	screenIdx := 0
	if s, ok := params["screen"].(float64); ok && s >= 0 {
		screenIdx = int(s)
	}
	sc := screenRect(screenIdx)

	if _, hasNorm := params["norm_x"]; hasNorm {
		nx := toFloat(params["norm_x"])
		ny := toFloat(params["norm_y"])
		return sc.xOffset + int(nx*float64(sc.logicalW)),
			sc.yOffset + int(ny*float64(sc.logicalH))
	}

	if _, hasModel := params["model_x"]; hasModel {
		mx := toFloat(params["model_x"])
		my := toFloat(params["model_y"])
		space := 1000.0
		if s, ok := params["model_space"].(float64); ok && s > 0 {
			space = s
		}
		return sc.xOffset + int(mx/space*float64(sc.logicalW)),
			sc.yOffset + int(my/space*float64(sc.logicalH))
	}

	return toInt(params["x"]), toInt(params["y"])
}

func addDesktopHandlers(m map[string]ToolHandler) {
	m["list_windows"]     = handleListWindows
	m["get_window_tree"]  = handleGetWindowTree
	m["click_element"]    = handleClickElement
	m["move_mouse"]       = handleMoveMouse
	m["type_text"]        = handleTypeText
	m["press_keys"]       = handlePressKeys
	m["launch_app"]       = handleLaunchApp
	m["focus_window"]     = handleFocusWindow
	m["find_element"]     = handleFindElement
	m["get_clipboard"]    = handleGetClipboard
	m["set_clipboard"]    = handleSetClipboard
	m["drag_to"]          = handleDragTo
	m["scroll"]           = handleScroll
	m["wait_for_element"] = handleWaitForElement

	// Coordinate-input tools — registered here so darwin overrides the
	// platform-agnostic stubs that would otherwise land from handlers.go.
	m["click_at"]          = handleClickAt
	m["double_click_at"]   = handleDoubleClickAt
	m["right_click_at"]    = handleRightClickAt
	m["get_screen_info"]   = handleGetScreenInfo
	m["get_cursor_pos"]    = handleGetCursorPos
	m["capture_annotated"] = handleCaptureAnnotated
	m["type_clipboard"]    = handleTypeClipboard
}

func platformCaptureScreen(outputPath string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return exec.CommandContext(ctx, "screencapture", "-x", outputPath).Run()
}

// getScreenDimensions reads physical PNG dimensions from the header bytes and
// compares to the logical screen size to compute scale factor.
func getScreenDimensions(pngData []byte) (screenDimInfo, error) {
	// PNG signature is 8 bytes; IHDR chunk starts at byte 8.
	// Width  = bytes 16-19 (big-endian uint32)
	// Height = bytes 20-23 (big-endian uint32)
	if len(pngData) < 24 {
		return screenDimInfo{}, fmt.Errorf("PNG too short")
	}
	physW := int(binary.BigEndian.Uint32(pngData[16:20]))
	physH := int(binary.BigEndian.Uint32(pngData[20:24]))

	lw, lh, _ := logicalScreenSize()
	sf := 1.0
	if lw > 0 && physW > 0 {
		sf = float64(physW) / float64(lw)
	}

	return screenDimInfo{
		scaleFactor: sf,
		logicalW:    lw,
		logicalH:    lh,
		physicalW:   physW,
		physicalH:   physH,
	}, nil
}

func platformMoveMouse(x, y int) (any, error) {
	if _, err := exec.LookPath("cliclick"); err == nil {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		err := exec.CommandContext(ctx, "cliclick", fmt.Sprintf("m:%d,%d", x, y)).Run()
		return map[string]any{"success": err == nil, "x": x, "y": y}, err
	}
	py := fmt.Sprintf(`
from Quartz.CoreGraphics import *
pt = CGPointMake(%d,%d)
ev = CGEventCreateMouseEvent(None,kCGEventMouseMoved,pt,kCGMouseButtonLeft)
CGEventPost(kCGHIDEventTap,ev)`, x, y)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	err := exec.CommandContext(ctx, "python3", "-c", py).Run()
	return map[string]any{"success": err == nil, "x": x, "y": y}, err
}

func handleGetClipboard(params map[string]any) (any, error) {
	out, err := exec.Command("pbpaste").Output()
	if err != nil {
		return nil, err
	}
	return map[string]any{"content": strings.TrimRight(string(out), "\n")}, nil
}

func handleSetClipboard(params map[string]any) (any, error) {
	content, _ := params["content"].(string)
	cmd := exec.Command("pbcopy")
	cmd.Stdin = strings.NewReader(content)
	return map[string]any{"success": true}, cmd.Run()
}

// ── list_windows ──────────────────────────────────────────────────────────────

func handleListWindows(params map[string]any) (any, error) {
	script := `
tell application "System Events"
	set out to ""
	set fg to name of first process whose frontmost is true
	repeat with proc in (every process whose background only is false)
		set pn to name of proc
		set pp to unix id of proc
		try
			repeat with w in windows of proc
				set wt to name of w
				set wp to position of w
				set ws to size of w
				set out to out & pn & "|||" & pp & "|||" & wt & "|||" & (item 1 of wp) & "|||" & (item 2 of wp) & "|||" & (item 1 of ws) & "|||" & (item 2 of ws) & "|||" & (pn = fg) & linefeed
			end repeat
		end try
	end repeat
	return out
end tell`

	out, err := runOsascript(script, 15*time.Second)
	if err != nil {
		return nil, fmt.Errorf("list_windows: %w", err)
	}

	windows := []map[string]any{}
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		parts := strings.Split(line, "|||")
		if len(parts) < 8 {
			continue
		}
		pid, _ := strconv.Atoi(strings.TrimSpace(parts[1]))
		left, _ := strconv.Atoi(strings.TrimSpace(parts[3]))
		top, _ := strconv.Atoi(strings.TrimSpace(parts[4]))
		w, _ := strconv.Atoi(strings.TrimSpace(parts[5]))
		h, _ := strconv.Atoi(strings.TrimSpace(parts[6]))
		windows = append(windows, map[string]any{
			"title":         strings.TrimSpace(parts[2]),
			"pid":           pid,
			"process_name":  strings.TrimSpace(parts[0]),
			"left":          left,
			"top":           top,
			"right":         left + w,
			"bottom":        top + h,
			"is_foreground": strings.TrimSpace(parts[7]) == "true",
		})
	}
	return map[string]any{"windows": windows}, nil
}

// ── get_window_tree ───────────────────────────────────────────────────────────

func handleGetWindowTree(params map[string]any) (any, error) {
	pid := 0
	if v, ok := params["pid"].(float64); ok {
		pid = int(v)
	}
	depth := 3
	if v, ok := params["depth"].(float64); ok {
		depth = int(v)
	}
	appName, _ := params["app_name"].(string)

	if pid == 0 {
		if appName != "" {
			// Look up PID by app name across all processes
			script := fmt.Sprintf(`tell application "System Events" to unix id of first process whose name contains %q`, appName)
			pidOut, err := runOsascript(script, 5*time.Second)
			if err != nil {
				// Try case-insensitive partial match
				script2 := fmt.Sprintf(`
tell application "System Events"
	repeat with proc in (every process whose background only is false)
		if name of proc contains %q then return unix id of proc
	end repeat
	return 0
end tell`, appName)
				pidOut, err = runOsascript(script2, 5*time.Second)
				if err != nil {
					return nil, fmt.Errorf("find app '%s': %w", appName, err)
				}
			}
			pid, _ = strconv.Atoi(strings.TrimSpace(pidOut))
		} else {
			pidOut, err := runOsascript(`tell application "System Events" to unix id of first process whose frontmost is true`, 5*time.Second)
			if err != nil {
				return nil, fmt.Errorf("get frontmost pid: %w", err)
			}
			pid, _ = strconv.Atoi(strings.TrimSpace(pidOut))
		}
	}

	js := fmt.Sprintf(`
ObjC.import('stdlib')
var se = Application('System Events')
var maxDepth = %d
var procs = se.processes.whose({unixId: %d})
if (procs.length === 0) {
    JSON.stringify({error: 'Process not found', pid: %d})
} else {
var proc = procs[0]
var elements = []
function walk(el, depth) {
    if (depth > maxDepth || elements.length > 300) return
    try {
        var role = el.role()
        var name = ''
        try { name = el.name() || '' } catch(e) {}
        var pos = [0,0], sz = [0,0]
        try { pos = el.position(); sz = el.size() } catch(e) {}
        if (sz[0] > 0 && sz[1] > 0) {
            elements.push({
                id: elements.length,
                name: String(name).substring(0,100),
                control_type: role,
                enabled: true,
                rect: {x: pos[0], y: pos[1], w: sz[0], h: sz[1]}
            })
        }
        var ch = el.uiElements()
        for (var i = 0; i < ch.length && i < 50; i++) walk(ch[i], depth+1)
    } catch(e) {}
}
var wins = proc.windows()
for (var w = 0; w < wins.length; w++) walk(wins[w], 0)
var title = ''
try { title = wins.length > 0 ? wins[0].name() : '' } catch(e) {}
JSON.stringify({window_title: title, pid: %d, element_count: elements.length, elements: elements})
}`, depth, pid, pid, pid)

	out, err := runOsascriptJS(js, 20*time.Second)
	if err != nil {
		return nil, fmt.Errorf("get_window_tree: %w", err)
	}

	var tree map[string]any
	if err := json.Unmarshal([]byte(out), &tree); err != nil {
		return nil, fmt.Errorf("parse tree: %w", err)
	}

	// Cache elements for click_element
	if elems, ok := tree["elements"].([]any); ok {
		elementCache.mu.Lock()
		elementCache.elements = make([]map[string]any, 0, len(elems))
		for _, e := range elems {
			if m, ok := e.(map[string]any); ok {
				elementCache.elements = append(elementCache.elements, m)
			}
		}
		elementCache.mu.Unlock()
	}
	return tree, nil
}

// ── click_element ─────────────────────────────────────────────────────────────

func handleClickElement(params map[string]any) (any, error) {
	id := toInt(params["element_id"])
	action, _ := params["action"].(string)
	if action == "" {
		action = "click"
	}

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

	var err error
	switch action {
	case "click":
		err = clickAt(x, y)
	case "double_click":
		err = doubleClickAt(x, y)
	case "right_click":
		err = rightClickAt(x, y)
	default:
		err = clickAt(x, y)
	}
	if err != nil {
		return nil, err
	}
	return map[string]any{"success": true, "action": action, "x": x, "y": y}, nil
}

func clickAt(x, y int) error {
	if _, err := exec.LookPath("cliclick"); err == nil {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return exec.CommandContext(ctx, "cliclick", fmt.Sprintf("c:%d,%d", x, y)).Run()
	}
	py := fmt.Sprintf(`
from Quartz.CoreGraphics import *
import time
pt = CGPointMake(%d,%d)
ev = CGEventCreateMouseEvent(None,kCGEventLeftMouseDown,pt,kCGMouseButtonLeft)
CGEventPost(kCGHIDEventTap,ev)
time.sleep(0.05)
ev = CGEventCreateMouseEvent(None,kCGEventLeftMouseUp,pt,kCGMouseButtonLeft)
CGEventPost(kCGHIDEventTap,ev)`, x, y)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return exec.CommandContext(ctx, "python3", "-c", py).Run()
}

func doubleClickAt(x, y int) error {
	if _, err := exec.LookPath("cliclick"); err == nil {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return exec.CommandContext(ctx, "cliclick", fmt.Sprintf("dc:%d,%d", x, y)).Run()
	}
	py := fmt.Sprintf(`
from Quartz.CoreGraphics import *
import time
pt = CGPointMake(%d,%d)
for _ in range(2):
    ev = CGEventCreateMouseEvent(None,kCGEventLeftMouseDown,pt,kCGMouseButtonLeft)
    CGEventSetIntegerValueField(ev,kCGMouseEventClickState,2)
    CGEventPost(kCGHIDEventTap,ev)
    ev = CGEventCreateMouseEvent(None,kCGEventLeftMouseUp,pt,kCGMouseButtonLeft)
    CGEventSetIntegerValueField(ev,kCGMouseEventClickState,2)
    CGEventPost(kCGHIDEventTap,ev)
    time.sleep(0.05)`, x, y)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return exec.CommandContext(ctx, "python3", "-c", py).Run()
}

func rightClickAt(x, y int) error {
	if _, err := exec.LookPath("cliclick"); err == nil {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return exec.CommandContext(ctx, "cliclick", fmt.Sprintf("rc:%d,%d", x, y)).Run()
	}
	py := fmt.Sprintf(`
from Quartz.CoreGraphics import *
import time
pt = CGPointMake(%d,%d)
ev = CGEventCreateMouseEvent(None,kCGEventRightMouseDown,pt,kCGMouseButtonRight)
CGEventPost(kCGHIDEventTap,ev)
time.sleep(0.05)
ev = CGEventCreateMouseEvent(None,kCGEventRightMouseUp,pt,kCGMouseButtonRight)
CGEventPost(kCGHIDEventTap,ev)`, x, y)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return exec.CommandContext(ctx, "python3", "-c", py).Run()
}

// ── click_at (NEW) ────────────────────────────────────────────────────────────
// Clicks at raw, normalized, or model-space coordinates.
//
// Params:
//   x, y            int   — raw logical pixel coords
//   norm_x, norm_y  float — 0.0–1.0 normalized (multiplied by screen size)
//   model_x, model_y int  — UI-TARS style; divide by model_space (default 1000) then multiply by screen
//   model_space     int   — denominator for model coords (default 1000)
//   action          str   — "click" | "double_click" | "right_click" (default "click")

func handleClickAt(params map[string]any) (any, error) {
	x, y := resolveCoords(params)
	action := stringOrDefault(params["action"], "click")

	if _, err := platformMoveMouse(x, y); err != nil {
		return nil, fmt.Errorf("move mouse: %w", err)
	}
	time.Sleep(30 * time.Millisecond)

	var err error
	switch action {
	case "double_click":
		err = doubleClickAt(x, y)
	case "right_click":
		err = rightClickAt(x, y)
	default:
		err = clickAt(x, y)
		action = "click"
	}
	if err != nil {
		return nil, fmt.Errorf("click_at: %w", err)
	}
	return map[string]any{"success": true, "action": action, "x": x, "y": y}, nil
}

// ── double_click_at (NEW) ─────────────────────────────────────────────────────

func handleDoubleClickAt(params map[string]any) (any, error) {
	params["action"] = "double_click"
	return handleClickAt(params)
}

// ── right_click_at (NEW) ──────────────────────────────────────────────────────

func handleRightClickAt(params map[string]any) (any, error) {
	params["action"] = "right_click"
	return handleClickAt(params)
}

// ── get_screen_info (NEW) ─────────────────────────────────────────────────────

func handleGetScreenInfo(params map[string]any) (any, error) {
	py := `import Cocoa,json,sys
screens=[]
for i,s in enumerate(Cocoa.NSScreen.screens()):
    f=s.frame()
    screens.append({
        "index":i,
        "logical_width":int(f.size.width),
        "logical_height":int(f.size.height),
        "x_offset":int(f.origin.x),
        "y_offset":int(f.origin.y),
        "scale_factor":s.backingScaleFactor(),
        "is_primary":i==0
    })
print(json.dumps(screens))`

	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "python3", "-c", py).Output()
	if err != nil {
		return nil, fmt.Errorf("get_screen_info: %w", err)
	}

	var screens []map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(string(out))), &screens); err != nil {
		return nil, fmt.Errorf("parse screen info: %w", err)
	}

	result := map[string]any{"screens": screens}
	if len(screens) > 0 {
		result["primary"] = screens[0]
	}
	return result, nil
}

// ── get_cursor_pos (NEW) ──────────────────────────────────────────────────────

func handleGetCursorPos(params map[string]any) (any, error) {
	py := `from Quartz.CoreGraphics import CGEventCreate,kCGEventNull,CGEventGetLocation
import json
ev=CGEventCreate(None)
pt=CGEventGetLocation(ev)
print(json.dumps({"x":int(pt.x),"y":int(pt.y)}))`

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "python3", "-c", py).Output()
	if err != nil {
		return nil, fmt.Errorf("get_cursor_pos: %w", err)
	}

	var pos map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(string(out))), &pos); err != nil {
		return nil, fmt.Errorf("parse cursor pos: %w", err)
	}
	return pos, nil
}

// ── type_text ─────────────────────────────────────────────────────────────────

func handleTypeText(params map[string]any) (any, error) {
	text, _ := params["text"].(string)
	if text == "" {
		return nil, fmt.Errorf("missing required parameter: text")
	}
	if elemID, ok := params["element_id"]; ok {
		if _, err := handleClickElement(map[string]any{"element_id": elemID}); err != nil {
			return nil, fmt.Errorf("focus element: %w", err)
		}
		time.Sleep(100 * time.Millisecond)
	}

	// Use clipboard method when explicitly requested or when text contains
	// non-ASCII characters (osascript keystroke breaks on unicode).
	useClipboard := false
	if uc, ok := params["use_clipboard"].(bool); ok {
		useClipboard = uc
	}
	if !useClipboard {
		for _, r := range text {
			if r > 127 {
				useClipboard = true
				break
			}
		}
	}

	if useClipboard {
		return typeViaClipboard(text)
	}

	// Properly escape the text for osascript string literal:
	// backslash must be doubled, double-quotes must be escaped,
	// newlines need special handling via key code.
	escaped := strings.ReplaceAll(text, `\`, `\\`)
	escaped = strings.ReplaceAll(escaped, `"`, `\"`)
	script := fmt.Sprintf(`tell application "System Events" to keystroke "%s"`, escaped)
	if _, err := runOsascript(script, 10*time.Second); err != nil {
		return nil, fmt.Errorf("type_text: %w", err)
	}
	return map[string]any{"success": true}, nil
}

// ── type_clipboard (NEW) ──────────────────────────────────────────────────────
// Types text by writing to the clipboard then pressing Cmd+V.
// Reliable for unicode, emoji, and any special characters.

func handleTypeClipboard(params map[string]any) (any, error) {
	text, _ := params["text"].(string)
	if text == "" {
		return nil, fmt.Errorf("missing required parameter: text")
	}
	return typeViaClipboard(text)
}

func typeViaClipboard(text string) (any, error) {
	// Write to clipboard via pbcopy
	pbcopy := exec.Command("pbcopy")
	pbcopy.Stdin = strings.NewReader(text)
	if err := pbcopy.Run(); err != nil {
		return nil, fmt.Errorf("pbcopy failed: %w", err)
	}

	// Press Cmd+V to paste
	script := `tell application "System Events" to keystroke "v" using command down`
	if _, err := runOsascript(script, 5*time.Second); err != nil {
		return nil, fmt.Errorf("paste failed: %w", err)
	}
	return map[string]any{"success": true, "method": "clipboard"}, nil
}

// ── press_keys ────────────────────────────────────────────────────────────────

func handlePressKeys(params map[string]any) (any, error) {
	keys, _ := params["keys"].(string)
	if keys == "" {
		return nil, fmt.Errorf("missing required parameter: keys")
	}
	script := keysToOsascript(keys)
	if _, err := runOsascript(script, 5*time.Second); err != nil {
		return nil, fmt.Errorf("press_keys: %w", err)
	}
	return map[string]any{"success": true, "keys": keys}, nil
}

// ── launch_app ────────────────────────────────────────────────────────────────

func handleLaunchApp(params map[string]any) (any, error) {
	app, _ := params["executable"].(string)
	if app == "" {
		return nil, fmt.Errorf("missing required parameter: executable")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := exec.CommandContext(ctx, "open", "-a", app).Run(); err != nil {
		// Try as direct path
		if err2 := exec.CommandContext(ctx, app).Start(); err2 != nil {
			return nil, fmt.Errorf("launch_app: %w", err)
		}
	}
	return map[string]any{"success": true, "app": app}, nil
}

// ── focus_window ──────────────────────────────────────────────────────────────

func handleFocusWindow(params map[string]any) (any, error) {
	pid := toInt(params["pid"])
	if pid == 0 {
		return nil, fmt.Errorf("missing required parameter: pid")
	}
	script := fmt.Sprintf(`tell application "System Events"
	set proc to first process whose unix id is %d
	set frontmost of proc to true
end tell`, pid)
	_, err := runOsascript(script, 5*time.Second)
	return map[string]any{"success": err == nil, "pid": pid}, nil
}

// ── find_element ──────────────────────────────────────────────────────────────

func handleFindElement(params map[string]any) (any, error) {
	name, _ := params["name"].(string)
	controlType, _ := params["control_type"].(string)
	appName, _ := params["app_name"].(string)

	// First search: frontmost window (or named app if specified)
	if _, err := handleGetWindowTree(params); err != nil {
		return nil, err
	}

	elementCache.mu.Lock()
	matches := findInCache(name, controlType)
	elementCache.mu.Unlock()

	// If nothing found and no specific app was requested, search all visible apps
	if len(matches) == 0 && appName == "" {
		allAppsScript := `tell application "System Events" to get unix id of every process whose background only is false`
		out, err := runOsascript(allAppsScript, 10*time.Second)
		if err == nil {
			for _, pidStr := range strings.Split(out, ",") {
				pidStr = strings.TrimSpace(pidStr)
				pid, err := strconv.Atoi(pidStr)
				if err != nil || pid == 0 {
					continue
				}
				p := map[string]any{"pid": float64(pid), "depth": float64(3)}
				if _, err := handleGetWindowTree(p); err != nil {
					continue
				}
				elementCache.mu.Lock()
				m := findInCache(name, controlType)
				elementCache.mu.Unlock()
				if len(m) > 0 {
					matches = m
					break
				}
			}
		}
	}

	return map[string]any{"match_count": len(matches), "elements": matches}, nil
}

// findInCache returns elements matching name and/or controlType from the cache.
// Caller must hold elementCache.mu.
func findInCache(name, controlType string) []map[string]any {
	var matches []map[string]any
	nameLower := strings.ToLower(name)
	for _, el := range elementCache.elements {
		elName := strings.ToLower(fmt.Sprintf("%v", el["name"]))
		if name != "" && !strings.Contains(elName, nameLower) {
			continue
		}
		if controlType != "" && el["control_type"] != controlType {
			continue
		}
		matches = append(matches, el)
	}
	return matches
}

// ── drag_to ───────────────────────────────────────────────────────────────────
// Drag from one position to another. Supports element IDs or raw coordinates.
// from_element_id / to_element_id: use cached element centers
// from_x,from_y / to_x,to_y: raw screen coordinates

func handleDragTo(params map[string]any) (any, error) {
	var fromX, fromY, toX, toY int

	// resolveEndpoint picks the coordinate for one drag endpoint.
	// Priority: element_id → norm → model → raw x/y.
	// prefix is "from" or "to"; the shared "screen" param applies to both.
	resolveEndpoint := func(prefix string) (int, int, error) {
		idKey := prefix + "_element_id"
		if idVal, ok := params[idKey]; ok {
			id := toInt(idVal)
			elementCache.mu.Lock()
			var rect map[string]any
			if id >= 0 && id < len(elementCache.elements) {
				if r, ok := elementCache.elements[id]["rect"].(map[string]any); ok {
					rect = r
				}
			}
			elementCache.mu.Unlock()
			if rect == nil {
				return 0, 0, fmt.Errorf("%s element [%d] not in cache", prefix, id)
			}
			return toInt(rect["x"]) + toInt(rect["w"])/2,
				toInt(rect["y"]) + toInt(rect["h"])/2, nil
		}
		// Build a mini-params map for resolveCoords using prefixed keys.
		ep := map[string]any{}
		if v, ok := params[prefix+"_norm_x"]; ok {
			ep["norm_x"] = v
			ep["norm_y"] = params[prefix+"_norm_y"]
		} else if v, ok := params[prefix+"_model_x"]; ok {
			ep["model_x"] = v
			ep["model_y"] = params[prefix+"_model_y"]
			if ms, ok := params["model_space"]; ok {
				ep["model_space"] = ms
			}
		} else {
			ep["x"] = params[prefix+"_x"]
			ep["y"] = params[prefix+"_y"]
		}
		if sc, ok := params["screen"]; ok {
			ep["screen"] = sc
		}
		x, y := resolveCoords(ep)
		return x, y, nil
	}

	var err error
	fromX, fromY, err = resolveEndpoint("from")
	if err != nil {
		return nil, err
	}
	toX, toY, err = resolveEndpoint("to")
	if err != nil {
		return nil, err
	}

	durationMs := 500
	if d, ok := params["duration_ms"].(float64); ok && d > 0 {
		durationMs = int(d)
	}

	steps := 20
	sleepPerStep := float64(durationMs) / 1000.0 / float64(steps)

	py := fmt.Sprintf(`
from Quartz.CoreGraphics import *
import time
steps = %d
for i in range(steps + 1):
    t = i / steps
    x = %d + (%d - %d) * t
    y = %d + (%d - %d) * t
    pt = CGPointMake(x, y)
    if i == 0:
        ev = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, pt, kCGMouseButtonLeft)
    elif i == steps:
        ev = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, pt, kCGMouseButtonLeft)
    else:
        ev = CGEventCreateMouseEvent(None, kCGEventLeftMouseDragged, pt, kCGMouseButtonLeft)
    CGEventPost(kCGHIDEventTap, ev)
    time.sleep(%f)
`, steps, fromX, toX, fromX, fromY, toY, fromY, sleepPerStep)

	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(durationMs+5000)*time.Millisecond)
	defer cancel()
	err = exec.CommandContext(ctx, "python3", "-c", py).Run()
	if err != nil {
		return nil, fmt.Errorf("drag_to failed: %w", err)
	}
	return map[string]any{"success": true, "from_x": fromX, "from_y": fromY, "to_x": toX, "to_y": toY}, nil
}

// ── scroll ────────────────────────────────────────────────────────────────────
// Scroll at the current mouse position or at specified coordinates.
// direction: "up", "down", "left", "right"
// amount:    number of scroll units (default 3)
// steps:     how many discrete events to send for smoother scrolling (default 1, max 10)

func handleScroll(params map[string]any) (any, error) {
	direction, _ := params["direction"].(string)
	if direction == "" {
		direction = "down"
	}
	amount := 3
	if a, ok := params["amount"].(float64); ok && a > 0 {
		amount = int(a)
	}
	steps := 1
	if s, ok := params["steps"].(float64); ok && s > 0 {
		steps = int(s)
		if steps > 10 {
			steps = 10
		}
	}

	// Optional: scroll at specific coordinates (move mouse first).
	// Accepts raw x/y, norm_x/norm_y, or model_x/model_y.
	hasPos := func() bool {
		for _, k := range []string{"x", "norm_x", "model_x"} {
			if _, ok := params[k]; ok {
				return true
			}
		}
		return false
	}
	if hasPos() {
		px, py := resolveCoords(params)
		platformMoveMouse(px, py)
		time.Sleep(50 * time.Millisecond)
	}

	var deltaX, deltaY int
	switch direction {
	case "up":
		deltaY = amount
	case "down":
		deltaY = -amount
	case "left":
		deltaX = amount
	case "right":
		deltaX = -amount
	}

	// When steps > 1 divide the total delta across steps for smooth scrolling.
	stepDeltaY := deltaY / steps
	stepDeltaX := deltaX / steps
	if stepDeltaY == 0 && deltaY != 0 {
		stepDeltaY = deltaY // avoid rounding to zero for small amounts
	}
	if stepDeltaX == 0 && deltaX != 0 {
		stepDeltaX = deltaX
	}

	py := fmt.Sprintf(`
from Quartz.CoreGraphics import *
import time
steps=%d
step_dy=%d
step_dx=%d
for i in range(steps):
    ev = CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitLine, 2, step_dy, step_dx)
    CGEventPost(kCGHIDEventTap, ev)
    if steps > 1:
        time.sleep(0.016)
`, steps, stepDeltaY, stepDeltaX)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	err := exec.CommandContext(ctx, "python3", "-c", py).Run()
	if err != nil {
		return nil, fmt.Errorf("scroll failed: %w", err)
	}
	return map[string]any{"success": true, "direction": direction, "amount": amount, "steps": steps}, nil
}

// ── wait_for_element ──────────────────────────────────────────────────────────
// Poll the accessibility tree until an element matching name/control_type appears.
// Returns the element when found, or error on timeout.

func handleWaitForElement(params map[string]any) (any, error) {
	name, _ := params["name"].(string)
	controlType, _ := params["control_type"].(string)
	if name == "" && controlType == "" {
		return nil, fmt.Errorf("at least one of name or control_type is required")
	}

	timeoutMs := 10000
	if t, ok := params["timeout"].(float64); ok && t > 0 {
		timeoutMs = int(t)
	}
	intervalMs := 500
	if i, ok := params["interval"].(float64); ok && i > 0 {
		intervalMs = int(i)
	}

	deadline := time.Now().Add(time.Duration(timeoutMs) * time.Millisecond)
	for time.Now().Before(deadline) {
		// Refresh the tree
		treeParams := make(map[string]any)
		if appName, ok := params["app_name"]; ok {
			treeParams["app_name"] = appName
		}
		if pid, ok := params["pid"]; ok {
			treeParams["pid"] = pid
		}
		handleGetWindowTree(treeParams)

		elementCache.mu.Lock()
		matches := findInCache(name, controlType)
		elementCache.mu.Unlock()

		if len(matches) > 0 {
			return map[string]any{"found": true, "match_count": len(matches), "elements": matches}, nil
		}

		time.Sleep(time.Duration(intervalMs) * time.Millisecond)
	}

	return map[string]any{"found": false, "match_count": 0, "elements": []any{}}, nil
}

// ── capture_annotated (NEW — SoM) ─────────────────────────────────────────────
// Takes a screenshot and overlays numbered bounding boxes for each cached element
// (Set-of-Marks / SoM annotation). Requires Pillow installed in python3.

func handleCaptureAnnotated(params map[string]any) (any, error) {
	// 1. Capture raw screenshot
	tmp := filepath.Join(os.TempDir(), fmt.Sprintf("dai-annot-%d.png", time.Now().UnixMilli()))
	defer os.Remove(tmp)
	if err := platformCaptureScreen(tmp); err != nil {
		return nil, fmt.Errorf("capture_annotated: screenshot failed: %w", err)
	}
	rawData, err := os.ReadFile(tmp)
	if err != nil {
		return nil, err
	}

	// 2. Collect elements from cache (call get_window_tree first if cache is empty)
	elementCache.mu.Lock()
	cachedElements := make([]map[string]any, len(elementCache.elements))
	copy(cachedElements, elementCache.elements)
	elementCache.mu.Unlock()

	if len(cachedElements) == 0 {
		if _, err := handleGetWindowTree(map[string]any{}); err == nil {
			elementCache.mu.Lock()
			cachedElements = make([]map[string]any, len(elementCache.elements))
			copy(cachedElements, elementCache.elements)
			elementCache.mu.Unlock()
		}
	}

	// 3. Determine scale factor from PNG header
	_, _, sf := logicalScreenSize()
	if sf <= 0 {
		sf = 1.0
	}

	// 4. Serialize data for Python
	elemsJSON, err := json.Marshal(cachedElements)
	if err != nil {
		return nil, fmt.Errorf("marshal elements: %w", err)
	}
	imgB64 := base64.StdEncoding.EncodeToString(rawData)
	inputJSON, err := json.Marshal(map[string]any{
		"image":        imgB64,
		"elements":     json.RawMessage(elemsJSON),
		"scale_factor": sf,
	})
	if err != nil {
		return nil, err
	}

	// Write input JSON to temp file to avoid argument-length limits
	inFile := filepath.Join(os.TempDir(), fmt.Sprintf("dai-annot-in-%d.json", time.Now().UnixMilli()))
	if err := os.WriteFile(inFile, inputJSON, 0600); err != nil {
		return nil, err
	}
	defer os.Remove(inFile)

	pyCode := fmt.Sprintf(`
import json,base64,io,sys
try:
    from PIL import Image,ImageDraw
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable,'-m','pip','install','--quiet','Pillow'])
    from PIL import Image,ImageDraw

with open(%q) as f:
    data=json.load(f)
img_bytes=base64.b64decode(data["image"])
elements=data["elements"]
scale=data.get("scale_factor",1.0)

img=Image.open(io.BytesIO(img_bytes)).convert("RGBA")
overlay=Image.new("RGBA",img.size,(0,0,0,0))
draw=ImageDraw.Draw(overlay)

colors=["#FF4444","#44BB44","#4477FF","#FFAA00","#FF44FF","#00CCCC","#FF8800","#8844FF"]
for el in elements:
    r=el.get("rect")
    if not r:
        continue
    x1=int(r["x"]*scale)
    y1=int(r["y"]*scale)
    x2=int((r["x"]+r["w"])*scale)
    y2=int((r["y"]+r["h"])*scale)
    if x2<=x1 or y2<=y1:
        continue
    color=colors[el["id"]%%len(colors)]
    cr=int(color[1:3],16)
    cg=int(color[3:5],16)
    cb=int(color[5:7],16)
    draw.rectangle([x1,y1,x2,y2],outline=(cr,cg,cb,200),width=2)
    label=str(el["id"])
    lw=len(label)*7+6
    draw.rectangle([x1,y1,x1+lw,y1+15],fill=(cr,cg,cb,220))
    draw.text((x1+3,y1+2),label,fill=(255,255,255,255))

result=Image.alpha_composite(img,overlay).convert("RGB")
buf=io.BytesIO()
result.save(buf,format="PNG")
print(base64.b64encode(buf.getvalue()).decode())
`, inFile)

	pyScript := filepath.Join(os.TempDir(), fmt.Sprintf("dai-annot-%d.py", time.Now().UnixMilli()))
	if err := os.WriteFile(pyScript, []byte(pyCode), 0644); err != nil {
		return nil, err
	}
	defer os.Remove(pyScript)

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	annotOut, err := exec.CommandContext(ctx, "python3", pyScript).Output()
	if err != nil {
		// Return unannotated image if Pillow not available
		return map[string]any{
			"type":           "screenshot",
			"mime_type":      "image/png",
			"data":           imgB64,
			"annotated":      false,
			"annotation_err": err.Error(),
			"element_count":  len(cachedElements),
		}, nil
	}

	return map[string]any{
		"type":          "screenshot",
		"mime_type":     "image/png",
		"data":          strings.TrimSpace(string(annotOut)),
		"annotated":     true,
		"element_count": len(cachedElements),
		"scale_factor":  sf,
	}, nil
}

// ── Helpers ───────────────────────────────────────────────────────────────────

func runOsascript(script string, timeout time.Duration) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	out, err := exec.CommandContext(ctx, "osascript", "-e", script).Output()
	return strings.TrimSpace(string(out)), err
}

func runOsascriptJS(script string, timeout time.Duration) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	out, err := exec.CommandContext(ctx, "osascript", "-l", "JavaScript", "-e", script).Output()
	return strings.TrimSpace(string(out)), err
}

// keysToOsascript converts a key combo string to an osascript command.
//
// Accepts comma or plus as separator, so "cmd,s" and "cmd+s" are equivalent.
// Modifier aliases: ctrl/control/⌃, cmd/command/⌘, opt/option/alt/⌥, shift/⇧.
// Special key names map to key codes; all others are sent as keystroke characters.
func keysToOsascript(keys string) string {
	// Normalise separators: replace "+" with "," but only when "+" is used as
	// a key separator rather than as the literal plus character.
	// Heuristic: if the string contains no comma but does contain "+", treat
	// "+" as the separator.
	norm := keys
	if !strings.Contains(keys, ",") && strings.Contains(keys, "+") {
		norm = strings.ReplaceAll(keys, "+", ",")
	}

	parts := strings.Split(strings.ToLower(strings.TrimSpace(norm)), ",")
	var mods []string
	var keyParts []string
	for _, p := range parts {
		p = strings.TrimSpace(p)
		switch p {
		case "ctrl", "control", "⌃":
			mods = append(mods, "control down")
		case "alt", "option", "opt", "⌥":
			mods = append(mods, "option down")
		case "shift", "⇧":
			mods = append(mods, "shift down")
		case "cmd", "command", "⌘":
			mods = append(mods, "command down")
		default:
			keyParts = append(keyParts, p)
		}
	}
	using := ""
	if len(mods) > 0 {
		using = fmt.Sprintf(" using {%s}", strings.Join(mods, ", "))
	}
	if len(keyParts) == 0 {
		return `tell application "System Events" to keystroke ""`
	}
	key := keyParts[0]

	// macOS virtual key codes (decimal).
	// Reference: HIToolbox/Events.h
	specialKeys := map[string]int{
		// Core
		"return": 36, "enter": 36,
		"tab": 48,
		"space": 49,
		"delete": 51, "backspace": 51,
		"escape": 53, "esc": 53,
		"caps_lock": 57,
		"fn": 63,

		// Forward delete
		"forward_delete": 117, "del": 117,

		// Arrow keys
		"left": 123, "right": 124, "down": 125, "up": 126,

		// Navigation
		"home": 115, "end": 119,
		"page_up": 116, "pageup": 116,
		"page_down": 121, "pagedown": 121,

		// Function keys F1–F15
		"f1": 122, "f2": 120, "f3": 99, "f4": 118,
		"f5": 96, "f6": 97, "f7": 98, "f8": 100,
		"f9": 101, "f10": 109, "f11": 103, "f12": 111,
		"f13": 105, "f14": 107, "f15": 113,

		// Numpad digits
		"num0": 82, "numpad0": 82,
		"num1": 83, "numpad1": 83,
		"num2": 84, "numpad2": 84,
		"num3": 85, "numpad3": 85,
		"num4": 86, "numpad4": 86,
		"num5": 87, "numpad5": 87,
		"num6": 88, "numpad6": 88,
		"num7": 89, "numpad7": 89,
		"num8": 91, "numpad8": 91,
		"num9": 92, "numpad9": 92,

		// Numpad operators / special
		"numpad_decimal": 65, "numpad_dot": 65,
		"numpad_multiply": 67, "numpad_star": 67,
		"numpad_plus": 69, "numpad_add": 69,
		"numpad_clear": 71,
		"numpad_divide": 75, "numpad_slash": 75,
		"numpad_enter": 76,
		"numpad_minus": 78, "numpad_subtract": 78,
		"numpad_equals": 81,

		// Media / special hardware keys
		"volume_up": 72, "volume_mute": 74, "volume_down": 73,
		"mute": 74,
		"play_pause": 100, // maps to F8 on Apple keyboards; use key code directly
		"brightness_up": 144, "brightness_down": 145,
		"mission_control": 160, "launchpad": 131,

		// Help / print screen equivalent
		"help": 114,

		// Scroll lock / pause — no standard macOS key codes; skip
	}

	if code, ok := specialKeys[key]; ok {
		return fmt.Sprintf(`tell application "System Events" to key code %d%s`, code, using)
	}
	// Single printable character
	escaped := strings.ReplaceAll(key, `"`, `\"`)
	escaped = strings.ReplaceAll(escaped, `\`, `\\`)
	return fmt.Sprintf(`tell application "System Events" to keystroke "%s"%s`, escaped, using)
}
