#!/usr/bin/env python3
"""
inventory.py - Scan directories and populate the tax pipeline dashboard

Creates the expected directory structure, discovers existing files,
seeds placeholders for downstream phases, and generates the HTML dashboard.

Usage:
    python scripts/inventory.py --year 2025
"""

import json
from datetime import datetime, timezone
from pathlib import Path
import click

from config_loader import load_config, PROJECT_ROOT
from dashboard import load_state, save_state, regenerate_html


def scan_dir(directory: Path, extensions: tuple = ()) -> list[dict]:
    """Return list of file entries in a directory (non-recursive)."""
    entries = []
    if not directory.exists():
        return entries
    for f in sorted(directory.iterdir()):
        if f.is_file() and (not extensions or f.suffix.lower() in extensions):
            entries.append({"name": f.name, "path": str(f)})
    return entries


def make_relative(entries: list[dict], project_root: Path) -> list[dict]:
    """Convert absolute paths to project-relative paths."""
    out = []
    for e in entries:
        e = dict(e)
        try:
            e["path"] = str(Path(e["path"]).resolve().relative_to(project_root.resolve()))
        except ValueError:
            pass
        out.append(e)
    return out


def placeholder_from(entries: list[dict], target_dir: str) -> list[dict]:
    """Create placeholder entries based on another directory's files."""
    return [
        {"name": e["name"], "path": f"{target_dir}/{e['name']}", "placeholder": True}
        for e in entries
    ]


def merge_placeholders_with_actual(
    placeholders: list[dict], actual: list[dict]
) -> list[dict]:
    """Merge placeholder list with actually-found files. Actual files override placeholders."""
    result = []
    actual_names = {e["name"] for e in actual}
    # Keep placeholders that aren't fulfilled
    for p in placeholders:
        if p["name"] not in actual_names:
            result.append(p)
    # Add all actual files
    result.extend(actual)
    # Sort by name
    result.sort(key=lambda e: e["name"])
    return result


