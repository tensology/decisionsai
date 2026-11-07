#!/usr/bin/env python3
"""Run in the existing decisions.tensology.com site checkout after publishing assets.

Only changes the install widget and its FAQ. Does not touch nginx or restart any app.
The website repository is separate; this script is versioned in decisionsai.
"""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("site", type=Path)
args = parser.parse_args()
frontend = args.site / "frontend"
manifest = json.loads((frontend / "public/downloads/latest-macos.json").read_text())
hero = frontend / "src/components/Hero.tsx"
s = hero.read_text()
a = s.index("const InstallCommand = () => {")
b = s.index("\nconst messages:", a)
component = r'''const InstallCommand = () => (
  <section id="install" className="mt-14 mb-10 rounded-xl border border-white/20 bg-[#0a1128] p-6 max-w-2xl">
    <h2 className="text-xl font-semibold text-white">Install DecisionsAI for Mac</h2>
    <a className="inline-block mt-4 rounded-lg bg-orange-600 px-6 py-3 font-semibold text-white" href="__URL__">Download for Apple Silicon — v__VERSION__</a>
    <ol className="mt-4 space-y-2 text-white/80 list-decimal pl-5">
      <li>Open the downloaded DMG.</li>
      <li>Drag DecisionsAI into Applications, then open it there.</li>
      <li>Choose Whisper or Vosk and confirm the optional model download, or continue without voice.</li>
      <li>Grant macOS permissions when using voice or desktop control; choose a cloud provider or optional Ollama in Settings.</li>
    </ol>
    <p className="mt-3 text-sm text-white/60">Python and dependencies are included. No Git checkout, Homebrew or Terminal setup is needed. Speech models are downloaded only after your choice. Updates are announced in the app; download the new DMG and replace the app after quitting. Your data is kept separately in Application Support.</p>
    <p className="mt-3 text-sm text-amber-300">Preview build: ad-hoc signed, not Apple-notarized. macOS may block first opening. Use System Settings → Privacy &amp; Security → Open Anyway only if you trust this download. Intel Macs are not supported by this build.</p>
    <a className="block mt-2 text-sm underline text-white/70" href="/downloads/SHA256SUMS">Download checksum</a>
    <details className="mt-5 text-sm text-white/60">
      <summary className="cursor-pointer">Developer source install / Linux / Windows</summary>
      <p className="mt-2">Developer fallback only: this clones the source and installs Python dependencies, and can take 20–60 minutes.</p>
      <code className="block mt-2 break-all">curl -fsSL https://decisions.tensology.com/install.sh | bash</code>
      <p className="mt-2">Then run <code>decisions</code>. Windows: use the <a className="underline" href="/install.ps1">PowerShell developer installer</a>.</p>
    </details>
  </section>
);
'''
component = component.replace("__URL__", manifest["url"]).replace("__VERSION__", manifest["version"])
hero.write_text(s[:a] + component + s[b:])
faq = frontend / "src/pages/FaqPage.tsx"
s = faq.read_text()
old = "One command. On macOS/Linux:"
for line in s.splitlines():
    if old in line:
        s = s.replace(line, "      'On Apple Silicon Macs, download the DMG from the install section, drag DecisionsAI into Applications and open it. Python is bundled; no Git checkout is needed. Choose Whisper or Vosk and confirm its separate model download, or continue without voice. This preview is ad-hoc signed, not Apple notarized. Linux and Windows retain the developer source installer.',")
faq.write_text(s)
print("Updated install widget and FAQ. Next run: cd frontend && npm run build")
