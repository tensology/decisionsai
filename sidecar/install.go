// install.go — auto-install the sidecar as a system service
// Run: decisionsai-sidecar --install --server wss://... --token ... --user ...
// This registers a launchd agent (macOS) or Windows service / Task Scheduler entry
// so the sidecar starts automatically on login and restarts if it crashes.
package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"text/template"
)

// installService installs the sidecar as a persistent background service.
func installService(serverURL, token, appUserID string) error {
	switch runtime.GOOS {
	case "darwin":
		return installLaunchd(serverURL, token, appUserID)
	case "windows":
		return installTaskScheduler(serverURL, token, appUserID)
	default:
		return installSystemd(serverURL, token, appUserID)
	}
}

func uninstallService() error {
	switch runtime.GOOS {
	case "darwin":
		return uninstallLaunchd()
	case "windows":
		return uninstallTaskScheduler()
	default:
		return uninstallSystemd()
	}
}

// ── macOS: launchd ────────────────────────────────────────────────────────────

const launchdLabel = "net.decisionsai.sidecar"

const launchdPlist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{{.Label}}</string>

  <key>ProgramArguments</key>
  <array>
    <string>{{.BinaryPath}}</string>
    <string>--server</string>
    <string>{{.ServerURL}}</string>
    <string>--token</string>
    <string>{{.Token}}</string>
    <string>--user</string>
    <string>{{.AppUserID}}</string>
  </array>

  <!-- Restart automatically if it crashes -->
  <key>KeepAlive</key>
  <true/>

  <!-- Start on login -->
  <key>RunAtLoad</key>
  <true/>

  <!-- Throttle restarts — wait 5s before restarting after a crash -->
  <key>ThrottleInterval</key>
  <integer>5</integer>

  <key>StandardOutPath</key>
  <string>{{.LogDir}}/sidecar.log</string>
  <key>StandardErrorPath</key>
  <string>{{.LogDir}}/sidecar.log</string>
</dict>
</plist>
`

func installLaunchd(serverURL, token, appUserID string) error {
	binaryPath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("could not determine binary path: %w", err)
	}
	binaryPath, _ = filepath.Abs(binaryPath)

	home, _ := os.UserHomeDir()
	logDir := filepath.Join(home, "Library", "Logs", "DecisionsAI")
	plistDir := filepath.Join(home, "Library", "LaunchAgents")
	plistPath := filepath.Join(plistDir, launchdLabel+".plist")

	if err := os.MkdirAll(logDir, 0755); err != nil {
		return err
	}
	if err := os.MkdirAll(plistDir, 0755); err != nil {
		return err
	}

	// Render plist template
	tmpl, _ := template.New("plist").Parse(launchdPlist)
	f, err := os.Create(plistPath)
	if err != nil {
		return fmt.Errorf("create plist: %w", err)
	}
	defer f.Close()

	if err := tmpl.Execute(f, map[string]string{
		"Label":      launchdLabel,
		"BinaryPath": binaryPath,
		"ServerURL":  serverURL,
		"Token":      token,
		"AppUserID":  appUserID,
		"LogDir":     logDir,
	}); err != nil {
		return err
	}

	// Unload existing agent if running, then load the new one
	exec.Command("launchctl", "unload", plistPath).Run()
	if err := exec.Command("launchctl", "load", plistPath).Run(); err != nil {
		return fmt.Errorf("launchctl load failed: %w", err)
	}

	fmt.Printf("✅ Sidecar installed as launchd agent: %s\n", launchdLabel)
	fmt.Printf("   Logs: %s/sidecar.log\n", logDir)
	fmt.Printf("   To uninstall: decisionsai-sidecar --uninstall\n")
	return nil
}

func uninstallLaunchd() error {
	home, _ := os.UserHomeDir()
	plistPath := filepath.Join(home, "Library", "LaunchAgents", launchdLabel+".plist")
	exec.Command("launchctl", "unload", plistPath).Run()
	os.Remove(plistPath)
	fmt.Println("✅ Sidecar launchd agent removed")
	return nil
}

// ── Windows: Task Scheduler ───────────────────────────────────────────────────
// Uses schtasks.exe — no admin required for user-level tasks.

func installTaskScheduler(serverURL, token, appUserID string) error {
	binaryPath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("could not determine binary path: %w", err)
	}
	binaryPath, _ = filepath.Abs(binaryPath)

	args := fmt.Sprintf(`--server "%s" --token "%s" --user "%s"`, serverURL, token, appUserID)

	// Create a task that runs at logon and restarts on failure
	cmd := exec.Command("schtasks.exe",
		"/Create", "/F",
		"/TN", `DecisionsAI\Sidecar`,
		"/TR", fmt.Sprintf(`"%s" %s`, binaryPath, args),
		"/SC", "ONLOGON",
		"/RL", "HIGHEST",
		"/DELAY", "0000:05", // 5 second delay after logon
	)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("schtasks create failed: %w\n%s", err, out)
	}

	// Start it now
	exec.Command("schtasks.exe", "/Run", "/TN", `DecisionsAI\Sidecar`).Run()

	fmt.Println("✅ Sidecar installed as Windows scheduled task: DecisionsAI\\Sidecar")
	fmt.Println("   Starts automatically on login")
	fmt.Println("   To uninstall: decisionsai-sidecar.exe --uninstall")
	return nil
}

func uninstallTaskScheduler() error {
	exec.Command("schtasks.exe", "/End", "/TN", `DecisionsAI\Sidecar`).Run()
	if out, err := exec.Command("schtasks.exe", "/Delete", "/F", "/TN", `DecisionsAI\Sidecar`).CombinedOutput(); err != nil {
		return fmt.Errorf("schtasks delete failed: %w\n%s", err, out)
	}
	fmt.Println("✅ Sidecar scheduled task removed")
	return nil
}

// ── Linux: systemd user service ───────────────────────────────────────────────

const systemdUnit = `[Unit]
Description=DecisionsAI Sidecar
After=network.target

