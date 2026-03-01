#!/usr/bin/env python3
"""
dashboard.py - Tax pipeline dashboard state management and HTML generation

Provides shared functions for all pipeline scripts to update a visual
dashboard showing file status across pipeline phases.

State file: data/dashboard-state.json
Output: tax-dashboard.html (project root)
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def load_state(project_root: Path) -> dict:
    """Read dashboard state from data/dashboard-state.json, or return empty default."""
    state_path = project_root / "data" / "dashboard-state.json"
    if state_path.exists():
        with open(state_path) as f:
            return json.load(f)
    return {
        "year": None,
        "prior_year": None,
        "updated_at": None,
        "raw_input": {},
        "extracted_input": {},
        "sanitized_input": {},
        "output": {},
        "status": {"processing_complete": False},
    }


def save_state(project_root: Path, state: dict) -> None:
    """Write state JSON to data/dashboard-state.json."""
    state_path = project_root / "data" / "dashboard-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


def update_phase(
    project_root: Path,
    phase: str,
    category: str,
    files: list,
) -> None:
    """
    Mark files as present for a given phase/category.

    Args:
        project_root: Project root directory
        phase: One of "raw_input", "extracted_input", "sanitized_input", "output"
        category: Sub-key like "current_sources", "prior_knowledge", etc.
        files: List of Path objects or path strings for files that now exist
    """
    state = load_state(project_root)
    phase_dict = state.setdefault(phase, {})
    existing = phase_dict.get(category, [])

    for f in files:
        fp = Path(f)
        name = fp.name
        # Make path relative to project root
        try:
            rel = str(fp.resolve().relative_to(project_root.resolve()))
        except ValueError:
            rel = str(fp)

        # Update existing entry or add new one
        found = False
        for entry in existing:
            if entry["name"] == name:
                entry["path"] = rel
                entry.pop("placeholder", None)
                found = True
                break
        if not found:
            existing.append({"name": name, "path": rel})

    phase_dict[category] = existing
    state[phase] = phase_dict
    save_state(project_root, state)


def highlight_json(content: str) -> str:
    """Regex-based JSON syntax highlighting returning HTML spans."""
    # Escape HTML first
    content = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Keys (quoted strings followed by colon)
    content = re.sub(
        r'("(?:[^"\\]|\\.)*")\s*:',
        r'<span class="json-key">\1</span>:',
        content,
    )
    # String values (quoted strings NOT already wrapped)
    content = re.sub(
        r'(?<!class="json-key">)("(?:[^"\\]|\\.)*")',
        r'<span class="json-str">\1</span>',
        content,
    )
    # Numbers
    content = re.sub(
        r'\b(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b',
        r'<span class="json-num">\1</span>',
        content,
    )
    # Booleans and null
    content = re.sub(
        r'\b(true|false|null)\b',
        r'<span class="json-bool">\1</span>',
        content,
    )
    return content


def render_md_preview(content: str) -> str:
    """Render markdown content as preformatted HTML text."""
    escaped = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return escaped


def _read_preview(project_root: Path, rel_path: str, max_bytes: int = 100_000) -> Optional[str]:
    """Read file contents for inline preview. Returns None if file doesn't exist or is binary."""
    full_path = project_root / rel_path
    if not full_path.exists():
        return None
    suffix = full_path.suffix.lower()
    if suffix not in (".json", ".md"):
        return None
    try:
        raw = full_path.read_text(errors="replace")
    except Exception:
        return None
    truncated = False
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        truncated = True
    if suffix == ".json":
        try:
            parsed = json.loads(full_path.read_text())
            raw = json.dumps(parsed, indent=2)
            if len(raw) > max_bytes:
                raw = raw[:max_bytes]
                truncated = True
        except Exception:
            pass
        highlighted = highlight_json(raw)
    else:
        highlighted = render_md_preview(raw)
    if truncated:
        highlighted += '\n<span class="truncated">(truncated)</span>'
    return highlighted


def _file_exists(project_root: Path, rel_path: str) -> bool:
    """Check whether a file exists on disk."""
    return (project_root / rel_path).exists()


def _render_file_list(
    project_root: Path,
    entries: list,
) -> str:
    """Render a list of file entries as <ul> HTML."""
    if not entries:
        return '<ul class="file-list"><li class="empty">&mdash;</li></ul>'
    lines = ['<ul class="file-list">']
    for entry in entries:
        name = entry.get("name", "?")
        path = entry.get("path", "")
        is_placeholder = entry.get("placeholder", False)
        exists = _file_exists(project_root, path) if path else False
        css_class = "found" if (exists and not is_placeholder) else "missing"
        link = f'<a href="{path}">{name}</a>' if path else name
        lines.append(f'  <li class="{css_class}">{link}')
        # Add collapsible preview for existing .json / .md files
        if css_class == "found" and path:
            preview_html = _read_preview(project_root, path)
            if preview_html is not None:
                lines.append(
                    f'    <details><summary>preview</summary>'
                    f'<pre><code>{preview_html}</code></pre></details>'
                )
        lines.append("  </li>")
    lines.append("</ul>")
    return "\n".join(lines)


