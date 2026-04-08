//go:build darwin

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// elementCache is declared in element_cache.go (shared across platforms)

func addDesktopHandlers(m map[string]ToolHandler) {
	m["list_windows"]    = handleListWindows
	m["get_window_tree"] = handleGetWindowTree
	m["click_element"]   = handleClickElement
	m["move_mouse"]      = handleMoveMouse
	m["type_text"]       = handleTypeText
	m["press_keys"]      = handlePressKeys
	m["launch_app"]      = handleLaunchApp
	m["focus_window"]    = handleFocusWindow
	m["find_element"]    = handleFindElement
	m["get_clipboard"]   = handleGetClipboard
	m["set_clipboard"]   = handleSetClipboard
}

func platformCaptureScreen(outputPath string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return exec.CommandContext(ctx, "screencapture", "-x", outputPath).Run()
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
	escaped := strings.ReplaceAll(text, `\`, `\\`)
	escaped = strings.ReplaceAll(escaped, `"`, `\"`)
	script := fmt.Sprintf(`tell application "System Events" to keystroke "%s"`, escaped)
	if _, err := runOsascript(script, 10*time.Second); err != nil {
		return nil, fmt.Errorf("type_text: %w", err)
	}
	return map[string]any{"success": true}, nil
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

func keysToOsascript(keys string) string {
	parts := strings.Split(strings.ToLower(strings.TrimSpace(keys)), ",")
	var mods []string
	var keyParts []string
	for _, p := range parts {
		p = strings.TrimSpace(p)
		switch p {
		case "ctrl", "control":
			mods = append(mods, "control down")
		case "alt", "option":
			mods = append(mods, "option down")
		case "shift":
			mods = append(mods, "shift down")
		case "cmd", "command":
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
	specialKeys := map[string]int{
		"enter": 36, "return": 36, "tab": 48, "escape": 53, "esc": 53,
		"delete": 51, "backspace": 51, "space": 49,
		"up": 126, "down": 125, "left": 123, "right": 124,
		"home": 115, "end": 119, "pageup": 116, "pagedown": 121,
		"f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96,
		"f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
	}
	if code, ok := specialKeys[key]; ok {
		return fmt.Sprintf(`tell application "System Events" to key code %d%s`, code, using)
	}
	escaped := strings.ReplaceAll(key, `"`, `\"`)
	return fmt.Sprintf(`tell application "System Events" to keystroke "%s"%s`, escaped, using)
}
