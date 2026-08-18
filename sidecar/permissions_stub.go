//go:build !darwin

package main

func probeMacOSPermissions() map[string]any {
	return nil
}

func markSidecarScreenRecordingOK()     {}
func markSidecarScreenRecordingFailed() {}
