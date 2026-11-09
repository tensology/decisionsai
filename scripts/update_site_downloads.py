#!/usr/bin/env python3
"""Idempotently apply the visual download homepage to the separate website.

Run after publishing assets: python3 scripts/update_site_downloads.py SITE_ROOT
Then: cd SITE_ROOT/frontend && npm run build. No service restart is needed.
Templates are versioned here so packaging/rebuilds cannot restore the old installer.
"""
import argparse
import json
import re
from pathlib import Path


def update_site(site):
    frontend = site / "frontend"
    manifest = json.loads((frontend / "public/downloads/latest-macos.json").read_text())
    template = (Path(__file__).parent / "site_downloads/Hero.tsx").read_text()
    hero = template.replace("__URL__", manifest["url"]).replace("__VERSION__", manifest["version"])
    (frontend / "src/components/Hero.tsx").write_text(hero)
    shared = Path(__file__).parent / "site_downloads/HeroFeatures.tsx"
    (frontend / "src/components/HeroFeatures.tsx").write_text(shared.read_text())
    faq = frontend / "src/pages/FaqPage.tsx"
    text = faq.read_text()
    for item, answer in {
        "setup-installation": "Download the Apple Silicon DMG from the homepage. Linux and Windows desktop downloads are not available yet.",
        "platform-support": "The published desktop download is for Apple Silicon Macs. Linux and Windows source launchers exist, but packaged desktop builds are not published.",
    }.items():
        pattern = r"(id: '" + item + r"',[\s\S]*?answer:\s*)'[^'\n]*'"
        text, count = re.subn(pattern, lambda m: m[1] + repr(answer), text, count=1)
        if count != 1:
            raise ValueError(f"Expected one FAQ entry: {item}")
    faq.write_text(text)
    footer = frontend / "src/components/Footer.tsx"
    text = footer.read_text().replace("import { Link }", "import { Link, useLocation }")
    if "const { pathname } = useLocation();" not in text:
        text = text.replace("const Footer = () => {", "const Footer = () => {\n  const { pathname } = useLocation();")
    if 'data-download-footer' not in text:
        marker = '  return (\n    <footer'
        compact = '''  if (pathname === '/') return (
    <footer data-download-footer className="px-5 py-8 text-center text-xs text-white/45">
      <Link to="/about" className="hover:text-white">About</Link> · <Link to="/privacy" className="hover:text-white">Privacy</Link> · <a href="https://github.com/tensology/decisionsai" className="hover:text-white">GitHub</a> · <a href="https://www.tensology.com" className="hover:text-white">Tensology</a>
    </footer>
  );

'''
        if marker not in text:
            raise ValueError("Footer marker not found")
        text = text.replace(marker, compact + marker, 1)
    footer.write_text(text)
    print("Updated visual download homepage, compact footer and platform FAQ.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("site", type=Path)
    update_site(parser.parse_args().site)