@click.command()
@click.option(
    "--year", "-y", required=True, type=int, help="Current tax year (prior = year - 1)"
)
def main(year: int):
    """
    Scan directories and populate the tax pipeline dashboard.

    Creates expected directory structure, discovers existing files,
    and generates tax-dashboard.html at the project root.
    """
    prior = year - 1
    project_root = PROJECT_ROOT.resolve()
    config = load_config()
    tax_knowledge_rel = config.get("paths", {}).get("tax_knowledge", "data/tax-knowledge")

    click.echo(f"Tax Dashboard Inventory — {year} (prior: {prior})")
    click.echo(f"Project root: {project_root}\n")

    # -------------------------------------------------------------------
    # 1. Ensure directory structure exists
    # -------------------------------------------------------------------
    dirs_to_create = [
        f"data/raw/{year}/sources",
        f"data/raw/{year}/filed",
        f"data/raw/{year}/knowledge",
        f"data/raw/{prior}/sources",
        f"data/raw/{prior}/filed",
        f"data/raw/{prior}/knowledge",
        "data/extracted",
        "data/sanitized",
        "data/vault",
        "data/instructions",
        f"data/output/{year}",
    ]
    for d in dirs_to_create:
        (project_root / d).mkdir(parents=True, exist_ok=True)
    click.echo("Directories verified.")

    # -------------------------------------------------------------------
    # 2. Scan prior year directories
    # -------------------------------------------------------------------
    prior_raw_sources = scan_dir(project_root / f"data/raw/{prior}/sources")
    prior_raw_knowledge = scan_dir(project_root / f"data/raw/{prior}/knowledge")
    prior_raw_filed = scan_dir(project_root / f"data/raw/{prior}/filed")
    prior_tax_knowledge = scan_dir(
        project_root / tax_knowledge_rel / str(prior),
        extensions=(".json", ".md"),
    )

    # -------------------------------------------------------------------
    # 3. Scan current year directories & merge with placeholders
    # -------------------------------------------------------------------
    cur_raw_sources_actual = scan_dir(project_root / f"data/raw/{year}/sources")
    cur_raw_knowledge_actual = scan_dir(project_root / f"data/raw/{year}/knowledge")

    cur_sources_placeholders = placeholder_from(
        prior_raw_sources, f"data/raw/{year}/sources"
    )
    cur_knowledge_placeholders = placeholder_from(
        prior_raw_knowledge, f"data/raw/{year}/knowledge"
    )

    cur_raw_sources = merge_placeholders_with_actual(
        cur_sources_placeholders, cur_raw_sources_actual
    )
    cur_raw_knowledge = merge_placeholders_with_actual(
        cur_knowledge_placeholders, cur_raw_knowledge_actual
    )

    # Current year tax knowledge
    cur_tax_knowledge = scan_dir(
        project_root / tax_knowledge_rel / str(year),
        extensions=(".json", ".md"),
    )

    # -------------------------------------------------------------------
    # 4. Build downstream placeholders
    # -------------------------------------------------------------------
    # Extracted
    ext_prior_sources = [
        {"name": f"{prior}-sources.json", "path": f"data/extracted/{prior}-sources.json", "placeholder": True}
    ]
    ext_current_sources = [
        {"name": f"{year}-sources.json", "path": f"data/extracted/{year}-sources.json", "placeholder": True}
    ]
    ext_prior_filed = [
        {"name": f"{prior}-filed.json", "path": f"data/extracted/{prior}-filed.json", "placeholder": True}
    ]

    # Check which extracted files actually exist
    for entry_list in (ext_prior_sources, ext_current_sources, ext_prior_filed):
        for entry in entry_list:
            if (project_root / entry["path"]).exists():
                entry.pop("placeholder", None)

    # Tax knowledge in extracted column = the processed knowledge files
    ext_prior_knowledge = [dict(e) for e in prior_tax_knowledge]
    ext_current_knowledge = [dict(e) for e in cur_tax_knowledge]

    # Sanitized
    san_prior_sources = [
        {"name": f"{prior}-sources.json", "path": f"data/sanitized/{prior}-sources.json", "placeholder": True}
    ]
    san_current_sources = [
        {"name": f"{year}-sources.json", "path": f"data/sanitized/{year}-sources.json", "placeholder": True}
    ]
    san_prior_filed = [
        {"name": f"{prior}-filed.json", "path": f"data/sanitized/{prior}-filed.json", "placeholder": True}
    ]

    for entry_list in (san_prior_sources, san_current_sources, san_prior_filed):
        for entry in entry_list:
            if (project_root / entry["path"]).exists():
                entry.pop("placeholder", None)

    # Output — look at prior year output to guess expected forms, else generic
    prior_output_dir = project_root / f"data/output/{prior}"
    if prior_output_dir.exists():
        prior_output_files = [
            f.name for f in sorted(prior_output_dir.iterdir())
            if f.is_file() and f.suffix.lower() == ".pdf"
        ]
    else:
        prior_output_files = []

    if prior_output_files:
        output_filed = [
            {"name": name, "path": f"data/output/{year}/{name}", "placeholder": True}
            for name in prior_output_files
        ]
    else:
        output_filed = [
            {"name": "1040-filled.pdf", "path": f"data/output/{year}/1040-filled.pdf", "placeholder": True}
        ]

    for entry in output_filed:
        if (project_root / entry["path"]).exists():
            entry.pop("placeholder", None)

    output_instructions = [
        {"name": f"{year}.json", "path": f"data/instructions/{year}.json", "placeholder": True}
    ]
    for entry in output_instructions:
        if (project_root / entry["path"]).exists():
            entry.pop("placeholder", None)

    output_assembled = [
        {"name": f"{year}-filed.pdf", "path": f"data/output/{year}/{year}-filed.pdf", "placeholder": True}
    ]
    for entry in output_assembled:
        if (project_root / entry["path"]).exists():
            entry.pop("placeholder", None)

    # -------------------------------------------------------------------
    # 5. Build and save state
    # -------------------------------------------------------------------
    state = load_state(project_root)
    state["year"] = year
    state["prior_year"] = prior

    state["raw_input"] = {
        "prior_sources": make_relative(prior_raw_sources, project_root),
        "current_sources": make_relative(cur_raw_sources, project_root),
        "prior_knowledge": make_relative(prior_raw_knowledge, project_root),
        "current_knowledge": make_relative(cur_raw_knowledge, project_root),
        "prior_filed": make_relative(prior_raw_filed, project_root),
    }
    state["extracted_input"] = {
        "prior_sources": ext_prior_sources,
        "current_sources": ext_current_sources,
        "prior_knowledge": make_relative(ext_prior_knowledge, project_root),
        "current_knowledge": make_relative(ext_current_knowledge, project_root),
        "prior_filed": ext_prior_filed,
    }
    state["sanitized_input"] = {
        "prior_sources": san_prior_sources,
        "current_sources": san_current_sources,
        "prior_filed": san_prior_filed,
    }
    state["output"] = {
        "current_instructions": output_instructions,
        "current_filed": output_filed,
        "current_assembled": output_assembled,
    }
    state.setdefault("status", {}).setdefault("processing_complete", False)

    save_state(project_root, state)

    # -------------------------------------------------------------------
    # 6. Generate HTML
    # -------------------------------------------------------------------
    html_path = regenerate_html(project_root)
    click.echo(f"\nDashboard written to: {html_path}")

    # -------------------------------------------------------------------
    # 7. Print summary
    # -------------------------------------------------------------------
    def count_status(entries):
        found = sum(
            1 for e in entries
            if not e.get("placeholder") and (project_root / e.get("path", "")).exists()
        )
        return found, len(entries)

    cur_src_found, cur_src_total = count_status(cur_raw_sources)
    cur_know_found, cur_know_total = count_status(cur_raw_knowledge)

    click.echo(f"\nCurrent year sources:   {cur_src_found}/{cur_src_total} found")
    click.echo(f"Current year knowledge: {cur_know_found}/{cur_know_total} found")
    click.echo("\nDone.")


if __name__ == "__main__":
    main()
