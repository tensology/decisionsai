//go:build darwin

package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// Stdlib-only — must not create NSApplication (that causes infinite Dock bounce).
const pythonDockHidePreamble = `
import ctypes
class _PSN(ctypes.Structure):
    _fields_ = [("highLongOfPSN", ctypes.c_uint32), ("lowLongOfPSN", ctypes.c_uint32)]
try:
    _lib = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    _lib.TransformProcessType(ctypes.byref(_PSN(0, 2)), ctypes.c_uint32(4))
except Exception:
    pass
`

func sidecarPython() string {
	if p := strings.TrimSpace(os.Getenv("DECISIONS_PYTHON")); p != "" {
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	home, _ := os.UserHomeDir()
	venv := filepath.Join(home, ".virtualenvs", "decisions", "bin", "python")
	if _, err := os.Stat(venv); err == nil {
		return venv
	}
	return "python3"
}

func runSidecarPython(ctx context.Context, code string) error {
	_, err := runSidecarPythonOutput(ctx, code)
	return err
}

func runSidecarPythonOutput(ctx context.Context, code string) ([]byte, error) {
	combined := pythonDockHidePreamble + "\n" + code
	cmd := exec.CommandContext(ctx, sidecarPython(), "-c", combined)
	return cmd.CombinedOutput()
}

func runSidecarPythonScript(ctx context.Context, scriptPath string) ([]byte, error) {
	wrapper := pythonDockHidePreamble + fmt.Sprintf(`
import runpy
runpy.run_path(%q, run_name="__main__")
`, scriptPath)
	cmd := exec.CommandContext(ctx, sidecarPython(), "-c", wrapper)
	return cmd.CombinedOutput()
}
