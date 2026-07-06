# Advanced Profile Tool — Standards & Conventions

## Workspace Structure

```
Advanced Profile Tool/          ← git repository root AND plugin source root
  __init__.py
  plugin.py                     — Main plugin code (dock widget + map tool)
  profile_line_tool.py          — Interactive polyline digitising map tool
  metadata.txt
  icon.svg
  .gitignore
  install_dev.bat/ps1           — Developer machine (NTFS junction)
  install.bat/ps1               — Office machine (ZIP install)
  update.bat/ps1                — Git pull from GitHub
  update_init.bat               — Convert ZIP install to git (first-time)
  package_dist.bat/ps1          — Clean dist ZIP for third-party users
  APT.md                        — This document
  sync_versions.py              — Sync metadata from plugin.py → Prompt files
```

See [`plugindistribution.md`](../plugindistribution.md) for the full distribution workflow.

## Version & Metadata

Version is declared in two places — keep them in sync manually:

| File | Field |
|---|---|
| `plugin.py` | `__version__ = '0.x'` |
| `metadata.txt` | `version=0.x` |

## Prompt–Code Sync

`sync_versions.py` reads metadata constants from `plugin.py` and updates
the `[[ METADATA_START ]]` block in the linked prompt file (when a `Prompt/`
folder exists).

Constants required in `plugin.py`:
```python
__version__    = '0.5'
TOOL_ID        = 'fta_profile_tool'
DISPLAY_NAME   = 'Advanced Profile Tool'
GROUP_NAME     = 'Advanced Flood & Terrain Auditor'
_LINKED_PROMPT = 'FTA_Normal_Profile_V01_GM.txt'   # filename inside Prompt/
```

Run from this folder:
```
python sync_versions.py           # update prompt metadata
python sync_versions.py --dry-run # preview without writing
```

## QGIS Plugin Folder Name

QGIS loads this plugin from a folder named **`AdvancedProfileTool`** (no spaces).
On the developer machine this is an NTFS junction created by `install_dev.bat`.

## GitHub Repository

`https://github.com/DMagaju/Advanced-Profile-Tool`