def regenerate_html(project_root: Path) -> Path:
    """
    Read state, verify file existence on disk, and write tax-dashboard.html.

    Returns the path to the generated HTML file.
    """
    state = load_state(project_root)
    year = state.get("year", "?")
    prior = state.get("prior_year", "?")
    updated = state.get("updated_at", "")
    processing_complete = state.get("status", {}).get("processing_complete", False)

    raw = state.get("raw_input", {})
    ext = state.get("extracted_input", {})
    san = state.get("sanitized_input", {})
    out = state.get("output", {})

    # Build table cells for each section. Order: raw(prior, cur), ext(prior, cur), san(prior, cur), output(cur filed, cur asm)
    def cell(phase_dict, key):
        return _render_file_list(project_root, phase_dict.get(key, []))

    processing_badge = ""
    if processing_complete:
        processing_badge = '<div class="badge badge-complete">Processing Complete</div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tax Dashboard — {year}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #fafafa; color: #333; padding: 16px; }}
  h1 {{ margin-bottom: 4px; font-size: 22px; }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 12px; }}
  table {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
  th {{ background: #1a237e; color: #fff; padding: 8px 6px; font-size: 13px; text-align: center; border: 1px solid #0d1553; }}
  td {{ vertical-align: top; padding: 4px 8px; border: 1px solid #ccc; font-size: 13px; }}
  .sub-header td {{ background: #e8eaf6; font-weight: bold; font-size: 12px; text-align: center; padding: 4px; }}
  .group-label td {{ background: #f5f5f5; font-weight: bold; font-size: 12px; border-top: 2px solid #999; }}
  ul.file-list {{ list-style: none; padding: 0; margin: 0; }}
  ul.file-list li {{ padding: 2px 0; }}
  ul.file-list li.empty {{ color: #bbb; }}
  .found a {{ color: #2e7d32; text-decoration: none; }}
  .found a:hover {{ text-decoration: underline; }}
  .missing a {{ color: #9e9e9e; text-decoration: none; }}
  .missing a:hover {{ text-decoration: underline; }}
  details {{ margin-top: 2px; }}
  details summary {{ cursor: pointer; font-size: 11px; color: #666; }}
  details pre {{ max-height: 400px; overflow-y: auto; font-size: 11px; background: #f8f8f8; border: 1px solid #ddd; padding: 6px; margin-top: 2px; white-space: pre-wrap; word-break: break-word; }}
  .json-key {{ color: #1565c0; }}
  .json-str {{ color: #2e7d32; }}
  .json-num {{ color: #e65100; }}
  .json-bool {{ color: #7b1fa2; }}
  .truncated {{ color: #999; font-style: italic; }}
  .badge {{ display: inline-block; padding: 4px 12px; border-radius: 4px; font-size: 12px; font-weight: bold; margin: 8px 0; }}
  .badge-complete {{ background: #c8e6c9; color: #2e7d32; }}
</style>
</head>
<body>

<h1>Tax Pipeline Dashboard</h1>
<p class="meta">Year: {year} | Prior year: {prior} | Updated: {updated}</p>
{processing_badge}

<table>
  <thead>
    <tr>
      <th colspan="2">Raw Input</th>
      <th colspan="2">Extracted Input</th>
      <th colspan="2">Sanitized Input</th>
      <th colspan="2">Output</th>
    </tr>
  </thead>
  <tbody>
    <!-- Sources row group -->
    <tr class="sub-header">
      <td>Prior Year Sources</td>
      <td>Current Year Sources</td>
      <td>Prior Year Sources</td>
      <td>Current Year Sources</td>
      <td>Prior Year Sources</td>
      <td>Current Year Sources</td>
      <td>Current Year Filed</td>
      <td>Current Year Assembled</td>
    </tr>
    <tr>
      <td>{cell(raw, "prior_sources")}</td>
      <td>{cell(raw, "current_sources")}</td>
      <td>{cell(ext, "prior_sources")}</td>
      <td>{cell(ext, "current_sources")}</td>
      <td>{cell(san, "prior_sources")}</td>
      <td>{cell(san, "current_sources")}</td>
      <td>{cell(out, "current_filed")}</td>
      <td>{cell(out, "current_assembled")}</td>
    </tr>

    <!-- Tax Knowledge row group -->
    <tr class="sub-header">
      <td>Prior Year Tax Knowledge</td>
      <td>Current Year Tax Knowledge</td>
      <td>Prior Year Tax Knowledge</td>
      <td>Current Year Tax Knowledge</td>
      <td colspan="2" style="color:#999; text-align:center; font-style:italic;">(tax knowledge not sanitized)</td>
      <td colspan="2"></td>
    </tr>
    <tr>
      <td>{cell(raw, "prior_knowledge")}</td>
      <td>{cell(raw, "current_knowledge")}</td>
      <td>{cell(ext, "prior_knowledge")}</td>
      <td>{cell(ext, "current_knowledge")}</td>
      <td colspan="2"></td>
      <td colspan="2"></td>
    </tr>

    <!-- Filed / Assembled row group -->
    <tr class="sub-header">
      <td>Prior Year Filed</td>
      <td></td>
      <td>Prior Year Filed</td>
      <td></td>
      <td>Prior Year Filed</td>
      <td></td>
      <td colspan="2"></td>
    </tr>
    <tr>
      <td>{cell(raw, "prior_filed")}</td>
      <td></td>
      <td>{cell(ext, "prior_filed")}</td>
      <td></td>
      <td>{cell(san, "prior_filed")}</td>
      <td></td>
      <td colspan="2"></td>
    </tr>
  </tbody>
</table>

</body>
</html>
"""

    out_path = project_root / "tax-dashboard.html"
    out_path.write_text(html)
    return out_path
