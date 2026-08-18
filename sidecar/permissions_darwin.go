//go:build darwin

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func sidecarRunDir() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".decisions", "run")
}

func sidecarScreenMarkerPath() string {
	return filepath.Join(sidecarRunDir(), "sidecar_screen_ok")
}

func sidecarExecutableHash() (string, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", err
	}
	f, err := os.Open(exe)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)[:16]), nil
}

// markSidecarScreenRecordingOK records a successful screenshot for this binary build.
func markSidecarScreenRecordingOK() {
	hash, err := sidecarExecutableHash()
	if err != nil {
		return
	}
	_ = os.MkdirAll(sidecarRunDir(), 0o755)
	_ = os.WriteFile(sidecarScreenMarkerPath(), []byte(hash+"\n"), 0o644)
}

func markSidecarScreenRecordingFailed() {
	_ = os.Remove(sidecarScreenMarkerPath())
}

func probeMacOSPermissions() map[string]any {
	exe, _ := os.Executable()
	return map[string]any{
		"executable":       exe,
		"screen_recording": probeScreenRecordingPermission(),
		"automation":       probeAutomationPermission(),
		"accessibility":    probeAccessibilityPermission(),
	}
}

func probeScreenRecordingPermission() map[string]any {
	// Never call screencapture here — it triggers the macOS permission dialog on every
	// launch when the binary hash changed or TCC is out of sync with System Settings.
	hash, err := sidecarExecutableHash()
	if err != nil {
		return map[string]any{
			"ok":       false,
			"verified": false,
			"detail":   "could not read sidecar binary",
		}
	}

	markerData, readErr := os.ReadFile(sidecarScreenMarkerPath())
	if readErr == nil {
		markerHash := strings.TrimSpace(string(markerData))
		if markerHash == hash {
			return map[string]any{
				"ok":       true,
				"verified": true,
				"detail":   "ok",
			}
		}
	}

	return map[string]any{
		"ok":       false,
		"verified": false,
		"detail":   "enable Screen Recording for decisionsai-sidecar in System Settings (verified on first screenshot)",
	}
}

func probeAutomationPermission() map[string]any {
	script := `tell application "System Events" to return name of first process whose frontmost is true`
	out, err := runOsascript(script, 3*time.Second)
	ok := err == nil
	detail := strings.TrimSpace(out)
	if err != nil {
		detail = err.Error()
		if strings.Contains(strings.ToLower(detail), "not allowed") ||
			strings.Contains(strings.ToLower(detail), "assistive") ||
			strings.Contains(strings.ToLower(detail), "denied") {
			detail = "Automation denied — allow decisionsai-sidecar to control System Events"
		}
	} else if detail == "" {
		detail = "ok"
	} else {
		detail = "ok (" + detail + ")"
	}
	return map[string]any{"ok": ok, "detail": detail}
}

func probeAccessibilityPermission() map[string]any {
	// Prefer cliclick — never spawn Python from /health (shows in Dock and bounces).
	if _, err := exec.LookPath("cliclick"); err == nil {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		out, err := exec.CommandContext(ctx, "cliclick", "p").Output()
		text := strings.TrimSpace(string(out))
		ok := err == nil && strings.Contains(text, ",")
		detail := text
		if err != nil {
			detail = err.Error()
		}
		if detail == "" {
			detail = "ok (cliclick)"
		}
		return map[string]any{"ok": ok, "detail": detail, "via": "cliclick"}
	}
	return map[string]any{
		"ok":     false,
		"detail": "install cliclick for accessibility checks (brew install cliclick)",
		"via":    "none",
	}
}