[Service]
ExecStart={{.BinaryPath}} --server {{.ServerURL}} --token {{.Token}} --user {{.AppUserID}}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
`

func installSystemd(serverURL, token, appUserID string) error {
	binaryPath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("could not determine binary path: %w", err)
	}
	binaryPath, _ = filepath.Abs(binaryPath)

	home, _ := os.UserHomeDir()
	unitDir := filepath.Join(home, ".config", "systemd", "user")
	unitPath := filepath.Join(unitDir, "decisionsai-sidecar.service")

	if err := os.MkdirAll(unitDir, 0755); err != nil {
		return err
	}

	tmpl, _ := template.New("unit").Parse(systemdUnit)
	f, err := os.Create(unitPath)
	if err != nil {
		return err
	}
	defer f.Close()

	tmpl.Execute(f, map[string]string{
		"BinaryPath": binaryPath,
		"ServerURL":  serverURL,
		"Token":      token,
		"AppUserID":  appUserID,
	})

	exec.Command("systemctl", "--user", "daemon-reload").Run()
	exec.Command("systemctl", "--user", "enable", "--now", "decisionsai-sidecar").Run()

	fmt.Println("✅ Sidecar installed as systemd user service")
	fmt.Println("   To check status: systemctl --user status decisionsai-sidecar")
	fmt.Println("   To uninstall: decisionsai-sidecar --uninstall")
	return nil
}

func uninstallSystemd() error {
	exec.Command("systemctl", "--user", "disable", "--now", "decisionsai-sidecar").Run()
	home, _ := os.UserHomeDir()
	os.Remove(filepath.Join(home, ".config", "systemd", "user", "decisionsai-sidecar.service"))
	exec.Command("systemctl", "--user", "daemon-reload").Run()
	fmt.Println("✅ Sidecar systemd service removed")
	return nil
}

// ── Status check ──────────────────────────────────────────────────────────────

func serviceStatus() string {
	switch runtime.GOOS {
	case "darwin":
		out, err := exec.Command("launchctl", "list", launchdLabel).Output()
		if err != nil {
			return "not installed"
		}
		if strings.Contains(string(out), `"PID"`) {
			return "running"
		}
		return "installed (not running)"
	case "windows":
		out, _ := exec.Command("schtasks.exe", "/Query", "/TN", `DecisionsAI\Sidecar`, "/FO", "LIST").Output()
		if strings.Contains(string(out), "Running") {
			return "running"
		}
		if len(out) > 0 {
			return "installed (not running)"
		}
		return "not installed"
	default:
		out, _ := exec.Command("systemctl", "--user", "is-active", "decisionsai-sidecar").Output()
		return strings.TrimSpace(string(out))
	}
}
