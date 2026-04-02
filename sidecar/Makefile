# DecisionsAI Sidecar — build targets

.PHONY: all mac mac-arm mac-intel windows clean deps

all: mac windows

deps:
	go mod tidy

mac-arm: deps
	GOOS=darwin GOARCH=arm64 go build -ldflags="-s -w" -o dist/decisionsai-sidecar-mac-arm64 .

mac-intel: deps
	GOOS=darwin GOARCH=amd64 go build -ldflags="-s -w" -o dist/decisionsai-sidecar-mac-amd64 .

mac: mac-arm mac-intel
	# Create universal binary (requires macOS with lipo)
	lipo -create -output dist/decisionsai-sidecar-mac \
		dist/decisionsai-sidecar-mac-arm64 \
		dist/decisionsai-sidecar-mac-amd64 || \
	cp dist/decisionsai-sidecar-mac-arm64 dist/decisionsai-sidecar-mac

windows: deps
	GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o dist/decisionsai-sidecar.exe .

clean:
	rm -rf dist/

# Quick local build for current platform
local: deps
	mkdir -p dist
	go build -o dist/decisionsai-sidecar .
