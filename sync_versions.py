"""
sync_versions.py — Prompt–code synchronisation for Advanced Profile Tool.

Scans *.py in the same folder for files that declare _LINKED_PROMPT,
extracts version metadata from module-level constants, and rewrites the
[[ METADATA_START ]] block in the linked prompt file inside Prompt/.

Run from the plugin root (Advanced Profile Tool/):
    python sync_versions.py           — update all linked prompt files
    python sync_versions.py --dry-run — preview changes without writing
"""

import os
import re
import sys
from datetime import date

WORKSPACE  = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = WORKSPACE
PROMPT_DIR = os.path.join(WORKSPACE, 'Prompt')
TODAY      = date.today().isoformat()

METADATA_RE = re.compile(
    r'\[\[ METADATA_START[^\]]*\]\].*?\[\[ METADATA_END \]\]',
    re.DOTALL
)

_FIELDS = {
    'version':       re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']',   re.MULTILINE),
    'tool_id':       re.compile(r'^TOOL_ID\s*=\s*["\']([^"\']+)["\']',        re.MULTILINE),
    'display_name':  re.compile(r'^DISPLAY_NAME\s*=\s*["\']([^"\']+)["\']',   re.MULTILINE),
    'group_name':    re.compile(r'^GROUP_NAME\s*=\s*["\']([^"\']+)["\']',     re.MULTILINE),
    'linked_prompt': re.compile(r'^_LINKED_PROMPT\s*=\s*["\']([^"\']+)["\']', re.MULTILINE),
}


def _extract(src):
    return {key: (m.group(1) if (m := pat.search(src)) else None)
            for key, pat in _FIELDS.items()}


def _build_block(meta, code_filename):
    return (
        '[[ METADATA_START — auto-synced by sync_versions.py, do not edit manually ]]\n'
        f'Tool Name        : {meta["display_name"] or "Unknown"}\n'
        f'Tool ID          : {meta["tool_id"] or "unknown"}\n'
        f'Version          : {meta["version"] or "?"}\n'
        f'Linked Code File : {code_filename}\n'
        f'Group / Library  : {meta["group_name"] or "Advanced Flood & Terrain Auditor"}\n'
        f'Developer        : Dipendra Magaju\n'
        f'Last Synced      : {TODAY}\n'
        '[[ METADATA_END ]]'
    )


def _sync_one(code_path, dry_run=False):
    with open(code_path, encoding='utf-8') as f:
        src = f.read()

    meta = _extract(src)
    if not meta['linked_prompt']:
        return 'skipped (no _LINKED_PROMPT declared)'

    prompt_path = os.path.join(PROMPT_DIR, meta['linked_prompt'])
    if not os.path.isfile(prompt_path):
        return f'ERROR — linked prompt not found: {prompt_path}'

    with open(prompt_path, encoding='utf-8') as f:
        prompt_content = f.read()

    new_block = _build_block(meta, os.path.basename(code_path))

    if METADATA_RE.search(prompt_content):
        new_content = METADATA_RE.sub(new_block, prompt_content, count=1)
    else:
        new_content = new_block + '\n\n' + prompt_content

    if new_content == prompt_content:
        return 'unchanged'

    if not dry_run:
        with open(prompt_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return 'updated'
    return 'updated (dry-run — not written)'


def main():
    dry_run = '--dry-run' in sys.argv

    if not os.path.isdir(PLUGIN_DIR):
        print(f'ERROR: Plugin directory not found: {PLUGIN_DIR}')
        sys.exit(1)
    if not os.path.isdir(PROMPT_DIR):
        print(f'Note: Prompt directory not found — nothing to sync.')
        return

    py_files = sorted(
        f for f in os.listdir(PLUGIN_DIR)
        if f.endswith('.py') and '_LINKED_PROMPT' in
        open(os.path.join(PLUGIN_DIR, f), encoding='utf-8').read()
    )

    print(f'sync_versions.py{"  [dry-run]" if dry_run else ""}  —  {TODAY}')
    print(f'Plugin dir : {PLUGIN_DIR}')
    print(f'Prompt dir : {PROMPT_DIR}')
    print()

    if not py_files:
        print('  No files with _LINKED_PROMPT found in plugin directory.')
        return

    any_error = False
    for filename in py_files:
        status = _sync_one(os.path.join(PLUGIN_DIR, filename), dry_run=dry_run)
        marker = 'ERR' if status.startswith('ERROR') else (' - ' if status == 'unchanged' else ' + ')
        print(f'  {marker}  {filename}: {status}')
        if status.startswith('ERROR'):
            any_error = True

    print()
    if any_error:
        print('Finished with errors — check the ERROR lines above.')
        sys.exit(1)
    else:
        print('Done.')


if __name__ == '__main__':
    main()
