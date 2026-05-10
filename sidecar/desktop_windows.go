//go:build windows

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

var (
	user32            = syscall.NewLazyDLL("user32.dll")
	procSetCursorPos  = user32.NewProc("SetCursorPos")
	procMouseEvent    = user32.NewProc("mouse_event")
	procGetForeground = user32.NewProc("GetForegroundWindow")
	procGetWindowPid  = user32.NewProc("GetWindowThreadProcessId")
	procSetForeground = user32.NewProc("SetForegroundWindow")
	procShowWindow    = user32.NewProc("ShowWindow")
)

const (
	MOUSEEVENTF_LEFTDOWN  = 0x0002
	MOUSEEVENTF_LEFTUP    = 0x0004
	MOUSEEVENTF_RIGHTDOWN = 0x0008
	MOUSEEVENTF_RIGHTUP   = 0x0010
)

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

	// Stub helpers for tools not yet fully implemented on Windows
	stub := func(name string) ToolHandler {
		return func(params map[string]any) (any, error) {
			return nil, fmt.Errorf("%s is not yet implemented on Windows", name)
		}
	}
	m["drag_to"]          = stub("drag_to")
	m["scroll"]           = stub("scroll")
	m["wait_for_element"] = stub("wait_for_element")

	// New coordinate-input tools — click_at has a real Windows implementation;
	// the rest are stubs.
	m["click_at"]          = handleClickAt
	m["double_click_at"]   = handleDoubleClickAt
	m["right_click_at"]    = handleRightClickAt
	m["get_screen_info"]   = stub("get_screen_info")
	m["get_cursor_pos"]    = stub("get_cursor_pos")
	m["capture_annotated"] = stub("capture_annotated")
	m["type_clipboard"]    = handleTypeClipboard
}

func platformMoveMouse(x, y int) (any, error) {
	procSetCursorPos.Call(uintptr(x), uintptr(y))
	return map[string]any{"success": true, "x": x, "y": y}, nil
}

func platformCaptureScreen(outputPath string) error {
	ps := fmt.Sprintf(
		`Add-Type -AssemblyName System.Windows.Forms;`+
			`$s=[System.Windows.Forms.Screen]::PrimaryScreen;`+
			`$b=New-Object System.Drawing.Bitmap($s.Bounds.Width,$s.Bounds.Height);`+
			`$g=[System.Drawing.Graphics]::FromImage($b);`+
			`$g.CopyFromScreen($s.Bounds.Location,[System.Drawing.Point]::Empty,$s.Bounds.Size);`+
			`$b.Save('%s')`, outputPath)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	return exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-Command", ps).Run()
}

// getScreenDimensions — Windows stub. Returns error so the caller omits the
// metadata fields rather than emitting wrong values.
func getScreenDimensions(pngData []byte) (screenDimInfo, error) {
	return screenDimInfo{}, fmt.Errorf("screen dimension metadata not available on Windows")
}

func handleGetClipboard(params map[string]any) (any, error) {
	out, err := runPS(`Get-Clipboard`, 5*time.Second)
	if err != nil {
		return nil, err
	}
	return map[string]any{"content": strings.TrimRight(out, "\r\n")}, nil
}

func handleSetClipboard(params map[string]any) (any, error) {
	content, _ := params["content"].(string)
	escaped := strings.ReplaceAll(content, "'", "''")
	_, err := runPS(fmt.Sprintf("Set-Clipboard -Value '%s'", escaped), 5*time.Second)
	return map[string]any{"success": err == nil}, err
}

// ── click_at (Windows) ────────────────────────────────────────────────────────
// Clicks at raw pixel coordinates. Normalized / model-space inputs are not yet
// supported on Windows (no Cocoa equivalent); raw x/y only for now.

