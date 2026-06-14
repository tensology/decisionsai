# DecisionsAI plugins

All IDE bridge plugins and vendored harness packs live here.

| Path | Purpose |
|------|---------|
| `plugins/codex-ide/` | DecisionsAI Codex plugin source. Installs to `~/plugins/decisions-codex`. |
| `plugins/cursor-ide/` | DecisionsAI Cursor plugin source. Installs to `~/.cursor/plugins/local/decisions-cursor`. |
| `plugins/ecc/` | Vendored ECC harness pack (skills, agents, commands, Claude/Codex surfaces). |

## Setup

Codex and Cursor plugins are installed automatically during DecisionsAI setup when those tools are detected. Manual install:

```bash
python3 plugins/codex-ide/scripts/install_local.py
python3 plugins/cursor-ide/scripts/install_local.py
```

The ECC harness pack is projected into installed harnesses by `distr/core/harness_pack.py` during setup.

## Code references

Use `distr.core.plugins` for canonical repo paths instead of hardcoding directories:

```python
from distr.core.plugins import codex_ide_source, cursor_ide_source, ecc_vendor_dir
```
