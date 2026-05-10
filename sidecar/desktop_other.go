//go:build !darwin && !windows

// Stub for Linux and other platforms — desktop tools not yet implemented.
package main

import "fmt"

func addDesktopHandlers(m map[string]ToolHandler) {
	stub := func(name string) ToolHandler {
		return func(params map[string]any) (any, error) {
			return nil, fmt.Errorf("%s is not supported on this platform", name)
		}
	}
	for _, name := range []string{
		"list_windows", "get_window_tree", "click_element",
		"type_text", "press_keys", "launch_app", "focus_window", "find_element",
		"move_mouse", "drag_to", "scroll", "wait_for_element",
		// New tools
		"click_at", "double_click_at", "right_click_at",
		"get_screen_info", "get_cursor_pos", "capture_annotated", "type_clipboard",
	} {
		m[name] = stub(name)
	}
	m["get_clipboard"] = handleGetClipboardLinux
	m["set_clipboard"] = handleSetClipboardLinux
}

func platformCaptureScreen(outputPath string) error {
	// Try scrot, then import (ImageMagick)
	import_cmd := fmt.Sprintf("import -window root %s", outputPath)
	_ = import_cmd
	return fmt.Errorf("screenshot not implemented on this platform — install scrot or ImageMagick")
}

// getScreenDimensions — Linux stub.
func getScreenDimensions(pngData []byte) (screenDimInfo, error) {
	return screenDimInfo{}, fmt.Errorf("screen dimension metadata not available on this platform")
}

func platformMoveMouse(x, y int) (any, error) {
	return nil, fmt.Errorf("move_mouse is not supported on this platform")
}

func handleGetClipboardLinux(params map[string]any) (any, error) {
	return nil, fmt.Errorf("clipboard not implemented on Linux — install xclip")
}

func handleSetClipboardLinux(params map[string]any) (any, error) {
	return nil, fmt.Errorf("clipboard not implemented on Linux — install xclip")
}