func handleClickAt(params map[string]any) (any, error) {
	x := toInt(params["x"])
	y := toInt(params["y"])
	action := stringOrDefault(params["action"], "click")

	procSetCursorPos.Call(uintptr(x), uintptr(y))
	time.Sleep(30 * time.Millisecond)

	switch action {
	case "right_click":
		procMouseEvent.Call(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
		procMouseEvent.Call(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
	case "double_click":
		procMouseEvent.Call(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
		procMouseEvent.Call(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
		time.Sleep(50 * time.Millisecond)
		procMouseEvent.Call(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
		procMouseEvent.Call(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
	default:
		action = "click"
		procMouseEvent.Call(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
		procMouseEvent.Call(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
	}
	return map[string]any{"success": true, "action": action, "x": x, "y": y}, nil
}

func handleDoubleClickAt(params map[string]any) (any, error) {
	params["action"] = "double_click"
	return handleClickAt(params)
}

func handleRightClickAt(params map[string]any) (any, error) {
	params["action"] = "right_click"
	return handleClickAt(params)
}

// ── type_clipboard (Windows) ──────────────────────────────────────────────────
// Sets clipboard content and sends Ctrl+V.

func handleTypeClipboard(params map[string]any) (any, error) {
	text, _ := params["text"].(string)
	if text == "" {
		return nil, fmt.Errorf("missing required parameter: text")
	}
	escaped := strings.ReplaceAll(text, "'", "''")
	if _, err := runPS(fmt.Sprintf("Set-Clipboard -Value '%s'", escaped), 5*time.Second); err != nil {
		return nil, fmt.Errorf("set clipboard: %w", err)
	}
	// Send Ctrl+V
	script := `Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.SendKeys]::SendWait('^v')`
	if _, err := runPS(script, 5*time.Second); err != nil {
		return nil, fmt.Errorf("paste: %w", err)
	}
	return map[string]any{"success": true, "method": "clipboard"}, nil
}

// ── list_windows ──────────────────────────────────────────────────────────────

func handleListWindows(params map[string]any) (any, error) {
	script := `
Add-Type @'
using System;using System.Collections.Generic;using System.Runtime.InteropServices;
using System.Text;using System.Diagnostics;
public class WE {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc f,IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,StringBuilder s,int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  public delegate bool EnumWindowsProc(IntPtr h,IntPtr l);
  [StructLayout(LayoutKind.Sequential)] public struct RECT{public int L,T,R,B;}
  public static List<Dictionary<string,object>> List(){
    var res=new List<Dictionary<string,object>>();
    var fg=GetForegroundWindow();
    EnumWindows((h,_)=>{
      if(!IsWindowVisible(h))return true;
      var sb=new StringBuilder(256);GetWindowText(h,sb,256);
      var t=sb.ToString();if(string.IsNullOrWhiteSpace(t))return true;
      uint pid;GetWindowThreadProcessId(h,out pid);
      RECT r;GetWindowRect(h,out r);
      string pn="";try{pn=System.Diagnostics.Process.GetProcessById((int)pid).ProcessName;}catch{}
      var d=new Dictionary<string,object>();
      d["title"]=t;d["pid"]=pid;d["process_name"]=pn;
      d["left"]=r.L;d["top"]=r.T;d["right"]=r.R;d["bottom"]=r.B;
      d["is_foreground"]=h==fg;
      res.Add(d);return true;
    },IntPtr.Zero);return res;
  }
}
'@
[WE]::List()|ConvertTo-Json -Depth 3 -Compress`

	out, err := runPS(script, 10*time.Second)
	if err != nil {
		return nil, fmt.Errorf("list_windows: %w", err)
	}
	var windows []map[string]any
	if err := json.Unmarshal([]byte(out), &windows); err != nil {
		var single map[string]any
		if err2 := json.Unmarshal([]byte(out), &single); err2 == nil {
			windows = []map[string]any{single}
		}
	}
	return map[string]any{"windows": windows}, nil
}

// ── get_window_tree — UIAutomation via PowerShell ─────────────────────────────

func handleGetWindowTree(params map[string]any) (any, error) {
	pid := toInt(params["pid"])
	depth := 5
	if v, ok := params["depth"].(float64); ok {
		depth = int(v)
	}

	pidClause := ""
	if pid > 0 {
		pidClause = fmt.Sprintf("$pid=%d", pid)
	} else {
		pidClause = `
Add-Type @'
using System;using System.Runtime.InteropServices;
public class FG{[DllImport("user32.dll")]public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")]public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);}
'@
$hw=[FG]::GetForegroundWindow();$p=0;[FG]::GetWindowThreadProcessId($hw,[ref]$p)|Out-Null;$pid=$p`
	}

	script := fmt.Sprintf(`
%s
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$ae=[System.Windows.Automation.AutomationElement]
$root=$ae::RootElement
$cond=New-Object System.Windows.Automation.PropertyCondition($ae::ProcessIdProperty,$pid)
$win=$root.FindFirst([System.Windows.Automation.TreeScope]::Children,$cond)
if($win -eq $null){Write-Output '{"error":"window not found"}';exit}
$elements=@()
$id=0
function Walk($el,$d){
  if($d -gt %d -or $elements.Count -gt 300){return}
  $r=$el.Current.BoundingRectangle
  if($r.Width -gt 0 -and $r.Height -gt 0){
    $elements+=[PSCustomObject]@{
      id=$script:id++;name=$el.Current.Name;
      control_type=$el.Current.ControlType.ProgrammaticName.Replace("ControlType.","");
      automation_id=$el.Current.AutomationId;enabled=$el.Current.IsEnabled;
      rect=@{x=[int]$r.X;y=[int]$r.Y;w=[int]$r.Width;h=[int]$r.Height}
    }
  }
  $children=$el.FindAll([System.Windows.Automation.TreeScope]::Children,
    [System.Windows.Automation.Condition]::TrueCondition)
  foreach($c in $children){Walk $c ($d+1)}
}
Walk $win 0
@{window_title=$win.Current.Name;pid=$pid;element_count=$elements.Count;elements=$elements}|ConvertTo-Json -Depth 5 -Compress`,
		pidClause, depth)

	out, err := runPS(script, 20*time.Second)
	if err != nil {
		return nil, fmt.Errorf("get_window_tree: %w", err)
	}

	var tree map[string]any
	if err := json.Unmarshal([]byte(out), &tree); err != nil {
		return nil, fmt.Errorf("parse tree: %w", err)
	}

	// Cache elements
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

	procSetCursorPos.Call(uintptr(x), uintptr(y))
	time.Sleep(50 * time.Millisecond)

	switch action {
	case "right_click":
		procMouseEvent.Call(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
		procMouseEvent.Call(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
	case "double_click":
		procMouseEvent.Call(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
		procMouseEvent.Call(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
		time.Sleep(50 * time.Millisecond)
		procMouseEvent.Call(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
		procMouseEvent.Call(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
	default:
		procMouseEvent.Call(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
		procMouseEvent.Call(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
	}

	return map[string]any{"success": true, "action": action, "x": x, "y": y}, nil
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

	// Use clipboard method if requested or if text contains non-ASCII
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
		return handleTypeClipboard(params)
	}

	escaped := escapeSendKeys(text)
	script := fmt.Sprintf(`Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.SendKeys]::SendWait('%s')`, escaped)
	_, err := runPS(script, 5*time.Second)
	return map[string]any{"success": err == nil}, err
}

// ── press_keys ────────────────────────────────────────────────────────────────

func handlePressKeys(params map[string]any) (any, error) {
	keys, _ := params["keys"].(string)
	if keys == "" {
		return nil, fmt.Errorf("missing required parameter: keys")
	}
	sk := convertToSendKeys(keys)
	script := fmt.Sprintf(`Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.SendKeys]::SendWait('%s')`, sk)
	_, err := runPS(script, 5*time.Second)
	return map[string]any{"success": err == nil, "keys": keys}, err
}

// ── launch_app ────────────────────────────────────────────────────────────────

func handleLaunchApp(params map[string]any) (any, error) {
	exe, _ := params["executable"].(string)
	if exe == "" {
		return nil, fmt.Errorf("missing required parameter: executable")
	}
	escaped := strings.ReplaceAll(exe, "'", "''")
	out, err := runPS(fmt.Sprintf(`$p=Start-Process -FilePath '%s' -PassThru;@{success=$true;pid=$p.Id}|ConvertTo-Json -Compress`, escaped), 10*time.Second)
	if err != nil {
		return nil, err
	}
	var result map[string]any
	json.Unmarshal([]byte(out), &result)
	return result, nil
}

// ── focus_window ──────────────────────────────────────────────────────────────

func handleFocusWindow(params map[string]any) (any, error) {
	pid := toInt(params["pid"])
	if pid == 0 {
		return nil, fmt.Errorf("missing required parameter: pid")
	}
	script := fmt.Sprintf(`
Add-Type @'
using System;using System.Runtime.InteropServices;using System.Diagnostics;
public class F{[DllImport("user32.dll")]public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")]public static extern bool ShowWindow(IntPtr h,int n);
public static bool Focus(int pid){var p=Process.GetProcessById(pid);if(p==null||p.MainWindowHandle==IntPtr.Zero)return false;ShowWindow(p.MainWindowHandle,9);return SetForegroundWindow(p.MainWindowHandle);}}
'@
[F]::Focus(%d)`, pid)
	out, _ := runPS(script, 5*time.Second)
	return map[string]any{"success": strings.TrimSpace(out) == "True", "pid": pid}, nil
}

// ── find_element ──────────────────────────────────────────────────────────────

func handleFindElement(params map[string]any) (any, error) {
	if _, err := handleGetWindowTree(params); err != nil {
		return nil, err
	}
	name, _ := params["name"].(string)
	controlType, _ := params["control_type"].(string)

	elementCache.mu.Lock()
	defer elementCache.mu.Unlock()

	var matches []map[string]any
	for _, el := range elementCache.elements {
		if name != "" && el["name"] != name {
			continue
		}
		if controlType != "" && el["control_type"] != controlType {
			continue
		}
		matches = append(matches, el)
	}
	return map[string]any{"match_count": len(matches), "elements": matches}, nil
}

// ── Helpers ───────────────────────────────────────────────────────────────────

func runPS(script string, timeout time.Duration) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	out, err := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-Command", script).Output()
	return strings.TrimSpace(string(out)), err
}

func convertToSendKeys(keys string) string {
	// Normalise + as separator same as Darwin side
	norm := keys
	if !strings.Contains(keys, ",") && strings.Contains(keys, "+") {
		norm = strings.ReplaceAll(keys, "+", ",")
	}

	parts := strings.Split(strings.ToLower(strings.TrimSpace(norm)), ",")
	mods := ""
	var keyParts []string
	for _, p := range parts {
		p = strings.TrimSpace(p)
		switch p {
		case "ctrl", "control":
			mods += "^"
		case "alt", "option", "opt":
			mods += "%"
		case "shift":
			mods += "+"
		case "cmd", "command", "win", "windows":
			// Windows key not directly supported by SendKeys; ignore gracefully
		default:
			keyParts = append(keyParts, p)
		}
	}
	if len(keyParts) == 0 {
		return mods
	}
	key := keyParts[0]
	specialKeys := map[string]string{
		"enter": "{ENTER}", "return": "{ENTER}",
		"tab": "{TAB}",
		"escape": "{ESC}", "esc": "{ESC}",
		"backspace": "{BACKSPACE}", "delete": "{DELETE}",
		"forward_delete": "{DELETE}", "del": "{DELETE}",
		"up": "{UP}", "down": "{DOWN}",
		"left": "{LEFT}", "right": "{RIGHT}",
		"home": "{HOME}", "end": "{END}",
		"page_up": "{PGUP}", "pageup": "{PGUP}",
		"page_down": "{PGDN}", "pagedown": "{PGDN}",
		"f1": "{F1}", "f2": "{F2}", "f3": "{F3}", "f4": "{F4}",
		"f5": "{F5}", "f6": "{F6}", "f7": "{F7}", "f8": "{F8}",
		"f9": "{F9}", "f10": "{F10}", "f11": "{F11}", "f12": "{F12}",
		"f13": "{F13}", "f14": "{F14}", "f15": "{F15}",
		"space": " ",
		"caps_lock": "{CAPSLOCK}",
		"num_lock": "{NUMLOCK}",
		"scroll_lock": "{SCROLLLOCK}",
		"print_screen": "{PRTSC}",
		"pause": "{BREAK}",
		"insert": "{INSERT}",
	}
	if mapped, ok := specialKeys[key]; ok {
		return mods + mapped
	}
	return mods + key
}

func escapeSendKeys(text string) string {
	r := strings.NewReplacer(
		"+", "{+}", "^", "{^}", "%", "{%}", "~", "{~}",
		"(", "{(}", ")", "{)}", "{", "{{}", "}", "{}}",
	)
	return r.Replace(text)
}

// elementCache — declared in element_cache.go (shared)
var _ = unsafe.Pointer(nil)
