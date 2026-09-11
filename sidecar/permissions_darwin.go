//go:build darwin

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
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
		"code_identity":    sidecarCodeIdentity(),
		"screen_recording": probeScreenRecordingPermission(),
		"automation":       probeAutomationPermission(),
		"accessibility":    probeAccessibilityPermission(),
	}
}

func sidecarCodeIdentity() map[string]any {
	exe, err := os.Executable()
	if err != nil {
		return map[string]any{"stable": false, "detail": err.Error()}
	}
	hash, _ := sidecarExecutableHash()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	out, signErr := exec.CommandContext(
		ctx,
		"codesign",
		"-d",
		"-r-",
		"--verbose=4",
		exe,
	).CombinedOutput()
	detail := strings.TrimSpace(string(out))
	stable := signErr == nil && !strings.Contains(detail, "Signature=adhoc")
	identifier := ""
	for _, line := range strings.Split(detail, "\n") {
		if strings.HasPrefix(line, "Identifier=") {
			identifier = strings.TrimSpace(strings.TrimPrefix(line, "Identifier="))
			break
		}
	}
	if signErr != nil && detail == "" {
		detail = signErr.Error()
	}
	return map[string]any{
		"stable":     stable,
		"identifier": identifier,
		"build_hash": hash,
		"detail":     detail,
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
	// Reading the cursor with `cliclick p` does not require enough privilege to
	// prove UI automation. Ask System Events for its AX trust state instead. The
	// request is attributed by TCC to this Sidecar process, including subprocesses.
	out, err := runOsascript(`tell application "System Events" to return UI elements enabled`, 3*time.Second)
	text := strings.ToLower(strings.TrimSpace(out))
	ok := err == nil && text == "true"
	if ok {
		return map[string]any{"ok": true, "verified": true, "detail": "ok", "via": "system_events_ax"}
	}
	detail := strings.TrimSpace(out)
	if err != nil {
		detail = fmt.Sprintf("Accessibility denied or stale for this Sidecar build: %v", err)
	} else if detail == "" || text == "false" {
		detail = "Accessibility denied or stale for this Sidecar build"
	}
	return map[string]any{"ok": false, "verified": true, "detail": detail, "via": "system_events_ax"}
}
